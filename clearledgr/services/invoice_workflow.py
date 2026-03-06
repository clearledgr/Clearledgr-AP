"""
Invoice Workflow Service

Orchestrates the complete invoice lifecycle:
Gmail Detection → Data Extraction → Slack Approval → ERP Posting

This is the heart of "Streak for Finance" - bringing AP workflow into the tools
finance teams already use.
"""

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

from clearledgr.core.ap_confidence import (
    DEFAULT_CRITICAL_FIELD_CONFIDENCE_THRESHOLD,
    evaluate_critical_field_confidence,
)
from clearledgr.core.ap_states import (
    APState,
    OverrideContext,
    classify_post_failure_recoverability,
)
from clearledgr.core.database import get_db
from clearledgr.services.slack_api import SlackAPIClient, get_slack_client
try:
    from clearledgr.services.teams_api import TeamsAPIClient
except Exception as e:  # pragma: no cover - optional integration in some local builds
    logging.getLogger(__name__).info("TeamsAPIClient not available: %s", e)
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
    field_confidences: Optional[Dict[str, Any]] = None
    correlation_id: Optional[str] = None


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
        except Exception as e:
            logger.warning("Failed to load org settings for %s: %s", self.organization_id, e)
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
        env_channel = (
            os.getenv("SLACK_APPROVAL_CHANNEL")
            or os.getenv("SLACK_DEFAULT_CHANNEL")
            or ""
        ).strip()
        return env_channel or "#finance-approvals"
    
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
            self._slack_client = get_slack_client(organization_id=self.organization_id)
        return self._slack_client

    @property
    def teams_client(self) -> Optional[Any]:
        """Lazy-load Teams client."""
        if TeamsAPIClient is None:
            return None
        if self._teams_client is None:
            self._teams_client = TeamsAPIClient.from_env(self.organization_id)
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
        summary["hard_block"] = highest_status == "exceeded"
        return summary

    def _critical_field_confidence_threshold(self) -> float:
        """Policy-adjustable threshold for critical extraction fields (default 95%)."""
        self._load_settings()
        if isinstance(self._settings, dict):
            for key in ("critical_field_confidence_threshold", "confidence_gate_threshold"):
                raw = self._settings.get(key)
                try:
                    if raw is None:
                        continue
                    value = float(raw)
                    if value > 1.0 and value <= 100.0:
                        value = value / 100.0
                    if 0.0 <= value <= 1.0:
                        return value
                except (TypeError, ValueError):
                    continue
        return DEFAULT_CRITICAL_FIELD_CONFIDENCE_THRESHOLD

    def _evaluate_invoice_confidence_gate(self, invoice: InvoiceData) -> Dict[str, Any]:
        return evaluate_critical_field_confidence(
            overall_confidence=invoice.confidence,
            field_values={
                "vendor": invoice.vendor_name,
                "amount": invoice.amount,
                "invoice_number": invoice.invoice_number,
                "due_date": invoice.due_date,
            },
            field_confidences=invoice.field_confidences,
            threshold=self._critical_field_confidence_threshold(),
        )

    def _evaluate_invoice_row_confidence_gate(
        self,
        invoice_row: Dict[str, Any],
        *,
        field_confidences_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        try:
            raw_meta = invoice_row.get("metadata")
            if isinstance(raw_meta, dict):
                metadata = raw_meta
            elif isinstance(raw_meta, str) and raw_meta.strip():
                metadata = json.loads(raw_meta)
        except Exception:
            metadata = {}

        field_confidences = field_confidences_override or metadata.get("field_confidences")
        return evaluate_critical_field_confidence(
            overall_confidence=invoice_row.get("confidence"),
            field_values={
                "vendor": invoice_row.get("vendor") or invoice_row.get("vendor_name"),
                "amount": invoice_row.get("amount"),
                "invoice_number": invoice_row.get("invoice_number"),
                "due_date": invoice_row.get("due_date"),
            },
            field_confidences=field_confidences,
            threshold=self._critical_field_confidence_threshold(),
        )

    # High-severity PO exception types that block approval without override.
    _PO_BLOCKING_EXCEPTION_TYPES = frozenset({
        "no_po", "price_mismatch", "no_gr", "over_invoice", "duplicate_invoice",
    })
    _PO_BLOCKING_SEVERITIES = frozenset({"high", "medium", "error"})

    def _check_po_exception_block(
        self,
        invoice_row: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return ``{"blocked": True, "exceptions": [...]}`` when the invoice
        has unresolved PO/receipt match exceptions that should prevent approval
        without an explicit override."""
        metadata: Dict[str, Any] = {}
        try:
            raw = invoice_row.get("metadata")
            if isinstance(raw, dict):
                metadata = raw
            elif isinstance(raw, str) and raw.strip():
                metadata = json.loads(raw)
        except Exception:
            metadata = {}

        po_match = metadata.get("po_match_result")
        if not isinstance(po_match, dict):
            return {"blocked": False, "exceptions": []}

        match_status = str(po_match.get("status") or "").lower()
        if match_status in {"matched", "override"}:
            return {"blocked": False, "exceptions": []}

        blocking: List[Dict[str, Any]] = []
        for exc in po_match.get("exceptions") or []:
            if not isinstance(exc, dict):
                continue
            ex_type = str(exc.get("type") or "").lower()
            severity = str(exc.get("severity") or "").lower()
            if ex_type in self._PO_BLOCKING_EXCEPTION_TYPES or severity in self._PO_BLOCKING_SEVERITIES:
                blocking.append(exc)

        return {"blocked": bool(blocking), "exceptions": blocking}

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
        except Exception as e:
            logger.warning("AP item lookup failed for gmail_id=%s: %s", gmail_id, e)
            return None
        return None

    @staticmethod
    def _parse_metadata_dict(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    def _get_ap_item_correlation_id(
        self,
        *,
        ap_item_id: Optional[str] = None,
        gmail_id: Optional[str] = None,
    ) -> Optional[str]:
        row: Optional[Dict[str, Any]] = None
        try:
            if ap_item_id and hasattr(self.db, "get_ap_item"):
                row = self.db.get_ap_item(ap_item_id)
            if row is None and gmail_id and hasattr(self.db, "get_invoice_status"):
                row = self.db.get_invoice_status(gmail_id)
            metadata = self._parse_metadata_dict((row or {}).get("metadata"))
            corr = str(metadata.get("correlation_id") or "").strip()
            return corr or None
        except Exception:
            return None

    def _ensure_ap_item_correlation_id(
        self,
        *,
        ap_item_id: Optional[str],
        gmail_id: Optional[str],
        preferred: Optional[str] = None,
    ) -> Optional[str]:
        correlation_id = (
            str(preferred or "").strip()
            or self._get_ap_item_correlation_id(ap_item_id=ap_item_id, gmail_id=gmail_id)
        )
        if not correlation_id:
            base = str(gmail_id or ap_item_id or uuid.uuid4().hex)
            correlation_id = f"ap_corr:{base}:{uuid.uuid4().hex[:8]}"

        if ap_item_id:
            try:
                row = self.db.get_ap_item(ap_item_id) if hasattr(self.db, "get_ap_item") else None
                metadata = self._parse_metadata_dict((row or {}).get("metadata"))
                if str(metadata.get("correlation_id") or "").strip() != correlation_id:
                    metadata["correlation_id"] = correlation_id
                    self.db.update_ap_item(ap_item_id, metadata=metadata)
            except Exception as exc:
                logger.error("Could not persist AP correlation ID for %s: %s", ap_item_id, exc)
        return correlation_id

    def _canonical_invoice_state(self, invoice_row: Optional[Dict[str, Any]]) -> Optional[str]:
        """Return canonical AP state from a legacy/canonical invoice row."""
        if not isinstance(invoice_row, dict):
            return None
        raw_state = invoice_row.get("state")
        if raw_state in (None, ""):
            raw_state = invoice_row.get("status")
        if raw_state in (None, ""):
            return None
        try:
            from clearledgr.core.ap_states import normalize_state

            return normalize_state(str(raw_state))
        except Exception:
            return str(raw_state)

    def build_invoice_data_from_ap_item(
        self,
        ap_item: Dict[str, Any],
        *,
        actor_id: Optional[str] = None,
    ) -> InvoiceData:
        """Build `InvoiceData` from a persisted AP row."""
        metadata = self._parse_metadata_dict((ap_item or {}).get("metadata"))
        return InvoiceData(
            gmail_id=str(ap_item.get("thread_id") or ap_item.get("id") or ""),
            subject=str(ap_item.get("subject") or ""),
            sender=str(ap_item.get("sender") or ""),
            vendor_name=str(ap_item.get("vendor_name") or ap_item.get("vendor") or "Unknown"),
            amount=float(ap_item.get("amount") or 0.0),
            currency=str(ap_item.get("currency") or "USD"),
            invoice_number=ap_item.get("invoice_number"),
            due_date=ap_item.get("due_date"),
            organization_id=str(ap_item.get("organization_id") or self.organization_id),
            user_id=actor_id or str(ap_item.get("user_id") or ""),
            confidence=float(ap_item.get("confidence") or 0.0),
            field_confidences=(
                ap_item.get("field_confidences")
                if isinstance(ap_item.get("field_confidences"), dict)
                else metadata.get("field_confidences")
            ),
            correlation_id=str(
                ap_item.get("correlation_id")
                or metadata.get("correlation_id")
                or ""
            ).strip()
            or None,
        )

    def evaluate_batch_route_low_risk_for_approval(self, ap_item: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate deterministic prechecks for batch `route_low_risk_for_approval`."""
        state = self._canonical_invoice_state(ap_item) or ""
        metadata = self._parse_metadata_dict((ap_item or {}).get("metadata"))
        reason_codes: List[str] = []

        if state != APState.VALIDATED.value:
            reason_codes.append("state_not_validated")

        requires_field_review = bool(
            ap_item.get("requires_field_review")
            or metadata.get("requires_field_review")
        )
        if requires_field_review:
            reason_codes.append("field_review_required")

        confidence_blockers = []
        raw_blockers = ap_item.get("confidence_blockers")
        if isinstance(raw_blockers, list):
            confidence_blockers = [entry for entry in raw_blockers if entry]
        elif isinstance(metadata.get("confidence_blockers"), list):
            confidence_blockers = [entry for entry in metadata.get("confidence_blockers") if entry]
        if confidence_blockers:
            reason_codes.append("confidence_blockers_present")

        budget_requires_decision = bool(
            ap_item.get("budget_requires_decision")
            or metadata.get("budget_requires_decision")
        )
        if budget_requires_decision:
            reason_codes.append("budget_decision_required")

        exception_code = str(
            ap_item.get("exception_code")
            or metadata.get("exception_code")
            or ""
        ).strip()
        if exception_code:
            reason_codes.append("exception_present")

        document_type = str(
            ap_item.get("document_type")
            or metadata.get("document_type")
            or metadata.get("email_type")
            or "invoice"
        ).strip().lower()
        if document_type and document_type != "invoice":
            reason_codes.append("non_invoice_document")

        if metadata.get("merged_into") or ap_item.get("is_merged_source"):
            reason_codes.append("merged_source")

        return {
            "eligible": len(reason_codes) == 0,
            "state": state or None,
            "reason_codes": reason_codes,
            "requires_field_review": requires_field_review,
            "confidence_blockers": confidence_blockers,
            "budget_requires_decision": budget_requires_decision,
            "exception_code": exception_code or None,
            "document_type": document_type or "invoice",
        }

    def evaluate_batch_retry_recoverable_failure(self, ap_item: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate deterministic prechecks for batch `retry_recoverable_failures`."""
        state = self._canonical_invoice_state(ap_item) or ""
        metadata = self._parse_metadata_dict((ap_item or {}).get("metadata"))
        last_error = str(
            ap_item.get("last_error")
            or metadata.get("last_error")
            or ""
        ).strip()
        exception_code = str(
            ap_item.get("exception_code")
            or metadata.get("exception_code")
            or ""
        ).strip()

        if state != APState.FAILED_POST.value:
            return {
                "eligible": False,
                "state": state or None,
                "reason_codes": ["state_not_failed_post"],
                "recoverability": {
                    "recoverable": False,
                    "reason": "state_not_failed_post",
                },
            }

        recoverability = classify_post_failure_recoverability(
            last_error=last_error,
            exception_code=exception_code,
        )
        reason_codes: List[str] = []
        if not recoverability.get("recoverable"):
            reason_codes.append(str(recoverability.get("reason") or "non_recoverable_failure"))

        return {
            "eligible": len(reason_codes) == 0,
            "state": state,
            "reason_codes": reason_codes,
            "recoverability": recoverability,
            "last_error": last_error or None,
            "exception_code": exception_code or None,
        }

    def _transition_invoice_state(
        self,
        gmail_id: str,
        target_state: str,
        correlation_id: Optional[str] = None,
        source: Optional[str] = "invoice_workflow",
        workflow_id: Optional[str] = None,
        run_id: Optional[str] = None,
        decision_reason: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        """
        Transition an invoice/AP item via the gmail_id bridge.

        If already in *target_state*, applies non-state updates only and returns success.
        """
        if not gmail_id or not hasattr(self.db, "get_invoice_status") or not hasattr(self.db, "update_invoice_status"):
            return False

        row = self.db.get_invoice_status(gmail_id)
        current_state = self._canonical_invoice_state(row)
        try:
            from clearledgr.core.ap_states import normalize_state

            normalized_target = normalize_state(target_state)
        except Exception:
            normalized_target = str(target_state or "").strip().lower()

        if current_state == normalized_target:
            if kwargs:
                ap_item_id = str((row or {}).get("id") or "") if isinstance(row, dict) else None
                resolved_corr = correlation_id or self._get_ap_item_correlation_id(
                    ap_item_id=ap_item_id,
                    gmail_id=gmail_id,
                )
                if resolved_corr:
                    kwargs["_correlation_id"] = resolved_corr
                if source:
                    kwargs["_source"] = source
                if workflow_id:
                    kwargs["_workflow_id"] = workflow_id
                if run_id:
                    kwargs["_run_id"] = run_id
                if decision_reason:
                    kwargs["_decision_reason"] = decision_reason
                return bool(self.db.update_invoice_status(gmail_id=gmail_id, **kwargs))
            return True

        ap_item_id = str((row or {}).get("id") or "") if isinstance(row, dict) else None
        resolved_corr = correlation_id or self._get_ap_item_correlation_id(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
        )
        if resolved_corr:
            kwargs["_correlation_id"] = resolved_corr
        if source:
            kwargs["_source"] = source
        if workflow_id:
            kwargs["_workflow_id"] = workflow_id
        if run_id:
            kwargs["_run_id"] = run_id
        if decision_reason:
            kwargs["_decision_reason"] = decision_reason
        return bool(self.db.update_invoice_status(gmail_id=gmail_id, status=normalized_target, **kwargs))

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
        decision_idempotency_key: Optional[str] = None,
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
                    "decision_idempotency_key": decision_idempotency_key,
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
            logger.error("Could not save approval snapshot for %s: %s", gmail_id, exc)

    def _approval_snapshot_by_decision_key(
        self,
        ap_item_id: Optional[str],
        decision_idempotency_key: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if not ap_item_id or not decision_idempotency_key or not hasattr(self.db, "get_approval_by_decision_key"):
            return None
        try:
            return self.db.get_approval_by_decision_key(ap_item_id, decision_idempotency_key)
        except Exception as exc:
            logger.error("Could not read approval snapshot by decision key: %s", exc)
            return None

    @staticmethod
    def _approval_payload_dict(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(row, dict):
            return {}
        raw = row.get("decision_payload")
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    def _acquire_decision_action_lock(
        self,
        *,
        ap_item_id: Optional[str],
        decision_idempotency_key: Optional[str],
        actor_id: str,
        source_channel: str,
        correlation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not ap_item_id or not decision_idempotency_key:
            return True
        lock_key = f"approval_action_lock:{decision_idempotency_key}"
        try:
            if self.db.get_ap_audit_event_by_key(lock_key):
                return False
        except Exception:
            pass
        try:
            self.db.append_ap_audit_event(
                {
                    "ap_item_id": ap_item_id,
                    "event_type": "approval_action_lock_acquired",
                    "actor_type": "user",
                    "actor_id": actor_id,
                    "reason": "idempotency_lock_acquired",
                    "metadata": {"source_channel": source_channel, **(metadata or {})},
                    "organization_id": self.organization_id,
                    "source": source_channel,
                    "correlation_id": correlation_id,
                    "idempotency_key": lock_key,
                }
            )
            return True
        except Exception as exc:
            # Unique constraint races can surface here; treat an existing key as duplicate lock held.
            try:
                if self.db.get_ap_audit_event_by_key(lock_key):
                    return False
            except Exception:
                pass
            logger.error("Could not persist decision-action lock %s: %s", lock_key, exc)
            return True

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
            logger.error("Could not update AP metadata for %s: %s", ap_item_id, exc)

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _vendor_followup_sla_hours() -> int:
        try:
            hours = int(os.getenv("CLEARLEDGR_VENDOR_FOLLOWUP_SLA_HOURS", "24"))
        except (TypeError, ValueError):
            hours = 24
        return max(1, min(hours, 168))

    def _record_vendor_followup_event(
        self,
        *,
        ap_item_id: Optional[str],
        event_type: str,
        actor_type: str,
        actor_id: str,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "invoice_workflow",
        correlation_id: Optional[str] = None,
    ) -> None:
        if not ap_item_id:
            return
        try:
            self.db.append_ap_audit_event(
                {
                    "ap_item_id": ap_item_id,
                    "event_type": event_type,
                    "actor_type": actor_type,
                    "actor_id": actor_id,
                    "reason": reason,
                    "metadata": metadata or {},
                    "organization_id": self.organization_id,
                    "source": source,
                    "correlation_id": correlation_id,
                }
            )
        except Exception as exc:
            logger.error("Could not record vendor follow-up event for %s: %s", ap_item_id, exc)

    def _apply_needs_info_followup_metadata(
        self,
        *,
        ap_item_id: Optional[str],
        draft_id: Optional[str],
        question: Optional[str] = None,
        actor_type: str = "system",
        actor_id: str = "system",
        source: str = "invoice_workflow",
        correlation_id: Optional[str] = None,
    ) -> None:
        """Persist normalized follow-up metadata for needs_info items.

        The metadata is intentionally lightweight and operator-facing:
        - needs_info_draft_id
        - followup_last_sent_at
        - followup_attempt_count
        - followup_next_action
        - followup_sla_due_at
        """
        if not ap_item_id:
            return
        try:
            row = self.db.get_ap_item(ap_item_id) if hasattr(self.db, "get_ap_item") else None
            metadata = self._parse_metadata_dict((row or {}).get("metadata"))
        except Exception:
            metadata = {}

        attempts = max(0, self._safe_int(metadata.get("followup_attempt_count"), 0))
        updates: Dict[str, Any] = {}
        if question and str(question).strip():
            updates["needs_info_question"] = str(question).strip()

        if draft_id:
            now = datetime.now(timezone.utc)
            due_at = now + timedelta(hours=self._vendor_followup_sla_hours())
            attempts += 1
            updates.update(
                {
                    "needs_info_draft_id": str(draft_id),
                    "followup_last_sent_at": now.isoformat(),
                    "followup_attempt_count": attempts,
                    "followup_next_action": "await_vendor_response",
                    "followup_sla_due_at": due_at.isoformat(),
                }
            )
            self._update_ap_item_metadata(ap_item_id, updates)
            self._record_vendor_followup_event(
                ap_item_id=ap_item_id,
                event_type="vendor_followup_draft_prepared",
                actor_type=actor_type,
                actor_id=actor_id,
                reason="needs_info_followup_draft_prepared",
                metadata={
                    "draft_id": str(draft_id),
                    "followup_attempt_count": attempts,
                    "followup_sla_due_at": due_at.isoformat(),
                },
                source=source,
                correlation_id=correlation_id,
            )
            return

        updates.setdefault("followup_attempt_count", attempts)
        updates.setdefault("followup_next_action", "prepare_vendor_followup_draft")
        if updates:
            self._update_ap_item_metadata(ap_item_id, updates)
        self._record_vendor_followup_event(
            ap_item_id=ap_item_id,
            event_type="vendor_followup_draft_pending",
            actor_type=actor_type,
            actor_id=actor_id,
            reason="needs_info_followup_draft_pending",
            metadata={
                "followup_attempt_count": attempts,
                "followup_next_action": updates.get("followup_next_action", "prepare_vendor_followup_draft"),
            },
            source=source,
            correlation_id=correlation_id,
        )

    async def _create_needs_info_vendor_draft(
        self,
        *,
        ap_item_id: Optional[str],
        thread_id: str,
        to_email: str,
        invoice_data: Dict[str, Any],
        question: Optional[str],
        user_id: Optional[str],
    ) -> Optional[str]:
        """Create a Gmail follow-up draft for needs_info state (best effort)."""
        if not ap_item_id:
            return None
        try:
            from clearledgr.services.auto_followup import AutoFollowUpService
            from clearledgr.services.gmail_api import GmailAPIClient

            gmail_user_id = str(user_id or "me").strip() or "me"
            gmail_client = GmailAPIClient(user_id=gmail_user_id)
            authenticated = await gmail_client.ensure_authenticated()
            if not authenticated:
                return None

            followup_svc = AutoFollowUpService(organization_id=self.organization_id)
            return await followup_svc.create_gmail_draft(
                gmail_client=gmail_client,
                ap_item_id=ap_item_id,
                thread_id=thread_id,
                to_email=to_email,
                invoice_data=invoice_data,
                question=question,
            )
        except Exception as exc:
            logger.error("needs_info draft creation skipped for %s: %s", ap_item_id, exc)
            return None

    @staticmethod
    def _normalize_human_action(action: str) -> str:
        token = str(action or "").strip().lower()
        if token in {"approved", "approve"}:
            return "approve"
        if token in {"rejected", "reject"}:
            return "reject"
        if token in {"needs_info", "request_info", "request-info"}:
            return "request_info"
        return token

    @classmethod
    def _is_human_override(cls, claude_recommendation: Optional[str], human_action: str) -> bool:
        rec = str(claude_recommendation or "").strip().lower()
        action = cls._normalize_human_action(human_action)
        if not rec or not action:
            return False
        if action == "approve":
            return rec in {"escalate", "reject", "needs_info"}
        if action in {"reject", "request_info"}:
            return rec == "approve"
        return False

    def _get_ap_decision_recommendation(self, ap_item_id: Optional[str]) -> Optional[str]:
        if not ap_item_id or not hasattr(self.db, "get_ap_item"):
            return None
        try:
            row = self.db.get_ap_item(ap_item_id)
            if not row:
                return None
            meta_raw = row.get("metadata") or {}
            metadata = (
                meta_raw
                if isinstance(meta_raw, dict)
                else json.loads(meta_raw)
                if isinstance(meta_raw, str) and meta_raw.strip()
                else {}
            )
            rec = str(metadata.get("ap_decision_recommendation") or "").strip().lower()
            return rec or None
        except Exception:
            return None

    def _record_vendor_decision_feedback(
        self,
        *,
        ap_item_id: Optional[str],
        vendor_name: Optional[str],
        human_action: str,
        actor_id: str,
        source_channel: str,
        correlation_id: Optional[str] = None,
        reason: Optional[str] = None,
        action_outcome: Optional[str] = None,
        final_state: Optional[str] = None,
        was_approved: Optional[bool] = None,
        amount: Optional[float] = None,
        invoice_date: Optional[str] = None,
    ) -> None:
        """Persist human decision feedback and terminal vendor outcomes.

        This powers vendor-level recommendation adaptation in AP decision routing.
        """
        vendor = str(vendor_name or "").strip()
        if not vendor:
            return
        human_decision = self._normalize_human_action(human_action)
        if not human_decision:
            return
        agent_rec = self._get_ap_decision_recommendation(ap_item_id)
        is_override = self._is_human_override(agent_rec, human_decision)

        if hasattr(self.db, "record_vendor_decision_feedback"):
            try:
                self.db.record_vendor_decision_feedback(
                    self.organization_id,
                    vendor,
                    ap_item_id=ap_item_id,
                    human_decision=human_decision,
                    agent_recommendation=agent_rec,
                    decision_override=is_override,
                    reason=reason,
                    source_channel=source_channel,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    action_outcome=action_outcome,
                )
            except Exception as exc:
                logger.error("Could not persist vendor decision feedback: %s", exc)

        if (
            final_state
            and was_approved is not None
            and hasattr(self.db, "update_vendor_profile_from_outcome")
            and ap_item_id
        ):
            try:
                self.db.update_vendor_profile_from_outcome(
                    self.organization_id,
                    vendor,
                    ap_item_id=ap_item_id,
                    final_state=final_state,
                    was_approved=bool(was_approved),
                    approval_override=is_override,
                    agent_recommendation=agent_rec,
                    human_decision=human_decision,
                    amount=amount,
                    invoice_date=invoice_date,
                )
            except Exception as exc:
                logger.error("Could not update vendor profile from human outcome: %s", exc)

    def _maybe_record_ap_decision_override(
        self,
        ap_item_id: Optional[str],
        human_action: str,  # "approved" or "rejected"
        actor_id: str,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Emit ap_decision_override audit event when a human disagrees with Claude.

        Disagreement: human approved something Claude said escalate/reject,
        or human rejected something Claude said approve.
        """
        if not ap_item_id:
            return
        try:
            row = self.db.get_ap_item(ap_item_id)
            if not row:
                return
            meta_raw = row.get("metadata") or {}
            meta = meta_raw if isinstance(meta_raw, dict) else json.loads(meta_raw) if isinstance(meta_raw, str) and meta_raw.strip() else {}
            claude_rec = str(meta.get("ap_decision_recommendation") or "").strip().lower()
            if not claude_rec:
                return
            is_override = self._is_human_override(claude_rec, human_action)
            if not is_override:
                return
            self.db.append_ap_audit_event({
                "ap_item_id": ap_item_id,
                "event_type": "ap_decision_override",
                "actor_type": "user",
                "actor_id": actor_id,
                "reason": f"human_{human_action}_override_claude_{claude_rec}",
                "metadata": {
                    "human_action": human_action,
                    "claude_recommendation": claude_rec,
                    "claude_model": meta.get("ap_decision_model", "unknown"),
                },
                "organization_id": self.organization_id,
                "correlation_id": correlation_id,
                "source": "human_decision",
            })
            logger.info(
                "[APDecision] Override recorded: human=%s claude=%s ap_item=%s actor=%s",
                human_action, claude_rec, ap_item_id, actor_id,
            )
        except Exception as exc:
            logger.error("Could not record ap_decision_override: %s", exc)

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
        except Exception as e:
            logger.warning("Failed to parse invoice metadata: %s", e)
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

        # 0) Field-presence checks — required fields must be non-null/non-empty.
        #    PLAN.md §4.2-1: deterministic field presence/format check.
        _REQUIRED_FIELDS = {
            "vendor_name": invoice.vendor_name,
            "amount": invoice.amount,
            "invoice_number": invoice.invoice_number,
        }
        for field_name, field_val in _REQUIRED_FIELDS.items():
            if field_val is None or (isinstance(field_val, str) and not field_val.strip()):
                add_reason(
                    f"missing_required_field_{field_name}",
                    f"Required field '{field_name}' is missing or empty",
                    severity="error",
                    details={"field": field_name},
                )
            elif isinstance(field_val, (int, float)) and field_val <= 0:
                add_reason(
                    f"invalid_required_field_{field_name}",
                    f"Required field '{field_name}' has invalid value: {field_val}",
                    severity="error",
                    details={"field": field_name, "value": field_val},
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

        # 2) PO/receipt matching.
        #    - 3-way match when PO number is available (PO + GR + Invoice)
        #    - 2-way match when no PO but goods receipts exist (GR + Invoice)
        po_match_result: Optional[Dict[str, Any]] = (
            invoice.po_match_result if isinstance(invoice.po_match_result, dict) else None
        )
        if po_match_result is None:
            try:
                po_service = get_purchase_order_service(self.organization_id)
                if invoice.po_number:
                    match = po_service.match_invoice_to_po(
                        invoice_id=invoice.gmail_id,
                        invoice_amount=invoice.amount,
                        invoice_vendor=invoice.vendor_name,
                        invoice_po_number=invoice.po_number,
                        invoice_lines=None,
                    )
                else:
                    match = po_service.match_invoice_to_gr(
                        invoice_id=invoice.gmail_id,
                        invoice_amount=invoice.amount,
                        invoice_vendor=invoice.vendor_name,
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

        # 4) Duplicate invoice check — same vendor + invoice_number already exists.
        #    PLAN.md §4.2: deterministic dedup at validation boundary.
        if invoice.vendor_name and invoice.invoice_number:
            try:
                existing = None
                if hasattr(self.db, "get_ap_item_by_vendor_invoice"):
                    existing = self.db.get_ap_item_by_vendor_invoice(
                        self.organization_id,
                        invoice.vendor_name,
                        invoice.invoice_number,
                    )
                if existing and str(existing.get("state") or "") not in ("rejected",):
                    add_reason(
                        "duplicate_invoice",
                        f"Duplicate: invoice {invoice.invoice_number} from {invoice.vendor_name} already exists (state={existing.get('state')})",
                        severity="error",
                        details={
                            "existing_ap_item_id": str(existing.get("id") or ""),
                            "existing_state": str(existing.get("state") or ""),
                        },
                    )
            except Exception as dedup_exc:
                logger.warning("Duplicate check failed (non-fatal): %s", dedup_exc)

        # 5) Critical-field confidence gate (launch-critical, server-enforced).
        confidence_gate = self._evaluate_invoice_confidence_gate(invoice)
        if confidence_gate.get("requires_field_review"):
            add_reason(
                "confidence_field_review_required",
                "Critical extracted fields require review before posting",
                severity="warning",
                details={
                    "threshold": confidence_gate.get("threshold"),
                    "threshold_pct": confidence_gate.get("threshold_pct"),
                    "confidence_blockers": confidence_gate.get("confidence_blockers") or [],
                },
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
            "confidence_gate": confidence_gate,
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

    def _record_validation_gate_failure(
        self,
        invoice: InvoiceData,
        gate: Dict[str, Any],
        *,
        correlation_id: Optional[str] = None,
    ) -> None:
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
        except Exception as e:
            # Legacy status storage may not support rejection_reason updates at this stage.
            logger.warning("Failed to update invoice rejection status for %s: %s", invoice.gmail_id, e)

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
            if ap_item_id:
                # H1/H12: Populate exception_code and exception_severity on the AP item
                # at workflow time so they are durable and queryable (PLAN.md §4.4).
                primary_code = reason_codes[0] if reason_codes else "validation_failed"
                severity = "error"
                for r in (gate.get("reasons") or []):
                    if isinstance(r, dict) and r.get("severity") == "error":
                        severity = "error"
                        break
                try:
                    self.db.update_ap_item(
                        ap_item_id,
                        exception_code=primary_code,
                        exception_severity=severity,
                    )
                except Exception:
                    pass  # Non-fatal — audit event is the authoritative record
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
                        "correlation_id": correlation_id,
                        "source": "invoice_workflow",
                    }
                )
        except Exception as exc:
            logger.error("Could not append deterministic validation audit event: %s", exc)

    def _get_ap_decision(
        self,
        invoice: InvoiceData,
        validation_gate: Dict[str, Any],
    ):
        """Assemble vendor context and call APDecisionService. Never raises.

        Returns an APDecision object.  If the API key is absent or Claude fails,
        the service's built-in fallback reproduces the existing rule-based routing
        so the workflow is never blocked.
        """
        from clearledgr.services.ap_decision import APDecisionService

        decision_feedback: Dict[str, Any] = {}
        try:
            vendor_profile = (
                self.db.get_vendor_profile(self.organization_id, invoice.vendor_name)
                if hasattr(self.db, "get_vendor_profile") else None
            )
            vendor_history = (
                self.db.get_vendor_invoice_history(self.organization_id, invoice.vendor_name, limit=6)
                if hasattr(self.db, "get_vendor_invoice_history") else []
            )
            decision_feedback = (
                self.db.get_vendor_decision_feedback_summary(
                    self.organization_id,
                    invoice.vendor_name,
                    window_days=180,
                )
                if hasattr(self.db, "get_vendor_decision_feedback_summary")
                else {}
            )

            # Best-effort correction suggestions
            suggestions: Dict[str, Any] = {}
            try:
                from clearledgr.services.correction_learning import CorrectionLearningService
                svc = CorrectionLearningService(self.db, self.organization_id)
                gl_sug = svc.suggest("gl_code", {"vendor": invoice.vendor_name})
                if gl_sug:
                    suggestions["gl_code"] = gl_sug
            except Exception:
                pass

            org_config: Dict[str, Any] = {}
            try:
                cfg = self.db.get_org_config(self.organization_id) if hasattr(self.db, "get_org_config") else {}
                if isinstance(cfg, dict):
                    org_config = cfg
            except Exception:
                pass

            decision_svc = APDecisionService()
            decision = decision_svc.decide(
                invoice,
                vendor_profile=vendor_profile,
                vendor_history=vendor_history,
                decision_feedback=decision_feedback,
                correction_suggestions=suggestions,
                validation_gate=validation_gate,
                org_config=org_config,
            )
            logger.info(
                "[APDecision] %s → %s (confidence=%.2f fallback=%s): %s",
                invoice.vendor_name, decision.recommendation,
                decision.confidence, decision.fallback,
                decision.reasoning[:120],
            )
            return decision
        except Exception as exc:
            logger.warning("[APDecision] Unexpected error, using conservative fallback: %s", exc)
            from clearledgr.services.ap_decision import APDecisionService
            return APDecisionService()._fallback_decision(
                invoice,
                validation_gate,
                decision_feedback=decision_feedback,
            )

    async def process_new_invoice(self, invoice: InvoiceData) -> Dict[str, Any]:
        """
        Process a newly detected invoice email.
        
        Flow:
        1. Save invoice to database with 'received' status
        2. If confidence >= threshold, auto-approve and post
        3. Otherwise, send to Slack for approval
        
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

        # Save invoice to database (canonical AP state: received)
        invoice_id = self.db.save_invoice_status(
            gmail_id=invoice.gmail_id,
            status="received",
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
        correlation_id = self._ensure_ap_item_correlation_id(
            ap_item_id=invoice_id,
            gmail_id=invoice.gmail_id,
            preferred=invoice.correlation_id,
        )
        invoice.correlation_id = correlation_id

        # Deterministic controls always run before confidence-based routing.
        validation_gate = self._evaluate_deterministic_validation(invoice)
        confidence_gate = validation_gate.get("confidence_gate") if isinstance(validation_gate, dict) else None
        self._update_ap_item_metadata(
            invoice_id,
            {
                "validation_gate": validation_gate,
                "confidence_gate": confidence_gate or {},
                "requires_field_review": bool(
                    isinstance(confidence_gate, dict) and confidence_gate.get("requires_field_review")
                ),
                "confidence_blockers": (
                    confidence_gate.get("confidence_blockers") if isinstance(confidence_gate, dict) else []
                ) or [],
                "field_confidences": invoice.field_confidences or {},
                "correlation_id": correlation_id,
            },
        )

        # Validation/extraction completed: advance AP item to canonical `validated`
        # before routing to human approval or auto-posting.
        self._transition_invoice_state(
            invoice.gmail_id,
            "validated",
            correlation_id=correlation_id,
            workflow_id="invoice_entry",
        )

        # --- AP reasoning layer: Claude decides with vendor context ---
        ap_decision = self._get_ap_decision(invoice, validation_gate)

        # Populate InvoiceData reasoning fields (surfaced in Slack cards, Gmail sidebar)
        invoice.reasoning_summary = ap_decision.reasoning
        invoice.reasoning_risks = ap_decision.risk_flags
        invoice.vendor_intelligence = {
            **(invoice.vendor_intelligence or {}),
            "vendor_context": ap_decision.vendor_context_used,
            "ap_decision": ap_decision.recommendation,
            "decision_feedback": {
                "count": ap_decision.vendor_context_used.get("feedback_count", 0),
                "override_rate": ap_decision.vendor_context_used.get("feedback_override_rate", 0.0),
                "strictness_bias": ap_decision.vendor_context_used.get("feedback_strictness_bias", "neutral"),
            },
        }

        # Persist Claude's reasoning into ap_item metadata so the Gmail sidebar
        # card can show it proactively (without requiring the "Why?" button click).
        # Use invoice_id directly — it was returned by save_invoice_status() above,
        # so we know the row exists. _lookup_ap_item_id would silently return None here.
        self._update_ap_item_metadata(
            invoice_id,
            {
                "ap_decision_reasoning": ap_decision.reasoning[:1024],  # cap length
                "ap_decision_recommendation": ap_decision.recommendation,
                "ap_decision_risk_flags": ap_decision.risk_flags,
                "ap_decision_model": ap_decision.model,
                "vendor_intelligence": invoice.vendor_intelligence,
            },
        )

        # Deterministic gate is a hard guardrail that overrides Claude.
        # If it fires, route to human — but use Claude's reasoning as context.
        if not validation_gate.get("passed", True):
            self._record_validation_gate_failure(
                invoice,
                validation_gate,
                correlation_id=correlation_id,
            )
            logger.info(
                "Routing invoice %s to approval due to deterministic controls: %s",
                invoice.gmail_id,
                ", ".join(validation_gate.get("reason_codes") or []),
            )
            result = await self._send_for_approval(
                invoice,
                extra_context={
                    "validation_gate": validation_gate,
                    "ap_decision": ap_decision.recommendation,
                    "ap_reasoning": ap_decision.reasoning,
                },
            )
            if isinstance(result, dict):
                result.setdefault("validation_gate", validation_gate)
                result.setdefault("reason_codes", validation_gate.get("reason_codes") or [])
            return result

        # Claude says needs_info: transition to needs_info state with the exact question.
        if ap_decision.recommendation == "needs_info" and ap_decision.info_needed:
            logger.info(
                "AP decision needs_info for %s: %s",
                invoice.gmail_id, ap_decision.info_needed[:80],
            )
            self._transition_invoice_state(
                invoice.gmail_id, "needs_info",
                correlation_id=correlation_id,
                decision_reason="ap_decision_needs_info",
            )
            ap_item_id = self._lookup_ap_item_id(invoice.gmail_id)
            self._update_ap_item_metadata(
                ap_item_id,
                {
                    "needs_info_question": ap_decision.info_needed,
                    "ap_decision_reasoning": ap_decision.reasoning,
                    "ap_decision_risk_flags": ap_decision.risk_flags,
                },
            )
            draft_id = await self._create_needs_info_vendor_draft(
                ap_item_id=ap_item_id,
                thread_id=invoice.gmail_id,
                to_email=invoice.sender,
                invoice_data={
                    "subject": invoice.subject,
                    "vendor_name": invoice.vendor_name,
                    "amount": invoice.amount,
                    "invoice_number": invoice.invoice_number,
                },
                question=ap_decision.info_needed,
                user_id=invoice.user_id,
            )
            self._apply_needs_info_followup_metadata(
                ap_item_id=ap_item_id,
                draft_id=draft_id,
                question=ap_decision.info_needed,
                actor_type="system",
                actor_id="ap_agent",
                source="invoice_workflow",
                correlation_id=correlation_id,
            )

            return {
                "status": "needs_info",
                "invoice_id": invoice.gmail_id,
                "reason": ap_decision.reasoning,
                "info_needed": ap_decision.info_needed,
                "risk_flags": ap_decision.risk_flags,
                "ap_decision": "needs_info",
            }

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
        
        # Route based on Claude's recommendation (gate already passed above).
        if ap_decision.recommendation == "approve":
            logger.info(
                "AP decision approve for %s (confidence=%.2f fallback=%s)",
                invoice.gmail_id, ap_decision.confidence, ap_decision.fallback,
            )
            return await self._auto_approve_and_post(
                invoice, reason=f"ap_decision_approve"
            )

        if ap_decision.recommendation == "reject":
            logger.info(
                "AP decision reject for %s: %s",
                invoice.gmail_id, ap_decision.reasoning[:80],
            )
            return await self._send_for_approval(
                invoice,
                extra_context={
                    "ap_decision": "reject",
                    "ap_reasoning": ap_decision.reasoning,
                    "risk_flags": ap_decision.risk_flags,
                },
            )

        # escalate or unrecognised recommendation → send for human approval
        return await self._send_for_approval(
            invoice,
            extra_context={
                "ap_decision": ap_decision.recommendation,
                "ap_reasoning": ap_decision.reasoning,
                "risk_flags": ap_decision.risk_flags,
            },
        )
    
    async def _auto_approve_and_post(
        self, 
        invoice: InvoiceData, 
        reason: str = "high_confidence",
    ) -> Dict[str, Any]:
        """Auto-approve invoice and post to ERP."""
        existing = self.db.get_invoice_status(invoice.gmail_id)
        existing_state = self._canonical_invoice_state(existing)
        if existing_state in {"posted_to_erp", "closed"}:
            return {
                "status": "already_posted",
                "invoice_id": invoice.gmail_id,
                "erp_bill_id": (existing or {}).get("erp_bill_id") or (existing or {}).get("erp_reference"),
            }
        if existing and (existing.get("erp_reference") or existing.get("erp_bill_id")):
            return {
                "status": "already_posted",
                "invoice_id": invoice.gmail_id,
                "erp_bill_id": existing.get("erp_bill_id") or existing.get("erp_reference"),
            }

        ap_item_id = self._lookup_ap_item_id(
            gmail_id=invoice.gmail_id,
            vendor_name=invoice.vendor_name,
            invoice_number=invoice.invoice_number,
        )
        correlation_id = self._ensure_ap_item_correlation_id(
            ap_item_id=ap_item_id,
            gmail_id=invoice.gmail_id,
            preferred=invoice.correlation_id,
        )
        invoice.correlation_id = correlation_id

        # Canonical AP path for auto-approval:
        # validated -> needs_approval -> approved -> ready_to_post
        approved_by = f"clearledgr-auto:{reason}"
        approved_at = datetime.now(timezone.utc).isoformat()
        current_state = existing_state or self._canonical_invoice_state(self.db.get_invoice_status(invoice.gmail_id))

        if current_state == "received":
            self._transition_invoice_state(invoice.gmail_id, "validated", correlation_id=correlation_id)
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(invoice.gmail_id))
        if current_state == "validated":
            self._transition_invoice_state(invoice.gmail_id, "needs_approval", correlation_id=correlation_id)
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(invoice.gmail_id))
        if current_state in {"needs_approval", "approved"}:
            self._transition_invoice_state(
                gmail_id=invoice.gmail_id,
                target_state="approved",
                correlation_id=correlation_id,
                approved_by=approved_by,
                approved_at=approved_at,
            )
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(invoice.gmail_id))
        if current_state in {"approved", "ready_to_post"}:
            self._transition_invoice_state(invoice.gmail_id, "ready_to_post", correlation_id=correlation_id)
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(invoice.gmail_id))
        if current_state not in {"ready_to_post"}:
            return {
                "status": "error",
                "invoice_id": invoice.gmail_id,
                "reason": f"invalid_state_for_auto_post:{current_state or 'unknown'}",
            }
        
        # Post to ERP
        result = await self._post_to_erp(invoice, correlation_id=correlation_id)
        post_attempted_at = datetime.now(timezone.utc).isoformat()
        
        if result.get("status") == "success":
            erp_reference = (
                result.get("erp_reference")
                or result.get("bill_id")
                or result.get("reference_id")
                or result.get("doc_num")
            )
            self._transition_invoice_state(
                gmail_id=invoice.gmail_id,
                target_state="posted_to_erp",
                correlation_id=correlation_id,
                erp_reference=erp_reference,
                erp_posted_at=post_attempted_at,
                post_attempted_at=post_attempted_at,
                last_error=None,
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

            # VENDOR INTELLIGENCE: Update vendor profile from this outcome
            try:
                ap_item_id = self._lookup_ap_item_id(invoice.gmail_id)
                agent_rec = (invoice.vendor_intelligence or {}).get("ap_decision")
                if hasattr(self.db, "update_vendor_profile_from_outcome") and ap_item_id:
                    self.db.update_vendor_profile_from_outcome(
                        self.organization_id,
                        invoice.vendor_name,
                        ap_item_id=ap_item_id,
                        final_state="posted_to_erp",
                        was_approved=True,
                        approval_override=False,
                        agent_recommendation=str(agent_rec or "approve"),
                        human_decision=None,
                        amount=invoice.amount,
                        invoice_date=invoice.due_date,
                    )
            except Exception as exc:
                logger.error("[VendorStore] Failed to update vendor profile after auto-post: %s", exc)
            
            # Notify in Slack (informational, not approval)
            try:
                await self._send_posted_notification(invoice, result, reason)
            except Exception as e:
                logger.warning(f"Failed to send Slack notification: {e}")

            # M1: Transition posted_to_erp → closed (terminal state).
            # All post-processing (learning, vendor profile, notifications) is
            # complete — the AP item lifecycle is finished.
            try:
                self._transition_invoice_state(
                    gmail_id=invoice.gmail_id,
                    target_state="closed",
                    correlation_id=correlation_id,
                )
            except Exception as close_exc:
                logger.warning("Failed to transition to closed: %s", close_exc)
        else:
            failure_reason = (
                str(result.get("error_message") or "")
                or str(result.get("reason") or "")
                or str(result.get("status") or "")
                or "erp_post_failed"
            )
            self._transition_invoice_state(
                gmail_id=invoice.gmail_id,
                target_state="failed_post",
                correlation_id=correlation_id,
                post_attempted_at=post_attempted_at,
                last_error=failure_reason,
            )
        
        return {
            "status": "auto_approved" if result.get("status") == "success" else "error",
            "invoice_id": invoice.gmail_id,
            "reason": reason,
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
            self._transition_invoice_state(
                gmail_id=invoice.gmail_id,
                target_state="needs_approval",
                slack_thread_id=existing_thread.get("thread_id") or existing_thread.get("thread_ts"),
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
            teams_status = self._send_teams_budget_card(invoice, budget_summary, context_payload)
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
        self._transition_invoice_state(
            gmail_id=invoice.gmail_id,
            target_state="needs_approval",
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
            self._transition_invoice_state(
                gmail_id=invoice.gmail_id,
                target_state="needs_approval",
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
            teams_status = self._send_teams_budget_card(invoice, budget_summary, context_payload)
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

            # H4: Audit approval request dispatch (PLAN.md §4.7)
            if ap_item_id:
                channels_notified = ["slack"]
                if isinstance(teams_status, dict) and teams_status.get("status") == "sent":
                    channels_notified.append("teams")
                try:
                    self.db.append_ap_audit_event(
                        {
                            "ap_item_id": ap_item_id,
                            "event_type": "approval_requested",
                            "actor_type": "system",
                            "actor_id": "invoice_workflow",
                            "reason": f"Approval request sent to {', '.join(channels_notified)}",
                            "metadata": {
                                "channels": channels_notified,
                                "slack_channel": message.channel,
                                "slack_ts": message.ts,
                                "vendor": invoice.vendor_name,
                                "amount": invoice.amount,
                            },
                            "organization_id": self.organization_id,
                            "source": "invoice_workflow",
                        }
                    )
                except Exception:
                    pass  # Non-fatal

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

    def _send_teams_budget_card(
        self,
        invoice: InvoiceData,
        budget_summary: Dict[str, Any],
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Best-effort Teams delivery for approval/budget decisions."""
        client = self.teams_client
        if client is None:
            return {"status": "skipped", "reason": "teams_client_unavailable"}
        try:
            approval_copy = self._build_approval_surface_copy(
                invoice=invoice,
                extra_context=extra_context or {"budget": budget_summary},
                budget_summary=budget_summary,
            )
            result = client.send_invoice_budget_card(
                email_id=invoice.gmail_id,
                organization_id=self.organization_id,
                vendor=invoice.vendor_name,
                amount=invoice.amount,
                currency=invoice.currency,
                invoice_number=invoice.invoice_number,
                budget=budget_summary,
                decision_reason_summary=approval_copy.get("why_summary"),
                next_step_lines=(
                    ([f"Recommended now: {approval_copy.get('recommended_action_text')}"] if approval_copy.get("recommended_action_text") else [])
                    + (approval_copy.get("what_happens_next") or [])
                ),
                requested_by_text=approval_copy.get("requested_by_text"),
                source_of_truth_text=approval_copy.get("source_of_truth_text"),
                source_url=approval_copy.get("gmail_url"),
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
        except Exception as e:
            # Approval flow must not fail due to optional context derivation.
            logger.warning("Optional context derivation failed: %s", e)

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

    @staticmethod
    def _humanize_reason_code(code: Any) -> str:
        raw = str(code or "").strip()
        if not raw:
            return ""
        return raw.replace("_", " ")

    @staticmethod
    def _dedupe_reason_lines(lines: List[str], limit: int = 3) -> List[str]:
        deduped: List[str] = []
        seen = set()
        for line in lines:
            text = str(line or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(text)
            if len(deduped) >= max(1, int(limit)):
                break
        return deduped

    def _build_approval_surface_copy(
        self,
        invoice: InvoiceData,
        extra_context: Optional[Dict[str, Any]] = None,
        budget_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build parity copy for Slack/Teams approval cards (AX7)."""
        extra_context = extra_context or {}
        budget_summary = budget_summary or {}
        gmail_url = f"https://mail.google.com/mail/u/0/#search/{invoice.gmail_id}"

        why_scored: List[tuple[int, str]] = []
        budget_status = str((budget_summary or {}).get("status") or "").strip().lower()
        if bool((budget_summary or {}).get("requires_decision")) or budget_status in {"critical", "exceeded"}:
            if budget_status in {"critical", "exceeded"}:
                why_scored.append(
                    (120, f"Budget check is {budget_status.replace('_', ' ')} and requires an approval decision.")
                )
            else:
                why_scored.append((105, "Budget check requires an approval decision before posting."))

        confidence_gate = extra_context.get("confidence_gate")
        confidence_gate = confidence_gate if isinstance(confidence_gate, dict) else {}
        confidence_blockers = confidence_gate.get("blockers")
        if not isinstance(confidence_blockers, list):
            confidence_blockers = []
        if bool(confidence_gate.get("requires_field_review")) or confidence_blockers:
            blocker = confidence_blockers[0] if confidence_blockers else {}
            if isinstance(blocker, dict):
                field = str(blocker.get("field") or blocker.get("code") or "critical field").replace("_", " ")
                why_scored.append((95, f"Extraction confidence is low for {field}; human review is required."))
            else:
                why_scored.append((90, "Extraction confidence is low for a critical field; human review is required."))
        elif float(invoice.confidence or 0.0) < 0.95:
            why_scored.append(
                (70, f"Extraction confidence is {invoice.confidence * 100:.0f}%, so the agent is asking for a review before posting.")
            )

        validation_gate = extra_context.get("validation_gate")
        validation_gate = validation_gate if isinstance(validation_gate, dict) else {}
        validation_reasons = validation_gate.get("reasons")
        if not isinstance(validation_reasons, list):
            validation_reasons = []
        for reason in validation_reasons[:2]:
            if not isinstance(reason, dict):
                continue
            message = str(reason.get("message") or "").strip()
            code = self._humanize_reason_code(reason.get("code"))
            if message:
                why_scored.append((85, message))
            elif code:
                why_scored.append((80, f"Validation flagged: {code}."))
        if not why_scored:
            reason_codes = validation_gate.get("reason_codes")
            if isinstance(reason_codes, list):
                for code in reason_codes[:2]:
                    text = self._humanize_reason_code(code)
                    if text:
                        why_scored.append((72, f"Validation flagged: {text}."))

        po_match = extra_context.get("po_match_result")
        po_match = po_match if isinstance(po_match, dict) else {}
        po_exceptions = po_match.get("exceptions") if isinstance(po_match.get("exceptions"), list) else []
        if po_exceptions:
            first_po_exception = po_exceptions[0]
            if isinstance(first_po_exception, dict):
                po_type = str(first_po_exception.get("type") or first_po_exception.get("code") or "").strip().lower()
                if po_type:
                    why_scored.append((88, f"PO/receipt exception detected: {po_type.replace('_', ' ')}."))

        approval_context = extra_context.get("approval_context")
        approval_context = approval_context if isinstance(approval_context, dict) else {}
        open_vendor_items = int(approval_context.get("vendor_open_invoices") or 0)
        if open_vendor_items > 1:
            why_scored.append((60, f"Vendor has {open_vendor_items} open invoice(s), so this decision impacts current AP queue risk."))

        if int(invoice.potential_duplicates or 0) > 0:
            why_scored.append(
                (92, f"Potential duplicate risk detected ({int(invoice.potential_duplicates)} similar invoice(s)).")
            )

        if not why_scored:
            why_scored.append((50, "Approval is required before the AP workflow can post this invoice to ERP."))

        why_candidates = [line for _score, line in sorted(why_scored, key=lambda entry: entry[0], reverse=True)]
        why_summary = " ".join(self._dedupe_reason_lines(why_candidates, limit=2)).strip()

        requires_budget_decision = bool((budget_summary or {}).get("requires_decision"))
        hard_budget_block = bool((budget_summary or {}).get("hard_block")) or budget_status == "exceeded"
        confidence_requires_review = bool(confidence_gate.get("requires_field_review")) or bool(confidence_blockers)
        has_validation_blockers = bool(validation_reasons) or bool(validation_gate.get("reason_codes"))
        has_duplicate_risk = int(invoice.potential_duplicates or 0) > 0
        recommended_action_text = (
            "Request budget adjustment unless this invoice is business-critical and override is justified."
            if requires_budget_decision and hard_budget_block
            else "Approve override with explicit justification, or request budget clarification."
            if requires_budget_decision
            else "Request info first to resolve policy/evidence blockers before posting."
            if has_validation_blockers or confidence_requires_review
            else "Reject only if duplicate risk is confirmed; otherwise request clarification."
            if has_duplicate_risk
            else "Approve / Post to ERP once checks look correct."
        )

        if requires_budget_decision:
            approve_line = (
                "Approve override: records hard-budget-block justification, then the AP workflow posts to ERP (API-first, browser fallback if needed)."
                if hard_budget_block
                else "Approve override: records justification, then the AP workflow posts to ERP (API-first, browser fallback if needed)."
            )
            request_info_line = (
                "Request info: routes back for budget or policy clarification and preserves AP audit linkage."
                if has_validation_blockers
                else "Request info: sends the invoice back for clarification and keeps the audit trail intact."
            )
            reject_line = "Reject: marks the invoice rejected and records the decision in the AP audit trail."
            if has_duplicate_risk:
                reject_line = (
                    "Reject: use when duplicate risk is confirmed; invoice is marked rejected and linked in the AP audit trail."
                )
            next_lines = [approve_line, request_info_line, reject_line]
        else:
            approve_line = (
                "Approve / Post to ERP: captures confidence override context for flagged fields, then attempts ERP posting (API-first, browser fallback if needed)."
                if confidence_requires_review
                else "Approve / Post to ERP: the AP workflow attempts ERP posting automatically (API-first, browser fallback if needed)."
            )
            request_info_line = (
                "Request info: returns the invoice to needs-info and asks for missing policy/evidence details before posting."
                if has_validation_blockers
                else "Request info: returns the invoice to needs-info so the agent can collect missing details."
            )
            reject_line = "Reject: records the rejection and stops further posting for this invoice."
            if has_duplicate_risk:
                reject_line = (
                    "Reject: use when duplicate risk is confirmed; rejection is recorded and posting is stopped for this invoice."
                )
            next_lines = [approve_line, request_info_line, reject_line]

        return {
            "why_summary": why_summary,
            "what_happens_next": next_lines,
            "recommended_action_text": recommended_action_text,
            "requested_by_text": "Requested by Clearledgr AP Agent on behalf of the AP workflow.",
            "source_of_truth_text": "Source of truth: Gmail thread and Clearledgr AP context (Open in Gmail / View in Gmail).",
            "gmail_url": gmail_url,
        }
    
    def _build_approval_blocks(
        self,
        invoice: InvoiceData,
        extra_context: Optional[Dict] = None,
    ) -> list:
        """Build compact Slack Block Kit blocks for approval request.

        Structure:
        1. Header (1 block)
        2. Invoice details (1 block - 4 fields)
        3. Flags - only if something needs attention (0-2 blocks)
        4. Actions (1 block)
        5. Footer context (1 block)
        """
        # ========== CONFIDENCE ==========
        if invoice.confidence >= 0.9:
            confidence_text = f"High ({invoice.confidence*100:.0f}%)"
        elif invoice.confidence >= 0.7:
            confidence_text = f"Medium ({invoice.confidence*100:.0f}%)"
        else:
            confidence_text = f"Low ({invoice.confidence*100:.0f}%)"

        # ========== DUE DATE WARNING ==========
        due_warning = ""
        days_until = invoice.priority.get("days_until_due") if invoice.priority else None
        if days_until is not None:
            if days_until < 0:
                due_warning = f" *OVERDUE {abs(days_until)}d*"
            elif days_until == 0:
                due_warning = " *DUE TODAY*"
            elif days_until <= 3:
                due_warning = f" _{days_until}d left_"
        elif invoice.due_date:
            try:
                due = datetime.strptime(invoice.due_date, "%Y-%m-%d")
                days_until = (due - datetime.now()).days
                if days_until < 0:
                    due_warning = f" *OVERDUE {abs(days_until)}d*"
                elif days_until <= 3:
                    due_warning = f" _{days_until}d left_"
            except Exception:
                pass

        # ========== PO MATCH STATUS ==========
        po_text = "N/A"
        po_match = getattr(invoice, "po_match_result", None)
        if not po_match and extra_context:
            po_match = (extra_context or {}).get("po_match_result")
        if po_match:
            po_num = po_match.get("po_number") or po_match.get("po_id")
            match_status = po_match.get("match_status", "").lower()
            if po_num and "match" in match_status and "exception" not in match_status:
                po_text = f"#{po_num} matched"
            elif po_num:
                po_text = f"#{po_num} (exceptions)"
            else:
                po_text = "No match"
        elif invoice.po_number:
            po_text = f"#{invoice.po_number}"

        # ========== HEADER ==========
        priority_level = invoice.priority.get("priority", "") if invoice.priority else ""
        priority_text = invoice.priority.get("priority_label", "") if invoice.priority else ""
        if priority_level == "CRITICAL":
            header_text = "CRITICAL: Invoice Approval"
        elif priority_level == "HIGH":
            header_text = "HIGH: Invoice Approval"
        elif priority_text == "URGENT":
            header_text = "URGENT: Invoice Approval"
        else:
            header_text = "Invoice Approval"

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": header_text}},
        ]

        # ========== EXTRACTION SOURCE CONTEXT (Pillar 2) ==========
        extracted_fields = []
        missing_fields = []
        for field_name, value in [("vendor", invoice.vendor_name), ("amount", invoice.amount), ("invoice #", invoice.invoice_number), ("due date", invoice.due_date), ("PO #", invoice.po_number)]:
            if value and str(value) not in ("N/A", "0", "0.0", "None", ""):
                extracted_fields.append(field_name)
            else:
                missing_fields.append(field_name)

        source_parts = []
        if extracted_fields:
            source_parts.append(f"Extracted: {', '.join(extracted_fields)}")
        if missing_fields:
            source_parts.append(f"Missing: {', '.join(missing_fields)}")

        if source_parts:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": " | ".join(source_parts) + f" | Confidence: {confidence_text}"}]
            })

        # ========== AGENT REASONING (only when populated) ==========
        if invoice.reasoning_summary:
            reasoning_parts = [f"*Agent:* {invoice.reasoning_summary}"]

            if invoice.reasoning_factors:
                factor_strs = []
                for f in invoice.reasoning_factors[:4]:
                    name = str(f.get("factor", "")).replace("_", " ").title()
                    score = f.get("score", 0)
                    detail = f.get("detail", "")
                    factor_strs.append(f"{name}: {score:.1f}" + (f" — {detail}" if detail else ""))
                if factor_strs:
                    reasoning_parts.append("*Factors:* " + " | ".join(factor_strs))

            if invoice.reasoning_risks:
                risk_text = " | ".join(str(r) for r in invoice.reasoning_risks[:3])
                reasoning_parts.append(f"*Risks:* {risk_text}")

            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(reasoning_parts)},
            })

        # ========== MAIN DETAILS (4 fields) ==========
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Vendor:*\n{invoice.vendor_name}"},
                {"type": "mrkdwn", "text": f"*Amount:*\n{invoice.currency} {invoice.amount:,.2f}"},
                {"type": "mrkdwn", "text": f"*Invoice #:*\n{invoice.invoice_number or 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Due:*\n{invoice.due_date or 'N/A'}{due_warning}"},
            ]
        })

        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*PO:*\n{po_text}"},
                {"type": "mrkdwn", "text": f"*GL:*\n{(invoice.vendor_intelligence or {}).get('suggested_gl', 'Auto')}"},
            ]
        })

        # ========== FLAGS (only when something needs attention) ==========

        # Budget impact — only show if warning/critical/exceeded
        budget_checks = self._normalize_budget_checks(invoice.budget_impact)
        if not budget_checks and extra_context:
            budget_checks = self._normalize_budget_checks(extra_context.get("budget_impact"))
        budget_summary = self._compute_budget_summary(budget_checks) if budget_checks else {
            "status": "healthy",
            "requires_decision": False,
        }
        approval_copy = self._build_approval_surface_copy(
            invoice=invoice,
            extra_context=extra_context or {},
            budget_summary=budget_summary,
        )

        flagged_budgets = [b for b in (budget_checks or []) if str(b.get("after_approval_status") or b.get("status") or "").lower() in ("warning", "critical", "exceeded")]
        if flagged_budgets:
            budget_lines = []
            for budget in flagged_budgets[:2]:
                status = str(budget.get("after_approval_status") or budget.get("status") or "").lower()
                try:
                    pct = float(budget.get("after_approval_percent") or budget.get("percent_used") or 0)
                except (TypeError, ValueError):
                    pct = 0.0
                name = str(budget.get("budget_name") or "Budget")
                marker = "RED" if status == "exceeded" else "AMBER"
                budget_lines.append(f"• *{name}*  {marker} {pct:.0f}% used")
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Budget:* " + " | ".join(budget_lines)}
            })

        # Policy violations — only show if non-compliant
        if invoice.policy_compliance and not invoice.policy_compliance.get("compliant", True):
            violations = invoice.policy_compliance.get("violations", [])[:2]
            if violations:
                viol_text = " | ".join(v.get("message", "") for v in violations if v.get("message"))
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Policy:* {viol_text}"}
                })

        # Duplicate warning
        if invoice.potential_duplicates and invoice.potential_duplicates > 0:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Duplicate:* {invoice.potential_duplicates} similar invoice(s) found"}
            })

        # Validation gate issues
        validation_gate = (extra_context or {}).get("validation_gate") if extra_context else None
        if validation_gate and validation_gate.get("reason_codes"):
            reasons = validation_gate.get("reasons") or []
            gate_msgs = [str(r.get("message") or r.get("code", "")) for r in reasons[:2] if isinstance(r, dict)]
            if gate_msgs:
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Validation:* " + " | ".join(gate_msgs)}
                })

        why_summary = str(approval_copy.get("why_summary") or "").strip()
        if why_summary:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Why this needs your decision:*\n{why_summary}"},
                }
            )
        recommended_action_text = str(approval_copy.get("recommended_action_text") or "").strip()
        if recommended_action_text:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Recommended now:*\n{recommended_action_text}"},
                }
            )

        what_happens_next = approval_copy.get("what_happens_next")
        if isinstance(what_happens_next, list) and what_happens_next:
            next_lines = [f"• {str(line).strip()}" for line in what_happens_next[:3] if str(line).strip()]
            if next_lines:
                blocks.append(
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "*What happens next:*\n" + "\n".join(next_lines)},
                    }
                )

        # ========== ACTIONS ==========
        requires_budget_decision = bool(budget_summary.get("requires_decision"))
        approval_override_value = json.dumps({
            "gmail_id": invoice.gmail_id,
            "justification": "Approved over budget in Slack",
            "decision": "approve_override",
        })

        gmail_link = f"https://mail.google.com/mail/u/0/#search/{invoice.gmail_id}"

        blocks.append({"type": "divider"})
        blocks.append({
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
                        "text": {"type": "plain_text", "text": "Request info"},
                        "action_id": f"request_info_{invoice.gmail_id}",
                        "value": invoice.gmail_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "action_id": f"reject_budget_{invoice.gmail_id}",
                        "value": invoice.gmail_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View in Gmail"},
                        "action_id": f"view_invoice_{invoice.gmail_id}",
                        "url": gmail_link,
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
                        "text": {"type": "plain_text", "text": "Request info"},
                        "action_id": f"request_info_{invoice.gmail_id}",
                        "value": invoice.gmail_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View in Gmail"},
                        "action_id": f"view_invoice_{invoice.gmail_id}",
                        "url": gmail_link,
                    },
                ]
            )
        })

        # Footer
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"From: {invoice.sender} | {invoice.gmail_id}"},
                {"type": "mrkdwn", "text": str(approval_copy.get("requested_by_text") or "Requested by Clearledgr AP Agent on behalf of the AP workflow.")},
                {"type": "mrkdwn", "text": str(approval_copy.get("source_of_truth_text") or "Source of truth: Gmail thread and Clearledgr AP context.")},
            ]
        })

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
        actor_display: Optional[str] = None,
        action_run_id: Optional[str] = None,
        decision_request_ts: Optional[str] = None,
        decision_idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
        allow_budget_override: bool = False,
        override_justification: Optional[str] = None,
        allow_confidence_override: bool = False,
        field_confidences: Optional[Dict[str, Any]] = None,
        allow_po_exception_override: bool = False,
        po_override_reason: Optional[str] = None,
        override_context: Optional["OverrideContext"] = None,  # structured override metadata
    ) -> Dict[str, Any]:
        """
        Approve an invoice and post to ERP.
        
        Called when user clicks Approve in Slack or Gmail extension.
        """
        # Get invoice data
        invoice_data = self.db.get_invoice_status(gmail_id)
        if not invoice_data:
            return {"status": "error", "reason": "Invoice not found"}

        resolved_source_channel = str(source_channel or "slack").strip().lower() or "slack"
        resolved_channel_id = source_channel_id or slack_channel
        resolved_message_ref = source_message_ref or slack_ts
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=gmail_id,
            vendor_name=invoice_data.get("vendor") or invoice_data.get("vendor_name"),
            invoice_number=invoice_data.get("invoice_number"),
        )
        correlation_id = self._ensure_ap_item_correlation_id(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
            preferred=correlation_id,
        )
        existing_decision_snapshot = self._approval_snapshot_by_decision_key(
            ap_item_id,
            decision_idempotency_key,
        )
        if existing_decision_snapshot:
            existing_status = str(existing_decision_snapshot.get("status") or "").strip().lower()
            existing_payload = self._approval_payload_dict(existing_decision_snapshot)
            if existing_status == "approved":
                return {
                    "status": "approved",
                    "invoice_id": gmail_id,
                    "approved_by": approved_by,
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                    "erp_result": existing_payload.get("erp_result") or {},
                    "reason": "duplicate_approval_action",
                }
            if existing_status == "failed":
                return {
                    "status": "error",
                    "invoice_id": gmail_id,
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                    "reason": "duplicate_approval_action",
                    "erp_result": existing_payload.get("erp_result") or {},
                }
            if existing_status == "processing":
                return {
                    "status": "duplicate_in_progress",
                    "invoice_id": gmail_id,
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                    "reason": "duplicate_approval_action_in_progress",
                }
        
        invoice_state = self._canonical_invoice_state(invoice_data)
        if invoice_state in {"posted_to_erp", "closed"}:
            return {"status": "error", "reason": "Invoice already posted"}
        if invoice_data.get("erp_bill_id") or invoice_data.get("erp_reference"):
            return {"status": "error", "reason": "Invoice already posted"}

        budget_checks = self._load_budget_context_from_invoice_row(invoice_data)
        budget_summary = self._compute_budget_summary(budget_checks)
        confidence_gate = self._evaluate_invoice_row_confidence_gate(
            invoice_data,
            field_confidences_override=field_confidences,
        )
        confidence_blockers = confidence_gate.get("confidence_blockers") or []

        # Persist per-field confidences to the AP item row so accuracy trends
        # are queryable without re-parsing audit events.
        if ap_item_id:
            gate_field_confidences = confidence_gate.get("field_confidences") or {}
            if gate_field_confidences:
                try:
                    self.db.update_ap_item(
                        ap_item_id,
                        field_confidences=json.dumps(gate_field_confidences),
                        _actor_type="system",
                        _actor_id="confidence_gate",
                    )
                except Exception as _fc_err:
                    logger.warning("field_confidences persist failed: %s", _fc_err)

        # Hard block: budget exceeded cannot be overridden with justification alone
        if budget_summary.get("hard_block"):
            return {
                "status": "needs_budget_decision",
                "invoice_id": gmail_id,
                "reason": "budget_exceeded_hard_block",
                "budget": budget_summary,
                "options": [
                    "request_budget_adjustment",
                    "reject_over_budget",
                ],
            }
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

        if confidence_gate.get("requires_field_review") and not allow_confidence_override:
            return {
                "status": "needs_field_review",
                "invoice_id": gmail_id,
                "reason": "critical_field_confidence_below_threshold",
                "requires_field_review": True,
                "confidence_blockers": confidence_blockers,
                "threshold": confidence_gate.get("threshold_pct")
                or round(float(confidence_gate.get("threshold") or 0) * 100),
                "options": [
                    "review_fields",
                    "approve_override_with_justification",
                    "reject",
                ],
            }
        if allow_confidence_override and confidence_gate.get("requires_field_review"):
            if not str(override_justification or "").strip():
                return {
                    "status": "error",
                    "invoice_id": gmail_id,
                    "reason": "confidence_override_requires_justification",
                }

        # PO exception blocking: check for unresolved high-severity PO exceptions
        po_block = self._check_po_exception_block(invoice_data)
        if po_block.get("blocked") and not allow_po_exception_override:
            return {
                "status": "needs_po_resolution",
                "invoice_id": gmail_id,
                "reason": "po_exceptions_require_resolution",
                "po_exceptions": po_block.get("exceptions", []),
                "options": [
                    "override_with_reason",
                    "resolve_exceptions",
                    "reject",
                ],
            }
        if allow_po_exception_override and po_block.get("blocked"):
            if not str(po_override_reason or "").strip():
                return {
                    "status": "error",
                    "invoice_id": gmail_id,
                    "reason": "po_override_requires_reason",
                }

        if decision_idempotency_key and not self._acquire_decision_action_lock(
            ap_item_id=ap_item_id,
            decision_idempotency_key=decision_idempotency_key,
            actor_id=approved_by,
            source_channel=resolved_source_channel,
            correlation_id=correlation_id,
            metadata={
                "gmail_id": gmail_id,
                "run_id": action_run_id,
                "request_ts": decision_request_ts,
                "source_message_ref": resolved_message_ref,
            },
        ):
            existing_decision_snapshot = self._approval_snapshot_by_decision_key(ap_item_id, decision_idempotency_key)
            if existing_decision_snapshot:
                existing_status = str(existing_decision_snapshot.get("status") or "").strip().lower()
                existing_payload = self._approval_payload_dict(existing_decision_snapshot)
                if existing_status == "approved":
                    return {
                        "status": "approved",
                        "invoice_id": gmail_id,
                        "approved_by": approved_by,
                        "duplicate_action": True,
                        "decision_idempotency_key": decision_idempotency_key,
                        "erp_result": existing_payload.get("erp_result") or {},
                        "reason": "duplicate_approval_action",
                    }
                if existing_status == "failed":
                    return {
                        "status": "error",
                        "invoice_id": gmail_id,
                        "duplicate_action": True,
                        "decision_idempotency_key": decision_idempotency_key,
                        "reason": "duplicate_approval_action",
                        "erp_result": existing_payload.get("erp_result") or {},
                    }
            return {
                "status": "duplicate_in_progress",
                "invoice_id": gmail_id,
                "duplicate_action": True,
                "decision_idempotency_key": decision_idempotency_key,
                "reason": "duplicate_approval_action_in_progress",
            }

        approved_at = datetime.now(timezone.utc).isoformat()
        current_state = invoice_state
        if current_state == "received":
            self._transition_invoice_state(gmail_id, "validated", correlation_id=correlation_id)
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(gmail_id))
        if current_state == "validated":
            self._transition_invoice_state(gmail_id, "needs_approval", correlation_id=correlation_id)
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(gmail_id))
        if current_state in {"needs_approval", "approved"}:
            self._transition_invoice_state(
                gmail_id=gmail_id,
                target_state="approved",
                correlation_id=correlation_id,
                approved_by=approved_by,
                approved_at=approved_at,
            )
            current_state = self._canonical_invoice_state(self.db.get_invoice_status(gmail_id))
        if current_state not in {"approved", "ready_to_post"}:
            return {"status": "error", "reason": f"invalid_state_for_post:{current_state or 'unknown'}"}
        self._transition_invoice_state(gmail_id, "ready_to_post", correlation_id=correlation_id)
        
        # Build invoice object for ERP
        invoice = InvoiceData(
            gmail_id=gmail_id,
            subject=invoice_data.get("email_subject", ""),
            sender="",
            vendor_name=invoice_data.get("vendor") or invoice_data.get("vendor_name") or "Unknown",
            amount=invoice_data.get("amount", 0),
            currency=invoice_data.get("currency", "USD"),
            invoice_number=invoice_data.get("invoice_number"),
            due_date=invoice_data.get("due_date"),
            organization_id=self.organization_id,
            invoice_text=invoice_data.get("email_body", ""),  # For discount detection
            budget_impact=budget_checks,
        )
        if isinstance(field_confidences, dict) and field_confidences:
            self._update_ap_item_metadata(ap_item_id, {"field_confidences": field_confidences})
        if allow_confidence_override and confidence_gate.get("requires_field_review"):
            self._update_ap_item_metadata(
                ap_item_id,
                {
                    "confidence_gate": confidence_gate,
                    "requires_field_review": False,
                    "confidence_override": {
                        "used": True,
                        "actor": approved_by,
                        "at": approved_at,
                        "source_channel": resolved_source_channel,
                        "justification": override_justification,
                        "blockers": confidence_blockers,
                    },
                },
            )
            if ap_item_id:
                try:
                    _override_meta: Dict[str, Any] = {
                        "source_channel": resolved_source_channel,
                        "channel_id": resolved_channel_id,
                        "message_ref": resolved_message_ref,
                        "justification": override_justification,
                        "confidence_gate": confidence_gate,
                    }
                    # Merge structured override context when supplied (Gap #15 fix)
                    if override_context is not None:
                        _override_meta.update(override_context.to_dict())
                    self.db.append_ap_audit_event(
                        {
                            "ap_item_id": ap_item_id,
                            "event_type": "confidence_override_used",
                            "actor_type": "user",
                            "actor_id": approved_by,
                            "reason": "critical_field_confidence_override",
                            "metadata": _override_meta,
                            "organization_id": self.organization_id,
                            "correlation_id": correlation_id,
                            "source": resolved_source_channel,
                        }
                    )
                except Exception as exc:
                    logger.error("Could not append confidence override audit event: %s", exc)

        self._maybe_record_ap_decision_override(
            ap_item_id, "approved", approved_by, correlation_id=correlation_id
        )
        self._record_approval_snapshot(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
            channel_id=resolved_channel_id,
            message_ts=resolved_message_ref,
            source_channel=resolved_source_channel,
            source_message_ref=gmail_id,
            status="processing",
            decision_idempotency_key=decision_idempotency_key,
            decision_payload={
                "decision": (
                    "approve_override"
                    if (allow_budget_override or allow_confidence_override or allow_po_exception_override)
                    else "approve"
                ),
                "run_id": action_run_id,
                "request_ts": decision_request_ts,
                "actor_display": actor_display,
                "source_channel": resolved_source_channel,
                "source_message_ref": resolved_message_ref,
            },
            approved_by=approved_by,
            approved_at=approved_at,
        )

        # Post to ERP
        if decision_idempotency_key:
            result = await self._post_to_erp(
                invoice,
                idempotency_key=decision_idempotency_key,
                correlation_id=correlation_id,
            )
        else:
            result = await self._post_to_erp(invoice, correlation_id=correlation_id)
        post_attempted_at = datetime.now(timezone.utc).isoformat()
        
        if result.get("status") == "success":
            erp_reference = (
                result.get("erp_reference")
                or result.get("bill_id")
                or result.get("reference_id")
                or result.get("doc_num")
            )
            self._transition_invoice_state(
                gmail_id=gmail_id,
                target_state="posted_to_erp",
                correlation_id=correlation_id,
                erp_reference=erp_reference,
                erp_posted_at=post_attempted_at,
                post_attempted_at=post_attempted_at,
                last_error=None,
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

            # BUDGET: Record spending against applicable budgets
            try:
                budget_service = get_budget_awareness(self.organization_id)
                for check in budget_checks:
                    budget_id = check.get("budget_id") or check.get("budget_name", "").lower().replace(" ", "_")
                    if budget_id:
                        budget_service.record_spending(budget_id, invoice.amount)
                        logger.info("Recorded budget spending: %s += %.2f", budget_id, invoice.amount)
            except Exception as e:
                logger.warning("Failed to record budget spending: %s", e)

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
                    "decision": (
                        "approve_override"
                        if (allow_budget_override or allow_confidence_override or allow_po_exception_override)
                        else "approve"
                    ),
                    "override_justification": override_justification,
                    "confidence_override": bool(allow_confidence_override and confidence_gate.get("requires_field_review")),
                    "confidence_gate": confidence_gate,
                    "po_override_reason": po_override_reason,
                    "po_exceptions_overridden": po_block.get("exceptions") if allow_po_exception_override else None,
                    "budget": budget_summary,
                    "budget_impact": budget_checks,
                    "erp_result": result,
                    "run_id": action_run_id,
                    "request_ts": decision_request_ts,
                    "actor_display": actor_display,
                    "decision_idempotency_key": decision_idempotency_key,
                },
                approved_by=approved_by,
                approved_at=approved_at,
                decision_idempotency_key=decision_idempotency_key,
            )
            self._record_vendor_decision_feedback(
                ap_item_id=ap_item_id,
                vendor_name=invoice.vendor_name,
                human_action="approve",
                actor_id=approved_by,
                source_channel=resolved_source_channel,
                correlation_id=correlation_id,
                reason=override_justification,
                action_outcome="posted_to_erp",
                final_state="posted_to_erp",
                was_approved=True,
                amount=invoice.amount,
                invoice_date=invoice.due_date,
            )

            # M1: Transition posted_to_erp → closed (terminal state).
            try:
                self._transition_invoice_state(
                    gmail_id=gmail_id,
                    target_state="closed",
                    correlation_id=correlation_id,
                )
            except Exception as close_exc:
                logger.warning("Failed to transition to closed: %s", close_exc)
        else:
            failure_reason = (
                str(result.get("error_message") or "")
                or str(result.get("reason") or "")
                or str(result.get("status") or "")
                or "erp_post_failed"
            )
            self._transition_invoice_state(
                gmail_id=gmail_id,
                target_state="failed_post",
                correlation_id=correlation_id,
                post_attempted_at=post_attempted_at,
                last_error=failure_reason,
                exception_code="erp_post_failed",
                exception_severity="error",
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
                    "decision": (
                        "approve_override"
                        if (allow_budget_override or allow_confidence_override or allow_po_exception_override)
                        else "approve"
                    ),
                    "override_justification": override_justification,
                    "confidence_override": bool(allow_confidence_override and confidence_gate.get("requires_field_review")),
                    "confidence_gate": confidence_gate,
                    "po_override_reason": po_override_reason,
                    "budget": budget_summary,
                    "budget_impact": budget_checks,
                    "erp_result": result,
                    "run_id": action_run_id,
                    "request_ts": decision_request_ts,
                    "actor_display": actor_display,
                    "decision_idempotency_key": decision_idempotency_key,
                },
                decision_idempotency_key=decision_idempotency_key,
            )
            self._record_vendor_decision_feedback(
                ap_item_id=ap_item_id,
                vendor_name=invoice.vendor_name,
                human_action="approve",
                actor_id=approved_by,
                source_channel=resolved_source_channel,
                correlation_id=correlation_id,
                reason=failure_reason,
                action_outcome="failed_post",
            )
            # Gap #5: Enqueue durable retry so the background loop can recover
            # items stuck in failed_post after a crash or transient ERP error.
            if ap_item_id:
                self._enqueue_erp_post_retry(
                    ap_item_id=ap_item_id,
                    gmail_id=gmail_id,
                    correlation_id=correlation_id,
                )

        return {
            "status": "approved" if result.get("status") == "success" else "error",
            "invoice_id": gmail_id,
            "approved_by": approved_by,
            "decision_idempotency_key": decision_idempotency_key,
            "budget_override": bool(allow_budget_override),
            "confidence_override": bool(allow_confidence_override and confidence_gate.get("requires_field_review")),
            "requires_field_review": bool(confidence_gate.get("requires_field_review")),
            "confidence_blockers": confidence_blockers,
            "override_justification": override_justification,
            "budget": budget_summary,
            "confidence_gate": confidence_gate,
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
        actor_display: Optional[str] = None,
        action_run_id: Optional[str] = None,
        decision_request_ts: Optional[str] = None,
        decision_idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reject an invoice with reason."""
        invoice_data = self.db.get_invoice_status(gmail_id)
        if not invoice_data:
            return {"status": "error", "reason": "Invoice not found"}
        rejected_at = datetime.now(timezone.utc).isoformat()
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=gmail_id,
            vendor_name=invoice_data.get("vendor") or invoice_data.get("vendor_name"),
            invoice_number=invoice_data.get("invoice_number"),
        )
        correlation_id = self._ensure_ap_item_correlation_id(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
            preferred=correlation_id,
        )
        resolved_source_channel = str(source_channel or "slack").strip().lower() or "slack"
        resolved_channel_id = source_channel_id or slack_channel
        resolved_message_ref = source_message_ref or slack_ts
        existing_decision_snapshot = self._approval_snapshot_by_decision_key(
            ap_item_id,
            decision_idempotency_key,
        )
        if existing_decision_snapshot:
            existing_status = str(existing_decision_snapshot.get("status") or "").strip().lower()
            if existing_status == "rejected":
                return {
                    "status": "rejected",
                    "invoice_id": gmail_id,
                    "rejected_by": rejected_by,
                    "reason": reason,
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                }
            if existing_status in {"processing", "pending_adjustment", "approved"}:
                return {
                    "status": "duplicate_in_progress",
                    "invoice_id": gmail_id,
                    "reason": "duplicate_reject_action_in_progress",
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                }
        if decision_idempotency_key and not self._acquire_decision_action_lock(
            ap_item_id=ap_item_id,
            decision_idempotency_key=decision_idempotency_key,
            actor_id=rejected_by,
            source_channel=resolved_source_channel,
            correlation_id=correlation_id,
            metadata={
                "gmail_id": gmail_id,
                "run_id": action_run_id,
                "request_ts": decision_request_ts,
                "source_message_ref": resolved_message_ref,
                "action": "reject",
            },
        ):
            return {
                "status": "duplicate_in_progress",
                "invoice_id": gmail_id,
                "reason": "duplicate_reject_action_in_progress",
                "duplicate_action": True,
                "decision_idempotency_key": decision_idempotency_key,
            }
        
        # Update status
        self.db.update_invoice_status(
            gmail_id=gmail_id,
            status="rejected",
            rejection_reason=reason,
            rejected_by=rejected_by,
            rejected_at=rejected_at,
            _correlation_id=correlation_id,
            _source=resolved_source_channel,
            _workflow_id="approval_decision",
            _run_id=action_run_id,
            _decision_reason="reject",
        )
        
        # Update Slack thread status
        thread = self.db.get_slack_thread(gmail_id)
        if thread:
            self.db.update_slack_thread_status(
                gmail_id=gmail_id,
                channel_id=thread.get("channel_id"),
                thread_ts=thread.get("thread_ts"),
                thread_id=thread.get("thread_id") or thread.get("thread_ts"),
                status="rejected",
                rejection_reason=reason,
            )
        
        # Update Slack message
        if resolved_source_channel == "slack" and resolved_channel_id and resolved_message_ref:
            await self._update_slack_rejected(
                resolved_channel_id, resolved_message_ref, invoice_data, rejected_by, reason
            )
        self._maybe_record_ap_decision_override(
            ap_item_id, "rejected", rejected_by, correlation_id=correlation_id
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
                "run_id": action_run_id,
                "request_ts": decision_request_ts,
                "actor_display": actor_display,
                "decision_idempotency_key": decision_idempotency_key,
            },
            rejected_by=rejected_by,
            rejected_at=rejected_at,
            rejection_reason=reason,
            decision_idempotency_key=decision_idempotency_key,
        )
        self._record_vendor_decision_feedback(
            ap_item_id=ap_item_id,
            vendor_name=invoice_data.get("vendor") or invoice_data.get("vendor_name"),
            human_action="reject",
            actor_id=rejected_by,
            source_channel=resolved_source_channel,
            correlation_id=correlation_id,
            reason=reason,
            action_outcome="rejected",
            final_state="rejected",
            was_approved=False,
            amount=invoice_data.get("amount"),
            invoice_date=invoice_data.get("due_date"),
        )
        
        logger.info(f"Invoice rejected: {gmail_id} by {rejected_by} - {reason}")
        
        return {
            "status": "rejected",
            "invoice_id": gmail_id,
            "rejected_by": rejected_by,
            "reason": reason,
            "decision_idempotency_key": decision_idempotency_key,
        }

    def _enqueue_erp_post_retry(
        self,
        *,
        ap_item_id: str,
        gmail_id: str,
        correlation_id: Optional[str] = None,
        max_retries: int = 3,
    ) -> None:
        """Create a durable retry job for ERP post recovery.

        Called after an item lands in ``failed_post`` so the background loop
        can attempt ``resume_workflow`` on the next tick.  Idempotent: a second
        call for the same ap_item_id is a no-op (same idempotency_key).
        """
        if not hasattr(self.db, "create_agent_retry_job"):
            return
        idem_key = f"erp_post_retry:{ap_item_id}"
        try:
            now = datetime.now(timezone.utc).isoformat()
            self.db.create_agent_retry_job(
                {
                    "organization_id": self.organization_id,
                    "ap_item_id": ap_item_id,
                    "gmail_id": gmail_id,
                    "job_type": "erp_post_retry",
                    "status": "pending",
                    "retry_count": 0,
                    "max_retries": max_retries,
                    "next_retry_at": now,
                    "idempotency_key": idem_key,
                    "correlation_id": correlation_id,
                }
            )
            logger.info(
                "Enqueued erp_post_retry job for ap_item_id=%s (corr=%s)",
                ap_item_id,
                correlation_id,
            )
        except Exception as exc:
            logger.warning("Failed to enqueue erp_post_retry for %s: %s", ap_item_id, exc)

    async def resume_workflow(self, ap_item_id: str) -> Dict[str, Any]:
        """Re-enter the ERP post step for an AP item stuck in a recoverable state.

        Safe to call multiple times — each step is idempotent:
        - ``ready_to_post``: re-runs ERP post directly.
        - ``failed_post``: transitions back to ``ready_to_post``, then re-runs.
        - Any other state: returns ``{"status": "not_resumable", ...}``.

        Uses a stable idempotency key ``resume:<ap_item_id>:erp_post`` so
        a duplicate network call never double-posts to the ERP.
        """
        if not hasattr(self.db, "get_ap_item"):
            return {"status": "error", "reason": "db_not_supported"}

        row = self.db.get_ap_item(ap_item_id)
        if not row:
            return {"status": "error", "reason": "ap_item_not_found", "ap_item_id": ap_item_id}

        current_state = self._canonical_invoice_state(row)
        gmail_id = str(row.get("thread_id") or "")
        correlation_id = self._get_ap_item_correlation_id(ap_item_id=ap_item_id)

        if current_state not in {"failed_post", "ready_to_post"}:
            return {
                "status": "not_resumable",
                "ap_item_id": ap_item_id,
                "current_state": current_state,
                "reason": "state_does_not_support_resume",
            }

        if not gmail_id:
            return {
                "status": "error",
                "ap_item_id": ap_item_id,
                "reason": "missing_gmail_id_on_ap_item",
            }

        # If in failed_post, step back to ready_to_post first (idempotent if already there)
        if current_state == "failed_post":
            self._transition_invoice_state(
                gmail_id,
                "ready_to_post",
                correlation_id=correlation_id,
                source="resume_workflow",
            )

        # Build InvoiceData from the persisted row
        invoice = InvoiceData(
            gmail_id=gmail_id,
            subject=str(row.get("subject") or ""),
            sender=str(row.get("sender") or ""),
            vendor_name=str(row.get("vendor_name") or "Unknown"),
            amount=float(row.get("amount") or 0),
            currency=str(row.get("currency") or "USD"),
            invoice_number=row.get("invoice_number"),
            due_date=row.get("due_date"),
            organization_id=self.organization_id,
            correlation_id=correlation_id,
        )

        # Stable idempotency key ensures the ERP never double-posts on resume
        idempotency_key = f"resume:{ap_item_id}:erp_post"
        result = await self._post_to_erp(
            invoice,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        post_attempted_at = datetime.now(timezone.utc).isoformat()

        if result.get("status") == "success":
            erp_reference = (
                result.get("erp_reference")
                or result.get("bill_id")
                or result.get("reference_id")
                or result.get("doc_num")
            )
            self._transition_invoice_state(
                gmail_id,
                "posted_to_erp",
                correlation_id=correlation_id,
                source="resume_workflow",
                erp_reference=erp_reference,
                erp_posted_at=post_attempted_at,
                post_attempted_at=post_attempted_at,
                last_error=None,
            )
            if ap_item_id:
                try:
                    self.db.append_ap_audit_event(
                        {
                            "ap_item_id": ap_item_id,
                            "event_type": "erp_post_resumed",
                            "actor_type": "system",
                            "actor_id": "resume_workflow",
                            "reason": "workflow_crash_recovery",
                            "metadata": {
                                "erp_reference": erp_reference,
                                "idempotency_key": idempotency_key,
                                "recovered_from_state": current_state,
                            },
                            "organization_id": self.organization_id,
                            "correlation_id": correlation_id,
                            "source": "resume_workflow",
                        }
                    )
                except Exception as exc:
                    logger.error("Could not append erp_post_resumed audit event: %s", exc)
            logger.info(
                "resume_workflow: ap_item_id=%s recovered to posted_to_erp (ref=%s)",
                ap_item_id,
                erp_reference,
            )
            # M1: Transition posted_to_erp → closed after successful recovery.
            try:
                self._transition_invoice_state(
                    gmail_id, "closed", correlation_id=correlation_id,
                )
            except Exception as close_exc:
                logger.warning("Failed to transition recovered item to closed: %s", close_exc)
            return {
                "status": "recovered",
                "ap_item_id": ap_item_id,
                "erp_reference": erp_reference,
                "erp_result": result,
            }

        # Post still failed — leave in failed_post with updated error
        failure_reason = (
            str(result.get("error_message") or "")
            or str(result.get("reason") or "")
            or str(result.get("status") or "")
            or "erp_post_failed"
        )
        self._transition_invoice_state(
            gmail_id,
            "failed_post",
            correlation_id=correlation_id,
            source="resume_workflow",
            post_attempted_at=post_attempted_at,
            last_error=failure_reason,
        )
        logger.warning(
            "resume_workflow: ap_item_id=%s ERP post still failing: %s",
            ap_item_id,
            failure_reason,
        )
        return {
            "status": "still_failing",
            "ap_item_id": ap_item_id,
            "reason": failure_reason,
            "erp_result": result,
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
        actor_display: Optional[str] = None,
        action_run_id: Optional[str] = None,
        decision_request_ts: Optional[str] = None,
        decision_idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark invoice for budget adjustment before final approval."""
        invoice_data = self.db.get_invoice_status(gmail_id)
        if not invoice_data:
            return {"status": "error", "reason": "Invoice not found"}

        reason_text = str(reason or "budget_adjustment_requested").strip() or "budget_adjustment_requested"
        requested_at = datetime.now(timezone.utc).isoformat()
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=gmail_id,
            vendor_name=invoice_data.get("vendor") or invoice_data.get("vendor_name"),
            invoice_number=invoice_data.get("invoice_number"),
        )
        correlation_id = self._ensure_ap_item_correlation_id(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
            preferred=correlation_id,
        )
        resolved_source_channel = str(source_channel or "slack").strip().lower() or "slack"
        resolved_channel_id = source_channel_id or slack_channel
        resolved_message_ref = source_message_ref or slack_ts
        existing_decision_snapshot = self._approval_snapshot_by_decision_key(
            ap_item_id,
            decision_idempotency_key,
        )
        if existing_decision_snapshot:
            existing_status = str(existing_decision_snapshot.get("status") or "").strip().lower()
            if existing_status == "pending_adjustment":
                return {
                    "status": "needs_info",
                    "invoice_id": gmail_id,
                    "requested_by": requested_by,
                    "reason": reason_text,
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                }
            if existing_status in {"processing", "approved", "rejected"}:
                return {
                    "status": "duplicate_in_progress",
                    "invoice_id": gmail_id,
                    "reason": "duplicate_request_info_action_in_progress",
                    "duplicate_action": True,
                    "decision_idempotency_key": decision_idempotency_key,
                }
        if decision_idempotency_key and not self._acquire_decision_action_lock(
            ap_item_id=ap_item_id,
            decision_idempotency_key=decision_idempotency_key,
            actor_id=requested_by,
            source_channel=resolved_source_channel,
            correlation_id=correlation_id,
            metadata={
                "gmail_id": gmail_id,
                "run_id": action_run_id,
                "request_ts": decision_request_ts,
                "source_message_ref": resolved_message_ref,
                "action": "request_info",
            },
        ):
            return {
                "status": "duplicate_in_progress",
                "invoice_id": gmail_id,
                "reason": "duplicate_request_info_action_in_progress",
                "duplicate_action": True,
                "decision_idempotency_key": decision_idempotency_key,
            }

        self.db.update_invoice_status(
            gmail_id=gmail_id,
            status="needs_info",
            rejection_reason=reason_text,
            rejected_by=requested_by,
            rejected_at=requested_at,
            _correlation_id=correlation_id,
            _source=resolved_source_channel,
            _workflow_id="approval_decision",
            _run_id=action_run_id,
            _decision_reason="request_info",
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
                "run_id": action_run_id,
                "request_ts": decision_request_ts,
                "actor_display": actor_display,
                "decision_idempotency_key": decision_idempotency_key,
            },
            rejected_by=requested_by,
            rejected_at=requested_at,
            rejection_reason=reason_text,
            decision_idempotency_key=decision_idempotency_key,
        )
        self._record_vendor_decision_feedback(
            ap_item_id=ap_item_id,
            vendor_name=invoice_data.get("vendor") or invoice_data.get("vendor_name"),
            human_action="request_info",
            actor_id=requested_by,
            source_channel=resolved_source_channel,
            correlation_id=correlation_id,
            reason=reason_text,
            action_outcome="needs_info",
        )

        ap_row = self.db.get_ap_item(ap_item_id) if ap_item_id and hasattr(self.db, "get_ap_item") else None
        ap_meta = self._parse_metadata_dict((ap_row or {}).get("metadata"))
        followup_question = str(ap_meta.get("needs_info_question") or reason_text).strip() or reason_text
        if followup_question:
            self._update_ap_item_metadata(ap_item_id, {"needs_info_question": followup_question})

        draft_id = await self._create_needs_info_vendor_draft(
            ap_item_id=ap_item_id,
            thread_id=gmail_id,
            to_email=str(invoice_data.get("sender") or ""),
            invoice_data={
                "subject": invoice_data.get("email_subject") or invoice_data.get("subject") or "",
                "vendor_name": invoice_data.get("vendor") or invoice_data.get("vendor_name") or "",
                "amount": invoice_data.get("amount") or 0.0,
                "invoice_number": invoice_data.get("invoice_number") or "",
            },
            question=followup_question,
            user_id=invoice_data.get("user_id"),
        )
        self._apply_needs_info_followup_metadata(
            ap_item_id=ap_item_id,
            draft_id=draft_id,
            question=followup_question,
            actor_type="user",
            actor_id=requested_by,
            source=resolved_source_channel,
            correlation_id=correlation_id,
        )

        return {
            "status": "needs_info",
            "invoice_id": gmail_id,
            "requested_by": requested_by,
            "reason": reason_text,
            "decision_idempotency_key": decision_idempotency_key,
        }
    
    async def _post_to_erp(
        self,
        invoice: InvoiceData,
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Post approved invoice to ERP as a Bill.

        Enforces state guard (PLAN.md §4.6-1): posting only from ``ready_to_post``.
        Enforces mandatory idempotency key (PLAN.md §7.3-1): generates one if
        the caller did not provide one.
        """
        # B2: State guard — only post from ready_to_post (PLAN.md §4.6)
        ap_item_id = self._lookup_ap_item_id(
            gmail_id=invoice.gmail_id,
            vendor_name=invoice.vendor_name,
            invoice_number=invoice.invoice_number,
        )
        if ap_item_id:
            db = _get_db()
            existing = db.get_ap_item(ap_item_id)
            current_state = str(existing.get("state") or "").strip().lower() if existing else ""
            if current_state not in ("ready_to_post",):
                logger.error(
                    "State guard: refusing ERP post for AP item %s in state '%s' (expected ready_to_post)",
                    ap_item_id, current_state,
                )
                return {
                    "status": "error",
                    "reason": "illegal_state_for_posting",
                    "current_state": current_state,
                    "expected_state": "ready_to_post",
                }

        # B3: Mandatory idempotency key — generate if not provided (PLAN.md §7.3)
        if not idempotency_key:
            import uuid as _uuid
            idempotency_key = f"auto:{invoice.gmail_id or invoice.invoice_number or ''}:{_uuid.uuid4().hex[:8]}"
            logger.warning("Generated auto idempotency_key=%s (caller did not provide one)", idempotency_key)

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

        # H3: Audit ERP post attempt before execution (PLAN.md §4.7)
        if ap_item_id:
            try:
                self.db.append_ap_audit_event(
                    {
                        "ap_item_id": ap_item_id,
                        "event_type": "erp_post_attempted",
                        "actor_type": "system",
                        "actor_id": "invoice_workflow",
                        "metadata": {
                            "idempotency_key": idempotency_key,
                            "vendor": invoice.vendor_name,
                            "amount": invoice.amount,
                            "invoice_number": invoice.invoice_number,
                        },
                        "organization_id": self.organization_id,
                        "correlation_id": correlation_id or invoice.correlation_id,
                        "source": "invoice_workflow",
                    }
                )
            except Exception:
                pass  # Non-fatal

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
            idempotency_key=idempotency_key,
            correlation_id=correlation_id or invoice.correlation_id,
        )

        # H3: Audit ERP post result (PLAN.md §4.7)
        if ap_item_id:
            post_event_type = "erp_post_succeeded" if result.get("status") == "success" else "erp_post_failed"
            try:
                self.db.append_ap_audit_event(
                    {
                        "ap_item_id": ap_item_id,
                        "event_type": post_event_type,
                        "actor_type": "system",
                        "actor_id": "invoice_workflow",
                        "metadata": {
                            "idempotency_key": idempotency_key,
                            "erp_reference": result.get("erp_reference") or result.get("bill_id"),
                            "erp_type": result.get("erp") or result.get("erp_type"),
                            "status": result.get("status"),
                            "reason": result.get("reason"),
                        },
                        "organization_id": self.organization_id,
                        "correlation_id": correlation_id or invoice.correlation_id,
                        "source": "invoice_workflow",
                    }
                )
            except Exception:
                pass  # Non-fatal

        if result.get("status") == "success":
            result["vendor_id"] = vendor_id
            logger.info(f"Posted bill to ERP: {result.get('bill_id')}")
        
        return result
    
    async def _send_posted_notification(
        self,
        invoice: InvoiceData,
        erp_result: Dict[str, Any],
        reason: str = "high_confidence",
    ) -> None:
        """Send notification that invoice was auto-posted with reasoning."""
        _ = reason
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
        """Update Slack message to remove buttons and post threaded confirmation."""
        doc_number = erp_result.get("doc_num") or erp_result.get("document_number") or erp_result.get("erp_document")
        bill_id = erp_result.get("bill_id")
        erp_type = erp_result.get("erp_type", "ERP")
        gl_code = erp_result.get("gl_code") or (invoice.vendor_intelligence or {}).get("suggested_gl", "")

        # 1. Update original card — remove buttons, add "Approved" badge
        approved_blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                f"*{invoice.vendor_name}* — {invoice.currency} {invoice.amount:,.2f}\n"
                f"Invoice #: {invoice.invoice_number or 'N/A'} | "
                f"Approved by {approved_by}"
            )}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "Posted to ERP"}]},
        ]
        try:
            await self.slack_client.update_message(channel, ts, "Invoice approved", approved_blocks)
        except Exception as e:
            logger.warning(f"Failed to update Slack card: {e}")

        # 2. Post threaded confirmation with details
        ref_parts = []
        if bill_id:
            ref_parts.append(f"Bill ID: `{bill_id}`")
        if doc_number:
            ref_parts.append(f"Doc #: `{doc_number}`")
        if gl_code:
            ref_parts.append(f"GL: `{gl_code}`")

        confirm_text = (
            f"Posted to {erp_type}\n"
            + (" | ".join(ref_parts) + "\n" if ref_parts else "")
            + f"Approved by {approved_by}"
        )
        try:
            await self.slack_client.send_message(
                channel=channel,
                text=confirm_text,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": confirm_text}}],
                thread_ts=ts,
            )
        except Exception as e:
            logger.warning(f"Failed to post threaded confirmation: {e}")
    
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
