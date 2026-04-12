"""Smaller support routes extracted from the Gmail extension adapter."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from clearledgr.api.gmail_extension_common import resolve_org_id_for_user
from clearledgr.core.auth import get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.gmail_extension_support import (
    build_amount_validation_payload,
    build_form_prefill_payload,
    build_gl_suggestion_payload,
    build_needs_info_draft_payload,
    build_vendor_suggestion_payload,
)


router = APIRouter()


@router.get("/health")
def extension_health():
    return {
        "status": "ok",
        "service": "clearledgr-gmail-extension",
        "differentiators": [
            "audit_link_generation",
            "human_in_the_loop",
            "multi_system_routing",
        ],
    }


class GLSuggestionRequest(BaseModel):
    vendor_name: str
    amount: Optional[float] = None
    description: Optional[str] = None
    organization_id: Optional[str] = "default"


class VendorSuggestionRequest(BaseModel):
    sender_email: Optional[str] = None
    sender_name: Optional[str] = None
    subject: Optional[str] = None
    extracted_vendor: Optional[str] = None
    organization_id: Optional[str] = "default"


@router.post("/suggestions/gl-code")
async def suggest_gl_code(
    request: GLSuggestionRequest,
    _user=Depends(get_current_user),
):
    org_id = resolve_org_id_for_user(_user, request.organization_id)
    return build_gl_suggestion_payload(
        organization_id=org_id,
        vendor_name=request.vendor_name,
    )


@router.post("/suggestions/vendor")
async def suggest_vendor(
    request: VendorSuggestionRequest,
    _user=Depends(get_current_user),
):
    org_id = resolve_org_id_for_user(_user, request.organization_id)
    return build_vendor_suggestion_payload(
        organization_id=org_id,
        sender_email=request.sender_email,
        extracted_vendor=request.extracted_vendor,
    )


@router.post("/suggestions/amount-validation")
async def validate_amount(
    vendor_name: str = Body(...),
    amount: float = Body(...),
    organization_id: str = Body("default"),
    _user=Depends(get_current_user),
):
    resolve_org_id_for_user(_user, organization_id)
    return build_amount_validation_payload(vendor_name, amount)


@router.get("/suggestions/form-prefill/{email_id}")
async def get_form_prefill(
    email_id: str,
    organization_id: str = "default",
    _user=Depends(get_current_user),
):
    org_id = resolve_org_id_for_user(_user, organization_id)
    db = get_db()
    invoice = db.get_invoice_by_email_id(email_id)
    try:
        return build_form_prefill_payload(
            email_id=email_id,
            organization_id=org_id,
            invoice=invoice,
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="org_mismatch")


@router.get("/needs-info-draft/{ap_item_id}")
async def get_needs_info_draft(
    ap_item_id: str,
    reason: Optional[str] = Query(None, description="What information is needed — pre-fills the email body"),
    _user=Depends(get_current_user),
):
    db = get_db()
    ap_item = db.get_ap_item(ap_item_id)
    try:
        return build_needs_info_draft_payload(
            ap_item_id=ap_item_id,
            ap_item=ap_item,
            reason=reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
