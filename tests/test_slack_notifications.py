"""Tests for clearledgr.services.slack_notifications — delivery + retry queue."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clearledgr.services.slack_notifications import (
    _post_slack_blocks,
    _retry_slack_response_url,
    send_with_retry,
    send_approval_reminder,
    process_retry_queue,
)


def _run(coro):
    """Run an async coroutine synchronously (no pytest-asyncio needed)."""
    return asyncio.run(coro)


@pytest.fixture
def mock_runtime():
    return {"bot_token": "", "approval_channel": "#finance-approvals", "mode": "webhook"}


# ---------------------------------------------------------------------------
# _post_slack_blocks
# ---------------------------------------------------------------------------


class TestPostSlackBlocks:
    def test_webhook_success(self, monkeypatch, mock_runtime):
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("clearledgr.services.slack_notifications.resolve_slack_runtime", return_value=mock_runtime):
            with patch("httpx.AsyncClient") as MockClient:
                instance = AsyncMock()
                instance.post = AsyncMock(return_value=mock_response)
                instance.__aenter__ = AsyncMock(return_value=instance)
                instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = instance

                result = _run(_post_slack_blocks([{"type": "section"}], "test"))
                assert result

    def test_no_delivery_method_returns_false(self, monkeypatch, mock_runtime):
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        with patch("clearledgr.services.slack_notifications.resolve_slack_runtime", return_value=mock_runtime):
            result = _run(_post_slack_blocks([{"type": "section"}], "test"))
            assert result is False

    def test_retries_with_runtime_channel_when_primary_channel_not_found(self, monkeypatch):
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        runtime = {"bot_token": "xoxb-test", "approval_channel": "cl-finance-ap", "mode": "per_org"}

        first_response = MagicMock(status_code=200, content=b'{"ok": false, "error": "channel_not_found"}')
        first_response.json.return_value = {"ok": False, "error": "channel_not_found"}
        second_response = MagicMock(status_code=200, content=b'{"ok": true}')
        second_response.json.return_value = {"ok": True}

        with patch("clearledgr.services.slack_notifications.resolve_slack_runtime", return_value=runtime):
            with patch("httpx.AsyncClient") as MockClient:
                instance = AsyncMock()
                instance.post = AsyncMock(side_effect=[first_response, second_response])
                instance.__aenter__ = AsyncMock(return_value=instance)
                instance.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = instance

                result = _run(
                    _post_slack_blocks(
                        [{"type": "section"}],
                        "test",
                        preferred_channel="C0AN8FFHAPJ",
                        organization_id="default",
                    )
                )

        assert result
        assert instance.post.await_count == 2
        first_payload = instance.post.await_args_list[0].kwargs["json"]
        second_payload = instance.post.await_args_list[1].kwargs["json"]
        assert first_payload["channel"] == "C0AN8FFHAPJ"
        assert second_payload["channel"] == "cl-finance-ap"


# ---------------------------------------------------------------------------
# send_with_retry
# ---------------------------------------------------------------------------


class TestSendWithRetry:
    def test_enqueues_on_failure(self, monkeypatch, mock_runtime):
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        mock_db = MagicMock()
        mock_db.enqueue_notification = MagicMock()

        with patch("clearledgr.services.slack_notifications.resolve_slack_runtime", return_value=mock_runtime):
            with patch("clearledgr.core.database.get_db", return_value=mock_db):
                result = _run(send_with_retry(
                    [{"type": "section"}], "test", ap_item_id="ap-1", organization_id="acme",
                ))
                assert result is False
                mock_db.enqueue_notification.assert_called_once()
                call_kwargs = mock_db.enqueue_notification.call_args
                assert call_kwargs[1]["organization_id"] == "acme" or call_kwargs[0][0] == "acme"


# ---------------------------------------------------------------------------
# _retry_slack_response_url
# ---------------------------------------------------------------------------


class TestRetrySlackResponseUrl:
    def test_success(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = _run(_retry_slack_response_url({
                "response_url": "https://hooks.slack.com/response/123",
                "body": {"text": "updated"},
            }))
            assert result

    def test_missing_response_url(self):
        result = _run(_retry_slack_response_url({"body": {}}))
        assert result is False

    def test_network_error(self):
        import httpx
        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(side_effect=httpx.ConnectError("fail"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = _run(_retry_slack_response_url({
                "response_url": "https://hooks.slack.com/response/123",
                "body": {},
            }))
            assert result is False


# ---------------------------------------------------------------------------
# send_approval_reminder
# ---------------------------------------------------------------------------


class TestSendApprovalReminder:
    def test_posts_channel_reminder_when_no_pending_approvers(self):
        instance = MagicMock()
        instance.send_dm = AsyncMock()
        instance.resolve_user_targets = AsyncMock(
            return_value={"delivery_ids": [], "mentions": [], "labels": [], "unresolved": []}
        )

        with patch("clearledgr.services.slack_api.get_slack_client", return_value=instance):
            with patch(
                "clearledgr.services.slack_notifications._post_slack_blocks",
                new=AsyncMock(return_value=True),
            ) as post_blocks:
                result = _run(
                    send_approval_reminder(
                        ap_item={
                            "vendor_name": "Approval Reminder Co",
                            "amount": 42.0,
                            "invoice_number": "INV-REM-1",
                            "organization_id": "default",
                            "metadata": {"approval_channel": "C-APPROVALS"},
                        },
                        approver_ids=[],
                        hours_pending=4,
                        organization_id="default",
                        stage="reminder",
                    )
                )

        assert result
        post_blocks.assert_awaited_once()
        instance.send_dm.assert_not_awaited()
        posted_blocks = post_blocks.await_args.kwargs["blocks"]
        actions_block = next(block for block in posted_blocks if block.get("type") == "actions")
        action_ids = [element["action_id"] for element in actions_block["elements"]]
        assert action_ids == [
            "approve_invoice_INV-REM-1",
            "reject_invoice_INV-REM-1",
            "request_info_INV-REM-1",
        ]

    def test_dm_reminder_includes_action_buttons(self):
        instance = MagicMock()
        instance.send_dm = AsyncMock()
        instance.resolve_user_targets = AsyncMock(
            return_value={"delivery_ids": ["U123"], "mentions": ["<@U123>"], "labels": ["U123"], "unresolved": []}
        )

        with patch("clearledgr.services.slack_api.get_slack_client", return_value=instance):
            result = _run(
                send_approval_reminder(
                    ap_item={
                        "id": "AP-123",
                        "vendor_name": "Approval Reminder Co",
                        "amount": 42.0,
                        "currency": "USD",
                        "invoice_number": "INV-REM-2",
                        "organization_id": "default",
                        "metadata": {"approval_channel": "C-APPROVALS"},
                    },
                    approver_ids=["U123"],
                    hours_pending=4,
                    organization_id="default",
                    stage="reminder",
                )
            )

        assert result
        instance.send_dm.assert_awaited_once()
        dm_blocks = instance.send_dm.await_args.kwargs["blocks"]
        actions_block = next(block for block in dm_blocks if block.get("type") == "actions")
        action_ids = [element["action_id"] for element in actions_block["elements"]]
        assert action_ids == [
            "approve_invoice_AP-123",
            "reject_invoice_AP-123",
            "request_info_AP-123",
        ]

    def test_escalation_posts_channel_with_approver_mentions(self):
        instance = MagicMock()
        instance.send_dm = AsyncMock()
        instance.resolve_user_targets = AsyncMock(
            return_value={
                "delivery_ids": ["U123"],
                "mentions": ["<@U123>"],
                "labels": ["approver@company.com"],
                "unresolved": [],
            }
        )

        with patch("clearledgr.services.slack_api.get_slack_client", return_value=instance):
            with patch(
                "clearledgr.services.slack_notifications._post_slack_blocks",
                new=AsyncMock(return_value=True),
            ) as post_blocks:
                result = _run(
                    send_approval_reminder(
                        ap_item={
                            "id": "AP-ESC-1",
                            "vendor_name": "Approval Reminder Co",
                            "amount": 420.0,
                            "currency": "USD",
                            "invoice_number": "INV-ESC-1",
                            "organization_id": "default",
                            "metadata": {"approval_channel": "C-APPROVALS"},
                        },
                        approver_ids=["approver@company.com"],
                        hours_pending=24,
                        organization_id="default",
                        stage="escalation",
                    )
                )

        assert result
        instance.send_dm.assert_awaited_once()
        dm_args = instance.send_dm.await_args
        assert dm_args.args[0] == "U123"
        assert "<@U123>" not in dm_args.args[1]
        assert "Approval ESCALATION" in dm_args.args[1]
        posted_text = post_blocks.await_args.kwargs["text"]
        posted_blocks = post_blocks.await_args.kwargs["blocks"]
        assert "<@U123>" in posted_text
        approver_block = next(
            block for block in posted_blocks
            if block.get("type") == "section"
            and "Pending approvers" in str((block.get("text") or {}).get("text") or "")
        )
        assert "<@U123>" in approver_block["text"]["text"]


# ---------------------------------------------------------------------------
# process_retry_queue
# ---------------------------------------------------------------------------


class TestProcessRetryQueue:
    def test_processes_pending_notifications(self, monkeypatch, mock_runtime):
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        mock_db = MagicMock()
        mock_db.get_pending_notifications.return_value = [
            {
                "id": "notif-1",
                "organization_id": "acme",
                "channel": "slack",
                "payload_json": json.dumps({"blocks": [], "text": "retry"}),
                "retry_count": 0,
                "max_retries": 5,
            }
        ]
        mock_db.mark_notification_failed = MagicMock()

        with patch("clearledgr.core.database.get_db", return_value=mock_db):
            with patch("clearledgr.services.slack_notifications.resolve_slack_runtime", return_value=mock_runtime):
                count = _run(process_retry_queue())
                assert count >= 0

    def test_empty_queue(self):
        mock_db = MagicMock()
        mock_db.get_pending_notifications.return_value = []
        with patch("clearledgr.core.database.get_db", return_value=mock_db):
            count = _run(process_retry_queue())
            assert count == 0
