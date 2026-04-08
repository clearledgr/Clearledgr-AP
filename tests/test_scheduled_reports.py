"""Tests for scheduled report delivery.

Covers schedule retrieval, due-checking logic, and delivery tracking.
Uses a tmp_path DB fixture with org creation.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.services.scheduled_reports import (  # noqa: E402
    DEFAULT_SCHEDULES,
    ScheduledReportService,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "scheduled-reports.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    _db = db_module.get_db()
    _db.initialize()
    _db.create_organization("default", "Default", settings={})
    return _db


@pytest.fixture()
def svc(db):
    return ScheduledReportService(organization_id="default")


# ---------------------------------------------------------------------------
# get_schedules
# ---------------------------------------------------------------------------


class TestGetSchedules:
    def test_returns_defaults_when_no_custom_schedules(self, svc):
        schedules = svc.get_schedules()
        assert schedules == DEFAULT_SCHEDULES
        assert len(schedules) == 2

    def test_returns_custom_schedules_when_configured(self, svc, db):
        custom = [{"id": "custom_daily", "report_type": "posting_status",
                    "frequency": "daily", "hour_utc": 9, "enabled": True}]
        db.update_organization("default", settings_json={"report_schedules": custom})
        schedules = svc.get_schedules()
        assert len(schedules) == 1
        assert schedules[0]["id"] == "custom_daily"

    def test_defaults_returned_when_settings_empty(self, svc, db):
        db.update_organization("default", settings_json={})
        schedules = svc.get_schedules()
        assert schedules == DEFAULT_SCHEDULES


# ---------------------------------------------------------------------------
# _is_due
# ---------------------------------------------------------------------------


class TestIsDue:
    def test_weekly_schedule_matches_correct_day_and_hour(self, svc):
        schedule = {
            "id": "weekly_test",
            "frequency": "weekly",
            "day_of_week": 0,  # Monday
            "hour_utc": 7,
            "enabled": True,
        }
        # Monday at 07:xx UTC
        monday_7am = datetime(2026, 4, 6, 7, 30, 0, tzinfo=timezone.utc)  # Apr 6 2026 is Monday
        assert svc._is_due(schedule, monday_7am) is True

    def test_weekly_schedule_wrong_day(self, svc):
        schedule = {
            "id": "weekly_test",
            "frequency": "weekly",
            "day_of_week": 0,  # Monday
            "hour_utc": 7,
            "enabled": True,
        }
        # Tuesday at 07:xx UTC
        tuesday_7am = datetime(2026, 4, 7, 7, 30, 0, tzinfo=timezone.utc)
        assert svc._is_due(schedule, tuesday_7am) is False

    def test_weekly_schedule_wrong_hour(self, svc):
        schedule = {
            "id": "weekly_test",
            "frequency": "weekly",
            "day_of_week": 0,
            "hour_utc": 7,
            "enabled": True,
        }
        monday_9am = datetime(2026, 4, 6, 9, 0, 0, tzinfo=timezone.utc)
        assert svc._is_due(schedule, monday_9am) is False

    def test_monthly_schedule_matches_correct_day_and_hour(self, svc):
        schedule = {
            "id": "monthly_test",
            "frequency": "monthly",
            "day_of_month": 1,
            "hour_utc": 7,
            "enabled": True,
        }
        first_of_month = datetime(2026, 5, 1, 7, 15, 0, tzinfo=timezone.utc)
        assert svc._is_due(schedule, first_of_month) is True

    def test_monthly_schedule_wrong_day(self, svc):
        schedule = {
            "id": "monthly_test",
            "frequency": "monthly",
            "day_of_month": 1,
            "hour_utc": 7,
            "enabled": True,
        }
        second_of_month = datetime(2026, 5, 2, 7, 15, 0, tzinfo=timezone.utc)
        assert svc._is_due(schedule, second_of_month) is False

    def test_daily_schedule_matches_any_day_at_correct_hour(self, svc):
        schedule = {
            "id": "daily_test",
            "frequency": "daily",
            "hour_utc": 9,
            "enabled": True,
        }
        any_day_9am = datetime(2026, 4, 15, 9, 0, 0, tzinfo=timezone.utc)
        assert svc._is_due(schedule, any_day_9am) is True

    def test_does_not_re_deliver_within_same_hour(self, svc):
        """If already delivered this hour, _is_due returns False."""
        schedule = {
            "id": "no_redelivery",
            "frequency": "daily",
            "hour_utc": 7,
            "enabled": True,
        }
        now = datetime(2026, 4, 6, 7, 30, 0, tzinfo=timezone.utc)
        # Mark as delivered 10 minutes ago
        ten_minutes_ago = datetime(2026, 4, 6, 7, 20, 0, tzinfo=timezone.utc)
        svc._mark_delivered("no_redelivery", ten_minutes_ago)

        assert svc._is_due(schedule, now) is False


# ---------------------------------------------------------------------------
# _mark_delivered / _get_last_delivered round-trip
# ---------------------------------------------------------------------------


class TestMarkAndGetDelivered:
    def test_round_trip(self, svc):
        now = datetime(2026, 4, 6, 7, 0, 0, tzinfo=timezone.utc)
        svc._mark_delivered("test_schedule", now)
        last = svc._get_last_delivered("test_schedule")
        assert last is not None
        assert "2026-04-06" in last

    def test_get_last_delivered_returns_none_when_never_delivered(self, svc):
        assert svc._get_last_delivered("nonexistent_schedule") is None

    def test_multiple_schedules_tracked_independently(self, svc):
        t1 = datetime(2026, 4, 6, 7, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 4, 7, 7, 0, 0, tzinfo=timezone.utc)
        svc._mark_delivered("schedule_a", t1)
        svc._mark_delivered("schedule_b", t2)

        last_a = svc._get_last_delivered("schedule_a")
        last_b = svc._get_last_delivered("schedule_b")
        assert last_a is not None
        assert last_b is not None
        assert "2026-04-06" in last_a
        assert "2026-04-07" in last_b

    def test_mark_delivered_overwrites_previous(self, svc):
        t1 = datetime(2026, 4, 6, 7, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 4, 13, 7, 0, 0, tzinfo=timezone.utc)
        svc._mark_delivered("weekly_aging", t1)
        svc._mark_delivered("weekly_aging", t2)
        last = svc._get_last_delivered("weekly_aging")
        assert "2026-04-13" in last
