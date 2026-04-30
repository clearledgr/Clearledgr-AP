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
from typing import Any, Dict

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


class APIKeyCreateRequest(BaseModel):
    label: str = Field("", max_length=120)


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
        ...
      }
    """
    db = get_db()
    raw_key = _generate_raw_key()
    user_id = getattr(user, "user_id", "") or getattr(user, "email", "")

    record = db.create_api_key(
        organization_id=user.organization_id,
        user_id=user_id,
        raw_key=raw_key,
        label=body.label or "",
    )
    # Echo the raw key in the response — this is the only chance the
    # caller has to capture it. The next list/get call will only
    # return the prefix.
    record["raw_key"] = raw_key
    record["is_active"] = True
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
    record = db.create_api_key(
        organization_id=user.organization_id,
        user_id=user_id,
        raw_key=raw_key,
        label=existing.get("label") or "",
    )
    record["raw_key"] = raw_key
    record["is_active"] = True
    record["rotated_from"] = key_id
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
