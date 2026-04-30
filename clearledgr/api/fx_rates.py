"""FX rates API — Module 9 (multi-currency reporting).

  GET    /api/workspace/fx-rates                     list rates (filterable)
  POST   /api/workspace/fx-rates                     upsert a manual rate
  DELETE /api/workspace/fx-rates/{id}                delete a rate
  GET    /api/workspace/fx-rates/convert             convert one amount
                                                     (preview tool for the
                                                      dashboard)
  GET    /api/workspace/fx-rates/functional-currency report the org's
                                                     functional currency
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.core.stores.fx_rate_store import VALID_FX_SOURCES
from clearledgr.services import workspace_fx

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspace/fx-rates", tags=["fx-rates"])


class FxRateUpsertRequest(BaseModel):
    from_currency: str = Field(..., min_length=3, max_length=3)
    to_currency: str = Field(..., min_length=3, max_length=3)
    rate: float = Field(..., gt=0)
    as_of_date: Optional[str] = None
    source: str = Field("manual")
    note: Optional[str] = None


@router.get("/functional-currency")
def get_functional_currency_endpoint(
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    return {
        "organization_id": user.organization_id,
        "functional_currency": workspace_fx.get_functional_currency(db, user.organization_id),
    }


@router.get("/convert")
def convert_amount(
    amount: float = Query(..., gt=0),
    from_currency: str = Query(..., min_length=3, max_length=3, alias="from"),
    to_currency: str = Query(..., min_length=3, max_length=3, alias="to"),
    as_of_date: Optional[str] = Query(None, alias="as_of"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    result = workspace_fx.convert(
        db,
        organization_id=user.organization_id,
        amount=amount,
        from_currency=from_currency,
        to_currency=to_currency,
        as_of_date=as_of_date,
    )
    if result is None:
        return {
            "ok": False,
            "from_currency": from_currency.upper(),
            "to_currency": to_currency.upper(),
            "as_of_date": as_of_date or date.today().isoformat(),
            "amount": amount,
            "message": "No rate available for this pair / date.",
        }
    return {"ok": True, **result.to_dict(), "amount": amount}


@router.get("")
def list_rates(
    from_currency: Optional[str] = Query(None, alias="from"),
    to_currency: Optional[str] = Query(None, alias="to"),
    limit: int = Query(200, ge=1, le=500),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    rows = db.list_fx_rates(
        user.organization_id,
        from_currency=from_currency,
        to_currency=to_currency,
        limit=limit,
    )
    return {"rates": rows}


@router.post("")
def upsert_rate(
    body: FxRateUpsertRequest,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    if body.source not in VALID_FX_SOURCES:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_source",
                    "message": f"source must be one of {sorted(VALID_FX_SOURCES)}"},
        )
    if body.from_currency.upper() == body.to_currency.upper():
        raise HTTPException(
            status_code=400,
            detail={"code": "identity_rate_not_allowed",
                    "message": "from_currency and to_currency cannot be the same."},
        )

    db = get_db()
    actor = getattr(user, "user_id", "") or getattr(user, "email", "")
    try:
        rate = db.upsert_fx_rate({
            "organization_id": user.organization_id,
            "from_currency": body.from_currency,
            "to_currency": body.to_currency,
            "rate": body.rate,
            "as_of_date": body.as_of_date,
            "source": body.source,
            "note": body.note,
            "created_by": actor,
        })
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_rate", "message": str(exc)},
        ) from exc
    return {"rate": rate}


@router.delete("/{rate_id}")
def delete_rate(
    rate_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    deleted = db.delete_fx_rate(rate_id, user.organization_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="rate_not_found")
    return {"deleted": True, "rate_id": rate_id}
