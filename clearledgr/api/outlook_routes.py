"""Outlook / Microsoft 365 OAuth and webhook routes.

§12 #6 — Outlook is not shipped in V1. All routes in this module are
gated behind the ``FEATURE_OUTLOOK_ENABLED`` flag; without it, every
endpoint returns 404 with a reason code pointing at the V1 boundary.
The code stays intact for the post-V1 moment when Outlook support
becomes an intentional product decision.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.core.feature_flags import is_outlook_enabled, outlook_disabled_payload

logger = logging.getLogger(__name__)


def _require_outlook_enabled() -> None:
    """Dependency applied to every Outlook route — 404s the whole
    surface when the V1 flag is off. Runs before any handler body so
    no OAuth, token exchange, or webhook subscription can fire from a
    V1 deployment.
    """
    if not is_outlook_enabled():
        raise HTTPException(status_code=404, detail=outlook_disabled_payload())


router = APIRouter(
    prefix="/outlook",
    tags=["outlook"],
    dependencies=[Depends(_require_outlook_enabled)],
)


# ---------------------------------------------------------------------------
# OAuth connect flow
# ---------------------------------------------------------------------------

@router.get("/connect/start")
def outlook_connect_start(
    user: TokenData = Depends(get_current_user),
):
    """Start Microsoft OAuth flow — returns the authorization URL."""
    from clearledgr.services.outlook_api import is_outlook_configured, generate_auth_url

    if not is_outlook_configured():
        return JSONResponse(
            status_code=400,
            content={"error": "Outlook integration not configured (MICROSOFT_CLIENT_ID missing)"},
        )

    state = f"{user.user_id}:{user.organization_id}"
    url = generate_auth_url(state=state)
    return {"auth_url": url}


@router.get("/callback")
async def outlook_callback(
    code: str = Query(...),
    state: str = Query(default=""),
):
    """OAuth callback — exchanges code for tokens and stores them."""
    from clearledgr.services.outlook_api import (
        exchange_code_for_tokens,
        outlook_token_store,
        OutlookToken,
    )

    try:
        token = await exchange_code_for_tokens(code)
    except Exception as exc:
        logger.error("Outlook OAuth token exchange failed: %s", exc)
        return JSONResponse(
            status_code=400,
            content={"error": "Token exchange failed", "detail": str(exc)},
        )

    # If state contains user_id:org_id, use it; otherwise use token's user_id
    if ":" in state:
        user_id, org_id = state.split(":", 1)
        token = OutlookToken(
            user_id=user_id,
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            expires_at=token.expires_at,
            email=token.email,
        )
    else:
        org_id = "default"

    outlook_token_store.store(token)

    # Save initial autopilot state
    db = get_db()
    db.save_outlook_autopilot_state(
        user_id=token.user_id,
        email=token.email,
        last_error=None,
    )

    logger.info("Outlook connected for user=%s email=%s", token.user_id, token.email)

    redirect = os.getenv("OUTLOOK_CONNECT_REDIRECT", "/")
    return RedirectResponse(url=redirect)


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------

@router.post("/disconnect")
async def outlook_disconnect(
    user: TokenData = Depends(get_current_user),
):
    """Disconnect Outlook — removes tokens and stops polling."""
    from clearledgr.services.outlook_api import outlook_token_store

    outlook_token_store.delete(user.user_id)
    logger.info("Outlook disconnected for user=%s", user.user_id)
    return {"status": "disconnected"}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/status")
def outlook_status(
    user: TokenData = Depends(get_current_user),
):
    """Check Outlook connection status for the current user."""
    from clearledgr.services.outlook_api import outlook_token_store

    token = outlook_token_store.get(user.user_id)
    db = get_db()
    state = db.get_outlook_autopilot_state(user.user_id) or {}

    return {
        "connected": bool(token),
        "email": token.email if token else None,
        "expires_at": token.expires_at.isoformat() if token else None,
        "is_expired": token.is_expired() if token else False,
        "autopilot": {
            "last_scan_at": state.get("last_scan_at"),
            "subscription_id": state.get("subscription_id"),
            "subscription_expiration": state.get("subscription_expiration"),
            "last_error": state.get("last_error"),
        },
    }


# ---------------------------------------------------------------------------
# Webhook (change notifications from Microsoft Graph)
# ---------------------------------------------------------------------------

@router.post("/webhook")
async def outlook_webhook(request: Request):
    """Handle Microsoft Graph change notifications.

    Microsoft sends a validation request first (with validationToken),
    then actual notifications for new messages.
    """
    # Validation handshake
    validation_token = request.query_params.get("validationToken")
    if validation_token:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content=validation_token)

    # Actual notification
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    webhook_secret = os.getenv("OUTLOOK_WEBHOOK_SECRET", "")

    for notification in body.get("value", []):
        client_state = notification.get("clientState", "")
        if webhook_secret and client_state != webhook_secret:
            logger.warning("Outlook webhook: invalid clientState")
            continue

        resource = notification.get("resource", "")
        change_type = notification.get("changeType", "")

        if change_type == "created" and "messages" in resource:
            # A new message arrived — the autopilot poll loop will pick it up
            # on its next tick.  We log it for observability but don't process
            # inline to avoid webhook timeout issues.
            logger.info("Outlook webhook: new message notification for resource=%s", resource)

    return JSONResponse(status_code=202, content={"status": "accepted"})
