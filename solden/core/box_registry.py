"""Box type registry.

The Box is the product — one persistent home per workflow instance.
This module makes that first-class in code: each workflow type
registers the shape its Boxes take so shared primitives (audit trail,
health observability, reconstructability checks) can dispatch by
``box_type`` instead of hardcoding AP.

Three BoxTypes are registered today:

- ``ap_item`` — the production wedge; the operator-facing record.
- ``purchase_order`` — an AP-*peer* (stands alone, no parent FK). This
  is the real proof the manifesto's "the architecture that runs AP runs
  procurement / compliance / vendor onboarding" generalizes beyond a
  single domain.
- ``bank_match`` — AP-*subordinate*: every Box carries a
  ``parent_ap_item_id`` back to its AP item; the typed sub-workflow for
  the closing leg, not an independent domain.

Tenant-declared ``WorkflowSpec`` types ride this same registry via the
dynamic resolver (see :func:`set_dynamic_resolver` / :func:`resolve`),
so the spine is domain-general by construction, not by special-casing.

The ``vendor_onboarding_session`` registration was removed when vendor
onboarding was deprioritized per the AP-as-wedge product call (see
``memory/project_vendor_onboarding_subordinate.md``). The manifesto
names vendor onboarding as a generalization target, so the underlying
state machine + table + service code stay in the repo as option-value;
this registry simply doesn't surface VO Boxes to the runtime today.

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
from solden.core.purchase_order_states import PO_TERMINAL_STATES
from solden.services.purchase_orders import POStatus


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
    gated_actions
        Action names (engine planner vocabulary, e.g. ``"post_bill"``) that
        require the autonomy/deliberation gate before they may run
        autonomously for this Box type. Empty = no action is gated (the type
        has no risky autonomous writes). Declaring governance per type is what
        lets a second Box type plug into the gate instead of the engine
        hardcoding ``box_type == "ap_item"``.
    governance_skill_id
        The skill whose deliberation gate governs this type's gated actions
        (e.g. ``"ap_v1"`` for ap_item). ``None`` with a non-empty
        ``gated_actions`` means the type declares risky actions but no way to
        govern them yet — the engine fails closed (denies autonomous execution,
        requires human approval) rather than running them ungated.
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
    gated_actions: FrozenSet[str] = field(default_factory=frozenset)
    governance_skill_id: Optional[str] = None


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
    """Return the statically-registered BoxType for *name*. KeyError if unknown.

    Only built-in types and code-declared specs live in ``BOX_TYPES``.
    Tenant-authored DB specs are resolved per-org via :func:`resolve`.
    """
    if name not in BOX_TYPES:
        raise KeyError(f"Unknown box_type: {name!r}")
    return BOX_TYPES[name]


# A tenant DB spec can't be a global registry entry (two orgs may define the
# same box_type name differently), so the workflow layer installs an org-aware
# resolver here that builds a transient BoxType from the resolved spec.
_DYNAMIC_RESOLVER: Optional[Any] = None


def set_dynamic_resolver(fn: Optional[Any]) -> None:
    """Install an org-aware ``(box_type, organization_id) -> BoxType|None`` hook."""
    global _DYNAMIC_RESOLVER
    _DYNAMIC_RESOLVER = fn


def resolve(name: str, organization_id: Optional[str] = None) -> BoxType:
    """Resolve a BoxType, statically or via the org-aware dynamic resolver.

    Used where per-type policy (e.g. ``exception_state``) is needed for a type
    that may be a tenant DB spec. Raises KeyError if unresolvable.
    """
    bt = BOX_TYPES.get(name)
    if bt is not None:
        return bt
    if _DYNAMIC_RESOLVER is not None:
        bt = _DYNAMIC_RESOLVER(name, organization_id)
        if bt is not None:
            return bt
    raise KeyError(f"Unknown box_type: {name!r}")


def _dispatch_source_table(box_type: str) -> str:
    """Source table for CRUD dispatch.

    Statically-registered types report their own table; any other type is a
    declarative spec riding the shared ``boxes`` table. The generic store
    resolves the governing spec itself (from the box row / payload org), so
    dispatch needs no org context.
    """
    bt = BOX_TYPES.get(box_type)
    return bt.source_table if bt is not None else "boxes"


def get_box(box_type: str, box_id: str, db: Any) -> Optional[Dict[str, Any]]:
    """Load one Box row by (type, id). Returns the underlying store row.

    Dispatches to the appropriate store method based on ``box_type``.
    This is the generic read primitive other Box-level code (audit
    joins, health drill-down, the coordination engine) can use without
    knowing which table a Box lives in.
    """
    source_table = _dispatch_source_table(box_type)
    if source_table == "ap_items":
        return db.get_ap_item(box_id)
    if source_table == "bank_match_boxes":
        return db.get_bank_match(box_id)
    if source_table == "purchase_orders":
        return db.get_purchase_order(box_id)
    if source_table == "boxes":
        return db.get_generic_box(box_type, box_id)
    raise NotImplementedError(
        f"get_box has no loader for source_table={source_table!r}"
    )


def create_box(box_type: str, payload: Dict[str, Any], db: Any) -> Dict[str, Any]:
    """Create a Box of *box_type*. Dispatches to the per-type store insert.

    Generic counterpart to :func:`get_box` so the engine can open a Box
    without naming a table.
    """
    source_table = _dispatch_source_table(box_type)
    if source_table == "ap_items":
        return db.create_ap_item(payload)
    if source_table == "bank_match_boxes":
        return db.create_bank_match(payload)
    if source_table == "purchase_orders":
        return db.create_purchase_order_box(payload)
    if source_table == "boxes":
        return db.create_generic_box(box_type, payload)
    raise NotImplementedError(
        f"create_box has no creator for source_table={source_table!r}"
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
    source_table = _dispatch_source_table(box_type)
    if source_table == "ap_items":
        patch = dict(fields)
        if state is not None:
            patch["state"] = state
        return db.update_ap_item(box_id, **patch)
    if source_table == "bank_match_boxes":
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
    if source_table == "purchase_orders":
        if state is None:
            raise ValueError("update_box for purchase_order requires a 'state'")
        if fields:
            raise ValueError(
                "update_box for purchase_order accepts only state/actor_id/reason; "
                f"got extra fields {sorted(fields)}"
            )
        return db.update_purchase_order_state(
            box_id, state, actor_id=actor_id or "", reason=reason or ""
        )
    if source_table == "boxes":
        if state is None:
            raise ValueError("update_box for boxes requires a 'state'")
        if fields:
            raise ValueError(
                "update_box for boxes accepts only state/actor_id/reason; "
                f"got extra fields {sorted(fields)}"
            )
        return db.update_generic_box_state(
            box_type, box_id, state, actor_id=actor_id or "", reason=reason or ""
        )
    raise NotImplementedError(
        f"update_box has no writer for source_table={source_table!r}"
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


# Risky financial writes that must clear the ap_v1 autonomy/deliberation gate
# before the agent runs them unsupervised. The engine maps these action names
# to governance tokens (see coordination_engine._GOVERNANCE_GATED_ACTIONS);
# this set is what the engine checks to decide "is this action gated for this
# Box type". Keep the two in sync.
_AP_GATED_ACTIONS = frozenset({
    "post_bill",
    "schedule_payment",
    "reverse_erp_post",
    "freeze_vendor_payments",
})

register(BoxType(
    name="ap_item",
    source_table="ap_items",
    state_field="state",
    open_states=frozenset(_AP_OPEN),
    terminal_states=frozenset(_AP_TERMINAL),
    exception_states=frozenset(_AP_EXCEPTION),
    initial_state=APState.RECEIVED.value,
    exception_state=APState.NEEDS_INFO.value,
    gated_actions=_AP_GATED_ACTIONS,
    governance_skill_id="ap_v1",
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


# purchase_order — Solden's third BoxType, and the first AP-*peer*
# (bank_match was AP-subordinate via a parent FK; a PO stands alone).
# Reuses the existing purchase_orders table as its source; the row's
# ``status`` column is the state field (aliased to ``state`` by the
# store deserializer). Like bank_match it has no human-stall state —
# a failed action raises a box_exception rather than parking the box.
_PO_TERMINAL = {s.value for s in PO_TERMINAL_STATES}
_PO_OPEN = {s.value for s in POStatus} - _PO_TERMINAL

register(BoxType(
    name="purchase_order",
    source_table="purchase_orders",
    state_field="status",
    open_states=frozenset(_PO_OPEN),
    terminal_states=frozenset(_PO_TERMINAL),
    exception_states=frozenset(),
    initial_state=POStatus.DRAFT.value,
    exception_state=None,
))


__all__ = [
    "BoxType",
    "BOX_TYPES",
    "register",
    "get",
    "resolve",
    "set_dynamic_resolver",
    "get_box",
    "create_box",
    "update_box",
]
