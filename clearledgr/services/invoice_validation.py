"""
Invoice Validation Mixin

Extracted from InvoiceWorkflowService to separate validation/gate logic
from the core workflow orchestration.

All methods use self.db, self.organization_id, self._settings, self._load_settings(),
self._observer_registry, etc. — these are set in InvoiceWorkflowService.__init__
and resolve via self at runtime (standard mixin pattern).
"""

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta

from clearledgr.core.ap_confidence import (
    DEFAULT_CRITICAL_FIELD_CONFIDENCE_THRESHOLD,
    evaluate_critical_field_confidence,
)
from clearledgr.core.ap_states import (
    APState,
    classify_post_failure_recoverability,
)
from clearledgr.services.approval_card_builder import (
    budget_status_rank,
    normalize_budget_checks,
    compute_budget_summary,
)
from clearledgr.services.invoice_models import InvoiceData

logger = logging.getLogger(__name__)


class InvoiceValidationMixin:
    """Mixin providing validation, gate, and helper methods for InvoiceWorkflowService."""

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _budget_status_rank(status: str) -> int:
        return budget_status_rank(status)

    def _normalize_budget_checks(self, raw: Any) -> List[Dict[str, Any]]:
        return normalize_budget_checks(raw)

    def _compute_budget_summary(self, budget_checks: List[Dict[str, Any]]) -> Dict[str, Any]:
        return compute_budget_summary(budget_checks)

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
        learned_threshold_overrides = None
        learned_profile_id = None
        learned_signal_count = 0
        if invoice.organization_id and invoice.vendor_name:
            try:
                from clearledgr.services.correction_learning import get_correction_learning_service

                learned_adjustments = get_correction_learning_service(str(invoice.organization_id)).get_extraction_confidence_adjustments(
                    vendor_name=invoice.vendor_name,
                    sender_domain=invoice.sender,
                    document_type="invoice",
                )
                learned_threshold_overrides = learned_adjustments.get("threshold_overrides") or None
                learned_profile_id = learned_adjustments.get("profile_id")
                learned_signal_count = int(learned_adjustments.get("signal_count") or 0)
            except Exception:
                learned_threshold_overrides = None
                learned_profile_id = None
                learned_signal_count = 0

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
            vendor_name=invoice.vendor_name,
            sender=invoice.sender,
            document_type="invoice",
            primary_source="attachment" if invoice.attachment_url else "email",
            has_attachment=bool(invoice.attachment_url),
            learned_threshold_overrides=learned_threshold_overrides,
            learned_profile_id=learned_profile_id,
            learned_signal_count=learned_signal_count,
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
        learned_threshold_overrides = None
        learned_profile_id = None
        learned_signal_count = 0
        organization_id = invoice_row.get("organization_id") or metadata.get("organization_id")
        vendor_name = invoice_row.get("vendor") or invoice_row.get("vendor_name")
        if organization_id and vendor_name:
            try:
                from clearledgr.services.correction_learning import get_correction_learning_service

                learned_adjustments = get_correction_learning_service(str(organization_id)).get_extraction_confidence_adjustments(
                    vendor_name=vendor_name,
                    sender_domain=metadata.get("source_sender_domain") or invoice_row.get("sender"),
                    document_type=invoice_row.get("document_type") or metadata.get("document_type") or metadata.get("email_type"),
                )
                learned_threshold_overrides = learned_adjustments.get("threshold_overrides") or None
                learned_profile_id = learned_adjustments.get("profile_id")
                learned_signal_count = int(learned_adjustments.get("signal_count") or 0)
            except Exception:
                learned_threshold_overrides = None
                learned_profile_id = None
                learned_signal_count = 0

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
            vendor_name=invoice_row.get("vendor") or invoice_row.get("vendor_name"),
            sender=invoice_row.get("sender"),
            document_type=invoice_row.get("document_type") or metadata.get("document_type") or metadata.get("email_type"),
            primary_source=metadata.get("primary_source"),
            has_attachment=bool(
                invoice_row.get("attachment_url")
                or invoice_row.get("has_attachment")
                or metadata.get("has_attachment")
            ),
            sender_domain=metadata.get("source_sender_domain"),
            learned_threshold_overrides=learned_threshold_overrides,
            learned_profile_id=learned_profile_id,
            learned_signal_count=learned_signal_count,
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
            from clearledgr.services.budget_awareness import get_budget_awareness
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

    @staticmethod
    def _parse_json_list(raw: Any) -> List[Any]:
        if isinstance(raw, list):
            return list(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return []
        return []

    def _blocking_source_conflicts(self, raw_conflicts: Any) -> List[Dict[str, Any]]:
        blockers: List[Dict[str, Any]] = []
        for conflict in self._parse_json_list(raw_conflicts):
            if not isinstance(conflict, dict):
                continue
            field = str(conflict.get("field") or "").strip().lower()
            if not field or not self._coerce_bool(conflict.get("blocking")):
                continue
            blockers.append(conflict)
        return blockers

    def evaluate_financial_action_field_review_gate(
        self,
        invoice_row: Dict[str, Any],
        *,
        field_confidences_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return the hard-stop field review gate for approval/posting actions."""
        row = invoice_row if isinstance(invoice_row, dict) else {}
        metadata = self._parse_metadata_dict(row.get("metadata"))
        confidence_gate = self._evaluate_invoice_row_confidence_gate(
            row,
            field_confidences_override=field_confidences_override,
        )

        confidence_blockers = self._parse_json_list(row.get("confidence_blockers"))
        if not confidence_blockers:
            confidence_blockers = self._parse_json_list(metadata.get("confidence_blockers"))
        if not confidence_blockers:
            raw_gate_blockers = confidence_gate.get("confidence_blockers")
            confidence_blockers = raw_gate_blockers if isinstance(raw_gate_blockers, list) else []

        source_conflicts = self._parse_json_list(row.get("source_conflicts"))
        if not source_conflicts:
            source_conflicts = self._parse_json_list(metadata.get("source_conflicts"))
        blocking_source_conflicts = self._blocking_source_conflicts(source_conflicts)

        requires_field_review = bool(
            self._coerce_bool(row.get("requires_field_review"))
            or self._coerce_bool(metadata.get("requires_field_review"))
            or bool(confidence_gate.get("requires_field_review"))
            or bool(confidence_blockers)
            or bool(blocking_source_conflicts)
        )

        blocked_fields: List[str] = []
        for issue in list(confidence_blockers) + list(blocking_source_conflicts):
            if not isinstance(issue, dict):
                continue
            field = str(issue.get("field") or "").strip().lower()
            if field and field not in blocked_fields:
                blocked_fields.append(field)

        blocked = requires_field_review or bool(blocking_source_conflicts)
        return {
            "blocked": blocked,
            "reason": "field_review_required" if blocked else None,
            "detail": (
                "Financial action blocked until required field review is completed."
                if blocked
                else None
            ),
            "requires_field_review": requires_field_review,
            "confidence_gate": confidence_gate,
            "confidence_blockers": confidence_blockers,
            "source_conflicts": source_conflicts,
            "blocking_source_conflicts": blocking_source_conflicts,
            "blocked_fields": blocked_fields,
            "exception_code": (
                "field_conflict"
                if blocking_source_conflicts
                else ("field_review_required" if requires_field_review else None)
            ),
            "exception_severity": (
                "high"
                if blocking_source_conflicts
                else ("medium" if requires_field_review else None)
            ),
        }

    def evaluate_financial_action_precheck(
        self,
        ap_item: Dict[str, Any],
        *,
        allowed_states: List[str],
        state_reason_code: str,
    ) -> Dict[str, Any]:
        """Evaluate state plus hard-stop review blockers for mutating financial actions."""
        state = self._canonical_invoice_state(ap_item) or ""
        field_review_gate = self.evaluate_financial_action_field_review_gate(ap_item)
        reason_codes: List[str] = []

        if state not in {str(value or "").strip().lower() for value in allowed_states}:
            reason_codes.append(state_reason_code)
        if field_review_gate.get("blocked"):
            reason_codes.append("field_review_required")
        if field_review_gate.get("blocking_source_conflicts"):
            reason_codes.append("blocking_source_conflicts")

        return {
            "eligible": len(reason_codes) == 0,
            "state": state or None,
            "reason_codes": list(dict.fromkeys(reason_codes)),
            "requires_field_review": bool(field_review_gate.get("requires_field_review")),
            "confidence_blockers": field_review_gate.get("confidence_blockers") or [],
            "source_conflicts": field_review_gate.get("source_conflicts") or [],
            "blocking_source_conflicts": field_review_gate.get("blocking_source_conflicts") or [],
            "blocked_fields": field_review_gate.get("blocked_fields") or [],
            "exception_code": field_review_gate.get("exception_code"),
        }

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
            line_items=metadata.get("line_items") if isinstance(metadata.get("line_items"), list) else None,
        )

    def _persist_financial_action_field_review_gate(
        self,
        ap_item_id: Optional[str],
        gate: Dict[str, Any],
    ) -> None:
        """Persist the latest field-review blocker snapshot for blocked financial actions."""
        if not ap_item_id or not isinstance(gate, dict) or not gate.get("blocked"):
            return
        self._update_ap_item_metadata(
            ap_item_id,
            {
                "requires_field_review": True,
                "confidence_gate": gate.get("confidence_gate") or {},
                "confidence_blockers": gate.get("confidence_blockers") or [],
                "source_conflicts": gate.get("source_conflicts") or [],
                "exception_code": gate.get("exception_code"),
                "exception_severity": gate.get("exception_severity"),
            },
        )
        try:
            self.db.update_ap_item(
                ap_item_id,
                exception_code=gate.get("exception_code"),
                exception_severity=gate.get("exception_severity"),
            )
        except Exception as exc:
            logger.error("Could not persist field-review block metadata for %s: %s", ap_item_id, exc)

    def evaluate_batch_route_low_risk_for_approval(self, ap_item: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate deterministic prechecks for batch `route_low_risk_for_approval`."""
        state = self._canonical_invoice_state(ap_item) or ""
        metadata = self._parse_metadata_dict((ap_item or {}).get("metadata"))
        reason_codes: List[str] = []
        field_review_gate = self.evaluate_financial_action_field_review_gate(ap_item)

        if state != APState.VALIDATED.value:
            reason_codes.append("state_not_validated")

        requires_field_review = bool(field_review_gate.get("requires_field_review"))
        if requires_field_review:
            reason_codes.append("field_review_required")

        confidence_blockers = field_review_gate.get("confidence_blockers") or []
        if confidence_blockers:
            reason_codes.append("confidence_blockers_present")
        if field_review_gate.get("blocking_source_conflicts"):
            reason_codes.append("blocking_source_conflicts")

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
            "reason_codes": list(dict.fromkeys(reason_codes)),
            "requires_field_review": requires_field_review,
            "confidence_blockers": confidence_blockers,
            "source_conflicts": field_review_gate.get("source_conflicts") or [],
            "blocking_source_conflicts": field_review_gate.get("blocking_source_conflicts") or [],
            "blocked_fields": field_review_gate.get("blocked_fields") or [],
            "budget_requires_decision": budget_requires_decision,
            "exception_code": field_review_gate.get("exception_code") or exception_code or None,
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
        field_review_gate = self.evaluate_financial_action_field_review_gate(ap_item)
        if not recoverability.get("recoverable"):
            reason_codes.append(str(recoverability.get("reason") or "non_recoverable_failure"))
        if field_review_gate.get("blocked"):
            reason_codes.append("field_review_required")
        if field_review_gate.get("blocking_source_conflicts"):
            reason_codes.append("blocking_source_conflicts")

        return {
            "eligible": len(reason_codes) == 0,
            "state": state,
            "reason_codes": list(dict.fromkeys(reason_codes)),
            "recoverability": recoverability,
            "last_error": last_error or None,
            "exception_code": field_review_gate.get("exception_code") or exception_code or None,
            "requires_field_review": bool(field_review_gate.get("requires_field_review")),
            "confidence_blockers": field_review_gate.get("confidence_blockers") or [],
            "source_conflicts": field_review_gate.get("source_conflicts") or [],
            "blocking_source_conflicts": field_review_gate.get("blocking_source_conflicts") or [],
            "blocked_fields": field_review_gate.get("blocked_fields") or [],
        }

    @staticmethod
    def _enrich_transition_kwargs(
        kwargs: Dict[str, Any],
        *,
        correlation_id: Optional[str],
        source: Optional[str],
        workflow_id: Optional[str],
        run_id: Optional[str],
        decision_reason: Optional[str],
    ) -> None:
        """Attach tracking metadata to kwargs before a DB status update."""
        if correlation_id:
            kwargs["_correlation_id"] = correlation_id
        if source:
            kwargs["_source"] = source
        if workflow_id:
            kwargs["_workflow_id"] = workflow_id
        if run_id:
            kwargs["_run_id"] = run_id
        if decision_reason:
            kwargs["_decision_reason"] = decision_reason

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

        ap_item_id = str((row or {}).get("id") or "") if isinstance(row, dict) else None
        resolved_corr = correlation_id or self._get_ap_item_correlation_id(
            ap_item_id=ap_item_id,
            gmail_id=gmail_id,
        )
        self._enrich_transition_kwargs(
            kwargs,
            correlation_id=resolved_corr,
            source=source,
            workflow_id=workflow_id,
            run_id=run_id,
            decision_reason=decision_reason,
        )

        if current_state == normalized_target:
            if kwargs:
                return bool(self.db.update_invoice_status(gmail_id=gmail_id, **kwargs))
            return True

        success = bool(self.db.update_invoice_status(gmail_id=gmail_id, status=normalized_target, **kwargs))
        if not success:
            logger.error(
                "State transition failed: gmail_id=%s from=%s to=%s — update returned False",
                gmail_id,
                current_state,
                normalized_target,
            )
            raise RuntimeError(
                f"State transition failed for {gmail_id}: "
                f"{current_state!r} -> {normalized_target!r}"
            )
        if success and self._observer_registry:
            try:
                import asyncio
                from clearledgr.services.state_observers import StateTransitionEvent

                event = StateTransitionEvent(
                    ap_item_id=ap_item_id or "",
                    organization_id=self.organization_id,
                    old_state=current_state,
                    new_state=normalized_target,
                    actor_id=kwargs.get("approved_by") or kwargs.get("rejected_by"),
                    correlation_id=resolved_corr,
                    source=source or "invoice_workflow",
                    gmail_id=gmail_id,
                    metadata={k: v for k, v in kwargs.items() if not k.startswith("_")},
                )
                # Run observers synchronously — they are trivially fast DB writes.
                # Use existing loop if available, otherwise create one.
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._observer_registry.notify(event))
                except RuntimeError:
                    asyncio.run(self._observer_registry.notify(event))
            except Exception as obs_exc:
                logger.debug("Observer dispatch skipped: %s", obs_exc)
        return success

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

    async def _evaluate_deterministic_validation(self, invoice: InvoiceData) -> Dict[str, Any]:
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
                from clearledgr.services.policy_compliance import get_policy_compliance
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
                add_reason(
                    "policy_service_unavailable",
                    "Policy compliance check could not be completed",
                    severity="warning",
                    details={"error": str(exc)},
                )
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
                from clearledgr.services.purchase_orders import get_purchase_order_service
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

        # 3b) ERP pre-flight checks (vendor exists, duplicate bill, GL validity).
        #     Non-blocking on ERP unavailability — warnings only if ERP is down.
        erp_preflight = None
        try:
            from clearledgr.integrations.erp_router import erp_preflight_check as _erp_preflight

            gl_codes_to_check: List[str] = []
            suggested_gl = (invoice.vendor_intelligence or {}).get("suggested_gl")
            if suggested_gl:
                gl_codes_to_check.append(str(suggested_gl))

            erp_preflight = await _erp_preflight(
                organization_id=self.organization_id,
                vendor_name=invoice.vendor_name,
                invoice_number=invoice.invoice_number,
                gl_codes=gl_codes_to_check or None,
            )
            invoice.erp_preflight = erp_preflight

            if erp_preflight.get("erp_available"):
                # Bill already exists in ERP → error (blocks gate, forces human review)
                if erp_preflight.get("bill_exists") is True:
                    ref = erp_preflight.get("bill_erp_ref") or {}
                    add_reason(
                        "erp_duplicate_bill",
                        f"Invoice {invoice.invoice_number} already exists in "
                        f"{erp_preflight.get('erp_type', 'ERP')} (ref: {ref.get('bill_id', 'unknown')})",
                        severity="error",
                        details={"erp_type": erp_preflight.get("erp_type"), "bill_ref": ref},
                    )
                # Vendor not found in ERP → warning (flags but doesn't block)
                if erp_preflight.get("vendor_exists") is False:
                    add_reason(
                        "erp_vendor_not_found",
                        f"Vendor '{invoice.vendor_name}' not found in "
                        f"{erp_preflight.get('erp_type', 'ERP')}. Create vendor before posting.",
                        severity="warning",
                        details={"erp_type": erp_preflight.get("erp_type")},
                    )
                # GL codes not in org mapping → warning
                if erp_preflight.get("gl_valid") is False:
                    add_reason(
                        "erp_invalid_gl_codes",
                        f"GL codes {erp_preflight.get('invalid_gl_codes', [])} not in org GL mapping",
                        severity="warning",
                        details={"invalid_gl_codes": erp_preflight.get("invalid_gl_codes", [])},
                    )
        except Exception as preflight_exc:
            logger.warning("ERP pre-flight check failed (non-fatal): %s", preflight_exc)

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
        elif invoice.vendor_name and not invoice.invoice_number:
            # H3: No invoice number — fall back to vendor + amount + date range matching
            # to catch potential duplicates that would otherwise be missed entirely.
            try:
                if hasattr(self.db, "get_ap_items_by_vendor") and invoice.amount:
                    from datetime import timedelta
                    recent_items = self.db.get_ap_items_by_vendor(
                        self.organization_id,
                        invoice.vendor_name,
                        days=7,
                        limit=20,
                    )
                    for existing in (recent_items or []):
                        if str(existing.get("state") or "") in ("rejected",):
                            continue
                        existing_amount = existing.get("amount")
                        if existing_amount is None or invoice.amount is None:
                            continue
                        try:
                            existing_amount = float(existing_amount)
                        except (TypeError, ValueError):
                            continue
                        if existing_amount <= 0:
                            continue
                        # 2% tolerance
                        amount_diff = abs(invoice.amount - existing_amount) / max(existing_amount, 0.01)
                        if amount_diff <= 0.02:
                            add_reason(
                                "possible_duplicate_no_invoice_number",
                                f"Possible duplicate: same vendor ({invoice.vendor_name}), "
                                f"similar amount (${invoice.amount:,.2f} vs ${existing_amount:,.2f}) "
                                f"within 7 days, but no invoice number to confirm",
                                severity="warning",
                                details={
                                    "existing_ap_item_id": str(existing.get("id") or ""),
                                    "existing_state": str(existing.get("state") or ""),
                                    "existing_amount": existing_amount,
                                    "amount_diff_pct": round(amount_diff * 100, 2),
                                },
                            )
                            break  # One warning is enough
            except Exception as fuzzy_dedup_exc:
                logger.warning("Fuzzy duplicate check failed (non-fatal): %s", fuzzy_dedup_exc)

        # 4b) Discount amount consistency check.
        if invoice.discount_amount is not None and invoice.discount_amount > 0:
            # Informational: check if discount + amount ~= subtotal
            if invoice.subtotal is not None and invoice.subtotal > 0 and invoice.amount is not None:
                expected_subtotal = invoice.amount + invoice.discount_amount
                tolerance = max(invoice.subtotal * 0.02, 0.01)  # 2% tolerance
                if abs(expected_subtotal - invoice.subtotal) <= tolerance:
                    # Discount makes mathematical sense — informational note only
                    add_reason(
                        "discount_applied",
                        f"Discount of {invoice.discount_amount} applied; "
                        f"amount ({invoice.amount}) + discount ({invoice.discount_amount}) "
                        f"≈ subtotal ({invoice.subtotal})",
                        severity="info",
                        details={
                            "discount_amount": invoice.discount_amount,
                            "discount_terms": invoice.discount_terms,
                        },
                    )
                else:
                    add_reason(
                        "discount_amount_inconsistent",
                        f"Discount amount ({invoice.discount_amount}) doesn't reconcile: "
                        f"amount ({invoice.amount}) + discount ({invoice.discount_amount}) = "
                        f"{expected_subtotal}, but subtotal is {invoice.subtotal}",
                        severity="warning",
                        details={
                            "discount_amount": invoice.discount_amount,
                            "expected_subtotal": expected_subtotal,
                            "actual_subtotal": invoice.subtotal,
                        },
                    )

        # 4c) Bank/payment details mismatch check.
        if isinstance(invoice.bank_details, dict) and invoice.bank_details:
            try:
                vendor_intelligence = invoice.vendor_intelligence or {}
                vendor_bank_changed_at = vendor_intelligence.get("bank_details_changed_at")
                stored_bank = vendor_intelligence.get("bank_details")
                if vendor_bank_changed_at and isinstance(stored_bank, dict) and stored_bank:
                    # Compare extracted bank details against stored vendor bank details
                    mismatch_fields = []
                    for bk in ("account_number", "routing_number", "iban", "swift", "sort_code"):
                        extracted_val = (invoice.bank_details.get(bk) or "").strip()
                        stored_val = (stored_bank.get(bk) or "").strip()
                        if extracted_val and stored_val and extracted_val != stored_val:
                            mismatch_fields.append(bk)
                    if mismatch_fields:
                        sev = "error" if (invoice.amount or 0) >= 5000 else "warning"
                        add_reason(
                            "bank_details_mismatch_from_invoice",
                            f"Bank details on invoice differ from vendor profile on: {', '.join(mismatch_fields)}",
                            severity=sev,
                            details={
                                "mismatched_fields": mismatch_fields,
                                "invoice_bank_details": invoice.bank_details,
                                "stored_bank_details": stored_bank,
                            },
                        )
            except Exception as bank_exc:
                logger.warning("Bank details comparison failed (non-fatal): %s", bank_exc)

        # 5a-pre) Payment terms mismatch detection.
        try:
            invoice_terms = getattr(invoice, "payment_terms", None) or ""
            if invoice_terms and invoice.vendor_name:
                vp = None
                try:
                    vp = self.db.get_vendor_profile(self.organization_id, invoice.vendor_name) or {}
                except Exception:
                    vp = {}
                profile_terms = vp.get("payment_terms") or ""
                if profile_terms and invoice_terms.strip().lower() != profile_terms.strip().lower():
                    add_reason(
                        "payment_terms_mismatch",
                        f"Invoice terms '{invoice_terms}' differ from vendor profile terms '{profile_terms}'",
                        severity="warning",
                        details={"invoice_terms": invoice_terms, "profile_terms": profile_terms},
                    )
        except Exception:
            pass

        # 5a-pre2) GL code validation against cached chart of accounts.
        try:
            if invoice.line_items:
                from clearledgr.integrations.erp_router import get_chart_of_accounts
                import asyncio as _aio
                try:
                    loop = _aio.get_running_loop()
                    coa = []  # Can't await in sync context; skip if no loop
                except RuntimeError:
                    coa = []
                if not coa:
                    # Try cached CoA from org settings
                    from clearledgr.integrations.erp_router import _get_cached_chart_of_accounts
                    cached = _get_cached_chart_of_accounts(self.organization_id)
                    if cached:
                        coa = cached.get("accounts", [])
                if coa:
                    valid_codes = {str(a.get("code") or a.get("id") or "").strip() for a in coa if a.get("active", True)}
                    for item in invoice.line_items:
                        gl = str(item.get("gl_code") or "").strip()
                        if gl and valid_codes and gl not in valid_codes:
                            add_reason(
                                "invalid_gl_code",
                                f"GL code '{gl}' not found in chart of accounts",
                                severity="warning",
                                details={"gl_code": gl, "line_description": item.get("description", "")},
                            )
                            break  # One warning is enough
        except Exception as exc:
            logger.warning("GL code validation against CoA skipped: %s", exc)

        # 5a) Period close — block posting to locked periods.
        try:
            from clearledgr.services.period_close import get_period_close_service
            period_check = get_period_close_service(self.organization_id).check_posting_allowed(
                getattr(invoice, "invoice_date", None),
            )
            if not period_check.get("allowed", True):
                add_reason(
                    "period_locked",
                    period_check.get("message", f"Period {period_check.get('period')} is locked"),
                    severity="error",
                    details=period_check,
                )
        except Exception:
            pass

        # 5b) Tax compliance — validate vendor tax ID if available.
        try:
            from clearledgr.services.tax_compliance import validate_tax_id
            vendor_profile = None
            try:
                vendor_profile = self.db.get_vendor_profile(self.organization_id, invoice.vendor_name) or {}
            except Exception:
                vendor_profile = {}
            meta = vendor_profile.get("metadata") or {}
            if isinstance(meta, str):
                import json as _json
                try:
                    meta = _json.loads(meta)
                except Exception:
                    meta = {}
            erp_tax_id = meta.get("erp_tax_id") or ""
            if erp_tax_id:
                tax_valid = validate_tax_id(erp_tax_id)
                if not tax_valid.get("valid"):
                    add_reason(
                        "invalid_vendor_tax_id",
                        f"Vendor tax ID '{erp_tax_id}' has invalid format",
                        severity="warning",
                        details=tax_valid,
                    )
        except Exception:
            pass

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
            "erp_preflight": erp_preflight,
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
