"""Tests for §9 vendor-activation Slack confirmation.

Covers the notification helper itself and the wiring into
activate_vendor_in_erp so a future refactor that accidentally
drops the Slack call is caught in CI.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clearledgr.services.slack_notifications import (
    send_vendor_activated_notification,
)


class TestSendVendorActivatedNotification:
    @pytest.mark.asyncio
    async def test_posts_to_slack_with_vendor_and_erp(self):
        with patch(
            "clearledgr.services.slack_notifications._post_slack_blocks",
            new=AsyncMock(return_value={"ok": True, "via": "bot"}),
        ) as mock_post:
            result = await send_vendor_activated_notification(
                vendor_name="Acme Ltd",
                erp_system="quickbooks",
                erp_vendor_id="QB-9876",
                organization_id="org_123",
            )
        assert result == {"ok": True, "via": "bot"}
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        assert kwargs["organization_id"] == "org_123"
        # Text + blocks must name the vendor and the ERP so the AP
        # team can tell at a glance which activation fired.
        assert "Acme Ltd" in kwargs["text"]
        assert "quickbooks" in kwargs["text"]
        block_text = kwargs["blocks"][0]["text"]["text"]
        assert "Acme Ltd" in block_text
        assert "QB-9876" in block_text
        assert "live in quickbooks" in block_text

    @pytest.mark.asyncio
    async def test_handles_missing_erp_vendor_id(self):
        # erp_vendor_id can be None when the ERP connector didn't
        # return an ID (legacy connector behaviour). The message
        # should still fire with a clean fallback.
        with patch(
            "clearledgr.services.slack_notifications._post_slack_blocks",
            new=AsyncMock(return_value={"ok": True}),
        ) as mock_post:
            await send_vendor_activated_notification(
                vendor_name="Acme Ltd",
                erp_system="xero",
                erp_vendor_id=None,
                organization_id="org_123",
            )
        block_text = mock_post.call_args.kwargs["blocks"][0]["text"]["text"]
        assert "ERP ID" not in block_text
        assert "Acme Ltd" in block_text

    @pytest.mark.asyncio
    async def test_fallback_erp_label_when_system_missing(self):
        with patch(
            "clearledgr.services.slack_notifications._post_slack_blocks",
            new=AsyncMock(return_value={"ok": True}),
        ) as mock_post:
            await send_vendor_activated_notification(
                vendor_name="Acme Ltd",
                erp_system="",
                erp_vendor_id="V1",
                organization_id="org_123",
            )
        text = mock_post.call_args.kwargs["text"]
        assert "ERP" in text  # generic label used when erp_system blank


class TestActivationSlackWiring:
    """End-to-end wiring: activate_vendor_in_erp calls send_vendor_activated_notification."""

    @pytest.mark.asyncio
    async def test_activation_fires_slack_post(self):
        # Mock the DB + ERP dispatcher. We want to exercise the slack
        # call path only, not the DB transitions (tested elsewhere).
        db = MagicMock()
        db.get_onboarding_session_by_id.return_value = {
            "id": "sess_1",
            "organization_id": "org_123",
            "vendor_name": "Acme Ltd",
            "state": "bank_verified",
            "is_active": 1,
            "metadata": {},
        }
        db.attach_erp_vendor_id = MagicMock()
        db.transition_onboarding_session_state = MagicMock()
        db.revoke_session_tokens = MagicMock()
        db.append_ap_audit_event = MagicMock()
        db.get_erp_connections = MagicMock(return_value=[{"erp_type": "quickbooks"}])

        from clearledgr.services import vendor_onboarding_lifecycle

        with patch.object(
            vendor_onboarding_lifecycle,
            "_dispatch_erp_create_vendor",
            new=AsyncMock(return_value="QB-5432"),
        ), patch(
            "clearledgr.services.slack_notifications.send_vendor_activated_notification",
            new=AsyncMock(return_value={"ok": True}),
        ) as mock_slack:
            result = await vendor_onboarding_lifecycle.activate_vendor_in_erp(
                session_id="sess_1", db=db,
            )

        assert result.success is True
        assert result.erp_vendor_id == "QB-5432"
        # Slack notification fired with the expected payload
        mock_slack.assert_called_once()
        kwargs = mock_slack.call_args.kwargs
        assert kwargs["vendor_name"] == "Acme Ltd"
        assert kwargs["erp_system"] == "quickbooks"
        assert kwargs["erp_vendor_id"] == "QB-5432"
        assert kwargs["organization_id"] == "org_123"

    @pytest.mark.asyncio
    async def test_slack_failure_does_not_roll_back_activation(self):
        # A Slack outage must never revert an already-successful ERP
        # activation. The function returns success=True even when
        # the notification raises.
        db = MagicMock()
        db.get_onboarding_session_by_id.return_value = {
            "id": "sess_1",
            "organization_id": "org_123",
            "vendor_name": "Acme Ltd",
            "state": "bank_verified",
            "is_active": 1,
            "metadata": {},
        }
        db.attach_erp_vendor_id = MagicMock()
        db.transition_onboarding_session_state = MagicMock()
        db.revoke_session_tokens = MagicMock()
        db.append_ap_audit_event = MagicMock()
        db.get_erp_connections = MagicMock(return_value=[])

        from clearledgr.services import vendor_onboarding_lifecycle

        with patch.object(
            vendor_onboarding_lifecycle,
            "_dispatch_erp_create_vendor",
            new=AsyncMock(return_value="QB-9999"),
        ), patch(
            "clearledgr.services.slack_notifications.send_vendor_activated_notification",
            new=AsyncMock(side_effect=RuntimeError("slack down")),
        ):
            result = await vendor_onboarding_lifecycle.activate_vendor_in_erp(
                session_id="sess_1", db=db,
            )

        assert result.success is True
        assert result.erp_vendor_id == "QB-9999"
