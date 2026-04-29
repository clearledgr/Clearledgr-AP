"""Bank reconciliation matcher (Wave 2 / C6).

Walks unmatched ``bank_statement_lines`` for an org and pairs each
outflow (signed-negative amount) against an existing
``payment_confirmations`` row using:

  * Amount equality (within configurable tolerance, default 0.01)
  * Currency match (exact)
  * Settlement window (statement value_date within ±N days of the
    confirmation's settlement_at; default 5 days)
  * Optional reference-number match boost (statement bank_reference /
    end_to_end_id matches confirmation payment_reference)

When a single confidence-weighted candidate wins clearly, mark the
line ``matched`` and emit an audit event. When two candidates tie,
mark the line ``unmatched`` with ``match_reason=ambiguous_n`` so an
operator confirms manually.

End-to-end this closes AP cycle Stage 9: the bank's ledger has
agreed with our ledger that the bill was paid.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


_DEFAULT_AMOUNT_TOLERANCE = 0.01
_DEFAULT_DATE_WINDOW_DAYS = 5


@dataclass
class MatchOutcome:
    line_id: str
    status: str            # matched | ambiguous | unmatched
    payment_confirmation_id: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None


def _to_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse a date / datetime string and return a tz-aware UTC
    datetime. Accepts ISO 8601, plain ``YYYY-MM-DD``, or compact
    ``YYYYMMDD`` (OFX). Naïve inputs are interpreted as UTC."""
    if not value:
        return None
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:len(fmt) + 8], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _amounts_match(
    statement_amount: Decimal,
    confirmation_amount: Optional[Decimal],
    *,
    tolerance: float,
) -> bool:
    """Statement debits (outflows) are negative; confirmations are
    positive (the bill amount). Compare absolute values within
    tolerance."""
    if confirmation_amount is None:
        return False
    return abs(abs(statement_amount) - abs(confirmation_amount)) <= Decimal(str(tolerance))


def _date_close(
    statement_date: Optional[str],
    settlement_at: Optional[str],
    *,
    window_days: int,
) -> Tuple[bool, Optional[int]]:
    sd = _to_dt(statement_date)
    cd = _to_dt(settlement_at)
    if sd is None or cd is None:
        return False, None
    delta_days = abs((sd - cd).days)
    return delta_days <= window_days, delta_days


def _reference_match(line: Dict[str, Any], conf: Dict[str, Any]) -> bool:
    """Cheap textual containment: if statement bank_reference or
    end_to_end_id matches the confirmation's payment_reference or
    payment_id, that's a strong tie-breaker. Case-insensitive."""
    needles = [
        str(conf.get("payment_reference") or "").strip().lower(),
        str(conf.get("payment_id") or "").strip().lower(),
    ]
    needles = [n for n in needles if n]
    if not needles:
        return False
    haystacks = [
        str(line.get("bank_reference") or "").strip().lower(),
        str(line.get("end_to_end_id") or "").strip().lower(),
        str(line.get("description") or "").strip().lower(),
    ]
    for n in needles:
        for h in haystacks:
            if n and h and (n in h or h in n):
                return True
    return False


def _score_candidate(
    line: Dict[str, Any],
    conf: Dict[str, Any],
    *,
    delta_days: Optional[int],
    window_days: int,
    has_ref_match: bool,
) -> float:
    """0.0–1.0 confidence. 1.0 = perfect amount + same date + ref match."""
    score = 0.6  # baseline for amount + currency + date in-window
    if delta_days is not None and window_days > 0:
        score += 0.2 * max(0.0, 1.0 - (delta_days / window_days))
    if has_ref_match:
        score += 0.2
    return min(1.0, round(score, 3))


def _find_candidates(
    db,
    *,
    organization_id: str,
    line: Dict[str, Any],
    amount_tolerance: float,
    date_window_days: int,
) -> List[Tuple[Dict[str, Any], float]]:
    """Return list of (confirmation, confidence) candidates for a
    single statement line."""
    statement_amount = line.get("amount")
    currency = (line.get("currency") or "").upper()
    if statement_amount is None or not currency:
        return []
    if statement_amount >= 0:
        # Inflow / refund — out of scope for the AP-side matcher.
        return []

    sd_value = line.get("value_date") or line.get("booking_date")
    sd = _to_dt(sd_value)

    if sd is None:
        confirmations = db.list_payment_confirmations(
            organization_id, status="confirmed", limit=500,
        )
    else:
        from_dt = (sd - timedelta(days=date_window_days)).date().isoformat()
        to_dt = (sd + timedelta(days=date_window_days)).date().isoformat() + "T23:59:59"
        confirmations = db.list_payment_confirmations(
            organization_id,
            status="confirmed",
            from_ts=from_dt,
            to_ts=to_dt,
            limit=500,
        )

    out: List[Tuple[Dict[str, Any], float]] = []
    for conf in confirmations:
        if str(conf.get("currency") or "").upper() not in ("", currency):
            continue
        if not _amounts_match(
            statement_amount, conf.get("amount"), tolerance=amount_tolerance,
        ):
            continue
        ok, delta_days = _date_close(
            sd_value, conf.get("settlement_at"), window_days=date_window_days,
        )
        # If neither side has a usable date, we still allow the match
        # but score lower; if one side has a date, it must be in window.
        if not ok and (sd is not None and conf.get("settlement_at")):
            continue
        ref = _reference_match(line, conf)
        score = _score_candidate(
            line, conf,
            delta_days=delta_days,
            window_days=date_window_days,
            has_ref_match=ref,
        )
        out.append((conf, score))
    out.sort(key=lambda pair: pair[1], reverse=True)
    return out


def match_statement_line(
    db,
    *,
    organization_id: str,
    line: Dict[str, Any],
    amount_tolerance: float = _DEFAULT_AMOUNT_TOLERANCE,
    date_window_days: int = _DEFAULT_DATE_WINDOW_DAYS,
    actor_id: Optional[str] = None,
) -> MatchOutcome:
    """Try to match a single statement line; persist + audit the
    outcome.

    Returns the MatchOutcome regardless of result so the caller (a
    bulk matcher) can track stats.
    """
    line_id = line["id"]
    candidates = _find_candidates(
        db,
        organization_id=organization_id,
        line=line,
        amount_tolerance=amount_tolerance,
        date_window_days=date_window_days,
    )
    if not candidates:
        return MatchOutcome(
            line_id=line_id,
            status="unmatched",
            reason="no_candidates",
        )

    top_conf, top_score = candidates[0]
    # Ambiguous if the runner-up is within 0.05 confidence — too close
    # to auto-resolve, kick to human.
    if len(candidates) > 1 and (top_score - candidates[1][1]) < 0.05:
        return MatchOutcome(
            line_id=line_id,
            status="ambiguous",
            confidence=top_score,
            reason=f"ambiguous_{len(candidates)}_candidates",
        )

    db.update_bank_statement_line_match(
        line_id,
        payment_confirmation_id=top_conf["id"],
        match_status="matched",
        match_confidence=top_score,
        match_reason="auto_matched",
        matched_by=actor_id or "bank_reconciliation_matcher",
    )

    # Audit event — keyed by line id so re-running the matcher is
    # idempotent at the audit layer too.
    try:
        db.append_audit_event({
            "ap_item_id": top_conf.get("ap_item_id"),
            "box_id": top_conf.get("ap_item_id") or line_id,
            "box_type": "ap_item" if top_conf.get("ap_item_id") else "bank_statement_line",
            "event_type": "bank_statement_line_matched",
            "actor_type": "system",
            "actor_id": actor_id or "bank_reconciliation_matcher",
            "organization_id": organization_id,
            "source": "bank_reconciliation",
            "idempotency_key": f"bank_match:{organization_id}:{line_id}",
            "metadata": {
                "bank_statement_line_id": line_id,
                "payment_confirmation_id": top_conf["id"],
                "confidence": top_score,
                "match_reason": "auto_matched",
                "amount": str(line.get("amount")),
                "currency": line.get("currency"),
            },
        })
    except Exception:
        logger.exception(
            "bank_reconciliation: audit emit failed line=%s", line_id,
        )

    return MatchOutcome(
        line_id=line_id,
        status="matched",
        payment_confirmation_id=top_conf["id"],
        confidence=top_score,
        reason="auto_matched",
    )


def reconcile_import(
    db,
    *,
    organization_id: str,
    import_id: str,
    amount_tolerance: float = _DEFAULT_AMOUNT_TOLERANCE,
    date_window_days: int = _DEFAULT_DATE_WINDOW_DAYS,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Walk every unmatched line in an import and try to auto-match.

    Returns a summary suitable for the UI: matched / ambiguous /
    unmatched counts."""
    unmatched = db.list_bank_statement_lines(
        organization_id, import_id=import_id, match_status="unmatched",
    )
    summary = {
        "import_id": import_id,
        "total": len(unmatched),
        "matched": 0,
        "ambiguous": 0,
        "unmatched": 0,
    }
    for line in unmatched:
        outcome = match_statement_line(
            db,
            organization_id=organization_id,
            line=line,
            amount_tolerance=amount_tolerance,
            date_window_days=date_window_days,
            actor_id=actor_id,
        )
        if outcome.status == "matched":
            summary["matched"] += 1
        elif outcome.status == "ambiguous":
            summary["ambiguous"] += 1
        else:
            summary["unmatched"] += 1
    db.update_bank_statement_import_match_count(import_id, summary["matched"])
    return summary
