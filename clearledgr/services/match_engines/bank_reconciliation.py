"""Bank reconciliation match engine — second implementation of the
:class:`MatchEngine` protocol. Concrete proof the abstraction
generalizes beyond AP.

Matches a **bank statement line** against an **ERP GL transaction**
within a configurable tolerance window:

* Amount equality (exact, or within ``amount_tolerance``)
* Date proximity (within ``date_window_days``)
* Currency match
* (Optional) reference number / memo string overlap as tie-breaker

This is the core of bank reconciliation — what controllers do today
manually in NetSuite's "Match Bank Data" or SAP's `FF67` transaction.
With the abstraction, future workflows (intercompany pairing, vendor
statement recon) plug in by writing similar small engines.

Tolerances flow through the versioned :class:`PolicyService` under
the ``match_tolerances`` policy kind, so changes are audit-replayable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from clearledgr.services.match_engine import (
    MatchCandidate,
    MatchInput,
    MatchStatus,
    get_tolerance_for,
    register_match_engine,
)

logger = logging.getLogger(__name__)


class BankReconciliationMatchEngine:
    """Match a bank-statement line to an ERP GL transaction.

    Expected ``MatchInput.payload`` shape:

    ::

        {
            "amount": -1234.56,           # signed amount (negative = outflow)
            "currency": "USD",
            "posted_at": "2026-04-25T...",
            "description": "ACME CORP INV-001",
            "account_id": "<bank_account_id>",
            "reference_number": "ABC123",  # optional — bank ref / cheque #
        }

    Returns candidates from the existing
    ``transactions`` table (ERP-side GL transactions imported during
    nightly close). The ``find_candidates`` query uses amount + date
    window as the cheap pre-filter; ``score`` does the per-candidate
    fine-grained variance (date offset, description overlap).
    """

    match_type = "bank_reconciliation"

    async def find_candidates(
        self, input: MatchInput, *, limit: int = 10,
    ) -> List[MatchCandidate]:
        from clearledgr.core.database import get_db
        db = get_db()
        amount = _safe_float(input.payload.get("amount"))
        currency = str(input.payload.get("currency") or "").strip().upper()
        posted_at = str(input.payload.get("posted_at") or "").strip()
        if amount is None or not currency or not posted_at:
            return []

        amount_tolerance = float(get_tolerance_for(
            input.organization_id, match_type="bank_reconciliation",
            key="amount_tolerance", default=0.01,
        ))
        date_window_days = int(get_tolerance_for(
            input.organization_id, match_type="bank_reconciliation",
            key="date_window_days", default=3,
        ))

        # Date window: posted_at ± N days
        try:
            anchor = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        except ValueError:
            try:
                anchor = datetime.strptime(posted_at[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return []
        date_lower = (anchor - timedelta(days=date_window_days)).isoformat()
        date_upper = (anchor + timedelta(days=date_window_days)).isoformat()

        candidates: List[MatchCandidate] = []
        if not hasattr(db, "connect"):
            return []

        # The 'transactions' table is created by migration v9 (per-tenant
        # GL transaction store used by recon). Schema:
        #   id, organization_id, transaction_data (JSON), created_at, updated_at
        # The transaction_data JSON carries amount, currency, posted_at,
        # description, account_id, reference_number. Schema-flexible by
        # design — fields that change per ERP can live in the JSON
        # without ALTER TABLE.
        db.initialize()
        with db.connect() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT id, transaction_data FROM transactions
                    WHERE organization_id = %s
                      AND created_at >= %s AND created_at <= %s
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    (input.organization_id, date_lower, date_upper, int(limit * 5)),
                )
                rows = cur.fetchall()
            except Exception as exc:  # noqa: BLE001
                logger.warning("bank_recon: candidate query failed — %s", exc)
                return []

        import json
        for row in rows or []:
            row_d = dict(row)
            tx_data = row_d.get("transaction_data") or {}
            if isinstance(tx_data, str):
                try:
                    tx_data = json.loads(tx_data)
                except Exception:
                    continue
            if not isinstance(tx_data, dict):
                continue
            tx_amount = _safe_float(tx_data.get("amount"))
            tx_currency = str(tx_data.get("currency") or "").strip().upper()
            if tx_amount is None or tx_currency != currency:
                continue
            if abs(tx_amount - amount) > amount_tolerance + 0.001:
                continue
            candidates.append(MatchCandidate(
                right_type="gl_transaction",
                right_id=str(row_d.get("id") or ""),
                score=0.0,  # filled in score()
                variance={},
                metadata={
                    "tx_amount": tx_amount,
                    "tx_currency": tx_currency,
                    "tx_posted_at": tx_data.get("posted_at"),
                    "tx_description": tx_data.get("description"),
                    "tx_reference_number": tx_data.get("reference_number"),
                    "tx_account_id": tx_data.get("account_id"),
                },
            ))
            if len(candidates) >= limit:
                break
        return candidates

    async def score(
        self, input: MatchInput, candidate: MatchCandidate,
    ) -> MatchCandidate:
        """Score on a 0.0-1.0 scale combining amount-exactness, date
        proximity, and description / reference-number overlap.

        Weights: amount 0.5, date 0.3, description 0.2 — amount is
        the most important signal; date narrows the window;
        description / ref breaks ties when amounts and dates collide.
        """
        amount = _safe_float(input.payload.get("amount")) or 0.0
        tx_amount = candidate.metadata.get("tx_amount") or 0.0
        amount_tolerance = float(get_tolerance_for(
            input.organization_id, match_type="bank_reconciliation",
            key="amount_tolerance", default=0.01,
        ))
        amount_diff = abs(tx_amount - amount)
        amount_score = max(0.0, 1.0 - (amount_diff / max(amount_tolerance, 0.01)))
        amount_score = min(amount_score, 1.0)

        date_window_days = int(get_tolerance_for(
            input.organization_id, match_type="bank_reconciliation",
            key="date_window_days", default=3,
        ))
        date_score = _date_proximity_score(
            input.payload.get("posted_at"),
            candidate.metadata.get("tx_posted_at"),
            window_days=date_window_days,
        )

        desc_score = _description_overlap_score(
            input.payload.get("description"),
            candidate.metadata.get("tx_description"),
            input.payload.get("reference_number"),
            candidate.metadata.get("tx_reference_number"),
        )

        composite = (0.5 * amount_score) + (0.3 * date_score) + (0.2 * desc_score)
        candidate.score = round(composite, 4)
        candidate.variance = {
            "amount_diff": round(amount_diff, 4),
            "amount_score": round(amount_score, 4),
            "date_score": round(date_score, 4),
            "description_score": round(desc_score, 4),
        }
        return candidate

    async def decide(
        self,
        input: MatchInput,
        candidates: List[MatchCandidate],
    ) -> tuple[MatchStatus, Optional[MatchCandidate], List[str]]:
        if not candidates:
            return MatchStatus.NO_MATCH, None, ["no_gl_candidate"]

        sorted_c = sorted(candidates, key=lambda c: c.score, reverse=True)
        best = sorted_c[0]

        # Multi-match if top two are within 0.05 of each other AND both
        # are high-scoring. The controller's manual review picks one.
        if len(sorted_c) >= 2 and (sorted_c[0].score - sorted_c[1].score) < 0.05 and sorted_c[1].score >= 0.85:
            return (
                MatchStatus.MULTIPLE_MATCHES, best,
                ["ambiguous_top_candidates"],
            )
        if best.score >= 0.95:
            return MatchStatus.MATCHED, best, []
        if best.score >= 0.75:
            return MatchStatus.PARTIAL_MATCH, best, ["below_high_confidence_threshold"]
        return MatchStatus.EXCEPTION, best, ["score_too_low"]


# ─── Helpers ───────────────────────────────────────────────────────


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_proximity_score(left: Any, right: Any, *, window_days: int) -> float:
    """1.0 when same day, scales linearly to 0.0 at window edge."""
    if not left or not right:
        return 0.0
    try:
        ld = datetime.fromisoformat(str(left).replace("Z", "+00:00"))
        rd = datetime.fromisoformat(str(right).replace("Z", "+00:00"))
    except ValueError:
        try:
            ld = datetime.strptime(str(left)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            rd = datetime.strptime(str(right)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return 0.0
    diff_days = abs((ld - rd).total_seconds()) / 86400.0
    if diff_days >= window_days:
        return 0.0
    return max(0.0, 1.0 - (diff_days / max(window_days, 1)))


def _description_overlap_score(
    left_desc: Any,
    right_desc: Any,
    left_ref: Any,
    right_ref: Any,
) -> float:
    """Crude string-overlap score. Reference-number exact match
    pegs at 1.0; partial token overlap on description scales between
    0 and 0.7."""
    left_ref_s = str(left_ref or "").strip().upper()
    right_ref_s = str(right_ref or "").strip().upper()
    if left_ref_s and right_ref_s and left_ref_s == right_ref_s:
        return 1.0

    left_d = str(left_desc or "").strip().upper()
    right_d = str(right_desc or "").strip().upper()
    if not left_d or not right_d:
        return 0.0
    left_tokens = set(_tokenize(left_d))
    right_tokens = set(_tokenize(right_d))
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    if union == 0:
        return 0.0
    jaccard = overlap / union
    return min(0.7, jaccard)


def _tokenize(text: str) -> List[str]:
    """Cheap tokenizer for description matching — split on
    non-alphanumeric, drop tokens shorter than 3 chars (mostly
    noise — 'ON' / 'TO' / 'IN' don't help match)."""
    import re
    parts = re.split(r"[^A-Z0-9]+", text)
    return [p for p in parts if len(p) >= 3]


# Register at import time
register_match_engine(BankReconciliationMatchEngine())
