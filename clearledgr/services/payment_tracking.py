"""Payment-tracking service (Wave 2 / C2).

Single end-to-end entry point for "the bank says this bill was paid":

  1. Insert a row into ``payment_confirmations`` (the ledger of who
     paid what, when, by which rail).
  2. Walk the AP item through the canonical payment lifecycle so the
     state machine, append-only audit timeline and Box exception
     surface all stay coherent.
  3. Emit a ``payment_confirmation_recorded`` audit event keyed by
     ``payment_confirmation:{org}:{source}:{payment_id}`` so a webhook
     redelivery is a no-op end-to-end.

Idempotent at three layers:

  * ``payment_confirmations`` UNIQUE INDEX on
    ``(organization_id, source, payment_id)``
  * ``audit_events.idempotency_key`` UNIQUE
  * The pre-check via ``get_payment_confirmation_by_external_id``
    short-circuits before either INSERT runs.

Used by the ERP webhook receivers (C3), the manual-confirmation
endpoint (C4) and the bank-rec auto-match path (C6).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from clearledgr.core.ap_states import IllegalTransitionError
from clearledgr.core.stores.payment_confirmations_store import (
    PaymentConfirmationConflict,
)

logger = logging.getLogger(__name__)


# Status → target AP state. ``disputed`` does NOT auto-transition;
# it just records the confirmation and emits an audit event so the
# operator can decide.
_STATUS_TO_TARGET_STATE: Dict[str, Optional[str]] = {
    "confirmed": "payment_executed",
    "failed": "payment_failed",
    "disputed": None,
}


# Multi-step paths through the state machine. The webhook can land
# while the AP item is still in posted_to_erp (the agent loop hasn't
# yet flipped to awaiting_payment), so we walk the chain rather than
# attempt a direct illegal transition.
_TRANSITION_PATHS: Dict[tuple, list] = {
    ("posted_to_erp", "payment_executed"): ["awaiting_payment", "payment_executed"],
    ("posted_to_erp", "payment_failed"): ["awaiting_payment", "payment_failed"],
    ("awaiting_payment", "payment_executed"): ["payment_executed"],
    ("awaiting_payment", "payment_failed"): ["payment_failed"],
    ("payment_in_flight", "payment_executed"): ["payment_executed"],
    ("payment_in_flight", "payment_failed"): ["payment_failed"],
    # Retry after a failed attempt. failed -> awaiting_payment is the
    # canonical "queue for retry" step in C1; we then walk to executed.
    ("payment_failed", "payment_executed"): ["awaiting_payment", "payment_executed"],
    ("payment_failed", "payment_failed"): [],
}


_TERMINAL_STATES = frozenset({"closed", "reversed", "rejected"})


@dataclass
class PaymentConfirmationResult:
    """Outcome of :func:`record_payment_confirmation`.

    ``duplicate`` is True when the confirmation already existed
    (idempotent webhook redelivery). In the duplicate case, no state
    transition is attempted and no second audit event is written —
    the original event is the audit record.
    """
    confirmation: Dict[str, Any]
    duplicate: bool = False
    ap_state_before: Optional[str] = None
    ap_state_after: Optional[str] = None
    ap_state_unchanged_reason: Optional[str] = None
    audit_event_id: Optional[str] = None


def record_payment_confirmation(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    payment_id: str,
    source: str,
    status: str = "confirmed",
    settlement_at: Optional[str] = None,
    amount: Optional[Any] = None,
    currency: Optional[str] = None,
    method: Optional[str] = None,
    payment_reference: Optional[str] = None,
    bank_account_last4: Optional[str] = None,
    failure_reason: Optional[str] = None,
    notes: Optional[str] = None,
    actor_type: str = "system",
    actor_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> PaymentConfirmationResult:
    """End-to-end record of a payment event.

    Caller doesn't need to pre-check idempotency — this function does.
    Caller doesn't need to drive state transitions — this function
    walks them. Caller doesn't need to write the audit event —
    this function emits it.

    Returns a :class:`PaymentConfirmationResult` describing what
    happened, including ``ap_state_unchanged_reason`` when the AP
    item couldn't be transitioned (terminal state, unknown path,
    AP item missing, status=disputed).
    """
    # ── 0. Sanctions gate (Wave 3 / E1) ────────────────────────────
    # Defence-in-depth: if the vendor's rolled-up sanctions_status is
    # 'blocked', refuse to record any payment confirmation. The
    # AP-item-level exception flow catches this earlier; this is the
    # last line. Raises SanctionsBlockedError so the caller's HTTP /
    # webhook layer surfaces a 403-shaped error instead of silently
    # writing a payment row.
    try:
        from clearledgr.services.sanctions_screening import (
            gate_payment_against_sanctions,
        )
        ap_item_for_gate = db.get_ap_item(ap_item_id)
        if ap_item_for_gate is not None:
            gate_payment_against_sanctions(
                db,
                organization_id=organization_id,
                vendor_name=ap_item_for_gate.get("vendor_name"),
            )
    except Exception as exc:
        # Re-raise SanctionsBlockedError; swallow other transient
        # errors so a sanctions-store hiccup doesn't block legitimate
        # payments.
        from clearledgr.services.sanctions_screening import (
            SanctionsBlockedError,
        )
        if isinstance(exc, SanctionsBlockedError):
            raise
        logger.warning(
            "payment_tracking: sanctions gate raised non-blocking error: %s",
            exc,
        )

    # ── 1. Idempotency pre-check ───────────────────────────────────
    existing = db.get_payment_confirmation_by_external_id(
        organization_id, source, payment_id, ap_item_id,
    )
    if existing:
        ap_item = db.get_ap_item(ap_item_id)
        return PaymentConfirmationResult(
            confirmation=existing,
            duplicate=True,
            ap_state_before=(ap_item or {}).get("state"),
            ap_state_after=(ap_item or {}).get("state"),
            ap_state_unchanged_reason="duplicate_redelivery",
        )

    # ── 2. Insert the confirmation row ─────────────────────────────
    actor_id_resolved = actor_id or "payment_tracking"
    try:
        confirmation = db.create_payment_confirmation(
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            payment_id=payment_id,
            source=source,
            status=status,
            settlement_at=settlement_at,
            amount=amount,
            currency=currency,
            method=method,
            payment_reference=payment_reference,
            bank_account_last4=bank_account_last4,
            failure_reason=failure_reason,
            notes=notes,
            created_by=actor_id_resolved,
            metadata=metadata,
        )
    except PaymentConfirmationConflict:
        # Race: another concurrent caller inserted between the
        # pre-check and our INSERT. Re-fetch and report duplicate.
        winner = db.get_payment_confirmation_by_external_id(
            organization_id, source, payment_id, ap_item_id,
        )
        ap_item = db.get_ap_item(ap_item_id)
        return PaymentConfirmationResult(
            confirmation=winner or {},
            duplicate=True,
            ap_state_before=(ap_item or {}).get("state"),
            ap_state_after=(ap_item or {}).get("state"),
            ap_state_unchanged_reason="duplicate_race",
        )

    clean_status = (status or "confirmed").strip().lower()

    # ── 3. Walk the AP item through the state machine ──────────────
    ap_item = db.get_ap_item(ap_item_id)
    state_before: Optional[str] = (ap_item or {}).get("state")
    state_after: Optional[str] = state_before
    unchanged_reason: Optional[str] = None

    target_state = _STATUS_TO_TARGET_STATE.get(clean_status)

    if ap_item is None:
        unchanged_reason = "ap_item_not_found"
    elif target_state is None:
        unchanged_reason = f"no_auto_transition_for_status:{clean_status}"
    elif state_before in _TERMINAL_STATES:
        unchanged_reason = f"terminal:{state_before}"
    elif state_before == target_state:
        unchanged_reason = "already_at_target"
    else:
        path = _TRANSITION_PATHS.get((state_before, target_state))
        if path is None:
            unchanged_reason = f"no_path:{state_before}->{target_state}"
        else:
            for next_state in path:
                try:
                    db.update_ap_item(
                        ap_item_id,
                        state=next_state,
                        _actor_type=actor_type,
                        _actor_id=actor_id_resolved,
                        _source="payment_confirmation",
                        _correlation_id=correlation_id,
                        _decision_reason=(
                            f"payment_{clean_status}:{source}:{payment_id}"
                        ),
                    )
                    state_after = next_state
                except IllegalTransitionError as exc:
                    # The state machine moved underneath us between
                    # the lookup and the transition (rare). Stop
                    # walking and record where we got stuck — the
                    # confirmation row is still preserved.
                    logger.warning(
                        "payment_tracking: illegal mid-walk transition "
                        "ap_item=%s %s->%s: %s",
                        ap_item_id, state_after, next_state, exc,
                    )
                    unchanged_reason = (
                        f"illegal_transition:{state_after}->{next_state}"
                    )
                    break

    # ── 4. Emit the canonical audit event ──────────────────────────
    audit_metadata = {
        "confirmation_id": confirmation.get("id"),
        "payment_id": payment_id,
        "source": source,
        "status": clean_status,
        "settlement_at": settlement_at,
        "amount": (str(amount) if amount is not None else None),
        "currency": currency,
        "method": method,
        "payment_reference": payment_reference,
        "bank_account_last4": bank_account_last4,
        "failure_reason": failure_reason,
        "ap_state_before": state_before,
        "ap_state_after": state_after,
        "ap_state_unchanged_reason": unchanged_reason,
    }
    if metadata:
        audit_metadata["caller_metadata"] = metadata

    audit_event = db.append_audit_event({
        "ap_item_id": ap_item_id,
        "box_id": ap_item_id,
        "box_type": "ap_item",
        "event_type": "payment_confirmation_recorded",
        "actor_type": actor_type,
        "actor_id": actor_id_resolved,
        "organization_id": organization_id,
        "source": "payment_confirmation",
        "correlation_id": correlation_id,
        "idempotency_key": (
            f"payment_confirmation:{organization_id}:{source}"
            f":{payment_id}:{ap_item_id}"
        ),
        "decision_reason": failure_reason or notes,
        "metadata": audit_metadata,
    })

    # ── 5. Remittance advice (Wave 2 / C5) ─────────────────────────
    # Fire-and-forget: failures must NOT roll back the confirmation.
    # The remittance service is itself idempotent (audit event keyed
    # by payment_id) so re-invocation is safe.
    if clean_status == "confirmed" and ap_item is not None:
        try:
            from clearledgr.services.remittance_advice import (
                send_remittance_advice,
            )
            send_remittance_advice(
                db,
                organization_id=organization_id,
                ap_item_id=ap_item_id,
                payment_id=payment_id,
                confirmation=confirmation,
            )
        except Exception:
            logger.exception(
                "payment_tracking: remittance advice hook failed "
                "ap_item=%s payment_id=%s", ap_item_id, payment_id,
            )

    return PaymentConfirmationResult(
        confirmation=confirmation,
        duplicate=False,
        ap_state_before=state_before,
        ap_state_after=state_after,
        ap_state_unchanged_reason=unchanged_reason,
        audit_event_id=(audit_event or {}).get("id") if audit_event else None,
    )
