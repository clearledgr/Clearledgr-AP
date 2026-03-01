from __future__ import annotations

from clearledgr.core import database as db_module
from clearledgr.services.learning_calibration import get_learning_calibration_service


def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "learning-calibration.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


def test_learning_calibration_snapshot_recompute_and_latest_roundtrip(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)

    for idx in range(12):
        db.record_vendor_decision_feedback(
            "default",
            "Acme Supplies",
            ap_item_id=f"ap-{idx}",
            human_decision="approve" if idx < 7 else "reject",
            agent_recommendation="approve",
            decision_override=(idx >= 7),
            reason="policy_requirement_amt_500" if idx >= 7 else "ok",
            source_channel="slack",
            actor_id="user-1",
            action_outcome="completed",
        )

    service = get_learning_calibration_service("default", db=db)
    snapshot = service.recompute_snapshot(window_days=180, min_feedback=5)
    latest = service.get_latest_snapshot()

    assert snapshot["organization_id"] == "default"
    assert snapshot["calibration_version"]
    assert snapshot["summary"]["total_feedback"] == 12
    assert snapshot["summary"]["override_count"] == 5
    assert snapshot["status"] in {"monitor", "recalibration_needed", "stable"}
    assert snapshot["top_vendor_calibration_gaps"]
    assert latest["calibration_version"] == snapshot["calibration_version"]
    assert latest["snapshot_id"] == snapshot["snapshot_id"]


def test_learning_calibration_snapshot_handles_no_feedback(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    service = get_learning_calibration_service("default", db=db)

    snapshot = service.recompute_snapshot(window_days=30, min_feedback=10)

    assert snapshot["status"] == "insufficient_signal"
    assert snapshot["summary"]["total_feedback"] == 0
    assert snapshot["recommendations"]
    assert snapshot["calibration_version"]

