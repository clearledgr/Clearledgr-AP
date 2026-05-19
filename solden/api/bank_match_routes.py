"""Bank match BoxType endpoints — the second Box surface.

Phase 4 of the manifesto-truthing pass. Proves the generalization
claim: bank_match endpoints follow the same shape as ap_item
endpoints (read / list-children / typed action / export) on top of
the same audit + state-machine + export primitives.

Endpoints:

    GET  /api/workspace/bank-matches/{box_id}
    POST /api/workspace/bank-matches/{box_id}/accept
    POST /api/workspace/bank-matches/{box_id}/reject
    GET  /api/workspace/ap-items/{ap_item_id}/bank-match-boxes

The bank_match export route lives next to the AP export in
``solden.api.box_export`` so a single consumer's import surface
covers both BoxTypes.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from solden.core.auth import get_current_user
from solden.core.bank_match_states import BankMatchState
from solden.core.database import get_db
from solden.core.stores.bank_match_store import (
    IllegalBankMatchTransitionError,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspace", tags=["bank-match"])


def _session_org(user: Any) -> str:
    org = str(getattr(user, "organization_id", "") or "").strip()
    if not org:
        raise HTTPException(
            status_code=403, detail="user_missing_organization_id"
        )
    return org


def _require_bank_match(db: Any, box_id: str, organization_id: str) -> Dict[str, Any]:
    item = db.get_bank_match(box_id) if hasattr(db, "get_bank_match") else None
    if not item or str(item.get("organization_id") or "") != organization_id:
        raise HTTPException(status_code=404, detail="bank_match_not_found")
    return item


class DecisionRequest(BaseModel):
    reason: str = Field("", max_length=2000)


@router.get("/bank-matches/{box_id}")
def get_bank_match(
    box_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    organization_id = _session_org(_user)
    db = get_db()
    return _require_bank_match(db, box_id, organization_id)


@router.post("/bank-matches/{box_id}/accept")
def accept_bank_match(
    box_id: str,
    body: DecisionRequest,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Advance a proposed bank_match Box to ACCEPTED. Terminal."""
    organization_id = _session_org(_user)
    actor_id = str(getattr(_user, "email", "") or getattr(_user, "user_id", "") or "")
    db = get_db()
    _require_bank_match(db, box_id, organization_id)
    try:
        return db.update_bank_match_state(
            box_id,
            BankMatchState.ACCEPTED.value,
            actor_id=actor_id,
            reason=body.reason.strip(),
        )
    except IllegalBankMatchTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/bank-matches/{box_id}/reject")
def reject_bank_match(
    box_id: str,
    body: DecisionRequest,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Advance a proposed bank_match Box to REJECTED. Terminal."""
    organization_id = _session_org(_user)
    actor_id = str(getattr(_user, "email", "") or getattr(_user, "user_id", "") or "")
    db = get_db()
    _require_bank_match(db, box_id, organization_id)
    try:
        return db.update_bank_match_state(
            box_id,
            BankMatchState.REJECTED.value,
            actor_id=actor_id,
            reason=body.reason.strip(),
        )
    except IllegalBankMatchTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/ap-items/{ap_item_id}/bank-match-boxes")
def list_bank_match_boxes_for_ap(
    ap_item_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """List every bank_match Box hanging off this AP item.

    Distinct from the legacy ``/ap-items/{id}/bank-match`` endpoint
    (which returns a derived view over payment_confirmations +
    bank_statement_lines). This endpoint returns the typed Boxes
    themselves — each one independently auditable, exportable, and
    advance-able to a terminal state.
    """
    organization_id = _session_org(_user)
    db = get_db()
    # Tenant gate on the parent AP item first — 404 if cross-tenant
    # so we don't leak existence.
    parent = db.get_ap_item(ap_item_id)
    if not parent or str(parent.get("organization_id") or "") != organization_id:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    matches: List[Dict[str, Any]] = []
    if hasattr(db, "list_bank_matches_for_ap"):
        matches = db.list_bank_matches_for_ap(
            ap_item_id, organization_id=organization_id,
        ) or []
    return {
        "ap_item_id": ap_item_id,
        "count": len(matches),
        "boxes": matches,
    }
