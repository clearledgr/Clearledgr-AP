"""Shared ERP follow-on result persistence helpers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException

from clearledgr.core.ap_states import APState
from clearledgr.core.database import ClearledgrDB


_ERP_FOLLOW_ON_APPLIED_STATUSES = {"applied", "success", "completed", "already_applied"}
_ERP_FOLLOW_ON_PENDING_STATUSES = {
    "pending",
    "queued",
    "requested",
    "pending_browser_fallback",
    "pending_target_post",
}


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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_document_type_token(raw: Any) -> str:
    """Normalize document type using the canonical routing table."""
    from clearledgr.services.document_routing import get_route
    token = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not token:
        return "invoice"
    return get_route(token).type


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


def _require_item(db: ClearledgrDB, ap_item_id: str) -> Dict[str, Any]:
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    return item


def _summarize_linked_finance_documents(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "total_count": len(entries),
        "credit_note_count": 0,
        "credit_note_total": 0.0,
        "refund_count": 0,
        "refund_total": 0.0,
        "payment_count": 0,
        "payment_total": 0.0,
        "receipt_count": 0,
        "receipt_total": 0.0,
        "latest_linked_at": None,
    }
    latest_ts = 0.0
    latest_value: Optional[str] = None
    for entry in entries:
        document_type = _normalize_document_type_token(entry.get("document_type"))
        amount = abs(_safe_float(entry.get("amount")))
        linked_at = str(entry.get("linked_at") or "").strip()
        parsed = _parse_iso(linked_at)
        if parsed and parsed.timestamp() >= latest_ts:
            latest_ts = parsed.timestamp()
            latest_value = linked_at
        if document_type == "credit_note":
            summary["credit_note_count"] += 1
            summary["credit_note_total"] = round(summary["credit_note_total"] + amount, 2)
        elif document_type == "refund":
            summary["refund_count"] += 1
            summary["refund_total"] = round(summary["refund_total"] + amount, 2)
        elif document_type == "payment":
            summary["payment_count"] += 1
            summary["payment_total"] = round(summary["payment_total"] + amount, 2)
        elif document_type == "receipt":
            summary["receipt_count"] += 1
            summary["receipt_total"] = round(summary["receipt_total"] + amount, 2)
    summary["latest_linked_at"] = latest_value
    return summary


def _money_amount(value: Any) -> float:
    return round(abs(_safe_float(value)), 2)


def _related_item_document_type(item: Dict[str, Any]) -> str:
    metadata = _parse_json(item.get("metadata"))
    return _normalize_document_type_token(
        item.get("document_type")
        or item.get("email_type")
        or metadata.get("document_type")
        or metadata.get("email_type")
        or "invoice"
    )


def _finance_effect_review_blockers(
    *,
    related_document_type: str,
    related_state: str,
    original_amount: float,
    applied_credit_total: float,
    gross_cash_out_total: float,
    refund_total: float,
    over_credit_amount: float,
    overpayment_amount: float,
    credit_erp_status: str,
    cash_erp_status: str,
) -> List[Dict[str, Any]]:
    blockers: List[Dict[str, Any]] = []
    is_active_invoice = (
        related_document_type == "invoice"
        and related_state not in {
            APState.POSTED_TO_ERP.value,
            APState.CLOSED.value,
            APState.REJECTED.value,
        }
    )

    def _push(code: str, detail: str) -> None:
        blockers.append({"code": code, "detail": detail})

    if original_amount <= 0 and (applied_credit_total > 0 or gross_cash_out_total > 0 or refund_total > 0):
        _push(
            "linked_finance_target_amount_missing",
            "Linked finance documents cannot be applied automatically because the target record amount is missing or zero.",
        )
    if related_document_type != "invoice" and (applied_credit_total > 0 or gross_cash_out_total > 0 or refund_total > 0):
        _push(
            "linked_finance_target_not_invoice",
            "Linked finance effects point at a non-invoice record and need operator review before AP automation continues.",
        )
    if is_active_invoice and applied_credit_total > 0:
        if credit_erp_status in _ERP_FOLLOW_ON_PENDING_STATUSES:
            _push(
                "linked_credit_application_pending",
                "The linked credit note is waiting on downstream ERP application before invoice routing or posting can continue.",
            )
        elif credit_erp_status not in _ERP_FOLLOW_ON_APPLIED_STATUSES:
            _push(
                "linked_credit_adjustment_present",
                "A linked credit note changes the payable amount and should be reviewed before invoice routing or posting.",
            )
    if is_active_invoice and (gross_cash_out_total > 0 or refund_total > 0):
        if cash_erp_status in _ERP_FOLLOW_ON_PENDING_STATUSES:
            _push(
                "linked_settlement_application_pending",
                "The linked payment, receipt, or refund is waiting on downstream ERP settlement before invoice routing or posting can continue.",
            )
        elif cash_erp_status not in _ERP_FOLLOW_ON_APPLIED_STATUSES:
            _push(
                "linked_cash_application_present",
                "A linked payment, receipt, or refund changes settlement context and should be reviewed before invoice routing or posting.",
            )
    if over_credit_amount > 0:
        _push(
            "linked_over_credit",
            "Linked credits exceed the target record amount.",
        )
    if overpayment_amount > 0:
        _push(
            "linked_overpayment",
            "Linked cash applications exceed the remaining payable balance.",
        )
    if refund_total > gross_cash_out_total and refund_total > 0:
        _push(
            "linked_refund_exceeds_cash_out",
            "Linked refunds exceed the related cash-out evidence on the target record.",
        )
    return blockers


def _build_finance_effect_summary(
    *,
    related_item: Dict[str, Any],
    entries: List[Dict[str, Any]],
    summary: Dict[str, Any],
    existing_vendor_credit_summary: Optional[Dict[str, Any]] = None,
    existing_cash_application_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    related_document_type = _related_item_document_type(related_item)
    related_state = str(related_item.get("state") or "").strip().lower()
    currency = str(related_item.get("currency") or "USD").strip().upper() or "USD"
    original_amount = _money_amount(related_item.get("amount"))
    applied_credit_total = round(_safe_float(summary.get("credit_note_total")), 2)
    payment_confirmation_total = round(_safe_float(summary.get("payment_total")), 2)
    receipt_total = round(_safe_float(summary.get("receipt_total")), 2)
    refund_total = round(_safe_float(summary.get("refund_total")), 2)
    gross_cash_out_total = round(payment_confirmation_total + receipt_total, 2)
    net_cash_applied_total = round(gross_cash_out_total - refund_total, 2)
    settled_cash_total = round(max(net_cash_applied_total, 0.0), 2)
    remaining_payable_amount = round(max(original_amount - applied_credit_total, 0.0), 2)
    over_credit_amount = round(max(applied_credit_total - original_amount, 0.0), 2)
    remaining_balance_amount = round(max(remaining_payable_amount - settled_cash_total, 0.0), 2)
    overpayment_amount = round(max(settled_cash_total - remaining_payable_amount, 0.0), 2)

    if applied_credit_total <= 0:
        credit_application_state = "none"
    elif over_credit_amount > 0:
        credit_application_state = "over_credited"
    elif remaining_payable_amount == 0:
        credit_application_state = "fully_credited"
    else:
        credit_application_state = "partial"

    if refund_total > gross_cash_out_total and refund_total > 0:
        settlement_state = "refund_mismatch"
    elif remaining_balance_amount > 0:
        settlement_state = "partially_settled" if net_cash_applied_total > 0 else "open"
    elif overpayment_amount > 0:
        settlement_state = "overpaid"
    elif remaining_payable_amount == 0 and applied_credit_total > 0 and net_cash_applied_total <= 0:
        settlement_state = "credited"
    elif net_cash_applied_total > 0:
        settlement_state = "settled"
    else:
        settlement_state = "open"

    credit_note_ids: List[str] = []
    refund_ids: List[str] = []
    payment_ids: List[str] = []
    receipt_ids: List[str] = []
    for entry in entries:
        source_id = str(entry.get("source_ap_item_id") or "").strip()
        if not source_id:
            continue
        document_type = _normalize_document_type_token(entry.get("document_type"))
        if document_type == "credit_note" and source_id not in credit_note_ids:
            credit_note_ids.append(source_id)
        elif document_type == "refund" and source_id not in refund_ids:
            refund_ids.append(source_id)
        elif document_type == "payment" and source_id not in payment_ids:
            payment_ids.append(source_id)
        elif document_type == "receipt" and source_id not in receipt_ids:
            receipt_ids.append(source_id)

    vendor_credit_summary = (
        existing_vendor_credit_summary
        if isinstance(existing_vendor_credit_summary, dict)
        else {}
    )
    cash_application_summary = (
        existing_cash_application_summary
        if isinstance(existing_cash_application_summary, dict)
        else {}
    )
    credit_erp_status = str(vendor_credit_summary.get("erp_application_status") or "").strip().lower()
    cash_erp_status = str(cash_application_summary.get("erp_settlement_status") or "").strip().lower()
    if not credit_erp_status:
        credit_erp_status = "not_requested" if applied_credit_total > 0 else "not_applicable"
    if not cash_erp_status:
        cash_erp_status = (
            "not_requested"
            if gross_cash_out_total > 0 or refund_total > 0
            else "not_applicable"
        )

    blockers = _finance_effect_review_blockers(
        related_document_type=related_document_type,
        related_state=related_state,
        original_amount=original_amount,
        applied_credit_total=applied_credit_total,
        gross_cash_out_total=gross_cash_out_total,
        refund_total=refund_total,
        over_credit_amount=over_credit_amount,
        overpayment_amount=overpayment_amount,
        credit_erp_status=credit_erp_status,
        cash_erp_status=cash_erp_status,
    )

    return {
        "related_document_type": related_document_type,
        "related_state": related_state,
        "currency": currency,
        "original_amount": original_amount,
        "applied_credit_total": applied_credit_total,
        "remaining_payable_amount": remaining_payable_amount,
        "over_credit_amount": over_credit_amount,
        "credit_application_state": credit_application_state,
        "payment_confirmation_total": payment_confirmation_total,
        "receipt_total": receipt_total,
        "refund_total": refund_total,
        "gross_cash_out_total": gross_cash_out_total,
        "net_cash_applied_total": net_cash_applied_total,
        "remaining_balance_amount": remaining_balance_amount,
        "overpayment_amount": overpayment_amount,
        "settlement_state": settlement_state,
        "credit_note_ids": credit_note_ids,
        "refund_ids": refund_ids,
        "payment_ids": payment_ids,
        "receipt_ids": receipt_ids,
        "latest_linked_at": summary.get("latest_linked_at"),
        "credit_erp_application_status": credit_erp_status,
        "credit_erp_application_mode": vendor_credit_summary.get("erp_application_mode"),
        "credit_erp_application_reference": vendor_credit_summary.get("erp_application_reference"),
        "cash_erp_settlement_status": cash_erp_status,
        "cash_erp_settlement_mode": cash_application_summary.get("erp_settlement_mode"),
        "cash_erp_settlement_reference": cash_application_summary.get("erp_settlement_reference"),
        "blocked_reason_codes": [str(blocker.get("code") or "").strip() for blocker in blockers if str(blocker.get("code") or "").strip()],
        "blockers": blockers,
        "requires_review": bool(blockers),
    }


def _refresh_linked_finance_metadata(
    metadata: Dict[str, Any],
    *,
    related_item: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    existing_entries = metadata.get("linked_finance_documents")
    entries = list(existing_entries) if isinstance(existing_entries, list) else []
    summary = _summarize_linked_finance_documents(entries)
    metadata["linked_finance_documents"] = entries
    metadata["linked_finance_summary"] = summary

    existing_vendor_credit = (
        metadata.get("vendor_credit_summary")
        if isinstance(metadata.get("vendor_credit_summary"), dict)
        else {}
    )
    existing_cash_application = (
        metadata.get("cash_application_summary")
        if isinstance(metadata.get("cash_application_summary"), dict)
        else {}
    )
    finance_effect_summary = (
        _build_finance_effect_summary(
            related_item=related_item,
            entries=entries,
            summary=summary,
            existing_vendor_credit_summary=existing_vendor_credit,
            existing_cash_application_summary=existing_cash_application,
        )
        if related_item
        else {}
    )
    if finance_effect_summary:
        metadata["finance_effect_summary"] = finance_effect_summary
        metadata["finance_effect_blockers"] = finance_effect_summary.get("blockers") or []
        metadata["finance_effect_review_required"] = bool(finance_effect_summary.get("requires_review"))

    vendor_credit_summary = dict(existing_vendor_credit)
    vendor_credit_summary.update(
        {
            "count": summary["credit_note_count"],
            "applied_total": summary["credit_note_total"],
            "latest_linked_at": summary["latest_linked_at"],
            "application_state": finance_effect_summary.get("credit_application_state"),
            "remaining_payable_amount": finance_effect_summary.get("remaining_payable_amount"),
            "over_credit_amount": finance_effect_summary.get("over_credit_amount"),
        }
    )
    metadata["vendor_credit_summary"] = vendor_credit_summary

    cash_application_summary = dict(existing_cash_application)
    cash_application_summary.update(
        {
            "refund_count": summary["refund_count"],
            "refund_total": summary["refund_total"],
            "payment_count": summary["payment_count"],
            "payment_total": summary["payment_total"],
            "receipt_count": summary["receipt_count"],
            "receipt_total": summary["receipt_total"],
            "latest_linked_at": summary["latest_linked_at"],
            "gross_cash_out_total": finance_effect_summary.get("gross_cash_out_total"),
            "net_cash_applied_total": finance_effect_summary.get("net_cash_applied_total"),
            "remaining_balance_amount": finance_effect_summary.get("remaining_balance_amount"),
            "overpayment_amount": finance_effect_summary.get("overpayment_amount"),
            "settlement_state": finance_effect_summary.get("settlement_state"),
        }
    )
    metadata["cash_application_summary"] = cash_application_summary
    return metadata


def _normalize_erp_follow_on_status(result: Dict[str, Any]) -> str:
    status = str(result.get("status") or "").strip().lower()
    execution_mode = str(result.get("execution_mode") or "").strip().lower()
    reason = str(result.get("reason") or "").strip().lower()
    if status in {"success", "completed", "already_applied"}:
        return "applied"
    if status == "pending_browser_fallback":
        return "pending_browser_fallback"
    if execution_mode == "pending_target_post" or reason == "target_not_posted_to_erp":
        return "pending_target_post"
    if status == "blocked":
        return "blocked"
    if status == "skipped":
        return "skipped"
    return "failed"


def _apply_erp_follow_on_result(
    db: ClearledgrDB,
    *,
    source_ap_item_id: str,
    related_ap_item_id: str,
    action_type: str,
    result: Dict[str, Any],
    actor_id: str,
    organization_id: str,
    item_serializer: Optional[Callable[[ClearledgrDB, Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    source_item = _require_item(db, source_ap_item_id)
    related_item = _require_item(db, related_ap_item_id)
    source_metadata = _parse_json(source_item.get("metadata"))
    related_metadata = _parse_json(related_item.get("metadata"))
    resolution = source_metadata.get("non_invoice_resolution")
    if not isinstance(resolution, dict):
        resolution = {}

    normalized_status = _normalize_erp_follow_on_status(result)
    fallback = result.get("fallback") if isinstance(result.get("fallback"), dict) else {}
    follow_on = {
        "action_type": action_type,
        "status": normalized_status,
        "raw_status": str(result.get("status") or "").strip() or None,
        "execution_mode": str(result.get("execution_mode") or "").strip() or None,
        "erp_type": str(result.get("erp_type") or result.get("erp") or "").strip() or None,
        "erp_reference": (
            str(result.get("erp_reference") or "").strip()
            or str(fallback.get("erp_reference") or "").strip()
            or None
        ),
        "target_erp_reference": str(result.get("target_erp_reference") or "").strip() or None,
        "reason": str(result.get("reason") or "").strip() or None,
        "error_code": str(result.get("error_code") or "").strip() or None,
        "error_message": str(result.get("error_message") or "").strip() or None,
        "session_id": str(fallback.get("session_id") or "").strip() or None,
        "macro_name": str(fallback.get("macro_name") or "").strip() or None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    resolution["erp_follow_on"] = follow_on
    source_metadata["non_invoice_resolution"] = resolution

    if action_type == "apply_credit_note":
        vendor_credit_summary = (
            related_metadata.get("vendor_credit_summary")
            if isinstance(related_metadata.get("vendor_credit_summary"), dict)
            else {}
        )
        vendor_credit_summary.update(
            {
                "erp_application_status": normalized_status,
                "erp_application_mode": follow_on.get("execution_mode"),
                "erp_application_reference": follow_on.get("erp_reference"),
                "erp_application_session_id": follow_on.get("session_id"),
                "erp_last_applied_at": follow_on.get("updated_at"),
                "erp_pending_reason": follow_on.get("reason"),
                "erp_target_reference": follow_on.get("target_erp_reference"),
            }
        )
        related_metadata["vendor_credit_summary"] = vendor_credit_summary
        event_prefix = "erp_credit_application"
    else:
        cash_application_summary = (
            related_metadata.get("cash_application_summary")
            if isinstance(related_metadata.get("cash_application_summary"), dict)
            else {}
        )
        cash_application_summary.update(
            {
                "erp_settlement_status": normalized_status,
                "erp_settlement_mode": follow_on.get("execution_mode"),
                "erp_settlement_reference": follow_on.get("erp_reference"),
                "erp_settlement_session_id": follow_on.get("session_id"),
                "erp_last_settled_at": follow_on.get("updated_at"),
                "erp_pending_reason": follow_on.get("reason"),
                "erp_target_reference": follow_on.get("target_erp_reference"),
            }
        )
        related_metadata["cash_application_summary"] = cash_application_summary
        event_prefix = "erp_settlement_application"

    refreshed_related_metadata = _refresh_linked_finance_metadata(
        related_metadata,
        related_item={**related_item, "metadata": related_metadata},
    )

    db.update_ap_item(
        source_ap_item_id,
        **_filter_allowed_ap_item_updates(db, {"metadata": source_metadata}),
        _actor_type="user",
        _actor_id=actor_id,
        _source="non_invoice_erp_follow_on",
        _decision_reason=action_type,
    )
    db.update_ap_item(
        related_ap_item_id,
        **_filter_allowed_ap_item_updates(db, {"metadata": refreshed_related_metadata}),
        _actor_type="user",
        _actor_id=actor_id,
        _source="non_invoice_erp_follow_on",
        _decision_reason=action_type,
    )

    event_suffix = (
        "completed"
        if normalized_status == "applied"
        else "requested"
        if normalized_status in _ERP_FOLLOW_ON_PENDING_STATUSES
        else "failed"
    )
    for ap_item_id in (source_ap_item_id, related_ap_item_id):
        db.append_ap_audit_event(
            {
                "ap_item_id": ap_item_id,
                "event_type": f"{event_prefix}_{event_suffix}",
                "actor_type": "user",
                "actor_id": actor_id,
                "organization_id": organization_id,
                "source": "ap_item_non_invoice_erp_follow_on",
                "reason": normalized_status,
                "metadata": {
                    "action_type": action_type,
                    "source_ap_item_id": source_ap_item_id,
                    "related_ap_item_id": related_ap_item_id,
                    "erp_follow_on": follow_on,
                },
            }
        )

    refreshed_source = _require_item(db, source_ap_item_id)
    refreshed_related = _require_item(db, related_ap_item_id)
    if item_serializer is not None:
        refreshed_source = item_serializer(db, refreshed_source)
        refreshed_related = item_serializer(db, refreshed_related)
    return {
        "source_item": refreshed_source,
        "related_item": refreshed_related,
        "follow_on": follow_on,
    }
