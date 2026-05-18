"""Customer-side API keys — Module 11 (Settings + Account Management).

Customers create API keys for their own integrations (CI scripts,
internal dashboards, custom webhooks). Keys auth against the same
deps.py path that bearer tokens do — ``X-API-Key`` header → store
lookup by SHA-256 hash → token-data attached to the request.

Security shape:

  - The raw key is generated server-side with ``secrets.token_urlsafe``
    and returned to the caller **exactly once** in the create response.
    The store only ever holds the SHA-256 hash; we cannot recover the
    raw key after creation. This is the standard "show once, never
    again" pattern (Stripe, GitHub, AWS).
  - The list / get endpoints return only ``key_prefix`` (first 12
    chars + ellipsis) so operators recognise their own keys without
    leaking enough material to authenticate.
  - Revocation is a soft delete (``is_active = 0``) — preserves the
    audit trail of every key that ever existed for forensics, while
    failing auth immediately because ``validate_api_key`` filters on
    ``is_active``.
  - Rotation = revoke old + create new with the same label. Returns
    the new raw key (once) so the caller can update their integration.

Endpoints:

  POST   /api/workspace/api-keys                   create
  GET    /api/workspace/api-keys                   list (no raw key)
  GET    /api/workspace/api-keys/{id}              get (no raw key)
  POST   /api/workspace/api-keys/{id}/rotate       rotate
  DELETE /api/workspace/api-keys/{id}              revoke
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspace/api-keys", tags=["api-keys"])


# Prefix on every generated key so they're recognisable in logs and
# .env files. ``ck_`` = "clearledgr key".
_KEY_PREFIX = "ck_"

# urlsafe_b64 entropy. 32 bytes → ~43 chars after b64. Combined with
# the 3-char prefix, lands at ~46 chars — long enough to resist brute
# force, short enough to copy-paste cleanly.
_KEY_ENTROPY_BYTES = 32


def _generate_raw_key() -> str:
    return _KEY_PREFIX + secrets.token_urlsafe(_KEY_ENTROPY_BYTES)


# Module 11 spec line 353 — scoped API keys.
#
# Scopes are resource:action tokens. The catalog below is the
# canonical set; create / rotate validate against it. Routes that
# want to enforce a scope check call ``require_scope(user, "...")``
# from clearledgr/core/auth.py — when the auth path was an API key,
# the granted scopes are on the TokenData; cookie + bearer paths
# pass through untouched (the customer-side scopes are explicitly an
# API-key-only mechanism).
_SCOPE_CATALOG: List[str] = [
    # ── /v1 public-surface vocabulary (Box-type-agnostic) ──
    # The runtime claim is workflow-type-agnostic, so the public scope
    # vocabulary is too. Customer-issued keys for /v1 should use these.
    "records:read",
    "records:write",
    "intents:execute",
    "intents:preview",
    "audit:read",
    "webhooks:manage",
    # ── legacy AP-pinned vocabulary (Module 11, pre-/v1) ──
    # Still in the catalog so existing keys validate and existing
    # internal routes keep working. The /v1 auth dep's has_scope()
    # accepts these as synonyms for the new vocab during the 6-month
    # deprecation window:
    #   read:ap_items   → records:read
    #   write:ap_items  → records:write + intents:execute
    #   read:vendors    → records:read (vendor Box type when shipped)
    #   write:vendors   → records:write
    #   read:reports    → audit:read (closest semantic neighbour)
    #   manage:webhooks → webhooks:manage  (already the new spelling)
    "read:ap_items",
    "write:ap_items",
    "read:vendors",
    "write:vendors",
    "read:reports",
    "read:audit",
    "manage:webhooks",
]


class APIKeyCreateRequest(BaseModel):
    label: str = Field("", max_length=120)
    scopes: Optional[List[str]] = Field(default=None, max_length=64)
    # Agent attribution — every audit row written by a runtime built
    # from this key inherits these so "which agent did this?" stays
    # answerable years later.
    agent_id: Optional[str] = Field(default=None, max_length=120)
    agent_version: Optional[str] = Field(default=None, max_length=64)
    # ISO-8601 timestamp. None = never expires (legacy default).
    expires_at: Optional[str] = Field(default=None, max_length=64)


def _validate_scopes(scopes: Optional[List[str]]) -> List[str]:
    """Reject any scope token that isn't in the catalog. Empty / None
    means no scopes — the key is rejected by every scope-aware route.
    """
    if not scopes:
        return []
    cleaned = []
    for raw in scopes:
        token = str(raw or "").strip().lower()
        if not token:
            continue
        if token not in _SCOPE_CATALOG:
            raise HTTPException(
                status_code=422,
                detail={"reason": "unknown_scope", "scope": token, "catalog": _SCOPE_CATALOG},
            )
        cleaned.append(token)
    # Dedupe but preserve order.
    seen = set()
    return [s for s in cleaned if not (s in seen or seen.add(s))]


@router.get("/scopes/catalog")
def get_scope_catalog(
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the canonical scope vocabulary so the SPA can build a
    checkbox group without hardcoding the list."""
    return {"scopes": _SCOPE_CATALOG}


@router.post("")
def create_api_key(
    body: APIKeyCreateRequest,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Create a new API key. The raw key is returned ONCE — store it now.

    Response:
      {
        "id": "...",
        "key_prefix": "ck_xxxxx...",
        "raw_key": "ck_<base64>",       # only here, never again
        "label": "...",
        "scopes": ["read:ap_items", ...],
        ...
      }
    """
    db = get_db()
    raw_key = _generate_raw_key()
    user_id = getattr(user, "user_id", "") or getattr(user, "email", "")

    scopes = _validate_scopes(body.scopes)

    record = db.create_api_key(
        organization_id=user.organization_id,
        user_id=user_id,
        raw_key=raw_key,
        label=body.label or "",
        scopes=scopes,
        agent_id=body.agent_id,
        agent_version=body.agent_version,
        expires_at=body.expires_at,
    )
    # Echo the raw key in the response — this is the only chance the
    # caller has to capture it. The next list/get call will only
    # return the prefix.
    record["raw_key"] = raw_key
    record["is_active"] = True
    record["scopes"] = scopes
    return record


@router.get("")
def list_api_keys(
    include_revoked: bool = False,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    keys = db.list_api_keys(
        user.organization_id, include_revoked=include_revoked,
    )
    return {"api_keys": keys}


@router.get("/{key_id}")
def get_api_key(
    key_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    record = db.get_api_key(key_id, user.organization_id)
    if not record:
        raise HTTPException(status_code=404, detail="api_key_not_found")
    return record


@router.post("/{key_id}/rotate")
def rotate_api_key(
    key_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Rotate: revoke the old key + issue a new one with the same label.

    Returns the new raw key in the response — same "show once" rule
    as create. The old key is revoked atomically before the new one
    is issued so a brief window of "both keys valid" doesn't surface
    in the audit trail.
    """
    db = get_db()
    existing = db.get_api_key(key_id, user.organization_id)
    if not existing:
        raise HTTPException(status_code=404, detail="api_key_not_found")
    if not existing.get("is_active"):
        raise HTTPException(
            status_code=400,
            detail={"code": "key_already_revoked",
                    "message": "Cannot rotate a revoked key — create a new one instead."},
        )

    db.revoke_api_key(key_id, user.organization_id)

    raw_key = _generate_raw_key()
    user_id = getattr(user, "user_id", "") or getattr(user, "email", "")
    # Carry over the prior key's scopes — rotate is a "swap secret"
    # not a "change permissions". If the caller wants to alter scopes
    # they should DELETE + create a new key.
    prior_scopes = existing.get("scopes")
    if isinstance(prior_scopes, str):
        try:
            import json as _json
            prior_scopes = _json.loads(prior_scopes)
        except Exception:
            prior_scopes = []
    if not isinstance(prior_scopes, list):
        prior_scopes = []
    record = db.create_api_key(
        organization_id=user.organization_id,
        user_id=user_id,
        raw_key=raw_key,
        label=existing.get("label") or "",
        scopes=prior_scopes,
        # Carry the agent attribution + expiry across rotation — the
        # rotate semantic is "swap secret, keep identity", not "change
        # who this key represents". To change those, DELETE + create.
        agent_id=existing.get("agent_id"),
        agent_version=existing.get("agent_version"),
        expires_at=existing.get("expires_at"),
    )
    record["raw_key"] = raw_key
    record["is_active"] = True
    record["rotated_from"] = key_id
    record["scopes"] = prior_scopes
    return record


@router.delete("/{key_id}")
def revoke_api_key(
    key_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    revoked = db.revoke_api_key(key_id, user.organization_id)
    if not revoked:
        # Either doesn't exist, belongs to another org, or already
        # revoked. All collapse to 404 — the membership oracle stays
        # closed (same pattern as ap_item_not_found).
        existing = db.get_api_key(key_id, user.organization_id)
        if existing and not existing.get("is_active"):
            return {"revoked": False, "already_revoked": True, "id": key_id}
        raise HTTPException(status_code=404, detail="api_key_not_found")
    return {"revoked": True, "id": key_id}
