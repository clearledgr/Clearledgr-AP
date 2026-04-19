"""Fixture-based e2e tests for the NetSuite ERP path.

Covers the runtime shape the AP pipeline depends on: preflight
(vendor read, vendor-bill read, chart fetch), bill posting happy
path, 202 async polling, duplicate detection, and multi-subsidiary
plumbing. Does not hit a real NetSuite sandbox — every HTTP call
is mocked — but exercises the code paths that would fail silently
if the adapter regressed.

Companion to ``test_chart_of_accounts.py`` which covers COA fetch
in isolation. This file is the bill-post path + connection preflight.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from clearledgr.integrations.erp_netsuite import (
    post_bill_to_netsuite,
    preflight_netsuite,
)
from clearledgr.integrations.erp_router import ERPConnection


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _ns_connection(**overrides) -> ERPConnection:
    defaults = dict(
        type="netsuite",
        account_id="NS_TEST",
        consumer_key="ck",
        consumer_secret="cs",
        token_id="tid",
        token_secret="ts",
    )
    defaults.update(overrides)
    return ERPConnection(**defaults)


@dataclass
class _FakeBill:
    """Minimal Bill stand-in — matches the fields ``post_bill_to_netsuite`` reads."""
    vendor_id: str = "V-123"
    vendor_name: str = "Acme Inc"
    invoice_number: str = "INV-001"
    invoice_date: Optional[str] = "2026-04-01"
    due_date: Optional[str] = "2026-05-01"
    amount: float = 1000.0
    currency: str = "USD"
    description: Optional[str] = "Cloud services — April 2026"
    line_items: Optional[List[Dict[str, Any]]] = None
    tax_amount: Optional[float] = None
    discount_amount: Optional[float] = None
    discount_terms: Optional[str] = None
    payment_terms: Optional[str] = None
    po_number: Optional[str] = None


def _mock_response(status_code: int, json_payload: Any = None, *, headers: Optional[Dict] = None):
    import httpx
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_payload or {}
    resp.headers = headers or {}
    resp.text = str(json_payload) if json_payload else ""
    if 400 <= status_code < 600:
        # Emulate httpx.Response.raise_for_status — raise HTTPStatusError
        # with this same response attached so error-handling branches
        # in the ERP adapters can parse the body.
        exc = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp,
        )
        resp.raise_for_status = MagicMock(side_effect=exc)
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _fake_http_client(responses_by_call):
    """Build a mock http client whose calls cycle through ``responses_by_call``."""
    client = MagicMock()
    client.get = AsyncMock(side_effect=list(responses_by_call.get("GET", [])))
    client.post = AsyncMock(side_effect=list(responses_by_call.get("POST", [])))
    return client


# ---------------------------------------------------------------------------
# preflight_netsuite
# ---------------------------------------------------------------------------


class TestPreflightNetsuite:
    def test_preflight_happy_path_all_checks_pass(self):
        # Two GET probes (vendor, vendorBill) both return 200.
        # One POST probe is the SuiteQL chart-of-accounts fetch.
        # NetSuite type names are lowercase internal tokens
        # ("acctpay" not "Accounts Payable"); see _NS_ACCOUNT_TYPE_MAP.
        chart_payload = {
            "items": [
                {"id": "100", "acctnumber": "6100", "acctname": "Office Expenses",
                 "accttype": "Expense", "isinactive": "F"},
                {"id": "200", "acctnumber": "2000", "acctname": "Accounts Payable",
                 "accttype": "AcctPay", "isinactive": "F"},
                {"id": "300", "acctnumber": "1000", "acctname": "Cash",
                 "accttype": "Bank", "isinactive": "F"},
            ],
        }
        responses = {
            "GET": [_mock_response(200), _mock_response(200)],
            "POST": [_mock_response(200, chart_payload)],
        }
        client = _fake_http_client(responses)

        with patch("clearledgr.integrations.erp_netsuite.get_http_client", return_value=client):
            with patch("clearledgr.integrations.erp_netsuite._oauth_header", return_value="OAuth fake"):
                result = asyncio.run(preflight_netsuite(_ns_connection()))

        assert result["critical_ok"] is True
        assert result["checks"]["vendors_readable"]["ok"] is True
        assert result["checks"]["vendor_bills_readable"]["ok"] is True
        assert result["checks"]["chart_of_accounts_readable"]["ok"] is True
        assert result["chart_summary"]["total"] == 3
        assert result["chart_summary"]["expense_accounts"] == 1
        assert result["chart_summary"]["liability_accounts"] == 1
        assert result["warnings"] == []  # clean chart, no warnings

    def test_preflight_vendor_bills_forbidden_blocks_connection(self):
        """The token reads vendors fine but cannot read vendor bills.
        This is the exact scenario where a customer thinks their NetSuite
        connection is good, then the first invoice fails silently.
        """
        responses = {
            "GET": [_mock_response(200), _mock_response(403)],
            "POST": [_mock_response(200, {"items": [
                {"id": "100", "acctnumber": "6100", "acctname": "Exp", "accttype": "Expense", "isinactive": "F"},
            ]})],
        }
        client = _fake_http_client(responses)

        with patch("clearledgr.integrations.erp_netsuite.get_http_client", return_value=client):
            with patch("clearledgr.integrations.erp_netsuite._oauth_header", return_value="OAuth fake"):
                result = asyncio.run(preflight_netsuite(_ns_connection()))

        assert result["critical_ok"] is False
        assert result["checks"]["vendor_bills_readable"]["ok"] is False
        assert result["checks"]["vendor_bills_readable"]["status"] == 403
        assert "forbidden" in result["checks"]["vendor_bills_readable"]["detail"].lower()

    def test_preflight_warns_when_no_expense_accounts(self):
        chart_payload = {
            "items": [
                {"id": "100", "acctnumber": "2000", "acctname": "AP",
                 "accttype": "AcctPay", "isinactive": "F"},
            ],
        }
        responses = {
            "GET": [_mock_response(200), _mock_response(200)],
            "POST": [_mock_response(200, chart_payload)],
        }
        client = _fake_http_client(responses)

        with patch("clearledgr.integrations.erp_netsuite.get_http_client", return_value=client):
            with patch("clearledgr.integrations.erp_netsuite._oauth_header", return_value="OAuth fake"):
                result = asyncio.run(preflight_netsuite(_ns_connection()))

        assert result["critical_ok"] is True  # all three probes passed
        assert result["ok"] is False           # but warnings downgrade it
        assert any("no expense accounts" in w for w in result["warnings"])

    def test_preflight_without_account_id_fails_fast(self):
        result = asyncio.run(preflight_netsuite(ERPConnection(type="netsuite")))
        assert result["ok"] is False
        assert "account_id missing" in result["warnings"]


# ---------------------------------------------------------------------------
# post_bill_to_netsuite
# ---------------------------------------------------------------------------


class TestPostBillToNetsuite:
    def test_bill_post_happy_path_single_subsidiary(self):
        """Single-subsidiary account: no subsidiary on connection, bill
        should POST without a subsidiary field on the payload."""
        created_response = _mock_response(
            201, {"id": "BILL-NS-1", "tranId": "INV-001"}, headers={},
        )
        responses = {"POST": [created_response]}
        client = _fake_http_client(responses)

        captured_payload: Dict[str, Any] = {}

        async def _fake_post(url, **kwargs):
            captured_payload["url"] = url
            captured_payload["body"] = kwargs.get("json")
            return created_response

        client.post.side_effect = _fake_post

        with patch("clearledgr.integrations.erp_netsuite.get_http_client", return_value=client):
            with patch("clearledgr.integrations.erp_netsuite._oauth_header", return_value="OAuth fake"):
                result = asyncio.run(post_bill_to_netsuite(_ns_connection(), _FakeBill()))

        assert result["status"] == "success"
        assert result["erp"] == "netsuite"
        assert result["bill_id"] == "BILL-NS-1"
        # No subsidiary on single-sub tenant
        assert "subsidiary" not in captured_payload["body"]
        assert captured_payload["body"]["entity"] == {"id": "V-123"}
        assert captured_payload["body"]["currency"] == {"refName": "USD"}

    def test_bill_post_includes_subsidiary_when_configured(self):
        """OneWorld tenant: subsidiary_id on connection → attached to bill."""
        created_response = _mock_response(
            201, {"id": "BILL-NS-2", "tranId": "INV-002"},
        )
        client = _fake_http_client({"POST": []})
        captured_payload: Dict[str, Any] = {}

        async def _fake_post(url, **kwargs):
            captured_payload["body"] = kwargs.get("json")
            return created_response

        client.post.side_effect = _fake_post

        with patch("clearledgr.integrations.erp_netsuite.get_http_client", return_value=client):
            with patch("clearledgr.integrations.erp_netsuite._oauth_header", return_value="OAuth fake"):
                result = asyncio.run(post_bill_to_netsuite(
                    _ns_connection(subsidiary_id="7"),
                    _FakeBill(invoice_number="INV-002"),
                ))

        assert result["status"] == "success"
        assert captured_payload["body"]["subsidiary"] == {"id": "7"}

    def test_bill_post_duplicate_invoice_number_detected(self):
        """NetSuite's duplicate-reference-number error surfaces as a
        structured ``erp_duplicate_bill`` reason — not as a generic 400."""
        # NetSuite REST error shape: status.statusDetail[].code + .message
        dup_error = {
            "status": {
                "isSuccess": False,
                "statusDetail": [
                    {
                        "code": "DUPLICATE_REFERENCE_NUMBER",
                        "message": "You have entered a Reference Number that already exists in NetSuite.",
                    }
                ],
            },
        }
        error_response = _mock_response(400, dup_error)
        client = _fake_http_client({"POST": [error_response]})

        with patch("clearledgr.integrations.erp_netsuite.get_http_client", return_value=client):
            with patch("clearledgr.integrations.erp_netsuite._oauth_header", return_value="OAuth fake"):
                result = asyncio.run(post_bill_to_netsuite(_ns_connection(), _FakeBill()))

        assert result["status"] == "error"
        assert result["reason"] == "erp_duplicate_bill"

    def test_bill_post_401_surfaces_needs_reauth(self):
        """Expired TBA token → structured ``needs_reauth`` signal so the
        retry queue routes to re-authentication instead of blind retry.
        """
        auth_err = _mock_response(401, {"title": "Unauthorized"})
        client = _fake_http_client({"POST": [auth_err]})

        with patch("clearledgr.integrations.erp_netsuite.get_http_client", return_value=client):
            with patch("clearledgr.integrations.erp_netsuite._oauth_header", return_value="OAuth fake"):
                result = asyncio.run(post_bill_to_netsuite(_ns_connection(), _FakeBill()))

        assert result["status"] == "error"
        assert result.get("needs_reauth") is True
