"""Outgoing webhook delivery — notifies external systems of AP events.

Event types:
- invoice.received, invoice.validated, invoice.approved, invoice.rejected
- invoice.posted_to_erp, invoice.closed, invoice.needs_info
- payment.completed, payment.failed, payment.reversed

Delivery is async with HMAC-SHA256 signing.  Failed deliveries are
enqueued in the existing notification retry queue.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Map AP states to webhook event types
_STATE_TO_EVENT = {
    "received": "invoice.received",
    "validated": "invoice.validated",
    "needs_approval": "invoice.needs_approval",
    "approved": "invoice.approved",
    "rejected": "invoice.rejected",
    "ready_to_post": "invoice.ready_to_post",
    "posted_to_erp": "invoice.posted_to_erp",
    "closed": "invoice.closed",
    "needs_info": "invoice.needs_info",
    "failed_post": "invoice.failed_post",
}

WEBHOOK_TIMEOUT = 10  # seconds


def compute_signature(payload_bytes: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for webhook payload."""
    return hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


async def deliver_webhook(
    url: str,
    event_type: str,
    payload: Dict[str, Any],
    secret: str = "",
    webhook_id: str = "",
) -> bool:
    """Deliver a single webhook.  Returns True on success (2xx)."""
    delivery_id = webhook_id or f"whd_{uuid.uuid4().hex[:12]}"
    payload_with_meta = {
        "event": event_type,
        "delivery_id": delivery_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }

    body = json.dumps(payload_with_meta, default=str)
    body_bytes = body.encode("utf-8")

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "X-Clearledgr-Event": event_type,
        "X-Clearledgr-Delivery": delivery_id,
    }
    if secret:
        sig = compute_signature(body_bytes, secret)
        headers["X-Clearledgr-Signature"] = f"sha256={sig}"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                content=body_bytes,
                headers=headers,
                timeout=WEBHOOK_TIMEOUT,
            )
        if 200 <= response.status_code < 300:
            logger.debug("[Webhook] Delivered %s to %s (HTTP %d)", event_type, url, response.status_code)
            return True
        else:
            logger.warning("[Webhook] %s to %s returned HTTP %d", event_type, url, response.status_code)
            return False
    except Exception as exc:
        logger.warning("[Webhook] Delivery failed %s to %s: %s", event_type, url, exc)
        return False


async def emit_webhook_event(
    organization_id: str,
    event_type: str,
    payload: Dict[str, Any],
) -> int:
    """Emit a webhook event to all matching subscriptions.

    Attempts immediate delivery.  On failure, enqueues in the
    notification retry queue for later retries.

    Returns the number of subscriptions notified.
    """
    from clearledgr.core.database import get_db

    db = get_db()
    subscriptions = db.get_active_webhooks_for_event(organization_id, event_type)

    if not subscriptions:
        return 0

    delivered = 0
    for sub in subscriptions:
        url = sub.get("url", "")
        secret = sub.get("secret", "")
        sub_id = sub.get("id", "")

        ok = await deliver_webhook(
            url=url,
            event_type=event_type,
            payload=payload,
            secret=secret,
            webhook_id=f"whd_{sub_id}_{uuid.uuid4().hex[:8]}",
        )

        if ok:
            delivered += 1
        else:
            # Enqueue for retry using existing notification infrastructure
            try:
                db.enqueue_notification(
                    organization_id=organization_id,
                    channel="webhook",
                    payload={
                        "webhook_subscription_id": sub_id,
                        "url": url,
                        "secret": secret,
                        "event_type": event_type,
                        "data": payload,
                    },
                    ap_item_id=payload.get("ap_item_id"),
                    max_retries=5,
                )
            except Exception as exc:
                logger.error("[Webhook] Failed to enqueue retry for %s: %s", url, exc)

    return delivered


async def emit_state_change_webhook(
    organization_id: str,
    ap_item_id: str,
    new_state: str,
    prev_state: str = "",
    item_data: Optional[Dict[str, Any]] = None,
) -> int:
    """Convenience: emit a webhook for an AP state transition."""
    event_type = _STATE_TO_EVENT.get(new_state)
    if not event_type:
        return 0

    payload = {
        "ap_item_id": ap_item_id,
        "new_state": new_state,
        "prev_state": prev_state,
        "organization_id": organization_id,
    }
    if item_data:
        payload.update({
            "vendor_name": item_data.get("vendor_name", ""),
            "amount": item_data.get("amount"),
            "currency": item_data.get("currency", "USD"),
            "invoice_number": item_data.get("invoice_number", ""),
            "due_date": item_data.get("due_date", ""),
        })

    return await emit_webhook_event(organization_id, event_type, payload)


async def retry_webhook_delivery(notification: Dict[str, Any]) -> bool:
    """Retry a failed webhook delivery from the notification queue.

    Called by the background notification retry processor when
    channel='webhook'.
    """
    payload = notification.get("payload_json")
    if isinstance(payload, str):
        payload = json.loads(payload)

    url = payload.get("url", "")
    secret = payload.get("secret", "")
    event_type = payload.get("event_type", "")
    data = payload.get("data", {})

    if not url or not event_type:
        return False

    return await deliver_webhook(
        url=url,
        event_type=event_type,
        payload=data,
        secret=secret,
    )
