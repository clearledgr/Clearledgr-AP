"""Tests for Deterministic Planning Engine — Agent Design Specification §4."""
from __future__ import annotations

import pytest

from clearledgr.core.events import AgentEvent, AgentEventType
from clearledgr.core.plan import Action, Plan
from clearledgr.core.planning_engine import DeterministicPlanningEngine


@pytest.fixture
def engine():
    return DeterministicPlanningEngine()


@pytest.fixture
def email_event():
    return AgentEvent(
        type=AgentEventType.EMAIL_RECEIVED,
        source="test",
        payload={"message_id": "m1", "user_id": "u1", "mailbox": "ap@test.com"},
        organization_id="test-org",
    )


class TestEmailReceivedPlan:
    """§4.1: The 19-step invoice plan."""

    def test_plan_is_deterministic(self, engine, email_event):
        """Same event + state → same plan every time."""
        plan1 = engine.plan(email_event, {})
        plan2 = engine.plan(email_event, {})
        assert [a.name for a in plan1.actions] == [a.name for a in plan2.actions]

    def test_plan_has_read_email_first(self, engine, email_event):
        """Step 1 is always read_email."""
        plan = engine.plan(email_event, {})
        assert plan.actions[0].name == "read_email"

    def test_plan_has_classify_second(self, engine, email_event):
        """Step 2 is always classify_email (LLM)."""
        plan = engine.plan(email_event, {})
        assert plan.actions[1].name == "classify_email"
        assert plan.actions[1].layer == "LLM"

    def test_plan_includes_all_spec_actions(self, engine, email_event):
        """Plan must include every action from spec §4.1."""
        plan = engine.plan(email_event, {})
        action_names = [a.name for a in plan.actions]
        required = {
            "read_email", "classify_email", "apply_label", "create_box",
            "check_domain_match", "check_duplicate", "extract_invoice_fields",
            "run_extraction_guardrails", "check_amount_ceiling", "check_velocity",
            "update_box_fields", "lookup_po", "lookup_grn", "run_three_way_match",
            "move_box_stage", "send_slack_approval", "set_waiting_condition",
        }
        assert required.issubset(set(action_names))

    def test_det_llm_boundary(self, engine, email_event):
        """Only classify_email and extract_invoice_fields are LLM."""
        plan = engine.plan(email_event, {})
        llm_actions = {a.name for a in plan.actions if a.layer == "LLM"}
        assert llm_actions == {"classify_email", "extract_invoice_fields"}

    def test_plan_organization_id_is_set(self, engine, email_event):
        """Plan inherits organization_id from event."""
        plan = engine.plan(email_event, {})
        assert plan.organization_id == "test-org"


class TestApprovalReceivedPlans:
    """§4.2: Four decision paths."""

    def test_approved_plan_matches_spec(self, engine):
        """Approved plan: clear_waiting → pre_post → post_bill → stage → label → schedule → timeline → override → watch."""
        event = AgentEvent(
            type=AgentEventType.APPROVAL_RECEIVED, source="test",
            payload={"decision": "approved", "box_id": "b1"},
            organization_id="test",
        )
        plan = engine.plan(event, {})
        expected = [
            "clear_waiting_condition", "pre_post_validate", "post_bill",
            "move_box_stage", "apply_label", "schedule_payment",
            "post_timeline_entry", "send_slack_override_window", "watch_thread",
        ]
        assert [a.name for a in plan.actions] == expected

    def test_rejected_plan(self, engine):
        """Rejected plan: clear → exception → label → vendor_email → timeline."""
        event = AgentEvent(
            type=AgentEventType.APPROVAL_RECEIVED, source="test",
            payload={"decision": "rejected", "box_id": "b1"},
            organization_id="test",
        )
        plan = engine.plan(event, {})
        expected = [
            "clear_waiting_condition", "move_box_stage", "apply_label",
            "send_vendor_email", "post_timeline_entry",
        ]
        assert [a.name for a in plan.actions] == expected

    def test_approved_moves_to_approved_stage(self, engine):
        """Approved plan targets 'approved' stage."""
        event = AgentEvent(
            type=AgentEventType.APPROVAL_RECEIVED, source="test",
            payload={"decision": "approved", "box_id": "b1"},
            organization_id="test",
        )
        plan = engine.plan(event, {})
        stage_action = next(a for a in plan.actions if a.name == "move_box_stage")
        assert stage_action.params.get("target") == "approved"

    def test_rejected_moves_to_exception_stage(self, engine):
        """Rejected plan targets 'exception' stage."""
        event = AgentEvent(
            type=AgentEventType.APPROVAL_RECEIVED, source="test",
            payload={"decision": "rejected", "box_id": "b1"},
            organization_id="test",
        )
        plan = engine.plan(event, {})
        stage_action = next(a for a in plan.actions if a.name == "move_box_stage")
        assert stage_action.params.get("target") == "exception"


class TestTimerFiredPlans:
    """§4.3: Timer-based plans."""

    def test_grn_check_plan(self, engine):
        event = AgentEvent(
            type=AgentEventType.TIMER_FIRED, source="test",
            payload={"timer_type": "grn_check", "box_id": "b1"},
            organization_id="test",
        )
        plan = engine.plan(event, {})
        assert [a.name for a in plan.actions] == ["lookup_grn", "evaluate_grn_result"]

    def test_vendor_chase_plan(self, engine):
        event = AgentEvent(
            type=AgentEventType.TIMER_FIRED, source="test",
            payload={"timer_type": "vendor_chase"},
            organization_id="test",
        )
        plan = engine.plan(event, {})
        assert [a.name for a in plan.actions] == ["check_vendor_response", "send_vendor_email"]

    def test_approval_timeout_logs_timeline(self, engine):
        """Spec: Log escalation to Box timeline."""
        event = AgentEvent(
            type=AgentEventType.TIMER_FIRED, source="test",
            payload={"timer_type": "approval_timeout", "box_id": "b1"},
            organization_id="test",
        )
        plan = engine.plan(event, {})
        names = [a.name for a in plan.actions]
        assert "escalate_approval" in names
        assert "post_timeline_entry" in names

    def test_iban_deadline_alerts_ap_manager(self, engine):
        """Spec: freeze + alert AP Manager."""
        event = AgentEvent(
            type=AgentEventType.TIMER_FIRED, source="test",
            payload={"timer_type": "iban_verification_deadline"},
            organization_id="test",
        )
        plan = engine.plan(event, {})
        names = [a.name for a in plan.actions]
        assert "freeze_vendor_payments" in names
        assert "send_slack_exception" in names


class TestPlanSerialization:
    """Plan/Action serialization for pending_plan persistence."""

    def test_plan_roundtrips_through_json(self, engine, email_event):
        plan = engine.plan(email_event, {})
        serialized = plan.to_json()
        deserialized = Plan.from_json(serialized)
        assert len(deserialized.actions) == len(plan.actions)
        assert [a.name for a in deserialized.actions] == [a.name for a in plan.actions]
        assert [a.layer for a in deserialized.actions] == [a.layer for a in plan.actions]

    def test_remaining_from_preserves_tail(self, engine, email_event):
        plan = engine.plan(email_event, {})
        tail = plan.remaining_from(5)
        assert tail.step_count == plan.step_count - 5
        assert tail.actions[0].name == plan.actions[5].name


class TestUnknownEventType:
    """Unknown events raise, not silently drop."""

    def test_empty_payload_for_known_event_does_not_crash(self, engine):
        """A known event with minimal payload still plans successfully."""
        event = AgentEvent(
            type=AgentEventType.EMAIL_RECEIVED, source="test",
            payload={}, organization_id="test",
        )
        plan = engine.plan(event, {})
        assert isinstance(plan, Plan)

    def test_unknown_event_type_raises(self, engine):
        """An enum member with no planner must raise — silent drop would
        stall any Box attached to the event with no audit trail."""
        event = AgentEvent(
            type=AgentEventType.REFUND_DETECTED,  # V1.2 reserved, no planner
            source="test",
            payload={},
            organization_id="test",
        )
        with pytest.raises(RuntimeError, match="No planner for event type"):
            engine.plan(event, {})

    def test_unknown_event_with_box_id_records_exception(self, engine, tmp_path, monkeypatch):
        """When the event names a Box, the unhandled-event failure is
        recorded as a box exception so it's auditable."""
        from clearledgr.core.database import ClearledgrDB
        from clearledgr.core import database as db_module

        db = ClearledgrDB(db_path=str(tmp_path / "unknown_event.db"))
        db.initialize()
        monkeypatch.setattr(db_module, "_DB_INSTANCE", db)

        engine._db = db

        db.create_ap_item({
            "id": "AP-UNHANDLED",
            "thread_id": "thr-1",
            "state": "received",
            "vendor_name": "Test",
            "amount": 100.0,
            "organization_id": "org-1",
        })

        event = AgentEvent(
            type=AgentEventType.REFUND_DETECTED,
            source="test",
            payload={"box_id": "AP-UNHANDLED", "box_type": "ap_item"},
            organization_id="org-1",
        )
        with pytest.raises(RuntimeError):
            engine.plan(event, {"id": "AP-UNHANDLED"})

        exceptions = db.list_box_exceptions(box_type="ap_item", box_id="AP-UNHANDLED")
        assert len(exceptions) == 1
        assert exceptions[0]["exception_type"] == "unhandled_event_type"
        assert exceptions[0]["severity"] == "high"
        assert "refund_detected" in exceptions[0]["reason"]
