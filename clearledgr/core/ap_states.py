"""Canonical AP state machine as defined in PLAN.md Section 2.1.

All AP item state transitions MUST go through this module. No client or
service may force state transitions directly.

Primary path:
    received -> validated -> needs_approval -> approved -> ready_to_post
             -> posted_to_erp -> closed

Exception paths:
    validated -> needs_info
    needs_approval -> rejected
    ready_to_post -> failed_post
    failed_post -> ready_to_post  (retry)
    needs_info -> validated       (resubmit)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field as _dc_field
from enum import Enum
from typing import Any, Dict, FrozenSet, Optional

logger = logging.getLogger(__name__)


class APState(str, Enum):
    """Canonical AP item states from PLAN.md 2.1."""

    RECEIVED = "received"
    VALIDATED = "validated"
    NEEDS_INFO = "needs_info"
    NEEDS_APPROVAL = "needs_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    READY_TO_POST = "ready_to_post"
    POSTED_TO_ERP = "posted_to_erp"
    FAILED_POST = "failed_post"
    CLOSED = "closed"


VALID_TRANSITIONS: Dict[APState, FrozenSet[APState]] = {
    APState.RECEIVED: frozenset({APState.VALIDATED}),
    APState.VALIDATED: frozenset({APState.NEEDS_APPROVAL, APState.NEEDS_INFO}),
    APState.NEEDS_INFO: frozenset({APState.VALIDATED}),
    APState.NEEDS_APPROVAL: frozenset({APState.APPROVED, APState.REJECTED}),
    APState.APPROVED: frozenset({APState.READY_TO_POST}),
    APState.REJECTED: frozenset(),  # terminal
    APState.READY_TO_POST: frozenset({APState.POSTED_TO_ERP, APState.FAILED_POST}),
    APState.POSTED_TO_ERP: frozenset({APState.CLOSED}),
    APState.FAILED_POST: frozenset({APState.READY_TO_POST}),  # retry
    APState.CLOSED: frozenset(),  # terminal
}

# Mapping from legacy status strings to canonical states.
# Used during migration and for backward compatibility.
LEGACY_STATE_MAP: Dict[str, APState] = {
    "new": APState.RECEIVED,
    "pending": APState.NEEDS_APPROVAL,
    "pending_approval": APState.NEEDS_APPROVAL,
    "approved": APState.APPROVED,
    "posted": APState.POSTED_TO_ERP,
    "rejected": APState.REJECTED,
    "failed": APState.FAILED_POST,
    "closed": APState.CLOSED,
}

TERMINAL_STATES = frozenset({APState.REJECTED, APState.CLOSED})


class IllegalTransitionError(ValueError):
    """Raised when an AP item state transition violates the state machine."""

    def __init__(self, current: str, target: str, ap_item_id: str = ""):
        self.current = current
        self.target = target
        self.ap_item_id = ap_item_id
        super().__init__(
            f"Illegal AP state transition: {current!r} -> {target!r}"
            + (f" (ap_item_id={ap_item_id})" if ap_item_id else "")
        )


def normalize_state(raw: str) -> str:
    """Convert a legacy or canonical state string to its canonical value.

    Returns the canonical state string, or the original value if unrecognized.
    """
    raw_lower = raw.strip().lower()
    # Already canonical?
    try:
        return APState(raw_lower).value
    except ValueError:
        pass
    # Legacy mapping?
    mapped = LEGACY_STATE_MAP.get(raw_lower)
    if mapped:
        return mapped.value
    return raw_lower


def validate_transition(current: str, target: str) -> bool:
    """Check whether *current* -> *target* is a legal transition."""
    try:
        cur = APState(normalize_state(current))
        tgt = APState(normalize_state(target))
    except ValueError:
        return False
    return tgt in VALID_TRANSITIONS.get(cur, frozenset())


def transition_or_raise(
    current: str, target: str, ap_item_id: str = ""
) -> None:
    """Raise :class:`IllegalTransitionError` if the transition is illegal."""
    if not validate_transition(current, target):
        raise IllegalTransitionError(current, target, ap_item_id)


# Override types that an approver can invoke when bypassing a gate.
OVERRIDE_TYPE_BUDGET = "budget"
OVERRIDE_TYPE_CONFIDENCE = "confidence"
OVERRIDE_TYPE_PO_EXCEPTION = "po_exception"
OVERRIDE_TYPE_MULTI = "multi"


@dataclass
class OverrideContext:
    """Structured context for an override approval decision.

    Captures the policy-level metadata needed for audit compliance when an
    approver bypasses a confidence, budget, or PO-exception gate.  Replaces
    the ad-hoc ``allow_budget_override`` / ``override_justification`` boolean
    pairs with a first-class object that flows through to audit events.

    Fields
    ------
    override_type:
        One of the ``OVERRIDE_TYPE_*`` constants.  Use ``OVERRIDE_TYPE_MULTI``
        when more than one gate is being bypassed simultaneously.
    justification:
        Human-readable reason provided by the approver.
    actor_id:
        Identity of the approver triggering the override (email / user ID).
    policy_version:
        Version string of the override policy in effect at decision time.
        Defaults to ``"v1"`` until a versioned policy registry is introduced.
    confidence_threshold_used:
        Confidence threshold (0.0–1.0) that was in effect when the gate fired.
        ``None`` for non-confidence overrides.
    extra:
        Arbitrary additional context for extensibility (e.g. GL account,
        PO number) without breaking the dataclass contract.
    """

    override_type: str
    justification: str
    actor_id: str
    policy_version: str = "v1"
    confidence_threshold_used: Optional[float] = None
    extra: Dict[str, Any] = _dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for audit event metadata."""
        d: Dict[str, Any] = {
            "override_type": self.override_type,
            "justification": self.justification,
            "actor_id": self.actor_id,
            "policy_version": self.policy_version,
        }
        if self.confidence_threshold_used is not None:
            d["confidence_threshold_used"] = self.confidence_threshold_used
        if self.extra:
            d.update(self.extra)
        return d
