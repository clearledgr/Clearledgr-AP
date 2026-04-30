"""Tests for the single-pass invoice processor.

Three layers under test:

  1. ``_build_single_pass_prompt`` — prompt construction (deterministic).
  2. ``_parse_single_pass_response`` — JSON / markdown-fence handling.
  3. ``_validate_response`` — required-field schema gate that drops
     drifted Claude output before it reaches downstream consumers.
  4. ``process_invoice_single_pass`` — end-to-end with mocked Claude
     responses through the LLM Gateway: happy path, missing required
     field, malformed JSON, gateway error.

All Claude calls are mocked at the LLM Gateway boundary.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.services.single_pass_processor import (  # noqa: E402
    MAX_VISUAL_ATTACHMENTS,
    _build_single_pass_prompt,
    _parse_single_pass_response,
    _validate_response,
    process_invoice_single_pass,
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

    def test_prompt_includes_required_authoritative_schema_keys(self):
        # Authoritative output: classification + extraction.
        prompt = _build_single_pass_prompt(
            subject="Test", sender="test@test.com", body="body",
        )
        assert "classification" in prompt
        assert "extraction" in prompt

    def test_prompt_includes_advisory_schema_keys(self):
        # Advisory hints: gl_coding, duplicate_analysis, risk_assessment.
        prompt = _build_single_pass_prompt(
            subject="Test", sender="test@test.com", body="body",
        )
        for key in ("gl_coding", "duplicate_analysis", "risk_assessment"):
            assert key in prompt

    def test_prompt_does_not_request_routing_decision(self):
        # APDecisionService is the canonical decision-maker; the
        # single-pass response must NOT include a routing recommendation.
        prompt = _build_single_pass_prompt(
            subject="Test", sender="test@test.com", body="body",
        )
        assert "routing_decision" not in prompt
        assert "recommendation" not in prompt

    def test_prompt_marks_advisory_fields_explicitly(self):
        prompt = _build_single_pass_prompt(
            subject="Test", sender="test@test.com", body="body",
        )
        # The contract is loud: hints, not authoritative.
        assert "advisory" in prompt.lower()
        assert "authoritative" in prompt.lower()

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
            subject="Test", sender="test@test.com", body="body",
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
            subject="Test", sender="test@test.com", body="body",
        )
        assert "untrusted" in prompt.lower()


# ---------------------------------------------------------------------------
# _parse_single_pass_response
# ---------------------------------------------------------------------------


_VALID_RESPONSE_DICT = {
    "classification": {
        "document_type": "invoice",
        "confidence": 0.95,
        "reasoning": "Vendor bill with PO reference",
    },
    "extraction": {
        "vendor": "Acme Supplies",
        "amount": 1234.50,
        "currency": "USD",
        "invoice_number": "INV-001",
        "invoice_date": "2026-04-15",
        "due_date": "2026-05-15",
        "po_number": "PO-9001",
        "payment_terms": "Net 30",
        "tax_amount": 100.0,
        "subtotal": 1134.50,
        "line_items": [],
        "bank_details": {"bank_name": None, "account_number": None, "iban": None, "swift": None},
        "field_confidences": {"vendor": 0.99, "amount": 0.97, "invoice_number": 0.99, "due_date": 0.95},
        "overall_confidence": 0.96,
    },
    "gl_coding": {"suggested_gl_code": "6000", "reasoning": "Office supplies"},
    "duplicate_analysis": {"is_duplicate": False, "is_amendment": False, "supersedes_reference": None, "reasoning": "no match"},
    "risk_assessment": {"fraud_risk": "none", "fraud_signals": [], "amount_anomaly": "none", "amount_reasoning": "in range"},
}

_VALID_RESPONSE = json.dumps(_VALID_RESPONSE_DICT)


class TestParseSinglePassResponse:
    """JSON / markdown-fence handling."""

    def test_parse_valid_json(self):
        result = _parse_single_pass_response(_VALID_RESPONSE)
        assert result is not None
        assert result["classification"]["document_type"] == "invoice"
        assert result["extraction"]["vendor"] == "Acme Supplies"

    def test_parse_returns_none_for_empty_string(self):
        assert _parse_single_pass_response("") is None

    def test_parse_returns_none_for_none_input(self):
        assert _parse_single_pass_response(None) is None

    def test_parse_returns_none_for_invalid_json(self):
        assert _parse_single_pass_response("This is not JSON at all") is None

    def test_parse_returns_none_for_partial_json(self):
        assert _parse_single_pass_response('{"classification": {"document_type":') is None

    def test_parse_markdown_fenced_json(self):
        fenced = f"```json\n{_VALID_RESPONSE}\n```"
        result = _parse_single_pass_response(fenced)
        assert result is not None
        assert result["classification"]["document_type"] == "invoice"

    def test_parse_markdown_fenced_without_language_tag(self):
        fenced = f"```\n{_VALID_RESPONSE}\n```"
        result = _parse_single_pass_response(fenced)
        assert result is not None
        assert result["extraction"]["vendor"] == "Acme Supplies"

    def test_parse_markdown_fenced_with_surrounding_prose(self):
        text = f"Here is the result:\n\n```json\n{_VALID_RESPONSE}\n```\n\nHope that helps!"
        result = _parse_single_pass_response(text)
        assert result is not None
        assert result["classification"]["confidence"] == 0.95

    def test_parse_returns_none_for_fenced_invalid_json(self):
        fenced = "```json\n{broken json\n```"
        assert _parse_single_pass_response(fenced) is None


# ---------------------------------------------------------------------------
# _validate_response — schema gate
# ---------------------------------------------------------------------------


class TestValidateResponse:
    """The required-field gate that drops drifted Claude output."""

    def test_valid_response_passes(self):
        assert _validate_response(_VALID_RESPONSE_DICT) is None

    def test_missing_classification_dict_fails(self):
        bad = {**_VALID_RESPONSE_DICT}
        del bad["classification"]
        assert _validate_response(bad) is not None
        assert "classification" in _validate_response(bad)

    def test_missing_classification_document_type_fails(self):
        bad = json.loads(_VALID_RESPONSE)
        del bad["classification"]["document_type"]
        err = _validate_response(bad)
        assert err is not None
        assert "document_type" in err

    def test_classification_confidence_wrong_type_fails(self):
        bad = json.loads(_VALID_RESPONSE)
        bad["classification"]["confidence"] = "high"
        err = _validate_response(bad)
        assert err is not None
        assert "confidence" in err

    def test_extraction_amount_can_be_none(self):
        # Amount is nullable per the schema (e.g. for credit-note
        # detection where the amount sign matters less than the type).
        ok = json.loads(_VALID_RESPONSE)
        ok["extraction"]["amount"] = None
        assert _validate_response(ok) is None

    def test_extraction_vendor_can_be_none(self):
        ok = json.loads(_VALID_RESPONSE)
        ok["extraction"]["vendor"] = None
        assert _validate_response(ok) is None

    def test_top_level_not_dict_fails(self):
        assert _validate_response([]) is not None
        assert _validate_response("string") is not None

    def test_extraction_overall_confidence_required(self):
        bad = json.loads(_VALID_RESPONSE)
        del bad["extraction"]["overall_confidence"]
        err = _validate_response(bad)
        assert err is not None
        assert "overall_confidence" in err


# ---------------------------------------------------------------------------
# process_invoice_single_pass — end-to-end with mocked LLM Gateway
# ---------------------------------------------------------------------------


def _fake_llm_response(text: str):
    """Build a fake LLMResponse-shaped object the gateway returns."""
    class _Resp:
        def __init__(self, content):
            self.content = content
    return _Resp(text)


@pytest.mark.asyncio
async def test_process_invoice_single_pass_happy_path(monkeypatch):
    """Valid response from Claude flows through, gets validated, returns
    parsed result with the processing_mode marker."""
    fake_gateway = AsyncMock()
    fake_gateway.call = AsyncMock(return_value=_fake_llm_response(_VALID_RESPONSE))
    with patch(
        "clearledgr.services.single_pass_processor.get_llm_gateway",
        return_value=fake_gateway,
    ):
        result = await process_invoice_single_pass(
            subject="Invoice INV-001",
            sender="billing@acme.com",
            body="See attached.",
        )
    assert result is not None
    assert result["classification"]["document_type"] == "invoice"
    assert result["extraction"]["vendor"] == "Acme Supplies"
    assert result["processing_mode"] == "single_pass"
    assert result["api_calls"] == 1
    # Routing decision must NOT be in the result — it's not in the schema.
    assert "routing_decision" not in result


@pytest.mark.asyncio
async def test_process_invoice_single_pass_returns_none_on_missing_required_field(monkeypatch):
    """Schema validation drops drifted output and triggers fallback."""
    drifted = json.loads(_VALID_RESPONSE)
    del drifted["classification"]["document_type"]
    fake_gateway = AsyncMock()
    fake_gateway.call = AsyncMock(
        return_value=_fake_llm_response(json.dumps(drifted)),
    )
    with patch(
        "clearledgr.services.single_pass_processor.get_llm_gateway",
        return_value=fake_gateway,
    ):
        result = await process_invoice_single_pass(
            subject="Test", sender="test@test.com", body="body",
        )
    assert result is None


@pytest.mark.asyncio
async def test_process_invoice_single_pass_returns_none_on_malformed_json(monkeypatch):
    fake_gateway = AsyncMock()
    fake_gateway.call = AsyncMock(
        return_value=_fake_llm_response("this is not json"),
    )
    with patch(
        "clearledgr.services.single_pass_processor.get_llm_gateway",
        return_value=fake_gateway,
    ):
        result = await process_invoice_single_pass(
            subject="Test", sender="test@test.com", body="body",
        )
    assert result is None


@pytest.mark.asyncio
async def test_process_invoice_single_pass_returns_none_on_gateway_error(monkeypatch):
    """Any exception from the gateway must be swallowed and surface as
    None — never raise from this module."""
    fake_gateway = AsyncMock()
    fake_gateway.call = AsyncMock(side_effect=RuntimeError("anthropic 500"))
    with patch(
        "clearledgr.services.single_pass_processor.get_llm_gateway",
        return_value=fake_gateway,
    ):
        result = await process_invoice_single_pass(
            subject="Test", sender="test@test.com", body="body",
        )
    assert result is None


@pytest.mark.asyncio
async def test_process_invoice_single_pass_returns_none_on_empty_response(monkeypatch):
    fake_gateway = AsyncMock()
    fake_gateway.call = AsyncMock(return_value=_fake_llm_response(""))
    with patch(
        "clearledgr.services.single_pass_processor.get_llm_gateway",
        return_value=fake_gateway,
    ):
        result = await process_invoice_single_pass(
            subject="Test", sender="test@test.com", body="body",
        )
    assert result is None


@pytest.mark.asyncio
async def test_process_invoice_single_pass_handles_markdown_fenced_response(monkeypatch):
    """Claude occasionally returns ```json ... ``` even when the prompt
    asks for raw JSON. The processor must accept both."""
    fenced = f"```json\n{_VALID_RESPONSE}\n```"
    fake_gateway = AsyncMock()
    fake_gateway.call = AsyncMock(return_value=_fake_llm_response(fenced))
    with patch(
        "clearledgr.services.single_pass_processor.get_llm_gateway",
        return_value=fake_gateway,
    ):
        result = await process_invoice_single_pass(
            subject="Test", sender="test@test.com", body="body",
        )
    assert result is not None
    assert result["classification"]["document_type"] == "invoice"


class TestAttachmentTextPlumbing:
    """The bridge between gmail_triage_service and single-pass —
    `_collect_attachment_text` produces the `attachment_text` that
    flows into the single-pass prompt. Used to be hard-coded `""`
    with a TODO; the tests below pin the corrected behaviour so a
    future refactor can't silently drop attachment text again.
    """

    def test_collect_attachment_text_returns_empty_for_no_attachments(self):
        from clearledgr.services.gmail_triage_service import _collect_attachment_text
        assert _collect_attachment_text([]) == ""
        assert _collect_attachment_text(None) == ""

    def test_collect_attachment_text_returns_empty_when_no_content_text(self):
        from clearledgr.services.gmail_triage_service import _collect_attachment_text
        assert _collect_attachment_text([{"filename": "scan.png"}]) == ""
        assert _collect_attachment_text([{"filename": "x.pdf", "content_text": ""}]) == ""

    def test_collect_attachment_text_tags_each_excerpt_with_filename(self):
        from clearledgr.services.gmail_triage_service import _collect_attachment_text
        out = _collect_attachment_text([
            {"filename": "invoice.pdf", "content_text": "INV-9001\nAmount: $500"},
            {"name": "remit.txt", "content_text": "Payment ref ABC123"},
        ])
        assert "--- invoice.pdf ---" in out
        assert "--- remit.txt ---" in out
        assert "INV-9001" in out
        assert "Payment ref ABC123" in out

    def test_collect_attachment_text_caps_excerpts_at_4000_chars(self):
        from clearledgr.services.gmail_triage_service import _collect_attachment_text
        big = "X" * 5000
        out = _collect_attachment_text([{"filename": "huge.pdf", "content_text": big}])
        assert "...[truncated]" in out
        # Filename header + 4000-char body + truncation marker — comfortably
        # under 5000 total even with the header overhead.
        assert len(out) < 5000

    def test_collect_attachment_text_skips_non_dict_entries(self):
        from clearledgr.services.gmail_triage_service import _collect_attachment_text
        assert _collect_attachment_text([None, "not a dict", 42]) == ""

    def test_collect_attachment_text_falls_back_to_default_filename(self):
        from clearledgr.services.gmail_triage_service import _collect_attachment_text
        out = _collect_attachment_text([{"content_text": "no filename here"}])
        assert "--- attachment ---" in out
        assert "no filename here" in out


def test_single_pass_action_config_has_sufficient_output_budget():
    """Pin the action-registry config for SINGLE_PASS_EXTRACT.

    The schema (classification + extraction with line_items +
    field_confidences + 3 advisory blocks) realistically produces
    1800-5000 tokens depending on invoice complexity. The output
    budget MUST exceed EXTRACT_INVOICE_FIELDS (extraction alone)
    since single-pass also does classification + advisory analysis,
    and the timeout MUST allow for the larger response. If a future
    refactor lowers either, this test trips before drift reaches
    production.
    """
    from clearledgr.core.llm_gateway import ACTION_REGISTRY, LLMAction

    sp_config = ACTION_REGISTRY[LLMAction.SINGLE_PASS_EXTRACT]
    extract_config = ACTION_REGISTRY[LLMAction.EXTRACT_INVOICE_FIELDS]

    assert sp_config.max_output_tokens >= extract_config.max_output_tokens, (
        f"SINGLE_PASS_EXTRACT must have at least as much output budget as "
        f"EXTRACT_INVOICE_FIELDS (got {sp_config.max_output_tokens} vs "
        f"{extract_config.max_output_tokens})"
    )
    assert sp_config.max_output_tokens >= 4000, (
        f"SINGLE_PASS_EXTRACT output budget ({sp_config.max_output_tokens}) "
        "is below the 4000-token floor needed for line-itemised invoices"
    )
    assert sp_config.timeout_seconds >= 60, (
        f"SINGLE_PASS_EXTRACT timeout ({sp_config.timeout_seconds}s) is "
        "too tight for the larger response size"
    )
    assert sp_config.model_tier == "sonnet", (
        "SINGLE_PASS_EXTRACT must run on the sonnet tier — haiku quality "
        "is insufficient for the composite analysis"
    )


@pytest.mark.asyncio
async def test_process_invoice_single_pass_truncates_visual_attachments(monkeypatch):
    """More than MAX_VISUAL_ATTACHMENTS attachments — the call still
    succeeds but only the cap-many are forwarded; truncation is
    logged, not silent."""
    fake_gateway = AsyncMock()
    fake_gateway.call = AsyncMock(return_value=_fake_llm_response(_VALID_RESPONSE))
    with patch(
        "clearledgr.services.single_pass_processor.get_llm_gateway",
        return_value=fake_gateway,
    ):
        # Five attachments — more than the cap (3).
        attachments = [
            {"data": "AAAA", "mimeType": "application/pdf"} for _ in range(5)
        ]
        result = await process_invoice_single_pass(
            subject="Multi-PDF email",
            sender="test@test.com",
            body="body",
            has_visual_attachments=True,
            visual_attachments=attachments,
        )
    assert result is not None
    # Verify the gateway was called with at most MAX_VISUAL_ATTACHMENTS
    # image content blocks (plus one text content block for the prompt).
    args, kwargs = fake_gateway.call.call_args
    messages = kwargs.get("messages") or args[1]
    content = messages[0]["content"]
    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) <= MAX_VISUAL_ATTACHMENTS
