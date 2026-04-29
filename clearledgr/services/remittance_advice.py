"""Remittance advice generation + send (Wave 2 / C5).

A remittance advice is the courteous, audit-trail-grade notification
to the vendor that we have paid invoice X via rail Y on date Z. AP
cycle reference doc Stage 8 lists this as a deliverable; ISO 20022
pain.001/pain.002 messaging includes it as an embedded narrative.
For Clearledgr the surface is plain email — vendors don't run pain
parsers.

Per-vendor opt-out via ``vendor_profiles.remittance_opt_out`` — for
vendors that read their own bank-portal feeds and treat outbound
remittance emails as noise.

This module never raises into the caller. Failures (no Gmail token,
opted out, no contact email, send error) record an audit event with
the reason and return a structured result. The payment-tracking flow
must stay green even when the remittance-advice infra is broken.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class RemittanceAdviceResult:
    """Outcome of :func:`send_remittance_advice`.

    ``status`` values:
      * ``sent`` — email handed to Gmail send
      * ``opted_out`` — vendor profile flagged remittance_opt_out=1
      * ``no_email`` — neither remittance_email nor primary_contact_email set
      * ``no_gmail`` — org has no connected Gmail token to send from
      * ``not_confirmed`` — payment confirmation status != "confirmed"
      * ``duplicate`` — idempotency key already recorded
      * ``send_failed`` — Gmail send raised; logged + audit-recorded
    """
    status: str
    audit_event_id: Optional[str] = None
    sent_to: Optional[str] = None
    error: Optional[str] = None


def _idempotency_key(organization_id: str, ap_item_id: str, payment_id: str) -> str:
    return f"remittance_advice:{organization_id}:{ap_item_id}:{payment_id}"


def render_remittance_advice(
    *,
    organization_name: str,
    ap_item: Dict[str, Any],
    confirmation: Dict[str, Any],
    vendor_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Render the email subject + plain-text body.

    No HTML — vendor inboxes vary wildly and a plain-text remittance
    advice is the universally readable form. The body follows the
    classical AP-cycle template: vendor name, invoice number, amount,
    payment rail, settlement date, payment reference, contact line.
    """
    vendor_name = (
        ap_item.get("vendor_name")
        or (vendor_profile or {}).get("vendor_name")
        or "Vendor"
    )
    invoice_number = (
        ap_item.get("invoice_number")
        or ap_item.get("invoice_key")
        or ap_item.get("id")
        or "N/A"
    )
    amount = confirmation.get("amount") or ap_item.get("amount")
    currency = (
        confirmation.get("currency")
        or ap_item.get("currency")
        or ""
    )
    settlement_at = confirmation.get("settlement_at") or "—"
    method = confirmation.get("method") or "bank transfer"
    payment_reference = (
        confirmation.get("payment_reference")
        or confirmation.get("payment_id")
        or "—"
    )
    bank_last4 = confirmation.get("bank_account_last4")

    amount_str = (
        f"{currency} {float(amount):,.2f}".strip()
        if amount is not None else "—"
    )

    subject = (
        f"Remittance advice — payment for invoice {invoice_number} "
        f"({amount_str})"
    )

    bank_line = (
        f"Bank account ending in {bank_last4}.\n"
        if bank_last4 else ""
    )

    body = (
        f"Hello {vendor_name},\n\n"
        f"This is to confirm that {organization_name} has paid the "
        f"following invoice:\n\n"
        f"  Invoice number:    {invoice_number}\n"
        f"  Amount:            {amount_str}\n"
        f"  Settlement date:   {settlement_at}\n"
        f"  Payment method:    {method}\n"
        f"  Payment reference: {payment_reference}\n"
        f"{bank_line}"
        f"\n"
        f"Please contact us if any of the above does not match your "
        f"records.\n\n"
        f"Regards,\n"
        f"{organization_name} Accounts Payable\n"
    )

    return {"subject": subject, "body": body}


def _resolve_vendor_email(
    vendor_profile: Optional[Dict[str, Any]],
) -> Optional[str]:
    if not vendor_profile:
        return None
    return (
        (vendor_profile.get("remittance_email") or "").strip()
        or (vendor_profile.get("primary_contact_email") or "").strip()
        or None
    )


def _is_opted_out(vendor_profile: Optional[Dict[str, Any]]) -> bool:
    if not vendor_profile:
        return False
    flag = vendor_profile.get("remittance_opt_out")
    if isinstance(flag, bool):
        return flag
    try:
        return int(flag or 0) == 1
    except (TypeError, ValueError):
        return False


def _emit_audit(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    payment_id: str,
    status: str,
    sent_to: Optional[str],
    error: Optional[str],
) -> Optional[Dict[str, Any]]:
    try:
        return db.append_audit_event({
            "ap_item_id": ap_item_id,
            "box_id": ap_item_id,
            "box_type": "ap_item",
            "event_type": "remittance_advice_sent",
            "actor_type": "system",
            "actor_id": "remittance_advice",
            "organization_id": organization_id,
            "source": "remittance_advice",
            "idempotency_key": _idempotency_key(
                organization_id, ap_item_id, payment_id,
            ),
            "decision_reason": error,
            "metadata": {
                "remittance_status": status,
                "sent_to": sent_to,
                "payment_id": payment_id,
                "error": error,
            },
        })
    except Exception:
        logger.exception(
            "remittance_advice: audit emit failed org=%s ap_item=%s",
            organization_id, ap_item_id,
        )
        return None


def send_remittance_advice(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
    payment_id: str,
    confirmation: Dict[str, Any],
    organization_name: Optional[str] = None,
    sender=None,
) -> RemittanceAdviceResult:
    """End-to-end: render the advice, look up the vendor's preferred
    contact, send via Gmail, record an audit event.

    Idempotent on (org, ap_item_id, payment_id) — re-invocation for
    the same payment is a no-op short-circuit.

    ``sender`` is an optional callable matching::

        async def sender(to: str, subject: str, body: str) -> Dict

    Tests inject a fake sender so they don't require a live Gmail
    integration; production uses the default Gmail-token-resolved
    sender derived from the organization's connected accounts.
    """
    # Idempotency: check if we already emitted (or attempted) for
    # this payment. The audit_events.idempotency_key UNIQUE makes
    # this safe even on race.
    existing = db.get_ap_audit_event_by_key(
        _idempotency_key(organization_id, ap_item_id, payment_id),
    )
    if existing:
        return RemittanceAdviceResult(
            status="duplicate",
            audit_event_id=existing.get("id"),
        )

    if str(confirmation.get("status") or "").lower() != "confirmed":
        # We never send remittance advices for failed/disputed
        # payments — those need operator follow-up, not vendor
        # notification.
        evt = _emit_audit(
            db,
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            payment_id=payment_id,
            status="not_confirmed",
            sent_to=None,
            error=None,
        )
        return RemittanceAdviceResult(
            status="not_confirmed",
            audit_event_id=(evt or {}).get("id") if evt else None,
        )

    ap_item = db.get_ap_item(ap_item_id) or {}
    vendor_name = ap_item.get("vendor_name")
    vendor_profile = None
    if vendor_name:
        try:
            vendor_profile = db.get_vendor_profile(organization_id, vendor_name)
        except Exception:
            vendor_profile = None

    if _is_opted_out(vendor_profile):
        evt = _emit_audit(
            db,
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            payment_id=payment_id,
            status="opted_out",
            sent_to=None,
            error=None,
        )
        return RemittanceAdviceResult(
            status="opted_out",
            audit_event_id=(evt or {}).get("id") if evt else None,
        )

    to_email = _resolve_vendor_email(vendor_profile)
    if not to_email:
        evt = _emit_audit(
            db,
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            payment_id=payment_id,
            status="no_email",
            sent_to=None,
            error=None,
        )
        return RemittanceAdviceResult(
            status="no_email",
            audit_event_id=(evt or {}).get("id") if evt else None,
        )

    org_name = organization_name
    if not org_name:
        try:
            org_row = db.get_organization(organization_id)
            org_name = (org_row or {}).get("organization_name") or organization_id
        except Exception:
            org_name = organization_id

    rendered = render_remittance_advice(
        organization_name=org_name or organization_id,
        ap_item=ap_item,
        confirmation=confirmation,
        vendor_profile=vendor_profile,
    )

    if sender is None:
        # No injected sender. We don't try to spin up a Gmail client
        # synchronously here — that requires an OAuth token + an
        # event loop and is out of scope for the C5 commit. Instead,
        # log the intent + write the audit event so the operator can
        # follow up. C5b will wire the actual Gmail send.
        evt = _emit_audit(
            db,
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            payment_id=payment_id,
            status="no_gmail",
            sent_to=to_email,
            error=None,
        )
        return RemittanceAdviceResult(
            status="no_gmail",
            audit_event_id=(evt or {}).get("id") if evt else None,
            sent_to=to_email,
        )

    try:
        result = sender(
            to=to_email,
            subject=rendered["subject"],
            body=rendered["body"],
        )
        # Async sender path: support both sync and awaitable senders.
        import inspect
        if inspect.isawaitable(result):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(result)
        if isinstance(result, dict) and result.get("status") == "rate_limited":
            evt = _emit_audit(
                db,
                organization_id=organization_id,
                ap_item_id=ap_item_id,
                payment_id=payment_id,
                status="send_failed",
                sent_to=to_email,
                error="rate_limited",
            )
            return RemittanceAdviceResult(
                status="send_failed",
                audit_event_id=(evt or {}).get("id") if evt else None,
                sent_to=to_email,
                error="rate_limited",
            )
    except Exception as exc:
        logger.warning(
            "remittance_advice: send failed org=%s ap_item=%s — %s",
            organization_id, ap_item_id, exc,
        )
        evt = _emit_audit(
            db,
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            payment_id=payment_id,
            status="send_failed",
            sent_to=to_email,
            error=str(exc)[:200],
        )
        return RemittanceAdviceResult(
            status="send_failed",
            audit_event_id=(evt or {}).get("id") if evt else None,
            sent_to=to_email,
            error=str(exc)[:200],
        )

    evt = _emit_audit(
        db,
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        payment_id=payment_id,
        status="sent",
        sent_to=to_email,
        error=None,
    )
    return RemittanceAdviceResult(
        status="sent",
        audit_event_id=(evt or {}).get("id") if evt else None,
        sent_to=to_email,
    )
