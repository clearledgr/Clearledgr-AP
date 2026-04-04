"""Tests for vendor statement reconciliation.

Covers:
- Exact reference matching
- Partial reference matching
- Amount + date proximity matching
- Unmatched items on both sides
- Amount discrepancies
- Summary stats (match rate, totals, difference)
- API endpoint
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData
from clearledgr.services.vendor_statement_recon import VendorStatementRecon


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "recon.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    inst = db_module.get_db()
    inst.initialize()
    return inst


def _create_ap_item(db, item_id, vendor, amount, invoice_number="", state="posted_to_erp", invoice_date=None):
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
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "state": state,
        "organization_id": "default",
    })


# ---------------------------------------------------------------------------
# Matching tests
# ---------------------------------------------------------------------------

class TestReferenceMatching:
    def test_exact_reference_match(self, db):
        _create_ap_item(db, "r1", "Acme", 1000.0, invoice_number="INV-001")

        svc = VendorStatementRecon("default")
        result = svc.reconcile("Acme", [
            {"reference": "INV-001", "amount": 1000.0, "date": "2026-03-15"},
        ])

        assert result["summary"]["matched_count"] == 1
        assert result["matched"][0]["match_type"] == "reference_exact"

    def test_partial_reference_match(self, db):
        _create_ap_item(db, "r2", "Acme", 500.0, invoice_number="INV-2026-002")

        svc = VendorStatementRecon("default")
        result = svc.reconcile("Acme", [
            {"reference": "2026-002", "amount": 500.0, "date": "2026-03-20"},
        ])

        assert result["summary"]["matched_count"] == 1
        assert result["matched"][0]["match_type"] == "reference_partial"

    def test_normalized_reference_match(self, db):
        _create_ap_item(db, "r3", "Acme", 750.0, invoice_number="INV/003")

        svc = VendorStatementRecon("default")
        result = svc.reconcile("Acme", [
            {"reference": "INV-003", "amount": 750.0, "date": "2026-03-25"},
        ])

        # INV/003 normalizes to inv003, INV-003 normalizes to inv003
        assert result["summary"]["matched_count"] == 1


class TestAmountDateMatching:
    def test_amount_and_date_match(self, db):
        _create_ap_item(db, "ad1", "Beta", 2000.0, invoice_date="2026-03-10")

        svc = VendorStatementRecon("default")
        result = svc.reconcile("Beta", [
            {"reference": "", "amount": 2000.0, "date": "2026-03-12"},  # 2 days apart
        ])

        assert result["summary"]["matched_count"] == 1
        assert result["matched"][0]["match_type"] == "amount_date"

    def test_amount_only_match(self, db):
        _create_ap_item(db, "ao1", "Gamma", 3333.33)

        svc = VendorStatementRecon("default")
        result = svc.reconcile("Gamma", [
            {"reference": "", "amount": 3333.33, "date": ""},
        ])

        assert result["summary"]["matched_count"] == 1
        assert result["matched"][0]["match_type"] == "amount_only"


# ---------------------------------------------------------------------------
# Unmatched / discrepancy tests
# ---------------------------------------------------------------------------

class TestUnmatched:
    def test_unmatched_on_statement(self, db):
        # No AP items for this vendor
        svc = VendorStatementRecon("default")
        result = svc.reconcile("NoItems", [
            {"reference": "INV-X", "amount": 500.0, "date": "2026-03-01"},
        ])

        assert result["summary"]["unmatched_on_statement"] == 1
        assert result["summary"]["matched_count"] == 0

    def test_unmatched_in_clearledgr(self, db):
        _create_ap_item(db, "u1", "Delta", 1000.0, invoice_number="INV-100")
        _create_ap_item(db, "u2", "Delta", 2000.0, invoice_number="INV-200")

        svc = VendorStatementRecon("default")
        # Statement only has one of the two invoices
        result = svc.reconcile("Delta", [
            {"reference": "INV-100", "amount": 1000.0, "date": "2026-03-01"},
        ])

        assert result["summary"]["matched_count"] == 1
        assert result["summary"]["unmatched_in_clearledgr"] == 1
        assert result["unmatched_in_clearledgr"][0]["invoice_number"] == "INV-200"

    def test_amount_discrepancy(self, db):
        _create_ap_item(db, "disc1", "Epsilon", 1000.0, invoice_number="INV-D1")

        svc = VendorStatementRecon("default")
        result = svc.reconcile("Epsilon", [
            {"reference": "INV-D1", "amount": 1050.0, "date": "2026-03-01"},  # $50 difference
        ])

        assert result["summary"]["discrepancy_count"] == 1
        assert result["discrepancies"][0]["amount_difference"] == 50.0


# ---------------------------------------------------------------------------
# Summary tests
# ---------------------------------------------------------------------------

class TestReconSummary:
    def test_summary_totals(self, db):
        _create_ap_item(db, "s1", "Zeta", 1000.0, invoice_number="Z-001")
        _create_ap_item(db, "s2", "Zeta", 2000.0, invoice_number="Z-002")

        svc = VendorStatementRecon("default")
        result = svc.reconcile("Zeta", [
            {"reference": "Z-001", "amount": 1000.0, "date": "2026-03-01"},
            {"reference": "Z-002", "amount": 2000.0, "date": "2026-03-15"},
            {"reference": "Z-003", "amount": 500.0, "date": "2026-03-20"},  # not in Clearledgr
        ])

        s = result["summary"]
        assert s["statement_line_count"] == 3
        assert s["ap_item_count"] == 2
        assert s["matched_count"] == 2
        assert s["unmatched_on_statement"] == 1
        assert s["statement_total"] == 3500.0
        assert s["clearledgr_total"] == 3000.0
        assert s["difference"] == 500.0
        assert s["match_rate_pct"] == pytest.approx(66.7, abs=0.1)

    def test_empty_statement(self, db):
        svc = VendorStatementRecon("default")
        result = svc.reconcile("Nobody", [])
        assert result["summary"]["match_rate_pct"] == 0.0


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestReconEndpoint:
    @pytest.fixture()
    def client(self, db):
        from main import app
        from clearledgr.api import workspace_shell as ws_module

        def _fake_user():
            return TokenData(
                user_id="rc-user",
                email="rc@test.com",
                organization_id="default",
                role="owner",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        app.dependency_overrides[ws_module.get_current_user] = _fake_user
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(ws_module.get_current_user, None)

    def test_reconcile_endpoint(self, client, db):
        _create_ap_item(db, "api-r1", "Acme", 1000.0, invoice_number="INV-A1")

        resp = client.post(
            "/api/workspace/vendor-intelligence/reconcile-statement",
            json={
                "vendor_name": "Acme",
                "statement_items": [
                    {"reference": "INV-A1", "amount": 1000.0, "date": "2026-03-15"},
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["matched_count"] == 1

    def test_missing_fields_returns_400(self, client, db):
        resp = client.post(
            "/api/workspace/vendor-intelligence/reconcile-statement",
            json={"vendor_name": "Acme"},
        )
        assert resp.status_code == 400
