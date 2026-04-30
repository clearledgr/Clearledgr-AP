"""Tests for chart of accounts: ERP functions, router dispatcher, caching, API endpoint, GL validation.

Follows existing test patterns:
- tmp_path DB via monkeypatch.setenv("CLEARLEDGR_DB_PATH", ...)
- Reset _DB_INSTANCE in teardown (conftest.reset_service_singletons)
- asyncio.run() wrapping for async functions
- MagicMock / AsyncMock for HTTP calls
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clearledgr.core import database as db_module
from clearledgr.integrations.erp_router import (
    ERPConnection,
    get_chart_of_accounts,
    get_chart_of_accounts_quickbooks,
    get_chart_of_accounts_xero,
    get_chart_of_accounts_netsuite,
    get_chart_of_accounts_sap,
    _get_cached_chart_of_accounts,
    _save_chart_of_accounts_cache,
    erp_preflight_check,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


def _qb_connection(**overrides) -> ERPConnection:
    defaults = dict(type="quickbooks", access_token="tok_qb", realm_id="realm_123")
    defaults.update(overrides)
    return ERPConnection(**defaults)


def _xero_connection(**overrides) -> ERPConnection:
    defaults = dict(type="xero", access_token="tok_xero", tenant_id="tenant_abc")
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
        access_token="tok_sap",
        base_url="https://sap.example.com",
        company_code="1000",
    )
    defaults.update(overrides)
    return ERPConnection(**defaults)


def _mock_async_client(response):
    """Build a mock httpx.AsyncClient context manager returning *response*."""
    mock_client = AsyncMock()
    mock_client.get.return_value = response
    mock_client.post.return_value = response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _ok_response(payload: dict, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


# ===========================================================================
# QuickBooks — get_chart_of_accounts_quickbooks
# ===========================================================================

class TestChartOfAccountsQuickBooks:
    def test_returns_normalized_accounts(self):
        payload = {
            "QueryResponse": {
                "Account": [
                    {
                        "Id": "1",
                        "AcctNum": "5000",
                        "Name": "Cost of Goods Sold",
                        "AccountType": "Cost of Goods Sold",
                        "AccountSubType": "SuppliesMaterialsCogs",
                        "Active": True,
                        "CurrencyRef": {"value": "USD"},
                    },
                    {
                        "Id": "2",
                        "AcctNum": "6200",
                        "Name": "Office Expenses",
                        "AccountType": "Expense",
                        "AccountSubType": "OfficeGeneralAdministrativeExpenses",
                        "Active": True,
                    },
                    {
                        "Id": "3",
                        "AcctNum": "4000",
                        "Name": "Sales Revenue",
                        "AccountType": "Income",
                        "AccountSubType": "SalesOfProductIncome",
                        "Active": False,
                    },
                ]
            }
        }
        mock_client = _mock_async_client(_ok_response(payload))
        with patch("clearledgr.integrations.erp_quickbooks.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(get_chart_of_accounts_quickbooks(_qb_connection()))

        assert len(result) == 3
        assert result[0]["id"] == "1"
        assert result[0]["code"] == "5000"
        assert result[0]["type"] == "expense"
        assert result[0]["active"] is True
        assert result[1]["type"] == "expense"
        assert result[2]["type"] == "revenue"
        assert result[2]["active"] is False

    def test_no_credentials(self):
        result = asyncio.run(get_chart_of_accounts_quickbooks(ERPConnection(type="quickbooks")))
        assert result == []

    def test_error_returns_empty(self):
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("timeout")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("clearledgr.integrations.erp_quickbooks.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(get_chart_of_accounts_quickbooks(_qb_connection()))
        assert result == []

    def test_401_returns_empty(self):
        resp = MagicMock()
        resp.status_code = 401
        mock_client = _mock_async_client(resp)
        with patch("clearledgr.integrations.erp_quickbooks.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(get_chart_of_accounts_quickbooks(_qb_connection()))
        assert result == []


# ===========================================================================
# Xero — get_chart_of_accounts_xero
# ===========================================================================

class TestChartOfAccountsXero:
    def test_returns_normalized_accounts(self):
        payload = {
            "Accounts": [
                {
                    "AccountID": "a1",
                    "Code": "200",
                    "Name": "Sales",
                    "Type": "REVENUE",
                    "Class": "REVENUE",
                    "Status": "ACTIVE",
                    "CurrencyCode": "NZD",
                },
                {
                    "AccountID": "a2",
                    "Code": "400",
                    "Name": "Advertising",
                    "Type": "EXPENSE",
                    "Class": "EXPENSE",
                    "Status": "ACTIVE",
                },
                {
                    "AccountID": "a3",
                    "Code": "090",
                    "Name": "Bank Account",
                    "Type": "BANK",
                    "Class": "ASSET",
                    "Status": "ARCHIVED",
                },
            ]
        }
        mock_client = _mock_async_client(_ok_response(payload))
        with patch("clearledgr.integrations.erp_xero.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(get_chart_of_accounts_xero(_xero_connection()))

        assert len(result) == 3
        assert result[0]["type"] == "revenue"
        assert result[0]["code"] == "200"
        assert result[0]["active"] is True
        assert result[1]["type"] == "expense"
        assert result[2]["type"] == "asset"
        assert result[2]["active"] is False

    def test_no_credentials(self):
        result = asyncio.run(get_chart_of_accounts_xero(ERPConnection(type="xero")))
        assert result == []


# ===========================================================================
# NetSuite — get_chart_of_accounts_netsuite
# ===========================================================================

class TestChartOfAccountsNetSuite:
    def test_returns_normalized_accounts(self):
        payload = {
            "items": [
                {
                    "id": "100",
                    "acctnumber": "1000",
                    "acctname": "Cash and Cash Equivalents",
                    "accttype": "Bank",
                    "isinactive": "F",
                    "currency": {"refName": "USD"},
                },
                {
                    "id": "200",
                    "acctnumber": "5000",
                    "acctname": "Cost of Goods Sold",
                    "accttype": "COGS",
                    "isinactive": False,
                },
                {
                    "id": "300",
                    "acctnumber": "4000",
                    "acctname": "Sales Revenue",
                    "accttype": "Income",
                    "isinactive": "T",
                },
            ]
        }
        mock_client = _mock_async_client(_ok_response(payload))
        with patch("clearledgr.integrations.erp_netsuite.httpx.AsyncClient", return_value=mock_client):
            with patch("clearledgr.integrations.erp_netsuite._oauth_header", return_value="OAuth fake"):
                result = asyncio.run(get_chart_of_accounts_netsuite(_netsuite_connection()))

        assert len(result) == 3
        assert result[0]["type"] == "asset"
        assert result[0]["code"] == "1000"
        assert result[0]["active"] is True
        assert result[0]["currency"] == "USD"
        assert result[1]["type"] == "expense"
        assert result[2]["type"] == "revenue"
        assert result[2]["active"] is False

    def test_no_account_id(self):
        result = asyncio.run(get_chart_of_accounts_netsuite(ERPConnection(type="netsuite")))
        assert result == []


# ===========================================================================
# SAP — get_chart_of_accounts_sap
# ===========================================================================

class TestChartOfAccountsSAP:
    def test_returns_normalized_accounts(self):
        session_resp = {
            "status": "success",
            "erp": "sap",
            "session_cookie": "sess123",
            "csrf_token": None,
            "headers": {"Cookie": "B1SESSION=sess123"},
        }
        coa_payload = {
            "value": [
                {
                    "Code": "6000",
                    "Name": "General Expenses",
                    "AcctCurrency": "EUR",
                    "ActiveAccount": "tYES",
                    "GroupCode": "5",
                },
                {
                    "Code": "4000",
                    "Name": "Revenue",
                    "AcctCurrency": "EUR",
                    "ActiveAccount": "tNO",
                    "GroupCode": "4",
                },
            ]
        }

        with patch(
            "clearledgr.integrations.erp_sap._open_sap_service_layer_session",
            new_callable=AsyncMock,
            return_value=session_resp,
        ):
            mock_client = _mock_async_client(_ok_response(coa_payload))
            with patch("clearledgr.integrations.erp_sap.httpx.AsyncClient", return_value=mock_client):
                result = asyncio.run(get_chart_of_accounts_sap(_sap_connection()))

        assert len(result) == 2
        assert result[0]["type"] == "expense"
        assert result[0]["code"] == "6000"
        assert result[0]["active"] is True
        assert result[0]["currency"] == "EUR"
        assert result[1]["type"] == "revenue"
        assert result[1]["active"] is False

    def test_no_credentials(self):
        result = asyncio.run(get_chart_of_accounts_sap(ERPConnection(type="sap")))
        assert result == []


# ===========================================================================
# Router dispatcher — get_chart_of_accounts (cache miss, cache hit, force refresh)
# ===========================================================================

class TestChartOfAccountsDispatcher:
    def test_cache_miss_fetches_from_erp(self, db):
        org_id = "org_coa_miss"
        db.create_organization(organization_id=org_id, name="Test Org COA", settings={})
        db.save_erp_connection(
            organization_id=org_id, erp_type="quickbooks",
            access_token="tok", refresh_token=None, realm_id="realm1",
            tenant_id=None, base_url=None, credentials=None,
        )

        mock_accounts = [
            {"id": "1", "code": "5000", "name": "COGS", "type": "expense",
             "sub_type": "", "active": True, "currency": "USD"},
        ]

        mock_fetcher = AsyncMock(return_value=mock_accounts)
        with patch.dict(
            "clearledgr.integrations.erp_router._CHART_OF_ACCOUNTS_FETCHERS",
            {"quickbooks": mock_fetcher},
        ):
            result = asyncio.run(get_chart_of_accounts(org_id))

        assert len(result) == 1
        assert result[0]["code"] == "5000"
        mock_fetcher.assert_called_once()

        # Verify it was cached
        org = db.get_organization(org_id)
        cache = (org.get("settings_json") or {}).get("chart_of_accounts_cache")
        assert cache is not None
        assert cache["account_count"] == 1

    def test_cache_hit_returns_cached(self, db):
        org_id = "org_coa_hit"
        cached_accounts = [
            {"id": "99", "code": "9999", "name": "Cached", "type": "expense",
             "sub_type": "", "active": True, "currency": ""},
        ]
        settings = {
            "chart_of_accounts_cache": {
                "accounts": cached_accounts,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "erp_type": "quickbooks",
                "account_count": 1,
            }
        }
        db.create_organization(organization_id=org_id, name="Test Org Cached", settings=settings)
        db.save_erp_connection(
            organization_id=org_id, erp_type="quickbooks",
            access_token="tok", refresh_token=None, realm_id="realm1",
            tenant_id=None, base_url=None, credentials=None,
        )

        with patch(
            "clearledgr.integrations.erp_router.get_chart_of_accounts_quickbooks",
            new_callable=AsyncMock,
        ) as mock_fetch:
            result = asyncio.run(get_chart_of_accounts(org_id))

        assert len(result) == 1
        assert result[0]["code"] == "9999"
        mock_fetch.assert_not_called()

    def test_stale_cache_fetches_fresh(self, db):
        org_id = "org_coa_stale"
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        settings = {
            "chart_of_accounts_cache": {
                "accounts": [{"id": "old", "code": "OLD"}],
                "fetched_at": stale_time,
                "erp_type": "quickbooks",
                "account_count": 1,
            }
        }
        db.create_organization(organization_id=org_id, name="Test Org Stale", settings=settings)
        db.save_erp_connection(
            organization_id=org_id, erp_type="quickbooks",
            access_token="tok", refresh_token=None, realm_id="realm1",
            tenant_id=None, base_url=None, credentials=None,
        )

        fresh_accounts = [
            {"id": "new", "code": "NEW", "name": "Fresh", "type": "expense",
             "sub_type": "", "active": True, "currency": ""},
        ]

        mock_fetcher = AsyncMock(return_value=fresh_accounts)
        with patch.dict(
            "clearledgr.integrations.erp_router._CHART_OF_ACCOUNTS_FETCHERS",
            {"quickbooks": mock_fetcher},
        ):
            result = asyncio.run(get_chart_of_accounts(org_id))

        assert len(result) == 1
        assert result[0]["code"] == "NEW"
        mock_fetcher.assert_called_once()

    def test_force_refresh_bypasses_cache(self, db):
        org_id = "org_coa_force"
        settings = {
            "chart_of_accounts_cache": {
                "accounts": [{"id": "cached", "code": "CACHED"}],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "erp_type": "xero",
                "account_count": 1,
            }
        }
        db.create_organization(organization_id=org_id, name="Test Org Force", settings=settings)
        db.save_erp_connection(
            organization_id=org_id, erp_type="xero",
            access_token="tok", refresh_token=None, realm_id=None,
            tenant_id="t1", base_url=None, credentials=None,
        )

        fresh_accounts = [
            {"id": "fresh", "code": "FRESH", "name": "Fresh Xero", "type": "revenue",
             "sub_type": "", "active": True, "currency": ""},
        ]

        mock_fetcher = AsyncMock(return_value=fresh_accounts)
        with patch.dict(
            "clearledgr.integrations.erp_router._CHART_OF_ACCOUNTS_FETCHERS",
            {"xero": mock_fetcher},
        ):
            result = asyncio.run(get_chart_of_accounts(org_id, force_refresh=True))

        assert result[0]["code"] == "FRESH"
        mock_fetcher.assert_called_once()

    def test_no_erp_connection_returns_empty(self, db):
        org_id = "org_no_erp"
        db.create_organization(organization_id=org_id, name="No ERP", settings={})
        result = asyncio.run(get_chart_of_accounts(org_id))
        assert result == []

    def test_fetch_error_returns_empty(self, db):
        org_id = "org_coa_err"
        db.create_organization(organization_id=org_id, name="Err Org", settings={})
        db.save_erp_connection(
            organization_id=org_id, erp_type="quickbooks",
            access_token="tok", refresh_token=None, realm_id="realm1",
            tenant_id=None, base_url=None, credentials=None,
        )

        mock_fetcher = AsyncMock(side_effect=Exception("network error"))
        with patch.dict(
            "clearledgr.integrations.erp_router._CHART_OF_ACCOUNTS_FETCHERS",
            {"quickbooks": mock_fetcher},
        ):
            result = asyncio.run(get_chart_of_accounts(org_id))

        assert result == []


# ===========================================================================
# Account type normalization
# ===========================================================================

class TestAccountTypeNormalization:
    """Verify that raw ERP account types map to the standard set."""

    def test_quickbooks_type_mapping(self):
        from clearledgr.integrations.erp_quickbooks import _QB_ACCOUNT_TYPE_MAP
        assert _QB_ACCOUNT_TYPE_MAP["expense"] == "expense"
        assert _QB_ACCOUNT_TYPE_MAP["income"] == "revenue"
        assert _QB_ACCOUNT_TYPE_MAP["bank"] == "asset"
        assert _QB_ACCOUNT_TYPE_MAP["accounts payable"] == "liability"
        assert _QB_ACCOUNT_TYPE_MAP["equity"] == "equity"

    def test_xero_type_mapping(self):
        from clearledgr.integrations.erp_xero import _XERO_ACCOUNT_TYPE_MAP
        assert _XERO_ACCOUNT_TYPE_MAP["expense"] == "expense"
        assert _XERO_ACCOUNT_TYPE_MAP["revenue"] == "revenue"
        assert _XERO_ACCOUNT_TYPE_MAP["bank"] == "asset"
        assert _XERO_ACCOUNT_TYPE_MAP["currliab"] == "liability"
        assert _XERO_ACCOUNT_TYPE_MAP["equity"] == "equity"

    def test_netsuite_type_mapping(self):
        from clearledgr.integrations.erp_netsuite import _NS_ACCOUNT_TYPE_MAP
        assert _NS_ACCOUNT_TYPE_MAP["expense"] == "expense"
        assert _NS_ACCOUNT_TYPE_MAP["income"] == "revenue"
        assert _NS_ACCOUNT_TYPE_MAP["bank"] == "asset"
        assert _NS_ACCOUNT_TYPE_MAP["acctpay"] == "liability"
        assert _NS_ACCOUNT_TYPE_MAP["equity"] == "equity"

    def test_sap_group_code_mapping(self):
        from clearledgr.integrations.erp_sap import _SAP_GROUP_CODE_MAP
        assert _SAP_GROUP_CODE_MAP["5"] == "expense"
        assert _SAP_GROUP_CODE_MAP["4"] == "revenue"
        assert _SAP_GROUP_CODE_MAP["1"] == "asset"
        assert _SAP_GROUP_CODE_MAP["2"] == "liability"
        assert _SAP_GROUP_CODE_MAP["3"] == "equity"


# ===========================================================================
# GL validation with cached chart of accounts
# ===========================================================================

class TestGLValidationWithCachedCOA:
    def test_gl_codes_valid_against_cached_coa(self, db):
        org_id = "org_gl_coa"
        settings = {
            "chart_of_accounts_cache": {
                "accounts": [
                    {"id": "1", "code": "5000", "name": "COGS", "type": "expense",
                     "active": True, "currency": ""},
                    {"id": "2", "code": "6200", "name": "Office", "type": "expense",
                     "active": True, "currency": ""},
                ],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "erp_type": "quickbooks",
                "account_count": 2,
            }
        }
        db.create_organization(organization_id=org_id, name="GL COA Org", settings=settings)
        db.save_erp_connection(
            organization_id=org_id, erp_type="quickbooks",
            access_token="tok", refresh_token=None, realm_id="realm1",
            tenant_id=None, base_url=None, credentials=None,
        )

        # The GL code "5000" is in the cached COA but NOT in any gl_account_map
        result = asyncio.run(erp_preflight_check(org_id, gl_codes=["5000"]))

        assert result["gl_valid"] is True
        assert result["invalid_gl_codes"] == []
        assert "gl_validation" in result["checks_run"]

    def test_gl_codes_invalid_against_cached_coa(self, db):
        org_id = "org_gl_invalid"
        settings = {
            "chart_of_accounts_cache": {
                "accounts": [
                    {"id": "1", "code": "5000", "name": "COGS", "type": "expense",
                     "active": True, "currency": ""},
                ],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "erp_type": "quickbooks",
                "account_count": 1,
            }
        }
        db.create_organization(organization_id=org_id, name="GL Invalid Org", settings=settings)
        db.save_erp_connection(
            organization_id=org_id, erp_type="quickbooks",
            access_token="tok", refresh_token=None, realm_id="realm1",
            tenant_id=None, base_url=None, credentials=None,
        )

        result = asyncio.run(erp_preflight_check(org_id, gl_codes=["9999"]))

        assert result["gl_valid"] is False
        assert "9999" in result["invalid_gl_codes"]

    def test_gl_codes_valid_by_id_match(self, db):
        """Account ID (not just code) should be accepted as valid."""
        org_id = "org_gl_id"
        settings = {
            "chart_of_accounts_cache": {
                "accounts": [
                    {"id": "42", "code": "5000", "name": "COGS", "type": "expense",
                     "active": True, "currency": ""},
                ],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "erp_type": "quickbooks",
                "account_count": 1,
            }
        }
        db.create_organization(organization_id=org_id, name="GL ID Org", settings=settings)
        db.save_erp_connection(
            organization_id=org_id, erp_type="quickbooks",
            access_token="tok", refresh_token=None, realm_id="realm1",
            tenant_id=None, base_url=None, credentials=None,
        )

        result = asyncio.run(erp_preflight_check(org_id, gl_codes=["42"]))
        assert result["gl_valid"] is True


# ===========================================================================
# API endpoint
# ===========================================================================

class TestChartOfAccountsEndpoint:
    """Test the workspace API endpoint for chart of accounts."""

    def test_endpoint_returns_accounts(self, db):

        org_id = "org_api_coa"
        db.create_organization(organization_id=org_id, name="API COA Org", settings={})
        db.save_erp_connection(
            organization_id=org_id, erp_type="quickbooks",
            access_token="tok", refresh_token=None, realm_id="realm1",
            tenant_id=None, base_url=None, credentials=None,
        )

        mock_accounts = [
            {"id": "1", "code": "5000", "name": "COGS", "type": "expense",
             "sub_type": "", "active": True, "currency": "USD"},
            {"id": "2", "code": "6200", "name": "Office", "type": "expense",
             "sub_type": "", "active": False, "currency": "USD"},
        ]

        from clearledgr.api.workspace_shell import get_chart_of_accounts_endpoint

        mock_user = MagicMock()
        mock_user.organization_id = org_id
        mock_user.role = "admin"

        with patch(
            "clearledgr.api.workspace_shell._resolve_org_id",
            return_value=org_id,
        ):
            with patch(
                "clearledgr.integrations.erp_router.get_chart_of_accounts",
                new_callable=AsyncMock,
                return_value=mock_accounts,
            ):
                result = asyncio.run(
                    get_chart_of_accounts_endpoint(
                        organization_id=org_id,
                        force_refresh=False,
                        account_type=None,
                        active_only=True,
                        user=mock_user,
                    )
                )

        # active_only=True should filter out the inactive account
        assert result["account_count"] == 1
        assert result["accounts"][0]["code"] == "5000"

    def test_endpoint_filters_by_type(self, db):
        org_id = "org_api_filter"
        db.create_organization(organization_id=org_id, name="API Filter Org", settings={})

        mock_accounts = [
            {"id": "1", "code": "5000", "name": "COGS", "type": "expense",
             "sub_type": "", "active": True, "currency": ""},
            {"id": "2", "code": "4000", "name": "Revenue", "type": "revenue",
             "sub_type": "", "active": True, "currency": ""},
        ]

        from clearledgr.api.workspace_shell import get_chart_of_accounts_endpoint

        mock_user = MagicMock()
        mock_user.organization_id = org_id
        mock_user.role = "admin"

        with patch("clearledgr.api.workspace_shell._resolve_org_id", return_value=org_id):
            with patch(
                "clearledgr.integrations.erp_router.get_chart_of_accounts",
                new_callable=AsyncMock,
                return_value=mock_accounts,
            ):
                result = asyncio.run(
                    get_chart_of_accounts_endpoint(
                        organization_id=org_id,
                        force_refresh=False,
                        account_type="expense",
                        active_only=False,
                        user=mock_user,
                    )
                )

        assert result["account_count"] == 1
        assert result["accounts"][0]["type"] == "expense"


# ===========================================================================
# Cache helpers
# ===========================================================================

class TestCacheHelpers:
    def test_save_and_retrieve_cache(self, db):
        org_id = "org_cache_test"
        db.create_organization(organization_id=org_id, name="Cache Test", settings={})

        accounts = [
            {"id": "1", "code": "5000", "name": "COGS", "type": "expense",
             "sub_type": "", "active": True, "currency": "USD"},
        ]

        _save_chart_of_accounts_cache(org_id, accounts, "quickbooks")
        cached = _get_cached_chart_of_accounts(org_id)

        assert cached is not None
        assert cached["erp_type"] == "quickbooks"
        assert cached["account_count"] == 1
        assert cached["accounts"][0]["code"] == "5000"

    def test_no_cache_returns_none(self, db):
        org_id = "org_no_cache"
        db.create_organization(organization_id=org_id, name="No Cache", settings={})
        assert _get_cached_chart_of_accounts(org_id) is None

    def test_stale_cache_returns_none(self, db):
        org_id = "org_stale_cache"
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        settings = {
            "chart_of_accounts_cache": {
                "accounts": [],
                "fetched_at": stale_time,
                "erp_type": "quickbooks",
                "account_count": 0,
            }
        }
        db.create_organization(organization_id=org_id, name="Stale Cache", settings=settings)
        assert _get_cached_chart_of_accounts(org_id) is None

    def test_nonexistent_org_returns_none(self, db):
        assert _get_cached_chart_of_accounts("nonexistent_org_xyz") is None
