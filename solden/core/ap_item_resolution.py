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
    org_id = str(organization_id or "").strip()
    ref = str(reference_id or "").strip()
    if not ref or not org_id:
        return None

    item: Optional[Dict[str, Any]] = None
    getter = getattr(db, "get_ap_item", None)
    if callable(getter):
        candidate = getter(ref)
        if candidate:
            candidate_org = str(candidate.get("organization_id") or "").strip()
            if candidate_org == org_id or allow_foreign_id:
                item = candidate

    if not item and hasattr(db, "get_ap_item_by_thread"):
        item = db.get_ap_item_by_thread(org_id, ref)
    if not item and hasattr(db, "get_ap_item_by_message_id"):
        item = db.get_ap_item_by_message_id(org_id, ref)

    if not item:
        return None
    item_org = str(item.get("organization_id") or "").strip()
    if item_org != org_id and not allow_foreign_id:
        return None
    return item


def resolve_ap_context(
    db: Any,
    organization_id: str,
    reference_id: str,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Resolve an AP item by (org, reference). Org-scoped at every step.

    Pre-fix this called ``db.get_invoice_status(ref)`` WITHOUT passing
    organization_id, then ADOPTED ``invoice_row.organization_id`` as
    the resolved org. A thread_id collision across tenants would
    silently swap the caller's intended org for whichever tenant's
    row sorted last by ``created_at``. Slack flows downstream
    inherited the swapped org and could act on a foreign tenant's
    AP item.

    Now: pass ``organization_id`` to ``get_invoice_status``
    (M5-supported kwarg) so the lookup is SQL-scoped, and never adopt
    a foreign org from the row itself. If the row's org doesn't match
    the requested org, the lookup returns None and the function
    fails closed.
    """
    org_id = str(organization_id or "").strip()
    ref = str(reference_id or "").strip()
    invoice_row: Optional[Dict[str, Any]] = None

    if ref and org_id and hasattr(db, "get_invoice_status"):
        try:
            candidate = db.get_invoice_status(ref, organization_id=org_id)
            invoice_row = candidate if isinstance(candidate, dict) else None
        except TypeError:
            # Older callers / mocked DBs may not accept the kwarg yet.
            # Fall back to the unscoped form but DO NOT adopt the row's
            # org — verify it post-fetch.
            try:
                candidate = db.get_invoice_status(ref)
                invoice_row = candidate if isinstance(candidate, dict) else None
            except Exception:
                invoice_row = None
        except Exception:
            invoice_row = None

    # Defense in depth: if the row's org disagrees with the requested
    # org, drop the row. Pre-fix this branch ADOPTED the row's org —
    # the cross-tenant landmine. The check uses ``!=`` directly so
    # an empty-string row org also fails (was: ``if row_org and
    # row_org != org_id`` which let ``row_org == ""`` slip through —
    # caught downstream today, but the comment said "drop on
    # disagreement" while the code skipped the drop on empty-org
    # rows).
    if invoice_row and org_id:
        row_org = str(invoice_row.get("organization_id") or "").strip()
        if row_org != org_id:
            invoice_row = None

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
                invoice_row = db.get_invoice_status(
                    str(reference_id or "").strip(),
                    organization_id=organization_id,
                )
            except TypeError:
                try:
                    invoice_row = db.get_invoice_status(str(reference_id or "").strip())
                except Exception:
                    invoice_row = None
            except Exception:
                invoice_row = None
            # Defense in depth: drop foreign-org rows even if the
            # unscoped fallback path returned one. Use ``!=`` directly
            # so empty row org fails the check too — same M16
            # tightening as the resolve_ap_context branch above.
            if isinstance(invoice_row, dict) and organization_id:
                row_org = str(invoice_row.get("organization_id") or "").strip()
                if row_org != organization_id:
                    invoice_row = None
            row = invoice_row if isinstance(invoice_row, dict) else None
            organization_id = org_id

    metadata = _parse_json_dict((row or {}).get("metadata"))
    correlation_id = str((row or {}).get("correlation_id") or metadata.get("correlation_id") or "").strip()
    return correlation_id or None
