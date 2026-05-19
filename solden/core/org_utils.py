"""Organization-id resolution helpers — the canonical replacement for
the M4 ``or "default"`` anti-pattern.

Pre-this-module the codebase had ~125 sites doing
``str(organization_id or "default").strip() or "default"`` (and
syntactic equivalents: ``getattr(user, "organization_id", "default")``,
``settings.get("organization_id", "default")``, etc.). Each one was a
silent cross-tenant landmine: any caller that lost the org along the
way silently routed to the legacy ``"default"`` tenant, which under
M10 also unlocked platform-runtime privileges.

Three rounds of adversarial review found peer-files-I-missed,
semantic-equivalents, and contract-fanout bugs. The recursive pattern
won't terminate via per-file fixes — see
``memory/feedback_adversarial_review_required_for_security.md``.

This module provides two functions:

* ``assert_org_id(organization_id)`` — service / data layer helper.
  Raises ``ValueError`` on empty/missing org. No HTTP context.
* ``require_org(user, *, requested=None)`` — API layer helper.
  Derives the verified org from the authenticated session, optionally
  cross-checks against a body / URL value. Raises ``HTTPException(403)``
  on missing or mismatched org.

The legacy ``"default"`` placeholder is tolerated only by
``require_org``'s ``requested`` arg as "no value supplied" — it is
NEVER used as an actual tenant id. Any site that needs the literal
string for a real tenant id should be migrated to a UUID-shaped org
id (planned tenant-rename migration).
"""
from __future__ import annotations

import logging
from typing import Any, Optional


# Cross-tenant attempt detection in production hangs on these log
# lines. Severity: ``warning`` so they show up in the operator dash
# without flooding error pipelines, but persistent volume on this
# logger can be alerted on.
logger = logging.getLogger(__name__)


# Sentinel for "user has signed in but no organization is bound to
# them yet" — replaces the legacy ``"default"`` literal that used to
# absorb both unprovisioned users and a real legacy tenant. Treated
# as equivalent to a missing org by both ``assert_org_id`` and
# ``require_org``: it must NEVER be accepted as a real tenant id.
#
# Underscore prefix matches the ``_unauthenticated`` sentinel used by
# Teams audit logging — guaranteed not to collide with any real org
# id (UUID-shaped, slug-shaped, or domain-shaped).
#
# Migration v79 enforces this at the DB level via a CHECK constraint
# on ``organizations.id NOT IN ('default', '_unprovisioned')``.
UNPROVISIONED_SENTINEL = "_unprovisioned"

# Set of literals that must be rejected as tenant ids — both the
# legacy ``"default"`` (in case any pre-migration data lingers, or a
# new code site reintroduces it) and the new sentinel.
_INVALID_TENANT_LITERALS = frozenset({"default", UNPROVISIONED_SENTINEL})


class OrgIdMissing(ValueError):
    """Raised by ``assert_org_id`` when the supplied org is empty.

    Distinct from a generic ``ValueError`` so callers can opt to
    handle it without catching every value error in the call path.
    Subclass of ``ValueError`` so existing ``except ValueError``
    handlers still catch it.
    """


def assert_org_id(organization_id: Any, *, context: str = "") -> str:
    """Validate an ``organization_id`` and return the canonical form.

    Strict-fail: raises ``OrgIdMissing`` (subclass of ``ValueError``)
    if the input is empty / None / whitespace-only. Returns the
    stripped string otherwise.

    ``context`` is appended to the error message for grep-ability when
    a missing-org bug surfaces in production logs.

    Use at:

    * service-layer entry points that receive ``organization_id`` as
      a parameter (no ``user`` available);
    * data-layer write paths where a missing org would silently bind
      to the legacy ``"default"`` tenant.

    Do NOT use:

    * inside FastAPI routes — use ``require_org(user)`` instead so the
      session org is the source of truth, not a body / URL parameter.
    """
    org = str(organization_id or "").strip()
    if not org or org in _INVALID_TENANT_LITERALS:
        # ``_unprovisioned`` and the legacy ``default`` are sentinels,
        # never real tenants. Treat both the same as a missing org so
        # write paths fail closed instead of binding rows to a
        # legacy / sentinel bucket.
        msg = "organization_id is required"
        if context:
            msg += f" in {context}"
        raise OrgIdMissing(msg)
    return org


def require_org(user: Any, *, requested: Optional[Any] = None) -> str:
    """Derive the verified org from an authenticated session.

    Returns the user's session ``organization_id``. Raises
    ``HTTPException(403, "user_missing_organization_id")`` if the
    session has no org.

    Optional ``requested`` arg: when the route accepts an
    ``organization_id`` from the URL, body, or query string (legacy
    contract), pass it here. The check:

    * empty / None / the legacy ``"default"`` literal placeholder ->
      treated as "no value supplied"; the session org is returned.
    * non-empty AND equals session org -> session org returned.
    * non-empty AND differs from session org -> ``HTTPException(403,
      "org_mismatch")``.

    The legacy ``"default"`` placeholder is the ONLY string treated as
    a sentinel; any other value must match the session exactly.
    """
    # Lazy HTTPException import — keeps this module importable from
    # non-FastAPI contexts (e.g., Celery tasks, background scripts)
    # without dragging the FastAPI dependency tree along.
    from fastapi import HTTPException

    user_org = str(getattr(user, "organization_id", "") or "").strip()
    if not user_org or user_org in _INVALID_TENANT_LITERALS:
        # Log so operators can detect attempted access to org-scoped
        # endpoints from sessions that lack an org binding (likely a
        # mis-provisioned user or a malformed JWT). The
        # ``_unprovisioned`` sentinel is the post-tenant-rename signal
        # for "OAuth succeeded but no org is bound yet" — these users
        # land on the provisioning-pending screen, not org-scoped
        # routes.
        actor = (
            str(getattr(user, "user_id", "") or "")
            or str(getattr(user, "email", "") or "")
            or "<unknown>"
        )
        logger.warning(
            "[require_org] user_missing_organization_id actor=%s session_org=%r",
            actor, user_org,
        )
        # Distinct detail for the unprovisioned case so the frontend
        # can route to the "your organization isn't set up yet" screen
        # instead of the generic 403 page. Same status code: read-only
        # surfaces and write surfaces both fail closed for these users.
        detail = (
            "organization_pending_provisioning"
            if user_org == UNPROVISIONED_SENTINEL
            else "user_missing_organization_id"
        )
        raise HTTPException(status_code=403, detail=detail)
    if requested is None:
        return user_org
    req = str(requested or "").strip()
    if not req or req in _INVALID_TENANT_LITERALS:
        # Legacy ``"default"`` placeholder, ``"_unprovisioned"``
        # sentinel, or empty — caller didn't actually supply a real
        # tenant id. Use the session org.
        return user_org
    if req != user_org:
        # Log the mismatch with both sides so a cross-tenant attack
        # attempt (verified session of tenant A passing
        # ``organization_id=tenantB`` in URL/body) leaves a
        # structured trail. Persistent volume on this log line can
        # be alerted on.
        actor = (
            str(getattr(user, "user_id", "") or "")
            or str(getattr(user, "email", "") or "")
            or "<unknown>"
        )
        logger.warning(
            "[require_org] org_mismatch actor=%s session_org=%s requested=%s",
            actor, user_org, req,
        )
        raise HTTPException(status_code=403, detail="org_mismatch")
    return user_org


def coerce_org_id(organization_id: Any) -> Optional[str]:
    """Soft-fail variant: returns the stripped org string OR None.

    Use only when the caller has a legitimate read-side fallback for
    a missing org (e.g., a sweep job that iterates per-tenant and
    treats unbound rows as orphaned). NEVER use in a write path —
    use ``assert_org_id`` for those.
    """
    org = str(organization_id or "").strip()
    if not org or org in _INVALID_TENANT_LITERALS:
        return None
    return org
