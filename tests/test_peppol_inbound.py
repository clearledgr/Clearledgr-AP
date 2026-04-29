"""Tests for Wave 4 / F1 — PEPPOL UBL inbound parser + import API.

Covers:
  * Domestic invoice (DE, 19% standard rate) — parser extracts
    supplier, totals, line items, derives 'domestic' + T1.
  * Reverse-charge invoice (intra-EU B2B, category AE) — derives
    'reverse_charge' + RC code; treatment dominance preserved.
  * Zero-rated invoice (export, category G) — derives 'zero_rated'
    + T0.
  * Mixed-VAT invoice — primary treatment from largest taxable
    subtotal.
  * Malformed inputs — empty, non-XML, non-Invoice root: warnings
    populated, no exception.
  * Validation warnings — missing invoice_id / supplier_name.
  * API: preview returns canonical extraction; import creates an
    AP item with VAT split pre-populated; cross-field consistency
    (payable vs tax-inclusive) flagged when wrong.
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
from clearledgr.services.peppol_ubl_parser import (  # noqa: E402
    parse_peppol_ubl_invoice,
)


# ─── Sample fixtures ───────────────────────────────────────────────


SAMPLE_DOMESTIC_DE_19 = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0</cbc:CustomizationID>
  <cbc:ID>INV-2026-001</cbc:ID>
  <cbc:IssueDate>2026-04-29</cbc:IssueDate>
  <cbc:DueDate>2026-05-29</cbc:DueDate>
  <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyName><cbc:Name>Vendor X GmbH</cbc:Name></cac:PartyName>
      <cac:PartyTaxScheme>
        <cbc:CompanyID>DE123456789</cbc:CompanyID>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:PartyTaxScheme>
      <cac:PartyLegalEntity>
        <cbc:RegistrationName>Vendor X GmbH</cbc:RegistrationName>
        <cbc:CompanyID schemeID="0002">DE12345678</cbc:CompanyID>
      </cac:PartyLegalEntity>
      <cac:PostalAddress>
        <cbc:StreetName>Hauptstrasse 1</cbc:StreetName>
        <cbc:CityName>Munich</cbc:CityName>
        <cbc:PostalZone>80331</cbc:PostalZone>
        <cac:Country><cbc:IdentificationCode>DE</cbc:IdentificationCode></cac:Country>
      </cac:PostalAddress>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty>
    <cac:Party>
      <cac:PartyLegalEntity>
        <cbc:RegistrationName>Acme Holdings GmbH</cbc:RegistrationName>
      </cac:PartyLegalEntity>
    </cac:Party>
  </cac:AccountingCustomerParty>
  <cac:PaymentTerms>
    <cbc:Note>Net 30</cbc:Note>
  </cac:PaymentTerms>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="EUR">190.00</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">1000.00</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="EUR">190.00</cbc:TaxAmount>
      <cac:TaxCategory>
        <cbc:ID>S</cbc:ID>
        <cbc:Percent>19</cbc:Percent>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="EUR">1000.00</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="EUR">1000.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">1190.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">1190.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="EA">10</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">1000.00</cbc:LineExtensionAmount>
    <cac:Item>
      <cbc:Name>Server hosting</cbc:Name>
      <cac:ClassifiedTaxCategory>
        <cbc:ID>S</cbc:ID>
        <cbc:Percent>19</cbc:Percent>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:ClassifiedTaxCategory>
    </cac:Item>
    <cac:Price>
      <cbc:PriceAmount currencyID="EUR">100.00</cbc:PriceAmount>
    </cac:Price>
  </cac:InvoiceLine>
</Invoice>
"""


SAMPLE_REVERSE_CHARGE = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:ID>INV-RC-001</cbc:ID>
  <cbc:IssueDate>2026-04-29</cbc:IssueDate>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyLegalEntity>
        <cbc:RegistrationName>Vendor FR SARL</cbc:RegistrationName>
      </cac:PartyLegalEntity>
      <cac:PartyTaxScheme>
        <cbc:CompanyID>FR12345678901</cbc:CompanyID>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:PartyTaxScheme>
      <cac:PostalAddress>
        <cac:Country><cbc:IdentificationCode>FR</cbc:IdentificationCode></cac:Country>
      </cac:PostalAddress>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="EUR">0.00</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">5000.00</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="EUR">0.00</cbc:TaxAmount>
      <cac:TaxCategory>
        <cbc:ID>AE</cbc:ID>
        <cbc:Percent>0</cbc:Percent>
        <cbc:TaxExemptionReason>Reverse charge - Article 196 of Directive 2006/112/EC</cbc:TaxExemptionReason>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="EUR">5000.00</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="EUR">5000.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">5000.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">5000.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="EA">1</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">5000.00</cbc:LineExtensionAmount>
    <cac:Item>
      <cbc:Name>Consulting services</cbc:Name>
    </cac:Item>
  </cac:InvoiceLine>
</Invoice>
"""


SAMPLE_ZERO_RATED_EXPORT = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:ID>INV-EXP-001</cbc:ID>
  <cbc:IssueDate>2026-04-29</cbc:IssueDate>
  <cbc:DocumentCurrencyCode>USD</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyLegalEntity>
        <cbc:RegistrationName>Vendor US Inc</cbc:RegistrationName>
      </cac:PartyLegalEntity>
      <cac:PostalAddress>
        <cac:Country><cbc:IdentificationCode>US</cbc:IdentificationCode></cac:Country>
      </cac:PostalAddress>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="USD">0.00</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="USD">2500.00</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="USD">0.00</cbc:TaxAmount>
      <cac:TaxCategory>
        <cbc:ID>G</cbc:ID>
        <cbc:Percent>0</cbc:Percent>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:PayableAmount currencyID="USD">2500.00</cbc:PayableAmount>
    <cbc:TaxExclusiveAmount currencyID="USD">2500.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="USD">2500.00</cbc:TaxInclusiveAmount>
    <cbc:LineExtensionAmount currencyID="USD">2500.00</cbc:LineExtensionAmount>
  </cac:LegalMonetaryTotal>
</Invoice>
"""


SAMPLE_MIXED_VAT = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:ID>INV-MIX-001</cbc:ID>
  <cbc:IssueDate>2026-04-29</cbc:IssueDate>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyLegalEntity>
        <cbc:RegistrationName>Mixed Vendor</cbc:RegistrationName>
      </cac:PartyLegalEntity>
      <cac:PostalAddress>
        <cac:Country><cbc:IdentificationCode>NL</cbc:IdentificationCode></cac:Country>
      </cac:PostalAddress>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="EUR">42.00</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">200.00</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="EUR">42.00</cbc:TaxAmount>
      <cac:TaxCategory>
        <cbc:ID>S</cbc:ID>
        <cbc:Percent>21</cbc:Percent>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:TaxCategory>
    </cac:TaxSubtotal>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">100.00</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="EUR">0.00</cbc:TaxAmount>
      <cac:TaxCategory>
        <cbc:ID>Z</cbc:ID>
        <cbc:Percent>0</cbc:Percent>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="EUR">300.00</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="EUR">300.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">342.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">342.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</Invoice>
"""


SAMPLE_WITH_RC_DOMINANCE = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:ID>INV-RC-DOM</cbc:ID>
  <cbc:IssueDate>2026-04-29</cbc:IssueDate>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party><cac:PartyLegalEntity><cbc:RegistrationName>V</cbc:RegistrationName></cac:PartyLegalEntity></cac:Party>
  </cac:AccountingSupplierParty>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="EUR">0.00</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">1000.00</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="EUR">0.00</cbc:TaxAmount>
      <cac:TaxCategory>
        <cbc:ID>S</cbc:ID><cbc:Percent>19</cbc:Percent>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:TaxCategory>
    </cac:TaxSubtotal>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">100.00</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="EUR">0.00</cbc:TaxAmount>
      <cac:TaxCategory>
        <cbc:ID>AE</cbc:ID><cbc:Percent>19</cbc:Percent>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:PayableAmount currencyID="EUR">1100.00</cbc:PayableAmount>
    <cbc:TaxExclusiveAmount currencyID="EUR">1100.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">1100.00</cbc:TaxInclusiveAmount>
    <cbc:LineExtensionAmount currencyID="EUR">1100.00</cbc:LineExtensionAmount>
  </cac:LegalMonetaryTotal>
</Invoice>
"""


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
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


# ─── Parser: domestic ───────────────────────────────────────────────


def test_parse_domestic_de_extracts_supplier_and_totals():
    parsed = parse_peppol_ubl_invoice(SAMPLE_DOMESTIC_DE_19)
    assert parsed.invoice_id == "INV-2026-001"
    assert parsed.supplier_name == "Vendor X GmbH"
    assert parsed.supplier_vat_id == "DE123456789"
    assert parsed.supplier_country == "DE"
    assert parsed.currency == "EUR"
    assert parsed.payable_amount == Decimal("1190.00")
    assert parsed.tax_exclusive_amount == Decimal("1000.00")
    assert parsed.tax_amount == Decimal("190.00")
    assert parsed.derived_treatment == "domestic"
    assert parsed.derived_vat_code == "T1"
    assert parsed.derived_vat_rate == Decimal("19")
    assert parsed.payment_terms == "Net 30"
    assert len(parsed.line_items) == 1
    assert parsed.line_items[0]["description"] == "Server hosting"
    assert parsed.warnings == []


# ─── Parser: reverse charge ─────────────────────────────────────────


def test_parse_reverse_charge_intra_eu():
    parsed = parse_peppol_ubl_invoice(SAMPLE_REVERSE_CHARGE)
    assert parsed.derived_treatment == "reverse_charge"
    assert parsed.derived_vat_code == "RC"
    assert parsed.payable_amount == Decimal("5000.00")
    assert parsed.tax_amount == Decimal("0.00")
    # Exemption reason captured.
    assert parsed.tax_subtotals[0]["exemption_reason"]
    assert "196" in parsed.tax_subtotals[0]["exemption_reason"]


def test_parse_rc_dominance_overrules_standard():
    """A bill with both S (standard) and AE (RC) categories should
    derive as RC for routing — buyer self-accounts the whole bill."""
    parsed = parse_peppol_ubl_invoice(SAMPLE_WITH_RC_DOMINANCE)
    assert parsed.derived_treatment == "reverse_charge"


# ─── Parser: zero-rated ────────────────────────────────────────────


def test_parse_zero_rated_export():
    parsed = parse_peppol_ubl_invoice(SAMPLE_ZERO_RATED_EXPORT)
    assert parsed.derived_treatment == "zero_rated"
    assert parsed.derived_vat_code == "T0"


# ─── Parser: mixed VAT ─────────────────────────────────────────────


def test_parse_mixed_vat_picks_largest_subtotal_treatment():
    parsed = parse_peppol_ubl_invoice(SAMPLE_MIXED_VAT)
    # Largest taxable_amount is the S (200), so domestic wins
    # (no RC subtotal to dominate).
    assert parsed.derived_treatment == "domestic"
    assert parsed.derived_vat_code == "T1"
    assert parsed.derived_vat_rate == Decimal("21")


# ─── Parser: malformed ─────────────────────────────────────────────


def test_parse_empty_body_returns_warning():
    parsed = parse_peppol_ubl_invoice(b"")
    assert "empty_body" in parsed.warnings
    assert parsed.invoice_id is None


def test_parse_non_xml_returns_warning():
    parsed = parse_peppol_ubl_invoice(b"this is not XML")
    assert any("xml_parse_error" in w for w in parsed.warnings)


def test_parse_unexpected_root_returns_warning():
    body = b'<?xml version="1.0"?><FooBar/>'
    parsed = parse_peppol_ubl_invoice(body)
    assert any("unexpected_root" in w for w in parsed.warnings)


def test_parse_missing_invoice_id_warns():
    """A UBL doc without <ID> should still parse but warn."""
    body = b"""<?xml version="1.0"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party><cac:PartyLegalEntity><cbc:RegistrationName>V</cbc:RegistrationName></cac:PartyLegalEntity></cac:Party>
  </cac:AccountingSupplierParty>
  <cac:LegalMonetaryTotal>
    <cbc:PayableAmount currencyID="EUR">100.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</Invoice>"""
    parsed = parse_peppol_ubl_invoice(body)
    assert "missing_invoice_id" in parsed.warnings


# ─── API: preview ──────────────────────────────────────────────────


def test_api_preview_returns_extraction(client_orgA):
    resp = client_orgA.post(
        "/api/workspace/peppol/preview",
        content=SAMPLE_DOMESTIC_DE_19,
        headers={"Content-Type": "application/xml"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["invoice_id"] == "INV-2026-001"
    assert data["supplier_name"] == "Vendor X GmbH"
    assert data["payable_amount"] == 1190.0
    assert data["derived_treatment"] == "domestic"
    assert data["line_items_count"] == 1


def test_api_preview_empty_body_400(client_orgA):
    resp = client_orgA.post(
        "/api/workspace/peppol/preview", content=b"",
    )
    assert resp.status_code == 400


# ─── API: import ───────────────────────────────────────────────────


def test_api_import_creates_ap_item_with_vat_split(db, client_orgA):
    resp = client_orgA.post(
        "/api/workspace/peppol/import",
        content=SAMPLE_DOMESTIC_DE_19,
        headers={"Content-Type": "application/xml"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ap_item_id = data["ap_item_id"]

    fresh = db.get_ap_item(ap_item_id)
    assert fresh is not None
    assert fresh["vendor_name"] == "Vendor X GmbH"
    assert float(fresh["amount"]) == 1190.0
    assert fresh["currency"] == "EUR"
    assert fresh["invoice_number"] == "INV-2026-001"
    assert fresh["state"] == "received"
    # VAT split pre-populated from the UBL TaxTotal.
    assert fresh["net_amount"] == Decimal("1000.00")
    assert fresh["vat_amount"] == Decimal("190.00")
    assert fresh["tax_treatment"] == "domestic"
    assert fresh["vat_code"] == "T1"
    assert fresh["bill_country"] == "DE"


def test_api_import_reverse_charge_flagged_correctly(db, client_orgA):
    resp = client_orgA.post(
        "/api/workspace/peppol/import",
        content=SAMPLE_REVERSE_CHARGE,
        headers={"Content-Type": "application/xml"},
    )
    assert resp.status_code == 200
    data = resp.json()
    fresh = db.get_ap_item(data["ap_item_id"])
    assert fresh["tax_treatment"] == "reverse_charge"
    assert fresh["vat_code"] == "RC"
    assert fresh["bill_country"] == "FR"


def test_api_import_missing_payable_amount_400(client_orgA):
    body = b"""<?xml version="1.0"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:ID>INV-NO-AMT</cbc:ID>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party><cac:PartyLegalEntity><cbc:RegistrationName>V</cbc:RegistrationName></cac:PartyLegalEntity></cac:Party>
  </cac:AccountingSupplierParty>
</Invoice>"""
    resp = client_orgA.post(
        "/api/workspace/peppol/import",
        content=body,
        headers={"Content-Type": "application/xml"},
    )
    assert resp.status_code == 400
    assert "missing_payable_amount" in resp.json()["detail"]
