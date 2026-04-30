"""QuickBooks Online implementation of the IntakeAdapter protocol.

QBO webhooks notify when a ``Bill`` entity is created, updated, or
deleted in the customer's QuickBooks company. The webhook envelope
is a fanout — one HTTP POST can carry many entity events across
``Bill``, ``BillPayment``, etc. — so the route layer iterates the
envelope and calls :func:`handle_intake_event` once per ``Bill``
event with a synthetic single-entity payload.

QBO webhooks carry only entity IDs; the adapter's ``enrich`` step
calls back into the QuickBooks REST API to fetch the full Bill
(plus vendor reference + line items + amounts) and maps that to
:class:`InvoiceData`.

ACCPAY only: QBO doesn't expose customer-invoice events as
``Bill``; vendor bills and customer invoices live on different
entities (``Bill`` vs ``Invoice``). So unlike the Xero adapter, no
``ACCPAY`` filter is needed — every ``Bill`` event is a vendor bill
by definition.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Mapping, Optional

from clearledgr.core.ap_states import APState
from clearledgr.core.database import get_db
from clearledgr.core.erp_webhook_verify import verify_quickbooks_signature
from clearledgr.integrations.erp_router import _erp_connection_from_row
from clearledgr.services.intake_adapter import (
    IntakeEnvelope,
    StateUpdate,
    register_adapter,
)
from clearledgr.services.invoice_models import InvoiceData

logger = logging.getLogger(__name__)


_QUICKBOOKS_OPERATION_MAP = {
    "Create": "create",
    "Update": "update",
    "Delete": "cancelled",
    "Void": "cancelled",
    "Merge": "update",
}


class QuickBooksIntakeAdapter:
    """QBO Bill webhook intake.

    Synthetic per-entity payload shape (set by the webhook route):

    ::

        {
            "realmId": "<qbo realm>",
            "entity_id": "<bill id>",
            "operation": "Create"|"Update"|"Delete",
        }
    """

    source_type = "quickbooks"

    async def verify_signature(
        self, raw: bytes, headers: Mapping[str, str], secret: str,
    ) -> bool:
        signature = (
            headers.get("intuit-signature")
            or headers.get("Intuit-Signature")
            or headers.get("INTUIT-SIGNATURE")
        )
        return verify_quickbooks_signature(raw, signature, secret)

    async def parse_envelope(
        self, raw: bytes, headers: Mapping[str, str], organization_id: str,
    ) -> IntakeEnvelope:
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload = {}
        entity_id = str(payload.get("entity_id") or "").strip()
        raw_op = str(payload.get("operation") or "").strip()
        event_type = _QUICKBOOKS_OPERATION_MAP.get(raw_op, "")
        return IntakeEnvelope(
            source_type=self.source_type,
            event_type=event_type,
            source_id=entity_id,
            organization_id=organization_id,
            raw_payload=payload,
            event_id=str(payload.get("event_id") or "").strip() or None,
            received_at=str(payload.get("event_time") or "").strip() or None,
            channel_metadata={
                "qb_realm_id": str(payload.get("realmId") or "").strip(),
                "qb_operation": raw_op,
            },
        )

    async def enrich(
        self, organization_id: str, envelope: IntakeEnvelope,
    ) -> InvoiceData:
        connection = self._resolve_connection(organization_id)
        if connection is None:
            return self._thin_invoice_from_envelope(envelope, organization_id)

        bill_data = await self._fetch_bill(connection, envelope.source_id)
        if not bill_data:
            return self._thin_invoice_from_envelope(envelope, organization_id)

        return self._build_invoice_from_bill(envelope, bill_data, organization_id)

    async def derive_state_update(
        self, organization_id: str, envelope: IntakeEnvelope,
    ) -> StateUpdate:
        if envelope.event_type == "cancelled":
            return StateUpdate(target_state=APState.CLOSED.value)
        if envelope.event_type == "update":
            # QBO Bill updates can change amount / due_date / vendor;
            # we'd need to fetch the bill to know what changed. Keep
            # the implementation thin: refresh the AP item from the
            # ERP and let the workflow re-evaluate. No state change.
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
                if str(row.get("erp_type") or "").lower() == "quickbooks":
                    return _erp_connection_from_row(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qb_intake_adapter: connection lookup failed — %s", exc,
            )
        return None

    @staticmethod
    async def _fetch_bill(connection, bill_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the full Bill via QBO REST GET. Returns the raw QBO
        Bill dict on success, ``None`` on any failure (the caller
        falls back to thin-envelope intake so the AP item still
        gets created)."""
        if not bill_id:
            return None
        try:
            from clearledgr.core.http_client import get_http_client

            url = (
                f"https://quickbooks.api.intuit.com/v3/company/"
                f"{connection.realm_id}/bill/{bill_id}"
            )
            client = get_http_client()
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {connection.access_token}",
                    "Accept": "application/json",
                },
                params={"minorversion": "73"},
                timeout=30,
            )
            if response.status_code != 200:
                logger.warning(
                    "qb_intake_adapter: bill fetch %s returned %d",
                    bill_id, response.status_code,
                )
                return None
            body = response.json() or {}
            bill = body.get("Bill") if isinstance(body, dict) else None
            return bill if isinstance(bill, dict) else None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qb_intake_adapter: bill fetch raised for id=%s — %s",
                bill_id, exc,
            )
            return None

    def _thin_invoice_from_envelope(
        self, envelope: IntakeEnvelope, organization_id: str,
    ) -> InvoiceData:
        return InvoiceData(
            source_type="quickbooks",
            source_id=envelope.source_id,
            erp_native=True,
            erp_metadata={
                "qb_bill_id": envelope.source_id,
                "qb_realm_id": envelope.channel_metadata.get("qb_realm_id"),
                "fallback_thin_intake": True,
            },
            subject=f"QuickBooks Bill {envelope.source_id}",
            sender="<quickbooks@erp-native>",
            vendor_name="Unknown vendor",
            amount=0.0,
            currency="USD",
            invoice_number=envelope.source_id,
            confidence=1.0,
            organization_id=organization_id,
            correlation_id=f"erp-intake:qb:{envelope.event_id or envelope.source_id}",
        )

    @staticmethod
    def _build_invoice_from_bill(
        envelope: IntakeEnvelope,
        bill: Dict[str, Any],
        organization_id: str,
    ) -> InvoiceData:
        vendor_ref = bill.get("VendorRef") if isinstance(bill.get("VendorRef"), dict) else {}
        ap_account_ref = bill.get("APAccountRef") if isinstance(bill.get("APAccountRef"), dict) else {}
        vendor_name = str(vendor_ref.get("name") or "Unknown vendor")
        vendor_id = str(vendor_ref.get("value") or "")

        amount = _safe_float(bill.get("TotalAmt"), default=0.0)
        currency_ref = bill.get("CurrencyRef") if isinstance(bill.get("CurrencyRef"), dict) else {}
        currency = str(currency_ref.get("value") or "USD").upper()

        line_items: list = []
        for line in bill.get("Line") or []:
            if not isinstance(line, dict):
                continue
            description = str(line.get("Description") or "").strip()
            line_amount = _safe_float(line.get("Amount"))
            detail = (
                line.get("AccountBasedExpenseLineDetail")
                or line.get("ItemBasedExpenseLineDetail")
                or {}
            )
            account_ref = detail.get("AccountRef") if isinstance(detail.get("AccountRef"), dict) else {}
            line_items.append({
                "description": description or (account_ref.get("name") if account_ref else ""),
                "amount": line_amount,
                "gl_code": account_ref.get("value") if account_ref else None,
            })

        erp_metadata = {
            "qb_bill_id": str(bill.get("Id") or envelope.source_id),
            "qb_realm_id": envelope.channel_metadata.get("qb_realm_id"),
            "qb_doc_number": bill.get("DocNumber"),
            "qb_sync_token": bill.get("SyncToken"),
            "qb_vendor_id": vendor_id,
            "qb_ap_account_id": ap_account_ref.get("value"),
            "qb_balance": bill.get("Balance"),
            "qb_event_id": envelope.event_id,
        }
        erp_metadata = {k: v for k, v in erp_metadata.items() if v not in (None, "")}

        invoice_number = str(
            bill.get("DocNumber") or envelope.source_id,
        ).strip()

        return InvoiceData(
            source_type="quickbooks",
            source_id=envelope.source_id,
            erp_native=True,
            erp_metadata=erp_metadata,
            subject=f"QuickBooks Bill {invoice_number} — {vendor_name}",
            sender=f"{vendor_name} <quickbooks@erp-native>",
            vendor_name=vendor_name,
            amount=amount,
            currency=currency,
            invoice_number=invoice_number,
            due_date=str(bill.get("DueDate") or "").strip() or None,
            confidence=1.0,
            line_items=line_items or None,
            organization_id=organization_id,
            correlation_id=f"erp-intake:qb:{envelope.event_id or envelope.source_id}",
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


# Register at import time so the universal handler can find this adapter.
_QUICKBOOKS_ADAPTER = QuickBooksIntakeAdapter()
register_adapter(_QUICKBOOKS_ADAPTER)
