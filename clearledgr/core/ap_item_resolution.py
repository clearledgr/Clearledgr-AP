"""Shared AP item resolution helpers used across Gmail, Slack, Teams, and runtime paths."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple


def _parse_json_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def resolve_ap_item_reference(
    db: Any,
    organization_id: str,
    reference_id: str,
    *,
    allow_foreign_id: bool = False,
) -> Optional[Dict[str, Any]]:
    org_id = str(organization_id or "default").strip() or "default"
    ref = str(reference_id or "").strip()
    if not ref:
        return None

    item: Optional[Dict[str, Any]] = None
    getter = getattr(db, "get_ap_item", None)
    if callable(getter):
        candidate = getter(ref)
        if candidate:
            candidate_org = str(candidate.get("organization_id") or org_id).strip() or org_id
            if candidate_org == org_id or allow_foreign_id:
                item = candidate

    if not item and hasattr(db, "get_ap_item_by_thread"):
        item = db.get_ap_item_by_thread(org_id, ref)
    if not item and hasattr(db, "get_ap_item_by_message_id"):
        item = db.get_ap_item_by_message_id(org_id, ref)

    if not item:
        return None
    item_org = str(item.get("organization_id") or org_id).strip() or org_id
    if item_org != org_id and not allow_foreign_id:
        return None
    return item


def resolve_ap_context(
    db: Any,
    organization_id: str,
    reference_id: str,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    org_id = str(organization_id or "default").strip() or "default"
    ref = str(reference_id or "").strip()
    invoice_row: Optional[Dict[str, Any]] = None

    if ref and hasattr(db, "get_invoice_status"):
        try:
            candidate = db.get_invoice_status(ref)
            invoice_row = candidate if isinstance(candidate, dict) else None
        except Exception:
            invoice_row = None

    if invoice_row and invoice_row.get("organization_id"):
        org_id = str(invoice_row.get("organization_id") or org_id).strip() or org_id

    item = resolve_ap_item_reference(db, org_id, ref)
    if not item and invoice_row:
        fallback_item_id = str(invoice_row.get("ap_item_id") or "").strip()
        if fallback_item_id:
            item = resolve_ap_item_reference(db, org_id, fallback_item_id)

    return org_id, item


def resolve_ap_correlation_id(
    db: Any,
    organization_id: str,
    *,
    ap_item: Optional[Dict[str, Any]] = None,
    ap_item_id: Optional[str] = None,
    reference_id: Optional[str] = None,
) -> Optional[str]:
    row = ap_item
    if row is None and ap_item_id:
        row = resolve_ap_item_reference(db, organization_id, ap_item_id)
    if row is None and reference_id:
        org_id, resolved = resolve_ap_context(db, organization_id, reference_id)
        if resolved is not None:
            row = resolved
        elif hasattr(db, "get_invoice_status"):
            try:
                invoice_row = db.get_invoice_status(str(reference_id or "").strip())
            except Exception:
                invoice_row = None
            row = invoice_row if isinstance(invoice_row, dict) else None
            organization_id = org_id

    metadata = _parse_json_dict((row or {}).get("metadata"))
    correlation_id = str((row or {}).get("correlation_id") or metadata.get("correlation_id") or "").strip()
    return correlation_id or None
