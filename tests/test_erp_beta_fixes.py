"""Tests for beta blocker fixes: GL mapping, SAP pre-flight, token refresh retry, NetSuite normalization.

Follows existing test patterns:
- tmp_path DB via monkeypatch.setenv("CLEARLEDGR_DB_PATH", ...)
- Reset _DB_INSTANCE in teardown (conftest.reset_service_singletons)
- asyncio.run() wrapping
"""
from __future__ import annotations

import asyncio
import json
from typing import Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clearledgr.core import database as db_module
from clearledgr.integrations.erp_router import (
    Bill,
    ERPConnection,
    DEFAULT_ACCOUNT_MAP,
    _get_org_gl_map,
    get_account_code,
    post_bill,
    post_bill_to_quickbooks,
    post_bill_to_xero,
    post_bill_to_netsuite,
    post_bill_to_sap,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "beta_fixes.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
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
