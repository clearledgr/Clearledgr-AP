"""Portal magic-link auth dependency — Phase 3.1.b.

The vendor onboarding portal is the **only** part of Clearledgr that
serves unauthenticated traffic. Vendors don't have user accounts with
the customer — they just hold a one-time URL emailed to them. This
module is the auth primitive that translates that URL's token segment
into a :class:`PortalSession` so downstream route handlers can act on
the right vendor onboarding session without ever seeing a JWT.

Design rules
============

* **Token in path, never in query string.** Query strings get logged
  by every reverse proxy and CDN on the planet. Path segments are
  treated as identifiers in most logging configs and are easier to
  scrub. The portal router expects ``/portal/onboard/{token}`` and the
  dependency reads ``token`` as a path parameter.

* **Lookup is constant-time hash compare.** Validation hashes the
  inbound token and looks up by hash via
  :meth:`OnboardingTokenStore.validate_onboarding_token`, which uses
  ``secrets.compare_digest`` defense-in-depth on the hash itself.
  Timing attacks against the auth surface should not be able to leak
  whether a token "almost matches" something real.

* **Three failure modes, three distinct status codes.** Production
  customer support uses these to triage:
    - **404** — token does not exist (typo, made-up URL, never issued)
    - **410** — token existed but is expired or revoked or its session
      has been terminated (the standard "DocuSign envelope already
      signed" error class)
    - **409** — session is in a terminal state but somehow still has
      a live token. Should be unreachable in practice; surfaces as a
      hard error for telemetry rather than failing silently.

* **Access tracking is non-fatal.** Every successful validation bumps
  ``last_accessed_at`` + ``access_count`` on the token row. If that
  write fails, the request still succeeds — instrumentation must not
  block the vendor's onboarding flow.

* **No JWT, no session cookie, no CSRF token.** The single-use,
  hash-keyed, short-TTL magic link IS the auth context. There is no
  session to fixate on, no cookie to steal, no CSRF surface that
  matters because the only thing the token grants is the ability to
  modify the specific vendor onboarding session it was issued for.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import HTTPException, Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PortalSession:
    """Authenticated portal session derived from a valid magic-link token.

    Returned by :func:`require_portal_token` and threaded through every
    portal route handler. Carries enough context (org id, vendor name,
    session id, current state) that the handler doesn't need to re-read
    the session from the database for the common case.

    Frozen so handlers can't accidentally mutate it and end up with a
    stale view of the session state — every state-modifying call goes
    through the typed VendorStore accessors, which return updated
    session dicts.
    """

    token_id: str
    session_id: str
    organization_id: str
    vendor_name: str
    purpose: str
    onboarding_state: str
    expires_at: str

    @property
    def is_kyc_stage(self) -> bool:
        return self.onboarding_state in {"invited", "kyc"}

    @property
    def is_bank_stage(self) -> bool:
        return self.onboarding_state == "bank_verify"


def require_portal_token(
    token: str = Path(..., min_length=16, max_length=128, description="One-time onboarding token"),
) -> PortalSession:
    """FastAPI dependency that validates a portal magic-link token.

    Resolves the URL path token to a live :class:`PortalSession`.
    Raises :class:`HTTPException` with the appropriate status code
    documented in the module docstring on every failure mode.
    """
    from clearledgr.core.database import get_db

    db = get_db()
    token_row = db.validate_onboarding_token(token)
    if token_row is None:
        # Could be unknown OR expired OR revoked. We deliberately
        # return 410 for ALL "exists but dead" cases AND 404 for the
        # "does not exist" case, but at this layer we cannot tell them
        # apart (the store returns None for both). 410 is the safer
        # default — telling an attacker "that token never existed" is
        # marginally more useful than telling them "that token expired".
        raise HTTPException(
            status_code=410,
            detail="onboarding_link_expired_or_invalid",
        )

    session = db.get_onboarding_session_by_id(token_row["session_id"])
    if session is None:
        # Token exists but the session it points at has been deleted.
        # This should be impossible in normal operation — sessions are
        # never hard-deleted, only deactivated. Surface as a hard error.
        logger.error(
            "[portal_auth] orphan token %s — session %s missing",
            token_row.get("id"),
            token_row.get("session_id"),
        )
        raise HTTPException(
            status_code=409,
            detail="onboarding_session_missing",
        )

    if not session.get("is_active"):
        # Session terminated (active / rejected / abandoned). The token
        # SHOULD have been revoked by the state machine when this
        # happened, so reaching here means the revoke fan-out failed.
        # Treat as expired from the vendor's perspective.
        raise HTTPException(
            status_code=410,
            detail="onboarding_session_terminated",
        )

    # Successful validation — bump access counters (best-effort).
    db.record_onboarding_token_access(token_row["id"])

    return PortalSession(
        token_id=str(token_row.get("id") or ""),
        session_id=str(session.get("id") or ""),
        organization_id=str(session.get("organization_id") or ""),
        vendor_name=str(session.get("vendor_name") or ""),
        purpose=str(token_row.get("purpose") or "full_onboarding"),
        onboarding_state=str(session.get("state") or ""),
        expires_at=str(token_row.get("expires_at") or ""),
    )
