"""Slack approval-card update target.

When the Box transitions while a Slack approval card is live, this
target updates the existing Slack message with the new state badge
+ an audit footer (who advanced it, when), instead of leaving the
old card stale or posting a new one.

Lookup: read ``slack_thread`` from the AP item via
``get_slack_thread`` (the existing Phase A-shipped table for
ap_item ↔ Slack message mapping). Channel + ts → Slack
``chat.update`` API.

Per-target config:
    {
        "enabled": true,
        "show_actor_attribution": true | false  # default true
    }

Skipped when:
- The AP item has no slack_thread row (no card was ever posted)
- New state matches old state (no-op transition)
- Slack bot token isn't configured
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from clearledgr.services.annotation_targets.base import (
    AnnotationContext,
    AnnotationResult,
    register_target,
)

logger = logging.getLogger(__name__)


_STATE_BADGE_EMOJI = {
    "received": ":inbox_tray:",
    "validated": ":mag:",
    "needs_info": ":question:",
    "needs_approval": ":hourglass_flowing_sand:",
    "approved": ":white_check_mark:",
    "rejected": ":x:",
    "ready_to_post": ":outbox_tray:",
    "posted_to_erp": ":bank:",
    "failed_post": ":warning:",
    "snoozed": ":zzz:",
    "reversed": ":arrows_counterclockwise:",
    "closed": ":white_check_mark:",
}


class SlackCardUpdateTarget:
    target_type = "slack_card_update"

    async def apply(self, context: AnnotationContext) -> AnnotationResult:
        if context.old_state == context.new_state:
            return AnnotationResult(
                status="skipped", skip_reason="no_state_change",
            )

        from clearledgr.core.database import get_db
        db = get_db()
        if not hasattr(db, "get_slack_thread"):
            return AnnotationResult(
                status="skipped", skip_reason="db_lacks_slack_thread",
            )
        try:
            thread = db.get_slack_thread(context.box_id)
        except Exception:
            thread = None
        if not thread:
            return AnnotationResult(
                status="skipped", skip_reason="no_slack_thread_for_box",
            )

        channel_id = str(thread.get("channel_id") or "").strip()
        message_ts = str(thread.get("thread_ts") or thread.get("thread_id") or "").strip()
        if not channel_id or not message_ts:
            return AnnotationResult(
                status="skipped", skip_reason="incomplete_slack_thread",
            )

        try:
            from clearledgr.services.slack_api import get_slack_client
            client = get_slack_client()
        except Exception as exc:  # noqa: BLE001
            return AnnotationResult(
                status="skipped",
                skip_reason="slack_client_unavailable",
                metadata={"error": str(exc)[:200]},
            )

        show_actor = bool(context.target_config.get("show_actor_attribution", True))
        text = self._build_status_text(context, show_actor)
        blocks = self._build_status_blocks(context, show_actor)

        try:
            response = await client._request(
                "POST", "chat.update",
                data={
                    "channel": channel_id,
                    "ts": message_ts,
                    "text": text,
                    "blocks": blocks,
                },
            )
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            # Slack's "message_not_found" / "channel_not_found" /
            # "edit_window_closed" are permanent — surface as failed
            # but don't raise (no retry value).
            permanent_codes = {
                "message_not_found", "channel_not_found",
                "edit_window_closed", "cant_update_message",
            }
            if any(code in error_msg for code in permanent_codes):
                return AnnotationResult(
                    status="failed",
                    response_body_preview=error_msg[:500],
                    metadata={"reason": "slack_permanent_error"},
                )
            raise  # transient: outbox retries

        return AnnotationResult(
            status="succeeded",
            applied_value=context.new_state,
            external_id=f"{channel_id}/{message_ts}",
            metadata={"actor_attribution_shown": show_actor},
        )

    @staticmethod
    def _build_status_text(context: AnnotationContext, show_actor: bool) -> str:
        emoji = _STATE_BADGE_EMOJI.get(context.new_state, ":small_blue_diamond:")
        text = f"{emoji} *{_humanize(context.new_state)}*"
        if show_actor and context.actor_id:
            text += f" — by `{context.actor_id}`"
        return text

    @staticmethod
    def _build_status_blocks(
        context: AnnotationContext, show_actor: bool,
    ) -> List[Dict[str, Any]]:
        emoji = _STATE_BADGE_EMOJI.get(context.new_state, ":small_blue_diamond:")
        header_text = f"{emoji} {_humanize(context.new_state)}"
        blocks: List[Dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header_text},
            }
        ]
        context_elements: List[Dict[str, Any]] = [
            {"type": "mrkdwn", "text": f"_Was: {_humanize(context.old_state)}_"},
        ]
        if show_actor and context.actor_id:
            context_elements.append({
                "type": "mrkdwn",
                "text": f"_Advanced by `{context.actor_id}`_",
            })
        blocks.append({"type": "context", "elements": context_elements})
        return blocks


def _humanize(state: str) -> str:
    return (state or "unknown").replace("_", " ").title()


register_target(SlackCardUpdateTarget())
