"""Tests for Group 2 (first wave) of the agent-runtime audit:
idempotency on Celery retries + Redis Stream redeliveries.

Three fixes covered:

1. Plan.correlation_id is set from event.id by the planner; the
   coordination engine uses it to derive deterministic
   idempotency_keys for the audit pre/post-write rows. A second
   plan run with the same correlation_id (Celery retry) dedupes
   to the existing rows instead of double-writing the timeline.

2. _handle_post_bill checks ap_item.erp_reference before posting.
   On retry, an item that already posted returns
   {"noop": "already_posted"} without re-firing the ERP write.
   This prevents duplicate-bill creation.

3. The wait+pending_plan write is atomic — both fields are set in
   a single update_ap_item call so a process crash between them
   cannot produce split-brain state (orphaned plan with no wait,
   or wait with no remaining plan).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402
from solden.core.coordination_engine import CoordinationEngine  # noqa: E402
from solden.core.events import AgentEvent, AgentEventType  # noqa: E402
from solden.core.plan import Action, Plan  # noqa: E402
from solden.core.planning_engine import get_planning_engine  # noqa: E402


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgIdem", organization_name="Idempotency Test")
    return inst


def _make_engine(db) -> CoordinationEngine:
    return CoordinationEngine(db=db, organization_id="orgIdem")


def _seed_box(db, *, item_id: str, state: str = "ready_to_post",
              erp_reference: str = "") -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": "orgIdem",
        "vendor_name": "Acme",
        "amount": 100.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "received",  # always start at received; tests transition manually
    })
    if state != "received":
        # Transitions: received -> validated -> needs_approval -> approved -> ready_to_post
        path = ["validated", "needs_approval", "approved", "ready_to_post"]
        for s in path:
            db.update_ap_item(item["id"], state=s)
            if s == state:
                break
    if erp_reference:
        db.update_ap_item(item["id"], erp_reference=erp_reference)
    return db.get_ap_item(item["id"])


# ─── Plan.correlation_id ────────────────────────────────────────────


class TestPlanCorrelationId:
    def test_planner_sets_correlation_id_from_event_id(self, db):
        planner = get_planning_engine(db)
        event = AgentEvent(
            type=AgentEventType.EMAIL_RECEIVED,
            source="gmail_pubsub",
            payload={
                "message_id": "msg-1",
                "thread_id": "thr-1",
                "user_id": "alice@orgIdem.test",
                "mailbox": "alice@orgIdem.test",
            },
            organization_id="orgIdem",
        )
        plan = planner.plan(event)
        assert plan.correlation_id == event.id

    def test_planner_prefers_idempotency_key_over_id(self, db):
        """When the event carries a stable idempotency_key
        (e.g. Gmail message id), the planner uses that for
        correlation — same Gmail message redelivered through
        different AgentEvent ids dedupes correctly."""
        planner = get_planning_engine(db)
        event = AgentEvent(
            type=AgentEventType.EMAIL_RECEIVED,
            source="gmail_pubsub",
            payload={
                "message_id": "msg-2",
                "thread_id": "thr-2",
                "user_id": "alice@orgIdem.test",
                "mailbox": "alice@orgIdem.test",
            },
            organization_id="orgIdem",
            idempotency_key="gmail-msg-stable-key",
        )
        plan = planner.plan(event)
        assert plan.correlation_id == "gmail-msg-stable-key"

    def test_correlation_id_round_trips_through_pending_plan_serialization(self):
        plan = Plan(
            event_type="email_received",
            actions=[Action("apply_label", "DET", {}, "test")],
            box_id="box-1",
            organization_id="orgIdem",
            correlation_id="evt-abc",
        )
        rebuilt = Plan.from_json(plan.to_json())
        assert rebuilt.correlation_id == "evt-abc"


# ─── Audit dedupe via idempotency_key ──────────────────────────────


class TestAuditIdempotency:
    def test_pre_write_dedupes_on_replay(self, db):
        """Replaying _pre_write for the same plan + step + action
        returns the same audit row (no double-write to the
        timeline). Source of truth is audit_events.idempotency_key
        UNIQUE constraint."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-idem-1")
        plan = Plan(
            event_type="email_received",
            actions=[Action("apply_label", "DET", {}, "test")],
            box_id=item["id"],
            organization_id="orgIdem",
            correlation_id="evt-replay-1",
        )
        action = plan.actions[0]

        asyncio.run(engine._pre_write(item["id"], action, step=0, plan=plan))
        asyncio.run(engine._pre_write(item["id"], action, step=0, plan=plan))
        asyncio.run(engine._pre_write(item["id"], action, step=0, plan=plan))

        events = db.list_ap_audit_events(item["id"], limit=20)
        executing = [
            e for e in events
            if e.get("event_type") == "agent_action:apply_label:executing"
        ]
        assert len(executing) == 1, (
            f"Expected dedupe to keep 1 pre-write row, got {len(executing)}"
        )

    def test_post_write_dedupes_per_status(self, db):
        """The post-row's idempotency_key includes the status, so
        a retry that lands the same status dedupes; different
        statuses are distinct rows (they document distinct
        outcomes)."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-idem-2")
        plan = Plan(
            event_type="email_received",
            actions=[Action("apply_label", "DET", {}, "test")],
            box_id=item["id"],
            organization_id="orgIdem",
            correlation_id="evt-replay-2",
        )
        action = plan.actions[0]

        asyncio.run(engine._post_write(
            item["id"], action, step=0, timeline_id="TL-x",
            status="completed", result_summary="ok", plan=plan,
        ))
        asyncio.run(engine._post_write(
            item["id"], action, step=0, timeline_id="TL-x",
            status="completed", result_summary="ok", plan=plan,
        ))

        events = db.list_ap_audit_events(item["id"], limit=20)
        completed = [
            e for e in events
            if e.get("event_type") == "agent_action:apply_label:completed"
        ]
        assert len(completed) == 1

    def test_pre_and_post_writes_have_distinct_keys(self, db):
        """Pre- and post-rows must NOT collide; they cover
        different phases of the same step."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-idem-3")
        plan = Plan(
            event_type="email_received",
            actions=[Action("apply_label", "DET", {}, "test")],
            box_id=item["id"],
            organization_id="orgIdem",
            correlation_id="evt-distinct-1",
        )
        action = plan.actions[0]

        timeline_id = asyncio.run(
            engine._pre_write(item["id"], action, step=0, plan=plan)
        )
        asyncio.run(engine._post_write(
            item["id"], action, step=0, timeline_id=timeline_id,
            status="completed", result_summary="", plan=plan,
        ))

        events = db.list_ap_audit_events(item["id"], limit=20)
        types = {e.get("event_type") for e in events}
        assert "agent_action:apply_label:executing" in types
        assert "agent_action:apply_label:completed" in types

    def test_legacy_call_without_plan_does_not_dedupe(self, db):
        """Backward-compat: existing callers that don't pass plan
        (e.g. legacy direct invocations) get the prior behavior —
        each call writes a new row. Only plan-aware callers benefit
        from dedupe."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-idem-4")
        action = Action("apply_label", "DET", {}, "test")

        # Two pre-writes without plan: idempotency_key is None
        # (audit treats NULL as distinct), so both insert.
        asyncio.run(engine._pre_write(item["id"], action, step=0))
        asyncio.run(engine._pre_write(item["id"], action, step=0))

        events = db.list_ap_audit_events(item["id"], limit=20)
        executing = [
            e for e in events
            if e.get("event_type") == "agent_action:apply_label:executing"
        ]
        assert len(executing) == 2


# ─── _handle_post_bill ERP-reference dedupe ────────────────────────


class TestPostBillIdempotency:
    def test_post_bill_skips_when_erp_reference_already_set(self, db):
        """Replay of post_bill on a box that already posted must
        return noop without calling _post_to_erp. Prevents
        duplicate ERP records on Celery retry."""
        engine = _make_engine(db)
        item = _seed_box(
            db, item_id="AP-bill-1",
            state="approved",
            erp_reference="EXT-EXISTING-12345",
        )
        plan = Plan(
            event_type="approval_received",
            actions=[Action("post_bill", "DET", {}, "test")],
            box_id=item["id"],
            organization_id="orgIdem",
            correlation_id="evt-post-1",
        )

        # Sentinel: track whether _post_to_erp was called.
        post_to_erp_calls = {"count": 0}

        async def fake_post_to_erp(invoice):  # pragma: no cover
            post_to_erp_calls["count"] += 1
            return {"status": "posted", "reference_id": "EXT-NEW"}

        with patch.object(
            engine._get_workflow().__class__, "_post_to_erp",
            new=fake_post_to_erp,
        ):
            result = asyncio.run(
                engine._handle_post_bill(plan.actions[0], plan)
            )

        assert post_to_erp_calls["count"] == 0, (
            "post_bill must NOT re-fire the ERP write when erp_reference is set"
        )
        assert result["ok"] is True
        assert result["erp_reference"] == "EXT-EXISTING-12345"
        assert result["noop"] == "already_posted"

    def test_post_bill_runs_when_erp_reference_missing(self, db):
        """First-time post: handler runs the ERP write."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-bill-2", state="approved")
        plan = Plan(
            event_type="approval_received",
            actions=[Action("post_bill", "DET", {}, "test")],
            box_id=item["id"],
            organization_id="orgIdem",
            correlation_id="evt-post-2",
        )

        post_to_erp_calls = {"count": 0}

        async def fake_post_to_erp(self_, invoice):
            post_to_erp_calls["count"] += 1
            return {"status": "posted", "reference_id": "EXT-NEW"}

        with patch(
            "solden.services.invoice_workflow.InvoiceWorkflowService._post_to_erp",
            new=fake_post_to_erp,
        ):
            result = asyncio.run(
                engine._handle_post_bill(plan.actions[0], plan)
            )

        assert post_to_erp_calls["count"] == 1
        assert result["ok"] is True
        assert result["erp_reference"] == "EXT-NEW"


# ─── Atomic wait + pending_plan ────────────────────────────────────


class TestAtomicWaitPersistence:
    def test_wait_and_pending_plan_persist_in_one_update(self, db):
        """When an action signals a wait, both the waiting_condition
        and pending_plan land in a single update_ap_item call. Two
        separate writes had a split-brain hazard — process crash
        between them left the box in an inconsistent state."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-wait-1")
        action_set_wait = Action(
            "set_waiting_condition", "DET",
            {"type": "approval_response", "timeout_hours": 4},
            "test",
        )
        next_action = Action("apply_label", "DET", {}, "follow-up")
        plan = Plan(
            event_type="email_received",
            actions=[action_set_wait, next_action],
            box_id=item["id"],
            organization_id="orgIdem",
            correlation_id="evt-wait-1",
        )

        update_calls: list = []
        original_update = engine.db.update_ap_item

        def tracking_update(*args, **kwargs):
            update_calls.append({"args": args, "kwargs": dict(kwargs)})
            return original_update(*args, **kwargs)

        with patch.object(engine.db, "update_ap_item", side_effect=tracking_update):
            result = asyncio.run(engine.execute(plan))

        assert result.status == "waiting"
        # Find the call that wrote the wait. The atomic fix means
        # exactly one update carries BOTH waiting_condition and
        # pending_plan together (when there's remaining work).
        atomic_writes = [
            c for c in update_calls
            if "waiting_condition" in c["kwargs"]
            and "pending_plan" in c["kwargs"]
        ]
        assert len(atomic_writes) >= 1, (
            f"Expected at least one atomic wait+plan write; got: {update_calls}"
        )

        # Verify the persisted state matches what we expect.
        refreshed = db.get_ap_item(item["id"])
        assert refreshed.get("waiting_condition")
        assert refreshed.get("pending_plan")

        # The pending_plan should be the remaining actions (just
        # the follow-up apply_label, not set_waiting_condition).
        remaining = json.loads(refreshed["pending_plan"])
        action_names = [a["name"] for a in remaining["actions"]]
        assert action_names == ["apply_label"]
        # Remaining plan inherits the parent's correlation_id.
        assert remaining["correlation_id"] == "evt-wait-1"

    def test_wait_with_no_remaining_actions_skips_pending_plan(self, db):
        """When the wait fires on the last action, no remaining
        plan needs to be saved — only waiting_condition is
        written."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-wait-2")
        plan = Plan(
            event_type="email_received",
            actions=[Action(
                "set_waiting_condition", "DET",
                {"type": "approval_response", "timeout_hours": 4},
                "test",
            )],
            box_id=item["id"],
            organization_id="orgIdem",
            correlation_id="evt-wait-2",
        )

        result = asyncio.run(engine.execute(plan))
        assert result.status == "waiting"

        refreshed = db.get_ap_item(item["id"])
        assert refreshed.get("waiting_condition")
        # No remaining work → pending_plan remains None.
        assert not refreshed.get("pending_plan")
