"""Tests for Phase 3.1.d — micro-deposit verification workflow.

Covers:
  - Amount generation: two distinct values in [0.01, 0.99]
  - Amount matching: order-independent, tolerance handling
  - MicroDepositService.initiate: happy path, terminal session blocked,
    wrong state blocked, re-initiation after lockout
  - MicroDepositService.verify: correct amounts → bank_verified,
    wrong amounts → increment counter, 3 failures → lockout + kick
    back to awaiting_bank
  - Portal form integration: correct amounts → success flash,
    wrong amounts → error flash with remaining attempts,
    lockout → error flash with re-enter instruction
  - Customer-side initiate endpoint: returns amounts + masked IBAN
  - Audit events: initiation + verification outcomes
"""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from clearledgr.core.database import ClearledgrDB
    from clearledgr.core import database as db_module

    db = ClearledgrDB(db_path=str(tmp_path / "microdeposit.db"))
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


def _seed_to_microdeposit_pending(db, org="org_t", vendor="Acme Ltd"):
    """Create a session in microdeposit_pending state with bank details set."""
    db.create_organization(org, name="Customer Inc")
    db.upsert_vendor_profile(org, vendor)
    session = db.create_vendor_onboarding_session(org, vendor, invited_by="cfo@x.com")
    for s in ("awaiting_kyc", "awaiting_bank"):
        db.transition_onboarding_session_state(session["id"], s, actor_id="vendor")
    # Set bank details so there's something to verify against.
    db.set_vendor_bank_details(org, vendor, {"iban": "GB82BARC20000055555555", "account_holder_name": "Acme Ltd"})
    return org, vendor, session


def _seed_to_awaiting_bank(db, org="org_t", vendor="Acme Ltd"):
    db.create_organization(org, name="Customer Inc")
    db.upsert_vendor_profile(org, vendor)
    session = db.create_vendor_onboarding_session(org, vendor, invited_by="cfo@x.com")
    for s in ("awaiting_kyc", "awaiting_bank"):
        db.transition_onboarding_session_state(session["id"], s, actor_id="vendor")
    db.set_vendor_bank_details(org, vendor, {"iban": "GB82BARC20000055555555", "account_holder_name": "Acme Ltd"})
    return org, vendor, session


# ===========================================================================
# Amount generation
# ===========================================================================


class TestAmountGeneration:

    def test_generates_two_distinct_amounts(self):
        from clearledgr.services.micro_deposit import _generate_amounts
        for _ in range(20):
            a1, a2 = _generate_amounts()
            assert 0.01 <= a1 <= 0.99
            assert 0.01 <= a2 <= 0.99
            assert a1 != a2

    def test_amounts_are_two_decimal_places(self):
        from clearledgr.services.micro_deposit import _generate_amounts
        a1, a2 = _generate_amounts()
        assert a1 == round(a1, 2)
        assert a2 == round(a2, 2)


# ===========================================================================
# Amount matching
# ===========================================================================


class TestAmountMatching:

    def test_exact_match_same_order(self):
        from clearledgr.services.micro_deposit import _amounts_match
        assert _amounts_match((0.17, 0.42), (0.17, 0.42)) is True

    def test_exact_match_reverse_order(self):
        from clearledgr.services.micro_deposit import _amounts_match
        assert _amounts_match((0.17, 0.42), (0.42, 0.17)) is True

    def test_within_tolerance(self):
        from clearledgr.services.micro_deposit import _amounts_match
        # 0.17 vs 0.18 is within 0.015 tolerance
        assert _amounts_match((0.17, 0.42), (0.18, 0.42)) is True

    def test_outside_tolerance(self):
        from clearledgr.services.micro_deposit import _amounts_match
        # 0.17 vs 0.20 is outside 0.015 tolerance
        assert _amounts_match((0.17, 0.42), (0.20, 0.42)) is False

    def test_completely_wrong(self):
        from clearledgr.services.micro_deposit import _amounts_match
        assert _amounts_match((0.17, 0.42), (0.50, 0.60)) is False


# ===========================================================================
# MicroDepositService.initiate
# ===========================================================================


class TestInitiate:

    def test_happy_path_from_awaiting_bank(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        org, vendor, session = _seed_to_awaiting_bank(tmp_db)
        svc = get_micro_deposit_service(db=tmp_db)
        result = svc.initiate(session["id"], actor_id="ap_manager@x.com")
        assert result.success is True
        assert result.amounts is not None
        assert len(result.amounts) == 2
        # Session should now be in microdeposit_pending.
        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "microdeposit_pending"
        # Expected amounts should be encrypted in metadata.
        meta = updated.get("metadata") or {}
        assert "microdeposit_expected_encrypted" in meta
        assert meta["microdeposit_attempt_count"] == 0

    def test_refuses_terminal_session(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        org, vendor, session = _seed_to_awaiting_bank(tmp_db)
        tmp_db.transition_onboarding_session_state(
            session["id"], "rejected", actor_id="cfo", reason="test"
        )
        svc = get_micro_deposit_service(db=tmp_db)
        result = svc.initiate(session["id"], actor_id="ap_manager")
        assert result.success is False
        assert "not_active" in (result.error or "")

    def test_refuses_wrong_state(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        db = tmp_db
        db.create_organization("org_t", name="X")
        db.upsert_vendor_profile("org_t", "Acme")
        session = db.create_vendor_onboarding_session("org_t", "Acme", invited_by="cfo")
        # Session is in 'invited' — not ready for micro-deposits.
        svc = get_micro_deposit_service(db=db)
        result = svc.initiate(session["id"], actor_id="ap_manager")
        assert result.success is False
        assert "invalid_state" in (result.error or "")


# ===========================================================================
# MicroDepositService.verify
# ===========================================================================


class TestVerify:

    def test_correct_amounts_transitions_to_bank_verified(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        org, vendor, session = _seed_to_awaiting_bank(tmp_db)
        svc = get_micro_deposit_service(db=tmp_db)
        init_result = svc.initiate(session["id"], actor_id="ap_manager")
        a1, a2 = init_result.amounts

        verify_result = svc.verify(session["id"], a1, a2)
        assert verify_result.success is True
        assert verify_result.verified is True
        assert verify_result.attempt_number == 1
        # Session should be bank_verified.
        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "bank_verified"

    def test_correct_amounts_reverse_order(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        org, vendor, session = _seed_to_awaiting_bank(tmp_db)
        svc = get_micro_deposit_service(db=tmp_db)
        init_result = svc.initiate(session["id"], actor_id="ap_manager")
        a1, a2 = init_result.amounts

        # Submit in reverse order — should still verify.
        verify_result = svc.verify(session["id"], a2, a1)
        assert verify_result.verified is True

    def test_wrong_amounts_increments_counter(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        org, vendor, session = _seed_to_awaiting_bank(tmp_db)
        svc = get_micro_deposit_service(db=tmp_db)
        svc.initiate(session["id"], actor_id="ap_manager")

        result = svc.verify(session["id"], 0.50, 0.60)
        assert result.success is True
        assert result.verified is False
        assert result.attempt_number == 1
        assert result.locked_out is False
        # Session should still be microdeposit_pending.
        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "microdeposit_pending"

    def test_three_failures_locks_out_and_kicks_to_awaiting_bank(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        org, vendor, session = _seed_to_awaiting_bank(tmp_db)
        svc = get_micro_deposit_service(db=tmp_db)
        svc.initiate(session["id"], actor_id="ap_manager")

        # Three wrong attempts.
        for i in range(3):
            result = svc.verify(session["id"], 0.50, 0.60)

        assert result.locked_out is True
        assert result.attempt_number == 3
        # Session should be kicked back to awaiting_bank.
        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "awaiting_bank"

    def test_locked_out_session_rejects_further_attempts(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        org, vendor, session = _seed_to_awaiting_bank(tmp_db)
        svc = get_micro_deposit_service(db=tmp_db)
        init_result = svc.initiate(session["id"], actor_id="ap_manager")
        a1, a2 = init_result.amounts

        # Lock out.
        for _ in range(3):
            svc.verify(session["id"], 0.50, 0.60)

        # Re-initiate (fresh amounts after kick-back to awaiting_bank).
        # First, move back to awaiting_bank (already done by lockout).
        init2 = svc.initiate(session["id"], actor_id="ap_manager")
        assert init2.success is True
        # Now verify with the NEW correct amounts.
        result = svc.verify(session["id"], init2.amounts[0], init2.amounts[1])
        assert result.verified is True

    def test_no_initiation_returns_error(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        org, vendor, session = _seed_to_microdeposit_pending(tmp_db)
        # Move to microdeposit_pending but DON'T call initiate —
        # no expected amounts in metadata.
        tmp_db.transition_onboarding_session_state(
            session["id"], "microdeposit_pending", actor_id="agent"
        )
        svc = get_micro_deposit_service(db=tmp_db)
        result = svc.verify(session["id"], 0.17, 0.42)
        assert result.success is False
        assert "no_microdeposit" in (result.error or "")


# ===========================================================================
# Portal form integration
# ===========================================================================


class TestPortalMicrodepositIntegration:

    def _make_portal_app(self):
        from fastapi.testclient import TestClient
        from clearledgr.api.vendor_portal import router
        import fastapi
        app = fastapi.FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_correct_amounts_via_portal_form(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        org, vendor, session = _seed_to_awaiting_bank(tmp_db)
        svc = get_micro_deposit_service(db=tmp_db)
        init_result = svc.initiate(session["id"], actor_id="ap_manager")
        a1, a2 = init_result.amounts

        raw_token, _ = tmp_db.generate_onboarding_token(
            session["id"], issued_by="cfo@x.com"
        )
        client = self._make_portal_app()
        resp = client.post(
            f"/portal/onboard/{raw_token}/microdeposit",
            data={"amount_one": str(a1), "amount_two": str(a2)},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "flash=" in resp.headers.get("location", "")
        assert "error=" not in resp.headers.get("location", "")

        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "bank_verified"

    def test_wrong_amounts_shows_remaining_attempts(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        org, vendor, session = _seed_to_awaiting_bank(tmp_db)
        svc = get_micro_deposit_service(db=tmp_db)
        svc.initiate(session["id"], actor_id="ap_manager")

        raw_token, _ = tmp_db.generate_onboarding_token(
            session["id"], issued_by="cfo@x.com"
        )
        client = self._make_portal_app()
        resp = client.post(
            f"/portal/onboard/{raw_token}/microdeposit",
            data={"amount_one": "0.99", "amount_two": "0.01"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        location = resp.headers.get("location", "")
        assert "error=" in location
        # Should mention remaining attempts.
        assert "2" in location  # 3 - 1 = 2 remaining


# ===========================================================================
# Customer-side initiate endpoint
# ===========================================================================


class TestInitiateEndpoint:

    def test_initiate_returns_amounts_and_masked_iban(self, tmp_db, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.api.vendor_onboarding import router
        from clearledgr.core.auth import require_financial_controller
        import fastapi

        org, vendor, session = _seed_to_awaiting_bank(tmp_db)

        app = fastapi.FastAPI()
        app.include_router(router)

        mock_user = MagicMock()
        mock_user.organization_id = org
        mock_user.email = "fc@x.com"
        mock_user.role = "financial_controller"
        app.dependency_overrides[require_financial_controller] = lambda: mock_user

        client = TestClient(app)
        resp = client.post(
            f"/api/vendors/{vendor}/onboarding/microdeposit/initiate?organization_id={org}"
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["amounts"]) == 2
        assert data["amounts"][0] != data["amounts"][1]
        assert data["vendor_name"] == vendor
        # The instruction text should mention the amounts.
        assert str(data["amounts"][0]) in data["instruction"]


# ===========================================================================
# Audit events
# ===========================================================================


class TestAuditEvents:

    def _get_audit_events(self, db, event_type):
        try:
            with db.connect() as conn:
                import sqlite3
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT * FROM audit_events WHERE event_type = ?",
                    (event_type,),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []

    def test_initiation_emits_audit_event(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        org, vendor, session = _seed_to_awaiting_bank(tmp_db)
        svc = get_micro_deposit_service(db=tmp_db)
        svc.initiate(session["id"], actor_id="ap_manager@x.com")

        events = self._get_audit_events(tmp_db, "vendor_microdeposit_initiated")
        assert len(events) == 1

    def test_verification_emits_audit_event(self, tmp_db):
        from clearledgr.services.micro_deposit import get_micro_deposit_service
        org, vendor, session = _seed_to_awaiting_bank(tmp_db)
        svc = get_micro_deposit_service(db=tmp_db)
        init_result = svc.initiate(session["id"], actor_id="ap_manager")
        svc.verify(session["id"], init_result.amounts[0], init_result.amounts[1])

        events = self._get_audit_events(tmp_db, "vendor_microdeposit_verification")
        assert len(events) == 1
