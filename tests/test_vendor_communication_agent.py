"""Tests for the full vendor communication agent.

Covers:
1. Gmail send scope added
2. GmailAPIClient.send_draft / send_message
3. Vendor communication templates (render, sanitise, missing keys)
4. AutoFollowUpService.check_vendor_response
5. AutoFollowUpService.check_followup_escalation
6. Updated _handle_request_vendor_info (send-first, draft-fallback, metadata)
7. Slack notification helpers for vendor events
8. Background response scanning logic
"""

import asyncio
import json
import sys
import types
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. Gmail scope list includes gmail.send
# ---------------------------------------------------------------------------


def test_gmail_scopes_include_send():
    from clearledgr.services.gmail_api import GMAIL_SCOPES

    assert "https://www.googleapis.com/auth/gmail.send" in GMAIL_SCOPES


# ---------------------------------------------------------------------------
# 2. GmailAPIClient.send_draft / send_message exist and are async
# ---------------------------------------------------------------------------


def test_gmail_client_has_send_draft_method():
    from clearledgr.services.gmail_api import GmailAPIClient

    client = GmailAPIClient("test-user")
    assert hasattr(client, "send_draft")
    assert asyncio.iscoroutinefunction(client.send_draft)


def test_gmail_client_has_send_message_method():
    from clearledgr.services.gmail_api import GmailAPIClient

    client = GmailAPIClient("test-user")
    assert hasattr(client, "send_message")
    assert asyncio.iscoroutinefunction(client.send_message)


# ---------------------------------------------------------------------------
# 3. Vendor communication templates
# ---------------------------------------------------------------------------


def test_render_template_missing_po():
    from clearledgr.services.vendor_communication_templates import render_template

    result = render_template("missing_po", {
        "original_subject": "Invoice #123",
        "invoice_number": "INV-123",
        "currency": "USD",
        "amount": "1,500.00",
        "company_name": "Acme Corp",
    })
    assert "Purchase Order" in result["subject"]
    assert "INV-123" in result["body"]
    assert "Acme Corp" in result["body"]
    assert "1,500.00" in result["body"]


def test_render_template_followup_reminder():
    from clearledgr.services.vendor_communication_templates import render_template

    result = render_template("followup_reminder", {
        "original_subject": "Invoice #456",
        "original_question": "Please provide the PO number.",
        "invoice_number": "INV-456",
        "currency": "EUR",
        "amount": "2,000.00",
        "company_name": "TestCo",
    })
    assert "Follow-up" in result["subject"]
    assert "PO number" in result["body"]
    assert "TestCo" in result["body"]


def test_render_template_general_inquiry():
    from clearledgr.services.vendor_communication_templates import render_template

    result = render_template("general_inquiry", {
        "original_subject": "Payment",
        "question": "What is the correct amount?",
        "invoice_number": "X-99",
        "currency": "USD",
        "amount": "500",
        "company_name": "Clearledgr",
    })
    assert "What is the correct amount?" in result["body"]


def test_render_template_unknown_raises_key_error():
    from clearledgr.services.vendor_communication_templates import render_template

    with pytest.raises(KeyError, match="nonexistent_template"):
        render_template("nonexistent_template", {})


def test_render_template_sanitises_html():
    from clearledgr.services.vendor_communication_templates import render_template

    result = render_template("general_inquiry", {
        "original_subject": "Test",
        "question": "<script>alert('xss')</script>Important question",
        "invoice_number": "X-1",
        "currency": "USD",
        "amount": "100",
        "company_name": "TestCo",
    })
    assert "<script>" not in result["body"]
    assert "Important question" in result["body"]


def test_render_template_missing_context_keys_default_to_empty():
    from clearledgr.services.vendor_communication_templates import render_template

    # Only provide some keys — missing ones should not raise
    result = render_template("missing_po", {"original_subject": "Test"})
    assert "Test" in result["subject"]
    # missing fields become empty strings — just assert no exception


def test_render_template_truncates_long_values():
    from clearledgr.services.vendor_communication_templates import render_template, _MAX_FIELD_LEN

    long_value = "A" * 2000
    result = render_template("general_inquiry", {
        "question": long_value,
        "original_subject": "X",
    })
    # body should contain the truncated version
    assert "..." in result["body"]
    assert "A" * (_MAX_FIELD_LEN + 1) not in result["body"]


# ---------------------------------------------------------------------------
# 4. AutoFollowUpService.check_vendor_response
# ---------------------------------------------------------------------------


def _make_mock_message(msg_id, sender, date, snippet="reply text"):
    msg = MagicMock()
    msg.id = msg_id
    msg.sender = sender
    msg.date = date
    msg.snippet = snippet
    return msg


def test_check_vendor_response_detects_reply():
    from clearledgr.services.auto_followup import AutoFollowUpService

    svc = AutoFollowUpService("org-1")
    sent_at = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
    reply_date = datetime(2026, 3, 16, 14, 0, 0, tzinfo=timezone.utc)

    mock_gmail = MagicMock()
    mock_gmail.get_thread = AsyncMock(return_value=[
        _make_mock_message("msg-old", "me@acme.com", sent_at - timedelta(hours=1)),
        _make_mock_message("msg-reply", "billing@vendor.com", reply_date, "Here is the PO"),
    ])

    result = asyncio.run(svc.check_vendor_response(
        gmail_client=mock_gmail,
        ap_item_id="ap-1",
        thread_id="thread-1",
        followup_sent_at=sent_at.isoformat(),
        vendor_email="billing@vendor.com",
    ))

    assert result is not None
    assert result["response_detected"] is True
    assert result["message_id"] == "msg-reply"
    assert result["ap_item_id"] == "ap-1"


def test_check_vendor_response_matches_domain():
    from clearledgr.services.auto_followup import AutoFollowUpService

    svc = AutoFollowUpService("org-1")
    sent_at = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
    reply_date = datetime(2026, 3, 16, 14, 0, 0, tzinfo=timezone.utc)

    mock_gmail = MagicMock()
    mock_gmail.get_thread = AsyncMock(return_value=[
        _make_mock_message("msg-reply", "support@vendor.com", reply_date, "Info attached"),
    ])

    result = asyncio.run(svc.check_vendor_response(
        gmail_client=mock_gmail,
        ap_item_id="ap-2",
        thread_id="thread-2",
        followup_sent_at=sent_at.isoformat(),
        vendor_email="billing@vendor.com",
    ))

    assert result is not None
    assert result["response_detected"] is True


def test_check_vendor_response_returns_none_when_no_reply():
    from clearledgr.services.auto_followup import AutoFollowUpService

    svc = AutoFollowUpService("org-1")
    sent_at = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)

    mock_gmail = MagicMock()
    mock_gmail.get_thread = AsyncMock(return_value=[
        _make_mock_message("msg-old", "me@acme.com", sent_at - timedelta(hours=1)),
    ])

    result = asyncio.run(svc.check_vendor_response(
        gmail_client=mock_gmail,
        ap_item_id="ap-3",
        thread_id="thread-3",
        followup_sent_at=sent_at.isoformat(),
        vendor_email="billing@vendor.com",
    ))

    assert result is None


def test_check_vendor_response_ignores_messages_before_cutoff():
    from clearledgr.services.auto_followup import AutoFollowUpService

    svc = AutoFollowUpService("org-1")
    sent_at = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)

    mock_gmail = MagicMock()
    mock_gmail.get_thread = AsyncMock(return_value=[
        # Message from vendor but BEFORE the follow-up was sent
        _make_mock_message("msg-old-vendor", "billing@vendor.com", sent_at - timedelta(hours=2)),
    ])

    result = asyncio.run(svc.check_vendor_response(
        gmail_client=mock_gmail,
        ap_item_id="ap-4",
        thread_id="thread-4",
        followup_sent_at=sent_at.isoformat(),
        vendor_email="billing@vendor.com",
    ))

    assert result is None


def test_check_vendor_response_handles_gmail_error():
    from clearledgr.services.auto_followup import AutoFollowUpService

    svc = AutoFollowUpService("org-1")

    mock_gmail = MagicMock()
    mock_gmail.get_thread = AsyncMock(side_effect=RuntimeError("network error"))

    result = asyncio.run(svc.check_vendor_response(
        gmail_client=mock_gmail,
        ap_item_id="ap-5",
        thread_id="thread-5",
        followup_sent_at="2026-03-15T10:00:00+00:00",
        vendor_email="billing@vendor.com",
    ))

    assert result is None


# ---------------------------------------------------------------------------
# 5. AutoFollowUpService.check_followup_escalation
# ---------------------------------------------------------------------------


def test_escalation_returns_none_before_deadline():
    from clearledgr.services.auto_followup import AutoFollowUpService

    svc = AutoFollowUpService("org-1")
    # Sent 1 hour ago — not yet due for escalation (3 day default)
    sent_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    result = svc.check_followup_escalation(
        ap_item_id="ap-1",
        followup_sent_at=sent_at,
    )
    assert result is None


def test_escalation_returns_resend_when_under_max():
    from clearledgr.services.auto_followup import AutoFollowUpService

    svc = AutoFollowUpService("org-1")
    # Sent 4 days ago, only 1 attempt so far
    sent_at = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()

    result = svc.check_followup_escalation(
        ap_item_id="ap-1",
        followup_sent_at=sent_at,
        escalation_days=3,
        max_followups=3,
        followup_attempt_count=1,
    )
    assert result is not None
    assert result["action"] == "resend"
    assert result["attempt"] == 2


def test_escalation_returns_escalate_when_at_max():
    from clearledgr.services.auto_followup import AutoFollowUpService

    svc = AutoFollowUpService("org-1")
    sent_at = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

    result = svc.check_followup_escalation(
        ap_item_id="ap-1",
        followup_sent_at=sent_at,
        escalation_days=3,
        max_followups=3,
        followup_attempt_count=3,
    )
    assert result is not None
    assert result["action"] == "escalate"
    assert result["reason"] == "vendor_unresponsive"
    assert result["attempts"] == 3


def test_escalation_with_invalid_date_returns_none():
    from clearledgr.services.auto_followup import AutoFollowUpService

    svc = AutoFollowUpService("org-1")
    result = svc.check_followup_escalation(
        ap_item_id="ap-1",
        followup_sent_at="not-a-date",
    )
    assert result is None


# ---------------------------------------------------------------------------
# 6. Updated _handle_request_vendor_info (send + fallback + metadata)
# ---------------------------------------------------------------------------


def _install_fake_gmail_client_module(get_gmail_client_fn):
    """Install a fake clearledgr.services.gmail_client module in sys.modules."""
    mod = types.ModuleType("clearledgr.services.gmail_client")
    mod.get_gmail_client = get_gmail_client_fn
    sys.modules["clearledgr.services.gmail_client"] = mod
    return mod


def _uninstall_fake_gmail_client_module():
    sys.modules.pop("clearledgr.services.gmail_client", None)


def _invoice_payload(**overrides):
    base = {
        "vendor_name": "Widgets Inc",
        "amount": 2400.00,
        "currency": "USD",
        "gmail_thread_id": "thread-xyz-456",
        "gmail_id": "msg-xyz-456",
        "sender": "billing@widgets.com",
        "id": "ap-item-42",
        "subject": "Invoice #W-100",
        "invoice_number": "W-100",
    }
    base.update(overrides)
    return base


def test_request_vendor_info_sends_directly():
    """When send_message succeeds, no draft is created."""
    from clearledgr.core.skills.ap_skill import _handle_request_vendor_info
    from clearledgr.services.auto_followup import MissingInfoType

    mock_followup = MagicMock()
    mock_followup.detect_missing_info.return_value = [MissingInfoType.PO_NUMBER]

    mock_gmail_client = MagicMock()
    mock_gmail_client.send_message = AsyncMock(return_value={"id": "sent-msg-001"})

    _install_fake_gmail_client_module(lambda _org_id: mock_gmail_client)
    try:
        with patch(
            "clearledgr.services.auto_followup.get_auto_followup_service",
            return_value=mock_followup,
        ), patch(
            "clearledgr.core.skills.ap_skill.json",
            json,
        ):
            result = asyncio.run(
                _handle_request_vendor_info(
                    invoice_payload=_invoice_payload(),
                    question="Please provide the PO number.",
                    organization_id="org-1",
                )
            )
    finally:
        _uninstall_fake_gmail_client_module()

    assert result["ok"] is True
    assert result["sent"] is True
    assert result["message_id"] == "sent-msg-001"
    assert result["draft_created"] is False


def test_request_vendor_info_falls_back_to_draft():
    """When send_message fails, falls back to draft creation."""
    from clearledgr.core.skills.ap_skill import _handle_request_vendor_info
    from clearledgr.services.auto_followup import MissingInfoType

    mock_followup = MagicMock()
    mock_followup.detect_missing_info.return_value = [MissingInfoType.AMOUNT]
    mock_followup.create_gmail_draft = AsyncMock(return_value="draft-fallback-99")

    mock_gmail_client = MagicMock()
    mock_gmail_client.send_message = AsyncMock(side_effect=RuntimeError("gmail.send scope missing"))

    _install_fake_gmail_client_module(lambda _org_id: mock_gmail_client)
    try:
        with patch(
            "clearledgr.services.auto_followup.get_auto_followup_service",
            return_value=mock_followup,
        ):
            result = asyncio.run(
                _handle_request_vendor_info(
                    invoice_payload=_invoice_payload(),
                    question="What is the correct amount?",
                    organization_id="org-1",
                )
            )
    finally:
        _uninstall_fake_gmail_client_module()

    assert result["ok"] is True
    assert result["sent"] is False
    assert result["draft_created"] is True
    assert result["draft_id"] == "draft-fallback-99"


def test_request_vendor_info_no_missing_info_no_question():
    """No missing fields and no explicit question -> no send/draft."""
    from clearledgr.core.skills.ap_skill import _handle_request_vendor_info

    mock_followup = MagicMock()
    mock_followup.detect_missing_info.return_value = []

    with patch(
        "clearledgr.services.auto_followup.get_auto_followup_service",
        return_value=mock_followup,
    ):
        result = asyncio.run(
            _handle_request_vendor_info(
                invoice_payload=_invoice_payload(),
                question="",
                organization_id="org-1",
            )
        )

    assert result["ok"] is True
    assert result["sent"] is False
    assert result["draft_created"] is False
    assert result["reason"] == "no_missing_info"


def test_request_vendor_info_uses_template_when_no_question():
    """When no question is provided, uses template based on missing info type."""
    from clearledgr.core.skills.ap_skill import _handle_request_vendor_info
    from clearledgr.services.auto_followup import MissingInfoType

    mock_followup = MagicMock()
    mock_followup.detect_missing_info.return_value = [MissingInfoType.DUE_DATE]

    mock_gmail_client = MagicMock()
    sent_args = {}

    async def capture_send(**kwargs):
        sent_args.update(kwargs)
        return {"id": "sent-msg-002"}

    mock_gmail_client.send_message = capture_send

    _install_fake_gmail_client_module(lambda _org_id: mock_gmail_client)
    try:
        with patch(
            "clearledgr.services.auto_followup.get_auto_followup_service",
            return_value=mock_followup,
        ):
            result = asyncio.run(
                _handle_request_vendor_info(
                    invoice_payload=_invoice_payload(),
                    question=None,
                    organization_id="org-1",
                )
            )
    finally:
        _uninstall_fake_gmail_client_module()

    assert result["ok"] is True
    assert result["sent"] is True
    # Should have used the due_date template
    assert "Due Date" in sent_args.get("subject", "") or "Payment Due Date" in sent_args.get("subject", "")


# ---------------------------------------------------------------------------
# 7. Slack vendor notification functions
# ---------------------------------------------------------------------------


def test_send_vendor_response_notification_is_async():
    from clearledgr.services.slack_notifications import send_vendor_response_notification

    assert asyncio.iscoroutinefunction(send_vendor_response_notification)


def test_send_vendor_escalation_notification_is_async():
    from clearledgr.services.slack_notifications import send_vendor_escalation_notification

    assert asyncio.iscoroutinefunction(send_vendor_escalation_notification)


def test_send_vendor_response_notification_calls_send_with_retry():
    from clearledgr.services.slack_notifications import send_vendor_response_notification

    with patch("clearledgr.services.slack_notifications.send_with_retry", new_callable=AsyncMock, return_value=True) as mock_send:
        result = asyncio.run(send_vendor_response_notification(
            organization_id="org-1",
            vendor="Widgets Inc",
            invoice_number="INV-001",
            ap_item_id="ap-42",
        ))

    assert result is True
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert "Widgets Inc" in call_kwargs.kwargs.get("text", "") or "Widgets Inc" in (call_kwargs.args[1] if len(call_kwargs.args) > 1 else "")


def test_send_vendor_escalation_notification_calls_send_with_retry():
    from clearledgr.services.slack_notifications import send_vendor_escalation_notification

    with patch("clearledgr.services.slack_notifications.send_with_retry", new_callable=AsyncMock, return_value=True) as mock_send:
        result = asyncio.run(send_vendor_escalation_notification(
            organization_id="org-1",
            vendor="Slow Vendor LLC",
            invoice_number="INV-002",
            days_waiting=10,
            attempts=3,
            ap_item_id="ap-99",
        ))

    assert result is True
    mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# 8. All templates render without error
# ---------------------------------------------------------------------------


def test_all_templates_render_without_error():
    from clearledgr.services.vendor_communication_templates import VENDOR_TEMPLATES, render_template

    base_ctx = {
        "original_subject": "Test Invoice",
        "invoice_number": "INV-TEST",
        "vendor_name": "TestVendor",
        "amount": "1,000.00",
        "currency": "USD",
        "company_name": "TestCo",
        "question": "A question",
        "original_question": "Previous question",
    }

    for template_id in VENDOR_TEMPLATES:
        result = render_template(template_id, base_ctx)
        assert "subject" in result
        assert "body" in result
        assert len(result["subject"]) > 0
        assert len(result["body"]) > 0
