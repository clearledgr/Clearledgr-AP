"""Auto-post accrual JE API (Wave 5 / G5).

  POST /api/workspace/accrual-je/preview
      Body: { period_start, period_end, erp_type?, currency? }
      Returns the canonical received-not-billed accrual JE proposal
      for the period — line-level GRN sources + aggregated Dr/Cr +
      reversal date for next period.

      Operator review surface; the actual ERP post lives in a
      sibling integration that consumes this proposal.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.accrual_journal_entry import (
    build_accrual_je_proposal,
    render_accrual_proposal_text,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["accrual-je"],
)


class AccrualPreviewBody(BaseModel):
    period_start: str = Field(..., min_length=10)
    period_end: str = Field(..., min_length=10)
    erp_type: str = Field("xero")
    currency: str = Field("GBP")


@router.post("/accrual-je/preview")
def preview_accrual_je(
    body: AccrualPreviewBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    proposal = build_accrual_je_proposal(
        db,
        organization_id=user.organization_id,
        period_start=body.period_start,
        period_end=body.period_end,
        erp_type=body.erp_type,
        currency=body.currency,
    )
    out = proposal.to_dict()
    out["rendered_text"] = render_accrual_proposal_text(proposal)
    return out
