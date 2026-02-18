"""
Invoice Workflow Service

Orchestrates the complete invoice lifecycle:
Gmail Detection → Data Extraction → Slack Approval → ERP Posting

This is the heart of "Streak for Finance" - bringing AP workflow into the tools
finance teams already use.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from dataclasses import dataclass

from clearledgr.core.database import get_db
from clearledgr.services.slack_api import SlackAPIClient, get_slack_client
try:
    from clearledgr.services.teams_api import TeamsAPIClient
except Exception:  # pragma: no cover - optional integration in some local builds
    TeamsAPIClient = None  # type: ignore[assignment]
from clearledgr.services.policy_compliance import get_policy_compliance
from clearledgr.services.budget_awareness import get_budget_awareness
from clearledgr.services.purchase_orders import get_purchase_order_service
from clearledgr.integrations.erp_router import (
    Bill, Vendor, get_or_create_vendor
)
from clearledgr.services.erp_api_first import post_bill_api_first
from clearledgr.services.learning import get_learning_service

logger = logging.getLogger(__name__)


@dataclass
class InvoiceData:
    """Extracted invoice data from email."""
    gmail_id: str
    subject: str
    sender: str
    vendor_name: str
    amount: float
    currency: str = "USD"
    invoice_number: Optional[str] = None
    due_date: Optional[str] = None
    po_number: Optional[str] = None
    confidence: float = 0.0
    attachment_url: Optional[str] = None
    organization_id: Optional[str] = None
    user_id: Optional[str] = None
    # Raw invoice text for discount detection
    invoice_text: Optional[str] = None
    # Agent reasoning (added 2026-01-23)
    reasoning_summary: Optional[str] = None
    reasoning_factors: Optional[list] = None
    reasoning_risks: Optional[list] = None
    # Full intelligence (added 2026-01-23)
    vendor_intelligence: Optional[Dict] = None
    policy_compliance: Optional[Dict] = None
    priority: Optional[Dict] = None
    budget_impact: Optional[list] = None
    po_match_result: Optional[Dict[str, Any]] = None
    budget_check_result: Optional[Dict[str, Any]] = None
    potential_duplicates: int = 0
    insights: Optional[list] = None


class InvoiceWorkflowService:
    """
    Manages the complete invoice workflow.
    
    Usage:
        service = InvoiceWorkflowService(organization_id="acme")
        
        # When invoice detected in Gmail
        result = await service.process_new_invoice(invoice_data)
        
        # When approved in Slack
        result = await service.approve_invoice(gmail_id, approved_by="user@acme.com")
        
        # When rejected in Slack
        result = await service.reject_invoice(gmail_id, reason="Duplicate", rejected_by="user@acme.com")
    """
    
    def __init__(
        self,
        organization_id: str,
        slack_channel: Optional[str] = None,
        auto_approve_threshold: float = 0.95,
    ):
        self.organization_id = organization_id
        self._slack_channel = slack_channel
        self._auto_approve_threshold = auto_approve_threshold
        self.db = get_db()
        self._slack_client: Optional[SlackAPIClient] = None
        self._teams_client: Optional[Any] = None
        self._settings_loaded = False
        self._settings: Optional[Dict] = None
    
    def _load_settings(self):
        """Load organization settings if not already loaded."""
        if self._settings_loaded:
            return
        
        try:
            org = self.db.get_organization(self.organization_id)
            if org:
                settings = org.get("settings", {})
                if isinstance(settings, str):
                    import json
                    settings = json.loads(settings) if settings else {}
                self._settings = settings
        except:
            self._settings = {}
        
        self._settings_loaded = True
    
    @property
    def slack_channel(self) -> str:
        """Get Slack channel, using settings if available."""
        if self._slack_channel:
            return self._slack_channel
        
        self._load_settings()
        if self._settings:
            channels = self._settings.get("slack_channels", {})
            return channels.get("invoices", "#finance-approvals")
        
        return "#finance-approvals"
    
    @property
    def auto_approve_threshold(self) -> float:
        """Get auto-approve threshold from settings."""
        self._load_settings()
        if self._settings:
            return self._settings.get("auto_approve_threshold", self._auto_approve_threshold)
        return self._auto_approve_threshold
    
    def get_approval_channel_for_amount(self, amount: float) -> str:
        """Get appropriate Slack channel based on amount thresholds."""
        self._load_settings()
        
        if not self._settings:
            return self.slack_channel
        
        thresholds = self._settings.get("approval_thresholds", [])
        
        for threshold in thresholds:
            min_amt = threshold.get("min_amount", 0)
            max_amt = threshold.get("max_amount")
            
            if amount >= min_amt and (max_amt is None or amount < max_amt):
                return threshold.get("approver_channel", self.slack_channel)
        
        return self.slack_channel
    
    @property
    def slack_client(self) -> SlackAPIClient:
        """Lazy-load Slack client."""
        if self._slack_client is None:
            self._slack_client = get_slack_client()
        return self._slack_client

    @property
    def teams_client(self) -> Optional[Any]:
        """Lazy-load Teams client."""
        if TeamsAPIClient is None:
            return None
        if self._teams_client is None:
            self._teams_client = TeamsAPIClient.from_env()
        return self._teams_client

    @staticmethod
    def _budget_status_rank(status: str) -> int:
        value = str(status or "").strip().lower()
        if value == "exceeded":
            return 4
        if value == "critical":
            return 3
        if value == "warning":
            return 2
        if value == "healthy":
            return 1
        return 0

    def _normalize_budget_checks(self, raw: Any) -> List[Dict[str, Any]]:
        if isinstance(raw, list):
            return [entry for entry in raw if isinstance(entry, dict)]
        if isinstance(raw, dict):
            for key in ("checks", "budgets", "budget_impact"):
                nested = raw.get(key)
                if isinstance(nested, list):
                    return [entry for entry in nested if isinstance(entry, dict)]
            if raw.get("budget_name") or raw.get("after_approval_status"):
                return [raw]
        return []

    def _compute_budget_summary(self, budget_checks: List[Dict[str, Any]]) -> Dict[str, Any]:
        summary = {
            "status": "healthy",
            "requires_decision": False,
            "critical_count": 0,
            "exceeded_count": 0,
            "warning_count": 0,
            "checks": budget_checks,
        }
        highest_rank = 0
        highest_status = "healthy"
        for check in budget_checks:
            status = str(check.get("after_approval_status") or check.get("status") or "healthy").lower()
            rank = self._budget_status_rank(status)
            if rank > highest_rank:
                highest_rank = rank
                highest_status = status
            if status == "critical":
                summary["critical_count"] += 1
            elif status == "exceeded":
                summary["exceeded_count"] += 1
            elif status == "warning":
                summary["warning_count"] += 1

        summary["status"] = highest_status
        summary["requires_decision"] = highest_status in {"critical", "exceeded"}
        return summary

    def _get_invoice_budget_checks(self, invoice: InvoiceData) -> List[Dict[str, Any]]:
        checks = self._normalize_budget_checks(invoice.budget_impact)
        if checks:
            return checks
        try:
            budget_service = get_budget_awareness(self.organization_id)
            computed = budget_service.check_invoice(
                {
                    "vendor": invoice.vendor_name,
                    "amount": invoice.amount,
                    "vendor_intelligence": invoice.vendor_intelligence or {},
                }
            )
            checks = [entry.to_dict() for entry in computed] if computed else []
        except Exception as exc:
            logger.warning("Failed to evaluate budget impact for invoice %s: %s", invoice.gmail_id, exc)
            checks = []
        invoice.budget_impact = checks or None
        return checks

    def _lookup_ap_item_id(
        self,
        gmail_id: str,
        vendor_name: Optional[str] = None,
        invoice_number: Optional[str] = None,
    ) -> Optional[str]:
        try:
            if hasattr(self.db, "get_ap_item_by_thread"):
                by_thread = self.db.get_ap_item_by_thread(self.organization_id, gmail_id)
                if by_thread and by_thread.get("id"):
                    return str(by_thread["id"])
            if vendor_name and invoice_number and hasattr(self.db, "get_ap_item_by_vendor_invoice"):
                by_vendor_invoice = self.db.get_ap_item_by_vendor_invoice(
                    self.organization_id,
                    vendor_name,
                    invoice_number,
                )
                if by_vendor_invoice and by_vendor_invoice.get("id"):
                    return str(by_vendor_invoice["id"])
        except Exception:
            return None
        return None

    def _record_approval_snapshot(
        self,
        *,
        ap_item_id: Optional[str],
        gmail_id: str,
        channel_id: Optional[str],
        message_ts: Optional[str],
        source_channel: str = "slack",
        source_message_ref: Optional[str] = None,
        status: str,
        decision_payload: Optional[Dict[str, Any]] = None,
        approved_by: Optional[str] = None,
        approved_at: Optional[str] = None,
        rejected_by: Optional[str] = None,
        rejected_at: Optional[str] = None,
        rejection_reason: Optional[str] = None,
    ) -> None:
        if not ap_item_id or not hasattr(self.db, "save_approval"):
            return
        try:
            self.db.save_approval(
                {
                    "ap_item_id": ap_item_id,
                    "channel_id": channel_id or source_channel,
                    "message_ts": message_ts or source_message_ref or gmail_id,
                    "source_channel": source_channel,
                    "source_message_ref": source_message_ref or gmail_id,
                    "decision_payload": decision_payload or {},
                    "status": status,
                    "approved_by": approved_by,
                    "approved_at": approved_at,
                    "rejected_by": rejected_by,
                    "rejected_at": rejected_at,
                    "rejection_reason": rejection_reason,
                    "organization_id": self.organization_id,
                }
            )
        except Exception as exc:
            logger.debug("Could not save approval snapshot for %s: %s", gmail_id, exc)

    def _update_ap_item_metadata(self, ap_item_id: Optional[str], updates: Dict[str, Any]) -> None:
        """Best-effort metadata merge for AP item side-channel context."""
        if not ap_item_id:
            return
        try:
            row = self.db.get_ap_item(ap_item_id) if hasattr(self.db, "get_ap_item") else None
            if not row:
                return
            metadata_raw = row.get("metadata")
            if isinstance(metadata_raw, dict):
                metadata = dict(metadata_raw)
            elif isinstance(metadata_raw, str) and metadata_raw.strip():
                metadata = json.loads(metadata_raw)
            else:
                metadata = {}
            metadata.update(updates or {})
            self.db.update_ap_item(ap_item_id, metadata=metadata)
        except Exception as exc:
            logger.debug("Could not update AP metadata for %s: %s", ap_item_id, exc)

    def _load_budget_context_from_invoice_row(
        self,
        invoice_row: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        metadata = {}
        try:
            raw_meta = invoice_row.get("metadata")
            if isinstance(raw_meta, dict):
                metadata = raw_meta
            elif isinstance(raw_meta, str) and raw_meta.strip():
                metadata = json.loads(raw_meta)
        except Exception:
            metadata = {}

        checks = self._normalize_budget_checks(metadata.get("budget_impact"))
        if checks:
            return checks

        invoice = InvoiceData(
            gmail_id=str(invoice_row.get("gmail_id") or ""),
            subject=str(invoice_row.get("email_subject") or ""),
            sender=str(invoice_row.get("sender") or ""),
            vendor_name=str(invoice_row.get("vendor") or "Unknown"),
            amount=float(invoice_row.get("amount") or 0),
            currency=str(invoice_row.get("currency") or "USD"),
            invoice_number=invoice_row.get("invoice_number"),
            due_date=invoice_row.get("due_date"),
            organization_id=self.organization_id,
            budget_impact=None,
            vendor_intelligence=metadata.get("vendor_intelligence")
            if isinstance(metadata.get("vendor_intelligence"), dict)
            else {},
        )
        return self._get_invoice_budget_checks(invoice)

    def _evaluate_deterministic_validation(self, invoice: InvoiceData) -> Dict[str, Any]:
        """
        Apply deterministic pre-routing controls before confidence/agent-based routing.

        A failed gate forces human approval with reason codes.
        """
        checked_at = datetime.now(timezone.utc).isoformat()
        reason_codes: List[str] = []
        reasons: List[Dict[str, Any]] = []

        def add_reason(
            code: str,
            message: str,
            severity: str = "warning",
            details: Optional[Dict[str, Any]] = None,
        ) -> None:
            code_text = str(code or "").strip().lower()
            if code_text and code_text not in reason_codes:
                reason_codes.append(code_text)
            reasons.append(
                {
                    "code": code_text,
                    "message": str(message or code_text or "validation_failure"),
                    "severity": str(severity or "warning").lower(),
                    "details": details or {},
                }
            )

        # 1) Policy checks (PO-required and any explicit blocking actions).
        policy_result = invoice.policy_compliance
        if not isinstance(policy_result, dict):
            try:
                policy_service = get_policy_compliance(self.organization_id)
                policy_result = policy_service.check(
                    {
                        "vendor": invoice.vendor_name,
                        "amount": invoice.amount,
                        "currency": invoice.currency,
                        "invoice_number": invoice.invoice_number,
                        "po_number": invoice.po_number,
                        "purchase_order": invoice.po_number,
                        "vendor_intelligence": invoice.vendor_intelligence or {},
                        "budget_impact": invoice.budget_impact or [],
                    }
                ).to_dict()
            except Exception as exc:
                logger.warning("Failed to evaluate policy compliance for deterministic gate: %s", exc)
                policy_result = {"compliant": True, "violations": []}
        invoice.policy_compliance = policy_result

        for violation in (policy_result or {}).get("violations", []) or []:
            if not isinstance(violation, dict):
                continue
            policy_id = str(violation.get("policy_id") or "").lower()
            message = str(violation.get("message") or "policy_requirement")
            action = str(violation.get("action") or "").lower()
            severity = str(violation.get("severity") or "warning").lower()
            message_l = message.lower()
            if action in {"require_approval", "require_multi_approval", "flag_for_review"}:
                add_reason(
                    f"policy_requirement_{policy_id or 'unnamed'}",
                    message,
                    severity=severity,
                    details=violation,
                )
            if policy_id == "po_required" or "po required" in message_l:
                add_reason("po_required_missing", message, severity=severity, details=violation)
            if action == "block":
                add_reason(
                    f"policy_block_{policy_id or 'unknown'}",
                    message,
                    severity="error",
                    details=violation,
                )

        # 2) PO/receipt matching (only when PO context is available).
        po_match_result: Optional[Dict[str, Any]] = (
            invoice.po_match_result if isinstance(invoice.po_match_result, dict) else None
        )
        if invoice.po_number and po_match_result is None:
            try:
                po_service = get_purchase_order_service(self.organization_id)
                match = po_service.match_invoice_to_po(
                    invoice_id=invoice.gmail_id,
                    invoice_amount=invoice.amount,
                    invoice_vendor=invoice.vendor_name,
                    invoice_po_number=invoice.po_number,
                    invoice_lines=None,
                )
                po_match_result = match.to_dict() if hasattr(match, "to_dict") else dict(match)
            except Exception as exc:
                add_reason(
                    "po_match_error",
                    f"PO/receipt matching failed: {exc}",
                    severity="error",
                )
        if po_match_result:
            invoice.po_match_result = po_match_result
            match_status = str(po_match_result.get("status") or "").lower()
            exceptions = po_match_result.get("exceptions") or []
            if exceptions:
                for match_exception in exceptions:
                    if not isinstance(match_exception, dict):
                        continue
                    ex_type = str(match_exception.get("type") or "unknown").lower()
                    ex_msg = str(match_exception.get("message") or f"PO match exception: {ex_type}")
                    ex_severity = str(match_exception.get("severity") or "warning").lower()
                    add_reason(
                        f"po_match_{ex_type}",
                        ex_msg,
                        severity=ex_severity,
                        details=match_exception,
                    )
            elif match_status in {"exception", "partial_match"}:
                add_reason(
                    f"po_match_{match_status}",
                    f"PO match status is {match_status}",
                    severity="warning",
                    details={"status": match_status},
                )

        # 3) Budget impact checks.
        budget_checks = self._get_invoice_budget_checks(invoice)
        budget_summary = self._compute_budget_summary(budget_checks)

        for budget in budget_checks:
            after_status = str(budget.get("after_approval_status") or "").lower()
            if after_status in {"critical", "exceeded"}:
                code = "budget_exceeded" if after_status == "exceeded" else "budget_critical"
                warning_message = budget.get("warning_message")
                default_message = (
                    f"Budget '{budget.get('budget_name', 'Unnamed')}' would be {after_status} after approval"
                )
                add_reason(
                    code,
                    str(warning_message or default_message),
                    severity="error" if after_status == "exceeded" else "warning",
                    details=budget,
                )

        gate = {
            "passed": len(reason_codes) == 0,
            "checked_at": checked_at,
            "reason_codes": reason_codes,
            "reasons": reasons,
            "policy_compliance": policy_result or {},
            "po_match_result": po_match_result,
            "budget_impact": budget_checks,
            "budget": budget_summary,
        }
        invoice.budget_check_result = {
            "checked_at": checked_at,
            "failed_checks": len(reason_codes),
            "reason_codes": reason_codes,
            "status": budget_summary.get("status"),
            "requires_decision": bool(budget_summary.get("requires_decision")),
            "budget_impact": budget_checks,
        }
        return gate

    def _record_validation_gate_failure(self, invoice: InvoiceData, gate: Dict[str, Any]) -> None:
        """
        Best-effort persistence for validation-gate failures.
        Keeps legacy flow tolerant of mixed DB capabilities.
        """
        reason_codes = gate.get("reason_codes") or []
        if not reason_codes:
            return

        reason_text = ",".join(str(code) for code in reason_codes)

        try:
            self.db.update_invoice_status(
                gmail_id=invoice.gmail_id,
                rejection_reason=f"deterministic_validation:{reason_text}",
            )
        except Exception:
            # Legacy status storage may not support rejection_reason updates at this stage.
            pass

        ap_item_id: Optional[str] = None
        try:
            if hasattr(self.db, "get_ap_item_by_thread"):
                by_thread = self.db.get_ap_item_by_thread(self.organization_id, invoice.gmail_id)
                if by_thread:
                    ap_item_id = str(by_thread.get("id") or "")
            if not ap_item_id and invoice.invoice_number and hasattr(self.db, "get_ap_item_by_vendor_invoice"):
                by_vendor_invoice = self.db.get_ap_item_by_vendor_invoice(
                    self.organization_id,
                    invoice.vendor_name,
                    invoice.invoice_number,
                )
                if by_vendor_invoice:
                    ap_item_id = str(by_vendor_invoice.get("id") or "")
            if ap_item_id and hasattr(self.db, "append_ap_audit_event"):
                self.db.append_ap_audit_event(
                    {
                        "ap_item_id": ap_item_id,
                        "event_type": "deterministic_validation_failed",
                        "actor_type": "system",
                        "actor_id": "invoice_workflow",
                        "reason": reason_text,
                        "metadata": {
                            "reason_codes": reason_codes,
                            "reasons": gate.get("reasons") or [],
                        },
                        "organization_id": self.organization_id,
                    }
                )
        except Exception as exc:
            logger.debug("Could not append deterministic validation audit event: %s", exc)
    
    async def process_new_invoice(self, invoice: InvoiceData) -> Dict[str, Any]:
        """
        Process a newly detected invoice email.
        
        Flow:
        1. Save invoice to database with 'new' status
        2. Check if recurring (subscription) - different handling
        3. If confidence >= threshold, auto-approve and post
        4. Otherwise, send to Slack for approval
        
        Returns:
            Dict with status, invoice_id, and action taken
        """
        existing = self.db.get_invoice_status(invoice.gmail_id)
        if existing:
            if existing.get("status") == "posted":
                return {
                    "status": "already_posted",
                    "invoice_id": invoice.gmail_id,
                    "erp_bill_id": existing.get("erp_bill_id"),
                }
            if existing.get("status") == "pending_approval" and existing.get("slack_thread_id"):
                thread = self.db.get_slack_thread(invoice.gmail_id)
                return {
                    "status": "pending_approval",
                    "invoice_id": invoice.gmail_id,
                    "slack_channel": thread.get("channel_id") if thread else None,
                    "slack_ts": thread.get("thread_ts") if thread else None,
                    "existing": True,
                }

        # Check for recurring pattern first
        from clearledgr.services.recurring_detection import get_recurring_detector
        
        recurring_detector = get_recurring_detector(self.organization_id)
        recurring_analysis = recurring_detector.analyze_invoice(
            vendor=invoice.vendor_name,
            amount=invoice.amount,
            currency=invoice.currency,
            sender_email=invoice.sender,
        )
        
        # Save invoice to database
        invoice_id = self.db.save_invoice_status(
            gmail_id=invoice.gmail_id,
            status="new",
            email_subject=invoice.subject,
            vendor=invoice.vendor_name,
            amount=invoice.amount,
            currency=invoice.currency,
            invoice_number=invoice.invoice_number,
            due_date=invoice.due_date,
            confidence=invoice.confidence,
            organization_id=self.organization_id,
            user_id=invoice.user_id,
        )
        
        logger.info(f"New invoice detected: {invoice.vendor_name} ${invoice.amount} (confidence: {invoice.confidence})")

        # Deterministic controls always run before confidence-based routing.
        validation_gate = self._evaluate_deterministic_validation(invoice)
        if not validation_gate.get("passed", True):
            self._record_validation_gate_failure(invoice, validation_gate)
            logger.info(
                "Routing invoice %s to approval due to deterministic controls: %s",
                invoice.gmail_id,
                ", ".join(validation_gate.get("reason_codes") or []),
            )
            result = await self._send_for_approval(
                invoice,
                extra_context={
                    "validation_gate": validation_gate,
                },
            )
            if isinstance(result, dict):
                result.setdefault("validation_gate", validation_gate)
                result.setdefault("reason_codes", validation_gate.get("reason_codes") or [])
            return result
        
        # LEARNING: Check if we have a learned GL code for this vendor
        suggested_gl = None
        try:
            learning = get_learning_service(self.organization_id)
            suggestion = learning.suggest_gl_code(
                vendor=invoice.vendor_name,
                amount=invoice.amount,
            )
            if suggestion and suggestion.get("confidence", 0) > 0.5:
                suggested_gl = suggestion
                logger.info(f"Learning suggested GL {suggestion.get('gl_code')} for {invoice.vendor_name} (confidence: {suggestion.get('confidence'):.2f})")
                
                # Boost confidence if we've seen this vendor before
                if suggestion.get("confidence", 0) > 0.8:
                    invoice.confidence = min(0.99, invoice.confidence + 0.1)
        except Exception as e:
            logger.warning(f"Failed to get GL suggestion from learning: {e}")
        
        # Handle recurring subscriptions specially
        if recurring_analysis.get("is_recurring"):
            if recurring_analysis.get("auto_approve"):
                logger.info(f"Auto-approving recurring invoice from {invoice.vendor_name}")
                return await self._auto_approve_and_post(
                    invoice, 
                    reason="recurring_match",
                    recurring_info=recurring_analysis.get("pattern")
                )
            elif recurring_analysis.get("alerts"):
                # Has alerts - send for review with context
                return await self._send_for_approval(
                    invoice, 
                    extra_context={
                        "recurring": True,
                        "alerts": recurring_analysis.get("alerts"),
                        "pattern": recurring_analysis.get("pattern"),
                    }
                )
        
        # Check if we should auto-approve based on confidence
        if invoice.confidence >= self.auto_approve_threshold:
            logger.info(f"Auto-approving invoice (confidence {invoice.confidence} >= {self.auto_approve_threshold})")
            return await self._auto_approve_and_post(invoice)
        
        # Send to Slack for approval
        return await self._send_for_approval(invoice)
    
    async def _auto_approve_and_post(
        self, 
        invoice: InvoiceData, 
        reason: str = "high_confidence",
        recurring_info: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Auto-approve invoice and post to ERP."""
        existing = self.db.get_invoice_status(invoice.gmail_id)
        if existing and existing.get("status") == "posted":
            return {
                "status": "already_posted",
                "invoice_id": invoice.gmail_id,
                "erp_bill_id": existing.get("erp_bill_id"),
            }

        # Update status
        self.db.update_invoice_status(
            gmail_id=invoice.gmail_id,
            status="approved",
            approved_by=f"clearledgr-auto:{reason}",
        )
        
        # Post to ERP
        result = await self._post_to_erp(invoice)
        
        if result.get("status") == "success":
            self.db.update_invoice_status(
                gmail_id=invoice.gmail_id,
                status="posted",
                erp_bill_id=result.get("bill_id"),
                erp_vendor_id=result.get("vendor_id"),
            )
            
            # LEARNING: Record auto-approval to learn vendor→GL mappings
            try:
                learning = get_learning_service(self.organization_id)
                learning.record_approval(
                    vendor=invoice.vendor_name,
                    gl_code=result.get("gl_code", ""),
                    gl_description=result.get("gl_description", "Accounts Payable"),
                    amount=invoice.amount,
                    currency=invoice.currency,
                    was_auto_approved=True,
                    was_corrected=False,
                )
                logger.info(f"Recorded auto-approval for learning: {invoice.vendor_name}")
            except Exception as e:
                logger.warning(f"Failed to record auto-approval for learning: {e}")
            
            # Notify in Slack (informational, not approval)
            try:
                await self._send_posted_notification(invoice, result, reason, recurring_info)
            except Exception as e:
                logger.warning(f"Failed to send Slack notification: {e}")
        
        return {
            "status": "auto_approved",
            "invoice_id": invoice.gmail_id,
            "reason": reason,
            "recurring": recurring_info,
            "erp_result": result,
        }
    
    async def _send_for_approval(
        self, 
        invoice: InvoiceData,
        extra_context: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Send invoice to Slack for approval."""
        budget_checks = self._get_invoice_budget_checks(invoice)
        budget_summary = self._compute_budget_summary(budget_checks)
        context_payload = dict(extra_context or {})
        if "budget" not in context_payload:
            context_payload["budget"] = budget_summary
        if "budget_impact" not in context_payload:
            context_payload["budget_impact"] = budget_checks
        context_payload["approval_context"] = self._build_approval_context(
            invoice=invoice,
            context_payload=context_payload,
        )
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=invoice.gmail_id,
            vendor_name=invoice.vendor_name,
            invoice_number=invoice.invoice_number,
        )

        existing_thread = self.db.get_slack_thread(invoice.gmail_id)
        if existing_thread:
            # Ensure status is pending, but avoid duplicate Slack messages
            self.db.update_invoice_status(
                gmail_id=invoice.gmail_id,
                status="pending_approval",
                slack_thread_id=existing_thread.get("id"),
            )
            self._record_approval_snapshot(
                ap_item_id=ap_item_id,
                gmail_id=invoice.gmail_id,
                channel_id=existing_thread.get("channel_id"),
                message_ts=existing_thread.get("thread_ts"),
                source_channel="slack",
                source_message_ref=invoice.gmail_id,
                status="pending",
                decision_payload={
                    "budget": budget_summary,
                    "budget_impact": budget_checks,
                    "validation_gate": context_payload.get("validation_gate"),
                    "approval_context": context_payload.get("approval_context"),
                },
            )
            teams_status = self._send_teams_budget_card(invoice, budget_summary)
            if isinstance(teams_status, dict):
                teams_state = str(teams_status.get("status") or "unknown")
                self._update_ap_item_metadata(
                    ap_item_id,
                    {
                        "teams": {
                            "state": teams_state,
                            "channel": teams_status.get("channel_id"),
                            "message_id": teams_status.get("message_id"),
                            "reason": teams_status.get("reason"),
                        }
                    },
                )
                if teams_state == "sent":
                    self._record_approval_snapshot(
                        ap_item_id=ap_item_id,
                        gmail_id=invoice.gmail_id,
                        channel_id=str(teams_status.get("channel_id") or "teams"),
                        message_ts=str(teams_status.get("message_id") or invoice.gmail_id),
                        source_channel="teams",
                        source_message_ref=invoice.gmail_id,
                        status="pending",
                        decision_payload={
                            "budget": budget_summary,
                            "budget_impact": budget_checks,
                            "validation_gate": context_payload.get("validation_gate"),
                            "approval_context": context_payload.get("approval_context"),
                        },
                    )
            return {
                "status": "pending_approval",
                "invoice_id": invoice.gmail_id,
                "slack_channel": existing_thread.get("channel_id"),
                "slack_ts": existing_thread.get("thread_ts"),
                "existing": True,
                "budget": budget_summary,
                "teams": teams_status,
            }

        # Update status to pending
        self.db.update_invoice_status(
            gmail_id=invoice.gmail_id,
            status="pending_approval",
        )
        
        # Build approval message
        blocks = self._build_approval_blocks(invoice, context_payload)
        
        # Get appropriate channel based on amount
        approval_channel = self.get_approval_channel_for_amount(invoice.amount)
        
        try:
            # Send to Slack
            message = await self.slack_client.send_message(
                channel=approval_channel,
                text=f"Invoice approval needed: {invoice.vendor_name} - ${invoice.amount:,.2f}",
                blocks=blocks,
            )
            
            # Save Slack thread reference
            thread_id = self.db.save_slack_thread(
                invoice_id=invoice.gmail_id,
                channel_id=message.channel,
                thread_ts=message.ts,
                gmail_id=invoice.gmail_id,
                organization_id=self.organization_id,
            )
            
            # Update invoice with thread reference
            self.db.update_invoice_status(
                gmail_id=invoice.gmail_id,
                status="pending_approval",
                slack_thread_id=thread_id,
            )
            self._record_approval_snapshot(
                ap_item_id=ap_item_id,
                gmail_id=invoice.gmail_id,
                channel_id=message.channel,
                message_ts=message.ts,
                source_channel="slack",
                source_message_ref=invoice.gmail_id,
                status="pending",
                decision_payload={
                    "budget": budget_summary,
                    "budget_impact": budget_checks,
                    "validation_gate": context_payload.get("validation_gate"),
                    "approval_context": context_payload.get("approval_context"),
                },
            )
            teams_status = self._send_teams_budget_card(invoice, budget_summary)
            if isinstance(teams_status, dict):
                teams_state = str(teams_status.get("status") or "unknown")
                self._update_ap_item_metadata(
                    ap_item_id,
                    {
                        "teams": {
                            "state": teams_state,
                            "channel": teams_status.get("channel_id"),
                            "message_id": teams_status.get("message_id"),
                            "reason": teams_status.get("reason"),
                        }
                    },
                )
                if teams_state == "sent":
                    self._record_approval_snapshot(
                        ap_item_id=ap_item_id,
                        gmail_id=invoice.gmail_id,
                        channel_id=str(teams_status.get("channel_id") or "teams"),
                        message_ts=str(teams_status.get("message_id") or invoice.gmail_id),
                        source_channel="teams",
                        source_message_ref=invoice.gmail_id,
                        status="pending",
                        decision_payload={
                            "budget": budget_summary,
                            "budget_impact": budget_checks,
                            "validation_gate": context_payload.get("validation_gate"),
                            "approval_context": context_payload.get("approval_context"),
                        },
                    )
            
            logger.info(f"Sent approval request to Slack: {message.ts}")
            
            return {
                "status": "pending_approval",
                "invoice_id": invoice.gmail_id,
                "slack_channel": message.channel,
                "slack_ts": message.ts,
                "budget": budget_summary,
                "teams": teams_status,
            }
            
        except Exception as e:
            logger.error(f"Failed to send Slack approval: {e}")
            return {
                "status": "error",
                "invoice_id": invoice.gmail_id,
                "error": str(e),
            }

    def _send_teams_budget_card(self, invoice: InvoiceData, budget_summary: Dict[str, Any]) -> Dict[str, Any]:
        """Best-effort Teams delivery for approval/budget decisions."""
        client = self.teams_client
        if client is None:
            return {"status": "skipped", "reason": "teams_client_unavailable"}
        try:
            result = client.send_invoice_budget_card(
                email_id=invoice.gmail_id,
                organization_id=self.organization_id,
                vendor=invoice.vendor_name,
                amount=invoice.amount,
                currency=invoice.currency,
                invoice_number=invoice.invoice_number,
                budget=budget_summary,
            )
            if isinstance(result, dict):
                return result
            return {"status": "error", "reason": "invalid_teams_response"}
        except Exception as exc:
            logger.warning("Failed to send Teams approval card: %s", exc)
            return {"status": "error", "reason": str(exc)}

    def _build_approval_context(
        self,
        invoice: InvoiceData,
        context_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build compact cross-system context for approval surfaces."""
        summary: Dict[str, Any] = {
            "vendor_name": invoice.vendor_name,
            "vendor_spend_to_date": 0.0,
            "vendor_open_invoices": 0,
            "connected_systems": [],
            "source_count": 0,
        }
        try:
            if hasattr(self.db, "list_ap_items"):
                items = self.db.list_ap_items(self.organization_id, limit=5000)
                vendor_key = str(invoice.vendor_name or "").strip().lower()
                if vendor_key:
                    vendor_items = [
                        item
                        for item in items
                        if str(item.get("vendor_name") or "").strip().lower() == vendor_key
                    ]
                    summary["vendor_spend_to_date"] = round(
                        sum(float(item.get("amount") or 0) for item in vendor_items),
                        2,
                    )
                    summary["vendor_open_invoices"] = sum(
                        1
                        for item in vendor_items
                        if str(item.get("state") or "").strip().lower()
                        in {
                            "received",
                            "validated",
                            "needs_info",
                            "needs_approval",
                            "pending_approval",
                            "approved",
                            "ready_to_post",
                        }
                    )
        except Exception:
            # Approval flow must not fail due to optional context derivation.
            pass

        multi_system = context_payload.get("multi_system")
        if isinstance(multi_system, dict):
            connected = multi_system.get("connected_systems")
            if isinstance(connected, list):
                summary["connected_systems"] = [str(system) for system in connected if str(system).strip()]

        email_context = context_payload.get("email")
        if isinstance(email_context, dict):
            try:
                summary["source_count"] = int(email_context.get("source_count") or 0)
            except (TypeError, ValueError):
                summary["source_count"] = 0
        return summary
    
    def _build_approval_blocks(
        self, 
        invoice: InvoiceData,
        extra_context: Optional[Dict] = None,
    ) -> list:
        """Build Slack Block Kit blocks for approval request with full intelligence."""
        # Confidence indicator
        if invoice.confidence >= 0.9:
            confidence_text = "High"
        elif invoice.confidence >= 0.7:
            confidence_text = "Medium"
        else:
            confidence_text = "Low"
        
        # Priority from intelligence
        priority_text = ""
        if invoice.priority:
            priority_text = invoice.priority.get("priority_label", "")
        
        # Due date warning
        due_warning = ""
        days_until = invoice.priority.get("days_until_due") if invoice.priority else None
        if days_until is not None:
            if days_until < 0:
                due_warning = f" OVERDUE by {abs(days_until)} days"
            elif days_until == 0:
                due_warning = " DUE TODAY"
            elif days_until <= 3:
                due_warning = f" Due in {days_until} days"
        elif invoice.due_date:
            try:
                due = datetime.strptime(invoice.due_date, "%Y-%m-%d")
                days_until = (due - datetime.now()).days
                if days_until < 0:
                    due_warning = f" OVERDUE by {abs(days_until)} days"
                elif days_until <= 3:
                    due_warning = f" Due in {days_until} days"
            except:
                pass
        
        # Header - indicate priority and if recurring
        is_recurring = extra_context.get("recurring") if extra_context else False
        if is_recurring:
            header_text = "Recurring Invoice - Review Required"
        elif priority_text == "URGENT":
            header_text = "URGENT Invoice Approval Required"
        else:
            header_text = "Invoice Approval Required"
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": header_text
                }
            },
        ]
        
        # ========== VENDOR INTELLIGENCE ==========
        vendor_display = invoice.vendor_name
        vendor_intel = invoice.vendor_intelligence
        if vendor_intel:
            blocks.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": f"*Known vendor:* {vendor_intel.get('category', '')} > {vendor_intel.get('subcategory', '')} | Suggested GL: {vendor_intel.get('suggested_gl', 'N/A')}"
                }]
            })
        
        # Main invoice details
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Vendor:*\n{vendor_display}"},
                {"type": "mrkdwn", "text": f"*Amount:*\n{invoice.currency} {invoice.amount:,.2f}"},
                {"type": "mrkdwn", "text": f"*Invoice #:*\n{invoice.invoice_number or 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Due Date:*\n{invoice.due_date or 'N/A'}{due_warning}"},
            ]
        })
        
        # Priority and Confidence
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Priority:*\n{priority_text}" if priority_text else f"*Confidence:*\n{confidence_text}"},
                {"type": "mrkdwn", "text": f"*Confidence:*\n{confidence_text} ({invoice.confidence*100:.0f}%)"},
            ]
        })
        
        # ========== POLICY COMPLIANCE ==========
        if invoice.policy_compliance and not invoice.policy_compliance.get("compliant", True):
            policy_lines = []
            for violation in invoice.policy_compliance.get("violations", [])[:3]:
                policy_lines.append(f"{violation.get('message', '')}")
            
            if policy_lines:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Policy Requirements:*\n" + "\n".join(policy_lines)
                    }
                })
            
            # Required approvers
            approvers = invoice.policy_compliance.get("required_approvers", [])
            if approvers:
                blocks.append({
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": f"*Required approvers:* {', '.join(['@' + a for a in approvers])}"
                    }]
                })

        # ========== DETERMINISTIC VALIDATION GATE ==========
        validation_gate = (extra_context or {}).get("validation_gate") if extra_context else None
        if validation_gate and validation_gate.get("reason_codes"):
            gate_lines = []
            for reason in (validation_gate.get("reasons") or [])[:4]:
                if not isinstance(reason, dict):
                    continue
                code = str(reason.get("code") or "validation_issue")
                message = str(reason.get("message") or code)
                gate_lines.append(f"- `{code}`: {message}")
            if gate_lines:
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*Deterministic Validation Controls:*\n" + "\n".join(gate_lines),
                        },
                    }
                )

        # ========== BUDGET IMPACT ==========
        budget_checks = self._normalize_budget_checks(invoice.budget_impact)
        if not budget_checks and extra_context:
            budget_checks = self._normalize_budget_checks(extra_context.get("budget_impact"))
        budget_summary = self._compute_budget_summary(budget_checks) if budget_checks else {
            "status": "healthy",
            "requires_decision": False,
        }

        if budget_checks:
            budget_lines = []
            for budget in budget_checks[:3]:
                status = str(budget.get("after_approval_status") or budget.get("status") or "healthy").lower()
                try:
                    pct = float(budget.get("after_approval_percent") or budget.get("percent_used") or 0)
                except (TypeError, ValueError):
                    pct = 0.0
                name = str(budget.get("budget_name") or "Budget")
                try:
                    budget_amount = float(budget.get("budget_amount") or 0)
                except (TypeError, ValueError):
                    budget_amount = 0.0
                try:
                    after_approval_amount = float(budget.get("after_approval") or 0)
                except (TypeError, ValueError):
                    after_approval_amount = 0.0
                if budget.get("remaining") is not None:
                    try:
                        remaining = float(budget.get("remaining") or 0)
                    except (TypeError, ValueError):
                        remaining = 0.0
                else:
                    remaining = budget_amount - after_approval_amount
                marker = "RED" if status == "exceeded" else ("AMBER" if status == "critical" else "GREEN")
                budget_lines.append(
                    f"• *{name}* — {marker} `{status.upper()}` · {pct:.0f}% used after approval · ${remaining:,.0f} remaining"
                )
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Budget Widget:*\n" + "\n".join(budget_lines),
                    },
                }
            )
            if budget_summary.get("requires_decision"):
                blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": (
                                    "*Budget decision required:* approve override (with justification), "
                                    "request budget adjustment, or reject."
                                ),
                            }
                        ],
                    }
                )
        
        approval_context = (extra_context or {}).get("approval_context") if extra_context else None
        if isinstance(approval_context, dict):
            systems = approval_context.get("connected_systems") or []
            systems_text = ", ".join(str(system) for system in systems[:6]) if systems else "email_only"
            spend = float(approval_context.get("vendor_spend_to_date") or 0.0)
            open_count = int(approval_context.get("vendor_open_invoices") or 0)
            source_count = int(approval_context.get("source_count") or 0)
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "*Cross-System Context:*\n"
                            f"• Vendor spend to date: `${spend:,.2f}`\n"
                            f"• Open invoices for vendor: `{open_count}`\n"
                            f"• Linked source count: `{source_count}`\n"
                            f"• Connected systems: `{systems_text}`"
                        ),
                    },
                }
            )

        # ========== DUPLICATE WARNING ==========
        if invoice.potential_duplicates and invoice.potential_duplicates > 0:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Potential Duplicate:* Found {invoice.potential_duplicates} similar invoice(s)"
                }
            })
        
        # ========== INSIGHTS ==========
        if invoice.insights:
            insight_lines = [f"{i.get('title', '')}" for i in invoice.insights[:2]]
            if insight_lines:
                blocks.append({
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": "\n".join(insight_lines)
                    }]
                })
        
        # Add recurring alerts if present
        if extra_context and extra_context.get("alerts"):
            alert_texts = []
            for alert in extra_context.get("alerts", []):
                severity_label = {
                    "high": "HIGH",
                    "warning": "WARNING",
                    "info": "INFO",
                }.get(alert.get("severity", "info"), "INFO")
                alert_texts.append(f"[{severity_label}] {alert.get('message')}")
            
            if alert_texts:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Alerts:*\n" + "\n".join(alert_texts)
                    }
                })
        
        # Add recurring pattern info
        if extra_context and extra_context.get("pattern"):
            pattern = extra_context.get("pattern")
            blocks.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": f"Recurring: Usually {invoice.currency} {pattern.get('typical_amount', 0):,.2f} every ~{pattern.get('frequency_days', 30)} days ({pattern.get('invoice_count', 0)} previous invoices)"
                }]
            })
        
        # Add agent reasoning if available
        if invoice.reasoning_summary:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*AI Analysis:* {invoice.reasoning_summary}"
                }
            })
        
        # Add reasoning factors
        if invoice.reasoning_factors:
            factor_lines = []
            for f in invoice.reasoning_factors[:4]:
                score = f.get("score", 0)
                if score >= 0.8:
                    indicator = "HIGH"
                elif score >= 0.5:
                    indicator = "MEDIUM"
                else:
                    indicator = "LOW"
                factor_lines.append(f"[{indicator}] {f.get('detail', '')}")
            
            if factor_lines:
                blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "\n".join(factor_lines)}
                    ]
                })
        
        # Add risks if present
        if invoice.reasoning_risks:
            risk_lines = [f"{risk}" for risk in invoice.reasoning_risks[:3]]
            if risk_lines:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Risks:*\n" + "\n".join(risk_lines)
                    }
                })
        
        requires_budget_decision = bool(budget_summary.get("requires_decision"))
        approval_override_value = json.dumps(
            {
                "gmail_id": invoice.gmail_id,
                "justification": "Approved over budget in Slack",
                "decision": "approve_override",
            }
        )

        # Footer and actions
        blocks.extend([
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"From: {invoice.sender} | Subject: {invoice.subject[:50]}..."}
                ]
            },
            {
                "type": "actions",
                "elements": (
                    [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Approve override"},
                            "style": "primary",
                            "action_id": f"approve_budget_override_{invoice.gmail_id}",
                            "value": approval_override_value,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Request budget adjustment"},
                            "action_id": f"request_budget_adjustment_{invoice.gmail_id}",
                            "value": invoice.gmail_id,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Reject over budget"},
                            "style": "danger",
                            "action_id": f"reject_budget_{invoice.gmail_id}",
                            "value": invoice.gmail_id,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View in Gmail"},
                            "action_id": f"view_invoice_{invoice.gmail_id}",
                            "url": f"https://mail.google.com/mail/u/0/#search/{invoice.gmail_id}",
                        },
                    ]
                    if requires_budget_decision
                    else [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Post to ERP"},
                            "style": "primary",
                            "action_id": f"post_to_erp_{invoice.gmail_id}",
                            "value": invoice.gmail_id,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Reject"},
                            "style": "danger",
                            "action_id": f"reject_invoice_{invoice.gmail_id}",
                            "value": invoice.gmail_id,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View in Gmail"},
                            "action_id": f"view_invoice_{invoice.gmail_id}",
                            "url": f"https://mail.google.com/mail/u/0/#search/{invoice.gmail_id}",
                        },
                    ]
                )
            }
        ])
        
        return blocks
    
    async def approve_invoice(
        self,
        gmail_id: str,
        approved_by: str,
        slack_channel: Optional[str] = None,
        slack_ts: Optional[str] = None,
        source_channel: str = "slack",
        source_channel_id: Optional[str] = None,
        source_message_ref: Optional[str] = None,
        allow_budget_override: bool = False,
        override_justification: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Approve an invoice and post to ERP.
        
        Called when user clicks Approve in Slack or Gmail extension.
        """
        # Get invoice data
        invoice_data = self.db.get_invoice_status(gmail_id)
        if not invoice_data:
            return {"status": "error", "reason": "Invoice not found"}
        
        if invoice_data.get("status") == "posted":
            return {"status": "error", "reason": "Invoice already posted"}
        if invoice_data.get("erp_bill_id"):
            return {"status": "error", "reason": "Invoice already posted"}

        budget_checks = self._load_budget_context_from_invoice_row(invoice_data)
        budget_summary = self._compute_budget_summary(budget_checks)
        if budget_summary.get("requires_decision") and not allow_budget_override:
            return {
                "status": "needs_budget_decision",
                "invoice_id": gmail_id,
                "reason": "budget_requires_decision",
                "budget": budget_summary,
                "options": [
                    "approve_override_with_justification",
                    "request_budget_adjustment",
                    "reject_over_budget",
                ],
            }
        if allow_budget_override and budget_summary.get("requires_decision"):
            if not str(override_justification or "").strip():
                return {
                    "status": "error",
                    "invoice_id": gmail_id,
                    "reason": "budget_override_requires_justification",
                }
        
        # Update status to approved
        approved_at = datetime.now(timezone.utc).isoformat()
        self.db.update_invoice_status(
            gmail_id=gmail_id,
            status="approved",
            approved_by=approved_by,
            approved_at=approved_at,
        )
        
        # Build invoice object for ERP
        invoice = InvoiceData(
            gmail_id=gmail_id,
            subject=invoice_data.get("email_subject", ""),
            sender="",
            vendor_name=invoice_data.get("vendor", "Unknown"),
            amount=invoice_data.get("amount", 0),
            currency=invoice_data.get("currency", "USD"),
            invoice_number=invoice_data.get("invoice_number"),
            due_date=invoice_data.get("due_date"),
            organization_id=self.organization_id,
            invoice_text=invoice_data.get("email_body", ""),  # For discount detection
            budget_impact=budget_checks,
        )
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=gmail_id,
            vendor_name=invoice.vendor_name,
            invoice_number=invoice.invoice_number,
        )
        resolved_source_channel = str(source_channel or "slack").strip().lower() or "slack"
        resolved_channel_id = source_channel_id or slack_channel
        resolved_message_ref = source_message_ref or slack_ts
        
        # Post to ERP
        result = await self._post_to_erp(invoice)
        
        if result.get("status") == "success":
            self.db.update_invoice_status(
                gmail_id=gmail_id,
                status="posted",
                erp_bill_id=result.get("bill_id"),
                erp_vendor_id=result.get("vendor_id"),
            )
            
            # LEARNING: Record this approval to learn vendor→GL mappings
            try:
                learning = get_learning_service(self.organization_id)
                learning.record_approval(
                    vendor=invoice.vendor_name,
                    gl_code=result.get("gl_code", ""),
                    gl_description=result.get("gl_description", "Accounts Payable"),
                    amount=invoice.amount,
                    currency=invoice.currency,
                    was_auto_approved=False,
                    was_corrected=False,  # TODO: Track if user changed suggested GL
                )
                logger.info(f"Recorded approval for learning: {invoice.vendor_name} → GL {result.get('gl_code')}")
            except Exception as e:
                logger.warning(f"Failed to record approval for learning: {e}")
            
            # Update Slack message
            if resolved_source_channel == "slack" and resolved_channel_id and resolved_message_ref:
                await self._update_slack_approved(
                    resolved_channel_id, resolved_message_ref, invoice, approved_by, result
                )
            self._record_approval_snapshot(
                ap_item_id=ap_item_id,
                gmail_id=gmail_id,
                channel_id=resolved_channel_id,
                message_ts=resolved_message_ref,
                source_channel=resolved_source_channel,
                source_message_ref=gmail_id,
                status="approved",
                decision_payload={
                    "decision": "approve_override" if allow_budget_override else "approve",
                    "override_justification": override_justification,
                    "budget": budget_summary,
                    "budget_impact": budget_checks,
                },
                approved_by=approved_by,
                approved_at=approved_at,
            )
        else:
            # Revert to pending if ERP post failed
            self.db.update_invoice_status(
                gmail_id=gmail_id,
                status="pending_approval",
            )
            self._record_approval_snapshot(
                ap_item_id=ap_item_id,
                gmail_id=gmail_id,
                channel_id=resolved_channel_id,
                message_ts=resolved_message_ref,
                source_channel=resolved_source_channel,
                source_message_ref=gmail_id,
                status="failed",
                decision_payload={
                    "decision": "approve_override" if allow_budget_override else "approve",
                    "override_justification": override_justification,
                    "budget": budget_summary,
                    "budget_impact": budget_checks,
                    "erp_result": result,
                },
            )
        
        return {
            "status": "approved" if result.get("status") == "success" else "error",
            "invoice_id": gmail_id,
            "approved_by": approved_by,
            "budget_override": bool(allow_budget_override),
            "override_justification": override_justification,
            "budget": budget_summary,
            "erp_result": result,
        }
    
    async def reject_invoice(
        self,
        gmail_id: str,
        reason: str,
        rejected_by: str,
        slack_channel: Optional[str] = None,
        slack_ts: Optional[str] = None,
        source_channel: str = "slack",
        source_channel_id: Optional[str] = None,
        source_message_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reject an invoice with reason."""
        invoice_data = self.db.get_invoice_status(gmail_id)
        if not invoice_data:
            return {"status": "error", "reason": "Invoice not found"}
        rejected_at = datetime.now(timezone.utc).isoformat()
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=gmail_id,
            vendor_name=invoice_data.get("vendor"),
            invoice_number=invoice_data.get("invoice_number"),
        )
        resolved_source_channel = str(source_channel or "slack").strip().lower() or "slack"
        resolved_channel_id = source_channel_id or slack_channel
        resolved_message_ref = source_message_ref or slack_ts
        
        # Update status
        self.db.update_invoice_status(
            gmail_id=gmail_id,
            status="rejected",
            rejection_reason=reason,
            rejected_by=rejected_by,
            rejected_at=rejected_at,
        )
        
        # Update Slack thread status
        thread = self.db.get_slack_thread(gmail_id)
        if thread:
            self.db.update_slack_thread_status(
                thread_id=thread["id"],
                status="rejected",
                rejection_reason=reason,
            )
        
        # Update Slack message
        if resolved_source_channel == "slack" and resolved_channel_id and resolved_message_ref:
            await self._update_slack_rejected(
                resolved_channel_id, resolved_message_ref, invoice_data, rejected_by, reason
            )
        self._record_approval_snapshot(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
            channel_id=resolved_channel_id,
            message_ts=resolved_message_ref,
            source_channel=resolved_source_channel,
            source_message_ref=gmail_id,
            status="rejected",
            decision_payload={
                "decision": "reject",
                "reason": reason,
            },
            rejected_by=rejected_by,
            rejected_at=rejected_at,
            rejection_reason=reason,
        )
        
        logger.info(f"Invoice rejected: {gmail_id} by {rejected_by} - {reason}")
        
        return {
            "status": "rejected",
            "invoice_id": gmail_id,
            "rejected_by": rejected_by,
            "reason": reason,
        }

    async def request_budget_adjustment(
        self,
        gmail_id: str,
        requested_by: str,
        reason: Optional[str] = None,
        slack_channel: Optional[str] = None,
        slack_ts: Optional[str] = None,
        source_channel: str = "slack",
        source_channel_id: Optional[str] = None,
        source_message_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark invoice for budget adjustment before final approval."""
        invoice_data = self.db.get_invoice_status(gmail_id)
        if not invoice_data:
            return {"status": "error", "reason": "Invoice not found"}

        reason_text = str(reason or "budget_adjustment_requested").strip() or "budget_adjustment_requested"
        requested_at = datetime.now(timezone.utc).isoformat()
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=gmail_id,
            vendor_name=invoice_data.get("vendor"),
            invoice_number=invoice_data.get("invoice_number"),
        )
        resolved_source_channel = str(source_channel or "slack").strip().lower() or "slack"
        resolved_channel_id = source_channel_id or slack_channel
        resolved_message_ref = source_message_ref or slack_ts

        self.db.update_invoice_status(
            gmail_id=gmail_id,
            status="needs_info",
            rejection_reason=reason_text,
            rejected_by=requested_by,
            rejected_at=requested_at,
        )

        if resolved_source_channel == "slack" and resolved_channel_id and resolved_message_ref:
            await self._update_slack_budget_adjustment_requested(
                resolved_channel_id,
                resolved_message_ref,
                invoice_data,
                requested_by=requested_by,
                reason=reason_text,
            )

        self._record_approval_snapshot(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
            channel_id=resolved_channel_id,
            message_ts=resolved_message_ref,
            source_channel=resolved_source_channel,
            source_message_ref=gmail_id,
            status="pending_adjustment",
            decision_payload={
                "decision": "request_budget_adjustment",
                "reason": reason_text,
            },
            rejected_by=requested_by,
            rejected_at=requested_at,
            rejection_reason=reason_text,
        )

        return {
            "status": "needs_info",
            "invoice_id": gmail_id,
            "requested_by": requested_by,
            "reason": reason_text,
        }
    
    async def _post_to_erp(self, invoice: InvoiceData) -> Dict[str, Any]:
        """Post approved invoice to ERP as a Bill."""
        # First, get or create vendor
        vendor = Vendor(
            name=invoice.vendor_name,
            currency=invoice.currency,
        )
        
        vendor_result = await get_or_create_vendor(self.organization_id, vendor)
        
        if vendor_result.get("status") == "error":
            logger.error(f"Failed to get/create vendor: {vendor_result}")
            return vendor_result
        
        vendor_id = vendor_result.get("vendor_id")
        
        # Create and post bill
        bill = Bill(
            vendor_id=vendor_id,
            vendor_name=invoice.vendor_name,
            amount=invoice.amount,
            currency=invoice.currency,
            invoice_number=invoice.invoice_number,
            invoice_date=datetime.now().strftime("%Y-%m-%d"),
            due_date=invoice.due_date,
            description=f"Invoice from {invoice.vendor_name}",
            po_number=invoice.po_number,
        )
        
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=invoice.gmail_id,
            vendor_name=invoice.vendor_name,
            invoice_number=invoice.invoice_number,
        )

        result = await post_bill_api_first(
            organization_id=self.organization_id,
            bill=bill,
            actor_id="invoice_workflow",
            ap_item_id=ap_item_id,
            email_id=invoice.gmail_id,
            invoice_number=invoice.invoice_number,
            vendor_name=invoice.vendor_name,
            amount=invoice.amount,
            currency=invoice.currency,
            vendor_portal_url=invoice.attachment_url,
        )
        
        if result.get("status") == "success":
            result["vendor_id"] = vendor_id
            logger.info(f"Posted bill to ERP: {result.get('bill_id')}")
            
            # Auto-schedule payment based on due date
            await self._schedule_payment(invoice, vendor_id, result.get('bill_id'))
        
        return result
    
    async def _schedule_payment(
        self,
        invoice: InvoiceData,
        vendor_id: str,
        erp_bill_id: str,
    ) -> None:
        """Auto-schedule payment after successful ERP posting."""
        try:
            from clearledgr.core.database import get_db
            from clearledgr.services.early_payment_discounts import get_discount_service
            
            db = get_db()
            
            # Check for early payment discount opportunity
            discount_service = get_discount_service(self.organization_id)
            discount = None
            
            if invoice.invoice_text:
                discount = discount_service.detect_discount(
                    invoice_id=invoice.gmail_id,
                    invoice_text=invoice.invoice_text,
                    vendor_name=invoice.vendor_name,
                    amount=invoice.amount,
                )
            
            # Determine payment date
            if discount and discount.discount_percent > 0:
                # Schedule for discount deadline to capture savings
                payment_date = discount.discount_deadline.isoformat() if discount.discount_deadline else None
                payment_note = f"Early payment discount: {discount.discount_percent}% if paid by {payment_date}"
                logger.info(f"Scheduling payment for discount capture: {invoice.vendor_name} - save {discount.discount_percent}%")
            else:
                # Schedule for due date
                payment_date = invoice.due_date
                payment_note = "Standard payment terms"
            
            # Create payment record
            payment = db.save_ap_payment(
                invoice_id=invoice.gmail_id,
                vendor_id=vendor_id,
                vendor_name=invoice.vendor_name,
                amount=invoice.amount,
                currency=invoice.currency,
                method="ach",  # Default to ACH
                status="scheduled",
                organization_id=self.organization_id,
            )
            
            # Update payment with scheduled date
            if payment_date:
                db.update_ap_payment(
                    payment['payment_id'],
                    scheduled_date=payment_date,
                )
            
            logger.info(f"Payment scheduled: {payment['payment_id']} for {invoice.vendor_name} - ${invoice.amount}")
            
            # Log to audit trail
            self.audit.log(
                invoice_id=invoice.gmail_id,
                event_type="PAYMENT_SCHEDULED",
                actor="clearledgr-auto",
                details={
                    "payment_id": payment['payment_id'],
                    "vendor": invoice.vendor_name,
                    "amount": invoice.amount,
                    "scheduled_date": payment_date,
                    "erp_bill_id": erp_bill_id,
                    "discount_captured": discount.discount_percent if discount else 0,
                },
            )
            
        except Exception as e:
            logger.warning(f"Failed to schedule payment for {invoice.gmail_id}: {e}")
            # Don't fail the posting - payment can be scheduled manually
    
    async def _send_posted_notification(
        self,
        invoice: InvoiceData,
        erp_result: Dict[str, Any],
        reason: str = "high_confidence",
        recurring_info: Optional[Dict] = None,
    ) -> None:
        """Send notification that invoice was auto-posted with reasoning."""
        # Different message based on reason
        if reason == "recurring_match":
            reason_text = f"Recurring subscription (matched {recurring_info.get('invoice_count', 0)} previous invoices)"
        else:
            # Use agent reasoning if available
            if invoice.reasoning_summary:
                reason_text = f"{invoice.reasoning_summary}"
            else:
                reason_text = f"Auto-approved (confidence: {invoice.confidence*100:.0f}%)"
        
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Invoice Auto-Posted*\n"
                            f"*{invoice.vendor_name}* - {invoice.currency} {invoice.amount:,.2f}\n"
                            f"Bill ID: `{erp_result.get('bill_id')}`"
                }
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": reason_text}
                ]
            }
        ]
        
        # Add reasoning factors if available
        if invoice.reasoning_factors:
            factor_lines = []
            for f in invoice.reasoning_factors[:3]:  # Top 3 factors
                score_value = int(f.get("score", 0) * 5)
                factor_lines.append(f"Score {score_value}/5 - {f.get('detail', '')}")
            
            if factor_lines:
                blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "\n".join(factor_lines)}
                    ]
                })
        
        await self.slack_client.send_message(
            channel=self.slack_channel,
            text=f"Invoice auto-posted: {invoice.vendor_name} ${invoice.amount:,.2f}",
            blocks=blocks,
        )
    
    async def _update_slack_approved(
        self,
        channel: str,
        ts: str,
        invoice: InvoiceData,
        approved_by: str,
        erp_result: Dict[str, Any],
    ) -> None:
        """Update Slack message to show approved status."""
        doc_number = erp_result.get("doc_num") or erp_result.get("document_number") or erp_result.get("erp_document")
        bill_id = erp_result.get("bill_id")
        reference_line = f"Bill ID: `{bill_id}`" if bill_id else "Bill posted"
        if doc_number:
            reference_line = f"{reference_line} | Doc #: `{doc_number}`"

        blocks = [
            {
                "type": "section",
                "text": {
                "type": "mrkdwn",
                "text": f"*Invoice Approved & Posted*\n"
                        f"*{invoice.vendor_name}* - {invoice.currency} {invoice.amount:,.2f}\n"
                        f"Invoice #: {invoice.invoice_number or 'N/A'}"
            }
        },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Approved by {approved_by} | {reference_line}"}
                ]
            }
        ]
        
        try:
            await self.slack_client.update_message(channel, ts, "Invoice approved", blocks)
        except Exception as e:
            logger.warning(f"Failed to update Slack message: {e}")
    
    async def _update_slack_rejected(
        self,
        channel: str,
        ts: str,
        invoice_data: Dict[str, Any],
        rejected_by: str,
        reason: str,
    ) -> None:
        """Update Slack message to show rejected status."""
        blocks = [
            {
                "type": "section",
                "text": {
                "type": "mrkdwn",
                "text": f"*Invoice Rejected*\n"
                        f"*{invoice_data.get('vendor', 'Unknown')}* - {invoice_data.get('currency', 'USD')} {invoice_data.get('amount', 0):,.2f}\n"
                        f"Reason: {reason}"
            }
        },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Rejected by {rejected_by}"}
                ]
            }
        ]
        
        try:
            await self.slack_client.update_message(channel, ts, "Invoice rejected", blocks)
        except Exception as e:
            logger.warning(f"Failed to update Slack message: {e}")

    async def _update_slack_budget_adjustment_requested(
        self,
        channel: str,
        ts: str,
        invoice_data: Dict[str, Any],
        requested_by: str,
        reason: str,
    ) -> None:
        """Update Slack message when approver requests budget adjustment."""
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Budget Adjustment Requested*\n"
                        f"*{invoice_data.get('vendor', 'Unknown')}* - "
                        f"{invoice_data.get('currency', 'USD')} {invoice_data.get('amount', 0):,.2f}\n"
                        f"Reason: {reason}"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Requested by {requested_by}"},
                ],
            },
        ]
        try:
            await self.slack_client.update_message(channel, ts, "Budget adjustment requested", blocks)
        except Exception as e:
            logger.warning(f"Failed to update Slack message for budget adjustment: {e}")
    
    async def send_exception_alert(
        self,
        invoice: InvoiceData,
        exception_type: str,
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Send exception alert to Slack.
        
        Exception types:
        - duplicate: Potential duplicate invoice detected
        - amount_mismatch: Amount doesn't match PO
        - vendor_unknown: Vendor not in system
        - overdue: Invoice is past due date
        """
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Exception: {exception_type.replace('_', ' ').title()}"
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Vendor:*\n{invoice.vendor_name}"},
                    {"type": "mrkdwn", "text": f"*Amount:*\n{invoice.currency} {invoice.amount:,.2f}"},
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Details:*\n{details.get('message', 'No details available')}"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Review"},
                        "action_id": f"review_exception_{invoice.gmail_id}",
                        "value": invoice.gmail_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Dismiss"},
                        "action_id": f"dismiss_exception_{invoice.gmail_id}",
                        "value": invoice.gmail_id,
                    },
                ]
            }
        ]
        
        try:
            message = await self.slack_client.send_message(
                channel=self.slack_channel,
                text=f"Exception: {exception_type} - {invoice.vendor_name}",
                blocks=blocks,
            )
            
            return {
                "status": "sent",
                "channel": message.channel,
                "ts": message.ts,
            }
        except Exception as e:
            logger.error(f"Failed to send exception alert: {e}")
            return {"status": "error", "error": str(e)}


# Convenience function
def get_invoice_workflow(
    organization_id: str,
    slack_channel: Optional[str] = None,
) -> InvoiceWorkflowService:
    """Get an invoice workflow service instance."""
    return InvoiceWorkflowService(
        organization_id=organization_id,
        slack_channel=slack_channel,
    )
