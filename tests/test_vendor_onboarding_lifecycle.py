"""Tests for Phase 3.1.e — vendor onboarding lifecycle (chase + ERP activation).

Covers:
  - chase_stale_sessions: 24h chase, 48h chase, 72h escalation,
    30-day abandonment, idempotent chase cadence
  - activate_vendor_in_erp: happy path through bank_verified →
    ready_for_erp → active, ERP failure keeps session in ready_for_erp,
    token revocation on completion, audit event emission
  - _hours_since helper: valid timestamps, invalid timestamps, None
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from clearledgr.core.database import ClearledgrDB, get_db
    from clearledgr.core import database as db_module

    db = get_db()
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


def _seed_session(db, org="org_t", vendor="Acme Ltd", invited_hours_ago=0):
    db.create_organization(org, name="Customer Inc")
    db.upsert_vendor_profile(org, vendor)
    session = db.create_vendor_onboarding_session(org, vendor, invited_by="cfo@x.com")
    # Backdate invited_at to simulate passage of time.
    if invited_hours_ago > 0:
        past = (datetime.now(timezone.utc) - timedelta(hours=invited_hours_ago)).isoformat()
        sql = db._prepare_sql(
            "UPDATE vendor_onboarding_sessions SET invited_at = ?, last_activity_at = ? WHERE id = ?"
        )
        with db.connect() as conn:
            conn.execute(sql, (past, past, session["id"]))
            conn.commit()
        session = db.get_onboarding_session_by_id(session["id"])
    # Store contact info in metadata so chase emails can dispatch.
    db.transition_onboarding_session_state(
        session["id"], "invited", actor_id="agent", emit_audit=False,
        metadata_patch={
            "invite_email_to": "billing@acme.com",
            "contact_name": "Alice",
            "customer_name": "Customer Inc",
        },
    ) if False else None  # Can't self-transition — update metadata directly
    # Direct metadata update since invited→invited isn't a valid transition.
    import json
    meta = session.get("metadata") or {}
    meta.update({
        "invite_email_to": "billing@acme.com",
        "contact_name": "Alice",
        "customer_name": "Customer Inc",
    })
    update_sql = db._prepare_sql(
        "UPDATE vendor_onboarding_sessions SET metadata = ? WHERE id = ?"
    )
    with db.connect() as conn:
        conn.execute(update_sql, (json.dumps(meta), session["id"]))
        conn.commit()
    return db.get_onboarding_session_by_id(session["id"])


def _seed_to_bank_verified(db, org="org_t", vendor="Acme Ltd"):
    db.create_organization(org, name="Customer Inc")
    db.upsert_vendor_profile(org, vendor)
    session = db.create_vendor_onboarding_session(org, vendor, invited_by="cfo@x.com")
    for s in ("kyc", "bank_verify", "bank_verified"):
        db.transition_onboarding_session_state(session["id"], s, actor_id="agent")
    # Add contact metadata.
    import json
    updated = db.get_onboarding_session_by_id(session["id"])
    meta = updated.get("metadata") or {}
    meta.update({
        "invite_email_to": "billing@acme.com",
        "contact_name": "Alice",
        "customer_name": "Customer Inc",
    })
    update_sql = db._prepare_sql(
        "UPDATE vendor_onboarding_sessions SET metadata = ? WHERE id = ?"
    )
    with db.connect() as conn:
        conn.execute(update_sql, (json.dumps(meta), session["id"]))
        conn.commit()
    return db.get_onboarding_session_by_id(session["id"])


# ===========================================================================
# _hours_since
# ===========================================================================


class TestHoursSince:

    def test_valid_timestamp(self):
        from clearledgr.services.vendor_onboarding_lifecycle import _hours_since
        two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        hours = _hours_since(two_hours_ago)
        assert hours is not None
        assert 1.9 <= hours <= 2.1

    def test_none_returns_none(self):
        from clearledgr.services.vendor_onboarding_lifecycle import _hours_since
        assert _hours_since(None) is None
        assert _hours_since("") is None

    def test_invalid_returns_none(self):
        from clearledgr.services.vendor_onboarding_lifecycle import _hours_since
        assert _hours_since("not-a-date") is None


# ===========================================================================
# chase_stale_sessions
# ===========================================================================


class TestChaseStaleSessionsLogic:

    def test_no_sessions_returns_zero_counts(self, tmp_db, monkeypatch):
        from clearledgr.services.vendor_onboarding_lifecycle import chase_stale_sessions
        # No sessions exist — should return empty result.
        result = asyncio.run(chase_stale_sessions(db=tmp_db))
        assert result.sessions_scanned == 0
        assert result.chases_sent == 0

    def test_fresh_session_not_chased(self, tmp_db, monkeypatch):
        from clearledgr.services.vendor_onboarding_lifecycle import chase_stale_sessions
        # Just invited — < 24h ago.
        _seed_session(tmp_db, invited_hours_ago=1)
        result = asyncio.run(chase_stale_sessions(db=tmp_db))
        assert result.sessions_scanned == 1
        assert result.chases_sent == 0

    def test_24h_session_chased(self, tmp_db, monkeypatch):
        from clearledgr.services import vendor_onboarding_lifecycle as mod

        session = _seed_session(tmp_db, invited_hours_ago=25)

        # Mock the email dispatch so we don't need a real Gmail client.
        mock_dispatch = AsyncMock()
        monkeypatch.setattr(
            "clearledgr.services.vendor_onboarding_email.dispatch_onboarding_chase",
            mock_dispatch,
        )

        # Pin the magic-link rehydration to a known value so this test
        # stays focused on "chase_24h is dispatched" without a
        # dependency on the token-issuance happy path. Under full-suite
        # PG load, the real ``generate_onboarding_token`` occasionally
        # returns None on pool contention (raw_token is never persisted
        # to the row, so the chase always falls through to a fresh
        # issue), and _send_chase then skips dispatch — producing the
        # infamous "awaited 0 times" flake. Stubbing the bound method
        # isolates the test from that infrastructure quirk.
        fake_token_row = {"id": "test-token", "session_id": session["id"]}
        monkeypatch.setattr(
            tmp_db,
            "generate_onboarding_token",
            lambda session_id, issued_by, purpose="full_onboarding", ttl_days=30: (
                "fake-raw-token",
                fake_token_row,
            ),
        )

        result = asyncio.run(mod.chase_stale_sessions(db=tmp_db))
        assert result.chases_sent == 1
        mock_dispatch.assert_awaited_once()
        # Chase type should be chase_24h.
        call_kwargs = mock_dispatch.call_args.kwargs
        assert call_kwargs["chase_type"] == "chase_24h"

    def test_72h_session_escalated(self, tmp_db, monkeypatch):
        from clearledgr.services import vendor_onboarding_lifecycle as mod

        session = _seed_session(tmp_db, invited_hours_ago=73)

        mock_dispatch = AsyncMock()
        monkeypatch.setattr(
            "clearledgr.services.vendor_onboarding_email.dispatch_onboarding_chase",
            mock_dispatch,
        )

        result = asyncio.run(mod.chase_stale_sessions(db=tmp_db))
        assert result.escalations == 1
        # Session should now be in escalated state.
        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "blocked"

    def test_30d_session_abandoned(self, tmp_db, monkeypatch):
        from clearledgr.services import vendor_onboarding_lifecycle as mod

        session = _seed_session(tmp_db, invited_hours_ago=31 * 24)

        result = asyncio.run(mod.chase_stale_sessions(db=tmp_db))
        assert result.abandonments == 1
        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "closed_unsuccessful"
        assert updated["is_active"] is False


# ===========================================================================
# activate_vendor_in_erp
# ===========================================================================


import json


class TestActivateVendorInErp:

    def test_happy_path_bank_verified_to_active(self, tmp_db, monkeypatch):
        from clearledgr.services import vendor_onboarding_lifecycle as mod

        session = _seed_to_bank_verified(tmp_db)

        # Mock the ERP dispatcher to return a vendor ID.
        async def mock_erp(org_id, vendor_name, db):
            return "QB-VND-12345"

        monkeypatch.setattr(mod, "_dispatch_erp_create_vendor", mock_erp)

        # Mock Gmail so completion email doesn't crash.
        monkeypatch.setattr(
            "clearledgr.services.vendor_onboarding_email._get_gmail_client_for_org",
            AsyncMock(return_value=None),
        )

        result = asyncio.run(mod.activate_vendor_in_erp(session["id"], db=tmp_db))
        assert result.success is True
        assert result.erp_vendor_id == "QB-VND-12345"

        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "active"
        assert updated["is_active"] is False  # terminal
        assert updated["erp_vendor_id"] == "QB-VND-12345"

    def test_erp_failure_stays_in_ready_for_erp(self, tmp_db, monkeypatch):
        from clearledgr.services import vendor_onboarding_lifecycle as mod

        session = _seed_to_bank_verified(tmp_db)

        async def mock_erp_fail(org_id, vendor_name, db):
            raise ConnectionError("ERP unreachable")

        monkeypatch.setattr(mod, "_dispatch_erp_create_vendor", mock_erp_fail)

        result = asyncio.run(mod.activate_vendor_in_erp(session["id"], db=tmp_db))
        assert result.success is False
        assert "unreachable" in (result.error or "").lower()

        updated = tmp_db.get_onboarding_session_by_id(session["id"])
        assert updated["state"] == "ready_for_erp"
        assert updated["is_active"] is True  # not terminal

    def test_tokens_revoked_on_activation(self, tmp_db, monkeypatch):
        from clearledgr.services import vendor_onboarding_lifecycle as mod

        session = _seed_to_bank_verified(tmp_db)
        # Issue a token for this session.
        raw_token, _ = tmp_db.generate_onboarding_token(
            session["id"], issued_by="cfo@x.com"
        )
        # Verify token is live before activation.
        assert tmp_db.validate_onboarding_token(raw_token) is not None

        async def mock_erp(org_id, vendor_name, db):
            return "QB-VND-99"

        monkeypatch.setattr(mod, "_dispatch_erp_create_vendor", mock_erp)
        monkeypatch.setattr(
            "clearledgr.services.vendor_onboarding_email._get_gmail_client_for_org",
            AsyncMock(return_value=None),
        )

        asyncio.run(mod.activate_vendor_in_erp(session["id"], db=tmp_db))
        # Token should be revoked now.
        assert tmp_db.validate_onboarding_token(raw_token) is None

    def test_invalid_state_rejected(self, tmp_db, monkeypatch):
        from clearledgr.services import vendor_onboarding_lifecycle as mod
        # Session is in invited state — too early for activation.
        tmp_db.create_organization("org_t", name="X")
        tmp_db.upsert_vendor_profile("org_t", "Acme")
        session = tmp_db.create_vendor_onboarding_session(
            "org_t", "Acme", invited_by="cfo"
        )
        result = asyncio.run(mod.activate_vendor_in_erp(session["id"], db=tmp_db))
        assert result.success is False
        assert "invalid_state" in (result.error or "")


# ===========================================================================
# Audit events
# ===========================================================================


class TestActivationAudit:

    def test_activation_emits_audit_event(self, tmp_db, monkeypatch):
        from clearledgr.services import vendor_onboarding_lifecycle as mod

        session = _seed_to_bank_verified(tmp_db)

        async def mock_erp(org_id, vendor_name, db):
            return "NQ-1"

        monkeypatch.setattr(mod, "_dispatch_erp_create_vendor", mock_erp)
        monkeypatch.setattr(
            "clearledgr.services.vendor_onboarding_email._get_gmail_client_for_org",
            AsyncMock(return_value=None),
        )

        asyncio.run(mod.activate_vendor_in_erp(session["id"], db=tmp_db))

        # Check audit events. psycopg uses HybridRow (dict + positional)
        # so no row_factory override is needed on PG.
        with tmp_db.connect() as conn:
            if not tmp_db.use_postgres:
                import sqlite3
                conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM audit_events WHERE event_type = 'vendor_onboarding_activated'"
            )
            events = [dict(r) for r in cur.fetchall()]
        assert len(events) == 1
