"""Dispatch handlers for ERP-native bill events (NetSuite, SAP, …).

The webhook endpoints in :mod:`clearledgr.api.erp_webhooks` verify the
signature and record an audit event. This module is what they call
*after* verification: parse the payload, extract bill data, and create
or update a Clearledgr AP item ("Box") so the ERP-arrived bill is
visible to the rest of the coordination layer (Slack approvals,
exception queue, vendor profile, etc.).

This closes the loop on the deck's "ONE TRUTH · MANY WINDOWS" claim:
a bill that arrives via EDI, vendor portal, or AP-clerk-typed entry
in NetSuite/SAP — never touching Gmail — still becomes a Box and
flows through the same coordination pipeline as Gmail-arrived bills.

Phase 1 of ERP-native intake (this module):

* ``vendorbill.create`` → INSERT new AP item; state derived from the
  bill's payment status. No Slack routing yet — that's Phase 2.
* ``vendorbill.update`` → re-derive state from the new payment status,
  apply any valid transition.
* ``vendorbill.paid`` → transition to ``closed`` (terminal for the
  successful path).
* ``vendorbill.delete`` → transition to ``closed`` with a metadata
  note; we don't drop the row.

Idempotency: each handler keys off ``erp_reference == ns_internal_id``.
Replays of the same event are no-ops.

Failures: handlers log + raise. The webhook route catches and returns
2xx anyway — ERPs retry, and the audit event is already written.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from clearledgr.core.ap_states import APState, validate_transition
from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


# ─── Public entrypoint ──────────────────────────────────────────────


def dispatch_netsuite_event(organization_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Route a verified NetSuite webhook payload to the right handler.

    Returns a small status dict for the webhook response body — primarily
    for our own logs since NetSuite only cares about the status code.
    """
    event_type = str(payload.get("event_type") or "").strip().lower()
    bill = payload.get("bill") or {}
    ns_internal_id = str(bill.get("ns_internal_id") or "").strip()
    if not ns_internal_id:
        return {"ok": False, "reason": "missing_ns_internal_id"}

    if event_type == "vendorbill.create":
        return _handle_create(organization_id, payload, bill, ns_internal_id)
    if event_type == "vendorbill.update":
        return _handle_update(organization_id, payload, bill, ns_internal_id)
    if event_type == "vendorbill.paid":
        return _handle_paid(organization_id, payload, bill, ns_internal_id)
    if event_type == "vendorbill.delete":
        return _handle_delete(organization_id, payload, bill, ns_internal_id)
    # Unknown events are ignored (forward-compat for new event types
    # NetSuite or our SuiteScript may emit). Audit event is already
    # written by the webhook route.
    return {"ok": True, "reason": "ignored_event", "event_type": event_type}


# ─── Handlers ───────────────────────────────────────────────────────


def _handle_create(
    organization_id: str,
    envelope: Dict[str, Any],
    bill: Dict[str, Any],
    ns_internal_id: str,
) -> Dict[str, Any]:
    db = get_db()
    existing = db.get_ap_item_by_erp_reference(organization_id, ns_internal_id)
    if existing:
        # Replay or out-of-order delivery — caller already created the
        # Box. Forward to update path so any state drift since last sync
        # gets reconciled.
        return _handle_update(organization_id, envelope, bill, ns_internal_id, existing=existing)

    initial_state = _state_from_bill(bill)
    payload = _ap_item_payload_from_bill(
        organization_id=organization_id,
        bill=bill,
        envelope=envelope,
        ns_internal_id=ns_internal_id,
        state=initial_state,
    )
    item = db.create_ap_item(payload)
    ap_item_id = str((item or {}).get("id") or payload.get("id") or "").strip()
    logger.info(
        "erp_webhook_dispatch: created AP item %s for NetSuite bill %s (org=%s, state=%s)",
        ap_item_id, ns_internal_id, organization_id, initial_state,
    )
    _record_intake_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        envelope=envelope,
        action="created",
        target_state=initial_state,
    )

    # Phase 2 of write-direction: if this ERP-native bill landed at
    # needs_approval (NetSuite-side payment hold present), route to
    # Slack so an approver can release the hold without leaving Slack.
    # Best-effort — the Box is created either way; the route is async
    # and shouldn't block the webhook ACK.
    if initial_state == APState.NEEDS_APPROVAL.value and ap_item_id:
        _route_for_approval_async(item or {**payload, "id": ap_item_id})

    return {
        "ok": True,
        "action": "created",
        "ap_item_id": ap_item_id,
        "state": initial_state,
        "routed_to_slack": initial_state == APState.NEEDS_APPROVAL.value,
    }


def _handle_update(
    organization_id: str,
    envelope: Dict[str, Any],
    bill: Dict[str, Any],
    ns_internal_id: str,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    db = get_db()
    if existing is None:
        existing = db.get_ap_item_by_erp_reference(organization_id, ns_internal_id)
    if not existing:
        # Update arrived before the create — synthesize the create.
        return _handle_create(organization_id, envelope, bill, ns_internal_id)

    ap_item_id = str(existing.get("id") or "").strip()
    current_state = str(existing.get("state") or "").strip().lower()
    desired_state = _state_from_bill(bill)

    # Cheap field updates (amount, vendor, due-date, currency may have
    # changed in NetSuite). Drop anything that's None to avoid clobbering
    # values we don't have authority over.
    field_updates = {
        k: v for k, v in {
            "vendor_name": bill.get("entity_name") or bill.get("entity_id"),
            "amount": bill.get("amount"),
            "currency": (bill.get("currency") or "").upper() or None,
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
            return {"ok": False, "action": "update_failed", "ap_item_id": ap_item_id, "reason": str(exc)}

    target_state_for_audit = field_updates.get("state") or current_state
    _record_intake_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        envelope=envelope,
        action="updated",
        target_state=target_state_for_audit,
    )

    # Same Slack-routing trigger as create: if this update transitioned
    # the Box INTO needs_approval (e.g., NetSuite added a payment hold
    # after the fact), post the approval card. The approval module's
    # idempotency guard prevents duplicate cards.
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
        "fields_updated": [k for k in field_updates.keys() if not k.startswith("_")],
    }


def _handle_paid(
    organization_id: str,
    envelope: Dict[str, Any],
    bill: Dict[str, Any],
    ns_internal_id: str,
) -> Dict[str, Any]:
    db = get_db()
    existing = db.get_ap_item_by_erp_reference(organization_id, ns_internal_id)
    if not existing:
        # Paid event for a bill we never saw — synthesize the create
        # in a paid-already shape so we have a record.
        envelope_with_paid_bill = dict(envelope)
        envelope_with_paid_bill["bill"] = {**bill, "status_label": "Paid In Full"}
        return _handle_create(organization_id, envelope_with_paid_bill, envelope_with_paid_bill["bill"], ns_internal_id)

    ap_item_id = str(existing.get("id") or "").strip()
    current_state = str(existing.get("state") or "").strip().lower()
    if current_state == APState.CLOSED.value:
        return {"ok": True, "action": "noop_already_closed", "ap_item_id": ap_item_id}
    if not validate_transition(current_state, APState.CLOSED.value):
        logger.warning(
            "erp_webhook_dispatch: paid event but %s → closed is not a valid transition (ap_item=%s)",
            current_state, ap_item_id,
        )
        return {"ok": False, "action": "invalid_transition", "ap_item_id": ap_item_id, "from": current_state}
    try:
        db.update_ap_item(
            ap_item_id,
            state=APState.CLOSED.value,
            _actor_type="erp_webhook",
            _actor_id="netsuite",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "erp_webhook_dispatch: paid → closed update failed ap_item=%s — %s",
            ap_item_id, exc,
        )
        return {"ok": False, "action": "close_failed", "ap_item_id": ap_item_id}
    _record_intake_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        envelope=envelope,
        action="paid_closed",
        target_state=APState.CLOSED.value,
    )
    return {"ok": True, "action": "closed", "ap_item_id": ap_item_id, "state": APState.CLOSED.value}


def _handle_delete(
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
                ap_item_id,
                state=APState.CLOSED.value,
                _actor_type="erp_webhook",
                _actor_id="netsuite",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("erp_webhook_dispatch: delete → close failed: %s", exc)
            return {"ok": False, "action": "close_failed", "ap_item_id": ap_item_id}
    _record_intake_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        envelope=envelope,
        action="deleted_in_erp",
        target_state=APState.CLOSED.value,
    )
    return {"ok": True, "action": "closed_via_delete", "ap_item_id": ap_item_id}


# ─── Helpers ────────────────────────────────────────────────────────


def _state_from_bill(bill: Dict[str, Any]) -> str:
    """Derive the right AP state from a NetSuite vendor-bill payload.

    Mapping (Phase 1):

    - Status "Paid In Full" → ``closed``
    - Payment hold set → ``needs_approval``
    - Otherwise → ``posted_to_erp``  (the bill IS in NetSuite — Clearledgr
      is tracking it, not creating it; "posted" is the right semantic.)
    """
    status_label = str(bill.get("status_label") or bill.get("status") or "").strip().lower()
    if "paid" in status_label and "in full" in status_label:
        return APState.CLOSED.value
    payment_hold = str(bill.get("payment_hold") or "").strip().upper()
    if payment_hold in {"T", "TRUE", "Y", "YES", "1"}:
        return APState.NEEDS_APPROVAL.value
    return APState.POSTED_TO_ERP.value


def _ap_item_payload_from_bill(
    *,
    organization_id: str,
    bill: Dict[str, Any],
    envelope: Dict[str, Any],
    ns_internal_id: str,
    state: str,
) -> Dict[str, Any]:
    """Construct an ``ap_items`` INSERT payload from a NetSuite bill blob."""
    metadata = {
        "source": "netsuite_native",
        "ns_account_id": str(envelope.get("account_id") or "").strip(),
        "ns_subsidiary_id": bill.get("subsidiary_id"),
        "ns_status_label": bill.get("status_label"),
        "ns_payment_hold": bill.get("payment_hold"),
        "ns_approval_status": bill.get("approval_status"),
        "ns_external_id": bill.get("external_id"),
        "ns_event_id": envelope.get("event_id"),
    }
    # Filter out None metadata so the JSON stays small.
    metadata = {k: v for k, v in metadata.items() if v not in (None, "")}

    amount = bill.get("amount")
    try:
        amount_val = float(amount) if amount not in (None, "") else None
    except (TypeError, ValueError):
        amount_val = None

    return {
        "thread_id": None,  # ERP-native bills don't have an email thread
        "message_id": None,
        "subject": _bill_subject(bill),
        "sender": _bill_sender(bill),
        "vendor_name": bill.get("entity_name") or bill.get("entity_id") or "Unknown vendor",
        "amount": amount_val,
        "currency": (str(bill.get("currency") or "USD")).upper(),
        "invoice_number": bill.get("invoice_number") or bill.get("tran_id"),
        "invoice_date": bill.get("tran_date"),
        "due_date": bill.get("due_date"),
        "state": state,
        "confidence": 1.0,  # ERP-extracted fields are authoritative; we trust them
        "approval_required": state == APState.NEEDS_APPROVAL.value,
        "erp_reference": ns_internal_id,
        "erp_posted_at": datetime.now(timezone.utc).isoformat() if state == APState.POSTED_TO_ERP.value else None,
        "organization_id": organization_id,
        "approval_surface": "slack",  # ERP-native flows route to Slack/Teams by default
        "metadata": metadata,
        "document_type": "invoice",
    }


def _bill_subject(bill: Dict[str, Any]) -> str:
    inv = bill.get("invoice_number") or bill.get("tran_id") or ""
    vendor = bill.get("entity_name") or bill.get("entity_id") or "vendor"
    if inv:
        return f"NetSuite Bill {inv} — {vendor}"
    return f"NetSuite Bill — {vendor}"


def _bill_sender(bill: Dict[str, Any]) -> str:
    vendor = bill.get("entity_name") or bill.get("entity_id") or "vendor"
    return f"{vendor} <netsuite@erp-native>"


def _route_for_approval_async(ap_item: Dict[str, Any]) -> None:
    """Fire-and-forget Slack approval routing for an ERP-native AP item.

    Webhook handlers are sync (FastAPI BaseHTTPMiddleware path); the
    approval routing is async (Slack API + DB writes). We can't
    ``await`` here without restructuring the whole webhook pipeline,
    so we schedule the coroutine on the running loop and let it
    complete in the background. If there's no running loop (pytest in
    sync mode, etc.), we fall back to a brand-new loop in a thread.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    if not ap_item:
        return
    try:
        from clearledgr.services.erp_native_approval import route_for_approval
    except Exception as exc:  # noqa: BLE001
        logger.warning("erp_webhook_dispatch: approval module import failed — %s", exc)
        return

    async def _runner():
        try:
            await route_for_approval(ap_item)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "erp_webhook_dispatch: route_for_approval raised for ap_item=%s — %s",
                ap_item.get("id"), exc,
            )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_runner())
    except RuntimeError:
        # No running loop. Spin up a single-shot worker.
        with ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(asyncio.run, _runner())


def _record_intake_audit(
    *,
    organization_id: str,
    ap_item_id: str,
    envelope: Dict[str, Any],
    action: str,
    target_state: str,
) -> None:
    """Best-effort audit event for the ERP-native intake transition.

    Already-recorded ``erp_webhook_received`` audit (in the webhook
    route) covers the inbound HTTP call. This event covers the
    business-side action — what the dispatch *did* with the payload,
    so the timeline reads cleanly in the panel and the admin console.
    """
    if not ap_item_id:
        return
    db = get_db()
    if not hasattr(db, "record_audit_event"):
        return
    try:
        db.record_audit_event(
            actor_id="netsuite",
            actor_type="erp_webhook",
            action=f"erp_native_intake.{action}",
            box_id=ap_item_id,
            box_type="ap_item",
            entity_type="ap_item",
            entity_id=ap_item_id,
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
