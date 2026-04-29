"""Tests for Wave 3 / E2 — VAT calculator + return computation.

Covers:
  * calculate_vat() treatments:
      - Domestic UK 20%: net + VAT split correct, vat_code=T1.
      - Domestic zero rate (rate=0): vat=0, vat_code=T0.
      - Reverse charge (intra-EU B2B with VAT id): net=gross,
        vat = self-assessed at buyer's rate, vat_code=RC.
      - Cross-border without VAT id: zero_rated.
      - Cross-EU/non-EU: zero_rated.
      - Operator override forces treatment regardless of geography.
  * Bill-level persistence: recalculating an AP item writes
    net/vat/rate/code/treatment/bill_country.
  * VAT return rollup:
      - Box 4 (input reclaim) sums domestic + RC vat.
      - Box 1 (output) sums RC vat.
      - Box 7 (purchases ex-VAT) sums all non-OOS net.
      - Box 9 (EU acquisitions) sums RC net.
      - Box 5 = Box 3 - Box 4.
  * Return supersession: re-computing the same period flips the
    prior draft to 'superseded'.
  * Submission flow: draft -> submitted with reference; non-draft
    rejected.
  * API: preview, recalculate, compute, list, submit.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import vat as vat_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.vat_calculator import (  # noqa: E402
    calculate_vat,
)
from clearledgr.services.vat_return import (  # noqa: E402
    compute_and_persist_vat_return,
    compute_vat_return_boxes,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta DE GmbH")
    inst.update_organization(
        "orgA", settings={"tax": {"home_country": "GB"}},
    )
    inst.update_organization(
        "orgB", settings={"tax": {"home_country": "DE"}},
    )
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="user-1", email="op@orgA.com",
        organization_id=org, role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(vat_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


def _make_posted_ap_item(
    db, *, item_id: str, amount: float = 1200.0,
    org: str = "orgA", invoice_date: str = "2026-04-15",
    bill_country: str = "GB",
) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Vendor X",
        "amount": amount,
        "currency": "GBP",
        "invoice_date": invoice_date,
        "state": "received",
        "bill_country": bill_country,
    })
    for s in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp",
    ):
        db.update_ap_item(item["id"], state=s)
    return db.get_ap_item(item["id"])


# ─── calculate_vat() — treatments ───────────────────────────────────


def test_domestic_uk_20pct_split():
    r = calculate_vat(
        gross_amount="120.00",
        home_country="GB",
        bill_country="GB",
    )
    assert r.tax_treatment == "domestic"
    assert r.vat_code == "T1"
    assert r.vat_rate == Decimal("20.000")
    assert r.net_amount == Decimal("100.00")
    assert r.vat_amount == Decimal("20.00")


def test_domestic_de_19pct_split():
    r = calculate_vat(
        gross_amount="119.00",
        home_country="DE",
        bill_country="DE",
    )
    assert r.tax_treatment == "domestic"
    assert r.net_amount == Decimal("100.00")
    assert r.vat_amount == Decimal("19.00")


def test_zero_rated_when_rate_override_zero():
    r = calculate_vat(
        gross_amount="200.00",
        home_country="GB",
        bill_country="GB",
        rate_override=0,
    )
    # Domestic, rate=0 → T0 zero-rated supply
    assert r.vat_code == "T0"
    assert r.vat_amount == Decimal("0.00")
    assert r.net_amount == Decimal("200.00")


def test_intra_eu_b2b_reverse_charge():
    """UK org buying from a German B2B supplier with valid VAT id —
    reverse charge applies. UK is a special case post-Brexit (now
    treated as cross-border zero-rated for goods+services). Test the
    intra-EU case instead: DE org buying from FR supplier."""
    r = calculate_vat(
        gross_amount="1000.00",
        home_country="DE",
        bill_country="FR",
        seller_has_vat_id=True,
    )
    assert r.tax_treatment == "reverse_charge"
    assert r.vat_code == "RC"
    # Net = gross, vat self-assessed at home rate (DE 19%)
    assert r.net_amount == Decimal("1000.00")
    assert r.vat_rate == Decimal("19.000")
    assert r.vat_amount == Decimal("190.00")


def test_intra_eu_seller_without_vat_id_falls_back_to_zero_rated():
    r = calculate_vat(
        gross_amount="500.00",
        home_country="DE",
        bill_country="FR",
        seller_has_vat_id=False,
    )
    assert r.tax_treatment == "zero_rated"
    assert r.vat_amount == Decimal("0.00")


def test_cross_border_uk_eu_post_brexit_zero_rated():
    """UK ↔ EU after Brexit: not intra-EU, treated as cross-border."""
    r = calculate_vat(
        gross_amount="500.00",
        home_country="GB",
        bill_country="DE",
        seller_has_vat_id=True,
    )
    assert r.tax_treatment == "zero_rated"


def test_treatment_override_forces_exempt():
    r = calculate_vat(
        gross_amount="500.00",
        home_country="GB",
        bill_country="GB",
        treatment_override="exempt",
    )
    assert r.tax_treatment == "exempt"
    assert r.vat_code == "T2"
    assert r.vat_amount == Decimal("0.00")


def test_treatment_override_out_of_scope():
    r = calculate_vat(
        gross_amount="100.00",
        home_country="GB",
        bill_country="US",
        treatment_override="out_of_scope",
    )
    assert r.tax_treatment == "out_of_scope"
    assert r.vat_code == "OO"


def test_invalid_amount_raises():
    with pytest.raises(ValueError):
        calculate_vat(
            gross_amount="not-a-number",
            home_country="GB",
            bill_country="GB",
        )


# ─── AP item persistence ────────────────────────────────────────────


def test_recalculate_writes_split_to_ap_item(db, client_orgA):
    item = _make_posted_ap_item(
        db, item_id="AP-vat-1", amount=120.0, bill_country="GB",
    )
    resp = client_orgA.post(
        f"/api/workspace/ap-items/{item['id']}/vat-recalculate",
        json={"gross_amount": 120.0, "bill_country": "GB"},
    )
    assert resp.status_code == 200, resp.text
    fresh = db.get_ap_item(item["id"])
    assert fresh["net_amount"] == Decimal("100.00")
    assert fresh["vat_amount"] == Decimal("20.00")
    assert fresh["tax_treatment"] == "domestic"
    assert fresh["vat_code"] == "T1"
    assert fresh["bill_country"] == "GB"


def test_recalculate_cross_org_404(db, client_orgA):
    item_b = _make_posted_ap_item(
        db, item_id="AP-vat-cross", amount=100.0, org="orgB",
    )
    resp = client_orgA.post(
        f"/api/workspace/ap-items/{item_b['id']}/vat-recalculate",
        json={"gross_amount": 100.0, "bill_country": "DE"},
    )
    assert resp.status_code == 404


# ─── VAT return rollup ──────────────────────────────────────────────


def _seed_period_bills(db, *, org: str = "orgA"):
    """Set up: 2 domestic UK + 1 RC + 1 zero-rated within the period."""
    for idx, (treatment, gross, country, vat) in enumerate([
        ("domestic", 120.0, "GB", 20.0),
        ("domestic", 240.0, "GB", 40.0),
        ("reverse_charge", 1000.0, "FR", 200.0),
        ("zero_rated", 500.0, "DE", 0.0),
    ]):
        item = db.create_ap_item({
            "id": f"AP-rollup-{idx}",
            "organization_id": org,
            "vendor_name": f"V{idx}",
            "amount": gross,
            "currency": "GBP",
            "invoice_date": "2026-04-15",
            "state": "received",
        })
        for s in (
            "validated", "needs_approval", "approved",
            "ready_to_post", "posted_to_erp",
        ):
            db.update_ap_item(item["id"], state=s)
        # Now write the VAT split.
        if treatment == "domestic":
            net = gross - vat
        else:
            net = gross
        db.update_ap_item(
            item["id"],
            net_amount=Decimal(str(net)),
            vat_amount=Decimal(str(vat)),
            vat_rate=Decimal("20.000") if treatment != "zero_rated" else Decimal("0.000"),
            vat_code={
                "domestic": "T1", "reverse_charge": "RC", "zero_rated": "T0",
            }[treatment],
            tax_treatment=treatment,
            bill_country=country,
        )


def test_vat_return_box_rollup(db):
    _seed_period_bills(db, org="orgA")
    boxes = compute_vat_return_boxes(
        db,
        organization_id="orgA",
        period_start="2026-04-01",
        period_end="2026-04-30",
    )
    # Box 4 = domestic vat (20+40) + RC vat (200) = 260
    assert boxes["box4_vat_reclaimed"] == Decimal("260.00")
    # Box 1 = RC vat (200)
    assert boxes["box1_vat_due_on_sales"] == Decimal("200.00")
    # Box 7 = all non-OOS net = 100 + 200 + 1000 + 500 = 1800
    assert boxes["box7_total_purchases_ex_vat"] == Decimal("1800.00")
    # Box 9 = RC net = 1000
    assert boxes["box9_total_eu_purchases"] == Decimal("1000.00")
    # Box 5 = Box 3 - Box 4 = 200 - 260 = -60 (refund due)
    assert boxes["box5_net_vat_payable"] == Decimal("-60.00")


def test_vat_return_excludes_period_outliers(db):
    """Bills outside the period must not contribute."""
    item = db.create_ap_item({
        "id": "AP-out-of-period",
        "organization_id": "orgA",
        "vendor_name": "V-out",
        "amount": 999.0, "currency": "GBP",
        "invoice_date": "2026-02-15",
        "state": "received",
    })
    for s in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp",
    ):
        db.update_ap_item(item["id"], state=s)
    db.update_ap_item(
        item["id"],
        net_amount=Decimal("832.50"),
        vat_amount=Decimal("166.50"),
        vat_rate=Decimal("20.000"),
        vat_code="T1",
        tax_treatment="domestic",
        bill_country="GB",
    )
    boxes = compute_vat_return_boxes(
        db, organization_id="orgA",
        period_start="2026-04-01", period_end="2026-04-30",
    )
    assert boxes["box4_vat_reclaimed"] == Decimal("0.00")


def test_vat_return_supersedes_prior_draft(db):
    _seed_period_bills(db, org="orgA")
    first = compute_and_persist_vat_return(
        db, organization_id="orgA",
        period_start="2026-04-01", period_end="2026-04-30",
        jurisdiction="GB", currency="GBP",
    )
    second = compute_and_persist_vat_return(
        db, organization_id="orgA",
        period_start="2026-04-01", period_end="2026-04-30",
        jurisdiction="GB", currency="GBP",
    )
    assert first["id"] != second["id"]
    # The first one's status should now be 'superseded'.
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT status FROM vat_returns WHERE id = %s", (first["id"],),
        )
        row = cur.fetchone()
    assert row["status"] == "superseded"


# ─── API ────────────────────────────────────────────────────────────


def test_api_preview_pure_compute(db, client_orgA):
    resp = client_orgA.post(
        "/api/workspace/vat/preview",
        json={
            "gross_amount": 120.0, "bill_country": "GB",
            "seller_has_vat_id": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tax_treatment"] == "domestic"
    assert data["net_amount"] == 100.0
    assert data["vat_amount"] == 20.0


def test_api_preview_invalid_amount_400(client_orgA):
    resp = client_orgA.post(
        "/api/workspace/vat/preview",
        json={"gross_amount": "abc", "bill_country": "GB"},
    )
    assert resp.status_code in (400, 422)


def test_api_compute_then_list_then_submit(db, client_orgA):
    _seed_period_bills(db, org="orgA")
    compute_resp = client_orgA.post(
        "/api/workspace/vat-returns/compute",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-30",
            "jurisdiction": "GB",
            "currency": "GBP",
        },
    )
    assert compute_resp.status_code == 200
    return_id = compute_resp.json()["id"]

    list_resp = client_orgA.get("/api/workspace/vat-returns")
    assert list_resp.status_code == 200
    assert any(r["id"] == return_id for r in list_resp.json())

    submit_resp = client_orgA.post(
        f"/api/workspace/vat-returns/{return_id}/submit",
        json={"submission_reference": "HMRC-RECEIPT-2026-Q1"},
    )
    assert submit_resp.status_code == 200
    assert submit_resp.json()["status"] == "submitted"
    assert submit_resp.json()["submission_reference"] == "HMRC-RECEIPT-2026-Q1"


def test_api_submit_non_draft_400(db, client_orgA):
    _seed_period_bills(db, org="orgA")
    compute_resp = client_orgA.post(
        "/api/workspace/vat-returns/compute",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-30",
            "jurisdiction": "GB",
        },
    )
    return_id = compute_resp.json()["id"]
    client_orgA.post(
        f"/api/workspace/vat-returns/{return_id}/submit",
        json={"submission_reference": "HMRC-1"},
    )
    # Re-submit should fail because it's no longer draft.
    resp = client_orgA.post(
        f"/api/workspace/vat-returns/{return_id}/submit",
        json={"submission_reference": "HMRC-2"},
    )
    assert resp.status_code == 400


def test_api_get_return_cross_org_404(db, client_orgA):
    _seed_period_bills(db, org="orgB")
    row = compute_and_persist_vat_return(
        db, organization_id="orgB",
        period_start="2026-04-01", period_end="2026-04-30",
        jurisdiction="DE",
    )
    resp = client_orgA.get(f"/api/workspace/vat-returns/{row['id']}")
    assert resp.status_code == 404
