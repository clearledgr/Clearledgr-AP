"""Tests for APAgingReport service and the GET /aging API endpoint.

Covers:
- Aging buckets: current, 1-30, 31-60, 61-90, 90+ days past due
- Currency-aware totals (multi-currency support)
- Vendor breakdown per bucket per currency
- Summary stats (totals, overdue %, vendor count, weighted avg days past due)
- Items per bucket capped (default 50)
- Items with no due_date are excluded but counted in summary
- Closed/rejected items are excluded
- API endpoint returns correct structure
- Empty org returns empty buckets
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

from main import app
from clearledgr.api import ap_items_read_routes as read_routes_module
from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    db = db_module.get_db()
    db.initialize()
    return db


@pytest.fixture()
def client(db):
    def _fake_user():
        return TokenData(
            user_id="analyst-1",
            email="analyst@example.com",
            organization_id="default",
            role="owner",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[read_routes_module.get_current_user] = _fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(read_routes_module.get_current_user, None)


def _create_item(db, item_id, vendor, amount, due_date, state="approved", currency="USD"):
    """Create an ap_item with a specific due_date and state."""
    db.create_ap_item({
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"thread-{item_id}",
        "message_id": f"msg-{item_id}",
        "subject": f"Invoice from {vendor}",
        "sender": "billing@example.com",
        "vendor_name": vendor,
        "amount": amount,
        "currency": currency,
        "invoice_number": f"INV-{item_id}",
        "due_date": due_date,
        "state": state,
        "organization_id": "default",
    })


# ---------------------------------------------------------------------------
# Bucket classification tests
# ---------------------------------------------------------------------------

class TestAPAgingBuckets:
    """Test aging bucket classification."""

    def test_current_bucket(self, db):
        """Items due in the future land in 'current'."""
        future = (date.today() + timedelta(days=10)).isoformat()
        _create_item(db, "cur-1", "Vendor A", 1000.0, future)

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["buckets"]["current"]["count"] == 1
        assert result["buckets"]["current"]["totals_by_currency"]["USD"] == 1000.0
        assert result["buckets"]["1_30"]["count"] == 0

    def test_1_30_bucket(self, db):
        """Items 1-30 days past due."""
        past = (date.today() - timedelta(days=15)).isoformat()
        _create_item(db, "b30-1", "Vendor B", 500.0, past)

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["buckets"]["1_30"]["count"] == 1
        assert result["buckets"]["1_30"]["totals_by_currency"]["USD"] == 500.0

    def test_31_60_bucket(self, db):
        """Items 31-60 days past due."""
        past = (date.today() - timedelta(days=45)).isoformat()
        _create_item(db, "b60-1", "Vendor C", 750.0, past)

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["buckets"]["31_60"]["count"] == 1
        assert result["buckets"]["31_60"]["totals_by_currency"]["USD"] == 750.0

    def test_61_90_bucket(self, db):
        """Items 61-90 days past due."""
        past = (date.today() - timedelta(days=75)).isoformat()
        _create_item(db, "b90-1", "Vendor D", 2000.0, past)

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["buckets"]["61_90"]["count"] == 1
        assert result["buckets"]["61_90"]["totals_by_currency"]["USD"] == 2000.0

    def test_90_plus_bucket(self, db):
        """Items 90+ days past due."""
        past = (date.today() - timedelta(days=120)).isoformat()
        _create_item(db, "b90p-1", "Vendor E", 3000.0, past)

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["buckets"]["90_plus"]["count"] == 1
        assert result["buckets"]["90_plus"]["totals_by_currency"]["USD"] == 3000.0

    def test_due_today_is_current(self, db):
        """Item due exactly today lands in 'current' (0 days past due)."""
        today = date.today().isoformat()
        _create_item(db, "today-1", "Vendor F", 100.0, today)

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["buckets"]["current"]["count"] == 1


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------

class TestAPAgingFilters:
    """Test that closed/rejected/no-due-date items are excluded."""

    def test_closed_items_excluded(self, db):
        """Closed items should not appear in aging."""
        past = (date.today() - timedelta(days=10)).isoformat()
        _create_item(db, "closed-1", "Vendor G", 500.0, past, state="closed")

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["summary"]["total_open_count"] == 0

    def test_rejected_items_excluded(self, db):
        """Rejected items should not appear in aging."""
        past = (date.today() - timedelta(days=10)).isoformat()
        _create_item(db, "rej-1", "Vendor H", 300.0, past, state="rejected")

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["summary"]["total_open_count"] == 0

    def test_no_due_date_excluded_but_counted(self, db):
        """Items without due_date are excluded from buckets but counted in summary."""
        _create_item(db, "nodue-1", "Vendor I", 400.0, None, state="approved")

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["summary"]["total_open_count"] == 0
        assert result["summary"]["no_due_date_count"] == 1

    def test_posted_to_erp_included(self, db):
        """posted_to_erp is still 'open' for aging (not yet paid)."""
        past = (date.today() - timedelta(days=5)).isoformat()
        _create_item(db, "posted-1", "Vendor J", 800.0, past, state="posted_to_erp")

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["summary"]["total_open_count"] == 1


# ---------------------------------------------------------------------------
# Multi-currency tests
# ---------------------------------------------------------------------------

class TestAPAgingMultiCurrency:
    """Test currency-aware totals."""

    def test_separate_currency_totals(self, db):
        """USD and EUR should not be summed together."""
        past = (date.today() - timedelta(days=10)).isoformat()
        _create_item(db, "mc-1", "Vendor X", 1000.0, past, currency="USD")
        _create_item(db, "mc-2", "Vendor Y", 500.0, past, currency="EUR")

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        bucket = result["buckets"]["1_30"]
        assert bucket["totals_by_currency"]["USD"] == 1000.0
        assert bucket["totals_by_currency"]["EUR"] == 500.0
        assert bucket["count"] == 2

    def test_summary_currency_separation(self, db):
        """Summary totals are per-currency dicts, not single floats."""
        future = (date.today() + timedelta(days=5)).isoformat()
        past = (date.today() - timedelta(days=20)).isoformat()
        _create_item(db, "sc-1", "Vendor A", 1000.0, future, currency="USD")
        _create_item(db, "sc-2", "Vendor B", 2000.0, past, currency="NGN")

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["summary"]["total_open_payables"]["USD"] == 1000.0
        assert result["summary"]["total_open_payables"]["NGN"] == 2000.0
        assert result["summary"]["current_payables"]["USD"] == 1000.0
        assert result["summary"]["total_overdue"]["NGN"] == 2000.0

    def test_vendor_breakdown_per_currency(self, db):
        """Vendor breakdown includes a row per vendor per currency."""
        past = (date.today() - timedelta(days=10)).isoformat()
        _create_item(db, "vbc-1", "Acme Corp", 1000.0, past, currency="USD")
        _create_item(db, "vbc-2", "Acme Corp", 500.0, past, currency="EUR")

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        breakdown = result["vendor_breakdown"]
        assert len(breakdown) == 2  # one row per currency for Acme
        currencies = {row["currency"] for row in breakdown}
        assert currencies == {"USD", "EUR"}


# ---------------------------------------------------------------------------
# Vendor breakdown tests
# ---------------------------------------------------------------------------

class TestAPAgingVendorBreakdown:
    """Test vendor-level aging breakdown."""

    def test_vendor_breakdown_structure(self, db):
        """Each vendor gets per-bucket totals."""
        future = (date.today() + timedelta(days=5)).isoformat()
        past_20 = (date.today() - timedelta(days=20)).isoformat()
        past_50 = (date.today() - timedelta(days=50)).isoformat()

        _create_item(db, "vb-1", "Acme Corp", 1000.0, future)
        _create_item(db, "vb-2", "Acme Corp", 500.0, past_20)
        _create_item(db, "vb-3", "Beta LLC", 2000.0, past_50)

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        breakdown = result["vendor_breakdown"]
        assert len(breakdown) == 2

        # Sorted by total descending — Beta LLC (2000) first, then Acme (1500)
        assert breakdown[0]["vendor_name"] == "Beta LLC"
        assert breakdown[0]["total"] == 2000.0
        assert breakdown[0]["31_60"] == 2000.0

        assert breakdown[1]["vendor_name"] == "Acme Corp"
        assert breakdown[1]["total"] == 1500.0
        assert breakdown[1]["current"] == 1000.0
        assert breakdown[1]["1_30"] == 500.0


# ---------------------------------------------------------------------------
# Summary tests
# ---------------------------------------------------------------------------

class TestAPAgingSummary:
    """Test summary statistics."""

    def test_summary_totals(self, db):
        """Summary reflects correct totals and overdue %."""
        future = (date.today() + timedelta(days=10)).isoformat()
        past_10 = (date.today() - timedelta(days=10)).isoformat()
        past_100 = (date.today() - timedelta(days=100)).isoformat()

        _create_item(db, "sum-1", "Vendor K", 1000.0, future)      # current
        _create_item(db, "sum-2", "Vendor L", 2000.0, past_10)     # 1-30
        _create_item(db, "sum-3", "Vendor M", 3000.0, past_100)    # 90+

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        summary = result["summary"]
        assert summary["total_open_payables"]["USD"] == 6000.0
        assert summary["total_open_count"] == 3
        assert summary["total_overdue"]["USD"] == 5000.0
        assert summary["overdue_count"] == 2
        assert summary["current_payables"]["USD"] == 1000.0
        assert summary["current_count"] == 1
        assert summary["vendor_count"] == 3
        # Overdue % = 2/3 items = 66.7%
        assert summary["overdue_pct"] == 66.7

    def test_weighted_avg_days_past_due(self, db):
        """Weighted average should weight by dollar amount."""
        past_10 = (date.today() - timedelta(days=10)).isoformat()
        past_50 = (date.today() - timedelta(days=50)).isoformat()

        # $1000 at 10 days + $3000 at 50 days = (10*1000 + 50*3000) / 4000 = 40.0
        _create_item(db, "wavg-1", "Vendor A", 1000.0, past_10)
        _create_item(db, "wavg-2", "Vendor B", 3000.0, past_50)

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["summary"]["weighted_avg_days_past_due"] == 40.0

    def test_weighted_avg_none_when_all_current(self, db):
        """Weighted avg is None when no items are overdue."""
        future = (date.today() + timedelta(days=10)).isoformat()
        _create_item(db, "wavgn-1", "Vendor A", 1000.0, future)

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate()

        assert result["summary"]["weighted_avg_days_past_due"] is None

    def test_empty_org_summary(self, db):
        """Empty org returns zeros."""
        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("empty-org")
        result = report.generate()

        assert result["summary"]["total_open_payables"] == {}
        assert result["summary"]["total_open_count"] == 0
        assert result["summary"]["overdue_pct"] == 0.0
        assert result["summary"]["no_due_date_count"] == 0


# ---------------------------------------------------------------------------
# Items per bucket cap tests
# ---------------------------------------------------------------------------

class TestAPAgingItemsCap:
    """Test that items per bucket are capped."""

    def test_items_capped_at_limit(self, db):
        """Items array in each bucket should not exceed the limit."""
        past = (date.today() - timedelta(days=10)).isoformat()
        for i in range(10):
            _create_item(db, f"cap-{i}", f"Vendor {i}", 100.0, past)

        from clearledgr.services.ap_aging_report import APAgingReport
        report = APAgingReport("default")
        result = report.generate(items_per_bucket=3)

        bucket = result["buckets"]["1_30"]
        assert bucket["count"] == 10  # count reflects all items
        assert len(bucket["items"]) == 3  # but items array is capped


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestAPAgingEndpoint:
    """Test the GET /api/ap/items/aging endpoint."""

    def test_aging_endpoint_returns_200(self, client, db):
        future = (date.today() + timedelta(days=10)).isoformat()
        _create_item(db, "api-1", "Vendor N", 500.0, future)

        resp = client.get("/api/ap/items/aging?organization_id=default")
        assert resp.status_code == 200
        data = resp.json()
        assert "buckets" in data
        assert "vendor_breakdown" in data
        assert "summary" in data
        assert data["summary"]["total_open_count"] == 1

    def test_aging_endpoint_empty_org(self, client, db):
        """No AP items for this org — should return empty buckets."""
        resp = client.get("/api/ap/items/aging?organization_id=default")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_open_count"] == 0

    def test_aging_endpoint_has_as_of_date(self, client, db):
        resp = client.get("/api/ap/items/aging?organization_id=default")
        assert resp.status_code == 200
        data = resp.json()
        assert "as_of_date" in data
        assert data["as_of_date"] == date.today().isoformat()


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

class TestBucketLabel:
    """Test the _bucket_label helper uses AGING_BUCKETS, not hardcoded values."""

    def test_labels(self):
        from clearledgr.services.ap_aging_report import _bucket_label
        assert _bucket_label(-5) == "current"
        assert _bucket_label(0) == "current"
        assert _bucket_label(1) == "1_30"
        assert _bucket_label(30) == "1_30"
        assert _bucket_label(31) == "31_60"
        assert _bucket_label(60) == "31_60"
        assert _bucket_label(61) == "61_90"
        assert _bucket_label(90) == "61_90"
        assert _bucket_label(91) == "90_plus"
        assert _bucket_label(365) == "90_plus"


class TestParseDate:
    """Test APAgingReport._parse_date."""

    def test_iso_date(self):
        from clearledgr.services.ap_aging_report import APAgingReport
        assert APAgingReport._parse_date("2026-03-15") == date(2026, 3, 15)

    def test_iso_datetime(self):
        from clearledgr.services.ap_aging_report import APAgingReport
        assert APAgingReport._parse_date("2026-03-15T10:30:00+00:00") == date(2026, 3, 15)

    def test_none(self):
        from clearledgr.services.ap_aging_report import APAgingReport
        assert APAgingReport._parse_date(None) is None

    def test_garbage(self):
        from clearledgr.services.ap_aging_report import APAgingReport
        assert APAgingReport._parse_date("not-a-date") is None

    def test_date_object(self):
        from clearledgr.services.ap_aging_report import APAgingReport
        d = date(2026, 1, 1)
        assert APAgingReport._parse_date(d) == d
