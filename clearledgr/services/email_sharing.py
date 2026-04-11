"""
Shared Inbox Model — DESIGN_THESIS.md §5.2

"When the agent creates an invoice Box from an incoming vendor email,
that email is automatically shared with all members of the AP team."

Implementation:
1. All AP items are already scoped to organization_id (team-level) —
   any team member with the extension sees all items in the pipeline.

2. When a finance email arrives in an INDIVIDUAL team member's inbox
   (not the shared ap@ address), the agent:
   a) Creates the AP item as usual (org-scoped, visible to all)
   b) Applies Clearledgr labels to the email (visible in Gmail)
   c) Posts a timeline entry: "Invoice from [vendor] received in
      [person]'s inbox — added to AP Invoices pipeline."
   d) Sends a brief Slack notification to the AP channel so the team
      knows about the new item without checking the extension.

3. For the shared ap@ inbox, emails are naturally visible to all
   delegates. The agent applies labels and creates the AP item.

This module handles step (2) — the individual inbox sharing flow.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def share_individual_inbox_email(
    *,
    ap_item_id: str,
    gmail_id: str,
    sender: str,
    vendor_name: str,
    amount: float,
    currency: str,
    recipient_email: str,
    shared_inbox_email: Optional[str] = None,
    organization_id: str,
    db: Any = None,
) -> Dict[str, Any]:
    """§5.2: When a finance email arrives in an individual inbox, share it with the team.

    The AP item is already created and org-scoped. This function:
    1. Posts a timeline entry noting whose inbox received it
    2. Optionally notifies the AP Slack channel
    3. Returns sharing metadata for audit
    """
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    is_shared_inbox = (
        shared_inbox_email
        and recipient_email
        and recipient_email.lower().strip() == shared_inbox_email.lower().strip()
    )

    if is_shared_inbox:
        # Email arrived at shared ap@ — no special sharing needed,
        # all delegates see it naturally
        return {"shared": False, "reason": "shared_inbox_email"}

    # Individual inbox — post timeline entry per §5.2
    timeline_entry = {
        "event_type": "email_shared_to_pipeline",
        "summary": (
            f"Invoice from {vendor_name} ({currency} {amount:,.2f}) "
            f"received in {recipient_email}'s inbox — added to AP Invoices pipeline."
        ),
        "reason": (
            "Finance email detected in individual team member's inbox. "
            "Shared with the full AP team per §5.2 Shared Inbox Model."
        ),
        "next_action": "All team members can see this invoice in the pipeline.",
        "actor": "agent",
        "timestamp": now,
    }

    try:
        if hasattr(db, "append_ap_item_timeline_entry"):
            db.append_ap_item_timeline_entry(ap_item_id, timeline_entry)
        elif hasattr(db, "append_ap_audit_event"):
            db.append_ap_audit_event({
                "ap_item_id": ap_item_id,
                "event_type": "email_shared_to_pipeline",
                "actor_type": "agent",
                "actor_id": "email_sharing",
                "organization_id": organization_id,
                "source": "email_sharing",
                "payload_json": {
                    "recipient_email": recipient_email,
                    "vendor_name": vendor_name,
                    "amount": amount,
                    "currency": currency,
                },
            })
    except Exception as exc:
        logger.warning("[email_sharing] timeline entry failed: %s", exc)

    # Notify AP Slack channel
    try:
        from clearledgr.services.slack_notifications import _post_slack_blocks

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Invoice added to pipeline*\n"
                        f"{vendor_name} — {currency} {amount:,.2f}\n"
                        f"Received in {recipient_email}'s inbox. "
                        f"Now visible to all team members in the AP pipeline."
                    ),
                },
            },
        ]
        await _post_slack_blocks(
            blocks=blocks,
            text=f"Invoice from {vendor_name} added to pipeline from {recipient_email}'s inbox",
            organization_id=organization_id,
        )
    except Exception as exc:
        logger.debug("[email_sharing] Slack notification skipped: %s", exc)

    return {
        "shared": True,
        "recipient_email": recipient_email,
        "ap_item_id": ap_item_id,
        "timeline_entry_posted": True,
    }


def get_shared_inbox_email(organization_id: str, db: Any = None) -> Optional[str]:
    """Get the configured shared AP inbox email for an org."""
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    try:
        org = db.get_organization(organization_id)
        if not org:
            return None
        settings = org.get("settings_json")
        if isinstance(settings, str):
            import json
            settings = json.loads(settings)
        if isinstance(settings, dict):
            return settings.get("shared_inbox_email") or settings.get("ap_inbox_email")
    except Exception:
        pass
    return None
