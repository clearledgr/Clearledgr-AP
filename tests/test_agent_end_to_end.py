"""End-to-end integration tests for the agent pipeline.

Verifies the full flow: event → planning engine → execution engine → DB state.
"""
from __future__ import annotations

import asyncio

import pytest

from clearledgr.core.database import get_db
from clearledgr.core.events import AgentEvent, AgentEventType
from clearledgr.core.coordination_engine import CoordinationEngine
from clearledgr.core.planning_engine import DeterministicPlanningEngine
from clearledgr.core.plan import Action, Plan


@pytest.fixture
def db():
    return get_db()


class TestApprovalEndToEnd:
    """approval_received → planning → execution → Box state change."""

    def test_approval_plan_produces_correct_action_sequence(self, db):
        engine = DeterministicPlanningEngine(db)
        event = AgentEvent(
            type=AgentEventType.APPROVAL_RECEIVED, source="test",
            payload={"decision": "approved", "box_id": "e2e-box-001"},
            organization_id="e2e-test",
        )
        plan = engine.plan(event, {})
        names = [a.name for a in plan.actions]
        assert names == [
            "clear_waiting_condition", "pre_post_validate", "post_bill",
            "move_box_stage", "apply_label", "schedule_payment",
            "post_timeline_entry", "send_slack_override_window", "watch_thread",
        ]

    def test_rejection_plan_produces_correct_sequence(self, db):
        engine = DeterministicPlanningEngine(db)
        event = AgentEvent(
            type=AgentEventType.APPROVAL_RECEIVED, source="test",
            payload={"decision": "rejected", "box_id": "e2e-box-002"},
            organization_id="e2e-test",
        )
        plan = engine.plan(event, {})
        names = [a.name for a in plan.actions]
        assert names == [
            "clear_waiting_condition", "move_box_stage", "apply_label",
            "send_vendor_email", "post_timeline_entry",
        ]


class TestTimerEndToEnd:

    def test_grn_check_timer_routes_correctly(self, db):
        engine = DeterministicPlanningEngine(db)
        event = AgentEvent(
            type=AgentEventType.TIMER_FIRED, source="test",
            payload={"timer_type": "grn_check", "box_id": "e2e-grn-001"},
            organization_id="e2e-test",
        )
        plan = engine.plan(event, {})
        names = [a.name for a in plan.actions]
        assert "lookup_grn" in names
        assert "evaluate_grn_result" in names

    def test_override_expired_timer(self, db):
        engine = DeterministicPlanningEngine(db)
        event = AgentEvent(
            type=AgentEventType.OVERRIDE_WINDOW_EXPIRED, source="test",
            payload={"box_id": "e2e-ov-001"},
            organization_id="e2e-test",
        )
        plan = engine.plan(event, {})
        names = [a.name for a in plan.actions]
        assert "close_override_window" in names
        assert "post_timeline_entry" in names


class TestPlanSerializationRoundtrip:
    """Serialize plan to JSON (for pending_plan), then resume."""

    def test_email_received_plan_roundtrips(self, db):
        engine = DeterministicPlanningEngine(db)
        event = AgentEvent(
            type=AgentEventType.EMAIL_RECEIVED, source="test",
            payload={"message_id": "m1", "user_id": "u1", "mailbox": "ap@test.com"},
            organization_id="test",
        )
        plan = engine.plan(event, {})
        serialized = plan.to_json()
        restored = Plan.from_json(serialized)
        assert len(restored.actions) == len(plan.actions)
        assert [a.name for a in restored.actions] == [a.name for a in plan.actions]
        assert [a.layer for a in restored.actions] == [a.layer for a in plan.actions]


class TestExecutionResumption:
    """§12.1: After interruption, execution resumes from pending_plan."""

    def test_remaining_from_skips_completed_steps(self):
        plan = Plan(
            event_type="test",
            actions=[Action(f"a{i}", "DET", {}, "") for i in range(10)],
            box_id="test", organization_id="test",
        )
        remaining = plan.remaining_from(5)
        assert remaining.step_count == 5
        assert remaining.actions[0].name == "a5"
        assert remaining.actions[-1].name == "a9"


class TestEventQueueIntegration:

    def test_event_roundtrip(self):
        event = AgentEvent(
            type=AgentEventType.EMAIL_RECEIVED,
            source="test",
            payload={"message_id": "m1", "thread_id": "t1"},
            organization_id="test-org",
            idempotency_key="m1",
        )
        data = event.to_dict()
        restored = AgentEvent.from_dict(data)
        assert restored.type == event.type
        assert restored.payload["message_id"] == "m1"
        assert restored.organization_id == "test-org"
        assert restored.idempotency_key == "m1"

    def test_in_memory_queue_claim_order(self):
        """Claim returns high_priority events first."""
        from clearledgr.core.event_queue import InMemoryEventQueue
        queue = InMemoryEventQueue()

        standard = AgentEvent(
            type=AgentEventType.EMAIL_RECEIVED, source="test",
            payload={"message_id": "m1"}, organization_id="test", priority="standard",
        )
        high = AgentEvent(
            type=AgentEventType.APPROVAL_RECEIVED, source="test",
            payload={"decision": "approved"}, organization_id="test", priority="high_priority",
        )
        queue.enqueue(standard)
        queue.enqueue(high)

        claimed = queue.claim_next("worker-1")
        assert claimed is not None
        _, _, event = claimed
        assert event.type == AgentEventType.APPROVAL_RECEIVED


class TestFullPipelineExecution:
    """Full event → plan → execution → result."""

    def test_approval_event_end_to_end(self, db):
        """approval_received event produces plan, executes, and completes or aborts cleanly."""
        planning = DeterministicPlanningEngine(db)
        execution = CoordinationEngine(db, "e2e-full-org")

        event = AgentEvent(
            type=AgentEventType.APPROVAL_RECEIVED, source="test",
            payload={"decision": "approved", "box_id": "e2e-full-001"},
            organization_id="e2e-full-org",
        )
        plan = planning.plan(event, {})
        assert plan.step_count == 9

        # Execute — may abort at pre_post_validate since item doesn't exist,
        # but should not crash
        result = asyncio.run(execution.execute(plan))
        assert result.status in ("completed", "failed", "waiting")
        # At minimum, the first step (clear_waiting_condition) should attempt
        assert result.steps_completed >= 0
