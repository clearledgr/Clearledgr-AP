"""Tests for resume_from_pending_plan wiring + CAS (Group 2 deferred-b).

Closes the audit's #3 critical: ``_handle_resume_plan`` previously
read ``pending_plan`` and returned ``{"resumed_plan": ...}`` that
NO caller consumed. Boxes paused mid-19-step invoice plan never
advanced. Now the handler:

  1. Atomically reads + clears pending_plan (single UPDATE with
     RETURNING — Postgres row-level locking serializes concurrent
     resumers).
  2. Deserializes the saved Plan.
  3. Runs it via ``_execute_body`` (outer ``execute()`` already
     holds the per-box lock; nested ``execute()`` would deadlock
     against itself with ``status="lock_held"``).
  4. Bubbles the resumed plan's outcome up as a result dict.

What's tested here:

  * CAS primitive: read+clear is atomic; concurrent resumers see
    only one win.
  * Handler with no pending_plan returns no-op.
  * Handler with valid pending_plan resumes and runs the saved
    actions.
  * Resumed plan inherits the parent's correlation_id (from
    Plan.from_json).
  * Invalid JSON / wrong shape doesn't crash the handler.
  * Empty plan resumption is a no-op.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

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
    inst.ensure_organization("orgResume", organization_name="Resume Test")
    return inst


def _make_engine(db) -> CoordinationEngine:
    return CoordinationEngine(db=db, organization_id="orgResume")


def _seed_box(db, *, item_id: str) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": "orgResume",
        "vendor_name": "Vendor",
        "amount": 1.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "received",
    })
    return db.get_ap_item(item["id"])


def _saved_plan_json(box_id: str, action_name: str = "apply_label") -> str:
    plan = Plan(
        event_type="resumed",
        actions=[Action(action_name, "DET", {}, "resumed action")],
        box_id=box_id,
        organization_id="orgResume",
        correlation_id="evt-orig-1",
    )
    return plan.to_json()


# ─── CAS primitive ─────────────────────────────────────────────────


class TestPendingPlanCAS:
    def test_cas_returns_none_when_no_pending_plan(self, db):
        engine = _make_engine(db)
        box = _seed_box(db, item_id="AP-resume-cas-1")
        # No pending_plan set; CAS returns None.
        result = engine._cas_clear_pending_plan(box["id"])
        assert result is None

    def test_cas_returns_value_and_clears_column(self, db):
        engine = _make_engine(db)
        box = _seed_box(db, item_id="AP-resume-cas-2")
        saved = _saved_plan_json(box["id"])
        db.update_ap_item(box["id"], pending_plan=saved)

        # First call wins.
        first = engine._cas_clear_pending_plan(box["id"])
        assert first is not None
        # Could be string or dict depending on row factory; normalize.
        if isinstance(first, dict):
            first_dict = first
        else:
            first_dict = json.loads(first)
        assert first_dict["actions"][0]["name"] == "apply_label"

        # After CAS clears, the column is None.
        refreshed = db.get_ap_item(box["id"])
        assert not refreshed.get("pending_plan")

    def test_cas_concurrent_only_one_wins(self, db):
        """Two CAS calls back-to-back: first returns the saved
        plan, second returns None. Postgres' row-level lock on
        UPDATE serializes them; only the first sees the value."""
        engine = _make_engine(db)
        box = _seed_box(db, item_id="AP-resume-cas-3")
        saved = _saved_plan_json(box["id"])
        db.update_ap_item(box["id"], pending_plan=saved)

        a = engine._cas_clear_pending_plan(box["id"])
        b = engine._cas_clear_pending_plan(box["id"])
        assert a is not None
        assert b is None


# ─── _handle_resume_plan ───────────────────────────────────────────


class TestHandleResumePlan:
    def test_no_box_id_returns_noop(self, db):
        engine = _make_engine(db)
        plan = Plan(
            event_type="resumer",
            actions=[Action("resume_from_pending_plan", "DET", {}, "test")],
            box_id=None,
            organization_id="orgResume",
        )
        result = asyncio.run(
            engine._handle_resume_plan(plan.actions[0], plan)
        )
        assert result == {"ok": True, "resumed": False, "reason": "no_box_id"}

    def test_no_pending_plan_returns_noop(self, db):
        engine = _make_engine(db)
        box = _seed_box(db, item_id="AP-resume-1")
        plan = Plan(
            event_type="resumer",
            actions=[Action("resume_from_pending_plan", "DET", {}, "test")],
            box_id=box["id"],
            organization_id="orgResume",
        )
        result = asyncio.run(
            engine._handle_resume_plan(plan.actions[0], plan)
        )
        assert result["ok"] is True
        assert result["resumed"] is False
        assert result["reason"] == "no_pending_plan"

    def test_valid_pending_plan_is_resumed(self, db):
        engine = _make_engine(db)
        box = _seed_box(db, item_id="AP-resume-2")
        saved = _saved_plan_json(box["id"])
        db.update_ap_item(box["id"], pending_plan=saved)

        # Stub apply_label so we don't need a Gmail client.
        async def fake_apply_label(action, plan):
            return {"ok": True, "labeled": True}

        engine._handlers["apply_label"] = fake_apply_label

        outer_plan = Plan(
            event_type="resumer",
            actions=[Action("resume_from_pending_plan", "DET", {}, "test")],
            box_id=box["id"],
            organization_id="orgResume",
        )
        result = asyncio.run(
            engine._handle_resume_plan(outer_plan.actions[0], outer_plan)
        )
        assert result["ok"] is True
        assert result["resumed"] is True
        assert result["resumed_status"] == "completed"
        assert result["resumed_steps"] == 1

        # pending_plan was cleared by the CAS.
        refreshed = db.get_ap_item(box["id"])
        assert not refreshed.get("pending_plan")

    def test_concurrent_resume_only_one_runs_handler(self, db):
        """Two redelivered events both reaching the handler: only
        the CAS winner runs the saved actions. The other returns
        a no-op. Without this, the saved plan would double-execute."""
        engine = _make_engine(db)
        box = _seed_box(db, item_id="AP-resume-3")
        saved = _saved_plan_json(box["id"])
        db.update_ap_item(box["id"], pending_plan=saved)

        run_count = {"count": 0}

        async def counted_apply_label(action, plan):
            run_count["count"] += 1
            return {"ok": True}

        engine._handlers["apply_label"] = counted_apply_label

        outer = Plan(
            event_type="resumer",
            actions=[Action("resume_from_pending_plan", "DET", {}, "test")],
            box_id=box["id"],
            organization_id="orgResume",
        )

        result_a = asyncio.run(engine._handle_resume_plan(outer.actions[0], outer))
        result_b = asyncio.run(engine._handle_resume_plan(outer.actions[0], outer))

        # First wins, runs the action.
        assert result_a["resumed"] is True
        # Second loses the CAS, no-op.
        assert result_b["resumed"] is False
        assert result_b["reason"] == "no_pending_plan"
        # apply_label ran exactly once.
        assert run_count["count"] == 1

    def test_invalid_json_returns_noop(self, db):
        engine = _make_engine(db)
        box = _seed_box(db, item_id="AP-resume-4")
        # Garbage payload in pending_plan.
        db.update_ap_item(box["id"], pending_plan="not valid json {{{")

        outer = Plan(
            event_type="resumer",
            actions=[Action("resume_from_pending_plan", "DET", {}, "test")],
            box_id=box["id"],
            organization_id="orgResume",
        )
        result = asyncio.run(
            engine._handle_resume_plan(outer.actions[0], outer)
        )
        assert result["ok"] is True
        assert result["resumed"] is False
        assert result["reason"] == "deserialization_failed"

    def test_empty_plan_returns_noop(self, db):
        engine = _make_engine(db)
        box = _seed_box(db, item_id="AP-resume-5")
        empty_plan = Plan(
            event_type="resumer",
            actions=[],
            box_id=box["id"],
            organization_id="orgResume",
            correlation_id="evt-empty-1",
        )
        db.update_ap_item(box["id"], pending_plan=empty_plan.to_json())

        outer = Plan(
            event_type="resumer",
            actions=[Action("resume_from_pending_plan", "DET", {}, "test")],
            box_id=box["id"],
            organization_id="orgResume",
        )
        result = asyncio.run(
            engine._handle_resume_plan(outer.actions[0], outer)
        )
        assert result["resumed"] is False
        assert result["reason"] == "empty_plan"

    def test_resumed_plan_inherits_correlation_id(self, db):
        """The serialized plan carries correlation_id; the
        resumed Plan deserializes with the same id so its audit
        rows dedupe correctly under the original event identity."""
        engine = _make_engine(db)
        box = _seed_box(db, item_id="AP-resume-6")
        saved_plan = Plan(
            event_type="email_received",
            actions=[Action("apply_label", "DET", {}, "test")],
            box_id=box["id"],
            organization_id="orgResume",
            correlation_id="evt-original-correlation-7",
        )
        db.update_ap_item(box["id"], pending_plan=saved_plan.to_json())

        captured = {"correlation_id": None}

        async def capture_apply_label(action, plan):
            captured["correlation_id"] = plan.correlation_id
            return {"ok": True}

        engine._handlers["apply_label"] = capture_apply_label

        outer = Plan(
            event_type="resumer",
            actions=[Action("resume_from_pending_plan", "DET", {}, "test")],
            box_id=box["id"],
            organization_id="orgResume",
        )
        asyncio.run(engine._handle_resume_plan(outer.actions[0], outer))

        assert captured["correlation_id"] == "evt-original-correlation-7"
