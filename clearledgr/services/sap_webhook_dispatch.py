"""Dispatch handlers for SAP S/4HANA SupplierInvoice events.

Triggered via:

* **S/4HANA Cloud**: BTP Event Mesh subscribes to standard SAP
  business events (``sap.s4.beh.supplierinvoice.v1.SupplierInvoice.Created``,
  ``…Posted``, ``…Blocked``, ``…Released``, ``…Cancelled``,
  ``…Reversed``, ``…Paid``, ``…PaymentExecuted``) and forwards
  CloudEvents into Clearledgr's webhook at
  ``/erp/webhooks/sap/{organization_id}``.
* **S/4HANA on-premise**: an ABAP enhancement (BAdI on ``BUS2081``
  or ``MIRO`` post-save) fires the same payload shape via HTTPS
  through SAP Cloud Connector to the same endpoint.

The webhook handler in :mod:`clearledgr.api.erp_webhooks` verifies
the HMAC signature and then awaits :func:`dispatch_sap_event` to:

1. **`Created` / `Posted`**: fetch enrichment context from S/4HANA
   (supplier master, full supplier-invoice with items + GL
   distribution + PO linkage, linked PurchaseOrder + items, material
   documents = GRNs, supplier bank history). Upsert PO + GRs into
   Clearledgr's stores. Build :class:`InvoiceData` with
   ``erp_native=True`` and call
   ``InvoiceWorkflowService.process_new_invoice`` — the bill runs
   through the same vendor master gate, confidence gate, 3-way
   match, vendor fraud checks, AP Decision, and per-amount Slack
   approval routing as a Gmail-arrived bill.
2. **`Blocked` / `Released` / `Updated`**: lightweight state
   reconcile — refresh ERP-side fields, apply any valid state
   transition, but don't re-run the full pipeline (avoids
   re-routing approvals on every SAP-side header edit).
3. **`Paid` / `PaymentExecuted`**: transition Box to ``closed``.
4. **`Cancelled` / `Reversed`**: transition Box to ``closed`` with
   metadata note.

Composite key is ``CompanyCode + SupplierInvoice + FiscalYear`` —
serialized as ``"<CC>/<DocNum>/<FY>"`` and stored in
``ap_items.erp_reference`` for idempotent lookup.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.ap_states import APState, validate_transition
from clearledgr.core.database import get_db
from clearledgr.services.invoice_models import InvoiceData

logger = logging.getLogger(__name__)


# ─── Public entrypoint ──────────────────────────────────────────────


async def dispatch_sap_event(
    organization_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Route a verified SAP S/4HANA event payload to the right handler."""
    event_type, invoice = _normalize_event(payload)
    if not invoice:
        return {"ok": False, "reason": "missing_invoice_payload", "event_type": event_type}

    composite_key = _composite_key(invoice)
    if not composite_key:
        return {"ok": False, "reason": "missing_composite_key", "event_type": event_type}

    if event_type in {"created", "posted"}:
        return await _handle_create_or_posted(organization_id, payload, invoice, composite_key, event_type)
    if event_type in {"blocked", "released", "updated"}:
        return await _handle_update(organization_id, payload, invoice, composite_key)
    if event_type in {"paid", "payment_executed"}:
        return await _handle_paid(organization_id, payload, invoice, composite_key)
    if event_type in {"cancelled", "reversed"}:
        return await _handle_cancelled(organization_id, payload, invoice, composite_key)
    return {"ok": True, "reason": "ignored_event", "event_type": event_type}


# ─── Event normalization ───────────────────────────────────────────


_CLOUDEVENTS_SUFFIX_MAP = {
    "Created": "created", "Posted": "posted", "Blocked": "blocked",
    "Released": "released", "Cancelled": "cancelled", "Reversed": "cancelled",
    "Paid": "paid", "PaymentExecuted": "payment_executed", "Updated": "updated",
}


def _normalize_event(payload: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    raw_type = str(payload.get("type") or payload.get("event_type") or "").strip()
    invoice = payload.get("invoice") or payload.get("data") or {}
    if isinstance(invoice, dict) and "data" in invoice and isinstance(invoice["data"], dict):
        invoice = invoice["data"]
    event_type = ""
    if raw_type:
        if raw_type.startswith("sap."):
            for suffix, mapped in _CLOUDEVENTS_SUFFIX_MAP.items():
                if f".{suffix}." in raw_type or raw_type.endswith(f".{suffix}"):
                    event_type = mapped
                    break
        elif raw_type.startswith("supplier_invoice."):
            event_type = raw_type.split(".", 1)[1].lower()
        elif "." in raw_type:
            event_type = raw_type.rsplit(".", 1)[1].lower()
        else:
            event_type = raw_type.lower()
    return event_type, invoice if isinstance(invoice, dict) else {}


def _composite_key(invoice: Dict[str, Any]) -> Optional[str]:
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


async def _handle_create_or_posted(
    organization_id: str, envelope: Dict[str, Any],
    invoice: Dict[str, Any], composite_key: str, event_type: str,
) -> Dict[str, Any]:
    """Full-pipeline path: fetch enrichment + run process_new_invoice."""
    db = get_db()
    existing = db.get_ap_item_by_erp_reference(organization_id, composite_key)
    if existing:
        return await _handle_update(organization_id, envelope, invoice, composite_key, existing=existing)

    # Composite key parts for downstream calls
    parts = composite_key.split("/")
    if len(parts) != 3:
        return {"ok": False, "reason": "malformed_composite_key", "composite_key": composite_key}
    cc, doc, fy = parts

    # ── Enrich from S/4HANA ──
    try:
        from clearledgr.integrations.erp_sap_s4hana_intake import fetch_intake_context
        intake = await fetch_intake_context(
            organization_id=organization_id,
            company_code=cc, supplier_invoice=doc, fiscal_year=fy,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sap_webhook_dispatch: enrichment fetch failed for %s — %s; falling back to thin intake",
            composite_key, exc,
        )
        return _thin_intake(organization_id, envelope, invoice, composite_key, event_type)

    if not intake.get("bill_header"):
        return _thin_intake(organization_id, envelope, invoice, composite_key, event_type)

    # ── Upsert linked PO + material documents (GRNs) into Clearledgr stores ──
    if intake.get("linked_po"):
        try:
            from clearledgr.services.erp_intake_po_sync import upsert_sap_po
            upsert_sap_po(
                organization_id=organization_id,
                po_payload=intake["linked_po"],
                po_lines=intake.get("linked_po_lines") or [],
                material_documents=intake.get("material_documents") or [],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sap_webhook_dispatch: PO/GR upsert failed for %s — %s "
                "(pipeline will continue with reduced 3-way-match coverage)",
                composite_key, exc,
            )

    # ── Build InvoiceData from enrichment ──
    invoice_data = _build_invoice_data_from_intake(
        organization_id=organization_id,
        envelope=envelope,
        intake=intake,
        composite_key=composite_key,
        event_type=event_type,
    )

    # ── Run the full coordination pipeline ──
    try:
        from clearledgr.services.invoice_workflow import get_invoice_workflow
        workflow = get_invoice_workflow(organization_id)
        result = await workflow.process_new_invoice(invoice_data)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "sap_webhook_dispatch: process_new_invoice raised for %s — %s",
            composite_key, exc, exc_info=True,
        )
        return {"ok": False, "reason": "pipeline_failed", "error": str(exc)}

    ap_item_id = _resolve_ap_item_id_from_pipeline_result(db, invoice_data, result)
    if ap_item_id:
        try:
            db.update_ap_item(
                ap_item_id, erp_reference=composite_key,
                _actor_type="erp_webhook", _actor_id="sap_s4hana",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sap_webhook_dispatch: failed to stamp erp_reference on ap_item=%s — %s",
                ap_item_id, exc,
            )

    _record_intake_audit(
        organization_id=organization_id, ap_item_id=ap_item_id or "",
        envelope=envelope, action="created", target_state=str(result.get("state") or ""),
        composite_key=composite_key,
    )
    return {
        "ok": True, "action": "created",
        "ap_item_id": ap_item_id, "state": result.get("state"),
        "pipeline_status": result.get("status"), "pipeline_reason": result.get("reason"),
        "composite_key": composite_key,
    }


async def _handle_update(
    organization_id: str, envelope: Dict[str, Any],
    invoice: Dict[str, Any], composite_key: str,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    db = get_db()
    if existing is None:
        existing = db.get_ap_item_by_erp_reference(organization_id, composite_key)
    if not existing:
        return await _handle_create_or_posted(organization_id, envelope, invoice, composite_key, "posted")

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
            return {
                "ok": False, "action": "update_failed",
                "ap_item_id": ap_item_id, "reason": str(exc),
            }

    target_state_for_audit = field_updates.get("state") or current_state
    _record_intake_audit(
        organization_id=organization_id, ap_item_id=ap_item_id,
        envelope=envelope, action="updated", target_state=target_state_for_audit,
        composite_key=composite_key,
    )
    return {
        "ok": True, "action": "updated",
        "ap_item_id": ap_item_id, "state": target_state_for_audit,
        "composite_key": composite_key,
    }


async def _handle_paid(
    organization_id: str, envelope: Dict[str, Any],
    invoice: Dict[str, Any], composite_key: str,
) -> Dict[str, Any]:
    db = get_db()
    existing = db.get_ap_item_by_erp_reference(organization_id, composite_key)
    if not existing:
        invoice_with_paid = dict(invoice)
        invoice_with_paid.setdefault("InvoiceStatus", "Paid")
        return await _handle_create_or_posted(organization_id, envelope, invoice_with_paid, composite_key, "paid")
    ap_item_id = str(existing.get("id") or "").strip()
    current_state = str(existing.get("state") or "").strip().lower()
    if current_state == APState.CLOSED.value:
        return {"ok": True, "action": "noop_already_closed", "ap_item_id": ap_item_id}
    if not validate_transition(current_state, APState.CLOSED.value):
        return {"ok": False, "action": "invalid_transition", "from": current_state, "to": APState.CLOSED.value}
    try:
        db.update_ap_item(
            ap_item_id, state=APState.CLOSED.value,
            _actor_type="erp_webhook", _actor_id="sap_s4hana",
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "close_failed", "ap_item_id": ap_item_id, "error": str(exc)}
    _record_intake_audit(
        organization_id=organization_id, ap_item_id=ap_item_id,
        envelope=envelope, action="paid_closed", target_state=APState.CLOSED.value,
        composite_key=composite_key,
    )
    return {"ok": True, "action": "closed", "ap_item_id": ap_item_id, "state": APState.CLOSED.value}


async def _handle_cancelled(
    organization_id: str, envelope: Dict[str, Any],
    invoice: Dict[str, Any], composite_key: str,
) -> Dict[str, Any]:
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
                ap_item_id, state=APState.CLOSED.value,
                _actor_type="erp_webhook", _actor_id="sap_s4hana",
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "action": "close_failed", "ap_item_id": ap_item_id}
    _record_intake_audit(
        organization_id=organization_id, ap_item_id=ap_item_id,
        envelope=envelope, action="cancelled_in_erp", target_state=APState.CLOSED.value,
        composite_key=composite_key,
    )
    return {"ok": True, "action": "closed_via_cancel", "ap_item_id": ap_item_id}


# ─── InvoiceData construction from enrichment ──────────────────────


def _build_invoice_data_from_intake(
    *,
    organization_id: str, envelope: Dict[str, Any],
    intake: Dict[str, Any], composite_key: str, event_type: str,
) -> InvoiceData:
    header = intake.get("bill_header") or {}
    bill_lines = intake.get("bill_lines") or []
    vendor = intake.get("vendor") or {}
    bank_history = intake.get("vendor_bank_history") or []

    cc, doc, fy = composite_key.split("/")

    vendor_email = ""
    if isinstance(vendor, dict):
        vendor_email = str(vendor.get("EmailAddress") or vendor.get("email") or "").strip()
    sender = vendor_email or f"{header.get('supplier_name') or 'vendor'} <sap-s4hana@erp-native>"

    primary_bank = next(
        (b for b in bank_history if b.get("is_default")),
        bank_history[0] if bank_history else None,
    )
    bank_details = None
    if primary_bank:
        bank_details = {k: v for k, v in {
            "iban": primary_bank.get("iban"),
            "account_number": primary_bank.get("account_number"),
            "swift": primary_bank.get("swift"),
            "bank_name": primary_bank.get("bank_name"),
        }.items() if v}

    line_items: List[Dict[str, Any]] = []
    for line in bill_lines:
        line_items.append({
            "description": line.get("description") or "",
            "quantity": _safe_float(line.get("quantity")),
            "unit_price": _safe_float(line.get("unit_price")),
            "amount": _safe_float(line.get("amount")),
            "gl_code": line.get("gl_code"),
            "tax_amount": _safe_float(line.get("tax_amount")),
        })

    po_number = ""
    for line in bill_lines:
        candidate = str(line.get("purchase_order") or "").strip()
        if candidate:
            po_number = candidate
            break

    field_confidences = {
        "vendor_name": 1.0, "amount": 1.0, "currency": 1.0,
        "invoice_number": 1.0, "invoice_date": 1.0, "due_date": 1.0,
        "po_number": 1.0 if po_number else 0.0,
    }

    erp_metadata = {
        "company_code": cc,
        "supplier_invoice": doc,
        "fiscal_year": fy,
        "supplier_id": header.get("supplier"),
        "supplier_name": header.get("supplier_name"),
        "payment_blocking_reason": header.get("payment_block"),
        "sap_status": header.get("status"),
        "sap_intake_event": event_type,
        "sap_event_id": envelope.get("id") or envelope.get("event_id"),
        "po_numbers": list({str(line.get("purchase_order") or "").strip() for line in bill_lines if line.get("purchase_order")}),
        "material_doc_ids": [
            f"{md.get('MaterialDocument','')}/{md.get('MaterialDocumentYear','')}"
            for md in (intake.get("material_documents") or [])
            if isinstance(md, dict)
        ],
    }
    erp_metadata = {k: v for k, v in erp_metadata.items() if v not in (None, "", [])}

    return InvoiceData(
        source_type="sap_s4hana",
        source_id=composite_key,
        erp_native=True,
        erp_metadata=erp_metadata,
        subject=f"SAP Supplier Invoice {header.get('invoice_number') or doc} — {header.get('supplier_name') or 'vendor'}",
        sender=sender,
        vendor_name=header.get("supplier_name") or "Unknown supplier",
        amount=_safe_float(header.get("amount")) or 0.0,
        currency=str(header.get("currency") or "USD").upper(),
        invoice_number=str(header.get("invoice_number") or doc).strip() or doc,
        due_date=str(header.get("due_date") or "").strip() or None,
        po_number=po_number or None,
        confidence=1.0,
        bank_details=bank_details,
        line_items=line_items or None,
        field_confidences=field_confidences,
        organization_id=organization_id,
        correlation_id=f"erp-intake:{envelope.get('event_id') or envelope.get('id') or composite_key}",
        tax_amount=_safe_float(header.get("tax_amount")) or None,
    )


# ─── Helpers ────────────────────────────────────────────────────────


def _safe_float(value: Any, *, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_ap_item_id_from_pipeline_result(
    db: Any, invoice: InvoiceData, result: Dict[str, Any],
) -> str:
    candidate = str(result.get("ap_item_id") or "").strip()
    if candidate:
        return candidate
    if hasattr(db, "get_invoice_status"):
        try:
            row = db.get_invoice_status(invoice.gmail_id)
            if row:
                return str(row.get("ap_item_id") or "").strip()
        except Exception:
            pass
    return ""


def _state_from_invoice(invoice: Dict[str, Any]) -> str:
    """Lightweight state derivation for the update / paid / cancelled
    paths (NOT the create path — create runs the full pipeline)."""
    status = str(_pick(invoice, "InvoiceStatus", "DocumentStatus", "status") or "").strip().lower()
    if "paid" in status or "cleared" in status:
        return APState.CLOSED.value
    if "reverse" in status or "cancel" in status:
        return APState.CLOSED.value
    payment_block = _pick(invoice, "PaymentBlockingReason", "PaymentBlock", "ZLSPR")
    if payment_block and str(payment_block).strip() not in {"", " ", "0"}:
        return APState.NEEDS_APPROVAL.value
    return APState.POSTED_TO_ERP.value


def _thin_intake(
    organization_id: str, envelope: Dict[str, Any],
    invoice: Dict[str, Any], composite_key: str, event_type: str,
) -> Dict[str, Any]:
    """Fallback when enrichment fails (no S/4HANA connection, OData
    unreachable, JWKS misconfig)."""
    db = get_db()
    initial_state = _state_from_invoice(invoice)
    cc, doc, fy = composite_key.split("/")
    payload = {
        "thread_id": None,
        "subject": f"SAP Supplier Invoice {doc} — {_pick(invoice, 'SupplierName', 'supplier_name') or 'vendor'}",
        "sender": f"{_pick(invoice, 'SupplierName', 'supplier_name') or 'vendor'} <sap-s4hana@erp-native>",
        "vendor_name": _pick(invoice, "SupplierName", "supplier_name") or "Unknown supplier",
        "amount": _safe_float(_pick(invoice, "InvoiceGrossAmount", "GrossAmount", "amount", "WRBTR")),
        "currency": (_pick(invoice, "DocumentCurrency", "Currency", "WAERS") or "USD").upper(),
        "invoice_number": _pick(invoice, "SupplierInvoiceIDByInvcgParty", "invoice_number") or doc,
        "invoice_date": _pick(invoice, "InvoiceDate", "DocumentDate", "BLDAT"),
        "due_date": _pick(invoice, "NetDueDate", "due_date"),
        "state": initial_state,
        "confidence": 1.0,
        "approval_required": initial_state == APState.NEEDS_APPROVAL.value,
        "erp_reference": composite_key,
        "erp_posted_at": datetime.now(timezone.utc).isoformat() if initial_state == APState.POSTED_TO_ERP.value else None,
        "organization_id": organization_id,
        "approval_surface": "slack",
        "metadata": {
            "source": "sap_native",
            "sap_company_code": cc,
            "sap_supplier_invoice": doc,
            "sap_fiscal_year": fy,
            "sap_intake_event": event_type,
            "fallback_thin_intake": True,
            "fallback_reason": "enrichment_unavailable",
        },
        "document_type": "invoice",
    }
    item = db.create_ap_item(payload)
    ap_item_id = str((item or {}).get("id") or "").strip()
    _record_intake_audit(
        organization_id=organization_id, ap_item_id=ap_item_id,
        envelope=envelope, action="created_thin_intake_fallback",
        target_state=initial_state, composite_key=composite_key,
    )
    return {
        "ok": True, "action": "created_thin",
        "ap_item_id": ap_item_id, "state": initial_state,
        "fallback": "no_enrichment", "composite_key": composite_key,
    }


def _record_intake_audit(
    *,
    organization_id: str, ap_item_id: str, envelope: Dict[str, Any],
    action: str, target_state: str, composite_key: str,
) -> None:
    if not ap_item_id:
        return
    db = get_db()
    if not hasattr(db, "record_audit_event"):
        return
    try:
        db.record_audit_event(
            actor_id="sap_s4hana", actor_type="erp_webhook",
            action=f"erp_native_intake.{action}",
            box_id=ap_item_id, box_type="ap_item",
            entity_type="ap_item", entity_id=ap_item_id,
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
