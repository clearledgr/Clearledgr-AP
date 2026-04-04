"""Tests for paginated vendor list: ERP functions, router dispatcher, caching, API endpoint.

Covers:
- Per-ERP list functions (QuickBooks, Xero, NetSuite, SAP) with mocked HTTP
- Pagination: multiple pages fetched and concatenated
- Normalized vendor output schema
- Router dispatcher (list_all_vendors) with connection resolution
- Cache layer: save, retrieve, TTL expiry, force_refresh bypass
- API endpoint: GET /api/workspace/erp-vendors with filters
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData
from clearledgr.integrations.erp_router import (
    ERPConnection,
    list_all_vendors,
    list_all_vendors_quickbooks,
    list_all_vendors_xero,
    list_all_vendors_netsuite,
    list_all_vendors_sap,
    _get_cached_vendor_list,
    _save_vendor_list_cache,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "vendor-list.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
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


def _mock_async_client(*responses):
    """Build a mock httpx.AsyncClient that returns responses in sequence."""
    mock_client = AsyncMock()
    if len(responses) == 1:
        mock_client.get.return_value = responses[0]
        mock_client.post.return_value = responses[0]
    else:
        mock_client.get.side_effect = list(responses)
        mock_client.post.side_effect = list(responses)
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
# QuickBooks
# ===========================================================================

class TestListVendorsQuickBooks:
    def test_returns_normalized_vendors(self):
        payload = {
            "QueryResponse": {
                "Vendor": [
                    {
                        "Id": "101",
                        "DisplayName": "Acme Corp",
                        "PrimaryEmailAddr": {"Address": "ap@acme.com"},
                        "PrimaryPhone": {"FreeFormNumber": "555-1234"},
                        "TaxIdentifier": "12-3456789",
                        "CurrencyRef": {"value": "USD"},
                        "Active": True,
                        "BillAddr": {
                            "Line1": "123 Main St",
                            "City": "Austin",
                            "CountrySubDivisionCode": "TX",
                            "PostalCode": "78701",
                            "Country": "US",
                        },
                        "Balance": 5000.0,
                    },
                ]
            }
        }
        mock_client = _mock_async_client(_ok_response(payload))
        with patch("clearledgr.integrations.erp_quickbooks.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(list_all_vendors_quickbooks(_qb_connection()))

        assert len(result) == 1
        v = result[0]
        assert v["vendor_id"] == "101"
        assert v["name"] == "Acme Corp"
        assert v["email"] == "ap@acme.com"
        assert v["phone"] == "555-1234"
        assert v["tax_id"] == "12-3456789"
        assert v["active"] is True
        assert "Austin" in v["address"]
        assert v["balance"] == 5000.0

    def test_pagination_multiple_pages(self):
        page1 = {"QueryResponse": {"Vendor": [{"Id": str(i), "DisplayName": f"V{i}", "Active": True} for i in range(1000)]}}
        page2 = {"QueryResponse": {"Vendor": [{"Id": "1001", "DisplayName": "V1001", "Active": True}]}}

        mock_client = _mock_async_client(_ok_response(page1), _ok_response(page2))
        with patch("clearledgr.integrations.erp_quickbooks.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(list_all_vendors_quickbooks(_qb_connection()))

        assert len(result) == 1001

    def test_empty_on_missing_token(self):
        result = asyncio.run(list_all_vendors_quickbooks(_qb_connection(access_token=None)))
        assert result == []

    def test_empty_on_401(self):
        resp = _ok_response({}, status_code=401)
        resp.status_code = 401
        mock_client = _mock_async_client(resp)
        with patch("clearledgr.integrations.erp_quickbooks.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(list_all_vendors_quickbooks(_qb_connection()))
        assert result == []


# ===========================================================================
# Xero
# ===========================================================================

class TestListVendorsXero:
    def test_returns_normalized_vendors(self):
        payload = {
            "Contacts": [
                {
                    "ContactID": "x-200",
                    "Name": "Beta LLC",
                    "EmailAddress": "hello@beta.io",
                    "TaxNumber": "GB123456",
                    "DefaultCurrency": "GBP",
                    "ContactStatus": "ACTIVE",
                    "Addresses": [
                        {"AddressType": "STREET", "AddressLine1": "10 High St", "City": "London", "Country": "UK"},
                    ],
                    "Phones": [
                        {"PhoneType": "DEFAULT", "PhoneNumber": "020-1234"},
                    ],
                    "Balances": {"AccountsPayable": {"Outstanding": 1200.0}},
                },
            ]
        }
        mock_client = _mock_async_client(_ok_response(payload))
        with patch("clearledgr.integrations.erp_xero.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(list_all_vendors_xero(_xero_connection()))

        assert len(result) == 1
        v = result[0]
        assert v["vendor_id"] == "x-200"
        assert v["name"] == "Beta LLC"
        assert v["email"] == "hello@beta.io"
        assert v["currency"] == "GBP"
        assert v["active"] is True
        assert "London" in v["address"]
        assert v["balance"] == 1200.0

    def test_pagination_stops_at_partial_page(self):
        # Xero pages are 100 contacts; <100 means last page
        contacts = [{"ContactID": str(i), "Name": f"V{i}", "ContactStatus": "ACTIVE"} for i in range(50)]
        mock_client = _mock_async_client(_ok_response({"Contacts": contacts}))
        with patch("clearledgr.integrations.erp_xero.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(list_all_vendors_xero(_xero_connection()))
        assert len(result) == 50

    def test_empty_on_missing_tenant_id(self):
        result = asyncio.run(list_all_vendors_xero(_xero_connection(tenant_id=None)))
        assert result == []


# ===========================================================================
# NetSuite
# ===========================================================================

class TestListVendorsNetSuite:
    def test_returns_normalized_vendors(self):
        payload = {
            "items": [
                {
                    "id": 300,
                    "companyName": "Gamma Inc",
                    "email": "ap@gamma.ng",
                    "phone": "+234-1234",
                    "defaultAddress": "Lagos, Nigeria",
                    "terms": {"refName": "Net 30"},
                    "isInactive": False,
                    "currency": {"refName": "NGN"},
                },
            ]
        }
        mock_client = _mock_async_client(_ok_response(payload))
        with patch("clearledgr.integrations.erp_netsuite.httpx.AsyncClient", return_value=mock_client):
            with patch("clearledgr.integrations.erp_netsuite._oauth_header", return_value="OAuth ..."):
                result = asyncio.run(list_all_vendors_netsuite(_netsuite_connection()))

        assert len(result) == 1
        v = result[0]
        assert v["vendor_id"] == "300"
        assert v["name"] == "Gamma Inc"
        assert v["currency"] == "NGN"
        assert v["payment_terms"] == "Net 30"
        assert v["active"] is True
        assert "Lagos" in v["address"]

    def test_inactive_vendor_detected(self):
        payload = {"items": [{"id": 1, "companyName": "Dead Vendor", "isInactive": "T"}]}
        mock_client = _mock_async_client(_ok_response(payload))
        with patch("clearledgr.integrations.erp_netsuite.httpx.AsyncClient", return_value=mock_client):
            with patch("clearledgr.integrations.erp_netsuite._oauth_header", return_value="OAuth ..."):
                result = asyncio.run(list_all_vendors_netsuite(_netsuite_connection()))

        assert result[0]["active"] is False

    def test_empty_on_missing_account_id(self):
        result = asyncio.run(list_all_vendors_netsuite(_netsuite_connection(account_id=None)))
        assert result == []


# ===========================================================================
# SAP
# ===========================================================================

class TestListVendorsSAP:
    def test_returns_normalized_vendors(self):
        session_resp = {"status": "success", "headers": {"Cookie": "sess=abc"}}
        vendor_payload = {
            "value": [
                {
                    "CardCode": "S400",
                    "CardName": "Delta GmbH",
                    "EmailAddress": "info@delta.de",
                    "Phone1": "+49-123",
                    "Address": "Berlin, Germany",
                    "FederalTaxID": "DE123456",
                    "Currency": "EUR",
                    "PayTermsGrpCode": "30D",
                    "CurrentAccountBalance": 8000.0,
                    "Valid": "tYES",
                },
            ]
        }
        mock_client = _mock_async_client(_ok_response(vendor_payload))
        with patch("clearledgr.integrations.erp_sap.httpx.AsyncClient", return_value=mock_client):
            with patch("clearledgr.integrations.erp_sap._open_sap_service_layer_session", new_callable=AsyncMock, return_value=session_resp):
                result = asyncio.run(list_all_vendors_sap(_sap_connection()))

        assert len(result) == 1
        v = result[0]
        assert v["vendor_id"] == "S400"
        assert v["name"] == "Delta GmbH"
        assert v["currency"] == "EUR"
        assert v["tax_id"] == "DE123456"
        assert v["balance"] == 8000.0
        assert v["active"] is True

    def test_invalid_vendor_detected(self):
        session_resp = {"status": "success", "headers": {"Cookie": "sess=abc"}}
        vendor_payload = {"value": [{"CardCode": "S1", "CardName": "Old Vendor", "Valid": "tNO"}]}
        mock_client = _mock_async_client(_ok_response(vendor_payload))
        with patch("clearledgr.integrations.erp_sap.httpx.AsyncClient", return_value=mock_client):
            with patch("clearledgr.integrations.erp_sap._open_sap_service_layer_session", new_callable=AsyncMock, return_value=session_resp):
                result = asyncio.run(list_all_vendors_sap(_sap_connection()))

        assert result[0]["active"] is False

    def test_empty_on_session_failure(self):
        session_resp = {"status": "error"}
        mock_client = _mock_async_client(_ok_response({}))
        with patch("clearledgr.integrations.erp_sap.httpx.AsyncClient", return_value=mock_client):
            with patch("clearledgr.integrations.erp_sap._open_sap_service_layer_session", new_callable=AsyncMock, return_value=session_resp):
                result = asyncio.run(list_all_vendors_sap(_sap_connection()))
        assert result == []


# ===========================================================================
# Cache layer
# ===========================================================================

class TestVendorListCache:
    def test_save_and_retrieve(self, db):
        db.create_organization("cache-org", "Cache Org", settings={})
        vendors = [{"vendor_id": "1", "name": "Test Vendor", "active": True}]

        _save_vendor_list_cache("cache-org", vendors, "quickbooks")
        cached = _get_cached_vendor_list("cache-org")

        assert cached is not None
        assert cached["vendors"] == vendors
        assert cached["vendor_count"] == 1
        assert cached["erp_type"] == "quickbooks"

    def test_cache_miss_when_empty(self, db):
        db.create_organization("empty-org", "Empty Org", settings={})
        assert _get_cached_vendor_list("empty-org") is None

    def test_cache_miss_when_expired(self, db):
        db.create_organization("stale-org", "Stale Org", settings={})
        # Save with a timestamp 25 hours ago
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        db.update_organization("stale-org", settings_json={
            "vendor_list_cache": {
                "vendors": [{"vendor_id": "1", "name": "Old"}],
                "fetched_at": stale_time,
                "erp_type": "xero",
                "vendor_count": 1,
            }
        })
        assert _get_cached_vendor_list("stale-org") is None

    def test_cache_hit_when_fresh(self, db):
        db.create_organization("fresh-org", "Fresh Org", settings={})
        fresh_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.update_organization("fresh-org", settings_json={
            "vendor_list_cache": {
                "vendors": [{"vendor_id": "1", "name": "Fresh"}],
                "fetched_at": fresh_time,
                "erp_type": "netsuite",
                "vendor_count": 1,
            }
        })
        cached = _get_cached_vendor_list("fresh-org")
        assert cached is not None
        assert cached["vendors"][0]["name"] == "Fresh"


# ===========================================================================
# Router dispatcher (list_all_vendors)
# ===========================================================================

class TestListAllVendorsRouter:
    def test_dispatches_to_correct_erp(self, db):
        db.create_organization("qb-org", "QB Org", settings={})
        mock_vendors = [{"vendor_id": "1", "name": "QB Vendor", "active": True}]
        mock_fetcher = AsyncMock(return_value=mock_vendors)

        with patch("clearledgr.integrations.erp_router.get_erp_connection") as mock_conn:
            mock_conn.return_value = _qb_connection()
            with patch.dict("clearledgr.integrations.erp_router._VENDOR_LIST_FETCHERS", {"quickbooks": mock_fetcher}):
                result = asyncio.run(list_all_vendors("qb-org"))

        assert len(result) == 1
        assert result[0]["name"] == "QB Vendor"
        mock_fetcher.assert_called_once()

    def test_returns_empty_when_no_connection(self, db):
        with patch("clearledgr.integrations.erp_router.get_erp_connection", return_value=None):
            result = asyncio.run(list_all_vendors("no-erp-org"))
        assert result == []

    def test_uses_cache_when_available(self, db):
        db.create_organization("cached-org", "Cached Org", settings={})
        _save_vendor_list_cache("cached-org", [{"vendor_id": "c1", "name": "Cached"}], "xero")

        # Should NOT call the fetcher — cache hit
        with patch("clearledgr.integrations.erp_router.get_erp_connection") as mock_conn:
            result = asyncio.run(list_all_vendors("cached-org"))
            mock_conn.assert_not_called()

        assert len(result) == 1
        assert result[0]["name"] == "Cached"

    def test_force_refresh_bypasses_cache(self, db):
        db.create_organization("refresh-org", "Refresh Org", settings={})
        _save_vendor_list_cache("refresh-org", [{"vendor_id": "old", "name": "Old"}], "sap")
        fresh_vendors = [{"vendor_id": "new", "name": "New", "active": True}]
        mock_fetcher = AsyncMock(return_value=fresh_vendors)

        with patch("clearledgr.integrations.erp_router.get_erp_connection") as mock_conn:
            mock_conn.return_value = _sap_connection()
            with patch.dict("clearledgr.integrations.erp_router._VENDOR_LIST_FETCHERS", {"sap": mock_fetcher}):
                result = asyncio.run(list_all_vendors("refresh-org", force_refresh=True))

        assert len(result) == 1
        assert result[0]["name"] == "New"


# ===========================================================================
# API endpoint
# ===========================================================================

class TestERPVendorsEndpoint:
    @pytest.fixture()
    def client(self, db, monkeypatch):
        monkeypatch.setenv("WORKSPACE_SHELL_ENABLED", "true")
        from main import app
        from clearledgr.api import workspace_shell as ws_module

        def _fake_user():
            return TokenData(
                user_id="u1",
                email="user@example.com",
                organization_id="default",
                role="owner",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        app.dependency_overrides[ws_module.get_current_user] = _fake_user
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(ws_module.get_current_user, None)

    def _patch_list(self, vendors):
        """Patch the lazy import inside workspace_shell's endpoint."""
        return patch(
            "clearledgr.integrations.erp_router.list_all_vendors",
            new_callable=AsyncMock,
            return_value=vendors,
        )

    def test_endpoint_returns_200(self, client, db):
        mock_vendors = [
            {"vendor_id": "1", "name": "Acme", "email": "a@acme.com", "active": True},
            {"vendor_id": "2", "name": "Beta", "email": "b@beta.com", "active": False},
        ]
        with self._patch_list(mock_vendors):
            resp = client.get("/api/workspace/erp-vendors")

        assert resp.status_code == 200
        data = resp.json()
        assert "vendors" in data
        assert "vendor_count" in data
        # active_only=True by default, so inactive vendor is filtered
        assert data["vendor_count"] == 1
        assert data["vendors"][0]["name"] == "Acme"

    def test_endpoint_search_filter(self, client, db):
        mock_vendors = [
            {"vendor_id": "1", "name": "Acme Corp", "email": "a@acme.com", "active": True},
            {"vendor_id": "2", "name": "Beta LLC", "email": "b@beta.com", "active": True},
        ]
        with self._patch_list(mock_vendors):
            resp = client.get("/api/workspace/erp-vendors?search=beta&active_only=false")

        assert resp.status_code == 200
        data = resp.json()
        assert data["vendor_count"] == 1
        assert data["vendors"][0]["name"] == "Beta LLC"

    def test_endpoint_all_vendors_when_not_active_only(self, client, db):
        mock_vendors = [
            {"vendor_id": "1", "name": "Active", "active": True},
            {"vendor_id": "2", "name": "Inactive", "active": False},
        ]
        with self._patch_list(mock_vendors):
            resp = client.get("/api/workspace/erp-vendors?active_only=false")

        assert resp.status_code == 200
        assert resp.json()["vendor_count"] == 2
