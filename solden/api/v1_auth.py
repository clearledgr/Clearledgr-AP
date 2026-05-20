"""FastAPI auth dep for the public /v1 surface (customer-side agents).

The /v1 endpoints accept API keys only (no JWT). A key resolves to an
``AgentIdentity`` carrying:

* ``key_id`` — opaque internal id
* ``organization_id`` — the single tenant this key sees
* ``agent_id`` — identity the key represents (e.g. ``agent:cs-bot-prod``)
* ``agent_version`` — optional version stamp (e.g. ``2.4.1``)
* ``scopes`` — allow-list of scope tokens, OR ``None`` for legacy
  full-access keys (migration 74 contract)

Routes that need scope enforcement use ``Depends(require_agent_key(scope))``.
The dep:

1. Reads ``Authorization: Bearer sk_...`` or ``X-API-Key: sk_...``.
2. Looks the key up via ``db.validate_api_key`` (hash-compared).
3. Rejects expired or revoked keys.
4. Checks scope membership.
5. Returns ``AgentIdentity`` to the route handler.

Every rejection raises the typed ``AuthorizationDenied`` from
``solden.core.authorization`` — the global handler in main.py
emits the ``authorization_denied`` audit row before returning the
401/403 response.

Scope vocabulary: lives in ``clearledgr/api/api_keys.py:_SCOPE_CATALOG``
(``read:ap_items``, ``write:ap_items``, ``read:audit``, ...). The /v1
routes map onto that vocabulary so a single key can grant access to
both the workspace and the /v1 surface without a parallel taxonomy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import Request

from solden.core.authorization import (
    AuthorizationDenied,
    OrganizationMismatch,
    forbid,
)

# Re-exported so route handlers can write
#   ``user: AgentIdentity = Depends(require_agent_key("write:ap_items"))``
__all__ = [
    "AgentIdentity",
    "require_agent_key",
    "resolve_agent_key",
]


# Legacy → new scope synonym map. A key minted under the old Module-11
# vocabulary (verb:noun, AP-pinned) still satisfies a /v1 scope check
# expressed in the new vocabulary (noun:verb, Box-type-agnostic).
#
# Keys are NEW scope tokens; values are the legacy tokens that satisfy
# them. Order doesn't matter — has_scope() walks the tuple.
#
# 6-month deprecation window: keep accepting both until 2026-11. Then
# this map can be pared down (or removed) and warning logs added for
# the few keys still on legacy tokens.
_LEGACY_SCOPE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "records:read": ("read:ap_items", "read:vendors"),
    "records:write": ("write:ap_items", "write:vendors"),
    "intents:execute": ("write:ap_items",),
    "intents:preview": ("read:ap_items",),
    "audit:read": ("read:audit", "read:reports"),
    "webhooks:manage": ("manage:webhooks",),
}


@dataclass
class AgentIdentity:
    """The resolved identity behind a /v1 caller.

    Returned by ``require_agent_key`` after a successful auth check.
    Carries everything the audit chain needs to attribute the call.

    ``scopes`` semantics:

    * ``None`` — legacy key issued before migration 74 (no scope set
      recorded). The migration-74 docstring documents this as
      "full-access for backward compat". The /v1 auth dep honours
      that: any scope check passes.
    * ``[]`` — explicit empty list. The key has zero permissions and
      every scope check fails.
    * ``["read:ap_items", ...]`` — explicit allow-list. Standard
      membership check.
    """

    key_id: str
    organization_id: str
    agent_id: Optional[str]
    agent_version: Optional[str]
    scopes: Optional[List[str]]
    user_id: Optional[str] = None
    raw_row: dict = field(default_factory=dict, repr=False)

    @property
    def actor_label(self) -> str:
        """The string we write into ``audit_events.actor_id``.

        Prefers ``agent_id`` (when the key was bound to a named agent
        identity), falls back to ``user_id`` for legacy keys that
        haven't been migrated yet.
        """
        return self.agent_id or self.user_id or "unknown_agent"

    def has_scope(self, scope: str) -> bool:
        """Scope check with the migration-74 NULL = full-access contract.

        Also accepts legacy AP-pinned scope tokens as synonyms for the
        new Box-type-agnostic vocab during the 6-month deprecation
        window. A key minted with ``read:ap_items`` still passes a
        ``records:read`` check; ``write:ap_items`` covers
        ``records:write`` and ``intents:execute``. Lets existing
        customer integrations keep working through the rename day
        without a coordinated key rotation.
        """
        if self.scopes is None:
            return True  # legacy unscoped key
        if scope in self.scopes:
            return True
        # Walk the synonym map: each new-vocab scope lists the legacy
        # scopes that satisfy it.
        for legacy in _LEGACY_SCOPE_SYNONYMS.get(scope, ()):
            if legacy in self.scopes:
                return True
        return False


def _extract_raw_key(request: Request) -> Optional[str]:
    """Read the API key from either Authorization: Bearer or X-API-Key."""
    auth_header = request.headers.get("Authorization", "") or ""
    if auth_header.startswith("Bearer "):
        candidate = auth_header[7:].strip()
        if candidate:
            return candidate
    candidate = (request.headers.get("X-API-Key") or "").strip()
    return candidate or None


def _parse_scopes(row_scopes: Any) -> Optional[List[str]]:
    """Normalise ``api_keys.scopes`` into ``Optional[List[str]]``.

    Postgres returns JSONB as a Python list automatically (psycopg
    JSON adapter); SQLite would return a string. Handle both.
    """
    if row_scopes is None:
        return None
    if isinstance(row_scopes, list):
        return [str(s).strip().lower() for s in row_scopes if str(s).strip()]
    if isinstance(row_scopes, str):
        text = row_scopes.strip()
        if not text:
            return []
        try:
            import json

            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(s).strip().lower() for s in parsed if str(s).strip()]
        except (ValueError, TypeError):
            pass
    return []  # malformed value → no permissions (fail closed)


def _row_is_revoked(row: dict) -> bool:
    """``revoked_at`` set, or ``is_active`` flipped off."""
    if row.get("revoked_at"):
        return True
    if row.get("is_active") in (0, False, "0"):
        return True
    return False


def _row_is_expired(row: dict) -> bool:
    """``expires_at`` set and in the past."""
    expires_at = row.get("expires_at")
    if not expires_at:
        return False
    try:
        ts = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        return ts < datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False  # malformed expiry → don't lock out the caller


def resolve_agent_key(request: Request, *, db: Any = None) -> AgentIdentity:
    """Authenticate the /v1 caller and return their AgentIdentity.

    Raises ``AuthorizationDenied(http_status=401)`` if the key is
    missing or invalid; ``AuthorizationDenied(http_status=403)`` if
    the key is revoked or expired. Scope enforcement is layered on
    top via ``require_agent_key(scope)``.
    """
    raw_key = _extract_raw_key(request)
    if not raw_key:
        forbid(
            "missing_api_key",
            actor_type="agent",
            attempted_action="api_request",
            http_status=401,
            http_detail="missing_api_key",
        )

    if db is None:
        from solden.core.database import get_db

        db = get_db()

    # include_revoked so a revoked key surfaces as a row (-> 403 below)
    # instead of None (-> 401), keeping "revoked" distinct from "invalid".
    row = db.validate_api_key(raw_key, include_revoked=True)
    if row is None:
        forbid(
            "invalid_api_key",
            actor_type="agent",
            attempted_action="api_request",
            http_status=401,
            http_detail="invalid_api_key",
        )

    if _row_is_revoked(row):
        forbid(
            "api_key_revoked",
            actor_type="agent",
            actor_id=row.get("agent_id") or row.get("user_id"),
            organization_id=row.get("organization_id"),
            attempted_action="api_request",
            http_status=403,
            http_detail="api_key_revoked",
        )
    if _row_is_expired(row):
        forbid(
            "api_key_expired",
            actor_type="agent",
            actor_id=row.get("agent_id") or row.get("user_id"),
            organization_id=row.get("organization_id"),
            attempted_action="api_request",
            http_status=403,
            http_detail="api_key_expired",
        )

    return AgentIdentity(
        key_id=str(row.get("id") or ""),
        organization_id=str(row.get("organization_id") or ""),
        agent_id=row.get("agent_id"),
        agent_version=row.get("agent_version"),
        scopes=_parse_scopes(row.get("scopes")),
        user_id=row.get("user_id"),
        raw_row=row,
    )


def require_agent_key(scope: Optional[str] = None):
    """FastAPI dep factory: returns a dep that authenticates and
    enforces ``scope`` membership.

    Usage::

        @router.post("/intents/execute")
        async def execute(
            request: Request,
            agent: AgentIdentity = Depends(require_agent_key("write:ap_items")),
            ...
        ):
            ...

    Pass ``scope=None`` for endpoints that need only authentication,
    not scope (e.g. ``/v1/me``, ``/v1/health``).
    """

    async def _dep(request: Request) -> AgentIdentity:
        identity = resolve_agent_key(request)
        if scope and not identity.has_scope(scope):
            raise AuthorizationDenied(
                "invalid_scope",
                actor_type="agent",
                actor_id=identity.actor_label,
                organization_id=identity.organization_id,
                attempted_action=f"scope:{scope}",
                http_status=403,
                http_detail=f"missing_scope:{scope}",
                tool_scope=(
                    list(identity.scopes)
                    if identity.scopes is not None
                    else None
                ),
            )
        # Rate-limit check runs after auth + scope so a 429 is only
        # ever shown to an otherwise-authorised caller. Import here to
        # avoid a circular import at module load time (v1_rate_limit
        # imports AgentIdentity from this file).
        from solden.api.v1_rate_limit import enforce_v1_rate_limit

        enforce_v1_rate_limit(request, identity)
        return identity

    return _dep


def require_org_match(
    agent: AgentIdentity, claimed_org_id: Optional[str]
) -> None:
    """If a request body or URL carries ``organization_id``, verify it
    matches the key's org. Most /v1 routes won't need this — the key
    already pins org — but explicit guards stay cheap.
    """
    if not claimed_org_id:
        return
    if str(claimed_org_id) != agent.organization_id:
        raise OrganizationMismatch(
            actor_type="agent",
            actor_id=agent.actor_label,
            organization_id=agent.organization_id,
            attempted_action="cross_tenant_request",
        )
