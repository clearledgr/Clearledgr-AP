"""Declarative WorkflowSpec — define a Box type from data, not code.

Solden's three original Box types (``ap_item``, ``bank_match``,
``purchase_order``) were each hand-built across ~10 files: a per-type
table, a ``*_states.py`` transition graph, a store mixin, a
``box_registry`` entry, routes, and a ``main.py`` allowlist line. The
runtime's critical path (CoordinationEngine, audit hash-chain,
exception queue, ``Plan.box_type``) is already Box-type-agnostic, so
the only thing that still needs bespoke code is *declaring* a type.

This module removes that. A :class:`WorkflowSpec` carries everything a
Box type needs as data — its states, transitions, the action→state map,
its declared data fields — and :func:`register_spec` turns that into a
live, registry-registered Box type that rides the single generic
``boxes`` table (see ``generic_box_store.py``). A new built-in type is
now one spec declaration with zero bespoke Python.

The same model serializes to/from JSON (:func:`to_json` /
:func:`from_json`) so tenant-authored specs can live in the DB
(``workflow_specs`` table, Phase 2) and be resolved per-tenant at
runtime via the resolver seam (:func:`set_spec_resolver` /
:func:`resolve_spec`). The ``hooks`` / ``conditions`` fields are
declared here but only interpreted once the sandbox layer lands
(Phase 3); until then they are inert metadata.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

CURRENT_WORKFLOW_POLICY_VERSION = "v1"

# Built-in Box types own bespoke tables; a declared spec must not shadow them.
_RESERVED_BOX_TYPES: FrozenSet[str] = frozenset({
    "ap_item", "bank_match", "purchase_order",
})

# Columns the generic ``boxes`` table owns. A declared ``fields`` entry or a
# ``data`` payload key must never collide with these — the store always lets
# the native column win, but we reject the collision at spec-validation time
# so the author finds out early instead of silently losing a field.
RESERVED_DATA_KEYS: FrozenSet[str] = frozenset({
    "id", "state", "box_type", "organization_id",
    "spec_version", "data", "created_at", "updated_at",
})

_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


class IllegalWorkflowTransitionError(ValueError):
    """Raised when a declared-workflow state transition violates its spec."""


@dataclass
class WorkflowSpec:
    """Declarative definition of a Box type.

    A plain (non-frozen) dataclass: specs are only ever stored by their
    ``box_type`` string key and never hashed, so mutable dict/tuple fields
    are safe and ergonomic. The derived :class:`box_registry.BoxType`
    (built in :func:`register_spec`) stays frozen as before.
    """

    box_type: str
    url_slug: str
    states: Tuple[str, ...]
    initial_state: str
    terminal_states: Tuple[str, ...] = ()
    transitions: Dict[str, FrozenSet[str]] = field(default_factory=dict)
    action_states: Dict[str, str] = field(default_factory=dict)
    fields: Tuple[str, ...] = ()
    exception_state: Optional[str] = None
    policy_version: str = CURRENT_WORKFLOW_POLICY_VERSION
    # Storage version this spec was resolved at (1 for code-declared built-ins;
    # the DB row version for tenant specs). Boxes pin this so activating a new
    # version never changes the legal transitions of in-flight Boxes.
    version: int = 1
    # Phase 3 surface — declared now, interpreted by the sandbox later.
    hooks: Dict[str, Any] = field(default_factory=dict)
    conditions: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalize so inline sets/lists and JSON-loaded lists behave the same.
        self.states = tuple(self.states)
        self.terminal_states = tuple(self.terminal_states or ())
        self.transitions = {
            str(k): frozenset(v) for k, v in (self.transitions or {}).items()
        }
        self.action_states = dict(self.action_states or {})
        self.fields = tuple(self.fields or ())
        self.hooks = dict(self.hooks or {})
        self.conditions = dict(self.conditions or {})

    def next_states(self, current: str) -> FrozenSet[str]:
        return self.transitions.get(current, frozenset())


def validate_spec_transition(spec: WorkflowSpec, current: str, target: str) -> bool:
    """True iff *current* → *target* is a declared edge in *spec*."""
    return target in spec.next_states(current)


def validate_spec(spec: WorkflowSpec) -> List[str]:
    """Return a list of human-readable errors. Empty list == valid.

    Catches the spec-authoring mistakes that would otherwise strand a Box:
    undeclared states, unreachable states, dead-end non-terminal states,
    edges out of terminal states, action targets that don't exist, and
    field/box-type names that collide with reserved keys.
    """
    errors: List[str] = []
    states = set(spec.states)

    if not spec.box_type or not _NAME_RE.match(spec.box_type):
        errors.append(
            f"box_type {spec.box_type!r} must be snake_case "
            "([a-z][a-z0-9_]{1,62})"
        )
    if spec.box_type in _RESERVED_BOX_TYPES:
        errors.append(
            f"box_type {spec.box_type!r} is reserved by a built-in Box type"
        )
    if not spec.url_slug or not _SLUG_RE.match(spec.url_slug):
        errors.append(
            f"url_slug {spec.url_slug!r} must be kebab-case "
            "([a-z][a-z0-9-]{1,62})"
        )
    if not states:
        errors.append("states must be non-empty")
    for s in spec.states:
        if not _NAME_RE.match(s):
            errors.append(f"state {s!r} must be snake_case")
    if spec.initial_state not in states:
        errors.append(
            f"initial_state {spec.initial_state!r} is not in states"
        )
    for t in spec.terminal_states:
        if t not in states:
            errors.append(f"terminal_state {t!r} is not in states")
    if spec.exception_state is not None and spec.exception_state not in states:
        errors.append(
            f"exception_state {spec.exception_state!r} is not in states"
        )

    terminal = set(spec.terminal_states)
    for src, targets in spec.transitions.items():
        if src not in states:
            errors.append(f"transition source {src!r} is not a declared state")
        if src in terminal and targets:
            errors.append(
                f"terminal state {src!r} must have no outgoing transitions"
            )
        for tgt in targets:
            if tgt not in states:
                errors.append(
                    f"transition {src!r}->{tgt!r} targets an undeclared state"
                )

    # Every non-terminal state must have a way out, or a Box parks there forever.
    for s in spec.states:
        if s not in terminal and not spec.transitions.get(s):
            errors.append(
                f"state {s!r} is non-terminal but has no outgoing transitions"
            )

    # Reachability from initial_state (only meaningful if initial is valid).
    if spec.initial_state in states:
        seen = {spec.initial_state}
        frontier = [spec.initial_state]
        while frontier:
            cur = frontier.pop()
            for nxt in spec.transitions.get(cur, frozenset()):
                if nxt in states and nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        for s in spec.states:
            if s not in seen:
                errors.append(
                    f"state {s!r} is unreachable from initial_state "
                    f"{spec.initial_state!r}"
                )

    for action, target in spec.action_states.items():
        if not _NAME_RE.match(action):
            errors.append(f"action {action!r} must be snake_case")
        if target not in states:
            errors.append(
                f"action {action!r} targets undeclared state {target!r}"
            )

    for f in spec.fields:
        if f in RESERVED_DATA_KEYS:
            errors.append(f"field {f!r} collides with a reserved box column")

    return errors


# ---------------------------------------------------------------------------
# Spec registry (built-in / code-declared specs)
# ---------------------------------------------------------------------------

_SPECS: Dict[str, WorkflowSpec] = {}


def register_spec(spec: WorkflowSpec) -> WorkflowSpec:
    """Validate, register, and derive a live Box type from *spec*.

    One call does both: stores the spec in the in-process registry AND
    registers the corresponding :class:`box_registry.BoxType` (source
    table ``boxes``) so every generic runtime primitive can dispatch to
    it. Raises ``ValueError`` if the spec is invalid.
    """
    errors = validate_spec(spec)
    if errors:
        raise ValueError(
            f"Invalid WorkflowSpec {spec.box_type!r}: " + "; ".join(errors)
        )
    _SPECS[spec.box_type] = spec
    _register_box_type(spec)
    return spec


def boxtype_from_spec(spec: WorkflowSpec) -> "Any":
    """Derive a (non-registered) ``box_registry.BoxType`` from a spec.

    Used both by :func:`register_spec` (built-ins, registered statically) and
    by the box_registry dynamic resolver (tenant DB specs, resolved per-org
    and never stored in the global registry, since two orgs may define the
    same ``box_type`` name differently).
    """
    from solden.core import box_registry

    terminal = frozenset(spec.terminal_states)
    open_states = frozenset(s for s in spec.states if s not in terminal)
    exc = spec.exception_state
    return box_registry.BoxType(
        name=spec.box_type,
        source_table="boxes",
        state_field="state",
        open_states=open_states,
        terminal_states=terminal,
        exception_states=frozenset({exc}) if exc else frozenset(),
        initial_state=spec.initial_state,
        exception_state=exc,
    )


def _register_box_type(spec: WorkflowSpec) -> None:
    from solden.core import box_registry
    box_registry.register(boxtype_from_spec(spec))


def get_spec(box_type: str) -> Optional[WorkflowSpec]:
    """Return a code-registered spec by box_type, or None."""
    return _SPECS.get(box_type)


def iter_specs() -> List[WorkflowSpec]:
    """All code-registered specs (built-ins). Tenant DB specs are not here."""
    return list(_SPECS.values())


def unregister_spec(box_type: str) -> None:
    """Remove a code-registered spec and its derived BoxType (test cleanup)."""
    _SPECS.pop(box_type, None)
    from solden.core import box_registry
    box_registry.BOX_TYPES.pop(box_type, None)


# ---------------------------------------------------------------------------
# Resolver seam — Phase 2 installs a tenant/version-aware DB resolver here.
# ---------------------------------------------------------------------------

_SpecResolver = Callable[[str, Optional[str], Optional[int]], Optional[WorkflowSpec]]
_SPEC_RESOLVER: Optional[_SpecResolver] = None


def set_spec_resolver(fn: Optional[_SpecResolver]) -> None:
    """Install a tenant/version-aware resolver (Phase 2). None = code-only."""
    global _SPEC_RESOLVER
    _SPEC_RESOLVER = fn


def resolve_spec(
    box_type: str,
    organization_id: Optional[str] = None,
    version: Optional[int] = None,
) -> Optional[WorkflowSpec]:
    """Resolve the spec governing a Box.

    Phase 1: returns the code-registered spec. Phase 2: the installed
    resolver returns the tenant's active (or version-pinned) DB spec,
    falling back to the code registry.
    """
    if _SPEC_RESOLVER is not None:
        spec = _SPEC_RESOLVER(box_type, organization_id, version)
        if spec is not None:
            return spec
    return _SPECS.get(box_type)


# ---------------------------------------------------------------------------
# JSON (de)serialization — for DB-stored tenant specs (Phase 2)
# ---------------------------------------------------------------------------

def to_json(spec: WorkflowSpec) -> Dict[str, Any]:
    return {
        "box_type": spec.box_type,
        "url_slug": spec.url_slug,
        "states": list(spec.states),
        "initial_state": spec.initial_state,
        "terminal_states": list(spec.terminal_states),
        "transitions": {k: sorted(v) for k, v in spec.transitions.items()},
        "action_states": dict(spec.action_states),
        "fields": list(spec.fields),
        "exception_state": spec.exception_state,
        "policy_version": spec.policy_version,
        "hooks": dict(spec.hooks),
        "conditions": dict(spec.conditions),
    }


def from_json(data: Dict[str, Any]) -> WorkflowSpec:
    return WorkflowSpec(
        box_type=data["box_type"],
        url_slug=data["url_slug"],
        states=tuple(data.get("states") or ()),
        initial_state=data["initial_state"],
        terminal_states=tuple(data.get("terminal_states") or ()),
        transitions={
            k: frozenset(v) for k, v in (data.get("transitions") or {}).items()
        },
        action_states=dict(data.get("action_states") or {}),
        fields=tuple(data.get("fields") or ()),
        exception_state=data.get("exception_state"),
        policy_version=data.get("policy_version") or CURRENT_WORKFLOW_POLICY_VERSION,
        hooks=dict(data.get("hooks") or {}),
        conditions=dict(data.get("conditions") or {}),
    )
