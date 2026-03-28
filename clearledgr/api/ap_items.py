"""AP item APIs used by the Gmail extension focus-first sidebar."""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from clearledgr.core.ap_confidence import evaluate_critical_field_confidence
from clearledgr.core.ap_entity_routing import (
    match_entity_candidate,
    normalize_entity_candidate,
    resolve_entity_routing,
)
from clearledgr.core.database import ClearledgrDB, get_db
from clearledgr.core.ap_states import APState
from clearledgr.services.ap_context_connectors import build_multi_system_context
from clearledgr.services.erp_api_first import (
    apply_credit_note_api_first,
    apply_settlement_api_first,
)
from clearledgr.services.erp_follow_on_result import (
    _ERP_FOLLOW_ON_APPLIED_STATUSES,
    _ERP_FOLLOW_ON_PENDING_STATUSES,
    _apply_erp_follow_on_result,
    _refresh_linked_finance_metadata,
)
from clearledgr.services.ap_projection import build_worklist_items
from clearledgr.services.policy_compliance import get_approval_automation_policy
from clearledgr.api.ap_item_contracts import (
    BulkResolveFieldReviewRequest,
    LinkSourceRequest,
    MergeItemsRequest,
    ResolveEntityRouteRequest,
    ResolveFieldReviewRequest,
    ResolveNonInvoiceReviewRequest,
    ResubmitRejectedItemRequest,
    SplitItemRequest,
)


router = APIRouter(prefix="/api/ap/items", tags=["ap-items"])
logger = logging.getLogger(__name__)


def _load_org_settings_for_item(db: ClearledgrDB, organization_id: Any) -> Dict[str, Any]:
    org_id = str(organization_id or "").strip()
    if not org_id or not hasattr(db, "get_organization"):
        return {}
    org = db.get_organization(org_id) or {}
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            settings = {}
    return settings if isinstance(settings, dict) else {}


def _finance_agent_runtime_cls():
    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime

    return FinanceAgentRuntime


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


def _approval_followup_policy(organization_id: str) -> Dict[str, Any]:
    return get_approval_automation_policy(organization_id=organization_id or "default")


def _approval_followup_sla_minutes(approval_policy: Optional[Dict[str, Any]] = None) -> int:
    policy = approval_policy if isinstance(approval_policy, dict) else {}
    try:
        reminder_hours = int(policy.get("reminder_hours") or 4)
    except (TypeError, ValueError):
        reminder_hours = 4
    return max(60, min(reminder_hours * 60, 10080))


def _approval_followup_escalation_minutes(approval_policy: Optional[Dict[str, Any]] = None) -> int:
    policy = approval_policy if isinstance(approval_policy, dict) else {}
    try:
        escalation_hours = int(policy.get("escalation_hours") or 24)
    except (TypeError, ValueError):
        escalation_hours = 24
    return max(60, min(escalation_hours * 60, 20160))


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


def _pending_approver_ids(db: ClearledgrDB, ap_item_id: str, metadata: Dict[str, Any]) -> List[str]:
    if ap_item_id and hasattr(db, "get_pending_approver_ids"):
        try:
            rows = db.get_pending_approver_ids(ap_item_id)
            if isinstance(rows, list):
                pending = [str(value).strip() for value in rows if str(value).strip()]
                if pending:
                    return pending
        except Exception:
            pass
    raw = metadata.get("approval_sent_to")
    if isinstance(raw, list):
        return [str(value).strip() for value in raw if str(value).strip()]
    token = str(raw or "").strip()
    return [token] if token else []


def _build_approval_followup(
    db: ClearledgrDB,
    payload: Dict[str, Any],
    metadata: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    approval_policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = str(payload.get("state") or "").strip().lower()
    if state not in {APState.NEEDS_APPROVAL.value, "pending_approval"}:
        return {}

    now_utc = now or datetime.now(timezone.utc)
    organization_id = str(payload.get("organization_id") or "default").strip() or "default"
    policy = (
        approval_policy
        if isinstance(approval_policy, dict)
        else _approval_followup_policy(organization_id)
    )
    requested_at_raw = (
        payload.get("approval_requested_at")
        or metadata.get("approval_requested_at")
        or payload.get("updated_at")
        or payload.get("created_at")
    )
    requested_at = _parse_iso(requested_at_raw)
    wait_minutes = max(
        0,
        int((now_utc - requested_at).total_seconds() // 60),
    ) if requested_at else 0
    sla_minutes = _approval_followup_sla_minutes(policy)
    escalation_minutes = _approval_followup_escalation_minutes(policy)
    pending_assignees = _pending_approver_ids(db, str(payload.get("id") or "").strip(), metadata)
    sla_breached = bool(requested_at and wait_minutes >= sla_minutes)
    escalation_due = bool(requested_at and wait_minutes >= escalation_minutes)

    next_action = str(metadata.get("approval_next_action") or "").strip().lower()
    if not next_action:
        if escalation_due:
            next_action = "escalate_approval"
        elif sla_breached:
            next_action = "nudge_approval"
        elif pending_assignees:
            next_action = "wait_for_approval"
        else:
            next_action = "reassign_approval"

    return {
        "requested_at": requested_at.isoformat() if requested_at else None,
        "wait_minutes": wait_minutes,
        "sla_minutes": sla_minutes,
        "escalation_minutes": escalation_minutes,
        "sla_breached": sla_breached,
        "escalation_due": escalation_due,
        "pending_assignees": pending_assignees,
        "nudge_count": max(0, _safe_int(metadata.get("approval_nudge_count"), 0)),
        "escalation_count": max(0, _safe_int(metadata.get("approval_escalation_count"), 0)),
        "reassignment_count": max(0, _safe_int(metadata.get("approval_reassignment_count"), 0)),
        "last_nudged_at": str(metadata.get("approval_last_nudged_at") or "").strip() or None,
        "last_escalated_at": str(metadata.get("approval_last_escalated_at") or "").strip() or None,
        "last_reassigned_at": str(metadata.get("approval_last_reassigned_at") or "").strip() or None,
        "last_reassigned_to": str(metadata.get("approval_last_reassigned_to") or "").strip() or None,
        "escalation_channel": str(policy.get("escalation_channel") or "").strip() or None,
        "next_action": next_action,
    }


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


def _resolve_related_ap_item_for_non_invoice(
    db: ClearledgrDB,
    *,
    organization_id: str,
    source_ap_item_id: str,
    related_ap_item_id: Optional[str],
    related_reference: Optional[str],
) -> tuple[Optional[Dict[str, Any]], str]:
    related_id = str(related_ap_item_id or "").strip()
    reference = str(related_reference or "").strip()

    if related_id:
        candidate = _require_item(db, related_id)
        if str(candidate.get("organization_id") or "").strip() != str(organization_id or "").strip():
            raise HTTPException(status_code=404, detail="related_ap_item_not_found")
        if str(candidate.get("id") or "").strip() == str(source_ap_item_id or "").strip():
            raise HTTPException(status_code=400, detail="related_ap_item_cannot_match_source")
        return candidate, "linked"

    if not reference:
        return None, "not_requested"

    direct_candidate = db.get_ap_item(reference) if hasattr(db, "get_ap_item") else None
    if direct_candidate and str(direct_candidate.get("organization_id") or "").strip() == str(organization_id or "").strip():
        if str(direct_candidate.get("id") or "").strip() == str(source_ap_item_id or "").strip():
            raise HTTPException(status_code=400, detail="related_ap_item_cannot_match_source")
        return direct_candidate, "linked"

    lookup_methods = (
        getattr(db, "get_ap_item_by_invoice_number", None),
        getattr(db, "get_ap_item_by_erp_reference", None),
        getattr(db, "get_ap_item_by_invoice_key", None),
        getattr(db, "get_ap_item_by_workflow_id", None),
    )
    for getter in lookup_methods:
        if not callable(getter):
            continue
        try:
            candidate = getter(organization_id, reference)
        except TypeError:
            continue
        if not candidate:
            continue
        if str(candidate.get("id") or "").strip() == str(source_ap_item_id or "").strip():
            raise HTTPException(status_code=400, detail="related_ap_item_cannot_match_source")
        return candidate, "linked"

    return None, "reference_only"


def _non_invoice_link_event_type(document_type: str) -> str:
    normalized = _normalize_document_type_token(document_type)
    mapping = {
        "credit_note": "credit_note_linked",
        "refund": "refund_linked",
        "receipt": "receipt_linked",
        "payment": "payment_confirmation_linked",
        "payment_request": "payment_request_linked",
        "statement": "statement_linked",
        "bank_statement": "statement_linked",
    }
    return mapping.get(normalized, "non_invoice_linked")


def _build_linked_finance_document_entry(
    *,
    source_item: Dict[str, Any],
    document_type: str,
    resolution: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "source_ap_item_id": source_item.get("id"),
        "document_type": _normalize_document_type_token(document_type),
        "invoice_number": source_item.get("invoice_number"),
        "vendor_name": source_item.get("vendor_name") or source_item.get("vendor"),
        "amount": _safe_float(source_item.get("amount")),
        "currency": source_item.get("currency") or "USD",
        "outcome": resolution.get("outcome"),
        "accounting_treatment": resolution.get("accounting_treatment"),
        "downstream_queue": resolution.get("downstream_queue"),
        "linked_at": resolution.get("resolved_at"),
        "linked_by": resolution.get("resolved_by"),
        "related_reference": resolution.get("related_reference"),
        "thread_id": source_item.get("thread_id"),
        "message_id": source_item.get("message_id"),
    }


def _upsert_linked_finance_document(
    metadata: Dict[str, Any],
    *,
    entry: Dict[str, Any],
    related_item: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    existing = metadata.get("linked_finance_documents")
    rows = list(existing) if isinstance(existing, list) else []
    source_ap_item_id = str(entry.get("source_ap_item_id") or "").strip()
    outcome = str(entry.get("outcome") or "").strip()
    filtered = [
        row
        for row in rows
        if not (
            str((row or {}).get("source_ap_item_id") or "").strip() == source_ap_item_id
            and str((row or {}).get("outcome") or "").strip() == outcome
        )
    ]
    filtered.append(entry)
    filtered.sort(key=lambda row: _safe_sort_timestamp((row or {}).get("linked_at")), reverse=True)
    metadata["linked_finance_documents"] = filtered[:25]
    return _refresh_linked_finance_metadata(metadata, related_item=related_item)


def _create_statement_reconciliation_artifact(
    db: ClearledgrDB,
    *,
    item: Dict[str, Any],
    document_type: str,
    organization_id: str,
    resolution: Dict[str, Any],
    related_item: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not hasattr(db, "create_recon_session") or not hasattr(db, "create_recon_item"):
        return {}

    session = db.create_recon_session(
        organization_id=organization_id,
        source_type="gmail_statement",
    )
    transaction_date = (
        str(item.get("invoice_date") or "").strip()
        or str(item.get("due_date") or "").strip()
        or str(item.get("created_at") or "").strip()
        or None
    )
    reference = (
        str(resolution.get("related_reference") or "").strip()
        or str(item.get("invoice_number") or "").strip()
        or str(item.get("thread_id") or "").strip()
        or None
    )
    recon_item_id = db.create_recon_item(
        session_id=str(session.get("id") or "").strip(),
        organization_id=organization_id,
        row_index=1,
        transaction_date=transaction_date,
        description=str(item.get("subject") or item.get("vendor_name") or "Bank statement").strip() or "Bank statement",
        amount=_coerce_optional_float(item.get("amount")),
        reference=reference,
    )
    recon_metadata = {
        "source_ap_item_id": item.get("id"),
        "document_type": _normalize_document_type_token(document_type),
        "related_reference": resolution.get("related_reference"),
        "thread_id": item.get("thread_id"),
        "message_id": item.get("message_id"),
        "vendor_name": item.get("vendor_name") or item.get("vendor"),
    }
    update_kwargs: Dict[str, Any] = {
        "state": "review",
        "metadata": json.dumps(recon_metadata),
    }
    if related_item:
        update_kwargs["matched_ap_item_id"] = related_item.get("id")
        update_kwargs["match_confidence"] = 1.0
    db.update_recon_item(recon_item_id, **update_kwargs)
    if hasattr(db, "update_recon_session_counts"):
        db.update_recon_session_counts(str(session.get("id") or "").strip())
    return {
        "reconciliation_session_id": str(session.get("id") or "").strip() or None,
        "reconciliation_item_id": recon_item_id,
        "reconciliation_state": "review",
    }


def _link_related_item_for_non_invoice_resolution(
    db: ClearledgrDB,
    *,
    source_item: Dict[str, Any],
    source_document_type: str,
    resolution: Dict[str, Any],
    related_item: Dict[str, Any],
    actor_id: str,
    organization_id: str,
) -> Dict[str, Any]:
    related_metadata = _parse_json(related_item.get("metadata"))
    entry = _build_linked_finance_document_entry(
        source_item=source_item,
        document_type=source_document_type,
        resolution=resolution,
    )
    _upsert_linked_finance_document(related_metadata, entry=entry, related_item=related_item)
    db.update_ap_item(
        str(related_item.get("id") or "").strip(),
        **_filter_allowed_ap_item_updates(db, {"metadata": related_metadata}),
        _actor_type="user",
        _actor_id=actor_id,
        _source="non_invoice_downstream_linkage",
        _decision_reason=str(resolution.get("outcome") or "linked"),
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": str(related_item.get("id") or "").strip(),
            "event_type": _non_invoice_link_event_type(source_document_type),
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": organization_id,
            "source": "ap_item_non_invoice_related_link",
            "reason": str(resolution.get("outcome") or "linked"),
            "metadata": {
                "linked_ap_item_id": source_item.get("id"),
                "linked_document_type": _normalize_document_type_token(source_document_type),
                "linked_invoice_number": source_item.get("invoice_number"),
                "linked_amount": _safe_float(source_item.get("amount")),
                "linked_currency": source_item.get("currency") or "USD",
                "related_reference": resolution.get("related_reference"),
                "accounting_treatment": resolution.get("accounting_treatment"),
                "finance_effect_summary": related_metadata.get("finance_effect_summary"),
            },
        }
    )
    refreshed_related = _require_item(db, str(related_item.get("id") or "").strip())
    return build_worklist_item(db, refreshed_related)


async def _execute_non_invoice_erp_follow_on(
    db: ClearledgrDB,
    *,
    source_item: Dict[str, Any],
    related_item: Dict[str, Any],
    document_type: str,
    outcome: str,
    actor_id: str,
    organization_id: str,
) -> Optional[Dict[str, Any]]:
    normalized_type = _normalize_document_type_token(document_type)
    normalized_outcome = _normalize_non_invoice_outcome(outcome)
    related_reference = str(
        related_item.get("erp_reference")
        or _parse_json(related_item.get("metadata")).get("erp_reference")
        or ""
    ).strip()
    related_state = str(related_item.get("state") or "").strip().lower()
    target_invoice_number = str(related_item.get("invoice_number") or "").strip() or None

    source_currency = str(source_item.get("currency") or "").strip().upper()
    related_currency = str(related_item.get("currency") or "").strip().upper()
    resolved_currency = source_currency or related_currency or "USD"

    if source_currency and related_currency and source_currency != related_currency:
        return {
            "status": "error",
            "reason": "currency_mismatch",
            "source_currency": source_currency,
            "target_currency": related_currency,
            "source_ap_item_id": str(source_item.get("id") or "").strip(),
            "related_ap_item_id": str(related_item.get("id") or "").strip(),
        }

    if normalized_type == "credit_note" and normalized_outcome == "apply_to_invoice":
        action_type = "apply_credit_note"
        if related_state not in {APState.POSTED_TO_ERP.value, APState.CLOSED.value} or not related_reference:
            result = {
                "status": "skipped",
                "reason": "target_not_posted_to_erp",
                "execution_mode": "pending_target_post",
                "target_erp_reference": related_reference or target_invoice_number,
            }
        else:
            try:
                result = await apply_credit_note_api_first(
                    organization_id=organization_id,
                    target_ap_item_id=str(related_item.get("id") or "").strip(),
                    source_ap_item_id=str(source_item.get("id") or "").strip(),
                    actor_id=actor_id,
                    target_erp_reference=related_reference,
                    target_invoice_number=target_invoice_number,
                    credit_note_number=str(source_item.get("invoice_number") or "").strip() or None,
                    amount=_money_amount(source_item.get("amount")),
                    currency=resolved_currency,
                    note=str(source_item.get("subject") or "").strip() or None,
                    email_id=str(source_item.get("message_id") or "").strip() or None,
                    correlation_id=str(_parse_json(source_item.get("metadata")).get("correlation_id") or "").strip() or None,
                )
            except Exception:
                logger.exception("apply_credit_note_api_first failed for source=%s related=%s",
                                 source_item.get("id"), related_item.get("id"))
                result = {"status": "error", "reason": "internal_error", "error_code": "apply_credit_note_internal_error"}
    elif normalized_type in {"refund", "receipt", "payment"} and normalized_outcome == "link_to_payment":
        action_type = "apply_settlement"
        if related_state not in {APState.POSTED_TO_ERP.value, APState.CLOSED.value} or not related_reference:
            result = {
                "status": "skipped",
                "reason": "target_not_posted_to_erp",
                "execution_mode": "pending_target_post",
                "target_erp_reference": related_reference or target_invoice_number,
            }
        else:
            try:
                result = await apply_settlement_api_first(
                    organization_id=organization_id,
                    target_ap_item_id=str(related_item.get("id") or "").strip(),
                    source_ap_item_id=str(source_item.get("id") or "").strip(),
                    actor_id=actor_id,
                    source_document_type=normalized_type,
                    target_erp_reference=related_reference,
                    target_invoice_number=target_invoice_number,
                    source_reference=str(source_item.get("invoice_number") or "").strip() or None,
                    amount=_money_amount(source_item.get("amount")),
                    currency=resolved_currency,
                    note=str(source_item.get("subject") or "").strip() or None,
                    email_id=str(source_item.get("message_id") or "").strip() or None,
                    correlation_id=str(_parse_json(source_item.get("metadata")).get("correlation_id") or "").strip() or None,
                )
            except Exception:
                logger.exception("apply_settlement_api_first failed for source=%s related=%s",
                                 source_item.get("id"), related_item.get("id"))
                result = {"status": "error", "reason": "internal_error", "error_code": "apply_settlement_internal_error"}
    else:
        logger.info("Skipping ERP follow-on: unrecognized type=%s outcome=%s source=%s",
                     normalized_type, normalized_outcome, source_item.get("id"))
        return None

    return _apply_erp_follow_on_result(
        db,
        source_ap_item_id=str(source_item.get("id") or "").strip(),
        related_ap_item_id=str(related_item.get("id") or "").strip(),
        action_type=action_type,
        result=result,
        actor_id=actor_id,
        organization_id=organization_id,
        item_serializer=build_worklist_item,
    )


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
    threshold_override = raw_gate.get("threshold") if isinstance(raw_gate, dict) else None

    # Prefer first-class column value over metadata blob for field confidences
    raw_fc = payload.get("field_confidences") or metadata.get("field_confidences")
    if isinstance(raw_fc, str):
        try:
            raw_fc = json.loads(raw_fc)
        except (json.JSONDecodeError, TypeError):
            raw_fc = None

    learned_threshold_overrides = None
    learned_profile_id = None
    learned_signal_count = 0
    organization_id = payload.get("organization_id") or metadata.get("organization_id")
    vendor_name = payload.get("vendor_name") or payload.get("vendor")
    if organization_id and vendor_name:
        try:
            from clearledgr.services.correction_learning import get_correction_learning_service

            learned_adjustments = get_correction_learning_service(str(organization_id)).get_extraction_confidence_adjustments(
                vendor_name=vendor_name,
                sender_domain=metadata.get("source_sender_domain") or payload.get("sender"),
                document_type=payload.get("document_type") or metadata.get("document_type") or metadata.get("email_type"),
            )
            learned_threshold_overrides = learned_adjustments.get("threshold_overrides") or None
            learned_profile_id = learned_adjustments.get("profile_id")
            learned_signal_count = int(learned_adjustments.get("signal_count") or 0)
        except Exception:
            learned_threshold_overrides = None
            learned_profile_id = None
            learned_signal_count = 0

    return evaluate_critical_field_confidence(
        overall_confidence=payload.get("confidence"),
        field_values={
            "vendor": payload.get("vendor_name"),
            "amount": payload.get("amount"),
            "invoice_number": payload.get("invoice_number"),
            "due_date": payload.get("due_date"),
        },
        field_confidences=raw_fc,
        threshold=threshold_override,
        vendor_name=payload.get("vendor_name") or payload.get("vendor"),
        sender=payload.get("sender"),
        document_type=payload.get("document_type") or metadata.get("document_type") or metadata.get("email_type"),
        primary_source=metadata.get("primary_source"),
        has_attachment=bool(payload.get("has_attachment") or metadata.get("has_attachment")),
        sender_domain=metadata.get("source_sender_domain"),
        learned_threshold_overrides=learned_threshold_overrides,
        learned_profile_id=learned_profile_id,
        learned_signal_count=learned_signal_count,
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
    if payload.get("finance_effect_review_required"):
        return "review_finance_effects"
    if document_type == "invoice" and payload.get("entity_routing_status") == "needs_review":
        return "resolve_entity_route"
    if state in {APState.NEEDS_INFO.value}:
        followup_next = str(payload.get("followup_next_action") or "").strip().lower()
        return followup_next or "request_info"
    if state in {APState.FAILED_POST.value}:
        return "retry_post"
    if state in {APState.READY_TO_POST.value, APState.APPROVED.value}:
        return "post_to_erp"
    if state in {APState.NEEDS_APPROVAL.value, "pending_approval"}:
        approval_followup = payload.get("approval_followup") if isinstance(payload.get("approval_followup"), dict) else {}
        if approval_followup.get("sla_breached"):
            return "escalate_approval"
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
    if value in {
        "po_missing_reference",
        "po_amount_mismatch",
        "policy_validation_failed",
        "field_conflict",
        "erp_not_connected",
        "erp_not_configured",
        "erp_type_unsupported",
        "posting_blocked",
    }:
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
    "attachment": "Invoice attachment",
    "llm": "Current invoice parse",
    "parser": "Current invoice parse",
    "current_parse": "Current invoice parse",
    "ocr": "Current invoice parse",
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


def _current_field_review_value(payload: Dict[str, Any], field: str) -> Any:
    token = str(field or "").strip().lower()
    if token == "vendor":
        return payload.get("vendor_name") or payload.get("vendor")
    if token == "document_type":
        return payload.get("document_type")
    return payload.get(token)


def _infer_field_review_source(current_value: Any, email_value: Any, attachment_value: Any) -> Optional[str]:
    if current_value not in (None, ""):
        if attachment_value not in (None, "") and current_value == attachment_value:
            return "attachment"
        if email_value not in (None, "") and current_value == email_value:
            return "email"
    return None


def _build_field_review_surface(payload: Dict[str, Any]) -> Dict[str, Any]:
    field_provenance = payload.get("field_provenance") if isinstance(payload.get("field_provenance"), dict) else {}
    field_evidence = payload.get("field_evidence") if isinstance(payload.get("field_evidence"), dict) else {}
    source_conflicts = payload.get("source_conflicts") if isinstance(payload.get("source_conflicts"), list) else []
    confidence_blockers = payload.get("confidence_blockers") if isinstance(payload.get("confidence_blockers"), list) else []
    confidence_gate = payload.get("confidence_gate") if isinstance(payload.get("confidence_gate"), dict) else {}
    field_confidences = (
        payload.get("field_confidences")
        if isinstance(payload.get("field_confidences"), dict)
        else confidence_gate.get("field_confidences")
    )
    if not isinstance(field_confidences, dict):
        field_confidences = {}
    threshold_pct = confidence_gate.get("threshold_pct")
    if threshold_pct is None:
        threshold_pct = 95

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
        provenance_entry = field_provenance.get(field) if isinstance(field_provenance.get(field), dict) else {}
        evidence_entry = field_evidence.get(field) if isinstance(field_evidence.get(field), dict) else {}
        candidate_values = provenance_entry.get("candidates") if isinstance(provenance_entry.get("candidates"), dict) else {}
        confidence_value = blocker.get("confidence") if isinstance(blocker, dict) else None
        if confidence_value in (None, ""):
            confidence_value = field_confidences.get(field)
        confidence_pct = blocker.get("confidence_pct") if isinstance(blocker, dict) else None
        if confidence_pct in (None, "") and confidence_value not in (None, ""):
            try:
                confidence_pct = round(float(confidence_value) * 100)
            except (TypeError, ValueError):
                confidence_pct = None
        blocker_threshold_pct = blocker.get("threshold_pct") if isinstance(blocker, dict) else None
        if blocker_threshold_pct in (None, ""):
            blocker_threshold_pct = threshold_pct
        current_source = (
            str(provenance_entry.get("source") or "").strip().lower()
            or str(evidence_entry.get("source") or "").strip().lower()
            or None
        )
        current_value = provenance_entry.get("value")
        if current_value in (None, ""):
            current_value = evidence_entry.get("selected_value")
        if current_value in (None, ""):
            current_value = _current_field_review_value(payload, field)
        email_value = candidate_values.get("email")
        if email_value in (None, ""):
            email_value = evidence_entry.get("email_value")
        attachment_value = candidate_values.get("attachment")
        if attachment_value in (None, ""):
            attachment_value = evidence_entry.get("attachment_value")
        inferred_source = _infer_field_review_source(current_value, email_value, attachment_value)
        if not current_source:
            current_source = inferred_source
        current_source_label = _field_review_source_label(current_source) if current_source else None
        current_value_display = _format_field_review_value(field, current_value, payload)
        if confidence_pct not in (None, "") and blocker_threshold_pct not in (None, ""):
            paused_reason = (
                f"Review {field_label.lower()} before this invoice moves forward."
            )
            winner_reason = (
                f"Clearledgr read {current_value_display}"
                f"{f' from the {current_source_label.lower()}' if current_source_label else ''}. "
                f"Because {field_label.lower()} is a critical field, a person needs to confirm it before approval continues."
            )
            auto_check_note = (
                f"Auto-pass rule: {blocker_threshold_pct}% minimum. "
                f"This read scored {confidence_pct}%."
            )
        else:
            paused_reason = f"Review {field_label.lower()} before this invoice moves forward."
            winner_reason = (
                f"Clearledgr needs the {field_label.lower()} confirmed before this invoice can continue."
            )
            auto_check_note = None
        blockers.append(
            {
                "kind": "confidence",
                "field": field,
                "field_label": field_label,
                "blocking": True,
                "reason": reason,
                "reason_label": "This field did not clear the automatic check.",
                "paused_reason": paused_reason,
                "current_value": current_value,
                "current_value_display": current_value_display,
                "current_source": current_source,
                "current_source_label": current_source_label,
                "email_value": email_value,
                "email_value_display": _format_field_review_value(field, email_value, payload),
                "attachment_value": attachment_value,
                "attachment_value_display": _format_field_review_value(field, attachment_value, payload),
                "confidence": confidence_value,
                "confidence_pct": confidence_pct,
                "threshold_pct": blocker_threshold_pct,
                "winner_reason": winner_reason,
                "auto_check_note": auto_check_note,
            }
        )
        seen_fields.add(field)
        blocked_fields.append(field)
        blocked_field_labels.append(field_label.lower())

    pause_reason = ""
    if len(blockers) == 1:
        pause_reason = str(blockers[0].get("paused_reason") or "").strip()
    if not pause_reason and blocked_field_labels:
        pause_reason = (
            f"Review {_join_human_list(blocked_field_labels)} "
            f"before this invoice moves forward."
        )
        if any(str(entry.get("kind") or "") == "source_conflict" for entry in blockers):
            pause_reason = (
                f"Workflow paused until {_join_human_list(blocked_field_labels)} "
                f"is confirmed because the email and attachment disagree."
            )
    if not pause_reason and bool(payload.get("requires_field_review")):
        pause_reason = "Review the extracted fields before this invoice moves forward."

    return {
        "field_review_blockers": blockers,
        "blocked_fields": blocked_fields,
        "workflow_paused_reason": pause_reason or None,
    }


_PIPELINE_EXCEPTION_BLOCKER_MAP = {
    "policy_validation_failed": {
        "kind": "exception",
        "chip_label": "Policy block",
        "title": "Policy review required",
        "detail": "Approval or policy rules need review before this invoice can move forward.",
    },
    "po_missing_reference": {
        "kind": "po",
        "chip_label": "PO / GR issue",
        "title": "PO reference missing",
        "detail": "Add or confirm the purchase order reference before continuing.",
    },
    "po_amount_mismatch": {
        "kind": "po",
        "chip_label": "PO / GR issue",
        "title": "PO amount mismatch",
        "detail": "The invoice does not match the linked purchase order or goods receipt.",
    },
    "budget_overrun": {
        "kind": "budget",
        "chip_label": "Budget review",
        "title": "Budget review required",
        "detail": "This invoice exceeds the current budget guardrails.",
    },
    "missing_budget_context": {
        "kind": "budget",
        "chip_label": "Budget review",
        "title": "Budget context missing",
        "detail": "Budget context is missing and needs review before the invoice can continue.",
    },
}


def _humanize_pipeline_token(value: Any, fallback: str) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return fallback
    return token.replace("_", " ").strip().capitalize()


def _build_budget_blocker_detail(summary: Dict[str, Any]) -> str:
    exceeded = _safe_int(summary.get("exceeded_count"), 0)
    critical = _safe_int(summary.get("critical_count"), 0)
    warning = _safe_int(summary.get("warning_count"), 0)
    if exceeded > 0:
        return f"{exceeded} budget check{'s' if exceeded != 1 else ''} exceeded the approved threshold."
    if critical > 0:
        return f"{critical} budget check{'s' if critical != 1 else ''} require immediate review."
    if warning > 0:
        return f"{warning} budget check{'s' if warning != 1 else ''} are close to the limit."
    return "Budget review is required before this invoice can continue."


_FAILED_POST_PAUSE_REASONS = {
    "erp_not_connected": "Connect an ERP before this invoice can be posted.",
    "erp_not_configured": "Finish ERP configuration before this invoice can be posted.",
    "erp_type_unsupported": "This ERP connection does not support invoice posting yet.",
    "posting_blocked": "ERP posting is paused by rollout controls right now.",
}


def _failed_post_pause_reason(item: Dict[str, Any]) -> Optional[str]:
    state = str(item.get("state") or "").strip().lower()
    if state != "failed_post":
        return None
    exception_code = str(item.get("exception_code") or "").strip().lower()
    if exception_code in _FAILED_POST_PAUSE_REASONS:
        return _FAILED_POST_PAUSE_REASONS[exception_code]
    last_error = str(item.get("last_error") or "").strip()
    return last_error or None


def _build_pipeline_blockers(payload: Dict[str, Any], budget_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    blockers: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def append_blocker(
        *,
        kind: str,
        blocker_type: str,
        chip_label: str,
        title: str,
        detail: str,
        field: Optional[str] = None,
        severity: Optional[str] = None,
        code: Optional[str] = None,
    ) -> None:
        normalized_kind = str(kind or "").strip().lower()
        normalized_type = str(blocker_type or "").strip().lower()
        normalized_field = str(field or "").strip().lower()
        dedupe_key = (normalized_kind, normalized_type, normalized_field or str(code or "").strip().lower())
        if not normalized_kind or not normalized_type or dedupe_key in seen:
            return
        seen.add(dedupe_key)
        blockers.append(
            {
                "kind": normalized_kind,
                "type": normalized_type,
                "chip_label": str(chip_label or "").strip() or _humanize_pipeline_token(normalized_kind, "Blocker"),
                "title": str(title or "").strip() or "Blocker",
                "detail": str(detail or "").strip(),
                "field": normalized_field or None,
                "severity": str(severity or "").strip().lower() or None,
                "code": str(code or "").strip().lower() or None,
            }
        )

    field_review_blockers = (
        payload.get("field_review_blockers")
        if isinstance(payload.get("field_review_blockers"), list)
        else []
    )
    for blocker in field_review_blockers:
        if not isinstance(blocker, dict):
            continue
        field = str(blocker.get("field") or "").strip().lower()
        field_label = str(blocker.get("field_label") or _field_review_label(field)).strip() or "Field"
        blocker_kind = str(blocker.get("kind") or "").strip().lower()
        if blocker_kind == "source_conflict":
            email_value = blocker.get("email_value_display") or "Not found"
            attachment_value = blocker.get("attachment_value_display") or "Not found"
            append_blocker(
                kind="confidence",
                blocker_type="source_conflict",
                chip_label="Field review",
                title=f"{field_label} blocked",
                detail=f"Email {email_value} · Attachment {attachment_value}",
                field=field,
                severity="high",
                code=str(blocker.get("reason") or "source_conflict"),
            )
            continue

        confidence_pct = blocker.get("confidence_pct")
        threshold_pct = blocker.get("threshold_pct")
        if confidence_pct not in (None, "") and threshold_pct not in (None, ""):
            detail = (
                f"{field_label} confidence is {confidence_pct}%, below the {threshold_pct}% review threshold."
            )
        else:
            detail = str(blocker.get("reason_label") or "Critical extracted field needs review.").strip()
        append_blocker(
            kind="confidence",
            blocker_type="confidence_review",
            chip_label="Field review",
            title=f"{field_label} needs review",
            detail=detail,
            field=field,
            severity="high",
            code=str(blocker.get("reason") or "critical_field_review_required"),
        )

    state = str(payload.get("state") or "").strip().lower()
    entity_routing_status = str(payload.get("entity_routing_status") or "").strip().lower()
    entity_routing = payload.get("entity_routing") if isinstance(payload.get("entity_routing"), dict) else {}
    entity_reason = str(
        payload.get("entity_route_reason")
        or entity_routing.get("reason")
        or ""
    ).strip()
    if state == APState.NEEDS_APPROVAL.value:
        append_blocker(
            kind="approval",
            blocker_type="approval_waiting",
            chip_label="Approval waiting",
            title="Waiting on approval",
            detail=(
                "This invoice has been routed to an approver and is still waiting."
                if not bool((payload.get("approval_followup") or {}).get("sla_breached"))
                else "Approval is past the follow-up SLA and should be escalated or reassigned."
            ),
        )
    if state == APState.NEEDS_INFO.value:
        append_blocker(
            kind="info",
            blocker_type="needs_info",
            chip_label="Needs info",
            title="Needs follow-up",
            detail="Vendor or field follow-up is still needed before the invoice can continue.",
        )
    if state == APState.FAILED_POST.value:
        append_blocker(
            kind="erp",
            blocker_type="posting_failed",
            chip_label="ERP retry",
            title="ERP posting failed",
            detail="Posting to the ERP needs retry or recovery.",
        )
    if entity_routing_status == "needs_review":
        append_blocker(
            kind="entity",
            blocker_type="entity_review",
            chip_label="Entity review",
            title="Entity route needs review",
            detail=(
                entity_reason
                or "Choose the correct legal entity before approval routing can continue."
            ),
            severity="high",
            code="entity_route_review_required",
        )

    budget_status = str(payload.get("budget_status") or budget_summary.get("status") or "").strip().lower()
    if bool(payload.get("budget_requires_decision")) or budget_status in {"critical", "exceeded"}:
        append_blocker(
            kind="budget",
            blocker_type="budget_review",
            chip_label="Budget review",
            title="Budget review required",
            detail=_build_budget_blocker_detail(budget_summary),
            severity="critical" if budget_status == "exceeded" else "high",
            code=budget_status or "budget_review",
        )

    exception_code = _normalize_exception_code(payload.get("exception_code"))
    if exception_code in {"field_conflict", "field_review_required"}:
        exception_code = None
    if exception_code == "planner_failed":
        if not any(blocker.get("kind") == "confidence" for blocker in blockers):
            append_blocker(
                kind="processing",
                blocker_type="processing_issue",
                chip_label="Processing issue",
                title="Processing issue",
                detail="Invoice processing needs retry or refresh before it can continue.",
                severity="medium",
                code=exception_code,
            )
        exception_code = None

    if exception_code:
        mapped = _PIPELINE_EXCEPTION_BLOCKER_MAP.get(exception_code)
        if mapped:
            append_blocker(
                kind=mapped["kind"],
                blocker_type=exception_code,
                chip_label=mapped["chip_label"],
                title=mapped["title"],
                detail=mapped["detail"],
                severity=payload.get("exception_severity"),
                code=exception_code,
            )
        else:
            append_blocker(
                kind="exception",
                blocker_type=exception_code,
                chip_label="Policy block",
                title=_humanize_pipeline_token(exception_code, "Policy issue"),
                detail="This invoice is blocked and needs manual review before it can continue.",
                severity=payload.get("exception_severity"),
                code=exception_code,
            )

    return blockers


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


def build_worklist_item(
    db: ClearledgrDB,
    item: Dict[str, Any],
    *,
    approval_policy: Optional[Dict[str, Any]] = None,
    organization_settings: Optional[Dict[str, Any]] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    payload = dict(item or {})
    metadata = _parse_json(payload.get("metadata"))
    source_rows = list(sources or [])
    if not source_rows:
        source_rows = db.list_ap_item_sources(payload.get("id"))
    org_settings = (
        organization_settings
        if isinstance(organization_settings, dict)
        else _load_org_settings_for_item(db, payload.get("organization_id"))
    )

    # Preserve legacy behavior when source links do not exist yet.
    if not source_rows:
        if payload.get("thread_id"):
            source_rows.append(
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
            source_rows.append(
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
    payload["source_count"] = max(parsed_meta_source_count, len(source_rows))
    payload["primary_source"] = _build_primary_source(payload, source_rows)
    payload.update(_derive_attachment_summary(payload, metadata, source_rows))
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
    source_conflicts = metadata.get("source_conflicts") if isinstance(metadata.get("source_conflicts"), list) else []
    payload["requires_field_review"] = bool(
        any(isinstance(conflict, dict) and bool(conflict.get("blocking")) for conflict in source_conflicts)
        or confidence_gate.get("requires_field_review")
    )
    payload["requires_extraction_review"] = bool(metadata.get("requires_extraction_review"))
    payload["confidence_blockers"] = confidence_gate.get("confidence_blockers") or []
    payload["field_provenance"] = metadata.get("field_provenance") if isinstance(metadata.get("field_provenance"), dict) else {}
    payload["field_evidence"] = metadata.get("field_evidence") if isinstance(metadata.get("field_evidence"), dict) else {}
    payload["source_conflicts"] = source_conflicts
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
    entity_routing = resolve_entity_routing(metadata, payload, organization_settings=org_settings)
    selected_entity = entity_routing.get("selected") if isinstance(entity_routing.get("selected"), dict) else {}
    payload["entity_routing"] = entity_routing
    payload["entity_routing_status"] = str(entity_routing.get("status") or "").strip() or "not_needed"
    payload["entity_route_reason"] = str(entity_routing.get("reason") or "").strip() or None
    payload["entity_candidates"] = entity_routing.get("candidates") if isinstance(entity_routing.get("candidates"), list) else []
    payload["entity_id"] = (
        payload.get("entity_id")
        or selected_entity.get("entity_id")
        or metadata.get("entity_id")
        or None
    )
    payload["entity_code"] = (
        payload.get("entity_code")
        or selected_entity.get("entity_code")
        or metadata.get("entity_code")
        or None
    )
    payload["entity_name"] = (
        payload.get("entity_name")
        or selected_entity.get("entity_name")
        or metadata.get("entity_name")
        or None
    )
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
    payload["erp_follow_on"] = (
        payload["non_invoice_resolution"].get("erp_follow_on")
        if isinstance(payload["non_invoice_resolution"].get("erp_follow_on"), dict)
        else {}
    )
    payload["non_invoice_accounting_treatment"] = payload["non_invoice_resolution"].get("accounting_treatment")
    payload["non_invoice_downstream_queue"] = payload["non_invoice_resolution"].get("downstream_queue")
    payload["linked_record"] = (
        payload["non_invoice_resolution"].get("linked_record")
        if isinstance(payload["non_invoice_resolution"].get("linked_record"), dict)
        else None
    )
    payload["linked_finance_documents"] = (
        metadata.get("linked_finance_documents")
        if isinstance(metadata.get("linked_finance_documents"), list)
        else []
    )
    payload["linked_finance_summary"] = (
        metadata.get("linked_finance_summary")
        if isinstance(metadata.get("linked_finance_summary"), dict)
        else {}
    )
    payload["vendor_credit_summary"] = (
        metadata.get("vendor_credit_summary")
        if isinstance(metadata.get("vendor_credit_summary"), dict)
        else {}
    )
    payload["cash_application_summary"] = (
        metadata.get("cash_application_summary")
        if isinstance(metadata.get("cash_application_summary"), dict)
        else {}
    )
    payload["finance_effect_summary"] = (
        metadata.get("finance_effect_summary")
        if isinstance(metadata.get("finance_effect_summary"), dict)
        else {}
    )
    payload["finance_effect_blockers"] = (
        metadata.get("finance_effect_blockers")
        if isinstance(metadata.get("finance_effect_blockers"), list)
        else []
    )
    payload["finance_effect_review_required"] = bool(metadata.get("finance_effect_review_required"))
    payload["reconciliation_reference"] = {
        "session_id": payload["non_invoice_resolution"].get("reconciliation_session_id"),
        "item_id": payload["non_invoice_resolution"].get("reconciliation_item_id"),
        "state": payload["non_invoice_resolution"].get("reconciliation_state"),
    }
    payload["non_invoice_review_required"] = bool(
        _normalize_document_type_token(payload.get("document_type")) != "invoice"
        and state_token not in {APState.CLOSED.value, APState.REJECTED.value}
        and not payload["non_invoice_resolution"].get("resolved_at")
    )
    payload["approval_requested_at"] = (
        payload.get("approval_requested_at")
        or metadata.get("approval_requested_at")
        or (payload.get("updated_at") if state_token in {"needs_approval", "pending_approval"} else None)
    )
    approval_followup = _build_approval_followup(
        db,
        payload,
        metadata,
        approval_policy=approval_policy,
    )
    payload["approval_followup"] = approval_followup
    payload["approval_wait_minutes"] = max(0, _safe_int(approval_followup.get("wait_minutes"), 0))
    payload["approval_pending_assignees"] = (
        approval_followup.get("pending_assignees")
        if isinstance(approval_followup.get("pending_assignees"), list)
        else []
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
    if not str(payload.get("workflow_paused_reason") or "").strip():
        payload["workflow_paused_reason"] = _failed_post_pause_reason(payload)

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

    payload["next_action"] = _derive_next_action(payload)
    payload["pipeline_blockers"] = _build_pipeline_blockers(payload, budget_summary)
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


def _classify_vendor_issue(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    state = str(item.get("state") or "").strip().lower()
    workflow_pause_reason = str(
        item.get("workflow_paused_reason")
        or _failed_post_pause_reason(item)
        or ""
    ).strip()
    needs_info_question = str(item.get("needs_info_question") or "").strip()
    field_review_blockers = item.get("field_review_blockers") if isinstance(item.get("field_review_blockers"), list) else []
    entity_routing_status = str(item.get("entity_routing_status") or "").strip().lower()
    entity_route_reason = str(item.get("entity_route_reason") or "").strip()
    exception_code = str(item.get("exception_code") or "").strip().lower()

    if entity_routing_status == "needs_review":
        return {
            "kind": "entity_route",
            "label": "Entity routing",
            "summary": entity_route_reason or "Choose the legal entity before the invoice can continue.",
            "priority": 0,
        }
    if state == "failed_post":
        return {
            "kind": "failed_post",
            "label": "Posting retry",
            "summary": workflow_pause_reason or "ERP posting failed and needs a retry or connector review.",
            "priority": 1,
        }
    if state == "needs_info":
        return {
            "kind": "needs_info",
            "label": "Needs info",
            "summary": needs_info_question or workflow_pause_reason or "Follow up with the vendor or finance team for the missing information.",
            "priority": 2,
        }
    if bool(item.get("requires_field_review")) or field_review_blockers:
        return {
            "kind": "field_review",
            "label": "Field review",
            "summary": workflow_pause_reason or "Resolve the blocked invoice fields before continuing.",
            "priority": 3,
        }
    if exception_code:
        return {
            "kind": "policy_exception",
            "label": "Policy / exception",
            "summary": workflow_pause_reason or f"Resolve the {exception_code.replace('_', ' ')} blocker before continuing.",
            "priority": 4,
        }
    return None


def _summarize_vendor_issue(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    issue = _classify_vendor_issue(item)
    if not issue:
        return None
    return {
        **_summarize_related_item(item),
        "issue_kind": issue["kind"],
        "issue_label": issue["label"],
        "issue_summary": issue["summary"],
        "entity_routing_status": item.get("entity_routing_status"),
        "entity_route_reason": item.get("entity_route_reason"),
        "requires_field_review": bool(item.get("requires_field_review")),
        "next_action": item.get("next_action"),
        "needs_info_question": item.get("needs_info_question"),
    }


def _sort_vendor_issue_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            int((_classify_vendor_issue(item) or {}).get("priority") or 99),
            -_safe_sort_timestamp(item.get("updated_at") or item.get("created_at")),
        ),
    )


def _build_vendor_summary_rows(
    db: ClearledgrDB,
    organization_id: str,
    *,
    search: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    approval_policy = _approval_followup_policy(organization_id)
    organization_settings = _load_org_settings_for_item(db, organization_id)
    raw_rows = db.list_ap_items(organization_id, limit=5000)
    items = build_worklist_items(
        db,
        raw_rows,
        build_item=build_worklist_item,
        approval_policy=approval_policy,
        organization_settings=organization_settings,
    )
    vendor_profiles = (
        db.get_vendor_profiles_bulk(
            organization_id,
            [
                str(item.get("vendor_name") or item.get("vendor") or "Unknown").strip() or "Unknown"
                for item in items
            ],
        )
        if hasattr(db, "get_vendor_profiles_bulk")
        else {}
    )
    vendor_rows: Dict[str, Dict[str, Any]] = {}

    for raw_item, item in zip(raw_rows, items):
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
                "issue_count": 0,
                "issue_kinds": Counter(),
                "top_exception_codes": Counter(),
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
        issue = _classify_vendor_issue(item)
        if issue:
            row["issue_count"] += 1
            row["issue_kinds"][issue["kind"]] += 1
        exception_code = str(
            raw_item.get("exception_code")
            or item.get("exception_code")
            or ""
        ).strip().lower()
        if exception_code:
            row["top_exception_codes"][exception_code] += 1
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
        profile = (
            vendor_profiles.get(vendor_name)
            if isinstance(vendor_profiles, dict)
            else None
        ) or (db.get_vendor_profile(organization_id, vendor_name) if vendor_name else None)
        rows.append(
            {
                "vendor_name": vendor_name,
                "invoice_count": int(row.get("invoice_count") or 0),
                "open_count": int(row.get("open_count") or 0),
                "posted_count": int(row.get("posted_count") or 0),
                "failed_count": int(row.get("failed_count") or 0),
                "approval_count": int(row.get("approval_count") or 0),
                "needs_info_count": int(row.get("needs_info_count") or 0),
                "issue_count": int(row.get("issue_count") or 0),
                "issue_summary": {
                    "field_review": int(Counter(row.get("issue_kinds") or {}).get("field_review") or 0),
                    "entity_route": int(Counter(row.get("issue_kinds") or {}).get("entity_route") or 0),
                    "needs_info": int(Counter(row.get("issue_kinds") or {}).get("needs_info") or 0),
                    "failed_post": int(Counter(row.get("issue_kinds") or {}).get("failed_post") or 0),
                    "policy_exception": int(Counter(row.get("issue_kinds") or {}).get("policy_exception") or 0),
                },
                "total_amount": round(_safe_float(row.get("total_amount")), 2),
                "last_activity_at": row.get("last_activity_at") or None,
                "primary_email": sorted(row.get("sender_emails") or [""])[0] if row.get("sender_emails") else None,
                "sender_emails": sorted(row.get("sender_emails") or [])[:5],
                "top_states": [
                    {"state": state, "count": count}
                    for state, count in Counter(row.get("top_states") or {}).most_common(4)
                ],
                "top_exception_codes": [
                    {"exception_code": code, "count": count}
                    for code, count in Counter(row.get("top_exception_codes") or {}).most_common(3)
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
            int(row.get("issue_count") or 0),
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
    approval_policy = _approval_followup_policy(organization_id)
    organization_settings = _load_org_settings_for_item(db, organization_id)
    raw_vendor_rows = db.get_ap_items_by_vendor(
        organization_id,
        canonical_vendor_name,
        days=max(30, min(days, 365)),
        limit=max(6, min(invoice_limit, 30)),
    )
    items = build_worklist_items(
        db,
        raw_vendor_rows,
        build_item=build_worklist_item,
        approval_policy=approval_policy,
        organization_settings=organization_settings,
    )
    open_issue_items = _sort_vendor_issue_items([item for item in items if _classify_vendor_issue(item)])
    exception_counts = Counter(
        str(raw_item.get("exception_code") or item.get("exception_code") or "").strip().lower()
        for raw_item, item in zip(raw_vendor_rows, items)
        if str(raw_item.get("exception_code") or item.get("exception_code") or "").strip()
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
        "open_issues": [
            issue
            for issue in (
                _summarize_vendor_issue(item)
                for item in open_issue_items[:12]
            )
            if issue
        ],
        "issue_summary": {
            "total": len(open_issue_items),
            "field_review": sum(1 for item in open_issue_items if (_classify_vendor_issue(item) or {}).get("kind") == "field_review"),
            "entity_route": sum(1 for item in open_issue_items if (_classify_vendor_issue(item) or {}).get("kind") == "entity_route"),
            "needs_info": sum(1 for item in open_issue_items if (_classify_vendor_issue(item) or {}).get("kind") == "needs_info"),
            "failed_post": sum(1 for item in open_issue_items if (_classify_vendor_issue(item) or {}).get("kind") == "failed_post"),
            "policy_exception": sum(1 for item in open_issue_items if (_classify_vendor_issue(item) or {}).get("kind") == "policy_exception"),
        },
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
        approval_followup = item.get("approval_followup") if isinstance(item.get("approval_followup"), dict) else {}
        title = "Escalate approval" if approval_followup.get("sla_breached") else "Follow up on approval"
        recommended_slice = "waiting_on_approval"
        requested_at = _parse_iso(item.get("approval_requested_at")) or _parse_iso(item.get("updated_at")) or _parse_iso(item.get("created_at"))
        due_at = requested_at + timedelta(hours=24) if requested_at else None
        pending_assignees = approval_followup.get("pending_assignees") if isinstance(approval_followup.get("pending_assignees"), list) else []
        if approval_followup.get("sla_breached"):
            detail = "Approval is past the follow-up SLA and should be escalated or reassigned."
        elif pending_assignees:
            detail = f"Approval is still outstanding with {', '.join(str(value) for value in pending_assignees[:3])}."
        else:
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
        detail = (
            str(item.get("workflow_paused_reason") or _failed_post_pause_reason(item) or "").strip()
            or "ERP posting failed and should be retried or investigated."
        )
    elif state in {"approved", "ready_to_post"}:
        kind = "post_invoice"
        title = "Post approved invoice"
        recommended_slice = "ready_to_post"
        due_at = _parse_iso(item.get("due_date")) or (_parse_iso(item.get("updated_at")) or now) + timedelta(hours=8)
        detail = "The invoice is approved and ready to move into ERP."
    elif state in {"received", "validated"} and str(item.get("entity_routing_status") or "").strip().lower() == "needs_review":
        kind = "entity_route_review"
        title = "Resolve entity route"
        recommended_slice = "blocked_exception"
        due_at = _parse_iso(item.get("updated_at")) or _parse_iso(item.get("created_at"))
        detail = str(item.get("entity_route_reason") or "").strip() or "Choose the correct legal entity before approval routing can continue."
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
    approval_policy = _approval_followup_policy(organization_id)
    organization_settings = _load_org_settings_for_item(db, organization_id)
    items = build_worklist_items(
        db,
        db.list_ap_items(organization_id, limit=5000),
        build_item=build_worklist_item,
        approval_policy=approval_policy,
        organization_settings=organization_settings,
    )
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


def _resolve_item_for_detail(
    db: ClearledgrDB,
    *,
    organization_id: str,
    ap_item_ref: str,
) -> Dict[str, Any]:
    reference = str(ap_item_ref or "").strip()
    if not reference:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    direct_candidate = db.get_ap_item(reference)
    if direct_candidate and str(direct_candidate.get("organization_id") or "").strip() == str(organization_id or "").strip():
        return direct_candidate

    lookup_methods = (
        getattr(db, "get_ap_item_by_invoice_number", None),
        getattr(db, "get_ap_item_by_erp_reference", None),
        getattr(db, "get_ap_item_by_invoice_key", None),
        getattr(db, "get_ap_item_by_workflow_id", None),
        getattr(db, "get_ap_item_by_thread", None),
        getattr(db, "get_ap_item_by_message_id", None),
    )
    for getter in lookup_methods:
        if not callable(getter):
            continue
        try:
            candidate = getter(organization_id, reference)
        except TypeError:
            continue
        if candidate:
            return candidate

    raise HTTPException(status_code=404, detail="ap_item_not_found")


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


def _field_review_value_equals(left: Any, right: Any) -> bool:
    if left == right:
        return True
    try:
        return abs(float(left) - float(right)) < 1e-9
    except (TypeError, ValueError):
        return str(left or "").strip() == str(right or "").strip()


def _current_field_review_value(item: Dict[str, Any], field_token: str) -> Any:
    if field_token == "vendor":
        return item.get("vendor_name") or item.get("vendor")
    if field_token == "invoice_number":
        return item.get("invoice_number")
    if field_token == "document_type":
        metadata = _parse_json(item.get("metadata"))
        return metadata.get("document_type") or metadata.get("email_type") or item.get("document_type")
    return item.get(field_token)


def _derive_field_review_outcome(
    *,
    item: Dict[str, Any],
    field_token: str,
    blocker: Optional[Dict[str, Any]],
    resolved_value: Any,
    resolved_source: str,
) -> Dict[str, Any]:
    previous_source = str((blocker or {}).get("winning_source") or "").strip().lower()
    previous_value = (blocker or {}).get("winning_value")
    current_value = _current_field_review_value(item, field_token)
    tags = set()

    if resolved_source == "email":
        tags.add("resolved_with_email")
    elif resolved_source == "attachment":
        tags.add("resolved_with_attachment")
    elif resolved_source == "manual":
        tags.add("manual_entry")

    if previous_source and previous_source != resolved_source:
        tags.add("rejected_source")

    if resolved_source == "manual":
        outcome_type = "corrected"
    elif previous_source:
        outcome_type = (
            "confirmed_correct"
            if previous_source == resolved_source and _field_review_value_equals(previous_value, resolved_value)
            else "corrected"
        )
    else:
        outcome_type = (
            "confirmed_correct"
            if _field_review_value_equals(current_value, resolved_value)
            else "corrected"
        )

    tags.add(outcome_type)
    return {
        "outcome_type": outcome_type,
        "outcome_tags": sorted(tags),
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
    review_outcome = _derive_field_review_outcome(
        item=item,
        field_token=normalized_field,
        blocker=blocker,
        resolved_value=resolved_value,
        resolved_source=normalized_source,
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
        from clearledgr.services.correction_learning import get_correction_learning_service

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
        confidence_profile_id = (
            ((worklist_item.get("confidence_gate") or {}).get("profile_id"))
            or ((worklist_item.get("confidence_gate") or {}).get("learned_profile_id"))
        )
        truth_context = _build_operator_truth_context(
            db,
            item=item,
            metadata=metadata,
            field=normalized_field,
            selected_source=normalized_source,
            blocker=blocker,
            expected_fields=expected_fields,
        )
        truth_context["confidence_profile_id"] = confidence_profile_id
        learning_svc = get_correction_learning_service(str(item.get("organization_id") or organization_id or "default"))
        learning_svc.record_correction(
            correction_type=normalized_field,
            original_value=blocker.get("selected_value") if isinstance(blocker, dict) else item.get(normalized_field),
            corrected_value=resolved_value,
            context=truth_context,
            user_id=actor_id,
            invoice_id=item.get("thread_id") or item.get("message_id"),
            feedback=str(request.note or "").strip() or None,
        )
        learning_svc.record_review_outcome(
            field_name=normalized_field,
            outcome_type=review_outcome["outcome_type"],
            context=truth_context,
            user_id=actor_id,
            selected_source=normalized_source,
            outcome_tags=review_outcome["outcome_tags"],
            created_at=preview["resolved_at"],
        )
    except Exception:
        logger.exception("field review correction learning capture failed for %s", ap_item_id)

    refreshed = _require_item(db, ap_item_id)
    normalized_item = build_worklist_item(db, refreshed)
    auto_resume_result: Optional[Dict[str, Any]] = None
    auto_resumed = False

    if request.auto_resume and _should_auto_resume_after_field_resolution(normalized_item):
        runtime = _finance_agent_runtime_cls()(
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


from clearledgr.api.ap_items_action_routes import (
    router as _action_router,
    bulk_resolve_ap_item_field_review,
    link_ap_item_source,
    merge_ap_items,
    resubmit_rejected_item,
    resolve_ap_item_entity_route,
    resolve_ap_item_field_review,
    resolve_non_invoice_review,
    retry_erp_post,
    split_ap_item,
)
from clearledgr.api.ap_items_read_routes import (
    router as _read_router,
    get_ap_aggregation_metrics,
    get_ap_item_audit,
    get_ap_item_context,
    get_ap_item_detail,
    get_ap_item_sources,
    get_upcoming_ap_tasks,
    get_vendor_directory,
    get_vendor_record,
)

router.include_router(_read_router)
router.include_router(_action_router)
