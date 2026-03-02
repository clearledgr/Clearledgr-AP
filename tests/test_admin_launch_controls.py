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


def test_admin_ops_connector_readiness_endpoint(client, db):
    db.save_erp_connection(
        organization_id="default",
        erp_type="quickbooks",
        access_token="token",
        refresh_token="refresh",
        realm_id="realm-1",
    )
    _ = client.put(
        "/api/admin/ga-readiness",
        json={
            "organization_id": "default",
            "evidence": {
                "connector_checklists": {
                    "quickbooks": {"completed": True, "signed_off": True}
                }
            },
        },
    )

    response = client.get("/api/admin/ops/connector-readiness?organization_id=default")
    assert response.status_code == 200
    payload = response.json()
    report = payload["connector_readiness"]
    assert report["summary"]["configured_connectors_total"] == 1
    assert report["summary"]["enabled_connectors_total"] == 1
    assert any(row["erp_type"] == "quickbooks" for row in report["connectors"])


def test_admin_erp_connect_start_supports_sap_form(client, db):
    response = client.post(
        "/api/admin/integrations/erp/connect/start",
        json={"organization_id": "default", "erp_type": "sap"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["erp_type"] == "sap"
    assert payload["method"] == "form"
    assert payload["submit_url"] == "/api/admin/integrations/erp/connect/sap"
    field_names = {field["name"] for field in payload.get("fields", [])}
    assert {"base_url", "username", "password"}.issubset(field_names)


def test_admin_erp_connect_start_supports_netsuite_form(client, db):
    response = client.post(
        "/api/admin/integrations/erp/connect/start",
        json={"organization_id": "default", "erp_type": "netsuite"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["erp_type"] == "netsuite"
    assert payload["method"] == "form"
    assert payload["submit_url"] == "/api/admin/integrations/erp/connect/netsuite"
    field_names = {field["name"] for field in payload.get("fields", [])}
    assert {
        "account_id",
        "consumer_key",
        "consumer_secret",
        "token_id",
        "token_secret",
    }.issubset(field_names)


def test_admin_connect_netsuite_persists_connection(client, db, monkeypatch):
    async def _fake_get_netsuite_accounts(_connection):
        return [{"id": "2000", "name": "Accounts Payable"}]

    monkeypatch.setattr(
        "clearledgr.integrations.erp_router.get_netsuite_accounts",
        _fake_get_netsuite_accounts,
    )

    connect = client.post(
        "/api/admin/integrations/erp/connect/netsuite",
        json={
            "organization_id": "default",
            "account_id": "123456_SB1",
            "consumer_key": "consumer-key",
            "consumer_secret": "consumer-secret",
            "token_id": "token-id",
            "token_secret": "token-secret",
        },
    )
    assert connect.status_code == 200
    payload = connect.json()
    assert payload["success"] is True
    assert payload["erp_type"] == "netsuite"
    assert payload["accounts_found"] == 1

    integrations = client.get("/api/admin/integrations?organization_id=default")
    assert integrations.status_code == 200
    body = integrations.json()
    erp = next(item for item in body["integrations"] if item["name"] == "erp")
    assert erp["connected"] is True
    assert any((row.get("erp_type") == "netsuite") for row in erp.get("connections", []))


def test_admin_connect_sap_persists_connection(client, db, monkeypatch):
    class _Resp:
        def __init__(self, status_code: int):
            self.status_code = status_code

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            return _Resp(200)

    monkeypatch.setattr(admin_console_module.httpx, "AsyncClient", _FakeAsyncClient)

    connect = client.post(
        "/api/admin/integrations/erp/connect/sap",
        json={
            "organization_id": "default",
            "base_url": "https://sap.example.com/sap/byd/odata/v1/financials",
            "username": "integration-user",
            "password": "integration-secret",
        },
    )
    assert connect.status_code == 200
    assert connect.json()["erp_type"] == "sap"

    integrations = client.get("/api/admin/integrations?organization_id=default")
    assert integrations.status_code == 200
    payload = integrations.json()
    erp = next(item for item in payload["integrations"] if item["name"] == "erp")
    assert erp["connected"] is True
    assert any((row.get("erp_type") == "sap") for row in erp.get("connections", []))


def test_admin_teams_webhook_config_and_test(client, db, monkeypatch):
    save = client.post(
        "/api/admin/integrations/teams/webhook",
        json={
            "organization_id": "default",
            "webhook_url": "https://example.org/teams/incoming-webhook",
        },
    )
    assert save.status_code == 200
    assert save.json()["success"] is True

    integrations = client.get("/api/admin/integrations?organization_id=default")
    assert integrations.status_code == 200
    payload = integrations.json()
    teams = next(item for item in payload["integrations"] if item["name"] == "teams")
    assert teams["connected"] is True
    assert teams["managed_by"] == "org"

    class _FakeTeamsClient:
        def _post_json(self, _payload):
            return {"status": "sent", "status_code": 200}

    monkeypatch.setattr(admin_console_module.TeamsAPIClient, "from_env", lambda _org_id=None: _FakeTeamsClient())
    test = client.post(
        "/api/admin/integrations/teams/test",
        json={"organization_id": "default", "message": "test"},
    )
    assert test.status_code == 200
    assert test.json()["success"] is True


def test_admin_ops_learning_calibration_recompute_and_get(client, db):
    for idx in range(6):
        db.record_vendor_decision_feedback(
            "default",
            "Acme Supplies",
            ap_item_id=f"ap-{idx}",
            human_decision="approve" if idx < 4 else "reject",
            agent_recommendation="approve",
            decision_override=(idx >= 4),
            reason="policy_requirement_amt_500",
            source_channel="slack",
            actor_id="owner-1",
            action_outcome="completed",
        )

    recompute = client.post(
        "/api/admin/ops/learning-calibration/recompute",
        json={
            "organization_id": "default",
            "window_days": 180,
            "min_feedback": 5,
            "limit": 5000,
        },
    )
    assert recompute.status_code == 200
    recompute_payload = recompute.json()
    assert recompute_payload["success"] is True
    assert recompute_payload["snapshot"]["calibration_version"]
    assert recompute_payload["snapshot"]["summary"]["total_feedback"] == 6

    latest = client.get("/api/admin/ops/learning-calibration?organization_id=default")
    assert latest.status_code == 200
    latest_payload = latest.json()
    assert latest_payload["snapshot"]["calibration_version"] == recompute_payload["snapshot"]["calibration_version"]
