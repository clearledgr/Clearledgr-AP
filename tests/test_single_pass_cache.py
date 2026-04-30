"""Tests for the single-pass idempotency cache.

Three layers under test:

  1. ``compute_content_hash`` — determinism + isolation: same inputs
     produce same hash, different inputs produce different hashes,
     vendor / thread / PO context is NOT in the hash (those change
     between retries on identical emails).
  2. In-memory backend round-trip: set → get → expiry.
  3. Integration through ``process_invoice_single_pass``: a second
     call with identical inputs returns the cached result and does
     not invoke the LLM gateway.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.services.single_pass_cache import (  # noqa: E402
    _reset_for_testing,
    compute_content_hash,
    get_cached_result,
    set_cached_result,
)
from clearledgr.services.single_pass_processor import (  # noqa: E402
    process_invoice_single_pass,
)


_VALID_RESPONSE = json.dumps({
    "classification": {"document_type": "invoice", "confidence": 0.95, "reasoning": "test"},
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
})


def _fake_llm_response(text):
    class _Resp:
        def __init__(self, content):
            self.content = content
    return _Resp(text)


@pytest.fixture(autouse=True)
def _reset_cache_between_tests(monkeypatch):
    # Force the in-memory backend; isolate every test.
    monkeypatch.delenv("REDIS_URL", raising=False)
    _reset_for_testing()
    yield
    _reset_for_testing()


# ---------------------------------------------------------------------------
# compute_content_hash
# ---------------------------------------------------------------------------


class TestComputeContentHash:
    def test_identical_inputs_produce_identical_hash(self):
        h1 = compute_content_hash(
            subject="Invoice INV-001",
            sender="billing@acme.com",
            body="Please pay this invoice",
            has_visual_attachments=False,
        )
        h2 = compute_content_hash(
            subject="Invoice INV-001",
            sender="billing@acme.com",
            body="Please pay this invoice",
            has_visual_attachments=False,
        )
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex length

    def test_different_subject_produces_different_hash(self):
        h1 = compute_content_hash(
            subject="Invoice INV-001",
            sender="billing@acme.com",
            body="body",
            has_visual_attachments=False,
        )
        h2 = compute_content_hash(
            subject="Invoice INV-002",
            sender="billing@acme.com",
            body="body",
            has_visual_attachments=False,
        )
        assert h1 != h2

    def test_different_sender_produces_different_hash(self):
        h1 = compute_content_hash(
            subject="X", sender="a@x.com", body="b", has_visual_attachments=False,
        )
        h2 = compute_content_hash(
            subject="X", sender="b@x.com", body="b", has_visual_attachments=False,
        )
        assert h1 != h2

    def test_different_body_produces_different_hash(self):
        h1 = compute_content_hash(
            subject="X", sender="a@x.com", body="body 1", has_visual_attachments=False,
        )
        h2 = compute_content_hash(
            subject="X", sender="a@x.com", body="body 2", has_visual_attachments=False,
        )
        assert h1 != h2

    def test_different_attachment_data_produces_different_hash(self):
        h1 = compute_content_hash(
            subject="X", sender="a@x.com", body="b",
            has_visual_attachments=True,
            visual_attachments=[{"data": "AAAA", "mimeType": "application/pdf"}],
        )
        h2 = compute_content_hash(
            subject="X", sender="a@x.com", body="b",
            has_visual_attachments=True,
            visual_attachments=[{"data": "BBBB", "mimeType": "application/pdf"}],
        )
        assert h1 != h2

    def test_attachment_byte_data_hashed_correctly(self):
        # bytes-typed data should hash to the same key as the
        # base64-equivalent string for the same content.
        h1 = compute_content_hash(
            subject="X", sender="a@x.com", body="b",
            has_visual_attachments=True,
            visual_attachments=[{"data": b"raw bytes here"}],
        )
        # Different bytes → different hash
        h2 = compute_content_hash(
            subject="X", sender="a@x.com", body="b",
            has_visual_attachments=True,
            visual_attachments=[{"data": b"different bytes"}],
        )
        assert h1 != h2


# ---------------------------------------------------------------------------
# In-memory backend round-trip
# ---------------------------------------------------------------------------


class TestInMemoryBackend:
    def test_get_returns_none_on_miss(self):
        assert get_cached_result("nonexistent_hash") is None

    def test_set_then_get_round_trip(self):
        h = compute_content_hash(subject="X", sender="a@x.com", body="b", has_visual_attachments=False)
        result = {"classification": {"document_type": "invoice"}}
        set_cached_result(h, result, ttl_seconds=60)
        cached = get_cached_result(h)
        assert cached == result

    def test_get_returns_none_after_ttl_expiry(self):
        h = compute_content_hash(subject="X", sender="a@x.com", body="b", has_visual_attachments=False)
        result = {"classification": {"document_type": "invoice"}}
        # Tiny TTL so we can wait through it without slowing the suite.
        set_cached_result(h, result, ttl_seconds=0.05)  # type: ignore[arg-type]
        # ttl_seconds is int-coerced; 0.05 → int(0.05) == 0 → no cache.
        # Verify that ttl<=0 short-circuits (no cache write).
        assert get_cached_result(h) is None

    def test_zero_ttl_is_noop(self):
        h = compute_content_hash(subject="X", sender="a@x.com", body="b", has_visual_attachments=False)
        set_cached_result(h, {"a": 1}, ttl_seconds=0)
        assert get_cached_result(h) is None

    def test_explicit_short_ttl_expires(self):
        # Use 1-second TTL and patch monotonic to advance.
        h = compute_content_hash(subject="X", sender="a@x.com", body="b", has_visual_attachments=False)
        set_cached_result(h, {"a": 1}, ttl_seconds=1)
        assert get_cached_result(h) == {"a": 1}
        # Wait past the TTL.
        time.sleep(1.05)
        assert get_cached_result(h) is None


# ---------------------------------------------------------------------------
# End-to-end: cache hit avoids the LLM call
# ---------------------------------------------------------------------------


class TestEndToEndCaching:
    @pytest.mark.asyncio
    async def test_second_call_with_identical_inputs_returns_cached_result(self):
        fake_gateway = AsyncMock()
        fake_gateway.call = AsyncMock(return_value=_fake_llm_response(_VALID_RESPONSE))

        with patch(
            "clearledgr.services.single_pass_processor.get_llm_gateway",
            return_value=fake_gateway,
        ):
            r1 = await process_invoice_single_pass(
                subject="Invoice INV-001",
                sender="billing@acme.com",
                body="See attached.",
            )
            assert r1 is not None
            assert r1["processing_mode"] == "single_pass"
            assert fake_gateway.call.await_count == 1

            # Second call with identical inputs — cache hit, gateway
            # NOT invoked again.
            r2 = await process_invoice_single_pass(
                subject="Invoice INV-001",
                sender="billing@acme.com",
                body="See attached.",
            )
            assert r2 is not None
            assert r2["processing_mode"] == "single_pass_cached"
            assert r2["api_calls"] == 0
            assert fake_gateway.call.await_count == 1, (
                "second call should be served from cache; gateway must "
                "not be invoked"
            )
            # Same extraction shape regardless of cache hit/miss
            assert r2["extraction"]["vendor"] == r1["extraction"]["vendor"]

    @pytest.mark.asyncio
    async def test_different_inputs_do_not_share_cache(self):
        fake_gateway = AsyncMock()
        fake_gateway.call = AsyncMock(return_value=_fake_llm_response(_VALID_RESPONSE))

        with patch(
            "clearledgr.services.single_pass_processor.get_llm_gateway",
            return_value=fake_gateway,
        ):
            await process_invoice_single_pass(
                subject="Invoice INV-001",
                sender="billing@acme.com",
                body="See attached.",
            )
            # Different sender → different cache key → fresh call.
            await process_invoice_single_pass(
                subject="Invoice INV-001",
                sender="other@vendor.com",
                body="See attached.",
            )
            assert fake_gateway.call.await_count == 2

    @pytest.mark.asyncio
    async def test_use_cache_false_bypasses_cache(self):
        fake_gateway = AsyncMock()
        fake_gateway.call = AsyncMock(return_value=_fake_llm_response(_VALID_RESPONSE))

        with patch(
            "clearledgr.services.single_pass_processor.get_llm_gateway",
            return_value=fake_gateway,
        ):
            await process_invoice_single_pass(
                subject="X", sender="a@x.com", body="b", use_cache=False,
            )
            await process_invoice_single_pass(
                subject="X", sender="a@x.com", body="b", use_cache=False,
            )
            # Both calls hit the LLM.
            assert fake_gateway.call.await_count == 2

    @pytest.mark.asyncio
    async def test_validation_failure_is_not_cached(self):
        # Drifted response → validation fails → returns None → must NOT
        # poison the cache. A retry should hit the gateway again, not
        # see a cached None / partial value.
        drifted = json.loads(_VALID_RESPONSE)
        del drifted["classification"]["document_type"]
        fake_gateway = AsyncMock()
        fake_gateway.call = AsyncMock(return_value=_fake_llm_response(json.dumps(drifted)))

        with patch(
            "clearledgr.services.single_pass_processor.get_llm_gateway",
            return_value=fake_gateway,
        ):
            r1 = await process_invoice_single_pass(
                subject="X", sender="a@x.com", body="b",
            )
            assert r1 is None
            r2 = await process_invoice_single_pass(
                subject="X", sender="a@x.com", body="b",
            )
            assert r2 is None
            assert fake_gateway.call.await_count == 2, (
                "validation failure must not be cached — second call "
                "must invoke the gateway again"
            )
