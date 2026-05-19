"""Notification preferences API — Module 11.

Per-user toggles for the three notification channels (email, Slack,
in-app). Backed by ``users.preferences_json``; ``notifications`` lives
inside the broader prefs document so we don't grow a separate table
for what is configuration data.

  GET   /api/workspace/notification-preferences
  PATCH /api/workspace/notification-preferences
  GET   /api/workspace/notification-preferences/schema
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from solden.core.auth import TokenData, get_current_user
from solden.core.database import get_db
from solden.services import notification_preferences as svc

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace/notification-preferences",
    tags=["notification-preferences"],
)


class NotificationPrefsPatch(BaseModel):
    email: Optional[Dict[str, bool]] = None
    slack: Optional[Dict[str, bool]] = None
    in_app: Optional[Dict[str, bool]] = Field(default=None, alias="in_app")

    model_config = {"populate_by_name": True}


@router.get("")
def get_preferences(
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    user_id = getattr(user, "user_id", "") or getattr(user, "email", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="user_not_found")
    prefs = svc.load_notification_prefs(db, user_id)
    return {
        "user_id": user_id,
        "preferences": prefs,
    }


@router.patch("")
def patch_preferences(
    body: NotificationPrefsPatch,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    user_id = getattr(user, "user_id", "") or getattr(user, "email", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="user_not_found")

    # Read the existing prefs, then layer the patch on. The patch is
    # narrow (one channel at a time, often) and shouldn't reset the
    # other channels' toggles to defaults.
    current = svc.load_notification_prefs(db, user_id)
    if body.email is not None:
        current["email"] = {**current["email"], **body.email}
    if body.slack is not None:
        current["slack"] = {**current["slack"], **body.slack}
    if body.in_app is not None:
        current["in_app"] = {**current["in_app"], **body.in_app}

    saved = svc.save_notification_prefs(db, user_id, current)
    return {
        "user_id": user_id,
        "preferences": saved,
    }


@router.get("/schema")
def get_schema(
    _user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the canonical schema so the frontend can render every
    available toggle even if the user hasn't saved any prefs yet."""
    return {
        "channels": list(svc.VALID_CHANNELS),
        "defaults": svc.get_default_notification_prefs(),
    }
