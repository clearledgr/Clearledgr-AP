"""
Purchase Order Management Service

Complete PO lifecycle:
- PO Creation and management
- PO-to-Invoice matching
- 3-Way matching (PO + Goods Receipt + Invoice)
- Match exceptions and tolerances
"""

import logging
from datetime import datetime, date
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


class PurchaseOrderService:
    """
    Service for Purchase Order management and 3-way matching.
    """
    
    # Default tolerances
    PRICE_TOLERANCE_PERCENT = 2.0  # 2% price variance allowed
    QUANTITY_TOLERANCE_PERCENT = 5.0  # 5% quantity variance allowed
    AMOUNT_TOLERANCE = 10.0  # $10 absolute tolerance
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self._purchase_orders: Dict[str, PurchaseOrder] = {}
        self._goods_receipts: Dict[str, GoodsReceipt] = {}
        self._matches: Dict[str, ThreeWayMatch] = {}
        
        # Index for quick lookup
        self._po_by_number: Dict[str, str] = {}  # po_number -> po_id
        self._po_by_vendor: Dict[str, List[str]] = {}  # vendor_id -> [po_ids]
    
    # =========================================================================
    # PURCHASE ORDER MANAGEMENT
    # =========================================================================
    
    def create_po(
        self,
        vendor_id: str,
        vendor_name: str,
        requested_by: str,
        line_items: List[Dict[str, Any]] = None,
        **kwargs
    ) -> PurchaseOrder:
        """Create a new purchase order."""
        po = PurchaseOrder(
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            requested_by=requested_by,
            organization_id=self.organization_id,
            **kwargs
        )
        
        # Generate PO number if not provided
        if not po.po_number:
            po.po_number = f"PO-{datetime.now().strftime('%Y%m%d')}-{len(self._purchase_orders) + 1:04d}"
        
        # Add line items
        if line_items:
            for item_data in line_items:
                item = POLineItem(**item_data)
                po.add_line_item(item)
        
        self._purchase_orders[po.po_id] = po
        self._po_by_number[po.po_number] = po.po_id
        
        # Index by vendor
        if vendor_id not in self._po_by_vendor:
            self._po_by_vendor[vendor_id] = []
        self._po_by_vendor[vendor_id].append(po.po_id)
        
        logger.info(f"Created PO: {po.po_number} for {vendor_name}")
        return po
    
    def approve_po(self, po_id: str, approved_by: str) -> PurchaseOrder:
        """Approve a purchase order."""
        po = self._purchase_orders.get(po_id)
        if not po:
            raise ValueError(f"PO {po_id} not found")
        
        if po.status != POStatus.PENDING_APPROVAL:
            po.status = POStatus.PENDING_APPROVAL
        
        po.status = POStatus.APPROVED
        po.approved_by = approved_by
        po.approved_at = datetime.now()
        po.updated_at = datetime.now()
        
        logger.info(f"Approved PO: {po.po_number} by {approved_by}")
        return po
    
    def get_po(self, po_id: str) -> Optional[PurchaseOrder]:
        """Get a purchase order by ID."""
        return self._purchase_orders.get(po_id)
    
    def get_po_by_number(self, po_number: str) -> Optional[PurchaseOrder]:
        """Get a purchase order by PO number."""
        po_id = self._po_by_number.get(po_number)
        if po_id:
            return self._purchase_orders.get(po_id)
        return None
    
    def get_open_pos_for_vendor(self, vendor_id: str) -> List[PurchaseOrder]:
        """Get open POs for a vendor."""
        po_ids = self._po_by_vendor.get(vendor_id, [])
        return [
            self._purchase_orders[po_id] 
            for po_id in po_ids 
            if self._purchase_orders[po_id].status in [
                POStatus.APPROVED, 
                POStatus.PARTIALLY_RECEIVED,
                POStatus.PARTIALLY_INVOICED
            ]
        ]
    
    def search_pos(
        self,
        vendor_name: str = "",
        status: POStatus = None,
        from_date: date = None,
        to_date: date = None,
    ) -> List[PurchaseOrder]:
        """Search purchase orders."""
        results = list(self._purchase_orders.values())
        
        if vendor_name:
            vendor_lower = vendor_name.lower()
            results = [po for po in results if vendor_lower in po.vendor_name.lower()]
        
        if status:
            results = [po for po in results if po.status == status]
        
        if from_date:
            results = [po for po in results if po.order_date >= from_date]
        
        if to_date:
            results = [po for po in results if po.order_date <= to_date]
        
        return results
    
    # =========================================================================
    # GOODS RECEIPT MANAGEMENT
    # =========================================================================
    
    def create_goods_receipt(
        self,
        po_id: str,
        received_by: str,
        line_items: List[Dict[str, Any]],
        **kwargs
    ) -> GoodsReceipt:
        """Create a goods receipt against a PO."""
        po = self._purchase_orders.get(po_id)
        if not po:
            raise ValueError(f"PO {po_id} not found")
        
        gr = GoodsReceipt(
            po_id=po_id,
            po_number=po.po_number,
            vendor_id=po.vendor_id,
            vendor_name=po.vendor_name,
            received_by=received_by,
            organization_id=self.organization_id,
            **kwargs
        )
        
        # Generate GR number
        gr.gr_number = f"GR-{datetime.now().strftime('%Y%m%d')}-{len(self._goods_receipts) + 1:04d}"
        
        # Process line items and update PO
        for item_data in line_items:
            gr_line = GoodsReceiptLine(**item_data)
            gr.line_items.append(gr_line)
            
            # Update PO line received quantity
            if gr_line.po_line_id:
                for po_line in po.line_items:
                    if po_line.line_id == gr_line.po_line_id:
                        po_line.quantity_received += gr_line.quantity_received
                        break
        
        # Update PO status
        if po.is_fully_received:
            po.status = POStatus.FULLY_RECEIVED
        else:
            po.status = POStatus.PARTIALLY_RECEIVED
        
        gr.status = GRStatus.RECEIVED
        self._goods_receipts[gr.gr_id] = gr
        
        logger.info(f"Created GR: {gr.gr_number} for PO {po.po_number}")
        return gr
    
    def get_goods_receipts_for_po(self, po_id: str) -> List[GoodsReceipt]:
        """Get all goods receipts for a PO."""
        return [gr for gr in self._goods_receipts.values() if gr.po_id == po_id]
    
    # =========================================================================
    # 3-WAY MATCHING
    # =========================================================================
    
    def match_invoice_to_po(
        self,
        invoice_id: str,
        invoice_amount: float,
        invoice_vendor: str,
        invoice_po_number: str = "",
        invoice_lines: List[Dict[str, Any]] = None,
    ) -> ThreeWayMatch:
        """
        Perform 3-way matching: PO + Goods Receipt + Invoice.
        """
        match = ThreeWayMatch(
            invoice_id=invoice_id,
            invoice_amount=invoice_amount,
        )
        
        # Step 1: Find matching PO
        po = None
        if invoice_po_number:
            po = self.get_po_by_number(invoice_po_number)
        
        if not po:
            # Try to find PO by vendor + amount
            po = self._find_po_by_vendor_amount(invoice_vendor, invoice_amount)
        
        if not po:
            match.status = MatchStatus.EXCEPTION
            match.exceptions.append({
                "type": MatchExceptionType.NO_PO.value,
                "message": f"No matching PO found for vendor {invoice_vendor}",
                "severity": "high",
            })
            self._matches[match.match_id] = match
            return match
        
        match.po_id = po.po_id
        match.po_amount = po.total_amount
        
        # Step 2: Find matching Goods Receipt
        goods_receipts = self.get_goods_receipts_for_po(po.po_id)
        if not goods_receipts:
            match.status = MatchStatus.EXCEPTION
            match.exceptions.append({
                "type": MatchExceptionType.NO_GR.value,
                "message": f"No goods receipt found for PO {po.po_number}",
                "severity": "medium",
            })
            # Continue with 2-way match
        else:
            # Use most recent GR
            gr = max(goods_receipts, key=lambda g: g.created_at)
            match.gr_id = gr.gr_id
            match.gr_amount = sum(
                line.quantity_received * self._get_po_line_price(po, line.po_line_id)
                for line in gr.line_items
            )
        
        # Step 3: Check price variance
        if po:
            match.price_variance = invoice_amount - po.total_amount
            price_variance_pct = abs(match.price_variance) / po.total_amount * 100 if po.total_amount > 0 else 0
            
            if price_variance_pct > self.PRICE_TOLERANCE_PERCENT and abs(match.price_variance) > self.AMOUNT_TOLERANCE:
                match.exceptions.append({
                    "type": MatchExceptionType.PRICE_MISMATCH.value,
                    "message": f"Invoice amount ${invoice_amount:.2f} differs from PO ${po.total_amount:.2f} by {price_variance_pct:.1f}%",
                    "severity": "medium",
                    "variance": match.price_variance,
                    "variance_pct": price_variance_pct,
                })
        
        # Step 4: Check quantity variance (if line items provided)
        if invoice_lines and po:
            for inv_line in invoice_lines:
                po_line = self._find_matching_po_line(po, inv_line)
                if po_line:
                    qty_diff = inv_line.get("quantity", 0) - po_line.quantity
                    if qty_diff > 0:
                        match.quantity_variance += qty_diff
                        if qty_diff / po_line.quantity * 100 > self.QUANTITY_TOLERANCE_PERCENT:
                            match.exceptions.append({
                                "type": MatchExceptionType.OVER_INVOICE.value,
                                "message": f"Invoice quantity exceeds PO for {po_line.description}",
                                "severity": "low",
                                "item": po_line.item_number,
                                "po_qty": po_line.quantity,
                                "invoice_qty": inv_line.get("quantity", 0),
                            })
        
        # Step 5: Determine final status
        if not match.exceptions:
            match.status = MatchStatus.MATCHED
        elif all(e.get("severity") == "low" for e in match.exceptions):
            match.status = MatchStatus.PARTIAL_MATCH
        else:
            match.status = MatchStatus.EXCEPTION
        
        # Update PO invoiced quantities
        if match.status in [MatchStatus.MATCHED, MatchStatus.PARTIAL_MATCH]:
            self._update_po_invoiced(po, invoice_lines or [])
        
        self._matches[match.match_id] = match
        logger.info(f"3-way match result for invoice {invoice_id}: {match.status.value}")
        
        return match
    
    def _find_po_by_vendor_amount(
        self,
        vendor_name: str,
        amount: float,
    ) -> Optional[PurchaseOrder]:
        """Find a PO by vendor name and approximate amount."""
        vendor_lower = vendor_name.lower()
        
        for po in self._purchase_orders.values():
            if po.status not in [POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED, POStatus.PARTIALLY_INVOICED]:
                continue
            
            if vendor_lower in po.vendor_name.lower():
                # Check amount within tolerance
                if abs(po.total_amount - amount) <= self.AMOUNT_TOLERANCE:
                    return po
                if po.total_amount > 0:
                    variance_pct = abs(po.total_amount - amount) / po.total_amount * 100
                    if variance_pct <= self.PRICE_TOLERANCE_PERCENT:
                        return po
        
        return None
    
    def _get_po_line_price(self, po: PurchaseOrder, line_id: str) -> float:
        """Get unit price for a PO line."""
        for line in po.line_items:
            if line.line_id == line_id:
                return line.unit_price
        return 0.0
    
    def _find_matching_po_line(
        self,
        po: PurchaseOrder,
        invoice_line: Dict[str, Any],
    ) -> Optional[POLineItem]:
        """Find matching PO line for an invoice line."""
        item_number = invoice_line.get("item_number", "")
        description = invoice_line.get("description", "").lower()
        
        for po_line in po.line_items:
            if item_number and po_line.item_number == item_number:
                return po_line
            if description and description in po_line.description.lower():
                return po_line
        
        return None
    
    def _update_po_invoiced(
        self,
        po: PurchaseOrder,
        invoice_lines: List[Dict[str, Any]],
    ):
        """Update PO with invoiced quantities."""
        if not invoice_lines:
            # Mark entire PO as invoiced
            for line in po.line_items:
                line.quantity_invoiced = line.quantity
        else:
            for inv_line in invoice_lines:
                po_line = self._find_matching_po_line(po, inv_line)
                if po_line:
                    po_line.quantity_invoiced += inv_line.get("quantity", 0)
        
        if po.is_fully_invoiced:
            po.status = POStatus.FULLY_INVOICED
        else:
            po.status = POStatus.PARTIALLY_INVOICED
    
    def override_match_exception(
        self,
        match_id: str,
        override_by: str,
        reason: str,
    ) -> ThreeWayMatch:
        """Override match exceptions (management approval)."""
        match = self._matches.get(match_id)
        if not match:
            raise ValueError(f"Match {match_id} not found")
        
        match.status = MatchStatus.OVERRIDE
        match.override_by = override_by
        match.override_reason = reason
        
        logger.info(f"Match {match_id} overridden by {override_by}: {reason}")
        return match
    
    def get_match(self, match_id: str) -> Optional[ThreeWayMatch]:
        """Get a match result."""
        return self._matches.get(match_id)
    
    def get_match_exceptions(self) -> List[ThreeWayMatch]:
        """Get all matches with exceptions."""
        return [m for m in self._matches.values() if m.status == MatchStatus.EXCEPTION]
    
    # =========================================================================
    # STATISTICS
    # =========================================================================
    
    def get_summary(self) -> Dict[str, Any]:
        """Get PO/matching summary."""
        pos = list(self._purchase_orders.values())
        matches = list(self._matches.values())
        
        return {
            "total_pos": len(pos),
            "po_by_status": {
                status.value: len([p for p in pos if p.status == status])
                for status in POStatus
            },
            "total_po_value": sum(p.total_amount for p in pos),
            "open_po_value": sum(
                p.total_amount for p in pos 
                if p.status in [POStatus.APPROVED, POStatus.PARTIALLY_RECEIVED]
            ),
            "total_goods_receipts": len(self._goods_receipts),
            "total_matches": len(matches),
            "match_by_status": {
                status.value: len([m for m in matches if m.status == status])
                for status in MatchStatus
            },
            "pending_exceptions": len(self.get_match_exceptions()),
        }


# Singleton instance cache
_instances: Dict[str, PurchaseOrderService] = {}


def get_purchase_order_service(organization_id: str = "default") -> PurchaseOrderService:
    """Get or create PO service for organization."""
    if organization_id not in _instances:
        _instances[organization_id] = PurchaseOrderService(organization_id)
    return _instances[organization_id]
