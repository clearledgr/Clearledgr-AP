"""AP item APIs used by the Gmail extension focus-first sidebar."""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.ap_confidence import evaluate_critical_field_confidence
from clearledgr.core.auth import get_current_user, require_ops_user
from clearledgr.core.database import ClearledgrDB, get_db
from clearledgr.core.ap_states import APState
from clearledgr.core.errors import safe_error
from clearledgr.api.deps import verify_org_access
from clearledgr.services.ap_context_connectors import build_multi_system_context
from clearledgr.services.ap_operator_audit import normalize_operator_audit_events
from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime


router = APIRouter(prefix="/api/ap/items", tags=["ap-items"])
logger = logging.getLogger(__name__)


class LinkSourceRequest(BaseModel):
    source_type: str = Field(..., min_length=1)
    source_ref: str = Field(..., min_length=1)
    subject: Optional[str] = None
    sender: Optional[str] = None
    detected_at: Optional[str] = None
    metadata: Dict[str, Any] = {}


class MergeItemsRequest(BaseModel):
    source_ap_item_id: str = Field(..., min_length=1)
    actor_id: str = Field(default="system", min_length=1)
    reason: str = Field(default="manual_merge", min_length=1)


class SplitSourceRequest(BaseModel):
    source_type: str = Field(..., min_length=1)
    source_ref: str = Field(..., min_length=1)


class SplitItemRequest(BaseModel):
    actor_id: str = Field(default="system", min_length=1)
    reason: str = Field(default="manual_split", min_length=1)
    sources: List[SplitSourceRequest] = []


class ResubmitRejectedItemRequest(BaseModel):
    actor_id: str = Field(default="system", min_length=1)
    reason: str = Field(default="corrected_resubmission", min_length=1)
    initial_state: str = Field(default="received", min_length=1)
    copy_sources: bool = True
    thread_id: Optional[str] = None
    message_id: Optional[str] = None
    subject: Optional[str] = None
    sender: Optional[str] = None
    vendor_name: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    metadata: Dict[str, Any] = {}


class ResolveFieldReviewRequest(BaseModel):
    field: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1, description="email, attachment, or manual")
    manual_value: Optional[Any] = None
    note: Optional[str] = None
    auto_resume: bool = True


class BulkResolveFieldReviewRequest(BaseModel):
    ap_item_ids: List[str] = Field(..., min_length=1)
    field: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1, description="email, attachment, or manual")
    manual_value: Optional[Any] = None
    note: Optional[str] = None
    auto_resume: bool = True


class ResolveNonInvoiceReviewRequest(BaseModel):
    outcome: str = Field(..., min_length=1)
    related_reference: Optional[str] = None
    related_ap_item_id: Optional[str] = None
    note: Optional[str] = None
    close_record: bool = True


def _authenticated_actor(user: Any, fallback: str = "system") -> str:
    return str(
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or fallback
    ).strip() or fallback


def _parse_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _parse_json_list(raw: Any) -> List[Any]:
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _vendor_followup_sla_hours() -> int:
    try:
        hours = int(os.getenv("CLEARLEDGR_VENDOR_FOLLOWUP_SLA_HOURS", "24"))
    except (TypeError, ValueError):
        hours = 24
    return max(1, min(hours, 168))


def _vendor_followup_max_attempts() -> int:
    try:
        attempts = int(os.getenv("CLEARLEDGR_VENDOR_FOLLOWUP_MAX_ATTEMPTS", "3"))
    except (TypeError, ValueError):
        attempts = 3
    return max(1, min(attempts, 10))


def _derive_followup_next_action(
    *,
    state: str,
    metadata: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Optional[str]:
    if state != APState.NEEDS_INFO.value:
        return None
    normalized = str(metadata.get("followup_next_action") or "").strip().lower()
    attempts = max(0, _safe_int(metadata.get("followup_attempt_count"), 0))
    max_attempts = _vendor_followup_max_attempts()
    now_utc = now or datetime.now(timezone.utc)

    due_at = _parse_iso(metadata.get("followup_sla_due_at"))
    if due_at is None:
        last_sent_at = _parse_iso(metadata.get("followup_last_sent_at"))
        if last_sent_at is not None:
            due_at = last_sent_at + timedelta(hours=_vendor_followup_sla_hours())

    if attempts >= max_attempts:
        return "manual_vendor_escalation"
    if due_at is not None and due_at <= now_utc:
        return "nudge_vendor_followup"
    if attempts <= 0 and not str(metadata.get("needs_info_draft_id") or "").strip():
        return "prepare_vendor_followup_draft"
    return normalized or "await_vendor_response"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_optional_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_FIELD_REVIEW_MUTABLE_FIELDS = {
    "amount",
    "currency",
    "invoice_number",
    "vendor",
    "due_date",
    "document_type",
}

_NON_INVOICE_ALLOWED_OUTCOMES = {
    "credit_note": {"apply_to_invoice", "record_vendor_credit", "needs_followup"},
    "refund": {"link_to_payment", "record_vendor_refund", "needs_followup"},
    "receipt": {"link_to_payment", "archive_receipt", "needs_followup"},
    "payment": {"link_to_payment", "record_payment_confirmation", "needs_followup"},
    "payment_request": {"route_outside_invoice_workflow", "needs_followup"},
    "statement": {"send_to_reconciliation", "needs_followup"},
    "bank_statement": {"send_to_reconciliation", "needs_followup"},
    "other": {"mark_reviewed", "needs_followup"},
}


def _normalize_field_review_field(raw: Any) -> str:
    return str(raw or "").strip().lower()


def _normalize_field_review_source(raw: Any) -> str:
    token = str(raw or "").strip().lower().replace("-", "_")
    if token in {"email", "attachment", "manual"}:
        return token
    if token in {"manual_value", "manual_entry"}:
        return "manual"
    return token


def _normalize_document_type_token(raw: Any) -> str:
    token = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token == "credit_memo":
        return "credit_note"
    if token == "payment_confirmation":
        return "payment"
    if token == "bank_statement":
        return "statement"
    return token or "invoice"


def _normalize_non_invoice_outcome(raw: Any) -> str:
    return str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")


def _get_conflict_field(raw: Any) -> str:
    if isinstance(raw, dict):
        return _normalize_field_review_field(raw.get("field") or raw.get("code"))
    if isinstance(raw, str):
        return _normalize_field_review_field(raw)
    return ""


def _resolve_field_review_source_value(
    blocker: Optional[Dict[str, Any]],
    *,
    source: str,
    manual_value: Any,
) -> Any:
    if source == "manual":
        return manual_value
    blocker_payload = blocker if isinstance(blocker, dict) else {}
    return blocker_payload.get(f"{source}_value")


def _coerce_field_review_value(field: str, value: Any) -> Any:
    token = _normalize_field_review_field(field)
    if token not in _FIELD_REVIEW_MUTABLE_FIELDS:
        raise HTTPException(status_code=400, detail="unsupported_field_review_field")

    if token == "amount":
        numeric = _coerce_optional_float(value)
        if numeric is None:
            raise HTTPException(status_code=400, detail="invalid_amount_resolution")
        return round(numeric, 2)

    if token == "currency":
        resolved = str(value or "").strip().upper()
        if not resolved:
            raise HTTPException(status_code=400, detail="invalid_currency_resolution")
        return resolved

    if token == "document_type":
        resolved = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if resolved == "credit_memo":
            resolved = "credit_note"
        if resolved == "bank_statement":
            resolved = "statement"
        if resolved == "payment_confirmation":
            resolved = "payment"
        if resolved not in {"invoice", "receipt", "payment_request", "payment", "refund", "credit_note", "statement"}:
            raise HTTPException(status_code=400, detail="invalid_document_type_resolution")
        return resolved

    resolved = str(value or "").strip()
    if not resolved:
        raise HTTPException(status_code=400, detail="invalid_field_review_value")
    return resolved


def _field_resolution_column_updates(field: str, value: Any) -> Dict[str, Any]:
    token = _normalize_field_review_field(field)
    if token == "vendor":
        return {"vendor_name": value}
    if token in {"amount", "currency", "invoice_number", "due_date"}:
        return {token: value}
    return {}


def _filter_allowed_ap_item_updates(db: ClearledgrDB, updates: Dict[str, Any]) -> Dict[str, Any]:
    allowed = getattr(db, "_AP_ITEM_ALLOWED_COLUMNS", None)
    filtered = dict(updates)
    if isinstance(allowed, (set, frozenset)):
        filtered = {
            key: value
            for key, value in filtered.items()
            if key in allowed
        }
    serialized: Dict[str, Any] = {}
    for key, value in filtered.items():
        if key != "metadata" and isinstance(value, (dict, list)):
            serialized[key] = json.dumps(value)
        else:
            serialized[key] = value
    return serialized


def _build_operator_truth_context(
    db: ClearledgrDB,
    *,
    item: Dict[str, Any],
    metadata: Dict[str, Any],
    field: str,
    selected_source: str,
    blocker: Optional[Dict[str, Any]] = None,
    expected_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source_rows = db.list_ap_item_sources(str(item.get("id") or "").strip()) if hasattr(db, "list_ap_item_sources") else []
    primary_source_meta: Dict[str, Any] = {}
    if isinstance(source_rows, list):
        for row in source_rows:
            source_meta = _parse_json((row or {}).get("metadata"))
            if source_meta:
                primary_source_meta = source_meta
                break
    attachment_names = metadata.get("attachment_names")
    if not isinstance(attachment_names, list):
        attachment_names = primary_source_meta.get("attachment_names")
    document_type = metadata.get("document_type") or metadata.get("email_type") or item.get("document_type")
    return {
        "ap_item_id": item.get("id"),
        "field": field,
        "vendor": item.get("vendor_name") or item.get("vendor"),
        "sender": item.get("sender"),
        "subject": item.get("subject"),
        "snippet": metadata.get("source_snippet") or primary_source_meta.get("snippet"),
        "body_excerpt": metadata.get("source_body_excerpt") or primary_source_meta.get("body_excerpt"),
        "attachment_names": attachment_names if isinstance(attachment_names, list) else [],
        "document_type": document_type,
        "selected_source": selected_source,
        "source_channel": "gmail_route",
        "event_source": "field_review_resolution",
        "expected_fields": expected_fields or {},
        "blocker": blocker or {},
    }


def _should_auto_resume_after_field_resolution(item: Dict[str, Any]) -> bool:
    state = str(item.get("state") or "").strip().lower()
    document_type = _normalize_document_type_token(item.get("document_type"))
    return (
        state in {"ready_to_post", "failed_post"}
        and document_type == "invoice"
        and not bool(item.get("requires_field_review"))
    )


def _non_invoice_resolution_state(
    *,
    current_state: str,
    outcome: str,
    close_record: bool,
) -> str:
    if outcome == "needs_followup":
        return APState.NEEDS_INFO.value
    return current_state


def _non_invoice_resolution_semantics(
    *,
    document_type: str,
    outcome: str,
    close_record: bool,
) -> Dict[str, Any]:
    normalized_type = _normalize_document_type_token(document_type)
    normalized_outcome = _normalize_non_invoice_outcome(outcome)

    semantics = {
        "document_type": normalized_type,
        "accounting_treatment": "finance_document_reviewed",
        "downstream_queue": "finance_review",
        "review_status": "resolved" if close_record and normalized_outcome != "needs_followup" else "open",
        "blocks_invoice_workflow": normalized_type != "invoice",
    }

    if normalized_type == "credit_note":
        semantics.update(
            accounting_treatment="vendor_credit_applied" if normalized_outcome == "apply_to_invoice" else "vendor_credit_recorded",
            downstream_queue="vendor_credit_ledger",
        )
    elif normalized_type == "refund":
        semantics.update(
            accounting_treatment="vendor_refund_linked" if normalized_outcome == "link_to_payment" else "vendor_refund_recorded",
            downstream_queue="cash_application",
        )
    elif normalized_type == "receipt":
        semantics.update(
            accounting_treatment="expense_receipt_linked" if normalized_outcome == "link_to_payment" else "expense_receipt_archived",
            downstream_queue="expense_evidence",
        )
    elif normalized_type == "payment":
        semantics.update(
            accounting_treatment="payment_confirmation_linked" if normalized_outcome == "link_to_payment" else "payment_confirmation_recorded",
            downstream_queue="cash_disbursements",
        )
    elif normalized_type in {"statement", "bank_statement"}:
        semantics.update(
            accounting_treatment="queued_for_reconciliation",
            downstream_queue="reconciliation",
        )
    elif normalized_type == "payment_request":
        semantics.update(
            accounting_treatment="routed_outside_invoice_workflow",
            downstream_queue="payment_operations",
        )

    if normalized_outcome == "needs_followup":
        semantics.update(
            accounting_treatment=f"{normalized_type}_needs_followup",
            downstream_queue="operator_followup",
            review_status="open",
        )

    return semantics


def _derive_attachment_summary(
    payload: Dict[str, Any],
    metadata: Dict[str, Any],
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:
    attachment_url = str(payload.get("attachment_url") or metadata.get("attachment_url") or "").strip()
    attachment_count = max(
        _safe_int(payload.get("attachment_count"), 0),
        _safe_int(metadata.get("attachment_count"), 0),
    )
    attachment_names: List[str] = []
    has_attachment = bool(payload.get("has_attachment") or metadata.get("has_attachment") or attachment_url)

    def _append_name(value: Any) -> None:
        token = str(value or "").strip()
        if not token or token in attachment_names:
            return
        attachment_names.append(token)

    for source in sources:
        source_meta = _parse_json(source.get("metadata"))
        if not source_meta:
            continue
        attachment_count = max(attachment_count, _safe_int(source_meta.get("attachment_count"), 0))
        source_attachment_url = str(source_meta.get("attachment_url") or "").strip()
        if source_attachment_url and not attachment_url:
            attachment_url = source_attachment_url
        if source_meta.get("has_attachment") or source_attachment_url:
            has_attachment = True
        raw_names = source_meta.get("attachment_names")
        if isinstance(raw_names, list):
            for name in raw_names:
                _append_name(name)

    if not has_attachment:
        subject = str(payload.get("subject") or "").strip().lower()
        sender = str(payload.get("sender") or "").strip().lower()
        # Historical Gmail intake rows did not persist attachment metadata.
        # These sender/subject patterns are narrow enough to recover the file
        # signal for invoice emails that reliably ship with an attached doc.
        if "payments-noreply@google.com" in sender and "invoice is available" in subject:
            has_attachment = True
        elif "invoice+statements+" in sender and "@stripe.com" in sender:
            has_attachment = True

    if has_attachment and attachment_count <= 0:
        attachment_count = 1

    return {
        "has_attachment": has_attachment,
        "attachment_count": attachment_count,
        "attachment_url": attachment_url or None,
        "attachment_names": attachment_names,
    }


def _derive_confidence_gate(payload: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    raw_gate = metadata.get("confidence_gate")
    if isinstance(raw_gate, dict) and "requires_field_review" in raw_gate:
        gate = dict(raw_gate)
        blockers = gate.get("confidence_blockers")
        gate["confidence_blockers"] = blockers if isinstance(blockers, list) else []
        gate["requires_field_review"] = bool(gate.get("requires_field_review"))
        return gate

    # Prefer first-class column value over metadata blob for field confidences
    raw_fc = payload.get("field_confidences") or metadata.get("field_confidences")
    if isinstance(raw_fc, str):
        try:
            raw_fc = json.loads(raw_fc)
        except (json.JSONDecodeError, TypeError):
            raw_fc = None

    return evaluate_critical_field_confidence(
        overall_confidence=payload.get("confidence"),
        field_values={
            "vendor": payload.get("vendor_name"),
            "amount": payload.get("amount"),
            "invoice_number": payload.get("invoice_number"),
            "due_date": payload.get("due_date"),
        },
        field_confidences=raw_fc,
    )


def _derive_next_action(payload: Dict[str, Any]) -> str:
    if payload.get("is_merged_source") or payload.get("merged_into"):
        return "none"
    state = str(payload.get("state") or "").strip().lower()
    document_type = _normalize_document_type_token(payload.get("document_type"))
    if document_type != "invoice":
        resolution = payload.get("non_invoice_resolution") or {}
        if isinstance(resolution, dict) and resolution.get("resolved_at"):
            if state == APState.NEEDS_INFO.value or resolution.get("outcome") == "needs_followup":
                return "needs_non_invoice_followup"
            return "none"
        if state in {APState.CLOSED.value, APState.REJECTED.value}:
            return "none"
        if state in {APState.NEEDS_INFO.value}:
            return "needs_non_invoice_followup"
        return "resolve_non_invoice"
    if payload.get("requires_field_review"):
        return "review_fields"
    if state in {APState.NEEDS_INFO.value}:
        followup_next = str(payload.get("followup_next_action") or "").strip().lower()
        return followup_next or "request_info"
    if state in {APState.FAILED_POST.value}:
        return "retry_post"
    if state in {APState.READY_TO_POST.value, APState.APPROVED.value}:
        return "post_to_erp"
    if state in {APState.NEEDS_APPROVAL.value, "pending_approval"}:
        if payload.get("budget_requires_decision"):
            return "budget_decision"
        if payload.get("exception_code"):
            return "review_exception"
        return "approve_or_reject"
    if state in {APState.RECEIVED.value, APState.VALIDATED.value}:
        if payload.get("exception_code"):
            return "review_exception"
        return "route_for_approval"
    if state in {APState.REJECTED.value}:
        return "none" if payload.get("superseded_by_ap_item_id") else "resubmit"
    if state in {APState.POSTED_TO_ERP.value, APState.CLOSED.value}:
        return "none"
    return "review"


def _normalized_state_value(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value


def _superseded_invoice_key(source: Dict[str, Any], request: ResubmitRejectedItemRequest) -> str:
    base_key = str(source.get("invoice_key") or "").strip()
    if base_key:
        return base_key
    return "|".join(
        [
            str(request.vendor_name or source.get("vendor_name") or "").strip().lower(),
            str(request.invoice_number or source.get("invoice_number") or "").strip(),
            str(request.amount if request.amount is not None else source.get("amount") or "").strip(),
            str(request.currency or source.get("currency") or "USD").strip().upper(),
        ]
    )


def _resubmission_invoice_key(source: Dict[str, Any], request: ResubmitRejectedItemRequest) -> str:
    base_key = _superseded_invoice_key(source, request)
    source_hint = (
        str(request.message_id or "").strip()
        or str(request.thread_id or "").strip()
        or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    )
    return f"{base_key}|resub:{source_hint}"


def _copy_item_sources_for_resubmission(
    db: ClearledgrDB,
    *,
    source_ap_item_id: str,
    target_ap_item_id: str,
    actor_id: str,
) -> int:
    copied = 0
    for source_link in db.list_ap_item_sources(source_ap_item_id):
        metadata = _parse_json(source_link.get("metadata"))
        metadata.setdefault("resubmitted_from_ap_item_id", source_ap_item_id)
        metadata.setdefault("copied_by", actor_id)
        linked = db.link_ap_item_source(
            {
                "ap_item_id": target_ap_item_id,
                "source_type": source_link.get("source_type"),
                "source_ref": source_link.get("source_ref"),
                "subject": source_link.get("subject"),
                "sender": source_link.get("sender"),
                "detected_at": source_link.get("detected_at"),
                "metadata": metadata,
            }
        )
        if linked:
            copied += 1
    return copied


def _normalize_budget_checks(raw: Any) -> List[Dict[str, Any]]:
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


def _budget_status_rank(status: str) -> int:
    normalized = str(status or "").strip().lower()
    if normalized == "exceeded":
        return 4
    if normalized == "critical":
        return 3
    if normalized == "warning":
        return 2
    if normalized == "healthy":
        return 1
    return 0


def _normalize_exception_code(value: Any) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw in {"po_required_missing", "po_missing_reference"}:
        return "po_missing_reference"
    if raw.startswith("po_match_") or raw in {"po_amount_mismatch", "po_quantity_mismatch"}:
        return "po_amount_mismatch"
    if raw in {"budget_exceeded", "budget_critical", "budget_overrun"}:
        return "budget_overrun"
    if raw.startswith("policy_") or raw == "policy_validation_failed":
        return "policy_validation_failed"
    if raw == "missing_budget_context":
        return raw
    return raw


def _normalize_exception_severity(value: Any) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw in {"critical", "error"}:
        return "critical"
    if raw in {"high", "major"}:
        return "high"
    if raw in {"medium", "warning"}:
        return "medium"
    if raw in {"low", "info"}:
        return "low"
    return None


def _default_severity_for_exception(code: Optional[str]) -> Optional[str]:
    value = str(code or "").strip().lower()
    if not value:
        return None
    if value in {"budget_overrun"}:
        return "critical"
    if value in {"po_missing_reference", "po_amount_mismatch", "policy_validation_failed", "field_conflict"}:
        return "high"
    if value in {"missing_budget_context", "field_review_required"}:
        return "medium"
    return "medium"


def _derive_exception_from_metadata(
    metadata: Dict[str, Any],
    budget_summary: Dict[str, Any],
) -> Dict[str, Optional[str]]:
    code = _normalize_exception_code(metadata.get("exception_code"))
    severity = _normalize_exception_severity(metadata.get("exception_severity"))

    gate = _parse_json(metadata.get("validation_gate"))
    gate_reasons = gate.get("reasons") if isinstance(gate.get("reasons"), list) else []
    if not code:
        reason_codes = gate.get("reason_codes") if isinstance(gate.get("reason_codes"), list) else []
        if reason_codes:
            code = _normalize_exception_code(reason_codes[0])

    if not severity and code and gate_reasons:
        for reason in gate_reasons:
            if not isinstance(reason, dict):
                continue
            reason_code = _normalize_exception_code(reason.get("code"))
            if reason_code == code:
                severity = _normalize_exception_severity(reason.get("severity"))
                if severity:
                    break

    budget_status = str(budget_summary.get("status") or "").strip().lower()
    if not code and budget_summary.get("requires_decision"):
        code = "budget_overrun"
    if not severity and budget_summary.get("requires_decision"):
        severity = "critical" if budget_status == "exceeded" else "high"

    source_conflicts = metadata.get("source_conflicts") if isinstance(metadata.get("source_conflicts"), list) else []
    blocking_conflicts = [
        conflict for conflict in source_conflicts
        if isinstance(conflict, dict) and bool(conflict.get("blocking"))
    ]
    if not code and metadata.get("requires_field_review"):
        code = "field_conflict" if blocking_conflicts else "field_review_required"
    if not severity and metadata.get("requires_field_review"):
        severity = "high" if blocking_conflicts else "medium"

    if not severity:
        severity = _default_severity_for_exception(code)
    return {"code": code, "severity": severity}


_FIELD_REVIEW_LABELS = {
    "amount": "Amount",
    "currency": "Currency",
    "invoice_number": "Invoice number",
    "vendor": "Vendor",
    "invoice_date": "Invoice date",
    "due_date": "Due date",
    "document_type": "Document type",
}

_FIELD_REVIEW_SOURCE_LABELS = {
    "email": "Email",
    "attachment": "Attachment",
    "llm": "Model",
}

_FIELD_REVIEW_REASON_LABELS = {
    "source_value_mismatch": "Email and attachment disagree.",
    "attachment_llm_mismatch": "Attachment and model output disagree.",
}


def _field_review_label(field: Any) -> str:
    token = str(field or "").strip().lower()
    if not token:
        return "Field"
    return _FIELD_REVIEW_LABELS.get(token) or token.replace("_", " ").title()


def _field_review_source_label(source: Any) -> str:
    token = str(source or "").strip().lower()
    if not token:
        return "Source"
    return _FIELD_REVIEW_SOURCE_LABELS.get(token) or token.replace("_", " ").title()


def _format_field_review_value(field: str, value: Any, payload: Dict[str, Any]) -> str:
    if value in (None, ""):
        return "Not found"
    normalized_field = str(field or "").strip().lower()
    if normalized_field == "amount":
        try:
            amount_value = float(value)
            currency = str(payload.get("currency") or "USD").strip().upper() or "USD"
            return f"{currency} {amount_value:,.2f}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _join_human_list(values: List[str]) -> str:
    cleaned = [str(value).strip() for value in values if str(value or "").strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def _build_field_review_surface(payload: Dict[str, Any]) -> Dict[str, Any]:
    field_provenance = payload.get("field_provenance") if isinstance(payload.get("field_provenance"), dict) else {}
    field_evidence = payload.get("field_evidence") if isinstance(payload.get("field_evidence"), dict) else {}
    source_conflicts = payload.get("source_conflicts") if isinstance(payload.get("source_conflicts"), list) else []
    confidence_blockers = payload.get("confidence_blockers") if isinstance(payload.get("confidence_blockers"), list) else []

    blockers: List[Dict[str, Any]] = []
    blocked_fields: List[str] = []
    blocked_field_labels: List[str] = []
    seen_fields: set[str] = set()

    for conflict in source_conflicts:
        if not isinstance(conflict, dict) or not bool(conflict.get("blocking")):
            continue
        field = str(conflict.get("field") or "").strip().lower()
        if not field:
            continue

        provenance_entry = field_provenance.get(field) if isinstance(field_provenance.get(field), dict) else {}
        evidence_entry = field_evidence.get(field) if isinstance(field_evidence.get(field), dict) else {}
        values = conflict.get("values") if isinstance(conflict.get("values"), dict) else {}

        winning_source = (
            str(provenance_entry.get("source") or "").strip().lower()
            or str(conflict.get("preferred_source") or "").strip().lower()
            or str(evidence_entry.get("source") or "").strip().lower()
            or "attachment"
        )
        winning_value = provenance_entry.get("value")
        if winning_value in (None, ""):
            winning_value = evidence_entry.get("selected_value")
        if winning_value in (None, "") and winning_source:
            winning_value = values.get(winning_source)

        email_value = values.get("email")
        if email_value in (None, ""):
            email_value = evidence_entry.get("email_value")
        attachment_value = values.get("attachment")
        if attachment_value in (None, ""):
            attachment_value = evidence_entry.get("attachment_value")

        field_label = _field_review_label(field)
        winner_label = _field_review_source_label(winning_source)
        attachment_name = str(evidence_entry.get("attachment_name") or "").strip() or None
        reason = str(conflict.get("reason") or "").strip().lower() or "source_value_mismatch"
        winner_reason = f"{winner_label} currently wins because Clearledgr selected that value as canonical."
        if winning_source == "attachment" and attachment_name:
            winner_reason = (
                f"{winner_label} currently wins because Clearledgr selected the value from {attachment_name} as canonical."
            )

        blockers.append(
            {
                "kind": "source_conflict",
                "field": field,
                "field_label": field_label,
                "blocking": True,
                "reason": reason,
                "reason_label": _FIELD_REVIEW_REASON_LABELS.get(reason) or "Sources disagree and require review.",
                "email_value": email_value,
                "email_value_display": _format_field_review_value(field, email_value, payload),
                "attachment_value": attachment_value,
                "attachment_value_display": _format_field_review_value(field, attachment_value, payload),
                "winning_source": winning_source,
                "winning_source_label": winner_label,
                "winning_value": winning_value,
                "winning_value_display": _format_field_review_value(field, winning_value, payload),
                "attachment_name": attachment_name,
                "paused_reason": (
                    f"Workflow paused until {field_label.lower()} is confirmed because the email and attachment disagree."
                ),
                "winner_reason": winner_reason,
            }
        )
        if field not in seen_fields:
            seen_fields.add(field)
            blocked_fields.append(field)
            blocked_field_labels.append(field_label.lower())

    for blocker in confidence_blockers:
        if isinstance(blocker, str):
            field = str(blocker or "").strip().lower()
            reason = "critical_field_review_required"
        elif isinstance(blocker, dict):
            field = str(blocker.get("field") or blocker.get("code") or "").strip().lower()
            reason = str(blocker.get("reason") or blocker.get("code") or "critical_field_review_required").strip().lower()
        else:
            continue
        if not field or field in seen_fields:
            continue
        field_label = _field_review_label(field)
        blockers.append(
            {
                "kind": "confidence",
                "field": field,
                "field_label": field_label,
                "blocking": True,
                "reason": reason,
                "reason_label": "Critical extracted field needs review.",
                "paused_reason": f"Workflow paused until {field_label.lower()} is reviewed.",
            }
        )
        seen_fields.add(field)
        blocked_fields.append(field)
        blocked_field_labels.append(field_label.lower())

    pause_reason = ""
    if blocked_field_labels:
        pause_reason = (
            f"Workflow paused until {_join_human_list(blocked_field_labels)} "
            f"{'is' if len(blocked_field_labels) == 1 else 'are'} reviewed."
        )
        if any(str(entry.get("kind") or "") == "source_conflict" for entry in blockers):
            pause_reason = (
                f"Workflow paused until {_join_human_list(blocked_field_labels)} "
                f"{'is' if len(blocked_field_labels) == 1 else 'are'} confirmed because the email and attachment disagree."
            )
    elif bool(payload.get("requires_field_review")):
        pause_reason = "Workflow paused until extracted fields are reviewed."

    return {
        "field_review_blockers": blockers,
        "blocked_fields": blocked_fields,
        "workflow_paused_reason": pause_reason or None,
    }


def _summarize_budget_context(metadata: Dict[str, Any], approvals: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    checks = _normalize_budget_checks(metadata.get("budget_impact"))
    if not checks:
        checks = _normalize_budget_checks(metadata.get("budget"))
    if not checks:
        checks = _normalize_budget_checks(metadata.get("budget_check_result"))

    if not checks and approvals:
        for approval in approvals:
            payload = _parse_json(approval.get("decision_payload"))
            checks = _normalize_budget_checks(payload.get("budget_impact"))
            if checks:
                break
            checks = _normalize_budget_checks(payload.get("budget"))
            if checks:
                break

    status = str(metadata.get("budget_status") or metadata.get("status") or "unknown").strip().lower()
    highest_rank = _budget_status_rank(status)
    exceeded_count = 0
    critical_count = 0
    warning_count = 0
    rows: List[Dict[str, Any]] = []

    for check in checks:
        row_status = str(check.get("after_approval_status") or check.get("status") or "unknown").strip().lower()
        rank = _budget_status_rank(row_status)
        if rank > highest_rank:
            highest_rank = rank
            status = row_status
        if row_status == "exceeded":
            exceeded_count += 1
        elif row_status == "critical":
            critical_count += 1
        elif row_status == "warning":
            warning_count += 1

        budget_amount = _safe_float(check.get("budget_amount"))
        after_approval = _safe_float(check.get("after_approval"))
        remaining = check.get("remaining")
        if remaining is None:
            remaining = budget_amount - after_approval
        rows.append(
            {
                "name": check.get("budget_name") or "Budget",
                "status": row_status,
                "percent_after_approval": _safe_float(check.get("after_approval_percent") or check.get("percent_used")),
                "invoice_amount": _safe_float(check.get("invoice_amount")),
                "remaining": _safe_float(remaining),
                "warning_message": check.get("warning_message"),
            }
        )

    requires_decision = status in {"critical", "exceeded"}
    return {
        "status": status or "unknown",
        "requires_decision": requires_decision,
        "critical_count": critical_count,
        "exceeded_count": exceeded_count,
        "warning_count": warning_count,
        "checks": rows,
    }


def _build_primary_source(item: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    for source in sources:
        source_type = str(source.get("source_type") or "").strip().lower()
        source_ref = str(source.get("source_ref") or "").strip()
        if source_type == "gmail_thread" and source_ref:
            return {"thread_id": source_ref, "message_id": item.get("message_id")}
        if source_type == "gmail_message" and source_ref:
            return {"thread_id": item.get("thread_id"), "message_id": source_ref}
    return {"thread_id": item.get("thread_id"), "message_id": item.get("message_id")}


def build_worklist_item(db: ClearledgrDB, item: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(item or {})
    metadata = _parse_json(payload.get("metadata"))
    sources = db.list_ap_item_sources(payload.get("id"))

    # Preserve legacy behavior when source links do not exist yet.
    if not sources:
        if payload.get("thread_id"):
            sources.append(
                {
                    "source_type": "gmail_thread",
                    "source_ref": payload.get("thread_id"),
                    "subject": payload.get("subject"),
                    "sender": payload.get("sender"),
                    "detected_at": payload.get("created_at"),
                    "metadata": {},
                }
            )
        if payload.get("message_id"):
            sources.append(
                {
                    "source_type": "gmail_message",
                    "source_ref": payload.get("message_id"),
                    "subject": payload.get("subject"),
                    "sender": payload.get("sender"),
                    "detected_at": payload.get("created_at"),
                    "metadata": {},
                }
            )

    meta_source_count = metadata.get("source_count")
    try:
        parsed_meta_source_count = int(meta_source_count) if meta_source_count is not None else 0
    except (TypeError, ValueError):
        parsed_meta_source_count = 0
    payload["source_count"] = max(parsed_meta_source_count, len(sources))
    payload["primary_source"] = _build_primary_source(payload, sources)
    payload.update(_derive_attachment_summary(payload, metadata, sources))
    payload["supersedes_ap_item_id"] = payload.get("supersedes_ap_item_id") or metadata.get("supersedes_ap_item_id")
    payload["supersedes_invoice_key"] = payload.get("supersedes_invoice_key") or metadata.get("supersedes_invoice_key")
    payload["superseded_by_ap_item_id"] = payload.get("superseded_by_ap_item_id") or metadata.get("superseded_by_ap_item_id")
    payload["resubmission_reason"] = payload.get("resubmission_reason") or metadata.get("resubmission_reason")
    payload["is_resubmission"] = bool(payload.get("supersedes_ap_item_id"))
    payload["has_resubmission"] = bool(payload.get("superseded_by_ap_item_id"))
    payload["merged_into"] = metadata.get("merged_into")
    payload["is_merged_source"] = bool(metadata.get("merged_into"))
    payload["merge_reason"] = metadata.get("merge_reason")
    payload["has_context_conflict"] = bool(
        metadata.get("has_context_conflict") or metadata.get("context_conflict")
    )
    budget_summary = _summarize_budget_context(metadata)
    existing_exception = {
        "code": _normalize_exception_code(payload.get("exception_code")),
        "severity": _normalize_exception_severity(payload.get("exception_severity")),
    }
    derived_exception = _derive_exception_from_metadata(metadata, budget_summary)
    payload["exception_code"] = existing_exception["code"] or derived_exception["code"]
    payload["exception_severity"] = (
        existing_exception["severity"]
        or derived_exception["severity"]
        or _default_severity_for_exception(payload.get("exception_code"))
    )
    payload["budget_status"] = (
        metadata.get("budget_status")
        or payload.get("budget_status")
        or budget_summary.get("status")
    )
    payload["budget_requires_decision"] = bool(budget_summary.get("requires_decision"))
    confidence_gate = _derive_confidence_gate(payload, metadata)
    payload["confidence_gate"] = confidence_gate
    # Expose per-field confidence map for the Gmail card (field-level UX)
    raw_fc = payload.get("field_confidences") or metadata.get("field_confidences")
    if isinstance(raw_fc, str):
        try:
            raw_fc = json.loads(raw_fc)
        except (json.JSONDecodeError, TypeError):
            raw_fc = {}
    payload["field_confidences"] = raw_fc or confidence_gate.get("field_confidences") or {}
    payload["requires_field_review"] = bool(
        metadata.get("requires_field_review") or confidence_gate.get("requires_field_review")
    )
    payload["requires_extraction_review"] = bool(metadata.get("requires_extraction_review"))
    confidence_blockers = metadata.get("confidence_blockers")
    if isinstance(confidence_blockers, list):
        payload["confidence_blockers"] = confidence_blockers
    else:
        payload["confidence_blockers"] = confidence_gate.get("confidence_blockers") or []
    payload["field_provenance"] = metadata.get("field_provenance") if isinstance(metadata.get("field_provenance"), dict) else {}
    payload["field_evidence"] = metadata.get("field_evidence") if isinstance(metadata.get("field_evidence"), dict) else {}
    payload["source_conflicts"] = metadata.get("source_conflicts") if isinstance(metadata.get("source_conflicts"), list) else []
    payload["risk_signals"] = metadata.get("risk_signals") or {}
    payload["source_ranking"] = metadata.get("source_ranking") or {}
    payload["navigator"] = metadata.get("navigator") or {}
    # Document type: use stored value from ingestion, or infer from subject.
    # Non-invoice finance docs should stay out of AP payable routing.
    _doc_type = metadata.get("email_type") or metadata.get("document_type")
    if not _doc_type:
        _subject_lc = str(payload.get("subject") or "").lower()
        _receipt_kw = {"receipt", "order confirmation", "order receipt", "subscription receipt", "payment receipt"}
        _payment_kw = {"payment confirmation", "payment received", "payment processed", "payment successful", "payment completed"}
        _refund_kw = {"refund"}
        _credit_note_kw = {"credit note", "credit memo"}
        _statement_kw = {"bank statement", "card statement", "account statement", "billing statement"}
        _payment_request_kw = {"payment request", "requesting payment", "please pay"}
        if any(kw in _subject_lc for kw in _credit_note_kw):
            _doc_type = "credit_note"
        elif any(kw in _subject_lc for kw in _refund_kw):
            _doc_type = "refund"
        elif any(kw in _subject_lc for kw in _statement_kw):
            _doc_type = "statement"
        elif any(kw in _subject_lc for kw in _payment_request_kw):
            _doc_type = "payment_request"
        elif any(kw in _subject_lc for kw in _payment_kw):
            _doc_type = "payment"
        else:
            _doc_type = "receipt" if any(kw in _subject_lc for kw in _receipt_kw) else "invoice"
    payload["document_type"] = _normalize_document_type_token(_doc_type)
    payload["conflict_actions"] = metadata.get("conflict_actions") if isinstance(metadata.get("conflict_actions"), list) else []
    payload.update(_build_field_review_surface(payload))
    if metadata.get("priority_score") is not None:
        payload["priority_score"] = metadata.get("priority_score")
    elif hasattr(db, "_worklist_priority_score"):
        try:
            payload["priority_score"] = db._worklist_priority_score(payload)  # type: ignore[attr-defined]
        except Exception:
            pass

    # Claude AP reasoning — surface proactively so the sidebar card can display it.
    payload["ap_decision_reasoning"] = (
        metadata.get("ap_decision_reasoning") or payload.get("ap_decision_reasoning")
    )
    payload["ap_decision_recommendation"] = (
        metadata.get("ap_decision_recommendation")
        or (metadata.get("vendor_intelligence") or {}).get("ap_decision")
        or payload.get("ap_decision_recommendation")
    )
    payload["ap_decision_risk_flags"] = (
        metadata.get("ap_decision_risk_flags") or []
    )

    # needs_info follow-up — surface question + Gmail draft link for sidebar banner.
    needs_info_question = metadata.get("needs_info_question")
    payload["needs_info_question"] = needs_info_question if needs_info_question else None
    needs_info_draft_id = metadata.get("needs_info_draft_id")
    payload["needs_info_draft_id"] = needs_info_draft_id if needs_info_draft_id else None
    followup_last_sent_at = metadata.get("followup_last_sent_at")
    payload["followup_last_sent_at"] = str(followup_last_sent_at).strip() if followup_last_sent_at else None
    payload["followup_attempt_count"] = max(0, _safe_int(metadata.get("followup_attempt_count"), 0))
    followup_sla_due_at = metadata.get("followup_sla_due_at")
    payload["followup_sla_due_at"] = str(followup_sla_due_at).strip() if followup_sla_due_at else None
    payload["followup_next_action"] = _derive_followup_next_action(
        state=str(payload.get("state") or "").strip().lower(),
        metadata=metadata,
    )
    payload["queue_entered_at"] = (
        payload.get("queue_entered_at")
        or payload.get("received_at")
        or payload.get("created_at")
        or payload.get("updated_at")
    )
    state_token = str(payload.get("state") or "").strip().lower()
    non_invoice_resolution = metadata.get("non_invoice_resolution")
    payload["non_invoice_resolution"] = non_invoice_resolution if isinstance(non_invoice_resolution, dict) else {}
    payload["non_invoice_accounting_treatment"] = payload["non_invoice_resolution"].get("accounting_treatment")
    payload["non_invoice_downstream_queue"] = payload["non_invoice_resolution"].get("downstream_queue")
    payload["non_invoice_review_required"] = bool(
        _normalize_document_type_token(payload.get("document_type")) != "invoice"
        and state_token not in {APState.CLOSED.value, APState.REJECTED.value}
        and not payload["non_invoice_resolution"].get("resolved_at")
    )
    payload["next_action"] = _derive_next_action(payload)
    payload["approval_requested_at"] = (
        payload.get("approval_requested_at")
        or metadata.get("approval_requested_at")
        or (payload.get("updated_at") if state_token in {"needs_approval", "pending_approval"} else None)
    )
    erp_status = str(payload.get("erp_status") or "").strip().lower()
    if not erp_status:
        if state_token in {"posted", "posted_to_erp", "closed"} or payload.get("erp_reference") or payload.get("erp_bill_id"):
            erp_status = "posted"
        elif state_token == "failed_post":
            erp_status = "failed"
        elif state_token in {"approved", "ready_to_post"}:
            erp_status = "ready"
        elif metadata.get("erp_connector_available") or metadata.get("erp"):
            erp_status = "connected"
        else:
            erp_status = "not_connected"
    payload["erp_status"] = erp_status
    payload["erp_connector_available"] = bool(
        payload.get("erp_connector_available")
        or metadata.get("erp_connector_available")
        or metadata.get("erp")
    )

    # Correction learning: surface GL suggestion + previously-corrected fields.
    # suggest() is in-memory after rule load — fast per call.
    try:
        from clearledgr.services.correction_learning import CorrectionLearningService
        _vendor = payload.get("vendor_name") or payload.get("vendor")
        _org = payload.get("organization_id") or "default"
        if _vendor:
            _cls = CorrectionLearningService(_org)
            payload["gl_suggestion"] = _cls.suggest("gl_code", {"vendor": _vendor})
            # Surface vendor alias suggestions (catches normalisation corrections)
            payload["vendor_suggestion"] = _cls.suggest("vendor", {"raw_vendor": _vendor})
        else:
            payload["gl_suggestion"] = None
            payload["vendor_suggestion"] = None
    except Exception:
        payload["gl_suggestion"] = None
        payload["vendor_suggestion"] = None

    return payload


OPEN_AP_STATES = {
    "received",
    "validated",
    "needs_info",
    "needs_approval",
    "pending_approval",
    "approved",
    "ready_to_post",
    "failed_post",
}


def _safe_sort_timestamp(value: Any) -> float:
    parsed = _parse_iso(value)
    return parsed.timestamp() if parsed else 0.0


def _is_open_ap_state(state: Any) -> bool:
    return str(state or "").strip().lower() in OPEN_AP_STATES


def _summarize_related_item(item: Dict[str, Any]) -> Dict[str, Any]:
    state = str(item.get("state") or "").strip().lower()
    return {
        "id": item.get("id"),
        "vendor_name": item.get("vendor_name"),
        "invoice_number": item.get("invoice_number"),
        "amount": _safe_float(item.get("amount")),
        "currency": item.get("currency") or "USD",
        "state": state,
        "due_date": item.get("due_date"),
        "updated_at": item.get("updated_at") or item.get("created_at"),
        "thread_id": item.get("thread_id"),
        "message_id": item.get("message_id"),
        "erp_reference": item.get("erp_reference"),
        "exception_code": item.get("exception_code"),
        "is_open": _is_open_ap_state(state),
    }


def _group_sources_by_type(sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows: Dict[str, Dict[str, Any]] = {}
    for source in sources:
        source_type = str(source.get("source_type") or "unknown").strip().lower() or "unknown"
        bucket = rows.setdefault(
            source_type,
            {
                "source_type": source_type,
                "count": 0,
                "items": [],
            },
        )
        bucket["count"] += 1
        if len(bucket["items"]) < 5:
            bucket["items"].append(
                {
                    "source_ref": source.get("source_ref"),
                    "subject": source.get("subject"),
                    "sender": source.get("sender"),
                    "detected_at": source.get("detected_at"),
                    "metadata": _parse_json(source.get("metadata")),
                }
            )
    return {
        "groups": sorted(rows.values(), key=lambda row: (-int(row.get("count") or 0), str(row.get("source_type") or ""))),
        "count": len(sources),
    }


def _build_related_records_payload(
    current_item: Dict[str, Any],
    all_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    current_id = str(current_item.get("id") or "").strip()
    current_metadata = _parse_json(current_item.get("metadata"))
    vendor_key = str(current_item.get("vendor_name") or "").strip().lower()
    invoice_number = str(current_item.get("invoice_number") or "").strip().lower()

    vendor_recent = [
        _summarize_related_item(item)
        for item in sorted(
            (
                candidate
                for candidate in all_items
                if str(candidate.get("id") or "").strip() != current_id
                and str(candidate.get("vendor_name") or "").strip().lower() == vendor_key
            ),
            key=lambda row: _safe_sort_timestamp(row.get("updated_at") or row.get("created_at")),
            reverse=True,
        )[:6]
    ]
    duplicate_invoice_items = [
        _summarize_related_item(item)
        for item in sorted(
            (
                candidate
                for candidate in all_items
                if invoice_number
                and str(candidate.get("id") or "").strip() != current_id
                and str(candidate.get("invoice_number") or "").strip().lower() == invoice_number
            ),
            key=lambda row: _safe_sort_timestamp(row.get("updated_at") or row.get("created_at")),
            reverse=True,
        )[:4]
    ]
    previous_item = next(
        (
            _summarize_related_item(candidate)
            for candidate in all_items
            if str(candidate.get("id") or "").strip()
            == str(current_item.get("supersedes_ap_item_id") or current_metadata.get("supersedes_ap_item_id") or "").strip()
        ),
        None,
    )
    next_item = next(
        (
            _summarize_related_item(candidate)
            for candidate in all_items
            if str(candidate.get("id") or "").strip()
            == str(current_item.get("superseded_by_ap_item_id") or current_metadata.get("superseded_by_ap_item_id") or "").strip()
        ),
        None,
    )
    return {
        "vendor_recent_items": vendor_recent,
        "same_invoice_number_items": duplicate_invoice_items,
        "supersession": {
            "previous_item": previous_item,
            "next_item": next_item,
        },
    }


def _build_vendor_summary_rows(
    db: ClearledgrDB,
    organization_id: str,
    *,
    search: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    items = [build_worklist_item(db, row) for row in db.list_ap_items(organization_id, limit=5000)]
    vendor_rows: Dict[str, Dict[str, Any]] = {}

    for item in items:
        vendor_name = str(item.get("vendor_name") or item.get("vendor") or "Unknown").strip() or "Unknown"
        key = vendor_name.lower()
        row = vendor_rows.setdefault(
            key,
            {
                "vendor_name": vendor_name,
                "invoice_count": 0,
                "open_count": 0,
                "posted_count": 0,
                "failed_count": 0,
                "approval_count": 0,
                "needs_info_count": 0,
                "total_amount": 0.0,
                "last_activity_at": "",
                "sender_emails": set(),
                "top_states": Counter(),
            },
        )
        row["invoice_count"] += 1
        row["total_amount"] += _safe_float(item.get("amount"))
        state = str(item.get("state") or "").strip().lower()
        row["top_states"][state] += 1
        if _is_open_ap_state(state):
            row["open_count"] += 1
        if state in {"posted_to_erp", "closed"}:
            row["posted_count"] += 1
        if state == "failed_post":
            row["failed_count"] += 1
        if state in {"needs_approval", "pending_approval"}:
            row["approval_count"] += 1
        if state == "needs_info":
            row["needs_info_count"] += 1
        updated_at = str(item.get("updated_at") or item.get("created_at") or "")
        if updated_at > str(row.get("last_activity_at") or ""):
            row["last_activity_at"] = updated_at
        sender = str(item.get("sender") or "").strip()
        if sender:
            row["sender_emails"].add(sender)

    search_lc = str(search or "").strip().lower()
    rows: List[Dict[str, Any]] = []
    for row in vendor_rows.values():
        if search_lc and search_lc not in str(row.get("vendor_name") or "").lower():
            continue
        vendor_name = str(row.get("vendor_name") or "")
        profile = db.get_vendor_profile(organization_id, vendor_name) if vendor_name else None
        rows.append(
            {
                "vendor_name": vendor_name,
                "invoice_count": int(row.get("invoice_count") or 0),
                "open_count": int(row.get("open_count") or 0),
                "posted_count": int(row.get("posted_count") or 0),
                "failed_count": int(row.get("failed_count") or 0),
                "approval_count": int(row.get("approval_count") or 0),
                "needs_info_count": int(row.get("needs_info_count") or 0),
                "total_amount": round(_safe_float(row.get("total_amount")), 2),
                "last_activity_at": row.get("last_activity_at") or None,
                "primary_email": sorted(row.get("sender_emails") or [""])[0] if row.get("sender_emails") else None,
                "sender_emails": sorted(row.get("sender_emails") or [])[:5],
                "top_states": [
                    {"state": state, "count": count}
                    for state, count in Counter(row.get("top_states") or {}).most_common(4)
                ],
                "profile": {
                    "requires_po": bool((profile or {}).get("requires_po")),
                    "payment_terms": (profile or {}).get("payment_terms"),
                    "always_approved": bool((profile or {}).get("always_approved")),
                    "approval_override_rate": _safe_float((profile or {}).get("approval_override_rate")),
                    "anomaly_flags": list((profile or {}).get("anomaly_flags") or [])[:4],
                },
            }
        )

    rows.sort(
        key=lambda row: (
            int(row.get("open_count") or 0),
            _safe_float(row.get("total_amount")),
            _safe_sort_timestamp(row.get("last_activity_at")),
        ),
        reverse=True,
    )
    return rows[: max(1, min(limit, 200))]


def _build_vendor_detail_payload(
    db: ClearledgrDB,
    organization_id: str,
    vendor_name: str,
    *,
    days: int = 180,
    invoice_limit: int = 20,
) -> Dict[str, Any]:
    summary_rows = _build_vendor_summary_rows(db, organization_id, search=vendor_name, limit=200)
    summary = next(
        (
            row
            for row in summary_rows
            if str(row.get("vendor_name") or "").strip().lower() == str(vendor_name or "").strip().lower()
        ),
        None,
    )
    if not summary:
        raise HTTPException(status_code=404, detail="vendor_not_found")

    canonical_vendor_name = str(summary.get("vendor_name") or vendor_name).strip()
    profile = db.get_vendor_profile(organization_id, canonical_vendor_name) or {}
    history = db.get_vendor_invoice_history(organization_id, canonical_vendor_name, limit=max(6, min(invoice_limit, 30)))
    items = [
        build_worklist_item(db, row)
        for row in db.get_ap_items_by_vendor(
            organization_id,
            canonical_vendor_name,
            days=max(30, min(days, 365)),
            limit=max(6, min(invoice_limit, 30)),
        )
    ]
    exception_counts = Counter(
        str(item.get("exception_code") or "").strip().lower()
        for item in items
        if str(item.get("exception_code") or "").strip()
    )
    linked_item_rows = [_summarize_related_item(item) for item in items[:12]]

    return {
        "vendor_name": canonical_vendor_name,
        "summary": summary,
        "profile": {
            **profile,
            "vendor_aliases": list(profile.get("vendor_aliases") or [])[:8],
            "sender_domains": list(profile.get("sender_domains") or [])[:8],
            "anomaly_flags": list(profile.get("anomaly_flags") or [])[:8],
            "metadata": _parse_json(profile.get("metadata")),
        },
        "recent_items": linked_item_rows,
        "history": history,
        "top_exception_codes": [
            {"exception_code": code, "count": count}
            for code, count in exception_counts.most_common(6)
        ],
    }


def _classify_upcoming_status(due_at: Optional[datetime], now: datetime) -> str:
    if due_at is None:
        return "queued"
    if due_at <= now:
        return "overdue"
    if due_at.date() == now.date():
        return "today"
    if due_at <= now + timedelta(days=7):
        return "this_week"
    return "later"


def _build_upcoming_task(item: Dict[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
    state = str(item.get("state") or "").strip().lower()
    kind = ""
    title = ""
    detail = ""
    due_at: Optional[datetime] = None
    recommended_slice = "all_open"

    if state in {"needs_approval", "pending_approval"}:
        kind = "approval_follow_up"
        title = "Follow up on approval"
        recommended_slice = "waiting_on_approval"
        requested_at = _parse_iso(item.get("approval_requested_at")) or _parse_iso(item.get("updated_at")) or _parse_iso(item.get("created_at"))
        due_at = requested_at + timedelta(hours=24) if requested_at else None
        detail = "Approval is still outstanding and should be chased if it has gone quiet."
    elif state == "needs_info":
        kind = "vendor_follow_up"
        title = "Vendor follow-up"
        recommended_slice = "needs_info"
        due_at = _parse_iso(item.get("followup_sla_due_at")) or _parse_iso(item.get("updated_at")) or _parse_iso(item.get("created_at"))
        followup_next = str(item.get("followup_next_action") or "").strip().lower()
        if followup_next == "prepare_vendor_followup_draft":
            detail = "Prepare the first vendor reply so the invoice can continue."
        elif followup_next == "manual_vendor_escalation":
            detail = "Vendor follow-up has reached the attempt limit and needs manual escalation."
        else:
            detail = "Check whether the vendor needs another nudge or if the reply already arrived."
    elif state == "failed_post":
        kind = "erp_retry"
        title = "Retry ERP posting"
        recommended_slice = "failed_post"
        due_at = (_parse_iso(item.get("updated_at")) or _parse_iso(item.get("created_at")) or now) + timedelta(hours=4)
        detail = "ERP posting failed and should be retried or investigated."
    elif state in {"approved", "ready_to_post"}:
        kind = "post_invoice"
        title = "Post approved invoice"
        recommended_slice = "ready_to_post"
        due_at = _parse_iso(item.get("due_date")) or (_parse_iso(item.get("updated_at")) or now) + timedelta(hours=8)
        detail = "The invoice is approved and ready to move into ERP."
    elif state in {"received", "validated"} and (
        item.get("exception_code") or item.get("requires_field_review") or item.get("budget_requires_decision")
    ):
        kind = "review_blocker"
        title = "Resolve blocker"
        recommended_slice = "blocked_exception"
        due_at = _parse_iso(item.get("updated_at")) or _parse_iso(item.get("created_at"))
        detail = "Review the blocking signal before the invoice can move forward."
    else:
        return None

    status = _classify_upcoming_status(due_at, now)
    overdue_invoice = _parse_iso(item.get("due_date"))
    if overdue_invoice and overdue_invoice <= now and status not in {"overdue"}:
        detail = f"{detail} The invoice due date has already passed."

    return {
        "id": f"{kind}:{item.get('id')}",
        "kind": kind,
        "status": status,
        "title": title,
        "detail": detail,
        "due_at": due_at.isoformat() if due_at else None,
        "recommended_slice": recommended_slice,
        "ap_item_id": item.get("id"),
        "vendor_name": item.get("vendor_name") or item.get("vendor"),
        "invoice_number": item.get("invoice_number"),
        "amount": _safe_float(item.get("amount")),
        "currency": item.get("currency") or "USD",
        "state": state,
        "thread_id": item.get("thread_id"),
        "message_id": item.get("message_id"),
        "erp_status": item.get("erp_status"),
        "followup_next_action": item.get("followup_next_action"),
        "followup_draft_id": item.get("needs_info_draft_id"),
        "sender": item.get("sender"),
    }


def _build_upcoming_tasks_payload(db: ClearledgrDB, organization_id: str, *, limit: int = 50) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    items = [build_worklist_item(db, row) for row in db.list_ap_items(organization_id, limit=5000)]
    tasks = [
        task
        for task in (_build_upcoming_task(item, now) for item in items)
        if task
    ]
    tasks.sort(
        key=lambda row: (
            {"overdue": 0, "today": 1, "this_week": 2, "later": 3, "queued": 4}.get(str(row.get("status") or ""), 5),
            _safe_sort_timestamp(row.get("due_at")),
            -_safe_float(row.get("amount")),
        )
    )
    limited = tasks[: max(1, min(limit, 200))]
    by_status = Counter(str(task.get("status") or "") for task in limited)
    by_kind = Counter(str(task.get("kind") or "") for task in limited)
    return {
        "generated_at": now.isoformat(),
        "summary": {
            "total": len(limited),
            "overdue": int(by_status.get("overdue", 0)),
            "today": int(by_status.get("today", 0)),
            "this_week": int(by_status.get("this_week", 0)),
            "by_kind": dict(by_kind),
        },
        "tasks": limited,
    }


def _require_item(db: ClearledgrDB, ap_item_id: str) -> Dict[str, Any]:
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    return item


def _preview_field_review_resolution(
    db: ClearledgrDB,
    item: Dict[str, Any],
    *,
    metadata: Dict[str, Any],
    field: str,
    resolved_value: Any,
    resolved_source: str,
    actor_id: str,
    blocker: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    field_token = _normalize_field_review_field(field)
    now = datetime.now(timezone.utc).isoformat()

    column_updates = _field_resolution_column_updates(field_token, resolved_value)

    provenance = _parse_json(metadata.get("field_provenance"))
    provenance_entry = provenance.get(field_token) if isinstance(provenance.get(field_token), dict) else {}
    provenance_entry = dict(provenance_entry or {})
    provenance_entry.update(
        {
            "source": resolved_source,
            "value": resolved_value,
            "resolved_at": now,
            "resolved_by": actor_id,
            "resolution_note": (str(note or "").strip() or None),
        }
    )
    provenance[field_token] = provenance_entry
    metadata["field_provenance"] = provenance

    evidence = _parse_json(metadata.get("field_evidence"))
    evidence_entry = evidence.get(field_token) if isinstance(evidence.get(field_token), dict) else {}
    evidence_entry = dict(evidence_entry or {})
    evidence_entry.update(
        {
            "source": resolved_source,
            "selected_value": resolved_value,
            "resolved_at": now,
            "resolved_by": actor_id,
        }
    )
    if resolved_source == "manual":
        evidence_entry["manual_value"] = resolved_value
    evidence[field_token] = evidence_entry
    metadata["field_evidence"] = evidence

    source_conflicts = _parse_json_list(metadata.get("source_conflicts"))
    updated_conflicts: List[Dict[str, Any]] = []
    for conflict in source_conflicts:
        if not isinstance(conflict, dict):
            continue
        if _normalize_field_review_field(conflict.get("field")) != field_token:
            updated_conflicts.append(conflict)
            continue
        resolved_conflict = dict(conflict)
        resolved_conflict.update(
            {
                "blocking": False,
                "resolved": True,
                "resolved_at": now,
                "resolved_by": actor_id,
                "selected_source": resolved_source,
                "selected_value": resolved_value,
            }
        )
        if note:
            resolved_conflict["resolution_note"] = str(note).strip()
        updated_conflicts.append(resolved_conflict)
    metadata["source_conflicts"] = updated_conflicts

    confidence_blockers = _parse_json_list(
        item.get("confidence_blockers") or metadata.get("confidence_blockers")
    )
    filtered_confidence_blockers = [
        blocker
        for blocker in confidence_blockers
        if _get_conflict_field(blocker) != field_token
    ]
    metadata["confidence_blockers"] = filtered_confidence_blockers

    field_confidences = _parse_json(item.get("field_confidences")) or _parse_json(metadata.get("field_confidences"))
    if isinstance(field_confidences, dict):
        field_confidences[field_token] = 1.0
        metadata["field_confidences"] = field_confidences

    resolutions = _parse_json(metadata.get("field_review_resolutions"))
    resolutions[field_token] = {
        "field": field_token,
        "selected_source": resolved_source,
        "selected_value": resolved_value,
        "resolved_at": now,
        "resolved_by": actor_id,
        "note": str(note or "").strip() or None,
        "email_value": blocker.get("email_value") if isinstance(blocker, dict) else None,
        "attachment_value": blocker.get("attachment_value") if isinstance(blocker, dict) else None,
        "previous_winning_source": blocker.get("winning_source") if isinstance(blocker, dict) else None,
        "previous_winning_value": blocker.get("winning_value") if isinstance(blocker, dict) else None,
    }
    metadata["field_review_resolutions"] = resolutions

    conflict_actions = _parse_json_list(metadata.get("conflict_actions"))
    conflict_actions.append(
        {
            "action": "field_review_resolved",
            "field": field_token,
            "selected_source": resolved_source,
            "selected_value": resolved_value,
            "resolved_at": now,
            "resolved_by": actor_id,
            "note": str(note or "").strip() or None,
        }
    )
    metadata["conflict_actions"] = conflict_actions[-25:]

    if field_token == "document_type":
        metadata["document_type"] = resolved_value
        metadata["email_type"] = resolved_value

    metadata["requires_field_review"] = False
    metadata["requires_extraction_review"] = False
    metadata.pop("confidence_gate", None)

    preview_item = dict(item or {})
    preview_item.update(column_updates)
    preview_item["metadata"] = metadata
    preview_item["requires_field_review"] = False
    preview_item["confidence_blockers"] = filtered_confidence_blockers
    preview_item["source_conflicts"] = updated_conflicts
    if isinstance(field_confidences, dict):
        preview_item["field_confidences"] = field_confidences

    preview_worklist = build_worklist_item(db, preview_item)
    unresolved = bool(preview_worklist.get("field_review_blockers")) or bool(preview_worklist.get("requires_field_review"))
    metadata["requires_field_review"] = unresolved
    metadata["requires_extraction_review"] = unresolved

    column_payload: Dict[str, Any] = dict(column_updates)
    column_payload.update(
        {
            "metadata": metadata,
            "requires_field_review": unresolved,
            "source_conflicts": updated_conflicts,
            "confidence_blockers": filtered_confidence_blockers,
        }
    )
    if isinstance(field_confidences, dict):
        column_payload["field_confidences"] = field_confidences

    existing_exception = str(item.get("exception_code") or "").strip().lower()
    if not unresolved and existing_exception in {"field_conflict", "field_review_required"}:
        column_payload["exception_code"] = None
        column_payload["exception_severity"] = None
    elif unresolved and existing_exception in {"field_conflict", "field_review_required", ""}:
        column_payload["exception_code"] = "field_conflict"
        column_payload["exception_severity"] = "high"

    return {
        "metadata": metadata,
        "column_payload": _filter_allowed_ap_item_updates(db, column_payload),
        "resolved_at": now,
        "preview_worklist": preview_worklist,
        "unresolved": unresolved,
    }


def _build_context_payload(db: ClearledgrDB, item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _parse_json(item.get("metadata"))
    sources = db.list_ap_item_sources(item["id"])
    approvals = db.list_approvals_by_item(item["id"], limit=20)
    audit_events = db.list_ap_audit_events(item["id"])
    now = datetime.now(timezone.utc)

    multi_system, discovered_sources = build_multi_system_context(
        item=item,
        metadata=metadata,
        sources=sources,
        audit_events=audit_events,
    )
    for discovered in discovered_sources:
        try:
            db.link_ap_item_source(discovered)
        except Exception:
            # Keep context rendering resilient if source persistence fails.
            pass

    # Reload after discovery so source distribution/coverage reflects connector links.
    sources = db.list_ap_item_sources(item["id"])

    source_types: Dict[str, int] = {}
    for source in sources:
        source_type = str(source.get("source_type") or "unknown")
        source_types[source_type] = source_types.get(source_type, 0) + 1
    distribution = ", ".join(f"{k}:{v}" for k, v in sorted(source_types.items()))

    organization_id = str(item.get("organization_id") or "default")
    all_items = db.list_ap_items(organization_id, limit=5000)
    vendor_name = str(item.get("vendor_name") or "").strip()
    vendor_key = vendor_name.lower()
    vendor_items = []
    if vendor_key:
        for candidate in all_items:
            candidate_vendor = str(candidate.get("vendor_name") or "").strip().lower()
            if candidate_vendor == vendor_key:
                vendor_items.append(candidate)
    vendor_total_spend = round(sum(_safe_float(entry.get("amount")) for entry in vendor_items), 2)
    vendor_open_count = sum(
        1
        for entry in vendor_items
        if str(entry.get("state") or "").strip().lower()
        in {"received", "validated", "needs_info", "needs_approval", "pending_approval", "approved", "ready_to_post"}
    )
    vendor_posted_count = sum(
        1
        for entry in vendor_items
        if str(entry.get("state") or "").strip().lower() in {"closed", "posted_to_erp"}
    )

    browser_events = [
        event for event in audit_events if str(event.get("event_type") or "").startswith("browser_")
    ]
    recent_browser_events: List[Dict[str, Any]] = []
    for event in browser_events[-10:]:
        payload = event.get("payload_json") or {}
        request_payload = payload.get("request") if isinstance(payload, dict) else {}
        recent_browser_events.append(
            {
                "event_id": event.get("id"),
                "ts": event.get("ts"),
                "status": payload.get("status") if isinstance(payload, dict) else None,
                "tool_name": (request_payload or {}).get("tool_name"),
                "command_id": payload.get("command_id") if isinstance(payload, dict) else None,
                "result": payload.get("result") if isinstance(payload, dict) else None,
            }
        )

    payment_portals = [
        source for source in sources if str(source.get("source_type") or "").lower() == "portal"
    ]
    procurement = [
        source for source in sources if str(source.get("source_type") or "").lower() == "procurement"
    ]
    dms_documents = [
        source for source in sources if str(source.get("source_type") or "").lower() == "dms"
    ]
    card_sources = [
        source
        for source in sources
        if str(source.get("source_type") or "").lower() in {"card_statement", "credit_card", "card"}
    ]
    bank_sources = [
        source for source in sources if str(source.get("source_type") or "").lower() == "bank"
    ]
    payroll_sources = [
        source for source in sources if str(source.get("source_type") or "").lower() == "payroll"
    ]
    spreadsheet_sources = [
        source
        for source in sources
        if str(source.get("source_type") or "").lower() in {"spreadsheet", "sheets"}
    ]

    latest_approval = approvals[0] if approvals else None
    latest_approval_payload = _parse_json(latest_approval.get("decision_payload")) if latest_approval else {}
    thread_preview = latest_approval_payload.get("thread_preview")
    if not isinstance(thread_preview, list):
        thread_preview = []
    budget_summary = _summarize_budget_context(metadata, approvals)
    approval_budget = budget_summary if budget_summary.get("checks") else (_parse_json(latest_approval_payload.get("budget")) or budget_summary)
    teams_context = _parse_json(metadata.get("teams")) or {}
    if approvals:
        for approval in approvals:
            source_channel = str(approval.get("source_channel") or "").strip().lower()
            if source_channel not in {"teams", "microsoft_teams", "ms_teams"}:
                continue
            teams_payload = _parse_json(approval.get("decision_payload"))
            merged = dict(teams_context)
            merged.setdefault("channel", approval.get("channel_id"))
            merged.setdefault("message_id", approval.get("message_ts"))
            merged.setdefault("state", approval.get("status"))
            if teams_payload.get("decision"):
                merged["last_action"] = teams_payload.get("decision")
            if approval.get("approved_by"):
                merged["updated_by"] = approval.get("approved_by")
            elif approval.get("rejected_by"):
                merged["updated_by"] = approval.get("rejected_by")
            if approval.get("rejection_reason"):
                merged["reason"] = approval.get("rejection_reason")
            teams_context = merged
            break

    erp_reference = item.get("erp_reference")
    connector_available = bool(erp_reference or metadata.get("erp_connector_available") or metadata.get("erp"))
    multi_system_summary = multi_system.get("summary") if isinstance(multi_system.get("summary"), dict) else {}
    connected_systems = (
        list(multi_system_summary.get("connected_systems") or [])
        if isinstance(multi_system_summary.get("connected_systems"), list)
        else []
    )
    summary_lines: List[str] = []
    if vendor_name:
        summary_lines.append(
            f"{vendor_name}: ${vendor_total_spend:,.2f} total tracked spend "
            f"({vendor_open_count} open, {vendor_posted_count} posted)."
        )
    if connected_systems:
        summary_lines.append(f"Connected systems: {', '.join(connected_systems)}.")
    if budget_summary.get("status") in {"critical", "exceeded"}:
        summary_lines.append(f"Budget status is {budget_summary.get('status')}; approval decision is required.")
    if metadata.get("has_context_conflict"):
        summary_lines.append("Context conflict detected; review merge/source evidence before posting.")
    if not summary_lines:
        summary_lines.append("Context is available. Review linked sources and proceed with approval controls.")
    summary_text = " ".join(summary_lines)
    related_records = _build_related_records_payload({**item, "metadata": metadata}, all_items)
    source_groups = _group_sources_by_type(sources)

    context = {
        "schema_version": "2.0",
        "ap_item_id": item.get("id"),
        "generated_at": now.isoformat(),
        "freshness": {
            "age_seconds": 0,
            "is_stale": False,
        },
        "source_quality": {
            "distribution": distribution or "none",
            "total_sources": len(sources),
        },
        "email": {
            "source_count": len(sources),
            "sources": sources,
            "source_groups": source_groups,
        },
        "web": {
            "browser_event_count": len(browser_events),
            "recent_browser_events": recent_browser_events[-5:],
            "related_portals": payment_portals,
            "payment_portals": payment_portals,
            "procurement": procurement,
            "dms_documents": dms_documents,
            "card_statements": _parse_json(multi_system.get("card_statements")).get("matched_transactions")
            if isinstance(multi_system.get("card_statements"), dict)
            else [],
            "bank_transactions": _parse_json(multi_system.get("bank")).get("matched_transactions") if isinstance(multi_system.get("bank"), dict) else [],
            "spreadsheets": _parse_json(multi_system.get("spreadsheets")).get("references") if isinstance(multi_system.get("spreadsheets"), dict) else [],
            "connector_coverage": {
                "payment_portal": bool(payment_portals),
                "procurement": bool(procurement or _parse_json(multi_system.get("summary")).get("has_procurement")),
                "dms": bool(dms_documents),
                "card_statements": bool(
                    card_sources or _parse_json(multi_system.get("summary")).get("has_card_statements")
                ),
                "bank": bool(bank_sources or _parse_json(multi_system.get("summary")).get("has_bank")),
                "payroll": bool(payroll_sources or _parse_json(multi_system.get("summary")).get("has_payroll")),
                "spreadsheets": bool(
                    spreadsheet_sources or _parse_json(multi_system.get("summary")).get("has_spreadsheets")
                ),
            },
        },
        "approvals": {
            "count": len(approvals),
            "latest": latest_approval,
            "slack": {
                "thread_preview": thread_preview[:5],
            },
            "teams": teams_context,
            "budget": approval_budget,
            "payroll": multi_system.get("payroll") if isinstance(multi_system.get("payroll"), dict) else {},
            "aggregated": {
                "vendor_name": vendor_name or None,
                "vendor_spend_to_date": vendor_total_spend,
                "vendor_open_invoices": int(vendor_open_count),
                "vendor_posted_invoices": int(vendor_posted_count),
                "connected_systems": connected_systems,
                "source_count": len(sources),
            },
        },
        "erp": {
            "state": item.get("state"),
            "erp_reference": erp_reference,
            "erp_posted_at": item.get("erp_posted_at"),
            "connector_available": connector_available,
        },
        "supersession": {
            "supersedes_ap_item_id": item.get("supersedes_ap_item_id") or metadata.get("supersedes_ap_item_id"),
            "supersedes_invoice_key": item.get("supersedes_invoice_key") or metadata.get("supersedes_invoice_key"),
            "superseded_by_ap_item_id": item.get("superseded_by_ap_item_id") or metadata.get("superseded_by_ap_item_id"),
            "resubmission_reason": item.get("resubmission_reason") or metadata.get("resubmission_reason"),
        },
        "related_records": related_records,
        "po_match": metadata.get("po_match") or metadata.get("po_match_result") or {},
        "budget": budget_summary,
        "risk_signals": metadata.get("risk_signals") or {},
        "bank": multi_system.get("bank") if isinstance(multi_system.get("bank"), dict) else {},
        "card_statements": multi_system.get("card_statements")
        if isinstance(multi_system.get("card_statements"), dict)
        else {},
        "procurement": multi_system.get("procurement") if isinstance(multi_system.get("procurement"), dict) else {},
        "payroll": multi_system.get("payroll") if isinstance(multi_system.get("payroll"), dict) else {},
        "spreadsheets": multi_system.get("spreadsheets") if isinstance(multi_system.get("spreadsheets"), dict) else {},
        "dms_documents": multi_system.get("dms_documents")
        if isinstance(multi_system.get("dms_documents"), dict)
        else {},
        "multi_system": multi_system.get("summary") if isinstance(multi_system.get("summary"), dict) else {},
        "summary": {
            "text": summary_text,
            "highlights": summary_lines,
            "connected_systems": connected_systems,
            "vendor_spend_to_date": vendor_total_spend,
            "vendor_open_invoices": int(vendor_open_count),
        },
    }
    return context


@router.get("/upcoming")
def get_upcoming_ap_tasks(
    organization_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=200),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, _user)
    db = get_db()
    return _build_upcoming_tasks_payload(db, organization_id, limit=limit)


@router.get("/vendors")
def get_vendor_directory(
    organization_id: str = Query(default="default"),
    search: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, _user)
    db = get_db()
    rows = _build_vendor_summary_rows(db, organization_id, search=search, limit=limit)
    return {
        "organization_id": organization_id,
        "vendors": rows,
        "count": len(rows),
    }


@router.get("/vendors/{vendor_name}")
def get_vendor_record(
    vendor_name: str,
    organization_id: str = Query(default="default"),
    days: int = Query(default=180, ge=30, le=365),
    invoice_limit: int = Query(default=20, ge=6, le=30),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, _user)
    db = get_db()
    return _build_vendor_detail_payload(
        db,
        organization_id,
        vendor_name,
        days=days,
        invoice_limit=invoice_limit,
    )


@router.get("/metrics/aggregation")
def get_ap_aggregation_metrics(
    organization_id: str = Query(default="default"),
    limit: int = Query(default=10000, ge=100, le=50000),
    vendor_limit: int = Query(default=10, ge=1, le=50),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """AP aggregation metrics for embedded approvals and ops consumers."""
    verify_org_access(organization_id, _user)
    db = get_db()
    metrics = db.get_ap_aggregation_metrics(
        organization_id=organization_id,
        limit=limit,
        vendor_limit=vendor_limit,
    )
    return {"metrics": metrics}


@router.get("/{ap_item_id}/audit")
def get_ap_item_audit(
    ap_item_id: str,
    browser_only: bool = Query(False),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    item = _require_item(db, ap_item_id)
    verify_org_access(item.get("organization_id") or "default", _user)
    events = db.list_ap_audit_events(ap_item_id)
    if browser_only:
        events = [event for event in events if str(event.get("event_type") or "").startswith("browser_")]
    return {"events": normalize_operator_audit_events(events)}


@router.get("/{ap_item_id}/sources")
def get_ap_item_sources(
    ap_item_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    item = _require_item(db, ap_item_id)
    verify_org_access(item.get("organization_id") or "default", _user)
    sources = db.list_ap_item_sources(ap_item_id)
    return {"sources": sources, "source_count": len(sources)}


async def _execute_field_review_resolution(
    db: ClearledgrDB,
    *,
    ap_item_id: str,
    request: ResolveFieldReviewRequest,
    organization_id: str,
    user: Any,
) -> Dict[str, Any]:
    item = _require_item(db, ap_item_id)
    verify_org_access(item.get("organization_id") or organization_id or "default", user)

    normalized_field = _normalize_field_review_field(request.field)
    normalized_source = _normalize_field_review_source(request.source)
    if normalized_field not in _FIELD_REVIEW_MUTABLE_FIELDS:
        raise HTTPException(status_code=400, detail="unsupported_field_review_field")
    if normalized_source not in {"email", "attachment", "manual"}:
        raise HTTPException(status_code=400, detail="unsupported_field_review_source")

    actor_id = _authenticated_actor(user)
    metadata = _parse_json(item.get("metadata"))
    worklist_item = build_worklist_item(db, {**item, "metadata": metadata})
    blocker = next(
        (
            row
            for row in (worklist_item.get("field_review_blockers") or [])
            if _normalize_field_review_field(row.get("field")) == normalized_field
        ),
        None,
    )
    if not blocker:
        raise HTTPException(status_code=400, detail="field_review_blocker_not_found")

    source_value = _resolve_field_review_source_value(
        blocker,
        source=normalized_source,
        manual_value=request.manual_value,
    )
    if source_value in (None, ""):
        raise HTTPException(status_code=400, detail="field_review_value_unavailable")

    resolved_value = _coerce_field_review_value(normalized_field, source_value)
    preview = _preview_field_review_resolution(
        db,
        item,
        metadata=metadata,
        field=normalized_field,
        resolved_value=resolved_value,
        resolved_source=normalized_source,
        actor_id=actor_id,
        blocker=blocker,
        note=request.note,
    )

    db.update_ap_item(
        ap_item_id,
        **preview["column_payload"],
        _actor_type="user",
        _actor_id=actor_id,
        _source="field_review_resolution",
        _decision_reason="field_review_resolved",
    )

    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "field_correction",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": str(item.get("organization_id") or organization_id or "default"),
            "source": "ap_item_field_review_resolution",
            "reason": "field_review_resolved",
            "metadata": {
                "field": normalized_field,
                "selected_source": normalized_source,
                "selected_value": resolved_value,
                "note": str(request.note or "").strip() or None,
                "resolved_at": preview["resolved_at"],
            },
        }
    )

    try:
        from clearledgr.services.correction_learning import CorrectionLearningService

        preview_metadata = _parse_json(preview["column_payload"].get("metadata"))
        expected_fields = {
            "vendor": preview["column_payload"].get("vendor_name") or item.get("vendor_name") or item.get("vendor"),
            "primary_amount": preview["column_payload"].get("amount", item.get("amount")),
            "currency": preview["column_payload"].get("currency", item.get("currency")),
            "primary_invoice": preview["column_payload"].get("invoice_number", item.get("invoice_number")),
            "due_date": preview["column_payload"].get("due_date", item.get("due_date")),
            "email_type": (
                preview_metadata.get("document_type")
                or preview_metadata.get("email_type")
                or metadata.get("document_type")
                or metadata.get("email_type")
            ),
        }
        learning_svc = CorrectionLearningService(str(item.get("organization_id") or organization_id or "default"))
        learning_svc.record_correction(
            correction_type=normalized_field,
            original_value=blocker.get("selected_value") if isinstance(blocker, dict) else item.get(normalized_field),
            corrected_value=resolved_value,
            context=_build_operator_truth_context(
                db,
                item=item,
                metadata=metadata,
                field=normalized_field,
                selected_source=normalized_source,
                blocker=blocker,
                expected_fields=expected_fields,
            ),
            user_id=actor_id,
            invoice_id=item.get("thread_id") or item.get("message_id"),
            feedback=str(request.note or "").strip() or None,
        )
    except Exception:
        logger.exception("field review correction learning capture failed for %s", ap_item_id)

    refreshed = _require_item(db, ap_item_id)
    normalized_item = build_worklist_item(db, refreshed)
    auto_resume_result: Optional[Dict[str, Any]] = None
    auto_resumed = False

    if request.auto_resume and _should_auto_resume_after_field_resolution(normalized_item):
        runtime = FinanceAgentRuntime(
            organization_id=str(refreshed.get("organization_id") or organization_id or "default"),
            actor_id=actor_id,
            actor_email=getattr(user, "email", None),
            db=db,
        )
        auto_resume_result = await runtime.execute_intent(
            "retry_recoverable_failures",
            {
                "ap_item_id": ap_item_id,
                "email_id": str(refreshed.get("thread_id") or refreshed.get("message_id") or ap_item_id),
                "reason": "Resume workflow after field review resolution",
                "source_channel": "gmail_route",
                "source_channel_id": "gmail_route",
                "source_message_ref": str(refreshed.get("thread_id") or refreshed.get("message_id") or ap_item_id),
            },
        )
        auto_resume_status = str((auto_resume_result or {}).get("status") or "").strip().lower()
        auto_resumed = auto_resume_status in {"ready_to_post", "posted", "posted_to_erp", "recovered"}
        refreshed = _require_item(db, ap_item_id)
        normalized_item = build_worklist_item(db, refreshed)

    return {
        "status": "resolved_and_resumed" if auto_resumed else "resolved",
        "ap_item_id": ap_item_id,
        "field": normalized_field,
        "selected_source": normalized_source,
        "selected_value": resolved_value,
        "auto_resumed": auto_resumed,
        "auto_resume_result": auto_resume_result,
        "ap_item": normalized_item,
    }


@router.post("/{ap_item_id}/field-review/resolve")
async def resolve_ap_item_field_review(
    ap_item_id: str,
    request: ResolveFieldReviewRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = get_db()
    result = await _execute_field_review_resolution(
        db,
        ap_item_id=ap_item_id,
        request=request,
        organization_id=organization_id,
        user=user,
    )
    result["requires_field_review"] = bool((result.get("ap_item") or {}).get("requires_field_review"))
    return result


@router.post("/field-review/bulk-resolve")
async def bulk_resolve_ap_item_field_review(
    request: BulkResolveFieldReviewRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = get_db()
    ap_item_ids = [
        str(ap_item_id or "").strip()
        for ap_item_id in (request.ap_item_ids or [])
        if str(ap_item_id or "").strip()
    ]
    ap_item_ids = list(dict.fromkeys(ap_item_ids))[:50]
    if not ap_item_ids:
        raise HTTPException(status_code=400, detail="missing_ap_item_ids")

    single_request = ResolveFieldReviewRequest(
        field=request.field,
        source=request.source,
        manual_value=request.manual_value,
        note=request.note,
        auto_resume=request.auto_resume,
    )
    results: List[Dict[str, Any]] = []
    success_count = 0
    auto_resumed_count = 0

    for ap_item_id in ap_item_ids:
        try:
            result = await _execute_field_review_resolution(
                db,
                ap_item_id=ap_item_id,
                request=single_request,
                organization_id=organization_id,
                user=user,
            )
            result["requires_field_review"] = bool((result.get("ap_item") or {}).get("requires_field_review"))
            success_count += 1
            auto_resumed_count += int(bool(result.get("auto_resumed")))
            results.append(result)
        except HTTPException as exc:
            results.append(
                {
                    "status": "error",
                    "ap_item_id": ap_item_id,
                    "reason": str(exc.detail),
                    "http_status": exc.status_code,
                }
            )

    return {
        "status": "completed" if success_count == len(ap_item_ids) else ("partial" if success_count > 0 else "error"),
        "requested_count": len(ap_item_ids),
        "success_count": success_count,
        "failed_count": len(ap_item_ids) - success_count,
        "auto_resumed_count": auto_resumed_count,
        "results": results,
    }


@router.post("/{ap_item_id}/non-invoice/resolve")
def resolve_non_invoice_review(
    ap_item_id: str,
    request: ResolveNonInvoiceReviewRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = get_db()
    item = _require_item(db, ap_item_id)
    verify_org_access(item.get("organization_id") or organization_id or "default", user)

    metadata = _parse_json(item.get("metadata"))
    document_type = _normalize_document_type_token(
        item.get("document_type")
        or metadata.get("document_type")
        or metadata.get("email_type")
    )
    if document_type == "invoice":
        raise HTTPException(status_code=400, detail="invoice_document_not_supported")

    outcome = _normalize_non_invoice_outcome(request.outcome)
    allowed_outcomes = _NON_INVOICE_ALLOWED_OUTCOMES.get(document_type) or _NON_INVOICE_ALLOWED_OUTCOMES["other"]
    if outcome not in allowed_outcomes:
        raise HTTPException(status_code=400, detail="invalid_non_invoice_outcome")

    related_reference = str(request.related_reference or "").strip() or None
    related_ap_item_id = str(request.related_ap_item_id or "").strip() or None
    if outcome in {"apply_to_invoice", "link_to_payment"} and not (related_reference or related_ap_item_id):
        raise HTTPException(status_code=400, detail="related_reference_required")

    actor_id = _authenticated_actor(user)
    resolved_at = datetime.now(timezone.utc).isoformat()
    next_state = _non_invoice_resolution_state(
        current_state=str(item.get("state") or "").strip().lower() or APState.RECEIVED.value,
        outcome=outcome,
        close_record=bool(request.close_record),
    )

    resolution = {
        "document_type": document_type,
        "outcome": outcome,
        "related_reference": related_reference,
        "related_ap_item_id": related_ap_item_id,
        "note": str(request.note or "").strip() or None,
        "resolved_at": resolved_at,
        "resolved_by": actor_id,
        "closed_record": bool(request.close_record),
    }
    resolution.update(
        _non_invoice_resolution_semantics(
            document_type=document_type,
            outcome=outcome,
            close_record=bool(request.close_record),
        )
    )
    metadata["non_invoice_resolution"] = resolution
    metadata["non_invoice_review_required"] = False

    current_state = str(item.get("state") or "").strip().lower() or APState.RECEIVED.value
    update_payload: Dict[str, Any] = {
        "metadata": metadata,
    }
    if next_state != current_state:
        update_payload["state"] = next_state
    if outcome != "needs_followup":
        update_payload["exception_code"] = None
        update_payload["exception_severity"] = None

    db.update_ap_item(
        ap_item_id,
        **_filter_allowed_ap_item_updates(db, update_payload),
        _actor_type="user",
        _actor_id=actor_id,
        _source="non_invoice_review_resolution",
        _decision_reason=outcome,
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "non_invoice_review_resolved",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": str(item.get("organization_id") or organization_id or "default"),
            "source": "ap_item_non_invoice_review_resolution",
            "reason": outcome,
            "metadata": resolution,
        }
    )

    refreshed = _require_item(db, ap_item_id)
    normalized_item = build_worklist_item(db, refreshed)
    return {
        "status": "resolved",
        "ap_item_id": ap_item_id,
        "document_type": document_type,
        "outcome": outcome,
        "state": next_state,
        "ap_item": normalized_item,
    }


@router.post("/{ap_item_id}/sources/link")
def link_ap_item_source(
    ap_item_id: str,
    request: LinkSourceRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = get_db()
    item = _require_item(db, ap_item_id)
    verify_org_access(item.get("organization_id") or "default", _user)
    source = db.link_ap_item_source(
        {
            "ap_item_id": ap_item_id,
            "source_type": request.source_type,
            "source_ref": request.source_ref,
            "subject": request.subject,
            "sender": request.sender,
            "detected_at": request.detected_at,
            "metadata": request.metadata or {},
        }
    )
    return {"source": source}


@router.get("/{ap_item_id}/context")
def get_ap_item_context(
    ap_item_id: str,
    refresh: bool = Query(False),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    item = _require_item(db, ap_item_id)
    verify_org_access(item.get("organization_id") or "default", _user)

    if not refresh:
        cached = db.get_ap_item_context_cache(ap_item_id)
        if cached and isinstance(cached.get("context_json"), dict):
            context = dict(cached.get("context_json") or {})
            schema_version = str(context.get("schema_version") or "")
            if not schema_version.startswith("2."):
                context = {}
            if context:
                updated_at = _parse_iso(cached.get("updated_at"))
                if updated_at:
                    age_seconds = max(0, int((datetime.now(timezone.utc) - updated_at).total_seconds()))
                    freshness = context.get("freshness") if isinstance(context.get("freshness"), dict) else {}
                    freshness["age_seconds"] = age_seconds
                    freshness["is_stale"] = age_seconds > 300
                    context["freshness"] = freshness
                return context

    context = _build_context_payload(db, item)
    db.upsert_ap_item_context_cache(ap_item_id, context)
    return context


@router.post("/{ap_item_id}/resubmit")
def resubmit_rejected_item(
    ap_item_id: str,
    request: ResubmitRejectedItemRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = get_db()
    actor_id = _authenticated_actor(_user)
    source = _require_item(db, ap_item_id)
    verify_org_access(source.get("organization_id") or "default", _user)
    source_state = _normalized_state_value(source.get("state"))
    if source_state != APState.REJECTED.value:
        raise HTTPException(status_code=400, detail="resubmission_requires_rejected_state")

    existing_child_id = str(source.get("superseded_by_ap_item_id") or "").strip()
    if existing_child_id:
        existing_child = db.get_ap_item(existing_child_id)
        if existing_child:
            return {
                "status": "already_resubmitted",
                "source_ap_item_id": source["id"],
                "new_ap_item_id": existing_child_id,
                "ap_item": build_worklist_item(db, existing_child),
                "linkage": {
                    "supersedes_ap_item_id": source["id"],
                    "supersedes_invoice_key": existing_child.get("supersedes_invoice_key")
                    or source.get("invoice_key"),
                    "superseded_by_ap_item_id": existing_child_id,
                },
            }

    initial_state = _normalized_state_value(request.initial_state)
    if initial_state not in {APState.RECEIVED.value, APState.VALIDATED.value}:
        raise HTTPException(status_code=400, detail="invalid_resubmission_initial_state")

    source_meta = _parse_json(source.get("metadata"))
    new_meta = dict(source_meta)
    for stale_key in (
        "merged_into",
        "merge_reason",
        "merge_status",
        "suppressed_from_worklist",
        "confidence_override",
    ):
        new_meta.pop(stale_key, None)
    new_meta["supersedes_ap_item_id"] = source["id"]
    new_meta["supersedes_invoice_key"] = _superseded_invoice_key(source, request)
    new_meta["resubmission_reason"] = request.reason
    new_meta["resubmission"] = {
        "source_ap_item_id": source["id"],
        "reason": request.reason,
        "actor_id": actor_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if request.actor_id and str(request.actor_id).strip() and str(request.actor_id).strip() != actor_id:
        new_meta["requested_actor_id"] = str(request.actor_id).strip()
    if request.metadata:
        new_meta.update(request.metadata)

    create_payload: Dict[str, Any] = {
        "invoice_key": _resubmission_invoice_key(source, request),
        "thread_id": request.thread_id or source.get("thread_id"),
        "message_id": request.message_id or source.get("message_id"),
        "subject": request.subject or source.get("subject"),
        "sender": request.sender or source.get("sender"),
        "vendor_name": request.vendor_name or source.get("vendor_name"),
        "amount": request.amount if request.amount is not None else source.get("amount"),
        "currency": request.currency or source.get("currency") or "USD",
        "invoice_number": request.invoice_number or source.get("invoice_number"),
        "invoice_date": request.invoice_date or source.get("invoice_date"),
        "due_date": request.due_date or source.get("due_date"),
        "state": initial_state,
        "confidence": source.get("confidence"),
        "approval_required": bool(source.get("approval_required", True)),
        "workflow_id": source.get("workflow_id"),
        "run_id": None,
        "approval_surface": source.get("approval_surface") or "hybrid",
        "approval_policy_version": source.get("approval_policy_version"),
        "post_attempted_at": None,
        "last_error": None,
        "organization_id": source.get("organization_id"),
        "user_id": source.get("user_id"),
        "po_number": source.get("po_number"),
        "attachment_url": source.get("attachment_url"),
        "supersedes_ap_item_id": source["id"],
        "supersedes_invoice_key": _superseded_invoice_key(source, request),
        "superseded_by_ap_item_id": None,
        "resubmission_reason": request.reason,
        "metadata": new_meta,
    }
    created = db.create_ap_item(create_payload)

    db.update_ap_item(
        source["id"],
        superseded_by_ap_item_id=created["id"],
        _actor_type="user",
        _actor_id=actor_id,
    )
    source_after = db.get_ap_item(source["id"]) or source
    source_after_meta = _parse_json(source_after.get("metadata"))
    source_after_meta["superseded_by_ap_item_id"] = created["id"]
    source_after_meta["resubmission_reason"] = request.reason
    db.update_ap_item(source["id"], metadata=source_after_meta, _actor_type="user", _actor_id=actor_id)

    copied_sources = 0
    if request.copy_sources:
        copied_sources = _copy_item_sources_for_resubmission(
            db,
            source_ap_item_id=source["id"],
            target_ap_item_id=created["id"],
            actor_id=actor_id,
        )

    audit_key = f"ap_item_resubmission:{source['id']}:{created['id']}"
    db.append_ap_audit_event(
        {
            "ap_item_id": source["id"],
            "event_type": "ap_item_resubmitted",
            "actor_type": "user",
            "actor_id": actor_id,
            "payload_json": {
                "source_ap_item_id": source["id"],
                "new_ap_item_id": created["id"],
                "reason": request.reason,
                "copied_sources": copied_sources,
            },
            "organization_id": source.get("organization_id") or "default",
            "source": "ap_items_api",
            "decision_reason": request.reason,
            "idempotency_key": audit_key,
        }
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": created["id"],
            "event_type": "ap_item_resubmission_created",
            "actor_type": "user",
            "actor_id": actor_id,
            "payload_json": {
                "source_ap_item_id": source["id"],
                "new_ap_item_id": created["id"],
                "reason": request.reason,
                "copied_sources": copied_sources,
            },
            "organization_id": created.get("organization_id") or "default",
            "source": "ap_items_api",
            "decision_reason": request.reason,
            "idempotency_key": f"{audit_key}:new",
        }
    )

    return {
        "status": "resubmitted",
        "source_ap_item_id": source["id"],
        "new_ap_item_id": created["id"],
        "copied_sources": copied_sources,
        "linkage": {
            "supersedes_ap_item_id": source["id"],
            "supersedes_invoice_key": created.get("supersedes_invoice_key")
            or _superseded_invoice_key(source, request),
            "superseded_by_ap_item_id": created["id"],
            "resubmission_reason": request.reason,
        },
        "ap_item": build_worklist_item(db, created),
    }


@router.post("/{ap_item_id}/merge")
def merge_ap_items(ap_item_id: str, request: MergeItemsRequest, _user=Depends(require_ops_user)) -> Dict[str, Any]:
    db = get_db()
    actor_id = _authenticated_actor(_user)
    target = _require_item(db, ap_item_id)
    verify_org_access(target.get("organization_id") or "default", _user)
    source = _require_item(db, request.source_ap_item_id)

    if target.get("id") == source.get("id"):
        raise HTTPException(status_code=400, detail="cannot_merge_same_item")
    if str(target.get("organization_id") or "default") != str(source.get("organization_id") or "default"):
        raise HTTPException(status_code=400, detail="organization_mismatch")

    moved_count = 0
    for source_link in db.list_ap_item_sources(source["id"]):
        moved = db.move_ap_item_source(
            from_ap_item_id=source["id"],
            to_ap_item_id=target["id"],
            source_type=source_link.get("source_type"),
            source_ref=source_link.get("source_ref"),
        )
        if moved:
            moved_count += 1

    # Preserve legacy source pointers as explicit links when present.
    if source.get("thread_id"):
        db.link_ap_item_source(
            {
                "ap_item_id": target["id"],
                "source_type": "gmail_thread",
                "source_ref": source.get("thread_id"),
                "subject": source.get("subject"),
                "sender": source.get("sender"),
                "detected_at": source.get("created_at"),
                "metadata": {"merge_origin": source.get("id")},
            }
        )
    if source.get("message_id"):
        db.link_ap_item_source(
            {
                "ap_item_id": target["id"],
                "source_type": "gmail_message",
                "source_ref": source.get("message_id"),
                "subject": source.get("subject"),
                "sender": source.get("sender"),
                "detected_at": source.get("created_at"),
                "metadata": {"merge_origin": source.get("id")},
            }
        )

    target_meta = _parse_json(target.get("metadata"))
    merge_history = target_meta.get("merge_history")
    if not isinstance(merge_history, list):
        merge_history = []
    merged_at = datetime.now(timezone.utc).isoformat()
    merge_history.append(
        {
            "source_ap_item_id": source["id"],
            "reason": request.reason,
            "actor_id": actor_id,
            "merged_at": merged_at,
        }
    )
    target_meta["merge_history"] = merge_history
    target_meta["merge_reason"] = request.reason
    target_meta["has_context_conflict"] = False
    target_meta["source_count"] = len(db.list_ap_item_sources(target["id"]))
    db.update_ap_item(target["id"], metadata=target_meta)

    source_meta = _parse_json(source.get("metadata"))
    source_meta["merged_into"] = target["id"]
    source_meta["merge_reason"] = request.reason
    source_meta["merged_at"] = merged_at
    source_meta["merged_by"] = actor_id
    source_meta["merge_status"] = "merged_source"
    source_meta["source_count"] = 0
    source_meta["suppressed_from_worklist"] = True
    if source.get("state"):
        source_meta["merge_source_state"] = source.get("state")
    db.update_ap_item(source["id"], metadata=source_meta)

    db.append_ap_audit_event(
        {
            "ap_item_id": target["id"],
            "event_type": "ap_item_merged",
            "actor_type": "user",
            "actor_id": actor_id,
            "payload_json": {
                "target_ap_item_id": target["id"],
                "source_ap_item_id": source["id"],
                "reason": request.reason,
                "moved_sources": moved_count,
            },
            "organization_id": target.get("organization_id") or "default",
            "source": "ap_items_api",
            "decision_reason": request.reason,
            "idempotency_key": f"merge:{target['id']}:{source['id']}",
        }
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": source["id"],
            "event_type": "ap_item_merged_into",
            "actor_type": "user",
            "actor_id": actor_id,
            "payload_json": {
                "source_ap_item_id": source["id"],
                "target_ap_item_id": target["id"],
                "reason": request.reason,
            },
            "organization_id": source.get("organization_id") or "default",
            "source": "ap_items_api",
            "decision_reason": request.reason,
            "idempotency_key": f"merge-source:{source['id']}:{target['id']}",
        }
    )

    return {
        "status": "merged",
        "target_ap_item_id": target["id"],
        "source_ap_item_id": source["id"],
        "moved_sources": moved_count,
    }


@router.post("/{ap_item_id}/split")
def split_ap_item(ap_item_id: str, request: SplitItemRequest, _user=Depends(require_ops_user)) -> Dict[str, Any]:
    db = get_db()
    actor_id = _authenticated_actor(_user)
    parent = _require_item(db, ap_item_id)
    verify_org_access(parent.get("organization_id") or "default", _user)
    if not request.sources:
        raise HTTPException(status_code=400, detail="sources_required")

    created_items: List[Dict[str, Any]] = []
    parent_meta = _parse_json(parent.get("metadata"))
    now = datetime.now(timezone.utc).isoformat()

    for source in request.sources:
        current_sources = db.list_ap_item_sources(parent["id"], source_type=source.source_type)
        current = next((row for row in current_sources if row.get("source_ref") == source.source_ref), None)
        if not current:
            continue

        split_payload = {
            "invoice_key": f"{parent.get('invoice_key') or parent['id']}#split#{source.source_type}:{source.source_ref}",
            "thread_id": parent.get("thread_id"),
            "message_id": parent.get("message_id"),
            "subject": current.get("subject") or parent.get("subject"),
            "sender": current.get("sender") or parent.get("sender"),
            "vendor_name": parent.get("vendor_name"),
            "amount": parent.get("amount"),
            "currency": parent.get("currency") or "USD",
            "invoice_number": parent.get("invoice_number"),
            "invoice_date": parent.get("invoice_date"),
            "due_date": parent.get("due_date"),
            "state": "needs_info",
            "confidence": parent.get("confidence") or 0,
            "approval_required": bool(parent.get("approval_required", True)),
            "organization_id": parent.get("organization_id") or "default",
            "user_id": parent.get("user_id"),
            "metadata": {
                **parent_meta,
                "split_from_ap_item_id": parent["id"],
                "split_reason": request.reason,
                "split_actor_id": actor_id,
                "split_source": {"source_type": source.source_type, "source_ref": source.source_ref},
                "split_at": now,
            },
        }
        child = db.create_ap_item(split_payload)
        db.move_ap_item_source(
            from_ap_item_id=parent["id"],
            to_ap_item_id=child["id"],
            source_type=source.source_type,
            source_ref=source.source_ref,
        )

        if source.source_type == "gmail_thread":
            db.update_ap_item(child["id"], thread_id=source.source_ref)
        if source.source_type == "gmail_message":
            db.update_ap_item(child["id"], message_id=source.source_ref)

        db.append_ap_audit_event(
            {
                "ap_item_id": child["id"],
                "event_type": "ap_item_split_created",
                "actor_type": "user",
                "actor_id": actor_id,
                "payload_json": {
                    "parent_ap_item_id": parent["id"],
                    "source_type": source.source_type,
                    "source_ref": source.source_ref,
                    "reason": request.reason,
                },
                "organization_id": parent.get("organization_id") or "default",
                "source": "ap_items_api",
            }
        )
        created_items.append(child)

    if not created_items:
        raise HTTPException(status_code=400, detail="no_sources_split")

    parent_meta["source_count"] = len(db.list_ap_item_sources(parent["id"]))
    db.update_ap_item(parent["id"], metadata=parent_meta)

    return {
        "status": "split",
        "parent_ap_item_id": parent["id"],
        "created_items": [build_worklist_item(db, item) for item in created_items],
    }


@router.post("/{ap_item_id}/retry-post")
async def retry_erp_post(
    ap_item_id: str,
    organization_id: str = "default",
    _user=Depends(require_ops_user),
):
    """Retry posting an AP item through the canonical finance runtime."""
    verify_org_access(organization_id, _user)
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")

    if item.get("organization_id") != organization_id:
        raise HTTPException(status_code=403, detail="Organization mismatch")

    current_state = item.get("state") or item.get("status")
    if current_state != APState.FAILED_POST:
        raise HTTPException(
            status_code=400,
            detail=f"Can only retry from failed_post state (current: {current_state})",
        )

    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime

    runtime = FinanceAgentRuntime(
        organization_id=organization_id,
        actor_id=getattr(_user, "user_id", None) or getattr(_user, "email", None) or "ap_retry",
        actor_email=getattr(_user, "email", None) or getattr(_user, "user_id", None) or "ap_retry",
        db=db,
    )
    try:
        retry_result = await runtime.execute_intent(
            "retry_recoverable_failures",
            {
                "ap_item_id": ap_item_id,
                "email_id": str(item.get("thread_id") or ap_item_id),
                "reason": "retry_post_api",
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=safe_error(exc, "ERP posting")) from exc

    status = str((retry_result or {}).get("status") or "").strip().lower()
    if status == "posted":
        return {
            "status": "posted",
            "ap_item_id": ap_item_id,
            "erp_reference": (retry_result or {}).get("erp_reference"),
            "resume_result": retry_result.get("result") if isinstance(retry_result, dict) else None,
            "retry_result": retry_result,
        }
    if status == "blocked":
        reason = str((retry_result or {}).get("reason") or "retry_not_recoverable")
        raise HTTPException(status_code=400, detail=reason)
    if status == "ready_to_post":
        return {
            "status": "ready_to_post",
            "ap_item_id": ap_item_id,
            "erp_reference": (retry_result or {}).get("erp_reference"),
            "resume_result": retry_result.get("result") if isinstance(retry_result, dict) else None,
            "retry_result": retry_result,
        }
    if status == "error":
        reason = str((retry_result or {}).get("reason") or "erp_post_failed")
        raise HTTPException(
            status_code=502,
            detail=f"ERP posting failed: {reason}",
        )
    raise HTTPException(status_code=502, detail=f"ERP posting failed: {status or 'retry_failed'}")

    return {
        "status": status or "unknown",
        "ap_item_id": ap_item_id,
        "resume_result": resume_result,
    }
