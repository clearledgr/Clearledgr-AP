"""AP vendor analysis helpers — extracted from ap_item_service.py.

Contains vendor summary, vendor issue classification, vendor detail builders,
and related helper functions.

Every public name that previously lived in ``ap_item_service`` is
re-exported from there so existing callers are unaffected.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from clearledgr.core.database import ClearledgrDB
from clearledgr.core.ap_states import APState
from clearledgr.services.ap_projection import build_worklist_items
from clearledgr.services.policy_compliance import get_approval_automation_policy

# Lazy import helpers — these are imported at call-time from ap_item_service
# to avoid circular imports.  The functions themselves are defined in
# ap_field_review.py and re-exported through ap_item_service.
from clearledgr.services.ap_field_review import (
    _parse_json,
    _normalize_document_type_token,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared numeric helpers (duplicated here to avoid circular import)
# ---------------------------------------------------------------------------

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_sort_timestamp(value: Any) -> float:
    parsed = _parse_iso(value)
    return parsed.timestamp() if parsed else 0.0


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


# ---------------------------------------------------------------------------
# AP state sets
# ---------------------------------------------------------------------------

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


def _is_open_ap_state(state: Any) -> bool:
    return str(state or "").strip().lower() in OPEN_AP_STATES


# ---------------------------------------------------------------------------
# Related-item summarisation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Vendor issue classification
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Org settings loader (duplicated to avoid circular import)
# ---------------------------------------------------------------------------

def _load_org_settings_for_item(db: ClearledgrDB, organization_id: Any) -> Dict[str, Any]:
    org_id = str(organization_id or "").strip()
    if not org_id or not hasattr(db, "get_organization"):
        return {}
    org = db.get_organization(org_id) or {}
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        import json
        try:
            settings = json.loads(settings)
        except Exception:
            settings = {}
    return settings if isinstance(settings, dict) else {}


def _approval_followup_policy(organization_id: str) -> Dict[str, Any]:
    return get_approval_automation_policy(organization_id=organization_id or "default")


# ---------------------------------------------------------------------------
# Vendor summary / detail builders
# ---------------------------------------------------------------------------

def _build_vendor_summary_rows(
    db: ClearledgrDB,
    organization_id: str,
    *,
    search: str = "",
    limit: int = 50,
    build_worklist_item=None,
) -> List[Dict[str, Any]]:
    """Build vendor summary rows.

    ``build_worklist_item`` is passed as a callable to avoid a circular import
    back to ``ap_item_service``.
    """
    if build_worklist_item is None:
        from clearledgr.services.ap_item_service import build_worklist_item

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
    build_worklist_item=None,
) -> Dict[str, Any]:
    """Build the full vendor detail payload.

    ``build_worklist_item`` is passed as a callable to avoid a circular import.
    """
    if build_worklist_item is None:
        from clearledgr.services.ap_item_service import build_worklist_item

    summary_rows = _build_vendor_summary_rows(
        db, organization_id, search=vendor_name, limit=200,
        build_worklist_item=build_worklist_item,
    )
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
