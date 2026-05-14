"""Manual Box reassignment endpoint — operator-driven ownership override.

The manifesto's ownership promise needs both an auto-assignment path
(driven by org config + delegation walking) AND a manual override path
for cases where the operator knows better than the routing rules.
This module is the second.

Auto-assignment happens inside the coordination engine on state
transitions — see :mod:`clearledgr.services.box_owner` and the
``_maybe_assign_owner`` hook in ``CoordinationEngine``.

Endpoint::

    POST /api/workspace/ap-items/{ap_item_id}/reassign
        body: { "new_owner_email": "...", "reason": "..." }

Returns the resolved :class:`OwnerAssignment` and records an
``owner_changed`` audit event with ``actor_type='user'``,
``owner_source='manual'``. The audit event is the source of truth
for the reassignment history; the column on ``ap_items`` is just
the current state.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.box_owner import reassign_manually

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspace", tags=["box-owner"])


def _session_org(user: Any) -> str:
    org = str(getattr(user, "organization_id", "") or "").strip()
    if not org:
        raise HTTPException(
            status_code=403, detail="user_missing_organization_id"
        )
    return org


class ReassignRequest(BaseModel):
    new_owner_email: str = Field(..., min_length=3, max_length=320)
    reason: str = Field("", max_length=2000)


@router.post("/ap-items/{ap_item_id}/reassign")
def reassign_ap_item(
    ap_item_id: str,
    body: ReassignRequest,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Reassign a Box to a new owner. Records an ``owner_changed`` audit event.

    Tenant-scoped — a caller from org A cannot reassign org B's Box.
    Cross-tenant requests surface as 404 to avoid disclosing
    existence.
    """
    organization_id = _session_org(_user)
    actor_id = str(getattr(_user, "email", "") or getattr(_user, "user_id", "") or "")
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item or str(item.get("organization_id") or "") != organization_id:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    assignment = reassign_manually(
        db=db,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
        new_owner_email=body.new_owner_email.strip(),
        reason=body.reason.strip(),
        actor_id=actor_id,
    )
    return {
        "ap_item_id": ap_item_id,
        "owner": {
            "owner_id": assignment.owner_id,
            "owner_email": assignment.owner_email,
            "owner_source": assignment.owner_source,
            "original_owner_email": assignment.original_owner_email,
        },
    }
