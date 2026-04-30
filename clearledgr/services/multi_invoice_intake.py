"""Multi-invoice intake — pre-split bridge between Gmail attachments
and the per-invoice triage path.

The single-pass processor is shaped around "one email, one invoice."
Two real-world cases break that shape:

  A) **One PDF with N invoices in it.** Vendors (especially in the EU)
     bulk-export a month of invoices into a single PDF and email the
     stack. ``clearledgr/services/multi_invoice_splitter.py`` already
     detects boundaries and writes per-invoice sub-PDFs; this module
     calls it.

  B) **N separate PDF attachments in one email.** A vendor sends
     multiple invoices as multiple attachments. The single-pass
     processor caps visual attachments at ``MAX_VISUAL_ATTACHMENTS``
     (3) and silently truncates beyond that — without this bridge,
     invoice 4 disappears from the AP queue.

This module's job is to produce a flat list of "intake units" — one
per detected invoice — that the triage orchestrator can iterate over,
calling the existing single-pass + multi-call pipeline once per unit.

Each unit carries:

  - ``attachments``  — the visual attachments that belong to this
    invoice (typically one — either the whole original PDF, or one
    of the sub-PDFs from the splitter).
  - ``hint_invoice_number`` — when the splitter pre-detected an
    invoice number, surface it as a hint; ``None`` when single-pass
    should rely on its own extraction.
  - ``hint_total_text`` — same shape, the splitter's amount-text
    detection. Useful for amount sanity-checking downstream.

Failure modes:

  - PDF that doesn't parse / pdfplumber missing → return one unit
    containing the original attachment unchanged (matches the
    pre-bridge behaviour: single Box for the whole input).
  - Splitter returns one boundary → one unit, no fan-out — the
    common case for single-invoice PDFs.

Never raises.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class IntakeUnit:
    """One invoice's worth of intake material — attachments + hints."""
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    hint_invoice_number: Optional[str] = None
    hint_total_text: Optional[str] = None
    # Non-PDF (e.g. plain text) attachments are pinned to the FIRST
    # unit so they aren't dropped on the way through. The first unit
    # is treated as the "main" invoice for residual context.
    is_primary: bool = False


def _is_pdf_attachment(att: Dict[str, Any]) -> bool:
    mime = str(att.get("mimeType") or att.get("content_type") or "").lower()
    return "pdf" in mime


def _attachment_bytes(att: Dict[str, Any]) -> Optional[bytes]:
    """Pull the raw PDF bytes from an attachment dict in either of
    the two shapes the gmail intake produces:
    ``{"data": <bytes>}`` or ``{"data": <base64-str>}``."""
    data = att.get("data")
    if isinstance(data, bytes):
        return data
    if isinstance(data, str) and data:
        try:
            import base64
            return base64.b64decode(data)
        except Exception:
            return None
    return None


def split_email_attachments(
    attachments: List[Dict[str, Any]],
) -> List[IntakeUnit]:
    """Return one :class:`IntakeUnit` per detected invoice.

    Produces exactly one unit when:
      - The email has zero PDF attachments.
      - The email has one PDF attachment with one detected invoice.

    Fans out into multiple units when:
      - The email has one PDF whose splitter detects N>1 invoices.
      - The email has multiple PDF attachments (each becomes its own
        unit; if any of them ALSO contains multiple invoices, those
        cascade further).

    Non-PDF attachments are attached to the first (primary) unit so
    plain-text attachments / markdown / .txt files aren't lost.
    """
    if not attachments:
        return [IntakeUnit(is_primary=True)]

    pdfs: List[Dict[str, Any]] = [a for a in attachments if isinstance(a, dict) and _is_pdf_attachment(a)]
    non_pdfs: List[Dict[str, Any]] = [a for a in attachments if isinstance(a, dict) and not _is_pdf_attachment(a)]

    if not pdfs:
        return [IntakeUnit(attachments=list(non_pdfs), is_primary=True)]

    units: List[IntakeUnit] = []
    for pdf_index, pdf_att in enumerate(pdfs):
        pdf_units = _expand_single_pdf(pdf_att)
        if not pdf_units:
            # Splitter failed; keep the original PDF as one unit so
            # the AP item still gets created.
            pdf_units = [IntakeUnit(attachments=[pdf_att])]
        units.extend(pdf_units)

    # Non-PDF attachments hitch a ride on the first unit.
    if non_pdfs and units:
        units[0].attachments = list(non_pdfs) + list(units[0].attachments)

    if units:
        units[0].is_primary = True

    return units


def _expand_single_pdf(pdf_att: Dict[str, Any]) -> List[IntakeUnit]:
    """Run the multi-invoice splitter against one PDF attachment.
    Returns one unit per detected invoice. On any error returns
    ``[]`` (caller falls back to keeping the original attachment)."""
    pdf_bytes = _attachment_bytes(pdf_att)
    if not pdf_bytes:
        return []

    try:
        from clearledgr.services.multi_invoice_splitter import split_pdf_by_invoices
    except Exception as exc:
        logger.debug("[MultiInvoiceIntake] splitter import failed: %s", exc)
        return []

    try:
        result = split_pdf_by_invoices(pdf_bytes, write_split_pdfs=True)
    except Exception as exc:
        logger.warning(
            "[MultiInvoiceIntake] splitter raised on %s — keeping original PDF as one unit: %s",
            pdf_att.get("filename") or pdf_att.get("name") or "<unnamed>", exc,
        )
        return []

    if result.invoice_count <= 1 or not result.split_pdfs:
        # Single-invoice PDF — return one unit with the original
        # attachment unchanged (the splitter doesn't add value here).
        return [IntakeUnit(attachments=[pdf_att])]

    units: List[IntakeUnit] = []
    base_name = str(pdf_att.get("filename") or pdf_att.get("name") or "invoice").strip()
    media_type = str(
        pdf_att.get("mimeType") or pdf_att.get("content_type") or "application/pdf"
    )
    for idx, sub_pdf_bytes in enumerate(result.split_pdfs):
        if not sub_pdf_bytes:
            continue
        boundary = (
            result.boundaries[idx]
            if idx < len(result.boundaries)
            else None
        )
        sub_filename = f"{base_name.removesuffix('.pdf') or base_name}.split-{idx + 1}.pdf"
        sub_attachment = {
            "filename": sub_filename,
            "name": sub_filename,
            "data": sub_pdf_bytes,
            "mimeType": media_type,
            "content_type": media_type,
        }
        units.append(IntakeUnit(
            attachments=[sub_attachment],
            hint_invoice_number=getattr(boundary, "invoice_number", None) if boundary else None,
            hint_total_text=getattr(boundary, "total_amount_text", None) if boundary else None,
        ))

    if not units:
        return []
    return units
