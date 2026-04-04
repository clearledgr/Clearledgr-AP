"""
Outlook email processor — bridges Outlook messages into the AP pipeline.

Uses the same triage service as Gmail (run_inline_gmail_triage).
No fallbacks — if the triage pipeline fails, it fails visibly.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def process_outlook_email(
    client,
    message_id: str,
    user_id: str,
    organization_id: str,
) -> Optional[Dict[str, Any]]:
    """Process a single Outlook email through the AP triage pipeline.

    1. Fetch full message with attachments
    2. Download attachment bytes
    3. Run through the same triage service Gmail uses
    """
    msg = await client.get_message(message_id)

    if not msg.has_attachments or not msg.attachments:
        return None

    # Download invoice attachments
    attachment_data = []
    for att in msg.attachments:
        att_id = att.get("id")
        if not att_id:
            continue
        content_type = att.get("contentType", "")
        name = att.get("name", "")
        if not any(
            t in content_type.lower()
            for t in ("pdf", "image", "png", "jpeg", "jpg", "tiff")
        ) and not name.lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".tiff")):
            continue

        raw_bytes = await client.get_attachment(message_id, att_id)
        if raw_bytes:
            attachment_data.append({
                "filename": name,
                "mimeType": content_type,
                "data": base64.b64encode(raw_bytes).decode("utf-8"),
                "size": len(raw_bytes),
            })

    if not attachment_data:
        return None

    # Build triage payload — same structure as Gmail extension /triage endpoint
    payload = {
        "email_id": message_id,
        "thread_id": msg.conversation_id or message_id,
        "subject": msg.subject,
        "sender": msg.sender,
        "snippet": msg.snippet,
        "source": "outlook",
        "organization_id": organization_id,
        "user_id": user_id,
    }

    combined_text = "\n".join(filter(None, [
        f"Subject: {msg.subject}",
        f"From: {msg.sender}",
        msg.body_text or msg.snippet,
    ]))

    # Run through the real triage pipeline
    from clearledgr.services.gmail_triage_service import run_inline_gmail_triage

    result = await run_inline_gmail_triage(
        payload=payload,
        org_id=organization_id,
        combined_text=combined_text,
        attachments=attachment_data,
    )

    return result
