"""Shared helpers for Gmail extension router modules."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from clearledgr.core.database import get_db


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
    """
    org_id = str(organization_id or "default").strip() or "default"
    user_org = str(getattr(user, "organization_id", "") or "").strip()
    if user_org != org_id:
        raise HTTPException(status_code=403, detail="org_mismatch")


def resolve_org_id_for_user(user: Any, requested_org: Optional[str]) -> str:
    requested = str(requested_org or "").strip()
    if requested and requested != "default":
        assert_user_org_access(user, requested)
        return requested
    return str(getattr(user, "organization_id", None) or "default")


def authenticated_actor(user: Any, fallback: str = "extension") -> str:
    return str(
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or fallback
    ).strip() or fallback


def build_finance_runtime(user: Any, organization_id: str, *, db: Any = None):
    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime

    actor = authenticated_actor(user, fallback="gmail_extension")
    return FinanceAgentRuntime(
        organization_id=organization_id,
        actor_id=getattr(user, "user_id", None) or actor,
        actor_email=actor,
        db=db or get_db(),
    )


