"""Tests for vendor deduplication service.

Covers:
- Duplicate detection with fuzzy matching
- Merge: aliases consolidated, AP items reassigned, duplicate profiles deleted
- Alias add/remove
- Canonical name resolution from alias
- No duplicates returns empty
- API endpoints (detect, merge, alias management)
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
from clearledgr.services.vendor_dedup import VendorDedupService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


def _create_vendor(db, name, invoice_count=0, aliases=None):
    db.upsert_vendor_profile(
        "default", name,
        invoice_count=invoice_count,
        vendor_aliases=aliases or [],
    )


def _create_ap_item(db, item_id, vendor):
    db.create_ap_item({
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"t-{item_id}",
        "message_id": f"m-{item_id}",
        "subject": f"Invoice from {vendor}",
        "sender": "v@test.com",
        "vendor_name": vendor,
        "amount": 100.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "validated",
        "organization_id": "default",
    })


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_detects_similar_names(self, db):
        _create_vendor(db, "Acme Corp", invoice_count=10)
        _create_vendor(db, "Acme Corporation", invoice_count=3)
        _create_vendor(db, "ACME", invoice_count=1)

        svc = VendorDedupService("default")
        clusters = svc.detect_duplicates(threshold=0.7)

        assert len(clusters) == 1
        assert clusters[0]["canonical"]["vendor_name"] == "Acme Corp"  # most invoices
        dup_names = {d["vendor_name"] for d in clusters[0]["duplicates"]}
        assert "Acme Corporation" in dup_names or "ACME" in dup_names

    def test_no_duplicates_returns_empty(self, db):
        _create_vendor(db, "Alpha Inc", invoice_count=5)
        _create_vendor(db, "Beta LLC", invoice_count=3)
        _create_vendor(db, "Gamma GmbH", invoice_count=2)

        svc = VendorDedupService("default")
        clusters = svc.detect_duplicates(threshold=0.9)
        assert clusters == []

    def test_empty_org_returns_empty(self, db):
        svc = VendorDedupService("default")
        assert svc.detect_duplicates() == []

    def test_high_threshold_reduces_matches(self, db):
        _create_vendor(db, "Stripe Inc", invoice_count=5)
        _create_vendor(db, "Stripe Payments", invoice_count=2)

        svc = VendorDedupService("default")
        # At 0.99 threshold, these shouldn't match
        clusters = svc.detect_duplicates(threshold=0.99)
        assert len(clusters) == 0


# ---------------------------------------------------------------------------
# Merge tests
# ---------------------------------------------------------------------------

class TestMergeVendors:
    def test_merge_consolidates_aliases(self, db):
        _create_vendor(db, "Acme Corp", invoice_count=10)
        _create_vendor(db, "ACME", invoice_count=2, aliases=["acme.com"])

        svc = VendorDedupService("default")
        result = svc.merge_vendors("Acme Corp", ["ACME"])

        assert result["merged_count"] == 1
        assert "ACME" in result["aliases"]
        assert "acme.com" in result["aliases"]

        # Verify duplicate profile is deleted
        dup = db.get_vendor_profile("default", "ACME")
        assert dup is None

        # Verify canonical still exists with aliases
        canonical = db.get_vendor_profile("default", "Acme Corp")
        assert canonical is not None

    def test_merge_reassigns_ap_items(self, db):
        _create_vendor(db, "Acme Corp", invoice_count=5)
        _create_vendor(db, "ACME", invoice_count=2)
        _create_ap_item(db, "ap-dup-1", "ACME")
        _create_ap_item(db, "ap-dup-2", "ACME")

        svc = VendorDedupService("default")
        result = svc.merge_vendors("Acme Corp", ["ACME"])

        assert result["reassigned_items"] == 2

        # Verify AP items now point to canonical
        item = db.get_ap_item("ap-dup-1")
        assert item["vendor_name"] == "Acme Corp"

    def test_merge_no_duplicates_provided(self, db):
        result = VendorDedupService("default").merge_vendors("Acme", [])
        assert result["merged"] == 0

    def test_merge_multiple_duplicates(self, db):
        _create_vendor(db, "Stripe", invoice_count=20)
        _create_vendor(db, "Stripe Inc", invoice_count=5)
        _create_vendor(db, "STRIPE INC.", invoice_count=3)
        _create_vendor(db, "Stripe.com", invoice_count=1)

        svc = VendorDedupService("default")
        result = svc.merge_vendors("Stripe", ["Stripe Inc", "STRIPE INC.", "Stripe.com"])

        assert result["merged_count"] == 3
        assert len(result["aliases"]) == 3


# ---------------------------------------------------------------------------
# Alias tests
# ---------------------------------------------------------------------------

class TestAliasManagement:
    def test_add_alias(self, db):
        _create_vendor(db, "Acme Corp")

        svc = VendorDedupService("default")
        result = svc.add_alias("Acme Corp", "ACME")

        assert "ACME" in result["aliases"]

    def test_add_duplicate_alias_is_idempotent(self, db):
        _create_vendor(db, "Acme Corp")

        svc = VendorDedupService("default")
        svc.add_alias("Acme Corp", "ACME")
        result = svc.add_alias("Acme Corp", "ACME")

        assert result["aliases"].count("ACME") == 1

    def test_remove_alias(self, db):
        _create_vendor(db, "Acme Corp", aliases=["ACME", "Acme"])

        svc = VendorDedupService("default")
        result = svc.remove_alias("Acme Corp", "ACME")

        assert "ACME" not in result["aliases"]
        assert "Acme" in result["aliases"]

    def test_alias_on_nonexistent_vendor(self, db):
        svc = VendorDedupService("default")
        result = svc.add_alias("Nonexistent", "alias")
        assert result.get("error") == "vendor_not_found"


# ---------------------------------------------------------------------------
# Name resolution tests
# ---------------------------------------------------------------------------

class TestResolveVendorName:
    def test_resolves_alias_to_canonical(self, db):
        _create_vendor(db, "Acme Corp", aliases=["ACME", "Acme Corporation"])

        svc = VendorDedupService("default")
        assert svc.resolve_vendor_name("ACME") == "Acme Corp"
        assert svc.resolve_vendor_name("Acme Corporation") == "Acme Corp"

    def test_returns_raw_if_no_match(self, db):
        svc = VendorDedupService("default")
        assert svc.resolve_vendor_name("Unknown Vendor") == "Unknown Vendor"

    def test_exact_match_takes_priority(self, db):
        _create_vendor(db, "Stripe", aliases=[])
        _create_vendor(db, "Stripe Inc", aliases=["Stripe"])  # alias collision

        svc = VendorDedupService("default")
        # "Stripe" matches the canonical name directly
        assert svc.resolve_vendor_name("Stripe") == "Stripe"


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestDedupEndpoints:
    @pytest.fixture()
    def client(self, db):
        from main import app
        from clearledgr.api import workspace_shell as ws_module

        def _fake_user():
            return TokenData(
                user_id="dedup-user",
                email="dedup@test.com",
                organization_id="default",
                role="owner",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        app.dependency_overrides[ws_module.get_current_user] = _fake_user
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(ws_module.get_current_user, None)

    def test_detect_duplicates_endpoint(self, client, db):
        _create_vendor(db, "Acme Corp", invoice_count=10)
        _create_vendor(db, "Acme Corporation", invoice_count=2)

        resp = client.get("/api/workspace/vendor-intelligence/duplicates")
        assert resp.status_code == 200
        data = resp.json()
        assert "clusters" in data
        assert data["cluster_count"] >= 1

    def test_merge_endpoint(self, client, db):
        _create_vendor(db, "Acme Corp", invoice_count=10)
        _create_vendor(db, "ACME", invoice_count=2)

        resp = client.post(
            "/api/workspace/vendor-intelligence/merge",
            json={"canonical": "Acme Corp", "duplicates": ["ACME"]},
        )
        assert resp.status_code == 200
        assert resp.json()["merged_count"] == 1

    def test_add_alias_endpoint(self, client, db):
        _create_vendor(db, "Beta LLC")

        resp = client.post(
            "/api/workspace/vendor-intelligence/profiles/Beta LLC/aliases",
            json={"alias": "Beta"},
        )
        assert resp.status_code == 200
        assert "Beta" in resp.json()["aliases"]

    def test_detect_empty_org(self, client, db):
        resp = client.get("/api/workspace/vendor-intelligence/duplicates")
        assert resp.status_code == 200
        assert resp.json()["cluster_count"] == 0
