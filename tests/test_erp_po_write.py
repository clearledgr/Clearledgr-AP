"""ERP PO write-back — dispatch, flag gating, QB/Xero reference adapters (mocked HTTP)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.integrations import erp_po_write  # noqa: E402


class _FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._resp


def _conn(erp_type, **kw):
    defaults = {"access_token": "tok", "realm_id": None, "tenant_id": None, "base_url": None}
    defaults.update(kw)
    return SimpleNamespace(type=erp_type, **defaults)


def _po(**kw):
    base = {"po_id": "PO-erp-1", "po_number": "PO-erp-1", "vendor_name": "Acme",
            "vendor_id": "V1", "total_amount": 1000.0, "currency": "GBP",
            "line_items": [{"description": "Widget", "quantity": 2, "unit_price": 500.0}]}
    base.update(kw)
    return base


def test_disabled_when_flag_off(monkeypatch):
    monkeypatch.setattr(erp_po_write, "is_procurement_erp_write_enabled", lambda: False)
    out = asyncio.run(erp_po_write.create_purchase_order("org1", _po()))
    assert out["status"] == "disabled"


def test_idempotent_when_already_issued(monkeypatch):
    monkeypatch.setattr(erp_po_write, "is_procurement_erp_write_enabled", lambda: True)
    out = asyncio.run(erp_po_write.create_purchase_order("org1", _po(erp_po_id="EXISTING")))
    assert out["status"] == "already_issued" and out["erp_po_id"] == "EXISTING"


def test_quickbooks_adapter_builds_request_and_returns_id(monkeypatch):
    monkeypatch.setattr(erp_po_write, "is_procurement_erp_write_enabled", lambda: True)
    monkeypatch.setattr(
        "solden.integrations.erp_router.get_erp_connection",
        lambda org: _conn("quickbooks", realm_id="REALM9"),
    )
    fake = _FakeClient(_FakeResp(200, {"PurchaseOrder": {"Id": "QB-PO-77"}}))
    monkeypatch.setattr(erp_po_write, "get_http_client", lambda: fake)
    out = asyncio.run(erp_po_write.create_purchase_order("org1", _po(), idempotency_key="idem-1"))
    assert out["status"] == "success" and out["erp_po_id"] == "QB-PO-77"
    url, kwargs = fake.calls[0]
    assert "/v3/company/REALM9/purchaseorder" in url
    assert "requestid=idem-1" in url
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["json"]["VendorRef"]["value"] == "V1"


def test_xero_adapter_builds_request_and_returns_id(monkeypatch):
    monkeypatch.setattr(erp_po_write, "is_procurement_erp_write_enabled", lambda: True)
    monkeypatch.setattr(
        "solden.integrations.erp_router.get_erp_connection",
        lambda org: _conn("xero", tenant_id="TEN1"),
    )
    fake = _FakeClient(_FakeResp(200, {"PurchaseOrders": [{"PurchaseOrderID": "XERO-PO-5"}]}))
    monkeypatch.setattr(erp_po_write, "get_http_client", lambda: fake)
    out = asyncio.run(erp_po_write.create_purchase_order("org1", _po(), idempotency_key="idem-2"))
    assert out["status"] == "success" and out["erp_po_id"] == "XERO-PO-5"
    url, kwargs = fake.calls[0]
    assert url == "https://api.xero.com/api.xro/2.0/PurchaseOrders"
    assert kwargs["headers"]["Xero-tenant-id"] == "TEN1"
    assert kwargs["headers"]["Idempotency-Key"] == "idem-2"
    assert kwargs["json"]["PurchaseOrders"][0]["Contact"]["Name"] == "Acme"


def test_netsuite_and_sap_not_implemented(monkeypatch):
    monkeypatch.setattr(erp_po_write, "is_procurement_erp_write_enabled", lambda: True)
    for erp in ("netsuite", "sap"):
        monkeypatch.setattr(
            "solden.integrations.erp_router.get_erp_connection",
            lambda org, _e=erp: _conn(_e),
        )
        out = asyncio.run(erp_po_write.create_purchase_order("org1", _po()))
        assert out["status"] == "error" and out["reason"] == "po_write_not_implemented"


def test_no_connection(monkeypatch):
    monkeypatch.setattr(erp_po_write, "is_procurement_erp_write_enabled", lambda: True)
    monkeypatch.setattr("solden.integrations.erp_router.get_erp_connection", lambda org: None)
    out = asyncio.run(erp_po_write.create_purchase_order("org1", _po()))
    assert out["status"] == "error" and out["reason"] == "no_erp_connection"


def test_quickbooks_401_needs_reauth(monkeypatch):
    monkeypatch.setattr(erp_po_write, "is_procurement_erp_write_enabled", lambda: True)
    monkeypatch.setattr(
        "solden.integrations.erp_router.get_erp_connection",
        lambda org: _conn("quickbooks", realm_id="R"),
    )
    monkeypatch.setattr(erp_po_write, "get_http_client", lambda: _FakeClient(_FakeResp(401, {})))
    out = asyncio.run(erp_po_write.create_purchase_order("org1", _po()))
    assert out.get("needs_reauth") is True
