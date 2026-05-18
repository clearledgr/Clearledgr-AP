"""Plan and Action dataclasses — Agent Design Specification §4/§5.

A Plan is an ordered sequence of Actions produced by the Planning Engine
and consumed by the Coordination Engine. Plans are serializable to JSON
for persistence in the ``pending_plan`` column (crash recovery / async wait).

Every Action is either DET (deterministic) or LLM (Claude-assisted).
The Coordination Engine enforces this boundary — a DET action that
attempts to call Claude is logged as a bug.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class Action:
    """A single step in a Plan.

    Attributes:
        name: The action identifier (e.g. "classify_email", "post_bill").
              Must match a key in the CoordinationEngine's handler registry.
        layer: "DET" (deterministic) or "LLM" (Claude-assisted).
        params: Action-specific parameters passed to the handler.
        description: Human-readable text for the pre-execution timeline entry.
    """

    name: str
    layer: str  # "DET" | "LLM"
    params: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "layer": self.layer,
            "params": self.params,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Action:
        return cls(
            name=data["name"],
            layer=data.get("layer", "DET"),
            params=data.get("params", {}),
            description=data.get("description", ""),
        )


@dataclass
class Plan:
    """An ordered sequence of Actions produced by the Planning Engine.

    The Coordination Engine consumes the Plan one Action at a time.
    If a run is interrupted (async wait or crash), the remaining
    actions are serialized to ``pending_plan`` on the Box for
    resumption.

    ``correlation_id`` is the source-event id (``AgentEvent.id`` or
    its idempotency key) carried forward from intake. The
    coordination engine derives deterministic
    ``idempotency_key`` values from it so Celery retries / Redis
    Stream redeliveries don't double-fire timeline rows for the
    same step. When the planner doesn't supply one (legacy paths,
    direct construction in tests), the engine falls back to a
    plan-stable token (event_type + created_at).
    """

    event_type: str
    actions: List[Action]
    box_id: Optional[str] = None
    # organization_id has no default — every Plan is tenant-bound and a
    # missing org silently routed work to the legacy "default" tenant
    # pre-M19. Callers MUST supply a real org id; the canonical resolver
    # is require_org(user, requested=...) at the API boundary or
    # assert_org_id(...) at service entry points.
    organization_id: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    correlation_id: Optional[str] = None

    def remaining_from(self, step: int) -> "Plan":
        """Return a new Plan with only the actions from step onwards."""
        return Plan(
            event_type=self.event_type,
            actions=self.actions[step:],
            box_id=self.box_id,
            organization_id=self.organization_id,
            created_at=self.created_at,
            correlation_id=self.correlation_id,
        )

    @property
    def step_count(self) -> int:
        return len(self.actions)

    @property
    def is_empty(self) -> bool:
        return len(self.actions) == 0

    def to_json(self) -> str:
        """Serialize for ``pending_plan`` column persistence."""
        return json.dumps({
            "event_type": self.event_type,
            "actions": [a.to_dict() for a in self.actions],
            "box_id": self.box_id,
            "organization_id": self.organization_id,
            "created_at": self.created_at,
            "correlation_id": self.correlation_id,
        })

    @classmethod
    def from_json(cls, data: str) -> "Plan":
        """Deserialize from ``pending_plan`` column.

        ``organization_id`` is asserted — a serialised Plan without an
        org is a producer bug (the column is tenant-pinned by the table
        itself). Fail loud at read time so the corrupt row is visible.
        """
        from clearledgr.core.org_utils import assert_org_id

        d = json.loads(data)
        return cls(
            event_type=d.get("event_type", "resumed"),
            actions=[Action.from_dict(a) for a in d.get("actions", [])],
            box_id=d.get("box_id"),
            organization_id=assert_org_id(
                d.get("organization_id"), context="Plan.from_json"
            ),
            created_at=d.get("created_at", ""),
            correlation_id=d.get("correlation_id"),
        )


@dataclass
class CoordinationResult:
    """Result of running a Plan through the Coordination Engine."""

    status: str  # "completed" | "waiting" | "failed" | "aborted"
    steps_completed: int = 0
    steps_total: int = 0
    box_id: Optional[str] = None
    waiting_condition: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    last_action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "steps_completed": self.steps_completed,
            "steps_total": self.steps_total,
            "box_id": self.box_id,
            "waiting_condition": self.waiting_condition,
            "error": self.error,
            "last_action": self.last_action,
        }
