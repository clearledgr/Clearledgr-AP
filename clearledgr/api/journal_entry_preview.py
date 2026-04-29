"""Journal-entry preview API (Wave 3 / E4).

  GET /api/workspace/ap-items/{id}/journal-entry-preview
      Render the Dr/Cr lines an approver sees BEFORE clicking
      approve. Pulls the VAT split from the AP item (E2) and the
      org's GL account map. Returns both the structured form
      (lines + totals) and a plain-text rendering for embed.

  GET /api/workspace/ap-items/{id}/journal-entry-preview?erp=xero
      Override the ERP for the preview — useful when an org has
      multiple connected ERPs and the operator wants to see what
      it would look like in each.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.journal_entry_preview import (
    get_je_preview_for_ap_item,
    render_je_preview_text,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["journal-entry-preview"],
)


# ── Models ──────────────────────────────────────────────────────────


class JELineOut(BaseModel):
    direction: str
    account_code: str
    account_label: str
    amount: float
    currency: str
    line_role: str
    description: Optional[str] = None


class JEPreviewOut(BaseModel):
    ap_item_id: str
    erp_type: str
    treatment: str
    vat_code: str
    currency: str
    gross_amount: float
    net_amount: float
    vat_amount: float
    vat_rate: float
    lines: List[JELineOut]
    debit_total: float
    credit_total: float
    balanced: bool
    notes: List[str] = Field(default_factory=list)
    rendered_text: str


# ── Endpoints ───────────────────────────────────────────────────────


@router.get(
    "/ap-items/{ap_item_id}/journal-entry-preview",
    response_model=JEPreviewOut,
)
def get_journal_entry_preview(
    ap_item_id: str,
    erp: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    preview = get_je_preview_for_ap_item(
        db,
        organization_id=user.organization_id,
        ap_item_id=ap_item_id,
        erp_type=erp,
    )
    if preview is None:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    text = render_je_preview_text(preview)
    out = preview.to_dict()
    out["rendered_text"] = text
    return out
