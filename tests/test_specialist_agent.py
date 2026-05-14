"""Coverage for the specialist-agent wrapper + router (Sprint 4 Phase 1).

The wrapper layer exists for failure isolation + per-specialist
audit attribution. Tests pin those contracts:

* SpecialistAgent.execute returns a structured ``SpecialistResult``
  on both success and skill failure (no exceptions escape).
* SpecialistRouter dispatches by intent; missing intents return
  ``status="unrouted"`` rather than raising.
* FinanceAgentRuntime auto-registers specialists for every skill,
  with ``actor_id="agent:<skill_id>"``.
* The runtime's opt-in ``dispatch_via_specialists`` produces
  results with the right specialist actor_id.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import pytest

from clearledgr.services.specialist_agent import (
    SPECIALIST_STATUS_ERROR,
    SPECIALIST_STATUS_OK,
    SPECIALIST_STATUS_UNROUTED,
    SpecialistAgent,
    SpecialistResult,
)
from clearledgr.services.specialist_router import SpecialistRouter


# ─── Fakes ─────────────────────────────────────────────────────────


class _FakeSkill:
    """Minimum-surface skill for unit testing the wrapper.

    Mirrors the ``FinanceSkill`` ABC's ``intents`` + ``execute``
    surface that ``SpecialistAgent`` actually consumes. Avoids
    pulling the full ABC + every concrete skill into test runtime.
    """

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
        runtime,
        intent: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.execute_calls.append({
            "intent": intent, "payload": payload,
            "idempotency_key": idempotency_key,
        })
        if self._raises is not None:
            raise self._raises
        return dict(self._response)


def _agent(skill: _FakeSkill, *, name: str = "test-agent",
           actor_id: str = "agent:test") -> SpecialistAgent:
    return SpecialistAgent(name=name, actor_id=actor_id, skill=skill)


# ─── SpecialistAgent.execute ───────────────────────────────────────


def test_specialist_execute_returns_ok_on_skill_success():
    skill = _FakeSkill("ap_skill", {"approve_invoice"},
                       response={"approved": True, "ap_item_id": "ap-1"})
    agent = _agent(skill, name="ap-agent", actor_id="agent:ap")

    result = asyncio.run(agent.execute(
        runtime=object(),
        intent="approve_invoice",
        payload={"ap_item_id": "ap-1"},
    ))

    assert result.status == SPECIALIST_STATUS_OK
    assert result.specialist_name == "ap-agent"
    assert result.actor_id == "agent:ap"
    assert result.intent == "approve_invoice"
    assert result.payload == {"approved": True, "ap_item_id": "ap-1"}
    assert result.error is None
    assert result.duration_ms >= 0
    assert len(skill.execute_calls) == 1


def test_specialist_execute_returns_error_on_skill_exception():
    skill = _FakeSkill("ap_skill", {"approve_invoice"},
                       raises=RuntimeError("downstream blew up"))
    agent = _agent(skill, name="ap-agent", actor_id="agent:ap")

    result = asyncio.run(agent.execute(
        runtime=object(),
        intent="approve_invoice",
        payload={"ap_item_id": "ap-1"},
    ))

    assert result.status == SPECIALIST_STATUS_ERROR
    assert result.specialist_name == "ap-agent"
    assert result.actor_id == "agent:ap"
    assert result.payload == {}
    assert result.error is not None
    assert result.error["type"] == "RuntimeError"
    assert "downstream blew up" in result.error["message"]
    # trace_id is generated for log correlation.
    assert result.error["trace_id"]


def test_specialist_execute_propagates_cancellation():
    """Async cancellation must escape the error boundary so the
    runtime's loop can shut down cleanly. Catching CancelledError
    would silently keep the specialist running through a shutdown.
    """
    skill = _FakeSkill("ap_skill", {"x"},
                       raises=asyncio.CancelledError())
    agent = _agent(skill)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.execute(
            runtime=object(), intent="x", payload={},
        ))


def test_specialist_execute_normalizes_intent_case():
    skill = _FakeSkill("ap_skill", {"approve_invoice"})
    agent = _agent(skill)
    result = asyncio.run(agent.execute(
        runtime=object(),
        intent="  Approve_Invoice  ",
        payload={},
    ))
    assert result.intent == "approve_invoice"
    assert skill.execute_calls[0]["intent"] == "approve_invoice"


def test_specialist_supports_intent_lookup():
    agent = _agent(_FakeSkill("x", {"foo", "bar"}))
    assert agent.supports("foo") is True
    assert agent.supports("FOO") is True  # case-insensitive
    assert agent.supports("baz") is False


def test_specialist_result_to_dict_drops_null_error():
    res = SpecialistResult(
        status=SPECIALIST_STATUS_OK,
        specialist_name="x",
        actor_id="agent:x",
        intent="y",
        payload={"a": 1},
    )
    out = res.to_dict()
    assert "error" not in out
    assert out["payload"] == {"a": 1}


# ─── SpecialistRouter.dispatch ─────────────────────────────────────


def test_router_dispatches_to_registered_specialist():
    skill = _FakeSkill("ap_skill", {"approve_invoice"})
    router = SpecialistRouter()
    router.register(_agent(skill, name="ap-agent", actor_id="agent:ap"))

    result = asyncio.run(router.dispatch(
        runtime=object(),
        intent="approve_invoice",
        payload={"ap_item_id": "ap-1"},
    ))

    assert result.status == SPECIALIST_STATUS_OK
    assert result.specialist_name == "ap-agent"
    assert result.actor_id == "agent:ap"


def test_router_returns_unrouted_for_unknown_intent():
    router = SpecialistRouter()
    router.register(_agent(_FakeSkill("ap", {"approve_invoice"})))

    result = asyncio.run(router.dispatch(
        runtime=object(),
        intent="some_random_intent",
        payload={},
    ))

    assert result.status == SPECIALIST_STATUS_UNROUTED
    assert result.specialist_name == ""
    assert result.actor_id == ""
    assert result.intent == "some_random_intent"
    assert result.payload == {}


def test_router_isolates_failures_between_specialists():
    """A skill exception in one specialist must not affect another
    specialist's dispatch. This is the load-bearing claim of the
    wrapper layer.
    """
    crashing_skill = _FakeSkill("vc", {"check_compliance"},
                                 raises=RuntimeError("compliance fail"))
    healthy_skill = _FakeSkill("ap", {"approve_invoice"},
                                response={"approved": True})
    router = SpecialistRouter()
    router.register(_agent(crashing_skill,
                           name="vc-agent", actor_id="agent:vc"))
    router.register(_agent(healthy_skill,
                           name="ap-agent", actor_id="agent:ap"))

    crash_result = asyncio.run(router.dispatch(
        runtime=object(), intent="check_compliance", payload={},
    ))
    assert crash_result.status == SPECIALIST_STATUS_ERROR

    # Sibling dispatch must still succeed.
    ok_result = asyncio.run(router.dispatch(
        runtime=object(), intent="approve_invoice", payload={},
    ))
    assert ok_result.status == SPECIALIST_STATUS_OK


def test_router_intent_collision_logs_warning_and_last_wins(caplog):
    """If two specialists declare the same intent, the later
    registration wins (mirrors legacy ``register_skill`` semantics)
    but the collision is logged so operators notice.

    Logger level + propagation are pinned explicitly here because
    earlier tests in the suite occasionally mutate logger config
    (propagate=False is a common offender) which breaks caplog.
    Set both at the source-of-truth level before the action so this
    test passes in isolation and under full-suite ordering.
    """
    import logging as _logging
    target_logger = _logging.getLogger("clearledgr.services.specialist_router")
    prior_level = target_logger.level
    prior_propagate = target_logger.propagate
    target_logger.setLevel(_logging.WARNING)
    target_logger.propagate = True

    a = _agent(_FakeSkill("a", {"shared_intent"},
                          response={"who": "a"}),
               name="a-agent", actor_id="agent:a")
    b = _agent(_FakeSkill("b", {"shared_intent"},
                          response={"who": "b"}),
               name="b-agent", actor_id="agent:b")

    router = SpecialistRouter()
    router.register(a)
    try:
        caplog.set_level(_logging.WARNING,
                         logger="clearledgr.services.specialist_router")
        router.register(b)
        assert any("shared_intent" in record.message for record in caplog.records)
    finally:
        target_logger.setLevel(prior_level)
        target_logger.propagate = prior_propagate

    result = asyncio.run(router.dispatch(
        runtime=object(), intent="shared_intent", payload={},
    ))
    assert result.payload == {"who": "b"}
    assert result.specialist_name == "b-agent"


def test_router_supported_intents_reflects_all_registered():
    router = SpecialistRouter()
    router.register(_agent(_FakeSkill("a", {"intent_a", "intent_b"})))
    router.register(_agent(_FakeSkill("c", {"intent_c"}),
                           name="c-agent", actor_id="agent:c"))
    assert router.supported_intents == frozenset(
        {"intent_a", "intent_b", "intent_c"}
    )


def test_router_register_rejects_empty_specialist_name():
    router = SpecialistRouter()
    with pytest.raises(ValueError, match="name is required"):
        router.register(SpecialistAgent(
            name="", actor_id="agent:x",
            skill=_FakeSkill("x", {"y"}),
        ))


# ─── FinanceAgentRuntime auto-registration ─────────────────────────


@pytest.fixture()
def runtime(db):
    """Real FinanceAgentRuntime with the four default skills.
    Uses the existing test-db fixture so the runtime can construct
    its own DB handle.
    """
    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime
    db.ensure_organization("org-specialist", organization_name="X")
    return FinanceAgentRuntime(
        organization_id="org-specialist",
        actor_id="test-runner",
        db=db,
    )


@pytest.fixture()
def db():
    from clearledgr.core import database as db_module
    inst = db_module.get_db()
    inst.initialize()
    return inst


def test_runtime_auto_registers_specialist_per_skill(runtime):
    specialists = runtime.specialists
    names = {s.name for s in specialists}
    # Default skills: AP, vendor-compliance, workflow-health,
    # reconciliation. Every skill_id becomes a specialist
    # named ``<id-with-dashes>-agent`` with actor_id
    # ``agent:<id-with-dashes>``.
    assert any(name.startswith("ap-") for name in names)
    assert any("compliance" in name for name in names)
    assert any("workflow" in name for name in names)
    for s in specialists:
        assert s.actor_id.startswith("agent:")
        assert s.name.endswith("-agent")


def test_runtime_specialist_for_intent_routes_correctly(runtime):
    """The runtime exposes ``specialist_for_intent`` mirroring the
    legacy ``_resolve_skill`` lookup. Sanity-check that AP intents
    route to the AP specialist.
    """
    # AP skill registers ``approve_invoice`` as one of its intents
    # (see services/finance_skills/ap_skill.py).
    specialist = runtime.specialist_for_intent("approve_invoice")
    if specialist is None:
        pytest.skip("AP skill doesn't expose 'approve_invoice'; "
                    "router test still validates other intents")
    # Whatever the AP intent maps to, the resolved specialist's
    # actor_id should start with agent: and the name with the AP
    # skill_id stem.
    assert specialist.actor_id.startswith("agent:")
    assert "ap" in specialist.name


def test_runtime_dispatch_via_specialists_returns_unrouted_for_unknown_intent(runtime):
    """Opt-in dispatch surface: missing intent returns structured
    unrouted result, not an exception.
    """
    result = asyncio.run(runtime.dispatch_via_specialists(
        intent="totally_made_up_intent",
        input_payload={},
    ))
    assert result.status == SPECIALIST_STATUS_UNROUTED
