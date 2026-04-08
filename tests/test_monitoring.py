"""Tests for monitoring and alerting service.

Covers:
- Individual health checks (dead letters, auth failures, stale autopilot, overdue, posting failures)
- Threshold configuration via env vars
- Alert emission (slack, webhook, log)
- Healthy system returns no alerts
- API endpoint
- Background loop wiring
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData
from clearledgr.services.monitoring import MonitoringService, run_monitoring_checks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "monitoring.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    inst = db_module.get_db()
    inst.initialize()
    return inst


# ---------------------------------------------------------------------------
# Individual check tests
# ---------------------------------------------------------------------------

class TestDeadLetterCheck:
    def test_no_dead_letters_is_healthy(self, db):
        svc = MonitoringService("default")
        result = svc._check_dead_letters()
        assert result["alert"] is False
        assert result["value"] == 0

    def test_dead_letters_trigger_alert(self, db):
        # Enqueue and exhaust notifications
        for i in range(6):
            notif_id = db.enqueue_notification("default", "slack", {"text": f"test-{i}"})
            # Manually mark as dead_letter
            sql = db._prepare_sql("UPDATE pending_notifications SET status = 'dead_letter' WHERE id = ?")
            with db.connect() as conn:
                conn.cursor().execute(sql, (notif_id,))
                conn.commit()

        svc = MonitoringService("default")
        result = svc._check_dead_letters()
        assert result["alert"] is True
        assert result["value"] == 6


class TestAuthFailureCheck:
    def test_no_failures_is_healthy(self, db):
        svc = MonitoringService("default")
        result = svc._check_auth_failures()
        assert result["alert"] is False

    def test_auth_failures_trigger_alert(self, db):
        for i in range(5):
            db.save_gmail_autopilot_state(
                user_id=f"fail-{i}",
                email=f"fail{i}@test.com",
                last_error="auth_failed",
            )

        svc = MonitoringService("default")
        result = svc._check_auth_failures()
        assert result["alert"] is True
        assert result["value"] == 5


class TestStaleAutopilotCheck:
    def test_no_users_is_healthy(self, db):
        svc = MonitoringService("default")
        result = svc._check_stale_autopilot()
        assert result["alert"] is False
        assert "No autopilot users" in result["message"]

    def test_stale_user_triggers_alert(self, db):
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        db.save_gmail_autopilot_state(
            user_id="stale-1",
            email="stale@test.com",
            last_scan_at=stale_time,
        )

        svc = MonitoringService("default")
        result = svc._check_stale_autopilot()
        assert result["alert"] is True
        assert result["value"] == 1

    def test_fresh_user_is_healthy(self, db):
        fresh_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        db.save_gmail_autopilot_state(
            user_id="fresh-1",
            email="fresh@test.com",
            last_scan_at=fresh_time,
        )

        svc = MonitoringService("default")
        result = svc._check_stale_autopilot()
        assert result["alert"] is False


class TestOverdueCheck:
    def test_no_overdue_is_healthy(self, db):
        svc = MonitoringService("default")
        result = svc._check_overdue_invoices()
        assert result["alert"] is False

    def test_overdue_triggers_alert(self, db, monkeypatch):
        monkeypatch.setenv("MONITOR_THRESHOLD_OVERDUE_INVOICES_MAX", "2")
        # Create 5 overdue AP items
        from datetime import date
        past = (date.today() - timedelta(days=45)).isoformat()
        for i in range(5):
            db.create_ap_item({
                "id": f"overdue-{i}",
                "invoice_key": f"inv-od-{i}",
                "thread_id": f"t-od-{i}",
                "message_id": f"m-od-{i}",
                "subject": f"Invoice {i}",
                "sender": "v@test.com",
                "vendor_name": "Vendor",
                "amount": 100.0,
                "currency": "USD",
                "invoice_number": f"INV-OD-{i}",
                "due_date": past,
                "state": "approved",
                "organization_id": "default",
            })

        svc = MonitoringService("default")
        result = svc._check_overdue_invoices()
        assert result["alert"] is True
        assert result["value"] == 5


class TestPostingFailureCheck:
    def test_no_failures_is_healthy(self, db):
        svc = MonitoringService("default")
        result = svc._check_posting_failures()
        assert result["alert"] is False


class TestApproverHealthCheck:
    def test_no_approvers_configured_is_healthy(self, db):
        svc = MonitoringService("default")
        result = svc._check_approver_health()
        assert result["alert"] is False
        assert "No approver emails" in result["message"]

    def test_known_active_approver_is_healthy(self, db):
        db.ensure_organization("default", organization_name="Default")
        db.create_user(email="approver@company.com", name="Approver", organization_id="default", role="operator")
        db.update_organization("default", settings={
            "approval_thresholds": [
                {"min_amount": 0, "max_amount": None, "approver_channel": "#approvals", "approvers": ["approver@company.com"]},
            ]
        })

        svc = MonitoringService("default")
        result = svc._check_approver_health()
        assert result["alert"] is False
        assert result["value"] == 0

    def test_unknown_approver_triggers_alert(self, db):
        db.ensure_organization("default", organization_name="Default")
        db.update_organization("default", settings={
            "approval_thresholds": [
                {"min_amount": 0, "max_amount": None, "approver_channel": "#approvals", "approvers": ["departed@company.com"]},
            ]
        })

        svc = MonitoringService("default")
        result = svc._check_approver_health()
        assert result["alert"] is True
        assert result["value"] == 1
        assert result["problems"][0]["email"] == "departed@company.com"
        assert result["problems"][0]["issue"] == "unknown_user"

    def test_inactive_approver_triggers_alert(self, db):
        db.ensure_organization("default", organization_name="Default")
        user = db.create_user(email="inactive@company.com", name="Gone", organization_id="default", role="operator")
        db.update_user(user["id"], is_active=False)
        db.update_organization("default", settings={
            "approval_thresholds": [
                {"min_amount": 0, "max_amount": None, "approver_channel": "#approvals", "approvers": ["inactive@company.com"]},
            ]
        })

        svc = MonitoringService("default")
        result = svc._check_approver_health()
        assert result["alert"] is True
        assert result["problems"][0]["issue"] == "inactive_user"

    def test_stale_login_approver_triggers_alert(self, db, monkeypatch):
        monkeypatch.setenv("MONITOR_THRESHOLD_APPROVER_STALE_DAYS", "7")
        db.ensure_organization("default", organization_name="Default")
        user = db.create_user(email="stale@company.com", name="Stale", organization_id="default", role="operator")
        old_login = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        db.update_user(user["id"], last_seen_at=old_login)
        db.update_organization("default", settings={
            "approval_thresholds": [
                {"min_amount": 0, "max_amount": None, "approver_channel": "#approvals", "approvers": ["stale@company.com"]},
            ]
        })

        svc = MonitoringService("default")
        result = svc._check_approver_health()
        assert result["alert"] is True
        assert result["problems"][0]["issue"] == "stale_login"

    def test_recently_active_approver_is_healthy(self, db, monkeypatch):
        monkeypatch.setenv("MONITOR_THRESHOLD_APPROVER_STALE_DAYS", "7")
        db.ensure_organization("default", organization_name="Default")
        user = db.create_user(email="active@company.com", name="Active", organization_id="default", role="operator")
        recent_login = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        db.update_user(user["id"], last_seen_at=recent_login)
        db.update_organization("default", settings={
            "approval_thresholds": [
                {"min_amount": 0, "max_amount": None, "approver_channel": "#approvals", "approvers": ["active@company.com"]},
            ]
        })

        svc = MonitoringService("default")
        result = svc._check_approver_health()
        assert result["alert"] is False

    def test_multiple_problems_reported(self, db):
        db.ensure_organization("default", organization_name="Default")
        user = db.create_user(email="deactivated@company.com", name="Gone", organization_id="default", role="operator")
        db.update_user(user["id"], is_active=False)
        db.update_organization("default", settings={
            "approval_thresholds": [
                {"min_amount": 0, "max_amount": None, "approver_channel": "#approvals",
                 "approvers": ["unknown@company.com", "deactivated@company.com"]},
            ]
        })

        svc = MonitoringService("default")
        result = svc._check_approver_health()
        assert result["alert"] is True
        assert result["value"] == 2
        issues = {p["issue"] for p in result["problems"]}
        assert "unknown_user" in issues
        assert "inactive_user" in issues


# ---------------------------------------------------------------------------
# Full run tests
# ---------------------------------------------------------------------------

class TestRunAllChecks:
    def test_healthy_system(self, db):
        svc = MonitoringService("default")
        result = svc.run_all_checks()
        assert result["healthy"] is True
        assert result["alert_count"] == 0
        assert result["check_count"] == 6

    def test_unhealthy_system(self, db):
        # Create auth failures
        for i in range(5):
            db.save_gmail_autopilot_state(
                user_id=f"bad-{i}", email=f"bad{i}@t.com", last_error="auth_failed",
            )

        svc = MonitoringService("default")
        result = svc.run_all_checks()
        assert result["healthy"] is False
        assert result["alert_count"] >= 1


class TestRunMonitoringChecks:
    def test_emits_alerts(self, db):
        for i in range(5):
            db.save_gmail_autopilot_state(
                user_id=f"alert-{i}", email=f"a{i}@t.com", last_error="auth_failed",
            )

        with patch("clearledgr.services.monitoring._alert_channels", return_value=["log"]):
            result = asyncio.run(run_monitoring_checks("default"))

        assert result["alert_count"] >= 1


# ---------------------------------------------------------------------------
# Threshold override tests
# ---------------------------------------------------------------------------

class TestThresholdOverride:
    def test_env_override(self, db, monkeypatch):
        monkeypatch.setenv("MONITOR_THRESHOLD_DEAD_LETTER_MAX", "100")
        # Create 10 dead letters — under the overridden threshold of 100
        for i in range(10):
            nid = db.enqueue_notification("default", "slack", {"text": f"t-{i}"})
            sql = db._prepare_sql("UPDATE pending_notifications SET status = 'dead_letter' WHERE id = ?")
            with db.connect() as conn:
                conn.cursor().execute(sql, (nid,))
                conn.commit()

        svc = MonitoringService("default")
        result = svc._check_dead_letters()
        assert result["alert"] is False  # 10 < 100


# ---------------------------------------------------------------------------
# API endpoint test
# ---------------------------------------------------------------------------

class TestMonitoringEndpoint:
    @pytest.fixture()
    def client(self, db):
        from main import app
        from clearledgr.api.ops import get_current_user

        def _fake_user():
            return TokenData(
                user_id="ops-1",
                email="ops@test.com",
                organization_id="default",
                role="owner",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        app.dependency_overrides[get_current_user] = _fake_user
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_monitoring_health_endpoint(self, client, db):
        resp = client.get("/api/ops/monitoring-health?organization_id=default")
        assert resp.status_code == 200
        data = resp.json()
        assert "healthy" in data
        assert "checks" in data
        assert "alerts" in data
        assert data["check_count"] == 6


# ---------------------------------------------------------------------------
# Background wiring test
# ---------------------------------------------------------------------------

class TestBackgroundWiring:
    def test_monitoring_function_exists(self, db):
        from clearledgr.services.agent_background import _run_monitoring_checks
        with patch("clearledgr.services.monitoring.run_monitoring_checks", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"alert_count": 0, "healthy": True}
            asyncio.run(_run_monitoring_checks("default"))
            mock_run.assert_called_once_with(organization_id="default")
