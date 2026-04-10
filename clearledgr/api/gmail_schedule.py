"""Scheduled email send endpoint for Clearledgr.

Thesis section 3: AP Managers composing direct vendor communications can schedule
them to arrive at a specific date/time within Gmail, at a strategically chosen
moment.

The endpoint converts a Gmail draft into a scheduled message by injecting the
``X-Google-Delayed-Sending`` header and re-creating the draft so Gmail holds
delivery until the requested time.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, require_ops_user
from clearledgr.core.errors import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gmail", tags=["gmail-schedule"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ScheduleSendRequest(BaseModel):
    """Schedule a Gmail draft for future delivery."""

    organization_id: str = Field(..., description="Tenant org ID")
    draft_id: str = Field(..., min_length=1, description="Gmail draft ID to schedule")
    send_at: str = Field(
        ...,
        description="ISO-8601 datetime for delivery (e.g. 2026-04-15T09:00:00Z)",
    )
    vendor: Optional[str] = Field(
        None,
        description="Vendor name — included in the audit event for traceability",
    )


class ScheduleSendResponse(BaseModel):
    """Confirmation of a scheduled email."""

    scheduled: bool
    new_draft_id: str = Field(
        ...,
        description="Replacement draft ID carrying the schedule header",
    )
    send_at: str
    message: str = "Draft scheduled for delivery"


# ---------------------------------------------------------------------------
# Lazy module loaders (avoid circular imports at module level)
# ---------------------------------------------------------------------------

_GMAIL_API_MODULE = None
_AUDIT_TRAIL_MODULE = None


def _gmail_api():
    global _GMAIL_API_MODULE
    if _GMAIL_API_MODULE is None:
        from clearledgr.services import gmail_api as mod
        _GMAIL_API_MODULE = mod
    return _GMAIL_API_MODULE


def _audit_trail():
    global _AUDIT_TRAIL_MODULE
    if _AUDIT_TRAIL_MODULE is None:
        from clearledgr.services import audit_trail as mod
        _AUDIT_TRAIL_MODULE = mod
    return _AUDIT_TRAIL_MODULE


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/schedule-send", response_model=ScheduleSendResponse)
async def schedule_send(
    request: ScheduleSendRequest,
    user: TokenData = Depends(require_ops_user),
):
    """Schedule a Gmail draft for future delivery.

    Converts an existing draft into a scheduled message by injecting the
    ``X-Google-Delayed-Sending`` header. The draft stays in the user's
    Gmail Drafts folder with a "Scheduled" indicator until the requested
    time, at which point Gmail delivers it automatically.

    Requires ``gmail.send`` + ``gmail.modify`` OAuth scopes (already
    granted during Gmail onboarding).
    """
    # -- Validate send_at -------------------------------------------------
    try:
        send_at_dt = datetime.fromisoformat(request.send_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=422,
            detail="send_at_invalid: expected ISO-8601 datetime",
        )

    if send_at_dt.tzinfo is None:
        send_at_dt = send_at_dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if send_at_dt <= now:
        raise HTTPException(
            status_code=422,
            detail="send_at_in_past: scheduled time must be in the future",
        )

    send_at_unix_ms = int(send_at_dt.timestamp() * 1000)

    # -- Resolve Gmail identity -------------------------------------------
    user_id = str(getattr(user, "user_id", "") or "").strip()
    user_email = str(getattr(user, "email", "") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id_missing")

    # Try resolving the Gmail token by user_id first, then by email.
    gmail_mod = _gmail_api()
    token = gmail_mod.token_store.get(user_id)
    if not token and user_email:
        token = gmail_mod.token_store.get_by_email(user_email)
    if not token:
        raise HTTPException(status_code=409, detail="gmail_not_connected")

    gmail_client = gmail_mod.GmailAPIClient(token.user_id)
    if not await gmail_client.ensure_authenticated():
        raise HTTPException(status_code=409, detail="gmail_auth_expired")

    # -- Schedule the draft -----------------------------------------------
    try:
        result = await gmail_client.schedule_draft_send(
            draft_id=request.draft_id,
            send_at_unix_ms=send_at_unix_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        ref = safe_error(exc, context="schedule_draft_send")
        raise HTTPException(
            status_code=502,
            detail=f"gmail_schedule_failed: {ref}",
        )

    new_draft_id = str((result.get("id") or "")).strip()

    # -- Audit event ------------------------------------------------------
    try:
        _audit_trail().record_audit_event(
            user_email=user_email or user_id,
            action="email_scheduled",
            entity_type="gmail_draft",
            entity_id=new_draft_id or request.draft_id,
            source_type="api",
            metadata={
                "original_draft_id": request.draft_id,
                "new_draft_id": new_draft_id,
                "send_at": request.send_at,
                "vendor": request.vendor or None,
                "actor": user_email or user_id,
                "organization_id": request.organization_id,
            },
            organization_id=request.organization_id,
        )
    except Exception:
        # Audit failure must never block the happy path.
        logger.warning("Failed to record email_scheduled audit event", exc_info=True)

    return ScheduleSendResponse(
        scheduled=True,
        new_draft_id=new_draft_id,
        send_at=request.send_at,
    )
