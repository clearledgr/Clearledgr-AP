"""Workflow State Machine Protocol.

Structural typing contract for workflow-specific state machines.
AP, Reconciliation, FP&A, and future workflows each define their own
states and transitions. This protocol lets the runtime validate
transitions generically without knowing the workflow type.

Usage:
    from clearledgr.core.ap_states import AP_STATE_MACHINE
    from clearledgr.core.recon_states import RECON_STATE_MACHINE

    # Both satisfy WorkflowStateMachine by convention (structural typing)
    machine = AP_STATE_MACHINE
    assert machine.validate_transition("needs_approval", "approved")
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Protocol, runtime_checkable


@runtime_checkable
class WorkflowStateMachine(Protocol):
    """Protocol for workflow-specific state machines.

    Each workflow (AP, Reconciliation, Close, FP&A) implements this
    contract with its own states and transition rules.
    """

    @staticmethod
    def states() -> FrozenSet[str]:
        """All valid states in this workflow."""
        ...

    @staticmethod
    def transitions() -> Dict[str, FrozenSet[str]]:
        """Map of state → set of valid target states."""
        ...

    @staticmethod
    def terminal_states() -> FrozenSet[str]:
        """States that cannot transition further."""
        ...

    @staticmethod
    def validate_transition(current: str, target: str) -> bool:
        """Return True if current → target is a valid transition."""
        ...

    @staticmethod
    def normalize(raw: str) -> str:
        """Normalize a raw state string to the canonical form."""
        ...
