"""Tests for the single-pass invoice processor.

Covers prompt construction, response parsing (valid JSON, invalid JSON,
markdown-fenced JSON). All Claude API calls are mocked.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.services.single_pass_processor import (  # noqa: E402
    _build_single_pass_prompt,
    _parse_single_pass_response,
)


# ---------------------------------------------------------------------------
# _build_single_pass_prompt
# ---------------------------------------------------------------------------


class TestBuildSinglePassPrompt:
    """Verify prompt construction produces valid, complete prompt text."""

    def test_basic_prompt_contains_sender_and_subject(self):
        prompt = _build_single_pass_prompt(
            subject="Invoice INV-001",
            sender="billing@acme.com",
            body="Please find attached invoice.",
        )
        assert "billing@acme.com" in prompt
        assert "Invoice INV-001" in prompt
        assert "Please find attached invoice." in prompt

    def test_prompt_includes_json_schema_keys(self):
        prompt = _build_single_pass_prompt(
            subject="Test",
            sender="test@test.com",
            body="body",
        )
        for key in ("classification", "extraction", "gl_coding",
                     "duplicate_analysis", "risk_assessment", "routing_decision"):
            assert key in prompt

    def test_prompt_includes_vendor_context_when_provided(self):
        prompt = _build_single_pass_prompt(
            subject="Test",
            sender="test@test.com",
            body="body",
            vendor_context="Vendor has 50 prior invoices avg $5000",
        )
        assert "VENDOR HISTORY" in prompt
        assert "50 prior invoices" in prompt

    def test_prompt_includes_thread_context_when_provided(self):
        prompt = _build_single_pass_prompt(
            subject="Test",
            sender="test@test.com",
            body="body",
            thread_context="Thread has 3 messages about payment",
        )
        assert "THREAD CONTEXT" in prompt
        assert "3 messages about payment" in prompt

    def test_prompt_includes_po_context_when_provided(self):
        prompt = _build_single_pass_prompt(
            subject="Test",
            sender="test@test.com",
            body="body",
            po_context="PO-12345 for $10,000",
        )
        assert "PURCHASE ORDERS" in prompt
        assert "PO-12345" in prompt

    def test_prompt_includes_recent_invoices_context(self):
        prompt = _build_single_pass_prompt(
            subject="Test",
            sender="test@test.com",
            body="body",
            recent_invoices_context="INV-100 $5000, INV-101 $4800",
        )
        assert "RECENT INVOICES" in prompt
        assert "INV-100" in prompt

    def test_prompt_omits_context_sections_when_empty(self):
        prompt = _build_single_pass_prompt(
            subject="Test",
            sender="test@test.com",
            body="body",
        )
        assert "VENDOR HISTORY" not in prompt
        assert "THREAD CONTEXT" not in prompt
        assert "PURCHASE ORDERS" not in prompt
        assert "RECENT INVOICES" not in prompt

    def test_prompt_includes_visual_note_when_visual_attachments(self):
        prompt = _build_single_pass_prompt(
            subject="Test",
            sender="test@test.com",
            body="body",
            has_visual_attachments=True,
        )
        assert "Visual attachments" in prompt

    def test_prompt_omits_visual_note_when_no_visual_attachments(self):
        prompt = _build_single_pass_prompt(
            subject="Test",
            sender="test@test.com",
            body="body",
            has_visual_attachments=False,
        )
        assert "Visual attachments" not in prompt

    def test_prompt_includes_attachment_text(self):
        prompt = _build_single_pass_prompt(
            subject="Test",
            sender="test@test.com",
            body="body",
            attachment_text="INVOICE #12345\nAmount: $500.00",
        )
        assert "ATTACHMENT TEXT" in prompt
        assert "INVOICE #12345" in prompt

    def test_prompt_omits_attachment_section_when_empty(self):
        prompt = _build_single_pass_prompt(
            subject="Test",
            sender="test@test.com",
            body="body",
            attachment_text="",
        )
        assert "ATTACHMENT TEXT" not in prompt

    def test_prompt_contains_injection_guard(self):
        prompt = _build_single_pass_prompt(
            subject="Test",
            sender="test@test.com",
            body="body",
        )
        assert "untrusted" in prompt.lower()


# ---------------------------------------------------------------------------
# _parse_single_pass_response
# ---------------------------------------------------------------------------


class TestParseSinglePassResponse:
    """Verify JSON parsing handles valid, invalid, and markdown-fenced input."""

    VALID_RESPONSE = json.dumps({
        "classification": {"document_type": "invoice", "confidence": 0.95, "reasoning": "test"},
        "extraction": {"vendor": "Acme", "amount": 100.0},
        "gl_coding": {"suggested_gl_code": "6000", "reasoning": "test"},
        "duplicate_analysis": {"is_duplicate": False, "is_amendment": False},
        "risk_assessment": {"fraud_risk": "none"},
        "routing_decision": {"recommendation": "approve", "confidence": 0.9},
    })

    def test_parse_valid_json(self):
        result = _parse_single_pass_response(self.VALID_RESPONSE)
        assert result is not None
        assert result["classification"]["document_type"] == "invoice"
        assert result["extraction"]["vendor"] == "Acme"
        assert result["routing_decision"]["recommendation"] == "approve"

    def test_parse_returns_none_for_empty_string(self):
        assert _parse_single_pass_response("") is None

    def test_parse_returns_none_for_none_input(self):
        assert _parse_single_pass_response(None) is None

    def test_parse_returns_none_for_invalid_json(self):
        assert _parse_single_pass_response("This is not JSON at all") is None

    def test_parse_returns_none_for_partial_json(self):
        assert _parse_single_pass_response('{"classification": {"document_type":') is None

    def test_parse_markdown_fenced_json(self):
        fenced = f"```json\n{self.VALID_RESPONSE}\n```"
        result = _parse_single_pass_response(fenced)
        assert result is not None
        assert result["classification"]["document_type"] == "invoice"

    def test_parse_markdown_fenced_without_language_tag(self):
        fenced = f"```\n{self.VALID_RESPONSE}\n```"
        result = _parse_single_pass_response(fenced)
        assert result is not None
        assert result["extraction"]["vendor"] == "Acme"

    def test_parse_markdown_fenced_with_surrounding_prose(self):
        text = f"Here is the result:\n\n```json\n{self.VALID_RESPONSE}\n```\n\nHope that helps!"
        result = _parse_single_pass_response(text)
        assert result is not None
        assert result["routing_decision"]["recommendation"] == "approve"

    def test_parse_returns_none_for_fenced_invalid_json(self):
        fenced = "```json\n{broken json\n```"
        assert _parse_single_pass_response(fenced) is None
