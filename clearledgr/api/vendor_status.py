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
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import (
    TokenData,
    get_current_user,
    has_admin_access,
)
from clearledgr.core.database import get_db
from clearledgr.core.org_utils import require_org

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/vendors", tags=["vendor-status"])


_VALID_STATUS = {"active", "blocked", "archived"}


def _resolve_org_id(user: TokenData, requested: Optional[str]) -> str:
    """Tenant gate: clamp to the caller's organization."""
    return require_org(user, requested=requested)


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
    revalidation_summary: Optional[Dict[str, Any]] = None
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

        # Wave 1 / A11 — eager re-validation. A status flip to
        # blocked / archived must propagate to in-flight AP items
        # so the operator sees them in the exception queue without
        # waiting for the next gate-time recheck. The reverse path
        # (active again) clears any prior ``vendor_blocked`` flags
        # only on items the operator explicitly resolves — we don't
        # auto-clear because the original cause may still apply.
        revalidate_reason = None
        if new_status == "blocked":
            revalidate_reason = "vendor_blocked"
        elif new_status == "archived":
            revalidate_reason = "vendor_status_archived"

        if revalidate_reason is not None:
            from clearledgr.services.vendor_revalidation import (
                revalidate_in_flight_ap_items,
            )
            try:
                rv_result = revalidate_in_flight_ap_items(
                    db,
                    organization_id=org_id,
                    vendor_name=vendor_name,
                    reason=revalidate_reason,
                    actor=actor_email,
                )
                revalidation_summary = rv_result.to_dict()
            except Exception as exc:
                logger.warning(
                    "[vendor_status] revalidation failed for org=%s vendor=%s: %s",
                    org_id, vendor_name, exc,
                )

    response_body: Dict[str, Any] = {
        "organization_id": org_id,
        "vendor_name": vendor_name,
        "status": new_status,
        "status_reason": body.reason or None,
        "status_changed_at": updated.get("status_changed_at"),
        "status_changed_by": updated.get("status_changed_by"),
    }
    if revalidation_summary is not None:
        response_body["revalidation"] = revalidation_summary
    return response_body


# ─── Module 4 Pass D — Reverse vendor sync (Clearledgr → ERP) ────────


@router.post("/{vendor_name}/sync-erp")
async def sync_vendor_to_erp(
    vendor_name: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Push the in-Clearledgr vendor profile to the connected ERP.

    Admin-gated. Returns a structured result the SPA can render:
    ok / no_change / not_supported / no_erp_id / failed. Audit
    emission is handled by the push service so callers never lose
    the trail even if the network call timed out mid-flight.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)

    from clearledgr.services.vendor_erp_push import push_vendor_to_erp

    result = await push_vendor_to_erp(
        organization_id=org_id, vendor_name=vendor_name,
    )
    body = result.to_dict()
    # Surface the right HTTP status so the SPA can branch without
    # parsing the result dict twice. ``not_supported`` is a 200
    # because the request itself is well-formed — the operator
    # learns the ERP doesn't support reverse sync via the body.
    if result.status == "ok" or result.status == "no_change":
        return body
    if result.status == "not_supported":
        return body
    if result.status == "no_erp_id":
        raise HTTPException(status_code=409, detail=body)
    raise HTTPException(status_code=502, detail=body)


# ─── Module 4 Pass E — Bulk vendor import via CSV ─────────────────────


class CSVImportRequest(BaseModel):
    csv_text: str = Field(..., min_length=1, max_length=1_200_000)


@router.post("/import/preview")
def preview_vendor_csv_import(
    body: CSVImportRequest,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Dry-run preview of a CSV upload — no DB writes.

    Returns per-row validation results so the SPA can render a
    table with green/red rows. Operators fix the errors in their
    sheet, re-paste, and only commit once everything's clean.
    """
    _require_admin(user)
    _resolve_org_id(user, organization_id)
    from clearledgr.services.vendor_csv_import import parse_and_validate
    return parse_and_validate(body.csv_text).to_dict()


@router.post("/import/commit")
def commit_vendor_csv_import(
    body: CSVImportRequest,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Re-parse the CSV and apply each valid row.

    The dashboard normally calls /preview first to render the
    operator's review screen, then this endpoint with the same CSV
    text on commit. We re-parse rather than passing the parsed rows
    over the wire so the server is the single source of truth and
    a malicious client can't claim a row passed validation when it
    didn't.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)

    from clearledgr.services.vendor_csv_import import (
        commit_rows,
        parse_and_validate,
    )

    preview = parse_and_validate(body.csv_text)
    if preview.fatal_error:
        raise HTTPException(
            status_code=422,
            detail={"reason": "csv_invalid", "fatal_error": preview.fatal_error},
        )

    db = get_db()
    actor_email = (
        getattr(user, "email", None)
        or str(getattr(user, "user_id", "") or "unknown")
    )
    summary = commit_rows(db, org_id, preview.rows, actor=actor_email)
    return {
        "organization_id": org_id,
        **summary,
        "preview_summary": {
            "total_rows": preview.total_rows,
            "valid_rows": preview.valid_rows,
            "error_rows": preview.error_rows,
        },
    }


@router.post("/{vendor_name}/verify-registration")
def verify_vendor_registration(
    vendor_name: str,
    organization_id: Optional[str] = Query(default=None),
    registration_number: Optional[str] = Query(default=None),
    jurisdiction: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Module 4 spec line 158 — business registry vendor verification.

    Defaults to OpenCorporates (broad jurisdiction coverage, free
    tier for low volume). Can switch to Companies House (UK) by
    setting REGISTRY_PROVIDER=companies_house in the environment.

    The result is stamped on the vendor profile so the next time
    someone views the vendor detail page they see the verification
    state without re-querying.
    """
    from clearledgr.services.opencorporates_verifier import verify_vendor_registration as _verify
    org_id = require_org(user, requested=organization_id)

    db = get_db()
    profile = db.get_vendor_profile(org_id, vendor_name) if hasattr(db, "get_vendor_profile") else None
    if not profile:
        # Auto-create a stub profile so we have somewhere to stamp the result.
        if hasattr(db, "ensure_vendor_profile"):
            profile = db.ensure_vendor_profile(org_id, vendor_name)
        else:
            profile = {"organization_id": org_id, "vendor_name": vendor_name}

    result = _verify(
        company_name=vendor_name,
        registration_number=registration_number or profile.get("registration_number"),
        jurisdiction=jurisdiction or profile.get("jurisdiction"),
    )

    # Persist the verification result on the vendor profile.
    if hasattr(db, "update_vendor_profile"):
        try:
            db.update_vendor_profile(
                org_id, vendor_name,
                registry_verified=(result.get("status") == "verified"),
                registry_verification_at=datetime.now(timezone.utc).isoformat(),
                registry_verification_provider=result.get("registry"),
                registry_verification_payload=result,
            )
        except Exception as exc:
            logger.debug("[vendor.verify-registration] persist failed: %s", exc)

    return {"vendor_name": vendor_name, **result}
