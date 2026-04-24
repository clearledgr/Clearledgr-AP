"""Tests for the Box health drill-down endpoint.

DESIGN_THESIS.md §7 ("Box health observable"): the team must be
able to see which specific Boxes are stuck, in what stage, for how
long. Aggregates ("4 stuck") aren't enough — the product has to
breathe in the open. This test file encodes that contract.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _fresh_db(tmp_path, monkeypatch):
    import clearledgr.core.database as db_module
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


def _seed_state_entry(db, ap_id, new_state, minutes_ago, org_id="test-org",
                      box_type="ap_item"):
    """Insert a state_transition audit event with a controlled ts so
    ``get_box_health`` sees the Box entered ``new_state`` that long ago.
    """
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    sql = db._prepare_sql(
        """INSERT INTO audit_events
        (id, box_id, box_type, event_type, prev_state, new_state,
         actor_type, actor_id, payload_json, source, correlation_id,
         workflow_id, run_id, decision_reason, organization_id, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (
            str(uuid.uuid4()), ap_id, box_type, "state_transition", None, new_state,
            "agent", "test", "{}", "test", None, None, None, None,
            org_id, ts,
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

        stuck_ids = [b["box_id"] for b in health["stuck_boxes"]]
        assert "ap-stuck-approval" in stuck_ids
        assert "ap-fresh" not in stuck_ids

        stuck_row = next(b for b in health["stuck_boxes"] if b["box_id"] == "ap-stuck-approval")
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
        stuck_ids = [b["box_id"] for b in health["stuck_boxes"]]
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
        assert set(clusters["needs_info"]["sample_box_ids"]) == {"ap-ni-1", "ap-ni-2"}
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
        ids = [b["box_id"] for b in health["stuck_boxes"]]
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
        # Insert their audit event under other-org (helper now
        # writes box_id/box_type, not ap_item_id).
        _seed_state_entry(
            db, "ap-theirs", "validated", minutes_ago=500, org_id="other-org",
        )

        health = db.get_box_health("test-org")
        ids = [b["box_id"] for b in health["stuck_boxes"]]
        assert "ap-mine" in ids
        assert "ap-theirs" not in ids


class TestBoxHealthAcrossBoxTypes:
    """Post-Phase-1 generalization: get_box_health reads open/exception
    state sets from the registry, so non-AP Box types work too.
    """

    def test_registry_drives_state_sets_for_ap(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)

        _seed_ap_item(db, "ap-stuck", "validated")
        _seed_state_entry(db, "ap-stuck", "validated", minutes_ago=500)

        # Defaulting to ap_item (back-compat) still works.
        health_default = db.get_box_health("test-org", stuck_threshold_minutes=120)
        # Explicit box_type="ap_item" returns the same result.
        health_explicit = db.get_box_health(
            "test-org", stuck_threshold_minutes=120, box_type="ap_item",
        )
        assert health_default["stuck_count"] == health_explicit["stuck_count"]
        assert health_explicit["box_type"] == "ap_item"

    def test_vendor_onboarding_box_health(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)

        # Seed one pending vendor onboarding session.
        session = db.create_vendor_onboarding_session(
            organization_id="test-org",
            vendor_name="Acme Inc",
            invited_by="ap@test-org",
        )
        session_id = session["id"]

        # Backdate last_activity_at so the Box shows some time-in-stage.
        # (Health's fallback to updated_at/created_at covers absence of a
        # state_transition audit event for this session.)
        past = (datetime.now(timezone.utc) - timedelta(minutes=300)).isoformat()
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                db._prepare_sql(
                    "UPDATE vendor_onboarding_sessions "
                    "SET updated_at = ?, created_at = ? WHERE id = ?"
                ),
                (past, past, session_id),
            )
            conn.commit()

        health = db.get_box_health(
            "test-org",
            stuck_threshold_minutes=120,
            box_type="vendor_onboarding_session",
        )
        assert health["box_type"] == "vendor_onboarding_session"
        # The seeded session is in 'invited' state and past the stuck threshold.
        assert health["stuck_count"] >= 1
        stuck_ids = [b["box_id"] for b in health["stuck_boxes"]]
        assert session_id in stuck_ids
        # 'invited' is an open (pre-active) state → should appear in time-in-stage.
        assert "invited" in health["time_in_stage"]

    def test_unknown_box_type_raises(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        with pytest.raises((KeyError, NotImplementedError)):
            db.get_box_health("test-org", box_type="does_not_exist")
