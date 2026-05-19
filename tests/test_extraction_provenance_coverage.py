"""Phase 1, Gap 1 — every extraction producer emits ``field_provenance``.

Producers covered:

* PEPPOL UBL parser
  (``solden.services.peppol_ubl_parser.ParsedPeppolInvoice``)
* QuickBooks ERP-native intake adapter
* NetSuite ERP-native intake adapter
* Xero ERP-native intake adapter
* SAP S/4HANA ERP-native intake adapter

Email/Gmail and Claude Vision paths are covered by their own
extraction tests via ``email_parser._build_field_provenance`` (the
multi-source merge case) — the goal here is to guarantee parity for
every structured-source intake that previously dropped provenance.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from solden.services.extraction_provenance import (
    METHOD_API_PASSTHROUGH,
    METHOD_UBL_PARSER,
    SOURCE_ERP_NATIVE_NETSUITE,
    SOURCE_ERP_NATIVE_QUICKBOOKS,
    SOURCE_ERP_NATIVE_SAP,
    SOURCE_ERP_NATIVE_XERO,
    SOURCE_PEPPOL_UBL,
    build_passthrough_evidence,
    build_passthrough_provenance,
)


# ---------------------------------------------------------------------
# Helper-level invariants
# ---------------------------------------------------------------------


def test_passthrough_provenance_skips_empty_fields():
    out = build_passthrough_provenance(
        source=SOURCE_PEPPOL_UBL,
        source_ref="ref-1",
        method=METHOD_UBL_PARSER,
        fields={
            "vendor_name": "Acme",
            "amount": 0.0,         # zero is still a value
            "invoice_number": "",  # empty string drops
            "due_date": None,       # None drops
            "currency": "EUR",
        },
    )
    assert "vendor_name" in out
    assert "amount" in out
    assert "currency" in out
    assert "invoice_number" not in out
    assert "due_date" not in out
    for entry in out.values():
        assert entry["source"] == SOURCE_PEPPOL_UBL
        assert entry["source_ref"] == "ref-1"
        assert entry["method"] == METHOD_UBL_PARSER
        assert "extracted_at" in entry


def test_passthrough_evidence_mirrors_provenance_keys():
    provenance = build_passthrough_provenance(
        source=SOURCE_ERP_NATIVE_QUICKBOOKS,
        source_ref="bill-99",
        method=METHOD_API_PASSTHROUGH,
        fields={"vendor_name": "Acme", "amount": 200.0},
    )
    evidence = build_passthrough_evidence(
        field_provenance=provenance,
        source_label="QuickBooks Online",
    )
    assert set(evidence.keys()) == set(provenance.keys())
    for key, ev in evidence.items():
        assert ev["source_label"] == "QuickBooks Online"
        assert ev["source_ref"] == "bill-99"
        assert ev["selected_value"] == provenance[key]["value"]


# ---------------------------------------------------------------------
# PEPPOL UBL parser
# ---------------------------------------------------------------------


def test_peppol_ubl_parser_emits_field_provenance():
    from solden.services.peppol_ubl_parser import ParsedPeppolInvoice
    from decimal import Decimal

    parsed = ParsedPeppolInvoice(
        invoice_id="INV-2026-001",
        currency="EUR",
        supplier_name="Acme GmbH",
        payable_amount=Decimal("1500.00"),
        due_date="2026-06-15",
    )
    kwargs = parsed.to_invoice_data_kwargs()

    provenance = kwargs.get("field_provenance")
    assert isinstance(provenance, dict) and provenance, (
        "PEPPOL UBL parser must emit field_provenance"
    )
    for field in ("vendor_name", "amount", "currency", "invoice_number", "due_date"):
        entry = provenance.get(field)
        assert entry is not None, f"missing provenance for {field}"
        assert entry["source"] == SOURCE_PEPPOL_UBL
        assert entry["method"] == METHOD_UBL_PARSER
        assert entry["source_ref"] == "INV-2026-001"

    evidence = kwargs.get("field_evidence")
    assert isinstance(evidence, dict) and evidence
    for field, ev in evidence.items():
        assert ev["source_label"] == "PEPPOL e-invoice"


# ---------------------------------------------------------------------
# ERP-native intake adapters
# ---------------------------------------------------------------------


def _make_envelope(source_id: str, channel_metadata: Dict[str, Any], *, source_type: str = "test"):
    """Construct a minimal IntakeEnvelope for ERP adapter tests."""
    from solden.services.intake_adapter import IntakeEnvelope

    return IntakeEnvelope(
        source_type=source_type,
        event_type="bill.created",
        source_id=source_id,
        organization_id="org-1",
        event_id=f"evt-{source_id}",
        channel_metadata=channel_metadata,
        raw_payload={},
    )


def _assert_provenance(invoice, *, source: str, method: str, fields: tuple):
    prov = invoice.field_provenance
    assert isinstance(prov, dict) and prov, (
        f"{source} adapter did not emit field_provenance"
    )
    for field in fields:
        entry = prov.get(field)
        assert entry is not None, f"{source}: missing provenance for {field}"
        assert entry["source"] == source
        assert entry["method"] == method
    ev = invoice.field_evidence
    assert isinstance(ev, dict) and ev


def test_quickbooks_adapter_full_intake_emits_provenance():
    from solden.integrations.erp_quickbooks_intake_adapter import (
        QuickBooksIntakeAdapter,
    )

    envelope = _make_envelope("qb-bill-1", {"qb_realm_id": "realm-1"})
    bill = {
        "Id": "qb-bill-1",
        "VendorRef": {"name": "Acme Co", "value": "vendor-1"},
        "TotalAmt": 1234.56,
        "CurrencyRef": {"value": "USD"},
        "DocNumber": "INV-001",
        "DueDate": "2026-06-30",
        "Line": [],
    }
    invoice = QuickBooksIntakeAdapter._build_invoice_from_bill(envelope, bill, "org-1")
    _assert_provenance(
        invoice,
        source=SOURCE_ERP_NATIVE_QUICKBOOKS,
        method=METHOD_API_PASSTHROUGH,
        fields=("vendor_name", "amount", "currency", "invoice_number", "due_date"),
    )


def test_quickbooks_adapter_thin_intake_emits_provenance():
    from solden.integrations.erp_quickbooks_intake_adapter import (
        QuickBooksIntakeAdapter,
    )

    envelope = _make_envelope("qb-thin-1", {"qb_realm_id": "realm-1"})
    invoice = QuickBooksIntakeAdapter()._thin_invoice_from_envelope(envelope, "org-1")
    assert invoice.field_provenance
    assert invoice.field_provenance["invoice_number"]["source"] == SOURCE_ERP_NATIVE_QUICKBOOKS


def test_netsuite_adapter_full_intake_emits_provenance():
    from solden.integrations.erp_netsuite_intake_adapter import (
        NetSuiteIntakeAdapter,
    )

    envelope = _make_envelope("ns-1", {"ns_account_id": "acct-1"})
    intake = {
        "bill_header": {
            "vendor_name": "Acme NS",
            "amount": 999.0,
            "currency_id": "USD",
            "tran_id": "NS-1",
            "due_date": "2026-07-01",
            "tax_amount": 90.0,
            "subtotal": 909.0,
        },
        "bill_lines": [],
        "expense_lines": [],
    }
    invoice = NetSuiteIntakeAdapter._build_invoice_from_intake(envelope, intake, "org-1")
    _assert_provenance(
        invoice,
        source=SOURCE_ERP_NATIVE_NETSUITE,
        method=METHOD_API_PASSTHROUGH,
        fields=("vendor_name", "amount", "currency", "invoice_number", "due_date", "tax_amount"),
    )


def test_xero_adapter_full_intake_emits_provenance():
    from solden.integrations.erp_xero_intake_adapter import XeroIntakeAdapter

    envelope = _make_envelope("xero-1", {"xero_tenant_id": "tenant-1"})
    invoice_payload = {
        "Contact": {"Name": "Acme Xero", "ContactID": "c-1"},
        "Total": 500.0,
        "CurrencyCode": "GBP",
        "InvoiceNumber": "X-001",
        "InvoiceID": "x-001-id",
        "DueDate": "2026-06-30",
        "TotalTax": 50.0,
        "SubTotal": 450.0,
        "LineItems": [],
    }
    invoice = XeroIntakeAdapter._build_invoice_from_xero(envelope, invoice_payload, "org-1")
    _assert_provenance(
        invoice,
        source=SOURCE_ERP_NATIVE_XERO,
        method=METHOD_API_PASSTHROUGH,
        fields=("vendor_name", "amount", "currency", "invoice_number"),
    )


def test_sap_adapter_full_intake_emits_provenance():
    from solden.integrations.erp_sap_s4hana_intake_adapter import (
        SapS4HanaIntakeAdapter,
    )

    envelope = _make_envelope("CC1/DOC1/2026", {})
    intake = {
        "bill_header": {
            "supplier_name": "Acme SAP",
            "amount": 1200.0,
            "currency": "EUR",
            "invoice_number": "SAP-INV-1",
            "due_date": "2026-06-30",
            "tax_amount": 240.0,
        },
        "bill_lines": [],
    }
    invoice = SapS4HanaIntakeAdapter._build_invoice_from_intake(envelope, intake, "org-1")
    _assert_provenance(
        invoice,
        source=SOURCE_ERP_NATIVE_SAP,
        method=METHOD_API_PASSTHROUGH,
        fields=("vendor_name", "amount", "currency", "invoice_number", "due_date", "tax_amount"),
    )


# ---------------------------------------------------------------------
# Persistence: save_invoice_status carries provenance to ap_items.metadata
# ---------------------------------------------------------------------


def test_save_invoice_status_persists_field_provenance(postgres_test_db):
    """The ERP-native + PEPPOL paths persist via ``save_invoice_status``;
    confirm it carries field_provenance/field_evidence/erp_metadata
    onto ``ap_items.metadata`` so the audit chain reaches the row."""
    from solden.core.database import get_db

    db = get_db()
    db.initialize()

    field_provenance = {
        "vendor_name": {
            "source": SOURCE_ERP_NATIVE_QUICKBOOKS,
            "source_ref": "qb-bill-99",
            "method": METHOD_API_PASSTHROUGH,
            "extracted_at": "2026-05-03T12:00:00+00:00",
            "value": "Test Vendor",
        }
    }
    field_evidence = {
        "vendor_name": {
            "source": SOURCE_ERP_NATIVE_QUICKBOOKS,
            "source_label": "QuickBooks Online",
            "selected_value": "Test Vendor",
        }
    }
    erp_metadata = {"qb_bill_id": "qb-bill-99", "qb_realm_id": "realm-1"}

    item_id = db.save_invoice_status(
        gmail_id="qb-bill-99",
        status="received",
        email_subject="QB Bill 99 — Test Vendor",
        sender="<quickbooks@erp-native>",
        vendor="Test Vendor",
        amount=500.0,
        currency="USD",
        invoice_number="INV-99",
        organization_id="org-test",
        field_provenance=field_provenance,
        field_evidence=field_evidence,
        erp_metadata=erp_metadata,
        source_type="quickbooks",
    )
    assert item_id

    row = db.get_ap_item(item_id)
    assert row is not None
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        import json
        metadata = json.loads(metadata)
    assert isinstance(metadata, dict)
    assert metadata.get("field_provenance") == field_provenance
    assert metadata.get("field_evidence") == field_evidence
    assert metadata.get("erp_metadata") == erp_metadata
    assert metadata.get("source_type") == "quickbooks"
