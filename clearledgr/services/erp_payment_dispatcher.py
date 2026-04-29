"""ERP payment-event dispatcher (Wave 2 / C3).

Glue between the inbound ERP webhooks (in
:mod:`clearledgr.api.erp_webhooks`) and the payment-tracking service (in
:mod:`clearledgr.services.payment_tracking`).

Each ERP speaks a different webhook dialect:

  * **QuickBooks** — Intuit pushes a notification envelope listing
    entity ids that changed. ``BillPayment`` events are the payment
    signal; we follow up with a GET to fetch the full BillPayment
    record (linked Bills, total amount, txn date, pay type).
  * **Xero** — pushes an event envelope listing tenant + invoice ids
    that changed. ACCPAY (bill) invoices that hit Status=PAID or
    AmountDue=0 are the payment signal; we GET the invoice to read
    Payments[] and AmountPaid.
  * **NetSuite** — SuiteScript pushes the full payment payload
    (no follow-up call needed). Parser walks the
    ``vendor_payments`` block.
  * **SAP B1** — no public payment webhook; the polling task in C3e
    walks open AP items and calls a sibling helper to fetch payment
    state from B1.

Each parser emits :class:`ParsedPaymentEvent` records keyed by the
ERP-native bill reference. The dispatcher then resolves each bill
reference to an AP item via ``get_ap_item_by_erp_reference`` and
calls :func:`record_payment_confirmation`.

Idempotent end-to-end: redelivery of the same webhook results in
zero new ``payment_confirmations`` rows and zero new audit events.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from clearledgr.services.payment_tracking import (
    PaymentConfirmationResult,
    record_payment_confirmation,
)

logger = logging.getLogger(__name__)


@dataclass
class ParsedPaymentEvent:
    """Canonical shape every ERP parser yields, ready for dispatch.

    ``erp_bill_reference`` is the value we stored on
    ``ap_items.erp_reference`` at posting time, so the dispatcher
    can find the AP item without per-ERP lookup logic in the
    dispatcher itself.
    """
    payment_id: str
    source: str
    erp_bill_reference: str
    status: str = "confirmed"
    settlement_at: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    method: Optional[str] = None
    payment_reference: Optional[str] = None
    bank_account_last4: Optional[str] = None
    failure_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── QuickBooks ──────────────────────────────────────────────────────


def _parse_quickbooks_envelope(body: bytes) -> List[Dict[str, str]]:
    """Walk the Intuit notification envelope and return the entity
    references we care about. Each item is::

        {"name": "BillPayment", "id": "123", "operation": "Create"|"Update"}

    Operations other than Create/Update are dropped (Delete is handled
    by the void detection in the BillPayment fetch path)."""
    try:
        text = body.decode("utf-8", errors="replace") if body else ""
        envelope = json.loads(text) if text else {}
    except Exception:
        return []
    if not isinstance(envelope, dict):
        return []
    entities: List[Dict[str, str]] = []
    for note in envelope.get("eventNotifications") or []:
        if not isinstance(note, dict):
            continue
        change = note.get("dataChangeEvent") or {}
        for ent in change.get("entities") or []:
            if not isinstance(ent, dict):
                continue
            name = str(ent.get("name") or "")
            ent_id = str(ent.get("id") or "")
            op = str(ent.get("operation") or "")
            if not name or not ent_id:
                continue
            entities.append({"name": name, "id": ent_id, "operation": op})
    return entities


async def _fetch_qb_bill_payment_to_events(
    connection,
    bill_payment_id: str,
    operation: str,
) -> List[ParsedPaymentEvent]:
    """Fetch one QuickBooks BillPayment and convert it into one event
    per linked Bill.

    A single BillPayment can clear multiple bills (one Line per Bill);
    we emit a separate event for each so the AP-item-level state
    machine stays one-to-one.
    """
    from clearledgr.integrations.erp_quickbooks import (
        fetch_quickbooks_bill_payment,
    )
    fetched = await fetch_quickbooks_bill_payment(connection, bill_payment_id)
    if fetched.get("status") != "success":
        logger.info(
            "qb dispatcher: skipping bill_payment=%s reason=%s",
            bill_payment_id, fetched.get("reason"),
        )
        return []

    voided = bool(fetched.get("voided"))
    txn_date = fetched.get("txn_date")
    currency = fetched.get("currency")
    pay_type = fetched.get("pay_type")
    private_note = fetched.get("private_note")

    out: List[ParsedPaymentEvent] = []
    for link in fetched.get("linked_bills") or []:
        bill_id = str(link.get("bill_id") or "").strip()
        if not bill_id:
            continue
        out.append(ParsedPaymentEvent(
            payment_id=str(fetched.get("bill_payment_id") or bill_payment_id),
            source="quickbooks",
            erp_bill_reference=bill_id,
            status="failed" if voided else "confirmed",
            settlement_at=(
                f"{txn_date}T00:00:00+00:00" if txn_date else None
            ),
            amount=link.get("amount"),
            currency=currency,
            method=pay_type.lower() if pay_type else None,
            payment_reference=str(fetched.get("bill_payment_id") or ""),
            failure_reason=("voided" if voided else None),
            metadata={
                "qb_operation": operation,
                "qb_private_note": private_note,
            },
        ))
    return out


async def dispatch_quickbooks_payment_webhook(
    organization_id: str,
    raw_body: bytes,
    *,
    db=None,
) -> Dict[str, Any]:
    """Entry point called by the QBO webhook route after signature
    verification has passed. Returns a small summary suitable for
    logs / tests; never raises (logs and swallows so the webhook
    response stays a fast 200)."""
    from clearledgr.core.database import get_db
    from clearledgr.integrations.erp_router import get_erp_connection

    db = db or get_db()
    summary: Dict[str, Any] = {
        "events_parsed": 0,
        "events_dispatched": 0,
        "events_skipped": 0,
        "duplicates": 0,
    }

    entities = _parse_quickbooks_envelope(raw_body)
    bill_payment_ids = [
        e["id"] for e in entities
        if e["name"] == "BillPayment"
        and e["operation"] in ("Create", "Update")
    ]
    summary["events_parsed"] = len(bill_payment_ids)
    if not bill_payment_ids:
        return summary

    try:
        connection = get_erp_connection(organization_id, "quickbooks")
    except Exception:
        logger.exception(
            "qb dispatcher: connection lookup failed for org=%s",
            organization_id,
        )
        return summary
    if connection is None:
        logger.info(
            "qb dispatcher: no quickbooks connection for org=%s — skipping",
            organization_id,
        )
        return summary

    for bp_id in bill_payment_ids:
        try:
            events = await _fetch_qb_bill_payment_to_events(
                connection, bp_id, _operation_for(entities, "BillPayment", bp_id),
            )
        except Exception:
            logger.exception(
                "qb dispatcher: fetch failed bp=%s org=%s", bp_id, organization_id,
            )
            continue
        for evt in events:
            res = _dispatch_one(db, organization_id, evt)
            if res is None:
                summary["events_skipped"] += 1
            elif res.duplicate:
                summary["duplicates"] += 1
            else:
                summary["events_dispatched"] += 1
    return summary


def _operation_for(
    entities: List[Dict[str, str]], name: str, ent_id: str,
) -> str:
    for e in entities:
        if e.get("name") == name and e.get("id") == ent_id:
            return e.get("operation") or ""
    return ""


# ── Xero ────────────────────────────────────────────────────────────


def _parse_xero_envelope(body: bytes) -> List[Dict[str, str]]:
    """Walk Xero's events array. Each accepted event is::

        {"resource_id": "<invoice GUID>", "event_type": "UPDATE", "category": "INVOICE"}
    """
    try:
        text = body.decode("utf-8", errors="replace") if body else ""
        envelope = json.loads(text) if text else {}
    except Exception:
        return []
    if not isinstance(envelope, dict):
        return []
    out: List[Dict[str, str]] = []
    for evt in envelope.get("events") or []:
        if not isinstance(evt, dict):
            continue
        category = str(evt.get("eventCategory") or "").upper()
        event_type = str(evt.get("eventType") or "").upper()
        resource_id = str(evt.get("resourceId") or "").strip()
        if not resource_id:
            continue
        if category != "INVOICE":
            continue
        if event_type not in {"UPDATE", "CREATE"}:
            continue
        out.append({
            "resource_id": resource_id,
            "event_type": event_type,
            "category": category,
        })
    return out


async def _fetch_xero_invoice_to_events(
    connection,
    invoice_id: str,
) -> List[ParsedPaymentEvent]:
    """Fetch one Xero invoice; emit a confirmation event if it has
    cleared (AmountDue==0 or Status==PAID), or a failure event if a
    payment on it was VOIDED/DELETED. Otherwise emit nothing."""
    from clearledgr.integrations.erp_xero import get_payment_status_xero

    status = await get_payment_status_xero(connection, invoice_id)
    if not isinstance(status, dict):
        return []
    if status.get("paid"):
        # AmountDue == 0 case. Emit a confirmation. payment_id falls
        # back to the invoice id when no Payments[] is exposed
        # (credit-only closure).
        ref = (
            str(status.get("payment_reference") or "").strip()
            or invoice_id
        )
        return [ParsedPaymentEvent(
            payment_id=ref,
            source="xero",
            erp_bill_reference=invoice_id,
            status="confirmed",
            settlement_at=status.get("payment_date") or None,
            amount=(
                float(status["payment_amount"])
                if status.get("payment_amount") is not None else None
            ),
            payment_reference=str(status.get("payment_reference") or "") or None,
            method=str(status.get("payment_method") or "") or None,
            metadata={
                "closure_method": status.get("closure_method"),
            },
        )]
    if status.get("payment_failed"):
        return [ParsedPaymentEvent(
            payment_id=f"{invoice_id}:failed",
            source="xero",
            erp_bill_reference=invoice_id,
            status="failed",
            failure_reason=str(status.get("reason") or "payment_voided"),
        )]
    return []


async def dispatch_xero_payment_webhook(
    organization_id: str,
    raw_body: bytes,
    *,
    db=None,
) -> Dict[str, Any]:
    """Entry point for Xero webhook route after signature verification."""
    from clearledgr.core.database import get_db
    from clearledgr.integrations.erp_router import get_erp_connection

    db = db or get_db()
    summary: Dict[str, Any] = {
        "events_parsed": 0,
        "events_dispatched": 0,
        "events_skipped": 0,
        "duplicates": 0,
    }

    parsed = _parse_xero_envelope(raw_body)
    summary["events_parsed"] = len(parsed)
    if not parsed:
        return summary

    try:
        connection = get_erp_connection(organization_id, "xero")
    except Exception:
        logger.exception(
            "xero dispatcher: connection lookup failed for org=%s",
            organization_id,
        )
        return summary
    if connection is None:
        return summary

    for evt in parsed:
        invoice_id = evt["resource_id"]
        try:
            events = await _fetch_xero_invoice_to_events(connection, invoice_id)
        except Exception:
            logger.exception(
                "xero dispatcher: fetch failed invoice=%s org=%s",
                invoice_id, organization_id,
            )
            continue
        for parsed_evt in events:
            res = _dispatch_one(db, organization_id, parsed_evt)
            if res is None:
                summary["events_skipped"] += 1
            elif res.duplicate:
                summary["duplicates"] += 1
            else:
                summary["events_dispatched"] += 1
    return summary


# ── NetSuite ────────────────────────────────────────────────────────


def parse_netsuite_payment_payload(
    body: bytes,
) -> List[ParsedPaymentEvent]:
    """NetSuite SuiteScript pushes the full payment payload — no
    follow-up REST call. Expected shape::

        {
          "vendor_payments": [
            {
              "payment_id": "INTERNAL-id",
              "transaction_date": "2026-04-29",
              "amount": 500.00,
              "currency_code": "EUR",
              "payment_method": "ACH",
              "bill_internal_ids": ["1234"],
              "status": "Paid In Full" | "Voided" | ...,
              "memo": "..."
            },
            ...
          ]
        }

    Tolerant of payload variations (single ``vendor_payment`` vs
    ``vendor_payments`` list).
    """
    try:
        text = body.decode("utf-8", errors="replace") if body else ""
        envelope = json.loads(text) if text else {}
    except Exception:
        return []
    if not isinstance(envelope, dict):
        return []

    payments = envelope.get("vendor_payments")
    if payments is None:
        single = envelope.get("vendor_payment")
        payments = [single] if isinstance(single, dict) else []
    if not isinstance(payments, list):
        return []

    out: List[ParsedPaymentEvent] = []
    for p in payments:
        if not isinstance(p, dict):
            continue
        ns_status = str(p.get("status") or "").strip().lower()
        is_void = "void" in ns_status or "reversed" in ns_status
        is_failed = "fail" in ns_status or "decline" in ns_status
        bill_ids = p.get("bill_internal_ids") or []
        if isinstance(bill_ids, str):
            bill_ids = [bill_ids]
        if not isinstance(bill_ids, list):
            continue
        payment_id = str(p.get("payment_id") or "").strip()
        if not payment_id:
            continue
        for bill_id in bill_ids:
            bid = str(bill_id or "").strip()
            if not bid:
                continue
            out.append(ParsedPaymentEvent(
                payment_id=payment_id,
                source="netsuite",
                erp_bill_reference=bid,
                status=(
                    "failed" if (is_void or is_failed) else "confirmed"
                ),
                settlement_at=p.get("transaction_date") or None,
                amount=(
                    float(p["amount"]) if p.get("amount") is not None else None
                ),
                currency=p.get("currency_code") or None,
                method=p.get("payment_method") or None,
                payment_reference=p.get("reference") or payment_id,
                failure_reason=(
                    str(p.get("status") or "voided")
                    if (is_void or is_failed) else None
                ),
                metadata={
                    "ns_memo": p.get("memo"),
                    "ns_status": p.get("status"),
                },
            ))
    return out


def dispatch_netsuite_payment_webhook(
    organization_id: str,
    raw_body: bytes,
    *,
    db=None,
) -> Dict[str, Any]:
    """NetSuite dispatcher is sync (no follow-up REST roundtrip)."""
    from clearledgr.core.database import get_db
    db = db or get_db()
    summary: Dict[str, Any] = {
        "events_parsed": 0,
        "events_dispatched": 0,
        "events_skipped": 0,
        "duplicates": 0,
    }
    events = parse_netsuite_payment_payload(raw_body)
    summary["events_parsed"] = len(events)
    for evt in events:
        res = _dispatch_one(db, organization_id, evt)
        if res is None:
            summary["events_skipped"] += 1
        elif res.duplicate:
            summary["duplicates"] += 1
        else:
            summary["events_dispatched"] += 1
    return summary


# ── SAP B1 polling ──────────────────────────────────────────────────


async def poll_sap_b1_payments(
    organization_id: str,
    *,
    db=None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Polling fallback for SAP Business One.

    SAP B1 doesn't ship a payment webhook surface in the supported
    OData feed. Instead we walk the AP items currently in
    ``awaiting_payment`` / ``payment_in_flight`` for the org and
    ask SAP whether each one has been settled.

    Called by a Celery beat schedule (5 min cadence by default).
    """
    from clearledgr.core.database import get_db
    from clearledgr.integrations.erp_router import get_erp_connection
    from clearledgr.integrations.erp_sap import get_payment_status_sap

    db = db or get_db()
    summary: Dict[str, Any] = {
        "polled": 0,
        "events_dispatched": 0,
        "duplicates": 0,
        "errors": 0,
    }
    try:
        connection = get_erp_connection(organization_id, "sap")
    except Exception:
        logger.exception("sap b1 poll: connection lookup failed org=%s", organization_id)
        return summary
    if connection is None:
        return summary

    candidates: List[Dict[str, Any]] = []
    for state in ("awaiting_payment", "payment_in_flight"):
        rows = db.list_ap_items(
            organization_id=organization_id, state=state, limit=limit,
        )
        candidates.extend(rows or [])
    summary["polled"] = len(candidates)

    for item in candidates:
        bill_ref = str(item.get("erp_reference") or "").strip()
        if not bill_ref:
            continue
        try:
            sap_status = await get_payment_status_sap(connection, bill_ref)
        except Exception:
            logger.exception(
                "sap b1 poll: status fetch failed bill=%s org=%s",
                bill_ref, organization_id,
            )
            summary["errors"] += 1
            continue
        if not isinstance(sap_status, dict):
            continue
        if not sap_status.get("paid"):
            continue
        evt = ParsedPaymentEvent(
            payment_id=str(
                sap_status.get("payment_reference") or f"{bill_ref}:paid"
            ),
            source="sap_b1",
            erp_bill_reference=bill_ref,
            status="confirmed",
            settlement_at=sap_status.get("payment_date") or None,
            amount=(
                float(sap_status["payment_amount"])
                if sap_status.get("payment_amount") is not None else None
            ),
            payment_reference=str(sap_status.get("payment_reference") or "") or None,
            method=str(sap_status.get("payment_method") or "") or None,
        )
        res = _dispatch_one(db, organization_id, evt)
        if res is None:
            continue
        if res.duplicate:
            summary["duplicates"] += 1
        else:
            summary["events_dispatched"] += 1
    return summary


# ── Common dispatch ─────────────────────────────────────────────────


def _dispatch_one(
    db,
    organization_id: str,
    evt: ParsedPaymentEvent,
) -> Optional[PaymentConfirmationResult]:
    """Resolve the AP item by erp_reference and call the tracking
    service. Returns ``None`` when no AP item matches (orphan).
    """
    ap_item = db.get_ap_item_by_erp_reference(
        organization_id, evt.erp_bill_reference,
    )
    if ap_item is None:
        # Orphan: the bill exists in the ERP but we don't have an AP
        # item for it. Could be a manual-in-ERP payment, or the bill
        # was created outside Clearledgr. Emit an audit-only orphan
        # record so the operator can investigate.
        try:
            db.append_audit_event({
                "event_type": "payment_confirmation_orphan",
                "actor_type": "system",
                "actor_id": "erp_payment_dispatcher",
                "box_id": f"erp_payment_orphan:{evt.source}:{evt.payment_id}",
                "box_type": "erp_webhook",
                "organization_id": organization_id,
                "source": "payment_confirmation",
                "idempotency_key": (
                    f"payment_orphan:{organization_id}:{evt.source}:{evt.payment_id}"
                ),
                "metadata": {
                    "source": evt.source,
                    "payment_id": evt.payment_id,
                    "erp_bill_reference": evt.erp_bill_reference,
                    "amount": evt.amount,
                    "currency": evt.currency,
                    "settlement_at": evt.settlement_at,
                },
            })
        except Exception:
            logger.exception(
                "dispatcher: orphan audit emit failed source=%s payment_id=%s",
                evt.source, evt.payment_id,
            )
        return None

    return record_payment_confirmation(
        db,
        organization_id=organization_id,
        ap_item_id=ap_item["id"],
        payment_id=evt.payment_id,
        source=evt.source,
        status=evt.status,
        settlement_at=evt.settlement_at,
        amount=evt.amount,
        currency=evt.currency,
        method=evt.method,
        payment_reference=evt.payment_reference,
        bank_account_last4=evt.bank_account_last4,
        failure_reason=evt.failure_reason,
        actor_type="erp_webhook",
        actor_id=evt.source,
        metadata=evt.metadata or None,
    )
