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


# ── G5 carry-over: actual ERP post + scheduler view ───────────────


class AccrualPostBody(BaseModel):
    period_start: str = Field(..., min_length=10)
    period_end: str = Field(..., min_length=10)
    erp_type: str = Field("xero")
    currency: str = Field("GBP")
    jurisdiction: str = Field("GB")


@router.post("/accrual-je/post")
def post_accrual_je_endpoint(
    body: AccrualPostBody,
    user: TokenData = Depends(get_current_user),
):
    """Operator-triggered month-end accrual post. Builds the
    proposal + posts to the configured ERP + persists the run +
    schedules the reversal."""
    from clearledgr.services.accrual_journal_entry_post import (
        run_month_end_close,
    )
    from fastapi import HTTPException

    db = get_db()
    try:
        outcome = run_month_end_close(
            db,
            organization_id=user.organization_id,
            period_start=body.period_start,
            period_end=body.period_end,
            erp_type=body.erp_type,
            currency=body.currency,
            jurisdiction=body.jurisdiction,
            actor_id=user.user_id,
        )
    except ValueError as exc:
        msg = str(exc)
        if "duplicate_period_run" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return outcome.to_dict()


@router.get("/accrual-je/runs")
def list_runs(
    status: Optional[str] = None,
    limit: int = 50,
    user: TokenData = Depends(get_current_user),
):
    from clearledgr.services.accrual_journal_entry_post import (
        list_accrual_runs,
    )
    db = get_db()
    rows = list_accrual_runs(
        db,
        organization_id=user.organization_id,
        status=status,
        limit=limit,
    )
    # Decimals -> floats for JSON.
    out = []
    for r in rows:
        rec = dict(r)
        if rec.get("accrual_amount") is not None:
            rec["accrual_amount"] = float(rec["accrual_amount"])
        out.append(rec)
    return out


@router.get("/accrual-je/runs/{run_id}")
def get_run(
    run_id: str,
    user: TokenData = Depends(get_current_user),
):
    from clearledgr.services.accrual_journal_entry_post import (
        get_accrual_run,
    )
    from fastapi import HTTPException

    db = get_db()
    row = get_accrual_run(db, run_id)
    if row is None or row.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="run_not_found")
    if row.get("accrual_amount") is not None:
        row["accrual_amount"] = float(row["accrual_amount"])
    return row


@router.post("/accrual-je/reversal-sweep")
def reversal_sweep(
    user: TokenData = Depends(get_current_user),
):
    """Operator-triggered reversal sweep for the current org.
    Same logic as the daily Celery task but scoped to the
    authenticated user's org."""
    from clearledgr.services.accrual_journal_entry_post import (
        post_pending_reversals,
    )
    db = get_db()
    result = post_pending_reversals(
        db,
        organization_id=user.organization_id,
        actor_id=user.user_id,
    )
    return {
        "swept": result.swept,
        "reversed_ok": result.reversed_ok,
        "failed": result.failed,
        "details": list(result.details),
    }
