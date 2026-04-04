"""Tests for vendor master data sync from ERP to Clearledgr profiles.

Covers:
- New vendor creation in profiles
- Existing vendor update (payment terms, metadata)
- Deactivation detection
- Reactivation detection
- Payment terms change detection
- No ERP connection returns empty summary
- No vendors from ERP returns empty summary
- Sync summary structure
- Background agent wiring
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.services.vendor_erp_sync import sync_vendors_from_erp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "vendor-sync.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    inst = db_module.get_db()
    inst.initialize()
    inst.create_organization("sync-org", "Sync Org", settings={})
    return inst


def _mock_erp_vendors(*vendors):
    """Build a list of normalized ERP vendor dicts."""
    result = []
    for v in vendors:
        result.append({
            "vendor_id": v.get("vendor_id", "V1"),
            "name": v["name"],
            "email": v.get("email", ""),
            "phone": v.get("phone", ""),
            "tax_id": v.get("tax_id", ""),
            "currency": v.get("currency", "USD"),
            "active": v.get("active", True),
            "address": v.get("address", ""),
            "payment_terms": v.get("payment_terms", ""),
            "balance": v.get("balance", 0.0),
        })
    return result


# ---------------------------------------------------------------------------
# Core sync tests
# ---------------------------------------------------------------------------

class TestVendorERPSync:
    def _run_sync(self, erp_vendors, erp_type="quickbooks"):
        from clearledgr.integrations.erp_router import ERPConnection
        conn = ERPConnection(type=erp_type, access_token="tok", realm_id="r1")

        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=conn):
            with patch("clearledgr.integrations.erp_router.list_all_vendors", new_callable=AsyncMock, return_value=erp_vendors):
                return asyncio.run(sync_vendors_from_erp("sync-org"))

    def test_new_vendors_created(self, db):
        vendors = _mock_erp_vendors(
            {"name": "Acme Corp", "vendor_id": "QB-100", "payment_terms": "Net 30"},
            {"name": "Beta LLC", "vendor_id": "QB-200"},
        )
        summary = self._run_sync(vendors)

        assert summary["synced_count"] == 2
        assert summary["new_vendor_count"] == 2
        assert "Acme Corp" in summary["new_vendors"]
        assert "Beta LLC" in summary["new_vendors"]

        # Verify profiles were created
        profiles = db.get_vendor_profiles_bulk("sync-org", ["Acme Corp", "Beta LLC"])
        assert "Acme Corp" in profiles
        assert profiles["Acme Corp"]["payment_terms"] == "Net 30"
        meta = profiles["Acme Corp"].get("metadata") or {}
        assert meta["erp_vendor_id"] == "QB-100"
        assert meta["erp_type"] == "quickbooks"

    def test_existing_vendor_updated(self, db):
        # Pre-create a profile
        db.upsert_vendor_profile("sync-org", "Acme Corp", payment_terms="Net 15")

        vendors = _mock_erp_vendors(
            {"name": "Acme Corp", "vendor_id": "QB-100", "payment_terms": "Net 30"},
        )
        summary = self._run_sync(vendors)

        assert summary["synced_count"] == 1
        assert summary["new_vendor_count"] == 0  # not new
        assert summary["terms_changed_count"] == 1
        assert summary["terms_changed"][0]["old_terms"] == "Net 15"
        assert summary["terms_changed"][0]["new_terms"] == "Net 30"

        # Verify updated
        profiles = db.get_vendor_profiles_bulk("sync-org", ["Acme Corp"])
        assert profiles["Acme Corp"]["payment_terms"] == "Net 30"

    def test_deactivation_detected(self, db):
        # Pre-create an active vendor
        db.upsert_vendor_profile("sync-org", "Old Vendor", metadata={
            "erp_vendor_id": "QB-300",
            "erp_active": True,
        })

        vendors = _mock_erp_vendors(
            {"name": "Old Vendor", "vendor_id": "QB-300", "active": False},
        )
        summary = self._run_sync(vendors)

        assert summary["deactivated_count"] == 1
        assert "Old Vendor" in summary["deactivated_vendors"]

    def test_reactivation_detected(self, db):
        # Pre-create an inactive vendor
        db.upsert_vendor_profile("sync-org", "Dormant Vendor", metadata={
            "erp_vendor_id": "QB-400",
            "erp_active": False,
        })

        vendors = _mock_erp_vendors(
            {"name": "Dormant Vendor", "vendor_id": "QB-400", "active": True},
        )
        summary = self._run_sync(vendors)

        assert summary["reactivated_count"] == 1
        assert "Dormant Vendor" in summary["reactivated_vendors"]

    def test_no_erp_connection(self, db):
        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=None):
            summary = asyncio.run(sync_vendors_from_erp("sync-org"))

        assert summary["synced_count"] == 0
        assert summary["reason"] == "no_erp_connection"

    def test_no_vendors_from_erp(self, db):
        from clearledgr.integrations.erp_router import ERPConnection
        conn = ERPConnection(type="xero", access_token="tok", tenant_id="t1")

        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=conn):
            with patch("clearledgr.integrations.erp_router.list_all_vendors", new_callable=AsyncMock, return_value=[]):
                summary = asyncio.run(sync_vendors_from_erp("sync-org"))

        assert summary["synced_count"] == 0
        assert summary["reason"] == "no_vendors_from_erp"

    def test_summary_structure(self, db):
        vendors = _mock_erp_vendors({"name": "Test Corp"})
        summary = self._run_sync(vendors)

        required_keys = {
            "organization_id", "erp_type", "started_at", "completed_at",
            "duration_s", "erp_vendor_count", "synced_count",
            "new_vendors", "new_vendor_count",
            "deactivated_vendors", "deactivated_count",
            "reactivated_vendors", "reactivated_count",
            "terms_changed", "terms_changed_count",
        }
        assert required_keys.issubset(set(summary.keys()))
        assert summary["organization_id"] == "sync-org"
        assert summary["erp_type"] == "quickbooks"

    def test_metadata_preserved_on_update(self, db):
        # Pre-create with custom metadata
        db.upsert_vendor_profile("sync-org", "Acme Corp", metadata={
            "custom_field": "keep_me",
            "erp_vendor_id": "QB-100",
        })

        vendors = _mock_erp_vendors(
            {"name": "Acme Corp", "vendor_id": "QB-100", "email": "new@acme.com"},
        )
        self._run_sync(vendors)

        profiles = db.get_vendor_profiles_bulk("sync-org", ["Acme Corp"])
        meta = profiles["Acme Corp"]["metadata"]
        assert meta["custom_field"] == "keep_me"  # preserved
        assert meta["erp_email"] == "new@acme.com"  # updated

    def test_erp_fields_stored_in_metadata(self, db):
        vendors = _mock_erp_vendors({
            "name": "Full Vendor",
            "vendor_id": "QB-500",
            "email": "full@vendor.com",
            "phone": "555-1234",
            "address": "123 Main St",
            "tax_id": "12-3456789",
            "currency": "EUR",
            "balance": 5000.0,
        })
        self._run_sync(vendors)

        profiles = db.get_vendor_profiles_bulk("sync-org", ["Full Vendor"])
        meta = profiles["Full Vendor"]["metadata"]
        assert meta["erp_vendor_id"] == "QB-500"
        assert meta["erp_email"] == "full@vendor.com"
        assert meta["erp_phone"] == "555-1234"
        assert meta["erp_address"] == "123 Main St"
        assert meta["erp_tax_id"] == "12-3456789"
        assert meta["erp_currency"] == "EUR"
        assert meta["erp_balance"] == 5000.0
        assert "erp_synced_at" in meta

    def test_empty_vendor_names_skipped(self, db):
        vendors = [
            {"vendor_id": "1", "name": "", "active": True, "email": "", "phone": "",
             "tax_id": "", "currency": "", "address": "", "payment_terms": "", "balance": 0},
            {"vendor_id": "2", "name": "Good Vendor", "active": True, "email": "", "phone": "",
             "tax_id": "", "currency": "", "address": "", "payment_terms": "", "balance": 0},
        ]
        summary = self._run_sync(vendors)
        assert summary["synced_count"] == 1


# ---------------------------------------------------------------------------
# Background agent wiring test
# ---------------------------------------------------------------------------

class TestVendorSyncBackgroundWiring:
    def test_sync_function_is_called_in_background(self, db):
        from clearledgr.services.agent_background import _sync_vendor_master_data

        with patch("clearledgr.services.vendor_erp_sync.sync_vendors_from_erp", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = {
                "synced_count": 5,
                "new_vendor_count": 2,
                "new_vendors": ["A", "B"],
                "deactivated_count": 0,
                "deactivated_vendors": [],
                "terms_changed_count": 0,
                "terms_changed": [],
            }
            asyncio.run(_sync_vendor_master_data("sync-org"))
            mock_sync.assert_called_once_with(organization_id="sync-org")
