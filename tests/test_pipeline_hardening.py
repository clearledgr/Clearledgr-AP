"""Tests for pipeline hardening: callback retry, post-posting verification, attachment forwarding.

Follows existing test patterns from test_erp_preflight.py.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clearledgr.core import database as db_module
from clearledgr.integrations.erp_router import (
    ERPConnection,
    verify_bill_posted,
    attach_file_to_erp_bill,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


def _qb_connection(**overrides) -> ERPConnection:
    defaults = dict(type="quickbooks", access_token="tok_qb", realm_id="realm_123")
    defaults.update(overrides)
    return ERPConnection(**defaults)


# ===========================================================================
# Fix 1: Callback retry tests
# ===========================================================================

class TestSlackCallbackRetry:
    """Slack response_url retry on failure."""

    def test_success_does_not_enqueue(self, db):
        """Successful response_url POST should NOT enqueue."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("clearledgr.api.slack_invoices.httpx.AsyncClient", return_value=mock_client):
            from clearledgr.api.slack_invoices import _post_to_response_url
            asyncio.run(_post_to_response_url("https://hooks.slack.com/x", {"text": "ok"}))

        # No notification should be enqueued
        pending = db.get_pending_notifications(limit=10)
        assert len(pending) == 0

    def test_failure_enqueues_notification(self, db):
        """Failed response_url POST should enqueue for retry."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("connection timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("clearledgr.api.slack_invoices.httpx.AsyncClient", return_value=mock_client), \
             patch("clearledgr.api.slack_invoices.get_db", return_value=db):
            from clearledgr.api.slack_invoices import _post_to_response_url
            asyncio.run(_post_to_response_url("https://hooks.slack.com/x", {"text": "ok"}))

        pending = db.get_pending_notifications(limit=10)
        assert len(pending) == 1
        assert pending[0]["channel"] == "slack_response_url"
        payload = json.loads(pending[0]["payload_json"])
        assert payload["response_url"] == "https://hooks.slack.com/x"
        assert payload["body"] == {"text": "ok"}


class TestTeamsCallbackRetry:
    """Teams card update retry on failure.

    §12 / §6.8 — Teams is disabled in V1. These tests exercise the
    post-V1 retry behaviour where the flag is on; each method opts in
    via monkeypatch. The disabled-flag-returns-True short-circuit is
    covered separately in test_v1_boundary_flags.py.
    """

    def test_teams_failure_enqueues(self, db, monkeypatch):
        """Failed Teams card update should enqueue for retry."""
        monkeypatch.setenv("FEATURE_TEAMS_ENABLED", "true")
        from clearledgr.services.slack_notifications import _retry_teams_card_update
        payload = {
            "service_url": "https://smba.trafficmanager.net/x",
            "conversation_id": "conv123",
            "activity_id": "act456",
            "result_status": "approved",
            "actor_display": "Jane",
            "action": "approve",
            "reason": None,
        }
        mock_client = MagicMock()
        mock_client.update_activity = MagicMock(side_effect=Exception("timeout"))

        with patch("clearledgr.services.teams_api.TeamsAPIClient", return_value=mock_client):
            result = asyncio.run(_retry_teams_card_update(payload))
        assert result is False

    def test_teams_retry_success(self, monkeypatch):
        """Successful Teams card update retry returns True."""
        monkeypatch.setenv("FEATURE_TEAMS_ENABLED", "true")
        from clearledgr.services.slack_notifications import _retry_teams_card_update
        payload = {
            "service_url": "https://smba.trafficmanager.net/x",
            "conversation_id": "conv123",
            "activity_id": "act456",
            "result_status": "approved",
            "actor_display": "Jane",
            "action": "approve",
            "reason": None,
        }
        mock_client = MagicMock()
        mock_client.update_activity = MagicMock()

        with patch("clearledgr.services.teams_api.TeamsAPIClient", return_value=mock_client):
            result = asyncio.run(_retry_teams_card_update(payload))
        assert result is True
        mock_client.update_activity.assert_called_once()

    def test_teams_retry_missing_fields(self, monkeypatch):
        """Retry with missing required fields returns False."""
        monkeypatch.setenv("FEATURE_TEAMS_ENABLED", "true")
        from clearledgr.services.slack_notifications import _retry_teams_card_update
        result = asyncio.run(_retry_teams_card_update({"service_url": "", "conversation_id": "", "activity_id": ""}))
        assert result is False


class TestSlackResponseUrlRetry:
    """_retry_slack_response_url handler tests."""

    def test_success_returns_true(self):
        from clearledgr.services.slack_notifications import _retry_slack_response_url
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("clearledgr.services.slack_notifications.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_retry_slack_response_url({
                "response_url": "https://hooks.slack.com/x",
                "body": {"text": "hi"},
            }))
        assert result is True

    def test_empty_url_returns_false(self):
        from clearledgr.services.slack_notifications import _retry_slack_response_url
        result = asyncio.run(_retry_slack_response_url({"response_url": "", "body": {}}))
        assert result is False


class TestRetryQueueDispatch:
    """process_retry_queue dispatches by channel."""

    def test_dispatches_slack_response_url(self, db):
        """Retry queue should dispatch slack_response_url channel."""
        notif_id = db.enqueue_notification(
            organization_id="system",
            channel="slack_response_url",
            payload={"response_url": "https://hooks.slack.com/x", "body": {"text": "ok"}},
        )

        mock_retry = AsyncMock(return_value=True)
        with patch("clearledgr.services.slack_notifications._retry_slack_response_url", mock_retry), \
             patch("clearledgr.core.database.get_db", return_value=db):
            from clearledgr.services.slack_notifications import process_retry_queue
            processed = asyncio.run(process_retry_queue())

        assert processed == 1
        mock_retry.assert_called_once()

    def test_dispatches_teams_card_update(self, db):
        """Retry queue should dispatch teams_card_update channel."""
        db.enqueue_notification(
            organization_id="system",
            channel="teams_card_update",
            payload={"service_url": "https://x", "conversation_id": "c", "activity_id": "a"},
        )

        mock_retry = AsyncMock(return_value=True)
        with patch("clearledgr.services.slack_notifications._retry_teams_card_update", mock_retry), \
             patch("clearledgr.core.database.get_db", return_value=db):
            from clearledgr.services.slack_notifications import process_retry_queue
            processed = asyncio.run(process_retry_queue())

        assert processed == 1
        mock_retry.assert_called_once()


# ===========================================================================
# Fix 2: Post-posting verification tests
# ===========================================================================

class TestVerifyBillPosted:
    """verify_bill_posted() tests."""

    def test_no_invoice_number(self):
        result = asyncio.run(verify_bill_posted("org1", ""))
        assert result["verified"] is False
        assert result["reason"] == "no_invoice_number"

    def test_no_erp_connection(self):
        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=None):
            result = asyncio.run(verify_bill_posted("org1", "INV-001"))
        assert result["verified"] is True
        assert result["reason"] == "no_erp_connection"

    def test_bill_found_verified(self):
        conn = _qb_connection()
        mock_finder = AsyncMock(return_value={"bill_id": "B1", "doc_number": "INV-001", "amount": 100.0, "erp": "quickbooks"})

        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=conn), \
             patch.dict("clearledgr.integrations.erp_router._BILL_FINDERS", {"quickbooks": mock_finder}):
            result = asyncio.run(verify_bill_posted("org1", "INV-001", expected_amount=100.0))

        assert result["verified"] is True
        assert result["reason"] == "confirmed"
        assert result["bill"]["bill_id"] == "B1"

    def test_bill_not_found(self):
        conn = _qb_connection()
        mock_finder = AsyncMock(return_value=None)

        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=conn), \
             patch.dict("clearledgr.integrations.erp_router._BILL_FINDERS", {"quickbooks": mock_finder}):
            result = asyncio.run(verify_bill_posted("org1", "INV-001"))

        assert result["verified"] is False
        assert result["reason"] == "bill_not_found_in_erp"

    def test_amount_mismatch(self):
        conn = _qb_connection()
        mock_finder = AsyncMock(return_value={"bill_id": "B1", "doc_number": "INV-001", "amount": 200.0, "erp": "quickbooks"})

        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=conn), \
             patch.dict("clearledgr.integrations.erp_router._BILL_FINDERS", {"quickbooks": mock_finder}):
            result = asyncio.run(verify_bill_posted("org1", "INV-001", expected_amount=100.0))

        assert result["verified"] is False
        assert "amount_mismatch" in result["reason"]

    def test_amount_within_tolerance(self):
        conn = _qb_connection()
        mock_finder = AsyncMock(return_value={"bill_id": "B1", "doc_number": "INV-001", "amount": 100.005, "erp": "quickbooks"})

        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=conn), \
             patch.dict("clearledgr.integrations.erp_router._BILL_FINDERS", {"quickbooks": mock_finder}):
            result = asyncio.run(verify_bill_posted("org1", "INV-001", expected_amount=100.0))

        assert result["verified"] is True
        assert result["reason"] == "confirmed"

    def test_finder_error_defaults_verified(self):
        conn = _qb_connection()
        mock_finder = AsyncMock(side_effect=Exception("network error"))

        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=conn), \
             patch.dict("clearledgr.integrations.erp_router._BILL_FINDERS", {"quickbooks": mock_finder}):
            result = asyncio.run(verify_bill_posted("org1", "INV-001"))

        # On error, default to verified=True (don't block pipeline)
        assert result["verified"] is True
        assert "lookup_error" in result["reason"]

    def test_unknown_erp_type(self):
        conn = ERPConnection(type="unknown_erp")
        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=conn):
            result = asyncio.run(verify_bill_posted("org1", "INV-001"))

        assert result["verified"] is True
        assert result["reason"] == "no_finder_for_erp"


# ===========================================================================
# Fix 3: Attachment forwarding tests
# ===========================================================================

class TestAttachFileToErpBill:
    """attach_file_to_erp_bill() tests."""

    def test_no_connection_returns_none(self):
        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=None):
            result = asyncio.run(attach_file_to_erp_bill("org1", "B1", "https://example.com/inv.pdf"))
        assert result is None

    def test_unknown_erp_returns_none(self):
        conn = ERPConnection(type="unknown_erp")
        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=conn):
            result = asyncio.run(attach_file_to_erp_bill("org1", "B1", "https://example.com/inv.pdf"))
        assert result is None

    def test_download_failure_returns_none(self):
        conn = _qb_connection()
        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=conn), \
             patch("clearledgr.integrations.erp_router._download_attachment", AsyncMock(return_value=None)):
            result = asyncio.run(attach_file_to_erp_bill("org1", "B1", "https://example.com/inv.pdf"))
        assert result is None

    def test_successful_upload(self):
        conn = _qb_connection()
        mock_uploader = AsyncMock(return_value={"attached": True, "erp": "quickbooks"})

        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=conn), \
             patch("clearledgr.integrations.erp_router._download_attachment", AsyncMock(return_value=b"%PDF-1.4 test")), \
             patch.dict("clearledgr.integrations.erp_router._ATTACHMENT_UPLOADERS", {"quickbooks": mock_uploader}):
            result = asyncio.run(attach_file_to_erp_bill("org1", "B1", "https://example.com/inv.pdf"))

        assert result is not None
        assert result["attached"] is True
        assert result["erp"] == "quickbooks"

    def test_upload_error_returns_none(self):
        conn = _qb_connection()
        mock_uploader = AsyncMock(side_effect=Exception("upload failed"))

        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=conn), \
             patch("clearledgr.integrations.erp_router._download_attachment", AsyncMock(return_value=b"%PDF-1.4 test")), \
             patch.dict("clearledgr.integrations.erp_router._ATTACHMENT_UPLOADERS", {"quickbooks": mock_uploader}):
            result = asyncio.run(attach_file_to_erp_bill("org1", "B1", "https://example.com/inv.pdf"))

        assert result is None


class TestDownloadAttachment:
    """_download_attachment tests."""

    def test_empty_url_returns_none(self):
        from clearledgr.integrations.erp_router import _download_attachment
        result = asyncio.run(_download_attachment(""))
        assert result is None

    def test_successful_download(self):
        from clearledgr.integrations.erp_router import _download_attachment
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = b"%PDF test"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("clearledgr.integrations.erp_router.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_download_attachment("https://example.com/inv.pdf"))
        assert result == b"%PDF test"

    def test_download_error_returns_none(self):
        from clearledgr.integrations.erp_router import _download_attachment
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("clearledgr.integrations.erp_router.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_download_attachment("https://example.com/inv.pdf"))
        assert result is None
