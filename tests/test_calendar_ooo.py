"""Tests for clearledgr/services/calendar_ooo.py — DESIGN_THESIS §6.8.

Behaviours covered:
  - Cache hit avoids a second API call (5-min TTL).
  - Missing OAuth token → False (fail-open, no exception).
  - 200 OK with busy blocks → True.
  - 200 OK with empty busy array → False.
  - Non-2xx / network error → False (fail-open).
  - Sync wrapper works when called from sync code (no running loop).
  - Routing: _check_ooo_and_get_backup returns the configured backup
    when the calendar says OOO and ooo_backups is set.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clearledgr.services import calendar_ooo
from clearledgr.services.slack_notifications import _check_ooo_and_get_backup


@pytest.fixture(autouse=True)
def _clear_cache():
    calendar_ooo.clear_cache()
    yield
    calendar_ooo.clear_cache()


def _mock_db_with_token(access_token: str | None):
    db = MagicMock()
    if access_token is None:
        db.get_oauth_token_by_email.return_value = None
    else:
        db.get_oauth_token_by_email.return_value = {"access_token": access_token}
    return db


class TestCalendarOOO:
    @pytest.mark.asyncio
    async def test_missing_token_returns_false(self):
        db = _mock_db_with_token(None)
        result = await calendar_ooo.is_approver_ooo("nobody@acme.com", db=db)
        assert result is False

    @pytest.mark.asyncio
    async def test_busy_blocks_return_true(self):
        db = _mock_db_with_token("fake-token")
        with patch.object(calendar_ooo, "_query_freebusy", new=AsyncMock(return_value=True)):
            result = await calendar_ooo.is_approver_ooo("sarah@acme.com", db=db)
        assert result is True

    @pytest.mark.asyncio
    async def test_no_busy_blocks_return_false(self):
        db = _mock_db_with_token("fake-token")
        with patch.object(calendar_ooo, "_query_freebusy", new=AsyncMock(return_value=False)):
            result = await calendar_ooo.is_approver_ooo("sarah@acme.com", db=db)
        assert result is False

    @pytest.mark.asyncio
    async def test_api_error_fails_open(self):
        db = _mock_db_with_token("fake-token")
        # _query_freebusy returns None on any error (401, 403, network)
        with patch.object(calendar_ooo, "_query_freebusy", new=AsyncMock(return_value=None)):
            result = await calendar_ooo.is_approver_ooo("sarah@acme.com", db=db)
        assert result is False

    @pytest.mark.asyncio
    async def test_cache_hit_skips_second_call(self):
        db = _mock_db_with_token("fake-token")
        mock = AsyncMock(return_value=True)
        with patch.object(calendar_ooo, "_query_freebusy", new=mock):
            r1 = await calendar_ooo.is_approver_ooo("sarah@acme.com", db=db)
            r2 = await calendar_ooo.is_approver_ooo("sarah@acme.com", db=db)
        assert r1 is True
        assert r2 is True
        # Cache hit on the second call — only one freeBusy request.
        assert mock.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_email_returns_false_without_db_call(self):
        db = _mock_db_with_token("fake-token")
        result = await calendar_ooo.is_approver_ooo("", db=db)
        assert result is False
        assert not db.get_oauth_token_by_email.called

    def test_sync_wrapper_from_sync_context(self):
        db = _mock_db_with_token("fake-token")
        with patch.object(calendar_ooo, "_query_freebusy", new=AsyncMock(return_value=True)):
            result = calendar_ooo.is_approver_ooo_sync("alice@acme.com", db=db)
        assert result is True


class TestRoutingWithCalendarOOO:
    """§6.8 end-to-end: _check_ooo_and_get_backup uses the Calendar
    check when delegation rules don't match, and returns the configured
    backup when the calendar reports OOO.
    """

    def test_calendar_ooo_routes_to_configured_backup(self, monkeypatch):
        monkeypatch.setattr(
            calendar_ooo, "is_approver_ooo_sync",
            lambda email, **kwargs: email == "cfo@acme.com",
        )

        # DB with no delegation rule active — forces the flow to fall
        # through to the Calendar check.
        db = MagicMock()
        db.get_active_delegation.return_value = None

        from clearledgr.core.database import get_db as _real_get_db
        monkeypatch.setattr("clearledgr.core.database.get_db", lambda: db)

        org_settings = {
            "ooo_backups": {"cfo@acme.com": "controller@acme.com"},
        }
        backup = _check_ooo_and_get_backup("cfo@acme.com", org_settings)
        assert backup == "controller@acme.com"

    def test_calendar_available_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            calendar_ooo, "is_approver_ooo_sync",
            lambda email, **kwargs: False,  # Calendar clear — approver is available
        )

        db = MagicMock()
        db.get_active_delegation.return_value = None
        monkeypatch.setattr("clearledgr.core.database.get_db", lambda: db)

        org_settings = {
            "ooo_backups": {"cfo@acme.com": "controller@acme.com"},
        }
        backup = _check_ooo_and_get_backup("cfo@acme.com", org_settings)
        assert backup is None  # Calendar clear — route to original approver

    def test_manual_override_wins_over_calendar_check(self, monkeypatch):
        # Manual override in ooo_overrides should short-circuit BEFORE
        # the calendar check — admins who manually configure a backup
        # expect it to kick in immediately, regardless of calendar state.
        calendar_called = [False]

        def _fake(email, **kwargs):
            calendar_called[0] = True
            return True

        monkeypatch.setattr(calendar_ooo, "is_approver_ooo_sync", _fake)

        org_settings = {
            "ooo_overrides": {"cfo@acme.com": "manual-backup@acme.com"},
            "ooo_backups": {"cfo@acme.com": "calendar-backup@acme.com"},
        }
        backup = _check_ooo_and_get_backup("cfo@acme.com", org_settings)
        assert backup == "manual-backup@acme.com"
        assert calendar_called[0] is False  # Calendar API not hit
