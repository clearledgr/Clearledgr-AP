"""Reconciliation workflow state machine.

Follows the same pattern as ap_states.py. Each reconciliation session
moves transactions through matching, exception handling, and resolution.

Primary path:
    imported -> matching -> matched -> posted

Exception paths:
    matching -> exception   (no match found or ambiguous match)
    exception -> review     (human review needed)
    review -> resolved      (human resolved the exception)
    resolved -> posted      (correction posted to ERP/Sheets)
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Dict, FrozenSet

logger = logging.getLogger(__name__)


class ReconState(str, Enum):
    """Reconciliation item states."""

    IMPORTED = "imported"
    MATCHING = "matching"
    MATCHED = "matched"
    EXCEPTION = "exception"
    REVIEW = "review"
    RESOLVED = "resolved"
    POSTED = "posted"


RECON_VALID_TRANSITIONS: Dict[ReconState, FrozenSet[ReconState]] = {
    ReconState.IMPORTED: frozenset({ReconState.MATCHING}),
    ReconState.MATCHING: frozenset({ReconState.MATCHED, ReconState.EXCEPTION}),
    ReconState.MATCHED: frozenset({ReconState.POSTED}),
    ReconState.EXCEPTION: frozenset({ReconState.REVIEW, ReconState.MATCHING}),  # retry or escalate
    ReconState.REVIEW: frozenset({ReconState.RESOLVED, ReconState.MATCHING}),   # resolve or re-match
    ReconState.RESOLVED: frozenset({ReconState.POSTED}),
    ReconState.POSTED: frozenset(),  # terminal
}

RECON_TERMINAL_STATES: FrozenSet[ReconState] = frozenset({ReconState.POSTED})


def normalize_recon_state(raw: str) -> str:
    """Normalize a raw state string to the canonical recon state."""
    raw_lower = raw.strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return ReconState(raw_lower).value
    except ValueError:
        return raw_lower


def validate_recon_transition(current: str, target: str) -> bool:
    """Check whether current -> target is a legal reconciliation transition."""
    try:
        cur = ReconState(normalize_recon_state(current))
        tgt = ReconState(normalize_recon_state(target))
    except ValueError:
        return False
    return tgt in RECON_VALID_TRANSITIONS.get(cur, frozenset())


# ==================== WorkflowStateMachine protocol conformance ====================

class _ReconStateMachine:
    """Satisfies WorkflowStateMachine protocol via structural typing."""

    @staticmethod
    def states() -> FrozenSet[str]:
        return frozenset(s.value for s in ReconState)

    @staticmethod
    def transitions() -> Dict[str, FrozenSet[str]]:
        return {k.value: frozenset(v.value for v in vs) for k, vs in RECON_VALID_TRANSITIONS.items()}

    @staticmethod
    def terminal_states() -> FrozenSet[str]:
        return frozenset(s.value for s in RECON_TERMINAL_STATES)

    @staticmethod
    def validate_transition(current: str, target: str) -> bool:
        return validate_recon_transition(current, target)

    @staticmethod
    def normalize(raw: str) -> str:
        return normalize_recon_state(raw)


RECON_STATE_MACHINE = _ReconStateMachine()
