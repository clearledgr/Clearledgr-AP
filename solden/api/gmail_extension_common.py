"""Shared helpers for Gmail extension router modules."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from solden.core.database import get_db


_ADMIN_ROLES = {"admin", "owner"}


def is_admin_user(user: Any) -> bool:
    return str(getattr(user, "role", "") or "").strip().lower() in _ADMIN_ROLES


def assert_user_org_access(user: Any, organization_id: str) -> None:
    """Assert the user belongs to ``organization_id``.

    Role (admin / owner / ap_clerk / etc.) controls WHAT the user can
    do within their org — NOT WHICH org they can access. Previously
    this function returned early for admin/owner, which meant an admin
    of Org A could pass organization_id=Org_B in a request and read/
    write Org B's data. That was a cross-tenant vulnerability active
    the moment we had 2+ tenants.

    There is no super-admin concept in the product. If one is ever
    needed (platform operator tooling), it belongs on a separate,
    internal-only route — not a role check on the tenant-facing API.

    Pre-fix this function coerced both sides to ``"default"`` before
    comparing — a session whose ``organization_id`` was the legacy
    ``"default"`` literal could bypass the check by passing an empty
    body org. We now require both values to be non-empty before
    comparing; missing org on either side is a 403.
    """
    requested = str(organization_id or "").strip()
    user_org = str(getattr(user, "organization_id", "") or "").strip()
    if not requested or not user_org or user_org != requested:
        raise HTTPException(status_code=403, detail="org_mismatch")


def resolve_org_id_for_user(user: Any, requested_org: Optional[str]) -> str:
    """Resolve the org for a Gmail extension request.

    Contract:
      * If the request omits ``organization_id`` (None / "" / the
        legacy ``"default"`` placeholder), use the user's session
        org.
      * If the request supplies ``organization_id``, it MUST match
        the user's session org — otherwise 403.
      * If the user's session has no org at all, fail closed with
        403. Pre-fix this returned the literal ``"default"`` string,
        which silently routed the request to a shared bucket.
    """
    requested = str(requested_org or "").strip()
    user_org = str(getattr(user, "organization_id", "") or "").strip()
    if not user_org:
        raise HTTPException(
            status_code=403, detail="user_missing_organization_id"
        )
    # Treat the legacy ``"default"`` placeholder as "no org supplied"
    # for backward-compat with extension clients that still pass it
    # — but only as a sentinel, never as an actual tenant id. The
    # session org takes precedence.
    if not requested or requested == "default":
        return user_org
    if requested != user_org:
        raise HTTPException(status_code=403, detail="org_mismatch")
    return user_org


def authenticated_actor(user: Any, fallback: str = "extension") -> str:
    return str(
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or fallback
    ).strip() or fallback


def build_finance_runtime(user: Any, organization_id: str, *, db: Any = None):
    from solden.services.finance_agent_runtime import FinanceAgentRuntime

    actor = authenticated_actor(user, fallback="gmail_extension")
    return FinanceAgentRuntime(
        organization_id=organization_id,
        actor_id=getattr(user, "user_id", None) or actor,
        actor_email=actor,
        db=db or get_db(),
    )


