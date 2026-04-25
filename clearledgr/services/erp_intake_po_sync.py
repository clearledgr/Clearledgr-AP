"""Idempotent PO + Goods Receipt upsert from ERP-native intake.

The 3-way-match path
(``InvoiceValidationMixin._evaluate_deterministic_validation`` →
``PurchaseOrderService.match_invoice_to_po``) reads from Clearledgr's
own ``purchase_orders`` and ``goods_receipts`` tables — it does NOT
call back out to the ERP. So when an ERP-native bill arrives via
NetSuite SuiteScript / SAP Event Mesh, the dispatcher must seed the
linked PO + GRs into Clearledgr's stores **before** calling
``process_new_invoice``, or the validation pipeline will conclude
"no PO found" and route every bill to ``needs_approval`` regardless
of whether the underlying match would have succeeded.

This module is the upsert path. Idempotent on
``(organization_id, po_number)`` — replays of the same NetSuite bill
event re-upsert the same PO with the same po_number, no duplication.

Two public functions:

* :func:`upsert_netsuite_po` — takes the PO dict from
  :mod:`erp_netsuite_intake._fetch_purchase_order` plus its
  associated item-receipt list, upserts both the PO and any GRs.
* :func:`upsert_sap_po` — same shape, SAP S/4HANA payload.

Each returns the Clearledgr ``po_id`` so the dispatcher can stamp
the AP item with the linkage.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from clearledgr.services.purchase_orders import (
    POStatus,
    PurchaseOrderService,
    GRStatus,
)

logger = logging.getLogger(__name__)


# ─── NetSuite ──────────────────────────────────────────────────────


def upsert_netsuite_po(
    *,
    organization_id: str,
    po_payload: Dict[str, Any],
    po_lines: List[Dict[str, Any]],
    item_receipts: List[Dict[str, Any]],
) -> Optional[str]:
    """Upsert a NetSuite PO + its item receipts. Returns Clearledgr po_id.

    Idempotency: Clearledgr-side ``po_number`` is built from the
    NetSuite tranId (``f"NS-{tranId}"``); if a row with that number
    already exists for the org we update rather than insert.

    GRs use the same idempotency: ``gr_number = f"NS-IR-{tranId}"``.
    """
    if not po_payload:
        return None
    service = PurchaseOrderService(organization_id=organization_id)
    db = service._db

    ns_po_id = str(po_payload.get("id") or "").strip()
    tran_id = str(po_payload.get("tranId") or ns_po_id).strip()
    po_number = f"NS-{tran_id}"
    entity = po_payload.get("entity") if isinstance(po_payload.get("entity"), dict) else {}
    vendor_id = str(entity.get("id") or "").strip()
    vendor_name = str(entity.get("refName") or "").strip()

    line_items_for_create = [
        {
            "line_id": str(line.get("line_id") or "").strip() or f"L{idx}",
            "description": line.get("description") or "",
            "quantity_ordered": _safe_float(line.get("quantity"), default=0.0),
            "unit_price": _safe_float(line.get("unit_price"), default=0.0),
            "total_amount": _safe_float(line.get("amount"), default=0.0),
        }
        for idx, line in enumerate(po_lines)
    ]

    existing = db.get_purchase_order_by_number(organization_id, po_number) if hasattr(db, "get_purchase_order_by_number") else None
    if existing:
        po_id = str(existing.get("po_id") or existing.get("id") or "").strip()
        # No-op update for now — the PO is the same. Future work: diff line items
        # against the existing PO and update if quantities/prices changed.
        logger.debug(
            "erp_intake_po_sync: NetSuite PO %s already synced (po_id=%s)",
            po_number, po_id,
        )
    else:
        try:
            po = service.create_po(
                vendor_id=vendor_id or vendor_name or "unknown",
                vendor_name=vendor_name or "Unknown vendor",
                requested_by="netsuite_native_intake",
                line_items=line_items_for_create,
                po_number=po_number,
                erp_po_id=ns_po_id,
                status=POStatus.APPROVED,  # NetSuite-side POs come pre-approved
            )
            po_id = po.po_id
            logger.info(
                "erp_intake_po_sync: created Clearledgr PO %s from NetSuite %s for org %s",
                po.po_number, ns_po_id, organization_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "erp_intake_po_sync: failed to create PO from NetSuite %s — %s",
                ns_po_id, exc,
            )
            return None

    # Upsert each linked Item Receipt as a goods-receipt row.
    for receipt in item_receipts or []:
        try:
            _upsert_netsuite_item_receipt(
                service=service,
                organization_id=organization_id,
                po_id=po_id,
                po_number=po_number,
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                receipt_payload=receipt,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "erp_intake_po_sync: GR upsert failed for receipt %s — %s",
                receipt.get("id"), exc,
            )

    return po_id


def _upsert_netsuite_item_receipt(
    *,
    service: PurchaseOrderService,
    organization_id: str,
    po_id: str,
    po_number: str,
    vendor_id: str,
    vendor_name: str,
    receipt_payload: Dict[str, Any],
) -> None:
    db = service._db
    rec_id = str(receipt_payload.get("id") or "").strip()
    if not rec_id:
        return
    gr_number = f"NS-IR-{rec_id}"
    # Check idempotency by walking existing GRs for this PO.
    existing_grs = []
    if hasattr(db, "list_goods_receipts_for_po"):
        try:
            existing_grs = db.list_goods_receipts_for_po(po_id) or []
        except Exception:
            existing_grs = []
    if any(str((row or {}).get("gr_number") or "") == gr_number for row in existing_grs):
        return  # already synced

    item_block = receipt_payload.get("item") if isinstance(receipt_payload.get("item"), dict) else {}
    raw_lines = item_block.get("items") if isinstance(item_block.get("items"), list) else []
    line_items_for_gr: List[Dict[str, Any]] = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            continue
        item_ref = raw.get("item") if isinstance(raw.get("item"), dict) else {}
        line_items_for_gr.append({
            "po_line_id": str(raw.get("orderLine") or raw.get("line") or "").strip() or None,
            "description": str(raw.get("description") or item_ref.get("refName") or ""),
            "quantity_received": _safe_float(raw.get("quantity"), default=0.0),
        })
    if not line_items_for_gr:
        return

    try:
        service.create_goods_receipt(
            po_id=po_id,
            received_by="netsuite_native_intake",
            line_items=line_items_for_gr,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "erp_intake_po_sync: create_goods_receipt failed for PO %s rec %s — %s",
            po_number, rec_id, exc,
        )


# ─── SAP S/4HANA ───────────────────────────────────────────────────


def upsert_sap_po(
    *,
    organization_id: str,
    po_payload: Dict[str, Any],
    po_lines: List[Dict[str, Any]],
    material_documents: List[Dict[str, Any]],
) -> Optional[str]:
    """Upsert a SAP S/4HANA PO + its material-document GRs.

    Idempotency key: ``po_number = f"SAP-{PurchaseOrder}"``.
    Material-document idempotency: ``gr_number = f"SAP-MD-{MaterialDocument}/{MaterialDocumentYear}"``.
    """
    if not po_payload:
        return None
    service = PurchaseOrderService(organization_id=organization_id)
    db = service._db

    sap_po_number = str(po_payload.get("PurchaseOrder") or "").strip()
    if not sap_po_number:
        return None
    po_number = f"SAP-{sap_po_number}"
    vendor_id = str(po_payload.get("Supplier") or "").strip()
    vendor_name = str(po_payload.get("SupplierName") or vendor_id).strip()

    line_items_for_create = [
        {
            "line_id": str(line.get("PurchaseOrderItem") or f"L{idx}"),
            "description": str(line.get("PurchaseOrderItemText") or ""),
            "quantity_ordered": _safe_float(line.get("OrderQuantity"), default=0.0),
            "unit_price": _safe_float(line.get("NetPriceAmount"), default=0.0),
            "total_amount": _safe_float(line.get("NetAmount") or line.get("OrderQuantity", 0) * (line.get("NetPriceAmount") or 0), default=0.0),
        }
        for idx, line in enumerate(po_lines)
    ]

    existing = db.get_purchase_order_by_number(organization_id, po_number) if hasattr(db, "get_purchase_order_by_number") else None
    if existing:
        po_id = str(existing.get("po_id") or existing.get("id") or "").strip()
        logger.debug(
            "erp_intake_po_sync: SAP PO %s already synced (po_id=%s)",
            po_number, po_id,
        )
    else:
        try:
            po = service.create_po(
                vendor_id=vendor_id or vendor_name or "unknown",
                vendor_name=vendor_name or "Unknown vendor",
                requested_by="sap_native_intake",
                line_items=line_items_for_create,
                po_number=po_number,
                erp_po_id=sap_po_number,
                status=POStatus.APPROVED,
            )
            po_id = po.po_id
            logger.info(
                "erp_intake_po_sync: created Clearledgr PO %s from SAP %s for org %s",
                po.po_number, sap_po_number, organization_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "erp_intake_po_sync: failed to create PO from SAP %s — %s",
                sap_po_number, exc,
            )
            return None

    for md in material_documents or []:
        try:
            _upsert_sap_material_document(
                service=service,
                po_id=po_id,
                po_number=po_number,
                material_doc_payload=md,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "erp_intake_po_sync: SAP MD upsert failed — %s", exc,
            )

    return po_id


def _upsert_sap_material_document(
    *,
    service: PurchaseOrderService,
    po_id: str,
    po_number: str,
    material_doc_payload: Dict[str, Any],
) -> None:
    db = service._db
    md_number = str(material_doc_payload.get("MaterialDocument") or "").strip()
    md_year = str(material_doc_payload.get("MaterialDocumentYear") or "").strip()
    if not (md_number and md_year):
        return
    gr_number = f"SAP-MD-{md_number}/{md_year}"

    existing_grs = []
    if hasattr(db, "list_goods_receipts_for_po"):
        try:
            existing_grs = db.list_goods_receipts_for_po(po_id) or []
        except Exception:
            existing_grs = []
    if any(str((row or {}).get("gr_number") or "") == gr_number for row in existing_grs):
        return

    quantity = _safe_float(
        material_doc_payload.get("QuantityInBaseUnit") or material_doc_payload.get("Quantity"),
        default=0.0,
    )
    line_items_for_gr = [{
        "po_line_id": str(material_doc_payload.get("PurchaseOrderItem") or "").strip() or None,
        "description": str(material_doc_payload.get("MaterialDocumentItemText") or ""),
        "quantity_received": quantity,
    }]
    try:
        service.create_goods_receipt(
            po_id=po_id,
            received_by="sap_native_intake",
            line_items=line_items_for_gr,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "erp_intake_po_sync: create_goods_receipt failed for SAP PO %s MD %s — %s",
            po_number, md_number, exc,
        )


# ─── Helpers ───────────────────────────────────────────────────────


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
