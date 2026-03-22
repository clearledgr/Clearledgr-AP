from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Dict, List

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


def _move_item_to_failed_post(db, ap_item_id: str) -> None:
    for state in ("needs_approval", "approved", "ready_to_post", "failed_post"):
        assert db.update_ap_item(
            ap_item_id,
            state=state,
            _actor_type="test",
            _actor_id="test-suite",
        )


def test_post_bill_api_first_success_records_attempt_and_success(db, monkeypatch):
    item = _create_item(db)
    db.update_ap_item(item["id"], metadata={"correlation_id": "corr-erp-api-1"})

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="quickbooks"),
    )

    async def _fake_post_bill(organization_id: str, bill: Bill, **kwargs) -> Dict[str, str]:
        return {
            "status": "success",
            "erp": "quickbooks",
            "bill_id": "QB-1",
            "doc_num": "1001",
            "idempotency_key": kwargs.get("idempotency_key"),
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
            correlation_id="corr-erp-api-1",
            db=db,
        )
    )

    assert result["execution_mode"] == "api"
    assert result["idempotency_key"]
    assert result["erp_type"] == "quickbooks"
    assert result["erp_reference"] == "QB-1"
    assert result["error_code"] is None
    assert result["error_message"] is None
    assert result["raw_response_redacted"]["bill_id"] == "QB-1"
    assert result["fallback"]["requested"] is False
    assert result["routing"]["primary_mode"] == "api"
    event_types = _event_types(db, str(item["id"]))
    assert "erp_api_attempt" in event_types
    assert "erp_api_success" in event_types
    assert "erp_api_fallback_requested" not in event_types
    events = db.list_ap_audit_events(str(item["id"]))
    erp_events = [e for e in events if str(e.get("event_type") or "").startswith("erp_api_")]
    assert erp_events
    assert all(e.get("correlation_id") == "corr-erp-api-1" for e in erp_events)


def test_post_bill_api_first_requests_browser_fallback_on_api_failure(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="xero"),
    )

    async def _fake_post_bill(organization_id: str, bill: Bill, **kwargs) -> Dict[str, str]:
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
    assert result["erp_type"] == "xero"
    assert result["erp_reference"] is None
    assert result["error_code"] == "api_timeout"
    assert result["error_message"] == "api_timeout"
    assert result["raw_response_redacted"]["reason"] == "api_timeout"
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

    async def _fake_post_bill(organization_id: str, bill: Bill, **kwargs) -> Dict[str, str]:
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
    assert result["erp_type"] == "custom_erp"
    assert result["error_code"] == "fallback_disabled"
    assert result["error_message"] == "api_not_available_for_connector"
    assert result["raw_response_redacted"]["reason"] == "api_not_available_for_connector"
    assert result["fallback"]["reason"] == "fallback_disabled_for_connector"
    assert result["routing"]["primary_mode"] == "manual_review"
    event_types = _event_types(db, str(item["id"]))
    assert "erp_api_attempt" in event_types
    assert "erp_api_failed" in event_types


def test_post_bill_api_first_propagates_explicit_idempotency_key(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="quickbooks"),
    )

    captured = {}

    async def _fake_post_bill(organization_id: str, bill: Bill, **kwargs) -> Dict[str, str]:
        captured.update(kwargs)
        return {
            "status": "success",
            "erp": "quickbooks",
            "bill_id": "QB-IDEMP-1",
            "doc_num": "2001",
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
            idempotency_key="decision-key-123",
        )
    )

    assert captured["idempotency_key"] == "decision-key-123"
    assert result["idempotency_key"] == "decision-key-123"
    assert result["erp_type"] == "quickbooks"
    assert result["erp_reference"] == "QB-IDEMP-1"
    assert result["error_code"] is None


def test_apply_credit_note_api_first_prefers_quickbooks_api_and_records_success(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="quickbooks"),
    )

    async def _fake_apply_credit_note(
        organization_id: str,
        application,
        **kwargs,
    ) -> Dict[str, str]:
        return {
            "status": "success",
            "erp": "quickbooks",
            "erp_reference": "bp-qb-10",
            "target_erp_reference": application.target_erp_reference,
            "idempotency_key": kwargs.get("idempotency_key"),
        }

    monkeypatch.setattr(erp_api_first_module, "apply_credit_note", _fake_apply_credit_note)

    result = asyncio.run(
        erp_api_first_module.apply_credit_note_api_first(
            organization_id="default",
            target_ap_item_id=str(item["id"]),
            source_ap_item_id="source-credit-qb-10",
            actor_id="tester",
            target_erp_reference="bill-qb-10",
            target_invoice_number=str(item["invoice_number"]),
            credit_note_number="VC-QB-10",
            amount=25.0,
            currency="USD",
            note="Vendor credit",
            email_id=str(item["message_id"]),
            db=db,
        )
    )

    assert result["status"] == "success"
    assert result["execution_mode"] == "api"
    assert result["erp_type"] == "quickbooks"
    assert result["erp_reference"] == "bp-qb-10"
    assert result["fallback"]["requested"] is False
    assert result["routing"]["primary_mode"] == "api"
    event_types = _event_types(db, str(item["id"]))
    assert "erp_credit_application_attempt" in event_types
    assert "erp_credit_application_success" in event_types


def test_apply_settlement_api_first_quickbooks_refund_still_requests_browser_fallback(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="quickbooks"),
    )

    async def _fake_apply_settlement(
        organization_id: str,
        application,
        **kwargs,
    ) -> Dict[str, str]:
        return {
            "status": "error",
            "erp": "quickbooks",
            "reason": "refund_settlement_api_not_available_for_connector",
            "idempotency_key": kwargs.get("idempotency_key"),
        }

    async def _fake_dispatch_browser_follow_on(**kwargs) -> Dict[str, str]:
        return {
            "requested": True,
            "eligible": True,
            "reason": "fallback_dispatched",
            "ap_item_id": str(item["id"]),
            "session_id": "AGS-qb-refund-fallback-1",
            "macro_name": "apply_settlement_in_erp",
            "dispatch_status": "dispatched",
            "queued": 1,
            "blocked": 0,
            "denied": 0,
        }

    monkeypatch.setattr(erp_api_first_module, "apply_settlement", _fake_apply_settlement)
    monkeypatch.setattr(erp_api_first_module, "_dispatch_browser_follow_on", _fake_dispatch_browser_follow_on)

    result = asyncio.run(
        erp_api_first_module.apply_settlement_api_first(
            organization_id="default",
            target_ap_item_id=str(item["id"]),
            source_ap_item_id="source-refund-qb-10",
            actor_id="tester",
            source_document_type="refund",
            target_erp_reference="bill-qb-11",
            target_invoice_number=str(item["invoice_number"]),
            source_reference="REF-QB-10",
            amount=10.0,
            currency="USD",
            note="Refund",
            email_id=str(item["message_id"]),
            db=db,
        )
    )

    assert result["status"] == "pending_browser_fallback"
    assert result["execution_mode"] == "browser_fallback"
    assert result["erp_type"] == "quickbooks"
    assert result["fallback"]["requested"] is True
    assert result["routing"]["primary_mode"] == "api"
    event_types = _event_types(db, str(item["id"]))
    assert "erp_settlement_application_attempt" in event_types
    assert "erp_settlement_application_fallback_requested" in event_types


def test_apply_credit_note_api_first_prefers_xero_api_and_records_success(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="xero"),
    )

    async def _fake_apply_credit_note(
        organization_id: str,
        application,
        **kwargs,
    ) -> Dict[str, str]:
        return {
            "status": "success",
            "erp": "xero",
            "erp_reference": "allocation-xero-10",
            "target_erp_reference": application.target_erp_reference,
            "idempotency_key": kwargs.get("idempotency_key"),
        }

    monkeypatch.setattr(erp_api_first_module, "apply_credit_note", _fake_apply_credit_note)

    result = asyncio.run(
        erp_api_first_module.apply_credit_note_api_first(
            organization_id="default",
            target_ap_item_id=str(item["id"]),
            source_ap_item_id="source-credit-10",
            actor_id="tester",
            target_erp_reference="bill-xero-10",
            target_invoice_number=str(item["invoice_number"]),
            credit_note_number="CN-10",
            amount=25.0,
            currency="USD",
            note="Credit note",
            email_id=str(item["message_id"]),
            db=db,
        )
    )

    assert result["status"] == "success"
    assert result["execution_mode"] == "api"
    assert result["erp_type"] == "xero"
    assert result["erp_reference"] == "allocation-xero-10"
    assert result["fallback"]["requested"] is False
    assert result["routing"]["primary_mode"] == "api"
    event_types = _event_types(db, str(item["id"]))
    assert "erp_credit_application_attempt" in event_types
    assert "erp_credit_application_success" in event_types
    assert "erp_credit_application_fallback_requested" not in event_types


def test_apply_settlement_api_first_refund_still_requests_browser_fallback(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="xero"),
    )

    async def _fake_apply_settlement(
        organization_id: str,
        application,
        **kwargs,
    ) -> Dict[str, str]:
        return {
            "status": "error",
            "erp": "xero",
            "reason": "refund_settlement_api_not_available_for_connector",
            "idempotency_key": kwargs.get("idempotency_key"),
        }

    async def _fake_dispatch_browser_follow_on(**kwargs) -> Dict[str, str]:
        return {
            "requested": True,
            "eligible": True,
            "reason": "fallback_dispatched",
            "ap_item_id": str(item["id"]),
            "session_id": "AGS-refund-fallback-1",
            "macro_name": "apply_settlement_in_erp",
            "dispatch_status": "dispatched",
            "queued": 1,
            "blocked": 0,
            "denied": 0,
        }

    monkeypatch.setattr(erp_api_first_module, "apply_settlement", _fake_apply_settlement)
    monkeypatch.setattr(erp_api_first_module, "_dispatch_browser_follow_on", _fake_dispatch_browser_follow_on)

    result = asyncio.run(
        erp_api_first_module.apply_settlement_api_first(
            organization_id="default",
            target_ap_item_id=str(item["id"]),
            source_ap_item_id="source-refund-10",
            actor_id="tester",
            source_document_type="refund",
            target_erp_reference="bill-xero-11",
            target_invoice_number=str(item["invoice_number"]),
            source_reference="REF-10",
            amount=10.0,
            currency="USD",
            note="Refund",
            email_id=str(item["message_id"]),
            db=db,
        )
    )

    assert result["status"] == "pending_browser_fallback"
    assert result["execution_mode"] == "browser_fallback"
    assert result["erp_type"] == "xero"
    assert result["fallback"]["requested"] is True
    assert result["routing"]["primary_mode"] == "api"
    event_types = _event_types(db, str(item["id"]))
    assert "erp_settlement_application_attempt" in event_types
    assert "erp_settlement_application_fallback_requested" in event_types


def test_apply_credit_note_api_first_prefers_netsuite_api_and_records_success(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="netsuite"),
    )

    async def _fake_apply_credit_note(
        organization_id: str,
        application,
        **kwargs,
    ) -> Dict[str, str]:
        return {
            "status": "success",
            "erp": "netsuite",
            "erp_reference": "credit-ns-10:bill-ns-10",
            "target_erp_reference": application.target_erp_reference,
            "idempotency_key": kwargs.get("idempotency_key"),
        }

    monkeypatch.setattr(erp_api_first_module, "apply_credit_note", _fake_apply_credit_note)

    result = asyncio.run(
        erp_api_first_module.apply_credit_note_api_first(
            organization_id="default",
            target_ap_item_id=str(item["id"]),
            source_ap_item_id="source-credit-ns-10",
            actor_id="tester",
            target_erp_reference="bill-ns-10",
            target_invoice_number=str(item["invoice_number"]),
            credit_note_number="VC-10",
            amount=25.0,
            currency="USD",
            note="Vendor credit",
            email_id=str(item["message_id"]),
            db=db,
        )
    )

    assert result["status"] == "success"
    assert result["execution_mode"] == "api"
    assert result["erp_type"] == "netsuite"
    assert result["erp_reference"] == "credit-ns-10:bill-ns-10"
    assert result["fallback"]["requested"] is False
    assert result["routing"]["primary_mode"] == "api"
    event_types = _event_types(db, str(item["id"]))
    assert "erp_credit_application_attempt" in event_types
    assert "erp_credit_application_success" in event_types


def test_apply_settlement_api_first_netsuite_refund_still_requests_browser_fallback(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="netsuite"),
    )

    async def _fake_apply_settlement(
        organization_id: str,
        application,
        **kwargs,
    ) -> Dict[str, str]:
        return {
            "status": "error",
            "erp": "netsuite",
            "reason": "refund_settlement_api_not_available_for_connector",
            "idempotency_key": kwargs.get("idempotency_key"),
        }

    async def _fake_dispatch_browser_follow_on(**kwargs) -> Dict[str, str]:
        return {
            "requested": True,
            "eligible": True,
            "reason": "fallback_dispatched",
            "ap_item_id": str(item["id"]),
            "session_id": "AGS-netsuite-refund-fallback-1",
            "macro_name": "apply_settlement_in_erp",
            "dispatch_status": "dispatched",
            "queued": 1,
            "blocked": 0,
            "denied": 0,
        }

    monkeypatch.setattr(erp_api_first_module, "apply_settlement", _fake_apply_settlement)
    monkeypatch.setattr(erp_api_first_module, "_dispatch_browser_follow_on", _fake_dispatch_browser_follow_on)

    result = asyncio.run(
        erp_api_first_module.apply_settlement_api_first(
            organization_id="default",
            target_ap_item_id=str(item["id"]),
            source_ap_item_id="source-refund-ns-10",
            actor_id="tester",
            source_document_type="refund",
            target_erp_reference="bill-ns-11",
            target_invoice_number=str(item["invoice_number"]),
            source_reference="REF-10",
            amount=10.0,
            currency="USD",
            note="Refund",
            email_id=str(item["message_id"]),
            db=db,
        )
    )

    assert result["status"] == "pending_browser_fallback"
    assert result["execution_mode"] == "browser_fallback"
    assert result["erp_type"] == "netsuite"
    assert result["fallback"]["requested"] is True
    assert result["routing"]["primary_mode"] == "api"
    event_types = _event_types(db, str(item["id"]))
    assert "erp_settlement_application_attempt" in event_types
    assert "erp_settlement_application_fallback_requested" in event_types


def test_apply_credit_note_api_first_prefers_sap_api_and_records_success(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="sap"),
    )

    async def _fake_apply_credit_note(
        organization_id: str,
        application,
        **kwargs,
    ) -> Dict[str, str]:
        return {
            "status": "success",
            "erp": "sap",
            "erp_reference": "credit-sap-10",
            "target_erp_reference": application.target_erp_reference,
            "idempotency_key": kwargs.get("idempotency_key"),
        }

    monkeypatch.setattr(erp_api_first_module, "apply_credit_note", _fake_apply_credit_note)

    result = asyncio.run(
        erp_api_first_module.apply_credit_note_api_first(
            organization_id="default",
            target_ap_item_id=str(item["id"]),
            source_ap_item_id="source-credit-sap-10",
            actor_id="tester",
            target_erp_reference="123",
            target_invoice_number=str(item["invoice_number"]),
            credit_note_number="CN-SAP-10",
            amount=25.0,
            currency="USD",
            note="SAP credit note",
            email_id=str(item["message_id"]),
            db=db,
        )
    )

    assert result["status"] == "success"
    assert result["execution_mode"] == "api"
    assert result["erp_type"] == "sap"
    assert result["erp_reference"] == "credit-sap-10"
    assert result["fallback"]["requested"] is False
    assert result["routing"]["primary_mode"] == "api"
    event_types = _event_types(db, str(item["id"]))
    assert "erp_credit_application_attempt" in event_types
    assert "erp_credit_application_success" in event_types


def test_apply_settlement_api_first_sap_refund_still_requests_browser_fallback(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="sap"),
    )

    async def _fake_apply_settlement(
        organization_id: str,
        application,
        **kwargs,
    ) -> Dict[str, str]:
        return {
            "status": "error",
            "erp": "sap",
            "reason": "refund_settlement_api_not_available_for_connector",
            "idempotency_key": kwargs.get("idempotency_key"),
        }

    async def _fake_dispatch_browser_follow_on(**kwargs) -> Dict[str, str]:
        return {
            "requested": True,
            "eligible": True,
            "reason": "fallback_dispatched",
            "ap_item_id": str(item["id"]),
            "session_id": "AGS-sap-refund-fallback-1",
            "macro_name": "apply_settlement_in_erp",
            "dispatch_status": "dispatched",
            "queued": 1,
            "blocked": 0,
            "denied": 0,
        }

    monkeypatch.setattr(erp_api_first_module, "apply_settlement", _fake_apply_settlement)
    monkeypatch.setattr(erp_api_first_module, "_dispatch_browser_follow_on", _fake_dispatch_browser_follow_on)

    result = asyncio.run(
        erp_api_first_module.apply_settlement_api_first(
            organization_id="default",
            target_ap_item_id=str(item["id"]),
            source_ap_item_id="source-refund-sap-10",
            actor_id="tester",
            source_document_type="refund",
            target_erp_reference="123",
            target_invoice_number=str(item["invoice_number"]),
            source_reference="REF-SAP-10",
            amount=10.0,
            currency="USD",
            note="SAP refund",
            email_id=str(item["message_id"]),
            db=db,
        )
    )

    assert result["status"] == "pending_browser_fallback"
    assert result["execution_mode"] == "browser_fallback"
    assert result["erp_type"] == "sap"
    assert result["fallback"]["requested"] is True
    assert result["routing"]["primary_mode"] == "api"
    event_types = _event_types(db, str(item["id"]))
    assert "erp_settlement_application_attempt" in event_types
    assert "erp_settlement_application_fallback_requested" in event_types


def test_dispatch_browser_fallback_emits_preview_and_confirmation_audit_sequence(db):
    item = _create_item(db)

    class _FakeBrowserService:
        def __init__(self):
            self.dispatch_calls: List[dict] = []
            self.confirm_calls: List[dict] = []

        def create_session(self, **kwargs):
            return {
                "id": "AGS-preview-1",
                "organization_id": kwargs.get("organization_id"),
                "ap_item_id": kwargs.get("ap_item_id"),
                "state": "running",
            }

        def dispatch_macro(self, **kwargs):
            self.dispatch_calls.append(dict(kwargs))
            if kwargs.get("dry_run"):
                return {
                    "status": "preview",
                    "commands": [
                        {
                            "command": {"command_id": "cmd-open", "tool_name": "open_tab"},
                            "decision": {"requires_confirmation": False},
                        },
                        {
                            "command": {"command_id": "cmd-submit", "tool_name": "click"},
                            "decision": {"requires_confirmation": True},
                        },
                    ],
                }
            return {
                "status": "dispatched",
                "queued": 1,
                "blocked": 1,
                "denied": 0,
                "events": [
                    {"command_id": "cmd-open", "status": "queued"},
                    {"command_id": "cmd-submit", "status": "blocked_for_approval"},
                ],
            }

        def enqueue_command(self, **kwargs):
            self.confirm_calls.append(dict(kwargs))
            return {"command_id": kwargs["command"]["command_id"], "status": "queued"}

    service = _FakeBrowserService()
    result = asyncio.run(
        erp_api_first_module._dispatch_browser_fallback(
            db=db,
            service=service,
            organization_id="default",
            actor_id="approver@example.com",
            ap_item_id=str(item["id"]),
            email_id=str(item["thread_id"]),
            invoice_number=str(item["invoice_number"]),
            vendor_name=str(item["vendor_name"]),
            amount=float(item["amount"]),
            currency=str(item["currency"]),
            vendor_portal_url="https://vendor.example.com/invoice",
            erp_url="https://erp.example.com/bills",
        )
    )

    assert result["requested"] is True
    assert result["reason"] == "fallback_preview_confirmed_and_dispatched"
    assert result["preview"]["status"] == "preview"
    assert result["preview"]["command_count"] == 2
    assert result["preview"]["requires_confirmation_count"] == 1
    assert result["confirmation"]["required_count"] == 1
    assert result["confirmation"]["confirmed_count"] == 1
    assert result["blocked"] == 0
    assert result["queued"] == 2

    assert len(service.dispatch_calls) == 2
    assert service.dispatch_calls[0]["dry_run"] is True
    assert service.dispatch_calls[1]["dry_run"] is False
    assert len(service.confirm_calls) == 1
    assert service.confirm_calls[0]["confirm"] is True
    assert service.confirm_calls[0]["confirmed_by"] == "approver@example.com"
    assert service.confirm_calls[0]["command"]["command_id"] == "cmd-submit"

    event_types = _event_types(db, str(item["id"]))
    assert "erp_api_fallback_preview_created" in event_types
    assert "erp_api_fallback_confirmation_captured" in event_types


def test_post_bill_api_first_blocks_when_rollout_control_disables_erp_posting(db, monkeypatch):
    item = _create_item(db)
    db.ensure_organization("default", organization_name="default")
    db.update_organization(
        "default",
        settings={
            "rollback_controls": {
                "erp_posting_disabled": True,
                "reason": "erp_posting_paused_for_incident",
            }
        },
    )

    called = {"post_bill": 0}

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="quickbooks"),
    )

    async def _fake_post_bill(organization_id: str, bill: Bill, **kwargs) -> Dict[str, str]:
        called["post_bill"] += 1
        return {"status": "success", "erp": "quickbooks"}

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

    assert called["post_bill"] == 0
    assert result["status"] == "blocked"
    assert result["execution_mode"] == "blocked"
    assert result["reason"] == "erp_posting_paused_for_incident"
    assert result["erp_type"] == "quickbooks"
    assert result["error_code"] == "posting_blocked"
    assert result["error_message"] == "erp_posting_paused_for_incident"
    assert result["raw_response_redacted"]["reason"] == "erp_posting_paused_for_incident"
    assert result["fallback"]["reason"] == "erp_posting_disabled_by_rollout_control"

    event_types = _event_types(db, str(item["id"]))
    assert "erp_api_blocked" in event_types


def test_reconcile_browser_fallback_completion_updates_ap_item_and_is_idempotent(db):
    item = _create_item(db)
    _move_item_to_failed_post(db, str(item["id"]))
    session = db.create_agent_session(
        {
            "organization_id": "default",
            "ap_item_id": str(item["id"]),
            "created_by": "runner-service",
            "metadata": {"workflow_id": "erp_posting_fallback"},
        }
    )

    first = erp_api_first_module.reconcile_browser_fallback_completion(
        session_id=str(session["id"]),
        macro_name="post_invoice_to_erp",
        status="completed",
        actor_id="runner-service",
        erp_reference="ERP-FINAL-1",
        evidence={"screenshot_hash": "abc123"},
        idempotency_key="fallback-finalize-1",
        correlation_id="corr-finalize-1",
        db=db,
    )
    assert first["status"] == "success"
    assert first["duplicate"] is False
    assert first["ap_item_state"] == "posted_to_erp"

    updated = db.get_ap_item(str(item["id"]))
    assert updated["state"] == "posted_to_erp"
    assert updated["erp_reference"] == "ERP-FINAL-1"

    second = erp_api_first_module.reconcile_browser_fallback_completion(
        session_id=str(session["id"]),
        macro_name="post_invoice_to_erp",
        status="success",
        actor_id="runner-service",
        erp_reference="ERP-FINAL-1",
        idempotency_key="fallback-finalize-1",
        db=db,
    )
    assert second["duplicate"] is True

    event_types = _event_types(db, str(item["id"]))
    assert "erp_browser_fallback_completed" in event_types


def test_post_bill_api_first_treats_already_posted_as_successful_idempotent_result(db, monkeypatch):
    item = _create_item(db)

    monkeypatch.setattr(
        erp_api_first_module,
        "get_erp_connection",
        lambda organization_id: ERPConnection(type="quickbooks"),
    )

    async def _fake_post_bill(organization_id: str, bill: Bill, **kwargs) -> Dict[str, str]:
        return {
            "status": "already_posted",
            "erp": "quickbooks",
            "reference_id": "QB-ALREADY-1",
            "idempotency_key": kwargs.get("idempotency_key"),
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
            idempotency_key="already-posted-key",
        )
    )

    assert result["status"] == "already_posted"
    assert result["execution_mode"] == "api"
    assert result["erp_type"] == "quickbooks"
    assert result["erp_reference"] == "QB-ALREADY-1"
    assert result["error_code"] is None
    assert result["idempotency_key"] == "already-posted-key"
    assert result["fallback"]["requested"] is False
