"""Reconciliation and matching models."""
from typing import List, Optional
from pydantic import Field
from clearledgr.models.base import CLBaseModel
from clearledgr.models.transactions import BankTransaction, GLTransaction
from clearledgr.models.journal_entries import DraftJournalEntry

class MatchScoreBreakdown(CLBaseModel):
    amount_score: float = 0.0
    date_score: float = 0.0
    reference_score: float = 0.0
    vendor_score: float = 0.0
    llm_score: float | None = None
    total_score: float = 0.0

class MatchCandidate(CLBaseModel):
    bank_transaction_id: str
    gl_transaction_id: str
    score: float = Field(..., ge=0, le=1)
    amount_diff: float = Field(..., ge=0)
    date_diff_days: int = Field(..., ge=0)
    breakdown: MatchScoreBreakdown | None = None


class ReconciliationMatch(CLBaseModel):
    bank: BankTransaction
    gl: GLTransaction
    score: float = Field(..., ge=0, le=1)
    reason: Optional[str] = None
    breakdown: MatchScoreBreakdown | None = None


class ReconciliationConfig(CLBaseModel):
    amount_tolerance_pct: float = Field(default=0.5, ge=0)
    date_window_days: int = Field(default=3, ge=0)
    match_threshold: float = Field(default=0.8, ge=0, le=1)
    llm_enabled: bool = Field(default=True)


class ReconciliationResult(CLBaseModel):
    matches: List[ReconciliationMatch] = Field(default_factory=list)
    unmatched_bank: List[BankTransaction] = Field(default_factory=list)
    unmatched_gl: List[GLTransaction] = Field(default_factory=list)
    exceptions: List[str] = Field(default_factory=list)
    match_rate: float = Field(default=0.0, ge=0, le=1)
    config: Optional[ReconciliationConfig] = None
    draft_journal_entries: List[DraftJournalEntry] = Field(default_factory=list)
