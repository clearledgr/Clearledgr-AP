"""Typed authorization-denial exceptions + audit emission.

The Szpruch / Sudjianto runtime-governance framework (April 2026) requires
every authorisation decision, including denied ones, to land in the
audit chain. Denied cross-tenant reads are how you detect probing; denied
admin actions are how you detect privilege escalation attempts. Logging
the denial only at WARN level is not enough; it has to be in the same
sha256-chained ``audit_events`` table as every successful state
transition, so the proof writes itself as a side effect of the work.

This module is the single funnel for denials:

- ``AuthorizationDenied`` is the base exception class. Named subclasses
  (``OrganizationMismatch``, ``RoleRequired``, ``AdminRequired``,
  ``CrossTenantAccessDenied``) pre-fill the common shape so call sites
  stay one line.
- ``forbid(reason, ...)`` raises ``AuthorizationDenied`` with the given
  context. Drop-in replacement for ``raise HTTPException(status_code=403,
  detail=...)`` that carries structured fields the audit handler can write.
- ``emit_authorization_denied_audit(...)`` writes one ``authorization_denied``
  row into ``audit_events``. The FastAPI handlers in ``main.py`` call this
  before returning the 401/403 response.

FastAPI handlers in ``main.py`` catch three exception shapes and funnel
all of them through ``emit_authorization_denied_audit``:

1. ``AuthorizationDenied`` (typed) — richest context.
2. ``HTTPException`` with status 401/403 — covers existing call sites
   that ``raise HTTPException(status_code=403, ...)`` without yet using
   the typed exception.
3. ``PermissionError`` — covers service-layer raises that don't always
   reach FastAPI (e.g., a service called from a background task).

Defense in depth: even if a new code path raises ``HTTPException(403)``
without ever importing this module, the audit row still fires.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _get_db():
    """Indirection so unit tests can swap the DB getter without importing
    ``clearledgr.core.database`` (which pulls in psycopg). Production code
    calls through to the real factory; tests monkeypatch this function on
    the module."""
    from clearledgr.core.database import get_db

    return get_db()


class AuthorizationDenied(Exception):
    """An authorisation decision that denied access.

    Carries structured context (actor, resource, organisation, attempted
    action) so the audit handler records the denial without parsing the
    message.
    """

    def __init__(
        self,
        denial_reason: str,
        *,
        actor_type: str = "user",
        actor_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        attempted_action: Optional[str] = None,
        http_status: int = 403,
        http_detail: Optional[str] = None,
        tool_scope: Optional[list] = None,
    ) -> None:
        self.denial_reason = denial_reason
        self.actor_type = actor_type
        self.actor_id = actor_id
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.organization_id = organization_id
        self.attempted_action = attempted_action
        self.http_status = http_status
        self.http_detail = http_detail or denial_reason
        # Scope set the actor held at the moment of denial — pinned
        # on the audit row so "they had X scopes when they tried Y"
        # stays answerable even after a subsequent rotation strips
        # the key's scope context.
        self.tool_scope = (
            list(tool_scope) if tool_scope is not None else None
        )
        super().__init__(denial_reason)


class OrganizationMismatch(AuthorizationDenied):
    """Actor attempted to access a resource outside their organization."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("denial_reason", "organization_mismatch")
        kwargs.setdefault("http_detail", "org_mismatch")
        super().__init__(**kwargs)


class CrossTenantAccessDenied(AuthorizationDenied):
    """Actor attempted to access a resource owned by another tenant.

    Distinguished from ``OrganizationMismatch`` for telemetry: an
    OrganizationMismatch is usually a stale session or a misconfigured
    URL; a CrossTenantAccessDenied is usually a probe (the actor's
    session is fine, they're trying to reach a resource they shouldn't
    know about).
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("denial_reason", "cross_tenant_access_denied")
        kwargs.setdefault("http_detail", "cross_tenant_access_denied")
        super().__init__(**kwargs)


class RoleRequired(AuthorizationDenied):
    """Actor lacks the required role for the attempted action."""

    def __init__(self, required_role: str, **kwargs: Any) -> None:
        kwargs.setdefault("denial_reason", f"role_required:{required_role}")
        kwargs.setdefault("http_detail", f"{required_role}_required")
        super().__init__(**kwargs)
        self.required_role = required_role


class AdminRequired(RoleRequired):
    """Actor is not an admin."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(required_role="admin", **kwargs)


def forbid(
    denial_reason: str,
    *,
    actor_type: str = "user",
    actor_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    attempted_action: Optional[str] = None,
    http_status: int = 403,
    http_detail: Optional[str] = None,
) -> None:
    """Raise ``AuthorizationDenied`` with structured context.

    Drop-in replacement for ``raise HTTPException(status_code=403,
    detail=...)`` at sites where the typed subclasses don't fit.
    """
    raise AuthorizationDenied(
        denial_reason=denial_reason,
        actor_type=actor_type,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        organization_id=organization_id,
        attempted_action=attempted_action,
        http_status=http_status,
        http_detail=http_detail,
    )


def emit_authorization_denied_audit(
    *,
    denial_reason: str,
    actor_type: str = "user",
    actor_id: Optional[str] = None,
    tool_scope: Optional[list] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    attempted_action: Optional[str] = None,
    request_path: Optional[str] = None,
    request_method: Optional[str] = None,
    http_status: int = 403,
) -> None:
    """Write a single ``authorization_denied`` row to ``audit_events``.

    The audit_events table is Box-keyed (``box_id`` + ``box_type``
    identify which Box this event is about). For denials about a
    specific resource we use that resource as the Box. For denials at
    the organization or session level (admin pages, cross-tenant probes
    against an unspecified resource) we use ``box_type="organization"``,
    ``box_id=organization_id`` (or ``"unknown"`` when the actor's org
    cannot be resolved).

    This function never raises. If the audit insert fails for any reason
    (DB down, idempotency collision, schema mismatch), the failure is
    logged at ERROR and swallowed — the upstream 401/403 response still
    fires. The paper's requirement is that denials are recorded; it does
    not require denials to be blocked when the audit chain is down.
    """
    try:
        box_type = resource_type or "organization"
        box_id = resource_id or organization_id or "unknown"

        db = _get_db()
        db.append_audit_event(
            {
                "event_type": "authorization_denied",
                "box_type": box_type,
                "box_id": box_id,
                "actor_type": actor_type,
                "actor_id": actor_id or "unknown",
                "organization_id": organization_id or "_unknown",
                "source": "authorization",
                "tool_scope": tool_scope,
                "payload_json": {
                    "denial_reason": denial_reason,
                    "attempted_action": attempted_action,
                    "request_path": request_path,
                    "request_method": request_method,
                    "http_status": http_status,
                },
            }
        )
    except Exception:
        logger.exception("Failed to emit authorization_denied audit event")
