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

from clearledgr.core import database as db_module
from clearledgr.integrations.erp_router import (
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
    conn = ERPConnection(type="sap", access_token="tok", base_url="https://sap.example.com", company_code="1000")
    bill = _make_bill()

    post_response = MagicMock()
    post_response.status_code = 200
    post_response.raise_for_status = MagicMock()
    post_response.json.return_value = {"DocEntry": "12345", "DocNum": "67890"}

    csrf_response = MagicMock()
    csrf_response.headers = {"x-csrf-token": "test-csrf-token"}

    with patch("clearledgr.integrations.erp_router.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.return_value.post = AsyncMock(return_value=post_response)
        mock_client.return_value.get = AsyncMock(return_value=csrf_response)

        result = asyncio.run(post_bill_to_sap(conn, bill))

    assert result["status"] == "success"
    assert result["erp"] == "sap"
    assert result["bill_id"] == "12345"


# ---------------------------------------------------------------------------
# Token Refresh Retry (QB + Xero)
# ---------------------------------------------------------------------------


def test_qb_token_refresh_retry_on_401(db):
    db.ensure_organization("default")

    first_call = True

    async def mock_post_bill_to_qb(conn, bill, gl_map=None):
        nonlocal first_call
        if first_call:
            first_call = False
            return {"status": "error", "erp": "quickbooks", "reason": "Token expired", "needs_reauth": True}
        return {"status": "success", "erp": "quickbooks", "bill_id": "123"}

    async def mock_refresh(conn):
        conn.access_token = "new_token"
        return "new_token"

    bill = _make_bill()

    with patch("clearledgr.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("clearledgr.integrations.erp_router.post_bill_to_quickbooks", side_effect=mock_post_bill_to_qb), \
         patch("clearledgr.integrations.erp_router.refresh_quickbooks_token", side_effect=mock_refresh), \
         patch("clearledgr.integrations.erp_router.set_erp_connection") as mock_set:
        mock_get_conn.return_value = ERPConnection(
            type="quickbooks", access_token="old", refresh_token="rt", realm_id="123",
            client_id="cid", client_secret="csec",
        )
        result = asyncio.run(post_bill("default", bill))

    assert result["status"] == "success"
    assert result["bill_id"] == "123"
    mock_set.assert_called_once()


def test_qb_token_refresh_failure_returns_original_error(db):
    db.ensure_organization("default")

    async def mock_post_bill_to_qb(conn, bill, gl_map=None):
        return {"status": "error", "erp": "quickbooks", "reason": "Token expired", "needs_reauth": True}

    async def mock_refresh_fail(conn):
        return None  # refresh failed

    bill = _make_bill()

    with patch("clearledgr.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("clearledgr.integrations.erp_router.post_bill_to_quickbooks", side_effect=mock_post_bill_to_qb), \
         patch("clearledgr.integrations.erp_router.refresh_quickbooks_token", side_effect=mock_refresh_fail), \
         patch("clearledgr.integrations.erp_router.set_erp_connection") as mock_set:
        mock_get_conn.return_value = ERPConnection(
            type="quickbooks", access_token="old", refresh_token="rt", realm_id="123",
            client_id="cid", client_secret="csec",
        )
        result = asyncio.run(post_bill("default", bill))

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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_quickbooks_connection()), \
         patch("clearledgr.integrations.erp_router.get_bill_quickbooks", AsyncMock(return_value=bill_context)), \
         patch("clearledgr.integrations.erp_router.find_vendor_credit_quickbooks", AsyncMock(return_value=None)), \
         patch("clearledgr.integrations.erp_router.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(
            apply_credit_note(
                "default",
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
    assert first_post.args[0].endswith("/vendorcredit")
    assert first_post.kwargs["json"]["VendorRef"]["value"] == "vendor-qb-1"
    assert first_post.kwargs["json"]["APAccountRef"]["value"] == "33"
    assert first_post.kwargs["json"]["DocNumber"] == "VC-QB-100"
    second_post = mock_client.post.await_args_list[1]
    assert second_post.args[0].endswith("/billpayment")
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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_quickbooks_connection()), \
         patch("clearledgr.integrations.erp_router.get_bill_quickbooks", AsyncMock(return_value=bill_context)), \
         patch("clearledgr.integrations.erp_router.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(
            apply_settlement(
                "default",
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
    assert post_args.args[0].endswith("/billpayment")
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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_quickbooks_connection()), \
         patch("clearledgr.integrations.erp_router.httpx.AsyncClient") as mock_client:
        result = asyncio.run(apply_settlement("default", application))

    assert result["status"] == "error"
    assert result["reason"] == "refund_settlement_api_not_available_for_connector"
    mock_client.assert_not_called()


def test_quickbooks_credit_application_refresh_retry_on_401(db):
    db.ensure_organization("default")

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

    with patch("clearledgr.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("clearledgr.integrations.erp_router.apply_credit_note_to_quickbooks", side_effect=mock_apply_credit_to_qb), \
         patch("clearledgr.integrations.erp_router.refresh_quickbooks_token", side_effect=mock_refresh), \
         patch("clearledgr.integrations.erp_router.set_erp_connection") as mock_set:
        mock_get_conn.return_value = _quickbooks_connection(access_token="old_qb_token")
        result = asyncio.run(apply_credit_note("default", application, idempotency_key="idem-credit-qb-refresh"))

    assert result["status"] == "success"
    assert result["erp_reference"] == "bp-qb-401"
    mock_set.assert_called_once()


def test_quickbooks_settlement_refresh_retry_on_401(db):
    db.ensure_organization("default")

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

    with patch("clearledgr.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("clearledgr.integrations.erp_router.apply_settlement_to_quickbooks", side_effect=mock_apply_settlement_to_qb), \
         patch("clearledgr.integrations.erp_router.refresh_quickbooks_token", side_effect=mock_refresh), \
         patch("clearledgr.integrations.erp_router.set_erp_connection") as mock_set:
        mock_get_conn.return_value = _quickbooks_connection(access_token="old_qb_token")
        result = asyncio.run(apply_settlement("default", application, idempotency_key="idem-settlement-qb-refresh"))

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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_xero_connection()), \
         patch("clearledgr.integrations.erp_router.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(
            apply_credit_note(
                "default",
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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_xero_connection()), \
         patch("clearledgr.integrations.erp_router.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(
            apply_settlement(
                "default",
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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_xero_connection()), \
         patch("clearledgr.integrations.erp_router.httpx.AsyncClient") as mock_client:
        result = asyncio.run(apply_settlement("default", application))

    assert result["status"] == "error"
    assert result["reason"] == "refund_settlement_api_not_available_for_connector"
    mock_client.assert_not_called()


def test_xero_credit_application_refresh_retry_on_401(db):
    db.ensure_organization("default")

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

    with patch("clearledgr.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("clearledgr.integrations.erp_router.apply_credit_note_to_xero", side_effect=mock_apply_credit_to_xero), \
         patch("clearledgr.integrations.erp_router.refresh_xero_token", side_effect=mock_refresh), \
         patch("clearledgr.integrations.erp_router.set_erp_connection") as mock_set:
        mock_get_conn.return_value = _xero_connection(access_token="old_token")
        result = asyncio.run(apply_credit_note("default", application, idempotency_key="idem-credit-refresh"))

    assert result["status"] == "success"
    assert result["erp_reference"] == "allocation-xero-2"
    mock_set.assert_called_once()


def test_xero_settlement_refresh_retry_on_401(db):
    db.ensure_organization("default")

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

    with patch("clearledgr.integrations.erp_router.get_erp_connection") as mock_get_conn, \
         patch("clearledgr.integrations.erp_router.apply_settlement_to_xero", side_effect=mock_apply_settlement_to_xero), \
         patch("clearledgr.integrations.erp_router.refresh_xero_token", side_effect=mock_refresh), \
         patch("clearledgr.integrations.erp_router.set_erp_connection") as mock_set:
        mock_get_conn.return_value = _xero_connection(access_token="old_token")
        result = asyncio.run(apply_settlement("default", application, idempotency_key="idem-settlement-refresh"))

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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_netsuite_connection()), \
         patch("clearledgr.integrations.erp_router.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(
            apply_credit_note(
                "default",
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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_netsuite_connection()), \
         patch("clearledgr.integrations.erp_router.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(
            apply_settlement(
                "default",
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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_netsuite_connection()), \
         patch("clearledgr.integrations.erp_router.httpx.AsyncClient") as mock_client:
        result = asyncio.run(apply_settlement("default", application))

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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_netsuite_connection()), \
         patch("clearledgr.integrations.erp_router.apply_credit_note_to_netsuite", side_effect=mock_apply_credit_to_netsuite):
        result = asyncio.run(apply_credit_note("default", application, idempotency_key="idem-credit-ns-refresh"))

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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_netsuite_connection()), \
         patch("clearledgr.integrations.erp_router.apply_settlement_to_netsuite", side_effect=mock_apply_settlement_to_netsuite):
        result = asyncio.run(apply_settlement("default", application, idempotency_key="idem-settlement-ns-refresh"))

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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_sap_connection()), \
         patch("clearledgr.integrations.erp_router.find_credit_note_sap", AsyncMock(return_value=None)), \
         patch("clearledgr.integrations.erp_router.get_purchase_invoice_sap", AsyncMock(return_value=bill_context)), \
         patch("clearledgr.integrations.erp_router.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(
            apply_credit_note(
                "default",
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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_sap_connection()), \
         patch("clearledgr.integrations.erp_router.get_purchase_invoice_sap", AsyncMock(return_value=bill_context)), \
         patch("clearledgr.integrations.erp_router.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(
            apply_settlement(
                "default",
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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_sap_connection()), \
         patch("clearledgr.integrations.erp_router.httpx.AsyncClient") as mock_client:
        result = asyncio.run(apply_settlement("default", application))

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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_sap_connection()), \
         patch("clearledgr.integrations.erp_router.apply_credit_note_to_sap", side_effect=mock_apply_credit_to_sap):
        result = asyncio.run(apply_credit_note("default", application, idempotency_key="idem-credit-sap-refresh"))

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

    with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=_sap_connection()), \
         patch("clearledgr.integrations.erp_router.apply_settlement_to_sap", side_effect=mock_apply_settlement_to_sap):
        result = asyncio.run(apply_settlement("default", application, idempotency_key="idem-settlement-sap-refresh"))

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
    from clearledgr.api.auth import _sanitize_redirect_path
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        _sanitize_redirect_path("//attacker.com/phishing")
    assert exc_info.value.status_code == 400


def test_redirect_path_allows_valid_path():
    from clearledgr.api.auth import _sanitize_redirect_path
    assert _sanitize_redirect_path("/dashboard") == "/dashboard"
    assert _sanitize_redirect_path("/") == "/"
