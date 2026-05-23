"""Tests for beta blocker fixes: GL mapping, SAP pre-flight, token refresh retry, NetSuite normalization.

Follows existing test patterns:
- tmp_path DB via monkeypatch.setenv("CLEARLEDGR_DB_PATH", ...)
- Reset _DB_INSTANCE in teardown (conftest.reset_service_singletons)
- asyncio.run() wrapping
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from solden.core import database as db_module
from solden.integrations.erp_router import (
    Bill,
    CreditApplication,
    ERPConnection,
    SettlementApplication,
    DEFAULT_ACCOUNT_MAP,
    _get_org_gl_map,
    apply_credit_note,
    apply_settlement,
    get_account_code,
    post_bill,
    post_bill_to_netsuite,
    post_bill_to_sap,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


@pytest.fixture(autouse=True)
def _enable_settlement_write(monkeypatch):
    # apply_settlement (cash-side ERP write) is hard-gated OFF by default
    # (FEATURE_ERP_SETTLEMENT_WRITE). These tests exercise the enabled write
    # path, so turn it on for the module. Only affects apply_settlement gating.
    monkeypatch.setenv("FEATURE_ERP_SETTLEMENT_WRITE", "true")


def _make_bill(**overrides) -> Bill:
    defaults = dict(
        vendor_id="V001",
        vendor_name="Test Vendor",
        amount=500.0,
        currency="USD",
        invoice_number="INV-001",
        invoice_date="2026-03-01",
        due_date="2026-03-31",
    )
    defaults.update(overrides)
    return Bill(**defaults)


def _quickbooks_connection(**overrides) -> ERPConnection:
    defaults = dict(
        type="quickbooks",
        access_token="tok_qb",
        refresh_token="rt_qb",
        realm_id="realm_abc",
        client_id="cid_qb",
        client_secret="secret_qb",
    )
    defaults.update(overrides)
    return ERPConnection(**defaults)


def _xero_connection(**overrides) -> ERPConnection:
    defaults = dict(
        type="xero",
        access_token="tok_xero",
        refresh_token="rt_xero",
        tenant_id="tenant_abc",
        client_id="cid_xero",
        client_secret="secret_xero",
    )
    defaults.update(overrides)
    return ERPConnection(**defaults)


def _netsuite_connection(**overrides) -> ERPConnection:
    defaults = dict(
        type="netsuite",
        account_id="NS123",
        consumer_key="ck",
        consumer_secret="cs",
        token_id="tid",
        token_secret="ts",
    )
    defaults.update(overrides)
    return ERPConnection(**defaults)


def _sap_connection(**overrides) -> ERPConnection:
    defaults = dict(
        type="sap",
        access_token="sap_session_token",
        base_url="https://sap.example.com/b1s/v2",
        company_code="SBODEMOUS",
    )
    defaults.update(overrides)
    return ERPConnection(**defaults)


# ---------------------------------------------------------------------------
# GL Account Mapping
# ---------------------------------------------------------------------------


def test_default_account_map_has_expenses_for_all_erps():
    for erp in ("quickbooks", "xero", "netsuite", "sap"):
        assert "expenses" in DEFAULT_ACCOUNT_MAP[erp], f"{erp} missing 'expenses' key"


def test_get_account_code_uses_custom_map():
    assert get_account_code("quickbooks", "expenses", {"expenses": "9999"}) == "9999"


def test_get_account_code_falls_back_to_default():
    assert get_account_code("quickbooks", "expenses") == "7"
    assert get_account_code("xero", "expenses") == "400"
    assert get_account_code("netsuite", "expenses") == "67"
    assert get_account_code("sap", "expenses") == "6000"


def test_get_org_gl_map_returns_empty_for_unknown_org(db):
    result = _get_org_gl_map("nonexistent-org")
    assert result == {}


def test_get_org_gl_map_reads_from_settings(db):
    org = db.ensure_organization("test-gl-org")
    gl_map = {"expenses": "8000", "cash": "1100"}
    settings = json.loads(org.get("settings_json") or "{}")
    settings["gl_account_map"] = gl_map
    db.update_organization("test-gl-org", settings_json=settings)

    result = _get_org_gl_map("test-gl-org")
    assert result == gl_map


# ---------------------------------------------------------------------------
# SAP Pre-flight Validation
# ---------------------------------------------------------------------------


def test_sap_preflight_rejects_missing_vendor_id():
    conn = ERPConnection(type="sap", access_token="tok", base_url="https://sap.example.com", company_code="1000")
    bill = _make_bill(vendor_id="")
    result = asyncio.run(post_bill_to_sap(conn, bill))
    assert result["status"] == "error"
    assert result["reason"] == "sap_validation_failed"
    assert "vendor_id" in result["missing_fields"]


def test_sap_preflight_rejects_zero_amount():
    conn = ERPConnection(type="sap", access_token="tok", base_url="https://sap.example.com", company_code="1000")
    bill = _make_bill(amount=0)
    result = asyncio.run(post_bill_to_sap(conn, bill))
    assert result["status"] == "error"
    assert "amount" in result["missing_fields"]


def test_sap_preflight_rejects_missing_company_code():
    conn = ERPConnection(type="sap", access_token="tok", base_url="https://sap.example.com")
    bill = _make_bill()
    result = asyncio.run(post_bill_to_sap(conn, bill))
    assert result["status"] == "error"
    assert "company_code" in result["missing_fields"]
    assert result["erp"] == "sap"


def test_sap_preflight_passes_valid_bill():
    """Verify SAP pre-flight passes and we reach the HTTP call (which we mock).

    B5 rewrote SAP posting to use session auth (Login → CSRF fetch → POST).
    With a non-base64 access_token, the legacy path treats it as a session cookie.
    We must mock both .get (CSRF fetch) and .post (invoice creation).
    """
    # B1 dispatcher heuristic in is_sap_s4hana_connection treats any
    # base_url WITHOUT ``/b1s/`` as S/4HANA. The B1 Service Layer flow
    # this test exercises requires the ``/b1s/v1`` path segment so the
    # dispatcher routes to ``_post_bill_to_sap_b1`` (which uses the
    # mocked POST/CSRF flow); without it the call routes to the S/4HANA
    # path which expects bill.line_items + a different mock surface.
    conn = ERPConnection(type="sap", access_token="tok", base_url="https://sap.example.com/b1s/v1", company_code="1000")
    bill = _make_bill()

    post_response = MagicMock()
    post_response.status_code = 200
    post_response.raise_for_status = MagicMock()
    post_response.json.return_value = {"DocEntry": "12345", "DocNum": "67890"}

    csrf_response = MagicMock()
    csrf_response.headers = {"x-csrf-token": "test-csrf-token"}

    # erp_sap was refactored to call ``get_http_client()`` (singleton)
    # rather than constructing ``httpx.AsyncClient()`` per call. Patch the
    # factory so it returns our mock; the rest of the flow (session
    # helper + post) remains unchanged.
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=post_response)
    fake_client.get = AsyncMock(return_value=csrf_response)

    with patch("solden.integrations.erp_sap.get_http_client", return_value=fake_client):
        result = asyncio.run(post_bill_to_sap(conn, bill))

    assert result["status"] == "success"
    assert result["erp"] == "sap"
    assert result["bill_id"] == "12345"


# ---------------------------------------------------------------------------
# Token Refresh Retry (QB + Xero)
# ---------------------------------------------------------------------------


def test_qb_token_refresh_retry_on_401(db):
    db.ensure_organization("org-test")

    first_call = True

    # Accept arbitrary kwargs so the mock keeps working when the real
    # ``post_bill_to_quickbooks`` signature gains optional kwargs
    # (Module 5 added ``field_mappings`` + ``custom_fields``). The test
    # asserts on the retry behaviour, not on the kwargs themselves.
    async def mock_post_bill_to_qb(conn, bill, **_kwargs):
        nonlocal first_call
        if first_call:
            first_call = False
            return {"status": "error", "erp": "quickbooks", "reason": "Token expired", "needs_reauth": True}
        return {"status": "success", "erp": "quickbooks", "bill_id": "123"}

    async def mock_refresh(conn):
        conn.access_token = "new_token"
        return "new_token"

    bill = _make_bill()

    with patch("solden.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("solden.integrations.erp_router.post_bill_to_quickbooks", side_effect=mock_post_bill_to_qb), \
         patch("solden.integrations.erp_router.refresh_quickbooks_token", side_effect=mock_refresh), \
         patch("solden.integrations.erp_router.set_erp_connection") as mock_set:
        mock_get_conn.return_value = ERPConnection(
            type="quickbooks", access_token="old", refresh_token="rt", realm_id="123",
            client_id="cid", client_secret="csec",
        )
        result = asyncio.run(post_bill("org-test", bill))

    assert result["status"] == "success"
    assert result["bill_id"] == "123"
    mock_set.assert_called_once()


def test_qb_token_refresh_failure_returns_original_error(db):
    db.ensure_organization("org-test")

    async def mock_post_bill_to_qb(conn, bill, **_kwargs):
        return {"status": "error", "erp": "quickbooks", "reason": "Token expired", "needs_reauth": True}

    async def mock_refresh_fail(conn):
        return None  # refresh failed

    bill = _make_bill()

    with patch("solden.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("solden.integrations.erp_router.post_bill_to_quickbooks", side_effect=mock_post_bill_to_qb), \
         patch("solden.integrations.erp_router.refresh_quickbooks_token", side_effect=mock_refresh_fail), \
         patch("solden.integrations.erp_router.set_erp_connection") as mock_set:
        mock_get_conn.return_value = ERPConnection(
            type="quickbooks", access_token="old", refresh_token="rt", realm_id="123",
            client_id="cid", client_secret="csec",
        )
        result = asyncio.run(post_bill("org-test", bill))

    assert result["status"] == "error"
    assert result["needs_reauth"] is True
    mock_set.assert_not_called()


def test_quickbooks_credit_application_uses_native_vendor_credit_and_bill_payment_api():
    vendor_credit_response = MagicMock()
    vendor_credit_response.status_code = 200
    vendor_credit_response.raise_for_status = MagicMock()
    vendor_credit_response.json.return_value = {"VendorCredit": {"Id": "vc-qb-1", "DocNumber": "VC-QB-100"}}

    bill_payment_response = MagicMock()
    bill_payment_response.status_code = 200
    bill_payment_response.raise_for_status = MagicMock()
    bill_payment_response.json.return_value = {"BillPayment": {"Id": "bp-qb-1"}}

    mock_client = AsyncMock()
    mock_client.post.side_effect = [vendor_credit_response, bill_payment_response]
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    application = CreditApplication(
        target_erp_reference="bill-qb-1",
        amount=75.0,
        currency="USD",
        credit_note_number="VC-QB-100",
    )

    bill_context = {
        "status": "success",
        "erp": "quickbooks",
        "bill_id": "bill-qb-1",
        "vendor_id": "vendor-qb-1",
        "ap_account_id": "33",
        "doc_number": "BILL-100",
    }

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_quickbooks_connection()), \
         patch("solden.integrations.erp_router.get_bill_quickbooks", AsyncMock(return_value=bill_context)), \
         patch("solden.integrations.erp_router.find_vendor_credit_quickbooks", AsyncMock(return_value=None)), \
         patch("solden.integrations.erp_quickbooks.get_http_client", return_value=mock_client):
        result = asyncio.run(
            apply_credit_note(
                "org-test",
                application,
                ap_item_id="ap-credit-qb-1",
                idempotency_key="idem-credit-qb-1",
            )
        )

    assert result["status"] == "success"
    assert result["erp"] == "quickbooks"
    assert result["erp_reference"] == "bp-qb-1"
    assert result["credit_note_reference"] == "vc-qb-1"
    assert result["ap_item_id"] == "ap-credit-qb-1"
    first_post = mock_client.post.await_args_list[0]
    # URL is path + idempotency requestid query param.
    assert "/vendorcredit" in first_post.args[0]
    assert "requestid=" in first_post.args[0], (
        f"vendor credit must carry requestid for QBO dedup, got {first_post.args[0]!r}"
    )
    assert first_post.kwargs["json"]["VendorRef"]["value"] == "vendor-qb-1"
    assert first_post.kwargs["json"]["APAccountRef"]["value"] == "33"
    assert first_post.kwargs["json"]["DocNumber"] == "VC-QB-100"
    second_post = mock_client.post.await_args_list[1]
    assert "/billpayment" in second_post.args[0]
    assert "requestid=" in second_post.args[0], (
        f"bill payment must carry requestid for QBO dedup, got {second_post.args[0]!r}"
    )
    linked_txns = second_post.kwargs["json"]["Line"][0]["LinkedTxn"]
    assert linked_txns[0]["TxnId"] == "bill-qb-1"
    assert linked_txns[0]["TxnType"] == "Bill"
    assert linked_txns[1]["TxnId"] == "vc-qb-1"
    assert linked_txns[1]["TxnType"] == "VendorCredit"


def test_quickbooks_settlement_uses_native_bill_payment_api():
    bill_payment_response = MagicMock()
    bill_payment_response.status_code = 200
    bill_payment_response.raise_for_status = MagicMock()
    bill_payment_response.json.return_value = {"BillPayment": {"Id": "bp-qb-2"}}

    mock_client = AsyncMock()
    mock_client.post.return_value = bill_payment_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    application = SettlementApplication(
        target_erp_reference="bill-qb-2",
        amount=55.0,
        currency="USD",
        source_reference="PAY-QB-200",
        source_document_type="receipt",
    )

    bill_context = {
        "status": "success",
        "erp": "quickbooks",
        "bill_id": "bill-qb-2",
        "vendor_id": "vendor-qb-2",
        "doc_number": "BILL-200",
    }

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_quickbooks_connection()), \
         patch("solden.integrations.erp_router.get_bill_quickbooks", AsyncMock(return_value=bill_context)), \
         patch("solden.integrations.erp_quickbooks.get_http_client", return_value=mock_client):
        result = asyncio.run(
            apply_settlement(
                "org-test",
                application,
                ap_item_id="ap-settlement-qb-1",
                idempotency_key="idem-settlement-qb-1",
            )
        )

    assert result["status"] == "success"
    assert result["erp"] == "quickbooks"
    assert result["erp_reference"] == "bp-qb-2"
    assert result["ap_item_id"] == "ap-settlement-qb-1"
    post_args = mock_client.post.await_args
    assert "/billpayment" in post_args.args[0]
    assert "requestid=" in post_args.args[0], (
        f"settlement must carry requestid for QBO dedup, got {post_args.args[0]!r}"
    )
    payment_payload = post_args.kwargs["json"]
    assert payment_payload["VendorRef"]["value"] == "vendor-qb-2"
    assert payment_payload["PayType"] == "Check"
    assert payment_payload["CheckPayment"]["BankAccountRef"]["value"] == "1"
    assert payment_payload["Line"][0]["LinkedTxn"][0]["TxnId"] == "bill-qb-2"
    assert payment_payload["Line"][0]["LinkedTxn"][0]["TxnType"] == "Bill"


def test_quickbooks_refund_settlement_stays_off_native_api():
    application = SettlementApplication(
        target_erp_reference="bill-qb-3",
        amount=20.0,
        currency="USD",
        source_reference="REF-QB-100",
        source_document_type="refund",
    )

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_quickbooks_connection()), \
         patch("solden.integrations.erp_quickbooks.get_http_client") as mock_client:
        result = asyncio.run(apply_settlement("org-test", application))

    assert result["status"] == "error"
    assert result["reason"] == "refund_settlement_api_not_available_for_connector"
    mock_client.assert_not_called()


def test_quickbooks_credit_application_refresh_retry_on_401(db):
    db.ensure_organization("org-test")

    first_call = True

    async def mock_apply_credit_to_qb(conn, application, gl_map=None, idempotency_key=None):
        nonlocal first_call
        if first_call:
            first_call = False
            return {"status": "error", "erp": "quickbooks", "reason": "Token expired", "needs_reauth": True}
        return {"status": "success", "erp": "quickbooks", "erp_reference": "bp-qb-401"}

    async def mock_refresh(conn):
        conn.access_token = "new_qb_token"
        return "new_qb_token"

    application = CreditApplication(
        target_erp_reference="bill-qb-4",
        amount=30.0,
        currency="USD",
        credit_note_number="VC-QB-401",
    )

    with patch("solden.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("solden.integrations.erp_router.apply_credit_note_to_quickbooks", side_effect=mock_apply_credit_to_qb), \
         patch("solden.integrations.erp_router.refresh_quickbooks_token", side_effect=mock_refresh), \
         patch("solden.integrations.erp_router.set_erp_connection") as mock_set:
        mock_get_conn.return_value = _quickbooks_connection(access_token="old_qb_token")
        result = asyncio.run(apply_credit_note("org-test", application, idempotency_key="idem-credit-qb-refresh"))

    assert result["status"] == "success"
    assert result["erp_reference"] == "bp-qb-401"
    mock_set.assert_called_once()


def test_quickbooks_settlement_refresh_retry_on_401(db):
    db.ensure_organization("org-test")

    first_call = True

    async def mock_apply_settlement_to_qb(conn, application, gl_map=None, idempotency_key=None):
        nonlocal first_call
        if first_call:
            first_call = False
            return {"status": "error", "erp": "quickbooks", "reason": "Token expired", "needs_reauth": True}
        return {"status": "success", "erp": "quickbooks", "erp_reference": "bp-qb-402"}

    async def mock_refresh(conn):
        conn.access_token = "new_qb_token"
        return "new_qb_token"

    application = SettlementApplication(
        target_erp_reference="bill-qb-5",
        amount=40.0,
        currency="USD",
        source_reference="PAY-QB-401",
        source_document_type="payment",
    )

    with patch("solden.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("solden.integrations.erp_router.apply_settlement_to_quickbooks", side_effect=mock_apply_settlement_to_qb), \
         patch("solden.integrations.erp_router.refresh_quickbooks_token", side_effect=mock_refresh), \
         patch("solden.integrations.erp_router.set_erp_connection") as mock_set:
        mock_get_conn.return_value = _quickbooks_connection(access_token="old_qb_token")
        result = asyncio.run(apply_settlement("org-test", application, idempotency_key="idem-settlement-qb-refresh"))

    assert result["status"] == "success"
    assert result["erp_reference"] == "bp-qb-402"
    mock_set.assert_called_once()


def test_xero_credit_application_uses_native_allocation_api():
    lookup_response = MagicMock()
    lookup_response.status_code = 200
    lookup_response.raise_for_status = MagicMock()
    lookup_response.json.return_value = {
        "CreditNotes": [
            {
                "CreditNoteID": "credit-xero-1",
                "CreditNoteNumber": "CN-100",
                "RemainingCredit": 120.0,
                "Status": "AUTHORISED",
            }
        ]
    }

    apply_response = MagicMock()
    apply_response.status_code = 200
    apply_response.raise_for_status = MagicMock()
    apply_response.json.return_value = {
        "Allocations": [
            {
                "AllocationID": "allocation-xero-1",
                "Amount": 75.0,
                "Invoice": {"InvoiceID": "bill-xero-1"},
            }
        ]
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = lookup_response
    mock_client.put.return_value = apply_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    application = CreditApplication(
        target_erp_reference="bill-xero-1",
        amount=75.0,
        currency="USD",
        credit_note_number="CN-100",
    )

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_xero_connection()), \
         patch("solden.integrations.erp_xero.get_http_client", return_value=mock_client):
        result = asyncio.run(
            apply_credit_note(
                "org-test",
                application,
                ap_item_id="ap-credit-1",
                idempotency_key="idem-credit-1",
            )
        )

    assert result["status"] == "success"
    assert result["erp"] == "xero"
    assert result["erp_reference"] == "allocation-xero-1"
    assert result["ap_item_id"] == "ap-credit-1"
    assert mock_client.get.await_args.kwargs["params"]["where"] == 'Type=="ACCPAYCREDIT" AND CreditNoteNumber=="CN-100"'
    put_kwargs = mock_client.put.await_args.kwargs
    assert put_kwargs["json"]["Allocations"][0]["Invoice"]["InvoiceID"] == "bill-xero-1"
    assert put_kwargs["json"]["Allocations"][0]["Amount"] == 75.0
    assert put_kwargs["headers"]["Idempotency-Key"] == "idem-credit-1"


def test_xero_settlement_uses_native_payment_api():
    payment_response = MagicMock()
    payment_response.status_code = 200
    payment_response.raise_for_status = MagicMock()
    payment_response.json.return_value = {
        "Payments": [
            {
                "PaymentID": "payment-xero-1",
                "Amount": 55.0,
                "Reference": "PAY-100",
            }
        ]
    }

    mock_client = AsyncMock()
    mock_client.put.return_value = payment_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    application = SettlementApplication(
        target_erp_reference="bill-xero-2",
        amount=55.0,
        currency="USD",
        source_reference="PAY-100",
        source_document_type="receipt",
    )

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_xero_connection()), \
         patch("solden.integrations.erp_xero.get_http_client", return_value=mock_client):
        result = asyncio.run(
            apply_settlement(
                "org-test",
                application,
                ap_item_id="ap-settlement-1",
                idempotency_key="idem-settlement-1",
            )
        )

    assert result["status"] == "success"
    assert result["erp_reference"] == "payment-xero-1"
    assert result["ap_item_id"] == "ap-settlement-1"
    put_kwargs = mock_client.put.await_args.kwargs
    payment = put_kwargs["json"]["Payments"][0]
    assert payment["Invoice"]["InvoiceID"] == "bill-xero-2"
    assert payment["Account"]["Code"] == "090"
    assert payment["Reference"] == "PAY-100"
    assert put_kwargs["headers"]["Idempotency-Key"] == "idem-settlement-1"


def test_xero_refund_settlement_stays_off_native_api():
    application = SettlementApplication(
        target_erp_reference="bill-xero-3",
        amount=20.0,
        currency="USD",
        source_reference="REF-100",
        source_document_type="refund",
    )

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_xero_connection()), \
         patch("solden.integrations.erp_xero.get_http_client") as mock_client:
        result = asyncio.run(apply_settlement("org-test", application))

    assert result["status"] == "error"
    assert result["reason"] == "refund_settlement_api_not_available_for_connector"
    mock_client.assert_not_called()


def test_xero_credit_application_refresh_retry_on_401(db):
    db.ensure_organization("org-test")

    first_call = True

    async def mock_apply_credit_to_xero(conn, application, idempotency_key=None):
        nonlocal first_call
        if first_call:
            first_call = False
            return {"status": "error", "erp": "xero", "reason": "Token expired", "needs_reauth": True}
        return {"status": "success", "erp": "xero", "erp_reference": "allocation-xero-2"}

    async def mock_refresh(conn):
        conn.access_token = "new_token"
        return "new_token"

    application = CreditApplication(
        target_erp_reference="bill-xero-4",
        amount=30.0,
        currency="USD",
        credit_note_number="CN-401",
    )

    with patch("solden.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("solden.integrations.erp_router.apply_credit_note_to_xero", side_effect=mock_apply_credit_to_xero), \
         patch("solden.integrations.erp_router.refresh_xero_token", side_effect=mock_refresh), \
         patch("solden.integrations.erp_router.set_erp_connection") as mock_set:
        mock_get_conn.return_value = _xero_connection(access_token="old_token")
        result = asyncio.run(apply_credit_note("org-test", application, idempotency_key="idem-credit-refresh"))

    assert result["status"] == "success"
    assert result["erp_reference"] == "allocation-xero-2"
    mock_set.assert_called_once()


def test_xero_settlement_refresh_retry_on_401(db):
    db.ensure_organization("org-test")

    first_call = True

    async def mock_apply_settlement_to_xero(conn, application, gl_map=None, idempotency_key=None):
        nonlocal first_call
        if first_call:
            first_call = False
            return {"status": "error", "erp": "xero", "reason": "Token expired", "needs_reauth": True}
        return {"status": "success", "erp": "xero", "erp_reference": "payment-xero-2"}

    async def mock_refresh(conn):
        conn.access_token = "new_token"
        return "new_token"

    application = SettlementApplication(
        target_erp_reference="bill-xero-5",
        amount=40.0,
        currency="USD",
        source_reference="PAY-401",
        source_document_type="payment",
    )

    with patch("solden.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("solden.integrations.erp_router.apply_settlement_to_xero", side_effect=mock_apply_settlement_to_xero), \
         patch("solden.integrations.erp_router.refresh_xero_token", side_effect=mock_refresh), \
         patch("solden.integrations.erp_router.set_erp_connection") as mock_set:
        mock_get_conn.return_value = _xero_connection(access_token="old_token")
        result = asyncio.run(apply_settlement("org-test", application, idempotency_key="idem-settlement-refresh"))

    assert result["status"] == "success"
    assert result["erp_reference"] == "payment-xero-2"
    mock_set.assert_called_once()


def test_netsuite_credit_application_uses_native_update_api():
    lookup_response = MagicMock()
    lookup_response.raise_for_status = MagicMock()
    lookup_response.json.return_value = {
        "items": [
            {
                "id": "credit-ns-1",
                "tranid": "VC-100",
                "amountremaining": 120.0,
                "entity": "vendor-ns-1",
            }
        ]
    }

    apply_response = MagicMock()
    apply_response.status_code = 204
    apply_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = lookup_response
    mock_client.patch.return_value = apply_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    application = CreditApplication(
        target_erp_reference="bill-ns-1",
        amount=75.0,
        currency="USD",
        credit_note_number="VC-100",
    )

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_netsuite_connection()), \
         patch("solden.integrations.erp_netsuite.get_http_client", return_value=mock_client):
        result = asyncio.run(
            apply_credit_note(
                "org-test",
                application,
                ap_item_id="ap-credit-ns-1",
                idempotency_key="idem-credit-ns-1",
            )
        )

    assert result["status"] == "success"
    assert result["erp"] == "netsuite"
    assert result["erp_reference"] == "credit-ns-1:bill-ns-1"
    assert result["ap_item_id"] == "ap-credit-ns-1"
    query = mock_client.post.await_args.kwargs["json"]["q"]
    assert "type = 'VendCred'" in query
    patch_payload = mock_client.patch.await_args.kwargs["json"]
    assert patch_payload["apply"]["items"][0]["doc"]["id"] == "bill-ns-1"
    assert patch_payload["apply"]["items"][0]["amount"] == 75.0


def test_netsuite_settlement_uses_native_vendor_payment_api():
    bill_response = MagicMock()
    bill_response.status_code = 200
    bill_response.raise_for_status = MagicMock()
    bill_response.json.return_value = {
        "id": "bill-ns-2",
        "tranId": "BILL-200",
        "entity": {"id": "vendor-ns-2"},
    }

    payment_response = MagicMock()
    payment_response.status_code = 200
    payment_response.raise_for_status = MagicMock()
    payment_response.json.return_value = {"id": "payment-ns-1"}

    mock_client = AsyncMock()
    mock_client.get.return_value = bill_response
    mock_client.post.return_value = payment_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    application = SettlementApplication(
        target_erp_reference="bill-ns-2",
        amount=55.0,
        currency="USD",
        source_reference="PAY-200",
        source_document_type="receipt",
    )

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_netsuite_connection()), \
         patch("solden.integrations.erp_netsuite.get_http_client", return_value=mock_client):
        result = asyncio.run(
            apply_settlement(
                "org-test",
                application,
                ap_item_id="ap-settlement-ns-1",
                idempotency_key="idem-settlement-ns-1",
            )
        )

    assert result["status"] == "success"
    assert result["erp_reference"] == "payment-ns-1"
    assert result["ap_item_id"] == "ap-settlement-ns-1"
    payment_payload = mock_client.post.await_args.kwargs["json"]
    assert payment_payload["entity"]["id"] == "vendor-ns-2"
    assert payment_payload["account"]["id"] == "1000"
    assert payment_payload["apply"]["items"][0]["doc"]["id"] == "bill-ns-2"
    assert payment_payload["apply"]["items"][0]["amount"] == 55.0


def test_netsuite_refund_settlement_stays_off_native_api():
    application = SettlementApplication(
        target_erp_reference="bill-ns-3",
        amount=20.0,
        currency="USD",
        source_reference="REF-200",
        source_document_type="refund",
    )

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_netsuite_connection()), \
         patch("solden.integrations.erp_netsuite.get_http_client") as mock_client:
        result = asyncio.run(apply_settlement("org-test", application))

    assert result["status"] == "error"
    assert result["reason"] == "refund_settlement_api_not_available_for_connector"
    mock_client.assert_not_called()


def test_netsuite_credit_application_retry_on_401():
    first_call = True

    async def mock_apply_credit_to_netsuite(conn, application, idempotency_key=None):
        nonlocal first_call
        if first_call:
            first_call = False
            return {"status": "error", "erp": "netsuite", "reason": "Authentication failed", "needs_reauth": True}
        return {"status": "success", "erp": "netsuite", "erp_reference": "credit-ns-2:bill-ns-4"}

    application = CreditApplication(
        target_erp_reference="bill-ns-4",
        amount=30.0,
        currency="USD",
        credit_note_number="VC-401",
    )

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_netsuite_connection()), \
         patch("solden.integrations.erp_router.apply_credit_note_to_netsuite", side_effect=mock_apply_credit_to_netsuite):
        result = asyncio.run(apply_credit_note("org-test", application, idempotency_key="idem-credit-ns-refresh"))

    assert result["status"] == "success"
    assert result["erp_reference"] == "credit-ns-2:bill-ns-4"


def test_netsuite_settlement_retry_on_401():
    first_call = True

    async def mock_apply_settlement_to_netsuite(conn, application, gl_map=None, idempotency_key=None):
        nonlocal first_call
        if first_call:
            first_call = False
            return {"status": "error", "erp": "netsuite", "reason": "Authentication failed", "needs_reauth": True}
        return {"status": "success", "erp": "netsuite", "erp_reference": "payment-ns-2"}

    application = SettlementApplication(
        target_erp_reference="bill-ns-5",
        amount=40.0,
        currency="USD",
        source_reference="PAY-401",
        source_document_type="payment",
    )

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_netsuite_connection()), \
         patch("solden.integrations.erp_router.apply_settlement_to_netsuite", side_effect=mock_apply_settlement_to_netsuite):
        result = asyncio.run(apply_settlement("org-test", application, idempotency_key="idem-settlement-ns-refresh"))

    assert result["status"] == "success"
    assert result["erp_reference"] == "payment-ns-2"


# ---------------------------------------------------------------------------
# SAP Native Follow-on Paths
# ---------------------------------------------------------------------------


def test_sap_credit_application_uses_native_purchase_credit_note_api():
    csrf_response = MagicMock()
    csrf_response.status_code = 200
    csrf_response.raise_for_status = MagicMock()
    csrf_response.headers = {"x-csrf-token": "csrf-token-1"}

    create_response = MagicMock()
    create_response.status_code = 201
    create_response.raise_for_status = MagicMock()
    create_response.json.return_value = {"DocEntry": "credit-sap-1", "DocNum": "9001"}

    mock_client = AsyncMock()
    mock_client.get.return_value = csrf_response
    mock_client.post.return_value = create_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    application = CreditApplication(
        target_erp_reference="123",
        amount=75.0,
        currency="USD",
        credit_note_number="CN-SAP-100",
    )

    bill_context = {
        "status": "success",
        "erp": "sap",
        "bill_id": "123",
        "vendor_id": "V001",
        "doc_num": "SAP-BILL-1",
        "doc_total": 120.0,
        "document_lines": [
            {"LineNum": 0, "LineTotal": 50.0, "AccountCode": "6000"},
            {"LineNum": 1, "LineTotal": 70.0, "AccountCode": "6000"},
        ],
    }

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_sap_connection()), \
         patch("solden.integrations.erp_router.find_credit_note_sap", AsyncMock(return_value=None)), \
         patch("solden.integrations.erp_router.get_purchase_invoice_sap", AsyncMock(return_value=bill_context)), \
         patch("solden.integrations.erp_sap.get_http_client", return_value=mock_client):
        result = asyncio.run(
            apply_credit_note(
                "org-test",
                application,
                ap_item_id="ap-credit-sap-1",
                idempotency_key="idem-credit-sap-1",
            )
        )

    assert result["status"] == "success"
    assert result["erp"] == "sap"
    assert result["erp_reference"] == "credit-sap-1"
    assert result["ap_item_id"] == "ap-credit-sap-1"
    post_args = mock_client.post.await_args
    assert post_args.args[0].endswith("/PurchaseCreditNotes")
    credit_payload = post_args.kwargs["json"]
    assert credit_payload["CardCode"] == "V001"
    assert credit_payload["NumAtCard"] == "CN-SAP-100"
    assert len(credit_payload["DocumentLines"]) == 2
    assert credit_payload["DocumentLines"][0]["BaseEntry"] == 123
    assert credit_payload["DocumentLines"][0]["BaseLine"] == 0
    assert credit_payload["DocumentLines"][0]["LineTotal"] == 50.0
    assert credit_payload["DocumentLines"][1]["BaseLine"] == 1
    assert credit_payload["DocumentLines"][1]["LineTotal"] == 25.0


def test_sap_settlement_uses_native_vendor_payment_api():
    csrf_response = MagicMock()
    csrf_response.status_code = 200
    csrf_response.raise_for_status = MagicMock()
    csrf_response.headers = {"x-csrf-token": "csrf-token-2"}

    payment_response = MagicMock()
    payment_response.status_code = 201
    payment_response.raise_for_status = MagicMock()
    payment_response.json.return_value = {"DocEntry": "payment-sap-1", "DocNum": "9101"}

    mock_client = AsyncMock()
    mock_client.get.return_value = csrf_response
    mock_client.post.return_value = payment_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    application = SettlementApplication(
        target_erp_reference="124",
        amount=55.0,
        currency="USD",
        source_reference="PAY-SAP-200",
        source_document_type="receipt",
    )

    bill_context = {
        "status": "success",
        "erp": "sap",
        "bill_id": "124",
        "vendor_id": "V002",
        "doc_num": "SAP-BILL-2",
        "document_lines": [],
    }

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_sap_connection()), \
         patch("solden.integrations.erp_router.get_purchase_invoice_sap", AsyncMock(return_value=bill_context)), \
         patch("solden.integrations.erp_sap.get_http_client", return_value=mock_client):
        result = asyncio.run(
            apply_settlement(
                "org-test",
                application,
                ap_item_id="ap-settlement-sap-1",
                idempotency_key="idem-settlement-sap-1",
            )
        )

    assert result["status"] == "success"
    assert result["erp"] == "sap"
    assert result["erp_reference"] == "payment-sap-1"
    assert result["ap_item_id"] == "ap-settlement-sap-1"
    post_args = mock_client.post.await_args
    assert post_args.args[0].endswith("/VendorPayments")
    payment_payload = post_args.kwargs["json"]
    assert payment_payload["CardCode"] == "V002"
    assert payment_payload["DocType"] == "rSupplier"
    assert payment_payload["TransferAccount"] == "1000"
    assert payment_payload["TransferSum"] == 55.0
    assert payment_payload["Invoices"][0]["DocEntry"] == 124
    assert payment_payload["Invoices"][0]["InvoiceType"] == "it_PurchaseInvoice"
    assert payment_payload["Invoices"][0]["SumApplied"] == 55.0


def test_sap_refund_settlement_stays_off_native_api():
    application = SettlementApplication(
        target_erp_reference="125",
        amount=20.0,
        currency="USD",
        source_reference="REF-SAP-1",
        source_document_type="refund",
    )

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_sap_connection()), \
         patch("solden.integrations.erp_sap.get_http_client") as mock_client:
        result = asyncio.run(apply_settlement("org-test", application))

    assert result["status"] == "error"
    assert result["reason"] == "refund_settlement_api_not_available_for_connector"
    mock_client.assert_not_called()


def test_sap_credit_application_retry_on_401():
    first_call = True

    async def mock_apply_credit_to_sap(conn, application, idempotency_key=None):
        nonlocal first_call
        if first_call:
            first_call = False
            return {"status": "error", "erp": "sap", "reason": "authentication_failed", "needs_reauth": True}
        return {"status": "success", "erp": "sap", "erp_reference": "credit-sap-2"}

    application = CreditApplication(
        target_erp_reference="126",
        amount=30.0,
        currency="USD",
        credit_note_number="CN-SAP-401",
    )

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_sap_connection()), \
         patch("solden.integrations.erp_router.apply_credit_note_to_sap", side_effect=mock_apply_credit_to_sap):
        result = asyncio.run(apply_credit_note("org-test", application, idempotency_key="idem-credit-sap-refresh"))

    assert result["status"] == "success"
    assert result["erp_reference"] == "credit-sap-2"


def test_sap_settlement_retry_on_401():
    first_call = True

    async def mock_apply_settlement_to_sap(conn, application, gl_map=None, idempotency_key=None):
        nonlocal first_call
        if first_call:
            first_call = False
            return {"status": "error", "erp": "sap", "reason": "authentication_failed", "needs_reauth": True}
        return {"status": "success", "erp": "sap", "erp_reference": "payment-sap-2"}

    application = SettlementApplication(
        target_erp_reference="127",
        amount=40.0,
        currency="USD",
        source_reference="PAY-SAP-401",
        source_document_type="payment",
    )

    with patch("solden.integrations.erp_router.get_erp_connection", return_value=_sap_connection()), \
         patch("solden.integrations.erp_router.apply_settlement_to_sap", side_effect=mock_apply_settlement_to_sap):
        result = asyncio.run(apply_settlement("org-test", application, idempotency_key="idem-settlement-sap-refresh"))

    assert result["status"] == "success"
    assert result["erp_reference"] == "payment-sap-2"


# ---------------------------------------------------------------------------
# NetSuite Error Normalization
# ---------------------------------------------------------------------------


def test_netsuite_error_includes_erp_key():
    conn = ERPConnection(type="netsuite")
    bill = _make_bill()
    result = asyncio.run(post_bill_to_netsuite(conn, bill))
    assert result["erp"] == "netsuite"
    assert result["status"] == "error"
    assert "details" not in result


def test_netsuite_accepts_gl_map():
    """Verify NetSuite function accepts gl_map parameter."""
    conn = ERPConnection(type="netsuite")
    bill = _make_bill()
    result = asyncio.run(post_bill_to_netsuite(conn, bill, gl_map={"expenses": "999"}))
    # Will fail due to missing account_id, but should accept the parameter
    assert result["erp"] == "netsuite"


# ---------------------------------------------------------------------------
# ERPConnection.company_code
# ---------------------------------------------------------------------------


def test_erp_connection_has_company_code():
    conn = ERPConnection(type="sap", company_code="1000")
    assert conn.company_code == "1000"


def test_erp_connection_company_code_defaults_none():
    conn = ERPConnection(type="sap")
    assert conn.company_code is None


# ---------------------------------------------------------------------------
# Redirect Path Traversal
# ---------------------------------------------------------------------------


def test_redirect_path_rejects_double_slash():
    from solden.api.auth import _sanitize_redirect_path
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        _sanitize_redirect_path("//attacker.com/phishing")
    assert exc_info.value.status_code == 400


def test_redirect_path_allows_valid_path():
    from solden.api.auth import _sanitize_redirect_path
    assert _sanitize_redirect_path("/dashboard") == "/dashboard"
    assert _sanitize_redirect_path("/") == "/"


# ---------------------------------------------------------------------------
# Bill-posting idempotency end-to-end (audit pass 1)
# ---------------------------------------------------------------------------


def test_post_bill_to_quickbooks_appends_requestid_query_param_for_idempotency():
    """Pre-fix, ``post_bill_to_quickbooks`` accepted no idempotency
    parameter, so a transient timeout + retry created a duplicate
    Bill in QBO. Now the function accepts ``idempotency_key`` and
    forwards it to Intuit's ``requestid`` query parameter (max 50
    chars), which QBO uses to dedupe.
    """
    from solden.integrations.erp_quickbooks import post_bill_to_quickbooks

    success_response = MagicMock()
    success_response.status_code = 200
    success_response.raise_for_status = MagicMock()
    success_response.json.return_value = {"Bill": {"Id": "qb-bill-1", "DocNumber": "INV-001", "SyncToken": "0"}}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=success_response)

    conn = ERPConnection(
        type="quickbooks", access_token="tok", realm_id="123",
        client_id="cid", client_secret="csec",
    )
    bill = _make_bill()

    with patch("solden.integrations.erp_quickbooks.get_http_client", return_value=mock_client):
        result = asyncio.run(post_bill_to_quickbooks(
            conn, bill, idempotency_key="auto:ap-99:erp_post",
        ))

    assert result["status"] == "success"
    # The URL passed to client.post must carry ?requestid=auto:ap-99:erp_post
    call_args = mock_client.post.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert "requestid=auto:ap-99:erp_post" in url, (
        f"expected requestid query param on QB URL, got {url!r}"
    )


def test_post_bill_to_xero_sends_idempotency_key_header():
    """Xero adapter must forward ``idempotency_key`` via the
    ``Idempotency-Key`` header (Xero's native dedupe mechanism).
    Pre-fix, the function ignored the kwarg entirely.
    """
    from solden.integrations.erp_xero import post_bill_to_xero

    success_response = MagicMock()
    success_response.status_code = 200
    success_response.raise_for_status = MagicMock()
    success_response.json.return_value = {
        "Invoices": [
            {"InvoiceID": "xero-inv-1", "InvoiceNumber": "INV-001", "Status": "AUTHORISED"}
        ],
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=success_response)

    conn = ERPConnection(
        type="xero", access_token="tok", tenant_id="ten-1",
        client_id="cid", client_secret="csec",
    )
    bill = _make_bill()

    with patch("solden.integrations.erp_xero.get_http_client", return_value=mock_client):
        result = asyncio.run(post_bill_to_xero(
            conn, bill, idempotency_key="auto:ap-77:erp_post",
        ))

    assert result["status"] == "success"
    call_args = mock_client.post.call_args
    headers = call_args.kwargs.get("headers", {})
    assert headers.get("Idempotency-Key") == "auto:ap-77:erp_post", (
        f"Xero adapter must send Idempotency-Key header; got headers={headers!r}"
    )


def test_post_bill_to_netsuite_returns_validation_failed_on_missing_vendor():
    """Pre-fix, NetSuite let missing-vendor / zero-amount bills reach
    the API and returned a generic NetSuite error code; SAP returned
    the structured ``sap_validation_failed`` shape with
    ``missing_fields``. The two adapters now share the shape: NetSuite
    returns ``netsuite_validation_failed`` with the same
    ``missing_fields`` list.
    """
    from solden.integrations.erp_netsuite import post_bill_to_netsuite

    # vendor_id missing on the bill
    bill = _make_bill(vendor_id="", amount=500.0, currency="USD")
    conn = ERPConnection(
        type="netsuite", access_token="tok", account_id="acct-1",
        consumer_key="ck", consumer_secret="cs",
        token_id="tid", token_secret="tsec",
    )

    result = asyncio.run(post_bill_to_netsuite(conn, bill))

    assert result["status"] == "error"
    assert result["erp"] == "netsuite"
    assert result["reason"] == "netsuite_validation_failed"
    assert "vendor_id" in result["missing_fields"]


def test_post_bill_to_netsuite_returns_validation_failed_on_missing_subsidiary_oneworld():
    """OneWorld tenants require a subsidiary on every Vendor Bill.
    Pre-fix, a missing subsidiary on a OneWorld connection silently
    posted to the wrong entity. Pre-flight now refuses."""
    from solden.integrations.erp_netsuite import post_bill_to_netsuite

    bill = _make_bill(vendor_id="V001", amount=500.0, currency="USD")
    conn = ERPConnection(
        type="netsuite", access_token="tok", account_id="acct-1",
        consumer_key="ck", consumer_secret="cs",
        token_id="tid", token_secret="tsec",
        # subsidiary_id is None (default) — should fail when is_oneworld=True
    )
    # Stamp is_oneworld=True via attribute (ERPConnection doesn't expose
    # it as a dataclass field; the helper reads via getattr).
    conn.is_oneworld = True  # type: ignore[attr-defined]

    result = asyncio.run(post_bill_to_netsuite(conn, bill))

    assert result["status"] == "error"
    assert result["reason"] == "netsuite_validation_failed"
    assert "subsidiary_id" in result["missing_fields"]


def test_post_bill_to_sap_b1_validates_currency_length():
    """Pre-fix, SAP B1 stripped currency without checking length —
    a missing/blank currency posted ``DocCurrency: ""`` and burned an
    API roundtrip on a generic 400. Now blocked at pre-flight with a
    structured ``missing_fields`` list."""
    from solden.integrations.erp_sap import post_bill_to_sap

    # currency is empty — should fail pre-flight
    bill = _make_bill(vendor_id="V001", amount=500.0)
    bill.currency = ""  # type: ignore[attr-defined]
    conn = ERPConnection(
        type="sap", access_token="tok",
        base_url="https://sap-b1.example.com:50000/b1s/v1",
        company_code="1000",
    )

    result = asyncio.run(post_bill_to_sap(conn, bill))

    assert result["status"] == "error"
    assert result["reason"] == "sap_validation_failed"
    assert "currency" in result["missing_fields"]


def test_post_bill_to_quickbooks_does_not_leak_raw_response_body_on_non_json_error():
    """Memory rule: no raw response bodies in returned values. Pre-fix
    a non-JSON error response (HTML 502, gateway error, auth-redirect
    body) was sliced [:200] and returned in ``erp_error_detail`` —
    which lands on AP item ``last_error`` and the admin console.
    Those bodies can carry partial bearer tokens echoed in headers,
    internal hostnames, or PII from the bill payload. The detail
    field now carries an opaque ``http_<status>_non_json_response``
    placeholder; the truncated body is logged at debug for ops.
    """
    from solden.integrations.erp_quickbooks import post_bill_to_quickbooks
    import httpx

    sensitive_body = (
        "<html><body>500 Internal Server Error\n"
        "Bearer abc.def.ghi (leaked-token-fragment)\n"
        "internal-host: prod-qbo-host-1.intuit.svc"
        "</body></html>"
    )

    class _LeakyResp:
        status_code = 502
        text = sensitive_body

        def json(self):
            raise ValueError("not json")

        def raise_for_status(self):
            req = httpx.Request("POST", "https://quickbooks.api.intuit.com/v3/company/x/bill")
            resp = httpx.Response(502, request=req)
            raise httpx.HTTPStatusError("502", request=req, response=self)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_LeakyResp())

    conn = ERPConnection(
        type="quickbooks", access_token="tok", realm_id="123",
        client_id="cid", client_secret="csec",
    )
    bill = _make_bill()

    with patch("solden.integrations.erp_quickbooks.get_http_client", return_value=mock_client):
        result = asyncio.run(post_bill_to_quickbooks(conn, bill))

    assert result["status"] == "error"
    detail = str(result.get("erp_error_detail") or "")
    # The opaque placeholder is the only thing returned.
    assert detail == "http_502_non_json_response", (
        f"unexpected erp_error_detail: {detail!r}"
    )
    # None of the sensitive fragments appear anywhere in the result.
    flat = str(result)
    assert "Bearer" not in flat
    assert "leaked-token-fragment" not in flat
    assert "internal-host" not in flat
    assert "prod-qbo-host-1" not in flat


def test_verify_bill_posted_returns_indeterminate_on_finder_exception(db):
    """Pre-fix, ``verify_bill_posted`` returned ``verified=True`` on
    finder lookup error / rate-limit / no-finder branches — making
    the verifier statistically useless under exactly the conditions
    where verification matters most. Now those branches return
    ``verified=False, indeterminate=True`` so the caller can route
    to manual reconciliation rather than treating an unverified
    post as confirmed.
    """
    from solden.integrations.erp_router import verify_bill_posted

    db.ensure_organization("org-test")

    # finder raises → indeterminate
    async def exploding_finder(connection, invoice_number):
        raise RuntimeError("ERP API down")

    with patch("solden.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch.dict(
             "solden.integrations.erp_router._BILL_FINDERS",
             {"quickbooks": exploding_finder},
         ):
        mock_get_conn.return_value = ERPConnection(
            type="quickbooks", access_token="tok", realm_id="123",
            client_id="cid", client_secret="csec",
        )
        result = asyncio.run(verify_bill_posted(
            "org-test", "INV-001", expected_amount=500.0,
        ))

    assert result["verified"] is False
    assert result.get("indeterminate") is True
    assert result["reason"].startswith("lookup_error:")


def test_decision_action_lock_fails_closed_on_persist_exception():
    """Pre-fix, when ``append_audit_event`` raised AND the re-check
    also raised, ``_acquire_decision_action_lock`` returned ``True``
    (fail-open) — letting both an original click and a duplicate
    Slack double-firing reach ``_post_to_erp``. Combined with the
    state-guard TOCTOU race, that's how a Slack double-click became
    a duplicate ERP post. Now fails CLOSED on persist exception:
    the cost of one false-positive 'duplicate locked' is far less
    than the cost of a duplicate bill in the customer's ERP.
    """
    from solden.services.invoice_validation import InvoiceValidationMixin

    svc = InvoiceValidationMixin.__new__(InvoiceValidationMixin)
    svc.organization_id = "org_lock_test"
    fail_count = {"persist": 0, "recheck": 0}

    class _ExplodingDB:
        def get_ap_audit_event_by_key(self_inner, key):
            fail_count["recheck"] += 1
            raise RuntimeError("DB unavailable for re-check too")

        def append_audit_event(self_inner, payload):
            fail_count["persist"] += 1
            raise RuntimeError("DB unavailable for lock persist")

    svc.db = _ExplodingDB()  # type: ignore[attr-defined]

    # When both the persist and the re-check fail, must return False
    # (fail-closed). Pre-fix returned True (fail-open).
    result = svc._acquire_decision_action_lock(
        ap_item_id="ap-lock-1",
        decision_idempotency_key="key-double-click",
        actor_id="alice@co",
        source_channel="slack",
    )
    assert result is False, (
        "lock must fail-closed when persist AND re-check both fail; "
        f"persist_calls={fail_count['persist']} recheck_calls={fail_count['recheck']}"
    )
    # Both code paths (persist attempt + re-check attempt) must have run.
    assert fail_count["persist"] == 1
    assert fail_count["recheck"] >= 1


def test_post_bill_router_threads_idempotency_key_to_quickbooks(db):
    """The router-level ``post_bill`` accepts ``idempotency_key`` and
    must forward it to the per-ERP adapter. Pre-fix, the router accepted
    the kwarg at the entry but dropped it before the adapter call —
    every adapter call posted without dedupe.
    """
    db.ensure_organization("org-test")

    captured_kwargs: dict = {}

    async def mock_qb(conn, bill, **kwargs):
        captured_kwargs.update(kwargs)
        return {"status": "success", "erp": "quickbooks", "bill_id": "rb-1"}

    bill = _make_bill()

    with patch("solden.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("solden.integrations.erp_router.post_bill_to_quickbooks", side_effect=mock_qb):
        mock_get_conn.return_value = ERPConnection(
            type="quickbooks", access_token="tok", realm_id="123",
            client_id="cid", client_secret="csec",
        )
        result = asyncio.run(post_bill(
            "org-test", bill, idempotency_key="auto:ap-router-1:erp_post",
        ))

    assert result["status"] == "success"
    assert captured_kwargs.get("idempotency_key") == "auto:ap-router-1:erp_post", (
        f"router must thread idempotency_key to adapter; got {captured_kwargs!r}"
    )


def test_apply_settlement_disabled_by_default_moves_no_money(monkeypatch):
    """The money-movement invariant: with FEATURE_ERP_SETTLEMENT_WRITE off, the
    cash-side ERP write never fires — apply_settlement short-circuits to skipped
    and no adapter is reached. (Overrides the module's autouse enable.)"""
    monkeypatch.setenv("FEATURE_ERP_SETTLEMENT_WRITE", "false")
    application = SettlementApplication(
        target_erp_reference="BILL-1", amount=100.0, currency="USD",
        source_document_type="payment",
    )
    qb_spy = AsyncMock()
    with patch("solden.integrations.erp_router.get_erp_connection",
               return_value=_quickbooks_connection()), \
         patch("solden.integrations.erp_router.apply_settlement_to_quickbooks", qb_spy):
        result = asyncio.run(apply_settlement("org-test", application))
    assert result["status"] == "skipped"
    assert result["reason"] == "settlement_write_disabled"
    qb_spy.assert_not_called()
