"""Tests for Wave 4 / F3 — country-specific VAT return form mapping.

Covers:
  * map_to_country_form per jurisdiction:
      GB — identity 9-box layout.
      DE — Kz67 (RC services), Kz66 (input VAT), Kz83 (net payable).
      NL — 4a/5a/5b/5c rubrieken.
      FR — lines 17 / 19 / 28.
  * canonical_boxes echo preserved on every mapping for audit.
  * Unsupported jurisdiction raises ValueError.
  * API: GET /vat-returns/{id}/form, jurisdiction override, 4xx on
    unsupported, cross-org 404.
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
from clearledgr.services.vat_return import (  # noqa: E402
    compute_and_persist_vat_return,
)
from clearledgr.services.vat_return_forms import (  # noqa: E402
    map_to_country_form,
    supported_jurisdictions,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta DE GmbH")
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


_SAMPLE_BOXES = {
    "box1_vat_due_on_sales": Decimal("200.00"),         # RC self-assessed
    "box2_vat_due_on_acquisitions": Decimal("0.00"),
    "box3_total_vat_due": Decimal("200.00"),
    "box4_vat_reclaimed": Decimal("260.00"),            # 60 dom + 200 RC
    "box5_net_vat_payable": Decimal("-60.00"),          # refund
    "box6_total_sales_ex_vat": Decimal("0.00"),
    "box7_total_purchases_ex_vat": Decimal("1800.00"),
    "box8_total_eu_sales": Decimal("0.00"),
    "box9_total_eu_purchases": Decimal("1000.00"),
}


# ─── Mapper: GB (identity) ──────────────────────────────────────────


def test_map_gb_returns_9_box_layout():
    out = map_to_country_form(_SAMPLE_BOXES, jurisdiction="GB", currency="GBP")
    assert out["jurisdiction"] == "GB"
    assert out["form_name"] == "VAT Return"
    codes = [f["code"] for f in out["fields"]]
    assert codes == ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
    box5 = next(f for f in out["fields"] if f["code"] == "5")
    assert box5["amount"] == -60.0
    assert out["summary"]["net_vat_payable"] == -60.0
    assert out["summary"]["currency"] == "GBP"


# ─── Mapper: DE UStVA ───────────────────────────────────────────────


def test_map_de_uses_ustva_codes():
    out = map_to_country_form(_SAMPLE_BOXES, jurisdiction="DE", currency="EUR")
    assert out["jurisdiction"] == "DE"
    assert out["form_name"] == "UStVA"
    codes = {f["code"]: f for f in out["fields"]}
    assert codes["Kz67"]["amount"] == 200.0   # RC services output
    assert codes["Kz66"]["amount"] == 260.0   # input VAT reclaim
    assert codes["Kz83"]["amount"] == -60.0   # net payable
    assert codes["Kz93"]["amount"] == 1000.0  # EU acquisitions base
    assert codes["Kz62"]["amount"] == 1800.0  # purchases ex VAT
    assert "canonical_boxes" in out


# ─── Mapper: NL BTW ────────────────────────────────────────────────


def test_map_nl_uses_btw_rubrieken():
    out = map_to_country_form(_SAMPLE_BOXES, jurisdiction="NL", currency="EUR")
    assert out["jurisdiction"] == "NL"
    assert out["form_name"] == "BTW-aangifte"
    codes = {f["code"]: f for f in out["fields"]}
    assert codes["4a_belast"]["amount"] == 1000.0  # EU acquisitions base
    assert codes["4a_btw"]["amount"] == 200.0       # output VAT (RC)
    assert codes["5a"]["amount"] == 200.0           # total output VAT
    assert codes["5b"]["amount"] == 260.0           # input VAT to deduct
    assert codes["5c"]["amount"] == -60.0           # net VAT due


# ─── Mapper: FR CA3 ────────────────────────────────────────────────


def test_map_fr_uses_ca3_lines():
    out = map_to_country_form(_SAMPLE_BOXES, jurisdiction="FR", currency="EUR")
    assert out["jurisdiction"] == "FR"
    assert out["form_name"] == "CA3"
    codes = {f["code"]: f for f in out["fields"]}
    assert codes["08_base"]["amount"] == 1000.0  # base HT acquisitions
    assert codes["17"]["amount"] == 200.0         # auto-liquidation
    assert codes["19"]["amount"] == 260.0         # TVA déductible
    assert codes["28"]["amount"] == -60.0         # net due / crédit


# ─── Edge cases ─────────────────────────────────────────────────────


def test_unsupported_jurisdiction_raises():
    with pytest.raises(ValueError):
        map_to_country_form(_SAMPLE_BOXES, jurisdiction="ZA", currency="ZAR")


def test_jurisdiction_lowercase_normalised():
    out = map_to_country_form(_SAMPLE_BOXES, jurisdiction="de", currency="EUR")
    assert out["jurisdiction"] == "DE"


def test_canonical_boxes_echo_preserved():
    out = map_to_country_form(_SAMPLE_BOXES, jurisdiction="DE", currency="EUR")
    assert "canonical_boxes" in out
    assert out["canonical_boxes"]["box4_vat_reclaimed"] == 260.0


def test_supported_list():
    assert "GB" in supported_jurisdictions()
    assert "DE" in supported_jurisdictions()
    assert "NL" in supported_jurisdictions()
    assert "FR" in supported_jurisdictions()


# ─── API ────────────────────────────────────────────────────────────


def _seed_period_with_rc_bills(db, *, org: str = "orgA"):
    """Stand up enough AP items in a period so a vat_return row has
    non-zero boxes."""
    for idx, (treatment, gross, country, vat) in enumerate([
        ("domestic", 360.0, "GB", 60.0),
        ("reverse_charge", 1000.0, "FR", 200.0),
        ("zero_rated", 500.0, "DE", 0.0),
    ]):
        item = db.create_ap_item({
            "id": f"AP-form-{org}-{idx}",
            "organization_id": org,
            "vendor_name": f"V-form-{idx}",
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
        net = gross - vat if treatment == "domestic" else gross
        db.update_ap_item(
            item["id"],
            net_amount=Decimal(str(net)),
            vat_amount=Decimal(str(vat)),
            vat_rate=Decimal("20.000") if treatment != "zero_rated" else Decimal("0.000"),
            vat_code={"domestic": "T1", "reverse_charge": "RC", "zero_rated": "T0"}[treatment],
            tax_treatment=treatment,
            bill_country=country,
        )


def _persist_return(db, org: str = "orgA", jurisdiction: str = "GB") -> str:
    _seed_period_with_rc_bills(db, org=org)
    row = compute_and_persist_vat_return(
        db, organization_id=org,
        period_start="2026-04-01", period_end="2026-04-30",
        jurisdiction=jurisdiction, currency="GBP",
    )
    return row["id"]


def test_api_get_form_default_jurisdiction(db, client_orgA):
    return_id = _persist_return(db, jurisdiction="GB")
    resp = client_orgA.get(
        f"/api/workspace/vat-returns/{return_id}/form",
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["jurisdiction"] == "GB"
    assert data["form_name"] == "VAT Return"


def test_api_get_form_jurisdiction_override(db, client_orgA):
    return_id = _persist_return(db, jurisdiction="GB")
    resp = client_orgA.get(
        f"/api/workspace/vat-returns/{return_id}/form?jurisdiction=DE",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["jurisdiction"] == "DE"
    assert data["form_name"] == "UStVA"


def test_api_get_form_unsupported_400(db, client_orgA):
    return_id = _persist_return(db, jurisdiction="GB")
    resp = client_orgA.get(
        f"/api/workspace/vat-returns/{return_id}/form?jurisdiction=ZZ",
    )
    assert resp.status_code == 400


def test_api_get_form_cross_org_404(db, client_orgA):
    return_id = _persist_return(db, org="orgB", jurisdiction="DE")
    resp = client_orgA.get(
        f"/api/workspace/vat-returns/{return_id}/form",
    )
    assert resp.status_code == 404
