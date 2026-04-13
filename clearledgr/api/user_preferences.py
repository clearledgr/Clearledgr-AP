"""Per-user preferences endpoints.

User preferences are per-user UI state (pipeline view mode, column
order, saved-view selection, template choices). They are NOT org-scoped
admin data, so the ops-role gate that guards `/api/workspace/*` does
not apply here. Any authenticated workspace member can read and write
their own preferences.

Endpoints:
  GET   /api/user/preferences  — read the current user's prefs
  PATCH /api/user/preferences  — deep-merge a patch into the user's prefs

Isolation: both handlers resolve the user via `get_current_user`, so
the row loaded is always the authenticated user's. There is no way to
address another user's preferences through these endpoints.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db

# Reuse the existing helpers from workspace_shell so there's one
# canonical load/save/merge path. Underscored names are fine to import.
from clearledgr.api.workspace_shell import (
    _deep_merge_dict,
    _load_user_preferences,
    _resolve_org_id,
    _save_user_preferences,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/user", tags=["user-preferences"])


class UserPreferencesPatchRequest(BaseModel):
    """Patch body for PATCH /api/user/preferences.

    `organization_id` is optional — resolved from the authenticated
    user if absent. It is retained so that a user who belongs to
    multiple orgs can disambiguate. The patch itself is deep-merged
    into the stored preferences.
    """

    organization_id: Optional[str] = None
    patch: Dict[str, Any]


@router.get("/preferences")
def get_user_preferences(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    current_user = db.get_user(user.user_id)
    if not current_user:
        raise HTTPException(status_code=404, detail="user_not_found")
    if str(current_user.get("organization_id") or org_id) != org_id:
        raise HTTPException(status_code=403, detail="org_access_denied")
    return {
        "organization_id": org_id,
        "user_id": current_user.get("id") or user.user_id,
        "preferences": _load_user_preferences(current_user),
    }


@router.patch("/preferences")
def patch_user_preferences(
    request: UserPreferencesPatchRequest,
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    current_user = db.get_user(user.user_id)
    if not current_user:
        raise HTTPException(status_code=404, detail="user_not_found")
    if str(current_user.get("organization_id") or org_id) != org_id:
        raise HTTPException(status_code=403, detail="org_access_denied")
    preferences = _deep_merge_dict(
        _load_user_preferences(current_user),
        request.patch or {},
    )
    _save_user_preferences(
        str(current_user.get("id") or user.user_id),
        preferences,
    )
    return {
        "success": True,
        "organization_id": org_id,
        "user_id": current_user.get("id") or user.user_id,
        "preferences": preferences,
    }
