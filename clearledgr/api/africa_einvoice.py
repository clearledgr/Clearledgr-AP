"""Africa e-invoice generation API (Wave 4 / F4).

  POST /api/workspace/ap-items/{id}/africa-einvoice?country=NG
      Generate the country-specific e-invoice payload for the AP
      item. Returns the JSON envelope ready to hand to the org's
      certified Access / Service Provider (Sovos, Pwani Tech, etc.).

      ``country`` query param: NG | KE | ZA. The org's tax_number /
      branch_code is read from settings_json["tax"].

  POST /api/workspace/africa-einvoice/preview?country=NG
      Pure compute — accepts a payload body of explicit context +
      lines + totals, returns the country-specific envelope. Used
      by the workspace UI's preview surface.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.africa_einvoice import (
    AfricaEInvoiceContext,
    AfricaEInvoiceLine,
    build_africa_einvoice,
    build_einvoice_from_ap_item,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["africa-einvoice"],
)


_SUPPORTED_COUNTRIES = ("NG", "KE", "ZA")


class _Line(BaseModel):
    description: str
    quantity: float = Field(..., ge=0)
    unit_price: float = Field(..., ge=0)
    line_amount: float = Field(..., ge=0)
    tax_amount: float = 0.0
    tax_rate: float = 0.0
    item_code: Optional[str] = None
    hs_code: Optional[str] = None


class PreviewBody(BaseModel):
    issuer_name: str = Field(..., min_length=1)
    issuer_tax_id: str = Field(..., min_length=1)
    issuer_branch_code: Optional[str] = None
    customer_name: Optional[str] = None
    customer_tax_id: Optional[str] = None
    customer_country: Optional[str] = None
    document_id: str = Field(..., min_length=1)
    document_type: str = Field("invoice")
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    currency: str = Field(..., min_length=3, max_length=3)
    reference_document_id: Optional[str] = None
    lines: List[_Line] = Field(default_factory=list)
    total_amount: float = Field(..., ge=0)
    total_tax: float = Field(0.0, ge=0)


@router.post("/africa-einvoice/preview")
def preview_einvoice(
    body: PreviewBody,
    country: str = Query(..., min_length=2, max_length=4),
    user: TokenData = Depends(get_current_user),
):
    code = (country or "").upper()
    if code not in _SUPPORTED_COUNTRIES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unsupported_country:{code!r}; "
                f"supported={list(_SUPPORTED_COUNTRIES)}"
            ),
        )
    context = AfricaEInvoiceContext(
        issuer_name=body.issuer_name,
        issuer_tax_id=body.issuer_tax_id,
        issuer_country=code,
        issuer_branch_code=body.issuer_branch_code,
        customer_name=body.customer_name,
        customer_tax_id=body.customer_tax_id,
        customer_country=body.customer_country,
        document_id=body.document_id,
        document_type=body.document_type,
        issue_date=body.issue_date,
        due_date=body.due_date,
        currency=body.currency,
        reference_document_id=body.reference_document_id,
    )
    lines = [
        AfricaEInvoiceLine(
            description=ln.description,
            quantity=Decimal(str(ln.quantity)),
            unit_price=Decimal(str(ln.unit_price)),
            line_amount=Decimal(str(ln.line_amount)),
            tax_amount=Decimal(str(ln.tax_amount)),
            tax_rate=Decimal(str(ln.tax_rate)),
            item_code=ln.item_code,
            hs_code=ln.hs_code,
        )
        for ln in body.lines
    ]
    return build_africa_einvoice(
        country_code=code, context=context, lines=lines,
        total_amount=Decimal(str(body.total_amount)),
        total_tax=Decimal(str(body.total_tax)),
    )


@router.post("/ap-items/{ap_item_id}/africa-einvoice")
def ap_item_einvoice(
    ap_item_id: str,
    country: str = Query(..., min_length=2, max_length=4),
    document_type: str = Query("invoice"),
    user: TokenData = Depends(get_current_user),
):
    """Generate a NG / KE / ZA e-invoice payload from one AP item."""
    code = (country or "").upper()
    if code not in _SUPPORTED_COUNTRIES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unsupported_country:{code!r}; "
                f"supported={list(_SUPPORTED_COUNTRIES)}"
            ),
        )
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    org = db.get_organization(user.organization_id) or {
        "id": user.organization_id,
        "name": user.organization_id,
    }
    try:
        return build_einvoice_from_ap_item(
            country_code=code,
            ap_item=item,
            organization=org,
            document_type=document_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
