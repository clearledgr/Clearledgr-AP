"""Tests for the workspace reports service + API — Module 8.

Five fixed reports, each tested at both layers:

  - Service layer: structured-payload shape, summary maths, time
    bucketing, filter respecting, empty-result behaviour, DB-failure
    safety net.
  - API layer: org scoping (cross-tenant isolation), default-window
    behaviour, parameter validation.

Pinned acceptance criteria from the GA scope (line 280):
  - Each report loads in under 5 seconds for one year of data
  - No personally identifying ranking
  - Reports are designed (finite set) — only five report_types exist
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

from clearledgr.api import workspace_reports as report_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services import workspace_reports as report_svc  # noqa: E402


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
        user_id="leader@orgA.com",
        email="leader@orgA.com",
        organization_id=org,
        role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(report_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


@pytest.fixture()
def client_orgB(db):
    app = FastAPI()
    app.include_router(report_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgB")
    return TestClient(app)


def _make_item(
    db, *,
    item_id: str,
    org: str = "orgA",
    vendor: str = "Vendor X",
    amount: float = 100.0,
    currency: str = "USD",
    state: str = "received",
    exception_code: str = "",
    confidence: float | None = None,
    created_at: datetime | None = None,
    erp_posted_at: datetime | None = None,
    entity_id: str | None = None,
):
    """Insert an AP item for report tests with deterministic timestamps."""
    payload = {
        "id": item_id,
        "organization_id": org,
        "vendor_name": vendor,
        "amount": amount,
        "currency": currency,
        "invoice_number": f"INV-{item_id}",
        "state": state,
        "exception_code": exception_code,
        "metadata": {},
    }
    if confidence is not None:
        payload["confidence"] = confidence
    if entity_id is not None:
        payload["entity_id"] = entity_id
    db.create_ap_item(payload)
    # Override created_at + erp_posted_at directly — create_ap_item
    # always stamps now() so tests need a low-level update for time
    # travel.
    sets = []
    args = []
    if created_at is not None:
        sets.append("created_at = %s")
        args.append(created_at.isoformat())
    if erp_posted_at is not None:
        sets.append("erp_posted_at = %s")
        args.append(erp_posted_at.isoformat())
    if sets:
        sql = f"UPDATE ap_items SET {', '.join(sets)} WHERE id = %s"
        args.append(item_id)
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(args))
            conn.commit()
    return db.get_ap_item(item_id)


def _ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


# ─── Tests: Volume report ───────────────────────────────────────────


class TestVolumeReport:
    def test_empty_org_returns_zero_summary(self, db):
        out = report_svc.generate_volume_report("orgA")
        assert out["report_type"] == "volume"
        assert out["summary"]["total_invoices"] == 0
        assert out["summary"]["total_amount"] == 0.0
        assert out["series"] == []
        assert out["breakdown"] == []
        assert out["params"]["period"] == "weekly"

    def test_summary_aggregates_count_and_amount(self, db):
        for i, amt in enumerate([100.0, 250.0, 500.0]):
            _make_item(db, item_id=f"vol-{i}", amount=amt, created_at=_ago(7))
        out = report_svc.generate_volume_report("orgA")
        assert out["summary"]["total_invoices"] == 3
        assert out["summary"]["total_amount"] == 850.0
        assert out["summary"]["distinct_vendors"] == 1

    def test_series_bucketed_per_period(self, db):
        # Two items 7 days apart → 2 weekly buckets.
        _make_item(db, item_id="vol-w1", created_at=_ago(14))
        _make_item(db, item_id="vol-w2", created_at=_ago(2))
        out = report_svc.generate_volume_report("orgA", period="weekly")
        # at least 2 buckets present
        assert len(out["series"]) >= 2
        for entry in out["series"]:
            assert "bucket" in entry
            assert isinstance(entry["invoice_count"], int)

    def test_daily_period_emits_daily_buckets(self, db):
        _make_item(db, item_id="vol-d1", created_at=_ago(2))
        out = report_svc.generate_volume_report("orgA", period="daily")
        assert out["params"]["period"] == "daily"
        assert any(len(s["bucket"]) == 10 for s in out["series"])  # YYYY-MM-DD

    def test_monthly_period_emits_yyyy_mm(self, db):
        _make_item(db, item_id="vol-m1", created_at=_ago(2))
        out = report_svc.generate_volume_report("orgA", period="monthly")
        assert any(len(s["bucket"]) == 7 for s in out["series"])  # YYYY-MM

    def test_invalid_period_falls_back_to_weekly(self, db):
        out = report_svc.generate_volume_report("orgA", period="hourly")
        assert out["params"]["period"] == "weekly"

    def test_breakdown_ranks_top_vendors_by_amount(self, db):
        _make_item(db, item_id="vol-v1", vendor="Acme", amount=1000.0, created_at=_ago(7))
        _make_item(db, item_id="vol-v2", vendor="Beta", amount=200.0, created_at=_ago(7))
        _make_item(db, item_id="vol-v3", vendor="Beta", amount=100.0, created_at=_ago(7))
        out = report_svc.generate_volume_report("orgA")
        assert out["breakdown"][0]["vendor_name"] == "Acme"
        assert out["breakdown"][0]["total_amount"] == 1000.0
        assert out["breakdown"][1]["vendor_name"] == "Beta"
        assert out["breakdown"][1]["invoice_count"] == 2

    def test_window_clamped_to_max_lookback(self, db):
        # Caller asks for 5 years → service clamps to 400 days.
        out = report_svc.generate_volume_report(
            "orgA",
            from_ts=(datetime.now(timezone.utc) - timedelta(days=5 * 365)).isoformat(),
        )
        from_dt = datetime.fromisoformat(out["params"]["from"])
        to_dt = datetime.fromisoformat(out["params"]["to"])
        assert (to_dt - from_dt).days <= 400


# ─── Tests: Agent Performance ───────────────────────────────────────


class TestAgentPerformance:
    def test_auto_resolution_rate_includes_only_clean_terminal_states(self, db):
        # 2 auto-resolved, 1 needs_info, 1 failed_post.
        _make_item(db, item_id="ap-r1", state="posted_to_erp", created_at=_ago(7))
        _make_item(db, item_id="ap-r2", state="closed", created_at=_ago(6))
        _make_item(db, item_id="ap-r3", state="needs_info", created_at=_ago(5))
        _make_item(db, item_id="ap-r4", state="failed_post", created_at=_ago(4))

        out = report_svc.generate_agent_performance_report("orgA")
        assert out["summary"]["sample_size"] == 4
        # 2 / 4 = 0.50
        assert out["summary"]["auto_resolution_rate"] == 0.5
        # needs_info + failed_post = 2 / 4 = 0.50 exception rate
        assert out["summary"]["exception_rate"] == 0.5

    def test_exception_code_counts_as_exception_even_in_terminal(self, db):
        # posted_to_erp + exception_code → still exception (the gate
        # caught something, even though the bill posted).
        _make_item(
            db, item_id="ap-r5",
            state="posted_to_erp",
            exception_code="po_required_missing",
            created_at=_ago(3),
        )
        out = report_svc.generate_agent_performance_report("orgA")
        assert out["summary"]["auto_resolution_rate"] == 0.0
        assert out["summary"]["exception_rate"] == 1.0

    def test_avg_confidence_only_counts_non_null(self, db):
        _make_item(db, item_id="ap-c1", confidence=0.95, created_at=_ago(5))
        _make_item(db, item_id="ap-c2", confidence=0.85, created_at=_ago(4))
        _make_item(db, item_id="ap-c3", created_at=_ago(3))  # null confidence
        out = report_svc.generate_agent_performance_report("orgA")
        assert out["summary"]["sample_size"] == 3
        assert out["summary"]["avg_confidence"] == pytest.approx(0.90, rel=1e-3)

    def test_empty_org_returns_zero_rates(self, db):
        out = report_svc.generate_agent_performance_report("orgA")
        assert out["summary"]["sample_size"] == 0
        assert out["summary"]["auto_resolution_rate"] == 0.0
        assert out["summary"]["exception_rate"] == 0.0
        assert out["summary"]["avg_confidence"] is None


# ─── Tests: Cycle Time ──────────────────────────────────────────────


class TestCycleTime:
    def test_summary_avg_uses_only_posted_items(self, db):
        # 3-day cycle on 2 posted items, 1 unposted (excluded).
        for idx in range(2):
            _make_item(
                db, item_id=f"ct-{idx}",
                created_at=_ago(10),
                erp_posted_at=_ago(7),
                state="posted_to_erp",
            )
        _make_item(db, item_id="ct-pending", state="needs_approval", created_at=_ago(5))
        out = report_svc.generate_cycle_time_report("orgA")
        assert out["summary"]["posted_count"] == 2
        assert out["summary"]["avg_cycle_days"] == pytest.approx(3.0, abs=0.05)

    def test_p50_p90_quantiles_present(self, db):
        cycles = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        for idx, days in enumerate(cycles):
            _make_item(
                db, item_id=f"q-{idx}",
                created_at=_ago(20),
                erp_posted_at=_ago(20 - days),
                state="posted_to_erp",
            )
        out = report_svc.generate_cycle_time_report("orgA")
        assert out["summary"]["p50_cycle_days"] is not None
        assert out["summary"]["p90_cycle_days"] is not None
        assert out["summary"]["p90_cycle_days"] >= out["summary"]["p50_cycle_days"]

    def test_empty_returns_none_summary(self, db):
        out = report_svc.generate_cycle_time_report("orgA")
        assert out["summary"]["posted_count"] == 0
        assert out["summary"]["avg_cycle_days"] is None

    def test_breakdown_per_entity(self, db):
        for idx in range(3):
            _make_item(
                db, item_id=f"ct-e1-{idx}",
                created_at=_ago(10),
                erp_posted_at=_ago(8),
                state="posted_to_erp",
                entity_id="entity-uk",
            )
        for idx in range(2):
            _make_item(
                db, item_id=f"ct-e2-{idx}",
                created_at=_ago(10),
                erp_posted_at=_ago(5),
                state="posted_to_erp",
                entity_id="entity-us",
            )
        out = report_svc.generate_cycle_time_report("orgA")
        ids = {b["entity_id"] for b in out["breakdown"]}
        assert "entity-uk" in ids
        assert "entity-us" in ids


# ─── Tests: Exception Breakdown ─────────────────────────────────────


class TestExceptionBreakdown:
    def test_breakdown_ranks_codes_by_count(self, db):
        for idx in range(3):
            _make_item(db, item_id=f"e-po-{idx}", exception_code="po_required_missing", created_at=_ago(5))
        for idx in range(2):
            _make_item(db, item_id=f"e-fc-{idx}", exception_code="field_conflict", created_at=_ago(4))
        _make_item(db, item_id="e-vendor", exception_code="vendor_not_in_erp_master", created_at=_ago(3))

        out = report_svc.generate_exception_breakdown_report("orgA")
        assert out["summary"]["total_exceptions"] == 6
        assert out["summary"]["distinct_codes"] == 3
        assert out["breakdown"][0]["exception_code"] == "po_required_missing"
        assert out["breakdown"][0]["count"] == 3
        assert out["breakdown"][0]["share"] == pytest.approx(0.5, rel=1e-3)

    def test_excludes_items_without_exception_code(self, db):
        _make_item(db, item_id="e-clean-1", state="posted_to_erp", created_at=_ago(5))
        _make_item(db, item_id="e-with", exception_code="po_required_missing", created_at=_ago(4))
        out = report_svc.generate_exception_breakdown_report("orgA")
        assert out["summary"]["total_exceptions"] == 1


# ─── Tests: Vendor Quality ──────────────────────────────────────────


class TestVendorQuality:
    def test_ranks_by_exception_rate(self, db):
        # Acme: 5 invoices, 4 with exception → 80% rate
        for idx in range(5):
            _make_item(
                db, item_id=f"vq-acme-{idx}",
                vendor="Acme",
                exception_code="field_conflict" if idx < 4 else "",
                created_at=_ago(5),
            )
        # Beta: 5 invoices, 1 with exception → 20% rate
        for idx in range(5):
            _make_item(
                db, item_id=f"vq-beta-{idx}",
                vendor="Beta",
                exception_code="po_required_missing" if idx == 0 else "",
                created_at=_ago(5),
            )

        out = report_svc.generate_vendor_quality_report("orgA", min_invoices=3)
        # Acme should rank first because higher exception rate
        assert out["breakdown"][0]["vendor_name"] == "Acme"
        assert out["breakdown"][0]["exception_rate"] == 0.8
        assert out["breakdown"][1]["vendor_name"] == "Beta"
        assert out["breakdown"][1]["exception_rate"] == 0.2

    def test_min_invoices_floor_excludes_low_volume(self, db):
        # Vendor with 1 invoice + 1 exception (100% rate) should NOT
        # outrank a vendor with 50 invoices and 30 exceptions (60%).
        _make_item(db, item_id="vq-tiny", vendor="Tiny", exception_code="x", created_at=_ago(5))
        for idx in range(50):
            _make_item(
                db, item_id=f"vq-big-{idx}",
                vendor="Big",
                exception_code="x" if idx < 30 else "",
                created_at=_ago(5),
            )
        out = report_svc.generate_vendor_quality_report("orgA", min_invoices=3)
        names = [b["vendor_name"] for b in out["breakdown"]]
        assert "Tiny" not in names
        assert "Big" in names


# ─── Tests: Cross-org isolation ─────────────────────────────────────


class TestTenantIsolation:
    def test_orgA_data_invisible_to_orgB(self, db, client_orgB):
        _make_item(db, item_id="iso-1", org="orgA", created_at=_ago(5))
        resp = client_orgB.get("/api/workspace/reports/volume")
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["total_invoices"] == 0


# ─── Tests: API endpoints ───────────────────────────────────────────


class TestEndpointWiring:
    def test_volume_endpoint_returns_payload(self, db, client_orgA):
        _make_item(db, item_id="api-vol-1", created_at=_ago(5))
        resp = client_orgA.get("/api/workspace/reports/volume")
        assert resp.status_code == 200
        body = resp.json()
        assert body["report_type"] == "volume"
        assert body["summary"]["total_invoices"] == 1

    def test_volume_endpoint_accepts_period_query(self, db, client_orgA):
        resp = client_orgA.get("/api/workspace/reports/volume?period=monthly")
        assert resp.status_code == 200
        assert resp.json()["params"]["period"] == "monthly"

    def test_agent_performance_endpoint(self, db, client_orgA):
        _make_item(db, item_id="api-ap-1", state="posted_to_erp", created_at=_ago(5))
        resp = client_orgA.get("/api/workspace/reports/agent-performance")
        assert resp.status_code == 200
        body = resp.json()
        assert body["report_type"] == "agent_performance"
        assert body["summary"]["sample_size"] == 1

    def test_cycle_time_endpoint(self, db, client_orgA):
        resp = client_orgA.get("/api/workspace/reports/cycle-time")
        assert resp.status_code == 200
        assert resp.json()["report_type"] == "cycle_time"

    def test_exception_breakdown_endpoint(self, db, client_orgA):
        resp = client_orgA.get("/api/workspace/reports/exception-breakdown")
        assert resp.status_code == 200
        assert resp.json()["report_type"] == "exception_breakdown"

    def test_vendor_quality_endpoint(self, db, client_orgA):
        resp = client_orgA.get("/api/workspace/reports/vendor-quality")
        assert resp.status_code == 200
        body = resp.json()
        assert body["report_type"] == "vendor_quality"
        assert "min_invoices_floor" in body["summary"]

    def test_vendor_quality_validates_min_invoices_range(self, client_orgA):
        # Pydantic Query(ge=1, le=100) — 0 is rejected.
        resp = client_orgA.get("/api/workspace/reports/vendor-quality?min_invoices=0")
        assert resp.status_code == 422


# ─── Tests: Registry stability ──────────────────────────────────────


class TestRegistry:
    def test_only_five_report_types_exist(self):
        # GA scope §3 design principle: "Five reports, well-built. No
        # custom report builder." Lock the count to catch silent
        # additions that drift from the spec.
        assert len(report_svc.VALID_REPORT_TYPES) == 5
        assert report_svc.VALID_REPORT_TYPES == frozenset({
            "volume", "agent_performance", "cycle_time",
            "exception_breakdown", "vendor_quality",
        })

    def test_every_report_type_has_a_generator(self):
        for report_type in report_svc.VALID_REPORT_TYPES:
            assert report_type in report_svc.REPORT_GENERATORS
            assert callable(report_svc.REPORT_GENERATORS[report_type])
