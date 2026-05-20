"""Box type registry.

The Box is the product — one persistent home per workflow instance.
This module makes that first-class in code: each workflow type
registers the shape its Boxes take so shared primitives (audit trail,
health observability, reconstructability checks) can dispatch by
``box_type`` instead of hardcoding AP.

As of the manifesto-truthing pass (2026-05-14) two BoxTypes are
registered: ``ap_item`` and ``bank_match``. The second proves the
architectural primitive generalizes — the manifesto's "the
architecture that runs AP runs procurement / compliance / vendor
onboarding" claim no longer rests on a single type.

bank_match is **AP-subordinate**: every bank_match Box carries a
``parent_ap_item_id`` FK back to its AP item. AP stays the
operator-facing record; bank_match is the typed sub-workflow for
the closing leg.

The ``vendor_onboarding_session`` registration was removed when
vendor onboarding was deprioritized per the AP-as-wedge product call
(see ``memory/project_vendor_onboarding_subordinate.md``). The
underlying state machine + table + service code remain in the repo
as option-value; this registry just no longer surfaces VO Boxes to
the runtime.

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

from solden.core.ap_states import APState
from solden.core.bank_match_states import (
    BANK_MATCH_TERMINAL_STATES,
    BankMatchState,
)


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
    initial_state
        The state a freshly-created Box of this type enters. Lets the
        planner/coordination engine open a Box without hardcoding a
        per-type literal (e.g. AP's ``"received"``).
    exception_state
        The state a Box moves to when work stalls and a human is needed.
        ``None`` means this type has no stuck state (e.g. bank_match): the
        engine then raises a box_exception without moving state.
    """

    name: str
    source_table: str
    state_field: str
    open_states: FrozenSet[str]
    terminal_states: FrozenSet[str]
    exception_states: FrozenSet[str]
    stuck_thresholds: Dict[str, int] = field(default_factory=dict)
    initial_state: str = ""
    exception_state: Optional[str] = None


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


def get_box(box_type: str, box_id: str, db: Any) -> Optional[Dict[str, Any]]:
    """Load one Box row by (type, id). Returns the underlying store row.

    Dispatches to the appropriate store method based on ``box_type``.
    This is the generic read primitive other Box-level code (audit
    joins, health drill-down, the coordination engine) can use without
    knowing which table a Box lives in.
    """
    bt = get(box_type)
    if bt.source_table == "ap_items":
        return db.get_ap_item(box_id)
    if bt.source_table == "bank_match_boxes":
        return db.get_bank_match(box_id)
    raise NotImplementedError(
        f"get_box has no loader for source_table={bt.source_table!r}"
    )


def load_box(box_type: str, box_id: str, db: Any) -> Optional[Dict[str, Any]]:
    """Deprecated alias for :func:`get_box`. Retained for existing callers."""
    return get_box(box_type, box_id, db)


def create_box(box_type: str, payload: Dict[str, Any], db: Any) -> Dict[str, Any]:
    """Create a Box of *box_type*. Dispatches to the per-type store insert.

    Generic counterpart to :func:`get_box` so the engine can open a Box
    without naming a table.
    """
    bt = get(box_type)
    if bt.source_table == "ap_items":
        return db.create_ap_item(payload)
    if bt.source_table == "bank_match_boxes":
        return db.create_bank_match(payload)
    raise NotImplementedError(
        f"create_box has no creator for source_table={bt.source_table!r}"
    )


def update_box(
    box_type: str,
    box_id: str,
    db: Any,
    *,
    state: Optional[str] = None,
    actor_id: Optional[str] = None,
    reason: Optional[str] = None,
    **fields: Any,
) -> Any:
    """Update a Box of *box_type*. Dispatches to the per-type store writer.

    ``state`` / ``actor_id`` / ``reason`` are explicit so a caller can
    drive a transition uniformly across types. The two registered types
    have deliberately different write shapes and this seam encodes that
    rather than papering over it:

    - ``ap_items`` takes a whitelisted column patch (``update_ap_item``).
      ``state`` folds into the patch; ``actor_id`` / ``reason`` are
      bank_match transition metadata and are ignored for AP (its audit
      row attributes the actor through its own path).
    - ``bank_match_boxes`` has no arbitrary column patch — only a
      validated state advance (``update_bank_match_state``), which
      requires ``state`` and a non-empty actor. Arbitrary ``**fields``
      are rejected rather than silently dropped.
    """
    bt = get(box_type)
    if bt.source_table == "ap_items":
        patch = dict(fields)
        if state is not None:
            patch["state"] = state
        return db.update_ap_item(box_id, **patch)
    if bt.source_table == "bank_match_boxes":
        if state is None:
            raise ValueError("update_box for bank_match requires a 'state'")
        if fields:
            raise ValueError(
                "update_box for bank_match accepts only state/actor_id/reason; "
                f"got extra fields {sorted(fields)}"
            )
        return db.update_bank_match_state(
            box_id, state, actor_id=actor_id or "", reason=reason or ""
        )
    raise NotImplementedError(
        f"update_box has no writer for source_table={bt.source_table!r}"
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
    initial_state=APState.RECEIVED.value,
    exception_state=APState.NEEDS_INFO.value,
))


# bank_match — Solden's second BoxType (manifesto-truthing pass).
# AP-subordinate: every Box references a parent ap_item via
# parent_ap_item_id. Same audit funnel, same export shape, distinct
# lifecycle.
_BANK_MATCH_TERMINAL = {s.value for s in BANK_MATCH_TERMINAL_STATES}
_BANK_MATCH_OPEN = {BankMatchState.PROPOSED.value}

register(BoxType(
    name="bank_match",
    source_table="bank_match_boxes",
    state_field="state",
    open_states=frozenset(_BANK_MATCH_OPEN),
    terminal_states=frozenset(_BANK_MATCH_TERMINAL),
    exception_states=frozenset(),  # bank_match has no "stuck" state by design
    initial_state=BankMatchState.PROPOSED.value,
    exception_state=None,  # no human-stall state; raise an exception instead
))


__all__ = [
    "BoxType",
    "BOX_TYPES",
    "register",
    "get",
    "get_box",
    "load_box",
    "create_box",
    "update_box",
]
