"""Bank match state machine — the second BoxType.

The manifesto claims: "The architecture that runs AP runs procurement.
Same shape. Same primitives. Same broken coordination today."

Until this module, the only registered BoxType was ``ap_item`` —
which made the generalization claim aspirational. ``bank_match``
proves the primitive by ringing a second workflow type through the
same Box-lifecycle scaffolding: typed state machine, append-only
audit, structured exceptions, portable export.

bank_match is **AP-subordinate** by design (decision 2026-05-02,
preserved in the manifesto-truthing pass): every bank_match Box
carries a ``parent_ap_item_id`` foreign key back to the AP item it
reconciles. The bank_match has its own lifecycle and audit trail —
distinct surface, distinct state — but the AP item remains the
operator-facing record.

Lifecycle::

    PROPOSED ──accept──► ACCEPTED  (terminal)
        │
        └──reject────────► REJECTED  (terminal)

PROPOSED is the entry state when the bank-reconciliation matcher
proposes a match between a payment_confirmation and a
bank_statement_line. An operator (or a high-confidence auto-accept
policy) advances to ACCEPTED or REJECTED. Both are terminal — a
rejected match doesn't reopen; a new PROPOSED Box is created if a
later matcher pass finds a different candidate.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet


class BankMatchState(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


VALID_BANK_MATCH_TRANSITIONS: Dict[BankMatchState, FrozenSet[BankMatchState]] = {
    BankMatchState.PROPOSED: frozenset({
        BankMatchState.ACCEPTED,
        BankMatchState.REJECTED,
    }),
    BankMatchState.ACCEPTED: frozenset(),  # terminal
    BankMatchState.REJECTED: frozenset(),  # terminal
}


BANK_MATCH_TERMINAL_STATES: FrozenSet[BankMatchState] = frozenset({
    BankMatchState.ACCEPTED,
    BankMatchState.REJECTED,
})


VALID_BANK_MATCH_STATE_VALUES: FrozenSet[str] = frozenset(
    s.value for s in BankMatchState
)


# Current bank_match policy version — analogous to
# CURRENT_AP_POLICY_VERSION. Stamped on every audit_events row for a
# bank_match Box so the version of the matching/reconciliation policy
# that authorized each transition is preserved in the timeline.
CURRENT_BANK_MATCH_POLICY_VERSION = "v1"


def validate_bank_match_transition(current: str, target: str) -> bool:
    """Whether *current* -> *target* is a legal bank_match transition."""
    try:
        cur = BankMatchState(current)
        tgt = BankMatchState(target)
    except ValueError:
        return False
    return tgt in VALID_BANK_MATCH_TRANSITIONS.get(cur, frozenset())
