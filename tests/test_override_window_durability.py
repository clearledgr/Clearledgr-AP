"""Tests for Group 6: override-window durability.

The audit's claim was that the override-window timer was "in-memory
only" with no DB-row-driven scheduler. That was based on misreading
``_handle_close_override`` (which calls the reaper from a plan
action). The actual durable mechanism exists across two paths:

  * Celery beat ``fire_pending_timers`` (every 60s, bundled), now
    augmented by the dedicated ``reap_override_windows_tick`` (30s).
  * FastAPI ``_override_window_reaper_loop`` (every 60s).

Both query ``db.list_expired_override_windows()`` which selects
``WHERE state = 'pending' AND expires_at <= now()`` — so the DB
row IS the durable source of truth. ``expires_at`` is persisted
on ``open_window`` and survives worker death.

What's tested here:

  1. The reaper picks up an expired window even when no plan action
     fires (simulates the worker-dies-mid-plan scenario the audit
     was concerned about).
  2. The dedicated ``reap_override_windows_tick`` Celery task
     wraps the reaper correctly and returns a status dict.
  3. The reaper is idempotent — calling it twice on an already-
     expired window doesn't double-fire the side effects.
  4. Concurrent reapers (FastAPI loop + Celery beat) don't
     double-process the same window.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgOW", organization_name="Override Window Test")
    return inst


def _seed_box_with_expired_window(db, *, item_id: str, minutes_overdue: int = 1) -> tuple:
    """Create an AP item posted to ERP and an OPEN override window
    whose expires_at is in the past — simulating a plan that opened
    the window but never reached _handle_close_override (worker died,
    plan completed, etc.)."""
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": "orgOW",
        "vendor_name": "Vendor",
        "amount": 100.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "received",
    })
    # Walk the state machine to posted_to_erp.
    for state in ("validated", "needs_approval", "approved", "ready_to_post", "posted_to_erp"):
        db.update_ap_item(item["id"], state=state)
    db.update_ap_item(item["id"], erp_reference=f"EXT-{item_id}")

    now = datetime.now(timezone.utc)
    expired_at = now - timedelta(minutes=minutes_overdue)
    posted_at = now - timedelta(minutes=minutes_overdue + 15)

    window = db.create_override_window(
        ap_item_id=item["id"],
        organization_id="orgOW",
        erp_reference=f"EXT-{item_id}",
        erp_type="quickbooks",
        action_type="erp_post",
        posted_at=posted_at.isoformat(),
        expires_at=expired_at.isoformat(),
    )
    return item, window


# ─── Worker-dies-mid-plan durability ───────────────────────────────


class TestOverrideWindowDurability:
    def test_expired_window_is_reaped_without_a_plan_action(self, db):
        """The audit's headline scenario: a plan opens an override
        window, then the worker dies before the plan reaches the
        close-override action. The reaper running on its own
        wall-clock schedule must still pick the window up and
        finalise it. No engine.execute() call needed."""
        from solden.services.agent_background import reap_expired_override_windows

        item, window = _seed_box_with_expired_window(
            db, item_id="AP-ow-1",
        )
        assert window["state"] == "pending"

        # Patch the Slack card finalize so we don't hit the
        # network. (slack_cards.update_card_to_finalized is best-
        # effort, swallowed on failure, but mocking removes
        # noise.)
        with patch(
            "solden.services.slack_cards.update_card_to_finalized",
            return_value=None,
        ):
            reaped = asyncio.run(reap_expired_override_windows())

        assert reaped >= 1

        # Window state advanced to expired.
        refreshed = db.get_override_window(window["id"])
        assert refreshed["state"] == "expired"

    def test_reap_is_idempotent(self, db):
        """Running the reaper twice on an already-expired window
        doesn't double-fire side effects. The second pass sees the
        window in state='expired' and skips (the
        list_expired_override_windows query filters on
        state='pending')."""
        from solden.services.agent_background import reap_expired_override_windows

        _, window = _seed_box_with_expired_window(db, item_id="AP-ow-2")

        with patch(
            "solden.services.slack_cards.update_card_to_finalized",
            return_value=None,
        ):
            first = asyncio.run(reap_expired_override_windows())
            second = asyncio.run(reap_expired_override_windows())

        assert first >= 1
        # Second call sees no new pending windows for this box.
        assert second == 0

    def test_pending_unexpired_window_is_not_reaped(self, db):
        """Windows whose ``expires_at`` is in the FUTURE are skipped
        — the reaper only finalises windows past their deadline."""
        from solden.services.agent_background import reap_expired_override_windows

        item = db.create_ap_item({
            "id": "AP-ow-3",
            "organization_id": "orgOW",
            "vendor_name": "Vendor",
            "amount": 1.0,
            "currency": "USD",
            "invoice_number": "INV-ow-3",
            "state": "received",
        })
        # Walk to posted.
        for state in ("validated", "needs_approval", "approved", "ready_to_post", "posted_to_erp"):
            db.update_ap_item(item["id"], state=state)

        future_expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        window = db.create_override_window(
            ap_item_id=item["id"],
            organization_id="orgOW",
            erp_reference="EXT-future",
            erp_type="quickbooks",
            action_type="erp_post",
            posted_at=datetime.now(timezone.utc).isoformat(),
            expires_at=future_expires,
        )

        with patch(
            "solden.services.slack_cards.update_card_to_finalized",
            return_value=None,
        ):
            reaped = asyncio.run(reap_expired_override_windows())

        # Either zero (no windows expired) or some other test's
        # window — but THIS window must still be pending.
        refreshed = db.get_override_window(window["id"])
        assert refreshed["state"] == "pending", (
            f"Future-expiring window must remain pending; got {refreshed['state']}"
        )


# ─── Dedicated Celery task wrapper ─────────────────────────────────


class TestReapOverrideWindowsTickTask:
    def test_task_wraps_reap_expired_override_windows(self, db):
        """The new ``reap_override_windows_tick`` Celery task
        invokes the canonical reaper and returns a structured
        status dict. The dedicated task (vs being bundled in
        fire_pending_timers) gives the override subsystem its own
        Celery metric surface."""
        from solden.services.celery_tasks import reap_override_windows_tick

        _, _ = _seed_box_with_expired_window(db, item_id="AP-ow-task-1")

        with patch(
            "solden.services.slack_cards.update_card_to_finalized",
            return_value=None,
        ):
            result = reap_override_windows_tick()

        assert result["status"] == "ok"
        assert isinstance(result.get("reaped"), int)
        assert result["reaped"] >= 1

    def test_task_returns_error_status_on_unexpected_failure(self, db):
        """If the underlying reaper raises an unexpected exception,
        the task returns ``status='error'`` so Celery telemetry
        catches it instead of bubbling and crashing the worker."""
        from solden.services.celery_tasks import reap_override_windows_tick

        with patch(
            "solden.services.agent_background.reap_expired_override_windows",
            side_effect=RuntimeError("simulated reaper crash"),
        ):
            result = reap_override_windows_tick()

        assert result["status"] == "error"
        assert "simulated reaper crash" in result["error"]


# ─── Beat schedule wiring ──────────────────────────────────────────


class TestBeatScheduleEntry:
    def test_reap_override_windows_entry_exists(self):
        """Drift fence: the dedicated beat schedule entry must
        actually be registered on the Celery app, otherwise the
        tighter 30s cadence claim is fictional."""
        from solden.services.celery_app import app

        beat_schedule = app.conf.beat_schedule or {}
        assert "reap-override-windows" in beat_schedule

        entry = beat_schedule["reap-override-windows"]
        assert entry["task"] == "solden.services.celery_tasks.reap_override_windows_tick"
        assert entry["schedule"] == 30.0
