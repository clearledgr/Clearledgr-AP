"""Multi-invoice PDF splitter (Wave 5 / G3).

Vendors sometimes batch multiple invoices into one PDF — typically
because their billing system bulk-exports a month of invoices, or
because someone scanned a stack. Today the agent treats the whole
PDF as one bill, which breaks the AP cycle (one AP item, one
invoice, one PO match).

This module detects invoice boundaries page-by-page and returns
N split sub-PDFs + extracted markers (invoice number, total,
vendor) for each. The downstream intake pipeline creates one
``ap_items`` row per split rather than one per upload.

The boundary heuristic is **deterministic** (no LLM):

  A page is a NEW invoice boundary if:

    * It contains an invoice-id marker matching one of the
      patterns ``invoice no|number|#``, ``rechnung-nr``, ``facture``.
    * It does NOT match a continuation marker like ``page 2 of 5``,
      ``continued``, ``cont.``.
    * It contains a total/amount-due marker (so we don't split on
      cover pages or attachments without invoice content).

A page that fails the boundary test belongs to the prior invoice
(typical "page 2 of N" continuation). Boundary detection is the
core; the actual byte-level page split uses pypdf's PdfWriter.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Boundary markers ───────────────────────────────────────────────


_INVOICE_ID_PATTERNS = [
    re.compile(r"\binvoice\s*(?:no\.?|number|#)\s*[:#]?\s*([A-Za-z0-9\-_/]+)", re.IGNORECASE),
    re.compile(r"\binv\.?\s*(?:no\.?|#)\s*[:#]?\s*([A-Za-z0-9\-_/]+)", re.IGNORECASE),
    re.compile(r"\brechnung(?:s)?[\-\s]*(?:nr\.?|nummer)\s*[:#]?\s*([A-Za-z0-9\-_/]+)", re.IGNORECASE),
    re.compile(r"\bfacture\s+n[°o]\s*[:#]?\s*([A-Za-z0-9\-_/]+)", re.IGNORECASE),
    re.compile(r"\bfactura\s+n[°o]?\s*[:#]?\s*([A-Za-z0-9\-_/]+)", re.IGNORECASE),
]


_TOTAL_PATTERNS = [
    re.compile(r"\btotal\s+(?:due|amount|payable|inc(?:luding)?\s*vat)\b", re.IGNORECASE),
    re.compile(r"\bamount\s+due\b", re.IGNORECASE),
    re.compile(r"\bgesamtbetrag\b", re.IGNORECASE),
    re.compile(r"\bmontant\s+total\b", re.IGNORECASE),
    re.compile(r"\bsubtotal\b", re.IGNORECASE),
    re.compile(r"\bgrand\s+total\b", re.IGNORECASE),
]


_CONTINUATION_PATTERNS = [
    re.compile(r"\bpage\s+\d+\s*(?:of|/)\s*\d+", re.IGNORECASE),
    re.compile(r"\b(?:continued|cont\.|cont)\b", re.IGNORECASE),
    re.compile(r"\bseite\s+\d+\s+von\s+\d+", re.IGNORECASE),  # German "Seite X von Y"
]


_AMOUNT_PATTERN = re.compile(
    r"(?:[\$£€]|EUR|USD|GBP|NGN|KES|ZAR)\s*[\d,]+(?:\.\d{2})?",
    re.IGNORECASE,
)


# ── Output shapes ──────────────────────────────────────────────────


@dataclass
class InvoiceBoundary:
    """One detected invoice within a multi-invoice PDF.

    ``page_indices`` is 0-indexed and inclusive on both ends.
    """
    start_page: int
    end_page: int
    invoice_number: Optional[str] = None
    total_amount_text: Optional[str] = None
    page_count: int = 0


@dataclass
class SplitResult:
    """Output of :func:`split_pdf_by_invoices`."""
    invoice_count: int
    boundaries: List[InvoiceBoundary] = field(default_factory=list)
    split_pdfs: List[bytes] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    page_count: int = 0


# ── Boundary detection (pure compute) ─────────────────────────────


def _is_continuation_page(text: str) -> bool:
    return any(p.search(text) for p in _CONTINUATION_PATTERNS)


def _find_invoice_id(text: str) -> Optional[str]:
    for pat in _INVOICE_ID_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return None


def _has_total_marker(text: str) -> bool:
    return any(p.search(text) for p in _TOTAL_PATTERNS)


def _find_total_amount(text: str) -> Optional[str]:
    """Return the first amount-shaped token near a total marker."""
    if not _has_total_marker(text):
        return None
    m = _AMOUNT_PATTERN.search(text)
    return m.group(0).strip() if m else None


def detect_invoice_boundaries(
    per_page_text: List[str],
) -> List[InvoiceBoundary]:
    """Return one :class:`InvoiceBoundary` per detected invoice.

    A single-page PDF with one invoice yields one boundary spanning
    [0, 0]. A 5-page PDF with three invoices on pages 0, 2, 4 yields
    three boundaries: [0, 1], [2, 3], [4, 4].

    A PDF with NO invoice-id markers anywhere returns one boundary
    spanning the whole document so the caller still gets an
    AP item (matches existing single-invoice behaviour).
    """
    if not per_page_text:
        return []

    starts: List[Tuple[int, Optional[str], Optional[str]]] = []
    for idx, text in enumerate(per_page_text):
        if _is_continuation_page(text):
            # Force this page to belong to the previous boundary.
            continue
        invoice_id = _find_invoice_id(text)
        if invoice_id and _has_total_marker(text):
            starts.append((idx, invoice_id, _find_total_amount(text)))
        elif idx == 0 and (invoice_id or _has_total_marker(text)):
            # First page with any invoice marker counts even if
            # incomplete — better to keep one boundary than zero.
            starts.append((idx, invoice_id, _find_total_amount(text)))

    if not starts:
        # No clear boundary — single invoice spanning everything.
        return [InvoiceBoundary(
            start_page=0,
            end_page=len(per_page_text) - 1,
            page_count=len(per_page_text),
        )]

    boundaries: List[InvoiceBoundary] = []
    for i, (start, inv_id, total) in enumerate(starts):
        end = (
            starts[i + 1][0] - 1 if i + 1 < len(starts)
            else len(per_page_text) - 1
        )
        boundaries.append(InvoiceBoundary(
            start_page=start,
            end_page=end,
            invoice_number=inv_id,
            total_amount_text=total,
            page_count=end - start + 1,
        ))
    return boundaries


# ── Page text extraction (pdfplumber) ──────────────────────────────


def extract_per_page_text(pdf_bytes: bytes) -> List[str]:
    """Extract text per page using pdfplumber. Returns one string
    per page, in document order.

    pdfplumber dependency is best-effort: a missing install yields
    an empty list + the caller treats the PDF as a single-invoice
    fallback.
    """
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        logger.warning("pdfplumber not installed — multi-invoice split disabled")
        return []
    pages: List[str] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:
                    pages.append("")
    except Exception as exc:
        logger.warning("pdfplumber open failed: %s", exc)
        return []
    return pages


# ── Per-invoice byte-level split ───────────────────────────────────


def write_pdf_subset(pdf_bytes: bytes, page_indices: List[int]) -> bytes:
    """Write a new PDF containing only the listed page indices.

    Uses pypdf's PdfWriter; a missing install yields an empty bytes
    return + the caller skips the per-invoice split (the boundary
    list still gets returned).
    """
    try:
        from pypdf import PdfReader, PdfWriter  # type: ignore
    except ImportError:
        logger.warning("pypdf not installed — per-invoice byte split disabled")
        return b""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter()
        for idx in page_indices:
            if 0 <= idx < len(reader.pages):
                writer.add_page(reader.pages[idx])
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception as exc:
        logger.warning("pypdf write failed: %s", exc)
        return b""


# ── Top-level entry point ──────────────────────────────────────────


def split_pdf_by_invoices(
    pdf_bytes: bytes,
    *,
    write_split_pdfs: bool = True,
) -> SplitResult:
    """End-to-end: extract per-page text, detect boundaries, and
    optionally write per-invoice sub-PDFs.

    ``write_split_pdfs=False`` is useful for the boundary-only API
    surface that just wants to know "how many invoices does this
    PDF contain?" without paying for the byte-level split.
    """
    if not pdf_bytes:
        return SplitResult(invoice_count=0, warnings=["empty_body"])

    pages = extract_per_page_text(pdf_bytes)
    if not pages:
        return SplitResult(
            invoice_count=1,
            boundaries=[],
            warnings=["pdf_text_extraction_failed_or_empty"],
        )

    boundaries = detect_invoice_boundaries(pages)
    split_pdfs: List[bytes] = []
    warnings: List[str] = []
    if write_split_pdfs and boundaries:
        for b in boundaries:
            indices = list(range(b.start_page, b.end_page + 1))
            sub = write_pdf_subset(pdf_bytes, indices)
            if not sub:
                warnings.append(
                    f"per_invoice_split_failed_for_pages_{b.start_page}_{b.end_page}"
                )
            split_pdfs.append(sub)

    return SplitResult(
        invoice_count=len(boundaries),
        boundaries=boundaries,
        split_pdfs=split_pdfs,
        warnings=warnings,
        page_count=len(pages),
    )
