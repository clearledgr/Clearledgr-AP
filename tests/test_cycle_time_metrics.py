"""Tests for Wave 5 / G6 — cycle-time + touchless-rate instrumentation.

Covers:
  * Empty period: zero bills posted, no_bills_posted_in_period note.
  * Single touchless bill: walked through every state with
    actor_type='system' on every transition. touchless_rate=1.0.
  * Single touched bill: at least one transition has
    actor_type='user'. touchless_rate=0.0.
  * Mixed period: 3 bills, 2 touchless, 1 touched -> 0.6667 rate.
  * Per-stage breakdown: each stage_pair gets a sample with
    average/median/p90 hours.
  * Bills posted outside the period are excluded.
  * Bills not yet posted (state < posted_to_erp) are excluded from
    posted_count but counted in bills_in_period.
  * Tenant isolation.
  * API: returns the report; cross-org scoped.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import cycle_time_metrics as ct_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.cycle_time_metrics import (  # noqa: E402
    compute_cycle_time_report,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta Co")
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="user-1", email="op@orgA.com",
        organization_id=org, role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(ct_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


def _walk_to_posted(
    db, item_id: str, *,
    org: str = "orgA",
    actor_pattern: str = "system",
    created_offset_hours: float = -24.0,
):
    """Helper: create an AP item with backdated created_at and walk
    every state with the chosen actor_type so the test can assert
    touchless vs touched."""
    base = datetime.now(timezone.utc)
    created_at = (
        base + timedelta(hours=created_offset_hours)
    ).isoformat()
    db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Vendor X",
        "amount": 100.0,
        "currency": "USD",
        "state": "received",
    })
    # Force a backdated created_at so the bill falls within the test
    # period without depending on the fixture clock.
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ap_items SET created_at = %s WHERE id = %s",
            (created_at, item_id),
        )
        conn.commit()
    for state in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp",
    ):
        db.update_ap_item(
            item_id, state=state,
            _actor_type=actor_pattern,
            _actor_id=f"{actor_pattern}@{org}",
            _source="cycle_time_test",
        )


# ─── Empty + isolation ─────────────────────────────────────────────


def test_empty_period(db):
    period_start = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    period_end = datetime.now(timezone.utc).isoformat()
    report = compute_cycle_time_report(
        db,
        organization_id="orgA",
        period_start=period_start[:10],
        period_end=period_end[:10],
    )
    assert report.bills_posted_in_period == 0
    assert report.touchless_rate is None
    assert "no_bills_posted_in_period" in report.notes


def test_tenant_isolation(db):
    _walk_to_posted(db, "AP-cyc-iso-A", org="orgA")
    _walk_to_posted(db, "AP-cyc-iso-B", org="orgB")
    period_start = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    period_end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    report_a = compute_cycle_time_report(
        db, organization_id="orgA",
        period_start=period_start[:10], period_end=period_end[:10],
    )
    report_b = compute_cycle_time_report(
        db, organization_id="orgB",
        period_start=period_start[:10], period_end=period_end[:10],
    )
    assert report_a.bills_posted_in_period == 1
    assert report_b.bills_posted_in_period == 1


# ─── Touchless / touched ───────────────────────────────────────────


def test_single_touchless_bill(db):
    _walk_to_posted(db, "AP-cyc-touchless", actor_pattern="system")
    start = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    report = compute_cycle_time_report(
        db, organization_id="orgA",
        period_start=start[:10], period_end=end[:10],
    )
    assert report.bills_posted_in_period == 1
    assert report.touchless_count == 1
    assert report.touchless_rate == 1.0
    assert report.end_to_end_median_hours is not None


def test_single_touched_bill(db):
    _walk_to_posted(db, "AP-cyc-touched", actor_pattern="user")
    start = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    report = compute_cycle_time_report(
        db, organization_id="orgA",
        period_start=start[:10], period_end=end[:10],
    )
    assert report.touchless_count == 0
    assert report.touchless_rate == 0.0


def test_mixed_period_touchless_rate(db):
    _walk_to_posted(db, "AP-cyc-mix-1", actor_pattern="system")
    _walk_to_posted(db, "AP-cyc-mix-2", actor_pattern="system")
    _walk_to_posted(db, "AP-cyc-mix-3", actor_pattern="user")
    start = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    report = compute_cycle_time_report(
        db, organization_id="orgA",
        period_start=start[:10], period_end=end[:10],
    )
    assert report.bills_posted_in_period == 3
    assert report.touchless_count == 2
    assert abs(report.touchless_rate - 2 / 3) < 0.001


# ─── Stage breakdown ──────────────────────────────────────────────


def test_stage_breakdown_per_pair(db):
    _walk_to_posted(db, "AP-cyc-stages-1", actor_pattern="system")
    _walk_to_posted(db, "AP-cyc-stages-2", actor_pattern="system")
    start = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    report = compute_cycle_time_report(
        db, organization_id="orgA",
        period_start=start[:10], period_end=end[:10],
    )
    pairs = {
        (s.from_state, s.to_state) for s in report.stages
    }
    assert ("received", "validated") in pairs
    assert ("ready_to_post", "posted_to_erp") in pairs
    posted_pair = next(
        s for s in report.stages
        if (s.from_state, s.to_state) == ("ready_to_post", "posted_to_erp")
    )
    assert posted_pair.sample_count == 2


# ─── Period boundaries ────────────────────────────────────────────


def test_in_flight_bill_excluded_from_posted_count(db):
    """An AP item still in 'needs_approval' shouldn't bump
    posted_count, but should bump bills_in_period."""
    item = db.create_ap_item({
        "id": "AP-cyc-in-flight",
        "organization_id": "orgA",
        "vendor_name": "Vendor X",
        "amount": 100.0,
        "currency": "USD",
        "state": "received",
    })
    db.update_ap_item(item["id"], state="validated")
    db.update_ap_item(item["id"], state="needs_approval")
    start = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    report = compute_cycle_time_report(
        db, organization_id="orgA",
        period_start=start[:10], period_end=end[:10],
    )
    assert report.bills_in_period >= 1
    assert report.bills_posted_in_period == 0


def test_bill_posted_outside_period_excluded(db):
    """Walk to posted with a created_at FAR in the past so the
    posting is also outside the test window."""
    item = db.create_ap_item({
        "id": "AP-cyc-old",
        "organization_id": "orgA",
        "vendor_name": "Vendor X",
        "amount": 100.0,
        "currency": "USD",
        "state": "received",
    })
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ap_items SET created_at = %s WHERE id = %s",
            (old, item["id"]),
        )
        conn.commit()
    # Walk through states (timestamps will be 'now', so the AP
    # item's posted-state event lands today — but the period we
    # query is in the past, so this bill is outside the window.)
    for s in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp",
    ):
        db.update_ap_item(item["id"], state=s)
    start = (datetime.now(timezone.utc) - timedelta(days=210)).isoformat()
    end = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
    report = compute_cycle_time_report(
        db, organization_id="orgA",
        period_start=start[:10], period_end=end[:10],
    )
    # Posted-state event is today (now), outside the window
    # [200d, 180d] ago, so bills_posted should be 0.
    assert report.bills_posted_in_period == 0


# ─── API ───────────────────────────────────────────────────────────


def test_api_get_cycle_time(db, client_orgA):
    _walk_to_posted(db, "AP-cyc-api", actor_pattern="system")
    start = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()[:10]
    end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()[:10]
    resp = client_orgA.get(
        f"/api/workspace/metrics/cycle-time?period_start={start}&period_end={end}",
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["bills_posted_in_period"] == 1
    assert data["touchless_rate"] == 1.0
    assert "stages" in data and len(data["stages"]) > 0


def test_api_missing_query_params_422(client_orgA):
    resp = client_orgA.get("/api/workspace/metrics/cycle-time")
    assert resp.status_code == 422
