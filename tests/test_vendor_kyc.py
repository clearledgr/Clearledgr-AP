"""Tests for Phase 2.4 — vendor KYC schema (DESIGN_THESIS.md §3).

Covers:
  - Migration v16 adds the 6 KYC columns (plus audit timestamp)
  - VendorStore typed accessors: get/update, partial merge, field-name
    whitelisting, director_names validation
  - compute_vendor_ytd_spend filters by year + final_state
  - VendorRiskScoreService formula — every component, edge cases,
    clamping, component breakdown
  - iban_verified derivation: True iff bank_details_encrypted + no freeze
  - REST API: GET returns full vendor-intelligence shape + masked bank,
    PUT requires Financial Controller role, partial patch, cross-tenant
    blocked, audit event emitted with field names only
"""
from __future__ import annotations

import asyncio
import importlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from clearledgr.core.database import ClearledgrDB, get_db
    from clearledgr.core import database as db_module

    db = get_db()
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


def _seed_vendor(db, vendor="Acme", **kwargs):
    db.create_organization("org_t", name="X")
    defaults: Dict[str, Any] = {"invoice_count": 5}
    defaults.update(kwargs)
    db.upsert_vendor_profile("org_t", vendor, **defaults)


# ===========================================================================
# Migration v16
# ===========================================================================


class TestMigrationV16:

    def test_kyc_columns_present_after_init(self, tmp_db):
        """Fresh DB initialize should include the Phase 2.4 columns via
        the updated CREATE TABLE statement."""
        with tmp_db.connect() as conn:
            cur = conn.cursor()
            if tmp_db.use_postgres:
                cur.execute(
                    tmp_db._prepare_sql(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = ?"
                    ),
                    ("vendor_profiles",),
                )
                columns = {row[0] for row in cur.fetchall()}
            else:
                cur.execute("PRAGMA table_info(vendor_profiles)")
                columns = {row[1] for row in cur.fetchall()}
        for col in (
            "registration_number",
            "vat_number",
            "registered_address",
            "director_names",
            "kyc_completion_date",
            "vendor_kyc_updated_at",
        ):
            assert col in columns, f"missing column {col}"

    def test_migration_v16_idempotent(self, tmp_db):
        """Re-running v16 on an already-initialized DB is a no-op."""
        from clearledgr.core.migrations import _MIGRATIONS
        m16 = next(m for m in _MIGRATIONS if m[0] == 16)
        with tmp_db.connect() as conn:
            # PG: run in autocommit so the try/except ALTER TABLE
            # pattern inside the migration doesn't poison the txn when
            # columns already exist on re-run.
            if tmp_db.use_postgres:
                conn.autocommit = True
            cur = conn.cursor()
            m16[2](cur, tmp_db)  # should not raise
            if not tmp_db.use_postgres:
                conn.commit()


# ===========================================================================
# VendorStore KYC accessors
# ===========================================================================


class TestVendorStoreKycAccessors:

    def test_get_empty_kyc(self, tmp_db):
        _seed_vendor(tmp_db)
        kyc = tmp_db.get_vendor_kyc("org_t", "Acme")
        assert kyc == {
            "registration_number": None,
            "vat_number": None,
            "registered_address": None,
            "director_names": [],
            "kyc_completion_date": None,
            "vendor_kyc_updated_at": None,
        }

    def test_get_unknown_vendor_returns_none(self, tmp_db):
        tmp_db.create_organization("org_t", name="X")
        assert tmp_db.get_vendor_kyc("org_t", "Ghost") is None

    def test_full_update(self, tmp_db):
        _seed_vendor(tmp_db)
        updated = tmp_db.update_vendor_kyc(
            "org_t", "Acme",
            patch={
                "registration_number": "CH-1234",
                "vat_number": "CH123456789",
                "registered_address": "123 Main St, Zurich",
                "director_names": ["Alice", "Bob"],
                "kyc_completion_date": "2026-03-01",
            },
            actor_id="fc@test",
        )
        assert updated["registration_number"] == "CH-1234"
        assert updated["director_names"] == ["Alice", "Bob"]
        assert updated["kyc_completion_date"] == "2026-03-01"
        assert updated["vendor_kyc_updated_at"] is not None

    def test_partial_patch_merges(self, tmp_db):
        _seed_vendor(tmp_db)
        tmp_db.update_vendor_kyc(
            "org_t", "Acme",
            patch={"registration_number": "CH-1234", "vat_number": "VAT-1"},
            actor_id="fc@test",
        )
        tmp_db.update_vendor_kyc(
            "org_t", "Acme",
            patch={"registered_address": "Zurich"},
            actor_id="fc@test",
        )
        kyc = tmp_db.get_vendor_kyc("org_t", "Acme")
        assert kyc["registration_number"] == "CH-1234"
        assert kyc["vat_number"] == "VAT-1"
        assert kyc["registered_address"] == "Zurich"

    def test_empty_patch_is_no_op(self, tmp_db):
        _seed_vendor(tmp_db)
        assert tmp_db.update_vendor_kyc("org_t", "Acme", patch={}) is None

    def test_unknown_field_rejected(self, tmp_db):
        _seed_vendor(tmp_db)
        # All fields unknown → nothing to write → None
        assert (
            tmp_db.update_vendor_kyc(
                "org_t", "Acme", patch={"nope": "whatever"}
            )
            is None
        )

    def test_unknown_field_dropped_but_known_fields_applied(self, tmp_db):
        _seed_vendor(tmp_db)
        updated = tmp_db.update_vendor_kyc(
            "org_t", "Acme",
            patch={"registration_number": "CH-1", "nope": "whatever"},
        )
        assert updated is not None
        assert updated["registration_number"] == "CH-1"

    def test_director_names_non_list_rejected(self, tmp_db):
        _seed_vendor(tmp_db)
        # String input for director_names → the field is dropped. If
        # it's the only field, the patch is empty after filtering → None.
        result = tmp_db.update_vendor_kyc(
            "org_t", "Acme",
            patch={"director_names": "Alice, Bob"},
        )
        assert result is None

    def test_director_names_list_trims_empty(self, tmp_db):
        _seed_vendor(tmp_db)
        updated = tmp_db.update_vendor_kyc(
            "org_t", "Acme",
            patch={"director_names": ["Alice", "", "Bob", "  "]},
        )
        assert updated["director_names"] == ["Alice", "Bob"]

    def test_director_names_cleared_via_none(self, tmp_db):
        _seed_vendor(tmp_db)
        tmp_db.update_vendor_kyc(
            "org_t", "Acme", patch={"director_names": ["Alice"]}
        )
        updated = tmp_db.update_vendor_kyc(
            "org_t", "Acme", patch={"director_names": None}
        )
        assert updated["director_names"] == []

    def test_update_bumps_kyc_updated_at(self, tmp_db):
        _seed_vendor(tmp_db)
        first = tmp_db.update_vendor_kyc(
            "org_t", "Acme", patch={"registration_number": "CH-1"}
        )
        import time
        time.sleep(0.01)
        second = tmp_db.update_vendor_kyc(
            "org_t", "Acme", patch={"vat_number": "VAT-1"}
        )
        assert second["vendor_kyc_updated_at"] > first["vendor_kyc_updated_at"]

    def test_update_unknown_vendor_returns_none(self, tmp_db):
        tmp_db.create_organization("org_t", name="X")
        assert (
            tmp_db.update_vendor_kyc(
                "org_t", "Ghost", patch={"registration_number": "X"}
            )
            is None
        )


# ===========================================================================
# compute_vendor_ytd_spend
# ===========================================================================


class TestComputeVendorYtdSpend:

    def test_zero_when_no_history(self, tmp_db):
        _seed_vendor(tmp_db)
        assert tmp_db.compute_vendor_ytd_spend("org_t", "Acme") == 0.0

    def test_sums_posted_invoices_in_current_year(self, tmp_db):
        _seed_vendor(tmp_db)
        tmp_db.record_vendor_invoice(
            "org_t", "Acme", "AP-1",
            amount=500.0, final_state="posted_to_erp", was_approved=True,
        )
        tmp_db.record_vendor_invoice(
            "org_t", "Acme", "AP-2",
            amount=300.0, final_state="posted_to_erp", was_approved=True,
        )
        assert tmp_db.compute_vendor_ytd_spend("org_t", "Acme") == 800.0

    def test_ignores_rejected_invoices(self, tmp_db):
        _seed_vendor(tmp_db)
        tmp_db.record_vendor_invoice(
            "org_t", "Acme", "AP-1",
            amount=500.0, final_state="posted_to_erp", was_approved=True,
        )
        tmp_db.record_vendor_invoice(
            "org_t", "Acme", "AP-rejected",
            amount=999.0, final_state="rejected", was_approved=False,
        )
        assert tmp_db.compute_vendor_ytd_spend("org_t", "Acme") == 500.0

    def test_filter_by_year(self, tmp_db):
        _seed_vendor(tmp_db)
        tmp_db.record_vendor_invoice(
            "org_t", "Acme", "AP-1",
            amount=500.0, final_state="posted_to_erp", was_approved=True,
        )
        # The record_vendor_invoice helper stamps created_at to now,
        # which is 2026. Request 2025 → empty.
        assert (
            tmp_db.compute_vendor_ytd_spend("org_t", "Acme", year=2025) == 0.0
        )


# ===========================================================================
# VendorRiskScoreService
# ===========================================================================


class TestVendorRiskScoreService:

    def _svc(self, db):
        from clearledgr.services.vendor_risk import VendorRiskScoreService
        return VendorRiskScoreService("org_t", db=db)

    def test_unknown_vendor(self, tmp_db):
        tmp_db.create_organization("org_t", name="X")
        r = self._svc(tmp_db).compute("Ghost")
        assert r.vendor_found is False
        assert r.score == 0
        assert r.components == []

    def test_new_vendor_no_kyc(self, tmp_db):
        _seed_vendor(tmp_db, invoice_count=0)
        r = self._svc(tmp_db).compute("Acme")
        # new(30) + kyc_missing(15) + miss_reg(5) + miss_vat(5) + miss_dir(5) = 60
        assert r.score == 60
        codes = {c.code for c in r.components}
        assert codes == {
            "new_vendor",
            "kyc_missing",
            "missing_registration_number",
            "missing_vat_number",
            "missing_director_names",
        }

    def test_solid_vendor_zero_risk(self, tmp_db):
        _seed_vendor(
            tmp_db,
            invoice_count=10,
            approval_override_rate=0.1,
            last_invoice_date=(
                datetime.now(timezone.utc) - timedelta(days=3)
            ).isoformat(),
        )
        tmp_db.update_vendor_kyc(
            "org_t", "Acme",
            patch={
                "registration_number": "CH-1",
                "vat_number": "VAT-1",
                "director_names": ["Alice"],
                "kyc_completion_date": (
                    datetime.now(timezone.utc) - timedelta(days=30)
                ).isoformat(),
            },
        )
        r = self._svc(tmp_db).compute("Acme")
        assert r.score == 0
        assert r.components == []

    def test_iban_freeze_active(self, tmp_db):
        _seed_vendor(
            tmp_db,
            invoice_count=10,
            iban_change_pending=1,
            iban_change_detected_at=datetime.now(timezone.utc).isoformat(),
        )
        tmp_db.update_vendor_kyc(
            "org_t", "Acme",
            patch={
                "registration_number": "CH-1",
                "vat_number": "VAT-1",
                "director_names": ["Alice"],
                "kyc_completion_date": datetime.now(timezone.utc).isoformat(),
            },
        )
        r = self._svc(tmp_db).compute("Acme")
        # freeze(50) only
        assert r.score == 50
        assert [c.code for c in r.components] == ["iban_change_freeze_active"]

    def test_recent_bank_change(self, tmp_db):
        _seed_vendor(
            tmp_db,
            invoice_count=10,
            bank_details_changed_at=(
                datetime.now(timezone.utc) - timedelta(days=5)
            ).isoformat(),
        )
        tmp_db.update_vendor_kyc(
            "org_t", "Acme",
            patch={
                "registration_number": "CH-1",
                "vat_number": "VAT-1",
                "director_names": ["Alice"],
                "kyc_completion_date": datetime.now(timezone.utc).isoformat(),
            },
        )
        r = self._svc(tmp_db).compute("Acme")
        assert r.score == 15
        assert [c.code for c in r.components] == ["recent_bank_change"]

    def test_bank_change_outside_window_ignored(self, tmp_db):
        _seed_vendor(
            tmp_db,
            invoice_count=10,
            bank_details_changed_at=(
                datetime.now(timezone.utc) - timedelta(days=60)
            ).isoformat(),
        )
        tmp_db.update_vendor_kyc(
            "org_t", "Acme",
            patch={
                "registration_number": "CH-1",
                "vat_number": "VAT-1",
                "director_names": ["Alice"],
                "kyc_completion_date": datetime.now(timezone.utc).isoformat(),
            },
        )
        r = self._svc(tmp_db).compute("Acme")
        assert r.score == 0

    def test_high_override_rate(self, tmp_db):
        _seed_vendor(tmp_db, invoice_count=10, approval_override_rate=0.45)
        tmp_db.update_vendor_kyc(
            "org_t", "Acme",
            patch={
                "registration_number": "CH-1",
                "vat_number": "VAT-1",
                "director_names": ["Alice"],
                "kyc_completion_date": datetime.now(timezone.utc).isoformat(),
            },
        )
        r = self._svc(tmp_db).compute("Acme")
        assert r.score == 20
        assert [c.code for c in r.components] == ["high_override_rate"]

    def test_stale_kyc(self, tmp_db):
        _seed_vendor(tmp_db, invoice_count=10)
        tmp_db.update_vendor_kyc(
            "org_t", "Acme",
            patch={
                "registration_number": "CH-1",
                "vat_number": "VAT-1",
                "director_names": ["Alice"],
                "kyc_completion_date": (
                    datetime.now(timezone.utc) - timedelta(days=400)
                ).isoformat(),
            },
        )
        r = self._svc(tmp_db).compute("Acme")
        assert r.score == 10
        assert [c.code for c in r.components] == ["kyc_stale"]

    def test_score_clamped_at_100(self, tmp_db):
        _seed_vendor(
            tmp_db,
            invoice_count=0,
            iban_change_pending=1,
            iban_change_detected_at=datetime.now(timezone.utc).isoformat(),
            approval_override_rate=0.5,
            bank_details_changed_at=(
                datetime.now(timezone.utc) - timedelta(days=1)
            ).isoformat(),
        )
        r = self._svc(tmp_db).compute("Acme")
        # new(30) + freeze(50) + recent(15) + override(20) + kyc_missing(15)
        # + miss_reg(5) + miss_vat(5) + miss_dir(5) = 145 → clamped to 100
        assert r.score == 100

    def test_component_breakdown_structure(self, tmp_db):
        _seed_vendor(tmp_db, invoice_count=0)
        r = self._svc(tmp_db).compute("Acme")
        d = r.to_dict()
        assert "score" in d
        assert "components" in d
        assert "computed_at" in d
        assert "vendor_found" in d
        assert isinstance(d["components"], list)
        for comp in d["components"]:
            assert "code" in comp
            assert "label" in comp
            assert "points" in comp


# ===========================================================================
# iban_verified derivation (part of the GET response)
# ===========================================================================


class TestIbanVerifiedDerivation:

    def test_verified_when_bank_details_set_no_freeze(self, tmp_db):
        from clearledgr.api.vendor_kyc import _derive_iban_verification
        _seed_vendor(tmp_db)
        tmp_db.set_vendor_bank_details(
            "org_t", "Acme", {"iban": "GB82WEST12345698765432"}
        )
        profile = tmp_db.get_vendor_profile("org_t", "Acme")
        result = _derive_iban_verification(profile)
        assert result["iban_verified"] is True
        assert result["iban_verified_at"] is not None

    def test_unverified_when_no_bank_details(self, tmp_db):
        from clearledgr.api.vendor_kyc import _derive_iban_verification
        _seed_vendor(tmp_db)
        profile = tmp_db.get_vendor_profile("org_t", "Acme")
        result = _derive_iban_verification(profile)
        assert result["iban_verified"] is False
        assert result["iban_verified_at"] is None

    def test_unverified_when_freeze_active(self, tmp_db):
        from clearledgr.api.vendor_kyc import _derive_iban_verification
        _seed_vendor(tmp_db)
        tmp_db.set_vendor_bank_details(
            "org_t", "Acme", {"iban": "GB82WEST12345698765432"}
        )
        tmp_db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details={"iban": "DE89999999999999999999"},
            sender_domain="evil.com",
        )
        profile = tmp_db.get_vendor_profile("org_t", "Acme")
        result = _derive_iban_verification(profile)
        # Freeze active → NOT verified even though bank_details_encrypted is set
        assert result["iban_verified"] is False


# ===========================================================================
# REST API
# ===========================================================================


class TestVendorKycAPI:

    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.core.database import ClearledgrDB, get_db
        from clearledgr.core import database as db_module
        import main

        db = get_db()
        db.initialize()
        monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
        importlib.reload(main)
        client = TestClient(main.app)
        yield client, main, db

    def _override_user(self, main, role: str, org_id: str = "org_t"):
        from clearledgr.core.auth import (
            TokenData,
            get_current_user,
            require_financial_controller,
        )

        def _user():
            return TokenData(
                user_id="u1",
                email="u1@test",
                organization_id=org_id,
                role=role,
                exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )

        main.app.dependency_overrides[get_current_user] = _user
        main.app.dependency_overrides[require_financial_controller] = _user

    def test_get_404_on_unknown_vendor(self, app_client):
        client, main, db = app_client
        db.create_organization("org_t", name="X")
        self._override_user(main, "ap_clerk")
        try:
            resp = client.get(
                "/api/vendors/Ghost/kyc?organization_id=org_t"
            )
            assert resp.status_code == 404
        finally:
            main.app.dependency_overrides.clear()

    def test_get_returns_full_intelligence_shape(self, app_client):
        client, main, db = app_client
        _seed_vendor(db, invoice_count=10)
        db.set_vendor_bank_details(
            "org_t", "Acme", {"iban": "GB82WEST12345698765432"}
        )
        db.update_vendor_kyc(
            "org_t", "Acme",
            patch={
                "registration_number": "CH-1234",
                "vat_number": "VAT-1",
                "director_names": ["Alice"],
                "kyc_completion_date": datetime.now(timezone.utc).isoformat(),
            },
        )
        db.record_vendor_invoice(
            "org_t", "Acme", "AP-1",
            amount=500.0, final_state="posted_to_erp", was_approved=True,
        )
        self._override_user(main, "ap_clerk")
        try:
            resp = client.get(
                "/api/vendors/Acme/kyc?organization_id=org_t"
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["vendor_name"] == "Acme"
            assert body["kyc"]["registration_number"] == "CH-1234"
            assert body["iban_verified"] is True
            assert body["iban_verified_at"] is not None
            assert body["verified_bank_details_masked"]["iban"].startswith("GB82")
            assert body["ytd_spend"] == 500.0
            assert body["ytd_spend_year"] == datetime.now(timezone.utc).year
            # set_vendor_bank_details bumps bank_details_changed_at to
            # "now", which the risk score flags as recent_bank_change
            # (+15). That's the only component — the vendor is
            # otherwise clean.
            assert body["risk_score"]["score"] == 15
            assert [
                c["code"] for c in body["risk_score"]["components"]
            ] == ["recent_bank_change"]
            # Raw IBAN is never in the response
            assert "GB82WEST12345698765432" not in json.dumps(body)
        finally:
            main.app.dependency_overrides.clear()

    def test_get_any_org_member_can_read(self, app_client):
        client, main, db = app_client
        _seed_vendor(db)
        # Even read_only can GET the KYC view
        self._override_user(main, "read_only")
        try:
            resp = client.get(
                "/api/vendors/Acme/kyc?organization_id=org_t"
            )
            assert resp.status_code == 200
        finally:
            main.app.dependency_overrides.clear()

    def test_put_requires_financial_controller(self, app_client):
        client, main, db = app_client
        _seed_vendor(db)
        # AP Manager should be rejected
        from clearledgr.core.auth import TokenData, get_current_user

        def _ap_manager():
            return TokenData(
                user_id="u1",
                email="u1@test",
                organization_id="org_t",
                role="ap_manager",
                exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )

        main.app.dependency_overrides[get_current_user] = _ap_manager
        try:
            resp = client.put(
                "/api/vendors/Acme/kyc?organization_id=org_t",
                json={"registration_number": "CH-1"},
            )
            assert resp.status_code == 403
            assert resp.json()["detail"] == "financial_controller_role_required"
        finally:
            main.app.dependency_overrides.clear()

    def test_put_happy_path(self, app_client):
        client, main, db = app_client
        _seed_vendor(db)
        self._override_user(main, "financial_controller")
        try:
            resp = client.put(
                "/api/vendors/Acme/kyc?organization_id=org_t",
                json={
                    "registration_number": "CH-9999",
                    "vat_number": "VAT-9999",
                    "director_names": ["Alice", "Bob"],
                    "kyc_completion_date": "2026-04-01",
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "updated"
            assert body["kyc"]["registration_number"] == "CH-9999"
            assert set(body["changed_fields"]) == {
                "registration_number",
                "vat_number",
                "director_names",
                "kyc_completion_date",
            }
        finally:
            main.app.dependency_overrides.clear()

    def test_put_partial_patch_preserves_other_fields(self, app_client):
        client, main, db = app_client
        _seed_vendor(db)
        db.update_vendor_kyc(
            "org_t", "Acme",
            patch={"registration_number": "CH-initial", "vat_number": "VAT-init"},
        )
        self._override_user(main, "financial_controller")
        try:
            resp = client.put(
                "/api/vendors/Acme/kyc?organization_id=org_t",
                json={"registered_address": "Zurich"},
            )
            assert resp.status_code == 200
            body = resp.json()
            # registration_number and vat_number should still be there
            assert body["kyc"]["registration_number"] == "CH-initial"
            assert body["kyc"]["vat_number"] == "VAT-init"
            assert body["kyc"]["registered_address"] == "Zurich"
            assert body["changed_fields"] == ["registered_address"]
        finally:
            main.app.dependency_overrides.clear()

    def test_put_empty_body_rejected(self, app_client):
        client, main, db = app_client
        _seed_vendor(db)
        self._override_user(main, "financial_controller")
        try:
            resp = client.put(
                "/api/vendors/Acme/kyc?organization_id=org_t",
                json={},
            )
            assert resp.status_code == 400
            assert resp.json()["detail"] == "empty_patch"
        finally:
            main.app.dependency_overrides.clear()

    def test_put_unknown_vendor_404(self, app_client):
        client, main, db = app_client
        db.create_organization("org_t", name="X")
        self._override_user(main, "financial_controller")
        try:
            resp = client.put(
                "/api/vendors/Ghost/kyc?organization_id=org_t",
                json={"registration_number": "X"},
            )
            assert resp.status_code == 404
        finally:
            main.app.dependency_overrides.clear()

    def test_cross_tenant_blocked_get(self, app_client):
        client, main, db = app_client
        _seed_vendor(db)
        self._override_user(main, "cfo", org_id="other_org")
        try:
            resp = client.get(
                "/api/vendors/Acme/kyc?organization_id=org_t"
            )
            assert resp.status_code == 403
        finally:
            main.app.dependency_overrides.clear()

    def test_cross_tenant_blocked_put(self, app_client):
        client, main, db = app_client
        _seed_vendor(db)
        self._override_user(main, "financial_controller", org_id="other_org")
        try:
            resp = client.put(
                "/api/vendors/Acme/kyc?organization_id=org_t",
                json={"registration_number": "CH-1"},
            )
            assert resp.status_code == 403
        finally:
            main.app.dependency_overrides.clear()

    def test_put_emits_audit_event_with_field_names_only(self, app_client):
        client, main, db = app_client
        _seed_vendor(db)
        self._override_user(main, "financial_controller")
        try:
            resp = client.put(
                "/api/vendors/Acme/kyc?organization_id=org_t",
                json={
                    "registration_number": "SECRET-REG-1234",
                    "vat_number": "SECRET-VAT-9999",
                },
            )
            assert resp.status_code == 200
            events = db.list_recent_ap_audit_events("org_t", limit=50)
            kyc_events = [
                e for e in events if e.get("event_type") == "vendor_kyc_updated"
            ]
            assert len(kyc_events) == 1
            event = kyc_events[0]
            payload = event.get("payload_json") or {}
            # Field names present
            assert set(payload.get("changed_fields") or []) == {
                "registration_number",
                "vat_number",
            }
            # Values NEVER present in the audit payload
            payload_str = json.dumps(payload)
            assert "SECRET-REG-1234" not in payload_str
            assert "SECRET-VAT-9999" not in payload_str
        finally:
            main.app.dependency_overrides.clear()

    def test_put_legacy_admin_still_works(self, app_client):
        """Legacy "admin" maps to "financial_controller" at token decode
        (Phase 2.3), so it should still pass require_financial_controller."""
        client, main, db = app_client
        _seed_vendor(db)
        from clearledgr.core.auth import TokenData, get_current_user

        def _legacy_admin():
            return TokenData(
                user_id="u1",
                email="u1@test",
                organization_id="org_t",
                role="admin",  # legacy, normalizes to financial_controller
                exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )

        main.app.dependency_overrides[get_current_user] = _legacy_admin
        try:
            resp = client.put(
                "/api/vendors/Acme/kyc?organization_id=org_t",
                json={"registration_number": "CH-LEGACY"},
            )
            assert resp.status_code == 200
        finally:
            main.app.dependency_overrides.clear()
