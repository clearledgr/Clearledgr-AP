"""Shared AP read-model helpers for bulk list surfaces.

These helpers keep list/read surfaces from redoing the same source/profile
lookups for every AP item while preserving the existing single-item projection
contract.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional


BuildWorklistItem = Callable[..., Dict[str, Any]]


def build_worklist_items(
    db: Any,
    rows: Iterable[Dict[str, Any]],
    *,
    build_item: BuildWorklistItem,
    approval_policy: Optional[Dict[str, Any]] = None,
    organization_settings: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    items = [dict(row or {}) for row in rows or []]
    if not items:
        return []

    sources_by_item: Dict[str, List[Dict[str, Any]]] = {}
    if hasattr(db, "list_ap_item_sources_bulk"):
        ap_item_ids = [
            str(row.get("id") or "").strip()
            for row in items
            if str(row.get("id") or "").strip()
        ]
        try:
            bulk_rows = db.list_ap_item_sources_bulk(ap_item_ids)
            if isinstance(bulk_rows, dict):
                sources_by_item = {
                    str(key or "").strip(): list(value or [])
                    for key, value in bulk_rows.items()
                }
        except Exception:
            sources_by_item = {}

    projected: List[Dict[str, Any]] = []
    for row in items:
        ap_item_id = str(row.get("id") or "").strip()
        projected.append(
            build_item(
                db,
                row,
                approval_policy=approval_policy,
                organization_settings=organization_settings,
                sources=sources_by_item.get(ap_item_id),
            )
        )
    return projected

