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
    """Resolve the org_id an authenticated user is acting under.

    Per-tenant isolation invariant: a user without an
    ``organization_id`` cannot act on tenant data. Reject loudly
    rather than silently routing to "default" — the silent fallback
    let any tokenless caller drift onto the platform tenant.
    """
    allowed_admin_roles = admin_roles or _ORG_ADMIN_ROLES
    user_org_raw = getattr(user, "organization_id", None)
    user_org = str(user_org_raw or "").strip()
    if not user_org:
        raise HTTPException(status_code=403, detail="missing_user_organization_id")

    requested = str(requested_org_id or "").strip()
    org_id = requested or user_org
    role = str(getattr(user, "role", "") or "").strip().lower()
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
    actor_type: str = "user",
    agent_version: Optional[str] = None,
    tool_scope: Optional[list] = None,
) -> Any:
    """Build a per-channel FinanceAgentRuntime.

    Called by Slack, Teams, NetSuite, SAP, and the /v1 customer-agent
    surface. ``actor_type`` and ``agent_version`` are additive and
    default to the workspace/JWT-style ``"user"`` so existing callers
    keep their behaviour. The /v1 router passes ``actor_type="agent"``
    so every audit row written through this runtime carries the
    agent attribution. ``tool_scope`` lets /v1 callers pin the API
    key's scope list onto every audit row the runtime writes — the
    answer to "what was this actor permitted to do at the moment of
    action?" lives in the audit chain, not in the api_keys row
    (which can rotate / revoke / expire and lose the context).
    """
    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime

    normalized_org = str(organization_id or "").strip()
    if not normalized_org:
        raise ValueError(
            "build_channel_runtime requires organization_id; "
            "pass 'default' explicitly for the platform runtime"
        )
    return FinanceAgentRuntime(
        organization_id=normalized_org,
        actor_id=str(actor_id or fallback_actor),
        actor_email=str(actor_email or actor_id or fallback_actor),
        db=db or get_db(),
        actor_type=actor_type,
        agent_version=agent_version,
        tool_scope=tool_scope,
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
