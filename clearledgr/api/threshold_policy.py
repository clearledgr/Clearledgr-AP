"""Threshold policy API (Wave 5 / G4).

Operator surface for the routing thresholds that drive
auto-approve / escalate / require-PO decisions.

  Org-level defaults:
    GET  /api/workspace/policy/thresholds
    PUT  /api/workspace/policy/thresholds

  Per-vendor overrides:
    GET  /api/workspace/vendors/{name}/thresholds
    PUT  /api/workspace/vendors/{name}/thresholds
    DELETE /api/workspace/vendors/{name}/thresholds  (revert to default)

  Resolved view (what the agent will actually use):
    GET  /api/workspace/policy/thresholds/resolve?vendor=<name>
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.threshold_policy import (
    get_org_thresholds,
    get_vendor_threshold_overrides,
    resolve_thresholds,
    set_org_thresholds,
    set_vendor_threshold_overrides,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["threshold-policy"],
)


# ── Models ──────────────────────────────────────────────────────────


class ThresholdsBody(BaseModel):
    auto_approve_min: Optional[float] = Field(None, ge=0.5, le=0.99)
    escalate_below: Optional[float] = Field(None, ge=0.5, le=0.99)
    po_required_above: Optional[float] = Field(None, ge=0)


class ThresholdsOut(BaseModel):
    auto_approve_min: Optional[float] = None
    escalate_below: Optional[float] = None
    po_required_above: Optional[float] = None


class ResolvedOut(BaseModel):
    organization_id: str
    vendor_name: Optional[str] = None
    auto_approve_min: float
    escalate_below: float
    po_required_above: Optional[float] = None
    source_chain: Dict[str, str]


def _serialize(block: Dict[str, Any]) -> ThresholdsOut:
    return ThresholdsOut(
        auto_approve_min=block.get("auto_approve_min"),
        escalate_below=block.get("escalate_below"),
        po_required_above=block.get("po_required_above"),
    )


# ── Org-level ──────────────────────────────────────────────────────


@router.get("/policy/thresholds", response_model=ThresholdsOut)
def get_org_threshold_policy(
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    block = get_org_thresholds(db, user.organization_id)
    return _serialize(block)


@router.put("/policy/thresholds", response_model=ThresholdsOut)
def put_org_threshold_policy(
    body: ThresholdsBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    try:
        block = set_org_thresholds(
            db,
            user.organization_id,
            auto_approve_min=body.auto_approve_min,
            escalate_below=body.escalate_below,
            po_required_above=body.po_required_above,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _serialize(block)


# ── Per-vendor ─────────────────────────────────────────────────────


@router.get(
    "/vendors/{vendor_name}/thresholds",
    response_model=ThresholdsOut,
)
def get_vendor_threshold_policy(
    vendor_name: str,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    profile = db.get_vendor_profile(user.organization_id, vendor_name)
    if profile is None:
        raise HTTPException(status_code=404, detail="vendor_not_found")
    block = get_vendor_threshold_overrides(
        db, user.organization_id, vendor_name,
    )
    return _serialize(block)


@router.put(
    "/vendors/{vendor_name}/thresholds",
    response_model=ThresholdsOut,
)
def put_vendor_threshold_policy(
    vendor_name: str,
    body: ThresholdsBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    profile = db.get_vendor_profile(user.organization_id, vendor_name)
    if profile is None:
        raise HTTPException(status_code=404, detail="vendor_not_found")
    try:
        block = set_vendor_threshold_overrides(
            db,
            user.organization_id,
            vendor_name,
            auto_approve_min=body.auto_approve_min,
            escalate_below=body.escalate_below,
            po_required_above=body.po_required_above,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _serialize(block)


@router.delete(
    "/vendors/{vendor_name}/thresholds",
    response_model=ThresholdsOut,
)
def clear_vendor_threshold_policy(
    vendor_name: str,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    profile = db.get_vendor_profile(user.organization_id, vendor_name)
    if profile is None:
        raise HTTPException(status_code=404, detail="vendor_not_found")
    set_vendor_threshold_overrides(
        db,
        user.organization_id,
        vendor_name,
        clear=True,
    )
    return ThresholdsOut()


# ── Resolved view ──────────────────────────────────────────────────


@router.get("/policy/thresholds/resolve", response_model=ResolvedOut)
def resolve_threshold_policy(
    vendor: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    result = resolve_thresholds(
        db,
        organization_id=user.organization_id,
        vendor_name=vendor,
    )
    return ResolvedOut(**result.to_dict())
