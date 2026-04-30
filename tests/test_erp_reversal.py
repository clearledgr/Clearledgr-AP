"""Tests for Phase 1.3 — reversible ERP posts.

Mock-only harness (no real ERP credentials). Each test patches
``httpx.AsyncClient`` so no network calls are made. Covers:

- Connector-level reverse_bill_from_{quickbooks, xero, netsuite, sap}:
  happy path, already-reversed, needs_reauth, payment_already_applied,
  generic HTTP errors, ERP-specific edge cases.
- Router-level reverse_bill() dispatcher:
  correct connector dispatch, two layers of idempotency, no-ERP skip,
  reauth retry loop, audit event emission, AP item metadata persistence,
  unknown ERP type.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import httpx
import pytest

from clearledgr.integrations.erp_router import (
    ERPConnection,
    reverse_bill,
    reverse_bill_from_netsuite,
    reverse_bill_from_quickbooks,
    reverse_bill_from_sap,
    reverse_bill_from_xero,
)


# ---------------------------------------------------------------------------
# Mock HTTP plumbing
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal httpx.Response stand-in for AsyncClient mocks."""

    def __init__(
        self,
        status_code: int = 200,
        json_body: Optional[Dict[str, Any]] = None,
        text_body: str = "",
        headers: Optional[Dict[str, str]] = None,
    ):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.text = text_body or json.dumps(json_body or {})
        self.headers = headers or {}
        self.content = self.text.encode("utf-8") if self.text else b""
        # httpx.Response has a request attribute used by raise_for_status
        self.request = MagicMock()

    def json(self) -> Any:
        return self._json_body

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            # Mimic httpx.HTTPStatusError signature
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=self,  # type: ignore[arg-type]
            )


class FakeAsyncClient:
    """Drop-in replacement for httpx.AsyncClient that records requests
    and returns a queue of scripted FakeResponse instances."""

    def __init__(self, responses: List[FakeResponse]):
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, *args, **kwargs):
        # Allow reuse as a context-manager constructor
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _pop(self) -> FakeResponse:
        if not self._responses:
            raise RuntimeError("FakeAsyncClient out of scripted responses")
        return self._responses.pop(0)

    async def post(self, url, *args, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return await self._pop()

    async def get(self, url, *args, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return await self._pop()

    async def delete(self, url, *args, **kwargs):
        self.calls.append({"method": "DELETE", "url": url, **kwargs})
        return await self._pop()


def _patch_httpx(monkeypatch, responses: List[FakeResponse]) -> FakeAsyncClient:
    """Patch httpx.AsyncClient in BOTH the router and every connector module
    so FakeAsyncClient is used regardless of where the call originates."""
    fake = FakeAsyncClient(responses)

    def _factory(*args, **kwargs):
        return fake

    monkeypatch.setattr("httpx.AsyncClient", _factory)
    monkeypatch.setattr(
        "clearledgr.integrations.erp_quickbooks.httpx.AsyncClient", _factory
    )
    monkeypatch.setattr(
        "clearledgr.integrations.erp_xero.httpx.AsyncClient", _factory
    )
    monkeypatch.setattr(
        "clearledgr.integrations.erp_netsuite.httpx.AsyncClient", _factory
    )
    monkeypatch.setattr(
        "clearledgr.integrations.erp_sap.httpx.AsyncClient", _factory
    )
    return fake


# ---------------------------------------------------------------------------
# Connection factories
# ---------------------------------------------------------------------------


def _qbo_connection() -> ERPConnection:
    return ERPConnection(
        type="quickbooks",
        access_token="qbo-access-token",
        refresh_token="qbo-refresh-token",
        realm_id="999777999",
    )


def _xero_connection() -> ERPConnection:
    return ERPConnection(
        type="xero",
        access_token="xero-access-token",
        refresh_token="xero-refresh-token",
        tenant_id="tenant-xyz",
    )


def _netsuite_connection() -> ERPConnection:
    return ERPConnection(
        type="netsuite",
        account_id="NS-TEST-ACCOUNT",
        consumer_key="ck",
        consumer_secret="cs",
        token_id="tk",
        token_secret="ts",
    )


def _sap_connection() -> ERPConnection:
    return ERPConnection(
        type="sap",
        access_token="sap-b1-session",
        base_url="https://sap-test.example/b1s/v1",
        company_code="1000",
    )


# ===========================================================================
# QuickBooks reversal
# ===========================================================================


class TestReverseQuickBooks:

    def test_happy_path_with_supplied_sync_token(self, monkeypatch):
        fake = _patch_httpx(
            monkeypatch,
            [
                FakeResponse(
                    status_code=200,
                    json_body={"Bill": {"Id": "42", "status": "Deleted"}},
                ),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_quickbooks(
                _qbo_connection(),
                "42",
                reason="human_override",
                sync_token="7",
            )
        )
        assert result["status"] == "success"
        assert result["erp"] == "quickbooks"
        assert result["reference_id"] == "42"
        assert result["reversal_method"] == "delete"
        # Exactly one call — no refetch because sync_token was provided.
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["method"] == "POST"
        assert "operation=delete" not in call["url"]  # operation is in params
        assert call["params"]["operation"] == "delete"
        assert call["json"]["Id"] == "42"
        assert call["json"]["SyncToken"] == "7"

    def test_fetches_fresh_sync_token_when_missing(self, monkeypatch):
        fake = _patch_httpx(
            monkeypatch,
            [
                # First: fetch-with-sync-token (REST GET)
                FakeResponse(
                    status_code=200,
                    json_body={
                        "Bill": {"Id": "42", "SyncToken": "3", "DocNumber": "INV-1"}
                    },
                ),
                # Second: the actual delete
                FakeResponse(
                    status_code=200,
                    json_body={"Bill": {"Id": "42", "status": "Deleted"}},
                ),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_quickbooks(
                _qbo_connection(),
                "42",
                reason="human_override",
                sync_token=None,
            )
        )
        assert result["status"] == "success"
        assert len(fake.calls) == 2
        assert fake.calls[0]["method"] == "GET"
        assert fake.calls[1]["method"] == "POST"
        # Uses the token we fetched, not the None caller passed in
        assert fake.calls[1]["json"]["SyncToken"] == "3"

    def test_stale_sync_token_triggers_refetch_and_retry(self, monkeypatch):
        fake = _patch_httpx(
            monkeypatch,
            [
                # First delete attempt — stale sync token
                FakeResponse(
                    status_code=400,
                    json_body={
                        "Fault": {
                            "Error": [
                                {"Detail": "Stale object version", "code": "5010"}
                            ]
                        }
                    },
                ),
                # Refetch for fresh token
                FakeResponse(
                    status_code=200,
                    json_body={
                        "Bill": {"Id": "42", "SyncToken": "9", "DocNumber": "INV-1"}
                    },
                ),
                # Retry delete with fresh token
                FakeResponse(
                    status_code=200,
                    json_body={"Bill": {"Id": "42", "status": "Deleted"}},
                ),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_quickbooks(
                _qbo_connection(),
                "42",
                reason="human_override",
                sync_token="1",  # intentionally stale
            )
        )
        assert result["status"] == "success"
        assert len(fake.calls) == 3
        # The last delete used the refreshed token
        assert fake.calls[-1]["json"]["SyncToken"] == "9"

    def test_bill_not_found_returns_already_reversed(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            [
                FakeResponse(status_code=404, json_body={}),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_quickbooks(
                _qbo_connection(),
                "42",
                reason="override",
                sync_token="7",
            )
        )
        assert result["status"] == "already_reversed"
        assert result["reversal_method"] == "delete"

    def test_unauthorized_signals_reauth(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            [
                FakeResponse(status_code=401, json_body={}),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_quickbooks(
                _qbo_connection(),
                "42",
                reason="override",
                sync_token="7",
            )
        )
        assert result["status"] == "error"
        assert result["needs_reauth"] is True

    def test_payment_applied_error(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            [
                FakeResponse(
                    status_code=400,
                    json_body={
                        "Fault": {
                            "Error": [
                                {
                                    "Detail": "Payment has been applied to this bill",
                                    "code": "6240",
                                }
                            ]
                        }
                    },
                ),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_quickbooks(
                _qbo_connection(),
                "42",
                reason="override",
                sync_token="7",
            )
        )
        assert result["status"] == "error"
        assert result["reason"] == "payment_already_applied"

    def test_missing_credentials(self, monkeypatch):
        conn = ERPConnection(type="quickbooks")
        result = asyncio.run(
            reverse_bill_from_quickbooks(conn, "42", reason="override")
        )
        assert result["status"] == "error"

    def test_missing_reference(self, monkeypatch):
        result = asyncio.run(
            reverse_bill_from_quickbooks(_qbo_connection(), "", reason="override")
        )
        assert result["status"] == "error"
        assert result["reason"] == "missing_erp_reference"


# ===========================================================================
# Xero reversal
# ===========================================================================


class TestReverseXero:

    def test_happy_path(self, monkeypatch):
        fake = _patch_httpx(
            monkeypatch,
            [
                FakeResponse(
                    status_code=200,
                    json_body={
                        "Invoices": [{"InvoiceID": "abc-123", "Status": "VOIDED"}]
                    },
                ),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_xero(
                _xero_connection(), "abc-123", reason="human_override"
            )
        )
        assert result["status"] == "success"
        assert result["erp"] == "xero"
        assert result["reversal_method"] == "void"
        assert result["reference_id"] == "abc-123"
        assert result["erp_status"] == "VOIDED"
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["method"] == "POST"
        assert "abc-123" in call["url"]
        assert call["json"]["Invoices"][0]["Status"] == "VOIDED"

    def test_bill_not_found_returns_already_reversed(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            [
                FakeResponse(status_code=404, json_body={}),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_xero(_xero_connection(), "abc", reason="override")
        )
        assert result["status"] == "already_reversed"

    def test_unauthorized_signals_reauth(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            [
                FakeResponse(status_code=401, json_body={}),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_xero(_xero_connection(), "abc", reason="override")
        )
        assert result["status"] == "error"
        assert result["needs_reauth"] is True

    def test_payment_allocated_error(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            [
                FakeResponse(
                    status_code=400,
                    json_body={
                        "Elements": [
                            {
                                "ValidationErrors": [
                                    {
                                        "Message": (
                                            "Invoice cannot be voided because "
                                            "payment has been allocated to it."
                                        )
                                    }
                                ]
                            }
                        ]
                    },
                ),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_xero(_xero_connection(), "abc", reason="override")
        )
        assert result["status"] == "error"
        assert result["reason"] == "payment_already_applied"

    def test_missing_credentials(self):
        conn = ERPConnection(type="xero")
        result = asyncio.run(
            reverse_bill_from_xero(conn, "abc", reason="override")
        )
        assert result["status"] == "error"


# ===========================================================================
# NetSuite reversal
# ===========================================================================


class TestReverseNetSuite:

    def test_happy_path_returns_204(self, monkeypatch):
        fake = _patch_httpx(
            monkeypatch,
            [FakeResponse(status_code=204, json_body={})],
        )
        result = asyncio.run(
            reverse_bill_from_netsuite(
                _netsuite_connection(), "99999", reason="override"
            )
        )
        assert result["status"] == "success"
        assert result["erp"] == "netsuite"
        assert result["reversal_method"] == "delete"
        assert result["reference_id"] == "99999"
        assert len(fake.calls) == 1
        assert fake.calls[0]["method"] == "DELETE"
        assert "vendorBill/99999" in fake.calls[0]["url"]

    def test_bill_not_found_returns_already_reversed(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            [FakeResponse(status_code=404, json_body={})],
        )
        result = asyncio.run(
            reverse_bill_from_netsuite(
                _netsuite_connection(), "99999", reason="override"
            )
        )
        assert result["status"] == "already_reversed"

    def test_unauthorized_signals_reauth(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            [FakeResponse(status_code=401, json_body={})],
        )
        result = asyncio.run(
            reverse_bill_from_netsuite(
                _netsuite_connection(), "99999", reason="override"
            )
        )
        assert result["status"] == "error"
        assert result["needs_reauth"] is True

    def test_cannot_delete_record_is_403(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            [
                FakeResponse(
                    status_code=403,
                    json_body={
                        "status": {
                            "isSuccess": False,
                            "statusDetail": [
                                {
                                    "code": "FORBIDDEN",
                                    "message": "User does not have delete permission for vendor bills",
                                }
                            ],
                        }
                    },
                )
            ],
        )
        result = asyncio.run(
            reverse_bill_from_netsuite(
                _netsuite_connection(), "99999", reason="override"
            )
        )
        assert result["status"] == "error"
        assert result["reason"] == "cannot_delete_record"

    def test_payment_applied_error(self, monkeypatch):
        _patch_httpx(
            monkeypatch,
            [
                FakeResponse(
                    status_code=400,
                    json_body={
                        "status": {
                            "isSuccess": False,
                            "statusDetail": [
                                {
                                    "code": "USER_ERROR",
                                    "message": "Bill cannot be deleted — it has been paid.",
                                }
                            ],
                        }
                    },
                )
            ],
        )
        result = asyncio.run(
            reverse_bill_from_netsuite(
                _netsuite_connection(), "99999", reason="override"
            )
        )
        assert result["status"] == "error"
        assert result["reason"] == "payment_already_applied"

    def test_missing_account_id(self):
        conn = ERPConnection(type="netsuite")
        result = asyncio.run(
            reverse_bill_from_netsuite(conn, "99999", reason="override")
        )
        assert result["status"] == "error"


# ===========================================================================
# SAP B1 reversal
# ===========================================================================


class TestReverseSAP:

    def _patch_sap_session(self, monkeypatch):
        """SAP reversal opens a Service Layer session first. Short-circuit it."""
        async def _fake_session(connection, client, fetch_csrf_for=None):
            return {
                "status": "success",
                "headers": {
                    "Cookie": "B1SESSION=fake",
                    "X-CSRF-Token": "csrf-fake",
                },
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_sap._open_sap_service_layer_session",
            _fake_session,
        )

    def test_happy_path_returns_204(self, monkeypatch):
        self._patch_sap_session(monkeypatch)
        fake = _patch_httpx(
            monkeypatch,
            [FakeResponse(status_code=204, json_body={}, text_body="")],
        )
        result = asyncio.run(
            reverse_bill_from_sap(_sap_connection(), "42", reason="override")
        )
        assert result["status"] == "success"
        assert result["erp"] == "sap"
        assert result["reversal_method"] == "cancel_document"
        assert result["reference_id"] == "42"
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["method"] == "POST"
        assert "/PurchaseInvoices(42)/Cancel" in call["url"]

    def test_happy_path_returns_200_with_cancellation_doc_entry(self, monkeypatch):
        self._patch_sap_session(monkeypatch)
        _patch_httpx(
            monkeypatch,
            [
                FakeResponse(
                    status_code=200,
                    json_body={"DocEntry": 99, "DocNum": "CANC-1"},
                ),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_sap(_sap_connection(), "42", reason="override")
        )
        assert result["status"] == "success"
        assert result["reversal_ref"] == "99"

    def test_already_cancelled_error(self, monkeypatch):
        self._patch_sap_session(monkeypatch)
        _patch_httpx(
            monkeypatch,
            [
                FakeResponse(
                    status_code=400,
                    json_body={
                        "error": {
                            "code": -5002,
                            "message": {
                                "lang": "en-us",
                                "value": "Document is already cancelled",
                            },
                        }
                    },
                ),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_sap(_sap_connection(), "42", reason="override")
        )
        assert result["status"] == "already_reversed"
        assert result["reason"] == "already_cancelled_in_erp"

    def test_unauthorized_signals_reauth(self, monkeypatch):
        self._patch_sap_session(monkeypatch)
        _patch_httpx(
            monkeypatch,
            [FakeResponse(status_code=401, json_body={})],
        )
        result = asyncio.run(
            reverse_bill_from_sap(_sap_connection(), "42", reason="override")
        )
        assert result["status"] == "error"
        assert result["needs_reauth"] is True

    def test_payment_applied_error(self, monkeypatch):
        self._patch_sap_session(monkeypatch)
        _patch_httpx(
            monkeypatch,
            [
                FakeResponse(
                    status_code=400,
                    json_body={
                        "error": {
                            "code": -5002,
                            "message": {
                                "lang": "en-us",
                                "value": "Invoice has been paid; cannot cancel.",
                            },
                        }
                    },
                ),
            ],
        )
        result = asyncio.run(
            reverse_bill_from_sap(_sap_connection(), "42", reason="override")
        )
        assert result["status"] == "error"
        assert result["reason"] == "payment_already_applied"

    def test_invalid_reference(self, monkeypatch):
        self._patch_sap_session(monkeypatch)
        result = asyncio.run(
            reverse_bill_from_sap(
                _sap_connection(), "not-a-number", reason="override"
            )
        )
        assert result["status"] == "error"
        assert result["reason"] == "invalid_bill_reference"


# ===========================================================================
# Dispatcher: reverse_bill()
# ===========================================================================


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh temp-file DB wired as the singleton."""
    from clearledgr.core.database import get_db
    from clearledgr.core import database as db_module

    db = get_db()
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


def _seed_posted_ap_item(
    db,
    ap_item_id: str = "AP-RVB-1",
    organization_id: str = "org_rvb",
    erp_reference: str = "42",
    *,
    metadata: Optional[Dict[str, Any]] = None,
):
    # create_ap_item JSON-encodes payload["metadata"] itself — pass a dict,
    # not a pre-encoded string, otherwise we get double-encoded metadata.
    db.create_ap_item(
        {
            "id": ap_item_id,
            "organization_id": organization_id,
            "vendor_name": "Test Vendor",
            "amount": 500.0,
            "currency": "USD",
            "state": "posted_to_erp",
            "erp_reference": erp_reference,
            "thread_id": f"thread-{ap_item_id}",
            "invoice_number": "INV-RVB",
            "metadata": metadata or {},
        }
    )
    return db.get_ap_item(ap_item_id)


class TestReverseBillDispatcher:

    def test_missing_reference_is_rejected(self, tmp_db, monkeypatch):
        result = asyncio.run(
            reverse_bill(
                "org_rvb",
                "",
                reason="override",
            )
        )
        assert result["status"] == "error"
        assert result["reason"] == "missing_erp_reference"

    def test_missing_reason_is_rejected(self, tmp_db, monkeypatch):
        result = asyncio.run(
            reverse_bill(
                "org_rvb",
                "42",
                reason="",
            )
        )
        assert result["status"] == "error"
        assert result["reason"] == "missing_reversal_reason"

    def test_no_erp_connected_returns_skipped(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: None,
        )
        result = asyncio.run(
            reverse_bill("org_rvb", "42", reason="override")
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "no_erp_connected"

    def test_dispatches_to_quickbooks(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: _qbo_connection(),
        )
        captured: Dict[str, Any] = {}

        async def _fake_qbo(connection, reference, *, reason, sync_token=None):
            captured["called"] = True
            captured["reference"] = reference
            captured["reason"] = reason
            captured["sync_token"] = sync_token
            return {
                "status": "success",
                "erp": "quickbooks",
                "reference_id": reference,
                "reversal_method": "delete",
                "reversal_ref": reference,
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill_from_quickbooks",
            _fake_qbo,
        )
        result = asyncio.run(
            reverse_bill("org_rvb", "42", reason="override")
        )
        assert result["status"] == "success"
        assert captured["called"] is True
        assert captured["reference"] == "42"
        assert captured["reason"] == "override"

    def test_dispatches_to_xero(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: _xero_connection(),
        )

        async def _fake_xero(connection, reference, *, reason):
            return {
                "status": "success",
                "erp": "xero",
                "reference_id": reference,
                "reversal_method": "void",
                "reversal_ref": reference,
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill_from_xero",
            _fake_xero,
        )
        result = asyncio.run(
            reverse_bill("org_rvb", "xero-1", reason="override")
        )
        assert result["status"] == "success"
        assert result["erp"] == "xero"

    def test_dispatches_to_netsuite(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: _netsuite_connection(),
        )

        async def _fake_ns(connection, reference, *, reason):
            return {
                "status": "success",
                "erp": "netsuite",
                "reference_id": reference,
                "reversal_method": "delete",
                "reversal_ref": reference,
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill_from_netsuite",
            _fake_ns,
        )
        result = asyncio.run(
            reverse_bill("org_rvb", "ns-1", reason="override")
        )
        assert result["status"] == "success"
        assert result["erp"] == "netsuite"

    def test_dispatches_to_sap(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: _sap_connection(),
        )

        async def _fake_sap(connection, reference, *, reason):
            return {
                "status": "success",
                "erp": "sap",
                "reference_id": reference,
                "reversal_method": "cancel_document",
                "reversal_ref": "99",
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill_from_sap",
            _fake_sap,
        )
        result = asyncio.run(
            reverse_bill("org_rvb", "42", reason="override")
        )
        assert result["status"] == "success"
        assert result["erp"] == "sap"

    def test_unknown_erp_type_returns_error(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: ERPConnection(type="unknown_erp"),
        )
        result = asyncio.run(
            reverse_bill("org_rvb", "42", reason="override")
        )
        assert result["status"] == "error"
        assert result["reason"] == "unknown_erp_type"

    def test_idempotency_ap_item_metadata_short_circuit(self, tmp_db, monkeypatch):
        """When the AP item already has reversal_reference in metadata,
        reverse_bill must NOT hit the ERP."""
        _seed_posted_ap_item(
            tmp_db,
            ap_item_id="AP-CACHED",
            metadata={
                "reversal_reference": "42",
                "reversal_method": "delete",
                "reversal_erp_type": "quickbooks",
            },
        )

        erp_hits = {"count": 0}

        async def _should_not_be_called(*a, **kw):
            erp_hits["count"] += 1
            return {"status": "error", "reason": "should_not_reach_erp"}

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill_from_quickbooks",
            _should_not_be_called,
        )
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: _qbo_connection(),
        )

        result = asyncio.run(
            reverse_bill(
                "org_rvb",
                "42",
                reason="override",
                ap_item_id="AP-CACHED",
            )
        )
        assert result["status"] == "already_reversed"
        assert erp_hits["count"] == 0

    def test_idempotency_audit_key_short_circuit(self, tmp_db, monkeypatch):
        """When an erp_reversal_succeeded audit event exists for the
        idempotency_key, reverse_bill must return the cached result
        without hitting the ERP."""
        tmp_db.append_audit_event(
            {
                "ap_item_id": "AP-KEY",
                "event_type": "erp_reversal_succeeded",
                "actor_type": "user",
                "actor_id": "user_1",
                "reason": "previous reversal",
                "idempotency_key": "rev-key-xyz",
                "organization_id": "org_rvb",
                "source": "test",
                "metadata": {
                    "erp": "xero",
                    "reversal_ref": "xero-invoice-1",
                    "reversal_method": "void",
                },
            }
        )

        erp_hits = {"count": 0}

        async def _should_not_be_called(*a, **kw):
            erp_hits["count"] += 1
            return {"status": "error", "reason": "should_not_reach_erp"}

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill_from_xero",
            _should_not_be_called,
        )
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: _xero_connection(),
        )

        result = asyncio.run(
            reverse_bill(
                "org_rvb",
                "xero-invoice-1",
                reason="override",
                idempotency_key="rev-key-xyz",
            )
        )
        assert result["status"] == "already_reversed"
        assert erp_hits["count"] == 0
        assert result.get("reversal_ref") == "xero-invoice-1"

    def test_reauth_retry_on_quickbooks_401(self, tmp_db, monkeypatch):
        """First call returns needs_reauth, dispatcher refreshes token and
        retries once; second call succeeds."""
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: _qbo_connection(),
        )

        call_count = {"n": 0}

        async def _fake_qbo(connection, reference, *, reason, sync_token=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "status": "error",
                    "erp": "quickbooks",
                    "reason": "Token expired",
                    "needs_reauth": True,
                }
            return {
                "status": "success",
                "erp": "quickbooks",
                "reference_id": reference,
                "reversal_method": "delete",
                "reversal_ref": reference,
            }

        async def _fake_refresh(connection):
            return "new-access-token"

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill_from_quickbooks",
            _fake_qbo,
        )
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.refresh_quickbooks_token",
            _fake_refresh,
        )
        # set_erp_connection is called after refresh — stub to a no-op
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.set_erp_connection",
            lambda org_id, conn: None,
        )

        result = asyncio.run(
            reverse_bill("org_rvb", "42", reason="override")
        )
        assert result["status"] == "success"
        assert call_count["n"] == 2

    def test_audit_event_emitted_on_success(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: _xero_connection(),
        )

        async def _fake_xero(connection, reference, *, reason):
            return {
                "status": "success",
                "erp": "xero",
                "reference_id": reference,
                "reversal_method": "void",
                "reversal_ref": reference,
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill_from_xero",
            _fake_xero,
        )

        _seed_posted_ap_item(
            tmp_db, ap_item_id="AP-AUDIT", erp_reference="xero-1"
        )

        asyncio.run(
            reverse_bill(
                "org_rvb",
                "xero-1",
                reason="human_override",
                ap_item_id="AP-AUDIT",
                actor_id="cfo_user_1",
                idempotency_key="audit-key-1",
            )
        )

        events = tmp_db.list_ap_audit_events("AP-AUDIT")
        success_events = [
            e for e in events if e.get("event_type") == "erp_reversal_succeeded"
        ]
        assert len(success_events) == 1
        event = success_events[0]
        assert event["actor_id"] == "cfo_user_1"
        payload = event.get("payload_json") or {}
        assert payload["original_erp_reference"] == "xero-1"
        assert payload["reversal_ref"] == "xero-1"
        assert payload["reversal_method"] == "void"
        assert payload["reversal_reason"] == "human_override"
        assert payload["erp"] == "xero"

    def test_audit_event_emitted_on_failure(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: _xero_connection(),
        )

        async def _fake_xero(connection, reference, *, reason):
            return {
                "status": "error",
                "erp": "xero",
                "reference_id": reference,
                "reversal_method": "void",
                "reason": "payment_already_applied",
                "erp_error_detail": "Payment allocated",
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill_from_xero",
            _fake_xero,
        )

        _seed_posted_ap_item(
            tmp_db, ap_item_id="AP-FAIL", erp_reference="xero-1"
        )

        result = asyncio.run(
            reverse_bill(
                "org_rvb",
                "xero-1",
                reason="override",
                ap_item_id="AP-FAIL",
            )
        )
        assert result["status"] == "error"

        events = tmp_db.list_ap_audit_events("AP-FAIL")
        failure_events = [
            e for e in events if e.get("event_type") == "erp_reversal_failed"
        ]
        assert len(failure_events) == 1

    def test_reversal_reference_persisted_on_ap_item(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: _qbo_connection(),
        )

        async def _fake_qbo(connection, reference, *, reason, sync_token=None):
            return {
                "status": "success",
                "erp": "quickbooks",
                "reference_id": reference,
                "reversal_method": "delete",
                "reversal_ref": reference,
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill_from_quickbooks",
            _fake_qbo,
        )

        _seed_posted_ap_item(
            tmp_db, ap_item_id="AP-PERSIST", erp_reference="42"
        )

        asyncio.run(
            reverse_bill(
                "org_rvb",
                "42",
                reason="override",
                ap_item_id="AP-PERSIST",
            )
        )

        updated = tmp_db.get_ap_item("AP-PERSIST")
        meta = updated.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta["reversal_reference"] == "42"
        assert meta["reversal_method"] == "delete"
        assert meta["reversal_erp_type"] == "quickbooks"
        assert meta["reversal_reason"] == "override"

    def test_sync_token_read_from_ap_item_metadata_for_quickbooks(
        self, tmp_db, monkeypatch
    ):
        """When dispatching to QuickBooks, the dispatcher should read
        erp_sync_token from the AP item's metadata and forward it to
        the connector so no refetch is needed."""
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id, entity_id=None: _qbo_connection(),
        )

        captured = {"sync_token": None}

        async def _fake_qbo(connection, reference, *, reason, sync_token=None):
            captured["sync_token"] = sync_token
            return {
                "status": "success",
                "erp": "quickbooks",
                "reference_id": reference,
                "reversal_method": "delete",
                "reversal_ref": reference,
            }

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.reverse_bill_from_quickbooks",
            _fake_qbo,
        )

        _seed_posted_ap_item(
            tmp_db,
            ap_item_id="AP-TOKEN",
            erp_reference="42",
            metadata={"erp_sync_token": "13"},
        )

        asyncio.run(
            reverse_bill(
                "org_rvb",
                "42",
                reason="override",
                ap_item_id="AP-TOKEN",
            )
        )
        assert captured["sync_token"] == "13"
