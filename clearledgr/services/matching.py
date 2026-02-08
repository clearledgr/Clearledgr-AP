"""Matching service for reconciliation agents."""
from __future__ import annotations

from datetime import date, datetime
from typing import List

from clearledgr.services.optimal_matching import HungarianMatcher
from clearledgr.models.reconciliation import ReconciliationConfig, ReconciliationMatch, ReconciliationResult
from clearledgr.models.transactions import BankTransaction, GLTransaction


def match_bank_to_gl(
    bank_transactions: List[BankTransaction],
    gl_transactions: List[GLTransaction],
    config: ReconciliationConfig,
) -> ReconciliationResult:
    if not bank_transactions or not gl_transactions:
        return ReconciliationResult(
            matches=[],
            unmatched_bank=bank_transactions,
            unmatched_gl=gl_transactions,
            exceptions=["No transactions available to match."],
            match_rate=0.0,
            config=config,
        )

    cost_matrix = HungarianMatcher.compute_cost_matrix(
        sources=[_to_match_dict(t) for t in bank_transactions],
        targets=[_to_match_dict(t) for t in gl_transactions],
        source_amount_key="amount",
        target_amount_key="amount",
        tolerance_pct=config.amount_tolerance_pct,
        window_days=config.date_window_days,
        parse_date_fn=_parse_date,
    )

    pairs = HungarianMatcher.hungarian_algorithm(cost_matrix)

    matches: List[ReconciliationMatch] = []
    matched_bank = set()
    matched_gl = set()

    for src_idx, tgt_idx in pairs:
        cost = cost_matrix[src_idx][tgt_idx]
        score = max(0.0, 1.0 - cost) if cost is not None else 0.0
        bank_txn = bank_transactions[src_idx]
        gl_txn = gl_transactions[tgt_idx]
        matches.append(ReconciliationMatch(bank=bank_txn, gl=gl_txn, score=round(score, 4)))
        matched_bank.add(src_idx)
        matched_gl.add(tgt_idx)

    unmatched_bank = [tx for idx, tx in enumerate(bank_transactions) if idx not in matched_bank]
    unmatched_gl = [tx for idx, tx in enumerate(gl_transactions) if idx not in matched_gl]

    total = len(bank_transactions) + len(gl_transactions)
    match_rate = (len(matches) * 2 / total) if total else 0.0

    exceptions = []
    if unmatched_bank:
        exceptions.append(f"{len(unmatched_bank)} bank transactions unmatched.")
    if unmatched_gl:
        exceptions.append(f"{len(unmatched_gl)} GL transactions unmatched.")

    return ReconciliationResult(
        matches=matches,
        unmatched_bank=unmatched_bank,
        unmatched_gl=unmatched_gl,
        exceptions=exceptions,
        match_rate=round(match_rate, 4),
        config=config,
    )


def _parse_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


def _to_match_dict(txn):
    return {
        "amount": txn.amount.amount,
        "date": txn.transaction_date,
    }
