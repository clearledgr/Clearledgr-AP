"""Focused tests for Slack escalation activity behavior."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from clearledgr.workflows.gmail_activities import send_slack_notification_activity


def _run(coro):
    return asyncio.run(coro)


class _FakeDB:
    def __init__(self, *, metadata=None, slack_thread=None):
        self.item = {
            "id": "ap-1",
            "thread_id": "gmail-thread-1",
            "metadata": dict(metadata or {}),
            "updated_at": "2026-03-26T10:00:00+00:00",
            "created_at": "2026-03-26T09:00:00+00:00",
        }
        self.slack_thread = slack_thread
        self.metadata_patches = []
        self.saved_thread = None
        self.updated_thread = None

    def get_ap_item(self, ap_item_id):
        return self.item if ap_item_id == "ap-1" else None

    def get_invoice_status(self, gmail_id):
        return self.item if gmail_id == "gmail-thread-1" else None

    def get_slack_thread(self, gmail_id):
        if gmail_id != "gmail-thread-1":
            return None
        return self.slack_thread

    def save_slack_thread(self, gmail_id, channel_id="", thread_ts="", **kwargs):
        self.saved_thread = {
            "gmail_id": gmail_id,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "thread_id": kwargs.get("thread_id"),
        }
        return thread_ts

    def update_slack_thread_status(self, gmail_id, **kwargs):
        self.updated_thread = {"gmail_id": gmail_id, **kwargs}
        return True

    def update_ap_item_metadata_merge(self, ap_item_id, patch):
        self.metadata_patches.append((ap_item_id, dict(patch or {})))
        self.item["metadata"].update(dict(patch or {}))
        return True


def test_send_slack_notification_activity_threads_existing_escalations_and_normalizes_confidence():
    db = _FakeDB(
        metadata={
            "approval_requested_at": "2026-03-26T10:00:00+00:00",
            "approval_last_escalated_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        },
        slack_thread={"channel_id": "C-APPROVALS", "thread_ts": "170.123", "thread_id": "170.123"},
    )
    fake_client = MagicMock()
    fake_client.send_message = AsyncMock(
        return_value=SimpleNamespace(channel="C-APPROVALS", ts="171.456", thread_ts="170.123")
    )

    with patch("clearledgr.workflows.gmail_activities.get_db", return_value=db):
        with patch(
            "clearledgr.workflows.gmail_activities.resolve_slack_runtime",
            return_value={"bot_token": "xoxb-live", "approval_channel": "cl-finance-ap"},
        ):
            with patch("clearledgr.workflows.gmail_activities.SlackAPIClient", return_value=fake_client):
                with patch(
                    "clearledgr.workflows.gmail_activities.send_with_retry",
                    new=AsyncMock(return_value=False),
                ) as fallback_send:
                    result = _run(
                        send_slack_notification_activity(
                            {
                                "organization_id": "default",
                                "channel": "cl-finance-ap",
                                "email_id": "gmail-thread-1",
                                "ap_item_id": "ap-1",
                                "extraction": {
                                    "vendor": "Google Cloud EMEA Limited",
                                    "amount": 38.46,
                                    "currency": "EUR",
                                    "confidence": 1.0,
                                },
                                "confidence_result": {"confidence_pct": 1.0, "mismatches": []},
                            }
                        )
                    )

    assert result["status"] == "sent"
    assert result["threaded"] is True
    assert result["thread_ts"] == "170.123"
    fake_client.send_message.assert_awaited_once()
    call = fake_client.send_message.await_args
    assert call.kwargs["channel"] == "C-APPROVALS"
    assert call.kwargs["thread_ts"] == "170.123"
    blocks = call.kwargs["blocks"]
    assert "*Confidence:* 100.0%" in blocks[1]["text"]["text"]
    assert "*Why this was escalated:*" in blocks[2]["text"]["text"]
    assert "Approval has been waiting for" in blocks[2]["text"]["text"]
    actions = blocks[3]["elements"]
    assert [element["text"]["text"] for element in actions] == ["Approve", "Reject", "Request info"]
    assert actions[0]["action_id"] == "approve_invoice_gmail-thread-1"
    assert actions[1]["action_id"] == "reject_invoice_gmail-thread-1"
    assert actions[2]["action_id"] == "request_info_gmail-thread-1"
    fallback_send.assert_not_awaited()


def test_send_slack_notification_activity_dedupes_recent_escalations_in_existing_thread():
    db = _FakeDB(
        metadata={
            "approval_last_escalated_at": datetime.now(timezone.utc).isoformat(),
        },
        slack_thread={"channel_id": "C-APPROVALS", "thread_ts": "170.123", "thread_id": "170.123"},
    )
    fake_client = MagicMock()
    fake_client.send_message = AsyncMock()

    with patch("clearledgr.workflows.gmail_activities.get_db", return_value=db):
        with patch(
            "clearledgr.workflows.gmail_activities.resolve_slack_runtime",
            return_value={"bot_token": "xoxb-live", "approval_channel": "cl-finance-ap"},
        ):
            with patch("clearledgr.workflows.gmail_activities.SlackAPIClient", return_value=fake_client):
                result = _run(
                    send_slack_notification_activity(
                        {
                            "organization_id": "default",
                            "channel": "cl-finance-ap",
                            "email_id": "gmail-thread-1",
                            "ap_item_id": "ap-1",
                            "extraction": {
                                "vendor": "Google Cloud EMEA Limited",
                                "amount": 38.46,
                                "currency": "EUR",
                            },
                            "confidence_result": {"confidence_pct": 88.0, "mismatches": []},
                        }
                    )
                )

    assert result["status"] == "deduped"
    assert result["deduped"] is True
    assert result["threaded"] is True
    assert result["thread_ts"] == "170.123"
    fake_client.send_message.assert_not_awaited()
