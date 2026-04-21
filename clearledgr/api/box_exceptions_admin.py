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
    if str(organization_id or "default") != str(user.organization_id):
        raise HTTPException(status_code=403, detail="org_mismatch")


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
    items = db.list_unresolved_exceptions(
        user.organization_id,
        box_type=box_type,
        limit=limit,
    )
    if severity:
        items = [row for row in items if str(row.get("severity")) == severity]
    items.sort(key=lambda r: (
        _SEVERITY_RANK.get(str(r.get("severity")), 99),
        str(r.get("raised_at") or ""),
    ))
    return {"items": items, "count": len(items)}


@router.get("/exceptions/stats")
def exception_stats(
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Counts by severity and exception_type for the admin dashboard."""
    _require_admin(user)
    db = get_db()
    items = db.list_unresolved_exceptions(user.organization_id, limit=500)

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
    """Mark an exception resolved. The acting user is the resolver."""
    _require_admin(user)
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
