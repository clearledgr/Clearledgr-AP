"""Slack card builders for the Phase 1.4 override-window UX.

These are pure presentation helpers — no DB or HTTP I/O — except for the
``post_undo_card_for_window`` and ``update_card_*`` helpers which take
the slack client as a dependency. Card builders are kept here so the
state observer, the action handler, the API endpoint, and the reaper
can all share a single source of truth for the Block Kit shape.

Card states:
  - **Pending**: posted, undo button live, countdown text
  - **Reversed**: post was undone, button replaced with reversal info
  - **Finalized**: window expired naturally, button removed
  - **Failed**: reversal attempt errored — escalation hint shown
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_ERP_DISPLAY_NAMES: Dict[str, str] = {
    "quickbooks": "QuickBooks Online",
    "xero": "Xero",
    "netsuite": "NetSuite",
    "sap": "SAP B1",
}


# ---------------------------------------------------------------------------
# Pure card builders
# ---------------------------------------------------------------------------


def _format_amount(amount: Any, currency: Any) -> str:
    try:
        amt = float(amount or 0)
    except (TypeError, ValueError):
        amt = 0.0
    cur = str(currency or "USD").upper()
    return f"{cur} {amt:,.2f}"


def _format_window_remaining(window: Dict[str, Any]) -> str:
    """Render the override window's expiry as human-readable text."""
    expires_iso = window.get("expires_at") or ""
    try:
        expires_dt = datetime.fromisoformat(str(expires_iso).replace("Z", "+00:00"))
    except ValueError:
        return "Undo window active."
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
    delta = (expires_dt - datetime.now(timezone.utc)).total_seconds()
    if delta <= 0:
        return "Undo window expired."
    minutes = int(delta // 60)
    if minutes <= 0:
        return "Undo window: less than 1 minute remaining."
    if minutes == 1:
        return "Undo window: 1 minute remaining."
    return f"Undo window: {minutes} minutes remaining."


def build_undo_post_card(
    *,
    ap_item: Dict[str, Any],
    window: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Block Kit blocks for the initial 'Posted — undo available' card.

    Includes a danger-styled Undo button with an action_id of
    ``undo_post_{ap_item_id}`` and a confirm dialog so misclicks
    don't fire a reversal.
    """
    ap_item_id = str(ap_item.get("id") or "")
    window_id = str(window.get("id") or "")
    vendor_name = str(ap_item.get("vendor_name") or "Unknown vendor")
    invoice_number = str(ap_item.get("invoice_number") or "—")
    amount_text = _format_amount(ap_item.get("amount"), ap_item.get("currency"))
    erp_type_raw = str(window.get("erp_type") or "").lower()
    erp_display = _ERP_DISPLAY_NAMES.get(erp_type_raw, erp_type_raw.upper() or "ERP")
    erp_reference = str(window.get("erp_reference") or "")
    expires_text = _format_window_remaining(window)

    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Posted to {erp_display}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Vendor*\n{vendor_name}"},
                {"type": "mrkdwn", "text": f"*Amount*\n{amount_text}"},
                {"type": "mrkdwn", "text": f"*Invoice #*\n{invoice_number}"},
                {"type": "mrkdwn", "text": f"*ERP reference*\n`{erp_reference}`"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f":hourglass_flowing_sand: {expires_text}"},
                {
                    "type": "mrkdwn",
                    "text": (
                        "If this post was a mistake, click *Undo post* below — "
                        "the bill will be reversed at the ERP level. "
                        "DESIGN_THESIS.md §8."
                    ),
                },
            ],
        },
        {
            "type": "actions",
            "block_id": f"undo_post_actions_{ap_item_id}",
            "elements": [
                {
                    "type": "button",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Undo post", "emoji": True},
                    # action_id encodes the override_window id so the handler
                    # can look up the window directly without an extra db
                    # round-trip on the AP item.
                    "action_id": f"undo_post_{window_id}",
                    "value": window_id,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Reverse this post?"},
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"This will reverse the bill in *{erp_display}* "
                                f"({erp_reference}) and route the invoice back "
                                "to human review. This cannot be re-undone."
                            ),
                        },
                        "confirm": {"type": "plain_text", "text": "Reverse it"},
                        "deny": {"type": "plain_text", "text": "Keep posted"},
                    },
                }
            ],
        },
    ]
    return blocks


def build_card_reversed(
    *,
    ap_item: Dict[str, Any],
    window: Dict[str, Any],
    actor_id: str,
    reversal_ref: Optional[str] = None,
    reversal_method: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Card after a successful reversal — undo button removed."""
    vendor_name = str(ap_item.get("vendor_name") or "Unknown vendor")
    amount_text = _format_amount(ap_item.get("amount"), ap_item.get("currency"))
    invoice_number = str(ap_item.get("invoice_number") or "—")
    erp_type_raw = str(window.get("erp_type") or "").lower()
    erp_display = _ERP_DISPLAY_NAMES.get(erp_type_raw, erp_type_raw.upper() or "ERP")
    method_text = (reversal_method or "reversed").replace("_", " ")
    reversal_ref_text = f" (reversal: `{reversal_ref}`)" if reversal_ref else ""

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Reversed in {erp_display}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Vendor*\n{vendor_name}"},
                {"type": "mrkdwn", "text": f"*Amount*\n{amount_text}"},
                {"type": "mrkdwn", "text": f"*Invoice #*\n{invoice_number}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Reversed by*\n{actor_id}",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f":white_check_mark: Bill reversed via {method_text}"
                        f"{reversal_ref_text}. The invoice has been returned "
                        "to human review."
                    ),
                },
            ],
        },
    ]


def build_card_finalized(
    *,
    ap_item: Dict[str, Any],
    window: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Card after the override window expires naturally."""
    vendor_name = str(ap_item.get("vendor_name") or "Unknown vendor")
    amount_text = _format_amount(ap_item.get("amount"), ap_item.get("currency"))
    invoice_number = str(ap_item.get("invoice_number") or "—")
    erp_type_raw = str(window.get("erp_type") or "").lower()
    erp_display = _ERP_DISPLAY_NAMES.get(erp_type_raw, erp_type_raw.upper() or "ERP")
    erp_reference = str(window.get("erp_reference") or "")

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Posted to {erp_display}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Vendor*\n{vendor_name}"},
                {"type": "mrkdwn", "text": f"*Amount*\n{amount_text}"},
                {"type": "mrkdwn", "text": f"*Invoice #*\n{invoice_number}"},
                {"type": "mrkdwn", "text": f"*ERP reference*\n`{erp_reference}`"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        ":lock: Override window has closed. This post is final. "
                        "Any further changes require a manual credit note in the ERP."
                    ),
                },
            ],
        },
    ]


def build_card_reversal_failed(
    *,
    ap_item: Dict[str, Any],
    window: Dict[str, Any],
    actor_id: str,
    failure_reason: str,
    failure_message: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Card after a reversal attempt fails at the ERP level — manual intervention."""
    vendor_name = str(ap_item.get("vendor_name") or "Unknown vendor")
    amount_text = _format_amount(ap_item.get("amount"), ap_item.get("currency"))
    invoice_number = str(ap_item.get("invoice_number") or "—")
    erp_type_raw = str(window.get("erp_type") or "").lower()
    erp_display = _ERP_DISPLAY_NAMES.get(erp_type_raw, erp_type_raw.upper() or "ERP")
    erp_reference = str(window.get("erp_reference") or "")

    detail_text = failure_message or failure_reason.replace("_", " ")

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":warning: Reversal failed in {erp_display}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Vendor*\n{vendor_name}"},
                {"type": "mrkdwn", "text": f"*Amount*\n{amount_text}"},
                {"type": "mrkdwn", "text": f"*Invoice #*\n{invoice_number}"},
                {"type": "mrkdwn", "text": f"*ERP reference*\n`{erp_reference}`"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Reversal attempted by*: {actor_id}\n"
                    f"*Failure reason*: `{failure_reason}`\n"
                    f"{detail_text}"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        ":exclamation: Manual intervention required. "
                        "The bill is still posted in the ERP — log in to "
                        f"{erp_display} to verify state and apply a credit "
                        "note if necessary."
                    ),
                },
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Network-bound helpers — used by the observer, action handler, and reaper.
# These are thin wrappers around the SlackAPIClient that take a DB handle so
# they can resolve the org's Slack channel from settings_json.
# ---------------------------------------------------------------------------


def _resolve_slack_channel_for_org(db: Any, organization_id: str) -> Optional[str]:
    """Look up the Slack channel an org receives undo cards in.

    Reads from ``settings_json["slack_channels"]["invoices"]`` (the same
    field the approval workflow uses), falling back to env vars and
    finally to None.
    """
    try:
        org = db.get_organization(organization_id)
    except Exception as exc:
        logger.warning(
            "[slack_cards] Could not load org %s for channel resolution: %s",
            organization_id, exc,
        )
        org = None

    if org:
        settings = org.get("settings") or org.get("settings_json") or {}
        if isinstance(settings, str):
            try:
                import json as _json
                settings = _json.loads(settings)
            except Exception:
                settings = {}
        if isinstance(settings, dict):
            slack_channels = settings.get("slack_channels") or {}
            if isinstance(slack_channels, dict):
                channel = slack_channels.get("invoices")
                if channel:
                    return str(channel)

    env_channel = (
        os.getenv("SLACK_APPROVAL_CHANNEL")
        or os.getenv("SLACK_DEFAULT_CHANNEL")
        or ""
    ).strip()
    return env_channel or None


async def _get_slack_client_for_org(organization_id: str) -> Any:
    """Best-effort SlackAPIClient instantiation. Returns None if unavailable."""
    try:
        from clearledgr.services.slack_api import get_slack_client
        return get_slack_client(organization_id)
    except Exception as exc:
        logger.warning(
            "[slack_cards] Could not get slack client for %s: %s",
            organization_id, exc,
        )
        return None


async def post_undo_card_for_window(
    *,
    organization_id: str,
    ap_item: Dict[str, Any],
    window: Dict[str, Any],
    db: Any,
) -> Optional[Dict[str, Any]]:
    """Post the initial undo card to Slack and return the message refs.

    Returns ``{"channel": ..., "message_ts": ...}`` on success, or None
    on any failure (no Slack client, no channel configured, post failed).
    Failure is non-fatal — the override window still exists; the user
    can trigger reversal via the API or the ops surface.
    """
    channel = _resolve_slack_channel_for_org(db, organization_id)
    if not channel:
        logger.info(
            "[slack_cards] No Slack channel configured for %s — skipping undo card",
            organization_id,
        )
        return None

    client = await _get_slack_client_for_org(organization_id)
    if client is None:
        return None

    blocks = build_undo_post_card(ap_item=ap_item, window=window)
    fallback_text = (
        f"Posted {_format_amount(ap_item.get('amount'), ap_item.get('currency'))} "
        f"to ERP — undo available for "
        f"{_format_window_remaining(window).lower()}"
    )

    try:
        message = await client.send_message(
            channel=channel,
            text=fallback_text,
            blocks=blocks,
        )
    except Exception as exc:
        logger.warning(
            "[slack_cards] Failed to post undo card for ap_item=%s: %s",
            ap_item.get("id"), exc,
        )
        return None

    return {
        "channel": getattr(message, "channel", channel),
        "message_ts": getattr(message, "ts", None),
    }


async def update_card_to_reversed(
    *,
    organization_id: str,
    ap_item: Dict[str, Any],
    window: Dict[str, Any],
    actor_id: str,
    reversal_ref: Optional[str] = None,
    reversal_method: Optional[str] = None,
) -> bool:
    """Update the existing card to show the reversed state. Returns True on success."""
    channel = window.get("slack_channel")
    ts = window.get("slack_message_ts")
    if not channel or not ts:
        return False

    client = await _get_slack_client_for_org(organization_id)
    if client is None:
        return False

    blocks = build_card_reversed(
        ap_item=ap_item,
        window=window,
        actor_id=actor_id,
        reversal_ref=reversal_ref,
        reversal_method=reversal_method,
    )
    try:
        await client.update_message(
            channel=channel,
            ts=ts,
            text=f"Reversed by {actor_id}",
            blocks=blocks,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[slack_cards] Failed to update card to reversed for window=%s: %s",
            window.get("id"), exc,
        )
        return False


async def update_card_to_finalized(
    *,
    organization_id: str,
    ap_item: Dict[str, Any],
    window: Dict[str, Any],
) -> bool:
    """Update the existing card to show the finalized (window-expired) state."""
    channel = window.get("slack_channel")
    ts = window.get("slack_message_ts")
    if not channel or not ts:
        return False

    client = await _get_slack_client_for_org(organization_id)
    if client is None:
        return False

    blocks = build_card_finalized(ap_item=ap_item, window=window)
    try:
        await client.update_message(
            channel=channel,
            ts=ts,
            text="Override window closed — post is final",
            blocks=blocks,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[slack_cards] Failed to update card to finalized for window=%s: %s",
            window.get("id"), exc,
        )
        return False


async def update_card_to_reversal_failed(
    *,
    organization_id: str,
    ap_item: Dict[str, Any],
    window: Dict[str, Any],
    actor_id: str,
    failure_reason: str,
    failure_message: Optional[str] = None,
) -> bool:
    """Update the existing card to show a failed reversal attempt."""
    channel = window.get("slack_channel")
    ts = window.get("slack_message_ts")
    if not channel or not ts:
        return False

    client = await _get_slack_client_for_org(organization_id)
    if client is None:
        return False

    blocks = build_card_reversal_failed(
        ap_item=ap_item,
        window=window,
        actor_id=actor_id,
        failure_reason=failure_reason,
        failure_message=failure_message,
    )
    try:
        await client.update_message(
            channel=channel,
            ts=ts,
            text=f"Reversal failed: {failure_reason}",
            blocks=blocks,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[slack_cards] Failed to update card to reversal_failed for window=%s: %s",
            window.get("id"), exc,
        )
        return False
