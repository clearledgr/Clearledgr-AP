"""Reclassification JE API (Wave 6 / H4).

  POST /api/workspace/ap-items/{id}/reclassify/preview
      Body: { to_account, reason, from_account?, amount?,
              posting_date?, erp_type? }
      Generate the canonical reclassification JE proposal without
      persisting anything. Returns the structured JE + a plain-text
      rendering for embed.

  POST /api/workspace/ap-items/{id}/reclassify
      Same body — generates the proposal AND records the
      back-link on the AP item + emits the canonical audit event.
      The actual ERP-side post is the integration layer's job;
      this endpoint records the operator's decision to reclassify.

  GET /api/workspace/ap-items/{id}/reclassifications
      List all reclassifications recorded against the bill,
      newest first.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.reclassification_je import (
    NotPostedError,
    build_reclassification_proposal,
    list_reclassifications,
    record_reclassification,
    render_reclassification_text,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["reclassification"],
)


class ReclassifyBody(BaseModel):
    to_account: str = Field(..., min_length=1, max_length=64)
    reason: str = Field(..., min_length=1, max_length=2000)
    from_account: Optional[str] = Field(None, max_length=64)
    amount: Optional[float] = Field(None, gt=0)
    posting_date: Optional[str] = None
    erp_type: Optional[str] = Field(None, max_length=32)


@router.post("/ap-items/{ap_item_id}/reclassify/preview")
def preview_reclassification(
    ap_item_id: str,
    body: ReclassifyBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    try:
        proposal = build_reclassification_proposal(
            db,
            organization_id=user.organization_id,
            ap_item_id=ap_item_id,
            to_account=body.to_account,
            reason=body.reason,
            from_account=body.from_account,
            amount=body.amount,
            posting_date=body.posting_date,
            erp_type=body.erp_type,
            actor_id=user.user_id,
        )
    except NotPostedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=(
                404 if "ap_item_not_found" in str(exc) else 400
            ),
            detail=str(exc),
        )
    out = proposal.to_dict()
    out["rendered_text"] = render_reclassification_text(proposal)
    return out


@router.post("/ap-items/{ap_item_id}/reclassify")
def commit_reclassification(
    ap_item_id: str,
    body: ReclassifyBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    try:
        proposal = build_reclassification_proposal(
            db,
            organization_id=user.organization_id,
            ap_item_id=ap_item_id,
            to_account=body.to_account,
            reason=body.reason,
            from_account=body.from_account,
            amount=body.amount,
            posting_date=body.posting_date,
            erp_type=body.erp_type,
            actor_id=user.user_id,
        )
    except NotPostedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=(
                404 if "ap_item_not_found" in str(exc) else 400
            ),
            detail=str(exc),
        )
    record = record_reclassification(
        db,
        organization_id=user.organization_id,
        proposal=proposal,
        actor_id=user.user_id,
    )
    out = proposal.to_dict()
    out["recorded"] = record
    out["rendered_text"] = render_reclassification_text(proposal)
    return out


@router.get("/ap-items/{ap_item_id}/reclassifications")
def list_for_ap_item(
    ap_item_id: str,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    return list_reclassifications(
        db,
        organization_id=user.organization_id,
        ap_item_id=ap_item_id,
    )
