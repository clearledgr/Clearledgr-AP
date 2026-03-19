"""Gmail Label Management for AP Status Pipeline.

Fyxer-inspired: use native Gmail labels so users see invoice status
in their inbox without opening any extension or workspace shell.

Label hierarchy:
  Clearledgr/Invoice         — detected as an invoice
  Clearledgr/Needs Approval  — waiting for human review
  Clearledgr/Approved        — approved, ready to post
  Clearledgr/Posted          — successfully posted to ERP
  Clearledgr/Rejected        — rejected by approver
  Clearledgr/Payment Request — non-invoice payment request
  Clearledgr/Processed       — catch-all "we saw this email"
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

CLEARLEDGR_LABELS = {
    "invoice":        "Clearledgr/Invoice",
    "needs_approval": "Clearledgr/Needs Approval",
    "approved":       "Clearledgr/Approved",
    "posted":         "Clearledgr/Posted",
    "rejected":       "Clearledgr/Rejected",
    "payment":        "Clearledgr/Payment Request",
    "processed":      "Clearledgr/Processed",
}

# AP state → label key mapping
AP_STATE_TO_LABEL = {
    "needs_approval":   "needs_approval",
    "pending_approval": "needs_approval",
    "approved":         "approved",
    "ready_to_post":    "approved",
    "posted_to_erp":    "posted",
    "closed":           "posted",
    "rejected":         "rejected",
}

# Cache label IDs per-user to avoid repeated list_labels calls
_label_id_cache: Dict[str, Dict[str, str]] = {}


async def ensure_label(client, label_key: str, user_email: str = "") -> Optional[str]:
    """Get or create a Clearledgr label, return its Gmail label ID."""
    label_name = CLEARLEDGR_LABELS.get(label_key)
    if not label_name:
        return None

    cache_key = user_email or "default"
    if cache_key in _label_id_cache and label_key in _label_id_cache[cache_key]:
        return _label_id_cache[cache_key][label_key]

    try:
        labels = await client.list_labels()
        label = next((l for l in labels if l.get("name") == label_name), None)
        if not label:
            label = await client.create_label(label_name)
        label_id = label.get("id") if label else None
        if label_id:
            _label_id_cache.setdefault(cache_key, {})[label_key] = label_id
        return label_id
    except Exception as exc:
        logger.warning("Could not ensure label %s: %s", label_name, exc)
        return None


async def apply_label(client, message_id: str, label_key: str, user_email: str = ""):
    """Apply a Clearledgr status label to a Gmail message."""
    label_id = await ensure_label(client, label_key, user_email)
    if label_id:
        try:
            await client.add_label(message_id, [label_id])
        except Exception as exc:
            logger.warning("Could not apply label %s to %s: %s", label_key, message_id, exc)


async def remove_label(client, message_id: str, label_key: str, user_email: str = ""):
    """Remove a Clearledgr status label from a Gmail message."""
    label_id = await ensure_label(client, label_key, user_email)
    if label_id:
        try:
            await client.remove_label(message_id, [label_id])
        except Exception:
            pass  # Label may not be on the message


async def update_ap_label(client, message_id: str, new_state: str, user_email: str = ""):
    """Update Gmail labels to reflect a new AP state.

    Removes old status labels (needs_approval, approved, posted, rejected)
    and applies the label matching the new state.
    """
    new_label_key = AP_STATE_TO_LABEL.get(new_state)
    if not new_label_key:
        return

    # Remove all status labels except 'invoice' and 'processed' (those are permanent)
    status_keys = {"needs_approval", "approved", "posted", "rejected"}
    for key in status_keys:
        if key != new_label_key:
            await remove_label(client, message_id, key, user_email)

    await apply_label(client, message_id, new_label_key, user_email)
    logger.info("Gmail label updated: %s → %s for message %s", new_state, new_label_key, message_id)
