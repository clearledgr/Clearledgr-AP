"""Vendor inquiry API (Wave 6 / H2).

  POST /api/workspace/vendor-inquiries/lookup
      Body: { sender_email, invoice_number }
      Returns the sanitized status block the AP operator can use
      when replying to the vendor from their own email client.
      Solden does not author the body and does not send.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.vendor_inquiry import lookup_vendor_inquiry

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["vendor-inquiry"],
)


class InquiryBody(BaseModel):
    sender_email: str = Field(..., min_length=3)
    invoice_number: str = Field(..., min_length=1)


class InquiryLookupOut(BaseModel):
    found: bool
    status: Optional[str] = None
    last_updated_at: Optional[str] = None
    invoice_number: Optional[str] = None
    payment_reference: Optional[str] = None
    settlement_at: Optional[str] = None
    no_match_reason: Optional[str] = None


@router.post(
    "/vendor-inquiries/lookup", response_model=InquiryLookupOut,
)
def lookup(
    body: InquiryBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    result = lookup_vendor_inquiry(
        db,
        organization_id=user.organization_id,
        sender_email=body.sender_email,
        invoice_number=body.invoice_number,
    )
    return InquiryLookupOut(**result.to_dict())
