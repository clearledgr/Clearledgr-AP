"""Manual payment confirmation API (Wave 2 / C4).

Operators reach this surface when an AP item paid out-of-band: a paper
cheque mailed to a vendor, a treasury wire booked from the bank
portal, or a small-tenant org that doesn't have an ERP webhook
subscription. The endpoint funnels into the same
:func:`record_payment_confirmation` service that the ERP webhooks
use, so the AP item walks the canonical
``posted_to_erp -> awaiting_payment -> payment_executed`` path with a
matching audit event.

Endpoints (all require workspace auth, all org-scoped via the
authenticated user's TokenData.organization_id):

  * ``POST /api/workspace/payment-confirmations`` — record a new
    confirmation. Body:
        {
          "ap_item_id": "AP-...",
          "payment_id": "...",        # external bank reference
          "source": "manual",
          "status": "confirmed" | "failed" | "disputed",
          "settlement_at": "2026-04-29",
          "amount": 1500.00,
          "currency": "EUR",
          "method": "wire" | "ach" | "check" | "card" | "other",
          "payment_reference": "wire-77",
          "bank_account_last4": "4242",
          "failure_reason": "...",
          "notes": "..."
        }

  * ``GET /api/workspace/payment-confirmations`` — list with filters
    (status, source, settlement window, limit).

  * ``GET /api/workspace/payment-confirmations/{confirmation_id}`` —
    single confirmation by id.

  * ``GET /api/workspace/ap-items/{ap_item_id}/payment-confirmations``
    — feed per AP item (the chain of attempts).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.payment_tracking import (
    PaymentConfirmationResult,
    record_payment_confirmation,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["payment-confirmations"],
)


# ── Request / response models ───────────────────────────────────────


_VALID_STATUSES = {"confirmed", "failed", "disputed"}


class PaymentConfirmationCreate(BaseModel):
    ap_item_id: str = Field(..., min_length=1, max_length=128)
    payment_id: str = Field(..., min_length=1, max_length=128)
    source: str = Field("manual", min_length=1, max_length=64)
    status: str = Field("confirmed")
    settlement_at: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = Field(None, max_length=8)
    method: Optional[str] = Field(None, max_length=32)
    payment_reference: Optional[str] = Field(None, max_length=128)
    bank_account_last4: Optional[str] = Field(None, max_length=8)
    failure_reason: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = Field(None, max_length=2000)

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        if v not in _VALID_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(_VALID_STATUSES)}"
            )
        return v


class PaymentConfirmationOut(BaseModel):
    id: str
    organization_id: str
    ap_item_id: str
    payment_id: str
    source: str
    status: str
    settlement_at: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    method: Optional[str] = None
    payment_reference: Optional[str] = None
    bank_account_last4: Optional[str] = None
    failure_reason: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    created_by: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PaymentConfirmationCreateResponse(BaseModel):
    confirmation: PaymentConfirmationOut
    duplicate: bool
    ap_state_before: Optional[str] = None
    ap_state_after: Optional[str] = None
    ap_state_unchanged_reason: Optional[str] = None
    audit_event_id: Optional[str] = None


def _serialize(row: Optional[Dict[str, Any]]) -> Optional[PaymentConfirmationOut]:
    if not row:
        return None
    return PaymentConfirmationOut(
        id=row["id"],
        organization_id=row["organization_id"],
        ap_item_id=row["ap_item_id"],
        payment_id=row["payment_id"],
        source=row["source"],
        status=row["status"],
        settlement_at=row.get("settlement_at"),
        amount=(
            float(row["amount"])
            if row.get("amount") is not None else None
        ),
        currency=row.get("currency"),
        method=row.get("method"),
        payment_reference=row.get("payment_reference"),
        bank_account_last4=row.get("bank_account_last4"),
        failure_reason=row.get("failure_reason"),
        notes=row.get("notes"),
        created_at=row.get("created_at"),
        created_by=row.get("created_by"),
        metadata=row.get("metadata") or {},
    )


# ── Endpoints ───────────────────────────────────────────────────────


@router.post(
    "/payment-confirmations",
    response_model=PaymentConfirmationCreateResponse,
)
def create_payment_confirmation(
    body: PaymentConfirmationCreate,
    user: TokenData = Depends(get_current_user),
):
    """Operator records an offline / out-of-band payment.

    The AP item must belong to the operator's organization. We don't
    require any specific role here beyond authenticated workspace
    user — the AP cycle audit event captures the actor identity, and
    SOX SoD enforcement (Wave 1 / D1) gates the upstream approve step
    rather than this confirmation step.
    """
    db = get_db()
    ap_item = db.get_ap_item(body.ap_item_id)
    if ap_item is None:
        raise HTTPException(
            status_code=404, detail="ap_item_not_found",
        )
    if str(ap_item.get("organization_id") or "") != user.organization_id:
        raise HTTPException(
            status_code=404, detail="ap_item_not_found",
        )

    try:
        result: PaymentConfirmationResult = record_payment_confirmation(
            db,
            organization_id=user.organization_id,
            ap_item_id=body.ap_item_id,
            payment_id=body.payment_id,
            source=body.source,
            status=body.status,
            settlement_at=body.settlement_at,
            amount=body.amount,
            currency=body.currency,
            method=body.method,
            payment_reference=body.payment_reference,
            bank_account_last4=body.bank_account_last4,
            failure_reason=body.failure_reason,
            notes=body.notes,
            actor_type="user",
            actor_id=user.user_id,
            metadata={"recorded_via": "workspace_shell"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    out = _serialize(result.confirmation)
    if out is None:
        # Should not happen — record_payment_confirmation guarantees
        # confirmation is populated except on raise.
        raise HTTPException(
            status_code=500, detail="confirmation_serialization_failed",
        )
    return PaymentConfirmationCreateResponse(
        confirmation=out,
        duplicate=result.duplicate,
        ap_state_before=result.ap_state_before,
        ap_state_after=result.ap_state_after,
        ap_state_unchanged_reason=result.ap_state_unchanged_reason,
        audit_event_id=result.audit_event_id,
    )


@router.get(
    "/payment-confirmations",
    response_model=List[PaymentConfirmationOut],
)
def list_payment_confirmations(
    status: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    from_ts: Optional[str] = Query(default=None, alias="from"),
    to_ts: Optional[str] = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=1000),
    user: TokenData = Depends(get_current_user),
):
    """Org-wide payment-confirmation feed for the dashboard payment-
    tracking surface."""
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400, detail="invalid_status_filter",
        )
    db = get_db()
    rows = db.list_payment_confirmations(
        user.organization_id,
        status=status,
        source=source,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
    )
    return [_serialize(r) for r in rows if r is not None]


@router.get(
    "/payment-confirmations/{confirmation_id}",
    response_model=PaymentConfirmationOut,
)
def get_payment_confirmation(
    confirmation_id: str,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    row = db.get_payment_confirmation(confirmation_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail="confirmation_not_found",
        )
    if str(row.get("organization_id") or "") != user.organization_id:
        raise HTTPException(
            status_code=404, detail="confirmation_not_found",
        )
    return _serialize(row)


@router.get(
    "/ap-items/{ap_item_id}/payment-confirmations",
    response_model=List[PaymentConfirmationOut],
)
def list_payment_confirmations_for_ap_item(
    ap_item_id: str,
    user: TokenData = Depends(get_current_user),
):
    """Per-AP-item history feed: every payment attempt + retry for one
    bill, newest first."""
    db = get_db()
    ap_item = db.get_ap_item(ap_item_id)
    if ap_item is None:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    if str(ap_item.get("organization_id") or "") != user.organization_id:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    rows = db.list_payment_confirmations_for_ap_item(
        user.organization_id, ap_item_id,
    )
    return [_serialize(r) for r in rows if r is not None]
