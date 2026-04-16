"""Canonical vendor onboarding state machine — Phase 3.1.a.

DESIGN_THESIS.md §9 defines the four-stage Vendor Onboarding Pipeline:
``Invited → KYC → Bank Verify → Active``. This module is the structural
spine: every transition between vendor onboarding states MUST go through
``validate_transition`` / ``transition_or_raise``. No service or API may
write the ``state`` column on ``vendor_onboarding_sessions`` directly —
the typed accessors on :class:`VendorStore` enforce this.

State landscape (richer than the thesis's four-stage summary, because the
implementation needs to model partial-completion and recovery paths):

    invited
        Invite email + magic link dispatched. Awaiting any vendor action.

    awaiting_kyc
        Vendor opened the link or replied to the invite thread, but the
        KYC fields are not yet complete or validated.

    awaiting_bank
        KYC submission complete and validated. Waiting for the vendor to
        provide bank details (IBAN + account holder name).

    bank_verified
        Vendor's bank account has been verified. In V1 this is set
        directly when the vendor submits their IBAN; future versions
        will route submitted bank details through a provider (Adyen for
        EU customers, TrueLayer for UK + rest-of-world) and transition
        to ``bank_verified`` only on successful provider verification.
        The old micro-deposit flow that sat between these two states
        was removed — we don't run rails, we orchestrate them.

    ready_for_erp
        Internal staging state — all data collected, queued for
        :func:`create_vendor` dispatch to the customer's ERP.

    active (terminal)
        Vendor written to the ERP vendor master with AP-enabled status.
        The vendor can now submit invoices that flow through the AP
        pipeline. Confirmation posted to the finance team's Slack.

    escalated
        72h passed at any pre-active stage with no vendor response. The
        agent stops auto-chasing and routes to AP Manager via Slack.
        Recoverable: AP Manager can intervene and restart from any
        prior state once the vendor re-engages.

    rejected (terminal)
        Customer's AP team or CFO explicitly rejected the vendor (failed
        KYC review, sanctions hit, fraud signal). No payments will ever
        flow.

    abandoned (terminal)
        30 days passed without progress. Auto-closed by the chase loop.
        A new onboarding session can be opened later if the vendor
        re-engages — sessions are not unique per vendor over time.

Design notes
============

* **Recovery is modeled, not absent.** ``escalated`` is intentionally
  not terminal — most stalled onboardings unstick themselves once the
  AP Manager reaches out. The state machine has explicit edges from
  ``escalated`` back to every prior pre-active state so intervention
  doesn't require schema-bypass.

* **Re-onboarding is per-session, not per-vendor.** Sessions have their
  own primary key. A vendor whose first onboarding ``abandoned`` can be
  invited again — it's a brand-new session row, fresh state machine,
  fresh chase clock. The vendor's ``vendor_profiles`` row carries the
  durable identity; ``vendor_onboarding_sessions`` carries the temporal
  workflow state. Two layers, two responsibilities.

* **No legacy mapping.** Unlike :mod:`clearledgr.core.ap_states` which
  carries a ``LEGACY_STATE_MAP`` for the old AP status strings, the
  vendor onboarding state machine is greenfield. The
  :class:`~clearledgr.services.vendor_management.VendorManagementService`
  in-memory dict it replaces had no persisted state strings to migrate.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Dict, FrozenSet

logger = logging.getLogger(__name__)


class VendorOnboardingState(str, Enum):
    """Canonical vendor onboarding session states (DESIGN_THESIS.md §9)."""

    INVITED = "invited"
    AWAITING_KYC = "awaiting_kyc"
    AWAITING_BANK = "awaiting_bank"
    BANK_VERIFIED = "bank_verified"
    READY_FOR_ERP = "ready_for_erp"
    ACTIVE = "active"
    ESCALATED = "escalated"
    REJECTED = "rejected"
    ABANDONED = "abandoned"


# Every legal forward edge. Recovery edges from ``escalated`` back to
# pre-active states are listed explicitly — see the design note above.
VALID_TRANSITIONS: Dict[VendorOnboardingState, FrozenSet[VendorOnboardingState]] = {
    VendorOnboardingState.INVITED: frozenset({
        VendorOnboardingState.AWAITING_KYC,
        VendorOnboardingState.ESCALATED,
        VendorOnboardingState.REJECTED,
        VendorOnboardingState.ABANDONED,
    }),
    VendorOnboardingState.AWAITING_KYC: frozenset({
        VendorOnboardingState.AWAITING_BANK,
        VendorOnboardingState.ESCALATED,
        VendorOnboardingState.REJECTED,
        VendorOnboardingState.ABANDONED,
    }),
    VendorOnboardingState.AWAITING_BANK: frozenset({
        # V1 direct edge: vendor submits bank details in the portal,
        # we mark verified. When Adyen/TrueLayer adapters land this
        # same edge will be gated on provider-reported verification;
        # failure paths will stay in AWAITING_BANK or escalate.
        VendorOnboardingState.BANK_VERIFIED,
        VendorOnboardingState.ESCALATED,
        VendorOnboardingState.REJECTED,
        VendorOnboardingState.ABANDONED,
    }),
    VendorOnboardingState.BANK_VERIFIED: frozenset({
        VendorOnboardingState.READY_FOR_ERP,
        VendorOnboardingState.ESCALATED,
        VendorOnboardingState.REJECTED,
    }),
    VendorOnboardingState.READY_FOR_ERP: frozenset({
        VendorOnboardingState.ACTIVE,
        # ERP create_vendor failed and the retry queue exhausted —
        # human intervention required.
        VendorOnboardingState.ESCALATED,
        VendorOnboardingState.REJECTED,
    }),
    VendorOnboardingState.ESCALATED: frozenset({
        # AP Manager intervention can restart from any pre-active stage
        # once the vendor re-engages. The recovery target is recorded
        # on the audit event so the timeline is reconstructable.
        VendorOnboardingState.INVITED,
        VendorOnboardingState.AWAITING_KYC,
        VendorOnboardingState.AWAITING_BANK,
        VendorOnboardingState.BANK_VERIFIED,
        VendorOnboardingState.READY_FOR_ERP,
        VendorOnboardingState.REJECTED,
        VendorOnboardingState.ABANDONED,
    }),
    VendorOnboardingState.ACTIVE: frozenset(),       # terminal
    VendorOnboardingState.REJECTED: frozenset(),     # terminal
    VendorOnboardingState.ABANDONED: frozenset(),    # terminal
}

TERMINAL_STATES: FrozenSet[VendorOnboardingState] = frozenset({
    VendorOnboardingState.ACTIVE,
    VendorOnboardingState.REJECTED,
    VendorOnboardingState.ABANDONED,
})

# All valid state strings — exposed for DB-level constraint generation
# and for API/UI dropdown rendering.
VALID_STATE_VALUES: FrozenSet[str] = frozenset(s.value for s in VendorOnboardingState)


# Pre-active states are the ones the chase loop watches. Once the
# session is in any pre-active state, the auto-chase scheduler may
# fire 24h/48h/72h reminders against it.
PRE_ACTIVE_STATES: FrozenSet[VendorOnboardingState] = frozenset({
    VendorOnboardingState.INVITED,
    VendorOnboardingState.AWAITING_KYC,
    VendorOnboardingState.AWAITING_BANK,
})


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

    Returns the canonical state string when the input matches a member
    of :class:`VendorOnboardingState`. Unknown values are returned
    unchanged so that downstream validation produces a meaningful
    ``IllegalVendorOnboardingTransitionError`` rather than a silent
    pass-through.
    """
    raw_lower = (raw or "").strip().lower()
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
