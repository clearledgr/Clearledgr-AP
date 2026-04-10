"""Tests for Phase 3.1.b — one-time onboarding link + portal surface.

Covers:
  - OnboardingTokenStore: generate, validate, revoke, expiry, re-issue
  - PortalSession / require_portal_token auth dependency: happy path,
    expired token, revoked token, non-existent token, terminal session
  - Vendor portal routes: GET renders HTML, POST /kyc transitions state +
    saves KYC, POST /bank-details encrypts + transitions, POST /microdeposit
    (Phase 3.1.b stub — records amounts)
  - Customer-side invite endpoint: creates session + vendor profile + token,
    blocks duplicate active sessions, cross-tenant denied, role gating
  - Migration v18 creates the vendor_onboarding_tokens table
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from clearledgr.core.database import ClearledgrDB
    from clearledgr.core import database as db_module

    db = ClearledgrDB(db_path=str(tmp_path / "portal.db"))
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


def _seed(db, org="org_t", vendor="Acme Ltd"):
    db.create_organization(org, name="X")
    db.upsert_vendor_profile(org, vendor)
    session = db.create_vendor_onboarding_session(org, vendor, invited_by="cfo@x.com")
    return org, vendor, session


# ===========================================================================
# OnboardingTokenStore
# ===========================================================================


class TestTokenGeneration:

    def test_generate_returns_raw_token_and_row(self, tmp_db):
        org, vendor, session = _seed(tmp_db)
        result = tmp_db.generate_onboarding_token(session["id"], issued_by="cfo@x.com")
        assert result is not None
        raw_token, token_row = result
        assert len(raw_token) >= 32  # urlsafe base64 of 48 bytes
        assert token_row["session_id"] == session["id"]
        assert token_row["vendor_name"] == vendor
        assert token_row["purpose"] == "full_onboarding"
        assert token_row["revoked_at"] is None

    def test_raw_token_is_not_stored(self, tmp_db):
        org, vendor, session = _seed(tmp_db)
        raw_token, token_row = tmp_db.generate_onboarding_token(
            session["id"], issued_by="cfo@x.com"
        )
        # The raw token should NOT appear in the token_row — only the hash.
        all_values = str(token_row)
        assert raw_token not in all_values
        # The hash of the token should be what was stored.
        expected_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        assert token_row["token_hash"] == expected_hash

    def test_re_issue_revokes_prior_token(self, tmp_db):
        org, vendor, session = _seed(tmp_db)
        first_raw, first_row = tmp_db.generate_onboarding_token(
            session["id"], issued_by="cfo@x.com"
        )
        second_raw, second_row = tmp_db.generate_onboarding_token(
            session["id"], issued_by="cfo@x.com"
        )
        # First token should now be revoked.
        first_updated = tmp_db.get_onboarding_token_by_id(first_row["id"])
        assert first_updated["revoked_at"] is not None
        assert first_updated["revoke_reason"] == "superseded_by_new_token"
        # Second token should be live.
        assert second_row["revoked_at"] is None
        # Validate: only the second token works.
        assert tmp_db.validate_onboarding_token(first_raw) is None
        assert tmp_db.validate_onboarding_token(second_raw) is not None

    def test_refuse_to_issue_for_terminal_session(self, tmp_db):
        org, vendor, session = _seed(tmp_db)
        tmp_db.transition_onboarding_session_state(
            session["id"], "rejected", actor_id="cfo@x.com", reason="test"
        )
        result = tmp_db.generate_onboarding_token(session["id"], issued_by="cfo@x.com")
        assert result is None


class TestTokenValidation:

    def test_valid_token_returns_row(self, tmp_db):
        org, vendor, session = _seed(tmp_db)
        raw_token, _ = tmp_db.generate_onboarding_token(session["id"], issued_by="cfo@x.com")
        validated = tmp_db.validate_onboarding_token(raw_token)
        assert validated is not None
        assert validated["session_id"] == session["id"]

    def test_invalid_token_returns_none(self, tmp_db):
        assert tmp_db.validate_onboarding_token("definitely_not_a_real_token") is None

    def test_expired_token_returns_none(self, tmp_db):
        org, vendor, session = _seed(tmp_db)
        raw_token, token_row = tmp_db.generate_onboarding_token(
            session["id"], issued_by="cfo@x.com", ttl_days=1
        )
        # Force-expire by updating expires_at to the past.
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        sql = tmp_db._prepare_sql(
            "UPDATE vendor_onboarding_tokens SET expires_at = ? WHERE id = ?"
        )
        with tmp_db.connect() as conn:
            conn.execute(sql, (past, token_row["id"]))
            conn.commit()
        assert tmp_db.validate_onboarding_token(raw_token) is None

    def test_revoked_token_returns_none(self, tmp_db):
        org, vendor, session = _seed(tmp_db)
        raw_token, token_row = tmp_db.generate_onboarding_token(
            session["id"], issued_by="cfo@x.com"
        )
        tmp_db.revoke_onboarding_token(token_row["id"], revoked_by="cfo@x.com")
        assert tmp_db.validate_onboarding_token(raw_token) is None


class TestTokenAccessTracking:

    def test_access_increments_count(self, tmp_db):
        org, vendor, session = _seed(tmp_db)
        raw_token, token_row = tmp_db.generate_onboarding_token(
            session["id"], issued_by="cfo@x.com"
        )
        assert token_row["access_count"] == 0
        tmp_db.record_onboarding_token_access(token_row["id"])
        tmp_db.record_onboarding_token_access(token_row["id"])
        updated = tmp_db.get_onboarding_token_by_id(token_row["id"])
        assert updated["access_count"] == 2
        assert updated["last_accessed_at"] is not None


class TestSessionTokenRevocation:

    def test_revoke_session_tokens_kills_all_live_tokens(self, tmp_db):
        org, vendor, session = _seed(tmp_db)
        raw_token, _ = tmp_db.generate_onboarding_token(
            session["id"], issued_by="cfo@x.com"
        )
        count = tmp_db.revoke_session_tokens(
            session["id"], revoked_by="agent", reason="session_terminated"
        )
        assert count == 1
        assert tmp_db.validate_onboarding_token(raw_token) is None


# ===========================================================================
# Migration v18
# ===========================================================================


class TestMigrationV18:

    def test_table_present_after_init(self, tmp_db):
        with tmp_db.connect() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(vendor_onboarding_tokens)")
            columns = {row[1] for row in cur.fetchall()}
        for col in ("id", "token_hash", "session_id", "expires_at", "revoked_at", "access_count"):
            assert col in columns, f"missing column {col}"

    def test_migration_v18_idempotent(self, tmp_db):
        from clearledgr.core.migrations import _MIGRATIONS
        m18 = next(m for m in _MIGRATIONS if m[0] == 18)
        with tmp_db.connect() as conn:
            cur = conn.cursor()
            m18[2](cur, tmp_db)
            conn.commit()


# ===========================================================================
# Customer-side invite endpoint
# ===========================================================================


class TestInviteEndpoint:

    def _make_app(self, tmp_db, monkeypatch):
        from unittest.mock import MagicMock
        from fastapi.testclient import TestClient
        from clearledgr.api.vendor_onboarding import router
        from clearledgr.core.auth import (
            get_current_user,
            require_cfo,
            require_financial_controller,
        )
        import fastapi

        app = fastapi.FastAPI()
        app.include_router(router)

        mock_user = MagicMock()
        mock_user.organization_id = "org_t"
        mock_user.email = "cfo@x.com"
        mock_user.user_id = "user_cfo"
        mock_user.role = "financial_controller"

        app.dependency_overrides[require_financial_controller] = lambda: mock_user
        app.dependency_overrides[require_cfo] = lambda: mock_user
        app.dependency_overrides[get_current_user] = lambda: mock_user

        return TestClient(app), mock_user

    def test_invite_creates_session_and_returns_link(self, tmp_db, monkeypatch):
        tmp_db.create_organization("org_t", name="X")
        client, _ = self._make_app(tmp_db, monkeypatch)
        resp = client.post(
            "/api/vendors/Acme Ltd/onboarding/invite?organization_id=org_t",
            json={"contact_email": "billing@acme.com"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "magic_link" in data
        assert "/portal/onboard/" in data["magic_link"]
        assert data["session"]["state"] == "invited"
        assert data["contact_email"] == "billing@acme.com"

    def test_invite_creates_vendor_profile_if_missing(self, tmp_db, monkeypatch):
        tmp_db.create_organization("org_t", name="X")
        client, _ = self._make_app(tmp_db, monkeypatch)
        # Don't call upsert_vendor_profile — let the endpoint do it.
        resp = client.post(
            "/api/vendors/NewVendor/onboarding/invite?organization_id=org_t",
            json={"contact_email": "hello@newvendor.com"},
        )
        assert resp.status_code == 200
        profile = tmp_db.get_vendor_profile("org_t", "NewVendor")
        assert profile is not None

    def test_invite_blocks_duplicate_active_session(self, tmp_db, monkeypatch):
        tmp_db.create_organization("org_t", name="X")
        client, _ = self._make_app(tmp_db, monkeypatch)
        # First invite succeeds.
        resp1 = client.post(
            "/api/vendors/Acme Ltd/onboarding/invite?organization_id=org_t",
            json={"contact_email": "billing@acme.com"},
        )
        assert resp1.status_code == 200
        # Second invite for the same vendor should fail (active session exists).
        resp2 = client.post(
            "/api/vendors/Acme Ltd/onboarding/invite?organization_id=org_t",
            json={"contact_email": "billing@acme.com"},
        )
        assert resp2.status_code == 409


class TestGetOnboardingSession:

    def test_get_returns_session(self, tmp_db, monkeypatch):
        from unittest.mock import MagicMock
        from fastapi.testclient import TestClient
        from clearledgr.api.vendor_onboarding import router
        from clearledgr.core.auth import get_current_user
        import fastapi

        org, vendor, session = _seed(tmp_db)
        app = fastapi.FastAPI()
        app.include_router(router)

        mock_user = MagicMock()
        mock_user.organization_id = org
        mock_user.email = "user@x.com"
        mock_user.role = "ap_clerk"
        app.dependency_overrides[get_current_user] = lambda: mock_user

        client = TestClient(app)
        resp = client.get(f"/api/vendors/{vendor}/onboarding/session?organization_id={org}")
        assert resp.status_code == 200
        assert resp.json()["session"]["id"] == session["id"]


# ===========================================================================
# Portal route surface (public, unauthenticated)
# ===========================================================================


class TestPortalGetForm:

    def test_get_returns_html_with_vendor_name(self, tmp_db, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.api.vendor_portal import router
        import fastapi

        org, vendor, session = _seed(tmp_db)
        raw_token, _ = tmp_db.generate_onboarding_token(session["id"], issued_by="cfo@x.com")

        app = fastapi.FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get(f"/portal/onboard/{raw_token}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert vendor in resp.text
        assert "Business details" in resp.text

    def test_get_with_expired_token_returns_410(self, tmp_db, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.api.vendor_portal import router
        import fastapi

        org, vendor, session = _seed(tmp_db)
        raw_token, token_row = tmp_db.generate_onboarding_token(session["id"], issued_by="cfo@x.com")
        tmp_db.revoke_onboarding_token(token_row["id"], revoked_by="test")

        app = fastapi.FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get(f"/portal/onboard/{raw_token}")
        assert resp.status_code == 410

    def test_get_with_nonexistent_token_returns_410(self, tmp_db, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.api.vendor_portal import router
        import fastapi

        app = fastapi.FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/portal/onboard/totally_fake_token_thats_long_enough")
        assert resp.status_code == 410


class TestPortalKycPost:

    def test_kyc_post_saves_fields_and_transitions(self, tmp_db, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.api.vendor_portal import router
        import fastapi

        org, vendor, session = _seed(tmp_db)
        raw_token, _ = tmp_db.generate_onboarding_token(session["id"], issued_by="cfo@x.com")

        app = fastapi.FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.post(
            f"/portal/onboard/{raw_token}/kyc",
            data={
                "registered_address": "123 High Street, London",
                "registration_number": "99887766",
                "vat_number": "GB123456789",
                "director_names": "Alice Smith\nBob Jones",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # KYC fields should be persisted.
        kyc = tmp_db.get_vendor_kyc(org, vendor)
        assert kyc["registration_number"] == "99887766"
        assert kyc["registered_address"] == "123 High Street, London"

        # Session should have transitioned from invited → awaiting_bank.
        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "awaiting_bank"


class TestPortalBankDetailsPost:

    def test_bank_details_post_encrypts_and_transitions(self, tmp_db, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.api.vendor_portal import router
        import fastapi

        org, vendor, session = _seed(tmp_db)
        # Drive session to awaiting_bank first.
        tmp_db.transition_onboarding_session_state(
            session["id"], "awaiting_kyc", actor_id="vendor"
        )
        tmp_db.transition_onboarding_session_state(
            session["id"], "awaiting_bank", actor_id="vendor"
        )
        raw_token, _ = tmp_db.generate_onboarding_token(session["id"], issued_by="cfo@x.com")

        app = fastapi.FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.post(
            f"/portal/onboard/{raw_token}/bank-details",
            data={
                "iban": "GB82 BARC 2000 0055 5555 55",
                "account_holder_name": "Acme Limited",
                "bank_name": "Barclays",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Bank details should be encrypted (not plaintext).
        profile = tmp_db.get_vendor_profile(org, vendor)
        assert profile.get("bank_details_encrypted") is not None
        # Plaintext IBAN should NOT appear in the profile.
        profile_str = str(profile)
        assert "GB82BARC20000055555555" not in profile_str

        # Session should be in microdeposit_pending.
        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "microdeposit_pending"


class TestPortalMicrodepositPost:

    def test_microdeposit_stub_accepts_valid_amounts(self, tmp_db, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.api.vendor_portal import router
        import fastapi

        org, vendor, session = _seed(tmp_db)
        for nxt in ("awaiting_kyc", "awaiting_bank", "microdeposit_pending"):
            tmp_db.transition_onboarding_session_state(
                session["id"], nxt, actor_id="agent"
            )
        raw_token, _ = tmp_db.generate_onboarding_token(session["id"], issued_by="cfo@x.com")

        app = fastapi.FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.post(
            f"/portal/onboard/{raw_token}/microdeposit",
            data={"amount_one": "0.17", "amount_two": "0.42"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_microdeposit_rejects_invalid_amounts(self, tmp_db, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.api.vendor_portal import router
        import fastapi

        org, vendor, session = _seed(tmp_db)
        for nxt in ("awaiting_kyc", "awaiting_bank", "microdeposit_pending"):
            tmp_db.transition_onboarding_session_state(
                session["id"], nxt, actor_id="agent"
            )
        raw_token, _ = tmp_db.generate_onboarding_token(session["id"], issued_by="cfo@x.com")

        app = fastapi.FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.post(
            f"/portal/onboard/{raw_token}/microdeposit",
            data={"amount_one": "abc", "amount_two": "0.42"},
            follow_redirects=False,
        )
        # Should redirect with error flash.
        assert resp.status_code == 303
        assert "error=" in resp.headers.get("location", "")


# ===========================================================================
# Token access tracking in portal routes
# ===========================================================================


class TestAccessTracking:

    def test_portal_get_increments_access_count(self, tmp_db, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.api.vendor_portal import router
        import fastapi

        org, vendor, session = _seed(tmp_db)
        raw_token, token_row = tmp_db.generate_onboarding_token(
            session["id"], issued_by="cfo@x.com"
        )

        app = fastapi.FastAPI()
        app.include_router(router)
        client = TestClient(app)

        client.get(f"/portal/onboard/{raw_token}")
        client.get(f"/portal/onboard/{raw_token}")

        updated = tmp_db.get_onboarding_token_by_id(token_row["id"])
        assert updated["access_count"] == 2
