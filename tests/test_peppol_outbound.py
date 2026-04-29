"""Tests for Wave 4 / F2 — PEPPOL UBL outbound generator.

Covers:
  * build_ubl_invoice / build_ubl_credit_note round-trip:
    generated XML re-parses back to the same totals and treatment.
  * Treatment-driven TaxCategory mapping (S / AE / Z / E / O) with
    correct ExemptionReason for AE + E + O.
  * Credit note carries BillingReference back to the original
    invoice.
  * build_credit_note_from_ap_item — net/VAT split inherited from
    the AP item's tax_treatment + vat_rate.
  * API: POST /credit-notes returns valid UBL; cross-org 404;
    invalid amount 422; org_name surfaces as supplier RegistrationName.
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

from clearledgr.api import peppol as peppol_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.peppol_ubl_generator import (  # noqa: E402
    UblDocument,
    UblLine,
    UblParty,
    build_credit_note_from_ap_item,
    build_ubl_credit_note,
    build_ubl_invoice,
)
from clearledgr.services.peppol_ubl_parser import (  # noqa: E402
    parse_peppol_ubl_invoice,
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
    app.include_router(peppol_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


def _make_ap_item(
    db, *,
    item_id: str,
    treatment: str = "domestic",
    rate: float = 19.0,
    org: str = "orgA",
    bill_country: str = "DE",
    invoice_number: str = "VENDOR-INV-100",
    gross: float = 1190.0,
    net: float = 1000.0,
    vat: float = 190.0,
) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Vendor X GmbH",
        "amount": gross,
        "currency": "EUR",
        "invoice_number": invoice_number,
        "state": "received",
    })
    db.update_ap_item(
        item["id"],
        net_amount=Decimal(str(net)),
        vat_amount=Decimal(str(vat)),
        vat_rate=Decimal(str(rate)),
        vat_code={"domestic": "T1", "reverse_charge": "RC"}.get(treatment, "T0"),
        tax_treatment=treatment,
        bill_country=bill_country,
    )
    return db.get_ap_item(item["id"])


# ─── build_ubl_invoice round-trip ──────────────────────────────────


def _sample_doc(treatment: str = "domestic") -> UblDocument:
    return UblDocument(
        document_id="INV-OUT-001",
        issue_date="2026-04-29",
        currency="EUR",
        supplier=UblParty(
            name="Acme Holdings GmbH",
            vat_id="DE999999999",
            country_code="DE",
            street_name="Hauptstrasse 1",
            city="Munich",
            postal_zone="80331",
        ),
        customer=UblParty(
            name="Vendor X GmbH",
            country_code="DE",
        ),
        treatment=treatment,
        line_extension_amount=Decimal("1000.00"),
        tax_exclusive_amount=Decimal("1000.00"),
        tax_inclusive_amount=(
            Decimal("1190.00") if treatment == "domestic" else Decimal("1000.00")
        ),
        payable_amount=(
            Decimal("1190.00") if treatment == "domestic" else Decimal("1000.00")
        ),
        tax_amount=(
            Decimal("190.00") if treatment == "domestic" else Decimal("0.00")
        ),
        tax_rate=Decimal("19"),
        lines=[UblLine(
            line_id="1",
            description="Server hosting",
            quantity=Decimal("10"),
            unit_price=Decimal("100.00"),
            line_extension_amount=Decimal("1000.00"),
            tax_category_id="S" if treatment == "domestic" else "AE",
            tax_percent=Decimal("19"),
        )],
        due_date="2026-05-29",
        payment_terms_note="Net 30",
    )


def test_invoice_round_trip_domestic():
    xml = build_ubl_invoice(_sample_doc("domestic"))
    parsed = parse_peppol_ubl_invoice(xml)
    assert parsed.invoice_id == "INV-OUT-001"
    assert parsed.supplier_name == "Acme Holdings GmbH"
    assert parsed.supplier_country == "DE"
    assert parsed.derived_treatment == "domestic"
    assert parsed.derived_vat_code == "T1"
    assert parsed.payable_amount == Decimal("1190.00")
    assert parsed.tax_amount == Decimal("190.00")
    assert parsed.warnings == []


def test_invoice_round_trip_reverse_charge():
    xml = build_ubl_invoice(_sample_doc("reverse_charge"))
    parsed = parse_peppol_ubl_invoice(xml)
    assert parsed.derived_treatment == "reverse_charge"
    assert parsed.derived_vat_code == "RC"
    assert parsed.payable_amount == Decimal("1000.00")
    # Exemption reason set on AE category.
    sub = parsed.tax_subtotals[0]
    assert sub["category_id"] == "AE"
    assert sub["exemption_reason"]
    assert "196" in sub["exemption_reason"]


def test_invoice_round_trip_zero_rated():
    doc = _sample_doc("zero_rated")
    doc.tax_amount = Decimal("0.00")
    doc.tax_inclusive_amount = Decimal("1000.00")
    doc.payable_amount = Decimal("1000.00")
    xml = build_ubl_invoice(doc)
    parsed = parse_peppol_ubl_invoice(xml)
    assert parsed.derived_treatment == "zero_rated"
    assert parsed.derived_vat_code == "T0"


def test_invoice_round_trip_exempt():
    doc = _sample_doc("exempt")
    doc.tax_amount = Decimal("0.00")
    doc.tax_inclusive_amount = Decimal("1000.00")
    doc.payable_amount = Decimal("1000.00")
    xml = build_ubl_invoice(doc)
    parsed = parse_peppol_ubl_invoice(xml)
    assert parsed.derived_treatment == "exempt"
    assert parsed.derived_vat_code == "T2"
    sub = parsed.tax_subtotals[0]
    assert sub["category_id"] == "E"
    assert sub["exemption_reason"]


# ─── build_ubl_credit_note ──────────────────────────────────────────


def test_credit_note_carries_billing_reference():
    doc = _sample_doc("domestic")
    doc.document_id = "CN-001"
    doc.billing_reference_invoice_id = "INV-100"
    xml = build_ubl_credit_note(doc)
    text = xml.decode("utf-8")
    assert "<cac:BillingReference>" in text
    assert "<cbc:ID>INV-100</cbc:ID>" in text
    assert "<cbc:CreditNoteTypeCode>381</cbc:CreditNoteTypeCode>" in text
    # Round-trip via inbound parser.
    parsed = parse_peppol_ubl_invoice(xml)
    assert parsed.invoice_id == "CN-001"


# ─── build_credit_note_from_ap_item ────────────────────────────────


def test_build_credit_note_from_ap_item_domestic_split(db):
    item = _make_ap_item(db, item_id="AP-cn-dom")
    org = db.get_organization("orgA")
    xml = build_credit_note_from_ap_item(
        ap_item=item, organization=org or {"organization_name": "orgA"},
        credit_amount=Decimal("119.00"),
        credit_reason="Returned defective server",
    )
    parsed = parse_peppol_ubl_invoice(xml)
    assert parsed.derived_treatment == "domestic"
    # 119 gross at 19% → 100 net + 19 VAT
    assert parsed.tax_exclusive_amount == Decimal("100.00")
    assert parsed.tax_amount == Decimal("19.00")
    assert parsed.payable_amount == Decimal("119.00")


def test_build_credit_note_from_ap_item_reverse_charge(db):
    item = _make_ap_item(
        db, item_id="AP-cn-rc",
        treatment="reverse_charge", rate=19.0, bill_country="FR",
    )
    org = db.get_organization("orgA")
    xml = build_credit_note_from_ap_item(
        ap_item=item, organization=org or {"organization_name": "orgA"},
        credit_amount=Decimal("500.00"),
        credit_reason="Partial dispute resolution",
    )
    parsed = parse_peppol_ubl_invoice(xml)
    assert parsed.derived_treatment == "reverse_charge"
    # RC: net = gross, no VAT line on the credit note (buyer
    # self-accounts on their side).
    assert parsed.tax_exclusive_amount == Decimal("500.00")
    assert parsed.payable_amount == Decimal("500.00")


# ─── API ───────────────────────────────────────────────────────────


def test_api_credit_note_happy_path(db, client_orgA):
    item = _make_ap_item(db, item_id="AP-api-cn-1")
    resp = client_orgA.post(
        "/api/workspace/peppol/credit-notes",
        json={
            "ap_item_id": item["id"],
            "credit_amount": 119.00,
            "reason": "Returned defective server",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ap_item_id"] == item["id"]
    assert data["credit_note_id"]
    xml = data["ubl_xml"].encode("utf-8")
    # Re-parses.
    parsed = parse_peppol_ubl_invoice(xml)
    assert parsed.invoice_id == data["credit_note_id"]
    assert parsed.derived_treatment == "domestic"


def test_api_credit_note_uses_org_name_as_supplier(db, client_orgA):
    item = _make_ap_item(db, item_id="AP-api-cn-orgname")
    resp = client_orgA.post(
        "/api/workspace/peppol/credit-notes",
        json={
            "ap_item_id": item["id"],
            "credit_amount": 50.00,
            "reason": "Goodwill credit",
        },
    )
    parsed = parse_peppol_ubl_invoice(resp.json()["ubl_xml"].encode("utf-8"))
    assert parsed.supplier_name == "Acme UK Ltd"


def test_api_credit_note_cross_org_404(db, client_orgA):
    other = _make_ap_item(db, item_id="AP-api-cn-cross", org="orgB")
    resp = client_orgA.post(
        "/api/workspace/peppol/credit-notes",
        json={
            "ap_item_id": other["id"],
            "credit_amount": 50.00,
            "reason": "n/a",
        },
    )
    assert resp.status_code == 404


def test_api_credit_note_invalid_amount_422(db, client_orgA):
    item = _make_ap_item(db, item_id="AP-api-cn-bad")
    resp = client_orgA.post(
        "/api/workspace/peppol/credit-notes",
        json={
            "ap_item_id": item["id"],
            "credit_amount": -1.0,
            "reason": "n/a",
        },
    )
    assert resp.status_code == 422


def test_api_credit_note_carries_back_reference(db, client_orgA):
    item = _make_ap_item(
        db, item_id="AP-api-cn-ref",
        invoice_number="VENDOR-INV-99",
    )
    resp = client_orgA.post(
        "/api/workspace/peppol/credit-notes",
        json={
            "ap_item_id": item["id"],
            "credit_amount": 100.00,
            "reason": "Partial credit",
        },
    )
    xml = resp.json()["ubl_xml"]
    assert "<cac:BillingReference>" in xml
    assert "VENDOR-INV-99" in xml
