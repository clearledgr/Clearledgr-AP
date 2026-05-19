"""Tests for solden.api.erp_oauth — OAuth callback + token flows."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from solden.api.erp_oauth import (
    quickbooks_callback,
    xero_callback,
)


def _run(coro):
    """Run an async coroutine synchronously (no pytest-asyncio needed)."""
    return asyncio.run(coro)


def _stub_user(organization_id: str = "acme", user_id: str = "user-1"):
    """Stub for get_current_user — callbacks now require an authenticated session."""
    return SimpleNamespace(organization_id=organization_id, user_id=user_id)


# ---------------------------------------------------------------------------
# QuickBooks callback
# ---------------------------------------------------------------------------


class TestQuickBooksCallback:
    def test_error_param_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _run(quickbooks_callback(code="c", state="s", realmId="r", error="access_denied", user=_stub_user()))
        assert exc_info.value.status_code == 400

    def test_missing_code_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _run(quickbooks_callback(code=None, state="s", realmId="r", error=None, user=_stub_user()))
        assert exc_info.value.status_code == 400

    def test_missing_state_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _run(quickbooks_callback(code="c", state=None, realmId="r", error=None, user=_stub_user()))
        assert exc_info.value.status_code == 400

    def test_missing_realm_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _run(quickbooks_callback(code="c", state="s", realmId=None, error=None, user=_stub_user()))
        assert exc_info.value.status_code == 400

    def test_invalid_state_raises_400(self):
        with patch("solden.api.erp_oauth.validate_oauth_state", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                _run(quickbooks_callback(code="c", state="bad-state", realmId="r", error=None, user=_stub_user()))
            assert exc_info.value.status_code == 400

    def test_state_org_mismatch_raises_403(self):
        """State org from a different tenant must be rejected even if state is valid."""
        with patch(
            "solden.api.erp_oauth.validate_oauth_state",
            return_value={"organization_id": "tenant-b", "user_id": "user-1"},
        ):
            with pytest.raises(HTTPException) as exc_info:
                _run(quickbooks_callback(
                    code="c", state="s", realmId="r", error=None,
                    user=_stub_user(organization_id="tenant-a"),
                ))
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail == "oauth_state_org_mismatch"

    def test_state_user_mismatch_raises_403(self):
        """A leaked state cannot be redeemed by a different user in the same org."""
        with patch(
            "solden.api.erp_oauth.validate_oauth_state",
            return_value={"organization_id": "acme", "user_id": "user-original"},
        ):
            with pytest.raises(HTTPException) as exc_info:
                _run(quickbooks_callback(
                    code="c", state="s", realmId="r", error=None,
                    user=_stub_user(organization_id="acme", user_id="user-attacker"),
                ))
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail == "oauth_state_user_mismatch"

    def test_token_exchange_failure_raises_400(self):
        with patch(
            "solden.api.erp_oauth.validate_oauth_state",
            return_value={"organization_id": "acme", "user_id": "user-1"},
        ):
            with patch("solden.api.erp_oauth.exchange_quickbooks_code", new_callable=AsyncMock, side_effect=Exception("network")):
                with pytest.raises(HTTPException) as exc_info:
                    _run(quickbooks_callback(code="c", state="s", realmId="r", error=None, user=_stub_user()))
                assert exc_info.value.status_code == 400

    def test_successful_callback(self):
        tokens = {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        with patch(
            "solden.api.erp_oauth.validate_oauth_state",
            return_value={"organization_id": "acme", "user_id": "user-1"},
        ):
            with patch("solden.api.erp_oauth.exchange_quickbooks_code", new_callable=AsyncMock, return_value=tokens):
                with patch("solden.api.erp_oauth.save_erp_connection") as mock_save:
                    _run(quickbooks_callback(code="c", state="s", realmId="realm-1", error=None, user=_stub_user()))
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
            _run(xero_callback(code="c", state="s", error="access_denied", user=_stub_user()))
        assert exc_info.value.status_code == 400

    def test_missing_code_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _run(xero_callback(code=None, state="s", error=None, user=_stub_user()))
        assert exc_info.value.status_code == 400

    def test_invalid_state_raises_400(self):
        with patch("solden.api.erp_oauth.validate_oauth_state", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                _run(xero_callback(code="c", state="bad", error=None, user=_stub_user()))
            assert exc_info.value.status_code == 400

    def test_state_org_mismatch_raises_403(self):
        with patch(
            "solden.api.erp_oauth.validate_oauth_state",
            return_value={"organization_id": "tenant-b", "user_id": "user-1"},
        ):
            with pytest.raises(HTTPException) as exc_info:
                _run(xero_callback(
                    code="c", state="s", error=None,
                    user=_stub_user(organization_id="tenant-a"),
                ))
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail == "oauth_state_org_mismatch"

    def test_state_user_mismatch_raises_403(self):
        with patch(
            "solden.api.erp_oauth.validate_oauth_state",
            return_value={"organization_id": "acme", "user_id": "user-original"},
        ):
            with pytest.raises(HTTPException) as exc_info:
                _run(xero_callback(
                    code="c", state="s", error=None,
                    user=_stub_user(organization_id="acme", user_id="user-attacker"),
                ))
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail == "oauth_state_user_mismatch"

    def test_successful_callback_stores_tenant_id(self):
        tokens = {"access_token": "at", "refresh_token": "rt", "expires_in": 1800, "tenant_id": "xero-tenant"}
        with patch(
            "solden.api.erp_oauth.validate_oauth_state",
            return_value={"organization_id": "acme", "user_id": "user-1"},
        ):
            with patch("solden.api.erp_oauth.exchange_xero_code", new_callable=AsyncMock, return_value=tokens):
                with patch("solden.api.erp_oauth.save_erp_connection") as mock_save:
                    _run(xero_callback(code="c", state="s", error=None, user=_stub_user()))
                    mock_save.assert_called_once()
                    record = mock_save.call_args[0][0]
                    assert record.erp_type == "xero"
                    assert record.tenant_id == "xero-tenant"
