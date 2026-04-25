"""SAP S/4HANA read-direction enrichment for ERP-native bill intake.

The SAP counterpart of :mod:`erp_netsuite_intake`. When the BTP
Event Mesh subscription / ABAP-BAdI fires a webhook for a
SupplierInvoice event, this module pulls the full coordination
context the email-arrival pipeline needs: vendor master, full
supplier-invoice with item lines + GL distribution, linked
PurchaseOrder + items, MaterialDocuments (S/4HANA's GRN
equivalent), supplier bank history.

Public entry point: :func:`fetch_intake_context`.

Service paths consumed (overridable via
``erp_connections.credentials``):

* ``API_SUPPLIERINVOICE_PROCESS_SRV`` — supplier invoice + lines.
* ``API_PURCHASEORDER_PROCESS_SRV`` — purchase order + lines.
* ``API_SUPPLIER_SRV`` — supplier master + bank.
* ``API_MATERIAL_DOCUMENT_SRV`` — material documents (GRNs) for
  the PO.

Each individual sub-fetch is wrapped in try/except — a missing
optional record (no PO yet, no supplier bank info) returns an empty
section rather than failing the whole intake.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TypedDict

from clearledgr.integrations.erp_sap_s4hana import (
    get_material_documents_for_po,
    get_purchase_order_s4hana,
    get_supplier,
    get_supplier_invoice_with_items,
)

logger = logging.getLogger(__name__)


class IntakeContext(TypedDict, total=False):
    bill_header: Dict[str, Any]
    bill_lines: List[Dict[str, Any]]
    vendor: Optional[Dict[str, Any]]
    linked_po: Optional[Dict[str, Any]]
    linked_po_lines: List[Dict[str, Any]]
    material_documents: List[Dict[str, Any]]
    vendor_bank_history: List[Dict[str, Any]]
    raw_payload: Dict[str, Any]


async def fetch_intake_context(
    *,
    organization_id: str,
    company_code: str,
    supplier_invoice: str,
    fiscal_year: str,
) -> IntakeContext:
    """Pull every enrichment field the coordination pipeline needs.

    Best-effort same as the NetSuite path: a missing PO / bank
    record returns an empty section rather than failing the whole
    intake.
    """
    context: IntakeContext = {
        "bill_header": {},
        "bill_lines": [],
        "vendor": None,
        "linked_po": None,
        "linked_po_lines": [],
        "material_documents": [],
        "vendor_bank_history": [],
        "raw_payload": {},
    }

    invoice_resp = await get_supplier_invoice_with_items(
        organization_id=organization_id,
        company_code=company_code,
        supplier_invoice=supplier_invoice,
        fiscal_year=fiscal_year,
    )
    if not invoice_resp.get("ok"):
        logger.warning(
            "sap_intake: supplier-invoice fetch failed cc=%s doc=%s fy=%s — %s",
            company_code, supplier_invoice, fiscal_year, invoice_resp,
        )
        return context

    raw = _unwrap_odata_entity(invoice_resp.get("data") or {})
    context["raw_payload"] = raw
    context["bill_header"] = _extract_bill_header(raw)
    context["bill_lines"] = _extract_bill_item_lines(raw)

    # Supplier — `Supplier` field on the invoice header is the supplier id.
    supplier_id = str(context["bill_header"].get("supplier") or "").strip()
    if supplier_id:
        try:
            supplier_resp = await get_supplier(
                organization_id=organization_id, supplier_id=supplier_id,
            )
            if supplier_resp.get("ok"):
                supplier_payload = _unwrap_odata_entity(supplier_resp.get("data") or {})
                context["vendor"] = supplier_payload
                context["vendor_bank_history"] = _extract_supplier_bank(supplier_payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sap_intake: supplier fetch failed for %s — %s", supplier_id, exc)

    # PO — pick the first PO referenced in any line. SAP Cloud schema
    # carries `PurchaseOrder` inline on each `A_SupplierInvoiceItem` row.
    po_numbers = _extract_po_numbers(context["bill_lines"])
    if po_numbers:
        primary_po = po_numbers[0]
        try:
            po_resp = await get_purchase_order_s4hana(
                organization_id=organization_id, purchase_order=primary_po,
            )
            if po_resp.get("ok"):
                po_payload = _unwrap_odata_entity(po_resp.get("data") or {})
                context["linked_po"] = po_payload
                context["linked_po_lines"] = _extract_po_lines(po_payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sap_intake: PO fetch failed for %s — %s", primary_po, exc)

        # Material documents (GRNs) for the PO.
        try:
            md_resp = await get_material_documents_for_po(
                organization_id=organization_id, purchase_order=primary_po,
            )
            if md_resp.get("ok"):
                md_data = md_resp.get("data") or {}
                # OData v2 returns `{"d": {"results": [...]}}` for collections
                d = md_data.get("d") if isinstance(md_data, dict) else {}
                results = d.get("results") if isinstance(d, dict) else md_data.get("value") or md_data.get("results")
                if isinstance(results, list):
                    context["material_documents"] = [r for r in results if isinstance(r, dict)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("sap_intake: material-doc fetch failed for PO %s — %s", primary_po, exc)

    return context


# ─── Field extractors ──────────────────────────────────────────────


def _unwrap_odata_entity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """OData v2 wraps single entities in ``{"d": {...}}``. v4 returns
    them flat. Tolerate both."""
    if not isinstance(payload, dict):
        return {}
    if "d" in payload and isinstance(payload["d"], dict):
        return payload["d"]
    return payload


def _extract_bill_header(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "company_code": str(payload.get("CompanyCode") or ""),
        "supplier_invoice": str(payload.get("SupplierInvoice") or ""),
        "fiscal_year": str(payload.get("FiscalYear") or ""),
        "supplier": str(payload.get("Supplier") or ""),
        "supplier_name": str(payload.get("SupplierName") or ""),
        "amount": payload.get("InvoiceGrossAmount") or payload.get("GrossAmount"),
        "currency": str(payload.get("DocumentCurrency") or "").upper(),
        "tax_amount": payload.get("TaxAmount"),
        "invoice_number": str(payload.get("SupplierInvoiceIDByInvcgParty") or ""),
        "invoice_date": str(payload.get("InvoiceDate") or payload.get("DocumentDate") or ""),
        "due_date": str(payload.get("NetDueDate") or ""),
        "payment_block": str(payload.get("PaymentBlockingReason") or ""),
        "status": str(payload.get("InvoiceStatus") or payload.get("DocumentStatus") or ""),
        "memo": str(payload.get("HeaderText") or ""),
    }


def _extract_bill_item_lines(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract line items from the expanded `to_SupplierInvoiceItem`
    navigation property. OData v2 nests under
    ``{"to_SupplierInvoiceItem": {"results": [...]}}``; v4 flattens
    to a list. Tolerate both."""
    nav = payload.get("to_SupplierInvoiceItem")
    items = []
    if isinstance(nav, dict) and isinstance(nav.get("results"), list):
        items = nav["results"]
    elif isinstance(nav, list):
        items = nav
    out: List[Dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        out.append({
            "line_number": raw.get("SupplierInvoiceItem"),
            "description": str(raw.get("SupplierInvoiceItemText") or ""),
            "quantity": raw.get("QuantityInPurchaseOrderUnit"),
            "unit_price": (
                None
                if not raw.get("QuantityInPurchaseOrderUnit") or float(raw.get("QuantityInPurchaseOrderUnit") or 0) == 0
                else (float(raw.get("SupplierInvoiceItemAmount") or 0) / float(raw.get("QuantityInPurchaseOrderUnit") or 1))
            ),
            "amount": raw.get("SupplierInvoiceItemAmount"),
            "gl_code": str(raw.get("GLAccount") or ""),
            "tax_amount": raw.get("TaxAmount"),
            "purchase_order": str(raw.get("PurchaseOrder") or ""),
            "purchase_order_item": str(raw.get("PurchaseOrderItem") or ""),
        })
    return out


def _extract_po_numbers(bill_lines: List[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for line in bill_lines:
        po = str(line.get("purchase_order") or "").strip()
        if po and po not in seen:
            seen.append(po)
    return seen


def _extract_po_lines(po_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    nav = po_payload.get("to_PurchaseOrderItem")
    items = []
    if isinstance(nav, dict) and isinstance(nav.get("results"), list):
        items = nav["results"]
    elif isinstance(nav, list):
        items = nav
    out: List[Dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        out.append(raw)  # keep the raw shape so upsert_sap_po reads field names directly
    return out


def _extract_supplier_bank(supplier_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    nav = supplier_payload.get("to_SupplierBank")
    items = []
    if isinstance(nav, dict) and isinstance(nav.get("results"), list):
        items = nav["results"]
    elif isinstance(nav, list):
        items = nav
    out: List[Dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        out.append({
            "iban": str(raw.get("IBAN") or "").strip() or None,
            "account_number": str(raw.get("BankAccount") or "").strip() or None,
            "swift": str(raw.get("SWIFTCode") or "").strip() or None,
            "bank_name": str(raw.get("BankName") or "").strip() or None,
            "is_default": False,  # SAP doesn't expose this concept directly; we treat the first as primary
            "source": "to_SupplierBank",
        })
    return out
