"""Tests for the S/4HANA payment carry-over.

Covers:
  * is_sap_s4hana_connection — heuristic matches base_url shape.
  * get_payment_status_sap_s4hana — IsCleared / IsCancelled paths,
    invalid composite key, OData v2 vs v4 envelope shapes.
  * poll_sap_b1_payments routes to S/4HANA when the connection
    isn't B1-shaped.
  * poll_sap_s4hana_payments — walks awaiting_payment items, calls
    record_payment_confirmation through _dispatch_one when cleared.
  * S/4HANA intake adapter no longer auto-closes on 'paid'
    (defers to the payment dispatcher).
  * CPI CloudEvents parser: cleared / paid / cancelled events
    emit ParsedPaymentEvent records; non-payment events skipped.
  * dispatch_sap_s4hana_payment_webhook end-to-end against an AP
    item — payment_confirmations row created, AP item walks to
    payment_executed.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.integrations.erp_sap import (  # noqa: E402
    is_sap_s4hana_connection,
)
from clearledgr.integrations.erp_sap_s4hana_intake_adapter import (  # noqa: E402
    SapS4HanaIntakeAdapter,
)
from clearledgr.services import erp_payment_dispatcher as dispatcher  # noqa: E402
from clearledgr.services.erp_payment_dispatcher import (  # noqa: E402
    _parse_sap_s4hana_payment_envelope,
    dispatch_sap_s4hana_payment_webhook,
    poll_sap_b1_payments,
    poll_sap_s4hana_payments,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme")
    return inst


def _conn(*, base_url: str, access_token: str = "tok") -> SimpleNamespace:
    return SimpleNamespace(
        type="sap",
        access_token=access_token,
        base_url=base_url,
        company_code="1000",
        tenant_id=None,
        realm_id=None,
        webhook_secret=None,
    )


def _make_awaiting_ap_item(
    db, *, item_id: str, erp_reference: str, org: str = "orgA",
) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Vendor X",
        "amount": 1190.0,
        "currency": "EUR",
        "state": "received",
        "erp_reference": erp_reference,
    })
    for s in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp", "awaiting_payment",
    ):
        db.update_ap_item(item["id"], state=s)
    return db.get_ap_item(item["id"])


# ─── Connection-shape heuristic ───────────────────────────────────


def test_is_s4hana_detects_b1_url():
    b1 = _conn(base_url="https://sap-b1.example.com:50000/b1s/v1")
    assert is_sap_s4hana_connection(b1) is False


def test_is_s4hana_detects_s4_url():
    s4 = _conn(base_url="https://my-s4hana.api.sap")
    assert is_sap_s4hana_connection(s4) is True


def test_is_s4hana_handles_empty_base_url():
    blank = _conn(base_url="")
    # No URL -> default to S/4HANA path (modern default).
    assert is_sap_s4hana_connection(blank) is True


# ─── S/4HANA payment status fetch ────────────────────────────────


def test_get_payment_status_s4hana_invalid_key():
    from clearledgr.integrations.erp_sap import (
        get_payment_status_sap_s4hana,
    )
    import asyncio

    conn = _conn(base_url="https://s4.example.com")
    result = asyncio.run(get_payment_status_sap_s4hana(
        conn, "not-a-composite-key",
    ))
    assert result["paid"] is False
    assert "invalid_invoice_key" in result["error"]


def test_get_payment_status_s4hana_cleared():
    from clearledgr.integrations.erp_sap import (
        get_payment_status_sap_s4hana,
    )
    import asyncio

    conn = _conn(base_url="https://s4.example.com")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "d": {
                    "CompanyCode": "1000",
                    "SupplierInvoice": "5105600000",
                    "FiscalYear": "2026",
                    "IsCleared": "true",
                    "IsCancelled": "false",
                    "InvoiceGrossAmount": 1190.00,
                    "DocumentCurrency": "EUR",
                    "ClearingDocument": "1500001234",
                    "ClearingDate": "2026-04-29",
                }
            }

    async def fake_get(url, headers=None, timeout=None):
        return FakeResponse()

    fake_client = SimpleNamespace(get=fake_get)
    with patch(
        "clearledgr.integrations.erp_sap.get_http_client",
        return_value=fake_client,
    ):
        result = asyncio.run(get_payment_status_sap_s4hana(
            conn, "1000/5105600000/2026",
        ))
    assert result["paid"] is True
    assert result["payment_amount"] == 1190.00
    assert result["payment_reference"] == "1500001234"
    assert result["currency"] == "EUR"


def test_get_payment_status_s4hana_cancelled():
    from clearledgr.integrations.erp_sap import (
        get_payment_status_sap_s4hana,
    )
    import asyncio

    conn = _conn(base_url="https://s4.example.com")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "IsCleared": "false",
                "IsCancelled": "true",
                "InvoiceGrossAmount": 0,
            }

    async def fake_get(url, headers=None, timeout=None):
        return FakeResponse()

    with patch(
        "clearledgr.integrations.erp_sap.get_http_client",
        return_value=SimpleNamespace(get=fake_get),
    ):
        result = asyncio.run(get_payment_status_sap_s4hana(
            conn, "1000/5105600000/2026",
        ))
    assert result["paid"] is False
    assert result.get("payment_failed") is True
    assert result["reason"] == "invoice_cancelled"


# ─── Polling routing ──────────────────────────────────────────────


def test_b1_poller_routes_to_s4hana_when_url_not_b1(db):
    """poll_sap_b1_payments should auto-route to S/4HANA when the
    connection's base_url isn't B1-shaped — covering the user's
    "what about S/4HANA" gap."""
    import asyncio

    s4_conn = _conn(base_url="https://my-s4.api.sap")
    captured = {"called": False}

    async def fake_s4_poll(organization_id, db=None, limit=50):
        captured["called"] = True
        captured["org"] = organization_id
        return {
            "polled": 0, "events_dispatched": 0,
            "duplicates": 0, "errors": 0,
        }

    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=s4_conn,
    ), patch(
        "clearledgr.services.erp_payment_dispatcher.poll_sap_s4hana_payments",
        new=fake_s4_poll,
    ):
        asyncio.run(poll_sap_b1_payments(organization_id="orgA", db=db))

    assert captured["called"] is True
    assert captured["org"] == "orgA"


# ─── S/4HANA polling end-to-end ───────────────────────────────────


def test_poll_s4hana_dispatches_cleared_payment(db):
    """An awaiting_payment AP item with a S/4HANA composite ref
    + a cleared OData response → record_payment_confirmation
    fires and the AP item walks to payment_executed."""
    import asyncio

    item = _make_awaiting_ap_item(
        db, item_id="AP-s4-poll-1",
        erp_reference="1000/5105600000/2026",
    )
    s4_conn = _conn(base_url="https://my-s4.api.sap")

    async def fake_status(connection, key):
        return {
            "paid": True,
            "payment_amount": 1190.00,
            "payment_date": "2026-04-29",
            "payment_method": "",
            "payment_reference": "1500001234",
            "partial": False,
            "remaining_balance": 0.0,
            "currency": "EUR",
        }

    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=s4_conn,
    ), patch(
        "clearledgr.integrations.erp_sap.get_payment_status_sap_s4hana",
        new=fake_status,
    ):
        result = asyncio.run(poll_sap_s4hana_payments(
            organization_id="orgA", db=db,
        ))

    assert result["polled"] == 1
    assert result["events_dispatched"] == 1
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "payment_executed"
    rows = db.list_payment_confirmations_for_ap_item("orgA", item["id"])
    assert len(rows) == 1
    assert rows[0]["source"] == "sap_s4hana"


def test_poll_s4hana_skips_items_without_composite_key(db):
    """AP items whose erp_reference isn't S/4HANA-shaped (no
    slash) are skipped without erroring."""
    import asyncio

    _make_awaiting_ap_item(
        db, item_id="AP-s4-skip-1",
        erp_reference="some-b1-doc-entry",  # B1-shaped, not CC/DOC/FY
    )
    s4_conn = _conn(base_url="https://my-s4.api.sap")

    with patch(
        "clearledgr.integrations.erp_router.get_erp_connection",
        return_value=s4_conn,
    ), patch(
        "clearledgr.integrations.erp_sap.get_payment_status_sap_s4hana",
        new=AsyncMock(side_effect=AssertionError("should not be called")),
    ):
        result = asyncio.run(poll_sap_s4hana_payments(
            organization_id="orgA", db=db,
        ))

    assert result["polled"] == 1
    assert result["events_dispatched"] == 0


# ─── Intake adapter no longer shortcuts 'paid' to CLOSED ─────────


def test_intake_adapter_paid_no_longer_short_circuits():
    """The intake adapter must defer 'paid' state updates to the
    payment dispatcher so the C2 lifecycle (record_payment_confirmation
    + remittance + bank-rec) fires properly. derive_state_update
    should now return target_state=None for paid events."""
    import asyncio
    from clearledgr.services.intake_adapter import IntakeEnvelope

    adapter = SapS4HanaIntakeAdapter()
    envelope = IntakeEnvelope(
        source_type="sap_s4hana",
        source_id="1000/5105600000/2026",
        event_type="paid",
        organization_id="orgA",
        raw_payload={"data": {"CompanyCode": "1000"}},
        channel_metadata={"invoice_payload": {}},
    )
    update = asyncio.run(
        adapter.derive_state_update("orgA", envelope),
    )
    assert update.target_state is None  # NOT APState.CLOSED


# ─── CPI CloudEvents parser ───────────────────────────────────────


_CPI_PAID_EVENT = json.dumps({
    "specversion": "1.0",
    "type": "sap.s4.beh.suppliere2einvoice.cleared.v1",
    "source": "/sap/SI/100/SUPPLIERINVOICE",
    "id": "evt-uuid-1",
    "data": {
        "CompanyCode": "1000",
        "SupplierInvoice": "5105600000",
        "FiscalYear": "2026",
        "ClearingDocument": "1500001234",
        "ClearingDate": "2026-04-29",
        "InvoiceGrossAmount": 1190.00,
        "DocumentCurrency": "EUR",
    },
}).encode("utf-8")


_CPI_CANCELLED_EVENT = json.dumps({
    "specversion": "1.0",
    "type": "sap.s4.beh.suppliere2einvoice.cancelled.v1",
    "id": "evt-uuid-2",
    "data": {
        "CompanyCode": "1000",
        "SupplierInvoice": "5105600001",
        "FiscalYear": "2026",
    },
}).encode("utf-8")


_CPI_BILL_EVENT = json.dumps({
    "specversion": "1.0",
    "type": "sap.s4.beh.suppliere2einvoice.created.v1",
    "id": "evt-uuid-3",
    "data": {"CompanyCode": "1000"},
}).encode("utf-8")


def test_cpi_parser_extracts_cleared_event():
    out = _parse_sap_s4hana_payment_envelope(_CPI_PAID_EVENT)
    assert len(out) == 1
    evt = out[0]
    assert evt.source == "sap_s4hana"
    assert evt.erp_bill_reference == "1000/5105600000/2026"
    assert evt.status == "confirmed"
    assert evt.payment_reference == "1500001234"
    assert evt.amount == 1190.00


def test_cpi_parser_extracts_cancelled_event():
    out = _parse_sap_s4hana_payment_envelope(_CPI_CANCELLED_EVENT)
    assert len(out) == 1
    assert out[0].status == "failed"
    assert out[0].failure_reason == "cancelled"


def test_cpi_parser_skips_non_payment_events():
    """Bill-creation events should NOT spawn payment dispatch — the
    intake adapter handles them. The payment dispatcher must
    silently skip non-payment event types."""
    out = _parse_sap_s4hana_payment_envelope(_CPI_BILL_EVENT)
    assert out == []


def test_cpi_parser_handles_events_array_envelope():
    body = json.dumps({"events": [
        json.loads(_CPI_PAID_EVENT.decode()),
        json.loads(_CPI_CANCELLED_EVENT.decode()),
    ]}).encode("utf-8")
    out = _parse_sap_s4hana_payment_envelope(body)
    assert len(out) == 2


def test_cpi_parser_handles_malformed():
    assert _parse_sap_s4hana_payment_envelope(b"") == []
    assert _parse_sap_s4hana_payment_envelope(b"not-json") == []
    assert _parse_sap_s4hana_payment_envelope(
        json.dumps({"data": {"CompanyCode": "1000"}}).encode()
    ) == []  # missing 'type' to classify


# ─── End-to-end webhook dispatch ──────────────────────────────────


def test_cpi_webhook_dispatch_records_payment(db):
    item = _make_awaiting_ap_item(
        db, item_id="AP-s4-cpi-1",
        erp_reference="1000/5105600000/2026",
    )
    result = dispatch_sap_s4hana_payment_webhook(
        organization_id="orgA",
        raw_body=_CPI_PAID_EVENT,
        db=db,
    )
    assert result["events_parsed"] == 1
    assert result["events_dispatched"] == 1
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "payment_executed"
    rows = db.list_payment_confirmations_for_ap_item("orgA", item["id"])
    assert rows[0]["source"] == "sap_s4hana"
    assert rows[0]["payment_reference"] == "1500001234"


def test_cpi_webhook_dispatch_redelivery_idempotent(db):
    item = _make_awaiting_ap_item(
        db, item_id="AP-s4-cpi-idem",
        erp_reference="1000/5105600000/2026",
    )
    first = dispatch_sap_s4hana_payment_webhook(
        organization_id="orgA",
        raw_body=_CPI_PAID_EVENT, db=db,
    )
    second = dispatch_sap_s4hana_payment_webhook(
        organization_id="orgA",
        raw_body=_CPI_PAID_EVENT, db=db,
    )
    assert first["events_dispatched"] == 1
    assert second["duplicates"] == 1
    assert second["events_dispatched"] == 0
    rows = db.list_payment_confirmations_for_ap_item("orgA", item["id"])
    assert len(rows) == 1
