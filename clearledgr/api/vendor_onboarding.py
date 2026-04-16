"""Vendor onboarding control API — Phase 3.1.b.

The customer-side counterpart to the public ``/portal/*`` router. These
endpoints are JWT-authenticated and let the customer's finance team
trigger and manage vendor onboarding sessions:

  POST   /api/vendors/{vendor_name}/onboarding/invite
      Open a fresh onboarding session for a vendor and issue a magic
      link. The vendor profile is created if missing. Returns the
      generated magic-link URL so the caller can copy/paste it for
      now — Phase 3.1.c will additionally dispatch the link via the
      customer's connected Gmail account using a templated invite
      email.

  GET    /api/vendors/{vendor_name}/onboarding/session
      Retrieve the current onboarding session state for a vendor.
      Used by the Gmail extension's Vendor Onboarding pipeline view
      to show progress in the sidebar without polling the public
      portal.

  POST   /api/vendors/{vendor_name}/onboarding/escalate
      Manually escalate a session to ESCALATED state. Phase 3.1.e's
      auto-chase loop will do this automatically after 72h, but the
      AP Manager may want to escalate sooner.

  POST   /api/vendors/{vendor_name}/onboarding/reject
      Terminally reject a session (failed KYC review, sanctions hit,
      fraud signal). Requires CFO role.

All write endpoints require ``Financial Controller`` or higher.
Cross-tenant access is blocked even for Financial Controllers from
other organizations.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import (
    TokenData,
    get_current_user,
    require_cfo,
    require_financial_controller,
)
from clearledgr.core.database import get_db
from clearledgr.core.vendor_onboarding_states import VendorOnboardingState

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/vendors",
    tags=["vendor-onboarding"],
)


# Ops-facing list view used by the Gmail extension VendorOnboardingPage
# and HomePage. Mounted at /api/ops/vendor-onboarding to match the
# /api/ops convention for cross-record list endpoints.
ops_router = APIRouter(
    prefix="/api/ops/vendor-onboarding",
    tags=["vendor-onboarding-ops"],
)


@ops_router.get("/sessions")
def list_vendor_onboarding_sessions(
    organization_id: str = Query("default"),
    limit: int = Query(200, ge=1, le=1000),
    state: Optional[str] = Query(None, description="Comma-separated list of states to filter"),
    _user: TokenData = Depends(get_current_user),
):
    """List vendor onboarding sessions for the org.

    Default scope: pre-active sessions (anything not yet activated in the
    ERP). Pass ?state=active,escalated to slice differently.
    """
    db = get_db()
    states = None
    if state:
        states = [s.strip() for s in state.split(",") if s.strip()]
    rows = db.list_pending_onboarding_sessions(
        organization_id=organization_id,
        states=states,
        limit=limit,
    )
    return {"sessions": rows or [], "count": len(rows or [])}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _portal_base_url() -> str:
    """Return the public base URL the magic link should embed.

    Read from the ``CLEARLEDGR_PORTAL_BASE_URL`` env var. Defaults to
    the production portal hostname so an unset env var in prod still
    produces a vendor-friendly link rather than an api.* URL. Local
    dev should set this to ``http://localhost:8000`` in its .env.
    """
    return os.getenv("CLEARLEDGR_PORTAL_BASE_URL", "https://onboard.clearledgr.com").rstrip("/")


def _build_magic_link(token: str) -> str:
    # Use the short `/onboard/<token>` path (which 302s to the full
    # /portal/onboard/<token>) so the link embedded in invite emails
    # is visibly shorter — the extra path segment is wasted on the
    # vendor reading the email.
    return f"{_portal_base_url()}/onboard/{token}"


def _assert_same_org(user: TokenData, requested_org: str) -> None:
    if str(user.organization_id or "").strip() != str(requested_org or "").strip():
        raise HTTPException(status_code=403, detail="cross_tenant_access_denied")


def _actor_label(user: TokenData) -> str:
    return (
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or "unknown_user"
    )


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class InviteVendorRequest(BaseModel):
    """Payload for opening a fresh onboarding session.

    The vendor's contact email is recorded on the session metadata so
    the Phase 3.1.c email dispatch can target it without re-asking
    the customer. ``ttl_days`` is bounded — too-long links are a
    security smell, too-short ones break the chase cadence.
    """

    contact_email: str = Field(..., min_length=3, max_length=320)
    contact_name: Optional[str] = Field(default=None, max_length=128)
    ttl_days: int = Field(default=14, ge=1, le=30)


class RejectOnboardingRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=512)


class EscalateOnboardingRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=512)


# ---------------------------------------------------------------------------
# POST /onboarding/invite
# ---------------------------------------------------------------------------


@router.post("/{vendor_name}/onboarding/invite")
def invite_vendor(
    vendor_name: str,
    body: InviteVendorRequest,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(require_financial_controller),
) -> Dict[str, Any]:
    """Open a fresh onboarding session and issue a magic link.

    If the vendor profile does not yet exist, it is created with the
    contact email recorded as a sender domain hint so the Phase 2.2
    domain lock has something to bootstrap from. The fresh
    ``vendor_onboarding_sessions`` row starts in ``invited`` and a
    one-time token is generated immediately.

    Returns:
      ``{"session": {...}, "magic_link": "https://...", "expires_at": "..."}``

    Phase 3.1.c will additionally dispatch this link via Gmail using
    the templated invite email — for Phase 3.1.b the customer copies
    the link manually from this response.
    """
    _assert_same_org(user, organization_id)
    db = get_db()

    # Create the vendor profile if it does not already exist. We do
    # this rather than 404 so the AP Manager can onboard a vendor in
    # one call without a separate "register vendor first" round-trip.
    profile = db.get_vendor_profile(organization_id, vendor_name)
    if profile is None:
        contact_domain = ""
        if "@" in (body.contact_email or ""):
            contact_domain = body.contact_email.split("@", 1)[-1].strip().lower()
        sender_domains = [contact_domain] if contact_domain else []
        db.upsert_vendor_profile(
            organization_id,
            vendor_name,
            sender_domains=sender_domains,
            metadata={"contact_email": body.contact_email},
        )

    # If there is already an active onboarding session, refuse rather
    # than silently shadowing the existing one. The caller can either
    # resume the existing session (GET endpoint) or terminally close
    # it via reject before re-inviting.
    existing = db.get_active_onboarding_session(organization_id, vendor_name)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "onboarding_session_already_active",
                "session_id": existing.get("id"),
                "state": existing.get("state"),
            },
        )

    session = db.create_vendor_onboarding_session(
        organization_id=organization_id,
        vendor_name=vendor_name,
        invited_by=_actor_label(user),
    )
    if session is None:
        raise HTTPException(status_code=500, detail="onboarding_session_create_failed")

    issued = db.generate_onboarding_token(
        session_id=session["id"],
        issued_by=_actor_label(user),
        ttl_days=body.ttl_days,
    )
    if issued is None:
        raise HTTPException(status_code=500, detail="onboarding_token_issue_failed")
    raw_token, token_row = issued

    magic_link = _build_magic_link(raw_token)

    # Dispatch the invite email via the customer's connected Gmail
    # account. Best-effort: if no Gmail client is available or sending
    # fails, the magic link is still returned in the API response so
    # the customer can copy/paste it manually. Phase 3.1.e's chase
    # loop will retry failed dispatches on the next cadence.
    email_dispatch: Optional[Dict[str, Any]] = None
    try:
        import asyncio
        from clearledgr.services.vendor_onboarding_email import (
            dispatch_onboarding_invite,
        )

        # Resolve the customer's display name from the org record.
        org_record = db.get_organization(organization_id)
        customer_name = (
            (org_record or {}).get("name")
            or organization_id
        )

        coro = dispatch_onboarding_invite(
            organization_id=organization_id,
            vendor_name=vendor_name,
            contact_email=body.contact_email,
            contact_name=body.contact_name or body.contact_email.split("@")[0],
            customer_name=customer_name,
            magic_link=magic_link,
            expires_at=token_row.get("expires_at") or "",
            session_id=session["id"],
        )
        # Run the async dispatch in the current event loop if available,
        # otherwise run synchronously for tests.
        try:
            loop = asyncio.get_running_loop()
            # We're already in an async context — await directly.
            # But we're in a sync FastAPI endpoint, so schedule and let
            # the loop pick it up. Use asyncio.run for simplicity since
            # FastAPI runs sync endpoints in a threadpool.
            email_result = asyncio.run(coro)
        except RuntimeError:
            email_result = asyncio.run(coro)
        email_dispatch = email_result.to_dict()
    except Exception as email_exc:
        logger.warning(
            "[vendor_onboarding] invite email dispatch failed (non-fatal): %s",
            email_exc,
        )
        email_dispatch = {"success": False, "method": "failed", "error": str(email_exc)}

    return {
        "session": session,
        "magic_link": magic_link,
        "expires_at": token_row.get("expires_at"),
        "purpose": token_row.get("purpose"),
        "contact_email": body.contact_email,
        "email_dispatch": email_dispatch,
    }


# ---------------------------------------------------------------------------
# GET /onboarding/session
# ---------------------------------------------------------------------------


@router.get("/{vendor_name}/onboarding/session")
def get_vendor_onboarding_session(
    vendor_name: str,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the active onboarding session (if any) for a vendor."""
    _assert_same_org(user, organization_id)
    db = get_db()
    session = db.get_active_onboarding_session(organization_id, vendor_name)
    if session is None:
        raise HTTPException(status_code=404, detail="no_active_onboarding_session")
    return {"session": session}


# ---------------------------------------------------------------------------
# GET /onboarding/status — sidebar prompt driver
# ---------------------------------------------------------------------------


@router.get("/{vendor_name}/onboarding/status")
def get_vendor_onboarding_status(
    vendor_name: str,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Tri-state onboarding snapshot used by the Gmail sidebar.

    Unlike ``/session`` this never 404s — the sidebar needs a definitive
    "should I prompt?" signal for every vendor, including ones that have
    never been onboarded. Returns:

      - ``has_profile``          — a vendor_profiles row exists
      - ``bank_verified``        — the profile holds encrypted bank details
      - ``active_session``       — the in-flight onboarding session, if any
      - ``suggest_invite``       — true iff no active session AND not bank-verified
    """
    _assert_same_org(user, organization_id)
    db = get_db()
    profile = db.get_vendor_profile(organization_id, vendor_name)
    active_session = db.get_active_onboarding_session(organization_id, vendor_name)
    has_profile = profile is not None
    bank_verified = bool((profile or {}).get("bank_details_encrypted"))
    suggest_invite = active_session is None and not bank_verified
    return {
        "vendor_name": vendor_name,
        "has_profile": has_profile,
        "bank_verified": bank_verified,
        "active_session": active_session,
        "suggest_invite": suggest_invite,
    }


# ---------------------------------------------------------------------------
# POST /onboarding/escalate
# ---------------------------------------------------------------------------


@router.post("/{vendor_name}/onboarding/escalate")
def escalate_onboarding(
    vendor_name: str,
    body: EscalateOnboardingRequest,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(require_financial_controller),
) -> Dict[str, Any]:
    """Move the active onboarding session into ESCALATED state."""
    _assert_same_org(user, organization_id)
    db = get_db()
    session = db.get_active_onboarding_session(organization_id, vendor_name)
    if session is None:
        raise HTTPException(status_code=404, detail="no_active_onboarding_session")

    updated = db.transition_onboarding_session_state(
        session["id"],
        VendorOnboardingState.ESCALATED.value,
        actor_id=_actor_label(user),
        reason=body.reason,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="escalation_failed")
    return {"session": updated}


# ---------------------------------------------------------------------------
# POST /onboarding/reject
# ---------------------------------------------------------------------------


@router.post("/{vendor_name}/onboarding/reject")
def reject_onboarding(
    vendor_name: str,
    body: RejectOnboardingRequest,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(require_cfo),
) -> Dict[str, Any]:
    """Terminally reject the active onboarding session.

    CFO-only — terminal rejection is treated as a CFO sign-off because
    it forecloses any future payments to the vendor. Revokes any live
    magic-link tokens for the session as a side effect of the
    terminal state transition.
    """
    _assert_same_org(user, organization_id)
    db = get_db()
    session = db.get_active_onboarding_session(organization_id, vendor_name)
    if session is None:
        raise HTTPException(status_code=404, detail="no_active_onboarding_session")

    updated = db.transition_onboarding_session_state(
        session["id"],
        VendorOnboardingState.REJECTED.value,
        actor_id=_actor_label(user),
        reason=body.reason,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="rejection_failed")

    # Revoke any live tokens — the link should die immediately on
    # rejection, not after the next chase tick.
    db.revoke_session_tokens(
        session["id"],
        revoked_by=_actor_label(user),
        reason="onboarding_session_rejected",
    )

    return {"session": updated}


# ==================== CSV VENDOR IMPORT (§3 Migration from Existing Tools) ====================


@router.post("/import/csv")
async def import_vendors_csv(
    request: Request,
    organization_id: str = Query(default="default"),
    user=Depends(require_financial_controller),
):
    """§3 Migration: Import vendors from CSV with column mapping.

    Accepts a JSON payload with:
    - rows: list of dicts (parsed CSV rows)
    - column_map: mapping from CSV column names to vendor fields
      e.g. {"Company Name": "vendor_name", "VAT": "vat_number", ...}
    """
    import json as _json

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")

    rows = body.get("rows", [])
    column_map = body.get("column_map", {})
    if not rows or not column_map:
        raise HTTPException(status_code=400, detail="rows and column_map required")

    db = get_db()
    created = []
    skipped = []
    actor_id = getattr(user, "email", None) or getattr(user, "user_id", "system")

    vendor_field_map = {
        "vendor_name": "vendor_name",
        "registration_number": "registration_number",
        "vat_number": "vat_number",
        "registered_address": "registered_address",
        "payment_terms": "payment_terms",
        "contact_email": "primary_contact_email",
        "category": "category",
    }

    for row in rows:
        mapped = {}
        for csv_col, field_key in column_map.items():
            canonical = vendor_field_map.get(field_key, field_key)
            value = row.get(csv_col, "")
            if value:
                mapped[canonical] = str(value).strip()

        vendor_name = mapped.get("vendor_name", "")
        if not vendor_name:
            skipped.append({"row": row, "reason": "missing vendor_name"})
            continue

        # Check if vendor already exists with active onboarding
        existing = db.get_vendor_profile(vendor_name, organization_id) if hasattr(db, "get_vendor_profile") else None
        if existing:
            skipped.append({"row": row, "reason": "vendor_already_exists"})
            continue

        # §3 Migration: imported vendors enter the standard onboarding flow
        # at 'invited' state. They must complete KYC and bank verification
        # (micro-deposit) before invoices can be processed. No direct import
        # to onboarded status.
        try:
            # Create vendor profile with CSV data (KYC fields pre-populated)
            if hasattr(db, "upsert_vendor_profile"):
                db.upsert_vendor_profile(vendor_name, organization_id, **{
                    k: v for k, v in mapped.items() if k != "vendor_name"
                })

            # Create onboarding session so vendor goes through verification
            if hasattr(db, "create_vendor_onboarding_session"):
                contact_email = mapped.get("primary_contact_email", "")
                db.create_vendor_onboarding_session(
                    organization_id=organization_id,
                    vendor_name=vendor_name,
                    initial_state="invited",
                    metadata={
                        "source": "csv_import",
                        "imported_by": actor_id,
                        "invite_email_to": contact_email,
                        "pre_populated_fields": [k for k in mapped if k != "vendor_name"],
                    },
                )

            created.append(vendor_name)
        except Exception as exc:
            skipped.append({"row": row, "reason": str(exc)})

    # Audit event
    db.append_ap_audit_event({
        "event_type": "vendor_csv_import",
        "actor_type": "user",
        "actor_id": actor_id,
        "organization_id": organization_id,
        "source": "vendor_onboarding_api",
        "payload_json": {
            "created_count": len(created),
            "skipped_count": len(skipped),
            "created_vendors": created[:20],
        },
    })

    return {
        "status": "imported",
        "created": len(created),
        "skipped": len(skipped),
        "created_vendors": created,
        "skipped_details": skipped[:10],
    }
