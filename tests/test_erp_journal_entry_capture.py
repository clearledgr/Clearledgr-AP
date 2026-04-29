"""Tests for Wave 1 / A2 — journal entry id capture per ERP.

Coverage per ERP (mocked HTTP):
  * QuickBooks — bill IS the journal record; ``erp_journal_entry_id``
    coincides with bill id by QBO's data model.
  * NetSuite — Vendor Bill IS the source transaction; JE id =
    bill internalid.
  * SAP B1 — PurchaseInvoice creates a SEPARATE OJDT row; JE id =
    response.JournalEntry (distinct from response.DocEntry).
  * Xero — Invoice has a separate Journal entity; follow-up GET
    /Journals?invoiceID=X retrieves JournalID.

Persistence:
  * ``ap_items.erp_journal_entry_id`` column is whitelisted on
    ``update_ap_item`` and survives the AP-store update path.
  * The post-success path in invoice_posting.py threads
    ``result["erp_journal_entry_id"]`` into the state transition.

Soft-fail:
  * A Xero JournalID fetch failure does NOT roll back the post —
    the InvoiceID is still durable; the JE id is filled later by
    the reconciliation pass.
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

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.integrations.erp_router import Bill  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    return inst


def _bill(**overrides):
    """Build a Bill dataclass with sensible defaults for posting."""
    base = dict(
        vendor_id="V1", vendor_name="Acme Corp",
        amount=500.0, currency="USD",
        invoice_number="INV-A2-001",
        invoice_date="2026-04-29",
    )
    base.update(overrides)
    return Bill(**base)


def _qb_connection(**extra):
    base = SimpleNamespace(
        type="quickbooks",
        access_token="tok", realm_id="123",
        refresh_token="rt",
    )
    for k, v in extra.items():
        setattr(base, k, v)
    return base


def _xero_connection():
    return SimpleNamespace(
        type="xero",
        access_token="tok", tenant_id="ten-1", refresh_token="rt",
    )


def _ns_connection():
    return SimpleNamespace(
        type="netsuite",
        account_id="123456",
        consumer_key="ck", consumer_secret="cs",
        token="tk", token_secret="ts",
        subsidiary_id=None,
    )


def _sap_connection():
    return SimpleNamespace(
        type="sap",
        access_token="tk", base_url="https://sap.example/b1s/v1",
        company_code="ACME",
    )


# ─── QuickBooks ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quickbooks_returns_journal_entry_id_equal_to_bill_id():
    from clearledgr.integrations.erp_quickbooks import post_bill_to_quickbooks

    class _Resp:
        status_code = 200
        def json(self):
            return {"Bill": {"Id": "777", "DocNumber": "INV-A2-001", "SyncToken": "0"}}
        def raise_for_status(self): pass

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=_Resp())

    with patch(
        "clearledgr.integrations.erp_quickbooks.get_http_client",
        return_value=fake_client,
    ):
        result = await post_bill_to_quickbooks(
            _qb_connection(), _bill(),
        )

    assert result["status"] == "success", result
    assert result["bill_id"] == "777"
    # Wave 1 / A2 — JE id present and equal to bill id (QBO's data model)
    assert result["erp_journal_entry_id"] == "777"


# ─── NetSuite ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_netsuite_returns_journal_entry_id_equal_to_bill_id():
    from clearledgr.integrations.erp_netsuite import post_bill_to_netsuite

    # Synchronous 200 response with the vendor bill body — NetSuite
    # returns this for tenants without async-prefer headers honoured.
    class _Resp:
        status_code = 200
        headers = {}
        text = ""
        def json(self):
            return {"id": "4242", "internalId": "4242", "tranId": "VB-001"}
        def raise_for_status(self): pass

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=_Resp())

    with patch(
        "clearledgr.integrations.erp_netsuite.get_http_client",
        return_value=fake_client,
    ), patch(
        "clearledgr.integrations.erp_netsuite._oauth_header",
        return_value="OAuth ...",
    ):
        result = await post_bill_to_netsuite(
            _ns_connection(), _bill(),
        )

    assert result["status"] == "success", result
    bill_id = result["bill_id"]
    assert bill_id == "4242"
    # Wave 1 / A2 — JE id present, coincides with bill id
    assert result["erp_journal_entry_id"] == "4242"


# ─── SAP B1 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sap_returns_journal_entry_distinct_from_doc_entry():
    """SAP B1 separates Bill DocEntry from JE DocEntry (OJDT). The
    ``JournalEntry`` field on the create response is the JE-side id."""
    from clearledgr.integrations.erp_sap import post_bill_to_sap

    class _Resp:
        status_code = 201
        text = ""
        def json(self):
            return {
                "DocEntry": 100,        # bill id
                "DocNum": "PI-1234",
                "JournalEntry": 200,    # SEPARATE OJDT entry id
            }
        def raise_for_status(self): pass

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=_Resp())

    async def _fake_session(*args, **kwargs):
        return {"status": "success", "headers": {"Cookie": "B1SESSION=x"}}

    with patch(
        "clearledgr.integrations.erp_sap.get_http_client",
        return_value=fake_client,
    ), patch(
        "clearledgr.integrations.erp_sap._open_sap_service_layer_session",
        side_effect=_fake_session,
    ):
        result = await post_bill_to_sap(_sap_connection(), _bill())

    assert result["status"] == "success"
    assert result["bill_id"] == 100
    # The JE id is DISTINCT from the bill DocEntry — auditor traceability
    # depends on capturing this separately for SAP B1.
    assert result["erp_journal_entry_id"] == "200"
    assert result["bill_id"] != result["erp_journal_entry_id"]


@pytest.mark.asyncio
async def test_sap_handles_journal_entry_replica_field_form():
    """Some B1 versions surface the JE id under
    ``JournalEntryReplica.DocEntry``; the adapter should accept either."""
    from clearledgr.integrations.erp_sap import post_bill_to_sap

    class _Resp:
        status_code = 201
        text = ""
        def json(self):
            return {
                "DocEntry": 50,
                "DocNum": "PI-99",
                "JournalEntryReplica": {"DocEntry": 99},
            }
        def raise_for_status(self): pass

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=_Resp())

    async def _fake_session(*args, **kwargs):
        return {"status": "success", "headers": {}}

    with patch(
        "clearledgr.integrations.erp_sap.get_http_client",
        return_value=fake_client,
    ), patch(
        "clearledgr.integrations.erp_sap._open_sap_service_layer_session",
        side_effect=_fake_session,
    ):
        result = await post_bill_to_sap(_sap_connection(), _bill())

    assert result["erp_journal_entry_id"] == "99"


# ─── Xero ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_xero_does_followup_journal_fetch():
    from clearledgr.integrations.erp_xero import post_bill_to_xero

    class _PostResp:
        status_code = 200
        def json(self):
            return {"Invoices": [{"InvoiceID": "inv-uuid-1", "InvoiceNumber": "INV-A2-001"}]}
        def raise_for_status(self): pass

    class _GetResp:
        status_code = 200
        def json(self):
            return {"Journals": [{"JournalID": "journal-uuid-9", "InvoiceID": "inv-uuid-1"}]}
        def raise_for_status(self): pass

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=_PostResp())
    fake_client.get = AsyncMock(return_value=_GetResp())

    with patch(
        "clearledgr.integrations.erp_xero.get_http_client",
        return_value=fake_client,
    ):
        result = await post_bill_to_xero(_xero_connection(), _bill())

    assert result["status"] == "success"
    assert result["bill_id"] == "inv-uuid-1"
    # Wave 1 / A2 — JE id pulled from the follow-up Journals fetch
    assert result["erp_journal_entry_id"] == "journal-uuid-9"
    # The follow-up GET must have happened
    fake_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_xero_journal_fetch_failure_is_soft():
    """A 500 from /Journals must NOT fail the post — the bill is
    durably created, the JE id back-fills via the recon pass."""
    from clearledgr.integrations.erp_xero import post_bill_to_xero

    class _PostResp:
        status_code = 200
        def json(self):
            return {"Invoices": [{"InvoiceID": "inv-fail-1", "InvoiceNumber": "X-1"}]}
        def raise_for_status(self): pass

    class _GetResp:
        status_code = 500
        def json(self): return {}
        def raise_for_status(self): raise RuntimeError("upstream 500")

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=_PostResp())
    fake_client.get = AsyncMock(return_value=_GetResp())

    with patch(
        "clearledgr.integrations.erp_xero.get_http_client",
        return_value=fake_client,
    ):
        result = await post_bill_to_xero(_xero_connection(), _bill())

    # Post still succeeded, JE id is None (recon will back-fill)
    assert result["status"] == "success"
    assert result["bill_id"] == "inv-fail-1"
    assert result["erp_journal_entry_id"] is None


# ─── Persistence ────────────────────────────────────────────────────


def test_ap_item_carries_erp_journal_entry_id_through_update(db):
    """``erp_journal_entry_id`` is whitelisted on ``update_ap_item``
    and survives the AP-store update path."""
    item = db.create_ap_item({
        "id": "AP-A2-persist-1",
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 250.0,
        "state": "ready_to_post",
    })
    ok = db.update_ap_item(
        item["id"],
        state="posted_to_erp",
        erp_reference="bill-100",
        erp_journal_entry_id="je-200",
    )
    assert ok is True
    fresh = db.get_ap_item(item["id"])
    assert fresh.get("erp_reference") == "bill-100"
    assert fresh.get("erp_journal_entry_id") == "je-200"
    assert fresh.get("state") == "posted_to_erp"


def test_je_id_index_is_queryable(db):
    """The partial index on (organization_id, erp_journal_entry_id)
    should let auditors find an AP item by JE id quickly."""
    item = db.create_ap_item({
        "id": "AP-A2-index-1",
        "organization_id": "default",
        "vendor_name": "Acme",
        "amount": 100.0,
        "state": "ready_to_post",
    })
    db.update_ap_item(
        item["id"],
        state="posted_to_erp",
        erp_journal_entry_id="audit-trace-target",
    )
    # Direct SQL — we don't have a typed lookup helper for JE id yet,
    # but the column + index makes "find me the AP item for JE X" a
    # one-liner the auditor can run.
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM ap_items "
            "WHERE organization_id = %s AND erp_journal_entry_id = %s",
            ("default", "audit-trace-target"),
        )
        row = cur.fetchone()
    assert row is not None
    rd = dict(row) if isinstance(row, dict) else row
    assert (rd.get("id") if isinstance(rd, dict) else rd[0]) == item["id"]
