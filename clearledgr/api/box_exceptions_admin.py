"""Admin/Backoffice surface for Box exceptions.

The deck promises four surfaces per Box: Gmail (work), Slack
(decisions), ERP (record), and Backoffice (customer admin). Phase 9
closes the Backoffice surface for the exceptions half of the Box
contract.

Endpoints:

- ``GET /api/admin/box/exceptions`` — org-scoped queue of unresolved
  exceptions, filterable by severity and box_type. Ordered by
  severity then raise-time so the most urgent bubble up.
- ``GET /api/admin/box/exceptions/stats`` — counts by severity and
  type for the dashboard header.
- ``POST /api/admin/box/exceptions/{exception_id}/resolve`` — mark an
  exception resolved from the admin UI. Emits the
  ``box.exception_resolved`` webhook.

All endpoints gate on ``role in {admin, owner}`` and require the
caller's organization_id to match the row's organization_id — one
org's exceptions are not visible to another tenant.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/admin/box",
    tags=["admin-box"],
    dependencies=[Depends(get_current_user)],
)


_ADMIN_ROLES = {"admin", "owner"}
_VALID_SEVERITIES = {"low", "medium", "high", "critical"}
# Display precedence: critical first, then high, medium, low. The
# underlying store returns rows ordered by ``severity DESC`` which is
# a lexicographic sort in SQLite — it puts "medium" > "low" > "high"
# > "critical", not what any operator expects. We re-sort here.
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _require_admin(user: TokenData) -> None:
    if user.role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="admin_required")


def _assert_org_match(user: TokenData, organization_id: str) -> None:
    """Assert the requested org matches the caller's session org.

    Pre-fix this coerced ``organization_id or "default"`` before
    comparing, the M4 landmine: a session whose
    ``user.organization_id`` was the legacy ``"default"`` literal
    could pass an empty body org and bypass the check. Same shape as
    the audit's ops.py / gmail_extension_common fixes — both sides
    must be non-empty and equal.
    """
    requested = str(organization_id or "").strip()
    user_org = str(getattr(user, "organization_id", "") or "").strip()
    if not requested or not user_org or requested != user_org:
        raise HTTPException(status_code=403, detail="org_mismatch")


def _attach_box_summaries(db, rows: List[Dict[str, Any]]) -> None:
    """Mutate rows in place, attaching a ``box_summary`` object so the
    workspace exception queue can render vendor / invoice / amount
    inline instead of falling back to "Unknown vendor".

    For ``ap_item`` rows the summary comes from the AP item record.
    For synthetic vendor-onboarding rows it comes from the metadata
    payload the synthesizer already populated. AP items are fetched
    one-at-a-time but the queue is capped at 200 rows so the latency
    cost is bounded; if this becomes hot, switch to a batched
    ``id IN (...)`` query.
    """
    for row in rows:
        if row.get("box_summary"):
            continue
        box_type = str(row.get("box_type") or "")
        box_id = str(row.get("box_id") or "")
        if box_type == "ap_item" and box_id and hasattr(db, "get_ap_item"):
            try:
                item = db.get_ap_item(box_id)
            except Exception:
                item = None
            if item:
                row["box_summary"] = {
                    "vendor_name": item.get("vendor_name") or item.get("vendor"),
                    "invoice_number": item.get("invoice_number"),
                    "amount": item.get("amount"),
                    "currency": item.get("currency"),
                }
                # Also expose vendor_name at the top level so older
                # clients that didn't read box_summary still render
                # something useful.
                if item.get("vendor_name") or item.get("vendor"):
                    row.setdefault(
                        "vendor_name",
                        item.get("vendor_name") or item.get("vendor"),
                    )
        elif row.get("synthetic"):
            meta = row.get("metadata") or {}
            vendor = meta.get("vendor_name")
            if vendor:
                row["box_summary"] = {"vendor_name": vendor}
                row.setdefault("vendor_name", vendor)


def _gather_unresolved(
    db,
    organization_id: str,
    *,
    box_type: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Merge canonical box_exceptions with synthetic vendor-onboarding
    signals. Module 4 Pass C — stuck or blocked vendor onboarding
    sessions surface as first-class exceptions in the queue without
    bloating ``box_exceptions`` with synthetic rows.

    Synthetic rows are only included when the caller didn't filter
    out the ``vendor_onboarding_session`` box type. The combined list
    is returned unsorted; callers handle severity ordering.

    Each row is enriched with a ``box_summary`` object so the
    workspace can render vendor / invoice / amount without an extra
    round-trip per row.
    """
    canonical = db.list_unresolved_exceptions(
        organization_id, box_type=box_type, limit=limit,
    )
    include_synthetic = (
        box_type is None
        or box_type == "vendor_onboarding_session"
    )
    if not include_synthetic:
        _attach_box_summaries(db, canonical)
        return canonical

    from clearledgr.services.vendor_onboarding_exceptions import (
        synthesize_onboarding_exceptions,
    )
    synthetic = synthesize_onboarding_exceptions(db, organization_id)
    merged = [*canonical, *synthetic]
    _attach_box_summaries(db, merged)
    return merged


@router.get("/exceptions")
def list_exceptions(
    box_type: Optional[str] = Query(None, description="Filter by box type (e.g. ap_item)"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    limit: int = Query(200, ge=1, le=500),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the org's unresolved-exception queue."""
    _require_admin(user)
    if severity and severity not in _VALID_SEVERITIES:
        raise HTTPException(status_code=400, detail="invalid_severity")

    db = get_db()
    items = _gather_unresolved(
        db, user.organization_id, box_type=box_type, limit=limit,
    )
    if severity:
        items = [row for row in items if str(row.get("severity")) == severity]
    items.sort(key=lambda r: (
        _SEVERITY_RANK.get(str(r.get("severity")), 99),
        str(r.get("raised_at") or ""),
    ))
    return {"items": items, "count": len(items)}


@router.get("/exceptions/graph")
def exception_graph(
    box_type: Optional[str] = Query(None, description="Filter by box type (e.g. ap_item)"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    limit: int = Query(200, ge=1, le=500),
    same_cause_window_days: int = Query(7, ge=1, le=90,
        description="Window for inferring shares_cause_with edges between exceptions"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Render the org's unresolved exceptions as a graph (Sprint 3-B).

    Nodes:
      * ``exception`` — one per row in the unresolved queue.
      * ``ap_item`` — one per AP item that has an exception attached.
      * ``vendor`` — one per distinct vendor across the AP items.

    Edges:
      * ``raised_on`` — exception → ap_item (the exception lives on
        the AP item).
      * ``billed_by`` — ap_item → vendor (matched by normalized
        vendor_name).
      * ``shares_cause_with`` — exception → exception when both share
        the same vendor + exception_type within
        ``same_cause_window_days``. Weighted by time-gap decay.

    The frontend renders this as a force-directed graph or clustered
    layout; the heavy lifting (cause-clustering, node typing) lives
    server-side so every renderer (workspace SPA, Slack card link
    target, future mobile) reads the same shape.
    """
    _require_admin(user)
    if severity and severity not in _VALID_SEVERITIES:
        raise HTTPException(status_code=400, detail="invalid_severity")

    db = get_db()
    items = _gather_unresolved(
        db, user.organization_id, box_type=box_type, limit=limit,
    )
    if severity:
        items = [row for row in items if str(row.get("severity")) == severity]

    # Fetch the AP items referenced by these exceptions so the graph
    # builder can decorate ap_item nodes with vendor + amount + state.
    # Synthetic vendor-onboarding exceptions (no underlying ap_item)
    # are fine — the builder handles missing records gracefully.
    ap_ids = {str(row.get("box_id") or "").strip()
              for row in items if row.get("box_id")}
    ap_records: List[Dict[str, Any]] = []
    if ap_ids and hasattr(db, "get_ap_item"):
        for ap_id in ap_ids:
            if not ap_id:
                continue
            try:
                record = db.get_ap_item(ap_id)
            except Exception:
                record = None
            if record:
                # Tenant-scope check: drop AP items that don't
                # belong to this org. ``get_ap_item`` is org-agnostic
                # in the current store; this is the defense-in-depth
                # gate that prevents a corrupted exception row
                # pointing at another tenant's AP item from leaking
                # data into the graph.
                if str(record.get("organization_id") or "") == user.organization_id:
                    ap_records.append(dict(record))

    from clearledgr.services.exception_graph import build_exception_graph
    return build_exception_graph(
        exceptions=items,
        ap_items=ap_records,
        organization_id=user.organization_id,
        same_cause_window_days=same_cause_window_days,
    )


@router.get("/exceptions/stats")
def exception_stats(
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Counts by severity and exception_type for the admin dashboard."""
    _require_admin(user)
    db = get_db()
    items = _gather_unresolved(db, user.organization_id, limit=500)

    by_severity: Dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    by_type: Dict[str, int] = {}
    by_box_type: Dict[str, int] = {}
    for row in items:
        sev = str(row.get("severity") or "medium")
        by_severity[sev] = by_severity.get(sev, 0) + 1
        t = str(row.get("exception_type") or "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        bt = str(row.get("box_type") or "unknown")
        by_box_type[bt] = by_box_type.get(bt, 0) + 1

    return {
        "total_unresolved": len(items),
        "by_severity": by_severity,
        "by_type": by_type,
        "by_box_type": by_box_type,
    }


@router.post("/exceptions/{exception_id}/resolve")
def resolve_exception(
    exception_id: str,
    body: Dict[str, Any] = Body(default_factory=dict),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Mark an exception resolved. The acting user is the resolver.

    Module 4 Pass C: synthetic vendor-onboarding rows (id prefixed
    ``vos:``) are read-only here. The operator resolves them by
    advancing the underlying onboarding session via the vendor
    surface — recording a fake row in ``box_exceptions`` would be a
    noisy half-measure (the real signal is the session state).
    """
    _require_admin(user)
    if str(exception_id or "").startswith("vos:"):
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "synthetic_exception",
                "message": (
                    "Vendor onboarding signals resolve via the vendor "
                    "surface, not the exception queue. Open the vendor "
                    "record to advance or close the session."
                ),
                "vendor_session_id": exception_id[4:],
            },
        )
    db = get_db()

    existing = db.get_box_exception(exception_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="exception_not_found")
    _assert_org_match(user, existing.get("organization_id") or "")

    if existing.get("resolved_at"):
        return {"status": "already_resolved", "exception": existing}

    note = str(body.get("resolution_note") or "").strip()
    resolved = db.resolve_box_exception(
        exception_id,
        resolved_by=str(user.email or user.user_id or "admin"),
        resolved_actor_type="user",
        resolution_note=note,
    )
    return {"status": "resolved", "exception": resolved}
