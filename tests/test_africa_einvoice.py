"""Tests for Wave 4 / F4 — Africa e-invoice format generators.

Covers:
  * Nigeria FIRS payload — supplier/customer TIN, line items with
    HS code, totals.
  * Kenya KRA eTIMS — TIN, BhfId, ItemList shape, Tax* fields,
    receipt type code (S/R).
  * South Africa SARS — issuer tax_number + branch_code, document
    block, lines + totals.
  * Dispatcher: NG/KE/ZA route correctly; unsupported country
    raises ValueError.
  * AP-item helper: derives net/vat/rate from the AP item; reads
    issuer_tax_id from settings_json["tax"]["tax_number"].
  * API: preview, AP-item generate, cross-org 404, unsupported
    country 400.
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

from clearledgr.api import africa_einvoice as africa_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.africa_einvoice import (  # noqa: E402
    AfricaEInvoiceContext,
    AfricaEInvoiceLine,
    build_africa_einvoice,
    build_einvoice_from_ap_item,
    build_etims_einvoice,
    build_firs_einvoice,
    build_sars_einvoice,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgNG", organization_name="Acme Nigeria Ltd")
    inst.ensure_organization("orgKE", organization_name="Acme Kenya Ltd")
    inst.ensure_organization("orgZA", organization_name="Acme SA Ltd")
    inst.ensure_organization("orgB", organization_name="Other")
    inst.update_organization(
        "orgNG", settings={"tax": {"tax_number": "12345678-0001", "country": "NG"}},
    )
    inst.update_organization(
        "orgKE",
        settings={"tax": {"tax_number": "P051234567X", "branch_code": "01"}},
    )
    inst.update_organization(
        "orgZA",
        settings={"tax": {"tax_number": "4123456789", "branch_code": "0001"}},
    )
    return inst


def _user(org: str) -> SimpleNamespace:
    return SimpleNamespace(
        user_id="user-1", email="op@example.com",
        organization_id=org, role="user",
    )


def _client_for(db, org: str) -> TestClient:
    app = FastAPI()
    app.include_router(africa_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user(org)
    return TestClient(app)


def _make_ap_item(
    db, *, item_id: str, org: str, country: str,
    gross: float = 116.0, net: float = 100.0, vat: float = 16.0,
    rate: float = 16.0, currency: str = "KES",
) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Vendor X Ltd",
        "amount": gross,
        "currency": currency,
        "invoice_number": f"INV-{item_id}",
        "state": "received",
    })
    db.update_ap_item(
        item["id"],
        net_amount=Decimal(str(net)),
        vat_amount=Decimal(str(vat)),
        vat_rate=Decimal(str(rate)),
        vat_code="T1",
        tax_treatment="domestic",
        bill_country=country,
    )
    return db.get_ap_item(item["id"])


def _ctx(country: str, **overrides) -> AfricaEInvoiceContext:
    base = dict(
        issuer_name="Acme Issuer Ltd",
        issuer_tax_id="ISSUER-TIN",
        issuer_country=country,
        customer_name="Vendor X",
        customer_tax_id="CUST-TIN",
        customer_country=country,
        document_id="INV-001",
        document_type="invoice",
        issue_date="2026-04-29",
        currency={"NG": "NGN", "KE": "KES", "ZA": "ZAR"}.get(country, "USD"),
    )
    base.update(overrides)
    return AfricaEInvoiceContext(**base)


def _line(amount: float = 100.0, tax: float = 16.0, rate: float = 16.0):
    return AfricaEInvoiceLine(
        description="Server hosting",
        quantity=Decimal("1"),
        unit_price=Decimal(str(amount)),
        line_amount=Decimal(str(amount)),
        tax_amount=Decimal(str(tax)),
        tax_rate=Decimal(str(rate)),
        item_code="SH-100",
        hs_code="998314",
    )


# ─── Nigeria FIRS ──────────────────────────────────────────────────


def test_firs_payload_shape():
    payload = build_firs_einvoice(
        context=_ctx("NG", currency="NGN"),
        lines=[_line(100.0, 7.5, 7.5)],
        total_amount=Decimal("107.50"),
        total_tax=Decimal("7.50"),
    )
    assert payload["invoice_type"] == "STANDARD"
    assert payload["currency"] == "NGN"
    assert payload["supplier"]["country_code"] == "NG"
    assert payload["supplier"]["tin"] == "ISSUER-TIN"
    assert payload["totals"]["total_inclusive"] == 107.50
    assert payload["totals"]["total_tax"] == 7.50
    assert payload["totals"]["total_excluding_tax"] == 100.0
    assert len(payload["line_items"]) == 1
    assert payload["line_items"][0]["hs_code"] == "998314"
    assert payload["metadata"]["format_version"].startswith("FIRS-EI")


def test_firs_credit_note_shape():
    ctx = _ctx("NG", currency="NGN", document_type="credit_note",
               document_id="CN-1", reference_document_id="INV-1")
    payload = build_firs_einvoice(
        context=ctx,
        lines=[_line(50.0, 0.0, 0.0)],
        total_amount=Decimal("50.00"),
        total_tax=Decimal("0.00"),
    )
    assert payload["invoice_type"] == "CREDIT_NOTE"
    assert payload["invoice_reference"] == "INV-1"


# ─── Kenya KRA eTIMS ───────────────────────────────────────────────


def test_etims_payload_shape():
    payload = build_etims_einvoice(
        context=_ctx("KE", issuer_branch_code="01"),
        lines=[_line(100.0, 16.0, 16.0)],
        total_amount=Decimal("116.00"),
        total_tax=Decimal("16.00"),
    )
    assert payload["Tin"] == "ISSUER-TIN"
    assert payload["BhfId"] == "01"
    assert payload["InvcNo"] == "INV-001"
    assert payload["RcptTyCd"] == "S"
    assert payload["Currency"] == "KES"
    assert payload["TotAmt"] == 116.00
    assert payload["TotTaxAmt"] == 16.00
    assert payload["TaxblAmtB"] == 100.00  # KE standard rate slot
    assert payload["TotItemCnt"] == 1
    assert len(payload["ItemList"]) == 1
    assert payload["ItemList"][0]["ItemCd"] == "SH-100"


def test_etims_credit_note_receipt_type():
    ctx = _ctx("KE", issuer_branch_code="01",
               document_type="credit_note", document_id="CN-1",
               reference_document_id="INV-1")
    payload = build_etims_einvoice(
        context=ctx,
        lines=[_line(100.0, 16.0, 16.0)],
        total_amount=Decimal("116.00"),
        total_tax=Decimal("16.00"),
    )
    assert payload["RcptTyCd"] == "R"
    assert payload["OrgInvcNo"] == "INV-1"


# ─── South Africa SARS ────────────────────────────────────────────


def test_sars_payload_shape():
    payload = build_sars_einvoice(
        context=_ctx("ZA", issuer_branch_code="0001"),
        lines=[_line(100.0, 15.0, 15.0)],
        total_amount=Decimal("115.00"),
        total_tax=Decimal("15.00"),
    )
    assert payload["schema"].startswith("SARS-EI")
    assert payload["submission_type"] == "INVOICE"
    assert payload["issuer"]["tax_number"] == "ISSUER-TIN"
    assert payload["issuer"]["branch_code"] == "0001"
    assert payload["issuer"]["country_code"] == "ZA"
    assert payload["tax"]["rate"] == 15.0
    assert payload["totals"]["total"] == 115.00
    assert payload["totals"]["vat"] == 15.00


# ─── Dispatcher ────────────────────────────────────────────────────


def test_dispatcher_routes_by_country():
    ng = build_africa_einvoice(
        country_code="NG", context=_ctx("NG", currency="NGN"),
        lines=[_line()], total_amount=Decimal("116"), total_tax=Decimal("16"),
    )
    ke = build_africa_einvoice(
        country_code="KE", context=_ctx("KE"),
        lines=[_line()], total_amount=Decimal("116"), total_tax=Decimal("16"),
    )
    za = build_africa_einvoice(
        country_code="ZA", context=_ctx("ZA"),
        lines=[_line()], total_amount=Decimal("115"), total_tax=Decimal("15"),
    )
    assert "supplier" in ng       # FIRS uses 'supplier'
    assert "Tin" in ke             # eTIMS uses 'Tin'
    assert "issuer" in za          # SARS uses 'issuer'


def test_dispatcher_rejects_unsupported_country():
    with pytest.raises(ValueError):
        build_africa_einvoice(
            country_code="BR", context=_ctx("BR"),
            lines=[_line()], total_amount=Decimal("100"), total_tax=Decimal("0"),
        )


# ─── AP-item helper ────────────────────────────────────────────────


def test_build_from_ap_item_pulls_org_tax_number(db):
    item = _make_ap_item(
        db, item_id="AP-af-ke", org="orgKE", country="KE",
        currency="KES", gross=116.0, net=100.0, vat=16.0, rate=16.0,
    )
    org = db.get_organization("orgKE")
    payload = build_einvoice_from_ap_item(
        country_code="KE", ap_item=item, organization=org,
    )
    assert payload["Tin"] == "P051234567X"
    assert payload["BhfId"] == "01"
    assert payload["TotAmt"] == 116.00


def test_build_from_ap_item_ng(db):
    item = _make_ap_item(
        db, item_id="AP-af-ng", org="orgNG", country="NG",
        currency="NGN", gross=107.5, net=100.0, vat=7.5, rate=7.5,
    )
    org = db.get_organization("orgNG")
    payload = build_einvoice_from_ap_item(
        country_code="NG", ap_item=item, organization=org,
    )
    assert payload["currency"] == "NGN"
    assert payload["supplier"]["tin"] == "12345678-0001"
    assert payload["totals"]["total_inclusive"] == 107.5


# ─── API ───────────────────────────────────────────────────────────


def test_api_preview_ng(db):
    client = _client_for(db, "orgNG")
    resp = client.post(
        "/api/workspace/africa-einvoice/preview?country=NG",
        json={
            "issuer_name": "Acme Nigeria Ltd",
            "issuer_tax_id": "12345678-0001",
            "document_id": "INV-API-1",
            "currency": "NGN",
            "lines": [{
                "description": "Server hosting",
                "quantity": 1, "unit_price": 100, "line_amount": 100,
                "tax_amount": 7.5, "tax_rate": 7.5,
            }],
            "total_amount": 107.5,
            "total_tax": 7.5,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["currency"] == "NGN"
    assert data["totals"]["total_inclusive"] == 107.5


def test_api_preview_unsupported_country_400(db):
    client = _client_for(db, "orgNG")
    resp = client.post(
        "/api/workspace/africa-einvoice/preview?country=US",
        json={
            "issuer_name": "X", "issuer_tax_id": "Y",
            "document_id": "1", "currency": "USD",
            "lines": [], "total_amount": 0, "total_tax": 0,
        },
    )
    assert resp.status_code == 400


def test_api_ap_item_generate(db):
    client = _client_for(db, "orgKE")
    item = _make_ap_item(
        db, item_id="AP-af-api-ke", org="orgKE", country="KE",
        currency="KES",
    )
    resp = client.post(
        f"/api/workspace/ap-items/{item['id']}/africa-einvoice?country=KE",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["Tin"] == "P051234567X"


def test_api_ap_item_cross_org_404(db):
    client = _client_for(db, "orgNG")
    item = _make_ap_item(
        db, item_id="AP-af-cross", org="orgB", country="NG",
        currency="NGN",
    )
    resp = client.post(
        f"/api/workspace/ap-items/{item['id']}/africa-einvoice?country=NG",
    )
    assert resp.status_code == 404
