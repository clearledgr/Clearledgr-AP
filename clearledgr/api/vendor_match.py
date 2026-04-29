"""Multi-attribute vendor match API (Wave 5 / G2).

  GET /api/workspace/ap-items/{id}/vendor-match
      Score the AP item against the stored vendor profile across
      name + VAT + IBAN + sender domain + address. Returns the
      per-attribute breakdown + overall_status + flags. The
      per-attribute observed/expected pair is what the operator's
      approval card embeds so they can see exactly which
      attributes agreed and which didn't.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.vendor_attribute_matcher import (
    evaluate_ap_item_vendor_match,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["vendor-match"],
)


class AttributeBreakdownOut(BaseModel):
    attribute: str
    matched: Optional[bool] = None
    score: Optional[float] = None
    expected: Any = None
    observed: Any = None
    note: Optional[str] = None


class VendorMatchOut(BaseModel):
    vendor_name: str
    overall_status: str
    confidence: float
    attributes: List[AttributeBreakdownOut] = Field(default_factory=list)
    flags: List[str] = Field(default_factory=list)


@router.get(
    "/ap-items/{ap_item_id}/vendor-match",
    response_model=VendorMatchOut,
)
def get_vendor_match(
    ap_item_id: str,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    result = evaluate_ap_item_vendor_match(
        db,
        organization_id=user.organization_id,
        ap_item_id=ap_item_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    return result.to_dict()
