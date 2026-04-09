"""Tests for Phase 2.1.b — IBAN change freeze + three-factor verification.

DESIGN_THESIS.md §8: IBAN change freeze with three-factor verification
(vendor email domain + phone confirmation + AP Manager sign-off).

Covers:
  - VendorStore freeze accessors (start / record / complete / reject)
  - IbanChangeFreezeService detection + three-factor workflow
  - Validation gate auto-starts the freeze on IBAN mismatch
  - Validation gate blocks every invoice for a frozen vendor
  - REST API: get status, record each factor, complete (requires all
    factors + CFO role), reject, cross-tenant blocked, non-CFO blocked
  - Audit events: every state change records an ap_audit_event
"""
from __future__ import annotations

import asyncio
import importlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest


SAMPLE_ORIGINAL = {
    "iban": "GB82WEST12345698765432",
    "account_number": "12345678",
    "sort_code": "20-00-00",
}
SAMPLE_ADVERSARIAL = {
    "iban": "DE89999999999999999999",
    "account_number": "99999999",
    "sort_code": "30-00-00",
}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from clearledgr.core.database import ClearledgrDB
    from clearledgr.core import database as db_module

    db = ClearledgrDB(db_path=str(tmp_path / "iban_freeze.db"))
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


def _seed_established_vendor(db, vendor: str = "Acme"):
    db.create_organization("org_t", name="X")
    db.upsert_vendor_profile(
        "org_t",
        vendor,
        invoice_count=5,
        avg_invoice_amount=10_000.0,
        always_approved=1,
        sender_domains=["acme.com"],
        last_invoice_date=(
            datetime.now(timezone.utc) - timedelta(days=7)
        ).isoformat(),
    )
    db.set_vendor_bank_details("org_t", vendor, SAMPLE_ORIGINAL)


# ===========================================================================
# VendorStore freeze accessors
# ===========================================================================


class TestVendorStoreFreezeAccessors:

    def test_start_freeze_auto_passes_known_domain(self, tmp_db):
        _seed_established_vendor(tmp_db)
        state = tmp_db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        assert state is not None
        assert state["email_domain_factor"]["verified"] is True
        assert state["email_domain_factor"]["matched_known_domain"] is True
        assert state["phone_factor"]["verified"] is False
        assert state["sign_off_factor"]["verified"] is False
        assert tmp_db.is_iban_change_pending("org_t", "Acme") is True

    def test_start_freeze_auto_fails_unknown_domain(self, tmp_db):
        _seed_established_vendor(tmp_db)
        state = tmp_db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="evil.com",
        )
        assert state["email_domain_factor"]["verified"] is False
        assert state["email_domain_factor"]["matched_known_domain"] is False

    def test_start_freeze_preserves_verified_details(self, tmp_db):
        _seed_established_vendor(tmp_db)
        tmp_db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        # Verified column untouched
        verified = tmp_db.get_vendor_bank_details("org_t", "Acme")
        assert verified == SAMPLE_ORIGINAL
        # Pending column has the new details
        pending = tmp_db.get_pending_bank_details("org_t", "Acme")
        assert pending == SAMPLE_ADVERSARIAL

    def test_start_freeze_idempotent(self, tmp_db):
        """A second start_iban_change_freeze call on an already-frozen
        vendor is a no-op that returns the existing state."""
        _seed_established_vendor(tmp_db)
        first = tmp_db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        second = tmp_db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details={"iban": "FR9999999999"},  # different again
            sender_domain="evil.com",
        )
        assert second == first
        # Pending is still the FIRST adversarial value
        pending = tmp_db.get_pending_bank_details("org_t", "Acme")
        assert pending == SAMPLE_ADVERSARIAL

    def test_start_freeze_rejects_unknown_vendor(self, tmp_db):
        tmp_db.create_organization("org_t", name="X")
        result = tmp_db.start_iban_change_freeze(
            "org_t", "Ghost",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        assert result is None

    def test_record_factor_merges_payload(self, tmp_db):
        _seed_established_vendor(tmp_db)
        tmp_db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="evil.com",
        )
        state = tmp_db.record_iban_change_factor(
            "org_t", "Acme",
            factor="phone_factor",
            payload={
                "verified_phone_number": "+44-20-1234-5678",
                "caller_name_at_vendor": "John Smith",
            },
        )
        assert state["phone_factor"]["verified"] is True
        assert state["phone_factor"]["verified_phone_number"] == "+44-20-1234-5678"

    def test_record_factor_rejects_unknown_factor(self, tmp_db):
        _seed_established_vendor(tmp_db)
        tmp_db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        result = tmp_db.record_iban_change_factor(
            "org_t", "Acme",
            factor="bogus_factor",
            payload={},
        )
        assert result is None

    def test_record_factor_returns_none_when_not_frozen(self, tmp_db):
        _seed_established_vendor(tmp_db)
        result = tmp_db.record_iban_change_factor(
            "org_t", "Acme",
            factor="phone_factor",
            payload={},
        )
        assert result is None

    def test_complete_promotes_pending_to_verified(self, tmp_db):
        _seed_established_vendor(tmp_db)
        tmp_db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        tmp_db.record_iban_change_factor(
            "org_t", "Acme",
            factor="phone_factor",
            payload={"verified_phone_number": "+44", "caller_name_at_vendor": "J"},
        )
        tmp_db.record_iban_change_factor(
            "org_t", "Acme",
            factor="sign_off_factor",
            payload={},
        )
        ok = tmp_db.complete_iban_change_freeze("org_t", "Acme")
        assert ok
        # Verified column now has the new details
        assert tmp_db.get_vendor_bank_details("org_t", "Acme") == SAMPLE_ADVERSARIAL
        # Pending cleared
        assert tmp_db.get_pending_bank_details("org_t", "Acme") is None
        assert tmp_db.is_iban_change_pending("org_t", "Acme") is False

    def test_reject_discards_pending_keeps_verified(self, tmp_db):
        _seed_established_vendor(tmp_db)
        tmp_db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="evil.com",
        )
        ok = tmp_db.reject_iban_change_freeze("org_t", "Acme")
        assert ok
        # Original verified details still in place
        assert tmp_db.get_vendor_bank_details("org_t", "Acme") == SAMPLE_ORIGINAL
        # Pending and freeze cleared
        assert tmp_db.get_pending_bank_details("org_t", "Acme") is None
        assert tmp_db.is_iban_change_pending("org_t", "Acme") is False

    def test_reject_no_op_when_not_frozen(self, tmp_db):
        _seed_established_vendor(tmp_db)
        assert tmp_db.reject_iban_change_freeze("org_t", "Acme") is False


# ===========================================================================
# IbanChangeFreezeService
# ===========================================================================


class TestIbanChangeFreezeService:

    def _service(self, db):
        from clearledgr.services.iban_change_freeze import IbanChangeFreezeService
        return IbanChangeFreezeService("org_t", db=db)

    def test_detect_no_change_when_details_match(self, tmp_db):
        _seed_established_vendor(tmp_db)
        svc = self._service(tmp_db)
        result = svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details=SAMPLE_ORIGINAL,
            sender_domain="acme.com",
        )
        assert result.status == "no_change"
        assert tmp_db.is_iban_change_pending("org_t", "Acme") is False

    def test_detect_no_change_when_no_stored_baseline(self, tmp_db):
        tmp_db.create_organization("org_t", name="X")
        tmp_db.upsert_vendor_profile("org_t", "Acme", invoice_count=1)
        svc = self._service(tmp_db)
        result = svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details=SAMPLE_ORIGINAL,
            sender_domain="acme.com",
        )
        assert result.status == "no_change"
        assert result.reason == "no_verified_baseline"

    def test_detect_starts_freeze_on_iban_mismatch(self, tmp_db):
        _seed_established_vendor(tmp_db)
        svc = self._service(tmp_db)
        result = svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="evil.com",
            triggering_ap_item_id="AP-TRIG-1",
        )
        assert result.status == "frozen"
        assert "iban" in result.mismatched_fields
        assert result.verification_state["email_domain_factor"]["verified"] is False
        assert tmp_db.is_iban_change_pending("org_t", "Acme") is True

    def test_detect_already_frozen(self, tmp_db):
        _seed_established_vendor(tmp_db)
        svc = self._service(tmp_db)
        svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="evil.com",
        )
        result = svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details={"iban": "FR9999"},
            sender_domain="other.com",
        )
        assert result.status == "already_frozen"

    def test_detect_missing_vendor_name(self, tmp_db):
        svc = self._service(tmp_db)
        result = svc.detect_and_maybe_freeze(
            vendor_name="",
            extracted_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="evil.com",
        )
        assert result.status == "no_vendor"

    def test_record_factor_stamps_actor_and_timestamp(self, tmp_db):
        _seed_established_vendor(tmp_db)
        svc = self._service(tmp_db)
        svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="evil.com",
        )
        result = svc.record_factor(
            vendor_name="Acme",
            factor="phone_factor",
            payload={
                "verified_phone_number": "+44-20-1234-5678",
                "caller_name_at_vendor": "John",
            },
            actor_id="cfo@test",
        )
        assert result.status == "recorded"
        phone = result.verification_state["phone_factor"]
        assert phone["verified"] is True
        assert phone["verified_by"] == "cfo@test"
        assert phone["verified_at"] is not None

    def test_record_factor_rejects_unknown(self, tmp_db):
        _seed_established_vendor(tmp_db)
        svc = self._service(tmp_db)
        result = svc.record_factor(
            vendor_name="Acme",
            factor="garbage",
            payload={},
            actor_id="cfo@test",
        )
        assert result.status == "unknown_factor"

    def test_record_factor_returns_not_frozen(self, tmp_db):
        _seed_established_vendor(tmp_db)
        svc = self._service(tmp_db)
        result = svc.record_factor(
            vendor_name="Acme",
            factor="phone_factor",
            payload={},
            actor_id="cfo@test",
        )
        assert result.status == "not_frozen"

    def test_complete_rejects_when_factors_missing(self, tmp_db):
        _seed_established_vendor(tmp_db)
        svc = self._service(tmp_db)
        svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",  # email auto-passes
        )
        # Only record phone_factor, leave sign_off missing
        svc.record_factor(
            vendor_name="Acme",
            factor="phone_factor",
            payload={"verified_phone_number": "+44", "caller_name_at_vendor": "J"},
            actor_id="cfo@test",
        )
        result = svc.complete_freeze(vendor_name="Acme", actor_id="cfo@test")
        assert result.status == "missing_factors"
        assert "sign_off_factor" in result.missing_factors
        # Still frozen
        assert tmp_db.is_iban_change_pending("org_t", "Acme") is True

    def test_complete_happy_path(self, tmp_db):
        _seed_established_vendor(tmp_db)
        svc = self._service(tmp_db)
        svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        svc.record_factor(
            vendor_name="Acme",
            factor="phone_factor",
            payload={"verified_phone_number": "+44", "caller_name_at_vendor": "J"},
            actor_id="cfo@test",
        )
        svc.record_factor(
            vendor_name="Acme",
            factor="sign_off_factor",
            payload={},
            actor_id="cfo@test",
        )
        result = svc.complete_freeze(vendor_name="Acme", actor_id="cfo@test")
        assert result.status == "completed"
        assert tmp_db.get_vendor_bank_details("org_t", "Acme") == SAMPLE_ADVERSARIAL
        assert tmp_db.is_iban_change_pending("org_t", "Acme") is False

    def test_reject_clears_freeze(self, tmp_db):
        _seed_established_vendor(tmp_db)
        svc = self._service(tmp_db)
        svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="evil.com",
        )
        result = svc.reject_freeze(
            vendor_name="Acme",
            actor_id="cfo@test",
            reason="Suspicious domain",
        )
        assert result.status == "rejected"
        # Verified details untouched
        assert tmp_db.get_vendor_bank_details("org_t", "Acme") == SAMPLE_ORIGINAL
        assert tmp_db.is_iban_change_pending("org_t", "Acme") is False

    def test_get_freeze_status_returns_masked(self, tmp_db):
        _seed_established_vendor(tmp_db)
        svc = self._service(tmp_db)
        svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        status = svc.get_freeze_status("Acme")
        assert status["frozen"] is True
        verified_masked = status["verified_bank_details_masked"]
        pending_masked = status["pending_bank_details_masked"]
        # NO raw values
        full_text = json.dumps(status)
        assert "GB82WEST12345698765432" not in full_text
        assert "DE89999999999999999999" not in full_text
        assert verified_masked["iban"] == "GB82 **** **** **** 5432"
        assert pending_masked["iban"] == "DE89 **** **** **** 9999"

    def test_audit_events_emitted(self, tmp_db):
        _seed_established_vendor(tmp_db)
        svc = self._service(tmp_db)
        svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="evil.com",
            triggering_ap_item_id="AP-AUD-1",
        )
        svc.record_factor(
            vendor_name="Acme",
            factor="phone_factor",
            payload={"verified_phone_number": "+44", "caller_name_at_vendor": "J"},
            actor_id="cfo@test",
        )
        svc.reject_freeze(
            vendor_name="Acme",
            actor_id="cfo@test",
            reason="Suspicious",
        )
        events = tmp_db.list_recent_ap_audit_events("org_t", limit=50)
        event_types = {e.get("event_type") for e in events}
        assert "iban_change_freeze_started" in event_types
        assert "iban_change_factor_recorded" in event_types
        assert "iban_change_freeze_rejected" in event_types


# ===========================================================================
# Validation gate hooks
# ===========================================================================


class TestValidationGateFreezeHooks:

    def _make_invoice(
        self, *, vendor: str = "Acme", bank_details: Dict[str, str] = None
    ):
        from clearledgr.services.invoice_models import InvoiceData

        inv = InvoiceData(
            gmail_id=f"gmail-{vendor}",
            subject="Invoice",
            sender=f"billing@{vendor.lower()}.com",
            vendor_name=vendor,
            amount=10_000.0,
            currency="GBP",
            invoice_number="INV-TEST",
            due_date="2026-05-01",
            confidence=0.97,
            organization_id="org_t",
            field_confidences={
                "vendor": 0.99,
                "amount": 0.98,
                "invoice_number": 0.97,
                "due_date": 0.95,
            },
        )
        inv.bank_details = bank_details
        return inv

    def test_gate_auto_starts_freeze_on_iban_mismatch(self, tmp_db):
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService
        _seed_established_vendor(tmp_db)
        service = InvoiceWorkflowService(organization_id="org_t")
        invoice = self._make_invoice(bank_details=SAMPLE_ADVERSARIAL)
        gate = asyncio.run(service._evaluate_deterministic_validation(invoice))

        # Freeze must now exist
        assert tmp_db.is_iban_change_pending("org_t", "Acme") is True
        # The FIRST invoice also carries the bank_details_mismatch_from_invoice reason
        codes = gate["reason_codes"]
        assert "bank_details_mismatch_from_invoice" in codes
        # ...and the iban_change_pending blocking reason (since freeze is now active)
        assert "iban_change_pending" in codes
        assert gate["passed"] is False

    def test_gate_blocks_every_subsequent_invoice_for_frozen_vendor(self, tmp_db):
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService
        _seed_established_vendor(tmp_db)
        # Freeze via the service directly (bypass the gate for setup)
        from clearledgr.services.iban_change_freeze import get_iban_change_freeze_service
        freeze_svc = get_iban_change_freeze_service("org_t", db=tmp_db)
        freeze_svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )

        service = InvoiceWorkflowService(organization_id="org_t")
        # Submit a new invoice with DIFFERENT details AGAIN
        invoice = self._make_invoice(bank_details={"iban": "FR7612345678901234567890"})
        gate = asyncio.run(service._evaluate_deterministic_validation(invoice))

        assert "iban_change_pending" in gate["reason_codes"]
        assert gate["passed"] is False
        # The iban_change_pending reason has severity=error
        reason = next(
            r for r in gate["reasons"] if r["code"] == "iban_change_pending"
        )
        assert reason["severity"] == "error"
        # Details contain factor names only, never values
        details_str = json.dumps(reason["details"])
        assert "GB82WEST12345698765432" not in details_str
        assert "DE89999999999999999999" not in details_str
        assert "FR7612345678901234567890" not in details_str

    def test_gate_blocks_invoice_with_no_bank_details_when_frozen(self, tmp_db):
        """A frozen vendor blocks EVERY invoice, even ones that don't
        carry bank details in the extraction."""
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService
        _seed_established_vendor(tmp_db)
        from clearledgr.services.iban_change_freeze import get_iban_change_freeze_service
        freeze_svc = get_iban_change_freeze_service("org_t", db=tmp_db)
        freeze_svc.detect_and_maybe_freeze(
            vendor_name="Acme",
            extracted_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )

        service = InvoiceWorkflowService(organization_id="org_t")
        invoice = self._make_invoice(bank_details=None)
        gate = asyncio.run(service._evaluate_deterministic_validation(invoice))
        assert "iban_change_pending" in gate["reason_codes"]
        assert gate["passed"] is False

    def test_gate_does_not_freeze_on_matching_details(self, tmp_db):
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService
        _seed_established_vendor(tmp_db)
        service = InvoiceWorkflowService(organization_id="org_t")
        invoice = self._make_invoice(bank_details=SAMPLE_ORIGINAL)
        asyncio.run(service._evaluate_deterministic_validation(invoice))
        assert tmp_db.is_iban_change_pending("org_t", "Acme") is False


# ===========================================================================
# REST API
# ===========================================================================


class TestIbanVerificationAPI:

    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.core.database import ClearledgrDB
        from clearledgr.core import database as db_module
        import main

        db = ClearledgrDB(db_path=str(tmp_path / "api.db"))
        db.initialize()
        monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
        importlib.reload(main)
        client = TestClient(main.app)
        yield client, main, db

    def _override_user(self, main, role: str, org_id: str = "org_t"):
        from clearledgr.core.auth import (
            TokenData,
            get_current_user,
            require_cfo,
        )
        from datetime import datetime, timezone

        def _user():
            return TokenData(
                user_id="u1",
                email="u1@test",
                organization_id=org_id,
                role=role,
                exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )

        main.app.dependency_overrides[get_current_user] = _user
        main.app.dependency_overrides[require_cfo] = _user

    def test_get_status_no_freeze(self, app_client):
        client, main, db = app_client
        _seed_established_vendor(db)
        self._override_user(main, "user")
        try:
            resp = client.get(
                "/api/vendors/Acme/iban-verification?organization_id=org_t"
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["frozen"] is False
            assert body["pending_bank_details_masked"] is None
        finally:
            main.app.dependency_overrides.clear()

    def test_get_status_frozen_shows_masked_pending(self, app_client):
        client, main, db = app_client
        _seed_established_vendor(db)
        db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="evil.com",
        )
        self._override_user(main, "user")
        try:
            resp = client.get(
                "/api/vendors/Acme/iban-verification?organization_id=org_t"
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["frozen"] is True
            assert body["pending_bank_details_masked"]["iban"] == "DE89 **** **** **** 9999"
            # No raw values
            assert "DE89999999999999999999" not in json.dumps(body)
            assert "GB82WEST12345698765432" not in json.dumps(body)
            # Missing factors listed
            assert "phone_factor" in body["missing_factors"]
            assert "sign_off_factor" in body["missing_factors"]
        finally:
            main.app.dependency_overrides.clear()

    def test_record_phone_factor_requires_cfo_role(self, app_client):
        client, main, db = app_client
        _seed_established_vendor(db)
        db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        # Non-CFO user
        from clearledgr.core.auth import TokenData, get_current_user
        from datetime import datetime, timezone

        def _ap_manager():
            return TokenData(
                user_id="u1",
                email="u1@test",
                organization_id="org_t",
                role="admin",  # not cfo
                exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )

        main.app.dependency_overrides[get_current_user] = _ap_manager
        try:
            resp = client.post(
                "/api/vendors/Acme/iban-verification/factors/phone"
                "?organization_id=org_t",
                json={
                    "verified_phone_number": "+44-20-1234-5678",
                    "caller_name_at_vendor": "John Smith",
                },
            )
            assert resp.status_code == 403
        finally:
            main.app.dependency_overrides.clear()

    def test_record_phone_factor_happy_path(self, app_client):
        client, main, db = app_client
        _seed_established_vendor(db)
        db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        self._override_user(main, "cfo")
        try:
            resp = client.post(
                "/api/vendors/Acme/iban-verification/factors/phone"
                "?organization_id=org_t",
                json={
                    "verified_phone_number": "+44-20-1234-5678",
                    "caller_name_at_vendor": "John Smith",
                    "notes": "Called main line",
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "recorded"
            assert body["verification_state"]["phone_factor"]["verified"] is True
        finally:
            main.app.dependency_overrides.clear()

    def test_record_sign_off_factor(self, app_client):
        client, main, db = app_client
        _seed_established_vendor(db)
        db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        self._override_user(main, "cfo")
        try:
            resp = client.post(
                "/api/vendors/Acme/iban-verification/factors/sign-off"
                "?organization_id=org_t",
                json={"notes": "Reviewed and approved"},
            )
            assert resp.status_code == 200
            assert resp.json()["verification_state"]["sign_off_factor"]["verified"] is True
        finally:
            main.app.dependency_overrides.clear()

    def test_record_email_domain_override(self, app_client):
        client, main, db = app_client
        _seed_established_vendor(db)
        db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="evil.com",  # auto-fails
        )
        self._override_user(main, "cfo")
        try:
            resp = client.post(
                "/api/vendors/Acme/iban-verification/factors/email-domain"
                "?organization_id=org_t",
                json={"override_note": "Vendor moved to new subsidiary"},
            )
            assert resp.status_code == 200
            state = resp.json()["verification_state"]
            assert state["email_domain_factor"]["verified"] is True
            assert state["email_domain_factor"]["manual_override"] is True
        finally:
            main.app.dependency_overrides.clear()

    def test_complete_requires_all_factors(self, app_client):
        client, main, db = app_client
        _seed_established_vendor(db)
        db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        self._override_user(main, "cfo")
        try:
            # Only email is verified (auto); phone + sign_off missing
            resp = client.post(
                "/api/vendors/Acme/iban-verification/complete"
                "?organization_id=org_t",
            )
            assert resp.status_code == 400
            detail = resp.json()["detail"]
            assert detail["error"] == "factors_incomplete"
            assert "phone_factor" in detail["missing_factors"]
            assert "sign_off_factor" in detail["missing_factors"]
        finally:
            main.app.dependency_overrides.clear()

    def test_complete_happy_path(self, app_client):
        client, main, db = app_client
        _seed_established_vendor(db)
        db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="acme.com",
        )
        db.record_iban_change_factor(
            "org_t", "Acme",
            factor="phone_factor",
            payload={"verified_phone_number": "+44", "caller_name_at_vendor": "J"},
        )
        db.record_iban_change_factor(
            "org_t", "Acme",
            factor="sign_off_factor",
            payload={},
        )
        self._override_user(main, "cfo")
        try:
            resp = client.post(
                "/api/vendors/Acme/iban-verification/complete"
                "?organization_id=org_t",
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "completed"
            # Verified column now has the new details
            assert db.get_vendor_bank_details("org_t", "Acme") == SAMPLE_ADVERSARIAL
        finally:
            main.app.dependency_overrides.clear()

    def test_reject_clears_freeze(self, app_client):
        client, main, db = app_client
        _seed_established_vendor(db)
        db.start_iban_change_freeze(
            "org_t", "Acme",
            pending_bank_details=SAMPLE_ADVERSARIAL,
            sender_domain="evil.com",
        )
        self._override_user(main, "cfo")
        try:
            resp = client.post(
                "/api/vendors/Acme/iban-verification/reject"
                "?organization_id=org_t",
                json={"reason": "Suspicious sender domain"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "rejected"
            # Verified details untouched
            assert db.get_vendor_bank_details("org_t", "Acme") == SAMPLE_ORIGINAL
        finally:
            main.app.dependency_overrides.clear()

    def test_cross_tenant_blocked(self, app_client):
        client, main, db = app_client
        _seed_established_vendor(db)
        self._override_user(main, "cfo", org_id="other_org")
        try:
            resp = client.get(
                "/api/vendors/Acme/iban-verification?organization_id=org_t"
            )
            assert resp.status_code == 403
            assert resp.json()["detail"] == "cross_tenant_access_denied"
        finally:
            main.app.dependency_overrides.clear()

    def test_not_frozen_returns_404_on_complete(self, app_client):
        client, main, db = app_client
        _seed_established_vendor(db)
        self._override_user(main, "cfo")
        try:
            resp = client.post(
                "/api/vendors/Acme/iban-verification/complete"
                "?organization_id=org_t",
            )
            assert resp.status_code == 404
        finally:
            main.app.dependency_overrides.clear()
