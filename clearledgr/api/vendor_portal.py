"""Vendor onboarding portal router — Phase 3.1.b.

The only Clearledgr endpoint surface that accepts unauthenticated
traffic. Vendors land here via a one-time magic-link emailed to them by
their customer's AP team. The router serves a single multi-section HTML
form (server-rendered Jinja2, zero JS framework) and two POST handlers
that advance the vendor onboarding state machine.

Routes
======

  GET   /portal/onboard/{token}
        Render the onboarding form. Returns the same form regardless of
        which stage the session is in — the template uses CSS classes
        to disable inactive sections so the vendor sees the full
        progress at a glance. 410 Gone if the link is expired/revoked.

  POST  /portal/onboard/{token}/kyc
        Form-encoded body: registration_number, vat_number,
        registered_address, director_names (newline-separated).
        Validates required fields, calls VendorStore.update_vendor_kyc
        with the new values, transitions the session from
        invited|kyc → bank_verify, returns a 303 redirect
        back to the GET form so the vendor sees the updated state.

  POST  /portal/onboard/{token}/bank-details
        Form-encoded body: iban, account_holder_name, bank_name (opt).
        Encrypts the bank details via VendorStore.set_vendor_bank_details
        (Fernet column encryption from Phase 2.1.a — never plaintext),
        transitions bank_verify → bank_verified, returns 303. V1 is a
        direct transition; once the Adyen (EU) and TrueLayer (UK/RoW)
        verifier adapters land, that edge will be gated on provider-
        reported verification.

Design rules
============

* **Server-rendered HTML, classic POST-Redirect-GET.** No JavaScript
  framework. The form posts via standard HTML form-data, the handler
  returns a 303 redirect, the browser re-fetches the GET. Works on
  any browser, any geographic region, any network condition. The
  whole portal page weighs less than 30KB including CSS.

* **No CSRF tokens.** The single-use, hash-keyed, short-TTL magic
  link IS the auth context. There is no JWT, no cookie, no session to
  fixate on. The only thing the link grants is the ability to modify
  the specific vendor onboarding session it was issued for. CSRF is
  therefore moot — there is no cross-site state to steal.

* **Bank details NEVER touch logs.** The IBAN and account holder
  name go straight from the form into Fernet encryption, then into
  the bank_details_encrypted column. The audit event records field
  names only, never values. Per the §19 plaintext-free discipline
  shared with Phase 2.1.a.

* **All errors are vendor-friendly.** No stack traces, no reason
  codes, no JSON. Validation errors render the same form again with
  a flash_error message. Expired links render the expired.html
  template. Internal errors render expired.html as a graceful
  fallback so the vendor isn't dumped at a 500 page.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from clearledgr.core.database import get_db
from clearledgr.core.portal_auth import PortalSession, require_portal_token
from clearledgr.core.vendor_onboarding_states import (
    IllegalVendorOnboardingTransitionError,
    VendorOnboardingState,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Template engine setup
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates",
    "portal",
)
templates = Jinja2Templates(directory=_TEMPLATE_DIR)


router = APIRouter(
    prefix="/portal",
    tags=["vendor-portal"],
    include_in_schema=False,  # Public surface — keep out of /docs
)


# Shortcut router: lets invite emails embed `…/onboard/{token}` instead
# of the full `…/portal/onboard/{token}` path. Makes the magic link
# visibly shorter and lets us keep the /portal/* prefix internally for
# route segmentation without leaking it into vendor-visible URLs.
shortcut_router = APIRouter(include_in_schema=False)


@shortcut_router.get("/onboard/{token}")
def onboard_shortcut(token: str):
    from urllib.parse import quote
    return RedirectResponse(url=f"/portal/onboard/{quote(token, safe='')}", status_code=302)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_form_context(
    request: Request,
    portal: PortalSession,
    *,
    flash_message: Optional[str] = None,
    flash_error: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble the variables the onboard.html template expects."""
    db = get_db()
    kyc = db.get_vendor_kyc(portal.organization_id, portal.vendor_name) or {}
    profile = db.get_vendor_profile(portal.organization_id, portal.vendor_name) or {}
    # Resolve the customer (buyer) name so the portal can tell the vendor
    # who they're onboarding with. Vendors often onboard with multiple
    # customers in parallel — "Onboarding with Acme" beats generic
    # Clearledgr branding.
    customer_name = ""
    try:
        org = db.get_organization(portal.organization_id) or {}
        customer_name = str(org.get("name") or org.get("display_name") or "").strip()
    except Exception:  # noqa: BLE001
        customer_name = ""
    return {
        "request": request,
        "token": _token_from_request(request),
        "vendor_name": portal.vendor_name,
        "customer_name": customer_name,
        "state": portal.onboarding_state,
        "kyc": kyc,
        "bank_submitted": bool(profile.get("bank_details_encrypted")),
        "flash_message": flash_message,
        "flash_error": flash_error,
    }


def _token_from_request(request: Request) -> str:
    """Extract the magic-link token from the URL path.

    Used by template helpers that need to render form action URLs
    without re-passing the token through every render call.
    """
    return str(request.path_params.get("token") or "")


def _split_director_names(raw: str) -> List[str]:
    if not raw:
        return []
    out: List[str] = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        cleaned = line.strip()
        if cleaned:
            out.append(cleaned)
    return out


def _safe_transition(
    session_id: str,
    target: VendorOnboardingState,
    actor_id: str,
    metadata_patch: Optional[Dict[str, Any]] = None,
) -> bool:
    """Attempt a state transition, swallow IllegalTransitionError.

    Returns True on success. Returns False if the transition is illegal
    (e.g. the vendor re-submits the KYC form when they're already in
    bank-verification stage). False is treated as a no-op by callers,
    not an error — the form just renders again.
    """
    db = get_db()
    try:
        result = db.transition_onboarding_session_state(
            session_id,
            target.value,
            actor_id=actor_id,
            metadata_patch=metadata_patch,
        )
        return result is not None
    except IllegalVendorOnboardingTransitionError:
        return False


# ---------------------------------------------------------------------------
# GET — render the form
# ---------------------------------------------------------------------------


@router.get("/onboard/{token}", response_class=HTMLResponse)
def render_onboarding_form(
    request: Request,
    portal: PortalSession = Depends(require_portal_token),
):
    """Show the multi-section onboarding form for the vendor.

    Same template renders all four stages — CSS class on each section
    indicates whether it's complete, active, or disabled based on the
    current onboarding state. The vendor can see the whole journey
    even if they only have one section to fill in.
    """
    flash = request.query_params.get("flash") or None
    error = request.query_params.get("error") or None
    return templates.TemplateResponse(
        request,
        "onboard.html",
        _build_form_context(request, portal, flash_message=flash, flash_error=error),
    )


# ---------------------------------------------------------------------------
# POST — KYC submission
# ---------------------------------------------------------------------------


@router.post("/onboard/{token}/kyc")
def submit_kyc(
    request: Request,
    token: str,
    registered_address: str = Form(..., max_length=512),
    registration_number: str = Form(..., max_length=128),
    vat_number: str = Form("", max_length=64),
    director_names: str = Form("", max_length=2048),
    portal: PortalSession = Depends(require_portal_token),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Save KYC fields, transition to bank_verify."""
    db = get_db()

    cleaned: Dict[str, Any] = {
        "registered_address": registered_address.strip(),
        "registration_number": registration_number.strip(),
    }
    vat_clean = (vat_number or "").strip()
    if vat_clean:
        cleaned["vat_number"] = vat_clean
    directors_list = _split_director_names(director_names)
    if directors_list:
        cleaned["director_names"] = directors_list
    # Stamp the KYC completion date — the vendor just submitted it.
    cleaned["kyc_completion_date"] = _today_iso()

    try:
        updated = db.update_vendor_kyc(
            portal.organization_id,
            portal.vendor_name,
            patch=cleaned,
            actor_id=f"vendor_portal:{portal.token_id}",
        )
    except Exception as exc:
        logger.warning("[vendor_portal] update_vendor_kyc failed: %s", exc)
        return _redirect_with_error(token, "Could not save KYC details. Please try again.")

    if updated is None:
        return _redirect_with_error(token, "Could not save KYC details. Please try again.")

    # Move from invited|kyc → bank_verify. If the session is
    # already past this stage (vendor re-submitted by mistake), treat
    # the call as a no-op and just re-render the form.
    if portal.onboarding_state in {"invited", "kyc"}:
        if portal.onboarding_state == "invited":
            _safe_transition(
                portal.session_id,
                VendorOnboardingState.KYC,
                actor_id=f"vendor_portal:{portal.token_id}",
            )
        _safe_transition(
            portal.session_id,
            VendorOnboardingState.BANK_VERIFY,
            actor_id=f"vendor_portal:{portal.token_id}",
        )

        # §2.2: Enqueue KYC_DOCUMENT_RECEIVED event
        try:
            from clearledgr.core.events import AgentEvent, AgentEventType
            from clearledgr.core.event_queue import get_event_queue
            get_event_queue().enqueue(AgentEvent(
                type=AgentEventType.KYC_DOCUMENT_RECEIVED,
                source="vendor_portal",
                payload={
                    "vendor_id": portal.vendor_name,
                    "document_type": "kyc_submission",
                    "session_id": portal.session_id,
                },
                organization_id=portal.organization_id,
            ))
        except Exception:
            pass  # Non-fatal

        # Best-effort vendor enrichment from Companies House / HMRC VAT
        # (DESIGN_THESIS §3). Runs in a background task so the vendor
        # redirect is not delayed by external API calls.
        background_tasks.add_task(
            _enrich_vendor_background,
            organization_id=portal.organization_id,
            vendor_name=portal.vendor_name,
            registration_number=registration_number.strip(),
            vat_number=vat_clean,
        )

    return _redirect_with_flash(token, "Business details saved.")


async def _enrich_vendor_background(
    organization_id: str,
    vendor_name: str,
    registration_number: str,
    vat_number: str,
) -> None:
    """Background task: enrich vendor from Companies House / HMRC VAT.

    Delegates to the lifecycle module's ``enrich_vendor_on_kyc`` which
    handles all error catching and logging internally.
    """
    try:
        from clearledgr.services.vendor_onboarding_lifecycle import (
            enrich_vendor_on_kyc,
        )

        await enrich_vendor_on_kyc(
            organization_id=organization_id,
            vendor_name=vendor_name,
            registration_number=registration_number or None,
            vat_number=vat_number or None,
        )
    except Exception as exc:
        # Final safety net — enrich_vendor_on_kyc already catches
        # internally, but guard against import errors or unexpected
        # failures so the background task never crashes silently.
        logger.warning(
            "[vendor_portal] background enrichment failed for %s/%s: %s",
            organization_id, vendor_name, exc,
        )


# ---------------------------------------------------------------------------
# POST — bank details submission
# ---------------------------------------------------------------------------


@router.post("/onboard/{token}/bank-details")
def submit_bank_details(
    request: Request,
    token: str,
    iban: str = Form(..., min_length=8, max_length=64),
    account_holder_name: str = Form(..., max_length=128),
    bank_name: str = Form("", max_length=128),
    portal: PortalSession = Depends(require_portal_token),
):
    """Encrypt + save bank details, transition to bank_verified.

    V1 ships a direct bank_verify → bank_verified edge after the IBAN
    passes structural + mod-97 checksum validation. The old micro-deposit
    confirmation step was removed; real account-ownership verification
    will land via pluggable provider adapters (Adyen for EU customers,
    TrueLayer for UK + RoW) which replace this direct transition with
    a provider-gated one.
    """
    from clearledgr.core.stores.bank_details import normalize_iban, validate_iban

    db = get_db()

    # IBAN structural + mod-97 checksum validation. A typo in any
    # single digit of an IBAN fails the checksum, which is the only
    # line of defence between "paid Acme" and "paid the stranger whose
    # IBAN is one digit away". Fail fast here so the vendor re-types
    # before we write anything to the encrypted bank_details column.
    iban_normalised = normalize_iban(iban)
    iban_error = validate_iban(iban_normalised)
    if iban_error:
        logger.info("[vendor_portal] IBAN rejected: %s", iban_error)
        return _redirect_with_error(
            token,
            "That IBAN doesn't look right. Please double-check and re-enter.",
        )

    bank_payload: Dict[str, Any] = {
        "iban": iban_normalised,
        "account_holder_name": account_holder_name.strip(),
    }
    bank_clean = (bank_name or "").strip()
    if bank_clean:
        bank_payload["bank_name"] = bank_clean

    try:
        ok = db.set_vendor_bank_details(
            portal.organization_id,
            portal.vendor_name,
            bank_payload,
            actor_id=f"vendor_portal:{portal.token_id}",
        )
    except Exception as exc:
        logger.warning("[vendor_portal] set_vendor_bank_details failed: %s", exc)
        return _redirect_with_error(token, "Could not save bank details. Please try again.")

    if not ok:
        return _redirect_with_error(token, "Could not save bank details. Please try again.")

    # Transition bank_verify → bank_verified. Only valid if the
    # session is currently in bank_verify — otherwise treat as an
    # idempotent no-op.
    if portal.onboarding_state == "bank_verify":
        _safe_transition(
            portal.session_id,
            VendorOnboardingState.BANK_VERIFIED,
            actor_id=f"vendor_portal:{portal.token_id}",
        )

    # §2.2: Enqueue IBAN_CHANGE_SUBMITTED event. Passes the normalized
    # IBAN so the agent's check_iban_change handler can compare against
    # the vendor's stored IBAN (same-IBAN resubmissions should NOT
    # trigger a payment freeze).
    try:
        from clearledgr.core.events import AgentEvent, AgentEventType
        from clearledgr.core.event_queue import get_event_queue
        get_event_queue().enqueue(AgentEvent(
            type=AgentEventType.IBAN_CHANGE_SUBMITTED,
            source="vendor_portal",
            payload={
                "vendor_id": portal.vendor_name,
                "session_id": portal.session_id,
                "new_iban": bank_payload["iban"],
            },
            organization_id=portal.organization_id,
        ))
    except Exception:
        pass  # Non-fatal

    return _redirect_with_flash(
        token,
        "Bank details saved. Your customer will activate you in their finance system shortly.",
    )


# ---------------------------------------------------------------------------
# Redirect helpers
# ---------------------------------------------------------------------------


def _redirect_with_flash(token: str, message: str) -> RedirectResponse:
    from urllib.parse import quote_plus
    return RedirectResponse(
        url=f"/portal/onboard/{token}?flash={quote_plus(message)}",
        status_code=303,
    )


def _redirect_with_error(token: str, message: str) -> RedirectResponse:
    from urllib.parse import quote_plus
    return RedirectResponse(
        url=f"/portal/onboard/{token}?error={quote_plus(message)}",
        status_code=303,
    )


def _today_iso() -> str:
    from datetime import date
    return date.today().isoformat()
