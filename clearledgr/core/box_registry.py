"""Box type registry.

The Box is the product — one persistent home per workflow instance.
This module makes that first-class in code: each workflow type
(``ap_item``, ``vendor_onboarding_session``, and the upcoming
``clawback_box``) registers the shape its Boxes take so shared
primitives (audit trail, health observability, reconstructability
checks) can dispatch by ``box_type`` instead of hardcoding AP.

The registry is deliberately flat: a dict of :class:`BoxType`
dataclasses keyed by name. No inheritance. Box-level invariants
(atomicity, timeline append-only, Rule 1 pre-write,
reconstructability) live in the stores and execution/coordination
layer and consult the registry when they need per-type policy
(open states, exception states, source table).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional

from clearledgr.core.ap_states import APState
from clearledgr.core.ap_states import VALID_TRANSITIONS as AP_VALID_TRANSITIONS
from clearledgr.core.vendor_onboarding_states import (
    PRE_ACTIVE_STATES as VO_PRE_ACTIVE_STATES,
)
from clearledgr.core.vendor_onboarding_states import (
    TERMINAL_STATES as VO_TERMINAL_STATES,
)
from clearledgr.core.vendor_onboarding_states import (
    VALID_TRANSITIONS as VO_VALID_TRANSITIONS,
)
from clearledgr.core.vendor_onboarding_states import VendorOnboardingState


@dataclass(frozen=True)
class BoxType:
    """Per-workflow-type Box shape.

    Attributes
    ----------
    name
        Canonical identifier written to ``audit_events.box_type`` and
        ``llm_call_log.box_type``. Stable contract; do not change without a
        migration.
    source_table
        The table whose rows are Boxes of this type.
    state_field
        Column on ``source_table`` that carries the current state.
    open_states
        States an active (non-terminal) Box can occupy. Used by
        ``get_box_health`` to compute time-in-stage buckets.
    terminal_states
        States that end a Box's lifecycle. Excluded from health views.
    exception_states
        Open states that indicate a stuck/blocked/exceptional Box.
        Bucketed as "exception clusters" in health output.
    stuck_thresholds
        Optional per-state minute thresholds beyond which a Box in that
        state is considered stuck. Falls back to a caller-provided
        default when absent.
    """

    name: str
    source_table: str
    state_field: str
    open_states: FrozenSet[str]
    terminal_states: FrozenSet[str]
    exception_states: FrozenSet[str]
    stuck_thresholds: Dict[str, int] = field(default_factory=dict)


BOX_TYPES: Dict[str, BoxType] = {}


def register(box_type: BoxType) -> None:
    """Register a Box type. Idempotent for identical re-registration."""
    existing = BOX_TYPES.get(box_type.name)
    if existing is not None and existing != box_type:
        raise ValueError(
            f"BoxType {box_type.name!r} is already registered with a "
            f"different definition"
        )
    BOX_TYPES[box_type.name] = box_type


def get(name: str) -> BoxType:
    """Return the BoxType for *name*. Raises KeyError if unknown."""
    if name not in BOX_TYPES:
        raise KeyError(f"Unknown box_type: {name!r}")
    return BOX_TYPES[name]


def load_box(box_type: str, box_id: str, db: Any) -> Optional[Dict[str, Any]]:
    """Load one Box row by (type, id). Returns the underlying store row.

    Dispatches to the appropriate store method based on ``box_type``.
    This is the generic read primitive other Box-level code (audit
    joins, health drill-down) can use without knowing which table a
    Box lives in.
    """
    bt = get(box_type)
    if bt.source_table == "ap_items":
        return db.get_ap_item(box_id)
    if bt.source_table == "vendor_onboarding_sessions":
        return db.get_vendor_onboarding_session(box_id)
    raise NotImplementedError(
        f"load_box has no loader for source_table={bt.source_table!r}"
    )


# ---------------------------------------------------------------------------
# Built-in registrations
# ---------------------------------------------------------------------------

_AP_TERMINAL = {
    APState.POSTED_TO_ERP.value,
    APState.REJECTED.value,
    APState.CLOSED.value,
    APState.REVERSED.value,
}
_AP_ALL = {s.value for s in APState}
_AP_OPEN = _AP_ALL - _AP_TERMINAL
_AP_EXCEPTION = {APState.NEEDS_INFO.value, APState.FAILED_POST.value}


register(BoxType(
    name="ap_item",
    source_table="ap_items",
    state_field="state",
    open_states=frozenset(_AP_OPEN),
    terminal_states=frozenset(_AP_TERMINAL),
    exception_states=frozenset(_AP_EXCEPTION),
))


_VO_TERMINAL = {s.value for s in VO_TERMINAL_STATES}
_VO_ALL = {s.value for s in VendorOnboardingState}
_VO_OPEN = _VO_ALL - _VO_TERMINAL
# Blocked is the vendor-onboarding exception signal; pre-active states
# can also be stuck waiting for vendor response but blocked is the
# explicit "something is wrong" state.
_VO_EXCEPTION = {VendorOnboardingState.BLOCKED.value}


register(BoxType(
    name="vendor_onboarding_session",
    source_table="vendor_onboarding_sessions",
    state_field="state",
    open_states=frozenset(_VO_OPEN),
    terminal_states=frozenset(_VO_TERMINAL),
    exception_states=frozenset(_VO_EXCEPTION),
))


__all__ = [
    "BoxType",
    "BOX_TYPES",
    "register",
    "get",
    "load_box",
]
