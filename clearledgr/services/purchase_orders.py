"""
Purchase Order Management Service

Complete PO lifecycle:
- PO Creation and management
- PO-to-Invoice matching
- 3-Way matching (PO + Goods Receipt + Invoice)
- Match exceptions and tolerances
"""

import logging
from datetime import datetime, date, timezone
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import uuid

logger = logging.getLogger(__name__)


class POStatus(Enum):
    """Purchase Order status."""
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PARTIALLY_RECEIVED = "partially_received"
    FULLY_RECEIVED = "fully_received"
    PARTIALLY_INVOICED = "partially_invoiced"
    FULLY_INVOICED = "fully_invoiced"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class GRStatus(Enum):
    """Goods Receipt status."""
    PENDING = "pending"
    RECEIVED = "received"
    PARTIAL = "partial"
    REJECTED = "rejected"


class MatchStatus(Enum):
    """3-Way match status."""
    PENDING = "pending"
    MATCHED = "matched"
    PARTIAL_MATCH = "partial_match"
    EXCEPTION = "exception"
    OVERRIDE = "override"


class MatchExceptionType(Enum):
    """Types of match exceptions."""
    QUANTITY_MISMATCH = "quantity_mismatch"
    PRICE_MISMATCH = "price_mismatch"
    NO_PO = "no_po"
    NO_GR = "no_gr"
    DUPLICATE_INVOICE = "duplicate_invoice"
    OVER_RECEIPT = "over_receipt"
    OVER_INVOICE = "over_invoice"


@dataclass
class POLineItem:
    """Line item on a purchase order."""
    line_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    item_number: str = ""
    description: str = ""
    quantity: float = 0.0
    unit_price: float = 0.0
    unit_of_measure: str = "EA"
    gl_code: str = ""
    cost_center: str = ""
    tax_code: str = ""
    
    # Received/Invoiced tracking
    quantity_received: float = 0.0
    quantity_invoiced: float = 0.0
    
    @property
    def line_total(self) -> float:
        return round(self.quantity * self.unit_price, 2)
    
    @property
    def quantity_open(self) -> float:
        return self.quantity - self.quantity_received
    
    @property
    def is_fully_received(self) -> bool:
        return self.quantity_received >= self.quantity
    
    @property
    def is_fully_invoiced(self) -> bool:
        return self.quantity_invoiced >= self.quantity
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "line_id": self.line_id,
            "item_number": self.item_number,
            "description": self.description,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "unit_of_measure": self.unit_of_measure,
            "gl_code": self.gl_code,
            "cost_center": self.cost_center,
            "line_total": self.line_total,
            "quantity_received": self.quantity_received,
            "quantity_invoiced": self.quantity_invoiced,
            "quantity_open": self.quantity_open,
            "is_fully_received": self.is_fully_received,
            "is_fully_invoiced": self.is_fully_invoiced,
        }


@dataclass
class PurchaseOrder:
    """Purchase Order."""
    po_id: str = field(default_factory=lambda: f"PO-{uuid.uuid4().hex[:8].upper()}")
    po_number: str = ""
    
    # Vendor
    vendor_id: str = ""
    vendor_name: str = ""
    
    # Dates
    order_date: date = field(default_factory=date.today)
    expected_delivery: Optional[date] = None
    
    # Lines
    line_items: List[POLineItem] = field(default_factory=list)
    
    # Totals
    subtotal: float = 0.0
    tax_amount: float = 0.0
    total_amount: float = 0.0
    currency: str = "USD"
    
    # Status
    status: POStatus = POStatus.DRAFT
    
    # Approval
    requested_by: str = ""
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    
    # Metadata
    notes: str = ""
    department: str = ""
    project: str = ""
    ship_to_address: str = ""
    
    # Tracking
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    organization_id: str = "default"
    
    # ERP Integration
    erp_po_id: str = ""
    
    def calculate_totals(self):
        """Recalculate totals from line items."""
        self.subtotal = sum(item.line_total for item in self.line_items)
        self.total_amount = self.subtotal + self.tax_amount
    
    def add_line_item(self, item: POLineItem):
        """Add a line item and recalculate."""
        self.line_items.append(item)
        self.calculate_totals()
    
    @property
    def is_fully_received(self) -> bool:
        return all(item.is_fully_received for item in self.line_items)
    
    @property
    def is_fully_invoiced(self) -> bool:
        return all(item.is_fully_invoiced for item in self.line_items)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "po_id": self.po_id,
            "po_number": self.po_number,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "order_date": self.order_date.isoformat(),
            "expected_delivery": self.expected_delivery.isoformat() if self.expected_delivery else None,
            "line_items": [item.to_dict() for item in self.line_items],
            "subtotal": self.subtotal,
            "tax_amount": self.tax_amount,
            "total_amount": self.total_amount,
            "currency": self.currency,
            "status": self.status.value,
            "requested_by": self.requested_by,
            "approved_by": self.approved_by,
            "notes": self.notes,
            "department": self.department,
            "is_fully_received": self.is_fully_received,
            "is_fully_invoiced": self.is_fully_invoiced,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class GoodsReceiptLine:
    """Line item on a goods receipt."""
    line_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    po_line_id: str = ""
    item_number: str = ""
    description: str = ""
    quantity_received: float = 0.0
    quantity_rejected: float = 0.0
    unit_of_measure: str = "EA"
    rejection_reason: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "line_id": self.line_id,
            "po_line_id": self.po_line_id,
            "item_number": self.item_number,
            "description": self.description,
            "quantity_received": self.quantity_received,
            "quantity_rejected": self.quantity_rejected,
            "unit_of_measure": self.unit_of_measure,
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class GoodsReceipt:
    """Goods Receipt / Receiving document."""
    gr_id: str = field(default_factory=lambda: f"GR-{uuid.uuid4().hex[:8].upper()}")
    gr_number: str = ""
    
    # Reference
    po_id: str = ""
    po_number: str = ""
    vendor_id: str = ""
    vendor_name: str = ""
    
    # Receipt details
    receipt_date: date = field(default_factory=date.today)
    received_by: str = ""
    delivery_note: str = ""
    carrier: str = ""
    
    # Lines
    line_items: List[GoodsReceiptLine] = field(default_factory=list)
    
    # Status
    status: GRStatus = GRStatus.PENDING
    
    # Metadata
    notes: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    organization_id: str = "default"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "gr_id": self.gr_id,
            "gr_number": self.gr_number,
            "po_id": self.po_id,
            "po_number": self.po_number,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "receipt_date": self.receipt_date.isoformat(),
            "received_by": self.received_by,
            "delivery_note": self.delivery_note,
            "line_items": [item.to_dict() for item in self.line_items],
            "status": self.status.value,
            "notes": self.notes,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ThreeWayMatch:
    """Result of 3-way matching."""
    match_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    
    # Documents
    invoice_id: str = ""
    po_id: str = ""
    gr_id: str = ""
    
    # Match details
    status: MatchStatus = MatchStatus.PENDING
    exceptions: List[Dict[str, Any]] = field(default_factory=list)
    
    # Amounts
    po_amount: float = 0.0
    gr_amount: float = 0.0
    invoice_amount: float = 0.0
    
    # Variances
    price_variance: float = 0.0
    quantity_variance: float = 0.0
    
    # Resolution
    override_by: Optional[str] = None
    override_reason: str = ""
    
    # Timestamps
    matched_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_id": self.match_id,
            "invoice_id": self.invoice_id,
            "po_id": self.po_id,
            "gr_id": self.gr_id,
            "status": self.status.value,
            "exceptions": self.exceptions,
            "po_amount": self.po_amount,
            "gr_amount": self.gr_amount,
            "invoice_amount": self.invoice_amount,
            "price_variance": self.price_variance,
            "quantity_variance": self.quantity_variance,
            "override_by": self.override_by,
            "override_reason": self.override_reason,
            "matched_at": self.matched_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Dataclass ↔ dict converters — the service talks dataclasses, the DB store
# speaks dicts. These funnel the conversion through one place so the rest of
# the service stays readable.
# ---------------------------------------------------------------------------


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.split("T")[0]).date()
    except Exception:
        return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _po_line_from_dict(d: Dict[str, Any]) -> POLineItem:
    return POLineItem(
        line_id=str(d.get("line_id") or uuid.uuid4().hex[:8]),
        item_number=str(d.get("item_number") or ""),
        description=str(d.get("description") or ""),
        quantity=float(d.get("quantity") or 0.0),
        unit_price=float(d.get("unit_price") or 0.0),
        unit_of_measure=str(d.get("unit_of_measure") or "EA"),
        gl_code=str(d.get("gl_code") or ""),
        cost_center=str(d.get("cost_center") or ""),
        tax_code=str(d.get("tax_code") or ""),
        quantity_received=float(d.get("quantity_received") or 0.0),
        quantity_invoiced=float(d.get("quantity_invoiced") or 0.0),
    )


def _po_from_dict(d: Optional[Dict[str, Any]]) -> Optional[PurchaseOrder]:
    if not d:
        return None
    line_dicts = d.get("line_items") or []
    lines = [_po_line_from_dict(ld) for ld in line_dicts if isinstance(ld, dict)]
    try:
        status = POStatus(str(d.get("status") or "draft"))
    except ValueError:
        status = POStatus.DRAFT
    po = PurchaseOrder(
        po_id=str(d.get("po_id") or ""),
        po_number=str(d.get("po_number") or ""),
        vendor_id=str(d.get("vendor_id") or ""),
        vendor_name=str(d.get("vendor_name") or ""),
        order_date=_parse_date(d.get("order_date")) or date.today(),
        expected_delivery=_parse_date(d.get("expected_delivery")),
        line_items=lines,
        subtotal=float(d.get("subtotal") or 0.0),
        tax_amount=float(d.get("tax_amount") or 0.0),
        total_amount=float(d.get("total_amount") or 0.0),
        currency=str(d.get("currency") or "USD"),
        status=status,
        requested_by=str(d.get("requested_by") or ""),
        approved_by=d.get("approved_by") or None,
        approved_at=_parse_datetime(d.get("approved_at")),
        notes=str(d.get("notes") or ""),
        department=str(d.get("department") or ""),
        project=str(d.get("project") or ""),
        ship_to_address=str(d.get("ship_to_address") or ""),
        created_at=_parse_datetime(d.get("created_at")) or datetime.now(timezone.utc),
        updated_at=_parse_datetime(d.get("updated_at")) or datetime.now(timezone.utc),
        organization_id=str(d.get("organization_id") or "default"),
        erp_po_id=str(d.get("erp_po_id") or ""),
    )
    return po


def _po_to_store_dict(po: PurchaseOrder) -> Dict[str, Any]:
    data = po.to_dict()
    data["organization_id"] = po.organization_id
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["erp_po_id"] = po.erp_po_id
    data["ship_to_address"] = po.ship_to_address
    data["project"] = po.project
    data["approved_at"] = po.approved_at.isoformat() if po.approved_at else None
    return data


def _gr_line_from_dict(d: Dict[str, Any]) -> GoodsReceiptLine:
    return GoodsReceiptLine(
        line_id=str(d.get("line_id") or uuid.uuid4().hex[:8]),
        po_line_id=str(d.get("po_line_id") or ""),
        item_number=str(d.get("item_number") or ""),
        description=str(d.get("description") or ""),
        quantity_received=float(d.get("quantity_received") or 0.0),
        quantity_rejected=float(d.get("quantity_rejected") or 0.0),
        unit_of_measure=str(d.get("unit_of_measure") or "EA"),
        rejection_reason=str(d.get("rejection_reason") or ""),
    )


def _gr_from_dict(d: Optional[Dict[str, Any]]) -> Optional[GoodsReceipt]:
    if not d:
        return None
    line_dicts = d.get("line_items") or []
    lines = [_gr_line_from_dict(ld) for ld in line_dicts if isinstance(ld, dict)]
    try:
        status = GRStatus(str(d.get("status") or "pending"))
    except ValueError:
        status = GRStatus.PENDING
    return GoodsReceipt(
        gr_id=str(d.get("gr_id") or ""),
        gr_number=str(d.get("gr_number") or ""),
        po_id=str(d.get("po_id") or ""),
        po_number=str(d.get("po_number") or ""),
        vendor_id=str(d.get("vendor_id") or ""),
        vendor_name=str(d.get("vendor_name") or ""),
        receipt_date=_parse_date(d.get("receipt_date")) or date.today(),
        received_by=str(d.get("received_by") or ""),
        delivery_note=str(d.get("delivery_note") or ""),
        carrier=str(d.get("carrier") or ""),
        line_items=lines,
        status=status,
        notes=str(d.get("notes") or ""),
        created_at=_parse_datetime(d.get("created_at")) or datetime.now(timezone.utc),
        organization_id=str(d.get("organization_id") or "default"),
    )


def _gr_to_store_dict(gr: GoodsReceipt) -> Dict[str, Any]:
    data = gr.to_dict()
    data["organization_id"] = gr.organization_id
    data["carrier"] = gr.carrier
    return data


def _match_from_dict(d: Optional[Dict[str, Any]]) -> Optional[ThreeWayMatch]:
    if not d:
        return None
    try:
        status = MatchStatus(str(d.get("status") or "pending"))
    except ValueError:
        status = MatchStatus.PENDING
    return ThreeWayMatch(
        match_id=str(d.get("match_id") or ""),
        invoice_id=str(d.get("invoice_id") or ""),
        po_id=str(d.get("po_id") or ""),
        gr_id=str(d.get("gr_id") or ""),
        status=status,
        exceptions=list(d.get("exceptions") or []),
        po_amount=float(d.get("po_amount") or 0.0),
        gr_amount=float(d.get("gr_amount") or 0.0),
        invoice_amount=float(d.get("invoice_amount") or 0.0),
        price_variance=float(d.get("price_variance") or 0.0),
        quantity_variance=float(d.get("quantity_variance") or 0.0),
        override_by=d.get("override_by") or None,
        override_reason=str(d.get("override_reason") or ""),
        matched_at=_parse_datetime(d.get("matched_at")) or datetime.now(timezone.utc),
    )


def _match_to_store_dict(match: ThreeWayMatch, organization_id: str) -> Dict[str, Any]:
    data = match.to_dict()
    data["organization_id"] = organization_id
    return data


class PurchaseOrderService:
    """Service for Purchase Order management and 3-way matching.

    State lives in the DB (``purchase_orders`` / ``goods_receipts`` /
    ``three_way_matches`` tables via the ``PurchaseOrderStore`` mixin).
    The service converts between the dataclass API its callers expect
    and the dict rows the store returns.

    Nothing is cached across calls — each method reads fresh state from
    the DB so multi-worker and post-deploy invariants hold. The
    singleton cache below only avoids re-constructing the service
    object itself; it holds no state.
    """

    # Default tolerances — thesis §6.6 informs these:
    PRICE_TOLERANCE_PERCENT = 2.0  # 2% price variance allowed
    QUANTITY_TOLERANCE_PERCENT = 5.0  # 5% quantity variance allowed
    AMOUNT_TOLERANCE = 10.0  # $10 absolute tolerance
    OPEN_STATUSES = (
        POStatus.APPROVED,
        POStatus.PARTIALLY_RECEIVED,
        POStatus.PARTIALLY_INVOICED,
    )

    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self._db = get_db()

    # ------------------------------------------------------------------
    # PURCHASE ORDER MANAGEMENT
    # ------------------------------------------------------------------

    def create_po(
        self,
        vendor_id: str,
        vendor_name: str,
        requested_by: str,
        line_items: List[Dict[str, Any]] = None,
        **kwargs,
    ) -> PurchaseOrder:
        """Create and persist a new purchase order."""
        po = PurchaseOrder(
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            requested_by=requested_by,
            organization_id=self.organization_id,
            **kwargs,
        )
        if not po.po_number:
            # Use the db-wide count for the suffix so numbers don't
            # collide across workers. Falls back to a short uuid when
            # the count query fails.
            try:
                existing_count = len(self._db.list_purchase_orders(self.organization_id, limit=10000))
            except Exception:
                existing_count = 0
            po.po_number = (
                f"PO-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{existing_count + 1:04d}"
            )
        if line_items:
            for item_data in line_items:
                po.add_line_item(POLineItem(**item_data))
        self._db.save_purchase_order(_po_to_store_dict(po))
        logger.info("Created PO: %s for %s", po.po_number, vendor_name)
        return po

    def approve_po(self, po_id: str, approved_by: str) -> PurchaseOrder:
        """Approve a purchase order."""
        po = self.get_po(po_id)
        if not po:
            raise ValueError(f"PO {po_id} not found")
        po.status = POStatus.APPROVED
        po.approved_by = approved_by
        po.approved_at = datetime.now(timezone.utc)
        po.updated_at = datetime.now(timezone.utc)
        self._db.save_purchase_order(_po_to_store_dict(po))
        logger.info("Approved PO: %s by %s", po.po_number, approved_by)
        return po

    def get_po(self, po_id: str) -> Optional[PurchaseOrder]:
        return _po_from_dict(self._db.get_purchase_order(po_id))

    def get_po_by_number(self, po_number: str) -> Optional[PurchaseOrder]:
        return _po_from_dict(
            self._db.get_purchase_order_by_number(self.organization_id, po_number)
        )

    def get_open_pos_for_vendor(self, vendor_id: str) -> List[PurchaseOrder]:
        """Open POs for a vendor. Matches the legacy signature (vendor_id
        as input); the underlying store matches by vendor_name which is
        typically what's populated. Callers already pass vendor_name in
        practice — see execution_engine and invoice_validation."""
        rows = self._db.list_purchase_orders_for_vendor(
            self.organization_id,
            vendor_id,
            open_only=True,
        )
        return [_po_from_dict(r) for r in rows if r]

    def search_pos(
        self,
        vendor_name: str = "",
        status: POStatus = None,
        from_date: date = None,
        to_date: date = None,
    ) -> List[PurchaseOrder]:
        """Filter POs by vendor substring / status / date range."""
        raw = self._db.list_purchase_orders(self.organization_id, limit=1000)
        pos = [_po_from_dict(r) for r in raw if r]
        if vendor_name:
            vendor_lower = vendor_name.lower()
            pos = [po for po in pos if po and vendor_lower in po.vendor_name.lower()]
        if status:
            pos = [po for po in pos if po and po.status == status]
        if from_date:
            pos = [po for po in pos if po and po.order_date and po.order_date >= from_date]
        if to_date:
            pos = [po for po in pos if po and po.order_date and po.order_date <= to_date]
        return [po for po in pos if po is not None]

    # ------------------------------------------------------------------
    # GOODS RECEIPT MANAGEMENT
    # ------------------------------------------------------------------

    def create_goods_receipt(
        self,
        po_id: str,
        received_by: str,
        line_items: List[Dict[str, Any]],
        **kwargs,
    ) -> GoodsReceipt:
        """Create a goods receipt against a PO and advance PO state."""
        po = self.get_po(po_id)
        if not po:
            raise ValueError(f"PO {po_id} not found")
        gr = GoodsReceipt(
            po_id=po_id,
            po_number=po.po_number,
            vendor_id=po.vendor_id,
            vendor_name=po.vendor_name,
            received_by=received_by,
            organization_id=self.organization_id,
            **kwargs,
        )
        try:
            existing_grs = len(self._db.list_goods_receipts_for_po(po_id))
        except Exception:
            existing_grs = 0
        gr.gr_number = (
            f"GR-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{existing_grs + 1:04d}"
        )
        for item_data in line_items:
            gr_line = GoodsReceiptLine(**item_data)
            gr.line_items.append(gr_line)
            if gr_line.po_line_id:
                for po_line in po.line_items:
                    if po_line.line_id == gr_line.po_line_id:
                        po_line.quantity_received += gr_line.quantity_received
                        break
        po.status = POStatus.FULLY_RECEIVED if po.is_fully_received else POStatus.PARTIALLY_RECEIVED
        gr.status = GRStatus.RECEIVED
        self._db.save_goods_receipt(_gr_to_store_dict(gr))
        self._db.save_purchase_order(_po_to_store_dict(po))
        logger.info("Created GR: %s for PO %s", gr.gr_number, po.po_number)
        return gr

    def get_goods_receipts_for_po(self, po_id: str) -> List[GoodsReceipt]:
        rows = self._db.list_goods_receipts_for_po(po_id)
        return [_gr_from_dict(r) for r in rows if r]

    # ------------------------------------------------------------------
    # 3-WAY MATCHING
    # ------------------------------------------------------------------

    def match_invoice_to_po(
        self,
        invoice_id: str,
        invoice_amount: float,
        invoice_vendor: str,
        invoice_po_number: str = "",
        invoice_lines: List[Dict[str, Any]] = None,
        invoice_currency: str = "",
    ) -> ThreeWayMatch:
        """Perform 3-way matching: PO + Goods Receipt + Invoice.

        ``invoice_currency`` is used solely to refuse the match when
        the invoice currency disagrees with the PO currency. Without
        the check, a EUR 1,000 invoice and a USD 1,000 PO would compare
        as variance=0 and silently MATCH, even though they're wildly
        different amounts in real money. Optional + default empty so
        existing callers stay backward-compatible; when missing, the
        currency guard is skipped (same behaviour as before).
        """
        match = ThreeWayMatch(
            invoice_id=invoice_id,
            invoice_amount=invoice_amount,
        )

        # Step 1: locate the PO. Direct hit by number first; fall back
        # to fuzzy vendor/amount match only when the invoice didn't
        # carry an explicit PO number.
        po: Optional[PurchaseOrder] = None
        if invoice_po_number:
            po = self.get_po_by_number(invoice_po_number)
        if not po:
            po = self._find_po_by_vendor_amount(invoice_vendor, invoice_amount)

        if not po:
            match.status = MatchStatus.EXCEPTION
            match.exceptions.append({
                "type": MatchExceptionType.NO_PO.value,
                "message": f"No matching PO found for vendor {invoice_vendor}",
                "severity": "high",
            })
            self._db.save_three_way_match(_match_to_store_dict(match, self.organization_id))
            return match

        match.po_id = po.po_id
        match.po_amount = po.total_amount

        # Step 1a: currency guard — refuse the match if the invoice and
        # PO disagree on currency. Without this, a EUR 1,000 invoice
        # against a USD 1,000 PO compares as variance=0 and falls
        # straight through to MATCHED, which is the wrong call by an
        # order of magnitude on real money. We don't try to FX-convert
        # here — that's a product decision that needs an explicit
        # exchange-rate source. We just refuse and surface the
        # mismatch.
        invoice_ccy = (invoice_currency or "").strip().upper()
        po_ccy = (str(po.currency or "")).strip().upper()
        if invoice_ccy and po_ccy and invoice_ccy != po_ccy:
            match.exceptions.append({
                "type": MatchExceptionType.PRICE_MISMATCH.value,
                "message": (
                    f"Currency mismatch: invoice in {invoice_ccy}, "
                    f"PO {po.po_number} in {po_ccy}"
                ),
                "severity": "high",
                "invoice_currency": invoice_ccy,
                "po_currency": po_ccy,
            })
            match.status = MatchStatus.EXCEPTION
            self._db.save_three_way_match(_match_to_store_dict(match, self.organization_id))
            return match

        # Step 2: most-recent GR for this PO.
        goods_receipts = self.get_goods_receipts_for_po(po.po_id)
        if not goods_receipts:
            match.exceptions.append({
                "type": MatchExceptionType.NO_GR.value,
                "message": f"No goods receipt found for PO {po.po_number}",
                "severity": "medium",
            })
        else:
            gr = max(goods_receipts, key=lambda g: g.created_at)
            match.gr_id = gr.gr_id
            # Sum GR line amounts but track which lines couldn't be
            # priced against the PO. A missing PO line for a GR row
            # used to silently contribute 0 to the GR total, which
            # made the match look closer to the invoice than it
            # actually was — concretely, a deleted PO line could let
            # an inflated invoice slip through MATCHED instead of
            # EXCEPTION. Now we flag it explicitly so a human can
            # decide whether the GR data is wrong, the PO was edited,
            # or the invoice is over.
            gr_total = 0.0
            orphan_lines = []
            for gr_line in gr.line_items:
                price, found = self._po_line_price_with_status(po, gr_line.po_line_id)
                gr_total += gr_line.quantity_received * price
                if not found:
                    orphan_lines.append(gr_line.po_line_id)
            match.gr_amount = gr_total
            if orphan_lines:
                match.exceptions.append({
                    "type": MatchExceptionType.NO_GR.value,
                    "message": (
                        f"GR {gr.gr_id} references {len(orphan_lines)} PO "
                        f"line(s) that no longer exist on PO {po.po_number}; "
                        f"GR amount may be understated"
                    ),
                    "severity": "high",
                    "orphan_po_line_ids": orphan_lines,
                })

        # Step 3: price variance (tolerance pct AND absolute floor).
        match.price_variance = invoice_amount - po.total_amount
        price_variance_pct = (
            abs(match.price_variance) / po.total_amount * 100
            if po.total_amount > 0
            else 0
        )
        if (
            price_variance_pct > self.PRICE_TOLERANCE_PERCENT
            and abs(match.price_variance) > self.AMOUNT_TOLERANCE
        ):
            match.exceptions.append({
                "type": MatchExceptionType.PRICE_MISMATCH.value,
                "message": (
                    f"Invoice amount {po.currency} {invoice_amount:,.2f} "
                    f"differs from PO {po.currency} {po.total_amount:,.2f} by "
                    f"{price_variance_pct:.1f}%"
                ),
                "severity": "medium",
                "variance": match.price_variance,
                "variance_pct": price_variance_pct,
            })

        # Step 4: per-line quantity check.
        if invoice_lines:
            for inv_line in invoice_lines:
                po_line = self._find_matching_po_line(po, inv_line)
                if po_line:
                    qty_diff = inv_line.get("quantity", 0) - po_line.quantity
                    if qty_diff > 0:
                        match.quantity_variance += qty_diff
                        if po_line.quantity > 0 and (
                            qty_diff / po_line.quantity * 100
                            > self.QUANTITY_TOLERANCE_PERCENT
                        ):
                            match.exceptions.append({
                                "type": MatchExceptionType.OVER_INVOICE.value,
                                "message": f"Invoice quantity exceeds PO for {po_line.description}",
                                "severity": "low",
                                "item": po_line.item_number,
                                "po_qty": po_line.quantity,
                                "invoice_qty": inv_line.get("quantity", 0),
                            })

        # Step 5: status.
        if not match.exceptions:
            match.status = MatchStatus.MATCHED
        elif all(e.get("severity") == "low" for e in match.exceptions):
            match.status = MatchStatus.PARTIAL_MATCH
        else:
            match.status = MatchStatus.EXCEPTION

        if match.status in (MatchStatus.MATCHED, MatchStatus.PARTIAL_MATCH):
            self._update_po_invoiced(po, invoice_lines or [])

        self._db.save_three_way_match(_match_to_store_dict(match, self.organization_id))
        logger.info("3-way match result for invoice %s: %s", invoice_id, match.status.value)
        return match

    def _find_po_by_vendor_amount(
        self,
        vendor_name: str,
        amount: float,
    ) -> Optional[PurchaseOrder]:
        """Find an open PO by vendor name + amount within tolerance."""
        candidates = self._db.list_purchase_orders_for_vendor(
            self.organization_id,
            vendor_name,
            open_only=True,
            limit=25,
        )
        for row in candidates:
            po = _po_from_dict(row)
            if not po:
                continue
            if abs(po.total_amount - amount) <= self.AMOUNT_TOLERANCE:
                return po
            if po.total_amount > 0:
                variance_pct = abs(po.total_amount - amount) / po.total_amount * 100
                if variance_pct <= self.PRICE_TOLERANCE_PERCENT:
                    return po
        return None

    def _get_po_line_price(
        self, po: Optional[PurchaseOrder], line_id: str
    ) -> float:
        """Return the unit price for a PO line, or 0.0 if not found.

        WARNING: unmatched line_id returns 0.0 silently — this means
        any GR line that references a po_line_id that doesn't exist
        on the PO (data drift, deleted PO line, ID mismatch on
        upstream import) contributes 0 to the GR amount. Callers that
        sum GR amounts should verify the line was actually found
        (use ``_po_line_price_with_status`` below) so a missing line
        doesn't silently make the GR look smaller than it is.
        """
        if not po:
            return 0.0
        for line in po.line_items:
            if line.line_id == line_id:
                return line.unit_price
        return 0.0

    def _po_line_price_with_status(
        self, po: Optional[PurchaseOrder], line_id: str
    ) -> tuple:
        """Like ``_get_po_line_price`` but also returns whether the
        line was actually found on the PO. Returns ``(price, found)``
        — callers that compute aggregates can flag a data-integrity
        issue when ``found`` is False."""
        if not po:
            return 0.0, False
        for line in po.line_items:
            if line.line_id == line_id:
                return line.unit_price, True
        return 0.0, False

    def _find_matching_po_line(
        self,
        po: PurchaseOrder,
        invoice_line: Dict[str, Any],
    ) -> Optional[POLineItem]:
        """Match invoice line to PO line: item number → substring → LLM."""
        item_number = str(invoice_line.get("item_number") or "")
        description = str(invoice_line.get("description") or "").lower()
        for po_line in po.line_items:
            if item_number and po_line.item_number == item_number:
                return po_line
            if description and description in po_line.description.lower():
                return po_line
        if description and po.line_items:
            return self._ai_match_po_line(invoice_line, po.line_items)
        return None

    def _ai_match_po_line(
        self,
        invoice_line: Dict[str, Any],
        po_lines: List[POLineItem],
    ) -> Optional[POLineItem]:
        """Use Claude to semantically match when deterministic fails."""
        try:
            import os
            import json as _json
            api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                return None
            inv_desc = invoice_line.get("description", "")
            inv_qty = invoice_line.get("quantity", 1)
            inv_amount = invoice_line.get("amount", 0)
            po_lines_text = "\n".join(
                f"  Line {i}: {pl.description} (qty: {pl.quantity}, unit price: {pl.unit_price})"
                for i, pl in enumerate(po_lines)
            )
            prompt = (
                "Match this invoice line item to the correct PO line.\n\n"
                f"INVOICE LINE:\n  Description: {inv_desc}\n  Quantity: {inv_qty}\n  Amount: {inv_amount}\n\n"
                f"PO LINES:\n{po_lines_text}\n\n"
                "Descriptions may use different words for the same item. "
                "Return JSON: "
                "{\"match_index\": <0-based index or null>, \"confidence\": 0.0-1.0, \"reasoning\": \"one sentence\"}\n"
                "Return ONLY valid JSON."
            )
            from clearledgr.core.llm_gateway import get_llm_gateway, LLMAction
            gateway = get_llm_gateway()
            llm_resp = gateway.call_sync(
                LLMAction.PO_LINE_MATCH,
                messages=[{"role": "user", "content": prompt}],
            )
            text = str(llm_resp.content) if llm_resp and llm_resp.content else ""
            result = _json.loads(text) if text else {}
            idx = result.get("match_index")
            conf = float(result.get("confidence") or 0)
            if idx is not None and 0 <= int(idx) < len(po_lines) and conf >= 0.6:
                return po_lines[int(idx)]
        except Exception as exc:
            logger.debug("AI PO line matching failed: %s", exc)
        return None

    def _update_po_invoiced(
        self,
        po: PurchaseOrder,
        invoice_lines: List[Dict[str, Any]],
    ) -> None:
        """Advance PO invoiced quantities + persist the status change."""
        if not invoice_lines:
            for line in po.line_items:
                line.quantity_invoiced = line.quantity
        else:
            for inv_line in invoice_lines:
                po_line = self._find_matching_po_line(po, inv_line)
                if po_line:
                    po_line.quantity_invoiced += float(inv_line.get("quantity") or 0)
        po.status = (
            POStatus.FULLY_INVOICED
            if po.is_fully_invoiced
            else POStatus.PARTIALLY_INVOICED
        )
        po.updated_at = datetime.now(timezone.utc)
        self._db.save_purchase_order(_po_to_store_dict(po))

    # ------------------------------------------------------------------
    # 2-WAY MATCHING (Invoice + GR, no PO)
    # ------------------------------------------------------------------

    def match_invoice_to_gr(
        self,
        invoice_id: str,
        invoice_amount: float,
        invoice_vendor: str,
        invoice_lines: List[Dict[str, Any]] = None,
    ) -> ThreeWayMatch:
        """2-way match: GR + Invoice (when no PO is available)."""
        match = ThreeWayMatch(
            invoice_id=invoice_id,
            invoice_amount=invoice_amount,
        )

        # Pull GRs for the vendor by walking the vendor's POs. GR rows
        # don't carry a vendor-name index right now, so this round-trip
        # through the vendor's POs is the supported access pattern.
        candidate_grs: List[GoodsReceipt] = []
        po_rows = self._db.list_purchase_orders_for_vendor(
            self.organization_id,
            invoice_vendor,
            open_only=False,
            limit=25,
        )
        for po_row in po_rows:
            po_id = str((po_row or {}).get("po_id") or "")
            if not po_id:
                continue
            for gr_row in self._db.list_goods_receipts_for_po(po_id):
                gr = _gr_from_dict(gr_row)
                if gr and gr.status in (GRStatus.RECEIVED, GRStatus.PARTIAL):
                    candidate_grs.append(gr)

        if not candidate_grs:
            match.status = MatchStatus.EXCEPTION
            match.exceptions.append({
                "type": MatchExceptionType.NO_GR.value,
                "message": f"No goods receipt found for vendor {invoice_vendor}",
                "severity": "high",
            })
            self._db.save_three_way_match(_match_to_store_dict(match, self.organization_id))
            return match

        best_gr: Optional[GoodsReceipt] = None
        best_diff = float("inf")
        for gr in candidate_grs:
            po = self.get_po(gr.po_id) if gr.po_id else None
            gr_total = sum(
                line.quantity_received * self._get_po_line_price(po, line.po_line_id)
                for line in gr.line_items
            )
            diff = abs(gr_total - invoice_amount)
            if diff < best_diff:
                best_diff = diff
                best_gr = gr
                match.gr_amount = gr_total

        if best_gr is None:
            match.status = MatchStatus.EXCEPTION
            match.exceptions.append({
                "type": MatchExceptionType.NO_GR.value,
                "message": "Could not determine matching goods receipt",
                "severity": "high",
            })
            self._db.save_three_way_match(_match_to_store_dict(match, self.organization_id))
            return match

        match.gr_id = best_gr.gr_id
        match.exceptions.append({
            "type": MatchExceptionType.NO_PO.value,
            "message": "Two-way match only (no PO available)",
            "severity": "low",
        })
        if match.gr_amount > 0:
            match.price_variance = invoice_amount - match.gr_amount
            variance_pct = abs(match.price_variance) / match.gr_amount * 100
            if (
                variance_pct > self.PRICE_TOLERANCE_PERCENT
                and abs(match.price_variance) > self.AMOUNT_TOLERANCE
            ):
                match.exceptions.append({
                    "type": MatchExceptionType.PRICE_MISMATCH.value,
                    "message": (
                        f"Invoice {invoice_amount:,.2f} differs from GR total "
                        f"{match.gr_amount:,.2f} by {variance_pct:.1f}%"
                    ),
                    "severity": "medium",
                    "variance": match.price_variance,
                    "variance_pct": variance_pct,
                })

        high_severity = [e for e in match.exceptions if e.get("severity") in ("high", "medium")]
        match.status = MatchStatus.EXCEPTION if high_severity else MatchStatus.PARTIAL_MATCH
        self._db.save_three_way_match(_match_to_store_dict(match, self.organization_id))
        logger.info("2-way match result for invoice %s: %s", invoice_id, match.status.value)
        return match

    def override_match_exception(
        self,
        match_id: str,
        override_by: str,
        reason: str,
    ) -> ThreeWayMatch:
        """Management override — clears exceptions and writes audit event."""
        match = self.get_match(match_id)
        if not match:
            raise ValueError(f"Match {match_id} not found")
        prev_status = match.status.value
        match.status = MatchStatus.OVERRIDE
        match.override_by = override_by
        match.override_reason = reason
        self._db.save_three_way_match(_match_to_store_dict(match, self.organization_id))
        try:
            self._db.append_ap_audit_event({
                "ap_item_id": match.invoice_id,
                "event_type": "po_match_override",
                "from_state": prev_status,
                "to_state": "override",
                "actor_type": "user",
                "actor_id": override_by,
                "metadata": {
                    "match_id": match_id,
                    "override_reason": reason,
                    "exceptions": match.exceptions,
                    "price_variance": match.price_variance,
                    "quantity_variance": match.quantity_variance,
                },
                "organization_id": self.organization_id,
                "source": "purchase_orders",
            })
        except Exception as exc:
            logger.warning("Failed to write PO override audit event: %s", exc)
        logger.info("Match %s overridden by %s: %s", match_id, override_by, reason)
        return match

    def get_match(self, match_id: str) -> Optional[ThreeWayMatch]:
        return _match_from_dict(self._db.get_three_way_match(match_id))

    def get_match_exceptions(self) -> List[ThreeWayMatch]:
        rows = self._db.list_three_way_matches(
            self.organization_id, status=MatchStatus.EXCEPTION.value, limit=500
        )
        return [_match_from_dict(r) for r in rows if r]

    # ------------------------------------------------------------------
    # STATISTICS
    # ------------------------------------------------------------------

    def get_summary(self) -> Dict[str, Any]:
        pos_rows = self._db.list_purchase_orders(self.organization_id, limit=5000)
        pos = [_po_from_dict(r) for r in pos_rows if r]
        match_rows = self._db.list_three_way_matches(self.organization_id, limit=5000)
        matches = [_match_from_dict(r) for r in match_rows if r]
        return {
            "total_pos": len(pos),
            "po_by_status": {
                status.value: len([p for p in pos if p and p.status == status])
                for status in POStatus
            },
            "total_po_value": sum(p.total_amount for p in pos if p),
            "open_po_value": sum(
                p.total_amount for p in pos
                if p and p.status in (POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED)
            ),
            "total_matches": len(matches),
            "match_by_status": {
                status.value: len([m for m in matches if m and m.status == status])
                for status in MatchStatus
            },
            "pending_exceptions": len([m for m in matches if m and m.status == MatchStatus.EXCEPTION]),
        }


# ---------------------------------------------------------------------------
# Singleton — holds only the service object, no business state (the DB is
# the source of truth). Safe for multi-worker deployments.
# ---------------------------------------------------------------------------
_instances: Dict[str, PurchaseOrderService] = {}


def get_purchase_order_service(organization_id: str = "default") -> PurchaseOrderService:
    """Get or create PO service for organization."""
    if organization_id not in _instances:
        _instances[organization_id] = PurchaseOrderService(organization_id)
    return _instances[organization_id]
