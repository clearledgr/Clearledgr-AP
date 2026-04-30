"""End-to-end + per-poster tests for Module 5 Pass C.

Pass A persisted custom field mappings under
``settings_json["erp_field_mappings"][erp_type]``. Pass B surfaced
connection health. Pass C wires the four ERP posters
(NetSuite/SAP/QuickBooks/Xero) to consume those mappings at posting
time:

  * Workflow fields (state/box_id/approver/correlation_id) are
    resolved to their values from the AP item at ``post_bill`` time
    and stamped onto the outbound bill payload.
  * Dimension fields (department/class/location/cost_center/...) are
    renamed in place so the poster writes the dimension under the
    customer's configured field name.

These tests pin the wire shape: each test mocks the ERP HTTP client,
calls the poster, and asserts the captured payload contains the
customer-configured field ids with the expected values.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.integrations.erp_router import (  # noqa: E402
    Bill,
    _dimension_field_name,
    _get_org_field_mappings,
    _resolve_workflow_custom_fields,
)


# ---------------------------------------------------------------------------
# Resolver helpers
# ---------------------------------------------------------------------------


def test_dimension_field_name_uses_default_when_unset():
    assert _dimension_field_name({}, "department_field", "department") == "department"


def test_dimension_field_name_uses_override_when_set():
    fm = {"department_field": "department_2"}
    assert _dimension_field_name(fm, "department_field", "department") == "department_2"


def test_dimension_field_name_falls_back_when_value_blank():
    """Blank override means 'use default' — operators leave a field
    empty in the UI to revert."""
    fm = {"department_field": ""}
    assert _dimension_field_name(fm, "department_field", "department") == "department"


def test_workflow_resolver_skips_unconfigured_fields():
    """An empty mapping returns {} — we never stamp default field ids
    silently, only configured ones."""
    out = _resolve_workflow_custom_fields(
        field_mappings={},
        organization_id="org_1",
        ap_item_id=None,
    )
    assert out == {}


def test_workflow_resolver_returns_empty_when_no_ap_item():
    out = _resolve_workflow_custom_fields(
        field_mappings={"state_field": "custbody_acme_state"},
        organization_id="org_1",
        ap_item_id=None,
    )
    assert out == {}


def test_workflow_resolver_pulls_state_box_id_from_ap_item():
    fake_db = MagicMock()
    fake_db.get_ap_item.return_value = {
        "id": "ap-123",
        "state": "approved",
        "approver_email": "approver@acme.test",
        "correlation_id": "corr-xyz",
    }
    with patch("clearledgr.integrations.erp_router._get_db", return_value=fake_db):
        out = _resolve_workflow_custom_fields(
            field_mappings={
                "state_field": "custbody_acme_state",
                "box_id_field": "custbody_acme_box",
                "approver_field": "custbody_acme_approver",
                "correlation_id_field": "custbody_acme_corr",
            },
            organization_id="org_1",
            ap_item_id="ap-123",
        )
    assert out == {
        "custbody_acme_state": "approved",
        "custbody_acme_box": "ap-123",
        "custbody_acme_approver": "approver@acme.test",
        "custbody_acme_corr": "corr-xyz",
    }


def test_workflow_resolver_skips_keys_without_value():
    fake_db = MagicMock()
    fake_db.get_ap_item.return_value = {"id": "ap-1", "state": "approved"}
    with patch("clearledgr.integrations.erp_router._get_db", return_value=fake_db):
        out = _resolve_workflow_custom_fields(
            field_mappings={
                "state_field": "custbody_state",
                "approver_field": "custbody_approver",  # no approver in ap_item
            },
            organization_id="org_1",
            ap_item_id="ap-1",
        )
    assert out == {"custbody_state": "approved"}
    assert "custbody_approver" not in out


def test_workflow_resolver_swallows_db_errors():
    """A DB hiccup must not block bill posting — return {} instead."""
    fake_db = MagicMock()
    fake_db.get_ap_item.side_effect = RuntimeError("db down")
    with patch("clearledgr.integrations.erp_router._get_db", return_value=fake_db):
        out = _resolve_workflow_custom_fields(
            field_mappings={"state_field": "x"},
            organization_id="org_1",
            ap_item_id="ap-1",
        )
    assert out == {}


def test_get_org_field_mappings_round_trip():
    """The persisted layout reads back per-erp-type."""
    fake_db = MagicMock()
    fake_db.get_organization.return_value = {
        "settings_json": {
            "erp_field_mappings": {
                "netsuite": {"state_field": "custbody_acme_state"},
                "sap": {"state_field": "ZZ_ACME_STATE"},
            }
        }
    }
    with patch("clearledgr.integrations.erp_router._get_db", return_value=fake_db):
        ns = _get_org_field_mappings("org_1", "netsuite")
        sap = _get_org_field_mappings("org_1", "sap")
        qb = _get_org_field_mappings("org_1", "quickbooks")
    assert ns == {"state_field": "custbody_acme_state"}
    assert sap == {"state_field": "ZZ_ACME_STATE"}
    assert qb == {}


# ---------------------------------------------------------------------------
# NetSuite poster
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_netsuite_stamps_custom_fields_at_top_level():
    """Workflow fields land as flat keys on the Vendor Bill body."""
    from clearledgr.integrations.erp_netsuite import post_bill_to_netsuite

    bill = Bill(
        vendor_id="V1",
        vendor_name="Acme",
        amount=500.0,
        invoice_number="INV-1",
        invoice_date="2026-04-29",
    )
    connection = SimpleNamespace(
        account_id="123456",
        consumer_key="ck", consumer_secret="cs",
        token="tk", token_secret="ts",
        subsidiary_id=None,
    )
    captured: dict = {}

    class _Resp:
        status_code = 202
        headers = {"Location": "https://x.suitetalk.api.netsuite.com/services/rest/record/v1/vendorBill/999"}
        text = ""
        def json(self): return {}
        def raise_for_status(self): pass

    async def _fake_post(url, json=None, headers=None, timeout=None):
        captured["body"] = json
        return _Resp()

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    with patch("clearledgr.integrations.erp_netsuite.get_http_client", return_value=fake_client), \
         patch("clearledgr.integrations.erp_netsuite._oauth_header", return_value="OAuth ..."):
        await post_bill_to_netsuite(
            connection, bill,
            custom_fields={
                "custbody_acme_state": "approved",
                "custbody_acme_box": "ap-xyz",
            },
        )
    assert captured["body"]["custbody_acme_state"] == "approved"
    assert captured["body"]["custbody_acme_box"] == "ap-xyz"


@pytest.mark.asyncio
async def test_netsuite_renames_dimension_fields_per_line():
    """field_mappings rewrites department→department_2 on each line."""
    from clearledgr.integrations.erp_netsuite import post_bill_to_netsuite

    bill = Bill(
        vendor_id="V1", vendor_name="Acme", amount=500.0,
        invoice_number="INV-1", invoice_date="2026-04-29",
        line_items=[{
            "amount": 500.0, "description": "Service",
            "department": "10", "class": "5", "location": "3",
        }],
    )
    connection = SimpleNamespace(
        account_id="123456",
        consumer_key="ck", consumer_secret="cs",
        token="tk", token_secret="ts",
        subsidiary_id=None,
    )
    captured: dict = {}

    class _Resp:
        status_code = 202
        headers = {"Location": "https://x/vendorBill/777"}
        text = ""
        def json(self): return {}
        def raise_for_status(self): pass

    async def _fake_post(url, json=None, **kwargs):
        captured["body"] = json
        return _Resp()

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    with patch("clearledgr.integrations.erp_netsuite.get_http_client", return_value=fake_client), \
         patch("clearledgr.integrations.erp_netsuite._oauth_header", return_value="OAuth ..."):
        await post_bill_to_netsuite(
            connection, bill,
            field_mappings={
                "department_field": "department_2",
                "class_field": "class_alt",
            },
        )
    line = captured["body"]["expense"]["items"][0]
    # NetSuite represents dimensions as { "id": ... }. Renamed keys
    # present, original keys gone.
    assert line.get("department_2") == {"id": "10"}
    assert line.get("class_alt") == {"id": "5"}
    assert "department" not in line
    assert "class" not in line
    # Location wasn't renamed in this test, so it stays under default
    assert line.get("location") == {"id": "3"}


# ---------------------------------------------------------------------------
# SAP poster
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sap_stamps_custom_fields_on_document():
    from clearledgr.integrations.erp_sap import post_bill_to_sap

    bill = Bill(
        vendor_id="V1", vendor_name="Acme", amount=500.0,
        invoice_number="INV-1", invoice_date="2026-04-29",
    )
    connection = SimpleNamespace(
        access_token="x", base_url="https://sap.example/b1s/v1",
        company_code="ACME", session_id=None,
    )
    captured: dict = {}

    class _Resp:
        status_code = 201
        text = ""
        def json(self): return {"DocEntry": 42}
        def raise_for_status(self): pass

    async def _fake_post(url, json=None, **kwargs):
        captured["body"] = json
        return _Resp()

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_session(*args, **kwargs):
        return {"status": "success", "headers": {"Cookie": "B1SESSION=x"}}

    with patch("clearledgr.integrations.erp_sap.get_http_client", return_value=fake_client), \
         patch("clearledgr.integrations.erp_sap._open_sap_service_layer_session", side_effect=_fake_session):
        await post_bill_to_sap(
            connection, bill,
            custom_fields={
                "U_ZZ_State": "approved",
                "U_ZZ_BoxId": "ap-sap-1",
            },
        )
    assert captured["body"]["U_ZZ_State"] == "approved"
    assert captured["body"]["U_ZZ_BoxId"] == "ap-sap-1"


@pytest.mark.asyncio
async def test_sap_renames_cost_center_per_line():
    from clearledgr.integrations.erp_sap import post_bill_to_sap

    bill = Bill(
        vendor_id="V1", vendor_name="Acme", amount=500.0,
        invoice_number="INV-1", invoice_date="2026-04-29",
        line_items=[{
            "amount": 500.0, "description": "Service",
            "CostCenter": "CC-100",
        }],
    )
    connection = SimpleNamespace(
        access_token="x", base_url="https://sap.example/b1s/v1",
        company_code="ACME",
    )
    captured: dict = {}

    class _Resp:
        status_code = 201
        text = ""
        def json(self): return {"DocEntry": 1}
        def raise_for_status(self): pass

    async def _fake_post(url, json=None, **kwargs):
        captured["body"] = json
        return _Resp()

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_session(*args, **kwargs):
        return {"status": "success", "headers": {}}

    with patch("clearledgr.integrations.erp_sap.get_http_client", return_value=fake_client), \
         patch("clearledgr.integrations.erp_sap._open_sap_service_layer_session", side_effect=_fake_session):
        await post_bill_to_sap(
            connection, bill,
            field_mappings={"cost_center_field": "U_ZZ_CC"},
        )
    line = captured["body"]["DocumentLines"][0]
    assert line.get("U_ZZ_CC") == "CC-100"
    assert "CostCenter" not in line


# ---------------------------------------------------------------------------
# QuickBooks poster
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quickbooks_stamps_custom_fields_as_array():
    from clearledgr.integrations.erp_quickbooks import post_bill_to_quickbooks

    bill = Bill(
        vendor_id="V1", vendor_name="Acme", amount=500.0,
        invoice_number="INV-1", invoice_date="2026-04-29",
    )
    connection = SimpleNamespace(
        access_token="x", realm_id="realm-1", refresh_token=None,
    )
    captured: dict = {}

    class _Resp:
        status_code = 200
        def json(self): return {"Bill": {"Id": "100"}}
        def raise_for_status(self): pass

    async def _fake_post(url, json=None, **kwargs):
        captured["body"] = json
        return _Resp()

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    with patch("clearledgr.integrations.erp_quickbooks.get_http_client", return_value=fake_client):
        await post_bill_to_quickbooks(
            connection, bill,
            custom_fields={"1": "approved", "2": "ap-qb-1"},
        )
    cf = captured["body"].get("CustomField", [])
    assert {entry["DefinitionId"] for entry in cf} == {"1", "2"}
    assert all(entry["Type"] == "StringType" for entry in cf)


# ---------------------------------------------------------------------------
# Xero poster
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xero_stamps_tracking_categories_per_line():
    from clearledgr.integrations.erp_xero import post_bill_to_xero

    bill = Bill(
        vendor_id="V1", vendor_name="Acme", amount=500.0,
        invoice_number="INV-1", invoice_date="2026-04-29",
        line_items=[{
            "description": "Consulting", "amount": 500.0,
            "tracking_1": "EU", "tracking_2": "Q1",
        }],
    )
    connection = SimpleNamespace(
        access_token="x", tenant_id="t-1", refresh_token=None,
    )
    captured: dict = {}

    class _Resp:
        status_code = 200
        def json(self): return {"Invoices": [{"InvoiceID": "X-1", "InvoiceNumber": "INV-1"}]}
        def raise_for_status(self): pass

    async def _fake_post(url, json=None, **kwargs):
        captured["body"] = json
        return _Resp()

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    with patch("clearledgr.integrations.erp_xero.get_http_client", return_value=fake_client):
        await post_bill_to_xero(
            connection, bill,
            field_mappings={
                "tracking_category_1_field": "Region",
                "tracking_category_2_field": "Quarter",
            },
        )
    line = captured["body"]["Invoices"][0]["LineItems"][0]
    tracking = line.get("Tracking") or []
    assert {t["Name"] for t in tracking} == {"Region", "Quarter"}
    assert {t["Option"] for t in tracking} == {"EU", "Q1"}
    # Upstream tracking_* keys must be stripped from the wire payload —
    # Xero rejects unknown LineItem keys.
    assert "tracking_1" not in line
    assert "tracking_2" not in line


@pytest.mark.asyncio
async def test_xero_appends_workflow_marker_to_reference():
    """Xero has no per-bill custom-field API — workflow markers go in
    the Reference field as a fallback."""
    from clearledgr.integrations.erp_xero import post_bill_to_xero

    bill = Bill(
        vendor_id="V1", vendor_name="Acme", amount=500.0,
        invoice_number="INV-1", invoice_date="2026-04-29",
        po_number="PO-100",
    )
    connection = SimpleNamespace(access_token="x", tenant_id="t-1", refresh_token=None)
    captured: dict = {}

    class _Resp:
        status_code = 200
        def json(self): return {"Invoices": [{"InvoiceID": "X-1", "InvoiceNumber": "INV-1"}]}
        def raise_for_status(self): pass

    async def _fake_post(url, json=None, **kwargs):
        captured["body"] = json
        return _Resp()

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    with patch("clearledgr.integrations.erp_xero.get_http_client", return_value=fake_client):
        await post_bill_to_xero(
            connection, bill,
            custom_fields={"state": "approved", "box_id": "ap-xero-1"},
        )
    ref = captured["body"]["Invoices"][0]["Reference"]
    assert "PO-100" in ref
    assert "clearledgr:" in ref
    assert "state=approved" in ref
    assert "box_id=ap-xero-1" in ref
    assert len(ref) <= 255
