"""Tests for the per-box advisory lock (Group 2 deferred-a).

Closes the audit's concurrency gap: two CoordinationEngine
instances on the same box (Celery redelivery, webhook + timer
overlap, agent loop + retry job racing) used to share state
freely, allowing duplicate ERP posts and interleaved state
transitions. Now a Postgres advisory lock keyed on (org, box)
serializes plan execution at the engine level.

What's tested here:

  1. Lock primitive (``_acquire_box_lock`` / ``_release_box_lock``):
       - Acquire returns connection on success
       - Concurrent acquire on the same box returns "held"
       - Different boxes don't block each other
       - Different orgs don't block each other (same box_id literal)
       - Release returns the connection to the pool

  2. Engine integration:
       - ``execute()`` returns ``status="lock_held"`` when another
         instance holds the lock; no audit rows written
       - ``execute()`` releases the lock after a successful run
       - ``execute()`` releases the lock when a handler raises
       - Plans with no box_id skip the lock entirely (intake actions)
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
    inst.ensure_organization("orgLockA", organization_name="Lock Test A")
    inst.ensure_organization("orgLockB", organization_name="Lock Test B")
    return inst


def _make_engine(db, org: str = "orgLockA") -> CoordinationEngine:
    return CoordinationEngine(db=db, organization_id=org)


def _seed_box(db, *, item_id: str, org: str = "orgLockA") -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Vendor",
        "amount": 1.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "received",
    })
    return db.get_ap_item(item["id"])


# ─── Lock primitive ─────────────────────────────────────────────────


class TestBoxLockPrimitive:
    def test_acquire_returns_connection_when_free(self, db):
        engine = _make_engine(db)
        box = _seed_box(db, item_id="AP-lock-1")
        conn, status = engine._acquire_box_lock(box["id"])
        try:
            assert status == "acquired"
            assert conn is not None
        finally:
            engine._release_box_lock(conn, box["id"])

    def test_concurrent_acquire_on_same_box_returns_held(self, db):
        """Second acquirer on the same (org, box) gets ``held``;
        the first has the advisory lock until it releases."""
        engine1 = _make_engine(db)
        engine2 = _make_engine(db)
        box = _seed_box(db, item_id="AP-lock-2")

        conn1, status1 = engine1._acquire_box_lock(box["id"])
        try:
            assert status1 == "acquired"
            conn2, status2 = engine2._acquire_box_lock(box["id"])
            assert status2 == "held"
            assert conn2 is None
        finally:
            engine1._release_box_lock(conn1, box["id"])

        # After release, a fresh acquire succeeds.
        conn3, status3 = engine2._acquire_box_lock(box["id"])
        try:
            assert status3 == "acquired"
        finally:
            engine2._release_box_lock(conn3, box["id"])

    def test_different_boxes_do_not_block_each_other(self, db):
        engine = _make_engine(db)
        box_a = _seed_box(db, item_id="AP-lock-3a")
        box_b = _seed_box(db, item_id="AP-lock-3b")

        conn_a, status_a = engine._acquire_box_lock(box_a["id"])
        try:
            assert status_a == "acquired"
            # A different box with the same engine: should also acquire.
            conn_b, status_b = engine._acquire_box_lock(box_b["id"])
            try:
                assert status_b == "acquired"
            finally:
                engine._release_box_lock(conn_b, box_b["id"])
        finally:
            engine._release_box_lock(conn_a, box_a["id"])

    def test_different_orgs_with_same_box_id_literal_do_not_block(self, db):
        """Same string for box_id but distinct orgs → distinct lock
        keys (org_id is part of the hash). The two locks don't
        interfere."""
        engine_a = _make_engine(db, org="orgLockA")
        engine_b = _make_engine(db, org="orgLockB")
        # Same string literal, different orgs.
        same_id = "AP-shared-id"
        # Seed in both orgs (avoid id collision: use distinct ids in db
        # but use the same literal for the lock-key hash test).
        _seed_box(db, item_id=f"{same_id}-A", org="orgLockA")
        _seed_box(db, item_id=f"{same_id}-B", org="orgLockB")

        conn_a, status_a = engine_a._acquire_box_lock(same_id)
        try:
            assert status_a == "acquired"
            conn_b, status_b = engine_b._acquire_box_lock(same_id)
            try:
                # Different orgs → keys differ → both acquire.
                assert status_b == "acquired"
            finally:
                engine_b._release_box_lock(conn_b, same_id)
        finally:
            engine_a._release_box_lock(conn_a, same_id)

    def test_no_box_id_returns_no_infra(self, db):
        engine = _make_engine(db)
        conn, status = engine._acquire_box_lock("")
        assert conn is None
        assert status == "no_infra"

    def test_lock_keys_are_stable_for_same_inputs(self, db):
        """The hash function is deterministic so the same (org,
        box) pair produces the same key on every call. Without
        this, restarted engines couldn't recognize their own
        prior locks."""
        engine = _make_engine(db, org="orgLockA")
        keys_1 = engine._box_lock_keys("AP-stable-1")
        keys_2 = engine._box_lock_keys("AP-stable-1")
        assert keys_1 == keys_2

    def test_lock_keys_differ_for_different_inputs(self, db):
        engine_a = _make_engine(db, org="orgLockA")
        engine_b = _make_engine(db, org="orgLockB")
        # Same box_id, different org.
        assert engine_a._box_lock_keys("AP-1") != engine_b._box_lock_keys("AP-1")
        # Same org, different box.
        assert (
            engine_a._box_lock_keys("AP-1")
            != engine_a._box_lock_keys("AP-2")
        )


# ─── Engine integration ────────────────────────────────────────────


class TestEngineLockIntegration:
    def test_execute_returns_lock_held_when_concurrent(self, db):
        """With one engine holding the lock, a second engine's
        ``execute()`` on the same box returns ``lock_held``
        immediately and does NOT write any audit rows."""
        holder = _make_engine(db)
        runner = _make_engine(db)
        box = _seed_box(db, item_id="AP-lock-int-1")

        held_conn, status = holder._acquire_box_lock(box["id"])
        try:
            assert status == "acquired"

            audit_before = len(db.list_ap_audit_events(box["id"], limit=50))

            plan = Plan(
                event_type="email_received",
                actions=[Action("apply_label", "DET", {"label": "x"}, "test")],
                box_id=box["id"],
                organization_id="orgLockA",
                correlation_id="evt-lock-1",
            )
            result = asyncio.run(runner.execute(plan))

            assert result.status == "lock_held"
            assert result.box_id == box["id"]
            assert result.steps_completed == 0

            # No audit rows written: lock-held bail-out happens before
            # any pre-write.
            audit_after = len(db.list_ap_audit_events(box["id"], limit=50))
            assert audit_after == audit_before
        finally:
            holder._release_box_lock(held_conn, box["id"])

    def test_execute_releases_lock_on_success(self, db):
        """After a clean ``execute()``, the lock is released —
        verified by acquiring it again from a different engine."""
        engine1 = _make_engine(db)
        engine2 = _make_engine(db)
        box = _seed_box(db, item_id="AP-lock-int-2")

        plan = Plan(
            event_type="email_received",
            actions=[Action("apply_label", "DET", {"label": "x"}, "test")],
            box_id=box["id"],
            organization_id="orgLockA",
            correlation_id="evt-lock-2",
        )

        # Stub apply_label so the test doesn't need a Gmail client.
        async def fake_apply_label(action, plan):
            return {"ok": True}

        engine1._handlers["apply_label"] = fake_apply_label
        result = asyncio.run(engine1.execute(plan))
        assert result.status == "completed"

        # Engine 2 should now be able to acquire the lock.
        conn, status = engine2._acquire_box_lock(box["id"])
        try:
            assert status == "acquired", "Lock was not released after execute()"
        finally:
            engine2._release_box_lock(conn, box["id"])

    def test_execute_releases_lock_on_handler_exception(self, db):
        """If a handler raises, the lock must still release —
        otherwise the box would be permanently stuck after any
        unhandled error."""
        engine1 = _make_engine(db)
        engine2 = _make_engine(db)
        box = _seed_box(db, item_id="AP-lock-int-3")

        async def raising_handler(action, plan):
            raise RuntimeError("simulated handler crash")

        engine1._handlers["apply_label"] = raising_handler
        plan = Plan(
            event_type="email_received",
            actions=[Action("apply_label", "DET", {}, "test")],
            box_id=box["id"],
            organization_id="orgLockA",
            correlation_id="evt-lock-3",
        )

        # The engine catches handler exceptions internally
        # (_execute_with_retry); the result will be aborted, not
        # propagated. Either way, the finally block must release
        # the lock.
        try:
            asyncio.run(engine1.execute(plan))
        except Exception:
            pass

        # Lock must be free now.
        conn, status = engine2._acquire_box_lock(box["id"])
        try:
            assert status == "acquired", (
                "Lock was not released after handler exception"
            )
        finally:
            engine2._release_box_lock(conn, box["id"])

    def test_execute_without_box_id_skips_lock(self, db):
        """A plan with no box (intake actions like create_box)
        runs without attempting a lock — there's nothing to
        serialize against yet."""
        engine = _make_engine(db)
        plan = Plan(
            event_type="email_received",
            actions=[Action("noop_action", "DET", {}, "test")],
            box_id=None,
            organization_id="orgLockA",
            correlation_id="evt-lock-4",
        )

        async def fake_handler(action, plan):
            return {"ok": True}

        engine._handlers["noop_action"] = fake_handler

        # Patch _acquire_box_lock to track whether it's called.
        acquire_calls = {"count": 0}
        original_acquire = engine._acquire_box_lock

        def tracking_acquire(*args, **kwargs):
            acquire_calls["count"] += 1
            return original_acquire(*args, **kwargs)

        with patch.object(engine, "_acquire_box_lock", side_effect=tracking_acquire):
            result = asyncio.run(engine.execute(plan))

        assert result.status == "completed"
        assert acquire_calls["count"] == 0, (
            "execute() should not attempt the lock when plan has no box_id"
        )
