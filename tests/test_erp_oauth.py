"""Tests for clearledgr.api.erp_oauth — OAuth callback + token flows."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from clearledgr.api.erp_oauth import (
    quickbooks_callback,
    xero_callback,
)


def _run(coro):
    """Run an async coroutine synchronously (no pytest-asyncio needed)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# QuickBooks callback
# ---------------------------------------------------------------------------


class TestQuickBooksCallback:
    def test_error_param_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _run(quickbooks_callback(code="c", state="s", realmId="r", error="access_denied"))
        assert exc_info.value.status_code == 400

    def test_missing_code_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _run(quickbooks_callback(code=None, state="s", realmId="r", error=None))
        assert exc_info.value.status_code == 400

    def test_missing_state_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _run(quickbooks_callback(code="c", state=None, realmId="r", error=None))
        assert exc_info.value.status_code == 400

    def test_missing_realm_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _run(quickbooks_callback(code="c", state="s", realmId=None, error=None))
        assert exc_info.value.status_code == 400

    def test_invalid_state_raises_400(self):
        with patch("clearledgr.api.erp_oauth.validate_oauth_state", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                _run(quickbooks_callback(code="c", state="bad-state", realmId="r", error=None))
            assert exc_info.value.status_code == 400

    def test_token_exchange_failure_raises_400(self):
        with patch("clearledgr.api.erp_oauth.validate_oauth_state", return_value={"organization_id": "acme"}):
            with patch("clearledgr.api.erp_oauth.exchange_quickbooks_code", new_callable=AsyncMock, side_effect=Exception("network")):
                with pytest.raises(HTTPException) as exc_info:
                    _run(quickbooks_callback(code="c", state="s", realmId="r", error=None))
                assert exc_info.value.status_code == 400

    def test_successful_callback(self):
        tokens = {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        with patch("clearledgr.api.erp_oauth.validate_oauth_state", return_value={"organization_id": "acme"}):
            with patch("clearledgr.api.erp_oauth.exchange_quickbooks_code", new_callable=AsyncMock, return_value=tokens):
                with patch("clearledgr.api.erp_oauth.save_erp_connection") as mock_save:
                    _run(quickbooks_callback(code="c", state="s", realmId="realm-1", error=None))
                    mock_save.assert_called_once()
                    record = mock_save.call_args[0][0]
                    assert record.erp_type == "quickbooks"
                    assert record.organization_id == "acme"
                    assert record.realm_id == "realm-1"


# ---------------------------------------------------------------------------
# Xero callback
# ---------------------------------------------------------------------------


class TestXeroCallback:
    def test_error_param_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _run(xero_callback(code="c", state="s", error="access_denied"))
        assert exc_info.value.status_code == 400

    def test_missing_code_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _run(xero_callback(code=None, state="s", error=None))
        assert exc_info.value.status_code == 400

    def test_invalid_state_raises_400(self):
        with patch("clearledgr.api.erp_oauth.validate_oauth_state", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                _run(xero_callback(code="c", state="bad", error=None))
            assert exc_info.value.status_code == 400

    def test_successful_callback_stores_tenant_id(self):
        tokens = {"access_token": "at", "refresh_token": "rt", "expires_in": 1800, "tenant_id": "xero-tenant"}
        with patch("clearledgr.api.erp_oauth.validate_oauth_state", return_value={"organization_id": "acme"}):
            with patch("clearledgr.api.erp_oauth.exchange_xero_code", new_callable=AsyncMock, return_value=tokens):
                with patch("clearledgr.api.erp_oauth.save_erp_connection") as mock_save:
                    _run(xero_callback(code="c", state="s", error=None))
                    mock_save.assert_called_once()
                    record = mock_save.call_args[0][0]
                    assert record.erp_type == "xero"
                    assert record.tenant_id == "xero-tenant"
