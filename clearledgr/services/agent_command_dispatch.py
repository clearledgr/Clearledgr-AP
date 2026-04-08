"""Shared runtime construction and command dispatch helpers."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)

_ORG_ADMIN_ROLES = {"admin", "owner", "api"}


def resolve_org_id_for_user(
    user: Any,
    requested_org_id: Optional[str],
    *,
    admin_roles: set[str] | None = None,
) -> str:
    allowed_admin_roles = admin_roles or _ORG_ADMIN_ROLES
    _raw_org = requested_org_id or getattr(user, "organization_id", None)
    if not _raw_org:
        logger.warning("resolve_org_id_for_user: no organization_id on user or request, falling back to 'default'")
    org_id = str(_raw_org or "default")
    role = str(getattr(user, "role", "") or "").strip().lower()
    _raw_user_org = getattr(user, "organization_id", None)
    if not _raw_user_org:
        logger.warning("resolve_org_id_for_user: user has no organization_id, falling back to 'default'")
    user_org = str(_raw_user_org or "default")
    if role not in allowed_admin_roles and org_id != user_org:
        raise HTTPException(status_code=403, detail="org_mismatch")
    return org_id


def resolve_actor_id(user: Any, fallback: str = "user") -> str:
    return str(
        getattr(user, "user_id", None)
        or getattr(user, "email", None)
        or fallback
    ).strip() or fallback


def resolve_actor_email(user: Any, fallback: str = "user") -> str:
    return str(
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or fallback
    ).strip() or fallback


def build_runtime_for_user(
    user: Any,
    requested_org_id: Optional[str],
    *,
    db: Any = None,
    admin_roles: set[str] | None = None,
    fallback_actor: str = "user",
) -> Any:
    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime

    org_id = resolve_org_id_for_user(
        user,
        requested_org_id,
        admin_roles=admin_roles,
    )
    return FinanceAgentRuntime(
        organization_id=org_id,
        actor_id=resolve_actor_id(user, fallback=fallback_actor),
        actor_email=resolve_actor_email(user, fallback=fallback_actor),
        db=db or get_db(),
    )


def build_channel_runtime(
    *,
    organization_id: Optional[str],
    actor_id: Optional[str],
    actor_email: Optional[str],
    db: Any = None,
    fallback_actor: str,
) -> Any:
    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime

    if not organization_id:
        logger.warning("build_channel_runtime called without organization_id, falling back to 'default'")
    return FinanceAgentRuntime(
        organization_id=str(organization_id or "default"),
        actor_id=str(actor_id or fallback_actor),
        actor_email=str(actor_email or actor_id or fallback_actor),
        db=db or get_db(),
    )


async def dispatch_runtime_intent(
    runtime: Any,
    intent: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    return await runtime.execute_intent(
        intent,
        payload if isinstance(payload, dict) else {},
        idempotency_key=idempotency_key,
    )
