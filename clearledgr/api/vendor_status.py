"""Vendor allowlist/blocklist API (Module 4 Pass B).

Per scope §Module 4: customer admins can mark a vendor as blocked
(no new invoices accepted) or active (default). Status writes flow
through ``VendorStore.set_vendor_status`` which validates the token
against the canonical set; the bill-validation gate
(``erp_router.pre_post_validate``) then refuses to post any AP item
whose vendor is blocked.

Endpoints:
  GET   /api/vendors/{vendor_name}/status
    — Read current status + reason + change attribution. Any
      authenticated workspace member.
  PATCH /api/vendors/{vendor_name}/status
    — Set status (active | blocked | archived) with an optional
      reason. Admin/owner gated. Audit-emitted with before/after.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import (
    TokenData,
    get_current_user,
    has_admin_access,
)
from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/vendors", tags=["vendor-status"])


_VALID_STATUS = {"active", "blocked", "archived"}


def _resolve_org_id(user: TokenData, requested: Optional[str]) -> str:
    """Tenant gate: clamp to the caller's organization."""
    org_id = str(requested or user.organization_id or "default").strip() or "default"
    if org_id != str(user.organization_id or "").strip():
        raise HTTPException(status_code=403, detail="org_access_denied")
    return org_id


def _require_admin(user: TokenData) -> None:
    if not has_admin_access(user.role):
        raise HTTPException(status_code=403, detail="admin_role_required")


class VendorStatusRequest(BaseModel):
    status: str = Field(..., min_length=1, max_length=32)
    reason: Optional[str] = Field(default=None, max_length=300)


@router.get("/{vendor_name}/status")
def get_vendor_status(
    vendor_name: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Return the current status row for a vendor.

    ``404`` when the vendor profile doesn't exist; that's distinct
    from ``status='active'`` (vendor exists, never been blocked).
    """
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    profile = db.get_vendor_profile(org_id, vendor_name)
    if not profile:
        raise HTTPException(status_code=404, detail="vendor_not_found")
    return {
        "organization_id": org_id,
        "vendor_name": profile.get("vendor_name") or vendor_name,
        "status": str(profile.get("status") or "active").strip().lower(),
        "status_reason": profile.get("status_reason") or None,
        "status_changed_at": profile.get("status_changed_at") or None,
        "status_changed_by": profile.get("status_changed_by") or None,
    }


@router.patch("/{vendor_name}/status")
def patch_vendor_status(
    vendor_name: str,
    body: VendorStatusRequest,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Set the vendor's allowlist/blocklist status.

    Admin/owner only. Validates the status token against the
    canonical set + emits a ``vendor_status_changed`` audit event
    with before/after so compliance can reconstruct who changed
    which vendor's status, when.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)

    new_status = (body.status or "").strip().lower()
    if new_status not in _VALID_STATUS:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "invalid_status",
                "allowed": sorted(_VALID_STATUS),
            },
        )

    db = get_db()
    existing = db.get_vendor_profile(org_id, vendor_name)
    if not existing:
        raise HTTPException(status_code=404, detail="vendor_not_found")
    before_status = str(existing.get("status") or "active").strip().lower()

    actor_email = (
        getattr(user, "email", None)
        or str(getattr(user, "user_id", "") or "unknown")
    )
    try:
        updated = db.set_vendor_status(
            organization_id=org_id,
            vendor_name=vendor_name,
            status=new_status,
            reason=body.reason,
            actor=actor_email,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"reason": "validation_failed", "message": str(exc)},
        )
    if updated is None:
        raise HTTPException(status_code=404, detail="vendor_not_found")

    # Only audit when something actually changed — re-saving the
    # same status should not flood the audit log with no-ops.
    if before_status != new_status:
        try:
            db.append_audit_event({
                "event_type": "vendor_status_changed",
                "actor_type": "user",
                "actor_id": str(getattr(user, "user_id", "") or "unknown"),
                "organization_id": org_id,
                "box_id": vendor_name,
                "box_type": "vendor",
                "source": "workspace_admin",
                "payload_json": {
                    "actor_email": actor_email,
                    "before": before_status,
                    "after": new_status,
                    "reason": body.reason or None,
                },
            })
        except Exception as exc:
            logger.warning(
                "[vendor_status] audit emit failed for org=%s vendor=%s: %s",
                org_id, vendor_name, exc,
            )

    return {
        "organization_id": org_id,
        "vendor_name": vendor_name,
        "status": new_status,
        "status_reason": body.reason or None,
        "status_changed_at": updated.get("status_changed_at"),
        "status_changed_by": updated.get("status_changed_by"),
    }
