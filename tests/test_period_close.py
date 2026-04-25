"""Tests for period close and accrual cutoff service.

Covers:
- Period detection (current period, closing window)
- Period lock/unlock
- Posting allowed check against locked periods
- Accrual report generation
- Backdated invoice detection
- Config persistence
- API endpoints
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData
from clearledgr.services.period_close import PeriodCloseService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    inst.create_organization("default", "Default Org", settings={})
    return inst


def _create_ap_item(db, item_id, vendor, amount, state="approved", invoice_date=None, created_at=None):
    db.create_ap_item({
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"t-{item_id}",
        "message_id": f"m-{item_id}",
        "subject": f"Invoice from {vendor}",
        "sender": "v@test.com",
        "vendor_name": vendor,
        "amount": amount,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "invoice_date": invoice_date,
        "state": state,
        "organization_id": "default",
    })
    if created_at:
        sql = "UPDATE ap_items SET created_at = %s WHERE id = %s"
        with db.connect() as conn:
            conn.cursor().execute(sql, (created_at, item_id))
            conn.commit()


# ---------------------------------------------------------------------------
# Period detection tests
# ---------------------------------------------------------------------------

class TestCurrentPeriod:
    def test_returns_period_info(self, db):
        svc = PeriodCloseService("default")
        result = svc.get_current_period()
        assert "period" in result
        assert "closes_on" in result
        assert "is_locked" in result
        assert "days_until_close" in result

    def test_default_close_day(self, db):
        svc = PeriodCloseService("default")
        result = svc.get_current_period()
        assert result["close_day_offset"] == 5


# ---------------------------------------------------------------------------
# Lock/unlock tests
# ---------------------------------------------------------------------------

class TestPeriodLock:
    def test_lock_period(self, db):
        svc = PeriodCloseService("default")
        assert svc.lock_period("2026-03") is True
        assert svc.is_period_locked("2026-03") is True

    def test_lock_idempotent(self, db):
        svc = PeriodCloseService("default")
        svc.lock_period("2026-03")
        assert svc.lock_period("2026-03") is False  # already locked

    def test_unlock_period(self, db):
        svc = PeriodCloseService("default")
        svc.lock_period("2026-03")
        assert svc.unlock_period("2026-03") is True
        assert svc.is_period_locked("2026-03") is False

    def test_unlock_not_locked(self, db):
        svc = PeriodCloseService("default")
        assert svc.unlock_period("2026-03") is False

    def test_posting_blocked_on_locked_period(self, db):
        svc = PeriodCloseService("default")
        svc.lock_period("2026-03")
        result = svc.check_posting_allowed("2026-03-15")
        assert result["allowed"] is False
        assert result["reason"] == "period_locked"

    def test_posting_allowed_on_open_period(self, db):
        svc = PeriodCloseService("default")
        result = svc.check_posting_allowed("2026-04-10")
        assert result["allowed"] is True

    def test_posting_allowed_with_no_date(self, db):
        svc = PeriodCloseService("default")
        result = svc.check_posting_allowed(None)
        assert result["allowed"] is True


# ---------------------------------------------------------------------------
# Accrual report tests
# ---------------------------------------------------------------------------

class TestAccrualReport:
    def test_accrual_candidates(self, db):
        # Create items in March that are approved but not paid
        _create_ap_item(db, "acc-1", "Acme", 5000.0, state="approved",
                        created_at="2026-03-15T10:00:00+00:00")
        _create_ap_item(db, "acc-2", "Beta", 3000.0, state="posted_to_erp",
                        created_at="2026-03-20T10:00:00+00:00")
        # Closed item should not appear
        _create_ap_item(db, "acc-3", "Gamma", 1000.0, state="closed",
                        created_at="2026-03-10T10:00:00+00:00")

        svc = PeriodCloseService("default")
        report = svc.generate_accrual_report("2026-03")

        assert report["accrual_count"] == 2
        assert report["total_by_currency"]["USD"] == 8000.0
        assert len(report["vendor_breakdown"]) == 2

    def test_empty_period(self, db):
        svc = PeriodCloseService("default")
        report = svc.generate_accrual_report("2025-01")
        assert report["accrual_count"] == 0
        assert report["total_by_currency"] == {}


# ---------------------------------------------------------------------------
# Backdated detection tests
# ---------------------------------------------------------------------------

class TestBackdatedDetection:
    def test_detects_backdated(self, db):
        # Invoice dated March 25 but received April 10 (after April 5 cutoff)
        _create_ap_item(db, "bd-1", "Late Vendor", 2000.0,
                        invoice_date="2026-03-25",
                        created_at="2026-04-10T10:00:00+00:00")

        svc = PeriodCloseService("default")
        items = svc.detect_backdated_invoices("2026-03")
        assert len(items) == 1
        assert items[0]["vendor_name"] == "Late Vendor"

    def test_not_backdated_if_received_before_cutoff(self, db):
        # Invoice dated March 25, received March 30 (before April 5 cutoff)
        _create_ap_item(db, "nbd-1", "On Time", 1000.0,
                        invoice_date="2026-03-25",
                        created_at="2026-03-30T10:00:00+00:00")

        svc = PeriodCloseService("default")
        items = svc.detect_backdated_invoices("2026-03")
        assert len(items) == 0


# ---------------------------------------------------------------------------
# Config persistence tests
# ---------------------------------------------------------------------------

class TestConfigPersistence:
    def test_save_and_load(self, db):
        svc = PeriodCloseService("default")
        config = {"close_day_offset": 10, "locked_periods": ["2026-01"], "auto_lock": True}
        svc.save_config(config)

        loaded = svc.get_config()
        assert loaded["close_day_offset"] == 10
        assert "2026-01" in loaded["locked_periods"]


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestPeriodCloseEndpoints:
    @pytest.fixture()
    def client(self, db):
        from main import app
        from clearledgr.api import workspace_shell as ws_module

        def _fake_user():
            return TokenData(
                user_id="pc-user",
                email="pc@test.com",
                organization_id="default",
                role="owner",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        app.dependency_overrides[ws_module.get_current_user] = _fake_user
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(ws_module.get_current_user, None)

    def test_current_period(self, client, db):
        resp = client.get("/api/workspace/period-close/current")
        assert resp.status_code == 200
        data = resp.json()
        assert "period" in data
        assert "closes_on" in data

    def test_lock_and_unlock(self, client, db):
        resp = client.post("/api/workspace/period-close/lock/2026-03")
        assert resp.status_code == 200
        assert resp.json()["status"] == "locked"

        resp = client.post("/api/workspace/period-close/unlock/2026-03")
        assert resp.status_code == 200
        assert resp.json()["status"] == "unlocked"

    def test_accrual_report(self, client, db):
        _create_ap_item(db, "api-acc", "Vendor X", 5000.0, state="approved",
                        created_at="2026-03-15T10:00:00+00:00")

        resp = client.get("/api/workspace/period-close/accruals/2026-03")
        assert resp.status_code == 200
        assert resp.json()["accrual_count"] >= 1

    def test_backdated(self, client, db):
        resp = client.get("/api/workspace/period-close/backdated/2026-03")
        assert resp.status_code == 200
        assert "backdated_count" in resp.json()
