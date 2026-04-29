"""Three-way match API (Wave 5 / G1).

  POST /api/workspace/ap-items/{id}/three-way-match
      Run / re-run the 3-way match against the org's PO + GR data.
      Persists match_status on the AP item, emits an audit event,
      returns the structured summary including a per-line breakdown.

  GET /api/workspace/ap-items/{id}/three-way-match
      Same as POST in semantics — re-runs and returns. We use POST
      for the canonical write-path semantics; GET is provided for
      operator-side dashboards that don't want to issue mutating
      verbs to view a status.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.three_way_match_runner import (
    ThreeWayMatchSummary,
    run_three_way_match,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["three-way-match"],
)


class LineBreakdownOut(BaseModel):
    description: str
    item_code: Optional[str] = None
    invoice_quantity: float = 0.0
    invoice_unit_price: float = 0.0
    invoice_amount: float = 0.0
    po_quantity: Optional[float] = None
    po_unit_price: Optional[float] = None
    gr_quantity_received: Optional[float] = None
    price_variance: Optional[float] = None
    price_variance_pct: Optional[float] = None
    quantity_variance: Optional[float] = None
    quantity_variance_pct: Optional[float] = None
    match_flag: str


class ThreeWayMatchOut(BaseModel):
    ap_item_id: str
    organization_id: str
    match_status: str
    po_id: Optional[str] = None
    po_number: Optional[str] = None
    gr_id: Optional[str] = None
    invoice_amount: float = 0.0
    po_amount: Optional[float] = None
    gr_amount: Optional[float] = None
    price_variance: Optional[float] = None
    price_variance_pct: Optional[float] = None
    quantity_variance: Optional[float] = None
    currency: Optional[str] = None
    exceptions: List[Dict[str, Any]] = Field(default_factory=list)
    line_breakdown: List[LineBreakdownOut] = Field(default_factory=list)
    note: Optional[str] = None


def _serialize(summary: ThreeWayMatchSummary) -> ThreeWayMatchOut:
    return ThreeWayMatchOut(**summary.to_dict())


@router.post(
    "/ap-items/{ap_item_id}/three-way-match",
    response_model=ThreeWayMatchOut,
)
def run_match(
    ap_item_id: str,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    summary = run_three_way_match(
        db,
        organization_id=user.organization_id,
        ap_item_id=ap_item_id,
        actor=user.user_id,
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    return _serialize(summary)


@router.get(
    "/ap-items/{ap_item_id}/three-way-match",
    response_model=ThreeWayMatchOut,
)
def get_match(
    ap_item_id: str,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    summary = run_three_way_match(
        db,
        organization_id=user.organization_id,
        ap_item_id=ap_item_id,
        actor=user.user_id,
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    return _serialize(summary)
