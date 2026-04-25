"""§13 tier-quota enforcement tests.

Covers the three quotas added in commit 2:
  - saved_views_per_pipeline (Starter 3, Pro+ unlimited)
  - agent_activity_retention_days (Starter 30, Pro+ 7y floor)
  - Read-only seat auto-expiry
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from clearledgr.services.subscription import PlanLimits, PlanTier


class TestPlanLimits:
    def test_starter_saved_view_cap(self):
        assert PlanLimits.for_tier(PlanTier.STARTER).saved_views_per_pipeline == 3

    def test_professional_saved_views_unlimited(self):
        assert PlanLimits.for_tier(PlanTier.PROFESSIONAL).saved_views_per_pipeline == -1

    def test_enterprise_saved_views_unlimited(self):
        assert PlanLimits.for_tier(PlanTier.ENTERPRISE).saved_views_per_pipeline == -1

    def test_starter_retention_30_days(self):
        # §13: "Agent Activity feed retention: Starter 30 days"
        assert PlanLimits.for_tier(PlanTier.STARTER).agent_activity_retention_days == 30

    def test_professional_retention_covers_7_year_statutory_floor(self):
        # §13: "Statutory minimum — default 7 years"
        days = PlanLimits.for_tier(PlanTier.PROFESSIONAL).agent_activity_retention_days
        assert days >= 365 * 7  # 7 years in days

    def test_enterprise_retention_covers_7_year_statutory_floor(self):
        days = PlanLimits.for_tier(PlanTier.ENTERPRISE).agent_activity_retention_days
        assert days >= 365 * 7


class TestSavedViewQuotaEnforcement:
    """Integration-level: POST /api/saved-views enforces the per-tier cap."""

    def test_starter_fourth_saved_view_rejected_with_402(self, tmp_path, monkeypatch):
        import clearledgr.core.database as db_module
        # Reset the subscription-service singleton so it binds to the
        # per-test DB rather than carrying the old reference from
        # whatever test ran before this one.
        import clearledgr.services.subscription as sub_mod
        sub_mod._subscription_service = None

        from main import app
        from clearledgr.core.auth import TokenData, get_current_user

        db = db_module.get_db()
        db.initialize()
        # Seed a Starter subscription
        from clearledgr.services.subscription import (
            get_subscription_service, PlanTier as _PT,
        )
        sub_svc = get_subscription_service()
        sub_svc.upgrade_plan("test-org", tier=_PT.STARTER)

        def _mock_user():
            return TokenData(
                user_id="u", email="u@x", organization_id="test-org",
                role="owner",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        app.dependency_overrides[get_current_user] = _mock_user

        from fastapi.testclient import TestClient
        client = TestClient(app)

        try:
            # Seed 3 existing saved views to hit the Starter cap.
            # Need a pipeline first — the ap-invoices pipeline is
            # seeded by migrations, so use its slug.
            payload = {
                "organization_id": "test-org",
                "pipeline_slug": "ap-invoices",
                "name": "View {n}",
                "filter_json": {"stage": "exception"},
                "sort_json": {},
                "show_in_inbox": False,
            }
            for n in range(3):
                body = dict(payload)
                body["name"] = f"View {n + 1}"
                resp = client.post("/api/saved-views", json=body)
                # Each of the first 3 either creates (200/201) or
                # returns the existing one — the Starter cap is 3
                # so all 3 must succeed.
                assert resp.status_code < 400, f"seed #{n} failed: {resp.status_code} {resp.text}"

            # 4th create should 402 with the specific error code.
            body = dict(payload)
            body["name"] = "View 4 — over cap"
            resp = client.post("/api/saved-views", json=body)
            assert resp.status_code == 402, (
                f"expected 402 over-cap, got {resp.status_code}: {resp.text}"
            )
            detail = resp.json().get("detail") or {}
            assert detail.get("error") == "saved_view_limit_reached"
            assert detail.get("limit") == 3
        finally:
            app.dependency_overrides.pop(get_current_user, None)


class TestAuditEventRetentionFilter:
    """§13 Agent Activity retention — query-time filter, not delete.

    audit_events is append-only (§7.6), so tier retention is enforced
    at read time: the customer-facing feed sees only rows within the
    tier's window, while internal audit-export paths still see the
    full record.
    """

    def _seed_events(self, tmp_path, monkeypatch):
        import clearledgr.core.database as db_module
        db = db_module.get_db()
        db.initialize()

        # Because audit_events is append-only, the only way to place
        # a row "in the past" for testing is a direct raw insert that
        # bypasses the Python helper (which always stamps ts=now).
        # We insert straight into the table with custom timestamps.
        from datetime import datetime, timezone, timedelta
        import uuid
        now = datetime.now(timezone.utc)

        def _insert_at(ts, event_type):
            with db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    (
                        "INSERT INTO audit_events "
                        "(id, box_id, box_type, event_type, actor_type, actor_id, "
                        " organization_id, decision_reason, ts) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    ),
                    (
                        f"evt-{uuid.uuid4().hex[:8]}",
                        "sentinel-box", "ap_item",
                        event_type, "agent", "agent",
                        "test-org", "test", ts.isoformat(),
                    ),
                )
                conn.commit()

        _insert_at(now - timedelta(days=5), "fresh")
        _insert_at(now - timedelta(days=31), "just_over")
        _insert_at(now - timedelta(days=365), "way_old")
        return db

    def test_retention_filter_hides_rows_beyond_window(self, tmp_path, monkeypatch):
        db = self._seed_events(tmp_path, monkeypatch)
        # Starter window: 30 days. Should only return "fresh".
        events = db.list_recent_ap_audit_events_with_retention(
            "test-org", limit=100, retention_days=30,
        )
        event_types = {e.get("event_type") for e in events}
        assert "fresh" in event_types
        assert "just_over" not in event_types
        assert "way_old" not in event_types

    def test_retention_filter_none_returns_full_history(self, tmp_path, monkeypatch):
        # Internal audit-export path passes None to bypass the tier
        # filter and see the full record.
        db = self._seed_events(tmp_path, monkeypatch)
        events = db.list_recent_ap_audit_events_with_retention(
            "test-org", limit=100, retention_days=None,
        )
        event_types = {e.get("event_type") for e in events}
        assert {"fresh", "just_over", "way_old"}.issubset(event_types)

    def test_retention_filter_zero_returns_full_history(self, tmp_path, monkeypatch):
        # retention_days=0 is the "no cap" sentinel (same as None).
        db = self._seed_events(tmp_path, monkeypatch)
        events = db.list_recent_ap_audit_events_with_retention(
            "test-org", limit=100, retention_days=0,
        )
        event_types = {e.get("event_type") for e in events}
        assert len(event_types) == 3

    def test_underlying_table_is_unchanged_by_filter(self, tmp_path, monkeypatch):
        # The filter is a query-time read — the append-only table
        # still carries every row. This is the §7.6 guarantee.
        db = self._seed_events(tmp_path, monkeypatch)
        # Narrow filter → limited feed
        filtered = db.list_recent_ap_audit_events_with_retention(
            "test-org", limit=100, retention_days=30,
        )
        # Full fetch (no retention) → all rows
        full = db.list_recent_ap_audit_events("test-org", limit=100)
        assert len(filtered) == 1
        assert len(full) == 3


class TestReadOnlySeatExpiry:
    def test_reap_expired_read_only_seats(self, tmp_path, monkeypatch):
        import clearledgr.core.database as db_module
        db = db_module.get_db()
        db.initialize()

        now = datetime.now(timezone.utc)
        yesterday = (now - timedelta(days=1)).isoformat()
        next_week = (now + timedelta(days=7)).isoformat()

        # Three seats: one expired, one still valid, one full seat
        # (should never be touched regardless of expiry).
        expired = db.create_user(
            email="expired@x", name="Expired",
            organization_id="test-org", role="read_only", is_active=True,
        )
        db.update_user(expired["id"], seat_type="read_only", seat_expires_at=yesterday)

        valid = db.create_user(
            email="valid@x", name="Valid",
            organization_id="test-org", role="read_only", is_active=True,
        )
        db.update_user(valid["id"], seat_type="read_only", seat_expires_at=next_week)

        full = db.create_user(
            email="full@x", name="Full",
            organization_id="test-org", role="owner", is_active=True,
        )
        # Full seats may have an expires_at for some reason — reaper
        # should still skip them because seat_type != 'read_only'.
        db.update_user(full["id"], seat_type="full", seat_expires_at=yesterday)

        reaped = db.reap_expired_seats()
        assert reaped == 1  # only the expired read-only seat

        # Expired user is now archived (is_active=0); other two stay active.
        assert db.get_user(expired["id"])["is_active"] is False
        assert db.get_user(valid["id"])["is_active"] is True
        assert db.get_user(full["id"])["is_active"] is True
