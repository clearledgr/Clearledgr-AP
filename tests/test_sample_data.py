"""Tests for Module 10 sample data mode (§320, §329).

Pinned by these tests:

  - load_sample_data inserts a curated set tagged is_sample=true.
    Idempotent: re-running returns the existing count.
  - clear_sample_data deletes only is_sample=true rows; production
    rows untouched.
  - Production reads (worklist via list_ap_items_by_org;
    workspace_reports.* aggregates) exclude is_sample=true rows by
    default — the contamination guarantee from spec §329 is
    enforced at the SQL level, not the application layer.
  - Sample preview endpoint returns the synthetic rows so the
    leader can browse them.
  - Cross-tenant isolation: orgB clearing does not affect orgA.
  - API endpoints: load / clear / status / preview round-trip
    correctly with admin auth.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import sample_data as sample_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services import sample_data as sample_svc  # noqa: E402
from clearledgr.services import workspace_reports  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta Co")
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=f"leader@{org}.com",
        email=f"leader@{org}.com",
        organization_id=org,
        role="owner",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(sample_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


@pytest.fixture()
def client_orgB(db):
    app = FastAPI()
    app.include_router(sample_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgB")
    return TestClient(app)


# ─── Tests: Loader ──────────────────────────────────────────────────


class TestLoader:
    def test_load_creates_curated_set(self, db):
        result = sample_svc.load_sample_data(db, "orgA")
        assert result["loaded"] == 10
        assert result["already_present"] == 0
        # Every loaded row should have is_sample=true.
        count = sample_svc.count_sample_data(db, "orgA")
        assert count == 10

    def test_load_is_idempotent(self, db):
        sample_svc.load_sample_data(db, "orgA")
        again = sample_svc.load_sample_data(db, "orgA")
        assert again["loaded"] == 0
        assert again["already_present"] == 10

    def test_loaded_rows_have_recognisable_vendor_prefix(self, db):
        sample_svc.load_sample_data(db, "orgA")
        items = sample_svc.list_sample_items(db, "orgA")
        assert all(
            "SAMPLE" in (item.get("vendor_name") or "")
            for item in items
        )

    def test_loaded_rows_span_exception_states(self, db):
        sample_svc.load_sample_data(db, "orgA")
        items = sample_svc.list_sample_items(db, "orgA")
        states = {item.get("state") for item in items}
        # We seed at least one of each: closed (auto-approved),
        # needs_approval, needs_info, failed_post.
        assert "closed" in states
        assert "needs_approval" in states
        assert "needs_info" in states
        assert "failed_post" in states


# ─── Tests: Clear ───────────────────────────────────────────────────


class TestClear:
    def test_clear_removes_only_sample_rows(self, db):
        # Seed a production row (no is_sample flag) and the sample set.
        db.create_ap_item({
            "id": "prod-1",
            "organization_id": "orgA",
            "vendor_name": "Real Vendor",
            "amount": 1500.0,
            "currency": "USD",
            "invoice_number": "INV-PROD-1",
            "state": "received",
        })
        sample_svc.load_sample_data(db, "orgA")

        result = sample_svc.clear_sample_data(db, "orgA")
        assert result["deleted"] == 10
        # Production row survives.
        prod = db.get_ap_item("prod-1")
        assert prod is not None
        assert prod.get("is_sample") in (False, 0)

    def test_clear_is_org_scoped(self, db):
        sample_svc.load_sample_data(db, "orgA")
        sample_svc.load_sample_data(db, "orgB")
        # orgA clears — orgB's sample data is untouched.
        sample_svc.clear_sample_data(db, "orgA")
        assert sample_svc.count_sample_data(db, "orgA") == 0
        assert sample_svc.count_sample_data(db, "orgB") == 10

    def test_clear_on_empty_is_noop(self, db):
        result = sample_svc.clear_sample_data(db, "orgA")
        assert result["deleted"] == 0


# ─── Tests: Production-read isolation ──────────────────────────────


class TestProductionReadFilter:
    def test_volume_report_excludes_sample_rows(self, db):
        # Real production row + sample row in the same org.
        db.create_ap_item({
            "id": "prod-vol-1",
            "organization_id": "orgVR",
            "vendor_name": "Real Vendor",
            "amount": 1000.0,
            "currency": "USD",
            "invoice_number": "INV-PROD-VOL-1",
            "state": "closed",
        })
        db.ensure_organization("orgVR", organization_name="Volume Read Test")
        sample_svc.load_sample_data(db, "orgVR")

        # Re-read the production row to confirm it exists.
        prod = db.get_ap_item("prod-vol-1")
        assert prod is not None

        report = workspace_reports.generate_volume_report("orgVR")
        # Only the 1 production invoice should count toward total_invoices.
        assert report["summary"]["total_invoices"] == 1
        # Vendor breakdown should be the real vendor only.
        names = {b.get("vendor_name") for b in report["breakdown"]}
        assert "Real Vendor" in names
        assert all(
            "SAMPLE" not in (n or "") for n in names
        )

    def test_exception_breakdown_excludes_sample_rows(self, db):
        db.ensure_organization("orgEB", organization_name="Excl BD")
        # Real exception
        db.create_ap_item({
            "id": "prod-eb-1",
            "organization_id": "orgEB",
            "vendor_name": "Real Vendor",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": "INV-PROD-EB-1",
            "state": "needs_info",
            "exception_code": "real_problem",
        })
        sample_svc.load_sample_data(db, "orgEB")

        report = workspace_reports.generate_exception_breakdown_report("orgEB")
        codes = {b["exception_code"] for b in report["breakdown"]}
        assert "real_problem" in codes
        assert "vendor_not_in_erp_master" not in codes
        assert "po_required_missing" not in codes


# ─── Tests: API ─────────────────────────────────────────────────────


class TestAPI:
    def test_status_before_load_is_zero(self, client_orgA):
        body = client_orgA.get(
            "/api/workspace/onboarding/sample-data/status"
        ).json()
        assert body["sample_count"] == 0
        assert body["loaded"] is False

    def test_load_round_trip(self, client_orgA):
        loaded = client_orgA.post(
            "/api/workspace/onboarding/sample-data/load"
        ).json()
        assert loaded["loaded"] == 10

        status = client_orgA.get(
            "/api/workspace/onboarding/sample-data/status"
        ).json()
        assert status["sample_count"] == 10
        assert status["loaded"] is True

    def test_load_idempotent_via_api(self, client_orgA):
        client_orgA.post("/api/workspace/onboarding/sample-data/load")
        again = client_orgA.post(
            "/api/workspace/onboarding/sample-data/load"
        ).json()
        assert again["loaded"] == 0
        assert again["already_present"] == 10

    def test_preview_returns_loaded_items(self, client_orgA):
        client_orgA.post("/api/workspace/onboarding/sample-data/load")
        body = client_orgA.get(
            "/api/workspace/onboarding/sample-data/preview"
        ).json()
        assert body["count"] == 10
        assert all(
            "SAMPLE" in item["vendor_name"] for item in body["items"]
        )

    def test_clear_via_api(self, client_orgA):
        client_orgA.post("/api/workspace/onboarding/sample-data/load")
        cleared = client_orgA.post(
            "/api/workspace/onboarding/sample-data/clear"
        ).json()
        assert cleared["deleted"] == 10

        status = client_orgA.get(
            "/api/workspace/onboarding/sample-data/status"
        ).json()
        assert status["sample_count"] == 0

    def test_cross_tenant_isolation_via_api(
        self, db, client_orgA, client_orgB,
    ):
        client_orgA.post("/api/workspace/onboarding/sample-data/load")
        # orgB should see zero — different tenant.
        b_status = client_orgB.get(
            "/api/workspace/onboarding/sample-data/status"
        ).json()
        assert b_status["sample_count"] == 0
        # orgB cannot clear orgA's sample data via the clear endpoint.
        # (It only operates on orgB's slice, leaves orgA alone.)
        client_orgB.post("/api/workspace/onboarding/sample-data/clear")
        a_status = client_orgA.get(
            "/api/workspace/onboarding/sample-data/status"
        ).json()
        assert a_status["sample_count"] == 10
