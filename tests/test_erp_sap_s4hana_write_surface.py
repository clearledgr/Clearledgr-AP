"""Tests for SAP S/4HANA write surface + per-line tax-code propagation.

Covers:
  * post_to_sap dispatches by connection shape — B1 URL routes to
    /JournalEntries; S/4HANA URL routes to API_JOURNALENTRY_SRV.
  * post_bill_to_sap dispatches — B1 -> /PurchaseInvoices, S/4HANA
    -> API_SUPPLIERINVOICE_PROCESS_SRV.
  * reverse_bill_from_sap dispatches — B1 -> /PurchaseInvoices/Cancel,
    S/4HANA -> CancelSupplierInvoice action.
  * S/4HANA payload shape: A_JournalEntry deep-create with
    DebitCreditCode S/H, GLAccount, AmountInTransactionCurrency.
  * S/4HANA bill payload: A_SupplierInvoice with TaxCode (MWSKZ)
    propagated from bill.vat_code.
  * S/4HANA composite-key return: "CompanyCode/SupplierInvoice/FiscalYear".
  * Per-line tax codes for B1: gl_map override > _DEFAULT_B1_TAXCODE_MAP.
  * Per-line tax codes for QB: TaxCodeRef on
    AccountBasedExpenseLineDetail; gl_map override.
  * Per-line tax codes for Xero: TaxType per-line; gl_map override
    beats default; legacy "OUTPUT" fallback only when no vat_code.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.integrations import erp_quickbooks  # noqa: E402
from clearledgr.integrations import erp_sap  # noqa: E402
from clearledgr.integrations import erp_xero  # noqa: E402


# ─── Fakes ──────────────────────────────────────────────────────────


def _b1_conn() -> SimpleNamespace:
    return SimpleNamespace(
        type="sap",
        access_token="b1-token",
        base_url="https://sap-b1.example.com:50000/b1s/v1",
        company_code="1000",
        tenant_id=None, realm_id=None, webhook_secret=None,
    )


def _s4_conn() -> SimpleNamespace:
    return SimpleNamespace(
        type="sap",
        access_token="s4-token",
        base_url="https://my-s4.api.sap",
        company_code="1000",
        tenant_id=None, realm_id=None, webhook_secret=None,
    )


def _qb_conn() -> SimpleNamespace:
    return SimpleNamespace(
        type="quickbooks",
        access_token="qb-token",
        realm_id="9999",
        tenant_id=None, base_url=None, company_code=None,
        webhook_secret=None,
    )


def _xero_conn() -> SimpleNamespace:
    return SimpleNamespace(
        type="xero",
        access_token="xero-token",
        tenant_id="tnt",
        realm_id=None, base_url=None, company_code=None,
        webhook_secret=None,
    )


class FakeResponse:
    def __init__(self, status_code: int = 200, body: dict = None):
        self.status_code = status_code
        self._body = body or {}
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            from httpx import HTTPStatusError, Request, Response
            req = Request("POST", "http://x")
            resp = Response(self.status_code, request=req)
            raise HTTPStatusError(
                f"http {self.status_code}", request=req, response=resp,
            )

    def json(self):
        return self._body


def _patch_post(captured: list, response_body: dict):
    """Patch the http client used by erp_sap so its post records the
    request and returns the canned response."""
    async def fake_post(url, json=None, headers=None, timeout=None,
                        params=None, data=None, **kwargs):
        captured.append({
            "url": url, "json": json,
            "headers": headers, "data": data,
        })
        return FakeResponse(200, response_body)

    return patch.object(
        erp_sap, "get_http_client",
        return_value=SimpleNamespace(post=fake_post, get=fake_post),
    )


def _bill(
    *, vat_code: str = "", currency: str = "USD",
    amount: float = 1190.0, line_items=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        vendor_id="VENDOR-1",
        vendor_name="Vendor X",
        amount=amount,
        currency=currency,
        invoice_number="INV-100",
        invoice_date="2026-04-29",
        due_date="2026-05-29",
        description="Bill",
        line_items=line_items,
        tax_amount=0,
        discount_amount=0,
        discount_terms=None,
        vat_code=vat_code,
        payment_terms=None,
        po_number="PO-1",
        bank_details=None,
    )


# ─── post_to_sap dispatch ─────────────────────────────────────────


def test_post_to_sap_b1_url_calls_journalentries():
    captured: list = []
    with _patch_post(captured, {"DocEntry": "100"}):
        result = asyncio.run(erp_sap.post_to_sap(
            _b1_conn(),
            {
                "date": "2026-04-29",
                "description": "test",
                "lines": [
                    {"debit": 100.0, "credit": 0, "account": "6000",
                     "account_name": "Expense"},
                    {"debit": 0, "credit": 100.0, "account": "1600",
                     "account_name": "AP"},
                ],
            },
        ))
    assert result["status"] == "success"
    assert "/JournalEntries" in captured[0]["url"]
    # B1 shape
    assert "JournalEntryLines" in captured[0]["json"]


def test_post_to_sap_s4hana_url_calls_api_journalentry_srv():
    captured: list = []
    with _patch_post(captured, {
        "AccountingDocument": "100000123",
        "FiscalYear": "2026",
        "CompanyCode": "1000",
    }):
        result = asyncio.run(erp_sap.post_to_sap(
            _s4_conn(),
            {
                "date": "2026-04-29",
                "description": "test",
                "currency": "EUR",
                "lines": [
                    {"debit": 100.0, "credit": 0, "account": "411000",
                     "account_name": "Expense"},
                    {"debit": 0, "credit": 100.0, "account": "211200",
                     "account_name": "AP"},
                ],
            },
        ))
    assert result["status"] == "success"
    assert "API_JOURNALENTRY_SRV" in captured[0]["url"]
    body = captured[0]["json"]
    assert body["CompanyCode"] == "1000"
    assert body["AccountingDocumentType"] == "SA"
    items = body["to_JournalEntryItem"]
    assert len(items) == 2
    debit = next(i for i in items if i["DebitCreditCode"] == "S")
    credit = next(i for i in items if i["DebitCreditCode"] == "H")
    assert debit["GLAccount"] == "411000"
    assert credit["GLAccount"] == "211200"
    # Composite-key return shape
    assert result["entry_id"] == "1000/100000123/2026"


def test_post_to_sap_s4hana_propagates_tax_code():
    captured: list = []
    with _patch_post(captured, {
        "AccountingDocument": "1", "FiscalYear": "2026",
        "CompanyCode": "1000",
    }):
        asyncio.run(erp_sap.post_to_sap(
            _s4_conn(),
            {
                "lines": [
                    {"debit": 100.0, "credit": 0, "account": "411000",
                     "tax_code": "V1"},
                ],
            },
        ))
    items = captured[0]["json"]["to_JournalEntryItem"]
    assert items[0]["TaxCode"] == "V1"


# ─── post_bill_to_sap dispatch ────────────────────────────────────


def test_post_bill_to_sap_b1_routes_to_purchaseinvoices():
    captured: list = []
    # B1 needs the session helper too — patch it to short-circuit.
    async def fake_session(connection, client, fetch_csrf_for=None):
        return {"status": "success", "headers": {}}

    with _patch_post(captured, {"DocEntry": 100, "JournalEntry": 500}), \
         patch.object(erp_sap, "_open_sap_service_layer_session", new=fake_session):
        result = asyncio.run(erp_sap.post_bill_to_sap(
            _b1_conn(), _bill(vat_code="T1", currency="EUR"),
        ))
    assert result["status"] == "success"
    assert "/PurchaseInvoices" in captured[0]["url"]
    # B1 tax-code propagation: vat_code=T1 -> 1S (default)
    line = captured[0]["json"]["DocumentLines"][0]
    assert line["TaxCode"] == "1S"


def test_post_bill_to_sap_b1_gl_map_overrides_tax_code():
    captured: list = []

    async def fake_session(connection, client, fetch_csrf_for=None):
        return {"status": "success", "headers": {}}

    with _patch_post(captured, {"DocEntry": 100}), \
         patch.object(erp_sap, "_open_sap_service_layer_session", new=fake_session):
        asyncio.run(erp_sap.post_bill_to_sap(
            _b1_conn(),
            _bill(vat_code="T1", currency="EUR"),
            gl_map={"tax_code_T1": "V7"},
        ))
    line = captured[0]["json"]["DocumentLines"][0]
    assert line["TaxCode"] == "V7"


def test_post_bill_to_sap_s4hana_uses_supplierinvoice_srv():
    captured: list = []
    with _patch_post(captured, {
        "SupplierInvoice": "5105600000",
        "FiscalYear": "2026",
        "CompanyCode": "1000",
    }):
        result = asyncio.run(erp_sap.post_bill_to_sap(
            _s4_conn(), _bill(vat_code="T1", currency="EUR"),
        ))
    assert result["status"] == "success"
    assert "API_SUPPLIERINVOICE_PROCESS_SRV" in captured[0]["url"]
    body = captured[0]["json"]
    assert body["CompanyCode"] == "1000"
    assert body["InvoicingParty"] == "VENDOR-1"
    assert body["InvoiceGrossAmount"] == 1190.0
    line = body["to_SupplierInvoiceItemGLAcct"][0]
    # Default S/4HANA MWSKZ for T1 -> V1
    assert line["TaxCode"] == "V1"
    # Composite-key return
    assert result["bill_id"] == "1000/5105600000/2026"
    # JE-id capture: in S/4HANA, supplier invoice IS the JE source
    assert result["erp_journal_entry_id"] == "1000/5105600000/2026"


def test_post_bill_to_sap_s4hana_per_line_vat_code_overrides_bill():
    captured: list = []
    line_items = [
        {"description": "Server", "amount": 1000.0, "vat_code": "T1"},
        {"description": "Insurance", "amount": 100.0, "vat_code": "T2"},
    ]
    with _patch_post(captured, {
        "SupplierInvoice": "1", "FiscalYear": "2026",
        "CompanyCode": "1000",
    }):
        asyncio.run(erp_sap.post_bill_to_sap(
            _s4_conn(),
            _bill(vat_code="T1", line_items=line_items, currency="EUR"),
        ))
    items = captured[0]["json"]["to_SupplierInvoiceItemGLAcct"]
    assert items[0]["TaxCode"] == "V1"  # T1 -> V1
    assert items[1]["TaxCode"] == "VE"  # T2 (exempt) -> VE


def test_post_bill_to_sap_s4hana_validates_company_code():
    conn = _s4_conn()
    conn.company_code = None
    result = asyncio.run(erp_sap.post_bill_to_sap(
        conn, _bill(vat_code="T1"),
    ))
    assert result["status"] == "error"
    assert "company_code" in result["missing_fields"]


def test_post_to_sap_s4hana_validates_company_code():
    conn = _s4_conn()
    conn.company_code = None
    result = asyncio.run(erp_sap.post_to_sap(
        conn, {"lines": [{"debit": 100.0, "credit": 0, "account": "x"}]},
    ))
    assert result["status"] == "error"
    assert "company_code" in result["reason"]


# ─── reverse_bill_from_sap dispatch ───────────────────────────────


def test_reverse_bill_from_sap_b1_routes_to_cancel_action():
    captured: list = []

    async def fake_session(connection, client, fetch_csrf_for=None):
        return {"status": "success", "headers": {}}

    with _patch_post(captured, {}), \
         patch.object(erp_sap, "_open_sap_service_layer_session", new=fake_session):
        asyncio.run(erp_sap.reverse_bill_from_sap(
            _b1_conn(), "100", reason="test",
        ))
    assert "/PurchaseInvoices(100)/Cancel" in captured[0]["url"]


def test_reverse_bill_from_sap_s4hana_routes_to_cancel_action():
    captured: list = []
    with _patch_post(captured, {"CancellationDocument": "999"}):
        result = asyncio.run(erp_sap.reverse_bill_from_sap(
            _s4_conn(), "1000/5105600000/2026", reason="test",
        ))
    assert result["status"] == "success"
    assert "CancelSupplierInvoice" in captured[0]["url"]
    assert "1000" in captured[0]["url"] and "5105600000" in captured[0]["url"]
    assert result["cancellation_id"] == "999"


def test_reverse_bill_from_sap_s4hana_rejects_non_composite_key():
    result = asyncio.run(erp_sap.reverse_bill_from_sap(
        _s4_conn(), "just-a-doc-entry", reason="test",
    ))
    assert result["status"] == "error"
    assert "invalid_invoice_key" in result["reason"]


# ─── Connection-shape heuristic ──────────────────────────────────


def test_is_s4hana_b1_url_matches_b1():
    assert erp_sap.is_sap_s4hana_connection(_b1_conn()) is False


def test_is_s4hana_other_url_matches_s4hana():
    assert erp_sap.is_sap_s4hana_connection(_s4_conn()) is True


# ─── QuickBooks per-line tax codes ────────────────────────────────


def _patch_qb_post(captured: list, body: dict):
    async def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        captured.append({"url": url, "json": json})
        return FakeResponse(200, body)

    async def fake_put(url, json=None, headers=None, timeout=None, **kwargs):
        return FakeResponse(200, body)

    async def fake_get(url, params=None, headers=None, timeout=None, **kwargs):
        return FakeResponse(200, body)

    return patch.object(
        erp_quickbooks, "get_http_client",
        return_value=SimpleNamespace(
            post=fake_post, put=fake_put, get=fake_get,
        ),
    )


def test_qb_bill_propagates_tax_code_per_line():
    captured: list = []
    body = {"Bill": {"Id": "1", "DocNumber": "001"}}
    with _patch_qb_post(captured, body):
        asyncio.run(erp_quickbooks.post_bill_to_quickbooks(
            _qb_conn(), _bill(vat_code="T1"),
        ))
    payload = captured[-1]["json"]
    line = payload["Line"][0]
    detail = line["AccountBasedExpenseLineDetail"]
    assert detail["TaxCodeRef"] == {"value": "TAX"}


def test_qb_gl_map_overrides_tax_code():
    captured: list = []
    body = {"Bill": {"Id": "1"}}
    with _patch_qb_post(captured, body):
        asyncio.run(erp_quickbooks.post_bill_to_quickbooks(
            _qb_conn(), _bill(vat_code="T1"),
            gl_map={"tax_code_T1": "9"},  # UK Std Rated id
        ))
    line = captured[-1]["json"]["Line"][0]
    assert line["AccountBasedExpenseLineDetail"]["TaxCodeRef"] == {"value": "9"}


def test_qb_per_line_vat_code_overrides_bill_default():
    captured: list = []
    body = {"Bill": {"Id": "1"}}
    line_items = [
        {"description": "Server", "amount": 100.0, "vat_code": "T1"},
        {"description": "Insurance", "amount": 50.0, "vat_code": "T2"},
    ]
    with _patch_qb_post(captured, body):
        asyncio.run(erp_quickbooks.post_bill_to_quickbooks(
            _qb_conn(),
            _bill(vat_code="T1", line_items=line_items),
        ))
    lines = captured[-1]["json"]["Line"]
    # First line uses T1 -> "TAX", second uses T2 -> "NON"
    tax_refs = [
        ln["AccountBasedExpenseLineDetail"].get("TaxCodeRef")
        for ln in lines if ln.get("DetailType") == "AccountBasedExpenseLineDetail"
    ]
    assert {"value": "TAX"} in tax_refs
    assert {"value": "NON"} in tax_refs


# ─── Xero per-line tax types ──────────────────────────────────────


def _patch_xero_post(captured: list, body: dict):
    async def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        captured.append({"url": url, "json": json})
        return FakeResponse(200, body)

    async def fake_put(url, json=None, headers=None, timeout=None, **kwargs):
        return FakeResponse(200, body)

    async def fake_get(url, headers=None, timeout=None, **kwargs):
        return FakeResponse(200, body)

    return patch.object(
        erp_xero, "get_http_client",
        return_value=SimpleNamespace(post=fake_post, put=fake_put, get=fake_get),
    )


def test_xero_bill_uses_input2_for_t1_default():
    captured: list = []
    body = {"Invoices": [{"InvoiceID": "abc", "InvoiceNumber": "INV-1"}]}
    with _patch_xero_post(captured, body):
        asyncio.run(erp_xero.post_bill_to_xero(
            _xero_conn(), _bill(vat_code="T1"),
        ))
    li = captured[-1]["json"]["Invoices"][0]["LineItems"][0]
    assert li["TaxType"] == "INPUT2"


def test_xero_bill_uses_reversecharges_for_rc():
    captured: list = []
    body = {"Invoices": [{"InvoiceID": "abc"}]}
    with _patch_xero_post(captured, body):
        asyncio.run(erp_xero.post_bill_to_xero(
            _xero_conn(), _bill(vat_code="RC"),
        ))
    li = captured[-1]["json"]["Invoices"][0]["LineItems"][0]
    assert li["TaxType"] == "REVERSECHARGES"


def test_xero_gl_map_overrides_tax_type():
    captured: list = []
    body = {"Invoices": [{"InvoiceID": "abc"}]}
    with _patch_xero_post(captured, body):
        asyncio.run(erp_xero.post_bill_to_xero(
            _xero_conn(), _bill(vat_code="T1"),
            gl_map={"tax_code_T1": "INPUT3"},
        ))
    li = captured[-1]["json"]["Invoices"][0]["LineItems"][0]
    assert li["TaxType"] == "INPUT3"


def test_xero_per_line_vat_code_wins():
    captured: list = []
    body = {"Invoices": [{"InvoiceID": "abc"}]}
    line_items = [
        {"description": "A", "amount": 100, "vat_code": "T1"},
        {"description": "B", "amount": 50, "vat_code": "T0"},
    ]
    with _patch_xero_post(captured, body):
        asyncio.run(erp_xero.post_bill_to_xero(
            _xero_conn(),
            _bill(vat_code="T1", line_items=line_items),
        ))
    lines = captured[-1]["json"]["Invoices"][0]["LineItems"]
    types = [li["TaxType"] for li in lines]
    assert "INPUT2" in types
    assert "ZERORATEDINPUT" in types


def test_xero_legacy_output_fallback_only_when_no_vat_code():
    """The legacy 'OUTPUT' fallback was used pre-fix when tax_amount
    > 0. Make sure it ONLY fires when no vat_code is set — otherwise
    we'd silently override the operator-configured TaxType."""
    captured: list = []
    body = {"Invoices": [{"InvoiceID": "abc"}]}
    bill = _bill(vat_code="T1")
    bill.tax_amount = 100.0
    with _patch_xero_post(captured, body):
        asyncio.run(erp_xero.post_bill_to_xero(_xero_conn(), bill))
    li = captured[-1]["json"]["Invoices"][0]["LineItems"][0]
    # T1 -> INPUT2 (purchase side), NOT OUTPUT
    assert li["TaxType"] == "INPUT2"
