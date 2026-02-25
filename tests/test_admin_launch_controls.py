from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app
from clearledgr.api import admin_console as admin_console_module
from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "admin-launch-controls.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


@pytest.fixture()
def client(db):
    def _fake_user():
        return TokenData(
            user_id="admin-user-1",
            email="admin@example.com",
            organization_id="default",
            role="owner",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[admin_console_module.get_current_user] = _fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(admin_console_module.get_current_user, None)


def test_admin_rollback_controls_put_get_and_health_projection(client, db):
    put = client.put(
        "/api/admin/rollback-controls",
        json={
            "organization_id": "default",
            "controls": {
                "erp_posting_disabled": True,
                "browser_fallback_disabled": True,
                "channel_actions_disabled": {"slack": True, "teams": False},
                "erp_connectors_disabled": ["XERO", "sap"],
                "reason": "incident_2026_02_25",
            },
        },
    )
    assert put.status_code == 200
    body = put.json()
    controls = body["rollback_controls"]
    assert controls["erp_posting_disabled"] is True
    assert controls["browser_fallback_disabled"] is True
    assert controls["channel_actions_disabled"]["slack"] is True
    assert controls["channel_actions_disabled"]["teams"] is False
    assert controls["erp_connectors_disabled"] == ["xero", "sap"]
    assert controls["updated_by"] == "admin-user-1"

    get = client.get("/api/admin/rollback-controls?organization_id=default")
    assert get.status_code == 200
    assert get.json()["rollback_controls"]["reason"] == "incident_2026_02_25"

    health = client.get("/api/admin/health?organization_id=default")
    assert health.status_code == 200
    health_body = health.json()
    assert health_body["launch_controls"]["rollback_controls"]["erp_posting_disabled"] is True
    assert "ga_readiness_summary" in health_body["launch_controls"]


def test_admin_ga_readiness_put_get_summary(client, db):
    put = client.put(
        "/api/admin/ga-readiness",
        json={
            "organization_id": "default",
            "evidence": {
                "source_of_record": {
                    "kind": "in_app_settings",
                    "external_url": "https://internal.example.com/launch/clearledgr-ap-v1",
                },
                "connector_checklists": {
                    "quickbooks": {"completed": True, "signed_off": True},
                    "xero": {"completed": True},
                },
                "runbooks": [
                    {"name": "AP Posting Rollback", "url": "https://runbooks.example.com/ap-rollback"}
                ],
                "parity_evidence": [
                    {"surface": "slack", "artifact": "slack_parity_2026_02_25.md"},
                    {"surface": "teams", "artifact": "teams_parity_2026_02_25.md"},
                ],
                "signoffs": [
                    {"role": "engineering", "signed_by": "eng-lead", "signed_at": "2026-02-25T12:00:00Z"},
                    {"role": "operations", "signed_by": "ops-lead", "signed_at": "2026-02-25T12:05:00Z"},
                ],
                "notes": ["GA dry-run complete"],
            },
        },
    )
    assert put.status_code == 200
    payload = put.json()
    summary = payload["summary"]
    assert summary["has_runbooks"] is True
    assert summary["has_parity_evidence"] is True
    assert summary["has_signoffs"] is True
    assert summary["connector_checklists_total"] == 2
    assert summary["connector_checklists_completed"] == 2
    assert summary["ready_for_ga"] is True

    get = client.get("/api/admin/ga-readiness?organization_id=default")
    assert get.status_code == 200
    get_payload = get.json()
    assert get_payload["ga_readiness"]["updated_by"] == "admin-user-1"
    assert len(get_payload["ga_readiness"]["runbooks"]) == 1
    assert len(get_payload["ga_readiness"]["parity_evidence"]) == 2
    assert get_payload["summary"]["ready_for_ga"] is True

