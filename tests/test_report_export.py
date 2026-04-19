"""Tests for report export service and API endpoint.

Covers:
- AP aging report export (CSV + JSON)
- Vendor spend report export
- Posting status report with date/vendor filters
- Unknown report type returns 400
- CSV serialization
- API endpoint returns correct format
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app
from clearledgr.api import workspace_shell as ws_module
from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData
from clearledgr.services.report_export import generate_report, rows_to_csv, REPORT_TYPES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "reports.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    inst = db_module.get_db()
    inst.initialize()
    return inst


@pytest.fixture()
def client(db):
    def _fake_user():
        return TokenData(
            user_id="reporter-1",
            email="reporter@example.com",
            organization_id="default",
            role="owner",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[ws_module.get_current_user] = _fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ws_module.get_current_user, None)


def _create_ap_item(db, item_id, vendor, amount, state="approved", due_date=None, erp_posted_at=None):
    db.create_ap_item({
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"thread-{item_id}",
        "message_id": f"msg-{item_id}",
        "subject": f"Invoice from {vendor}",
        "sender": "billing@example.com",
        "vendor_name": vendor,
        "amount": amount,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "due_date": due_date,
        "state": state,
        "organization_id": "default",
    })
    if erp_posted_at:
        db.update_ap_item(item_id, erp_posted_at=erp_posted_at)


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------

class TestAPAgingExport:
    def test_returns_vendor_breakdown_rows(self, db):
        future = (date.today() + timedelta(days=10)).isoformat()
        past = (date.today() - timedelta(days=20)).isoformat()
        _create_ap_item(db, "age-1", "Acme Corp", 1000.0, due_date=future)
        _create_ap_item(db, "age-2", "Beta LLC", 2000.0, due_date=past)

        rows, columns = generate_report("ap_aging", "default")

        assert len(rows) == 2
        assert "vendor_name" in columns
        assert "current" in columns
        assert "90_plus" in columns
        vendors = {r["vendor_name"] for r in rows}
        assert vendors == {"Acme Corp", "Beta LLC"}

    def test_empty_org_returns_empty(self, db):
        rows, columns = generate_report("ap_aging", "empty-org")
        assert rows == []
        assert len(columns) > 0


class TestVendorSpendExport:
    def test_returns_sectioned_rows(self, db):
        now = datetime.now(timezone.utc)
        _create_ap_item(db, "sp-1", "Vendor A", 5000.0, state="posted_to_erp")
        # Backdate
        created = (now - timedelta(days=10)).isoformat()
        with db.connect() as conn:
            conn.execute(
                "UPDATE ap_items SET created_at = ? WHERE id = ?",
                (created, "sp-1"),
            )
            conn.commit()

        rows, columns = generate_report("vendor_spend", "default", period_days=30)

        assert "section" in columns
        sections = {r["section"] for r in rows}
        # Should have at least vendor and monthly_trend sections
        assert "vendor" in sections or "monthly_trend" in sections

    def test_empty_org_returns_structure(self, db):
        rows, columns = generate_report("vendor_spend", "empty-org")
        assert isinstance(rows, list)
        assert "section" in columns


class TestPostingStatusExport:
    def test_returns_ap_items_with_timing(self, db):
        now = datetime.now(timezone.utc)
        posted = (now - timedelta(days=1)).isoformat()
        _create_ap_item(db, "ps-1", "Vendor X", 3000.0, state="posted_to_erp", erp_posted_at=posted)

        rows, columns = generate_report("posting_status", "default")

        assert len(rows) >= 1
        assert "days_to_post" in columns
        assert "erp_posted_at" in columns
        row = rows[0]
        assert row["vendor_name"] == "Vendor X"
        assert row["state"] == "posted_to_erp"

    def test_vendor_filter(self, db):
        _create_ap_item(db, "pf-1", "Acme Corp", 1000.0)
        _create_ap_item(db, "pf-2", "Beta LLC", 2000.0)

        rows, _ = generate_report("posting_status", "default", vendor="Acme")

        assert len(rows) == 1
        assert rows[0]["vendor_name"] == "Acme Corp"

    def test_date_filter(self, db):
        _create_ap_item(db, "df-1", "Vendor Y", 500.0)

        # Filter to future dates — should get nothing
        future = (date.today() + timedelta(days=30)).isoformat()
        rows, _ = generate_report("posting_status", "default", start_date=future)

        assert len(rows) == 0


class TestUnknownReportType:
    def test_returns_empty(self, db):
        rows, columns = generate_report("nonexistent", "default")
        assert rows == []
        assert columns == []


class TestCSVSerialization:
    def test_rows_to_csv(self):
        rows = [
            {"name": "Alice", "amount": 100.5},
            {"name": "Bob", "amount": 200.0},
        ]
        columns = ["name", "amount"]
        csv_str = rows_to_csv(rows, columns)

        # rows_to_csv prepends a UTF-8 BOM so Excel on Windows opens
        # the file as UTF-8. csv.DictReader is BOM-unaware — it would
        # read the first column as '\ufeffname'. Strip the BOM before
        # parsing so the keys match the columns list as users see them.
        if csv_str.startswith("\ufeff"):
            csv_str = csv_str[1:]
        reader = csv.DictReader(io.StringIO(csv_str))
        parsed = list(reader)
        assert len(parsed) == 2
        assert parsed[0]["name"] == "Alice"
        assert parsed[1]["amount"] == "200.0"

    def test_empty_rows(self):
        csv_str = rows_to_csv([], ["col_a", "col_b"])
        lines = csv_str.strip().split("\n")
        assert len(lines) == 1  # header only


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestReportExportEndpoint:
    def test_json_export(self, client, db):
        future = (date.today() + timedelta(days=5)).isoformat()
        _create_ap_item(db, "api-1", "Acme", 1000.0, due_date=future)

        resp = client.get("/api/workspace/reports/export?report_type=ap_aging&format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["report_type"] == "ap_aging"
        assert "rows" in data
        assert "columns" in data
        assert data["row_count"] >= 1

    def test_csv_export(self, client, db):
        _create_ap_item(db, "csv-1", "Beta", 2000.0)

        resp = client.get("/api/workspace/reports/export?report_type=posting_status&format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "attachment" in resp.headers.get("content-disposition", "")

        # Parse CSV
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) >= 1
        assert "vendor_name" in rows[0]

    def test_unknown_type_returns_400(self, client, db):
        resp = client.get("/api/workspace/reports/export?report_type=bogus&format=json")
        assert resp.status_code == 400
        assert "Unknown" in resp.json()["error"]

    def test_vendor_spend_export(self, client, db):
        resp = client.get("/api/workspace/reports/export?report_type=vendor_spend&format=json&period_days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert data["report_type"] == "vendor_spend"
        assert "columns" in data

    def test_posting_status_with_filters(self, client, db):
        _create_ap_item(db, "filt-1", "Gamma Corp", 500.0)

        resp = client.get("/api/workspace/reports/export?report_type=posting_status&format=json&vendor=Gamma")
        assert resp.status_code == 200
        data = resp.json()
        assert all(r["vendor_name"] == "Gamma Corp" for r in data["rows"])
