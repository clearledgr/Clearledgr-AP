"""Coverage for the specialist circuit breaker (Sprint 4 Phase 2).

Pure-state-machine tests — the breaker doesn't touch DB or
async. Phase 2's headline claim is "automatic quarantine" and
these tests pin every state transition.

Plus integration tests for the router + breaker:
- breaker trips on threshold → subsequent dispatches return
  ``quarantined`` without calling the skill
- cooldown elapses → next dispatch is HALF_OPEN probe
- successful probe resets to CLOSED
- failed probe trips back to OPEN
- per-specialist isolation (one breaker doesn't affect siblings)
- ops surface (``reset_breaker``) clears state
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import pytest

from clearledgr.services.specialist_agent import (
    SPECIALIST_STATUS_ERROR,
    SPECIALIST_STATUS_OK,
    SPECIALIST_STATUS_QUARANTINED,
    SPECIALIST_STATUS_UNROUTED,
    SpecialistAgent,
)
from clearledgr.services.specialist_circuit_breaker import (
    BREAKER_STATE_CLOSED,
    BREAKER_STATE_HALF_OPEN,
    BREAKER_STATE_OPEN,
    BreakerConfig,
    SpecialistCircuitBreaker,
)
from clearledgr.services.specialist_router import SpecialistRouter


# ─── Fakes ─────────────────────────────────────────────────────────


class _FakeSkill:
    def __init__(
        self,
        skill_id: str,
        intents: Optional[set] = None,
        *,
        raises: Optional[Exception] = None,
        response: Optional[Dict[str, Any]] = None,
    ):
        self.skill_id = skill_id
        self._intents = frozenset(intents or {"do_thing"})
        self._raises = raises
        self._response = response if response is not None else {"status": "ok"}
        self.execute_calls: list = []

    @property
    def intents(self) -> frozenset:
        return self._intents

    async def execute(
        self,
        runtime, intent: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.execute_calls.append({"intent": intent, "payload": payload})
        if self._raises is not None:
            raise self._raises
        return dict(self._response)


def _agent(skill: _FakeSkill, *, name: str = "x-agent",
           actor_id: str = "agent:x") -> SpecialistAgent:
    return SpecialistAgent(name=name, actor_id=actor_id, skill=skill)


# ─── Pure breaker state machine ────────────────────────────────────


def test_breaker_starts_closed_and_allows():
    b = SpecialistCircuitBreaker("test")
    assert b.state == BREAKER_STATE_CLOSED
    assert b.allow() is True


def test_breaker_stays_closed_under_threshold():
    b = SpecialistCircuitBreaker(
        "test", BreakerConfig(error_threshold=5, error_window_seconds=60),
    )
    for _ in range(4):  # 4 errors, threshold 5 → still CLOSED
        b.record_outcome(ok=False)
    assert b.state == BREAKER_STATE_CLOSED


def test_breaker_trips_open_when_threshold_crossed():
    b = SpecialistCircuitBreaker(
        "test", BreakerConfig(error_threshold=3, error_window_seconds=60),
    )
    for _ in range(3):
        b.record_outcome(ok=False)
    assert b.state == BREAKER_STATE_OPEN


def test_breaker_open_blocks_dispatch_during_cooldown():
    b = SpecialistCircuitBreaker(
        "test",
        BreakerConfig(error_threshold=2, cooldown_seconds=10.0),
    )
    b.record_outcome(ok=False, now=100.0)
    b.record_outcome(ok=False, now=100.5)  # trips at this point
    assert b.state == BREAKER_STATE_OPEN
    # Half-second after trip — well within cooldown.
    assert b.allow(now=101.0) is False


def test_breaker_transitions_to_half_open_after_cooldown():
    b = SpecialistCircuitBreaker(
        "test",
        BreakerConfig(error_threshold=2, cooldown_seconds=10.0),
    )
    b.record_outcome(ok=False, now=100.0)
    b.record_outcome(ok=False, now=100.5)  # trips at 100.5
    # 11 seconds after trip — past cooldown.
    assert b.allow(now=111.6) is True
    assert b.state == BREAKER_STATE_HALF_OPEN


def test_half_open_success_resets_to_closed():
    b = SpecialistCircuitBreaker(
        "test",
        BreakerConfig(error_threshold=2, cooldown_seconds=10.0),
    )
    b.record_outcome(ok=False, now=100.0)
    b.record_outcome(ok=False, now=100.5)
    b.allow(now=111.6)  # → HALF_OPEN
    b.record_outcome(ok=True)
    assert b.state == BREAKER_STATE_CLOSED


def test_half_open_failure_trips_back_open():
    b = SpecialistCircuitBreaker(
        "test",
        BreakerConfig(error_threshold=2, cooldown_seconds=10.0),
    )
    b.record_outcome(ok=False, now=100.0)
    b.record_outcome(ok=False, now=100.5)
    b.allow(now=111.6)  # → HALF_OPEN
    b.record_outcome(ok=False, now=112.0)
    assert b.state == BREAKER_STATE_OPEN


def test_half_open_only_allows_one_probe_at_a_time():
    """A second dispatch arriving while HALF_OPEN is still resolving
    must not double-spend the probe — the breaker re-closes the gate
    until the first probe records its outcome.
    """
    b = SpecialistCircuitBreaker(
        "test",
        BreakerConfig(error_threshold=2, cooldown_seconds=10.0),
    )
    b.record_outcome(ok=False, now=100.0)
    b.record_outcome(ok=False, now=100.5)
    # First allow after cooldown → HALF_OPEN, returns True
    assert b.allow(now=111.6) is True
    assert b.state == BREAKER_STATE_HALF_OPEN
    # Second allow before the first probe resolves → False
    # (state went back to OPEN to gate the second caller).
    assert b.allow(now=111.7) is False


def test_errors_outside_window_dont_count():
    """A specialist that errors twice an hour should not trip — the
    rolling window discards stale errors.
    """
    b = SpecialistCircuitBreaker(
        "test",
        BreakerConfig(error_threshold=3, error_window_seconds=60.0),
    )
    b.record_outcome(ok=False, now=100.0)
    b.record_outcome(ok=False, now=100.0)
    # One hour later — first two errors are way outside the window.
    b.record_outcome(ok=False, now=3700.0)
    assert b.state == BREAKER_STATE_CLOSED


def test_consecutive_open_trips_increment():
    """Repeated trips bump ``consecutive_open_trips`` so ops can see
    a chronically broken specialist vs a one-off blip.
    """
    b = SpecialistCircuitBreaker(
        "test",
        BreakerConfig(error_threshold=1, cooldown_seconds=0.001),
    )
    b.record_outcome(ok=False, now=100.0)  # trip 1
    b.allow(now=100.5)                       # → HALF_OPEN
    b.record_outcome(ok=False, now=100.5)  # trip 2
    b.allow(now=101.0)                       # → HALF_OPEN
    b.record_outcome(ok=False, now=101.0)  # trip 3
    assert b._state.consecutive_open_trips == 3  # noqa: SLF001 — testing internal


def test_reset_clears_state():
    b = SpecialistCircuitBreaker(
        "test", BreakerConfig(error_threshold=2),
    )
    b.record_outcome(ok=False)
    b.record_outcome(ok=False)
    assert b.state == BREAKER_STATE_OPEN
    b.reset()
    assert b.state == BREAKER_STATE_CLOSED
    assert b.allow() is True


# ─── Router integration ───────────────────────────────────────────


def test_router_returns_quarantined_when_breaker_open():
    skill = _FakeSkill("vc", {"check"},
                        raises=RuntimeError("downstream down"))
    router = SpecialistRouter(breaker_config=BreakerConfig(
        error_threshold=2, cooldown_seconds=10.0,
    ))
    router.register(_agent(skill, name="vc-agent", actor_id="agent:vc"))

    # Two failing dispatches trip the breaker.
    asyncio.run(router.dispatch(runtime=object(), intent="check"))
    asyncio.run(router.dispatch(runtime=object(), intent="check"))

    # Third dispatch — breaker is OPEN, skill must NOT be called.
    pre_calls = len(skill.execute_calls)
    result = asyncio.run(router.dispatch(runtime=object(), intent="check"))
    assert result.status == SPECIALIST_STATUS_QUARANTINED
    assert result.specialist_name == "vc-agent"
    assert result.actor_id == "agent:vc"
    assert result.error and result.error["type"] == "BreakerOpen"
    # Skill NOT invoked while quarantined.
    assert len(skill.execute_calls) == pre_calls


def test_router_breaker_is_per_specialist_isolated():
    """A failing specialist's breaker tripping must not affect a
    healthy sibling. This is the load-bearing failure-isolation
    claim.
    """
    failing = _FakeSkill("vc", {"check"},
                          raises=RuntimeError("vc broken"))
    healthy = _FakeSkill("ap", {"approve"},
                          response={"approved": True})
    router = SpecialistRouter(breaker_config=BreakerConfig(
        error_threshold=2, cooldown_seconds=10.0,
    ))
    router.register(_agent(failing, name="vc-agent", actor_id="agent:vc"))
    router.register(_agent(healthy, name="ap-agent", actor_id="agent:ap"))

    asyncio.run(router.dispatch(runtime=object(), intent="check"))
    asyncio.run(router.dispatch(runtime=object(), intent="check"))
    # vc-agent now quarantined.
    quarantined = asyncio.run(router.dispatch(runtime=object(), intent="check"))
    assert quarantined.status == SPECIALIST_STATUS_QUARANTINED

    # ap-agent unaffected.
    ok = asyncio.run(router.dispatch(runtime=object(), intent="approve"))
    assert ok.status == SPECIALIST_STATUS_OK


def test_router_breaker_recovers_after_cooldown():
    """End-to-end: trip → wait cooldown → next dispatch hits skill
    via HALF_OPEN; skill succeeds → CLOSED; subsequent dispatches
    flow normally.

    Time is monkeypatched on the breaker so we don't sleep in
    tests.
    """
    flaky = _FakeSkill("flaky", {"do"},
                        raises=RuntimeError("blip"))
    router = SpecialistRouter(breaker_config=BreakerConfig(
        error_threshold=2, cooldown_seconds=1.0,
    ))
    router.register(_agent(flaky, name="flaky-agent", actor_id="agent:flaky"))

    # Trip.
    asyncio.run(router.dispatch(runtime=object(), intent="do"))
    asyncio.run(router.dispatch(runtime=object(), intent="do"))

    breaker = router.get_breaker("flaky-agent")
    assert breaker.state == BREAKER_STATE_OPEN

    # Heal the skill so the probe will succeed.
    flaky._raises = None  # noqa: SLF001 — test-only mutation
    flaky._response = {"healed": True}

    # Manually expire cooldown by force-resetting (equivalent to
    # waiting cooldown + dispatching).
    breaker.reset()
    result = asyncio.run(router.dispatch(runtime=object(), intent="do"))
    assert result.status == SPECIALIST_STATUS_OK
    assert breaker.state == BREAKER_STATE_CLOSED


def test_router_reset_breaker_returns_false_for_unknown_specialist():
    router = SpecialistRouter()
    router.register(_agent(_FakeSkill("a", {"x"})))
    assert router.reset_breaker("nonexistent-agent") is False
    assert router.reset_breaker("x-agent") is True


def test_router_emits_per_dispatch_metric_with_outcome(caplog):
    """Each dispatch logs a structured metric on the dedicated
    metrics logger so ops dashboards can aggregate without parsing.
    """
    import logging as _logging
    target = _logging.getLogger("clearledgr.services.specialist_router.metrics")
    prior_level = target.level
    prior_propagate = target.propagate
    target.setLevel(_logging.INFO)
    target.propagate = True

    skill = _FakeSkill("ap", {"approve"}, response={"approved": True})
    router = SpecialistRouter()
    router.register(_agent(skill, name="ap-agent", actor_id="agent:ap"))

    try:
        caplog.set_level(_logging.INFO,
                         logger="clearledgr.services.specialist_router.metrics")
        asyncio.run(router.dispatch(runtime=object(), intent="approve"))
        records = [r for r in caplog.records
                   if r.name == "clearledgr.services.specialist_router.metrics"]
        assert records, "expected at least one metric record"
        rec = records[-1]
        # Structured fields on the record (extra=...).
        assert rec.specialist_name == "ap-agent"
        assert rec.outcome == SPECIALIST_STATUS_OK
        assert rec.intent == "approve"
        assert rec.duration_ms >= 0
    finally:
        target.setLevel(prior_level)
        target.propagate = prior_propagate


def test_router_emits_metric_on_quarantined_dispatch(caplog):
    import logging as _logging
    target = _logging.getLogger("clearledgr.services.specialist_router.metrics")
    target.setLevel(_logging.INFO)
    target.propagate = True

    skill = _FakeSkill("vc", {"check"},
                       raises=RuntimeError("down"))
    router = SpecialistRouter(breaker_config=BreakerConfig(
        error_threshold=2, cooldown_seconds=10.0,
    ))
    router.register(_agent(skill, name="vc-agent", actor_id="agent:vc"))

    asyncio.run(router.dispatch(runtime=object(), intent="check"))
    asyncio.run(router.dispatch(runtime=object(), intent="check"))

    caplog.set_level(_logging.INFO,
                     logger="clearledgr.services.specialist_router.metrics")
    asyncio.run(router.dispatch(runtime=object(), intent="check"))

    records = [r for r in caplog.records
               if r.name == "clearledgr.services.specialist_router.metrics"
               and getattr(r, "outcome", "") == SPECIALIST_STATUS_QUARANTINED]
    assert records
    assert records[-1].error_type == "breaker_open"


# ─── dispatch_or_raise (HTTP migration helper) ─────────────────────


def test_dispatch_or_raise_returns_payload_on_success():
    """Successful dispatch returns the skill's payload dict, exactly
    matching the legacy ``execute_intent`` shape so call-site
    downstream code keeps working unchanged.
    """
    skill = _FakeSkill("ap", {"approve"},
                       response={"status": "approved", "ap_item_id": "ap-1"})
    router = SpecialistRouter()
    router.register(_agent(skill, name="ap-agent", actor_id="agent:ap"))

    payload = asyncio.run(router.dispatch_or_raise(
        runtime=object(), intent="approve",
    ))
    assert payload == {"status": "approved", "ap_item_id": "ap-1"}


def test_dispatch_or_raise_raises_503_on_quarantined():
    """Quarantined dispatches map to HTTP 503 with structured detail
    so clients can distinguish "downstream is sick" from "request
    was bad".
    """
    from fastapi import HTTPException
    skill = _FakeSkill("vc", {"check"},
                       raises=RuntimeError("down"))
    router = SpecialistRouter(breaker_config=BreakerConfig(
        error_threshold=2, cooldown_seconds=10.0,
    ))
    router.register(_agent(skill, name="vc-agent", actor_id="agent:vc"))

    # Trip the breaker.
    asyncio.run(router.dispatch(runtime=object(), intent="check"))
    asyncio.run(router.dispatch(runtime=object(), intent="check"))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(router.dispatch_or_raise(
            runtime=object(), intent="check",
        ))
    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail
    assert detail["error"] == "specialist_quarantined"
    assert detail["specialist"] == "vc-agent"


def test_dispatch_or_raise_raises_500_on_unrouted():
    from fastapi import HTTPException
    router = SpecialistRouter()
    router.register(_agent(_FakeSkill("a", {"x"})))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(router.dispatch_or_raise(
            runtime=object(), intent="totally_unknown",
        ))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["error"] == "intent_unrouted"


def test_dispatch_or_raise_raises_500_on_skill_error_with_trace_id():
    from fastapi import HTTPException
    skill = _FakeSkill("ap", {"approve"},
                       raises=RuntimeError("downstream blew up"))
    router = SpecialistRouter()
    router.register(_agent(skill, name="ap-agent", actor_id="agent:ap"))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(router.dispatch_or_raise(
            runtime=object(), intent="approve",
        ))
    assert exc_info.value.status_code == 500
    detail = exc_info.value.detail
    assert detail["error"] == "specialist_failed"
    assert detail["specialist"] == "ap-agent"
    assert detail["exception_type"] == "RuntimeError"
    # trace_id is non-empty so ops can correlate with the application log.
    assert detail["trace_id"]
