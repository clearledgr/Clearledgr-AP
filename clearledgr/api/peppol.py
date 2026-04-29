"""PEPPOL UBL invoice import API (Wave 4 / F1).

  POST /api/workspace/peppol/import
      Body: raw UBL 2.1 XML invoice
      - Parses via peppol_ubl_parser.parse_peppol_ubl_invoice
      - Creates an ap_items row in 'received' state
      - Pre-populates net/vat/treatment/bill_country from the
        invoice's tax breakdown so the JE preview (E4) renders
        correctly without needing a separate vat-recalculate call.
      - Returns the AP item id + parser warnings so the operator
        sees any structural issues.

  POST /api/workspace/peppol/preview
      Same parser, no DB writes — returns the canonical extraction
      shape. Useful for the import dry-run UI.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.peppol_ubl_parser import (
    ParsedPeppolInvoice,
    parse_peppol_ubl_invoice,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace/peppol",
    tags=["peppol"],
)


_MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB cap


# ── Models ──────────────────────────────────────────────────────────


class PeppolImportResponse(BaseModel):
    ap_item_id: str
    invoice_id: Optional[str] = None
    supplier_name: Optional[str] = None
    payable_amount: Optional[float] = None
    currency: Optional[str] = None
    derived_treatment: Optional[str] = None
    derived_vat_code: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class PeppolPreviewResponse(BaseModel):
    invoice_id: Optional[str] = None
    supplier_name: Optional[str] = None
    supplier_country: Optional[str] = None
    supplier_vat_id: Optional[str] = None
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    currency: Optional[str] = None
    line_extension_amount: Optional[float] = None
    tax_exclusive_amount: Optional[float] = None
    tax_inclusive_amount: Optional[float] = None
    payable_amount: Optional[float] = None
    tax_amount: Optional[float] = None
    derived_treatment: Optional[str] = None
    derived_vat_code: Optional[str] = None
    derived_vat_rate: Optional[float] = None
    line_items_count: int = 0
    warnings: List[str] = Field(default_factory=list)


def _serialize_preview(parsed: ParsedPeppolInvoice) -> PeppolPreviewResponse:
    return PeppolPreviewResponse(
        invoice_id=parsed.invoice_id,
        supplier_name=parsed.supplier_name,
        supplier_country=parsed.supplier_country,
        supplier_vat_id=parsed.supplier_vat_id,
        issue_date=parsed.issue_date,
        due_date=parsed.due_date,
        currency=parsed.currency,
        line_extension_amount=(
            float(parsed.line_extension_amount)
            if parsed.line_extension_amount is not None else None
        ),
        tax_exclusive_amount=(
            float(parsed.tax_exclusive_amount)
            if parsed.tax_exclusive_amount is not None else None
        ),
        tax_inclusive_amount=(
            float(parsed.tax_inclusive_amount)
            if parsed.tax_inclusive_amount is not None else None
        ),
        payable_amount=(
            float(parsed.payable_amount)
            if parsed.payable_amount is not None else None
        ),
        tax_amount=(
            float(parsed.tax_amount)
            if parsed.tax_amount is not None else None
        ),
        derived_treatment=parsed.derived_treatment,
        derived_vat_code=parsed.derived_vat_code,
        derived_vat_rate=(
            float(parsed.derived_vat_rate)
            if parsed.derived_vat_rate is not None else None
        ),
        line_items_count=len(parsed.line_items or []),
        warnings=list(parsed.warnings),
    )


# ── Endpoints ───────────────────────────────────────────────────────


@router.post("/preview", response_model=PeppolPreviewResponse)
async def peppol_preview(
    request: Request,
    user: TokenData = Depends(get_current_user),
):
    """Pure parse — no AP item created. Useful for an import-dry-run
    button in the workspace UI."""
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="empty_body")
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="body_too_large")
    parsed = parse_peppol_ubl_invoice(raw)
    return _serialize_preview(parsed)


@router.post("/import", response_model=PeppolImportResponse)
async def peppol_import(
    request: Request,
    user: TokenData = Depends(get_current_user),
):
    """Parse + create AP item.

    The created AP item lands in ``received`` state with the VAT
    split already populated from the UBL TaxTotal — the agent's
    next step (validate / approve / post) treats it like any other
    bill.
    """
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="empty_body")
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="body_too_large")
    parsed = parse_peppol_ubl_invoice(raw)

    # Hard-fail conditions: no payable amount means we can't even put
    # a sensible row in ap_items.
    if parsed.payable_amount is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "peppol_invoice_unparseable:missing_payable_amount; "
                f"warnings={parsed.warnings}"
            ),
        )
    if not parsed.supplier_name:
        raise HTTPException(
            status_code=400,
            detail=(
                "peppol_invoice_unparseable:missing_supplier_name; "
                f"warnings={parsed.warnings}"
            ),
        )

    db = get_db()
    import uuid
    ap_item_id = f"AP-{uuid.uuid4().hex}"
    payload: Dict[str, Any] = {
        "id": ap_item_id,
        "organization_id": user.organization_id,
        "vendor_name": parsed.supplier_name,
        "amount": float(parsed.payable_amount),
        "currency": parsed.currency or "EUR",
        "invoice_number": parsed.invoice_id,
        "due_date": parsed.due_date,
        "invoice_date": parsed.issue_date,
        "state": "received",
        "sender": parsed.supplier_vat_id or parsed.supplier_name,
        "user_id": user.user_id,
        "metadata": {
            "intake_source": "peppol_ubl",
            "peppol_customization_id": parsed.customization_id,
            "supplier_vat_id": parsed.supplier_vat_id,
            "supplier_country": parsed.supplier_country,
            "tax_subtotals": [
                {
                    "taxable_amount": (
                        float(s["taxable_amount"])
                        if s.get("taxable_amount") is not None else None
                    ),
                    "tax_amount": (
                        float(s["tax_amount"])
                        if s.get("tax_amount") is not None else None
                    ),
                    "category_id": s.get("category_id"),
                    "percent": (
                        float(s["percent"])
                        if s.get("percent") is not None else None
                    ),
                }
                for s in parsed.tax_subtotals
            ],
            "warnings": list(parsed.warnings),
        },
    }
    db.create_ap_item(payload)

    # Wire the VAT split now so the JE preview (E4) is correct
    # without an extra vat-recalculate call.
    update_kwargs: Dict[str, Any] = {}
    if parsed.tax_exclusive_amount is not None:
        update_kwargs["net_amount"] = parsed.tax_exclusive_amount
    if parsed.tax_amount is not None:
        update_kwargs["vat_amount"] = parsed.tax_amount
    if parsed.derived_vat_rate is not None:
        update_kwargs["vat_rate"] = parsed.derived_vat_rate
    if parsed.derived_vat_code:
        update_kwargs["vat_code"] = parsed.derived_vat_code
    if parsed.derived_treatment:
        update_kwargs["tax_treatment"] = parsed.derived_treatment
    if parsed.supplier_country:
        update_kwargs["bill_country"] = parsed.supplier_country
    if update_kwargs:
        db.update_ap_item(
            ap_item_id,
            **update_kwargs,
            _actor_type="user",
            _actor_id=user.user_id,
            _source="peppol_import",
        )

    return PeppolImportResponse(
        ap_item_id=ap_item_id,
        invoice_id=parsed.invoice_id,
        supplier_name=parsed.supplier_name,
        payable_amount=(
            float(parsed.payable_amount)
            if parsed.payable_amount is not None else None
        ),
        currency=parsed.currency,
        derived_treatment=parsed.derived_treatment,
        derived_vat_code=parsed.derived_vat_code,
        warnings=list(parsed.warnings),
    )
