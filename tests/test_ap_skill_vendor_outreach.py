"""Tests for the request_vendor_info tool in APSkill.

Validates that:
- A Gmail draft is created when missing info or a question is present
- No draft when nothing is missing and no question supplied
- Error when Gmail is not authenticated
- Error when thread_id or sender_email is missing
- APSkill exposes 5 tools including request_vendor_info
- System prompt mentions request_vendor_info
"""
import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clearledgr.core.skills.ap_skill import APSkill, _handle_request_vendor_info
from clearledgr.core.skills.base import AgentTask
from clearledgr.services.auto_followup import MissingInfoType


def _install_fake_gmail_client_module(get_gmail_client_fn):
    """Install a fake clearledgr.services.gmail_client module in sys.modules."""
    mod = types.ModuleType("clearledgr.services.gmail_client")
    mod.get_gmail_client = get_gmail_client_fn
    sys.modules["clearledgr.services.gmail_client"] = mod
    return mod


def _uninstall_fake_gmail_client_module():
    sys.modules.pop("clearledgr.services.gmail_client", None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_creates_draft_successfully():
    """Happy path: followup service detects missing info, Gmail draft created."""
    mock_followup = MagicMock()
    mock_followup.detect_missing_info.return_value = [MissingInfoType.PO_NUMBER]
    mock_followup.create_gmail_draft = AsyncMock(return_value="draft-id-999")

    mock_gmail_client = MagicMock()

    _install_fake_gmail_client_module(lambda _org_id: mock_gmail_client)
    try:
        with patch(
            "clearledgr.services.auto_followup.get_auto_followup_service",
            return_value=mock_followup,
        ):
            result = asyncio.run(
                _handle_request_vendor_info(
                    invoice_payload=_invoice_payload(),
                    question="Please provide the PO number for this invoice.",
                    organization_id="org-1",
                )
            )
    finally:
        _uninstall_fake_gmail_client_module()

    assert result["ok"] is True
    assert result["draft_created"] is True
    assert result["draft_id"] == "draft-id-999"
    assert result["to"] == "billing@widgets.com"
    assert "po_number" in result["missing_info"]


def test_no_missing_info_no_question():
    """No missing fields and no explicit question -> no draft created."""
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
    assert result["draft_created"] is False
    assert result["reason"] == "no_missing_info"


def test_gmail_not_authenticated():
    """If gmail_client raises, the function catches the error gracefully."""
    mock_followup = MagicMock()
    mock_followup.detect_missing_info.return_value = [MissingInfoType.AMOUNT]

    def _raise(*_args, **_kwargs):
        raise RuntimeError("gmail_not_authenticated")

    _install_fake_gmail_client_module(_raise)
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

    assert result["ok"] is False
    assert result["draft_created"] is False


def test_missing_thread_id():
    """Without gmail_thread_id/gmail_id or sender, return error."""
    mock_followup = MagicMock()
    mock_followup.detect_missing_info.return_value = [MissingInfoType.DUE_DATE]

    # Remove thread_id and sender so both are empty
    payload = _invoice_payload(
        gmail_thread_id="",
        gmail_id="",
        sender="",
        sender_email="",
    )

    with patch(
        "clearledgr.services.auto_followup.get_auto_followup_service",
        return_value=mock_followup,
    ):
        result = asyncio.run(
            _handle_request_vendor_info(
                invoice_payload=payload,
                question="When is this due?",
                organization_id="org-1",
            )
        )

    assert result["ok"] is False
    assert "thread_id" in result["error"].lower() or "sender" in result["error"].lower()
    assert result["draft_created"] is False


def test_ap_skill_has_five_tools():
    """APSkill.get_tools() returns exactly 6 tools and includes request_vendor_info + verify_erp_posting."""
    skill = APSkill("org-test")
    tools = skill.get_tools()
    names = [t.name for t in tools]

    assert len(tools) == 7
    assert "request_vendor_info" in names
    assert "enrich_with_context" in names
    assert "run_validation_gate" in names
    assert "get_ap_decision" in names
    assert "execute_routing" in names
    assert "verify_erp_posting" in names
    assert "check_payment_readiness" in names


def test_system_prompt_mentions_vendor_info():
    """The AP system prompt must reference request_vendor_info so Claude knows it exists."""
    task = AgentTask(
        task_type="ap_invoice_processing",
        organization_id="org-1",
        payload={
            "invoice": {
                "vendor_name": "Test Vendor",
                "amount": 100.0,
                "currency": "USD",
                "confidence": 0.90,
            }
        },
    )

    # Patch out the cross-invoice analyzer to avoid DB calls in build_system_prompt
    with patch(
        "clearledgr.services.cross_invoice_analysis.get_cross_invoice_analyzer",
        side_effect=ImportError("not needed"),
    ):
        prompt = APSkill("org-1").build_system_prompt(task)

    assert "request_vendor_info" in prompt
