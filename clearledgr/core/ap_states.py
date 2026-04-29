"""Canonical AP state machine as defined in PLAN.md Section 2.1.

All AP item state transitions MUST go through this module. No client or
service may force state transitions directly.

Primary path:
    received -> validated -> needs_approval -> approved -> ready_to_post
             -> posted_to_erp -> closed

Exception paths:
    validated -> needs_info
    needs_approval -> needs_info
    needs_approval -> rejected
    ready_to_post -> failed_post
    failed_post -> ready_to_post  (retry)
    needs_info -> validated       (resubmit)

Override-window reversal path (DESIGN_THESIS.md §8):
    posted_to_erp -> reversed (terminal)
        A human can reverse an autonomous ERP post within the override
        window (default 15 min). The reversal calls the Phase 1.3
        ERP-level reverse_bill API, which creates a cancelling document
        or soft-deletes the bill depending on the ERP. After a
        successful reversal the AP item lands in ``reversed`` and is
        then closed out. Reversal after the window has expired is NOT
        a state-machine action — the window is finalized and only an
        out-of-band intervention (e.g., a manual credit note) can
        undo it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field as _dc_field
from enum import Enum
from typing import Any, Dict, FrozenSet, Optional

logger = logging.getLogger(__name__)


class APState(str, Enum):
    """Canonical AP item states from PLAN.md 2.1 + Phase 1.4 reversal."""

    RECEIVED = "received"
    VALIDATED = "validated"
    NEEDS_INFO = "needs_info"
    NEEDS_APPROVAL = "needs_approval"
    # Wave 6 / H1 — dual-approval (two-person) state. High-value
    # bills (above ``settings_json[routing_thresholds]
    # [dual_approval_threshold]``) land here after the first
    # signature. A second, distinct approver is required to advance
    # to APPROVED. Self-approval is enforced by the apply layer
    # (first_approver != second_approver).
    NEEDS_SECOND_APPROVAL = "needs_second_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    READY_TO_POST = "ready_to_post"
    POSTED_TO_ERP = "posted_to_erp"
    FAILED_POST = "failed_post"
    # DESIGN_THESIS.md §3 Gmail Power Features: snooze a thread —
    # archive and return it to the top of the queue after a set time.
    # The pre-snooze state is stored in metadata so the reaper can
    # restore it when the snooze expires.
    SNOOZED = "snoozed"
    # DESIGN_THESIS.md §8 override-window reversal outcome. The bill
    # was posted and then reversed at the ERP level within the window.
    REVERSED = "reversed"
    CLOSED = "closed"
    # Wave 2 / C1 — payment-tracking lifecycle. Per AP cycle reference
    # doc Stage 7-9 + Mo's brief: "we don't execute payment but we
    # track it to complete the process". Auditor traceability needs
    # the chain bill posted → payment scheduled → payment executed →
    # workflow closed visible in our Box, even when payment execution
    # is the customer's ERP/bank doing the work.
    AWAITING_PAYMENT = "awaiting_payment"
    PAYMENT_IN_FLIGHT = "payment_in_flight"
    PAYMENT_EXECUTED = "payment_executed"
    PAYMENT_FAILED = "payment_failed"


VALID_TRANSITIONS: Dict[APState, FrozenSet[APState]] = {
    APState.RECEIVED: frozenset({APState.VALIDATED, APState.CLOSED, APState.REJECTED}),
    APState.VALIDATED: frozenset({APState.NEEDS_APPROVAL, APState.NEEDS_INFO, APState.SNOOZED, APState.CLOSED}),
    APState.NEEDS_INFO: frozenset({APState.VALIDATED, APState.SNOOZED, APState.CLOSED}),
    # Wave 6 / H1 — needs_approval can either advance to APPROVED
    # (single-signature path, low-value) or to NEEDS_SECOND_APPROVAL
    # (dual-signature path, above the org's threshold).
    APState.NEEDS_APPROVAL: frozenset({
        APState.APPROVED, APState.NEEDS_SECOND_APPROVAL,
        APState.REJECTED, APState.NEEDS_INFO, APState.SNOOZED,
        APState.CLOSED,
    }),
    # Wave 6 / H1 — second-approval state. Second approver advances
    # to APPROVED, or rejects, or sends back for info. A revoke of
    # the first approver's signature returns to NEEDS_APPROVAL.
    APState.NEEDS_SECOND_APPROVAL: frozenset({
        APState.APPROVED, APState.NEEDS_APPROVAL,
        APState.REJECTED, APState.NEEDS_INFO, APState.SNOOZED,
        APState.CLOSED,
    }),
    APState.APPROVED: frozenset({APState.READY_TO_POST, APState.NEEDS_INFO, APState.CLOSED}),
    APState.REJECTED: frozenset({APState.CLOSED}),
    APState.READY_TO_POST: frozenset({APState.POSTED_TO_ERP, APState.FAILED_POST, APState.CLOSED}),
    # posted_to_erp can advance to AWAITING_PAYMENT (payment-tracking
    # enabled — Wave 2 default), close directly (legacy / disabled
    # path), or be reversed inside the override window.
    APState.POSTED_TO_ERP: frozenset({
        APState.AWAITING_PAYMENT, APState.CLOSED, APState.REVERSED,
    }),
    APState.FAILED_POST: frozenset({APState.READY_TO_POST, APState.SNOOZED, APState.CLOSED}),
    # Snoozed can return to any pre-snooze state (stored in metadata).
    APState.SNOOZED: frozenset({
        APState.VALIDATED, APState.NEEDS_INFO,
        APState.NEEDS_APPROVAL, APState.NEEDS_SECOND_APPROVAL,
        APState.FAILED_POST, APState.CLOSED,
    }),
    # Wave 2 / C1 — payment lifecycle.
    # awaiting_payment: bill is posted, customer's ERP / payment-rail
    # process owns the next move. We sit and listen for ERP webhooks
    # OR a manual confirmation from the operator.
    APState.AWAITING_PAYMENT: frozenset({
        APState.PAYMENT_IN_FLIGHT,
        # Some webhooks / manual confirmations skip the in-flight
        # window (e.g. SAP B1 polling fires only on the cleared
        # outgoing payment).
        APState.PAYMENT_EXECUTED,
        APState.PAYMENT_FAILED,
        # Operator can override-close (cash sale, no-payment-needed,
        # vendor wrote off the invoice). Audit captures the override.
        APState.CLOSED,
        APState.REVERSED,
    }),
    APState.PAYMENT_IN_FLIGHT: frozenset({
        APState.PAYMENT_EXECUTED,
        APState.PAYMENT_FAILED,
        APState.REVERSED,
    }),
    APState.PAYMENT_EXECUTED: frozenset({
        # After remittance advice is sent + bank-rec matched, the
        # workflow closes. Reversed is allowed for clawback /
        # vendor-disputed-after-payment cases (rare but the doc
        # Stage 9 "post-payment dispute" path needs it).
        APState.CLOSED,
        APState.REVERSED,
    }),
    APState.PAYMENT_FAILED: frozenset({
        # Operator decides: retry (back to awaiting), give up
        # (reverse), or close without payment. Hard-recovery paths
        # are explicit in the audit log.
        APState.AWAITING_PAYMENT,
        APState.REVERSED,
        APState.CLOSED,
    }),
    # Reversed is terminal. An item that was posted then reversed is a
    # distinct outcome from an item that was posted and successfully
    # paid out — they should not share the ``closed`` bucket. Keeping
    # ``reversed`` terminal lets the Kanban "Paid" column match strictly
    # on ``closed`` without a reversed-then-closed item flipping into it.
    APState.REVERSED: frozenset(),  # terminal
    APState.CLOSED: frozenset(),    # terminal — successfully completed
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

TERMINAL_STATES = frozenset({APState.REJECTED, APState.REVERSED, APState.CLOSED})

# All valid state strings — used for DB-level enforcement triggers.
VALID_STATE_VALUES: FrozenSet[str] = frozenset(s.value for s in APState)


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


# Retry recoverability hints for `failed_post` handling.
# Used by batch autonomy prechecks to avoid retrying hard failures.
RECOVERABLE_POST_FAILURE_TOKENS = frozenset(
    {
        "timeout",
        "timed out",
        "temporar",
        "transient",
        "service unavailable",
        "network",
        "connection",
        "rate limit",
        "throttle",
        "gateway",
        "http_502",
        "http_503",
        "http_504",
        "retryable",
        "connector_timeout",
    }
)

NON_RECOVERABLE_POST_FAILURE_TOKENS = frozenset(
    {
        "validation",
        "invalid",
        "schema",
        "duplicate",
        "already posted",
        "already_exists",
        "permission",
        "forbidden",
        "unauthorized",
        "auth_failed",
        "erp_not_connected",
        "erp_not_configured",
        "erp_type_unsupported",
        "posting_blocked",
        "rollout_control",
        "no erp connected",
        "not properly configured",
        "erp posting disabled",
        "missing required",
        "unmapped",
        "policy_blocked",
        "realm_id",
        "tenant_id",
        "account_id",
        "configuration_stale",
    }
)


def classify_post_failure_recoverability(
    *,
    last_error: Any = None,
    exception_code: Any = None,
) -> Dict[str, Any]:
    """Classify whether a failed ERP post appears recoverable.

    The classifier is intentionally conservative:
    - explicit non-recoverable hints block retries
    - known transient hints allow retries
    - unknown failures default to recoverable, but with a generic reason
    """

    error_text = str(last_error or "").strip().lower()
    exception_text = str(exception_code or "").strip().lower()
    joined = " ".join(part for part in [error_text, exception_text] if part).strip()

    if not joined:
        return {"recoverable": False, "reason": "non_recoverable_empty_error"}

    for token in NON_RECOVERABLE_POST_FAILURE_TOKENS:
        if token in joined:
            return {
                "recoverable": False,
                "reason": f"non_recoverable_{token.replace(' ', '_')}",
                "matched_token": token,
            }

    for token in RECOVERABLE_POST_FAILURE_TOKENS:
        if token in joined:
            return {
                "recoverable": True,
                "reason": f"recoverable_{token.replace(' ', '_')}",
                "matched_token": token,
            }

    return {"recoverable": False, "reason": "non_recoverable_unclassified"}


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


# ==================== WorkflowStateMachine protocol conformance ====================

AP_TERMINAL_STATES: FrozenSet[APState] = frozenset({APState.REJECTED, APState.CLOSED})


class _APStateMachine:
    """Satisfies WorkflowStateMachine protocol via structural typing."""

    @staticmethod
    def states() -> FrozenSet[str]:
        return frozenset(s.value for s in APState)

    @staticmethod
    def transitions() -> Dict[str, FrozenSet[str]]:
        return {k.value: frozenset(v.value for v in vs) for k, vs in VALID_TRANSITIONS.items()}

    @staticmethod
    def terminal_states() -> FrozenSet[str]:
        return frozenset(s.value for s in AP_TERMINAL_STATES)

    @staticmethod
    def validate_transition(current: str, target: str) -> bool:
        return validate_transition(current, target)

    @staticmethod
    def normalize(raw: str) -> str:
        return normalize_state(raw)


AP_STATE_MACHINE = _APStateMachine()
