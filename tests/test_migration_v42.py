"""Migration v42 — drop ``ap_item_id`` on shared primitives.

Covers the fresh-DB path (new schema: box_id/box_type, no
``ap_item_id``) and the backfill path on a pre-v42 DB seeded with
AP and vendor-onboarding audit rows. Also asserts idempotency of
re-running the migration against an already-migrated schema.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

# The pre-v42 backfill tests simulate a historical SQLite schema by
# dropping and re-adding columns on an already-initialised DB. PG's
# stricter schema semantics (append-only triggers, strict column type
# system, different INSERT idempotency) make that simulation brittle —
# and the underlying v42 migration is already exercised on every
# session PG start. Mark the backfill simulations as SQLite-only and
# run the rest on whichever engine is active.
_PG_MODE = os.environ.get("TEST_DB_ENGINE", "postgres").strip().lower() == "postgres"
pytestmark_sqlite_only = pytest.mark.skipif(
    _PG_MODE,
    reason="pre-v42 schema simulation requires SQLite's lax DDL semantics",
)


def _fresh_db(tmp_path, monkeypatch):
    import clearledgr.core.database as db_module
    db = db_module.get_db()
    db.initialize()
    return db


def _column_names(db, table):
    # PRAGMA is SQLite-only; information_schema on PG.
    with db.connect() as conn:
        cur = conn.cursor()
        if db.use_postgres:
            cur.execute(
                (
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = %s ORDER BY ordinal_position"
                ),
                (table,),
            )
            return [r[0] for r in cur.fetchall()]
        cur.execute(f"PRAGMA table_info({table})")
        return [r[1] for r in cur.fetchall()]


def _rerun_v42(db):
    from clearledgr.core.migrations import _MIGRATIONS
    _, _, fn = next(m for m in _MIGRATIONS if m[0] == 42)
    with db.connect() as conn:
        cur = conn.cursor()
        fn(cur, db)
        conn.commit()


class TestFreshSchema:

    def test_box_columns_present_ap_item_id_gone(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        for table in ("audit_events", "llm_call_log", "pending_notifications"):
            cols = _column_names(db, table)
            assert "box_id" in cols, f"{table} missing box_id"
            assert "box_type" in cols, f"{table} missing box_type"
            assert "ap_item_id" not in cols, (
                f"{table} still has ap_item_id after v42 (should be dropped)"
            )


@pytestmark_sqlite_only
class TestBackfillOnPreV42DB:
    """Simulate a pre-v42 schema where ``audit_events`` has
    ``ap_item_id`` but neither box_id nor box_type, then run v42
    and verify the backfill + DROP COLUMN land cleanly.
    """

    def _build_pre_v42_db(self, tmp_path, monkeypatch):
        """Create a DB at pre-v42 schema — add ap_item_id column
        and seed rows on it, then drop the new box_id/box_type
        columns so we can observe the migration run from scratch.
        """
        db = _fresh_db(tmp_path, monkeypatch)
        with db.connect() as conn:
            cur = conn.cursor()
            # Drop append-only trigger + indexes that reference the
            # current columns; SQLite otherwise errors on DROP COLUMN.
            cur.execute("DROP TRIGGER IF EXISTS trg_audit_events_no_update")
            cur.execute("DROP INDEX IF EXISTS idx_audit_box")
            cur.execute("DROP INDEX IF EXISTS idx_audit_events_box")
            cur.execute("ALTER TABLE audit_events DROP COLUMN box_id")
            cur.execute("ALTER TABLE audit_events DROP COLUMN box_type")
            cur.execute("ALTER TABLE audit_events ADD COLUMN ap_item_id TEXT")
            conn.commit()
        return db

    def _seed_ap_row(self, db, ap_item_id):
        sql = (
            """INSERT INTO audit_events
            (id, ap_item_id, event_type, prev_state, new_state,
             actor_type, actor_id, payload_json, source, correlation_id,
             workflow_id, run_id, decision_reason, organization_id, ts)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        )
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                str(uuid.uuid4()), ap_item_id, "state_transition",
                None, "validated", "agent", "test", "{}", "test",
                None, None, None, None, "test-org",
                "2026-04-17T12:00:00Z",
            ))
            conn.commit()

    def _seed_vendor_row(self, db, session_id):
        payload = json.dumps({"session_id": session_id, "reason": "test"})
        sql = (
            """INSERT INTO audit_events
            (id, ap_item_id, event_type, prev_state, new_state,
             actor_type, actor_id, payload_json, source, correlation_id,
             workflow_id, run_id, decision_reason, organization_id, ts)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        )
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                str(uuid.uuid4()), "",
                "vendor_onboarding_state_transition", "invited", "kyc",
                "agent", "test", payload, "test",
                None, None, None, None, "test-org",
                "2026-04-17T12:00:00Z",
            ))
            conn.commit()

    def test_ap_row_backfill_then_column_drop(self, tmp_path, monkeypatch):
        db = self._build_pre_v42_db(tmp_path, monkeypatch)
        self._seed_ap_row(db, "ap-123")
        self._seed_ap_row(db, "ap-456")

        _rerun_v42(db)

        # After v42: ap_item_id gone, box_id/box_type populated.
        cols = _column_names(db, "audit_events")
        assert "ap_item_id" not in cols
        assert "box_id" in cols
        assert "box_type" in cols

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT box_id, box_type FROM audit_events "
                "WHERE box_id IN ('ap-123', 'ap-456') ORDER BY box_id"
            )
            rows = cur.fetchall()

        assert len(rows) == 2
        for r in rows:
            box_id, box_type = r[0], r[1]
            assert box_id in ("ap-123", "ap-456")
            assert box_type == "ap_item"

    def test_vendor_row_backfill_from_payload_session_id(self, tmp_path, monkeypatch):
        db = self._build_pre_v42_db(tmp_path, monkeypatch)
        self._seed_vendor_row(db, "VO-abc123")
        self._seed_vendor_row(db, "VO-def456")

        _rerun_v42(db)

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT box_id, box_type FROM audit_events "
                "WHERE event_type = 'vendor_onboarding_state_transition' "
                "ORDER BY box_id"
            )
            rows = cur.fetchall()

        assert len(rows) == 2
        ids = sorted(r[0] for r in rows)
        assert ids == ["VO-abc123", "VO-def456"]
        for r in rows:
            assert r[1] == "vendor_onboarding_session"


class TestIdempotencyOnFreshSchema:
    """Fresh DBs ship with the post-v42 schema (box_id/box_type
    present, ap_item_id absent). Re-running v42 must be a no-op.
    """

    def test_rerun_on_fresh_schema_is_noop(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)

        # Seed one row the normal way.
        db.append_audit_event({
            "ap_item_id": "ap-fresh",
            "event_type": "state_transition",
            "to_state": "validated",
            "actor_type": "agent",
            "actor_id": "test",
            "organization_id": "test-org",
        })

        _rerun_v42(db)

        # Row still reads correctly post re-run.
        events = db.list_box_audit_events("ap_item", "ap-fresh")
        assert len(events) == 1
        assert events[0]["box_id"] == "ap-fresh"
        assert events[0]["box_type"] == "ap_item"


class TestIdempotencyKeyStillFires:
    """The idempotency_key UNIQUE constraint on audit_events must
    still fire after v42. Column topology changed, but uniqueness
    must be preserved.
    """

    def test_duplicate_idempotency_key_blocked(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)

        payload = {
            "ap_item_id": "ap-dup",
            "event_type": "state_transition",
            "to_state": "validated",
            "actor_type": "agent",
            "actor_id": "test",
            "organization_id": "test-org",
            "idempotency_key": "dup-key-1",
        }
        first = db.append_audit_event(payload)
        assert first is not None

        # Re-append with same key — funnel returns the existing row.
        second = db.append_audit_event(payload)
        assert second is not None
        assert second.get("id") == first.get("id")

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM audit_events WHERE idempotency_key = 'dup-key-1'"
            )
            count = cur.fetchone()[0]
        assert count == 1
