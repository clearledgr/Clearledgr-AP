from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Dict

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.integrations.erp_router import Bill, ERPConnection
from clearledgr.services import browser_agent as browser_agent_module
from clearledgr.services import erp_api_first as erp_api_first_module


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "erp_api_first.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    db_module._DB_INSTANCE = None
    browser_agent_module._SERVICE = None
    db = db_module.get_db()
    db.initialize()
    return db


def _create_item(db) -> Dict[str, str]:
    return db.create_ap_item(
        {
            "invoice_key": "vendor|api-first|100.00|",
            "thread_id": "thread-api-first",
            "message_id": "msg-api-first",
            "subject": "Invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": "INV-API-FIRST",
            "state": "validated",
            "confidence": 0.95,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "api-first-test",
        }
    )


def _event_types(db, ap_item_id: str) -> list[str]:
    return [str(event.get("event_type") or "") for event in db.list_ap_audit_events(ap_item_id)]


def _default_bill(item: Dict[str, str]) -> Bill:
    return Bill(
        vendor_id="VENDOR-1",
        vendor_name=str(item["vendor_name"]),
        amount=float(item["amount"]),
        currency=str(item["currency"]),
        invoice_number=str(item["invoice_number"]),
    )


def test_post_bill_api_first_success_records_attempt_and_success(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="quickbooks"),
    )

    async def _fake_post_bill(organization_id: str, bill: Bill) -> Dict[str, str]:
        return {
            "status": "success",
            "erp": "quickbooks",
            "bill_id": "QB-1",
            "doc_num": "1001",
        }

    monkeypatch.setattr(erp_api_first_module, "post_bill", _fake_post_bill)

    result = asyncio.run(
        erp_api_first_module.post_bill_api_first(
            organization_id="default",
            bill=_default_bill(item),
            actor_id="tester",
            ap_item_id=str(item["id"]),
            email_id=str(item["message_id"]),
            invoice_number=str(item["invoice_number"]),
            vendor_name=str(item["vendor_name"]),
            amount=float(item["amount"]),
            currency=str(item["currency"]),
            db=db,
        )
    )

    assert result["execution_mode"] == "api"
    assert result["fallback"]["requested"] is False
    assert result["routing"]["primary_mode"] == "api"
    event_types = _event_types(db, str(item["id"]))
    assert "erp_api_attempt" in event_types
    assert "erp_api_success" in event_types
    assert "erp_api_fallback_requested" not in event_types


def test_post_bill_api_first_requests_browser_fallback_on_api_failure(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="xero"),
    )

    async def _fake_post_bill(organization_id: str, bill: Bill) -> Dict[str, str]:
        return {"status": "error", "erp": "xero", "reason": "api_timeout"}

    async def _fake_dispatch_browser_fallback(**kwargs) -> Dict[str, str]:
        return {
            "requested": True,
            "eligible": True,
            "reason": "fallback_dispatched",
            "ap_item_id": str(item["id"]),
            "session_id": "AGS-test-fallback",
            "macro_name": "post_invoice_to_erp",
            "dispatch_status": "dispatched",
            "queued": 5,
            "blocked": 1,
            "denied": 0,
        }

    monkeypatch.setattr(erp_api_first_module, "post_bill", _fake_post_bill)
    monkeypatch.setattr(erp_api_first_module, "_dispatch_browser_fallback", _fake_dispatch_browser_fallback)

    result = asyncio.run(
        erp_api_first_module.post_bill_api_first(
            organization_id="default",
            bill=_default_bill(item),
            actor_id="tester",
            ap_item_id=str(item["id"]),
            email_id=str(item["message_id"]),
            invoice_number=str(item["invoice_number"]),
            vendor_name=str(item["vendor_name"]),
            amount=float(item["amount"]),
            currency=str(item["currency"]),
            db=db,
        )
    )

    assert result["status"] == "pending_browser_fallback"
    assert result["execution_mode"] == "browser_fallback"
    assert result["fallback"]["requested"] is True
    event_types = _event_types(db, str(item["id"]))
    assert "erp_api_attempt" in event_types
    assert "erp_api_fallback_requested" in event_types


def test_post_bill_api_first_fails_safe_when_connector_fallback_disabled(db, monkeypatch):
    item = _create_item(db)
    called = {"post_bill": 0, "fallback": 0}

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="custom_erp"),
    )

    async def _fake_post_bill(organization_id: str, bill: Bill) -> Dict[str, str]:
        called["post_bill"] += 1
        return {"status": "success"}

    async def _fake_dispatch_browser_fallback(**kwargs) -> Dict[str, str]:
        called["fallback"] += 1
        return {"requested": True}

    monkeypatch.setattr(erp_api_first_module, "post_bill", _fake_post_bill)
    monkeypatch.setattr(erp_api_first_module, "_dispatch_browser_fallback", _fake_dispatch_browser_fallback)

    result = asyncio.run(
        erp_api_first_module.post_bill_api_first(
            organization_id="default",
            bill=_default_bill(item),
            actor_id="tester",
            ap_item_id=str(item["id"]),
            email_id=str(item["message_id"]),
            invoice_number=str(item["invoice_number"]),
            vendor_name=str(item["vendor_name"]),
            amount=float(item["amount"]),
            currency=str(item["currency"]),
            db=db,
        )
    )

    assert called["post_bill"] == 0
    assert called["fallback"] == 0
    assert result["execution_mode"] == "api_failed"
    assert result["fallback"]["reason"] == "fallback_disabled_for_connector"
    assert result["routing"]["primary_mode"] == "manual_review"
    event_types = _event_types(db, str(item["id"]))
    assert "erp_api_attempt" in event_types
    assert "erp_api_failed" in event_types

