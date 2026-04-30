"""Vendor inquiry API (Wave 6 / H2).

  POST /api/workspace/vendor-inquiries/lookup
      Body: { sender_email, invoice_number }
      Returns the sanitized status + a pre-rendered reply the AP
      team can review and send.

  POST /api/workspace/vendor-inquiries/reply
      Body: { sender_email, invoice_number }
      Same lookup but wraps the reply text in a structured
      response convenient for the workspace UI's "Send reply" button.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.vendor_inquiry import (
    lookup_vendor_inquiry,
    render_inquiry_reply,
)

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
    narrative: Optional[str] = None
    no_match_reason: Optional[str] = None


class InquiryReplyOut(BaseModel):
    found: bool
    status: Optional[str] = None
    last_updated_at: Optional[str] = None
    no_match_reason: Optional[str] = None
    reply_subject: str
    reply_body: str


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


@router.post(
    "/vendor-inquiries/reply", response_model=InquiryReplyOut,
)
def reply(
    body: InquiryBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    org = db.get_organization(user.organization_id) or {}
    org_name = (
        org.get("name") or org.get("organization_name") or user.organization_id
    )
    result = lookup_vendor_inquiry(
        db,
        organization_id=user.organization_id,
        sender_email=body.sender_email,
        invoice_number=body.invoice_number,
    )
    rendered = render_inquiry_reply(
        organization_name=org_name,
        vendor_name=None,  # we don't expose vendor_name back to vendors
        invoice_number=body.invoice_number,
        result=result,
    )
    return InquiryReplyOut(
        found=result.found,
        status=result.status,
        last_updated_at=result.last_updated_at,
        no_match_reason=result.no_match_reason,
        reply_subject=rendered["subject"],
        reply_body=rendered["body"],
    )
