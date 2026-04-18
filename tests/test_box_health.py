"""Tests for the Box health drill-down endpoint.

DESIGN_THESIS.md §7 ("Box health observable"): the team must be
able to see which specific Boxes are stuck, in what stage, for how
long. Aggregates ("4 stuck") aren't enough — the product has to
breathe in the open. This test file encodes that contract.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "health.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import clearledgr.core.database as db_module
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


def _seed_ap_item(db, ap_id, state, **overrides):
    payload = {
        "id": ap_id,
        "invoice_key": f"inv-{ap_id}",
        "thread_id": f"t-{ap_id}",
        "message_id": f"m-{ap_id}",
        "subject": f"Invoice {ap_id}",
        "sender": "vendor@test.com",
        "vendor_name": overrides.get("vendor_name", "Acme Inc"),
        "amount": overrides.get("amount", 1000.0),
        "currency": "USD",
        "state": state,
        "organization_id": "test-org",
        "last_error": overrides.get("last_error"),
    }
    db.create_ap_item(payload)


def _seed_state_entry(db, ap_id, new_state, minutes_ago):
    """Insert a state_transition audit event with a controlled ts so
    ``get_box_health`` sees the Box entered ``new_state`` that long ago.
    """
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    sql = db._prepare_sql(
        """INSERT INTO audit_events
        (id, ap_item_id, event_type, prev_state, new_state,
         actor_type, actor_id, payload_json, source, correlation_id,
         workflow_id, run_id, decision_reason, organization_id, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (
            str(uuid.uuid4()), ap_id, "state_transition", None, new_state,
            "agent", "test", "{}", "test", None, None, None, None,
            "test-org", ts,
        ))
        conn.commit()


class TestBoxHealthDrillDown:

    def test_stuck_boxes_are_listed_with_time_in_stage(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)

        _seed_ap_item(db, "ap-stuck-approval", "needs_approval")
        _seed_state_entry(db, "ap-stuck-approval", "needs_approval", minutes_ago=500)

        _seed_ap_item(db, "ap-fresh", "needs_approval")
        _seed_state_entry(db, "ap-fresh", "needs_approval", minutes_ago=10)

        health = db.get_box_health(
            "test-org",
            stuck_threshold_minutes=120,
            approval_sla_minutes=240,
        )

        stuck_ids = [b["ap_item_id"] for b in health["stuck_boxes"]]
        assert "ap-stuck-approval" in stuck_ids
        assert "ap-fresh" not in stuck_ids

        stuck_row = next(b for b in health["stuck_boxes"] if b["ap_item_id"] == "ap-stuck-approval")
        assert stuck_row["state"] == "needs_approval"
        assert stuck_row["stuck_reason"] == "awaiting_approval_over_sla"
        assert stuck_row["time_in_stage_minutes"] >= 240

    def test_terminal_states_are_excluded(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)

        # posted_to_erp is terminal — even if ancient, it shouldn't appear.
        _seed_ap_item(db, "ap-posted", "posted_to_erp")
        _seed_state_entry(db, "ap-posted", "posted_to_erp", minutes_ago=10_000)

        _seed_ap_item(db, "ap-rejected", "rejected")
        _seed_state_entry(db, "ap-rejected", "rejected", minutes_ago=10_000)

        health = db.get_box_health("test-org")
        stuck_ids = [b["ap_item_id"] for b in health["stuck_boxes"]]
        assert "ap-posted" not in stuck_ids
        assert "ap-rejected" not in stuck_ids
        # Terminal states also don't show up in time_in_stage buckets.
        assert "posted_to_erp" not in health["time_in_stage"]
        assert "rejected" not in health["time_in_stage"]

    def test_exception_clusters_group_by_state(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)

        _seed_ap_item(db, "ap-ni-1", "needs_info", last_error="missing PO reference")
        _seed_ap_item(db, "ap-ni-2", "needs_info", last_error="amount mismatch")
        _seed_ap_item(db, "ap-fp-1", "failed_post", last_error="401 unauthorized — token expired")
        _seed_ap_item(db, "ap-ok", "validated")

        health = db.get_box_health("test-org")
        clusters = {c["state"]: c for c in health["exception_clusters"]}

        assert "needs_info" in clusters
        assert clusters["needs_info"]["count"] == 2
        assert set(clusters["needs_info"]["sample_ap_item_ids"]) == {"ap-ni-1", "ap-ni-2"}
        assert len(clusters["needs_info"]["sample_errors"]) == 2

        assert "failed_post" in clusters
        assert clusters["failed_post"]["count"] == 1

        # Non-exception state must not appear.
        assert "validated" not in clusters

    def test_time_in_stage_buckets_summarise_all_open_states(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)

        _seed_ap_item(db, "ap-a", "validated")
        _seed_state_entry(db, "ap-a", "validated", minutes_ago=60)
        _seed_ap_item(db, "ap-b", "validated")
        _seed_state_entry(db, "ap-b", "validated", minutes_ago=120)
        _seed_ap_item(db, "ap-c", "needs_approval")
        _seed_state_entry(db, "ap-c", "needs_approval", minutes_ago=30)

        health = db.get_box_health("test-org", stuck_threshold_minutes=500)

        bucket = health["time_in_stage"]["validated"]
        assert bucket["count"] == 2
        assert 60 <= bucket["avg_minutes"] <= 120
        assert bucket["max_minutes"] >= 120

        na_bucket = health["time_in_stage"]["needs_approval"]
        assert na_bucket["count"] == 1

    def test_stuck_boxes_sorted_by_time_in_stage_desc(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)

        _seed_ap_item(db, "ap-old", "validated")
        _seed_state_entry(db, "ap-old", "validated", minutes_ago=800)
        _seed_ap_item(db, "ap-newer", "validated")
        _seed_state_entry(db, "ap-newer", "validated", minutes_ago=200)

        health = db.get_box_health("test-org", stuck_threshold_minutes=120)
        ids = [b["ap_item_id"] for b in health["stuck_boxes"]]
        assert ids.index("ap-old") < ids.index("ap-newer")

    def test_organization_isolation(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)

        _seed_ap_item(db, "ap-mine", "validated")
        _seed_state_entry(db, "ap-mine", "validated", minutes_ago=500)

        # Seed another org's stuck Box
        db.create_ap_item({
            "id": "ap-theirs",
            "invoice_key": "inv-other",
            "state": "validated",
            "organization_id": "other-org",
            "vendor_name": "Other Co",
            "amount": 500.0,
        })
        # Insert their audit event under other-org
        ts = (datetime.now(timezone.utc) - timedelta(minutes=500)).isoformat()
        sql = db._prepare_sql(
            """INSERT INTO audit_events
            (id, ap_item_id, event_type, prev_state, new_state,
             actor_type, actor_id, payload_json, source, correlation_id,
             workflow_id, run_id, decision_reason, organization_id, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        )
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                str(uuid.uuid4()), "ap-theirs", "state_transition", None, "validated",
                "agent", "test", "{}", "test", None, None, None, None,
                "other-org", ts,
            ))
            conn.commit()

        health = db.get_box_health("test-org")
        ids = [b["ap_item_id"] for b in health["stuck_boxes"]]
        assert "ap-mine" in ids
        assert "ap-theirs" not in ids
