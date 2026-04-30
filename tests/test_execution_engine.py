"""Tests for Execution Engine — Agent Design Specification §5."""
from __future__ import annotations

import asyncio

import pytest

from clearledgr.core.database import get_db
from clearledgr.core.coordination_engine import (
    CoordinationEngine,
    _classify_failure,
    _ACTION_TO_SLA_STEP,
    _ACTION_TIMEOUTS,
)
from clearledgr.core.plan import Action, Plan


@pytest.fixture
def engine():
    db = get_db()
    return CoordinationEngine(db=db, organization_id="test-org")


class TestHandlerRegistry:
    """Every spec §3 action must have a handler."""

    def test_all_spec_actions_registered(self, engine):
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
            "activate_vendor_in_erp",
            "freeze_vendor_payments",
            "check_iban_change", "check_domain_match", "check_velocity",
            "check_duplicate", "flag_internal_instruction", "check_amount_ceiling",
        }
        missing = spec_actions - set(engine._handlers.keys())
        assert not missing, f"Missing handlers: {missing}"


class TestLLMBoundaryFence:
    """Drift fence for spec §7.1: exactly 5 actions call the LLM.

    classify_email, extract_invoice_fields, generate_exception_reason,
    classify_vendor_response, draft_vendor_response.

    Any growth here must be a spec change first. The deck promise is
    "Rules decide. LLM describes." Adding another LLM call without a
    spec update breaks the audit story finance buyers will ask about.
    """

    SPEC_LLM_ACTIONS = frozenset({
        "classify_email",
        "extract_invoice_fields",
        "generate_exception_reason",
        "classify_vendor_response",
        "draft_vendor_response",
    })

    def test_spec_llm_actions_all_have_handlers(self, engine):
        missing = self.SPEC_LLM_ACTIONS - set(engine._handlers.keys())
        assert not missing, f"Spec §7.1 LLM actions missing handlers: {missing}"

    def test_llm_gateway_called_only_by_spec_llm_actions(self):
        """Grep-level drift fence: the only handlers whose source text
        invokes `gateway.call` or `gateway.call_sync` must be the spec
        §7.1 five. V1.1 onboarding stubs (classify_submitted_document,
        extract_vendor_fields) route to a pending-adapter handler that
        does NOT call the gateway, so the runtime surface stays at 5
        even though the planner flags them as LLM-kind.
        """
        import inspect
        from clearledgr.core import coordination_engine as ce_mod

        source = inspect.getsource(ce_mod)
        # Find handler methods that mention gateway calls. This is a
        # coarse grep but catches any new handler that quietly adds an
        # LLM call without spec review.
        import re
        llm_handlers = []
        # inspect.getsource is monolithic; use a different parse that
        # slices on def boundaries.
        method_blocks = re.split(r"\n    def (_handle_\w+)\b", source)
        # Pairs after split: [preamble, name, body, name, body, ...]
        for i in range(1, len(method_blocks), 2):
            name = method_blocks[i]
            body = method_blocks[i + 1] if i + 1 < len(method_blocks) else ""
            if "gateway.call" in body or "gateway.call_sync" in body:
                llm_handlers.append(name)

        # Map handler names back to action names via the dispatch dict.
        db = get_db()
        engine = CoordinationEngine(db=db, organization_id="fence-check")
        method_to_action: dict = {}
        for action_name, handler in engine._handlers.items():
            method_name = getattr(handler, "__name__", None)
            if method_name:
                method_to_action.setdefault(method_name, []).append(action_name)

        llm_actions = set()
        for method_name in llm_handlers:
            for action_name in method_to_action.get(method_name, []):
                llm_actions.add(action_name)

        extra = llm_actions - self.SPEC_LLM_ACTIONS
        assert not extra, (
            f"Handlers calling the LLM gateway outside spec §7.1: {extra}. "
            "If this is intentional, update AGENT_DESIGN_SPECIFICATION.md §7.1 "
            "and SPEC_LLM_ACTIONS in this test."
        )


class TestPlannerActionCoverage:
    """Drift fence: every action the planner produces must have a
    coordination-engine handler. Without this fence, a new planner
    action would KeyError at runtime on first dispatch.
    """

    def test_every_planner_action_has_handler(self, engine):
        import re
        from clearledgr.core import planning_engine as pe_mod
        import inspect

        source = inspect.getsource(pe_mod)
        # Action names are the first positional arg to Action(...)
        planner_actions = set(re.findall(r'Action\(\s*"([^"]+)"', source))
        missing = planner_actions - set(engine._handlers.keys())
        assert not missing, (
            f"Planner emits actions without handlers: {missing}. "
            "Every Action(...) constructed in planning_engine.py must map "
            "to a handler in CoordinationEngine._handlers, or coordination "
            "will KeyError at dispatch."
        )


class TestConcurrencySafety:
    """§11.2.5: No shared mutable state between concurrent workers."""

    def test_ctx_is_instance_level(self):
        """_ctx must be per-instance, not class-level."""
        db = get_db()
        engine1 = CoordinationEngine(db=db, organization_id="org-1")
        engine2 = CoordinationEngine(db=db, organization_id="org-2")
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
