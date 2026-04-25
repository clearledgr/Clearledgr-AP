"""Tests for tax compliance service (Europe/Africa).

Covers:
- VAT number validation (EU, UK, Nigeria, Kenya, Ghana, South Africa)
- Reverse charge detection (intra-EU B2B)
- WHT rate lookup
- VAT rate lookup
- Vendor payment totals
- Tax summary report
- API endpoints
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
from clearledgr.services.tax_compliance import (
    validate_tax_id,
    detect_reverse_charge,
    get_vat_rate,
    get_wht_rate,
    TaxComplianceService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


def _create_posted_item(db, item_id, vendor, amount, currency="USD"):
    now = datetime.now(timezone.utc)
    created = (now - timedelta(days=30)).isoformat()
    db.create_ap_item({
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"t-{item_id}",
        "message_id": f"m-{item_id}",
        "subject": f"Invoice from {vendor}",
        "sender": "v@test.com",
        "vendor_name": vendor,
        "amount": amount,
        "currency": currency,
        "invoice_number": f"INV-{item_id}",
        "state": "posted_to_erp",
        "organization_id": "default",
    })
    # Backdate to this year
    sql = "UPDATE ap_items SET created_at = %s WHERE id = %s"
    with db.connect() as conn:
        conn.cursor().execute(sql, (created, item_id))
        conn.commit()


# ---------------------------------------------------------------------------
# VAT validation tests
# ---------------------------------------------------------------------------

class TestVATValidation:
    def test_valid_german_vat(self):
        result = validate_tax_id("DE123456789")
        assert result["valid"] is True
        assert result["country"] == "DE"

    def test_valid_uk_vat(self):
        result = validate_tax_id("GB123456789")
        assert result["valid"] is True
        assert result["country"] == "GB"

    def test_valid_french_vat(self):
        result = validate_tax_id("FR12345678901")
        assert result["valid"] is True
        assert result["country"] == "FR"

    def test_valid_dutch_vat(self):
        result = validate_tax_id("NL123456789B01")
        assert result["valid"] is True
        assert result["country"] == "NL"

    def test_valid_nigeria_tin(self):
        result = validate_tax_id("12345678-1234", "NG")
        assert result["valid"] is True
        assert result["country"] == "NG"

    def test_valid_kenya_pin(self):
        result = validate_tax_id("A123456789B", "KE")
        assert result["valid"] is True
        assert result["country"] == "KE"

    def test_invalid_format(self):
        result = validate_tax_id("INVALID123")
        assert result["valid"] is False

    def test_empty_tax_id(self):
        result = validate_tax_id("")
        assert result["valid"] is False
        assert result["reason"] == "empty"

    def test_auto_detect_country(self):
        result = validate_tax_id("DE123456789")
        assert result["country"] == "DE"

    def test_strips_whitespace_and_dashes(self):
        result = validate_tax_id("DE 123 456 789")
        assert result["valid"] is True

    def test_valid_south_africa(self):
        result = validate_tax_id("1234567890", "ZA")
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# Reverse charge tests
# ---------------------------------------------------------------------------

class TestReverseCharge:
    def test_intra_eu_b2b(self):
        result = detect_reverse_charge("DE", "FR", seller_has_vat=True)
        assert result["reverse_charge"] is True
        assert result["reason"] == "intra_eu_b2b"

    def test_same_country_no_reverse_charge(self):
        result = detect_reverse_charge("DE", "DE")
        assert result["reverse_charge"] is False

    def test_non_eu_no_reverse_charge(self):
        result = detect_reverse_charge("NG", "KE")
        assert result["reverse_charge"] is False

    def test_eu_to_non_eu_no_reverse_charge(self):
        result = detect_reverse_charge("DE", "NG")
        assert result["reverse_charge"] is False


# ---------------------------------------------------------------------------
# Rate lookup tests
# ---------------------------------------------------------------------------

class TestRateLookups:
    def test_vat_rate_germany(self):
        assert get_vat_rate("DE") == 19.0

    def test_vat_rate_nigeria(self):
        assert get_vat_rate("NG") == 7.5

    def test_vat_rate_uk(self):
        assert get_vat_rate("GB") == 20.0

    def test_vat_rate_unknown(self):
        assert get_vat_rate("XX") is None

    def test_wht_rate_nigeria(self):
        assert get_wht_rate("NG") == 10.0

    def test_wht_rate_kenya(self):
        assert get_wht_rate("KE") == 5.0

    def test_wht_rate_no_wht_country(self):
        assert get_wht_rate("DE") is None


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------

class TestTaxComplianceService:
    def test_vendor_payment_totals(self, db):
        _create_posted_item(db, "t1", "Acme Corp", 5000.0)
        _create_posted_item(db, "t2", "Acme Corp", 3000.0)
        _create_posted_item(db, "t3", "Beta LLC", 2000.0)

        svc = TaxComplianceService("default")
        now = datetime.now(timezone.utc)
        totals = svc.get_vendor_payment_totals(
            f"{now.year}-01-01", f"{now.year + 1}-01-01",
        )

        assert len(totals) >= 2
        acme = next((v for v in totals if v["vendor_name"] == "Acme Corp"), None)
        assert acme is not None
        assert acme["total_paid"] == 8000.0
        assert acme["invoice_count"] == 2

    def test_tax_summary(self, db):
        _create_posted_item(db, "ts1", "Vendor X", 10000.0)

        svc = TaxComplianceService("default")
        summary = svc.generate_tax_summary(year=datetime.now(timezone.utc).year)

        assert summary["vendor_count"] >= 1
        assert "vendor_totals" in summary
        assert "vendors_missing_tax_id" in summary

    def test_empty_org(self, db):
        svc = TaxComplianceService("empty-org")
        summary = svc.generate_tax_summary()
        assert summary["vendor_count"] == 0


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestTaxComplianceEndpoints:
    @pytest.fixture()
    def client(self, db):
        from main import app
        from clearledgr.api import workspace_shell as ws_module

        def _fake_user():
            return TokenData(
                user_id="tax-user",
                email="tax@test.com",
                organization_id="default",
                role="owner",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        app.dependency_overrides[ws_module.get_current_user] = _fake_user
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(ws_module.get_current_user, None)

    def test_summary_endpoint(self, client, db):
        _create_posted_item(db, "api-t1", "Test Vendor", 5000.0)
        resp = client.get("/api/workspace/tax-compliance/summary?buyer_country=NG")
        assert resp.status_code == 200
        data = resp.json()
        assert "vendor_totals" in data
        assert data["buyer_country"] == "NG"

    def test_validate_endpoint(self, client, db):
        resp = client.post(
            "/api/workspace/tax-compliance/validate-tax-id",
            json={"tax_id": "DE123456789"},
        )
        assert resp.status_code == 200
        assert resp.json()["valid"] is True
        assert resp.json()["country"] == "DE"

    def test_validate_missing_tax_id(self, client, db):
        resp = client.post(
            "/api/workspace/tax-compliance/validate-tax-id",
            json={},
        )
        assert resp.status_code == 400
