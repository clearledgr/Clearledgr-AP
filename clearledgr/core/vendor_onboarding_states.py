"""Canonical vendor onboarding state machine.

Aligned to the vendor-onboarding spec §2.1 + design thesis §9. The
user-facing pipeline has four stages (Invited → KYC → Bank Verify →
Active) plus a `blocked` holding pattern and a `closed_unsuccessful`
terminal. This module exposes those six canonical states. Internal
sub-states (``bank_verified`` as a transient beat between provider
confirmation and ERP write; ``ready_for_erp`` as the queue to the ERP
connector) live below the surface but are still first-class state-
machine members so the execution engine can retry at the correct
resume point on a crash between "provider said yes" and "ERP wrote
the vendor row."

Canonical stages (surfaced to Kanban, timeline, chase logic):

    invited
        Invite email + portal link dispatched. Awaiting any vendor action.

    kyc
        Vendor has accessed the portal and is submitting documents /
        fields. The agent is running the KYC checks configured for the
        workspace (completeness, basic, or full tier).

    bank_verify
        KYC passed. The agent has dispatched the open banking
        verification link. The agent is waiting for the provider's
        signed confirmation and the name-match disposition.

    bank_verified
        (Internal.) Provider returned a pass and the name match landed
        inside the auto-proceed band. The vendor-master record is
        being drafted and validated. Users see this as still within
        the `bank_verify` stage on the Kanban — the split exists so a
        crash between "open banking succeeded" and "ERP wrote the
        record" resumes at the right point, not from zero.

    ready_for_erp
        (Internal.) Vendor-master draft validated. Queued for the ERP
        connector's ``write_vendor_to_erp`` call. Same rationale as
        bank_verified — this is the retry resume point if the ERP
        write fails transiently.

    active (terminal)
        Vendor written to the ERP vendor master with AP-enabled status.
        The vendor can now submit invoices that flow through the AP
        pipeline.

    blocked
        A specific blocker has been identified: missing document, KYC
        check failure, bank verification name mismatch, or AP Manager
        rejection of an exception. The blocker is named specifically
        on the Box timeline. The agent chases the vendor or waits for
        AP Manager decision rather than auto-advancing. Recoverable —
        explicit edges back to every pre-active stage exist so AP
        Manager intervention does not need to bypass the state
        machine.

    closed_unsuccessful (terminal)
        Onboarding ended without activation: vendor did not respond to
        chases, AP Manager withdrew the invitation, or a check
        produced a disposition that requires abandoning this session.
        The specific reason lives on ``closed_unsuccessful_reason`` in
        Box state.

Design notes
============

* **Recovery is modeled, not absent.** ``blocked`` is intentionally
  not terminal — most stalled onboardings unstick themselves once the
  AP Manager reaches out. The state machine has explicit edges from
  ``blocked`` back to every prior pre-active state.

* **Re-onboarding is per-session, not per-vendor.** A vendor whose
  first onboarding hit ``closed_unsuccessful`` can be invited again —
  it is a brand-new session row with a fresh chase clock.
  ``vendor_profiles`` carries the durable identity;
  ``vendor_onboarding_sessions`` carries the workflow state.

* **Historical rows are migrated, not translated at read time.** The
  prior names (awaiting_kyc, awaiting_bank, escalated, rejected,
  abandoned) are gone from the enum. Migration v38 rewrites existing
  rows in place and backfills ``closed_unsuccessful_reason`` from the
  old terminal state so no audit context is lost.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Dict, FrozenSet

logger = logging.getLogger(__name__)


class VendorOnboardingState(str, Enum):
    """Canonical vendor onboarding session states (vendor-onboarding-spec §2.1)."""

    INVITED = "invited"
    KYC = "kyc"
    BANK_VERIFY = "bank_verify"
    # Internal sub-states within the user-facing "bank_verify" stage.
    # Kept as first-class members so crash recovery resumes at the
    # correct point — see module docstring.
    BANK_VERIFIED = "bank_verified"
    READY_FOR_ERP = "ready_for_erp"
    ACTIVE = "active"
    BLOCKED = "blocked"
    CLOSED_UNSUCCESSFUL = "closed_unsuccessful"


# Every legal forward edge. Recovery edges from ``blocked`` back to
# pre-active states are listed explicitly — see the design note above.
VALID_TRANSITIONS: Dict[VendorOnboardingState, FrozenSet[VendorOnboardingState]] = {
    VendorOnboardingState.INVITED: frozenset({
        VendorOnboardingState.KYC,
        VendorOnboardingState.BLOCKED,
        VendorOnboardingState.CLOSED_UNSUCCESSFUL,
    }),
    VendorOnboardingState.KYC: frozenset({
        VendorOnboardingState.BANK_VERIFY,
        VendorOnboardingState.BLOCKED,
        VendorOnboardingState.CLOSED_UNSUCCESSFUL,
    }),
    VendorOnboardingState.BANK_VERIFY: frozenset({
        # Provider confirmation received + name match auto-passed.
        # Transitions into the internal bank_verified sub-state.
        VendorOnboardingState.BANK_VERIFIED,
        VendorOnboardingState.BLOCKED,
        VendorOnboardingState.CLOSED_UNSUCCESSFUL,
    }),
    VendorOnboardingState.BANK_VERIFIED: frozenset({
        VendorOnboardingState.READY_FOR_ERP,
        VendorOnboardingState.BLOCKED,
        VendorOnboardingState.CLOSED_UNSUCCESSFUL,
    }),
    VendorOnboardingState.READY_FOR_ERP: frozenset({
        VendorOnboardingState.ACTIVE,
        # ERP write failed and retry queue exhausted — human intervention.
        VendorOnboardingState.BLOCKED,
        VendorOnboardingState.CLOSED_UNSUCCESSFUL,
    }),
    VendorOnboardingState.BLOCKED: frozenset({
        # AP Manager can restart from any pre-active stage once the
        # blocker is resolved. Recovery target is logged to the audit
        # event so the timeline is reconstructable.
        VendorOnboardingState.INVITED,
        VendorOnboardingState.KYC,
        VendorOnboardingState.BANK_VERIFY,
        VendorOnboardingState.BANK_VERIFIED,
        VendorOnboardingState.READY_FOR_ERP,
        VendorOnboardingState.CLOSED_UNSUCCESSFUL,
    }),
    VendorOnboardingState.ACTIVE: frozenset(),               # terminal
    VendorOnboardingState.CLOSED_UNSUCCESSFUL: frozenset(),  # terminal
}

TERMINAL_STATES: FrozenSet[VendorOnboardingState] = frozenset({
    VendorOnboardingState.ACTIVE,
    VendorOnboardingState.CLOSED_UNSUCCESSFUL,
})

# All valid state strings — exposed for DB-level constraint generation
# and for API/UI dropdown rendering.
VALID_STATE_VALUES: FrozenSet[str] = frozenset(s.value for s in VendorOnboardingState)


# Pre-active states are the ones the chase loop watches. Once the
# session is in any pre-active state, the auto-chase scheduler may
# fire 24h/48h/72h reminders against it. ``blocked`` is also chase-
# eligible because some blockers are resolved by continued vendor
# engagement (e.g., a missing document still pending upload).
PRE_ACTIVE_STATES: FrozenSet[VendorOnboardingState] = frozenset({
    VendorOnboardingState.INVITED,
    VendorOnboardingState.KYC,
    VendorOnboardingState.BANK_VERIFY,
})


# Backward-compat alias map. External callers (tests, legacy stored
# JSON, older migrations) may still pass the prior names. ``normalize_state``
# translates them to the new canonical values. Writes through the state
# machine always use the new names; this is read-side compat only.
_LEGACY_STATE_ALIASES: Dict[str, str] = {
    "awaiting_kyc": VendorOnboardingState.KYC.value,
    "awaiting_bank": VendorOnboardingState.BANK_VERIFY.value,
    "escalated": VendorOnboardingState.BLOCKED.value,
    "rejected": VendorOnboardingState.CLOSED_UNSUCCESSFUL.value,
    "abandoned": VendorOnboardingState.CLOSED_UNSUCCESSFUL.value,
}


class IllegalVendorOnboardingTransitionError(ValueError):
    """Raised when a vendor onboarding state transition is illegal."""

    def __init__(self, current: str, target: str, session_id: str = ""):
        self.current = current
        self.target = target
        self.session_id = session_id
        suffix = f" (session_id={session_id})" if session_id else ""
        super().__init__(
            f"Illegal vendor onboarding state transition: "
            f"{current!r} -> {target!r}{suffix}"
        )


def normalize_state(raw: str) -> str:
    """Normalize a state string to its canonical lowercase value.

    Recognises both the current canonical names and the prior
    pre-rename names via ``_LEGACY_STATE_ALIASES`` so stored rows and
    older callers still map cleanly. Unknown values pass through
    unchanged so downstream validation surfaces a meaningful error
    rather than a silent coerce.
    """
    raw_lower = (raw or "").strip().lower()
    if raw_lower in _LEGACY_STATE_ALIASES:
        return _LEGACY_STATE_ALIASES[raw_lower]
    try:
        return VendorOnboardingState(raw_lower).value
    except ValueError:
        return raw_lower


def validate_transition(current: str, target: str) -> bool:
    """Return True iff *current → target* is a legal transition."""
    try:
        cur = VendorOnboardingState(normalize_state(current))
        tgt = VendorOnboardingState(normalize_state(target))
    except ValueError:
        return False
    return tgt in VALID_TRANSITIONS.get(cur, frozenset())


def transition_or_raise(
    current: str, target: str, session_id: str = ""
) -> None:
    """Raise :class:`IllegalVendorOnboardingTransitionError` on illegal edges."""
    if not validate_transition(current, target):
        raise IllegalVendorOnboardingTransitionError(current, target, session_id)


def is_terminal(state: str) -> bool:
    """Return True iff *state* is one of :data:`TERMINAL_STATES`."""
    try:
        return VendorOnboardingState(normalize_state(state)) in TERMINAL_STATES
    except ValueError:
        return False


def is_pre_active(state: str) -> bool:
    """Return True iff *state* is a chase-eligible pre-active state."""
    try:
        return VendorOnboardingState(normalize_state(state)) in PRE_ACTIVE_STATES
    except ValueError:
        return False
