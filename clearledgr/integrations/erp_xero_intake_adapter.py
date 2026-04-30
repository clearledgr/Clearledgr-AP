"""Xero implementation of the IntakeAdapter protocol.

Xero webhooks notify when an ``INVOICE`` resource is created or
updated in the customer's Xero organisation. Critically, Xero uses
ONE event channel for both vendor bills (``ACCPAY``) and customer
invoices (``ACCREC``); the webhook payload only carries the
resource id, NOT the type. So the adapter has to fetch the invoice
to determine ``ACCPAY`` vs ``ACCREC`` and skip ``ACCREC`` entirely
— ingesting outbound sales invoices as if they were inbound bills
would be a destructive bug.

The webhook envelope batches multiple events; the route layer
iterates and calls :func:`handle_intake_event` once per ``INVOICE``
event with a synthetic single-resource payload.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Mapping, Optional

from clearledgr.core.ap_states import APState
from clearledgr.core.database import get_db
from clearledgr.core.erp_webhook_verify import verify_xero_signature
from clearledgr.integrations.erp_router import _erp_connection_from_row
from clearledgr.services.intake_adapter import (
    IntakeEnvelope,
    StateUpdate,
    register_adapter,
)
from clearledgr.services.invoice_models import InvoiceData

logger = logging.getLogger(__name__)


_XERO_EVENT_TYPE_MAP = {
    "CREATE": "create",
    "UPDATE": "update",
    "DELETE": "cancelled",
}


class XeroIntakeAdapter:
    """Xero ACCPAY invoice webhook intake.

    Synthetic per-resource payload shape (set by the webhook route):

    ::

        {
            "tenant_id": "<xero tenant guid>",
            "resource_id": "<invoice guid>",
            "event_type": "CREATE"|"UPDATE"|"DELETE",
            "event_category": "INVOICE",
        }
    """

    source_type = "xero"

    async def verify_signature(
        self, raw: bytes, headers: Mapping[str, str], secret: str,
    ) -> bool:
        signature = (
            headers.get("x-xero-signature")
            or headers.get("X-Xero-Signature")
            or headers.get("X-XERO-SIGNATURE")
        )
        return verify_xero_signature(raw, signature, secret)

    async def parse_envelope(
        self, raw: bytes, headers: Mapping[str, str], organization_id: str,
    ) -> IntakeEnvelope:
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload = {}
        resource_id = str(payload.get("resource_id") or "").strip()
        raw_event = str(payload.get("event_type") or "").strip().upper()
        event_type = _XERO_EVENT_TYPE_MAP.get(raw_event, "")
        return IntakeEnvelope(
            source_type=self.source_type,
            event_type=event_type,
            source_id=resource_id,
            organization_id=organization_id,
            raw_payload=payload,
            event_id=str(payload.get("event_id") or "").strip() or None,
            received_at=str(payload.get("event_date_utc") or "").strip() or None,
            channel_metadata={
                "xero_tenant_id": str(payload.get("tenant_id") or "").strip(),
                "xero_event_type": raw_event,
                "xero_event_category": str(payload.get("event_category") or "INVOICE"),
            },
        )

    async def enrich(
        self, organization_id: str, envelope: IntakeEnvelope,
    ) -> InvoiceData:
        connection = self._resolve_connection(organization_id)
        if connection is None:
            return self._thin_invoice_from_envelope(envelope, organization_id)

        invoice = await self._fetch_invoice(connection, envelope.source_id)
        if not invoice:
            return self._thin_invoice_from_envelope(envelope, organization_id)

        # ACCPAY filter — silently skip ACCREC by returning a marker
        # that the dispatcher can use to short-circuit. The dispatcher
        # treats a thin-intake with `not_a_bill` metadata as a no-op
        # rather than creating a phantom AP item.
        invoice_type = str(invoice.get("Type") or "").upper()
        if invoice_type != "ACCPAY":
            logger.info(
                "xero_intake_adapter: skipping non-ACCPAY invoice "
                "id=%s type=%s",
                envelope.source_id, invoice_type,
            )
            return self._marker_invoice_for_skip(
                envelope, organization_id, invoice_type,
            )

        return self._build_invoice_from_xero(envelope, invoice, organization_id)

    async def derive_state_update(
        self, organization_id: str, envelope: IntakeEnvelope,
    ) -> StateUpdate:
        if envelope.event_type == "cancelled":
            return StateUpdate(target_state=APState.CLOSED.value)
        if envelope.event_type == "update":
            # Refresh-only; the workflow re-evaluates against the
            # fetched fields. No state change here.
            return StateUpdate(target_state=None, idempotent_no_op_allowed=True)
        return StateUpdate(target_state=None)

    # ─── Internal helpers ───

    @staticmethod
    def _resolve_connection(organization_id: str):
        db = get_db()
        if not hasattr(db, "get_erp_connections"):
            return None
        try:
            for row in db.get_erp_connections(organization_id):
                if str(row.get("erp_type") or "").lower() == "xero":
                    return _erp_connection_from_row(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "xero_intake_adapter: connection lookup failed — %s", exc,
            )
        return None

    @staticmethod
    async def _fetch_invoice(connection, invoice_id: str) -> Optional[Dict[str, Any]]:
        """GET /api.xro/2.0/Invoices/{InvoiceID}. Returns the raw
        Xero Invoice dict on success, ``None`` on any failure
        (caller falls back to thin-envelope intake)."""
        if not invoice_id:
            return None
        try:
            from clearledgr.core.http_client import get_http_client

            url = f"https://api.xero.com/api.xro/2.0/Invoices/{invoice_id}"
            client = get_http_client()
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "xero-tenant-id": connection.tenant_id,
                    "Accept": "application/json",
                },
                timeout=30,
            )
            if response.status_code != 200:
                logger.warning(
                    "xero_intake_adapter: invoice fetch %s returned %d",
                    invoice_id, response.status_code,
                )
                return None
            body = response.json() or {}
            invoices = body.get("Invoices") if isinstance(body, dict) else None
            if isinstance(invoices, list) and invoices:
                return invoices[0]
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "xero_intake_adapter: invoice fetch raised for id=%s — %s",
                invoice_id, exc,
            )
            return None

    def _thin_invoice_from_envelope(
        self, envelope: IntakeEnvelope, organization_id: str,
    ) -> InvoiceData:
        return InvoiceData(
            source_type="xero",
            source_id=envelope.source_id,
            erp_native=True,
            erp_metadata={
                "xero_invoice_id": envelope.source_id,
                "xero_tenant_id": envelope.channel_metadata.get("xero_tenant_id"),
                "fallback_thin_intake": True,
            },
            subject=f"Xero Invoice {envelope.source_id}",
            sender="<xero@erp-native>",
            vendor_name="Unknown vendor",
            amount=0.0,
            currency="USD",
            invoice_number=envelope.source_id,
            confidence=1.0,
            organization_id=organization_id,
            correlation_id=f"erp-intake:xero:{envelope.event_id or envelope.source_id}",
        )

    def _marker_invoice_for_skip(
        self,
        envelope: IntakeEnvelope,
        organization_id: str,
        invoice_type: str,
    ) -> InvoiceData:
        """Return an InvoiceData with metadata tagged ``not_a_bill``
        so the universal dispatcher's create branch can short-
        circuit instead of creating a phantom AP item for an
        ACCREC (sales) invoice. The dispatch handler reads
        ``erp_metadata.not_a_bill`` and skips downstream creation
        when present."""
        return InvoiceData(
            source_type="xero",
            source_id=envelope.source_id,
            erp_native=True,
            erp_metadata={
                "xero_invoice_id": envelope.source_id,
                "xero_tenant_id": envelope.channel_metadata.get("xero_tenant_id"),
                "xero_invoice_type": invoice_type,
                "not_a_bill": True,
                "skip_reason": "non_accpay_invoice",
            },
            subject=f"Xero {invoice_type} (skipped) {envelope.source_id}",
            sender="<xero@erp-native>",
            vendor_name="(non-bill)",
            amount=0.0,
            currency="USD",
            invoice_number=envelope.source_id,
            confidence=1.0,
            organization_id=organization_id,
            correlation_id=f"erp-intake:xero:{envelope.event_id or envelope.source_id}",
        )

    @staticmethod
    def _build_invoice_from_xero(
        envelope: IntakeEnvelope,
        invoice: Dict[str, Any],
        organization_id: str,
    ) -> InvoiceData:
        contact = invoice.get("Contact") if isinstance(invoice.get("Contact"), dict) else {}
        vendor_name = str(contact.get("Name") or "Unknown vendor")
        vendor_id = str(contact.get("ContactID") or "")
        contact_email = str(contact.get("EmailAddress") or "").strip()

        amount = _safe_float(invoice.get("Total"), default=0.0)
        currency = str(invoice.get("CurrencyCode") or "USD").upper()
        invoice_number = str(invoice.get("InvoiceNumber") or envelope.source_id).strip()

        line_items: list = []
        for line in invoice.get("LineItems") or []:
            if not isinstance(line, dict):
                continue
            line_items.append({
                "description": str(line.get("Description") or "").strip(),
                "quantity": _safe_float(line.get("Quantity")),
                "unit_price": _safe_float(line.get("UnitAmount")),
                "amount": _safe_float(line.get("LineAmount")),
                "gl_code": line.get("AccountCode"),
                "tax_amount": _safe_float(line.get("TaxAmount")),
            })

        erp_metadata = {
            "xero_invoice_id": str(invoice.get("InvoiceID") or envelope.source_id),
            "xero_tenant_id": envelope.channel_metadata.get("xero_tenant_id"),
            "xero_status": invoice.get("Status"),
            "xero_invoice_type": invoice.get("Type"),
            "xero_contact_id": vendor_id,
            "xero_amount_due": invoice.get("AmountDue"),
            "xero_amount_paid": invoice.get("AmountPaid"),
            "xero_event_id": envelope.event_id,
        }
        erp_metadata = {k: v for k, v in erp_metadata.items() if v not in (None, "")}

        sender = (
            contact_email
            or f"{vendor_name} <xero@erp-native>"
        )

        return InvoiceData(
            source_type="xero",
            source_id=envelope.source_id,
            erp_native=True,
            erp_metadata=erp_metadata,
            subject=f"Xero Bill {invoice_number} — {vendor_name}",
            sender=sender,
            vendor_name=vendor_name,
            amount=amount,
            currency=currency,
            invoice_number=invoice_number,
            due_date=_xero_iso_date(invoice.get("DueDate")),
            confidence=1.0,
            line_items=line_items or None,
            tax_amount=_safe_float(invoice.get("TotalTax")),
            subtotal=_safe_float(invoice.get("SubTotal")),
            organization_id=organization_id,
            correlation_id=f"erp-intake:xero:{envelope.event_id or envelope.source_id}",
            field_confidences={
                "vendor_name": 1.0, "amount": 1.0, "currency": 1.0,
                "invoice_number": 1.0, "due_date": 1.0,
            },
        )


def _safe_float(value: Any, *, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _xero_iso_date(value: Any) -> Optional[str]:
    """Xero JSON dates arrive as ``/Date(1234567890000+0000)/`` or
    plain ISO. Normalise to YYYY-MM-DD when possible; pass through
    otherwise so the consumer sees the raw value."""
    if value in (None, ""):
        return None
    raw = str(value).strip()
    if raw.startswith("/Date(") and raw.endswith(")/"):
        try:
            from datetime import datetime, timezone
            inner = raw[len("/Date("): -len(")/")]
            millis = int(inner.split("+", 1)[0].split("-", 1)[0])
            dt = datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
            return dt.date().isoformat()
        except Exception:
            return raw
    return raw[:10] if len(raw) >= 10 else raw


# Register at import time so the universal handler can find this adapter.
_XERO_ADAPTER = XeroIntakeAdapter()
register_adapter(_XERO_ADAPTER)
