"""Tests for the multi-invoice intake bridge.

Two layers:

  1. ``split_email_attachments`` — pure logic: zero PDFs, one
     single-invoice PDF, multi-invoice PDF, multiple PDFs, mixed
     PDF + non-PDF attachments. The underlying
     ``multi_invoice_splitter.split_pdf_by_invoices`` is mocked.

  2. End-to-end through ``run_inline_gmail_triage``: when the
     splitter detects multiple invoices, the caller gets one
     ``multi_invoice_results`` entry per detected invoice and
     each entry has its own disambiguated email_id.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.services.multi_invoice_intake import (  # noqa: E402
    IntakeUnit,
    split_email_attachments,
)


# ---------------------------------------------------------------------------
# split_email_attachments — pure logic
# ---------------------------------------------------------------------------


class TestSplitEmailAttachments:
    def test_no_attachments_returns_one_empty_primary_unit(self):
        units = split_email_attachments([])
        assert len(units) == 1
        assert units[0].is_primary is True
        assert units[0].attachments == []

    def test_no_pdfs_only_text_attachment_returns_one_primary_unit(self):
        units = split_email_attachments([
            {"filename": "notes.txt", "mimeType": "text/plain", "data": b"hello"},
        ])
        assert len(units) == 1
        assert units[0].is_primary is True
        assert len(units[0].attachments) == 1
        assert units[0].attachments[0]["filename"] == "notes.txt"

    def test_single_pdf_with_one_invoice_returns_one_unit(self):
        # Splitter returns invoice_count=1 → no fan-out.
        with patch(
            "clearledgr.services.multi_invoice_splitter.split_pdf_by_invoices",
        ) as mock_split:
            mock_split.return_value = type("R", (), {
                "invoice_count": 1, "split_pdfs": [], "boundaries": [],
            })()
            units = split_email_attachments([
                {"filename": "invoice.pdf", "mimeType": "application/pdf", "data": b"%PDF-1.4 ..."},
            ])
        assert len(units) == 1
        assert units[0].is_primary is True
        # Original PDF kept on the unit unchanged.
        assert units[0].attachments[0]["filename"] == "invoice.pdf"

    def test_single_pdf_with_three_invoices_fans_out_to_three_units(self):
        boundary_a = type("B", (), {"invoice_number": "INV-001", "total_amount_text": "$100"})()
        boundary_b = type("B", (), {"invoice_number": "INV-002", "total_amount_text": "$200"})()
        boundary_c = type("B", (), {"invoice_number": "INV-003", "total_amount_text": "$300"})()
        with patch(
            "clearledgr.services.multi_invoice_splitter.split_pdf_by_invoices",
        ) as mock_split:
            mock_split.return_value = type("R", (), {
                "invoice_count": 3,
                "split_pdfs": [b"%PDF-A", b"%PDF-B", b"%PDF-C"],
                "boundaries": [boundary_a, boundary_b, boundary_c],
            })()
            units = split_email_attachments([
                {"filename": "stack.pdf", "mimeType": "application/pdf", "data": b"%PDF-1.4 ..."},
            ])
        assert len(units) == 3
        assert units[0].is_primary is True
        assert all(not u.is_primary for u in units[1:])
        assert units[0].hint_invoice_number == "INV-001"
        assert units[1].hint_invoice_number == "INV-002"
        assert units[2].hint_invoice_number == "INV-003"
        # Each sub-attachment is named informatively
        assert "split-1" in units[0].attachments[0]["filename"]
        assert "split-2" in units[1].attachments[0]["filename"]
        assert "split-3" in units[2].attachments[0]["filename"]

    def test_multiple_pdfs_each_with_one_invoice_fan_out_per_pdf(self):
        with patch(
            "clearledgr.services.multi_invoice_splitter.split_pdf_by_invoices",
        ) as mock_split:
            mock_split.return_value = type("R", (), {
                "invoice_count": 1, "split_pdfs": [], "boundaries": [],
            })()
            units = split_email_attachments([
                {"filename": "a.pdf", "mimeType": "application/pdf", "data": b"%PDF-A"},
                {"filename": "b.pdf", "mimeType": "application/pdf", "data": b"%PDF-B"},
            ])
        # Two PDFs → two units.
        assert len(units) == 2
        assert units[0].is_primary is True
        assert units[0].attachments[0]["filename"] == "a.pdf"
        assert units[1].attachments[0]["filename"] == "b.pdf"

    def test_pdf_plus_text_attachment_text_pinned_to_primary_unit(self):
        # Two PDFs + one text attachment. The text rides on the
        # primary unit so it isn't lost.
        with patch(
            "clearledgr.services.multi_invoice_splitter.split_pdf_by_invoices",
        ) as mock_split:
            mock_split.return_value = type("R", (), {
                "invoice_count": 1, "split_pdfs": [], "boundaries": [],
            })()
            units = split_email_attachments([
                {"filename": "a.pdf", "mimeType": "application/pdf", "data": b"%PDF-A"},
                {"filename": "b.pdf", "mimeType": "application/pdf", "data": b"%PDF-B"},
                {"filename": "notes.txt", "mimeType": "text/plain", "data": b"hi"},
            ])
        assert len(units) == 2
        # Primary has 2 attachments — its own PDF + the text rider.
        primary_filenames = [a.get("filename") for a in units[0].attachments]
        assert "a.pdf" in primary_filenames
        assert "notes.txt" in primary_filenames
        # Sub-unit only has its own PDF.
        assert [a["filename"] for a in units[1].attachments] == ["b.pdf"]

    def test_splitter_failure_keeps_original_pdf_as_one_unit(self):
        # Splitter raises → fallback: keep the original PDF as one
        # unit so the AP item still gets created.
        with patch(
            "clearledgr.services.multi_invoice_splitter.split_pdf_by_invoices",
            side_effect=RuntimeError("pdfplumber crashed"),
        ):
            units = split_email_attachments([
                {"filename": "broken.pdf", "mimeType": "application/pdf", "data": b"%PDF-1.4 ..."},
            ])
        assert len(units) == 1
        assert units[0].attachments[0]["filename"] == "broken.pdf"

    def test_pdf_without_data_skipped_gracefully(self):
        # Attachment with no data field → splitter can't process →
        # fall back to keeping it as one unit.
        with patch(
            "clearledgr.services.multi_invoice_splitter.split_pdf_by_invoices",
        ) as mock_split:
            mock_split.return_value = type("R", (), {
                "invoice_count": 1, "split_pdfs": [], "boundaries": [],
            })()
            units = split_email_attachments([
                {"filename": "empty.pdf", "mimeType": "application/pdf"},
            ])
        assert len(units) == 1


# ---------------------------------------------------------------------------
# End-to-end through run_inline_gmail_triage
# ---------------------------------------------------------------------------


class TestTriageMultiInvoiceFanout:
    """run_inline_gmail_triage detects multi-invoice and returns
    `multi_invoice_results` with one entry per detected invoice."""

    @pytest.mark.asyncio
    async def test_single_invoice_returns_no_multi_invoice_field(self):
        # Single PDF, single invoice → no fan-out, no
        # multi_invoice_results field on the result.
        from clearledgr.services.gmail_triage_service import run_inline_gmail_triage

        with patch(
            "clearledgr.services.multi_invoice_intake.split_email_attachments",
        ) as mock_split, patch(
            "clearledgr.services.gmail_triage_service._try_single_pass",
        ) as mock_sp:
            mock_split.return_value = [IntakeUnit(attachments=[{"filename": "a.pdf"}], is_primary=True)]
            mock_sp.return_value = None  # force fall-through to multi-call path
            with patch(
                "clearledgr.services.gmail_triage_service.classify_email_activity",
            ) as mock_classify:
                mock_classify.return_value = {"type": "noise"}
                result = await run_inline_gmail_triage(
                    payload={"email_id": "msg-1", "sender": "test@x.com", "subject": "X"},
                    org_id="default",
                    combined_text="",
                    attachments=[{"filename": "a.pdf"}],
                )
        assert "multi_invoice_results" not in result
        assert "multi_invoice_count" not in result

    @pytest.mark.asyncio
    async def test_three_invoices_fan_out_into_three_results(self):
        from clearledgr.services.gmail_triage_service import run_inline_gmail_triage

        units = [
            IntakeUnit(attachments=[{"filename": "stack.split-1.pdf"}], hint_invoice_number="INV-001", is_primary=True),
            IntakeUnit(attachments=[{"filename": "stack.split-2.pdf"}], hint_invoice_number="INV-002"),
            IntakeUnit(attachments=[{"filename": "stack.split-3.pdf"}], hint_invoice_number="INV-003"),
        ]

        with patch(
            "clearledgr.services.multi_invoice_intake.split_email_attachments",
            return_value=units,
        ), patch(
            "clearledgr.services.gmail_triage_service._try_single_pass",
            return_value=None,
        ), patch(
            "clearledgr.services.gmail_triage_service.classify_email_activity",
            return_value={"type": "noise"},
        ):
            result = await run_inline_gmail_triage(
                payload={"email_id": "msg-1", "sender": "test@x.com", "subject": "X"},
                org_id="default",
                combined_text="",
                attachments=[{"filename": "stack.pdf"}],
            )

        assert result.get("multi_invoice_count") == 3
        assert len(result.get("multi_invoice_results") or []) == 3
        assert result.get("multi_invoice_strategy") == "splitter_fanout"
        # Sub-unit email_ids are disambiguated.
        sub_ids = [r.get("email_id") for r in result["multi_invoice_results"]]
        assert sub_ids[0] == "msg-1"
        assert sub_ids[1] == "msg-1::split-1"
        assert sub_ids[2] == "msg-1::split-2"

    @pytest.mark.asyncio
    async def test_splitter_invoice_number_hint_grafted_when_extraction_missing(self):
        # When the per-unit triage extraction doesn't include an
        # invoice_number but the splitter detected one, the hint
        # gets surfaced so it isn't lost.

        units = [
            IntakeUnit(attachments=[{"filename": "a.split-1.pdf"}], hint_invoice_number="INV-A", is_primary=True),
            IntakeUnit(attachments=[{"filename": "a.split-2.pdf"}], hint_invoice_number="INV-B"),
        ]

        sub_results = [
            # First sub-result: extraction.invoice_number is empty,
            # so the hint should fill it in.
            {
                "email_id": "msg-1",
                "extraction": {"invoice_number": None, "vendor": "Acme"},
            },
            {
                "email_id": "msg-1::split-1",
                "extraction": {"invoice_number": "INV-OVERRIDE", "vendor": "Acme"},
            },
        ]
        with patch(
            "clearledgr.services.multi_invoice_intake.split_email_attachments",
            return_value=units,
        ), patch(
            "clearledgr.services.gmail_triage_service.run_inline_gmail_triage",
            wraps=lambda *a, **kw: sub_results.pop(0) if kw.get("_is_subunit") else None,
        ):
            # The wraps trick is awkward; cleaner: use the real
            # function but stub everything underneath. For now, this
            # pinning test is OK as a smoke test — the recursion is
            # caught by the _is_subunit branch in real use.
            pass
        # This test stays simple: assert the grafting logic works
        # by calling _fan_out_multi_invoice directly with mocked
        # sub-results.
        from clearledgr.services.gmail_triage_service import _fan_out_multi_invoice

        async def _stub_subunit(**kwargs):
            # First call returns extraction without invoice_number
            return {
                "email_id": kwargs["payload"]["email_id"],
                "extraction": {"invoice_number": None, "vendor": "Acme"},
            }

        with patch(
            "clearledgr.services.gmail_triage_service.run_inline_gmail_triage",
            side_effect=_stub_subunit,
        ):
            primary = await _fan_out_multi_invoice(
                units=units,
                payload={"email_id": "msg-1"},
                org_id="default",
                combined_text="",
                agent_reasoning_fn=None,
            )
        assert primary["multi_invoice_count"] == 2
        # Primary's invoice_number got grafted from the splitter hint.
        assert primary["extraction"]["invoice_number"] == "INV-A"
        # Second result also got its hint grafted.
        sub2 = primary["multi_invoice_results"][1]
        assert sub2["extraction"]["invoice_number"] == "INV-B"
