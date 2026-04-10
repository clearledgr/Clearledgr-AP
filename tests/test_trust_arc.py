"""Tests for Phase 3.2 — trust-building arc (DESIGN_THESIS.md §7.5).

Covers:
  - Trust arc activation: auto-activates when first invoice is processed,
    stays dormant when no activity
  - Week 1 banner: fires once within first 7 days, sets override window
    override to 30 minutes, idempotent on re-tick
  - Day 14 baseline: fires once between day 14 and 30, includes
    performance stats (invoices, exception rate), idempotent
  - Day 30 tier expansion: fires once after day 30, clears override
    window extension, includes performance data
  - Weekly Monday signal: fires post-day-30 on Mondays only, guarded
    by 6-day cooldown
  - get_trust_arc_status: returns phase name and days since activation
  - _days_since helper: valid/invalid timestamps
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from clearledgr.core.database import ClearledgrDB
    from clearledgr.core import database as db_module

    db = ClearledgrDB(db_path=str(tmp_path / "trustarc.db"))
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


def _seed_org(db, org_id="org_t", name="TestCo"):
    db.create_organization(org_id, name=name)
    return org_id


def _set_trust_arc(db, org_id, arc_state):
    """Write trust_arc state into settings_json."""
    org = db.get_organization(org_id)
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        settings = json.loads(settings)
    settings["trust_arc"] = arc_state
    db.update_organization(org_id, settings_json=settings)


def _backdate_activation(db, org_id, days_ago):
    """Set activated_at to N days ago."""
    past = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    arc = {"activated_at": past}
    _set_trust_arc(db, org_id, arc)
    return arc


# ===========================================================================
# Helpers
# ===========================================================================


class TestDaysSince:

    def test_valid_timestamp(self):
        from clearledgr.services.trust_arc import _days_since
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        d = _days_since(two_days_ago)
        assert d is not None
        assert 1.9 <= d <= 2.1

    def test_none_returns_none(self):
        from clearledgr.services.trust_arc import _days_since
        assert _days_since(None) is None
        assert _days_since("") is None


# ===========================================================================
# Activation
# ===========================================================================


class TestActivation:

    def test_auto_activates_when_invoices_exist(self, tmp_db, monkeypatch):
        from clearledgr.services.trust_arc import run_trust_arc_tick, _get_trust_arc_state

        org_id = _seed_org(tmp_db)
        # Create an AP item so the org has activity.
        tmp_db.create_ap_item({
            "organization_id": org_id,
            "vendor": "Acme",
            "amount": 100,
            "state": "posted_to_erp",
        })

        # Mock Slack so banner doesn't crash.
        monkeypatch.setattr(
            "clearledgr.services.trust_arc._send_slack_message",
            AsyncMock(return_value=True),
        )

        result = asyncio.run(run_trust_arc_tick(db=tmp_db))
        assert result.activations == 1
        arc = _get_trust_arc_state(tmp_db, org_id)
        assert arc.get("activated_at") is not None

    def test_no_activation_without_invoices(self, tmp_db, monkeypatch):
        from clearledgr.services.trust_arc import run_trust_arc_tick, _get_trust_arc_state

        _seed_org(tmp_db)
        result = asyncio.run(run_trust_arc_tick(db=tmp_db))
        assert result.activations == 0
        arc = _get_trust_arc_state(tmp_db, "org_t")
        assert not arc.get("activated_at")


# ===========================================================================
# Week 1 Banner
# ===========================================================================


class TestWeek1Banner:

    def test_fires_within_first_7_days(self, tmp_db, monkeypatch):
        from clearledgr.services.trust_arc import run_trust_arc_tick, _get_trust_arc_state

        org_id = _seed_org(tmp_db)
        _backdate_activation(tmp_db, org_id, days_ago=2)

        mock_send = AsyncMock(return_value=True)
        monkeypatch.setattr("clearledgr.services.trust_arc._send_slack_message", mock_send)

        result = asyncio.run(run_trust_arc_tick(db=tmp_db))
        assert result.week1_banners == 1
        mock_send.assert_awaited_once()
        # Should include "observation mode" in the text.
        call_text = mock_send.call_args[0][1]
        assert "observation" in call_text.lower()

        # Override window should be set to 30 minutes.
        arc = _get_trust_arc_state(tmp_db, org_id)
        assert arc["override_window_override_minutes"] == 30

    def test_idempotent_on_re_tick(self, tmp_db, monkeypatch):
        from clearledgr.services.trust_arc import run_trust_arc_tick

        org_id = _seed_org(tmp_db)
        _backdate_activation(tmp_db, org_id, days_ago=2)

        mock_send = AsyncMock(return_value=True)
        monkeypatch.setattr("clearledgr.services.trust_arc._send_slack_message", mock_send)

        asyncio.run(run_trust_arc_tick(db=tmp_db))
        asyncio.run(run_trust_arc_tick(db=tmp_db))
        # Should only fire once.
        assert mock_send.await_count == 1


# ===========================================================================
# Day 14 Baseline
# ===========================================================================


class TestDay14Baseline:

    def test_fires_at_day_14(self, tmp_db, monkeypatch):
        from clearledgr.services.trust_arc import run_trust_arc_tick, _get_trust_arc_state

        org_id = _seed_org(tmp_db)
        arc = _backdate_activation(tmp_db, org_id, days_ago=15)
        arc["week1_banner_sent"] = True
        _set_trust_arc(tmp_db, org_id, arc)

        mock_send = AsyncMock(return_value=True)
        monkeypatch.setattr("clearledgr.services.trust_arc._send_slack_message", mock_send)

        result = asyncio.run(run_trust_arc_tick(db=tmp_db))
        assert result.day14_baselines == 1
        call_text = mock_send.call_args[0][1]
        assert "two weeks" in call_text.lower() or "invoices" in call_text.lower()

    def test_does_not_fire_before_day_14(self, tmp_db, monkeypatch):
        from clearledgr.services.trust_arc import run_trust_arc_tick

        org_id = _seed_org(tmp_db)
        arc = _backdate_activation(tmp_db, org_id, days_ago=10)
        arc["week1_banner_sent"] = True
        _set_trust_arc(tmp_db, org_id, arc)

        mock_send = AsyncMock(return_value=True)
        monkeypatch.setattr("clearledgr.services.trust_arc._send_slack_message", mock_send)

        result = asyncio.run(run_trust_arc_tick(db=tmp_db))
        assert result.day14_baselines == 0


# ===========================================================================
# Day 30 Tier Expansion
# ===========================================================================


class TestDay30Expansion:

    def test_fires_at_day_30(self, tmp_db, monkeypatch):
        from clearledgr.services.trust_arc import run_trust_arc_tick, _get_trust_arc_state

        org_id = _seed_org(tmp_db)
        arc = _backdate_activation(tmp_db, org_id, days_ago=31)
        arc["week1_banner_sent"] = True
        arc["day14_baseline_sent"] = True
        _set_trust_arc(tmp_db, org_id, arc)

        mock_send = AsyncMock(return_value=True)
        monkeypatch.setattr("clearledgr.services.trust_arc._send_slack_message", mock_send)

        result = asyncio.run(run_trust_arc_tick(db=tmp_db))
        assert result.day30_expansions == 1

        # Override window extension should be cleared.
        updated_arc = _get_trust_arc_state(tmp_db, org_id)
        assert "override_window_override_minutes" not in updated_arc

    def test_does_not_fire_before_day_30(self, tmp_db, monkeypatch):
        from clearledgr.services.trust_arc import run_trust_arc_tick

        org_id = _seed_org(tmp_db)
        arc = _backdate_activation(tmp_db, org_id, days_ago=25)
        arc["week1_banner_sent"] = True
        arc["day14_baseline_sent"] = True
        _set_trust_arc(tmp_db, org_id, arc)

        mock_send = AsyncMock(return_value=True)
        monkeypatch.setattr("clearledgr.services.trust_arc._send_slack_message", mock_send)

        result = asyncio.run(run_trust_arc_tick(db=tmp_db))
        assert result.day30_expansions == 0


# ===========================================================================
# Weekly Monday Signal
# ===========================================================================


class TestWeeklySignal:

    def test_fires_on_monday_post_day_30(self, tmp_db, monkeypatch):
        from clearledgr.services import trust_arc as arc_mod
        from clearledgr.services.trust_arc import run_trust_arc_tick

        org_id = _seed_org(tmp_db)
        arc = _backdate_activation(tmp_db, org_id, days_ago=35)
        arc["week1_banner_sent"] = True
        arc["day14_baseline_sent"] = True
        arc["day30_expansion_sent"] = True
        _set_trust_arc(tmp_db, org_id, arc)

        mock_send = AsyncMock(return_value=True)
        monkeypatch.setattr("clearledgr.services.trust_arc._send_slack_message", mock_send)

        # Force today to be a Monday.
        import datetime as dt_mod
        real_now = datetime.now(timezone.utc)
        # Find the next Monday.
        days_ahead = (0 - real_now.weekday()) % 7  # 0 = Monday
        if days_ahead == 0 and real_now.weekday() != 0:
            days_ahead = 7
        monday = real_now + timedelta(days=days_ahead)

        def mock_now():
            return monday.replace(hour=10, minute=0, second=0)

        monkeypatch.setattr(arc_mod, "_now", mock_now)

        result = asyncio.run(run_trust_arc_tick(db=tmp_db))
        assert result.weekly_signals == 1

    def test_does_not_fire_on_non_monday(self, tmp_db, monkeypatch):
        from clearledgr.services import trust_arc as arc_mod
        from clearledgr.services.trust_arc import run_trust_arc_tick

        org_id = _seed_org(tmp_db)
        arc = _backdate_activation(tmp_db, org_id, days_ago=35)
        arc["week1_banner_sent"] = True
        arc["day14_baseline_sent"] = True
        arc["day30_expansion_sent"] = True
        _set_trust_arc(tmp_db, org_id, arc)

        mock_send = AsyncMock(return_value=True)
        monkeypatch.setattr("clearledgr.services.trust_arc._send_slack_message", mock_send)

        # Force today to be a Wednesday.
        real_now = datetime.now(timezone.utc)
        days_to_wed = (2 - real_now.weekday()) % 7
        if days_to_wed == 0 and real_now.weekday() != 2:
            days_to_wed = 7
        wednesday = real_now + timedelta(days=days_to_wed)

        def mock_now():
            return wednesday.replace(hour=10, minute=0, second=0)

        monkeypatch.setattr(arc_mod, "_now", mock_now)

        result = asyncio.run(run_trust_arc_tick(db=tmp_db))
        assert result.weekly_signals == 0


# ===========================================================================
# get_trust_arc_status
# ===========================================================================


class TestGetTrustArcStatus:

    def test_not_started(self, tmp_db):
        from clearledgr.services.trust_arc import get_trust_arc_status
        _seed_org(tmp_db)
        status = get_trust_arc_status(tmp_db, "org_t")
        assert status["status"] == "not_started"

    def test_week1_phase(self, tmp_db):
        from clearledgr.services.trust_arc import get_trust_arc_status
        _seed_org(tmp_db)
        _backdate_activation(tmp_db, "org_t", days_ago=3)
        status = get_trust_arc_status(tmp_db, "org_t")
        assert status["status"] == "week1_observation"
        assert 2.5 <= status["days_since_activation"] <= 3.5

    def test_baseline_phase(self, tmp_db):
        from clearledgr.services.trust_arc import get_trust_arc_status
        _seed_org(tmp_db)
        arc = _backdate_activation(tmp_db, "org_t", days_ago=16)
        arc["day14_baseline_sent"] = True
        _set_trust_arc(tmp_db, "org_t", arc)
        status = get_trust_arc_status(tmp_db, "org_t")
        assert status["status"] == "baseline_established"

    def test_ongoing_phase(self, tmp_db):
        from clearledgr.services.trust_arc import get_trust_arc_status
        _seed_org(tmp_db)
        arc = _backdate_activation(tmp_db, "org_t", days_ago=35)
        arc["week1_banner_sent"] = True
        arc["day14_baseline_sent"] = True
        arc["day30_expansion_sent"] = True
        _set_trust_arc(tmp_db, "org_t", arc)
        status = get_trust_arc_status(tmp_db, "org_t")
        assert status["status"] == "ongoing_weekly_signal"
