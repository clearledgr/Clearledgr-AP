"""Dual approval API (Wave 6 / H1).

  POST /api/workspace/ap-items/{id}/approve/first
      First approver signs. Below threshold -> approved; at/above ->
      needs_second_approval.

  POST /api/workspace/ap-items/{id}/approve/second
      Second approver signs (must differ from first + from
      requester). Advances to approved.

  POST /api/workspace/ap-items/{id}/approve/revoke
      First approver pulls their signature back to
      needs_approval (only valid in needs_second_approval).

  GET  /api/workspace/policy/dual-approval
  PUT  /api/workspace/policy/dual-approval
      Per-org configuration of the threshold (gross amount above
      which a second signature is required).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.dual_approval import (
    DualApprovalNotPendingError,
    DualApprovalRequesterApprovalError,
    DualApprovalSelfApprovalError,
    first_approve,
    get_dual_approval_threshold,
    revoke_first_signature,
    second_approve,
    set_dual_approval_threshold,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["dual-approval"],
)


# ── Models ──────────────────────────────────────────────────────────


class ApproveBody(BaseModel):
    pass  # currently empty — actor identity from TokenData


class RevokeBody(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


class DualApprovalResultOut(BaseModel):
    ap_item_id: str
    new_state: str
    first_approver: Optional[str] = None
    first_approved_at: Optional[str] = None
    second_approver: Optional[str] = None
    second_approved_at: Optional[str] = None
    requires_second_signature: bool = False


class DualApprovalPolicyBody(BaseModel):
    dual_approval_threshold: Optional[float] = Field(None, ge=0)


class DualApprovalPolicyOut(BaseModel):
    dual_approval_threshold: Optional[float] = None


# ── Approve endpoints ──────────────────────────────────────────────


@router.post(
    "/ap-items/{ap_item_id}/approve/first",
    response_model=DualApprovalResultOut,
)
def first_approve_endpoint(
    ap_item_id: str,
    body: Optional[ApproveBody] = None,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    try:
        result = first_approve(
            db,
            organization_id=user.organization_id,
            ap_item_id=ap_item_id,
            approver_id=user.user_id,
            approver_email=user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DualApprovalRequesterApprovalError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return DualApprovalResultOut(**result.to_dict())


@router.post(
    "/ap-items/{ap_item_id}/approve/second",
    response_model=DualApprovalResultOut,
)
def second_approve_endpoint(
    ap_item_id: str,
    body: Optional[ApproveBody] = None,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    try:
        result = second_approve(
            db,
            organization_id=user.organization_id,
            ap_item_id=ap_item_id,
            approver_id=user.user_id,
            approver_email=user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DualApprovalNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (DualApprovalSelfApprovalError, DualApprovalRequesterApprovalError) as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return DualApprovalResultOut(**result.to_dict())


@router.post(
    "/ap-items/{ap_item_id}/approve/revoke",
    response_model=DualApprovalResultOut,
)
def revoke_endpoint(
    ap_item_id: str,
    body: Optional[RevokeBody] = None,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    try:
        result = revoke_first_signature(
            db,
            organization_id=user.organization_id,
            ap_item_id=ap_item_id,
            actor_id=user.user_id,
            reason=(body.reason if body else None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DualApprovalNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return DualApprovalResultOut(**result.to_dict())


# ── Policy endpoints ──────────────────────────────────────────────


@router.get(
    "/policy/dual-approval",
    response_model=DualApprovalPolicyOut,
)
def get_policy(
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    threshold = get_dual_approval_threshold(db, user.organization_id)
    return DualApprovalPolicyOut(
        dual_approval_threshold=(
            None if threshold == float("inf") else threshold
        ),
    )


@router.put(
    "/policy/dual-approval",
    response_model=DualApprovalPolicyOut,
)
def put_policy(
    body: DualApprovalPolicyBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    set_dual_approval_threshold(
        db, user.organization_id, body.dual_approval_threshold,
    )
    fresh = get_dual_approval_threshold(db, user.organization_id)
    return DualApprovalPolicyOut(
        dual_approval_threshold=(
            None if fresh == float("inf") else fresh
        ),
    )
