"""Specialist agent abstraction (Sprint 4, Phase 1).

The legacy ``FinanceAgentRuntime`` is monolithic — one runtime
holds every skill (AP, vendor-compliance, workflow-health,
reconciliation), one intent dispatch, one error boundary. A skill
exception in vendor-compliance crashed the runtime; audit rows
attributed every action to ``actor_id="finance-agent"`` regardless
of which skill ran.

Sprint 4 Phase 1 ships the **wrapper layer** for splitting the
monolith into specialists. Each ``SpecialistAgent`` wraps a
``FinanceSkill`` with:

* a stable ``name`` (``ap-agent`` / ``vendor-compliance-agent`` / ...)
* a per-specialist ``actor_id`` (``agent:ap`` / ``agent:vendor-
  compliance``) that flows into audit rows so operators can see
  which specialist did what.
* an **error boundary**: skill exceptions become structured
  ``SpecialistResult(status="error", error=...)`` returns rather
  than propagated exceptions. One specialist's crash doesn't kill
  its siblings.
* timing instrumentation for per-specialist observability.

Phase 2 (later sprints) plugs each specialist into its own agent
loop, adds CAS-based merge semantics for concurrent Box writes
(see :func:`clearledgr.core.stores.box_lifecycle_store.update_box_
with_cas`), and migrates production callers off the monolith.
This phase is additive and opt-in: existing ``FinanceAgentRuntime``
callers see no change.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
import uuid
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime
    from clearledgr.services.finance_skills.base import FinanceSkill


logger = logging.getLogger(__name__)


SPECIALIST_STATUS_OK = "ok"
SPECIALIST_STATUS_ERROR = "error"
SPECIALIST_STATUS_UNROUTED = "unrouted"
# Sprint 4 Phase 2: returned when the per-specialist circuit breaker
# is OPEN — dispatch short-circuits without invoking the skill.
SPECIALIST_STATUS_QUARANTINED = "quarantined"


@dataclasses.dataclass(frozen=True)
class SpecialistResult:
    """Structured outcome of a specialist dispatch.

    ``status`` is one of:

    * ``"ok"``        — skill executed cleanly. ``payload`` carries
                        the skill's response dict.
    * ``"error"``     — skill raised. ``payload`` is empty;
                        ``error`` carries ``{"type", "message",
                        "trace_id"}`` for log correlation.
    * ``"unrouted"``  — no specialist registered for this intent.
                        ``payload`` is empty.

    ``trace_id`` in errors is a randomly generated short hex string
    that's also logged with the full traceback at ERROR level —
    callers can grep their logs for the trace_id to find the full
    stack without exposing it in the structured response.
    """

    status: str
    specialist_name: str
    actor_id: str
    intent: str
    payload: Dict[str, Any] = dataclasses.field(default_factory=dict)
    error: Optional[Dict[str, str]] = None
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        out = dataclasses.asdict(self)
        if out.get("error") is None:
            out.pop("error")
        return out


@dataclasses.dataclass(frozen=True)
class SpecialistAgent:
    """A skill + identity + error boundary.

    Built once per ``FinanceSkill`` registration; reused across
    every dispatch. Stateless — runtime + payload come in on each
    call, nothing is cached between calls.

    ``name`` is the short identifier ops uses (e.g. ``ap-agent``).
    ``actor_id`` is what lands in audit rows
    (``agent:ap``); the prefix ``agent:`` keeps it distinct from
    user actors (``user:<email>``) and the legacy aggregate
    ``finance-agent`` actor.
    """

    name: str
    actor_id: str
    skill: "FinanceSkill"
    description: str = ""

    @property
    def intents(self) -> frozenset:
        return self.skill.intents

    def supports(self, intent: str) -> bool:
        normalized = (intent or "").strip().lower()
        return normalized in self.intents

    async def execute(
        self,
        runtime: "FinanceAgentRuntime",
        intent: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> SpecialistResult:
        """Run the wrapped skill with an error boundary.

        Returns a ``SpecialistResult`` regardless of outcome.
        Skill exceptions are caught + logged with a trace_id +
        returned as ``status="error"`` — the caller never sees
        the original exception. This is the **failure isolation**
        contract the wrapper exists to deliver.
        """
        start = time.monotonic()
        normalized_intent = (intent or "").strip().lower()
        request_payload = payload if isinstance(payload, dict) else {}
        try:
            response = await self.skill.execute(
                runtime,
                normalized_intent,
                request_payload,
                idempotency_key=idempotency_key,
            )
        except asyncio.CancelledError:
            # Cancellation must propagate so the runtime's loop can
            # surface it. Don't swallow.
            raise
        except Exception as exc:  # noqa: BLE001 — boundary is the point
            trace_id = uuid.uuid4().hex[:12]
            logger.exception(
                "[%s] specialist execute failed intent=%s trace_id=%s",
                self.name, normalized_intent, trace_id,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return SpecialistResult(
                status=SPECIALIST_STATUS_ERROR,
                specialist_name=self.name,
                actor_id=self.actor_id,
                intent=normalized_intent,
                payload={},
                error={
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                    "trace_id": trace_id,
                },
                duration_ms=duration_ms,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        return SpecialistResult(
            status=SPECIALIST_STATUS_OK,
            specialist_name=self.name,
            actor_id=self.actor_id,
            intent=normalized_intent,
            payload=response if isinstance(response, dict) else {},
            duration_ms=duration_ms,
        )
