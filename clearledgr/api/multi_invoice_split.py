"""Multi-invoice PDF split API (Wave 5 / G3).

  POST /api/workspace/pdf/split
      Body: raw PDF bytes (5 MB cap).
      Detects boundaries, returns the boundary list. Use
      ?include_split_pdfs=true to also receive base64-encoded
      per-invoice sub-PDFs (response gets larger; default false).

  POST /api/workspace/pdf/boundaries
      Body: pre-extracted per-page text as JSON
      ``{"pages": ["page 1 text", "page 2 text", ...]}``
      Useful when the operator already has page text from another
      OCR pipeline and just wants the boundary detection.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.services.multi_invoice_splitter import (
    detect_invoice_boundaries,
    split_pdf_by_invoices,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace/pdf",
    tags=["pdf-split"],
)


_MAX_BODY_BYTES = 5 * 1024 * 1024


class BoundaryOut(BaseModel):
    start_page: int
    end_page: int
    invoice_number: Optional[str] = None
    total_amount_text: Optional[str] = None
    page_count: int


class SplitResponse(BaseModel):
    invoice_count: int
    page_count: int = 0
    boundaries: List[BoundaryOut] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    split_pdfs_base64: Optional[List[str]] = None


class BoundariesBody(BaseModel):
    pages: List[str]


@router.post("/split", response_model=SplitResponse)
async def split_pdf(
    request: Request,
    include_split_pdfs: bool = Query(default=False),
    user: TokenData = Depends(get_current_user),
):
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="empty_body")
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="body_too_large")
    result = split_pdf_by_invoices(
        raw, write_split_pdfs=include_split_pdfs,
    )
    response = SplitResponse(
        invoice_count=result.invoice_count,
        page_count=result.page_count,
        boundaries=[
            BoundaryOut(
                start_page=b.start_page, end_page=b.end_page,
                invoice_number=b.invoice_number,
                total_amount_text=b.total_amount_text,
                page_count=b.page_count,
            )
            for b in result.boundaries
        ],
        warnings=list(result.warnings),
    )
    if include_split_pdfs:
        response.split_pdfs_base64 = [
            base64.b64encode(p).decode("ascii") if p else ""
            for p in result.split_pdfs
        ]
    return response


@router.post("/boundaries", response_model=List[BoundaryOut])
def detect_boundaries_from_text(
    body: BoundariesBody,
    user: TokenData = Depends(get_current_user),
):
    """Boundary-only mode for callers that already have per-page text
    (e.g. from a prior OCR step)."""
    boundaries = detect_invoice_boundaries(body.pages)
    return [
        BoundaryOut(
            start_page=b.start_page, end_page=b.end_page,
            invoice_number=b.invoice_number,
            total_amount_text=b.total_amount_text,
            page_count=b.page_count,
        )
        for b in boundaries
    ]
