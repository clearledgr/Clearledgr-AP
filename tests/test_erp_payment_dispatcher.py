"""Tests for Wave 2 / C3 — ERP payment webhook dispatcher.

Each ERP gets its own surface; the tests focus on:

  * QuickBooks
      - Envelope parser pulls BillPayment ids out of the
        ``eventNotifications`` envelope.
      - End-to-end dispatch: parser → fetch → record_payment_confirmation
        with a patched httpx layer.
      - A BillPayment whose PrivateNote contains "Voided" is recorded
        with status=failed.
      - Multiple linked bills on one BillPayment yield one
        confirmation per bill.
  * Xero
      - Envelope parser keeps INVOICE category, drops others.
      - Dispatch follows up with the existing ``get_payment_status_xero``;
        a paid invoice yields a confirmed event.
  * NetSuite
      - Sync parser converts ``vendor_payments`` arrays into one
        event per linked bill.
      - ``Voided`` status flips status=failed.
  * Orphan handling
      - A payment for a bill we have no AP item for emits an audit
        event with key payment_orphan:{org}:{source}:{payment_id}.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.services import erp_payment_dispatcher as dispatcher  # noqa: E402
from clearledgr.services.erp_payment_dispatcher import (  # noqa: E402
    ParsedPaymentEvent,
    dispatch_netsuite_payment_webhook,
    dispatch_quickbooks_payment_webhook,
    dispatch_xero_payment_webhook,
    parse_netsuite_payment_payload,
    _parse_quickbooks_envelope,
    _parse_xero_envelope,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    return inst


def _make_awaiting_ap_item(
    db, *, item_id: str, erp_reference: str, org: str = "default",
) -> dict:
    db.ensure_organization(org, organization_name=org)
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Acme",
        "amount": 1500.0,
        "state": "received",
        "erp_reference": erp_reference,
    })
    for s in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp", "awaiting_payment",
    ):
        db.update_ap_item(item["id"], state=s)
    return db.get_ap_item(item["id"])


# ─── QuickBooks parser ──────────────────────────────────────────────


def test_qb_envelope_parses_billpayment_create():
    body = (
        b'{"eventNotifications": [{"realmId": "1", "dataChangeEvent": '
        b'{"entities": [{"name": "BillPayment", "id": "42", '
        b'"operation": "Create"}]}}]}'
    )
    out = _parse_quickbooks_envelope(body)
    assert out == [{"name": "BillPayment", "id": "42", "operation": "Create"}]


def test_qb_envelope_ignores_unrelated_entities():
    body = (
        b'{"eventNotifications": [{"dataChangeEvent": {"entities": ['
        b'{"name": "Customer", "id": "9", "operation": "Update"}, '
        b'{"name": "BillPayment", "id": "42", "operation": "Create"}'
        b']}}]}'
    )
    parsed = _parse_quickbooks_envelope(body)
    # Both entities are returned; the dispatcher filters by name later.
    names = {e["name"] for e in parsed}
    assert "BillPayment" in names
    assert "Customer" in names


def test_qb_envelope_handles_malformed_body():
    assert _parse_quickbooks_envelope(b"") == []
    assert _parse_quickbooks_envelope(b"not-json") == []
    assert _parse_quickbooks_envelope(b'{"foo": []}') == []


@pytest.mark.asyncio
async def test_qb_dispatch_records_confirmation_end_to_end(db):
    item = _make_awaiting_ap_item(
        db, item_id="AP-qb-disp-1", erp_reference="QB-BILL-100",
    )
    body = (
        b'{"eventNotifications": [{"dataChangeEvent": {"entities": ['
        b'{"name": "BillPayment", "id": "BP-77", "operation": "Create"}'
        b']}}]}'
    )

    fake_connection = type("C", (), {
        "type": "quickbooks",
        "access_token": "tok",
        "realm_id": "1",
    })()
    fake_fetch = AsyncMock(return_value={
        "status": "success",
        "bill_payment_id": "BP-77",
        "txn_date": "2026-04-29",
        "total_amount": 1500.0,
        "currency": "EUR",
        "private_note": None,
        "pay_type": "Check",
        "linked_bills": [{"bill_id": "QB-BILL-100", "amount": 1500.0}],
        "voided": False,
    })

    with patch.object(
        dispatcher, "_fetch_qb_bill_payment_to_events",
        wraps=dispatcher._fetch_qb_bill_payment_to_events,
    ), patch(
        "clearledgr.services.erp_payment_dispatcher.get_erp_connection",
        return_value=fake_connection,
        create=True,
    ) if False else patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=fake_connection,
    ), patch(
        "clearledgr.integrations.erp_quickbooks.fetch_quickbooks_bill_payment",
        new=fake_fetch,
    ):
        result = await dispatch_quickbooks_payment_webhook(
            organization_id="default", raw_body=body, db=db,
        )

    assert result["events_parsed"] == 1
    assert result["events_dispatched"] == 1
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "payment_executed"

    rows = db.list_payment_confirmations_for_ap_item("default", item["id"])
    assert len(rows) == 1
    assert rows[0]["payment_id"] == "BP-77"
    assert rows[0]["source"] == "quickbooks"


@pytest.mark.asyncio
async def test_qb_dispatch_voided_billpayment_marks_failed(db):
    item = _make_awaiting_ap_item(
        db, item_id="AP-qb-disp-void", erp_reference="QB-BILL-V",
    )
    body = (
        b'{"eventNotifications": [{"dataChangeEvent": {"entities": ['
        b'{"name": "BillPayment", "id": "BP-V", "operation": "Update"}'
        b']}}]}'
    )
    fake_connection = type("C", (), {
        "type": "quickbooks", "access_token": "t", "realm_id": "1",
    })()
    fake_fetch = AsyncMock(return_value={
        "status": "success",
        "bill_payment_id": "BP-V",
        "txn_date": "2026-04-29",
        "total_amount": 1500.0,
        "currency": "EUR",
        "private_note": "Voided",
        "pay_type": "Check",
        "linked_bills": [{"bill_id": "QB-BILL-V", "amount": 1500.0}],
        "voided": True,
    })

    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=fake_connection,
    ), patch(
        "clearledgr.integrations.erp_quickbooks.fetch_quickbooks_bill_payment",
        new=fake_fetch,
    ):
        result = await dispatch_quickbooks_payment_webhook(
            organization_id="default", raw_body=body, db=db,
        )

    assert result["events_dispatched"] == 1
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "payment_failed"
    rows = db.list_payment_confirmations_for_ap_item("default", item["id"])
    assert rows[0]["status"] == "failed"
    assert rows[0]["failure_reason"] == "voided"


@pytest.mark.asyncio
async def test_qb_dispatch_multi_linked_bills_emits_one_event_each(db):
    a = _make_awaiting_ap_item(db, item_id="AP-qb-A", erp_reference="QB-A")
    b = _make_awaiting_ap_item(db, item_id="AP-qb-B", erp_reference="QB-B")
    body = (
        b'{"eventNotifications": [{"dataChangeEvent": {"entities": ['
        b'{"name": "BillPayment", "id": "BP-MULTI", "operation": "Create"}'
        b']}}]}'
    )
    fake_connection = type("C", (), {
        "type": "quickbooks", "access_token": "t", "realm_id": "1",
    })()
    fake_fetch = AsyncMock(return_value={
        "status": "success",
        "bill_payment_id": "BP-MULTI",
        "txn_date": "2026-04-29",
        "total_amount": 3000.0,
        "currency": "EUR",
        "pay_type": "Check",
        "linked_bills": [
            {"bill_id": "QB-A", "amount": 1500.0},
            {"bill_id": "QB-B", "amount": 1500.0},
        ],
        "voided": False,
    })
    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=fake_connection,
    ), patch(
        "clearledgr.integrations.erp_quickbooks.fetch_quickbooks_bill_payment",
        new=fake_fetch,
    ):
        result = await dispatch_quickbooks_payment_webhook(
            organization_id="default", raw_body=body, db=db,
        )
    assert result["events_dispatched"] == 2
    assert db.get_ap_item(a["id"])["state"] == "payment_executed"
    assert db.get_ap_item(b["id"])["state"] == "payment_executed"


@pytest.mark.asyncio
async def test_qb_dispatch_redelivery_idempotent(db):
    item = _make_awaiting_ap_item(
        db, item_id="AP-qb-idem", erp_reference="QB-IDEM",
    )
    body = (
        b'{"eventNotifications": [{"dataChangeEvent": {"entities": ['
        b'{"name": "BillPayment", "id": "BP-IDEM", "operation": "Create"}'
        b']}}]}'
    )
    fake_connection = type("C", (), {
        "type": "quickbooks", "access_token": "t", "realm_id": "1",
    })()
    fake_fetch = AsyncMock(return_value={
        "status": "success",
        "bill_payment_id": "BP-IDEM",
        "txn_date": "2026-04-29",
        "total_amount": 1500.0,
        "currency": "EUR",
        "pay_type": "Check",
        "linked_bills": [{"bill_id": "QB-IDEM", "amount": 1500.0}],
        "voided": False,
    })
    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=fake_connection,
    ), patch(
        "clearledgr.integrations.erp_quickbooks.fetch_quickbooks_bill_payment",
        new=fake_fetch,
    ):
        first = await dispatch_quickbooks_payment_webhook(
            organization_id="default", raw_body=body, db=db,
        )
        second = await dispatch_quickbooks_payment_webhook(
            organization_id="default", raw_body=body, db=db,
        )
    assert first["events_dispatched"] == 1
    assert second["duplicates"] == 1
    assert second["events_dispatched"] == 0
    rows = db.list_payment_confirmations_for_ap_item("default", item["id"])
    assert len(rows) == 1


# ─── Xero parser + dispatch ─────────────────────────────────────────


def test_xero_envelope_keeps_invoice_drops_others():
    body = (
        b'{"events": ['
        b'{"resourceId": "inv-1", "eventCategory": "INVOICE", "eventType": "UPDATE"},'
        b'{"resourceId": "ct-1", "eventCategory": "CONTACT", "eventType": "UPDATE"}'
        b']}'
    )
    out = _parse_xero_envelope(body)
    assert len(out) == 1
    assert out[0]["resource_id"] == "inv-1"


def test_xero_envelope_drops_non_update_events():
    body = (
        b'{"events": ['
        b'{"resourceId": "inv-1", "eventCategory": "INVOICE", "eventType": "DELETE"}'
        b']}'
    )
    out = _parse_xero_envelope(body)
    assert out == []


def test_xero_envelope_handles_intent_to_receive():
    """Xero's first-call handshake has events: []."""
    assert _parse_xero_envelope(b'{"events": []}') == []


@pytest.mark.asyncio
async def test_xero_dispatch_records_paid_invoice(db):
    item = _make_awaiting_ap_item(
        db, item_id="AP-xr-disp-1", erp_reference="xero-inv-paid",
    )
    body = (
        b'{"events": [{"resourceId": "xero-inv-paid", '
        b'"eventCategory": "INVOICE", "eventType": "UPDATE"}]}'
    )
    fake_connection = type("C", (), {
        "type": "xero", "access_token": "t", "tenant_id": "tnt",
    })()
    fake_status = AsyncMock(return_value={
        "paid": True,
        "payment_amount": 1500.0,
        "payment_date": "2026-04-29",
        "payment_method": "",
        "payment_reference": "xero-pmt-77",
        "partial": False,
        "remaining_balance": 0.0,
    })
    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=fake_connection,
    ), patch(
        "clearledgr.integrations.erp_xero.get_payment_status_xero",
        new=fake_status,
    ):
        result = await dispatch_xero_payment_webhook(
            organization_id="default", raw_body=body, db=db,
        )
    assert result["events_dispatched"] == 1
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "payment_executed"
    rows = db.list_payment_confirmations_for_ap_item("default", item["id"])
    assert rows[0]["source"] == "xero"
    assert rows[0]["payment_id"] == "xero-pmt-77"


@pytest.mark.asyncio
async def test_xero_dispatch_unpaid_invoice_no_event(db):
    _make_awaiting_ap_item(
        db, item_id="AP-xr-unpaid", erp_reference="xero-inv-unpaid",
    )
    body = (
        b'{"events": [{"resourceId": "xero-inv-unpaid", '
        b'"eventCategory": "INVOICE", "eventType": "UPDATE"}]}'
    )
    fake_connection = type("C", (), {
        "type": "xero", "access_token": "t", "tenant_id": "tnt",
    })()
    fake_status = AsyncMock(return_value={"paid": False, "reason": "unpaid"})
    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=fake_connection,
    ), patch(
        "clearledgr.integrations.erp_xero.get_payment_status_xero",
        new=fake_status,
    ):
        result = await dispatch_xero_payment_webhook(
            organization_id="default", raw_body=body, db=db,
        )
    assert result["events_dispatched"] == 0
    rows = db.list_payment_confirmations("default")
    assert rows == []


@pytest.mark.asyncio
async def test_xero_dispatch_voided_payment_records_failure(db):
    item = _make_awaiting_ap_item(
        db, item_id="AP-xr-void", erp_reference="xero-inv-void",
    )
    body = (
        b'{"events": [{"resourceId": "xero-inv-void", '
        b'"eventCategory": "INVOICE", "eventType": "UPDATE"}]}'
    )
    fake_connection = type("C", (), {
        "type": "xero", "access_token": "t", "tenant_id": "tnt",
    })()
    fake_status = AsyncMock(return_value={
        "paid": False,
        "payment_failed": True,
        "reason": "payment_voided_or_deleted",
    })
    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=fake_connection,
    ), patch(
        "clearledgr.integrations.erp_xero.get_payment_status_xero",
        new=fake_status,
    ):
        result = await dispatch_xero_payment_webhook(
            organization_id="default", raw_body=body, db=db,
        )
    assert result["events_dispatched"] == 1
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "payment_failed"


# ─── NetSuite parser + dispatch ─────────────────────────────────────


def test_netsuite_payload_parser_basic():
    body = (
        b'{"vendor_payments": [{'
        b'"payment_id": "VP-1", "transaction_date": "2026-04-29",'
        b' "amount": 1500.00, "currency_code": "USD",'
        b' "payment_method": "ACH",'
        b' "bill_internal_ids": ["NS-100"],'
        b' "status": "Paid In Full"}]}'
    )
    out = parse_netsuite_payment_payload(body)
    assert len(out) == 1
    evt = out[0]
    assert evt.payment_id == "VP-1"
    assert evt.erp_bill_reference == "NS-100"
    assert evt.status == "confirmed"
    assert evt.amount == 1500.0


def test_netsuite_payload_parser_voided_status():
    body = (
        b'{"vendor_payments": [{'
        b'"payment_id": "VP-V", "bill_internal_ids": ["NS-99"],'
        b' "status": "Voided"}]}'
    )
    out = parse_netsuite_payment_payload(body)
    assert out[0].status == "failed"
    assert "void" in out[0].failure_reason.lower()


def test_netsuite_payload_parser_one_event_per_linked_bill():
    body = (
        b'{"vendor_payments": [{'
        b'"payment_id": "VP-MULTI",'
        b' "bill_internal_ids": ["NS-A", "NS-B"],'
        b' "status": "Paid In Full"}]}'
    )
    out = parse_netsuite_payment_payload(body)
    assert {e.erp_bill_reference for e in out} == {"NS-A", "NS-B"}


def test_netsuite_payload_parser_skips_intake_only_payloads():
    """A NetSuite intake-only payload (vendor bill, not vendor_payments)
    should yield zero events without raising."""
    body = b'{"vendor_bill": {"id": "NS-1", "vendor": "Acme"}}'
    assert parse_netsuite_payment_payload(body) == []


def test_netsuite_dispatch_end_to_end(db):
    item = _make_awaiting_ap_item(
        db, item_id="AP-ns-disp-1", erp_reference="NS-100",
    )
    body = (
        b'{"vendor_payments": [{'
        b'"payment_id": "VP-1", "transaction_date": "2026-04-29",'
        b' "amount": 1500.00, "currency_code": "USD",'
        b' "payment_method": "ACH",'
        b' "bill_internal_ids": ["NS-100"],'
        b' "status": "Paid In Full"}]}'
    )
    result = dispatch_netsuite_payment_webhook(
        organization_id="default", raw_body=body, db=db,
    )
    assert result["events_dispatched"] == 1
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "payment_executed"


def test_netsuite_dispatch_redelivery_idempotent(db):
    _make_awaiting_ap_item(db, item_id="AP-ns-idem", erp_reference="NS-IDEM")
    body = (
        b'{"vendor_payments": [{'
        b'"payment_id": "VP-IDEM",'
        b' "bill_internal_ids": ["NS-IDEM"],'
        b' "status": "Paid In Full"}]}'
    )
    first = dispatch_netsuite_payment_webhook(
        organization_id="default", raw_body=body, db=db,
    )
    second = dispatch_netsuite_payment_webhook(
        organization_id="default", raw_body=body, db=db,
    )
    assert first["events_dispatched"] == 1
    assert second["duplicates"] == 1


# ─── Orphan handling ────────────────────────────────────────────────


def test_dispatcher_orphan_emits_audit(db):
    """A payment for a bill we don't have an AP item for emits an
    audit event so the operator can investigate, but does NOT
    insert a payment_confirmations row (no AP item to attach it to)."""
    body = (
        b'{"vendor_payments": [{'
        b'"payment_id": "VP-ORPHAN", "bill_internal_ids": ["NS-MISSING"],'
        b' "status": "Paid In Full"}]}'
    )
    result = dispatch_netsuite_payment_webhook(
        organization_id="default", raw_body=body, db=db,
    )
    assert result["events_skipped"] == 1
    expected_key = (
        "payment_orphan:default:netsuite:VP-ORPHAN"
    )
    fetched = db.get_ap_audit_event_by_key(expected_key)
    assert fetched is not None
    assert fetched["event_type"] == "payment_confirmation_orphan"
