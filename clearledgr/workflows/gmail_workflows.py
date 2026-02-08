"""Gmail-specific Temporal workflows for Clearledgr extension.

These workflows handle email processing actions triggered from the Gmail extension,
ensuring reliable execution even if the browser tab closes.

KEY DIFFERENTIATORS:
1. Audit-Link Generation - Every post generates a Clearledgr_Audit_ID
2. Human-in-the-Loop (HITL) - <95% confidence blocks "Post", shows "Review Mismatch"
3. Multi-System Routing - Approval triggers both ERP post AND Slack thread update
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

from clearledgr.workflows.temporal_activities import (
    audit_event_activity,
    invoice_extraction_activity,
    invoice_categorization_activity,
    route_exception_activity,
)
from clearledgr.workflows.gmail_activities import (
    classify_email_activity,
    extract_email_data_activity,
    match_bank_feed_activity,
    match_erp_activity,
    verify_match_confidence_activity,
    apply_gmail_label_activity,
    post_to_erp_activity,
    update_slack_thread_activity,
    send_slack_notification_activity,
    create_mismatch_review_task_activity,
)


DEFAULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)


@workflow.defn
class EmailTriageWorkflow:
    """
    Workflow for triaging a single email.
    
    Steps:
    1. Classify email (INVOICE, REMITTANCE, STATEMENT, EXCEPTION, NOISE)
    2. Extract financial data if relevant
    3. Apply appropriate Gmail labels
    4. Return classification result
    """
    
    @workflow.run
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        email_id = payload.get("email_id")
        organization_id = payload.get("organization_id")
        
        # Step 1: Classify the email
        classification = await workflow.execute_activity(
            classify_email_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        
        if classification.get("type") == "NOISE":
            return {
                "email_id": email_id,
                "classification": classification,
                "action": "skipped",
                "reason": "Not a finance email"
            }
        
        # Step 2: Extract financial data
        extraction = await workflow.execute_activity(
            extract_email_data_activity,
            {**payload, "classification": classification},
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=DEFAULT_RETRY,
        )
        
        # Step 3: Apply Gmail labels
        await workflow.execute_activity(
            apply_gmail_label_activity,
            {
                "email_id": email_id,
                "classification": classification,
                "organization_id": organization_id,
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=DEFAULT_RETRY,
        )
        
        # Step 4: Audit trail
        await workflow.execute_activity(
            audit_event_activity,
            {
                "user_email": payload.get("user_email", "system"),
                "action": "email_triaged",
                "entity_type": "email",
                "entity_id": email_id,
                "organization_id": organization_id,
                "metadata": {
                    "classification": classification.get("type"),
                    "confidence": classification.get("confidence"),
                    "vendor": extraction.get("vendor"),
                    "amount": extraction.get("amount"),
                },
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=DEFAULT_RETRY,
        )
        
        return {
            "email_id": email_id,
            "classification": classification,
            "extraction": extraction,
            "action": "triaged",
        }


@workflow.defn
class EmailProcessingWorkflow:
    """
    Full email processing workflow - from triage to ERP posting.
    
    DIFFERENTIATORS:
    1. HITL Gate - Confidence check before allowing "Post" action
    2. Audit-Link - Every post gets a traceable Clearledgr_Audit_ID
    3. Multi-System - Approval triggers ERP + Slack updates
    
    Steps:
    1. Classify and extract data
    2. Match against bank feed
    3. Match against ERP (PO, vendor)
    4. HITL: Verify confidence and identify mismatches
    5. Generate suggested action (Post vs Review Mismatch)
    6. If auto-approved AND confidence >= 95%: post to ERP + update Slack
    7. If confidence < 95%: create review task with specific discrepancies
    """
    
    @workflow.run
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        email_id = payload.get("email_id")
        organization_id = payload.get("organization_id")
        auto_approve = payload.get("auto_approve", False)
        approval_threshold = payload.get("approval_threshold", 1000)
        
        # Step 1: Triage the email
        triage_result = await EmailTriageWorkflow().run(payload)
        
        if triage_result.get("action") == "skipped":
            return triage_result
        
        classification = triage_result.get("classification", {})
        extraction = triage_result.get("extraction", {})
        
        # Step 2: Match against bank feed
        bank_match = await workflow.execute_activity(
            match_bank_feed_activity,
            {
                "extraction": extraction,
                "organization_id": organization_id,
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        
        # Step 3: Match against ERP
        erp_match = await workflow.execute_activity(
            match_erp_activity,
            {
                "extraction": extraction,
                "organization_id": organization_id,
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        
        # Step 4: HITL - Verify confidence and identify mismatches
        confidence_result = await workflow.execute_activity(
            verify_match_confidence_activity,
            {
                "extraction": extraction,
                "bank_match": bank_match,
                "erp_match": erp_match,
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=DEFAULT_RETRY,
        )
        
        # Step 5: Determine action based on confidence
        amount = extraction.get("amount", 0)
        can_post = confidence_result.get("can_post", False)
        requires_review = confidence_result.get("requires_review", True)
        
        # HITL Gate: Only allow auto-approve if confidence >= 95%
        can_auto_approve = (
            auto_approve and 
            amount < approval_threshold and 
            can_post and 
            not requires_review
        )
        
        suggested_action = self._determine_action_with_confidence(
            classification.get("type"),
            bank_match,
            erp_match,
            extraction,
            confidence_result
        )
        
        result = {
            "email_id": email_id,
            "classification": classification,
            "extraction": extraction,
            "bank_match": bank_match,
            "erp_match": erp_match,
            "confidence": confidence_result,
            "suggested_action": suggested_action,
            "auto_approved": False,
        }
        
        # Step 6a: If confidence >= 95% and auto-approved: Post to ERP + Update Slack
        if can_auto_approve and suggested_action.get("action") == "post_to_ledger":
            # Post to ERP with Audit-Link
            post_result = await workflow.execute_activity(
                post_to_erp_activity,
                {
                    "email_id": email_id,
                    "extraction": extraction,
                    "erp_match": erp_match,
                    "confidence_result": confidence_result,
                    "organization_id": organization_id,
                    "approved_by": payload.get("user_email", "auto"),
                },
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=DEFAULT_RETRY,
            )
            
            result["auto_approved"] = True
            result["post_result"] = post_result
            result["clearledgr_audit_id"] = post_result.get("clearledgr_audit_id")
            
            # MULTI-SYSTEM: Update Slack thread
            await workflow.execute_activity(
                update_slack_thread_activity,
                {
                    "email_id": email_id,
                    "vendor": extraction.get("vendor"),
                    "amount": extraction.get("amount"),
                    "currency": extraction.get("currency"),
                    "invoice_number": extraction.get("invoice_number"),
                    "clearledgr_audit_id": post_result.get("clearledgr_audit_id"),
                    "erp_document": post_result.get("document_number"),
                    "approved_by": payload.get("user_email", "auto"),
                    "organization_id": organization_id,
                },
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=DEFAULT_RETRY,
            )
            
            # Update Gmail label to Processed
            await workflow.execute_activity(
                apply_gmail_label_activity,
                {
                    "email_id": email_id,
                    "label": "Clearledgr/Processed",
                    "remove_label": "Clearledgr/Needs Review",
                    "organization_id": organization_id,
                },
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=DEFAULT_RETRY,
            )
        
        # Step 6b: If confidence < 95%: Create review task with mismatches
        elif requires_review or not can_post:
            # Create mismatch review task
            review_task = await workflow.execute_activity(
                create_mismatch_review_task_activity,
                {
                    "email_id": email_id,
                    "extraction": extraction,
                    "confidence_result": confidence_result,
                    "organization_id": organization_id,
                },
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=DEFAULT_RETRY,
            )
            result["review_task"] = review_task
            
            # Route as exception
            await workflow.execute_activity(
                route_exception_activity,
                {
                    "title": f"Review Mismatch: {extraction.get('vendor', 'Unknown')}",
                    "description": f"Confidence {confidence_result.get('confidence_pct', 0)}% - below 95% threshold",
                    "organization_id": organization_id,
                    "requester": payload.get("user_email"),
                    "metadata": {
                        "email_id": email_id,
                        "vendor": extraction.get("vendor"),
                        "amount": extraction.get("amount"),
                        "confidence": confidence_result.get("confidence_pct"),
                        "mismatches": confidence_result.get("mismatches", []),
                    },
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )
        
        # Step 7: Send Slack notification with confidence info
        if amount >= 10000 or classification.get("type") == "EXCEPTION" or requires_review:
            await workflow.execute_activity(
                send_slack_notification_activity,
                {
                    "type": "email_processed",
                    "email_id": email_id,
                    "classification": classification,
                    "extraction": extraction,
                    "suggested_action": suggested_action,
                    "confidence_result": confidence_result,
                    "organization_id": organization_id,
                },
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=DEFAULT_RETRY,
            )
        
        # Audit trail
        await workflow.execute_activity(
            audit_event_activity,
            {
                "user_email": payload.get("user_email", "system"),
                "action": "email_processed",
                "entity_type": "email",
                "entity_id": email_id,
                "organization_id": organization_id,
                "metadata": {
                    **result,
                    "confidence_check": "passed" if can_post else "failed",
                },
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=DEFAULT_RETRY,
        )
        
        return result
    
    def _determine_action_with_confidence(
        self,
        doc_type: str,
        bank_match: Dict[str, Any],
        erp_match: Dict[str, Any],
        extraction: Dict[str, Any],
        confidence_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Determine action based on matches AND confidence.
        
        HITL: If confidence < 95%, don't offer "Post" - offer "Review Mismatch" instead.
        """
        can_post = confidence_result.get("can_post", False)
        mismatches = confidence_result.get("mismatches", [])
        confidence_pct = confidence_result.get("confidence_pct", 0)
        
        # HITL Gate: Block posting if confidence too low
        if not can_post:
            mismatch_summary = "; ".join([m["message"] for m in mismatches[:2]])
            return {
                "action": "review_mismatch",
                "label": "Review Mismatch",
                "description": f"Confidence {confidence_pct}% - {mismatch_summary}",
                "requires_review": True,
                "can_post": False,
                "mismatches": mismatches,
                "reason": f"Below 95% confidence threshold ({confidence_pct}%)",
            }
        
        # High confidence - allow posting
        if doc_type == "INVOICE":
            if erp_match.get("poMatch"):
                return {
                    "action": "post_to_ledger",
                    "label": "Approve & Post",
                    "description": f"Match found: PO #{erp_match['poMatch'].get('number')} ({confidence_pct}% confidence)",
                    "requires_review": False,
                    "can_post": True,
                    "confidence": confidence_pct,
                }
            elif erp_match.get("vendorMatch"):
                return {
                    "action": "post_to_ledger",
                    "label": "Approve & Post",
                    "description": f"Known vendor: {erp_match['vendorMatch'].get('name')} ({confidence_pct}% confidence)",
                    "requires_review": False,
                    "can_post": True,
                    "confidence": confidence_pct,
                }
        
        elif doc_type == "REMITTANCE":
            if bank_match.get("matched"):
                return {
                    "action": "clear_invoice",
                    "label": "Clear Invoice",
                    "description": f"Bank match confirmed ({confidence_pct}% confidence)",
                    "requires_review": False,
                    "can_post": True,
                }
        
        elif doc_type == "STATEMENT":
            return {
                "action": "reconcile",
                "label": "Start Reconciliation",
                "description": "Bank statement ready for reconciliation",
                "requires_review": False,
                "can_post": False,  # Statements don't get "posted"
            }
        
        elif doc_type == "EXCEPTION":
            return {
                "action": "escalate",
                "label": "Escalate",
                "description": "Exception requires immediate attention",
                "requires_review": True,
                "urgent": True,
                "can_post": False,
            }
        
        # Default: require review
        return {
            "action": "review",
            "label": "Review",
            "description": f"Manual review needed ({confidence_pct}% confidence)",
            "requires_review": True,
            "can_post": False,
        }


@workflow.defn
class BulkEmailScanWorkflow:
    """
    Workflow for scanning multiple emails in bulk.
    
    Used when the extension triggers an inbox scan.
    """
    
    @workflow.run
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        email_ids: List[str] = payload.get("email_ids", [])
        organization_id = payload.get("organization_id")
        user_email = payload.get("user_email")
        
        results = {
            "total": len(email_ids),
            "processed": 0,
            "labeled": 0,
            "errors": [],
            "by_type": {
                "invoices": 0,
                "statements": 0,
                "payments": 0,
                "receipts": 0,
                "exceptions": 0,
            }
        }
        
        for email_id in email_ids:
            try:
                triage_result = await EmailTriageWorkflow().run({
                    "email_id": email_id,
                    "organization_id": organization_id,
                    "user_email": user_email,
                })
                
                results["processed"] += 1
                
                if triage_result.get("action") != "skipped":
                    results["labeled"] += 1
                    doc_type = triage_result.get("classification", {}).get("type", "").lower()
                    
                    if "invoice" in doc_type:
                        results["by_type"]["invoices"] += 1
                    elif "statement" in doc_type:
                        results["by_type"]["statements"] += 1
                    elif "remittance" in doc_type or "payment" in doc_type:
                        results["by_type"]["payments"] += 1
                    elif "receipt" in doc_type:
                        results["by_type"]["receipts"] += 1
                    elif "exception" in doc_type:
                        results["by_type"]["exceptions"] += 1
                        
            except Exception as e:
                results["errors"].append({
                    "email_id": email_id,
                    "error": str(e)
                })
        
        # Audit the bulk scan
        await workflow.execute_activity(
            audit_event_activity,
            {
                "user_email": user_email or "system",
                "action": "bulk_email_scan",
                "entity_type": "inbox",
                "organization_id": organization_id,
                "metadata": results,
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=DEFAULT_RETRY,
        )
        
        return results


@workflow.defn  
class ApproveAndPostWorkflow:
    """
    Workflow triggered when user clicks "Approve & Post" in the extension.
    
    DIFFERENTIATORS:
    1. Audit-Link: Generates Clearledgr_Audit_ID appended to ERP memo
    2. HITL Gate: Re-verifies confidence before posting (defense in depth)
    3. Multi-System: Posts to ERP AND updates Slack thread
    
    Ensures the posting happens reliably even if browser closes.
    """
    
    @workflow.run
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        email_id = payload.get("email_id")
        organization_id = payload.get("organization_id")
        user_email = payload.get("user_email")
        extraction = payload.get("extraction", {})
        bank_match = payload.get("bank_match", {})
        erp_match = payload.get("erp_match", {})
        
        # HITL Gate: Re-verify confidence before posting (defense in depth)
        confidence_result = await workflow.execute_activity(
            verify_match_confidence_activity,
            {
                "extraction": extraction,
                "bank_match": bank_match,
                "erp_match": erp_match,
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=DEFAULT_RETRY,
        )
        
        # Block if confidence dropped below threshold
        if not confidence_result.get("can_post", False):
            # Create review task instead
            review_task = await workflow.execute_activity(
                create_mismatch_review_task_activity,
                {
                    "email_id": email_id,
                    "extraction": extraction,
                    "confidence_result": confidence_result,
                    "organization_id": organization_id,
                },
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=DEFAULT_RETRY,
            )
            
            return {
                "email_id": email_id,
                "status": "blocked",
                "reason": f"Confidence {confidence_result.get('confidence_pct')}% below 95% threshold",
                "mismatches": confidence_result.get("mismatches", []),
                "review_task": review_task,
                "action_required": "review_mismatch",
            }
        
        # DIFFERENTIATOR 1: Post to ERP with Audit-Link
        post_result = await workflow.execute_activity(
            post_to_erp_activity,
            {
                "email_id": email_id,
                "extraction": extraction,
                "erp_match": erp_match,
                "confidence_result": confidence_result,
                "organization_id": organization_id,
                "approved_by": user_email,
            },
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=DEFAULT_RETRY,
        )
        
        # Check if ERP posting was blocked
        if post_result.get("status") == "blocked":
            return {
                "email_id": email_id,
                "status": "blocked",
                "reason": post_result.get("reason"),
                "mismatches": post_result.get("mismatches", []),
            }
        
        audit_id = post_result.get("clearledgr_audit_id")
        
        # DIFFERENTIATOR 3: Multi-System - Update Slack thread
        await workflow.execute_activity(
            update_slack_thread_activity,
            {
                "email_id": email_id,
                "vendor": extraction.get("vendor"),
                "amount": extraction.get("amount"),
                "currency": extraction.get("currency"),
                "invoice_number": extraction.get("invoice_number"),
                "clearledgr_audit_id": audit_id,
                "erp_document": post_result.get("document_number"),
                "approved_by": user_email,
                "organization_id": organization_id,
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=DEFAULT_RETRY,
        )
        
        # Update Gmail label
        await workflow.execute_activity(
            apply_gmail_label_activity,
            {
                "email_id": email_id,
                "label": "Clearledgr/Processed",
                "remove_label": "Clearledgr/Needs Review",
                "organization_id": organization_id,
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=DEFAULT_RETRY,
        )
        
        # Audit trail with Audit-Link
        await workflow.execute_activity(
            audit_event_activity,
            {
                "user_email": user_email,
                "action": "invoice_approved_and_posted",
                "entity_type": "invoice",
                "entity_id": audit_id,  # Use Audit-Link as entity ID
                "organization_id": organization_id,
                "metadata": {
                    "email_id": email_id,
                    "clearledgr_audit_id": audit_id,
                    "vendor": extraction.get("vendor"),
                    "amount": extraction.get("amount"),
                    "currency": extraction.get("currency"),
                    "invoice_number": extraction.get("invoice_number"),
                    "erp_document": post_result.get("document_number"),
                    "confidence": confidence_result.get("confidence_pct"),
                    "post_result": post_result,
                },
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=DEFAULT_RETRY,
        )
        
        return {
            "email_id": email_id,
            "status": "posted",
            "clearledgr_audit_id": audit_id,
            "erp_document": post_result.get("document_number"),
            "confidence": confidence_result.get("confidence_pct"),
            "post_result": post_result,
            "slack_updated": True,
        }
