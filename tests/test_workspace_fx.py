"""Tests for Module 9 — FX rates store + conversion service + API.

Pinned by these tests:

  - Store: upsert is idempotent on (org, from, to, as_of, source).
    Manual rate beats ERP rate when both exist for the same date.
  - find_fx_rate returns the latest rate where as_of_date <= the
    requested date.
  - Conversion: identity / direct / inverse / triangulation paths.
    None when no rate available.
  - API: rate CRUD with cross-tenant isolation; convert preview;
    functional currency report.
  - Volume report integration: cross-currency invoices roll up to
    the org's functional currency. Unconverted rows surface in
    summary.unconverted.
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

from clearledgr.api import fx_rates as fx_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services import workspace_fx  # noqa: E402
from clearledgr.services import workspace_reports  # noqa: E402


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
        user_id=f"leader@{org}.com",
        email=f"leader@{org}.com",
        organization_id=org,
        role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(fx_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


@pytest.fixture()
def client_orgB(db):
    app = FastAPI()
    app.include_router(fx_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgB")
    return TestClient(app)


# ─── Tests: Store ───────────────────────────────────────────────────


class TestStore:
    def test_upsert_creates_then_updates(self, db):
        a = db.upsert_fx_rate({
            "organization_id": "orgA",
            "from_currency": "EUR", "to_currency": "USD",
            "rate": 1.10, "as_of_date": "2026-04-01",
        })
        assert a["rate"] == 1.10
        b = db.upsert_fx_rate({
            "organization_id": "orgA",
            "from_currency": "EUR", "to_currency": "USD",
            "rate": 1.11, "as_of_date": "2026-04-01",
            "note": "second update",
        })
        # Same id (UNIQUE on org+from+to+as_of+source); rate updated.
        assert a["id"] == b["id"]
        assert b["rate"] == 1.11
        assert b["note"] == "second update"

    def test_invalid_currency_rejected(self, db):
        with pytest.raises(ValueError):
            db.upsert_fx_rate({
                "organization_id": "orgA",
                "from_currency": "USDX", "to_currency": "EUR", "rate": 1.0,
            })

    def test_invalid_rate_rejected(self, db):
        with pytest.raises(ValueError):
            db.upsert_fx_rate({
                "organization_id": "orgA",
                "from_currency": "EUR", "to_currency": "USD", "rate": -1,
            })

    def test_find_fx_rate_picks_latest_before_or_equal(self, db):
        db.upsert_fx_rate({
            "organization_id": "orgA",
            "from_currency": "EUR", "to_currency": "USD",
            "rate": 1.05, "as_of_date": "2026-01-01",
        })
        db.upsert_fx_rate({
            "organization_id": "orgA",
            "from_currency": "EUR", "to_currency": "USD",
            "rate": 1.10, "as_of_date": "2026-04-01",
        })
        # Asked-for date 2026-03-15 — should pick the Jan rate.
        rate = db.find_fx_rate("orgA", "EUR", "USD", "2026-03-15")
        assert rate["rate"] == 1.05

        # Asked-for date 2026-05-01 — should pick the Apr rate.
        rate = db.find_fx_rate("orgA", "EUR", "USD", "2026-05-01")
        assert rate["rate"] == 1.10

    def test_find_fx_rate_prefers_manual_over_erp_on_same_date(self, db):
        db.upsert_fx_rate({
            "organization_id": "orgA",
            "from_currency": "EUR", "to_currency": "USD",
            "rate": 1.10, "as_of_date": "2026-04-01",
            "source": "erp",
        })
        db.upsert_fx_rate({
            "organization_id": "orgA",
            "from_currency": "EUR", "to_currency": "USD",
            "rate": 1.12, "as_of_date": "2026-04-01",
            "source": "manual",
        })
        rate = db.find_fx_rate("orgA", "EUR", "USD", "2026-04-15")
        assert rate["rate"] == 1.12  # manual won

    def test_cross_org_isolation(self, db):
        db.upsert_fx_rate({
            "organization_id": "orgA",
            "from_currency": "EUR", "to_currency": "USD",
            "rate": 1.10, "as_of_date": "2026-04-01",
        })
        # orgB asks for the same pair — gets nothing.
        rate = db.find_fx_rate("orgB", "EUR", "USD", "2026-04-15")
        assert rate is None


# ─── Tests: Conversion service ──────────────────────────────────────


class TestConversion:
    def test_identity_path(self, db):
        result = workspace_fx.convert(
            db, organization_id="orgA",
            amount=100.0, from_currency="USD", to_currency="USD",
        )
        assert result is not None
        assert result.path == "identity"
        assert result.converted_amount == 100.0
        assert result.rate_used == 1.0

    def test_direct_path(self, db):
        db.upsert_fx_rate({
            "organization_id": "orgA",
            "from_currency": "EUR", "to_currency": "USD",
            "rate": 1.10, "as_of_date": "2026-04-01",
        })
        result = workspace_fx.convert(
            db, organization_id="orgA",
            amount=100.0, from_currency="EUR", to_currency="USD",
            as_of_date="2026-04-15",
        )
        assert result is not None
        assert result.path == "direct"
        assert result.converted_amount == 110.0

    def test_inverse_path(self, db):
        # Only EUR→USD stored; ask for USD→EUR.
        db.upsert_fx_rate({
            "organization_id": "orgA",
            "from_currency": "EUR", "to_currency": "USD",
            "rate": 1.25, "as_of_date": "2026-04-01",
        })
        result = workspace_fx.convert(
            db, organization_id="orgA",
            amount=125.0, from_currency="USD", to_currency="EUR",
            as_of_date="2026-04-15",
        )
        assert result is not None
        assert result.path == "inverse"
        # 125 / 1.25 = 100
        assert result.converted_amount == 100.0

    def test_triangulation_path(self, db):
        # GBP→USD and USD→EUR exist; convert GBP→EUR via USD hub.
        db.upsert_fx_rate({
            "organization_id": "orgA",
            "from_currency": "GBP", "to_currency": "USD",
            "rate": 1.27, "as_of_date": "2026-04-01",
        })
        db.upsert_fx_rate({
            "organization_id": "orgA",
            "from_currency": "USD", "to_currency": "EUR",
            "rate": 0.92, "as_of_date": "2026-04-01",
        })
        result = workspace_fx.convert(
            db, organization_id="orgA",
            amount=100.0, from_currency="GBP", to_currency="EUR",
            as_of_date="2026-04-15",
        )
        assert result is not None
        assert result.path == "triangulated"
        # 100 * 1.27 * 0.92 = 116.84
        assert result.converted_amount == pytest.approx(116.84, rel=1e-3)

    def test_no_rate_returns_none(self, db):
        result = workspace_fx.convert(
            db, organization_id="orgA",
            amount=100.0, from_currency="JPY", to_currency="EUR",
        )
        assert result is None

    def test_functional_currency_default_usd(self, db):
        assert workspace_fx.get_functional_currency(db, "orgA") == "USD"


# ─── Tests: API ─────────────────────────────────────────────────────


class TestAPI:
    def test_post_then_get(self, db, client_orgA):
        resp = client_orgA.post(
            "/api/workspace/fx-rates",
            json={"from_currency": "EUR", "to_currency": "USD",
                  "rate": 1.10, "as_of_date": "2026-04-01"},
        )
        assert resp.status_code == 200
        listed = client_orgA.get("/api/workspace/fx-rates").json()["rates"]
        assert any(r["from_currency"] == "EUR" for r in listed)

    def test_post_identity_pair_rejected(self, client_orgA):
        resp = client_orgA.post(
            "/api/workspace/fx-rates",
            json={"from_currency": "USD", "to_currency": "USD", "rate": 1.0},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "identity_rate_not_allowed"

    def test_convert_preview(self, db, client_orgA):
        client_orgA.post(
            "/api/workspace/fx-rates",
            json={"from_currency": "EUR", "to_currency": "USD",
                  "rate": 1.10, "as_of_date": "2026-04-01"},
        )
        resp = client_orgA.get(
            "/api/workspace/fx-rates/convert",
            params={"amount": 100, "from": "EUR", "to": "USD",
                    "as_of": "2026-04-15"},
        )
        body = resp.json()
        assert body["ok"] is True
        assert body["converted_amount"] == 110.0
        assert body["path"] == "direct"

    def test_convert_no_rate_returns_ok_false(self, client_orgA):
        resp = client_orgA.get(
            "/api/workspace/fx-rates/convert",
            params={"amount": 100, "from": "JPY", "to": "EUR"},
        )
        body = resp.json()
        assert body["ok"] is False

    def test_functional_currency_endpoint_default(self, client_orgA):
        body = client_orgA.get(
            "/api/workspace/fx-rates/functional-currency",
        ).json()
        assert body["functional_currency"] == "USD"

    def test_cross_tenant_isolation_on_list(self, db, client_orgA, client_orgB):
        client_orgA.post(
            "/api/workspace/fx-rates",
            json={"from_currency": "EUR", "to_currency": "USD",
                  "rate": 1.10, "as_of_date": "2026-04-01"},
        )
        b_listed = client_orgB.get("/api/workspace/fx-rates").json()["rates"]
        assert b_listed == []

    def test_delete_rate(self, db, client_orgA):
        created = client_orgA.post(
            "/api/workspace/fx-rates",
            json={"from_currency": "EUR", "to_currency": "USD",
                  "rate": 1.10, "as_of_date": "2026-04-01"},
        ).json()["rate"]
        resp = client_orgA.delete(f"/api/workspace/fx-rates/{created['id']}")
        assert resp.status_code == 200

        listed = client_orgA.get("/api/workspace/fx-rates").json()["rates"]
        assert all(r["id"] != created["id"] for r in listed)


# ─── Tests: Volume report integration ───────────────────────────────


class TestVolumeReportFXIntegration:
    def test_cross_currency_summary_rolls_up_to_functional(self, db):
        # Two invoices, one USD ($100) and one EUR (€100). With a
        # 1.10 EUR→USD rate the EUR row converts to $110. Functional
        # is USD by default → summary.total_amount should be ~210.
        db.upsert_fx_rate({
            "organization_id": "orgFx",
            "from_currency": "EUR", "to_currency": "USD",
            "rate": 1.10, "as_of_date": "2026-01-01",
        })
        db.ensure_organization("orgFx", organization_name="Fx Test")
        # Backdate so the rate's as_of <= invoice created_at.
        for idx, (amount, ccy) in enumerate([(100.0, "USD"), (100.0, "EUR")]):
            db.create_ap_item({
                "id": f"vol-fx-{idx}",
                "organization_id": "orgFx",
                "vendor_name": "Test Vendor",
                "amount": amount,
                "currency": ccy,
                "invoice_number": f"INV-{idx}",
                "state": "received",
                "metadata": {},
            })
        # Backdate created_at for the invoices to keep them inside the
        # default 90-day window (90 day default; test uses now).
        with db.connect() as conn:
            cur = conn.cursor()
            for idx in range(2):
                cur.execute(
                    "UPDATE ap_items SET created_at = %s WHERE id = %s",
                    ((datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
                     f"vol-fx-{idx}"),
                )
            conn.commit()

        report = workspace_reports.generate_volume_report("orgFx")
        assert report["summary"]["currency"] == "USD"
        assert report["summary"]["total_invoices"] == 2
        # 100 USD identity + 100 EUR * 1.10 = 210
        assert report["summary"]["total_amount"] == pytest.approx(210.0, rel=1e-3)
        assert "EUR" in report["summary"]["currencies_seen"]
        assert "USD" in report["summary"]["currencies_seen"]
        assert report["summary"]["unconverted"] == 0

    def test_unconverted_rows_surface_in_summary(self, db):
        # An invoice in JPY with no rate stored → should land in the
        # unconverted bucket count.
        db.ensure_organization("orgFx2", organization_name="Fx Test 2")
        db.create_ap_item({
            "id": "vol-fx-unc",
            "organization_id": "orgFx2",
            "vendor_name": "JP Vendor",
            "amount": 10000.0,
            "currency": "JPY",
            "invoice_number": "INV-JP",
            "state": "received",
            "metadata": {},
        })
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE ap_items SET created_at = %s WHERE id = %s",
                ((datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
                 "vol-fx-unc"),
            )
            conn.commit()

        report = workspace_reports.generate_volume_report("orgFx2")
        assert report["summary"]["unconverted"] >= 1
