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
from clearledgr.core.utils import safe_int
from clearledgr.services.invoice_models import InvoiceData

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# §7.6 Extraction Guardrails — module-level helpers
#
# Used by _evaluate_deterministic_validation to enforce the three thesis
# guardrails (reference format, amount range, PO existence) that the
# workflow didn't previously check at gate time. Kept at module level so
# they can be unit-tested in isolation against a mock DB without
# instantiating a full workflow service.
# ---------------------------------------------------------------------------

# Amount-range threshold. The thesis example is £12,000 max → £1,200,000
# invoice (100x) being the obvious case. Catching that cleanly while
# tolerating normal vendor-relationship growth means picking a ceiling
# that's high enough to accept a 5x-scale renewal but low enough to
# catch an order-of-magnitude extraction error. 10x splits the
# difference and matches operator intuition — an invoice an order of
# magnitude larger than anything this vendor has ever sent is worth
# stopping to confirm.
_AMOUNT_RANGE_MULTIPLIER_CEILING = 10.0

# Minimum history required before amount-range checks fire. Without
# enough history we have no meaningful baseline, and flagging every
# second invoice from a new vendor as "outside range" would train
# operators to ignore the signal.
_AMOUNT_RANGE_MIN_HISTORY = 3


def _invoice_reference_shape(reference: str) -> str:
    """Reduce an invoice reference to its structural pattern.

    "INV-2841"  → "AAA-####"
    "INV2841"   → "AAA####"
    "2024-001"  → "####-###"
    "PO-2041"   → "AA-####"

    The shape strips specific values but preserves letter/digit grouping
    and separators so "INV-2841" and "PO-2041" read as different shapes
    (the thesis's canonical example of an extraction error).
    """
    if not reference:
        return ""
    out: list[str] = []
    for ch in str(reference).strip().upper():
        if ch.isalpha():
            out.append("A")
        elif ch.isdigit():
            out.append("#")
        else:
            out.append(ch)
    return "".join(out)


def _check_reference_format(
    db: Any, organization_id: str, vendor_name: str, invoice_number: str,
) -> Optional[tuple]:
    """Return (expected_pattern, observed_pattern) when the invoice
    number's shape doesn't match the vendor's historical dominant
    shape. Returns ``None`` when either: the vendor has no usable
    history, or the shape matches.

    Dominant-shape threshold: ≥ 3 historical invoices AND ≥ 70% of
    them share the same shape. Vendors who use multiple formats
    legitimately (internal vs. reseller references, pre- and
    post-billing-platform-migration) won't trip the check.
    """
    if not hasattr(db, "get_vendor_invoice_history"):
        return None

    try:
        history = db.get_vendor_invoice_history(organization_id, vendor_name, limit=50)
    except Exception:
        return None
    if not history:
        return None

    shapes: Dict[str, int] = {}
    for row in history:
        ref = str((row or {}).get("invoice_number") or "").strip()
        if not ref:
            continue
        shape = _invoice_reference_shape(ref)
        shapes[shape] = shapes.get(shape, 0) + 1

    total = sum(shapes.values())
    if total < 3:
        return None  # Not enough history to establish a pattern.

    # Dominant shape = most common, must cover ≥ 70% of history.
    dominant_shape, dominant_count = max(shapes.items(), key=lambda kv: kv[1])
    if dominant_count / total < 0.7:
        return None  # Vendor uses multiple formats — no single norm.

    observed = _invoice_reference_shape(invoice_number)
    if observed == dominant_shape:
        return None

    return (dominant_shape, observed)


def _check_amount_range(
    db: Any, organization_id: str, vendor_name: str, amount: float,
) -> Optional[tuple]:
    """Return (historical_max, multiplier) when the amount is more than
    ``_AMOUNT_RANGE_MULTIPLIER_CEILING`` times the vendor's largest
    historical non-rejected invoice. Returns ``None`` when within
    range or when history is too thin to establish a baseline.
    """
    if amount is None or amount <= 0:
        return None
    if not hasattr(db, "get_vendor_invoice_history"):
        return None

    try:
        history = db.get_vendor_invoice_history(organization_id, vendor_name, limit=200)
    except Exception:
        return None

    # Filter to invoices that actually got approved/paid — rejected or
    # still-in-exception rows are not a reliable baseline.
    usable = [
        row for row in (history or [])
        if isinstance(row, dict)
        and (row.get("was_approved") or row.get("final_state") in {"posted_to_erp", "closed"})
    ]
    usable_amounts = [float(r.get("amount") or 0) for r in usable if (r.get("amount") or 0) > 0]
    if len(usable_amounts) < _AMOUNT_RANGE_MIN_HISTORY:
        return None

    historical_max = max(usable_amounts)
    if historical_max <= 0:
        return None
    multiplier = amount / historical_max
    if multiplier <= _AMOUNT_RANGE_MULTIPLIER_CEILING:
        return None

    return (historical_max, multiplier)


def _check_po_exists_in_erp(organization_id: str, po_number: str) -> Optional[bool]:
    """Return True if PO exists in the ERP, False if it does not,
    ``None`` if the check cannot be performed (service unavailable,
    ERP offline, etc.) so the caller can decide the default.

    Reads through the canonical PurchaseOrderService, which already
    abstracts over the four connected ERPs + the local PO cache.
    """
    if not po_number:
        return None
    try:
        from clearledgr.services.purchase_orders import get_purchase_order_service
        po_service = get_purchase_order_service(organization_id)
        po = po_service.get_po_by_number(po_number)
        return po is not None
    except Exception:
        return None


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

        # Calibrate field confidences based on historical correction rates
        if invoice.organization_id and invoice.vendor_name and invoice.field_confidences:
            try:
                from clearledgr.services.confidence_calibration import get_confidence_calibrator
                calibrator = get_confidence_calibrator(str(invoice.organization_id))
                calibrated = calibrator.calibrate(invoice.vendor_name, invoice.field_confidences)
                if calibrated:
                    invoice.field_confidences = calibrated
            except Exception:
                pass

            # Record this extraction for calibration tracking
            try:
                from clearledgr.services.confidence_calibration import get_confidence_calibrator
                calibrator = get_confidence_calibrator(str(invoice.organization_id))
                calibrator.record_extraction(
                    invoice.vendor_name,
                    list(invoice.field_confidences.keys()),
                )
            except Exception:
                pass

        if invoice.organization_id and invoice.vendor_name:
            try:
                from clearledgr.services.finance_learning import get_finance_learning_service

                learned_adjustments = get_finance_learning_service(str(invoice.organization_id)).get_extraction_confidence_adjustments(
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
                from clearledgr.services.finance_learning import get_finance_learning_service

                learned_adjustments = get_finance_learning_service(str(organization_id)).get_extraction_confidence_adjustments(
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
            self.db.append_audit_event(
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
        return safe_int(value, default)

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
            self.db.append_audit_event(
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
            self.db.append_audit_event({
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

        # 0b) §7.6 Extraction Guardrail: Amount cross-validation.
        # "The extracted amount is compared against any amount visible in the
        # email subject, body, and attachment. If they disagree, the agent
        # raises a low-confidence flag and does not proceed."
        if invoice.amount and invoice.subject:
            import re
            # Only match amounts that look like currency values:
            # Must have currency symbol OR decimal with 2 digits OR comma-separated thousands
            subject_amounts = re.findall(
                r'[\$\£\€]\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)'  # With currency symbol
                r'|(\d{1,3}(?:,\d{3})+(?:\.\d{2})?)'              # With comma thousands
                r'|(\d+\.\d{2})\b',                                # With exactly 2 decimal places
                invoice.subject,
            )
            for match_groups in subject_amounts:
                sa = next((g for g in match_groups if g), None)
                if not sa:
                    continue
                try:
                    subject_val = float(sa.replace(",", ""))
                    if subject_val >= 10 and abs(subject_val - invoice.amount) > 0.01:
                        # Subject mentions a different amount than extracted (ignore tiny numbers)
                        delta = abs(invoice.amount - subject_val)
                        if delta / max(invoice.amount, subject_val) > 0.02:
                            add_reason(
                                "amount_cross_validation_conflict",
                                (
                                    f"Extracted amount {invoice.currency} {invoice.amount:,.2f} "
                                    f"differs from amount in subject ({subject_val:,.2f}). "
                                    f"Delta: {delta:,.2f}. Requires human review."
                                ),
                                severity="warning",
                                details={
                                    "extracted_amount": invoice.amount,
                                    "subject_amount": subject_val,
                                    "delta": delta,
                                },
                            )
                            break
                except (ValueError, TypeError):
                    pass

        # 0c) §7.6 Extraction Guardrail: Currency consistency.
        # "The extracted currency is validated against the vendor's configured
        # currency in the ERP. A EUR invoice from a GBP vendor is flagged."
        if invoice.currency and invoice.vendor_name:
            try:
                vendor_profile = (
                    self.db.get_vendor_profile(invoice.vendor_name, self.organization_id)
                    if hasattr(self.db, "get_vendor_profile") else None
                )
                if vendor_profile:
                    vendor_currency = (
                        vendor_profile.get("default_currency")
                        or vendor_profile.get("currency")
                        or ""
                    ).upper().strip()
                    invoice_currency = invoice.currency.upper().strip()
                    if vendor_currency and invoice_currency and vendor_currency != invoice_currency:
                        add_reason(
                            "currency_mismatch",
                            (
                                f"Invoice currency {invoice_currency} does not match "
                                f"vendor's configured currency {vendor_currency}. "
                                f"The agent does not convert or assume."
                            ),
                            severity="warning",
                            details={
                                "invoice_currency": invoice_currency,
                                "vendor_currency": vendor_currency,
                                "vendor_name": invoice.vendor_name,
                            },
                        )
            except Exception:
                pass

        # 0d) §7.6 Extraction Guardrail: Reference format validation.
        # "Invoice references are validated against the format pattern
        # established from this vendor's historical invoices. A vendor who
        # always uses INV-XXXX format triggering an extraction of PO-2041
        # is flagged as a possible extraction error."
        if invoice.invoice_number and invoice.vendor_name:
            try:
                mismatch = _check_reference_format(
                    self.db, self.organization_id,
                    invoice.vendor_name, invoice.invoice_number,
                )
                if mismatch is not None:
                    expected_pattern, observed_pattern = mismatch
                    add_reason(
                        "reference_format_mismatch",
                        (
                            f"Extracted invoice reference {invoice.invoice_number!r} "
                            f"does not match this vendor's historical format "
                            f"({expected_pattern}). Observed pattern: {observed_pattern}. "
                            f"Likely extraction error — requires human review."
                        ),
                        severity="warning",
                        details={
                            "invoice_number": invoice.invoice_number,
                            "expected_pattern": expected_pattern,
                            "observed_pattern": observed_pattern,
                            "vendor_name": invoice.vendor_name,
                        },
                    )
            except Exception as exc:
                logger.debug("[guardrail] reference_format check failed: %s", exc)

        # 0e) §7.6 Extraction Guardrail: Amount range check.
        # "An invoice for £1,200,000 from a vendor whose largest previous
        # invoice was £12,000 triggers a mandatory human review regardless
        # of match result. It may be correct — but the agent cannot act
        # on it without explicit confirmation."
        if invoice.amount and invoice.vendor_name:
            try:
                range_breach = _check_amount_range(
                    self.db, self.organization_id,
                    invoice.vendor_name, float(invoice.amount),
                )
                if range_breach is not None:
                    historical_max, multiplier = range_breach
                    add_reason(
                        "amount_outside_vendor_range",
                        (
                            f"Invoice amount {invoice.currency or ''} "
                            f"{invoice.amount:,.2f} is {multiplier:.0f}x the "
                            f"vendor's largest previous invoice "
                            f"({historical_max:,.2f}). Mandatory human review "
                            f"before posting — may be correct but cannot be "
                            f"acted on autonomously."
                        ),
                        severity="warning",
                        details={
                            "invoice_amount": float(invoice.amount),
                            "historical_max": historical_max,
                            "multiplier": multiplier,
                            "vendor_name": invoice.vendor_name,
                        },
                    )
            except Exception as exc:
                logger.debug("[guardrail] amount_range check failed: %s", exc)

        # 0f) §7.6 Extraction Guardrail: PO reference existence.
        # "The extracted PO reference is validated against the ERP before
        # any matching begins. If the PO does not exist, the agent stops
        # immediately. It does not attempt to find a close match or guess
        # an alternative — it reports the exact PO number from the
        # invoice and the fact that it does not exist in the ERP."
        #
        # Deliberately scoped as a pre-matching stop. The post-match
        # po_not_found_in_erp signal fires later inside the match
        # pipeline; this guardrail surfaces the same fact earlier, with
        # the stronger "likely extraction error" framing the thesis
        # describes. Both exist because they answer different
        # operational questions: is this a real PO? vs. did the agent
        # read the PO reference correctly?
        if invoice.po_number and invoice.vendor_name:
            try:
                po_exists = _check_po_exists_in_erp(
                    self.organization_id, str(invoice.po_number).strip(),
                )
                if po_exists is False:
                    add_reason(
                        "po_reference_not_in_erp",
                        (
                            f"PO reference {invoice.po_number} extracted from the "
                            f"invoice does not exist in the ERP. The agent is "
                            f"stopping before 3-way match — this is most likely "
                            f"an extraction error, not a missing PO."
                        ),
                        severity="error",
                        details={
                            "po_number": invoice.po_number,
                            "vendor_name": invoice.vendor_name,
                        },
                    )
            except Exception as exc:
                logger.debug("[guardrail] po_reference_exists check failed: %s", exc)

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
                        invoice_currency=str(invoice.currency or ""),
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
        # Phase 2.1.a: read the stored vendor bank details via the typed
        # decryption accessor — never from `vendor_intelligence` (which
        # would carry plaintext through memory). Persist only the list
        # of MISMATCHED FIELD NAMES in the gate reason details. Never
        # the values themselves: the audit trail records "iban changed"
        # without recording either the old or new IBAN.
        #
        # Phase 2.1.b: when a mismatch is detected on an established
        # vendor, delegate to IbanChangeFreezeService to start a freeze.
        # The freeze will also cause the ``iban_change_pending`` blocking
        # reason code to fire via check 4d below on every subsequent
        # invoice for the vendor until a human completes the three-factor
        # verification flow.
        if isinstance(invoice.bank_details, dict) and invoice.bank_details:
            try:
                from clearledgr.core.stores.bank_details import (
                    diff_bank_details_field_names,
                    normalize_bank_details,
                )
                from clearledgr.services.iban_change_freeze import (
                    get_iban_change_freeze_service,
                )

                stored_bank: Optional[Dict[str, Any]] = None
                if invoice.vendor_name and hasattr(self.db, "get_vendor_bank_details"):
                    try:
                        stored_bank = self.db.get_vendor_bank_details(
                            self.organization_id, invoice.vendor_name
                        )
                    except Exception as fetch_exc:
                        logger.warning(
                            "Bank details fetch failed for %s/%s: %s",
                            self.organization_id, invoice.vendor_name, fetch_exc,
                        )
                        stored_bank = None

                if stored_bank:
                    extracted_clean = normalize_bank_details(invoice.bank_details)
                    mismatch_fields = diff_bank_details_field_names(
                        extracted_clean, stored_bank
                    )
                    if mismatch_fields:
                        sev = "error" if (invoice.amount or 0) >= 5000 else "warning"
                        add_reason(
                            "bank_details_mismatch_from_invoice",
                            (
                                "Bank details on invoice differ from vendor "
                                f"profile on: {', '.join(mismatch_fields)}"
                            ),
                            severity=sev,
                            details={
                                # Field names only — NEVER the values.
                                # DESIGN_THESIS.md §19: no plaintext bank
                                # data in audit logs.
                                "mismatched_fields": mismatch_fields,
                            },
                        )

                        # Auto-start the freeze when the IBAN or any
                        # sensitive banking field has changed. The
                        # freeze service records the pending details on
                        # the vendor profile and runs the email-domain
                        # factor auto-check. The existing
                        # ``bank_details_mismatch_from_invoice`` reason
                        # code above already blocks THIS invoice; the
                        # freeze also blocks every FUTURE invoice for
                        # this vendor via the check below.
                        try:
                            freeze_svc = get_iban_change_freeze_service(
                                self.organization_id, db=self.db
                            )
                            sender_domain = ""
                            sender_field = str(invoice.sender or "")
                            if "@" in sender_field:
                                sender_domain = sender_field.rsplit("@", 1)[-1]
                            sender_domain = sender_domain.strip().lower().strip(">")
                            ap_item_id_for_audit = self._lookup_ap_item_id(
                                gmail_id=invoice.gmail_id,
                                vendor_name=invoice.vendor_name,
                                invoice_number=invoice.invoice_number,
                            ) if hasattr(self, "_lookup_ap_item_id") else None
                            freeze_svc.detect_and_maybe_freeze(
                                vendor_name=invoice.vendor_name,
                                extracted_bank_details=extracted_clean,
                                sender_domain=sender_domain,
                                triggering_ap_item_id=ap_item_id_for_audit,
                            )
                        except Exception as freeze_exc:
                            logger.warning(
                                "Auto-freeze detect failed (non-fatal): %s",
                                freeze_exc,
                            )
            except Exception as bank_exc:
                logger.warning("Bank details comparison failed (non-fatal): %s", bank_exc)

        # 4d) IBAN change freeze — blocks every invoice for a vendor
        # whose freeze is still active (Phase 2.1.b). This fires
        # independently of the 4c mismatch detection above: the freeze
        # persists across multiple invoices until a human completes
        # three-factor verification via the /iban-verification API.
        # Severity=error so the Phase 1.1 enforcement machinery
        # force-escalates any LLM 'approve' on a frozen vendor.
        try:
            if invoice.vendor_name and hasattr(self.db, "is_iban_change_pending"):
                if self.db.is_iban_change_pending(
                    self.organization_id, invoice.vendor_name
                ):
                    verification_state = self.db.get_iban_change_verification_state(
                        self.organization_id, invoice.vendor_name
                    ) or {}
                    missing = [
                        name
                        for name in (
                            "email_domain_factor",
                            "phone_factor",
                            "sign_off_factor",
                        )
                        if not (verification_state.get(name) or {}).get("verified")
                    ]
                    add_reason(
                        "iban_change_pending",
                        (
                            f"Vendor '{invoice.vendor_name}' has a pending IBAN "
                            "change freeze. All invoices for this vendor are "
                            "blocked until three-factor verification completes "
                            "(DESIGN_THESIS.md §8)."
                        ),
                        severity="error",
                        details={
                            # Factor names only — never values. The
                            # pending bank details themselves live on
                            # the vendor profile's encrypted column.
                            "missing_factors": missing,
                        },
                    )
        except Exception as freeze_check_exc:
            logger.warning(
                "IBAN change freeze check failed (non-fatal): %s",
                freeze_check_exc,
            )

        # 4e) Vendor domain lock (Phase 2.2, DESIGN_THESIS.md §8 Group B).
        # Detects vendor impersonation: an inbound invoice arriving
        # from a sender domain that doesn't match the vendor's known
        # allowlist is blocked as suspected impersonation. Bootstrap
        # is covered by first_payment_hold — vendors with no known
        # domains yet skip this check and TOFU on the first successful
        # post via VendorDomainTrackingObserver.
        try:
            if invoice.vendor_name:
                from clearledgr.services.vendor_domain_lock import (
                    get_vendor_domain_lock_service,
                    STATUS_MISMATCH,
                )

                lock_svc = get_vendor_domain_lock_service(
                    self.organization_id, db=self.db
                )
                domain_result = lock_svc.check_sender_domain(
                    vendor_name=invoice.vendor_name,
                    sender=invoice.sender,
                )
                if domain_result.should_block:
                    add_reason(
                        "vendor_sender_domain_mismatch",
                        (
                            f"Invoice sender domain "
                            f"'{domain_result.sender_domain}' is not in the "
                            f"trusted allowlist for vendor "
                            f"'{invoice.vendor_name}'. Potential vendor "
                            "impersonation (DESIGN_THESIS.md §8)."
                        ),
                        severity="error",
                        details={
                            "sender_domain": domain_result.sender_domain,
                            "trusted_domains": domain_result.known_domains,
                        },
                    )

                # 4e.1) §8 Domain similarity detection. The thesis calls
                # this out by name: "Domain similarity detection flags
                # 'str1pe.com' emails when 'stripe.com' is in the vendor
                # master." Runs whenever the sender isn't an allowlisted
                # domain — that catches both the mismatched-vendor case
                # (invoice from str1pe.com claiming to be from Stripe) and
                # the unknown-vendor case (invoice from str1pe.com with no
                # vendor resolution yet). Fires AS WELL AS the mismatch
                # reason above so the AP Manager sees both facts:
                # "not on allowlist" + "looks like a known vendor".
                try:
                    from clearledgr.services.vendor_domain_lookalike import (
                        collect_org_trusted_domains,
                        detect_lookalike,
                    )
                    sender_domain = domain_result.sender_domain
                    is_not_allowlisted = domain_result.status in {
                        "mismatch", "no_known_domains",
                    }
                    if sender_domain and is_not_allowlisted:
                        trusted = collect_org_trusted_domains(
                            self.db, self.organization_id
                        )
                        lookalike = detect_lookalike(sender_domain, trusted)
                        if lookalike is not None:
                            add_reason(
                                "vendor_lookalike_domain",
                                (
                                    f"Sender domain "
                                    f"'{lookalike.sender_domain}' resembles "
                                    f"trusted vendor domain "
                                    f"'{lookalike.suspected_impersonation}' "
                                    f"({lookalike.category} match). "
                                    f"Likely impersonation attempt — do not "
                                    f"process without verifying out of band."
                                ),
                                severity="error",
                                details={
                                    "sender_domain": lookalike.sender_domain,
                                    "suspected_impersonation":
                                        lookalike.suspected_impersonation,
                                    "category": lookalike.category,
                                    "score": lookalike.score,
                                },
                            )
                except Exception as lookalike_exc:
                    logger.debug(
                        "[lookalike] detection failed (non-fatal): %s",
                        lookalike_exc,
                    )
        except Exception as domain_check_exc:
            logger.warning(
                "Vendor domain lock check failed (non-fatal): %s",
                domain_check_exc,
            )

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

        # 5c) Currency mismatch — convert if entity default differs from invoice currency.
        try:
            invoice_currency = str(invoice.currency or "").strip().upper()
            if invoice_currency and invoice_currency != "USD":
                from clearledgr.services.fx_conversion import convert
                org_currency = str(
                    (self._settings or {}).get("default_currency", "USD")
                ).strip().upper()
                if org_currency and invoice_currency != org_currency:
                    fx = convert(invoice.amount or 0, invoice_currency, org_currency)
                    if fx.get("converted_amount") is not None:
                        add_reason(
                            "currency_conversion_applied",
                            f"Invoice in {invoice_currency}, org uses {org_currency}. "
                            f"Converted: {org_currency} {fx['converted_amount']:,.2f} (rate: {fx['rate']})",
                            severity="info",
                            details=fx,
                        )
                    elif fx.get("error"):
                        add_reason(
                            "currency_conversion_unavailable",
                            f"Cannot convert {invoice_currency} to {org_currency}: {fx['error']}",
                            severity="warning",
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

        # ---------------------------------------------------------------
        # 6) Fraud-control primitives (DESIGN_THESIS.md §8 — architectural,
        #    not configurational). Every check here uses severity="error"
        #    so it is unambiguously blocking. Numeric parameters come from
        #    the organization's fraud_controls config (CFO-role-gated).
        # ---------------------------------------------------------------
        try:
            from clearledgr.core.fraud_controls import (
                load_fraud_controls,
                evaluate_payment_ceiling,
            )
            from clearledgr.core.prompt_guard import scan_invoice_fields

            fraud_config = load_fraud_controls(self.organization_id, self.db)
        except Exception as fc_exc:
            # If config loading itself fails, FAIL CLOSED with a specific
            # reason code. "No silent disabling" of fraud controls.
            logger.error(
                "[Gate] Failed to load fraud_controls for org %s — failing closed: %s",
                self.organization_id,
                fc_exc,
            )
            add_reason(
                "fraud_control_config_unavailable",
                "Could not load fraud-control configuration; invoice held for review",
                severity="error",
                details={"error": str(fc_exc)},
            )
            fraud_config = None

        if fraud_config is not None:
            # 6a) Payment amount ceiling (fail-closed on FX unavailability)
            try:
                ceiling_result = evaluate_payment_ceiling(
                    invoice_amount=float(invoice.amount or 0),
                    invoice_currency=str(invoice.currency or ""),
                    config=fraud_config,
                )
                if ceiling_result.fx_unavailable:
                    add_reason(
                        "fraud_control_fx_unavailable",
                        (
                            f"Cannot convert invoice amount from "
                            f"{ceiling_result.invoice_currency} to "
                            f"{ceiling_result.base_currency} to verify "
                            "payment ceiling; holding for review."
                        ),
                        severity="error",
                        details={
                            "invoice_amount": ceiling_result.invoice_amount,
                            "invoice_currency": ceiling_result.invoice_currency,
                            "base_currency": ceiling_result.base_currency,
                            "ceiling": ceiling_result.ceiling,
                        },
                    )
                elif ceiling_result.exceeds_ceiling:
                    add_reason(
                        "payment_ceiling_exceeded",
                        (
                            f"Invoice amount {ceiling_result.base_currency} "
                            f"{ceiling_result.converted_amount:,.2f} exceeds "
                            f"the configured payment ceiling of "
                            f"{ceiling_result.base_currency} "
                            f"{ceiling_result.ceiling:,.2f}. Auto-approval "
                            "is not permitted for amounts above the ceiling "
                            "(DESIGN_THESIS.md §8)."
                        ),
                        severity="error",
                        details={
                            "invoice_amount": ceiling_result.invoice_amount,
                            "invoice_currency": ceiling_result.invoice_currency,
                            "converted_amount": ceiling_result.converted_amount,
                            "base_currency": ceiling_result.base_currency,
                            "rate": ceiling_result.rate,
                            "ceiling": ceiling_result.ceiling,
                        },
                    )
            except Exception as ceiling_exc:
                logger.warning(
                    "[Gate] Payment ceiling evaluation raised: %s", ceiling_exc
                )
                add_reason(
                    "payment_ceiling_evaluation_failed",
                    "Payment ceiling check failed unexpectedly; invoice held for review",
                    severity="error",
                    details={"error": str(ceiling_exc)},
                )

            # 6b) First payment hold — block the first invoice from a new
            # vendor, OR the first invoice after extended dormancy.
            try:
                vendor_profile_for_first_payment = None
                if invoice.vendor_name:
                    vendor_profile_for_first_payment = self.db.get_vendor_profile(
                        self.organization_id, invoice.vendor_name
                    )

                is_brand_new = (
                    vendor_profile_for_first_payment is None
                    or int(vendor_profile_for_first_payment.get("invoice_count") or 0) == 0
                )

                is_dormant = False
                dormancy_days_observed: Optional[int] = None
                if (
                    vendor_profile_for_first_payment is not None
                    and not is_brand_new
                ):
                    last_invoice_at = (
                        vendor_profile_for_first_payment.get("last_invoice_date")
                        or vendor_profile_for_first_payment.get("last_invoice_at")
                        or vendor_profile_for_first_payment.get("updated_at")
                    )
                    if last_invoice_at:
                        try:
                            if isinstance(last_invoice_at, str):
                                last_dt = datetime.fromisoformat(
                                    last_invoice_at[:19].replace("Z", "+00:00")
                                )
                                if last_dt.tzinfo is None:
                                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                            else:
                                last_dt = last_invoice_at
                            days_since = (datetime.now(timezone.utc) - last_dt).days
                            dormancy_days_observed = days_since
                            if days_since >= fraud_config.first_payment_dormancy_days:
                                is_dormant = True
                        except Exception as dormancy_parse_exc:
                            logger.debug(
                                "[Gate] Could not parse last_invoice_at for dormancy check: %s",
                                dormancy_parse_exc,
                            )

                if is_brand_new:
                    add_reason(
                        "first_payment_hold",
                        (
                            f"First invoice from new vendor '{invoice.vendor_name}'. "
                            "New-vendor first payments are held for human review "
                            "per DESIGN_THESIS.md §8 (anti-vendor-impersonation)."
                        ),
                        severity="error",
                        details={
                            "vendor_name": invoice.vendor_name,
                            "reason": "new_vendor",
                        },
                    )
                elif is_dormant:
                    add_reason(
                        "first_payment_hold",
                        (
                            f"Vendor '{invoice.vendor_name}' has been dormant "
                            f"for {dormancy_days_observed} days (threshold: "
                            f"{fraud_config.first_payment_dormancy_days}). "
                            "Treating as first-payment hold per DESIGN_THESIS.md §8."
                        ),
                        severity="error",
                        details={
                            "vendor_name": invoice.vendor_name,
                            "reason": "dormancy",
                            "days_since_last_invoice": dormancy_days_observed,
                            "dormancy_threshold_days": fraud_config.first_payment_dormancy_days,
                        },
                    )
            except Exception as first_payment_exc:
                logger.warning(
                    "[Gate] First payment hold evaluation raised: %s",
                    first_payment_exc,
                )

            # 6c) Vendor velocity — block if vendor has submitted more than
            # the configured max invoices in the last 7 days.
            try:
                if invoice.vendor_name and hasattr(self.db, "get_ap_items_by_vendor"):
                    recent = self.db.get_ap_items_by_vendor(
                        self.organization_id,
                        invoice.vendor_name,
                        days=7,
                        limit=fraud_config.vendor_velocity_max_per_week + 5,
                    ) or []
                    # Exclude the current in-flight invoice (if it's already
                    # persisted) and rejected items, which do not contribute
                    # to velocity.
                    current_id = str(
                        getattr(invoice, "gmail_id", None)
                        or getattr(invoice, "ap_item_id", None)
                        or ""
                    )
                    active_recent = [
                        item
                        for item in recent
                        if str(item.get("id") or "") != current_id
                        and str(item.get("state") or "").lower() != "rejected"
                    ]
                    velocity_count = len(active_recent)
                    if velocity_count >= fraud_config.vendor_velocity_max_per_week:
                        add_reason(
                            "vendor_velocity_exceeded",
                            (
                                f"Vendor '{invoice.vendor_name}' has submitted "
                                f"{velocity_count} invoices in the last 7 days "
                                f"(configured max: "
                                f"{fraud_config.vendor_velocity_max_per_week}). "
                                "High-velocity submissions are held for human "
                                "review per DESIGN_THESIS.md §8."
                            ),
                            severity="error",
                            details={
                                "vendor_name": invoice.vendor_name,
                                "observed_count_7d": velocity_count,
                                "configured_max_per_week": (
                                    fraud_config.vendor_velocity_max_per_week
                                ),
                            },
                        )
            except Exception as velocity_exc:
                logger.warning(
                    "[Gate] Velocity evaluation raised: %s", velocity_exc
                )

            # 6d) Prompt injection detection across untrusted invoice fields.
            try:
                line_item_descriptions = []
                if isinstance(invoice.line_items, list):
                    for li in invoice.line_items:
                        if isinstance(li, dict) and li.get("description"):
                            line_item_descriptions.append(str(li["description"]))

                injection_results = scan_invoice_fields(
                    subject=invoice.subject or "",
                    vendor_name=invoice.vendor_name or "",
                    email_body=getattr(invoice, "invoice_text", "") or "",
                    attachment_text="",  # reserved for future pdf-text field
                    line_item_descriptions=line_item_descriptions,
                )
                detected_fields: List[Dict[str, Any]] = []
                all_matched_patterns: List[str] = []
                for idx, result in enumerate(injection_results):
                    if result.detected:
                        # Map index to field name for the audit trail.
                        if idx == 0:
                            field = "subject"
                        elif idx == 1:
                            field = "vendor_name"
                        elif idx == 2:
                            field = "invoice_text"
                        elif idx == 3:
                            field = "attachment_text"
                        else:
                            field = f"line_item_{idx - 4}_description"
                        detected_fields.append(
                            {
                                "field": field,
                                "matched_patterns": list(result.matched_patterns),
                            }
                        )
                        all_matched_patterns.extend(result.matched_patterns)

                if detected_fields:
                    add_reason(
                        "prompt_injection_detected",
                        (
                            "Prompt injection patterns detected in untrusted "
                            f"invoice fields: "
                            f"{', '.join(d['field'] for d in detected_fields)}. "
                            "Invoice rejected as attempted manipulation per "
                            "DESIGN_THESIS.md §8."
                        ),
                        severity="error",
                        details={
                            "detected_fields": detected_fields,
                            "unique_patterns": sorted(set(all_matched_patterns)),
                        },
                    )
            except Exception as injection_exc:
                logger.warning(
                    "[Gate] Injection detection raised: %s", injection_exc
                )

            # 6e) Vendor risk score gating — §3: "the agent's autonomy
            # thresholds adjust accordingly" for high-risk vendors.
            try:
                from clearledgr.services.vendor_risk import VendorRiskScoreService

                risk_service = VendorRiskScoreService(
                    organization_id=self.organization_id, db=self.db,
                )
                risk_result = risk_service.compute(
                    vendor_name=invoice.vendor_name or "",
                )
                if risk_result and risk_result.score >= 70:
                    add_reason(
                        "vendor_high_risk",
                        (
                            f"Vendor '{invoice.vendor_name}' has a risk score "
                            f"of {risk_result.score}/100 ({risk_result.level}). "
                            "High-risk vendors require human review per "
                            "DESIGN_THESIS.md §3."
                        ),
                        severity="warning",
                        details={
                            "vendor_name": invoice.vendor_name,
                            "risk_score": risk_result.score,
                            "risk_level": risk_result.level,
                            "risk_components": [
                                {"name": c.name, "points": c.points}
                                for c in (risk_result.components or [])
                                if c.points > 0
                            ],
                        },
                    )
                    # §6: Record fraud flag on the Box
                    try:
                        _ap_id = getattr(self, "_current_ap_item_id", None) or (invoice.gmail_id if hasattr(invoice, "gmail_id") else None)
                        if _ap_id and hasattr(self, "add_fraud_flag"):
                            self.add_fraud_flag(_ap_id, f"vendor_high_risk:{risk_result.score}")
                    except Exception:
                        pass
            except Exception as risk_exc:
                logger.warning(
                    "[Gate] Vendor risk evaluation raised: %s", risk_exc
                )

        # Gate "passed" is governed by severity, not raw reason-code count.
        # - severity="error":  definitive failure; blocks auto-approval.
        # - severity="warning": still blocks auto-approval (needs human review).
        # - severity="info":    surfaced in the gate dict for audit/telemetry,
        #   but does NOT block. (Example: 'discount_applied' — informational
        #   note about a legitimately discounted invoice.)
        # Every fraud-control primitive added to the gate uses severity="error"
        # so they are unambiguously blocking per DESIGN_THESIS.md §8.
        blocking_severities = {"error", "warning"}
        blocking_reasons = [
            r for r in reasons if str(r.get("severity") or "").lower() in blocking_severities
        ]
        blocking_reason_codes = [
            code
            for code in reason_codes
            if any(
                str(r.get("code") or "").lower() == code
                and str(r.get("severity") or "").lower() in blocking_severities
                for r in reasons
            )
        ]

        gate = {
            "passed": len(blocking_reasons) == 0,
            "checked_at": checked_at,
            # reason_codes continues to list ALL codes (including info) for
            # backward-compatible telemetry. The Phase 1.1 enforcement layer
            # reads gate.passed, not reason_codes, so info codes no longer
            # cause spurious overrides.
            "reason_codes": reason_codes,
            "blocking_reason_codes": blocking_reason_codes,
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
                self.db.append_audit_event(
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
