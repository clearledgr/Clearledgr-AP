"""Microsoft Teams outbound notifications (PLAN.md Section 5.3).

Sends Adaptive Card messages to Teams channels via the Bot Framework
REST API.  Parallel to ``slack_notifications.py`` but uses Teams
Adaptive Cards instead of Slack Block Kit.

Requires env vars:
    TEAMS_APP_ID      — Bot registration Application (client) ID
    TEAMS_APP_SECRET  — Bot registration client secret
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Microsoft identity token endpoint for bot-to-bot auth
_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
_BOT_SCOPE = "https://api.botframework.com/.default"

# Module-level token cache: {"access_token": str, "expires_at": float}
_token_cache: Dict[str, Any] = {}

# Default Teams service URL (overridable per-conversation)
_DEFAULT_SERVICE_URL = "https://smba.trafficmanager.net/amer"


# ---------------------------------------------------------------------------
# OAuth token management
# ---------------------------------------------------------------------------

async def _get_bot_token() -> str:
    """Obtain an OAuth2 access token for the bot using client credentials.

    Caches the token until 5 minutes before expiry.
    """
    now = time.time()
    if _token_cache.get("access_token") and now < _token_cache.get("expires_at", 0) - 300:
        return _token_cache["access_token"]

    app_id = os.getenv("TEAMS_APP_ID", "").strip()
    app_secret = os.getenv("TEAMS_APP_SECRET", "").strip()

    if not app_id or not app_secret:
        raise RuntimeError(
            "TEAMS_APP_ID and TEAMS_APP_SECRET must be set for Teams notifications"
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": app_id,
                "client_secret": app_secret,
                "scope": _BOT_SCOPE,
            },
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()

    _token_cache["access_token"] = body["access_token"]
    _token_cache["expires_at"] = now + body.get("expires_in", 3600)

    logger.info("Obtained new Bot Framework access token (expires_in=%s)", body.get("expires_in"))
    return _token_cache["access_token"]


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

async def _post_activity(
    service_url: str,
    conversation_id: str,
    activity: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Send an activity to a Teams conversation via the Bot Framework REST API.

    Args:
        service_url: The Teams service URL (e.g. ``https://smba.trafficmanager.net/amer``).
        conversation_id: The target conversation / channel ID.
        activity: The Bot Framework activity payload.

    Returns:
        The API response body on success, or ``None`` on failure.
    """
    token = await _get_bot_token()
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=activity,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Teams API error: status=%s body=%s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        return None
    except Exception as exc:
        logger.error("Failed to post Teams activity: %s", exc)
        return None


def _make_adaptive_card_activity(card_body: List[Dict[str, Any]], summary: str) -> Dict[str, Any]:
    """Wrap an Adaptive Card body list into a Bot Framework message activity."""
    return {
        "type": "message",
        "summary": summary,
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": card_body,
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# Public API — mirrors slack_notifications.py patterns
# ---------------------------------------------------------------------------

async def send_approval_request(
    item: Dict[str, Any],
    channel_id: str,
    organization_id: str,
    service_url: Optional[str] = None,
) -> bool:
    """Send an invoice approval Adaptive Card with Approve / Reject buttons.

    Args:
        item: AP item dict (must contain ``id``, ``vendor``, ``amount``,
              optionally ``due_date``, ``description``, ``exceptions``).
        channel_id: Teams channel / conversation ID.
        organization_id: Org ID for logging context.
        service_url: Teams service URL override.

    Returns:
        True if the message was sent successfully.
    """
    svc = (service_url or os.getenv("TEAMS_SERVICE_URL", "").strip() or _DEFAULT_SERVICE_URL)
    item_id = item.get("id", "unknown")
    vendor = item.get("vendor", "Unknown vendor")
    amount = item.get("amount", 0)
    due_date = item.get("due_date")
    description = item.get("description", "")
    exceptions = item.get("exceptions") or []

    # Build Adaptive Card body
    card_body: List[Dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": "Invoice Needs Approval",
        },
        {
            "type": "FactSet",
            "facts": [
                {"title": "Vendor", "value": vendor},
                {"title": "Amount", "value": f"${amount:,.2f}"},
                {"title": "Due", "value": due_date or "N/A"},
                {"title": "Org", "value": organization_id},
            ],
        },
    ]

    if description:
        card_body.append({
            "type": "TextBlock",
            "text": description[:300],
            "wrap": True,
            "isSubtle": True,
        })

    if exceptions:
        card_body.append({
            "type": "TextBlock",
            "text": "**Issues:**\n" + "\n".join(f"- {e}" for e in exceptions),
            "wrap": True,
            "color": "Attention",
        })

    # Action buttons
    card_body.append({
        "type": "ActionSet",
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Approve",
                "style": "positive",
                "data": {
                    "action": "approve_invoice",
                    "item_id": item_id,
                    "organization_id": organization_id,
                },
            },
            {
                "type": "Action.Submit",
                "title": "Reject",
                "style": "destructive",
                "data": {
                    "action": "reject_invoice",
                    "item_id": item_id,
                    "organization_id": organization_id,
                },
            },
            {
                "type": "Action.Submit",
                "title": "Flag for Review",
                "data": {
                    "action": "flag_invoice",
                    "item_id": item_id,
                    "organization_id": organization_id,
                },
            },
        ],
    })

    card_body.append({
        "type": "TextBlock",
        "text": f"Invoice ID: {item_id}",
        "isSubtle": True,
        "size": "Small",
    })

    activity = _make_adaptive_card_activity(
        card_body, summary=f"Invoice {item_id} needs approval"
    )

    result = await _post_activity(svc, channel_id, activity)
    if result is not None:
        logger.info(
            "Sent Teams approval request for item=%s org=%s",
            item_id,
            organization_id,
        )
        return True

    logger.warning(
        "Failed to send Teams approval request for item=%s org=%s",
        item_id,
        organization_id,
    )
    return False


async def send_status_update(
    item: Dict[str, Any],
    channel_id: str,
    new_state: str,
    service_url: Optional[str] = None,
    reply_to_id: Optional[str] = None,
) -> bool:
    """Send a status-update card (or threaded reply) to a Teams conversation.

    Args:
        item: AP item dict (``id``, ``vendor``, ``amount``).
        channel_id: Teams channel / conversation ID.
        new_state: The new AP state name (e.g. ``"approved"``, ``"posted"``).
        service_url: Teams service URL override.
        reply_to_id: If provided, reply to this activity ID (threads the message).

    Returns:
        True if the message was sent successfully.
    """
    svc = (service_url or os.getenv("TEAMS_SERVICE_URL", "").strip() or _DEFAULT_SERVICE_URL)
    item_id = item.get("id", "unknown")
    vendor = item.get("vendor", "Unknown vendor")
    amount = item.get("amount", 0)

    state_label_map = {
        "approved": "Approved",
        "rejected": "Rejected",
        "posted": "Posted to ERP",
        "paid": "Paid",
        "flagged": "Flagged for Review",
        "error": "Error",
    }
    state_label = state_label_map.get(new_state, new_state.replace("_", " ").title())

    color_map = {
        "approved": "Good",
        "posted": "Good",
        "paid": "Good",
        "rejected": "Attention",
        "error": "Attention",
        "flagged": "Warning",
    }
    color = color_map.get(new_state, "Default")

    card_body: List[Dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Medium",
            "weight": "Bolder",
            "text": f"Invoice Status Update: {state_label}",
            "color": color,
        },
        {
            "type": "FactSet",
            "facts": [
                {"title": "Invoice", "value": item_id},
                {"title": "Vendor", "value": vendor},
                {"title": "Amount", "value": f"${amount:,.2f}"},
                {"title": "New Status", "value": state_label},
            ],
        },
    ]

    erp_ref = item.get("erp_reference")
    if erp_ref:
        card_body.append({
            "type": "TextBlock",
            "text": f"ERP Reference: {erp_ref}",
            "isSubtle": True,
            "size": "Small",
        })

    activity = _make_adaptive_card_activity(
        card_body, summary=f"Invoice {item_id} — {state_label}"
    )

    # Thread the reply if we have a parent activity ID
    if reply_to_id:
        activity["replyToId"] = reply_to_id

    result = await _post_activity(svc, channel_id, activity)
    if result is not None:
        logger.info(
            "Sent Teams status update for item=%s state=%s",
            item_id,
            new_state,
        )
        return True

    logger.warning(
        "Failed to send Teams status update for item=%s state=%s",
        item_id,
        new_state,
    )
    return False
