"""Tests for Group 4 of the agent-runtime audit: async / resource
hygiene in CoordinationEngine.

Three fixes covered:

1. ``_pre_write`` and ``_post_write`` are async. The retry-loop
   backoff inside ``_pre_write`` uses ``await asyncio.sleep``
   instead of ``time.sleep`` — previously a sync sleep inside
   ``async def`` blocked the event loop for up to 1s under DB
   pressure, stalling every other coroutine on the worker. The
   sync DB call (``db.append_audit_event``) runs on a worker
   thread via ``asyncio.to_thread``.

2. Cancellation cleanup: when the parent task is cancelled
   mid-``_execute_with_retry``, the engine emits a
   ``cancelled``-status post_write under ``asyncio.shield`` so
   the timeline records the cancellation instead of leaving a
   dangling ``executing`` row.

3. ``_ctx`` is reset to an empty dict at the top of each new
   plan. Resumed plans (``event_type`` = ``"resumed"`` /
   ``"resumer"``) preserve ctx, since they're continuing prior
   work.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402
from solden.core.coordination_engine import CoordinationEngine  # noqa: E402
from solden.core.plan import Action, Plan  # noqa: E402


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgAsync", organization_name="Async Test")
    return inst


def _make_engine(db) -> CoordinationEngine:
    return CoordinationEngine(db=db, organization_id="orgAsync")


def _seed_box(db, *, item_id: str) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": "orgAsync",
        "vendor_name": "Vendor",
        "amount": 1.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "received",
    })
    return db.get_ap_item(item["id"])


# ─── 4a: pre/post-write are async + use asyncio.sleep ──────────────


class TestAsyncAuditWrites:
    def test_pre_write_is_async(self, db):
        """Calling _pre_write returns a coroutine, not a value.
        This catches a regression where the async signature gets
        accidentally reverted to sync."""
        engine = _make_engine(db)
        coro = engine._pre_write("box-1", Action("a", "DET", {}, ""), 0)
        assert asyncio.iscoroutine(coro)
        # Drain the coroutine so we don't get an unawaited warning.
        coro.close()

    def test_post_write_is_async(self, db):
        engine = _make_engine(db)
        coro = engine._post_write(
            "box-1", Action("a", "DET", {}, ""),
            0, "TL-x", "completed", "ok",
        )
        assert asyncio.iscoroutine(coro)
        coro.close()

    def test_pre_write_retry_uses_asyncio_sleep_not_time_sleep(self, db):
        """When the audit insert raises, the retry backoff must
        use ``await asyncio.sleep`` (yields the loop) rather than
        ``time.sleep`` (blocks the loop for hundreds of ms)."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-async-1")
        action = Action("apply_label", "DET", {}, "test")

        # Force the first 2 attempts to fail; 3rd succeeds.
        call_count = {"n": 0}
        original = engine.db.append_audit_event

        def flaky(payload):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("transient db blip")
            return original(payload)

        sleep_calls = {"count": 0, "delays": []}

        async def fake_asyncio_sleep(delay):
            sleep_calls["count"] += 1
            sleep_calls["delays"].append(delay)
            # Don't actually sleep — make tests fast.
            return None

        with patch.object(engine.db, "append_audit_event", side_effect=flaky), \
             patch("asyncio.sleep", side_effect=fake_asyncio_sleep):
            timeline_id = asyncio.run(
                engine._pre_write(item["id"], action, step=0)
            )

        assert timeline_id  # eventually succeeded
        # Two sleeps for two retries (50ms then 200ms backoff).
        assert sleep_calls["count"] == 2
        # The backoff schedule is 0.05 * 4^attempt.
        assert sleep_calls["delays"] == [0.05, 0.20]


# ─── 4b: cancellation cleanup ──────────────────────────────────────


class TestCancellationCleanup:
    def test_cancellation_emits_cancelled_post_write(self, db):
        """When the parent task is cancelled mid-action, the
        engine emits an ``agent_action:<name>:cancelled`` audit
        row and re-raises so the cancellation propagates."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-cancel-1")

        # Stub apply_label so it raises CancelledError when called.
        async def cancelling_handler(action, plan):
            raise asyncio.CancelledError()

        engine._handlers["apply_label"] = cancelling_handler

        plan = Plan(
            event_type="email_received",
            actions=[Action("apply_label", "DET", {}, "test")],
            box_id=item["id"],
            organization_id="orgAsync",
            correlation_id="evt-cancel-1",
        )

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(engine.execute(plan))

        events = db.list_ap_audit_events(item["id"], limit=20)
        types = [e.get("event_type") for e in events]
        # Both pre-write (executing) AND the cancelled post-write
        # must be on the timeline.
        assert "agent_action:apply_label:executing" in types
        assert "agent_action:apply_label:cancelled" in types

    def test_cancellation_does_not_leave_dangling_executing_rows(self, db):
        """For every executing pre-write, there must be a matching
        terminal post-write (completed, failed, or cancelled).
        Without the cancellation cleanup, cancelled actions would
        leave the executing row with no follow-up."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-cancel-2")

        async def cancelling_handler(action, plan):
            raise asyncio.CancelledError()

        engine._handlers["apply_label"] = cancelling_handler

        plan = Plan(
            event_type="email_received",
            actions=[Action("apply_label", "DET", {}, "test")],
            box_id=item["id"],
            organization_id="orgAsync",
            correlation_id="evt-cancel-2",
        )
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(engine.execute(plan))

        events = db.list_ap_audit_events(item["id"], limit=20)
        executing = [
            e for e in events
            if e.get("event_type") == "agent_action:apply_label:executing"
        ]
        terminal = [
            e for e in events
            if e.get("event_type", "").startswith("agent_action:apply_label:")
            and not e.get("event_type", "").endswith(":executing")
        ]
        assert len(executing) == len(terminal), (
            f"Mismatch between pre-write executing rows ({len(executing)}) "
            f"and terminal rows ({len(terminal)}); cancelled state is the "
            f"likely culprit."
        )


# ─── 4c: _ctx reset between plans ──────────────────────────────────


class TestCtxReset:
    def test_ctx_reset_at_top_of_each_plan(self, db):
        """After a plan completes, a SECOND plan run on the same
        engine instance must NOT see ctx keys from the first plan
        (vendor_profile, extracted_fields, body, attachments,
        match_result, etc.)."""
        engine = _make_engine(db)
        item_a = _seed_box(db, item_id="AP-ctx-1")
        item_b = _seed_box(db, item_id="AP-ctx-2")

        captured: dict = {}

        async def first_handler(action, plan):
            engine._ctx["leaked_key"] = "should not appear in plan B"
            engine._ctx["body"] = "first plan's email body"
            return {"ok": True}

        async def second_handler(action, plan):
            captured["ctx_at_start"] = dict(engine._ctx)
            return {"ok": True}

        engine._handlers["apply_label"] = first_handler
        plan_a = Plan(
            event_type="email_received",
            actions=[Action("apply_label", "DET", {}, "test")],
            box_id=item_a["id"],
            organization_id="orgAsync",
            correlation_id="evt-ctx-a",
        )
        asyncio.run(engine.execute(plan_a))

        engine._handlers["apply_label"] = second_handler
        plan_b = Plan(
            event_type="email_received",
            actions=[Action("apply_label", "DET", {}, "test")],
            box_id=item_b["id"],
            organization_id="orgAsync",
            correlation_id="evt-ctx-b",
        )
        asyncio.run(engine.execute(plan_b))

        # Plan B's handler ran with a fresh ctx — none of the
        # plan-A keys are visible.
        assert "leaked_key" not in captured["ctx_at_start"]
        assert "body" not in captured["ctx_at_start"]

    def test_resumed_plan_preserves_ctx(self, db):
        """A resumed plan continues prior work; its ctx should
        carry over the keys the saved plan had populated. Resumed
        plans are identified by event_type ``resumed`` or
        ``resumer``."""
        engine = _make_engine(db)
        item = _seed_box(db, item_id="AP-ctx-3")

        engine._ctx["pre_resume_marker"] = "should survive"

        captured: dict = {}

        async def capturing_handler(action, plan):
            captured["ctx_at_start"] = dict(engine._ctx)
            return {"ok": True}

        engine._handlers["apply_label"] = capturing_handler
        # event_type "resumed" signals to _execute_body that this
        # is a resumption, not a fresh plan.
        resumed_plan = Plan(
            event_type="resumed",
            actions=[Action("apply_label", "DET", {}, "test")],
            box_id=item["id"],
            organization_id="orgAsync",
            correlation_id="evt-resume-1",
        )
        # Run the body directly (skip the lock so we don't
        # interact with the resumer pattern).
        asyncio.run(engine._execute_body(resumed_plan))

        # The marker survives because resumed plans skip the reset.
        assert captured["ctx_at_start"].get("pre_resume_marker") == "should survive"
