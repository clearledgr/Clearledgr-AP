"""Notification service for Slack/email summaries."""
from __future__ import annotations

import logging
import os
from typing import Dict, List

import httpx


class NotificationService:
    """
    Sends exception-focused alerts and summaries.
    This now sends to Slack when SLACK_BOT_TOKEN and SLACK_DEFAULT_CHANNEL are configured.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("clearledgr.notifications")
        self.slack_token = os.getenv("SLACK_BOT_TOKEN", "")
        self.slack_channel = os.getenv("SLACK_DEFAULT_CHANNEL", "")

    def send_daily_summary(self, entity_id: str, results: Dict) -> None:
        summary = {
            "entity_id": entity_id,
            "processed": results.get("processed"),
            "matched": results.get("matched"),
            "match_rate": results.get("match_rate"),
            "exceptions": len(results.get("exceptions", [])),
            "draft_journal_entries": len(results.get("draft_journal_entries", [])),
            "exceptions_url": results.get("exceptions_url"),
            "drafts_url": results.get("drafts_url"),
        }
        self.logger.info("Daily summary: %s", summary)
        self._send_slack_blocks(self.build_slack_daily_summary(summary))

    def send_exception_alert(self, entity_id: str, exceptions: List[Dict]) -> None:
        payload = {"entity_id": entity_id, "exceptions": exceptions}
        self.logger.info("Exception alert: %s", payload)
        self._send_slack_blocks(self.build_slack_exception_alert(exceptions))

    def send_approval_request(self, entity_id: str, draft_count: int) -> None:
        payload = {"entity_id": entity_id, "drafts": draft_count}
        self.logger.info("Approval request: %s", payload)
        if draft_count:
            self._send_slack_blocks(self.build_draft_approval_prompt(draft_count))

    def send_drafts(self, drafts: List[Dict]) -> None:
        """Send draft journal entry cards with Approve/Post actions."""
        if not drafts:
            return
        self._send_slack_blocks(self.build_draft_cards(drafts))

    def send_exception_cards(self, exceptions: List[Dict]) -> None:
        if not exceptions:
            return
        self._send_slack_blocks(self.build_slack_exception_alert(exceptions))

    # Example Slack payload builders (to be wired to Slack SDK/webhooks)
    def build_slack_daily_summary(self, results: Dict) -> Dict:
        match_rate = results.get("match_rate") or 0
        return {
            "text": f"Bank Reconciliation Complete for {results.get('entity_id', '')}",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "Bank Reconciliation Complete"}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*Processed*\n{results.get('processed', 0)}"},
                    {"type": "mrkdwn", "text": f"*Matched*\n{results.get('matched', 0)} ({match_rate:.2%})"},
                    {"type": "mrkdwn", "text": f"*Exceptions*\n{len(results.get('exceptions', []))}"},
                    {"type": "mrkdwn", "text": f"*Draft JEs*\n{len(results.get('draft_journal_entries', []))}"},
                ]},
                {"type": "actions", "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "View Exceptions"}, "url": results.get("exceptions_url", "#")},
                    {"type": "button", "text": {"type": "plain_text", "text": "Approve Drafts"}, "url": results.get("drafts_url", "#")}
                ]}
            ]
        }

    def build_slack_exception_alert(self, exceptions: List[Dict]) -> Dict:
        items = "\n".join([f"• {ex.get('description','') or ex.get('reason','')}" for ex in exceptions[:5]])
        return {
            "text": "Reconciliation exceptions need review",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": "*Exceptions require review*"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": items}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Review Exceptions"},
                            "url": os.getenv("CLEARLEDGR_EXCEPTIONS_URL", "#"),
                        }
                    ],
                },
            ]
        }

    def build_draft_approval_prompt(self, draft_count: int) -> Dict:
        return {
            "text": "Draft journal entries ready for review",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*{draft_count}* draft journal entries ready for approval."}},
                {"type": "actions", "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "Approve Drafts"}, "url": os.getenv("CLEARLEDGR_DRAFTS_URL", "#")},
                    {"type": "button", "text": {"type": "plain_text", "text": "Open Sheets"}, "url": os.getenv("CLEARLEDGR_EXCEPTIONS_URL", "#")}
                ]}
            ]
        }

    def build_draft_cards(self, drafts: List[Dict]) -> Dict:
        blocks = [{"type": "header", "text": {"type": "plain_text", "text": "Draft Journal Entries"}}]
        for draft in drafts[:5]:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{draft.get('description','Draft JE')}* • {draft.get('date','')}\nAmount: {draft.get('total_debits','')}\nConfidence: {draft.get('confidence','')}"
                },
                "accessory": {
                    "type": "overflow",
                    "options": [
                        {"text": {"type": "plain_text", "text": "Approve"}, "value": f"approve::{draft.get('entry_id')}"},
                        {"text": {"type": "plain_text", "text": "Post"}, "value": f"post::{draft.get('entry_id')}"},
                        {"text": {"type": "plain_text", "text": "Reject"}, "value": f"reject::{draft.get('entry_id')}"}
                    ],
                    "action_id": "draft_actions"
                }
            })
            blocks.append({"type": "divider"})
        return {"text": "Draft journal entries ready for review", "blocks": blocks}

    def _send_slack_blocks(self, payload: Dict) -> None:
        """Send slack message if configured."""
        if not payload:
            return
        if not (self.slack_token and self.slack_channel):
            return

        try:
            httpx.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {self.slack_token}"},
                json={
                    "channel": self.slack_channel,
                    "text": payload.get("text") or "Clearledgr update",
                    "blocks": payload.get("blocks", []),
                },
                timeout=8.0,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Slack notification failed: %s", exc)
