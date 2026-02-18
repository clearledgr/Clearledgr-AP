"""API endpoints for the Clearledgr Gmail Extension.

These endpoints are called by the Chrome extension to trigger
Temporal workflows for reliable email processing.

KEY DIFFERENTIATORS:
1. Audit-Link Generation - Every post generates a Clearledgr_Audit_ID
2. Human-in-the-Loop (HITL) - <95% confidence blocks "Post", shows "Review Mismatch"
3. Multi-System Routing - Approval triggers both ERP post AND Slack thread update
4. Intelligent Agent - Vendor intelligence, policy compliance, priority detection
"""
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from pydantic import BaseModel

from clearledgr.api.deps import get_audit_service
from clearledgr.services.audit import AuditTrailService
from clearledgr.workflows.temporal_runtime import TemporalRuntime, temporal_enabled

# Import all intelligence services
from clearledgr.services.vendor_intelligence import get_vendor_intelligence
from clearledgr.services.policy_compliance import get_policy_compliance
from clearledgr.services.priority_detection import get_priority_detection
from clearledgr.services.audit_trail import get_audit_trail, AuditEventType
from clearledgr.services.budget_awareness import get_budget_awareness
from clearledgr.services.agent_reflection import get_agent_reflection
from clearledgr.services.proactive_insights import get_proactive_insights
from clearledgr.services.cross_invoice_analysis import get_cross_invoice_analyzer
from clearledgr.services.agent_reasoning import get_agent as get_reasoning_agent
from clearledgr.core.database import get_db
from clearledgr.api.ap_items import build_worklist_item


router = APIRouter(prefix="/extension", tags=["gmail-extension"])


# ==================== REQUEST MODELS ====================

class EmailTriageRequest(BaseModel):
    """Request to triage a single email."""
    email_id: str
    subject: Optional[str] = None
    sender: Optional[str] = None
    snippet: Optional[str] = None
    body: Optional[str] = None  # Full email body for better extraction
    attachments: Optional[List[Dict[str, Any]]] = None  # With content_base64 for Claude Vision
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class EmailProcessRequest(BaseModel):
    """Request to fully process an email (triage + match + action)."""
    email_id: str
    subject: Optional[str] = None
    sender: Optional[str] = None
    snippet: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    auto_approve: bool = False
    approval_threshold: float = 1000.0


class BulkScanRequest(BaseModel):
    """Request to scan multiple emails."""
    email_ids: List[str]
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class ApproveAndPostRequest(BaseModel):
    """Request to approve and post an invoice with HITL gate."""
    email_id: str
    extraction: Dict[str, Any]
    bank_match: Optional[Dict[str, Any]] = None
    erp_match: Optional[Dict[str, Any]] = None
    override: bool = False  # Force post despite low confidence
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class VerifyConfidenceRequest(BaseModel):
    """Request to verify match confidence (HITL check)."""
    email_id: str
    extraction: Dict[str, Any]
    bank_match: Optional[Dict[str, Any]] = None
    erp_match: Optional[Dict[str, Any]] = None
    organization_id: Optional[str] = None


class EscalateRequest(BaseModel):
    """Request to escalate to manager via Slack."""
    email_id: str
    vendor: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "USD"
    confidence: Optional[float] = None
    mismatches: List[Dict[str, Any]] = []
    message: Optional[str] = None
    channel: str = "#finance-escalations"
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class MatchBankRequest(BaseModel):
    """Request to match against bank feed."""
    extraction: Dict[str, Any]
    organization_id: Optional[str] = None


class MatchERPRequest(BaseModel):
    """Request to match against ERP."""
    extraction: Dict[str, Any]
    organization_id: Optional[str] = None


# ==================== ENDPOINTS ====================

@router.post("/triage")
async def triage_email(
    request: EmailTriageRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    """
    Triage a single email - classify, extract, and apply intelligence.
    
    This triggers the EmailTriageWorkflow which:
    1. Classifies the email (INVOICE, REMITTANCE, STATEMENT, etc.)
    2. Extracts financial data (vendor, amount, due date)
    3. Applies Gmail labels
    4. Enriches with vendor intelligence
    5. Checks policy compliance
    6. Calculates priority
    7. Detects duplicates/anomalies
    8. Self-validates extraction
    
    Returns immediately with workflow_id if Temporal is enabled,
    or waits for result if running inline.
    """
    payload = request.model_dump()
    org_id = request.organization_id or "default"
    
    # Build combined text for agent reasoning (used for both Temporal + inline)
    combined_text = "\n".join(
        [v for v in [request.subject, request.snippet, request.body] if v]
    ).strip()

    if temporal_enabled():
        runtime = TemporalRuntime()
        result = await runtime.start_workflow(
            "EmailTriageWorkflow",
            payload,
            task_queue="clearledgr-gmail",
            wait=True,
            timeout_seconds=30,
        )
        # Still apply intelligence to Temporal results
        if result.get("extraction"):
            result = await _apply_intelligence(result, org_id, request.email_id)
        # Apply agent reasoning (deep autonomy) even when Temporal is used
        result = _apply_agent_reasoning(
            result=result,
            org_id=org_id,
            combined_text=combined_text,
            attachments=request.attachments or [],
        )
        return result
    
    # Inline execution (no Temporal)
    from clearledgr.workflows.gmail_activities import (
        classify_email_activity,
        extract_email_data_activity,
    )
    
    # Initialize audit trail for this invoice
    trail = get_audit_trail(org_id)
    trail.log(
        invoice_id=request.email_id,
        event_type=AuditEventType.RECEIVED,
        summary=f"Email received from {request.sender or 'unknown'}",
        details={"subject": request.subject, "sender": request.sender},
    )
    
    classification = await classify_email_activity(payload)
    
    trail.log(
        invoice_id=request.email_id,
        event_type=AuditEventType.CLASSIFIED,
        summary=f"Classified as {classification.get('type', 'UNKNOWN')}",
        confidence=classification.get("confidence", 0),
        reasoning=classification.get("reason", "AI classification"),
    )
    
    if classification.get("type") == "NOISE":
        return {
            "email_id": request.email_id,
            "classification": classification,
            "action": "skipped",
        }
    
    extraction = await extract_email_data_activity({**payload, "classification": classification})
    extracted_amount = extraction.get("amount")
    amount_display = (
        f"{float(extracted_amount):,.2f}"
        if isinstance(extracted_amount, (int, float))
        else "Unknown"
    )

    trail.log(
        invoice_id=request.email_id,
        event_type=AuditEventType.EXTRACTED,
        summary=f"Extracted: {extraction.get('vendor', 'Unknown')} ${amount_display}",
        confidence=extraction.get("confidence", 0),
        vendor=extraction.get("vendor"),
        amount=extraction.get("amount"),
    )
    
    # ========== APPLY ALL INTELLIGENCE ==========
    
    # 1. Self-reflection: Agent checks its own work
    reflection = get_agent_reflection()
    original_text = f"{request.subject or ''} {request.snippet or ''} {request.body or ''}"
    reflection_result = reflection.reflect_on_extraction(extraction, original_text)
    
    if reflection_result.corrections_made:
        extraction = reflection_result.final_extraction
        trail.log(
            invoice_id=request.email_id,
            event_type=AuditEventType.VALIDATED,
            summary=f"Self-corrected {len(reflection_result.corrections_made)} field(s)",
            reasoning="; ".join(reflection_result.reflection_notes),
        )
    
    # 2. Vendor Intelligence: Know vendors before told
    vendor_intel = get_vendor_intelligence()
    vendor_info = vendor_intel.get_suggestion(extraction.get("vendor", ""))
    if vendor_info:
        extraction["vendor_intelligence"] = vendor_info
        # Apply suggested GL if not already set
        if not extraction.get("gl_code") and vendor_info.get("suggested_gl"):
            extraction["gl_code"] = vendor_info["suggested_gl"]
            extraction["gl_source"] = "vendor_intelligence"
    
    # 3. Policy Compliance: Check against company policies
    policy_service = get_policy_compliance(org_id)
    invoice_for_policy = {
        "vendor": extraction.get("vendor") or "",
        "amount": extraction.get("amount", 0),
        "category": extraction.get("category") or "",
        "vendor_intelligence": extraction.get("vendor_intelligence", {}),
    }
    policy_result = policy_service.check(invoice_for_policy)
    extraction["policy_compliance"] = policy_result.to_dict()
    
    if not policy_result.compliant:
        trail.log(
            invoice_id=request.email_id,
            event_type=AuditEventType.POLICY_CHECK,
            summary=f"Policy: {len(policy_result.violations)} requirement(s)",
            details={"violations": [v.message for v in policy_result.violations]},
        )
    
    # 4. Priority Detection: Smart urgency scoring
    priority_service = get_priority_detection(org_id)
    invoice_for_priority = {
        "id": request.email_id,
        "vendor": extraction.get("vendor"),
        "amount": extraction.get("amount", 0),
        "due_date": extraction.get("due_date"),
        "created_at": extraction.get("created_at"),
        "vendor_intelligence": extraction.get("vendor_intelligence", {}),
    }
    priority = priority_service.assess(invoice_for_priority)
    extraction["priority"] = priority.to_dict()
    
    # 5. Cross-Invoice Analysis: Duplicates and anomalies
    analyzer = get_cross_invoice_analyzer(org_id)
    analysis = analyzer.analyze(
        vendor=extraction.get("vendor", ""),
        amount=extraction.get("amount", 0),
        invoice_number=extraction.get("invoice_number"),
        invoice_date=extraction.get("invoice_date"),
        gmail_id=request.email_id,
    )
    extraction["cross_invoice_analysis"] = analysis.to_dict()
    duplicate_alerts = getattr(analysis, "duplicates", []) or []
    
    if duplicate_alerts:
        trail.log(
            invoice_id=request.email_id,
            event_type=AuditEventType.DUPLICATE_CHECK,
            summary=f"Potential duplicate detected",
            details={"duplicates": [getattr(d, "invoice_id", None) for d in duplicate_alerts]},
        )
    
    # 6. Budget Awareness: Check budget impact
    budget_service = get_budget_awareness(org_id)
    budget_checks = budget_service.check_invoice(invoice_for_policy)
    if budget_checks:
        extraction["budget_impact"] = [b.to_dict() for b in budget_checks]
        
        # Alert if budget critical
        for check in budget_checks:
            if check.after_approval_status.value in ["critical", "exceeded"]:
                trail.log(
                    invoice_id=request.email_id,
                    event_type=AuditEventType.ANALYZED,
                    summary=f"Budget alert: {check.budget.name} at {check.after_approval_percent:.0f}%",
                )
    
    # 7. Proactive Insights: Check for alerts
    insights_service = get_proactive_insights(org_id)
    insights = insights_service.analyze_after_invoice(invoice_for_priority)
    if insights:
        extraction["insights"] = [
            {"title": i.title, "description": i.description, "severity": i.severity}
            for i in insights
        ]
    
    # Record decision in audit trail
    trail.log(
        invoice_id=request.email_id,
        event_type=AuditEventType.DECISION_MADE,
        summary=f"Ready for processing - Priority: {priority.priority.label}",
        confidence=extraction.get("confidence", 0),
        reasoning=f"Vendor: {'known' if vendor_info else 'new'}, Policy: {'compliant' if policy_result.compliant else 'requirements'}, Duplicates: {len(duplicate_alerts)}",
    )
    
    # Legacy audit
    audit.record_event(
        user_email=request.user_email or "extension",
        action="email_triaged",
        entity_type="email",
        entity_id=request.email_id,
        organization_id=request.organization_id,
        metadata={
            "classification": classification.get("type"),
            "vendor": extraction.get("vendor"),
            "amount": extraction.get("amount"),
            "priority": priority.priority.value,
            "policy_compliant": policy_result.compliant,
            "potential_duplicates": len(duplicate_alerts),
        },
    )
    
    result = {
        "email_id": request.email_id,
        "classification": classification,
        "extraction": extraction,
        "action": "triaged",
        "ai_powered": True,
        "intelligence": {
            "vendor_known": vendor_info is not None,
            "vendor_info": vendor_info,
            "policy_compliant": policy_result.compliant,
            "policy_requirements": [v.message for v in policy_result.violations],
            "required_approvers": policy_result.required_approvers,
            "priority": priority.priority.value,
            "priority_label": priority.priority.label,
            "days_until_due": priority.days_until_due,
            "alerts": priority.alerts,
            "potential_duplicates": len(duplicate_alerts),
            "anomalies": [getattr(a, "anomaly_type", None) for a in (getattr(analysis, "anomalies", []) or [])],
            "budget_warnings": [
                b.warning_message for b in budget_checks if b.warning_message
            ] if budget_checks else [],
            "insights": [i.title for i in insights] if insights else [],
            "self_verified": reflection_result.self_verified,
        },
    }

    # Agent reasoning layer (deep autonomy)
    result = _apply_agent_reasoning(
        result=result,
        org_id=org_id,
        combined_text=combined_text,
        attachments=request.attachments or [],
    )

    return result


async def _apply_intelligence(result: Dict[str, Any], org_id: str, email_id: str) -> Dict[str, Any]:
    """Apply intelligence services to a triage result."""
    extraction = result.get("extraction", {})
    
    # Vendor Intelligence
    vendor_intel = get_vendor_intelligence()
    vendor_info = vendor_intel.get_suggestion(extraction.get("vendor", ""))
    if vendor_info:
        extraction["vendor_intelligence"] = vendor_info
    
    # Policy Compliance
    policy_service = get_policy_compliance(org_id)
    policy_result = policy_service.check({
        "vendor": extraction.get("vendor"),
        "amount": extraction.get("amount", 0),
        "vendor_intelligence": vendor_info or {},
    })
    extraction["policy_compliance"] = policy_result.to_dict()
    
    # Priority Detection
    priority_service = get_priority_detection(org_id)
    priority = priority_service.assess({
        "id": email_id,
        "vendor": extraction.get("vendor"),
        "amount": extraction.get("amount", 0),
        "due_date": extraction.get("due_date"),
    })
    extraction["priority"] = priority.to_dict()
    
    result["extraction"] = extraction
    result["intelligence"] = {
        "vendor_known": vendor_info is not None,
        "policy_compliant": policy_result.compliant,
        "priority": priority.priority.value,
        "priority_label": priority.priority.label,
    }
    
    return result


def _merge_agent_extraction(
    extraction: Dict[str, Any],
    agent_extraction: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill missing extraction fields from agent reasoning output."""
    if not agent_extraction:
        return extraction

    merged = dict(extraction or {})

    def _set_if_missing(key: str, value: Any):
        if value is None or value == "":
            return
        if merged.get(key) in (None, "", 0):
            merged[key] = value

    _set_if_missing("vendor", agent_extraction.get("vendor"))
    _set_if_missing("amount", agent_extraction.get("total_amount"))
    _set_if_missing("currency", agent_extraction.get("currency"))
    _set_if_missing("invoice_number", agent_extraction.get("invoice_number"))
    _set_if_missing("invoice_date", agent_extraction.get("invoice_date"))
    _set_if_missing("due_date", agent_extraction.get("due_date"))

    # Prefer agent line items if none exist
    if not merged.get("line_items") and agent_extraction.get("line_items"):
        merged["line_items"] = agent_extraction.get("line_items")

    return merged


def _apply_agent_reasoning(
    result: Dict[str, Any],
    org_id: str,
    combined_text: str,
    attachments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Run agent reasoning and merge decision + extraction."""
    if not combined_text and not attachments:
        return result

    try:
        agent = get_reasoning_agent(org_id)
        decision = agent.reason_about_invoice(combined_text, attachments)
    except Exception as exc:  # noqa: BLE001
        result.setdefault("agent_decision_error", str(exc))
        return result

    extraction = result.get("extraction") or {}
    extraction = _merge_agent_extraction(extraction, decision.extraction or {})

    # Boost confidence if agent produced one
    try:
        extraction_confidence = float(extraction.get("confidence") or 0.0)
        extraction["confidence"] = max(extraction_confidence, float(decision.confidence))
    except Exception:
        pass

    result["extraction"] = extraction
    result["agent_decision"] = decision.to_dict()
    return result


@router.post("/process")
async def process_email(
    request: EmailProcessRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    """
    Fully process an email - triage, match, and suggest/execute action.
    
    This triggers the EmailProcessingWorkflow which:
    1. Triages the email
    2. Matches against bank feed
    3. Matches against ERP (PO, vendor)
    4. Determines suggested action
    5. Auto-posts if approved and under threshold
    6. Routes exceptions if needed
    
    Use this for the "Process" button in the extension.
    """
    payload = request.model_dump()
    
    if temporal_enabled():
        runtime = TemporalRuntime()
        # Don't wait - this can take longer
        result = await runtime.start_workflow(
            "EmailProcessingWorkflow",
            payload,
            task_queue="clearledgr-gmail",
            wait=False,
        )
        return {
            "status": "processing",
            "workflow_id": result.get("workflow_id"),
            "email_id": request.email_id,
        }
    
    # Inline execution - simplified
    triage_result = await triage_email(
        EmailTriageRequest(**{k: v for k, v in payload.items() if k in EmailTriageRequest.model_fields}),
        audit=audit,
    )
    
    return {
        "email_id": request.email_id,
        "status": "processed_inline",
        "triage": triage_result,
    }


@router.post("/scan")
async def bulk_scan_emails(
    request: BulkScanRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    """
    Scan multiple emails in bulk.
    
    This triggers the BulkEmailScanWorkflow which processes
    each email through the triage workflow.
    
    Use this for inbox scanning.
    """
    payload = request.model_dump()
    
    if temporal_enabled():
        runtime = TemporalRuntime()
        result = await runtime.start_workflow(
            "BulkEmailScanWorkflow",
            payload,
            task_queue="clearledgr-gmail",
            wait=False,  # Don't wait for bulk operations
        )
        return {
            "status": "scanning",
            "workflow_id": result.get("workflow_id"),
            "email_count": len(request.email_ids),
        }
    
    # Inline execution
    results = {
        "total": len(request.email_ids),
        "processed": 0,
        "labeled": 0,
        "by_type": {},
    }
    
    for email_id in request.email_ids[:50]:  # Limit inline processing
        try:
            triage = await triage_email(
                EmailTriageRequest(
                    email_id=email_id,
                    organization_id=request.organization_id,
                    user_email=request.user_email,
                ),
                audit=audit,
            )
            results["processed"] += 1
            if triage.get("action") != "skipped":
                results["labeled"] += 1
        except Exception:
            pass
    
    return results


def _pipeline_bucket_for_state(state: Any) -> str:
    normalized = str(state or "").strip().lower()
    if normalized in {"new", "received", "validated"}:
        return "new"
    if normalized in {"needs_info", "needs_approval", "pending_approval"}:
        return "pending_approval"
    if normalized in {"approved", "ready_to_post"}:
        return "approved"
    if normalized in {"posted", "posted_to_erp", "closed"}:
        return "posted"
    if normalized in {"rejected"}:
        return "rejected"
    return "pending_approval"


def _build_extension_pipeline(db, organization_id: str, limit: int = 1000) -> Dict[str, List[Dict[str, Any]]]:
    items = db.list_ap_items(organization_id, limit=limit, prioritized=True)
    groups: Dict[str, List[Dict[str, Any]]] = {
        "new": [],
        "pending_approval": [],
        "approved": [],
        "posted": [],
        "rejected": [],
    }
    for item in items:
        normalized = build_worklist_item(db, item)
        bucket = _pipeline_bucket_for_state(normalized.get("state"))
        groups.setdefault(bucket, []).append(normalized)
    return groups


@router.get("/pipeline")
def get_invoice_pipeline(organization_id: Optional[str] = None):
    """Return invoice pipeline grouped by status for Gmail extension.

    This legacy endpoint is kept for compatibility and now mirrors the
    normalized exception taxonomy used by `/extension/worklist`.
    """
    org_id = organization_id or "default"
    db = get_db()
    return _build_extension_pipeline(db, org_id)


@router.get("/worklist")
def get_extension_worklist(
    organization_id: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=1000),
):
    """Return invoice-centric worklist for the focused Gmail sidebar."""
    org_id = organization_id or "default"
    db = get_db()
    items = db.list_ap_items(org_id, limit=limit, prioritized=True)
    normalized = [build_worklist_item(db, item) for item in items]
    return {
        "organization_id": org_id,
        "items": normalized,
        "total": len(normalized),
    }


@router.post("/approve-and-post")
async def approve_and_post(
    request: ApproveAndPostRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    """
    DIFFERENTIATOR: Approve and post an invoice to ERP with HITL gate.
    
    This triggers the ApproveAndPostWorkflow which:
    1. HITL: Re-verifies confidence (blocks if <95%)
    2. Generates Clearledgr_Audit_ID
    3. Posts to ERP with audit ID in memo
    4. Updates Slack thread (multi-system routing)
    5. Updates Gmail label to Processed
    6. Records audit trail
    
    Use this for the "Approve & Post" button in the extension.
    """
    from clearledgr.workflows.gmail_activities import (
        verify_match_confidence_activity,
        post_to_erp_activity,
        update_slack_thread_activity,
        generate_audit_id,
    )
    from datetime import datetime, timezone
    
    payload = request.model_dump()
    
    if temporal_enabled():
        runtime = TemporalRuntime()
        result = await runtime.start_workflow(
            "ApproveAndPostWorkflow",
            payload,
            task_queue="clearledgr-gmail",
            wait=True,
            timeout_seconds=60,
        )
        return result
    
    # Inline execution with HITL gate
    
    # Step 1: HITL - Verify confidence
    if not request.override:
        confidence_result = await verify_match_confidence_activity({
            "extraction": request.extraction,
            "bank_match": request.bank_match or {},
            "erp_match": request.erp_match or {},
        })
        
        if not confidence_result.get("can_post", False):
            return {
                "email_id": request.email_id,
                "status": "blocked",
                "reason": f"Confidence {confidence_result.get('confidence_pct')}% below 95% threshold",
                "confidence": confidence_result.get("confidence_pct"),
                "mismatches": confidence_result.get("mismatches", []),
                "action_required": "review_mismatch",
            }
    else:
        confidence_result = {"confidence_pct": 0, "can_post": True}  # Override
    
    # Step 2: Generate Audit-Link ID
    timestamp = datetime.now(timezone.utc).isoformat()
    audit_id = generate_audit_id(request.email_id, request.organization_id, timestamp)
    
    # Step 3: Post to ERP with audit ID
    post_result = await post_to_erp_activity({
        "email_id": request.email_id,
        "extraction": request.extraction,
        "erp_match": request.erp_match or {},
        "confidence_result": confidence_result,
        "organization_id": request.organization_id,
        "approved_by": request.user_email,
    })
    
    if post_result.get("status") == "blocked":
        return post_result
    
    # Step 4: Multi-System - Update Slack thread
    await update_slack_thread_activity({
        "email_id": request.email_id,
        "vendor": request.extraction.get("vendor"),
        "amount": request.extraction.get("amount"),
        "currency": request.extraction.get("currency", "USD"),
        "invoice_number": request.extraction.get("invoice_number"),
        "clearledgr_audit_id": post_result.get("clearledgr_audit_id", audit_id),
        "erp_document": post_result.get("document_number"),
        "approved_by": request.user_email,
        "organization_id": request.organization_id,
    })
    
    # Step 5: Record audit trail
    audit.record_event(
        user_email=request.user_email or "extension",
        action="invoice_approved_and_posted",
        entity_type="invoice",
        entity_id=post_result.get("clearledgr_audit_id", audit_id),
        organization_id=request.organization_id,
        metadata={
            "email_id": request.email_id,
            "clearledgr_audit_id": post_result.get("clearledgr_audit_id", audit_id),
            "extraction": request.extraction,
            "confidence": confidence_result.get("confidence_pct"),
            "override": request.override,
            "post_result": post_result,
        },
    )
    
    return {
        "email_id": request.email_id,
        "status": "posted",
        "clearledgr_audit_id": post_result.get("clearledgr_audit_id", audit_id),
        "erp_document": post_result.get("document_number"),
        "confidence": confidence_result.get("confidence_pct"),
        "slack_updated": True,
        "post_result": post_result,
    }


@router.post("/verify-confidence")
async def verify_confidence(
    request: VerifyConfidenceRequest,
):
    """
    DIFFERENTIATOR: HITL - Verify match confidence and identify mismatches.
    
    Returns:
    - confidence: 0-100%
    - can_post: True if >= 95%
    - mismatches: List of specific discrepancies
    
    Use this to check if an invoice can be posted or needs review.
    """
    from clearledgr.workflows.gmail_activities import verify_match_confidence_activity
    
    result = await verify_match_confidence_activity({
        "extraction": request.extraction,
        "bank_match": request.bank_match or {},
        "erp_match": request.erp_match or {},
    })
    
    return {
        "email_id": request.email_id,
        **result,
    }


@router.post("/match-bank")
async def match_bank_feed(
    request: MatchBankRequest,
):
    """
    Match extracted data against bank feed.
    
    Returns bank transaction match if found.
    """
    from clearledgr.workflows.gmail_activities import match_bank_feed_activity
    
    return await match_bank_feed_activity({
        "extraction": request.extraction,
        "organization_id": request.organization_id,
    })


@router.post("/match-erp")
async def match_erp(
    request: MatchERPRequest,
):
    """
    Match extracted data against ERP records (PO, vendor).
    
    Returns PO match, vendor match, and GL code suggestion.
    """
    from clearledgr.workflows.gmail_activities import match_erp_activity
    
    return await match_erp_activity({
        "extraction": request.extraction,
        "organization_id": request.organization_id,
    })


@router.post("/escalate")
async def escalate_to_manager(
    request: EscalateRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    """
    DIFFERENTIATOR: Multi-System Routing - Escalate to manager via Slack.
    
    Sends mismatch details to Slack for manager review.
    """
    from clearledgr.workflows.gmail_activities import send_slack_notification_activity
    
    # Build escalation message
    mismatch_text = "\n".join([f"• {m.get('message', str(m))}" for m in request.mismatches[:5]])
    
    amount_text = f"{request.currency} {request.amount:,.2f}" if isinstance(request.amount, (int, float)) else "Unknown"
    message = request.message or (
        f"*Invoice Review Required*\n\n"
        f"*Vendor:* {request.vendor or 'Unknown'}\n"
        f"*Amount:* {amount_text}\n"
        f"*Confidence:* {request.confidence or 0}%\n\n"
        f"*Issues:*\n{mismatch_text}"
    )
    
    result = await send_slack_notification_activity({
        "type": "escalation",
        "email_id": request.email_id,
        "classification": {"type": "INVOICE"},
        "extraction": {
            "vendor": request.vendor,
            "amount": request.amount,
            "currency": request.currency,
        },
        "confidence_result": {
            "confidence_pct": request.confidence,
            "mismatches": request.mismatches,
            "requires_review": True,
        },
        "organization_id": request.organization_id,
    })
    
    # Record escalation in audit trail
    audit.record_event(
        user_email=request.user_email or "extension",
        action="invoice_escalated",
        entity_type="invoice",
        entity_id=request.email_id,
        organization_id=request.organization_id,
        metadata={
            "vendor": request.vendor,
            "amount": request.amount,
            "confidence": request.confidence,
            "mismatches": request.mismatches,
            "channel": request.channel,
        },
    )
    
    return {
        "email_id": request.email_id,
        "status": "escalated",
        "channel": request.channel,
        "message": message,
    }


class SubmitForApprovalRequest(BaseModel):
    """Request to submit invoice for Slack approval with intelligence."""
    email_id: str
    subject: str
    sender: str
    vendor: str
    amount: float
    currency: str = "USD"
    invoice_number: Optional[str] = None
    due_date: Optional[str] = None
    po_number: Optional[str] = None
    confidence: float = 0.0
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    slack_channel: Optional[str] = None
    email_body: Optional[str] = None  # For discount detection
    # Intelligence data (from triage)
    vendor_intelligence: Optional[Dict[str, Any]] = None
    policy_compliance: Optional[Dict[str, Any]] = None
    priority: Optional[Dict[str, Any]] = None
    budget_impact: Optional[List[Dict[str, Any]]] = None
    potential_duplicates: int = 0
    insights: Optional[List[Dict[str, Any]]] = None
    # Agent reasoning + decision payload
    agent_decision: Optional[Dict[str, Any]] = None
    agent_confidence: Optional[float] = None
    reasoning_summary: Optional[str] = None
    reasoning_factors: Optional[List[Dict[str, Any]]] = None
    reasoning_risks: Optional[List[str]] = None


class RejectInvoiceRequest(BaseModel):
    """Request to reject an invoice from Gmail sidebar."""
    email_id: str
    reason: str
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class BudgetDecisionRequest(BaseModel):
    """Budget decision from Gmail/embedded approval surfaces."""
    email_id: str
    decision: str  # approve_override | request_budget_adjustment | reject
    justification: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


@router.post("/submit-for-approval")
async def submit_for_approval(
    request: SubmitForApprovalRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    """
    Submit an invoice for Slack approval with full intelligence.
    
    This is the main entry point for the Gmail → Slack → ERP flow.
    
    Behavior:
    - If confidence >= 95%, auto-approves and posts to ERP
    - If confidence < 95%, sends to Slack for manager approval
    - Shows vendor intelligence, policy requirements, budget impact in Slack
    
    Use this when an invoice is detected and ready for processing.
    """
    from clearledgr.services.invoice_workflow import InvoiceData, get_invoice_workflow
    
    org_id = request.organization_id or "default"
    
    # If intelligence not provided, generate it now
    vendor_intel = request.vendor_intelligence
    policy_result = request.policy_compliance
    priority_data = request.priority
    budget_checks = request.budget_impact
    
    if not vendor_intel:
        vi = get_vendor_intelligence()
        vendor_intel = vi.get_suggestion(request.vendor)
    
    if not policy_result:
        ps = get_policy_compliance(org_id)
        policy_check = ps.check({
            "vendor": request.vendor,
            "amount": request.amount,
            "vendor_intelligence": vendor_intel or {},
        })
        policy_result = policy_check.to_dict()
    
    if not priority_data:
        pd = get_priority_detection(org_id)
        priority = pd.assess({
            "id": request.email_id,
            "vendor": request.vendor,
            "amount": request.amount,
            "due_date": request.due_date,
        })
        priority_data = priority.to_dict()
    
    if not budget_checks:
        bs = get_budget_awareness(org_id)
        checks = bs.check_invoice({
            "vendor": request.vendor,
            "amount": request.amount,
            "vendor_intelligence": vendor_intel or {},
        })
        budget_checks = [c.to_dict() for c in checks] if checks else None
    
    # Log to audit trail
    trail = get_audit_trail(org_id)
    trail.log(
        invoice_id=request.email_id,
        event_type=AuditEventType.ROUTED,
        summary=f"Submitting for approval - Priority: {priority_data.get('priority_label', 'N/A')}",
        details={
            "policy_compliant": policy_result.get("compliant", True) if policy_result else True,
            "required_approvers": policy_result.get("required_approvers", []) if policy_result else [],
        },
        vendor=request.vendor,
        amount=request.amount,
    )
    
    agent_decision = request.agent_decision or {}
    agent_confidence = request.agent_confidence
    if agent_confidence is None:
        agent_confidence = agent_decision.get("confidence")

    reasoning_block = agent_decision.get("reasoning") or {}
    reasoning_summary = request.reasoning_summary or reasoning_block.get("summary")
    reasoning_factors = request.reasoning_factors or reasoning_block.get("factors")
    reasoning_risks = request.reasoning_risks or reasoning_block.get("risks")

    invoice = InvoiceData(
        gmail_id=request.email_id,
        subject=request.subject,
        sender=request.sender,
        vendor_name=request.vendor,
        amount=request.amount,
        currency=request.currency,
        invoice_number=request.invoice_number,
        due_date=request.due_date,
        po_number=request.po_number,
        confidence=request.confidence,
        organization_id=request.organization_id,
        user_id=request.user_email,
        invoice_text=request.email_body or f"{request.subject}\n{request.vendor}",  # For discount detection
        # Pass intelligence to workflow
        vendor_intelligence=vendor_intel,
        policy_compliance=policy_result,
        priority=priority_data,
        budget_impact=budget_checks,
        potential_duplicates=request.potential_duplicates,
        insights=request.insights,
        reasoning_summary=reasoning_summary,
        reasoning_factors=reasoning_factors,
        reasoning_risks=reasoning_risks,
    )
    
    workflow = get_invoice_workflow(
        organization_id=org_id,
        slack_channel=request.slack_channel,
    )
    
    # Respect agent decision when present
    decision = agent_decision.get("decision")
    if agent_confidence is not None:
        try:
            invoice.confidence = max(float(invoice.confidence), float(agent_confidence))
        except Exception:
            pass

    if decision and decision != "auto_approve":
        # Force human review path (even if confidence is high)
        invoice.confidence = min(invoice.confidence, workflow.auto_approve_threshold - 0.01)
    elif decision == "auto_approve":
        # Ensure auto-approve threshold is met
        invoice.confidence = max(invoice.confidence, workflow.auto_approve_threshold)

    result = await workflow.process_new_invoice(invoice)
    
    # Log result
    trail.log(
        invoice_id=request.email_id,
        event_type=AuditEventType.APPROVAL_REQUESTED if result.get("status") == "pending_approval" else AuditEventType.AUTO_APPROVED,
        summary=f"Status: {result.get('status')}",
    )
    
    # Legacy audit
    audit.record_event(
        user_email=request.user_email or "extension",
        action="invoice_submitted",
        entity_type="invoice",
        entity_id=request.email_id,
        organization_id=request.organization_id,
        metadata={
            "vendor": request.vendor,
            "amount": request.amount,
            "confidence": request.confidence,
            "result_status": result.get("status"),
            "policy_compliant": policy_result.get("compliant", True) if policy_result else True,
            "priority": priority_data.get("priority") if priority_data else None,
        },
    )
    
    return result


@router.post("/reject-invoice")
async def reject_invoice(
    request: RejectInvoiceRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    """Reject an invoice and keep pipeline state in sync."""
    from clearledgr.services.invoice_workflow import get_invoice_workflow

    org_id = request.organization_id or "default"
    rejected_by = request.user_email or "extension"
    workflow = get_invoice_workflow(org_id)
    result = await workflow.reject_invoice(
        gmail_id=request.email_id,
        reason=request.reason,
        rejected_by=rejected_by,
    )

    audit.record_event(
        user_email=rejected_by,
        action="invoice_rejected",
        entity_type="invoice",
        entity_id=request.email_id,
        organization_id=org_id,
        metadata={"reason": request.reason, "result": result},
    )

    if result.get("status") != "rejected":
        raise HTTPException(status_code=400, detail=result.get("reason", "Reject failed"))
    return result


@router.post("/budget-decision")
async def budget_decision(
    request: BudgetDecisionRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    """Handle explicit budget decisions from Gmail sidebar surfaces."""
    from clearledgr.services.invoice_workflow import get_invoice_workflow

    org_id = request.organization_id or "default"
    actor = request.user_email or "extension"
    workflow = get_invoice_workflow(org_id)
    decision = str(request.decision or "").strip().lower()

    if decision == "approve_override":
        if not str(request.justification or "").strip():
            raise HTTPException(status_code=400, detail="justification_required")
        result = await workflow.approve_invoice(
            gmail_id=request.email_id,
            approved_by=actor,
            allow_budget_override=True,
            override_justification=request.justification,
        )
        if result.get("status") not in {"approved", "error"}:
            raise HTTPException(status_code=400, detail=result.get("reason", "budget_override_failed"))
    elif decision == "request_budget_adjustment":
        result = await workflow.request_budget_adjustment(
            gmail_id=request.email_id,
            requested_by=actor,
            reason=request.justification or "budget_adjustment_requested_in_gmail",
        )
    elif decision == "reject":
        reason = request.justification or "rejected_over_budget_in_gmail"
        result = await workflow.reject_invoice(
            gmail_id=request.email_id,
            reason=reason,
            rejected_by=actor,
        )
    else:
        raise HTTPException(status_code=400, detail="invalid_budget_decision")

    audit.record_event(
        user_email=actor,
        action="budget_decision",
        entity_type="invoice",
        entity_id=request.email_id,
        organization_id=org_id,
        metadata={
            "decision": decision,
            "justification": request.justification,
            "result": result,
        },
    )
    return result


@router.get("/invoice-status/{gmail_id}")
async def get_invoice_status(gmail_id: str):
    """
    Get the current status of an invoice.
    
    Returns: new, pending_approval, approved, posted, rejected
    """
    from clearledgr.core.database import get_db
    
    db = get_db()
    status = db.get_invoice_status(gmail_id)
    
    if not status:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    return status


@router.get("/invoice-pipeline/{organization_id}")
async def get_invoice_pipeline_status(organization_id: str):
    """
    Get all invoices grouped by status (pipeline view).
    
    Returns invoices grouped into: new, pending_approval, approved, posted, rejected
    """
    from clearledgr.core.database import get_db
    
    db = get_db()
    pipeline = _build_extension_pipeline(db, organization_id)
    
    return {
        "organization_id": organization_id,
        "pipeline": pipeline,
        "counts": {status: len(invoices) for status, invoices in pipeline.items()},
    }


@router.get("/workflow/{workflow_id}")
async def get_workflow_status(workflow_id: str):
    """
    Get the status of a running workflow.
    
    Use this to poll for completion of async workflows.
    """
    if not temporal_enabled():
        raise HTTPException(status_code=400, detail="Temporal not enabled")
    
    runtime = TemporalRuntime()
    return await runtime.get_status(workflow_id)


@router.get("/health")
def extension_health():
    """Health check for extension API."""
    return {
        "status": "ok",
        "temporal_enabled": temporal_enabled(),
        "service": "clearledgr-gmail-extension",
        "differentiators": [
            "audit_link_generation",
            "human_in_the_loop",
            "multi_system_routing",
        ],
    }


# ==================== AI SUGGESTIONS FOR FORMS ====================
# These endpoints expose AI suggestions to pre-fill forms (human confirms)

class GLSuggestionRequest(BaseModel):
    """Request for GL code suggestion."""
    vendor_name: str
    amount: Optional[float] = None
    description: Optional[str] = None
    organization_id: Optional[str] = "default"


class VendorSuggestionRequest(BaseModel):
    """Request for vendor suggestion from email context."""
    sender_email: Optional[str] = None
    sender_name: Optional[str] = None
    subject: Optional[str] = None
    extracted_vendor: Optional[str] = None
    organization_id: Optional[str] = "default"


@router.post("/suggestions/gl-code")
async def suggest_gl_code(request: GLSuggestionRequest):
    """
    Get AI-suggested GL code for a vendor.
    
    Returns primary suggestion + alternatives with confidence scores.
    Human reviews and confirms/changes.
    """
    from clearledgr.services.learning import get_learning_service
    from clearledgr.services.vendor_intelligence import get_vendor_intelligence
    
    learning = get_learning_service(request.organization_id)
    vendor_intel = get_vendor_intelligence()
    
    # Get suggestion from learning service (based on historical patterns)
    learned = learning.suggest_gl_code(request.vendor_name)
    
    # Get suggestion from vendor intelligence (known vendor profiles)
    vendor_profile = vendor_intel.get_suggestion(request.vendor_name)
    
    # Combine suggestions
    suggestions = []
    
    # Primary suggestion from learning (historical data)
    if learned and learned.get("gl_code"):
        suggestions.append({
            "gl_code": learned["gl_code"],
            "gl_name": learned.get("gl_description", ""),
            "confidence": learned.get("confidence", 0.5),
            "source": "learning",
            "reason": f"Used {learned.get('occurrence_count', 0)} times for this vendor",
        })
    
    # Suggestion from vendor intelligence (known profiles)
    if vendor_profile and vendor_profile.get("suggested_gl"):
        # Don't duplicate if same as learned
        if not suggestions or suggestions[0]["gl_code"] != vendor_profile["suggested_gl"]:
            suggestions.append({
                "gl_code": vendor_profile["suggested_gl"],
                "gl_name": vendor_profile.get("gl_description", ""),
                "confidence": 0.7 if vendor_profile.get("known_vendor") else 0.4,
                "source": "vendor_profile",
                "reason": f"Typical for {vendor_profile.get('category', 'this vendor type')}",
            })
    
    # Add alternatives from learning service
    if learned and learned.get("alternatives"):
        for alt in learned["alternatives"][:2]:  # Max 2 alternatives
            if not any(s["gl_code"] == alt["gl_code"] for s in suggestions):
                suggestions.append({
                    "gl_code": alt["gl_code"],
                    "gl_name": alt.get("gl_description", ""),
                    "confidence": alt.get("confidence", 0.3),
                    "source": "alternative",
                    "reason": "Also used for similar vendors",
                })
    
    # Sort by confidence
    suggestions.sort(key=lambda x: x["confidence"], reverse=True)
    
    return {
        "vendor_name": request.vendor_name,
        "primary": suggestions[0] if suggestions else None,
        "alternatives": suggestions[1:3] if len(suggestions) > 1 else [],
        "has_suggestion": len(suggestions) > 0,
    }


@router.post("/suggestions/vendor")
async def suggest_vendor(request: VendorSuggestionRequest):
    """
    Get AI-suggested vendor match from email context.
    
    Returns matched vendor + confidence for human confirmation.
    """
    from clearledgr.services.fuzzy_matching import get_fuzzy_matcher
    from clearledgr.services.vendor_management import get_vendor_management_service
    
    matcher = get_fuzzy_matcher()
    vendor_service = get_vendor_management_service(request.organization_id)
    
    # Try to match from extracted vendor name first
    candidates = []
    
    if request.extracted_vendor:
        # Direct match from extraction
        match = matcher.find_best_vendor_match(
            request.extracted_vendor,
            vendor_service.get_all_vendors()
        )
        if match and match.get("score", 0) > 0.6:
            candidates.append({
                "vendor_id": match.get("vendor_id"),
                "vendor_name": match.get("vendor_name"),
                "confidence": match.get("score", 0.7),
                "source": "extraction",
                "matched_from": request.extracted_vendor,
            })
    
    if request.sender_email:
        # Match from sender email domain
        domain = request.sender_email.split("@")[-1] if "@" in request.sender_email else None
        if domain:
            domain_match = matcher.find_vendor_by_domain(
                domain,
                vendor_service.get_all_vendors()
            )
            if domain_match and not any(c["vendor_id"] == domain_match.get("vendor_id") for c in candidates):
                candidates.append({
                    "vendor_id": domain_match.get("vendor_id"),
                    "vendor_name": domain_match.get("vendor_name"),
                    "confidence": domain_match.get("score", 0.6),
                    "source": "email_domain",
                    "matched_from": domain,
                })
    
    # Sort by confidence
    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    
    return {
        "extracted_vendor": request.extracted_vendor,
        "primary": candidates[0] if candidates else None,
        "alternatives": candidates[1:3] if len(candidates) > 1 else [],
        "has_suggestion": len(candidates) > 0,
        "is_new_vendor": len(candidates) == 0,
    }


@router.post("/suggestions/amount-validation")
async def validate_amount(
    vendor_name: str = Body(...),
    amount: float = Body(...),
    organization_id: str = Body("default"),
):
    """
    Validate invoice amount against vendor history.
    
    Returns whether amount seems reasonable + expected range.
    """
    vendor_intel = get_vendor_intelligence()
    
    validation = vendor_intel.validate_amount(vendor_name, amount)
    
    return {
        "vendor_name": vendor_name,
        "amount": amount,
        "is_reasonable": validation.get("seems_reasonable", True),
        "expected_range": validation.get("expected_range"),
        "concern": validation.get("concern"),
        "message": validation.get("message"),
    }


@router.get("/suggestions/form-prefill/{email_id}")
async def get_form_prefill(
    email_id: str,
    organization_id: str = "default",
):
    """
    Get all AI suggestions to pre-fill a form for an invoice.
    
    Combines vendor match, GL suggestion, and amount validation.
    Returns everything needed to pre-fill invoice forms.
    """
    from clearledgr.core.database import get_db
    from clearledgr.services.learning import get_learning_service
    from clearledgr.services.vendor_intelligence import get_vendor_intelligence
    
    db = get_db()
    learning = get_learning_service(organization_id)
    vendor_intel = get_vendor_intelligence()
    
    # Get stored extraction data for this email
    invoice = db.get_invoice_by_email_id(email_id)
    
    if not invoice:
        return {
            "email_id": email_id,
            "has_data": False,
            "message": "No extraction data found for this email",
        }
    
    vendor_name = invoice.get("vendor") or invoice.get("vendor_name", "")
    amount = invoice.get("amount", 0)
    
    # Get GL suggestion
    gl_suggestion = learning.suggest_gl_code(vendor_name) if vendor_name else None
    vendor_profile = vendor_intel.get_suggestion(vendor_name) if vendor_name else None
    
    # Get amount validation
    amount_validation = vendor_intel.validate_amount(vendor_name, amount) if vendor_name and amount else None
    
    return {
        "email_id": email_id,
        "has_data": True,
        "prefill": {
            "vendor": {
                "name": vendor_name,
                "confidence": invoice.get("confidence", 0.5),
            },
            "amount": {
                "value": amount,
                "is_reasonable": amount_validation.get("seems_reasonable", True) if amount_validation else True,
                "expected_range": amount_validation.get("expected_range") if amount_validation else None,
                "concern": amount_validation.get("concern") if amount_validation else None,
            },
            "gl_code": {
                "suggested": gl_suggestion.get("gl_code") if gl_suggestion else (vendor_profile.get("suggested_gl") if vendor_profile else None),
                "name": gl_suggestion.get("gl_description") if gl_suggestion else (vendor_profile.get("gl_description") if vendor_profile else None),
                "confidence": gl_suggestion.get("confidence", 0.5) if gl_suggestion else 0.4,
                "source": "learning" if gl_suggestion else ("vendor_profile" if vendor_profile else None),
            },
            "invoice_number": invoice.get("invoice_number"),
            "invoice_date": invoice.get("invoice_date"),
            "due_date": invoice.get("due_date"),
        },
    }
