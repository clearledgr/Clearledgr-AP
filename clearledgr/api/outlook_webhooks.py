"""
Microsoft Graph Change Notification Webhook Handler

Receives push notifications from Microsoft Graph when new Outlook messages arrive.
Mirrors the Gmail webhook handler exactly.

Microsoft Graph notification format:
{
  "value": [{
    "subscriptionId": "...",
    "changeType": "created",
    "clientState": "<secret>",
    "resource": "Users/{user_id}/Messages/{message_id}",
    "resourceData": {"id": "{message_id}"}
  }]
}

On first subscription creation, Graph sends a GET with ?validationToken=<token>
that must be echoed back as 200 text/plain.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from clearledgr.core.errors import safe_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/outlook", tags=["outlook"])

WEBHOOK_SECRET = os.getenv("OUTLOOK_WEBHOOK_SECRET", "")


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------


@router.post("/webhook")
async def outlook_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    validationToken: Optional[str] = Query(default=None),
):
    """
    Receive Microsoft Graph change notifications.

    Microsoft sends a GET (or POST with validationToken query param) when
    registering a subscription — must respond 200 text/plain with the token.
    """
    # Subscription validation handshake
    if validationToken:
        logger.info("Outlook subscription validation: echoing token")
        return PlainTextResponse(content=validationToken, status_code=200)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Validate clientState if secret is configured
    notifications: List[Dict[str, Any]] = body.get("value", [])
    if WEBHOOK_SECRET:
        for note in notifications:
            if note.get("clientState") != WEBHOOK_SECRET:
                logger.warning("Outlook webhook: invalid clientState, ignoring notification")
                return {"status": "ignored"}

    for note in notifications:
        change_type = note.get("changeType")
        if change_type != "created":
            continue

        resource = note.get("resource", "")
        message_id = _extract_message_id(resource) or (note.get("resourceData") or {}).get("id")
        user_id = _extract_user_id(resource)

        if not message_id:
            logger.warning("Outlook webhook: could not extract message_id from resource: %s", resource)
            continue

        logger.info("Outlook webhook: new message %s for user %s", message_id, user_id)
        background_tasks.add_task(_process_outlook_message, message_id, user_id)

    # Always 202 to prevent Graph retries
    return {"status": "accepted"}


def _extract_message_id(resource: str) -> Optional[str]:
    """Extract message ID from Graph resource URL like Users/{uid}/Messages/{mid}."""
    match = re.search(r"/[Mm]essages/'?([A-Za-z0-9=_\-]+)'?", resource)
    return match.group(1) if match else None


def _extract_user_id(resource: str) -> Optional[str]:
    """Extract user ID from Graph resource URL."""
    match = re.search(r"[Uu]sers/'?([A-Za-z0-9@.\-_]+)'?/", resource)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------


async def _process_outlook_message(message_id: str, graph_user_id: Optional[str]) -> None:
    """
    Process a single Outlook message autonomously.
    Mirrors process_single_email() from gmail_webhooks.py.
    """
    from clearledgr.services.outlook_api import token_store, OutlookAPIClient
    from clearledgr.api.gmail_webhooks import (
        classify_email_with_llm,
        process_invoice_email as _gmail_invoice,
    )
    from clearledgr.core.engine import get_engine
    from clearledgr.services.ai_enhanced import EnhancedAIService

    # Resolve token — try by graph_user_id first, then scan all outlook tokens
    token = token_store.get(graph_user_id) if graph_user_id else None
    if not token:
        logger.warning("No Outlook token for graph_user_id=%s, cannot process message", graph_user_id)
        return

    client = OutlookAPIClient(token.user_id)
    if not await client.ensure_authenticated():
        logger.error("Outlook auth failed for user %s", token.user_id)
        return

    try:
        message = await client.get_message(message_id)
    except Exception as exc:
        logger.error("Failed to fetch Outlook message %s: %s", message_id, exc)
        return

    # Skip already-processed messages
    from clearledgr.core.database import get_db
    db = get_db()
    if db.get_finance_email_by_gmail_id(message.id):
        return
    if "Clearledgr/Processed" in message.labels:
        return

    engine = get_engine()
    ai_service = EnhancedAIService()

    classification = await classify_email_with_llm(
        subject=message.subject,
        sender=message.sender,
        snippet=message.snippet,
        body=message.body_text[:2000],
        attachments=message.attachments or [],
        ai_service=ai_service,
    )

    logger.info(
        "Outlook message '%s' classified as: %s (%.2f)",
        message.subject,
        classification.get("type"),
        classification.get("confidence", 0.0),
    )

    if classification.get("type") == "NOISE" or classification.get("confidence", 0) < 0.7:
        return

    category = str(classification.get("type") or "").lower()
    if category not in {"invoice", "payment_request"}:
        return

    engine.detect_finance_email(
        email_id=message.id,
        subject=message.subject,
        sender=message.sender,
        category=category,
        confidence=classification.get("confidence", 0.0),
        received_at=message.date,
        user_id=token.user_id,
    )

    if category == "invoice":
        # Reuse the Gmail invoice processing pipeline (same data shape)
        try:
            from clearledgr.services.invoice_workflow import InvoiceWorkflow
            workflow = InvoiceWorkflow()
            from clearledgr.services.gmail_api import GmailMessage
            gmail_msg = GmailMessage(
                id=message.id,
                thread_id=message.thread_id,
                subject=message.subject,
                sender=message.sender,
                recipient=message.recipient,
                date=message.date,
                snippet=message.snippet,
                body_text=message.body_text,
                body_html=message.body_html,
                labels=message.labels,
                attachments=message.attachments,
            )
            await workflow.process_email(gmail_msg, user_id=token.user_id)
        except Exception as exc:
            logger.error("Outlook invoice processing failed for %s: %s", message_id, exc)

    try:
        await client.mark_as_processed(message_id)
    except Exception as exc:
        logger.warning("Could not mark Outlook message %s as processed: %s", message_id, exc)


# ---------------------------------------------------------------------------
# OAuth endpoints
# ---------------------------------------------------------------------------


@router.get("/authorize")
async def outlook_authorize(user_id: str = Query(..., description="Internal user ID")):
    """Generate Microsoft OAuth authorization URL and redirect."""
    from fastapi.responses import RedirectResponse
    from clearledgr.services.outlook_api import generate_outlook_auth_url
    try:
        url = generate_outlook_auth_url(user_id)
        return RedirectResponse(url=url)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/callback")
async def outlook_callback(
    code: str = Query(...),
    state: str = Query(default=""),
    error: Optional[str] = Query(default=None),
    error_description: Optional[str] = Query(default=None),
):
    """
    Handle Microsoft OAuth callback, exchange code for tokens,
    create Graph subscription, redirect to frontend.
    """
    from fastapi.responses import RedirectResponse
    from clearledgr.services.outlook_api import exchange_outlook_code_for_tokens, OutlookSubscriptionService

    if error:
        logger.error("Outlook OAuth error: %s — %s", error, error_description)
        raise HTTPException(status_code=400, detail=f"OAuth error: {error_description or error}")

    user_id = state or "default"

    try:
        token = await exchange_outlook_code_for_tokens(code=code, user_id=user_id)
        logger.info("Outlook OAuth complete for user %s (%s)", user_id, token.email)
    except Exception as exc:
        logger.error("Outlook token exchange failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to exchange authorization code")

    # Set up Graph change notification subscription
    notification_url = os.getenv("OUTLOOK_WEBHOOK_URL", "")
    if notification_url:
        try:
            sub_svc = OutlookSubscriptionService(user_id)
            sub_data = await sub_svc.create_subscription(
                notification_url=notification_url,
                client_state=WEBHOOK_SECRET or user_id,
            )
            logger.info("Outlook subscription created: %s", sub_data.get("id"))
        except Exception as exc:
            logger.warning("Could not create Outlook subscription: %s", exc)

    redirect_url = os.getenv("OUTLOOK_CONNECT_REDIRECT", "/")
    return RedirectResponse(url=redirect_url)


@router.post("/disconnect")
async def outlook_disconnect(user_id: str = Query(...)):
    """Remove Outlook token and Graph subscription."""
    from clearledgr.services.outlook_api import token_store
    token_store.delete(user_id)
    logger.info("Outlook disconnected for user %s", user_id)
    return {"status": "disconnected", "user_id": user_id}


@router.get("/status/{user_id}")
async def outlook_status(user_id: str):
    """Return Outlook connection status for a user."""
    from clearledgr.services.outlook_api import token_store
    token = token_store.get(user_id)
    if not token:
        return {"connected": False, "user_id": user_id}
    return {
        "connected": True,
        "user_id": user_id,
        "email": token.email,
        "token_expired": token.is_expired(),
        "expires_at": token.expires_at.isoformat(),
    }
