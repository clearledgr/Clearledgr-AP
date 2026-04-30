"""Tests for Wave 5 / G3 — multi-invoice PDF splitter.

Covers the deterministic boundary-detection logic on per-page text
without requiring a real PDF binary. The pdfplumber/pypdf I/O layer
is exercised end-to-end via a small integration test using a real
PDF generated with pypdf.

Boundary heuristic:
  * Single-invoice PDF: one boundary spanning all pages.
  * Three-invoice PDF (each on one page): three boundaries.
  * Multi-page invoice with continuation marker on page 2: pages
    grouped under the first invoice.
  * Mixed: invoice + continuation + new invoice + new invoice.
  * No invoice markers anywhere: single fallback boundary.
  * International markers (Rechnung, Facture, Factura) detected.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import multi_invoice_split as split_routes  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.multi_invoice_splitter import (  # noqa: E402
    detect_invoice_boundaries,
    split_pdf_by_invoices,
    write_pdf_subset,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def client_orgA():
    app = FastAPI()
    app.include_router(split_routes.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        user_id="user-1", email="op@orgA.com",
        organization_id="orgA", role="user",
    )
    return TestClient(app)


# ─── Boundary detection ────────────────────────────────────────────


_INVOICE_PAGE_1 = (
    "ACME Corp Ltd\n"
    "Invoice No: INV-2026-001\n"
    "Date: 2026-04-29\n"
    "Item A: 5 x 100.00 = 500.00\n"
    "Total Due: GBP 500.00"
)


_INVOICE_PAGE_2_CONT = (
    "Page 2 of 2\n"
    "ACME Corp Ltd\n"
    "Notes: Net 30 days payment terms\n"
    "Bank: HSBC, Account: ************1234"
)


_INVOICE_PAGE_2 = (
    "Beta Vendor SARL\n"
    "Invoice Number: INV-2026-002\n"
    "Total Amount: EUR 1,200.00"
)


_INVOICE_PAGE_3 = (
    "Gamma GmbH\n"
    "Rechnung Nr.: R-2026-100\n"
    "Gesamtbetrag: EUR 850.00"
)


_INVOICE_FRENCH = (
    "Vendor FR SARL\n"
    "Facture N°: F-2026-50\n"
    "Montant Total: EUR 750.00"
)


def test_single_invoice_single_page():
    boundaries = detect_invoice_boundaries([_INVOICE_PAGE_1])
    assert len(boundaries) == 1
    assert boundaries[0].start_page == 0
    assert boundaries[0].end_page == 0
    assert boundaries[0].invoice_number == "INV-2026-001"


def test_three_separate_invoices_three_boundaries():
    boundaries = detect_invoice_boundaries([
        _INVOICE_PAGE_1, _INVOICE_PAGE_2, _INVOICE_PAGE_3,
    ])
    assert len(boundaries) == 3
    assert [b.start_page for b in boundaries] == [0, 1, 2]
    assert [b.end_page for b in boundaries] == [0, 1, 2]
    invoice_ids = [b.invoice_number for b in boundaries]
    assert "INV-2026-001" in invoice_ids
    assert "INV-2026-002" in invoice_ids
    assert "R-2026-100" in invoice_ids


def test_continuation_page_groups_with_prior():
    boundaries = detect_invoice_boundaries([
        _INVOICE_PAGE_1, _INVOICE_PAGE_2_CONT, _INVOICE_PAGE_2,
    ])
    assert len(boundaries) == 2
    assert boundaries[0].start_page == 0 and boundaries[0].end_page == 1
    assert boundaries[1].start_page == 2 and boundaries[1].end_page == 2


def test_mixed_invoice_and_continuation():
    pages = [
        _INVOICE_PAGE_1,                 # 0: start of inv 1
        _INVOICE_PAGE_2_CONT,            # 1: continuation
        _INVOICE_PAGE_2,                 # 2: start of inv 2
        _INVOICE_PAGE_3,                 # 3: start of inv 3
    ]
    boundaries = detect_invoice_boundaries(pages)
    assert len(boundaries) == 3
    assert boundaries[0].page_count == 2
    assert boundaries[1].page_count == 1
    assert boundaries[2].page_count == 1


def test_no_markers_returns_single_fallback_boundary():
    pages = [
        "Cover page",
        "Just some text about a vendor",
        "Bank details and signature",
    ]
    boundaries = detect_invoice_boundaries(pages)
    assert len(boundaries) == 1
    assert boundaries[0].start_page == 0
    assert boundaries[0].end_page == 2


def test_german_rechnung_detected():
    boundaries = detect_invoice_boundaries([_INVOICE_PAGE_3])
    assert len(boundaries) == 1
    assert boundaries[0].invoice_number == "R-2026-100"


def test_french_facture_detected():
    boundaries = detect_invoice_boundaries([_INVOICE_FRENCH])
    assert len(boundaries) == 1
    assert boundaries[0].invoice_number == "F-2026-50"


def test_total_amount_text_extracted():
    boundaries = detect_invoice_boundaries([_INVOICE_PAGE_1])
    assert boundaries[0].total_amount_text is not None
    assert "500.00" in boundaries[0].total_amount_text


def test_empty_pages_yields_empty_boundaries():
    assert detect_invoice_boundaries([]) == []


def test_invoice_id_without_total_skipped_unless_first_page():
    """A page with only an invoice id but no total marker should
    NOT spawn a new boundary mid-document — it's likely a header
    fragment, not an invoice start. First-page exception still
    applies so we don't lose single-page invoices."""
    pages = [
        _INVOICE_PAGE_1,                       # 0: full invoice
        "Invoice Number: 999\n(no total here)",  # 1: id only — skipped
    ]
    boundaries = detect_invoice_boundaries(pages)
    assert len(boundaries) == 1
    assert boundaries[0].end_page == 1


# ─── Integration: round-trip a real PDF through pypdf ──────────────


def _make_test_pdf_with_pages(page_texts: list) -> bytes:
    """Build a tiny PDF where each page contains the provided text.

    Uses pypdf's PageObject + a hand-built content stream because we
    don't have reportlab. The result is parseable by pdfplumber.
    """
    from pypdf import PdfWriter
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject, NameObject,
    )

    writer = PdfWriter()
    for text in page_texts:
        # Escape for inclusion in a content stream
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        # Break into lines for Tj operators
        commands_lines: list[str] = []
        commands_lines.append("BT")
        commands_lines.append("/F1 11 Tf")
        commands_lines.append("50 750 Td")
        for line in safe.split("\n"):
            commands_lines.append(f"({line}) Tj")
            commands_lines.append("0 -14 Td")
        commands_lines.append("ET")
        commands = "\n".join(commands_lines).encode("latin-1")

        page = writer.add_blank_page(width=595, height=842)
        # Inject a font + content stream.
        font_obj = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        font_ref = writer._add_object(font_obj)
        resources = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): font_ref,
            }),
        })
        page[NameObject("/Resources")] = resources
        content_stream = DecodedStreamObject()
        content_stream.set_data(commands)
        content_ref = writer._add_object(content_stream)
        page[NameObject("/Contents")] = content_ref

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_split_pdf_end_to_end_three_invoices():
    pdf = _make_test_pdf_with_pages([
        _INVOICE_PAGE_1, _INVOICE_PAGE_2, _INVOICE_PAGE_3,
    ])
    result = split_pdf_by_invoices(pdf)
    assert result.page_count == 3
    assert result.invoice_count == 3
    assert len(result.split_pdfs) == 3
    # Each sub-PDF contains exactly one page.
    from pypdf import PdfReader
    for sub in result.split_pdfs:
        assert sub  # non-empty
        sub_reader = PdfReader(io.BytesIO(sub))
        assert len(sub_reader.pages) == 1


def test_split_pdf_empty_body():
    result = split_pdf_by_invoices(b"")
    assert result.invoice_count == 0
    assert "empty_body" in result.warnings


def test_write_pdf_subset_emits_pages():
    pdf = _make_test_pdf_with_pages([
        _INVOICE_PAGE_1, _INVOICE_PAGE_2, _INVOICE_PAGE_3,
    ])
    sub = write_pdf_subset(pdf, [1, 2])
    from pypdf import PdfReader
    assert sub
    reader = PdfReader(io.BytesIO(sub))
    assert len(reader.pages) == 2


# ─── API ───────────────────────────────────────────────────────────


def test_api_split_returns_boundaries(client_orgA):
    pdf = _make_test_pdf_with_pages([_INVOICE_PAGE_1, _INVOICE_PAGE_2])
    resp = client_orgA.post(
        "/api/workspace/pdf/split", content=pdf,
        headers={"Content-Type": "application/pdf"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["invoice_count"] == 2
    assert len(data["boundaries"]) == 2


def test_api_split_with_split_pdfs(client_orgA):
    pdf = _make_test_pdf_with_pages([_INVOICE_PAGE_1, _INVOICE_PAGE_2])
    resp = client_orgA.post(
        "/api/workspace/pdf/split?include_split_pdfs=true",
        content=pdf,
    )
    data = resp.json()
    assert data["split_pdfs_base64"] is not None
    assert len(data["split_pdfs_base64"]) == 2


def test_api_split_empty_body_400(client_orgA):
    resp = client_orgA.post(
        "/api/workspace/pdf/split", content=b"",
    )
    assert resp.status_code == 400


def test_api_boundaries_text_only(client_orgA):
    resp = client_orgA.post(
        "/api/workspace/pdf/boundaries",
        json={"pages": [_INVOICE_PAGE_1, _INVOICE_PAGE_2_CONT, _INVOICE_PAGE_2]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["page_count"] == 2
