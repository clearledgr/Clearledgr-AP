"""VAT modeling API (Wave 3 / E2).

Operator + agent surface for the EU/UK launch:

  * POST /api/workspace/ap-items/{id}/vat-recalculate
      Recompute the bill's VAT split given the org's home country +
      the bill's seller country. Stores net/vat/rate/code/treatment
      on the AP item. Used by:
        - The agent's classification step on bill ingestion
        - An operator manually triggering a re-derive after editing
          the seller country / VAT rate

  * POST /api/workspace/vat/preview
      Pure compute (no persistence) — caller passes amounts +
      countries, gets back the canonical split. Used by approval
      cards / JE preview surfaces (E4) that need the numbers
      without touching the AP item.

  * POST /api/workspace/vat-returns/compute
      Compute + persist a draft VAT return for a period.

  * GET  /api/workspace/vat-returns
      List computed returns (drafts + submitted).

  * GET  /api/workspace/vat-returns/{id}
      Single return with the 9 boxes.

  * POST /api/workspace/vat-returns/{id}/submit
      Operator marks a draft as filed with the tax authority.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.vat_calculator import (
    calculate_vat,
    get_org_home_country,
)
from clearledgr.services.vat_return import (
    compute_and_persist_vat_return,
    get_vat_return,
    list_vat_returns,
    mark_vat_return_submitted,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["vat"],
)


# ── Models ──────────────────────────────────────────────────────────


class VATPreviewRequest(BaseModel):
    gross_amount: float
    bill_country: Optional[str] = Field(None, max_length=4)
    seller_has_vat_id: bool = False
    rate_override: Optional[float] = None
    treatment_override: Optional[str] = Field(None, max_length=32)


class VATPreviewResponse(BaseModel):
    gross_amount: float
    net_amount: float
    vat_amount: float
    vat_rate: float
    vat_code: str
    tax_treatment: str
    bill_country: Optional[str] = None
    home_country: Optional[str] = None
    note: Optional[str] = None


class VATReturnSubmitBody(BaseModel):
    submission_reference: str = Field(..., min_length=1, max_length=128)


class VATReturnComputeBody(BaseModel):
    period_start: str
    period_end: str
    jurisdiction: str = "GB"
    currency: str = "GBP"


def _serialize_vat_return(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


# ── Endpoints ───────────────────────────────────────────────────────


@router.post(
    "/vat/preview",
    response_model=VATPreviewResponse,
)
def preview_vat(
    body: VATPreviewRequest,
    user: TokenData = Depends(get_current_user),
):
    """Compute the canonical VAT split without persisting anything."""
    db = get_db()
    home = get_org_home_country(db, user.organization_id)
    try:
        result = calculate_vat(
            gross_amount=body.gross_amount,
            home_country=home,
            bill_country=body.bill_country,
            seller_has_vat_id=body.seller_has_vat_id,
            rate_override=body.rate_override,
            treatment_override=body.treatment_override,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return VATPreviewResponse(
        gross_amount=float(result.gross_amount),
        net_amount=float(result.net_amount),
        vat_amount=float(result.vat_amount),
        vat_rate=float(result.vat_rate),
        vat_code=result.vat_code,
        tax_treatment=result.tax_treatment,
        bill_country=result.bill_country,
        home_country=result.home_country,
        note=result.note,
    )


@router.post(
    "/ap-items/{ap_item_id}/vat-recalculate",
    response_model=VATPreviewResponse,
)
def recalculate_ap_item_vat(
    ap_item_id: str,
    body: Optional[VATPreviewRequest] = None,
    user: TokenData = Depends(get_current_user),
):
    """Recompute and persist the VAT split on an AP item.

    Body is optional — if omitted, the calculator uses the AP item's
    existing ``amount`` + ``bill_country`` and the org's home country.
    """
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    home = get_org_home_country(db, user.organization_id)
    bill_country = (
        (body.bill_country if body else None)
        or item.get("bill_country")
    )
    gross = (
        (body.gross_amount if body else None)
        or float(item.get("amount") or 0)
    )
    rate_override = body.rate_override if body else None
    treatment_override = body.treatment_override if body else None
    seller_has_vat_id = bool(body.seller_has_vat_id) if body else False

    try:
        result = calculate_vat(
            gross_amount=gross,
            home_country=home,
            bill_country=bill_country,
            seller_has_vat_id=seller_has_vat_id,
            rate_override=rate_override,
            treatment_override=treatment_override,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    db.update_ap_item(
        ap_item_id,
        net_amount=result.net_amount,
        vat_amount=result.vat_amount,
        vat_rate=result.vat_rate,
        vat_code=result.vat_code,
        tax_treatment=result.tax_treatment,
        bill_country=result.bill_country,
        _actor_type="user",
        _actor_id=user.user_id,
        _source="vat_recalculate",
    )
    return VATPreviewResponse(
        gross_amount=float(result.gross_amount),
        net_amount=float(result.net_amount),
        vat_amount=float(result.vat_amount),
        vat_rate=float(result.vat_rate),
        vat_code=result.vat_code,
        tax_treatment=result.tax_treatment,
        bill_country=result.bill_country,
        home_country=result.home_country,
        note=result.note,
    )


@router.post("/vat-returns/compute")
def compute_vat_return(
    body: VATReturnComputeBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    row = compute_and_persist_vat_return(
        db,
        organization_id=user.organization_id,
        period_start=body.period_start,
        period_end=body.period_end,
        jurisdiction=body.jurisdiction,
        currency=body.currency,
        actor=user.user_id,
    )
    return _serialize_vat_return(row)


@router.get("/vat-returns")
def list_returns(
    jurisdiction: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    rows = list_vat_returns(
        db,
        organization_id=user.organization_id,
        jurisdiction=jurisdiction,
        status=status,
        limit=limit,
    )
    return [_serialize_vat_return(r) for r in rows]


@router.get("/vat-returns/{return_id}")
def get_return(
    return_id: str,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    row = get_vat_return(db, return_id)
    if row is None or row.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="return_not_found")
    return _serialize_vat_return(row)


@router.post("/vat-returns/{return_id}/submit")
def submit_return(
    return_id: str,
    body: VATReturnSubmitBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    row = get_vat_return(db, return_id)
    if row is None or row.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="return_not_found")
    if (row.get("status") or "").lower() != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"return_status_not_draft:{row.get('status')}",
        )
    fresh = mark_vat_return_submitted(
        db, return_id,
        submission_reference=body.submission_reference,
        submitted_by=user.user_id,
    )
    return _serialize_vat_return(fresh)
