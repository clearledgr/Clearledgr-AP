"""
Gmail Pub/Sub Webhook Handler for AP v1.

Receives push notifications from Google Cloud Pub/Sub and processes
new emails for AP intake. No reconciliation or non-AP workflows.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, BackgroundTasks
from pydantic import BaseModel

from clearledgr.core.database import get_db
from clearledgr.services.gmail_api import GmailAPIClient, token_store
from clearledgr.services.email_parser import parse_email
from clearledgr.services.invoice_workflow import InvoiceData, get_invoice_workflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gmail", tags=["gmail"])


class PubSubMessage(BaseModel):
    message: Dict[str, Any]
    subscription: str


@router.post("/push")
async def gmail_push_notification(request: Request, background_tasks: BackgroundTasks):
    """
    Receive Gmail push notifications from Google Cloud Pub/Sub.
    Processing happens in the background to respond quickly to Google.
    """
    try:
        body = await request.json()
        message_data = body.get("message", {}).get("data", "")
        if message_data:
            decoded = base64.urlsafe_b64decode(message_data).decode("utf-8")
            notification = json.loads(decoded)
            email_address = notification.get("emailAddress")
            history_id = notification.get("historyId")
            background_tasks.add_task(process_gmail_notification, email_address, history_id)
        return {"status": "ok"}
    except Exception as exc:
        logger.error("Error processing Gmail push: %s", exc)
        return {"status": "error", "message": str(exc)}


async def process_gmail_notification(email_address: str, history_id: Optional[str]):
    """
    Process a Gmail notification in the background.
    """
    token = token_store.get_by_email(email_address or "")
    if not token:
        logger.warning("No token found for %s", email_address)
        return

    client = GmailAPIClient(token.user_id)
    if not await client.ensure_authenticated():
        logger.error("Failed to authenticate for %s", email_address)
        return

    db = get_db()
    db.save_gmail_autopilot_state(
        user_id=token.user_id,
        email=token.email,
        last_history_id=str(history_id) if history_id else None,
        last_scan_at=datetime.now(timezone.utc).isoformat(),
        last_error=None,
    )

    message_ids = []
    if history_id:
        try:
            history = await client.get_history(history_id)
            for record in history.get("history", []) or []:
                for added in record.get("messagesAdded", []):
                    message_ids.append(added.get("message", {}).get("id"))
        except Exception as exc:
            logger.warning("History lookup failed, falling back to list: %s", exc)

    if not message_ids:
        messages_response = await client.list_messages(
            query="in:inbox newer_than:2d",
            max_results=25,
        )
        message_ids = [m.get("id") for m in messages_response.get("messages", []) if m.get("id")]

    if not message_ids:
        return

    for message_id in message_ids:
        try:
            await process_single_email(client, message_id, token.user_id, token.email)
        except Exception as exc:
            logger.warning("Autopilot email processing failed: %s", exc)


async def process_single_email(
    client: GmailAPIClient,
    message_id: str,
    user_id: str,
    user_email: Optional[str],
) -> None:
    """
    Process a single email for AP intake.
    """
    message = await client.get_message(message_id)

    extraction = parse_email(
        subject=message.subject or "",
        body=message.body_text or "",
        sender=message.sender or "",
        attachments=message.attachments or [],
    )

    email_type = str(extraction.get("email_type") or "").lower()
    if email_type not in {"invoice", "payment_request", "credit_note"}:
        return

    workflow = get_invoice_workflow("default")

    invoice = InvoiceData(
        gmail_id=message.id,
        thread_id=message.thread_id,
        message_id=message.id,
        subject=message.subject or "",
        sender=message.sender or "",
        vendor_name=extraction.get("vendor") or message.sender or "Unknown vendor",
        amount=extraction.get("primary_amount"),
        currency=extraction.get("currency") or "USD",
        invoice_number=extraction.get("primary_invoice"),
        invoice_date=extraction.get("primary_date"),
        due_date=extraction.get("primary_date"),
        confidence=extraction.get("confidence") or 0.0,
        organization_id="default",
        user_id=user_email,
        metadata={"raw": extraction, "source": "gmail_autopilot"},
    )

    await workflow.process_new_invoice(invoice)
