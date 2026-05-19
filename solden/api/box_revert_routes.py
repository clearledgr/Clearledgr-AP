"""Reversibility endpoints — bounded undo on AP item transitions.

Manifesto §"History" — every reversal. This module exposes the
in-Solden state-revert path (approval undo) as a workspace endpoint.
The ERP-level reversal path stays in
``services/override_window.py`` and its existing routes — different
concern, different external coupling, different audit shape.

Endpoint::

    POST /api/workspace/ap-items/{ap_item_id}/revert-approval
        body: { "reason": "..." }

Outcomes:
  * ``200`` — reverted; body includes new_state and window_seconds_remaining at the moment of revert.
  * ``409`` — invalid state or window expired; body includes the reason code.
  * ``404`` — Box not found in the caller's tenant.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from solden.core.auth import get_current_user
from solden.core.database import get_db
from solden.services.approval_revert import attempt_approval_revert

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspace", tags=["box-revert"])


def _session_org(user: Any) -> str:
    org = str(getattr(user, "organization_id", "") or "").strip()
    if not org:
        raise HTTPException(
            status_code=403, detail="user_missing_organization_id"
        )
    return org


class RevertApprovalRequest(BaseModel):
    reason: str = Field("", max_length=2000)


@router.post("/ap-items/{ap_item_id}/revert-approval")
def revert_approval(
    ap_item_id: str,
    body: RevertApprovalRequest,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    organization_id = _session_org(_user)
    actor_id = str(getattr(_user, "email", "") or getattr(_user, "user_id", "") or "")
    db = get_db()
    outcome = attempt_approval_revert(
        db=db,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
        actor_id=actor_id,
        reason=body.reason.strip(),
    )
    if outcome.status == "not_found":
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    if outcome.status in {"expired", "invalid_state"}:
        raise HTTPException(status_code=409, detail=outcome.to_dict())
    return outcome.to_dict()
