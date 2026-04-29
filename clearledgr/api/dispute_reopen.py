"""Dispute reopen API (Wave 6 / H3).

  POST /api/workspace/ap-items/{id}/dispute-reopen
      Body: { reopen_kind: 'credit_note'|'rebill', correction_amount,
              reason, rebill_invoice_number? }
      Spawns a correction AP item linked to the (terminal) original.
      Returns the chain { original, correction, dispute }.

  GET /api/workspace/ap-items/{id}/dispute-reopen
      Read-only: return the dispute_reopen block from this AP
      item's metadata (whichever side of the chain).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.dispute_reopen import (
    DisputeReopenError,
    OriginalNotReopenableError,
    get_correction_chain,
    reopen_for_dispute,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["dispute-reopen"],
)


class ReopenBody(BaseModel):
    reopen_kind: str = Field(..., pattern="^(credit_note|rebill)$")
    correction_amount: float = Field(..., gt=0)
    reason: str = Field(..., min_length=1, max_length=2000)
    rebill_invoice_number: Optional[str] = Field(None, max_length=128)


class ReopenResultOut(BaseModel):
    original_ap_item_id: str
    correction_ap_item_id: str
    reopen_kind: str
    correction_amount: float
    dispute_id: Optional[str] = None
    audit_event_id: Optional[str] = None


@router.post(
    "/ap-items/{ap_item_id}/dispute-reopen",
    response_model=ReopenResultOut,
)
def reopen_endpoint(
    ap_item_id: str,
    body: ReopenBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    try:
        result = reopen_for_dispute(
            db,
            organization_id=user.organization_id,
            original_ap_item_id=ap_item_id,
            reopen_kind=body.reopen_kind,
            correction_amount=body.correction_amount,
            reason=body.reason,
            actor_id=user.user_id,
            rebill_invoice_number=body.rebill_invoice_number,
        )
    except OriginalNotReopenableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=(
                404 if "ap_item_not_found" in str(exc) else 400
            ),
            detail=str(exc),
        )
    return ReopenResultOut(**result.to_dict())


@router.get("/ap-items/{ap_item_id}/dispute-reopen")
def get_chain(
    ap_item_id: str,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    return get_correction_chain(
        db,
        organization_id=user.organization_id,
        ap_item_id=ap_item_id,
    )
