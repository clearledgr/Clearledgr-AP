"""Specialist intent router (Sprint 4, Phase 1).

Where ``FinanceAgentRuntime._intent_skill_map`` is a flat
intent→skill dict, the router is intent→specialist with:

* per-specialist registration (``register(specialist)``)
* explicit intent → specialist mapping built at registration time
* dispatch returning ``SpecialistResult`` instead of raising on
  unrouted intents
* introspection (``list_specialists`` / ``get_specialist``) for
  observability

Phase 1 keeps it side-by-side with the legacy intent map. Phase 2+
migrates callers off the legacy path; eventually the runtime's
``execute_intent`` becomes a thin shim over the router.

Tenant isolation: specialist-scoped state is per-runtime (not per-
process) because each ``FinanceAgentRuntime`` is bound to one org.
The router lives on the runtime instance.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .specialist_agent import (
    SPECIALIST_STATUS_ERROR,
    SPECIALIST_STATUS_OK,
    SPECIALIST_STATUS_QUARANTINED,
    SPECIALIST_STATUS_UNROUTED,
    SpecialistAgent,
    SpecialistResult,
)
from .specialist_circuit_breaker import (
    BreakerConfig,
    SpecialistCircuitBreaker,
)

if TYPE_CHECKING:
    from .finance_agent_runtime import FinanceAgentRuntime


logger = logging.getLogger(__name__)
# Dedicated logger for per-dispatch structured metrics. Shipping it
# on a child logger lets ops route specialist metrics independently
# from the router's operational logs (collisions, registrations).
metrics_logger = logging.getLogger("clearledgr.services.specialist_router.metrics")


class SpecialistRouter:
    """Intent → specialist dispatch with structured failure returns.

    Built per-runtime (so it's tenant-scoped). Callers register
    specialists at construction time; dispatch returns a
    ``SpecialistResult`` for every call regardless of routing
    success or skill outcome.
    """

    def __init__(
        self,
        *,
        breaker_config: Optional[BreakerConfig] = None,
    ) -> None:
        self._specialists: Dict[str, SpecialistAgent] = {}
        self._intent_to_specialist: Dict[str, SpecialistAgent] = {}
        # Sprint 4 Phase 2: per-specialist circuit breakers. Each
        # specialist gets its own breaker on registration; thresholds
        # come from the router-level ``breaker_config`` default
        # unless the specialist registers with a custom config.
        self._breakers: Dict[str, SpecialistCircuitBreaker] = {}
        self._breaker_config: BreakerConfig = breaker_config or BreakerConfig()

    def register(
        self,
        specialist: SpecialistAgent,
        *,
        breaker_config: Optional[BreakerConfig] = None,
    ) -> None:
        """Add a specialist + register every intent it supports.

        If two specialists declare the same intent, the *later*
        registration wins — the legacy ``register_skill`` has the
        same semantics, so we mirror it for compatibility. Logs a
        warning so operators notice intent collisions.

        Sprint 4 Phase 2: also creates a per-specialist circuit
        breaker. ``breaker_config`` overrides the router-level default
        for this specialist (e.g., raise the threshold for a
        specialist whose backend is known-flaky).
        """
        name = (specialist.name or "").strip().lower()
        if not name:
            raise ValueError("specialist name is required")
        self._specialists[name] = specialist
        for intent in specialist.intents:
            normalized = (intent or "").strip().lower()
            if not normalized:
                continue
            existing = self._intent_to_specialist.get(normalized)
            if existing is not None and existing.name != specialist.name:
                logger.warning(
                    "[specialist_router] intent %r reassigned from %s to %s",
                    normalized, existing.name, specialist.name,
                )
            self._intent_to_specialist[normalized] = specialist

        # Per-specialist breaker. Each specialist has independent
        # state — quarantining vendor-compliance doesn't affect AP.
        self._breakers[name] = SpecialistCircuitBreaker(
            name=name,
            config=breaker_config or self._breaker_config,
        )

    def list_specialists(self) -> List[SpecialistAgent]:
        """All registered specialists, in registration order."""
        return list(self._specialists.values())

    def get_specialist(self, name: str) -> Optional[SpecialistAgent]:
        return self._specialists.get((name or "").strip().lower())

    def specialist_for_intent(self, intent: str) -> Optional[SpecialistAgent]:
        return self._intent_to_specialist.get((intent or "").strip().lower())

    @property
    def supported_intents(self) -> frozenset:
        return frozenset(self._intent_to_specialist.keys())

    def get_breaker(self, specialist_name: str) -> Optional[SpecialistCircuitBreaker]:
        """Lookup helper for the per-specialist breaker (ops surface)."""
        return self._breakers.get((specialist_name or "").strip().lower())

    def reset_breaker(self, specialist_name: str) -> bool:
        """Force-reset a specialist's breaker to CLOSED. Returns True
        if the specialist exists, False otherwise. Used by ops
        tooling to recover a quarantined specialist after the
        downstream is fixed.
        """
        breaker = self.get_breaker(specialist_name)
        if breaker is None:
            return False
        breaker.reset()
        return True

    async def dispatch(
        self,
        runtime: "FinanceAgentRuntime",
        intent: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> SpecialistResult:
        """Route an intent to its specialist + execute.

        Always returns a ``SpecialistResult``. The status set:

          * ``unrouted``    — no specialist registered for the intent.
          * ``quarantined`` — specialist's breaker is OPEN; the
                              dispatch short-circuits without invoking
                              the skill. ``error`` carries the breaker
                              state for diagnostics.
          * ``ok`` / ``error`` — normal dispatch outcome from the
                                  specialist's wrapped execute.

        Every dispatch emits a structured metric line on the
        ``clearledgr.services.specialist_router.metrics`` logger so
        ops dashboards can graph per-specialist throughput, latency,
        and error rate without parsing the application log.
        """
        normalized = (intent or "").strip().lower()
        specialist = self._intent_to_specialist.get(normalized)
        if specialist is None:
            self._emit_metric(
                outcome=SPECIALIST_STATUS_UNROUTED,
                specialist_name="",
                intent=normalized,
                duration_ms=0,
                error_type=None,
            )
            return SpecialistResult(
                status=SPECIALIST_STATUS_UNROUTED,
                specialist_name="",
                actor_id="",
                intent=normalized,
                payload={},
            )

        # Sprint 4 Phase 2: breaker check.
        breaker = self._breakers.get(specialist.name.lower())
        if breaker is not None and not breaker.allow():
            self._emit_metric(
                outcome=SPECIALIST_STATUS_QUARANTINED,
                specialist_name=specialist.name,
                intent=normalized,
                duration_ms=0,
                error_type="breaker_open",
            )
            return SpecialistResult(
                status=SPECIALIST_STATUS_QUARANTINED,
                specialist_name=specialist.name,
                actor_id=specialist.actor_id,
                intent=normalized,
                payload={},
                error={
                    "type": "BreakerOpen",
                    "message": (
                        f"specialist {specialist.name!r} circuit breaker is OPEN; "
                        f"recent error rate exceeded threshold. Use "
                        f"``router.reset_breaker(name)`` after the downstream is fixed."
                    ),
                    "trace_id": "",
                },
                duration_ms=0,
            )

        result = await specialist.execute(
            runtime, normalized, payload,
            idempotency_key=idempotency_key,
        )

        # Record outcome on the breaker so it can decide whether to
        # trip / recover. ``unrouted`` and ``quarantined`` never
        # reach this branch — they short-circuit above.
        if breaker is not None:
            breaker.record_outcome(ok=result.status == SPECIALIST_STATUS_OK)

        # Per-dispatch structured metric.
        error_type = (
            (result.error or {}).get("type")
            if result.status == SPECIALIST_STATUS_ERROR
            else None
        )
        self._emit_metric(
            outcome=result.status,
            specialist_name=result.specialist_name,
            intent=normalized,
            duration_ms=result.duration_ms,
            error_type=error_type,
        )
        return result

    async def dispatch_or_raise(
        self,
        runtime: "FinanceAgentRuntime",
        intent: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Convenience wrapper for callers migrating from the legacy
        ``execute_intent`` contract.

        Returns the skill's payload dict on success; raises
        ``fastapi.HTTPException`` on every other outcome with an
        HTTP code that maps cleanly to the failure mode:

          * ``ok``           → returns ``result.payload`` (legacy
                                shape; existing downstream code keeps
                                reading ``status``/``reason``/etc.)
          * ``quarantined``  → 503, ``error="specialist_quarantined"``
                                (the specialist's breaker is OPEN;
                                retry after the cooldown).
          * ``unrouted``     → 500, ``error="intent_unrouted"`` —
                                programming error, no specialist
                                handles this intent.
          * ``error``        → 500, ``error="specialist_failed"``
                                with the trace_id for log correlation.

        Routes can migrate from ``runtime.execute_intent`` to this
        helper line-for-line — the success path returns the same
        dict shape, the error paths are structured instead of
        propagated exceptions.
        """
        from fastapi import HTTPException

        result = await self.dispatch(
            runtime, intent, payload,
            idempotency_key=idempotency_key,
        )
        if result.status == SPECIALIST_STATUS_OK:
            return result.payload
        if result.status == SPECIALIST_STATUS_QUARANTINED:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "specialist_quarantined",
                    "specialist": result.specialist_name,
                    "intent": result.intent,
                    "message": (
                        "specialist temporarily unavailable; "
                        "circuit breaker is OPEN. Retry after the "
                        "cooldown elapses."
                    ),
                },
            )
        if result.status == SPECIALIST_STATUS_UNROUTED:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "intent_unrouted",
                    "intent": result.intent,
                },
            )
        # SPECIALIST_STATUS_ERROR + any future status.
        err = result.error or {}
        raise HTTPException(
            status_code=500,
            detail={
                "error": "specialist_failed",
                "specialist": result.specialist_name,
                "intent": result.intent,
                "exception_type": err.get("type"),
                "trace_id": err.get("trace_id"),
            },
        )

    def _emit_metric(
        self,
        *,
        outcome: str,
        specialist_name: str,
        intent: str,
        duration_ms: int,
        error_type: Optional[str],
    ) -> None:
        """Structured per-dispatch metric line for ops dashboards.

        Logged at INFO level on a dedicated child logger so ops can
        route + aggregate specialist metrics independently from the
        router's operational events. Format is structured (kwargs
        on the log record) so JSON log shippers can parse without
        regex.
        """
        metrics_logger.info(
            "specialist.dispatch",
            extra={
                "specialist_name": specialist_name,
                "intent": intent,
                "outcome": outcome,
                "duration_ms": duration_ms,
                "error_type": error_type or "",
            },
        )
