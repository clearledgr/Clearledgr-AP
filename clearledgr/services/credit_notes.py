"""
Credit Notes & Debit Memos Service

Handles vendor credits and adjustments:
- Credit note detection and processing
- Debit memo creation
- Credit application to invoices
- Credit balance tracking
"""

import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
import uuid
import re

logger = logging.getLogger(__name__)


class CreditType(Enum):
    """Type of credit document."""
    CREDIT_NOTE = "credit_note"      # Vendor-issued credit
    DEBIT_MEMO = "debit_memo"        # Buyer-issued debit
    PRICE_ADJUSTMENT = "price_adj"   # Price correction
    RETURN_CREDIT = "return"         # Return for credit
    REBATE = "rebate"                # Volume rebate
    ALLOWANCE = "allowance"          # Trade allowance
    WRITE_OFF = "write_off"          # Write-off


class CreditStatus(Enum):
    """Status of credit document."""
    PENDING = "pending"
    VERIFIED = "verified"
    APPLIED = "applied"
    PARTIALLY_APPLIED = "partially_applied"
    DISPUTED = "disputed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class CreditLineItem:
    """Line item on a credit note."""
    line_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    description: str = ""
    quantity: float = 0.0
    unit_price: float = 0.0
    amount: float = 0.0
    original_invoice_line: str = ""
    gl_code: str = ""
    reason_code: str = ""
    
    def __post_init__(self):
        if self.amount == 0 and self.quantity and self.unit_price:
            self.amount = round(self.quantity * self.unit_price, 2)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "line_id": self.line_id,
            "description": self.description,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "amount": self.amount,
            "gl_code": self.gl_code,
            "reason_code": self.reason_code,
        }


@dataclass
class CreditApplication:
    """Record of credit applied to an invoice."""
    application_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    credit_id: str = ""
    invoice_id: str = ""
    amount_applied: float = 0.0
    applied_at: datetime = field(default_factory=datetime.now)
    applied_by: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "application_id": self.application_id,
            "credit_id": self.credit_id,
            "invoice_id": self.invoice_id,
            "amount_applied": self.amount_applied,
            "applied_at": self.applied_at.isoformat(),
            "applied_by": self.applied_by,
        }


@dataclass
class CreditNote:
    """Credit Note or Debit Memo."""
    credit_id: str = field(default_factory=lambda: f"CR-{uuid.uuid4().hex[:8].upper()}")
    credit_number: str = ""
    credit_type: CreditType = CreditType.CREDIT_NOTE
    
    # Vendor
    vendor_id: str = ""
    vendor_name: str = ""
    
    # Reference
    original_invoice_id: str = ""
    original_invoice_number: str = ""
    po_number: str = ""
    
    # Dates
    credit_date: date = field(default_factory=date.today)
    received_date: date = field(default_factory=date.today)
    expiry_date: Optional[date] = None
    
    # Amounts
    subtotal: float = 0.0
    tax_amount: float = 0.0
    total_amount: float = 0.0
    amount_applied: float = 0.0
    currency: str = "USD"
    
    # Line items
    line_items: List[CreditLineItem] = field(default_factory=list)
    
    # Status
    status: CreditStatus = CreditStatus.PENDING
    
    # Reason
    reason_code: str = ""
    reason_description: str = ""
    
    # Applications
    applications: List[CreditApplication] = field(default_factory=list)
    
    # Metadata
    notes: str = ""
    source: str = ""  # email, manual, erp_sync
    email_id: str = ""
    attachment_id: str = ""
    
    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    organization_id: str = "default"
    
    # ERP Integration
    erp_credit_id: str = ""
    posted_to_erp: bool = False
    
    @property
    def amount_remaining(self) -> float:
        """Unapplied credit amount."""
        return self.total_amount - self.amount_applied
    
    @property
    def is_fully_applied(self) -> bool:
        """Check if credit is fully applied."""
        return self.amount_remaining <= 0.01  # Allow small rounding
    
    def calculate_totals(self):
        """Recalculate totals from line items."""
        self.subtotal = sum(item.amount for item in self.line_items)
        self.total_amount = self.subtotal + self.tax_amount
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "credit_id": self.credit_id,
            "credit_number": self.credit_number,
            "credit_type": self.credit_type.value,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "original_invoice_id": self.original_invoice_id,
            "original_invoice_number": self.original_invoice_number,
            "credit_date": self.credit_date.isoformat(),
            "received_date": self.received_date.isoformat(),
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "subtotal": self.subtotal,
            "tax_amount": self.tax_amount,
            "total_amount": self.total_amount,
            "amount_applied": self.amount_applied,
            "amount_remaining": self.amount_remaining,
            "currency": self.currency,
            "line_items": [item.to_dict() for item in self.line_items],
            "status": self.status.value,
            "reason_code": self.reason_code,
            "reason_description": self.reason_description,
            "applications": [app.to_dict() for app in self.applications],
            "is_fully_applied": self.is_fully_applied,
            "notes": self.notes,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "posted_to_erp": self.posted_to_erp,
        }


class CreditNoteService:
    """
    Service for managing credit notes and debit memos.
    """
    
    # Detection patterns for credit notes
    CREDIT_PATTERNS = [
        r'credit\s*(?:note|memo)',
        r'credit\s*#?\s*\d+',
        r'refund',
        r'return\s*credit',
        r'price\s*adjustment',
        r'rebate',
        r'allowance',
        r'debit\s*(?:note|memo)',
    ]
    
    # Reason codes
    REASON_CODES = {
        "RETURN": "Merchandise return",
        "DAMAGE": "Damaged goods",
        "SHORT": "Shortage on delivery",
        "PRICE": "Price discrepancy",
        "QUAL": "Quality issue",
        "CANCEL": "Order cancellation",
        "REBATE": "Volume rebate",
        "ALLOW": "Trade allowance",
        "OTHER": "Other",
    }
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self._credits: Dict[str, CreditNote] = {}
        self._vendor_credits: Dict[str, List[str]] = {}  # vendor_id -> [credit_ids]
    
    def is_credit_document(self, subject: str, body: str = "") -> bool:
        """
        Detect if an email is a credit note or debit memo.
        """
        text = f"{subject} {body}".lower()
        
        for pattern in self.CREDIT_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        
        # Check for negative amounts
        if re.search(r'-\s*\$[\d,]+\.?\d*', text):
            return True
        
        return False
    
    def create_credit_note(
        self,
        vendor_id: str,
        vendor_name: str,
        total_amount: float,
        credit_type: CreditType = CreditType.CREDIT_NOTE,
        original_invoice_id: str = "",
        reason_code: str = "OTHER",
        line_items: List[Dict[str, Any]] = None,
        **kwargs
    ) -> CreditNote:
        """Create a new credit note."""
        credit = CreditNote(
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            total_amount=total_amount,
            credit_type=credit_type,
            original_invoice_id=original_invoice_id,
            reason_code=reason_code,
            reason_description=self.REASON_CODES.get(reason_code, ""),
            organization_id=self.organization_id,
            **kwargs
        )
        
        # Generate credit number
        if not credit.credit_number:
            prefix = "CR" if credit_type == CreditType.CREDIT_NOTE else "DM"
            credit.credit_number = f"{prefix}-{datetime.now().strftime('%Y%m%d')}-{len(self._credits) + 1:04d}"
        
        # Add line items
        if line_items:
            for item_data in line_items:
                item = CreditLineItem(**item_data)
                credit.line_items.append(item)
            credit.calculate_totals()
        else:
            # Single-line credit
            credit.subtotal = total_amount
        
        self._credits[credit.credit_id] = credit
        
        # Index by vendor
        if vendor_id not in self._vendor_credits:
            self._vendor_credits[vendor_id] = []
        self._vendor_credits[vendor_id].append(credit.credit_id)
        
        logger.info(f"Created {credit_type.value}: {credit.credit_number} for ${total_amount:.2f}")
        return credit
    
    def create_debit_memo(
        self,
        vendor_id: str,
        vendor_name: str,
        total_amount: float,
        reason_code: str,
        description: str = "",
        original_invoice_id: str = "",
        **kwargs
    ) -> CreditNote:
        """Create a debit memo (buyer-initiated)."""
        return self.create_credit_note(
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            total_amount=total_amount,
            credit_type=CreditType.DEBIT_MEMO,
            original_invoice_id=original_invoice_id,
            reason_code=reason_code,
            reason_description=description or self.REASON_CODES.get(reason_code, ""),
            source="manual",
            **kwargs
        )
    
    def extract_credit_from_email(
        self,
        email_id: str,
        subject: str,
        body: str,
        sender: str,
        vendor_name: str = "",
    ) -> Optional[CreditNote]:
        """
        Extract credit note details from an email.
        """
        if not self.is_credit_document(subject, body):
            return None
        
        text = f"{subject} {body}"
        
        # Extract credit number
        credit_number = ""
        credit_match = re.search(r'(?:credit|cr|cn)[\s#:]*([A-Z0-9\-]+)', text, re.IGNORECASE)
        if credit_match:
            credit_number = credit_match.group(1)
        
        # Extract amount
        amount = 0.0
        amount_matches = re.findall(r'\$[\d,]+\.?\d*', text)
        if amount_matches:
            # Take the largest amount as the credit total
            amounts = [float(a.replace('$', '').replace(',', '')) for a in amount_matches]
            amount = max(amounts)
        
        # Extract original invoice reference
        original_invoice = ""
        inv_match = re.search(r'(?:invoice|inv)[\s#:]*([A-Z0-9\-]+)', text, re.IGNORECASE)
        if inv_match:
            original_invoice = inv_match.group(1)
        
        # Determine credit type
        credit_type = CreditType.CREDIT_NOTE
        if 'debit' in text.lower():
            credit_type = CreditType.DEBIT_MEMO
        elif 'rebate' in text.lower():
            credit_type = CreditType.REBATE
        elif 'return' in text.lower():
            credit_type = CreditType.RETURN_CREDIT
        
        # Determine reason
        reason_code = "OTHER"
        for code, desc in self.REASON_CODES.items():
            if code.lower() in text.lower() or desc.lower() in text.lower():
                reason_code = code
                break
        
        if amount > 0:
            credit = self.create_credit_note(
                vendor_id=sender,
                vendor_name=vendor_name or sender,
                total_amount=amount,
                credit_type=credit_type,
                credit_number=credit_number,
                original_invoice_number=original_invoice,
                reason_code=reason_code,
                source="email",
                email_id=email_id,
            )
            return credit
        
        return None
    
    def verify_credit(self, credit_id: str, verified_by: str) -> CreditNote:
        """Verify a credit note."""
        credit = self._credits.get(credit_id)
        if not credit:
            raise ValueError(f"Credit {credit_id} not found")
        
        credit.status = CreditStatus.VERIFIED
        credit.updated_at = datetime.now()
        
        logger.info(f"Credit {credit.credit_number} verified by {verified_by}")
        return credit
    
    def apply_credit_to_invoice(
        self,
        credit_id: str,
        invoice_id: str,
        amount: float,
        applied_by: str,
    ) -> CreditApplication:
        """Apply credit to an invoice."""
        credit = self._credits.get(credit_id)
        if not credit:
            raise ValueError(f"Credit {credit_id} not found")
        
        if amount > credit.amount_remaining:
            raise ValueError(f"Amount ${amount:.2f} exceeds remaining credit ${credit.amount_remaining:.2f}")
        
        application = CreditApplication(
            credit_id=credit_id,
            invoice_id=invoice_id,
            amount_applied=amount,
            applied_by=applied_by,
        )
        
        credit.applications.append(application)
        credit.amount_applied += amount
        credit.updated_at = datetime.now()
        
        if credit.is_fully_applied:
            credit.status = CreditStatus.APPLIED
        else:
            credit.status = CreditStatus.PARTIALLY_APPLIED
        
        logger.info(f"Applied ${amount:.2f} from credit {credit.credit_number} to invoice {invoice_id}")
        return application
    
    def auto_apply_credits(self, vendor_id: str) -> List[CreditApplication]:
        """
        Automatically apply available credits to open invoices for a vendor.
        Returns list of applications made.
        """
        # This would integrate with invoice service to find open invoices
        # For now, return empty list - would need invoice service integration
        applications = []
        
        available_credits = self.get_available_credits_for_vendor(vendor_id)
        if not available_credits:
            return applications
        
        # TODO: Get open invoices from invoice service
        # For each invoice, apply oldest credits first (FIFO)
        
        logger.info(f"Auto-apply credits for vendor {vendor_id}: {len(applications)} applications")
        return applications
    
    def get_credit(self, credit_id: str) -> Optional[CreditNote]:
        """Get a credit note by ID."""
        return self._credits.get(credit_id)
    
    def get_credits_for_vendor(self, vendor_id: str) -> List[CreditNote]:
        """Get all credits for a vendor."""
        credit_ids = self._vendor_credits.get(vendor_id, [])
        return [self._credits[cid] for cid in credit_ids if cid in self._credits]
    
    def get_available_credits_for_vendor(self, vendor_id: str) -> List[CreditNote]:
        """Get credits with remaining balance for a vendor."""
        return [
            c for c in self.get_credits_for_vendor(vendor_id)
            if c.amount_remaining > 0 and c.status not in [CreditStatus.CANCELLED, CreditStatus.EXPIRED]
        ]
    
    def get_vendor_credit_balance(self, vendor_id: str) -> float:
        """Get total available credit balance for a vendor."""
        return sum(c.amount_remaining for c in self.get_available_credits_for_vendor(vendor_id))
    
    def search_credits(
        self,
        vendor_name: str = "",
        status: CreditStatus = None,
        credit_type: CreditType = None,
        from_date: date = None,
        to_date: date = None,
        has_balance: bool = None,
    ) -> List[CreditNote]:
        """Search credit notes."""
        results = list(self._credits.values())
        
        if vendor_name:
            vendor_lower = vendor_name.lower()
            results = [c for c in results if vendor_lower in c.vendor_name.lower()]
        
        if status:
            results = [c for c in results if c.status == status]
        
        if credit_type:
            results = [c for c in results if c.credit_type == credit_type]
        
        if from_date:
            results = [c for c in results if c.credit_date >= from_date]
        
        if to_date:
            results = [c for c in results if c.credit_date <= to_date]
        
        if has_balance is not None:
            if has_balance:
                results = [c for c in results if c.amount_remaining > 0]
            else:
                results = [c for c in results if c.amount_remaining <= 0]
        
        return results
    
    def get_summary(self) -> Dict[str, Any]:
        """Get credit summary statistics."""
        credits = list(self._credits.values())
        
        return {
            "total_credits": len(credits),
            "by_type": {
                ct.value: len([c for c in credits if c.credit_type == ct])
                for ct in CreditType
            },
            "by_status": {
                status.value: len([c for c in credits if c.status == status])
                for status in CreditStatus
            },
            "total_credit_value": sum(c.total_amount for c in credits),
            "total_applied": sum(c.amount_applied for c in credits),
            "total_available": sum(c.amount_remaining for c in credits),
            "pending_verification": len([c for c in credits if c.status == CreditStatus.PENDING]),
        }


# Singleton instance cache
_instances: Dict[str, CreditNoteService] = {}


def get_credit_note_service(organization_id: str = "default") -> CreditNoteService:
    """Get or create credit note service for organization."""
    if organization_id not in _instances:
        _instances[organization_id] = CreditNoteService(organization_id)
    return _instances[organization_id]
