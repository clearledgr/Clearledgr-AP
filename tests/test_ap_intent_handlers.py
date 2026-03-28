"""Tests for the AP intent handler registry and AP skill delegation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import clearledgr.services.finance_skills.ap_skill as ap_skill_module
from clearledgr.services.finance_skills.ap_intent_handlers import get_ap_intent_handler
from clearledgr.services.finance_skills.ap_skill import APFinanceSkill


def test_handler_registry_covers_all_supported_ap_intents():
    skill = APFinanceSkill()
    for intent in skill.intents:
        handler = get_ap_intent_handler(intent)
        assert handler.intent == intent


def test_ap_finance_skill_policy_precheck_delegates_to_handler(monkeypatch):
    class _StubHandler:
        intent = "request_approval"

        def policy_precheck(self, skill, runtime, payload):
            assert skill.skill_id == "ap_v1"
            assert runtime.organization_id == "default"
            assert payload == {"email_id": "gmail-thread-1"}
            return {
                "intent": self.intent,
                "ap_item": {"id": "ap-1"},
                "ap_item_id": "ap-1",
                "email_id": "gmail-thread-1",
                "policy_precheck": {"eligible": True, "reason_codes": []},
            }

        async def execute(self, skill, runtime, context, *, idempotency_key=None):
            raise AssertionError("execute should not be called in precheck test")

    monkeypatch.setattr(ap_skill_module, "get_ap_intent_handler", lambda _intent: _StubHandler())

    runtime = SimpleNamespace(organization_id="default")
    result = APFinanceSkill().policy_precheck(
        runtime,
        "request_approval",
        {"email_id": "gmail-thread-1"},
    )

    assert result["ap_item_id"] == "ap-1"
    assert result["policy_precheck"]["eligible"] is True


def test_ap_finance_skill_execute_delegates_to_handler(monkeypatch):
    calls = []

    class _StubHandler:
        intent = "request_approval"

        def policy_precheck(self, skill, runtime, payload):
            calls.append(("precheck", payload))
            return {
                "intent": self.intent,
                "ap_item": {"id": "ap-1"},
                "ap_item_id": "ap-1",
                "email_id": "gmail-thread-1",
                "policy_precheck": {"eligible": True, "reason_codes": []},
            }

        async def execute(self, skill, runtime, context, *, idempotency_key=None):
            calls.append(("execute", context["ap_item_id"], idempotency_key))
            return {
                "skill_id": skill.skill_id,
                "intent": self.intent,
                "status": "ok",
                "ap_item_id": context["ap_item_id"],
            }

    monkeypatch.setattr(ap_skill_module, "get_ap_intent_handler", lambda _intent: _StubHandler())

    runtime = SimpleNamespace(organization_id="default")
    result = asyncio.run(
        APFinanceSkill().execute(
            runtime,
            "request_approval",
            {"email_id": "gmail-thread-1"},
            idempotency_key="idem-handler-test-1",
        )
    )

    assert result["status"] == "ok"
    assert calls == [
        ("precheck", {"email_id": "gmail-thread-1"}),
        ("execute", "ap-1", "idem-handler-test-1"),
    ]


def test_get_ap_intent_handler_rejects_unknown_intent():
    with pytest.raises(ValueError, match="unsupported_intent:missing_intent"):
        get_ap_intent_handler("missing_intent")
