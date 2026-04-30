"""NetSuite implementation of the IntakeAdapter protocol.

Wraps the existing read-direction enrichment in
:mod:`erp_netsuite_intake` and the existing PO/GR upsert in
:mod:`erp_intake_po_sync` behind the channel-agnostic
:class:`IntakeAdapter` interface, so the universal
:func:`handle_intake_event` dispatch can drive NetSuite intake the
same way it drives every other channel.

Replaces the channel-specific dispatch logic that previously lived
in ``services/erp_webhook_dispatch.py``. The dispatch branching
(create / update / paid / cancelled) now lives in the universal
handler; this adapter is intake-side only — signature verification,
envelope parsing, enrichment, state-update derivation.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Mapping, Optional

from clearledgr.core.ap_states import APState
from clearledgr.core.database import get_db
from clearledgr.core.erp_webhook_verify import verify_netsuite_signature
from clearledgr.integrations.erp_router import _erp_connection_from_row
from clearledgr.services.intake_adapter import (
    IntakeEnvelope,
    StateUpdate,
    register_adapter,
)
from clearledgr.services.invoice_models import InvoiceData

logger = logging.getLogger(__name__)


_NETSUITE_EVENT_MAP = {
    "vendorbill.create": "create",
    "vendorbill.update": "update",
    "vendorbill.paid": "paid",
    "vendorbill.delete": "delete",
}


class NetSuiteIntakeAdapter:
    """NetSuite SuiteScript afterSubmit webhook intake."""

    source_type = "netsuite"

    async def verify_signature(
        self, raw: bytes, headers: Mapping[str, str], secret: str,
    ) -> bool:
        signature = headers.get("X-NetSuite-Signature") or headers.get("x-netsuite-signature")
        timestamp = headers.get("X-NetSuite-Timestamp") or headers.get("x-netsuite-timestamp")
        return verify_netsuite_signature(raw, signature, timestamp, secret)

    async def parse_envelope(
        self, raw: bytes, headers: Mapping[str, str], organization_id: str,
    ) -> IntakeEnvelope:
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload = {}
        bill = payload.get("bill") or {}
        ns_internal_id = str(bill.get("ns_internal_id") or "").strip()
        raw_event_type = str(payload.get("event_type") or "").strip().lower()
        event_type = _NETSUITE_EVENT_MAP.get(raw_event_type, "")
        return IntakeEnvelope(
            source_type=self.source_type,
            event_type=event_type,
            source_id=ns_internal_id,
            organization_id=organization_id,
            raw_payload=payload,
            event_id=str(payload.get("event_id") or "").strip() or None,
            received_at=str(payload.get("occurred_at") or "").strip() or None,
            channel_metadata={
                "ns_account_id": str(payload.get("account_id") or "").strip(),
                "raw_event_type": raw_event_type,
            },
        )

    async def enrich(
        self, organization_id: str, envelope: IntakeEnvelope,
    ) -> InvoiceData:
        connection = self._resolve_connection(organization_id)
        if connection is None:
            # Build a thin InvoiceData from the envelope alone — the
            # downstream pipeline handles missing PO/vendor enrichment
            # by routing to needs_approval.
            return self._thin_invoice_from_envelope(envelope, organization_id)

        from clearledgr.integrations.erp_netsuite_intake import fetch_intake_context
        intake = await fetch_intake_context(connection, envelope.source_id)
        if not intake.get("bill_header"):
            return self._thin_invoice_from_envelope(envelope, organization_id)

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
                    "netsuite_intake_adapter: PO/GR upsert failed for ns=%s — %s",
                    envelope.source_id, exc,
                )

        return self._build_invoice_from_intake(envelope, intake, organization_id)

    async def derive_state_update(
        self, organization_id: str, envelope: IntakeEnvelope,
    ) -> StateUpdate:
        bill = envelope.raw_payload.get("bill") or {}
        if envelope.event_type == "paid":
            return StateUpdate(target_state=APState.CLOSED.value)
        if envelope.event_type == "delete":
            return StateUpdate(target_state=APState.CLOSED.value)
        if envelope.event_type == "update":
            target_state = self._state_from_bill(bill)
            field_updates: Dict[str, Any] = {
                k: v for k, v in {
                    "vendor_name": bill.get("entity_name") or bill.get("entity_id"),
                    "amount": bill.get("amount"),
                    "currency": (str(bill.get("currency") or "").upper() or None),
                    "invoice_number": bill.get("invoice_number"),
                    "due_date": bill.get("due_date"),
                }.items()
                if v not in (None, "")
            }
            return StateUpdate(target_state=target_state, field_updates=field_updates)
        return StateUpdate(target_state=None)

    # ─── Internal helpers ───

    @staticmethod
    def _state_from_bill(bill: Dict[str, Any]) -> Optional[str]:
        status_label = str(bill.get("status_label") or bill.get("status") or "").strip().lower()
        if "paid" in status_label and "in full" in status_label:
            return APState.CLOSED.value
        payment_hold = str(bill.get("payment_hold") or "").strip().upper()
        if payment_hold in {"T", "TRUE", "Y", "YES", "1"}:
            return APState.NEEDS_APPROVAL.value
        return APState.POSTED_TO_ERP.value

    @staticmethod
    def _resolve_connection(organization_id: str):
        db = get_db()
        if not hasattr(db, "get_erp_connections"):
            return None
        try:
            for row in db.get_erp_connections(organization_id):
                if str(row.get("erp_type") or "").lower() == "netsuite":
                    return _erp_connection_from_row(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "netsuite_intake_adapter: connection lookup failed — %s", exc,
            )
        return None

    def _thin_invoice_from_envelope(
        self, envelope: IntakeEnvelope, organization_id: str,
    ) -> InvoiceData:
        bill = envelope.raw_payload.get("bill") or {}
        return InvoiceData(
            source_type="netsuite",
            source_id=envelope.source_id,
            erp_native=True,
            erp_metadata={
                "ns_internal_id": envelope.source_id,
                "ns_account_id": envelope.channel_metadata.get("ns_account_id"),
                "fallback_thin_intake": True,
            },
            subject=f"NetSuite Bill {bill.get('invoice_number') or envelope.source_id} — {bill.get('entity_name') or 'vendor'}",
            sender=f"{bill.get('entity_name') or 'vendor'} <netsuite@erp-native>",
            vendor_name=bill.get("entity_name") or "Unknown vendor",
            amount=_safe_float(bill.get("amount"), default=0.0),
            currency=str(bill.get("currency") or "USD").upper(),
            invoice_number=str(bill.get("invoice_number") or envelope.source_id).strip(),
            due_date=str(bill.get("due_date") or "").strip() or None,
            confidence=1.0,
            organization_id=organization_id,
            correlation_id=f"erp-intake:{envelope.event_id or envelope.source_id}",
        )

    @staticmethod
    def _build_invoice_from_intake(
        envelope: IntakeEnvelope,
        intake: Dict[str, Any],
        organization_id: str,
    ) -> InvoiceData:
        header = intake.get("bill_header") or {}
        bill_lines = intake.get("bill_lines") or []
        expense_lines = intake.get("expense_lines") or []
        vendor = intake.get("vendor") or {}
        bank_history = intake.get("vendor_bank_history") or []

        vendor_email = ""
        if isinstance(vendor, dict):
            vendor_email = str(vendor.get("email") or "").strip()
        sender = vendor_email or f"{header.get('vendor_name') or 'vendor'} <netsuite@erp-native>"

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

        po_number = ""
        for line in bill_lines:
            candidate = str(line.get("po_number") or "").strip()
            if candidate:
                po_number = candidate
                break

        field_confidences = {
            "vendor_name": 1.0, "amount": 1.0, "currency": 1.0,
            "invoice_number": 1.0, "invoice_date": 1.0, "due_date": 1.0,
            "po_number": 1.0 if po_number else 0.0,
        }

        erp_metadata = {
            "ns_internal_id": envelope.source_id,
            "ns_account_id": envelope.channel_metadata.get("ns_account_id"),
            "ns_subsidiary_id": header.get("subsidiary_id"),
            "ns_subsidiary_name": header.get("subsidiary_name"),
            "ns_status": header.get("status"),
            "ns_approval_status": header.get("approval_status"),
            "ns_payment_hold": header.get("payment_hold"),
            "ns_external_id": header.get("external_id"),
            "ns_event_id": envelope.event_id,
            "ns_po_internal_id": (
                (intake.get("linked_po") or {}).get("id")
                if isinstance(intake.get("linked_po"), dict) else None
            ),
            "ns_item_receipt_ids": [
                str((rec or {}).get("id") or "")
                for rec in (intake.get("goods_receipts") or [])
            ],
        }
        erp_metadata = {k: v for k, v in erp_metadata.items() if v not in (None, "", [])}

        return InvoiceData(
            source_type="netsuite",
            source_id=envelope.source_id,
            erp_native=True,
            erp_metadata=erp_metadata,
            subject=f"NetSuite Bill {header.get('tran_id') or envelope.source_id} — {header.get('vendor_name') or 'vendor'}",
            sender=sender,
            vendor_name=header.get("vendor_name") or "Unknown vendor",
            amount=_safe_float(header.get("amount"), default=0.0),
            currency=str(header.get("currency_id") or "USD").upper(),
            invoice_number=str(header.get("tran_id") or envelope.source_id).strip(),
            due_date=str(header.get("due_date") or "").strip() or None,
            po_number=po_number or None,
            confidence=1.0,
            bank_details=bank_details,
            line_items=line_items or None,
            field_confidences=field_confidences,
            organization_id=organization_id,
            correlation_id=f"erp-intake:{envelope.event_id or envelope.source_id}",
            tax_amount=_safe_float(header.get("tax_amount")) or None,
            subtotal=_safe_float(header.get("subtotal")) or None,
        )


def _safe_float(value: Any, *, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# Register at import time so the universal handler can find this adapter.
_NETSUITE_ADAPTER = NetSuiteIntakeAdapter()
register_adapter(_NETSUITE_ADAPTER)
