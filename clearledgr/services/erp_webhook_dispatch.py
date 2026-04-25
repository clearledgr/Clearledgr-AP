"""Dispatch handlers for ERP-native bill events (NetSuite).

The webhook endpoint in :mod:`clearledgr.api.erp_webhooks` verifies
the HMAC signature and records an audit event, then awaits this
module's :func:`dispatch_netsuite_event` to do the real work:

1. **On `vendorbill.create`**: fetch enrichment context from NetSuite
   (vendor record, full bill with line items + GL distribution,
   linked PO + lines, item-receipts as GRN equivalents, vendor bank
   history). Upsert PO + GRs into Clearledgr's own stores. Build an
   :class:`InvoiceData` with ``erp_native=True`` and the channel
   fields populated. Call ``InvoiceWorkflowService.process_new_invoice``
   — the bill runs through the same vendor master gate, confidence
   gate, 3-way match, vendor fraud checks, AP Decision, and Slack
   approval routing as a Gmail-arrived bill. The ``erp_native`` flag
   makes ``InvoicePostingMixin._post_to_erp`` short-circuit to
   "already posted by ERP" — no duplicate writes back into NetSuite.

2. **On `vendorbill.update`**: the bill changed in NetSuite. Re-fetch
   enrichment, re-derive state from the payment-block / status, apply
   any valid transition. We do NOT re-run the full pipeline — that
   would re-route the same bill through approval every time the AP
   clerk edits the memo field. Instead we update fields and apply
   state transitions only.

3. **On `vendorbill.paid`**: transition Box to ``closed``.

4. **On `vendorbill.delete`**: transition Box to ``closed`` with a
   metadata note.

Idempotency: the AP-item lookup keys off
``erp_reference == ns_internal_id``. Replays of the same event are
no-ops. Out-of-order delivery (paid before created) synthesizes a
create from the paid payload.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from clearledgr.core.ap_states import APState, validate_transition
from clearledgr.core.database import get_db
from clearledgr.integrations.erp_router import _erp_connection_from_row
from clearledgr.services.invoice_models import InvoiceData

logger = logging.getLogger(__name__)


# ─── Public entrypoint ──────────────────────────────────────────────


async def dispatch_netsuite_event(
    organization_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Route a verified NetSuite webhook payload to the right handler.

    Returns a small status dict for the webhook response body —
    primarily for our own logs since NetSuite only cares about the
    status code.
    """
    event_type = str(payload.get("event_type") or "").strip().lower()
    bill = payload.get("bill") or {}
    ns_internal_id = str(bill.get("ns_internal_id") or "").strip()
    if not ns_internal_id:
        return {"ok": False, "reason": "missing_ns_internal_id"}

    if event_type == "vendorbill.create":
        return await _handle_create(organization_id, payload, bill, ns_internal_id)
    if event_type == "vendorbill.update":
        return await _handle_update(organization_id, payload, bill, ns_internal_id)
    if event_type == "vendorbill.paid":
        return await _handle_paid(organization_id, payload, bill, ns_internal_id)
    if event_type == "vendorbill.delete":
        return await _handle_delete(organization_id, payload, bill, ns_internal_id)
    return {"ok": True, "reason": "ignored_event", "event_type": event_type}


# ─── Handlers ───────────────────────────────────────────────────────


async def _handle_create(
    organization_id: str,
    envelope: Dict[str, Any],
    bill: Dict[str, Any],
    ns_internal_id: str,
) -> Dict[str, Any]:
    """Full-pipeline path: fetch enrichment + run process_new_invoice."""
    db = get_db()
    existing = db.get_ap_item_by_erp_reference(organization_id, ns_internal_id)
    if existing:
        # Replay or out-of-order delivery — we already created this Box.
        # Forward to update path so any state drift since last sync is
        # reconciled.
        return await _handle_update(
            organization_id, envelope, bill, ns_internal_id, existing=existing,
        )

    connection = _resolve_netsuite_connection(db, organization_id)
    if connection is None:
        # No NetSuite connection on this org — fall back to the thin
        # path so we at least record the bill arrived. Validation is
        # impossible without ERP read access, so the Box enters at
        # `posted_to_erp` (the bill IS in NetSuite) without further
        # coordination.
        logger.warning(
            "erp_webhook_dispatch: no NetSuite connection for org=%s — falling back to thin intake",
            organization_id,
        )
        return _thin_intake(organization_id, envelope, bill, ns_internal_id)

    # ── Enrich from NetSuite ──
    try:
        from clearledgr.integrations.erp_netsuite_intake import fetch_intake_context
        intake = await fetch_intake_context(connection, ns_internal_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "erp_webhook_dispatch: enrichment fetch failed for ns=%s — %s; falling back to thin intake",
            ns_internal_id, exc,
        )
        return _thin_intake(organization_id, envelope, bill, ns_internal_id)

    if not intake.get("bill_header"):
        logger.warning(
            "erp_webhook_dispatch: no bill_header from enrichment for ns=%s — falling back to thin intake",
            ns_internal_id,
        )
        return _thin_intake(organization_id, envelope, bill, ns_internal_id)

    # ── Upsert linked PO + GRs into Clearledgr stores ──
    if intake.get("linked_po"):
        try:
            from clearledgr.services.erp_intake_po_sync import upsert_netsuite_po
            upsert_netsuite_po(
                organization_id=organization_id,
                po_payload=intake["linked_po"],
                po_lines=intake.get("linked_po_lines") or [],
                item_receipts=intake.get("goods_receipts") or [],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "erp_webhook_dispatch: PO/GR upsert failed for ns=%s — %s "
                "(pipeline will continue with reduced 3-way-match coverage)",
                ns_internal_id, exc,
            )

    # ── Build InvoiceData from enrichment + envelope ──
    invoice = _build_invoice_data_from_intake(
        organization_id=organization_id,
        envelope=envelope,
        intake=intake,
        bill_summary=bill,
        ns_internal_id=ns_internal_id,
    )

    # ── Run the full coordination pipeline ──
    try:
        from clearledgr.services.invoice_workflow import get_invoice_workflow
        workflow = get_invoice_workflow(organization_id)
        result = await workflow.process_new_invoice(invoice)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "erp_webhook_dispatch: process_new_invoice raised for ns=%s — %s",
            ns_internal_id, exc, exc_info=True,
        )
        return {"ok": False, "reason": "pipeline_failed", "error": str(exc)}

    # ── Stamp the resulting AP item with ERP linkage so subsequent
    # update / paid / delete events can find it via the existing
    # erp_reference key. ──
    ap_item_id = _resolve_ap_item_id_from_pipeline_result(db, invoice, result)
    if ap_item_id:
        try:
            db.update_ap_item(
                ap_item_id,
                erp_reference=ns_internal_id,
                _actor_type="erp_webhook",
                _actor_id="netsuite",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "erp_webhook_dispatch: failed to stamp erp_reference on ap_item=%s — %s",
                ap_item_id, exc,
            )

    _record_intake_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id or "",
        envelope=envelope,
        action="created",
        target_state=str(result.get("state") or ""),
    )
    return {
        "ok": True,
        "action": "created",
        "ap_item_id": ap_item_id,
        "state": result.get("state"),
        "pipeline_status": result.get("status"),
        "pipeline_reason": result.get("reason"),
    }


async def _handle_update(
    organization_id: str,
    envelope: Dict[str, Any],
    bill: Dict[str, Any],
    ns_internal_id: str,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Lightweight: refresh ERP-side fields + apply valid state transition.
    Does NOT re-run the full pipeline (avoids re-routing approvals on
    every NetSuite memo edit). For payment-block-added-after-creation
    cases the state derivation flips to needs_approval and the
    GmailLabelObserver / approval routing fires through the existing
    state-transition machinery."""
    db = get_db()
    if existing is None:
        existing = db.get_ap_item_by_erp_reference(organization_id, ns_internal_id)
    if not existing:
        return await _handle_create(organization_id, envelope, bill, ns_internal_id)

    ap_item_id = str(existing.get("id") or "").strip()
    current_state = str(existing.get("state") or "").strip().lower()
    desired_state = _state_from_bill(bill)

    field_updates = {
        k: v for k, v in {
            "vendor_name": bill.get("entity_name") or bill.get("entity_id"),
            "amount": bill.get("amount"),
            "currency": (str(bill.get("currency") or "").upper() or None),
            "invoice_number": bill.get("invoice_number"),
            "due_date": bill.get("due_date"),
        }.items()
        if v not in (None, "")
    }

    if desired_state != current_state and validate_transition(current_state, desired_state):
        field_updates["state"] = desired_state
        field_updates["_actor_type"] = "erp_webhook"
        field_updates["_actor_id"] = "netsuite"

    if field_updates:
        try:
            db.update_ap_item(ap_item_id, **field_updates)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "erp_webhook_dispatch: update failed ap_item=%s ns_id=%s — %s",
                ap_item_id, ns_internal_id, exc,
            )
            return {
                "ok": False, "action": "update_failed",
                "ap_item_id": ap_item_id, "reason": str(exc),
            }

    target_state_for_audit = field_updates.get("state") or current_state
    _record_intake_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        envelope=envelope,
        action="updated",
        target_state=target_state_for_audit,
    )
    return {
        "ok": True, "action": "updated",
        "ap_item_id": ap_item_id, "state": target_state_for_audit,
        "fields_updated": [k for k in field_updates.keys() if not k.startswith("_")],
    }


async def _handle_paid(
    organization_id: str,
    envelope: Dict[str, Any],
    bill: Dict[str, Any],
    ns_internal_id: str,
) -> Dict[str, Any]:
    db = get_db()
    existing = db.get_ap_item_by_erp_reference(organization_id, ns_internal_id)
    if not existing:
        envelope_with_paid_bill = dict(envelope)
        envelope_with_paid_bill["bill"] = {**bill, "status_label": "Paid In Full"}
        return await _handle_create(
            organization_id, envelope_with_paid_bill, envelope_with_paid_bill["bill"], ns_internal_id,
        )
    ap_item_id = str(existing.get("id") or "").strip()
    current_state = str(existing.get("state") or "").strip().lower()
    if current_state == APState.CLOSED.value:
        return {"ok": True, "action": "noop_already_closed", "ap_item_id": ap_item_id}
    if not validate_transition(current_state, APState.CLOSED.value):
        # Race condition: bill was paid in NetSuite while Clearledgr's
        # Box was in a state that doesn't normally close (e.g. NEEDS_INFO
        # → CLOSED is allowed; NEEDS_APPROVAL → CLOSED is allowed). If
        # we land here it's a real state-machine gap; surface clearly.
        logger.warning(
            "erp_webhook_dispatch: paid event but %s → closed is not a valid transition (ap_item=%s)",
            current_state, ap_item_id,
        )
        return {
            "ok": False, "action": "invalid_transition",
            "ap_item_id": ap_item_id, "from": current_state,
        }
    try:
        db.update_ap_item(
            ap_item_id, state=APState.CLOSED.value,
            _actor_type="erp_webhook", _actor_id="netsuite",
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "close_failed", "ap_item_id": ap_item_id, "error": str(exc)}
    _record_intake_audit(
        organization_id=organization_id, ap_item_id=ap_item_id,
        envelope=envelope, action="paid_closed", target_state=APState.CLOSED.value,
    )
    return {"ok": True, "action": "closed", "ap_item_id": ap_item_id, "state": APState.CLOSED.value}


async def _handle_delete(
    organization_id: str,
    envelope: Dict[str, Any],
    bill: Dict[str, Any],
    ns_internal_id: str,
) -> Dict[str, Any]:
    db = get_db()
    existing = db.get_ap_item_by_erp_reference(organization_id, ns_internal_id)
    if not existing:
        return {"ok": True, "action": "noop_no_box", "ns_internal_id": ns_internal_id}
    ap_item_id = str(existing.get("id") or "").strip()
    current_state = str(existing.get("state") or "").strip().lower()
    if current_state in {APState.CLOSED.value, APState.REJECTED.value, APState.REVERSED.value}:
        return {"ok": True, "action": "noop_terminal", "ap_item_id": ap_item_id, "state": current_state}
    if validate_transition(current_state, APState.CLOSED.value):
        try:
            db.update_ap_item(
                ap_item_id, state=APState.CLOSED.value,
                _actor_type="erp_webhook", _actor_id="netsuite",
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "action": "close_failed", "ap_item_id": ap_item_id}
    _record_intake_audit(
        organization_id=organization_id, ap_item_id=ap_item_id,
        envelope=envelope, action="deleted_in_erp", target_state=APState.CLOSED.value,
    )
    return {"ok": True, "action": "closed_via_delete", "ap_item_id": ap_item_id}


# ─── InvoiceData construction from enrichment ──────────────────────


def _build_invoice_data_from_intake(
    *,
    organization_id: str,
    envelope: Dict[str, Any],
    intake: Dict[str, Any],
    bill_summary: Dict[str, Any],
    ns_internal_id: str,
) -> InvoiceData:
    """Map NetSuite enrichment payload onto an InvoiceData ready for
    process_new_invoice. ERP-native flag set; field_confidences seeded
    at 1.0 since the ERP-extracted values are authoritative."""
    header = intake.get("bill_header") or {}
    bill_lines = intake.get("bill_lines") or []
    expense_lines = intake.get("expense_lines") or []
    vendor = intake.get("vendor") or {}
    bank_history = intake.get("vendor_bank_history") or []

    # Pull primary vendor email from the vendor record if available;
    # otherwise fall back to the synthetic erp-native sender so the
    # vendor-domain trust observer can be turned back on later
    # without code changes (see Phase A guard).
    vendor_email = ""
    if isinstance(vendor, dict):
        vendor_email = str(vendor.get("email") or "").strip()
    sender = vendor_email or f"{header.get('vendor_name') or 'vendor'} <netsuite@erp-native>"

    # Bank details — most-recent / default entry from the vendor record.
    primary_bank = next(
        (b for b in bank_history if b.get("is_default")),
        bank_history[0] if bank_history else None,
    )
    bank_details = None
    if primary_bank:
        bank_details = {
            "iban": primary_bank.get("iban"),
            "account_number": primary_bank.get("account_number"),
            "swift": primary_bank.get("swift"),
            "bank_name": primary_bank.get("bank_name"),
        }
        bank_details = {k: v for k, v in bank_details.items() if v}

    # Line items — combine item lines + expense lines into the
    # uniform shape the validation pipeline expects.
    line_items: list = []
    for line in bill_lines:
        line_items.append({
            "description": line.get("description") or line.get("item_name") or "",
            "quantity": _safe_float(line.get("quantity")),
            "unit_price": _safe_float(line.get("unit_price")),
            "amount": _safe_float(line.get("amount")),
            "gl_code": line.get("gl_code"),
            "tax_amount": _safe_float(line.get("tax_amount")),
        })
    for exp in expense_lines:
        line_items.append({
            "description": exp.get("description") or "",
            "amount": _safe_float(exp.get("amount")),
            "gl_code": exp.get("gl_code"),
        })

    # PO number — first PO referenced by any line. Multi-PO bills
    # surface as an exception in 3-way match.
    po_number = ""
    for line in bill_lines:
        candidate = str(line.get("po_number") or "").strip()
        if candidate:
            po_number = candidate
            break

    # Field confidences seeded at 1.0 — ERP-extracted fields are
    # authoritative. The confidence gate runs but treats every field
    # as fully trusted.
    field_confidences = {
        "vendor_name": 1.0, "amount": 1.0, "currency": 1.0,
        "invoice_number": 1.0, "invoice_date": 1.0, "due_date": 1.0,
        "po_number": 1.0 if po_number else 0.0,
    }

    erp_metadata = {
        "ns_internal_id": ns_internal_id,
        "ns_account_id": str(envelope.get("account_id") or "").strip(),
        "ns_subsidiary_id": header.get("subsidiary_id"),
        "ns_subsidiary_name": header.get("subsidiary_name"),
        "ns_status": header.get("status"),
        "ns_approval_status": header.get("approval_status"),
        "ns_payment_hold": header.get("payment_hold"),
        "ns_external_id": header.get("external_id"),
        "ns_event_id": envelope.get("event_id"),
        "ns_po_internal_id": (
            (intake.get("linked_po") or {}).get("id")
            if isinstance(intake.get("linked_po"), dict) else None
        ),
        "ns_item_receipt_ids": [
            str((rec or {}).get("id") or "")
            for rec in (intake.get("goods_receipts") or [])
        ],
    }
    erp_metadata = {k: v for k, v in erp_metadata.items() if v not in (None, "")}

    return InvoiceData(
        source_type="netsuite",
        source_id=ns_internal_id,
        erp_native=True,
        erp_metadata=erp_metadata,
        # Other fields
        subject=f"NetSuite Bill {header.get('tran_id') or ns_internal_id} — {header.get('vendor_name') or 'vendor'}",
        sender=sender,
        vendor_name=header.get("vendor_name") or bill_summary.get("entity_name") or "Unknown vendor",
        amount=_safe_float(header.get("amount") or bill_summary.get("amount"), default=0.0),
        currency=str(header.get("currency_id") or bill_summary.get("currency") or "USD").upper(),
        invoice_number=str(header.get("tran_id") or bill_summary.get("invoice_number") or "").strip() or ns_internal_id,
        due_date=str(header.get("due_date") or bill_summary.get("due_date") or "").strip() or None,
        po_number=po_number or None,
        confidence=1.0,
        bank_details=bank_details,
        line_items=line_items or None,
        field_confidences=field_confidences,
        organization_id=organization_id,
        correlation_id=f"erp-intake:{envelope.get('event_id') or ns_internal_id}",
        tax_amount=_safe_float(header.get("tax_amount")) or None,
        subtotal=_safe_float(header.get("subtotal")) or None,
    )


# ─── Helpers ────────────────────────────────────────────────────────


def _safe_float(value: Any, *, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_netsuite_connection(db: Any, organization_id: str):
    if not hasattr(db, "get_erp_connections"):
        return None
    try:
        for row in db.get_erp_connections(organization_id):
            if str(row.get("erp_type") or "").lower() == "netsuite":
                return _erp_connection_from_row(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("erp_webhook_dispatch: connection lookup failed — %s", exc)
    return None


def _resolve_ap_item_id_from_pipeline_result(
    db: Any, invoice: InvoiceData, result: Dict[str, Any]
) -> str:
    candidate = str(result.get("ap_item_id") or "").strip()
    if candidate:
        return candidate
    # Fall back to looking up by gmail_id (which for ERP-native is the
    # synthetic source_id) — that's the canonical idempotency key.
    if hasattr(db, "get_invoice_status"):
        try:
            row = db.get_invoice_status(invoice.gmail_id)
            if row:
                return str(row.get("ap_item_id") or "").strip()
        except Exception:
            pass
    return ""


def _state_from_bill(bill: Dict[str, Any]) -> str:
    """Lightweight state derivation for the update / paid / delete paths
    (NOT the create path — create runs the full pipeline whose
    AP-Decision determines state)."""
    status_label = str(bill.get("status_label") or bill.get("status") or "").strip().lower()
    if "paid" in status_label and "in full" in status_label:
        return APState.CLOSED.value
    payment_hold = str(bill.get("payment_hold") or "").strip().upper()
    if payment_hold in {"T", "TRUE", "Y", "YES", "1"}:
        return APState.NEEDS_APPROVAL.value
    return APState.POSTED_TO_ERP.value


def _thin_intake(
    organization_id: str,
    envelope: Dict[str, Any],
    bill: Dict[str, Any],
    ns_internal_id: str,
) -> Dict[str, Any]:
    """Fallback path for orgs without a configured NetSuite connection.

    We can't enrich (no OAuth tokens) so we can't run the full pipeline.
    Record what we know directly into ap_items so the bill at least
    appears in the queue. This is the pre-Phase-B behaviour preserved
    as a safety net — operators see *something* even when the
    connection is misconfigured.
    """
    db = get_db()
    initial_state = _state_from_bill(bill)
    payload = {
        "thread_id": None,
        "subject": f"NetSuite Bill {bill.get('invoice_number') or bill.get('tran_id') or ns_internal_id} — {bill.get('entity_name') or 'vendor'}",
        "sender": f"{bill.get('entity_name') or 'vendor'} <netsuite@erp-native>",
        "vendor_name": bill.get("entity_name") or bill.get("entity_id") or "Unknown vendor",
        "amount": bill.get("amount"),
        "currency": (str(bill.get("currency") or "USD")).upper(),
        "invoice_number": bill.get("invoice_number") or bill.get("tran_id"),
        "invoice_date": bill.get("tran_date"),
        "due_date": bill.get("due_date"),
        "state": initial_state,
        "confidence": 1.0,
        "approval_required": initial_state == APState.NEEDS_APPROVAL.value,
        "erp_reference": ns_internal_id,
        "erp_posted_at": datetime.now(timezone.utc).isoformat() if initial_state == APState.POSTED_TO_ERP.value else None,
        "organization_id": organization_id,
        "approval_surface": "slack",
        "metadata": {
            "source": "netsuite_native",
            "ns_account_id": str(envelope.get("account_id") or "").strip(),
            "fallback_thin_intake": True,
            "fallback_reason": "no_netsuite_connection_for_org",
        },
        "document_type": "invoice",
    }
    item = db.create_ap_item(payload)
    ap_item_id = str((item or {}).get("id") or "").strip()
    _record_intake_audit(
        organization_id=organization_id, ap_item_id=ap_item_id,
        envelope=envelope, action="created_thin_intake_fallback",
        target_state=initial_state,
    )
    return {
        "ok": True, "action": "created_thin",
        "ap_item_id": ap_item_id, "state": initial_state,
        "fallback": "no_connection",
    }


def _record_intake_audit(
    *,
    organization_id: str,
    ap_item_id: str,
    envelope: Dict[str, Any],
    action: str,
    target_state: str,
) -> None:
    if not ap_item_id:
        return
    db = get_db()
    if not hasattr(db, "record_audit_event"):
        return
    try:
        db.record_audit_event(
            actor_id="netsuite", actor_type="erp_webhook",
            action=f"erp_native_intake.{action}",
            box_id=ap_item_id, box_type="ap_item",
            entity_type="ap_item", entity_id=ap_item_id,
            organization_id=organization_id,
            metadata={
                "target_state": target_state,
                "event_type": envelope.get("event_type"),
                "event_id": envelope.get("event_id"),
                "ns_internal_id": (envelope.get("bill") or {}).get("ns_internal_id"),
                "source": "netsuite_native",
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "erp_webhook_dispatch: audit write failed for %s — %s",
            ap_item_id, exc,
        )
