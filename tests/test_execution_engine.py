"""Tests for Execution Engine — Agent Design Specification §5."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from clearledgr.core.database import get_db
from clearledgr.core.execution_engine import (
    ExecutionEngine,
    _classify_failure,
    _ACTION_TO_SLA_STEP,
    _ACTION_TIMEOUTS,
)
from clearledgr.core.plan import Action, Plan


@pytest.fixture
def engine():
    db = get_db()
    return ExecutionEngine(db=db, organization_id="test-org")


class TestHandlerRegistry:
    """Every spec §3 action must have a handler."""

    def test_all_49_spec_actions_registered(self, engine):
        spec_actions = {
            "read_email", "fetch_attachment", "apply_label", "remove_label",
            "split_thread", "send_email", "watch_thread",
            "classify_email", "extract_invoice_fields", "run_extraction_guardrails",
            "generate_exception_reason", "classify_vendor_response",
            "lookup_vendor_master", "lookup_po", "lookup_grn", "run_three_way_match",
            "post_bill", "pre_post_validate", "schedule_payment", "reverse_erp_post",
            "create_box", "update_box_fields", "move_box_stage", "post_timeline_entry",
            "link_vendor_to_box", "set_waiting_condition", "clear_waiting_condition", "set_pending_plan",
            "send_slack_approval", "send_slack_exception", "send_slack_override_window",
            "send_slack_digest", "send_vendor_email", "draft_vendor_response",
            "send_teams_approval", "post_gmail_notification",
            "create_vendor_record", "enrich_vendor", "run_adverse_media_check",
            "initiate_micro_deposit", "verify_micro_deposit", "activate_vendor_in_erp",
            "freeze_vendor_payments",
            "check_iban_change", "check_domain_match", "check_velocity",
            "check_duplicate", "flag_internal_instruction", "check_amount_ceiling",
        }
        missing = spec_actions - set(engine._handlers.keys())
        assert not missing, f"Missing handlers: {missing}"


class TestConcurrencySafety:
    """§11.2.5: No shared mutable state between concurrent workers."""

    def test_ctx_is_instance_level(self):
        """_ctx must be per-instance, not class-level."""
        db = get_db()
        engine1 = ExecutionEngine(db=db, organization_id="org-1")
        engine2 = ExecutionEngine(db=db, organization_id="org-2")
        engine1._ctx["key"] = "value1"
        engine2._ctx["key"] = "value2"
        assert engine1._ctx["key"] == "value1"
        assert engine2._ctx["key"] == "value2"


class TestFailureClassification:
    """§5.2: Every action can fail in one of four ways."""

    def test_transient_errors(self):
        assert _classify_failure(Exception("connection timeout")) == "transient"
        assert _classify_failure(Exception("rate_limit exceeded")) == "transient"
        assert _classify_failure(Exception("502 Bad Gateway")) == "transient"

    def test_persistent_errors(self):
        assert _classify_failure(Exception("permission denied")) == "persistent"
        assert _classify_failure(Exception("invalid data")) == "persistent"

    def test_dependency_errors(self):
        assert _classify_failure(Exception("connection refused")) == "dependency"
        assert _classify_failure(Exception("service unavailable offline")) == "dependency"

    def test_llm_errors(self):
        assert _classify_failure(Exception("anthropic API error")) == "llm"
        assert _classify_failure(Exception("claude safety refusal")) == "llm"


class TestActionTimeouts:
    """§5.1 Step 4: Per-action-type timeouts."""

    def test_llm_timeouts_30s(self):
        assert _ACTION_TIMEOUTS["classify_email"] == 30
        assert _ACTION_TIMEOUTS["extract_invoice_fields"] == 30

    def test_erp_timeouts_10s(self):
        assert _ACTION_TIMEOUTS["post_bill"] == 10
        assert _ACTION_TIMEOUTS["lookup_po"] == 10
        assert _ACTION_TIMEOUTS["lookup_grn"] == 10

    def test_gmail_api_timeouts_5s(self):
        assert _ACTION_TIMEOUTS["apply_label"] == 5


class TestSLAMapping:
    """§11: Every spec SLA step has an action → SLA mapping."""

    def test_sla_coverage(self):
        sla_steps_covered = set(_ACTION_TO_SLA_STEP.values())
        expected = {
            "classification", "extraction", "guardrails",
            "erp_lookup", "three_way_match", "erp_post", "slack_delivery",
        }
        assert expected.issubset(sla_steps_covered)


class TestExecuteEmptyPlan:
    def test_empty_plan_completes(self, engine):
        plan = Plan(event_type="test", actions=[])
        result = asyncio.run(engine.execute(plan))
        assert result.status == "completed"
        assert result.steps_total == 0


class TestRule1PreExecutionWrite:
    """§5.1 Rule 1: Every action is recorded to the Box timeline before it executes."""

    def test_pre_write_happens_before_execution(self, engine):
        call_order = []
        original_pre_write = engine._pre_write

        def track_pre_write(*args, **kwargs):
            call_order.append("pre_write")
            return original_pre_write(*args, **kwargs)

        async def track_handler(action, plan):
            call_order.append("handler")
            return {"ok": True}

        engine._handlers["test_action_rule1"] = track_handler
        engine._pre_write = track_pre_write

        plan = Plan(
            event_type="test",
            actions=[Action("test_action_rule1", "DET", {}, "Test")],
            box_id="test-box-rule1",
        )
        asyncio.run(engine.execute(plan))

        assert call_order.index("pre_write") < call_order.index("handler")


class TestWaitingConditionPersistence:
    """§5.1 Step 6: When action returns waiting_condition, stop execution."""

    def test_waiting_condition_stops_execution(self, engine):
        executed = []

        async def wait_handler(action, plan):
            return {"ok": True, "waiting_condition": {"type": "approval_response"}}

        async def after_handler(action, plan):
            executed.append("after")
            return {"ok": True}

        engine._handlers["wait_action_test"] = wait_handler
        engine._handlers["after_action_test"] = after_handler

        plan = Plan(
            event_type="test",
            actions=[
                Action("wait_action_test", "DET", {}, "Wait"),
                Action("after_action_test", "DET", {}, "After"),
            ],
        )
        result = asyncio.run(engine.execute(plan))

        assert result.status == "waiting"
        assert result.waiting_condition is not None
        assert "after" not in executed


class TestAbortOnPersistentFailure:
    """§5.2 Persistent failures stop the plan."""

    def test_abort_result_stops_plan(self, engine):
        executed = []

        async def failing_handler(action, plan):
            return {"_abort": True, "error": "invalid data"}

        async def after_handler(action, plan):
            executed.append("after")
            return {"ok": True}

        engine._handlers["fail_action_test"] = failing_handler
        engine._handlers["after_action_test2"] = after_handler

        plan = Plan(
            event_type="test",
            actions=[
                Action("fail_action_test", "DET", {}, "Fail"),
                Action("after_action_test2", "DET", {}, "After"),
            ],
        )
        result = asyncio.run(engine.execute(plan))

        assert result.status == "failed"
        assert "after" not in executed
