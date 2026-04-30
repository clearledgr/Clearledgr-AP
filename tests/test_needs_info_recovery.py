"""Tests for the needs_info recovery planner.

The planner activates the AGENT_PLANNING LLM action that was previously
registered but uncalled. It produces an advisory ordered recovery plan
for AP items routed to needs_info — vendor outreach, escalation,
timer-driven re-prompts. The plan is persisted to AP item metadata as
``agent_recovery_plan`` for operator tooling; it never executes
automatically.

Boundaries pinned by these tests:

  - The action menu is closed: any step naming an action outside
    ``RECOVERY_ACTION_WHITELIST`` is silently dropped.
  - Plans are capped at 3 steps; the LLM cannot return a 12-step
    bureaucratic chain.
  - The first step always has ``trigger_after_hours == 0`` (immediate),
    so the chain reads "do X now, then maybe Y in N hours".
  - Failures (no API key, gateway raise, parse error, every step
    filtered) return None and the needs_info path falls back to the
    deterministic single-question behavior.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from clearledgr.services.needs_info_recovery import (
    RECOVERY_ACTION_WHITELIST,
    RecoveryPlan,
    RecoveryStep,
    propose_recovery_plan,
)


def _make_invoice(**kwargs):
    defaults = dict(
        vendor_name="Acme Supplies",
        amount=1234.56,
        currency="USD",
        invoice_number="INV-42",
        due_date="2026-05-30",
        confidence=0.72,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_decision(**kwargs):
    defaults = dict(
        recommendation="needs_info",
        reasoning="Confidence below threshold and PO reference missing.",
        info_needed="Could you share the PO reference for INV-42?",
        risk_flags=["po_required_missing"],
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestSkipShortCircuits:
    def test_returns_none_when_recommendation_is_not_needs_info(self):
        invoice = _make_invoice()
        decision = _make_decision(recommendation="approve")
        out = asyncio.run(propose_recovery_plan(invoice, decision))
        assert out is None

    def test_returns_none_when_invoice_or_decision_missing(self):
        assert asyncio.run(propose_recovery_plan(None, _make_decision())) is None
        assert asyncio.run(propose_recovery_plan(_make_invoice(), None)) is None


class TestGatewayFailures:
    def test_returns_none_when_gateway_raises(self, monkeypatch):
        fake_gateway = SimpleNamespace(call=AsyncMock(side_effect=RuntimeError("no key")))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )
        out = asyncio.run(propose_recovery_plan(_make_invoice(), _make_decision()))
        assert out is None

    def test_returns_none_when_response_is_garbage_json(self, monkeypatch):
        fake_resp = SimpleNamespace(content="not actually json {{")
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )
        out = asyncio.run(propose_recovery_plan(_make_invoice(), _make_decision()))
        assert out is None

    def test_returns_none_when_response_has_no_summary(self, monkeypatch):
        fake_resp = SimpleNamespace(content='{"steps": []}')
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )
        out = asyncio.run(propose_recovery_plan(_make_invoice(), _make_decision()))
        assert out is None


class TestPlanShape:
    def test_returns_plan_when_response_is_valid(self, monkeypatch):
        fake_resp = SimpleNamespace(content=(
            '{'
            '"summary": "Ask vendor for missing PO; escalate if no response in 48h.",'
            '"steps": ['
            '  {"action": "request_specific_field", "rationale": "PO is required for this vendor", '
            '   "params": {"field": "po_number"}, "trigger_after_hours": 0},'
            '  {"action": "wait_with_timer", "rationale": "Give vendor 48h to respond", '
            '   "params": {"hours": 48}, "trigger_after_hours": 48},'
            '  {"action": "escalate_to_ap_manager", "rationale": "Auto-escalate if vendor unresponsive", '
            '   "params": {}, "trigger_after_hours": 72}'
            ']}'
        ))
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )

        plan = asyncio.run(propose_recovery_plan(_make_invoice(), _make_decision()))
        assert plan is not None
        assert isinstance(plan, RecoveryPlan)
        assert "PO" in plan.summary
        assert len(plan.steps) == 3
        assert plan.steps[0].action == "request_specific_field"
        assert plan.steps[0].params == {"field": "po_number"}
        assert plan.steps[0].trigger_after_hours == 0
        assert plan.steps[2].action == "escalate_to_ap_manager"

    def test_strips_code_fence_wrapper(self, monkeypatch):
        fake_resp = SimpleNamespace(content=(
            '```json\n'
            '{"summary": "Re-prompt vendor.","steps": ['
            ' {"action": "ask_vendor_followup", "rationale": "general re-prompt", '
            '  "params": {}, "trigger_after_hours": 0}'
            ']}\n```'
        ))
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )
        plan = asyncio.run(propose_recovery_plan(_make_invoice(), _make_decision()))
        assert plan is not None
        assert plan.steps[0].action == "ask_vendor_followup"


class TestActionWhitelist:
    def test_drops_steps_with_actions_outside_whitelist(self, monkeypatch):
        # The LLM tries to escape into arbitrary coordination actions
        # (delete_box, post_bill, run_three_way_match). All filtered.
        fake_resp = SimpleNamespace(content=(
            '{'
            '"summary": "Recovery plan",'
            '"steps": ['
            '  {"action": "delete_box", "rationale": "out of scope", "params": {}, "trigger_after_hours": 0},'
            '  {"action": "post_bill", "rationale": "out of scope", "params": {}, "trigger_after_hours": 0},'
            '  {"action": "ask_vendor_followup", "rationale": "real step", "params": {}, "trigger_after_hours": 0}'
            ']}'
        ))
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )

        plan = asyncio.run(propose_recovery_plan(_make_invoice(), _make_decision()))
        assert plan is not None
        # Only the whitelisted step survives
        assert len(plan.steps) == 1
        assert plan.steps[0].action == "ask_vendor_followup"

    def test_returns_none_when_every_step_is_filtered(self, monkeypatch):
        fake_resp = SimpleNamespace(content=(
            '{"summary": "All bad","steps": ['
            ' {"action": "delete_box", "rationale": "x", "params": {}, "trigger_after_hours": 0},'
            ' {"action": "drop_database", "rationale": "x", "params": {}, "trigger_after_hours": 0}'
            ']}'
        ))
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )
        out = asyncio.run(propose_recovery_plan(_make_invoice(), _make_decision()))
        assert out is None

    def test_whitelist_contents_are_stable(self):
        # Lock the whitelist — adding a new recovery action is a deliberate
        # plan-shape change, not a silent expansion of LLM authority.
        assert RECOVERY_ACTION_WHITELIST == frozenset({
            "ask_vendor_followup",
            "request_specific_field",
            "propose_resubmission",
            "escalate_to_ap_manager",
            "auto_match_against_po",
            "wait_with_timer",
            "mark_disputed",
        })


class TestNormalizers:
    def test_caps_at_three_steps(self, monkeypatch):
        steps = ", ".join(
            f'{{"action": "ask_vendor_followup", "rationale": "step {i}", '
            f'"params": {{}}, "trigger_after_hours": 0}}'
            for i in range(7)
        )
        fake_resp = SimpleNamespace(content=f'{{"summary": "Long","steps": [{steps}]}}')
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )
        plan = asyncio.run(propose_recovery_plan(_make_invoice(), _make_decision()))
        assert plan is not None
        assert len(plan.steps) == 3

    def test_first_step_is_normalised_to_immediate(self, monkeypatch):
        fake_resp = SimpleNamespace(content=(
            '{"summary": "Delayed first","steps": ['
            ' {"action": "ask_vendor_followup", "rationale": "x", "params": {}, "trigger_after_hours": 24}'
            ']}'
        ))
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )
        plan = asyncio.run(propose_recovery_plan(_make_invoice(), _make_decision()))
        assert plan is not None
        assert plan.steps[0].trigger_after_hours == 0  # normalized

    def test_trigger_delay_capped_at_two_weeks(self, monkeypatch):
        fake_resp = SimpleNamespace(content=(
            '{"summary": "Long delay","steps": ['
            ' {"action": "ask_vendor_followup", "rationale": "x", "params": {}, "trigger_after_hours": 0},'
            ' {"action": "escalate_to_ap_manager", "rationale": "x", "params": {}, "trigger_after_hours": 100000}'
            ']}'
        ))
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )
        plan = asyncio.run(propose_recovery_plan(_make_invoice(), _make_decision()))
        assert plan is not None
        # 14 days * 24h = 336h cap
        assert plan.steps[1].trigger_after_hours == 336


class TestSerialisation:
    def test_recovery_plan_to_dict_round_trip_shape(self):
        plan = RecoveryPlan(
            summary="Test",
            steps=[
                RecoveryStep(
                    action="ask_vendor_followup",
                    rationale="r",
                    params={"k": "v"},
                    trigger_after_hours=0,
                )
            ],
        )
        d = plan.to_dict()
        assert d["summary"] == "Test"
        assert d["model"] == "agent_planning"
        assert d["steps"][0]["action"] == "ask_vendor_followup"
        assert d["steps"][0]["params"] == {"k": "v"}
        assert d["steps"][0]["trigger_after_hours"] == 0
