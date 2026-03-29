"""Shared helpers for Gmail extension router modules."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from clearledgr.core.database import get_db


_ADMIN_ROLES = {"admin", "owner"}


def is_admin_user(user: Any) -> bool:
    return str(getattr(user, "role", "") or "").strip().lower() in _ADMIN_ROLES


def assert_user_org_access(user: Any, organization_id: str) -> None:
    org_id = str(organization_id or "default")
    user_org = str(getattr(user, "organization_id", "") or "")
    if is_admin_user(user):
        return
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


def temporal_enabled() -> bool:
    from clearledgr.workflows.temporal_runtime import temporal_enabled as _temporal_enabled

    return _temporal_enabled()
