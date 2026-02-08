"""Intelligent matching service with multi-factor scoring, LLM fuzzy assist, and pattern learning."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from difflib import SequenceMatcher

from clearledgr.models.patterns import MatchPattern
from clearledgr.models.reconciliation import (
    MatchScoreBreakdown,
    ReconciliationConfig,
    ReconciliationMatch,
    ReconciliationResult,
)
from clearledgr.models.transactions import BankTransaction, GLTransaction
from clearledgr.services.fuzzy_matching import reference_id_similarity, vendor_similarity
from clearledgr.services.llm_multimodal import MultiModalLLMService
from clearledgr.services.pattern_store import PatternStore


class IntelligentMatchingService:
    def __init__(
        self,
        config: Optional[ReconciliationConfig] = None,
        llm: Optional[MultiModalLLMService] = None,
        patterns: Optional[PatternStore] = None,
    ) -> None:
        self.config = config or ReconciliationConfig()
        self.llm = llm or MultiModalLLMService()
        self.pattern_store = patterns or PatternStore()

    def match(self, bank_txns: List[BankTransaction], gl_txns: List[GLTransaction]) -> ReconciliationResult:
        if not bank_txns or not gl_txns:
            return ReconciliationResult(
                matches=[],
                unmatched_bank=bank_txns or [],
                unmatched_gl=gl_txns or [],
                exceptions=["No transactions available to match."],
                match_rate=0.0,
                config=self.config,
            )

        pattern_boosts = self.pattern_store.list()
        matches: List[ReconciliationMatch] = []
        used_gl = set()

        for bank in bank_txns:
            best: Tuple[Optional[GLTransaction], float, MatchScoreBreakdown] = (None, 0.0, MatchScoreBreakdown())
            for gl in gl_txns:
                if gl.transaction_id in used_gl:
                    continue
                breakdown = self._score_pair(bank, gl, pattern_boosts)
                score = breakdown.total_score
                if score > best[1]:
                    best = (gl, score, breakdown)

            if best[0] and best[1] >= self.config.match_threshold:
                gl = best[0]
                used_gl.add(gl.transaction_id)
                matches.append(
                    ReconciliationMatch(
                        bank=bank,
                        gl=gl,
                        score=round(best[1], 4),
                        reason="auto_match",
                        breakdown=best[2],
                    )
                )

        unmatched_gl = [gl for gl in gl_txns if gl.transaction_id not in used_gl]
        match_rate = len(matches) * 2 / (len(bank_txns) + len(gl_txns))

        return ReconciliationResult(
            matches=matches,
            unmatched_bank=[b for b in bank_txns if b.transaction_id not in {m.bank.transaction_id for m in matches}],
            unmatched_gl=unmatched_gl,
            exceptions=[],
            match_rate=round(match_rate, 4),
            config=self.config,
        )

    def _score_pair(self, bank: BankTransaction, gl: GLTransaction, patterns: List[MatchPattern]) -> MatchScoreBreakdown:
        breakdown = MatchScoreBreakdown()

        amount_score = self._amount_score(bank.amount.amount, gl.amount.amount)
        date_score = self._date_score(bank.transaction_date, gl.transaction_date)
        ref_score = reference_id_similarity(
            getattr(bank, "reference_id", "") or bank.description or "",
            getattr(gl, "reference_id", "") or gl.description or "",
        )
        vendor_score = vendor_similarity(bank.counterparty or "", gl.counterparty or "")
        llm_score = self._llm_similarity(bank, gl) if self.config.llm_enabled else None

        # Pattern boost
        pattern_bonus = 0.0
        for pattern in patterns:
            if self._pattern_matches(pattern, bank, gl):
                pattern_bonus = max(pattern_bonus, min(0.2, pattern.confidence))
                self.pattern_store.increment_usage(pattern.pattern_id)

        total = (
            (amount_score * 0.4)
            + (date_score * 0.3)
            + (ref_score * 0.2)
            + (vendor_score * 0.1)
            + (llm_score if llm_score is not None else 0.0) * 0.2
            + pattern_bonus
        )

        breakdown.amount_score = round(amount_score, 4)
        breakdown.date_score = round(date_score, 4)
        breakdown.reference_score = round(ref_score, 4)
        breakdown.vendor_score = round(vendor_score, 4)
        breakdown.llm_score = round(llm_score, 4) if llm_score is not None else None
        breakdown.total_score = round(min(1.0, total), 4)
        return breakdown

    def _amount_score(self, amt1: float, amt2: float) -> float:
        if amt1 is None or amt2 is None:
            return 0.0
        if amt1 == 0 or amt2 == 0:
            return 0.0
        diff = abs(amt1 - amt2)
        tolerance = abs(amt1) * (self.config.amount_tolerance_pct / 100)
        if diff == 0:
            return 1.0
        if diff <= tolerance:
            return max(0.0, 1.0 - (diff / tolerance))
        return 0.0

    def _date_score(self, d1, d2) -> float:
        try:
            delta = abs((d1 - d2).days)
        except Exception:
            return 0.0
        if delta == 0:
            return 1.0
        if delta <= self.config.date_window_days:
            return max(0.0, 1.0 - (delta / max(1, self.config.date_window_days)))
        return 0.0

    def _llm_similarity(self, bank: BankTransaction, gl: GLTransaction) -> Optional[float]:
        if not self.llm:
            return None
        try:
            prompt = f"""You are reconciling two transactions. Give a similarity score 0-1.
Bank: amount={bank.amount.amount} {bank.amount.currency}, date={bank.transaction_date}, desc={bank.description}
GL: amount={gl.amount.amount} {gl.amount.currency}, date={gl.transaction_date}, desc={gl.description}
Respond with a single JSON: {{"score": <number 0-1>}}"""
            resp = self.llm.generate_json(prompt, schema={"type": "object", "properties": {"score": {"type": "number"}}})
            return float(resp.get("score", 0))
        except Exception:
            return None

    def _pattern_matches(self, pattern: MatchPattern, bank: BankTransaction, gl: GLTransaction) -> bool:
        bank_desc = (bank.description or "").lower()
        gl_desc = (gl.description or "").lower()
        return pattern.gateway_pattern.lower() in gl_desc and pattern.bank_pattern.lower() in bank_desc

    def record_correction(self, bank: BankTransaction, gl: GLTransaction, confidence: float = 0.9) -> None:
        pattern = MatchPattern(
            pattern_id=f"pattern_{uuid.uuid4().hex[:8]}",
            gateway_pattern=(gl.description or gl.counterparty or ""),
            bank_pattern=(bank.description or bank.counterparty or ""),
            confidence=confidence,
            match_count=1,
            last_used=datetime.utcnow(),
            last_updated=datetime.utcnow(),
        )
        self.pattern_store.upsert(pattern)

