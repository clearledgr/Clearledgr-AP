"""Bank statement import + reconciliation API (Wave 2 / C6).

Operator workflow:

  1. Download the latest CAMT.053 / OFX from your bank portal.
  2. POST /api/workspace/bank-statements/import — body is the raw
     file bytes plus a content-type / filename header.
  3. Server parses the file, persists one bank_statement_lines row
     per transaction, runs the auto-matcher, and returns a summary
     (matched / ambiguous / unmatched).
  4. UI lists ambiguous / unmatched lines for human resolution.
  5. Operator confirms a manual match via PUT
     /api/workspace/bank-statements/lines/{line_id}/match.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.bank_reconciliation_matcher import (
    reconcile_import,
)
from clearledgr.services.bank_statement_parsers import detect_and_parse

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace/bank-statements",
    tags=["bank-statements"],
)


# ── Models ──────────────────────────────────────────────────────────


class BankStatementImportSummary(BaseModel):
    id: str
    organization_id: str
    filename: Optional[str] = None
    format: str
    statement_iban: Optional[str] = None
    statement_account: Optional[str] = None
    statement_currency: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None
    line_count: int = 0
    matched_count: int = 0
    uploaded_at: Optional[str] = None
    uploaded_by: Optional[str] = None


class BankStatementLineOut(BaseModel):
    id: str
    import_id: str
    line_index: int
    value_date: Optional[str] = None
    booking_date: Optional[str] = None
    amount: float
    currency: str
    description: Optional[str] = None
    counterparty: Optional[str] = None
    counterparty_iban: Optional[str] = None
    bank_reference: Optional[str] = None
    end_to_end_id: Optional[str] = None
    payment_confirmation_id: Optional[str] = None
    match_status: str
    match_confidence: Optional[float] = None
    match_reason: Optional[str] = None


class ImportResponse(BaseModel):
    import_summary: BankStatementImportSummary
    reconciliation: Dict[str, Any]


class ManualMatchBody(BaseModel):
    payment_confirmation_id: str = Field(..., min_length=1)


def _serialize_import(row: Dict[str, Any]) -> BankStatementImportSummary:
    return BankStatementImportSummary(
        id=row["id"],
        organization_id=row["organization_id"],
        filename=row.get("filename"),
        format=row["format"],
        statement_iban=row.get("statement_iban"),
        statement_account=row.get("statement_account"),
        statement_currency=row.get("statement_currency"),
        from_date=row.get("from_date"),
        to_date=row.get("to_date"),
        opening_balance=(
            float(row["opening_balance"])
            if row.get("opening_balance") is not None else None
        ),
        closing_balance=(
            float(row["closing_balance"])
            if row.get("closing_balance") is not None else None
        ),
        line_count=int(row.get("line_count") or 0),
        matched_count=int(row.get("matched_count") or 0),
        uploaded_at=row.get("uploaded_at"),
        uploaded_by=row.get("uploaded_by"),
    )


def _serialize_line(row: Dict[str, Any]) -> BankStatementLineOut:
    return BankStatementLineOut(
        id=row["id"],
        import_id=row["import_id"],
        line_index=int(row["line_index"]),
        value_date=row.get("value_date"),
        booking_date=row.get("booking_date"),
        amount=float(row["amount"]),
        currency=row["currency"],
        description=row.get("description"),
        counterparty=row.get("counterparty"),
        counterparty_iban=row.get("counterparty_iban"),
        bank_reference=row.get("bank_reference"),
        end_to_end_id=row.get("end_to_end_id"),
        payment_confirmation_id=row.get("payment_confirmation_id"),
        match_status=row["match_status"],
        match_confidence=(
            float(row["match_confidence"])
            if row.get("match_confidence") is not None else None
        ),
        match_reason=row.get("match_reason"),
    )


# ── Endpoints ───────────────────────────────────────────────────────


@router.post("/import", response_model=ImportResponse)
async def import_bank_statement(
    request: Request,
    filename: str = Query(default="statement.xml"),
    user: TokenData = Depends(get_current_user),
):
    """Upload a CAMT.053 or OFX file as raw request body.

    Caps the body at 10 MB so a 100k-line monster statement doesn't
    OOM the worker. The matcher runs synchronously for now — typical
    statements have ~50–500 lines, well under the latency budget.
    """
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="empty_body")
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="body_too_large")

    parsed = detect_and_parse(raw, filename=filename)
    lines = parsed.get("lines") or []
    statement = parsed.get("statement") or {}
    fmt = parsed.get("format") or "unknown"
    if not lines:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported_or_empty_statement:{fmt}",
        )

    db = get_db()
    import_row = db.create_bank_statement_import(
        organization_id=user.organization_id,
        filename=filename,
        format=fmt,
        statement_iban=statement.get("iban"),
        statement_account=statement.get("account"),
        statement_currency=statement.get("currency"),
        from_date=statement.get("from_date"),
        to_date=statement.get("to_date"),
        opening_balance=statement.get("opening_balance"),
        closing_balance=statement.get("closing_balance"),
        line_count=len(lines),
        uploaded_by=user.user_id,
    )
    import_id = import_row["id"]

    for ln in lines:
        try:
            db.insert_bank_statement_line(
                organization_id=user.organization_id,
                import_id=import_id,
                line_index=int(ln["line_index"]),
                amount=ln["amount"],
                currency=ln["currency"],
                value_date=ln.get("value_date"),
                booking_date=ln.get("booking_date"),
                description=ln.get("description"),
                counterparty=ln.get("counterparty"),
                counterparty_iban=ln.get("counterparty_iban"),
                bank_reference=ln.get("bank_reference"),
                end_to_end_id=ln.get("end_to_end_id"),
            )
        except Exception:
            logger.exception(
                "bank_statement insert line failed import=%s idx=%s",
                import_id, ln.get("line_index"),
            )

    summary = reconcile_import(
        db,
        organization_id=user.organization_id,
        import_id=import_id,
        actor_id=user.user_id,
    )
    fresh = db.get_bank_statement_import(import_id)
    return ImportResponse(
        import_summary=_serialize_import(fresh or import_row),
        reconciliation=summary,
    )


@router.get("/imports", response_model=List[BankStatementImportSummary])
def list_imports(
    limit: int = Query(default=50, ge=1, le=500),
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    rows = db.list_bank_statement_imports(user.organization_id, limit=limit)
    return [_serialize_import(r) for r in rows]


@router.get(
    "/imports/{import_id}/lines",
    response_model=List[BankStatementLineOut],
)
def list_lines_for_import(
    import_id: str,
    match_status: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    imp = db.get_bank_statement_import(import_id)
    if imp is None or imp.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="import_not_found")
    try:
        rows = db.list_bank_statement_lines(
            user.organization_id,
            import_id=import_id,
            match_status=match_status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [_serialize_line(r) for r in rows]


@router.put(
    "/lines/{line_id}/match",
    response_model=BankStatementLineOut,
)
def manual_match_line(
    line_id: str,
    body: ManualMatchBody,
    user: TokenData = Depends(get_current_user),
):
    """Operator confirms an ambiguous / unmatched line.

    Validates that the payment_confirmation belongs to the same org,
    flips match_status to ``reconciled`` (operator-confirmed is
    stronger than ``matched``), and records the actor on the row.
    """
    db = get_db()
    line = db.get_bank_statement_line(line_id)
    if line is None or line.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="line_not_found")

    conf = db.get_payment_confirmation(body.payment_confirmation_id)
    if conf is None or conf.get("organization_id") != user.organization_id:
        raise HTTPException(
            status_code=404, detail="payment_confirmation_not_found",
        )
    db.update_bank_statement_line_match(
        line_id,
        payment_confirmation_id=conf["id"],
        match_status="reconciled",
        match_confidence=1.0,
        match_reason="operator_confirmed",
        matched_by=user.user_id,
    )
    fresh = db.get_bank_statement_line(line_id)
    return _serialize_line(fresh)
