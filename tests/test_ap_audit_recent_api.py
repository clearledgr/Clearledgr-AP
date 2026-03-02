from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import _apply_runtime_surface_profile, app  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import create_access_token  # noqa: E402


def _item_payload(item_id: str, org_id: str) -> dict:
    return {
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"thread-{item_id}",
        "message_id": f"msg-{item_id}",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": f"Vendor {item_id}",
        "amount": 123.45,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "needs_approval",
        "confidence": 0.91,
        "organization_id": org_id,
        "metadata": {},
    }


def _jwt_for(org_id: str, user_id: str = "user-test") -> str:
    return create_access_token(
        user_id=user_id,
        email=f"{user_id}@{org_id}.example",
        organization_id=org_id,
        expires_delta=timedelta(hours=1),
    )


def _auth_headers(org_id: str, user_id: str = "user-test") -> dict:
    return {"Authorization": f"Bearer {_jwt_for(org_id, user_id)}"}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "audit-recent.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    monkeypatch.setenv("AP_V1_STRICT_SURFACES", "true")
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    _apply_runtime_surface_profile()
    db_module._DB_INSTANCE = None
    d = db_module.get_db()
    d.initialize()
    return d


@pytest.fixture()
def client(db):
    return TestClient(app)


def test_recent_ap_audit_is_org_scoped_and_normalized(client, db):
    db.create_ap_item(_item_payload("alpha-1", "org-alpha"))
    db.create_ap_item(_item_payload("alpha-2", "org-alpha"))
    db.create_ap_item(_item_payload("beta-1", "org-beta"))

    db.append_ap_audit_event(
        {
            "id": "evt-alpha-1",
            "ap_item_id": "alpha-1",
            "event_type": "deterministic_validation_failed",
            "decision_reason": "policy_requirement_amt_500",
            "organization_id": "org-alpha",
            "ts": "2026-03-01T01:00:00+00:00",
        }
    )
    db.append_ap_audit_event(
        {
            "id": "evt-alpha-2",
            "ap_item_id": "alpha-2",
            "event_type": "approval_nudge_failed",
            "decision_reason": "approval_nudge",
            "organization_id": "org-alpha",
            "ts": "2026-03-01T02:00:00+00:00",
        }
    )
    db.append_ap_audit_event(
        {
            "id": "evt-beta-1",
            "ap_item_id": "beta-1",
            "event_type": "state_transition",
            "from_state": "received",
            "to_state": "validated",
            "organization_id": "org-beta",
            "ts": "2026-03-01T03:00:00+00:00",
        }
    )

    response = client.get(
        "/api/ap/audit/recent?organization_id=org-alpha&limit=10",
        headers=_auth_headers("org-alpha"),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_id"] == "org-alpha"

    events = payload["events"]
    assert len(events) == 2
    assert all(str(event.get("ap_item_id", "")).startswith("alpha-") for event in events)
    assert events[0]["ts"] >= events[1]["ts"]  # newest first
    assert isinstance(events[0].get("operator_title"), str)
    assert isinstance(events[0].get("operator_message"), str)
    assert isinstance(events[0].get("operator_severity"), str)


def test_recent_ap_audit_rejects_cross_org_access(client, db):
    db.create_ap_item(_item_payload("alpha-1", "org-alpha"))
    db.append_ap_audit_event(
        {
            "id": "evt-alpha-1",
            "ap_item_id": "alpha-1",
            "event_type": "state_transition",
            "from_state": "received",
            "to_state": "validated",
            "organization_id": "org-alpha",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )

    response = client.get(
        "/api/ap/audit/recent?organization_id=org-alpha&limit=10",
        headers=_auth_headers("org-beta"),
    )
    assert response.status_code == 403
