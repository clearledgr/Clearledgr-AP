"""Sanctions screening API (Wave 3 / E1).

Operator surface for the EU/UK launch:

  * POST /api/workspace/vendors/{vendor_name}/sanctions-screen
      Re-run a sanctions screen for a single vendor on demand.
  * GET  /api/workspace/sanctions-checks?status=hit&review_status=open
      Worklist of sanctions hits that need review.
  * GET  /api/workspace/sanctions-checks/{check_id}
      Single check with raw provider payload (compliance audit).
  * POST /api/workspace/sanctions-checks/{check_id}/clear
      Operator clears a hit as a false positive — review_status flips
      to 'cleared' and the vendor disposition rolls back to 'clear'
      if this was the latest check.
  * POST /api/workspace/sanctions-checks/{check_id}/confirm
      Operator confirms the hit — review_status='confirmed', vendor
      disposition flips to 'blocked'. Future payments are gated.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.sanctions_screening import (
    ScreeningResult,
    screen_vendor,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["sanctions"],
)


# ── Models ──────────────────────────────────────────────────────────


class SanctionsCheckOut(BaseModel):
    id: str
    organization_id: str
    vendor_name: str
    check_type: str
    provider: str
    provider_reference: Optional[str] = None
    status: str
    matches: Optional[List[Dict[str, Any]]] = None
    evidence: Optional[Dict[str, Any]] = None
    checked_at: str
    checked_by: Optional[str] = None
    review_status: str
    cleared_at: Optional[str] = None
    cleared_by: Optional[str] = None
    cleared_reason: Optional[str] = None


class ScreeningResultOut(BaseModel):
    vendor_name: str
    status: str
    sanctions_status: str
    check_id: Optional[str] = None
    matches_count: int = 0
    revalidated_ap_items: int = 0
    error: Optional[str] = None


class ScreenRequestBody(BaseModel):
    country: Optional[str] = Field(None, max_length=4)


class ReviewBody(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


def _serialize_check(row: Dict[str, Any]) -> SanctionsCheckOut:
    return SanctionsCheckOut(
        id=row["id"],
        organization_id=row["organization_id"],
        vendor_name=row["vendor_name"],
        check_type=row["check_type"],
        provider=row["provider"],
        provider_reference=row.get("provider_reference"),
        status=row["status"],
        matches=row.get("matches"),
        evidence=row.get("evidence"),
        checked_at=row["checked_at"],
        checked_by=row.get("checked_by"),
        review_status=row.get("review_status") or "open",
        cleared_at=row.get("cleared_at"),
        cleared_by=row.get("cleared_by"),
        cleared_reason=row.get("cleared_reason"),
    )


# ── Endpoints ───────────────────────────────────────────────────────


@router.post(
    "/vendors/{vendor_name}/sanctions-screen",
    response_model=ScreeningResultOut,
)
def trigger_vendor_screen(
    vendor_name: str,
    body: Optional[ScreenRequestBody] = None,
    user: TokenData = Depends(get_current_user),
):
    """Run an on-demand sanctions screen for one vendor."""
    db = get_db()
    profile = db.get_vendor_profile(user.organization_id, vendor_name)
    if profile is None:
        raise HTTPException(status_code=404, detail="vendor_not_found")

    country = (body.country if body else None) or None
    result: ScreeningResult = screen_vendor(
        db,
        organization_id=user.organization_id,
        vendor_name=vendor_name,
        country=country,
        actor=user.user_id,
    )
    return ScreeningResultOut(
        vendor_name=result.vendor_name,
        status=result.status,
        sanctions_status=result.sanctions_status,
        check_id=result.check_id,
        matches_count=result.matches_count,
        revalidated_ap_items=result.revalidated_ap_items,
        error=result.error,
    )


@router.get(
    "/sanctions-checks",
    response_model=List[SanctionsCheckOut],
)
def list_sanctions_checks(
    status: Optional[str] = Query(default=None),
    review_status: Optional[str] = Query(default=None),
    vendor_name: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    try:
        rows = db.list_sanctions_checks(
            user.organization_id,
            vendor_name=vendor_name,
            status=status,
            review_status=review_status,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [_serialize_check(r) for r in rows]


@router.get(
    "/sanctions-checks/{check_id}",
    response_model=SanctionsCheckOut,
)
def get_sanctions_check(
    check_id: str,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    row = db.get_sanctions_check(check_id)
    if row is None or row.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="check_not_found")
    return _serialize_check(row)


@router.post(
    "/sanctions-checks/{check_id}/clear",
    response_model=SanctionsCheckOut,
)
def clear_sanctions_check(
    check_id: str,
    body: ReviewBody,
    user: TokenData = Depends(get_current_user),
):
    """Mark a sanctions hit as a false positive.

    If this is the vendor's latest check, the rolled-up
    sanctions_status flips back to 'clear' so payments resume.
    """
    db = get_db()
    row = db.get_sanctions_check(check_id)
    if row is None or row.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="check_not_found")
    db.update_sanctions_check_review(
        check_id,
        review_status="cleared",
        cleared_by=user.user_id,
        cleared_reason=body.reason,
    )
    # Roll up: only flip the vendor disposition if THIS is the latest
    # check. Operators may clear an old hit while a newer hit is still
    # active.
    latest = db.get_latest_sanctions_check(
        user.organization_id, row["vendor_name"],
    )
    if latest and latest["id"] == check_id:
        db.upsert_vendor_profile(
            user.organization_id, row["vendor_name"],
            sanctions_status="clear",
        )
    fresh = db.get_sanctions_check(check_id)
    return _serialize_check(fresh)


@router.post(
    "/sanctions-checks/{check_id}/confirm",
    response_model=SanctionsCheckOut,
)
def confirm_sanctions_check(
    check_id: str,
    body: ReviewBody,
    user: TokenData = Depends(get_current_user),
):
    """Confirm a sanctions hit as a real match.

    Vendor disposition flips to 'blocked' — future payments are gated
    by :func:`gate_payment_against_sanctions`.
    """
    db = get_db()
    row = db.get_sanctions_check(check_id)
    if row is None or row.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="check_not_found")
    db.update_sanctions_check_review(
        check_id,
        review_status="confirmed",
        cleared_by=user.user_id,
        cleared_reason=body.reason,
    )
    db.upsert_vendor_profile(
        user.organization_id, row["vendor_name"],
        sanctions_status="blocked",
    )
    fresh = db.get_sanctions_check(check_id)
    return _serialize_check(fresh)
