"""AP field review helpers — extracted from ap_item_service.py.

Contains all ``_build_field_review_*``, ``_derive_field_review_*``, and
``_field_review_*`` helpers plus supporting normalisation and coercion
functions used by the field-review workflow.

Every public name that previously lived in ``ap_item_service`` is
re-exported from there so existing callers are unaffected.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from clearledgr.core.database import ClearledgrDB

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared low-level helpers (also used by the main module)
# ---------------------------------------------------------------------------

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


def _coerce_optional_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Mutable-field set
# ---------------------------------------------------------------------------

_FIELD_REVIEW_MUTABLE_FIELDS = {
    "amount",
    "currency",
    "invoice_number",
    "vendor",
    "due_date",
    "document_type",
}


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

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
    """Normalize document type using the canonical routing table."""
    from clearledgr.services.document_routing import get_route
    token = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not token:
        return "invoice"
    return get_route(token).type


def _get_conflict_field(raw: Any) -> str:
    if isinstance(raw, dict):
        return _normalize_field_review_field(raw.get("field") or raw.get("code"))
    if isinstance(raw, str):
        return _normalize_field_review_field(raw)
    return ""


# ---------------------------------------------------------------------------
# Source / value resolution helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Label / display helpers
# ---------------------------------------------------------------------------

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


def _current_field_review_value_from_payload(payload: Dict[str, Any], field: str) -> Any:
    """Return the *current* field value from a worklist-style payload dict.

    NOTE: There is a second ``_current_field_review_value`` helper (below)
    that operates on a raw DB item dict.  The two exist because the original
    module had two functions with the same name at different scopes.
    """
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


# ---------------------------------------------------------------------------
# Build the field-review surface payload
# ---------------------------------------------------------------------------

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
            current_value = _current_field_review_value_from_payload(payload, field)
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


# ---------------------------------------------------------------------------
# Preview / outcome helpers
# ---------------------------------------------------------------------------

def _field_review_value_equals(left: Any, right: Any) -> bool:
    if left == right:
        return True
    try:
        return abs(float(left) - float(right)) < 1e-9
    except (TypeError, ValueError):
        return str(left or "").strip() == str(right or "").strip()


def _current_field_review_value(item: Dict[str, Any], field_token: str) -> Any:
    """Return the current value of *field_token* from a raw DB item dict."""
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
