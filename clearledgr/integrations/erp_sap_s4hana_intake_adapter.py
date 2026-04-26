"""SAP S/4HANA implementation of the IntakeAdapter protocol.

Tolerates both BTP Event Mesh CloudEvents shape
(``sap.s4.beh.supplierinvoice.v1.SupplierInvoice.Created.v1``) and
ABAP-BAdI HTTP-push shape (``BUKRS / BELNR / GJAHR`` UPPER_SNAKE
field names). Composite key ``CompanyCode/SupplierInvoice/FiscalYear``
is the canonical source_id.

Replaces the channel-specific dispatch logic that previously lived
in ``services/sap_webhook_dispatch.py``.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Mapping, Optional

from clearledgr.core.ap_states import APState
from clearledgr.core.erp_webhook_verify import verify_sap_signature
from clearledgr.services.intake_adapter import (
    IntakeAdapter,
    IntakeEnvelope,
    StateUpdate,
    register_adapter,
)
from clearledgr.services.invoice_models import InvoiceData

logger = logging.getLogger(__name__)


_CLOUDEVENTS_SUFFIX_MAP = {
    "Created": "create",
    "Posted": "posted",
    "Blocked": "blocked",
    "Released": "released",
    "Cancelled": "cancelled",
    "Reversed": "cancelled",
    "Paid": "paid",
    "PaymentExecuted": "paid",
    "Updated": "update",
}

_SHORT_NAME_MAP = {
    "created": "create",
    "posted": "posted",
    "blocked": "blocked",
    "released": "released",
    "cancelled": "cancelled",
    "reversed": "cancelled",
    "paid": "paid",
    "payment_executed": "paid",
    "updated": "update",
}


class SapS4HanaIntakeAdapter:
    """SAP S/4HANA SupplierInvoice event intake (Cloud + on-prem)."""

    source_type = "sap_s4hana"

    async def verify_signature(
        self, raw: bytes, headers: Mapping[str, str], secret: str,
    ) -> bool:
        signature = headers.get("X-SAP-Signature") or headers.get("x-sap-signature")
        timestamp = headers.get("X-SAP-Timestamp") or headers.get("x-sap-timestamp")
        return verify_sap_signature(raw, signature, timestamp, secret)

    async def parse_envelope(
        self, raw: bytes, headers: Mapping[str, str], organization_id: str,
    ) -> IntakeEnvelope:
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload = {}
        invoice = payload.get("invoice") or payload.get("data") or {}
        if isinstance(invoice, dict) and "data" in invoice and isinstance(invoice["data"], dict):
            invoice = invoice["data"]
        if not isinstance(invoice, dict):
            invoice = {}

        event_type = self._normalize_event_type(payload)
        composite_key = self._composite_key(invoice)
        return IntakeEnvelope(
            source_type=self.source_type,
            event_type=event_type,
            source_id=composite_key,
            organization_id=organization_id,
            raw_payload=payload,
            event_id=str(payload.get("id") or payload.get("event_id") or "").strip() or None,
            received_at=None,
            channel_metadata={
                "raw_event_type": str(payload.get("type") or payload.get("event_type") or "").strip(),
                "invoice_payload": invoice,
            },
        )

    async def enrich(
        self, organization_id: str, envelope: IntakeEnvelope,
    ) -> InvoiceData:
        if not envelope.source_id or "/" not in envelope.source_id:
            return self._thin_invoice_from_envelope(envelope, organization_id)

        cc, doc, fy = envelope.source_id.split("/", 2)
        try:
            from clearledgr.integrations.erp_sap_s4hana_intake import fetch_intake_context
            intake = await fetch_intake_context(
                organization_id=organization_id,
                company_code=cc, supplier_invoice=doc, fiscal_year=fy,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sap_intake_adapter: enrichment fetch failed for %s — %s",
                envelope.source_id, exc,
            )
            return self._thin_invoice_from_envelope(envelope, organization_id)

        if not intake.get("bill_header"):
            return self._thin_invoice_from_envelope(envelope, organization_id)

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
                    "sap_intake_adapter: PO/GR upsert failed for %s — %s",
                    envelope.source_id, exc,
                )

        return self._build_invoice_from_intake(envelope, intake, organization_id)

    async def derive_state_update(
        self, organization_id: str, envelope: IntakeEnvelope,
    ) -> StateUpdate:
        invoice_payload = envelope.channel_metadata.get("invoice_payload") or {}
        if envelope.event_type == "paid":
            return StateUpdate(target_state=APState.CLOSED.value)
        if envelope.event_type == "cancelled":
            return StateUpdate(target_state=APState.CLOSED.value)
        if envelope.event_type in {"update", "blocked", "released"}:
            target_state = self._state_from_invoice(invoice_payload)
            field_updates: Dict[str, Any] = {
                k: v for k, v in {
                    "vendor_name": _pick(invoice_payload, "SupplierName", "supplier_name", "VendorName"),
                    "amount": _pick(invoice_payload, "InvoiceGrossAmount", "GrossAmount", "amount", "WRBTR"),
                    "currency": (_pick(invoice_payload, "DocumentCurrency", "Currency", "WAERS") or "").upper() or None,
                    "invoice_number": _pick(invoice_payload, "SupplierInvoiceIDByInvcgParty", "invoice_number"),
                    "due_date": _pick(invoice_payload, "NetDueDate", "due_date"),
                }.items()
                if v not in (None, "")
            }
            return StateUpdate(target_state=target_state, field_updates=field_updates)
        return StateUpdate(target_state=None)

    # ─── Internal helpers ───

    @staticmethod
    def _normalize_event_type(payload: Dict[str, Any]) -> str:
        raw = str(payload.get("type") or payload.get("event_type") or "").strip()
        if not raw:
            return ""
        if raw.startswith("sap."):
            for suffix, mapped in _CLOUDEVENTS_SUFFIX_MAP.items():
                if f".{suffix}." in raw or raw.endswith(f".{suffix}"):
                    return mapped
            return ""
        if raw.startswith("supplier_invoice."):
            short = raw.split(".", 1)[1].lower()
        elif "." in raw:
            short = raw.rsplit(".", 1)[1].lower()
        else:
            short = raw.lower()
        return _SHORT_NAME_MAP.get(short, "")

    @staticmethod
    def _composite_key(invoice: Dict[str, Any]) -> str:
        cc = _pick(invoice, "CompanyCode", "companyCode", "BUKRS", "company_code")
        doc = _pick(invoice, "SupplierInvoice", "supplierInvoice", "BELNR", "supplier_invoice", "doc_number")
        fy = _pick(invoice, "FiscalYear", "fiscalYear", "GJAHR", "fiscal_year")
        if not (cc and doc and fy):
            return ""
        return f"{cc}/{doc}/{fy}"

    @staticmethod
    def _state_from_invoice(invoice: Dict[str, Any]) -> Optional[str]:
        status = str(_pick(invoice, "InvoiceStatus", "DocumentStatus", "status") or "").strip().lower()
        if "paid" in status or "cleared" in status:
            return APState.CLOSED.value
        if "reverse" in status or "cancel" in status:
            return APState.CLOSED.value
        payment_block = _pick(invoice, "PaymentBlockingReason", "PaymentBlock", "ZLSPR")
        if payment_block and str(payment_block).strip() not in {"", " ", "0"}:
            return APState.NEEDS_APPROVAL.value
        return APState.POSTED_TO_ERP.value

    def _thin_invoice_from_envelope(
        self, envelope: IntakeEnvelope, organization_id: str,
    ) -> InvoiceData:
        payload = envelope.channel_metadata.get("invoice_payload") or {}
        cc, doc, fy = (envelope.source_id.split("/", 2) + ["", "", ""])[:3]
        return InvoiceData(
            source_type="sap_s4hana",
            source_id=envelope.source_id,
            erp_native=True,
            erp_metadata={
                "company_code": cc, "supplier_invoice": doc, "fiscal_year": fy,
                "fallback_thin_intake": True,
            },
            subject=f"SAP Supplier Invoice {doc} — {_pick(payload, 'SupplierName', 'supplier_name') or 'vendor'}",
            sender=f"{_pick(payload, 'SupplierName', 'supplier_name') or 'vendor'} <sap-s4hana@erp-native>",
            vendor_name=_pick(payload, "SupplierName", "supplier_name") or "Unknown supplier",
            amount=_safe_float(_pick(payload, "InvoiceGrossAmount", "GrossAmount", "amount", "WRBTR"), default=0.0),
            currency=str(_pick(payload, "DocumentCurrency", "Currency", "WAERS") or "USD").upper(),
            invoice_number=_pick(payload, "SupplierInvoiceIDByInvcgParty", "invoice_number") or doc,
            due_date=_pick(payload, "NetDueDate", "due_date") or None,
            confidence=1.0,
            organization_id=organization_id,
            correlation_id=f"erp-intake:{envelope.event_id or envelope.source_id}",
        )

    @staticmethod
    def _build_invoice_from_intake(
        envelope: IntakeEnvelope, intake: Dict[str, Any], organization_id: str,
    ) -> InvoiceData:
        header = intake.get("bill_header") or {}
        bill_lines = intake.get("bill_lines") or []
        vendor = intake.get("vendor") or {}
        bank_history = intake.get("vendor_bank_history") or []

        cc, doc, fy = envelope.source_id.split("/", 2)

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
            "sap_intake_event": envelope.event_type,
            "sap_event_id": envelope.event_id,
            "po_numbers": list({
                str(line.get("purchase_order") or "").strip()
                for line in bill_lines if line.get("purchase_order")
            }),
            "material_doc_ids": [
                f"{md.get('MaterialDocument','')}/{md.get('MaterialDocumentYear','')}"
                for md in (intake.get("material_documents") or [])
                if isinstance(md, dict)
            ],
        }
        erp_metadata = {k: v for k, v in erp_metadata.items() if v not in (None, "", [])}

        return InvoiceData(
            source_type="sap_s4hana",
            source_id=envelope.source_id,
            erp_native=True,
            erp_metadata=erp_metadata,
            subject=f"SAP Supplier Invoice {header.get('invoice_number') or doc} — {header.get('supplier_name') or 'vendor'}",
            sender=sender,
            vendor_name=header.get("supplier_name") or "Unknown supplier",
            amount=_safe_float(header.get("amount")) or 0.0,
            currency=str(header.get("currency") or "USD").upper(),
            invoice_number=str(header.get("invoice_number") or doc).strip(),
            due_date=str(header.get("due_date") or "").strip() or None,
            po_number=po_number or None,
            confidence=1.0,
            bank_details=bank_details,
            line_items=line_items or None,
            field_confidences=field_confidences,
            organization_id=organization_id,
            correlation_id=f"erp-intake:{envelope.event_id or envelope.source_id}",
            tax_amount=_safe_float(header.get("tax_amount")) or None,
        )


def _safe_float(value: Any, *, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pick(payload: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        if k in payload and payload[k] not in (None, ""):
            return str(payload[k]).strip()
    return ""


# Register at import time.
_SAP_ADAPTER = SapS4HanaIntakeAdapter()
register_adapter(_SAP_ADAPTER)
