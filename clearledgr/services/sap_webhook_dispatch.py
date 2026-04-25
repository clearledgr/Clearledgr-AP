"""Dispatch handlers for SAP S/4HANA SupplierInvoice events.

The SAP equivalent of :mod:`clearledgr.services.erp_webhook_dispatch`.
Triggered via:

* **S/4HANA Cloud**: BTP Event Mesh subscribes to the standard SAP
  business events ``sap.s4.beh.supplierinvoice.v1.SupplierInvoice.Created``,
  ``…Posted``, ``…Blocked``, ``…Cancelled`` and forwards each into
  Clearledgr's webhook endpoint at
  ``/erp/webhooks/sap/{organization_id}``.
* **S/4HANA on-premise**: an ABAP enhancement (BAdI on
  ``BUS2081`` / ``MIRO`` post-save) fires the same payload shape via
  HTTPS through SAP Cloud Connector to the same endpoint.

The webhook handler in ``clearledgr/api/erp_webhooks.py`` verifies the
HMAC signature and calls :func:`dispatch_sap_event` here, which:

* maps the SAP event onto our canonical AP state machine
* creates or advances the AP item Box
* triggers Slack approval routing when the bill enters at
  ``needs_approval`` (payment block present)

State derivation for SAP supplier invoices:

* ``Posted`` and not paid, no payment block → ``posted_to_erp``
* ``Posted`` with ``PaymentBlockingReason`` set → ``needs_approval``
* ``Reversed`` / ``Cancelled`` → ``closed`` (with metadata note)
* ``Paid`` → ``closed``

The composite document key is ``CompanyCode + SupplierInvoice +
FiscalYear`` — we serialize as ``"<CC>/<DocNum>/<FY>"`` and store in
``ap_items.erp_reference`` for idempotent lookup.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from clearledgr.core.ap_states import APState, validate_transition
from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


def dispatch_sap_event(organization_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Route a verified SAP S/4HANA event payload to the right handler.

    Accepted shapes:

    * SAP Event Mesh CloudEvents-style: ``{"type": "sap.s4.beh.supplierinvoice.v1.SupplierInvoice.Created.v1", "data": {...}}``
    * Custom ABAP-side payload (for on-prem BAdI senders):
      ``{"event_type": "supplier_invoice.created", "invoice": {...}}``

    Both forms carry the supplier-invoice composite key
    (``CompanyCode``, ``SupplierInvoice``, ``FiscalYear``) somewhere in
    the data — we normalize on extract.
    """
    event_type, invoice = _normalize_event(payload)
    if not invoice:
        return {"ok": False, "reason": "missing_invoice_payload", "event_type": event_type}

    composite_key = _composite_key(invoice)
    if not composite_key:
        return {"ok": False, "reason": "missing_composite_key", "event_type": event_type}

    if event_type in {"created", "posted"}:
        return _handle_create_or_posted(organization_id, payload, invoice, composite_key, event_type)
    if event_type == "blocked":
        return _handle_blocked(organization_id, payload, invoice, composite_key)
    if event_type == "released":
        return _handle_released(organization_id, payload, invoice, composite_key)
    if event_type in {"paid", "payment_executed"}:
        return _handle_paid(organization_id, payload, invoice, composite_key)
    if event_type in {"cancelled", "reversed"}:
        return _handle_cancelled(organization_id, payload, invoice, composite_key)
    return {"ok": True, "reason": "ignored_event", "event_type": event_type}


# ─── Event shape normalization ──────────────────────────────────────


_CLOUDEVENTS_SUFFIX_MAP = {
    "Created": "created",
    "Posted": "posted",
    "Blocked": "blocked",
    "Released": "released",
    "Cancelled": "cancelled",
    "Reversed": "cancelled",
    "Paid": "paid",
    "PaymentExecuted": "payment_executed",
}


def _normalize_event(payload: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """Collapse SAP Event Mesh (CloudEvents) and ABAP-BAdI shapes into
    a uniform ``(event_type, invoice_dict)`` tuple.
    """
    raw_type = str(payload.get("type") or payload.get("event_type") or "").strip()
    invoice = payload.get("invoice") or payload.get("data") or {}
    if isinstance(invoice, dict) and "data" in invoice and isinstance(invoice["data"], dict):
        invoice = invoice["data"]

    event_type = ""
    if raw_type:
        # CloudEvents style: sap.s4.beh.supplierinvoice.v1.SupplierInvoice.Created.v1
        if raw_type.startswith("sap."):
            for suffix, mapped in _CLOUDEVENTS_SUFFIX_MAP.items():
                if f".{suffix}." in raw_type or raw_type.endswith(f".{suffix}"):
                    event_type = mapped
                    break
        # supplier_invoice.created style
        elif raw_type.startswith("supplier_invoice."):
            event_type = raw_type.split(".", 1)[1].lower()
        # supplierinvoice.created or supplier-invoice.created
        elif "." in raw_type:
            event_type = raw_type.rsplit(".", 1)[1].lower()
        else:
            event_type = raw_type.lower()

    return event_type, invoice if isinstance(invoice, dict) else {}


def _composite_key(invoice: Dict[str, Any]) -> Optional[str]:
    """Build ``"<CC>/<DocNum>/<FY>"`` from the invoice payload.

    Tolerates the multiple field-name conventions SAP uses across
    Event Mesh (PascalCase), OData responses (PascalCase), and ABAP
    BAdI senders (UPPER_SNAKE).
    """
    cc = _pick(invoice, "CompanyCode", "companyCode", "BUKRS", "company_code")
    doc = _pick(invoice, "SupplierInvoice", "supplierInvoice", "BELNR", "supplier_invoice", "doc_number")
    fy = _pick(invoice, "FiscalYear", "fiscalYear", "GJAHR", "fiscal_year")
    if not (cc and doc and fy):
        return None
    return f"{cc}/{doc}/{fy}"


def _pick(payload: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        if k in payload and payload[k] not in (None, ""):
            return str(payload[k]).strip()
    return ""


# ─── Handlers ───────────────────────────────────────────────────────


def _handle_create_or_posted(
    organization_id: str,
    envelope: Dict[str, Any],
    invoice: Dict[str, Any],
    composite_key: str,
    event_type: str,
) -> Dict[str, Any]:
    db = get_db()
    existing = db.get_ap_item_by_erp_reference(organization_id, composite_key)
    if existing:
        return _handle_update(organization_id, envelope, invoice, composite_key, existing)

    initial_state = _state_from_invoice(invoice)
    payload = _ap_item_payload_from_invoice(
        organization_id=organization_id,
        invoice=invoice,
        envelope=envelope,
        composite_key=composite_key,
        state=initial_state,
        event_type=event_type,
    )
    item = db.create_ap_item(payload)
    ap_item_id = str((item or {}).get("id") or payload.get("id") or "").strip()
    logger.info(
        "sap_webhook_dispatch: created AP item %s for SAP invoice %s (org=%s, state=%s)",
        ap_item_id, composite_key, organization_id, initial_state,
    )
    _record_intake_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        envelope=envelope,
        action="created",
        target_state=initial_state,
        composite_key=composite_key,
    )

    # Slack-route on payment block — same pattern as NetSuite intake.
    if initial_state == APState.NEEDS_APPROVAL.value and ap_item_id:
        _route_for_approval_async(item or {**payload, "id": ap_item_id})

    return {
        "ok": True,
        "action": "created",
        "ap_item_id": ap_item_id,
        "state": initial_state,
        "routed_to_slack": initial_state == APState.NEEDS_APPROVAL.value,
        "composite_key": composite_key,
    }


def _handle_update(
    organization_id: str,
    envelope: Dict[str, Any],
    invoice: Dict[str, Any],
    composite_key: str,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    db = get_db()
    if existing is None:
        existing = db.get_ap_item_by_erp_reference(organization_id, composite_key)
    if not existing:
        return _handle_create_or_posted(organization_id, envelope, invoice, composite_key, "posted")

    ap_item_id = str(existing.get("id") or "").strip()
    current_state = str(existing.get("state") or "").strip().lower()
    desired_state = _state_from_invoice(invoice)

    field_updates = {
        k: v for k, v in {
            "vendor_name": _pick(invoice, "SupplierName", "supplier_name", "VendorName"),
            "amount": _pick(invoice, "InvoiceGrossAmount", "GrossAmount", "amount", "WRBTR"),
            "currency": (_pick(invoice, "DocumentCurrency", "Currency", "WAERS") or "").upper() or None,
            "invoice_number": _pick(invoice, "SupplierInvoiceIDByInvcgParty", "invoice_number"),
            "due_date": _pick(invoice, "NetDueDate", "due_date"),
        }.items()
        if v not in (None, "")
    }

    if desired_state != current_state and validate_transition(current_state, desired_state):
        field_updates["state"] = desired_state
        field_updates["_actor_type"] = "erp_webhook"
        field_updates["_actor_id"] = "sap_s4hana"

    if field_updates:
        try:
            db.update_ap_item(ap_item_id, **field_updates)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sap_webhook_dispatch: update failed ap_item=%s key=%s — %s",
                ap_item_id, composite_key, exc,
            )
            return {"ok": False, "action": "update_failed", "ap_item_id": ap_item_id, "reason": str(exc)}

    target_state_for_audit = field_updates.get("state") or current_state
    _record_intake_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        envelope=envelope,
        action="updated",
        target_state=target_state_for_audit,
        composite_key=composite_key,
    )

    if (
        field_updates.get("state") == APState.NEEDS_APPROVAL.value
        and current_state != APState.NEEDS_APPROVAL.value
    ):
        refreshed = db.get_ap_item(ap_item_id) if hasattr(db, "get_ap_item") else None
        _route_for_approval_async(refreshed or existing)

    return {
        "ok": True,
        "action": "updated",
        "ap_item_id": ap_item_id,
        "state": target_state_for_audit,
        "composite_key": composite_key,
    }


def _handle_blocked(organization_id, envelope, invoice, composite_key):
    """Payment block added in SAP after the bill was already posted."""
    return _handle_update(organization_id, envelope, invoice, composite_key)


def _handle_released(organization_id, envelope, invoice, composite_key):
    """Payment block cleared in SAP (could be from our Approve flow or
    from the SAP-side AP team manually). Either way, advance the Box."""
    return _handle_update(organization_id, envelope, invoice, composite_key)


def _handle_paid(organization_id, envelope, invoice, composite_key):
    db = get_db()
    existing = db.get_ap_item_by_erp_reference(organization_id, composite_key)
    if not existing:
        # Synthesize the create as already-paid.
        invoice_with_paid = dict(invoice)
        invoice_with_paid.setdefault("InvoiceStatus", "Paid")
        return _handle_create_or_posted(
            organization_id, envelope, invoice_with_paid, composite_key, "paid",
        )

    ap_item_id = str(existing.get("id") or "").strip()
    current_state = str(existing.get("state") or "").strip().lower()
    if current_state == APState.CLOSED.value:
        return {"ok": True, "action": "noop_already_closed", "ap_item_id": ap_item_id}
    if not validate_transition(current_state, APState.CLOSED.value):
        return {"ok": False, "action": "invalid_transition", "from": current_state, "to": APState.CLOSED.value}
    try:
        db.update_ap_item(
            ap_item_id,
            state=APState.CLOSED.value,
            _actor_type="erp_webhook",
            _actor_id="sap_s4hana",
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "close_failed", "ap_item_id": ap_item_id, "error": str(exc)}
    _record_intake_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        envelope=envelope,
        action="paid_closed",
        target_state=APState.CLOSED.value,
        composite_key=composite_key,
    )
    return {"ok": True, "action": "closed", "ap_item_id": ap_item_id, "state": APState.CLOSED.value}


def _handle_cancelled(organization_id, envelope, invoice, composite_key):
    db = get_db()
    existing = db.get_ap_item_by_erp_reference(organization_id, composite_key)
    if not existing:
        return {"ok": True, "action": "noop_no_box", "composite_key": composite_key}
    ap_item_id = str(existing.get("id") or "").strip()
    current_state = str(existing.get("state") or "").strip().lower()
    if current_state in {APState.CLOSED.value, APState.REJECTED.value, APState.REVERSED.value}:
        return {"ok": True, "action": "noop_terminal", "ap_item_id": ap_item_id}
    if validate_transition(current_state, APState.CLOSED.value):
        try:
            db.update_ap_item(
                ap_item_id,
                state=APState.CLOSED.value,
                _actor_type="erp_webhook",
                _actor_id="sap_s4hana",
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "action": "close_failed", "ap_item_id": ap_item_id, "error": str(exc)}
    _record_intake_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        envelope=envelope,
        action="cancelled_in_erp",
        target_state=APState.CLOSED.value,
        composite_key=composite_key,
    )
    return {"ok": True, "action": "closed_via_cancel", "ap_item_id": ap_item_id}


# ─── State derivation + payload construction ───────────────────────


def _state_from_invoice(invoice: Dict[str, Any]) -> str:
    """Map S/4HANA SupplierInvoice fields onto APState."""
    status = str(_pick(invoice, "InvoiceStatus", "DocumentStatus", "status") or "").strip().lower()
    if "paid" in status or "cleared" in status:
        return APState.CLOSED.value
    if "reverse" in status or "cancel" in status:
        return APState.CLOSED.value
    payment_block = _pick(invoice, "PaymentBlockingReason", "PaymentBlock", "ZLSPR")
    if payment_block and str(payment_block).strip() not in {"", " ", "0"}:
        return APState.NEEDS_APPROVAL.value
    return APState.POSTED_TO_ERP.value


def _ap_item_payload_from_invoice(
    *,
    organization_id: str,
    invoice: Dict[str, Any],
    envelope: Dict[str, Any],
    composite_key: str,
    state: str,
    event_type: str,
) -> Dict[str, Any]:
    cc = _pick(invoice, "CompanyCode", "companyCode", "BUKRS", "company_code")
    doc = _pick(invoice, "SupplierInvoice", "supplierInvoice", "BELNR", "supplier_invoice")
    fy = _pick(invoice, "FiscalYear", "fiscalYear", "GJAHR", "fiscal_year")

    metadata = {
        "source": "sap_native",
        "sap_company_code": cc,
        "sap_supplier_invoice": doc,
        "sap_fiscal_year": fy,
        "sap_status": _pick(invoice, "InvoiceStatus", "DocumentStatus", "status"),
        "sap_payment_block": _pick(invoice, "PaymentBlockingReason", "PaymentBlock", "ZLSPR"),
        "sap_intake_event": event_type,
        "sap_event_id": envelope.get("id") or envelope.get("event_id"),
    }
    metadata = {k: v for k, v in metadata.items() if v not in (None, "")}

    amount_raw = _pick(invoice, "InvoiceGrossAmount", "GrossAmount", "amount", "WRBTR")
    try:
        amount_val = float(amount_raw) if amount_raw not in (None, "") else None
    except (TypeError, ValueError):
        amount_val = None

    vendor_name = _pick(invoice, "SupplierName", "supplier_name", "VendorName") or "Unknown supplier"
    vendor_id = _pick(invoice, "Supplier", "supplier", "LIFNR") or vendor_name
    invoice_number = _pick(invoice, "SupplierInvoiceIDByInvcgParty", "invoice_number") or doc

    return {
        "thread_id": None,
        "message_id": None,
        "subject": f"SAP Supplier Invoice {invoice_number} — {vendor_name}",
        "sender": f"{vendor_name} <sap-s4hana@erp-native>",
        "vendor_name": vendor_name,
        "amount": amount_val,
        "currency": (_pick(invoice, "DocumentCurrency", "Currency", "WAERS") or "USD").upper(),
        "invoice_number": invoice_number,
        "invoice_date": _pick(invoice, "InvoiceDate", "DocumentDate", "BLDAT"),
        "due_date": _pick(invoice, "NetDueDate", "due_date"),
        "state": state,
        "confidence": 1.0,
        "approval_required": state == APState.NEEDS_APPROVAL.value,
        "erp_reference": composite_key,
        "erp_posted_at": datetime.now(timezone.utc).isoformat() if state == APState.POSTED_TO_ERP.value else None,
        "organization_id": organization_id,
        "approval_surface": "slack",
        "metadata": metadata,
        "document_type": "invoice",
    }


# ─── Slack routing trigger (mirrors NetSuite dispatcher) ───────────


def _route_for_approval_async(ap_item: Dict[str, Any]) -> None:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    if not ap_item:
        return
    try:
        from clearledgr.services.erp_native_approval import route_for_approval
    except Exception as exc:  # noqa: BLE001
        logger.warning("sap_webhook_dispatch: approval module import failed — %s", exc)
        return

    async def _runner():
        try:
            await route_for_approval(ap_item)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sap_webhook_dispatch: route_for_approval raised for ap_item=%s — %s",
                ap_item.get("id"), exc,
            )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_runner())
    except RuntimeError:
        with ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(asyncio.run, _runner())


# ─── Audit ──────────────────────────────────────────────────────────


def _record_intake_audit(
    *,
    organization_id: str,
    ap_item_id: str,
    envelope: Dict[str, Any],
    action: str,
    target_state: str,
    composite_key: str,
) -> None:
    if not ap_item_id:
        return
    db = get_db()
    if not hasattr(db, "record_audit_event"):
        return
    try:
        db.record_audit_event(
            actor_id="sap_s4hana",
            actor_type="erp_webhook",
            action=f"erp_native_intake.{action}",
            box_id=ap_item_id,
            box_type="ap_item",
            entity_type="ap_item",
            entity_id=ap_item_id,
            organization_id=organization_id,
            metadata={
                "target_state": target_state,
                "event_type": envelope.get("type") or envelope.get("event_type"),
                "event_id": envelope.get("id") or envelope.get("event_id"),
                "composite_key": composite_key,
                "source": "sap_native",
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sap_webhook_dispatch: audit write failed for %s — %s",
            ap_item_id, exc,
        )
