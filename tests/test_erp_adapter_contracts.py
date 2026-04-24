from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import sys
from pathlib import Path

from clearledgr.integrations.erp_router import Bill
from clearledgr.integrations.erp_router import ERPConnection, set_erp_connection
from clearledgr.services.erp.contracts import get_erp_bill_adapter
from clearledgr.core import database as db_module
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db = db_module.get_db()
    db.initialize()
    return db


def _bill() -> Bill:
    return Bill(
        vendor_id="V-1",
        vendor_name="Acme Supplies",
        amount=842.19,
        currency="USD",
        invoice_number="INV-1001",
    )


def test_router_backed_adapter_validate_requires_required_fields():
    adapter = get_erp_bill_adapter(erp_type="netsuite", post_handler=lambda *args, **kwargs: None)
    result = adapter.validate(
        {
            "invoice_number": "",
            "vendor_name": "Acme",
            "amount": 100.0,
            "currency": "USD",
        }
    )
    assert result["ok"] is False
    assert "invoice_number" in result["missing_fields"]
    assert result["erp_type"] == "netsuite"


def test_router_backed_adapter_post_delegates_to_handler():
    calls = {}

    async def _fake_post(organization_id: str, bill: Bill, **kwargs):
        calls["organization_id"] = organization_id
        calls["invoice_number"] = bill.invoice_number
        calls["kwargs"] = dict(kwargs)
        return {"status": "success", "erp": "xero", "bill_id": "X-1"}

    adapter = get_erp_bill_adapter(erp_type="xero", post_handler=_fake_post)
    result = asyncio.run(
        adapter.post(
            "default",
            _bill(),
            ap_item_id="ap-1",
            idempotency_key="idem-erp-adapter-1",
        )
    )

    assert result["status"] == "success"
    assert calls["organization_id"] == "default"
    assert calls["invoice_number"] == "INV-1001"
    assert calls["kwargs"]["ap_item_id"] == "ap-1"
    assert calls["kwargs"]["idempotency_key"] == "idem-erp-adapter-1"


def test_router_backed_adapter_status_and_reconcile_unconfigured_shape():
    async def _fake_post(_organization_id: str, _bill: Bill, **_kwargs):
        return {"status": "success"}

    adapter = get_erp_bill_adapter(erp_type="sap", post_handler=_fake_post)
    status = asyncio.run(adapter.get_status("default", "ERP-123"))
    reconcile = asyncio.run(adapter.reconcile("default", "ap-1"))

    assert status["status"] == "unconfigured"
    assert status["erp_type"] == "sap"
    assert status["connected"] is False
    assert status["external_ref"] == "ERP-123"
    assert reconcile["status"] == "unconfigured"
    assert reconcile["erp_type"] == "sap"
    assert reconcile["entity_id"] == "ap-1"
    assert reconcile["reconciled"] is False


def test_router_backed_adapter_status_and_reconcile_for_posted_item(db):
    async def _fake_post(_organization_id: str, _bill: Bill, **_kwargs):
        return {"status": "success"}

    set_erp_connection(
        "default",
        ERPConnection(
            type="netsuite",
            account_id="12345",
            consumer_key="ck",
            consumer_secret="cs",
            token_id="tk",
            token_secret="ts",
        ),
    )

    created = db.create_ap_item(
        {
            "invoice_key": "adapter|status|100",
            "thread_id": "thread-adapter-status",
            "message_id": "msg-adapter-status",
            "subject": "Invoice",
            "sender": "billing@example.com",
            "vendor_name": "Acme Supplies",
            "amount": 842.19,
            "currency": "USD",
            "invoice_number": "INV-1001",
            "state": "posted_to_erp",
            "organization_id": "default",
            "erp_reference": "ERP-123",
            "erp_posted_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    adapter = get_erp_bill_adapter(erp_type="netsuite", post_handler=_fake_post)
    status = asyncio.run(adapter.get_status("default", "ERP-123"))
    reconcile = asyncio.run(adapter.reconcile("default", created["id"]))

    assert status["status"] == "posted"
    assert status["connected"] is True
    assert status["ap_item_id"] == created["id"]
    assert status["erp_reference"] == "ERP-123"
    assert reconcile["status"] == "reconciled"
    assert reconcile["reconciled"] is True
    assert reconcile["erp_reference"] == "ERP-123"
