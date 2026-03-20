from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app
from clearledgr.api import ops as ops_module
from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "ap-extraction-drift.db"))
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


@pytest.fixture()
def client(db):
    def _fake_user():
        return TokenData(
            user_id="ops-user-1",
            email="ops@example.com",
            organization_id="default",
            role="owner",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[ops_module.get_current_user] = _fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ops_module.get_current_user, None)


def _set_item_timestamps(db, item_id: str, ts: datetime) -> None:
    iso_ts = ts.astimezone(timezone.utc).isoformat()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            db._prepare_sql("UPDATE ap_items SET created_at = ?, updated_at = ? WHERE id = ?"),
            (iso_ts, iso_ts, item_id),
        )
        conn.commit()


def _create_item(
    db,
    *,
    item_id: str,
    vendor: str,
    state: str,
    amount: float,
    created_at: datetime,
    metadata: dict | None = None,
) -> dict:
    item = db.create_ap_item(
        {
            "id": item_id,
            "invoice_key": f"inv-{item_id}",
            "thread_id": f"thread-{item_id}",
            "message_id": f"msg-{item_id}",
            "subject": f"Invoice {item_id}",
            "sender": "billing@example.com",
            "vendor_name": vendor,
            "amount": amount,
            "currency": "USD",
            "invoice_number": f"INV-{item_id}",
            "state": state,
            "organization_id": "default",
            "metadata": metadata or {},
        }
    )
    _set_item_timestamps(db, item["id"], created_at)
    return item


def test_ap_kpis_expose_vendor_drift_scorecards_and_review_sampling(client, db):
    now = datetime.now(timezone.utc)

    clean_email_provenance = {
        "field_provenance": {
            "amount": {"source": "email"},
            "currency": {"source": "email"},
            "invoice_number": {"source": "email"},
            "vendor": {"source": "email"},
        }
    }
    shifted_attachment_provenance = {
        "field_provenance": {
            "amount": {"source": "attachment"},
            "currency": {"source": "attachment"},
            "invoice_number": {"source": "attachment"},
            "vendor": {"source": "attachment"},
        }
    }

    for index in range(3):
        _create_item(
            db,
            item_id=f"ACME-BASE-{index}",
            vendor="Acme Forms",
            state="posted_to_erp",
            amount=100.0 + index,
            created_at=now - timedelta(days=20 + index),
            metadata=clean_email_provenance,
        )

    blocked_metadata = {
        **shifted_attachment_provenance,
        "requires_field_review": True,
        "source_conflicts": [
            {
                "field": "amount",
                "blocking": True,
                "reason": "source_value_mismatch",
                "preferred_source": "attachment",
                "values": {"email": "100.00", "attachment": "120.00"},
            },
            {
                "field": "invoice_number",
                "blocking": True,
                "reason": "source_value_mismatch",
                "preferred_source": "attachment",
                "values": {"email": "INV-OLD", "attachment": "INV-NEW"},
            },
        ],
    }
    blocked_open = _create_item(
        db,
        item_id="ACME-RECENT-BLOCKED-1",
        vendor="Acme Forms",
        state="validated",
        amount=120.0,
        created_at=now - timedelta(days=2),
        metadata=blocked_metadata,
    )
    _create_item(
        db,
        item_id="ACME-RECENT-BLOCKED-2",
        vendor="Acme Forms",
        state="approved",
        amount=121.0,
        created_at=now - timedelta(days=1),
        metadata=blocked_metadata,
    )
    clean_recent = _create_item(
        db,
        item_id="ACME-RECENT-CLEAN",
        vendor="Acme Forms",
        state="received",
        amount=119.0,
        created_at=now - timedelta(hours=8),
        metadata=shifted_attachment_provenance,
    )

    for index in range(2):
        _create_item(
            db,
            item_id=f"STABLE-{index}",
            vendor="Stable Vendor",
            state="posted_to_erp",
            amount=80.0 + index,
            created_at=now - timedelta(days=2 + index),
            metadata=clean_email_provenance,
        )

    response = client.get("/api/ops/ap-kpis?organization_id=default")
    assert response.status_code == 200

    payload = response.json()
    telemetry = (payload.get("kpis") or {}).get("agentic_telemetry") or {}
    extraction_drift = telemetry.get("extraction_drift") or {}
    summary = extraction_drift.get("summary") or {}

    assert summary["vendors_monitored"] >= 2
    assert summary["vendors_at_risk"] >= 1
    assert summary["high_risk_vendors"] >= 1
    assert summary["sampled_review_count"] >= 2

    scorecards = extraction_drift.get("vendor_scorecards") or []
    acme_scorecard = next(row for row in scorecards if row["vendor_name"] == "Acme Forms")
    assert acme_scorecard["drift_risk"] == "high"
    assert acme_scorecard["recent_requires_field_review_count"] == 2
    assert acme_scorecard["recent_blocking_conflict_count"] == 2
    assert "amount" in acme_scorecard["top_conflict_fields"]
    assert "invoice_number" in acme_scorecard["top_conflict_fields"]
    assert "field_review_rate_spike" in acme_scorecard["risk_signals"]
    assert "blocking_conflict_rate_spike" in acme_scorecard["risk_signals"]
    assert any(signal.startswith("source_shift:amount:email->attachment") for signal in acme_scorecard["risk_signals"])

    review_queue = [entry for entry in (extraction_drift.get("sampled_review_queue") or []) if entry["vendor_name"] == "Acme Forms"]
    assert len(review_queue) >= 2
    assert any(entry["ap_item_id"] == blocked_open["id"] for entry in review_queue)
    assert any(entry["ap_item_id"] == clean_recent["id"] for entry in review_queue)
    assert any(entry["sample_reason"] == "blocking_conflict_present" for entry in review_queue)
    assert any(entry["sample_reason"] == "vendor_layout_shift_check" for entry in review_queue)
