"""PRD v1 AP state machine and transition helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


AP_STATES = {
    "received",
    "validated",
    "needs_info",
    "needs_approval",
    "approved",
    "ready_to_post",
    "posted_to_erp",
    "closed",
    "rejected",
    "failed_post",
}


VALID_TRANSITIONS: Dict[str, set[str]] = {
    "received": {"validated"},
    "validated": {"needs_info", "needs_approval"},
    "needs_info": {"validated"},
    "needs_approval": {"approved", "rejected"},
    "approved": {"ready_to_post", "rejected"},
    "ready_to_post": {"posted_to_erp", "failed_post"},
    "failed_post": {"ready_to_post"},
    "posted_to_erp": {"closed"},
    "closed": set(),
    "rejected": set(),  # terminal unless explicit resubmission path creates a new item
}


class APStateError(ValueError):
    """Raised when an invalid transition is attempted."""


@dataclass(frozen=True)
class TransitionRequest:
    ap_item_id: str
    to_state: str
    actor_type: str
    actor_id: str
    reason: str = ""
    idempotency_key: Optional[str] = None
    metadata: Optional[Dict] = None


def assert_valid_transition(from_state: str, to_state: str) -> None:
    if from_state not in AP_STATES or to_state not in AP_STATES:
        raise APStateError(f"Unknown state transition: {from_state} -> {to_state}")
    allowed = VALID_TRANSITIONS.get(from_state, set())
    if to_state not in allowed:
        raise APStateError(f"Invalid transition: {from_state} -> {to_state}")
