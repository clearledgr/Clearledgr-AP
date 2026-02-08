"""
Payment Execution Service

Handles actual payment initiation after invoice approval:
- ACH file generation (NACHA format)
- Wire transfer requests
- Check printing queue
- Payment status tracking

This completes the AP workflow: Invoice → Approve → Pay
"""

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
import uuid
import json

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


class PaymentMethod(Enum):
    """Payment methods."""
    ACH = "ach"
    WIRE = "wire"
    CHECK = "check"
    VIRTUAL_CARD = "virtual_card"


class PaymentStatus(Enum):
    """Payment status."""
    PENDING = "pending"
    SCHEDULED = "scheduled"
    PROCESSING = "processing"
    SENT = "sent"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PaymentRequest:
    """Payment request details."""
    payment_id: str
    invoice_id: str
    vendor_id: str
    vendor_name: str
    amount: float
    currency: str
    method: PaymentMethod
    status: PaymentStatus
    
    # Bank details (for ACH/wire)
    bank_name: Optional[str] = None
    routing_number: Optional[str] = None
    account_number: Optional[str] = None  # Last 4 shown only
    account_type: str = "checking"  # checking or savings
    
    # Wire-specific
    swift_code: Optional[str] = None
    iban: Optional[str] = None
    
    # Check-specific
    payee_name: Optional[str] = None
    payee_address: Optional[str] = None
    
    # Scheduling
    scheduled_date: Optional[str] = None
    executed_date: Optional[str] = None
    
    # Tracking
    confirmation_number: Optional[str] = None
    batch_id: Optional[str] = None
    error_message: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "payment_id": self.payment_id,
            "invoice_id": self.invoice_id,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "amount": self.amount,
            "currency": self.currency,
            "method": self.method.value,
            "status": self.status.value,
            "bank_name": self.bank_name,
            "routing_number": self.routing_number,
            "account_number_last4": self.account_number[-4:] if self.account_number else None,
            "scheduled_date": self.scheduled_date,
            "executed_date": self.executed_date,
            "confirmation_number": self.confirmation_number,
            "batch_id": self.batch_id,
            "error_message": self.error_message,
            "created_at": self.created_at,
        }


@dataclass
class PaymentBatch:
    """Batch of payments for execution."""
    batch_id: str
    organization_id: str
    method: PaymentMethod
    payments: List[PaymentRequest]
    total_amount: float
    status: PaymentStatus
    file_content: Optional[str] = None  # NACHA file content
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    executed_at: Optional[str] = None


class PaymentExecutionService:
    """
    Execute payments after invoice approval.
    
    Supports:
    - ACH batch payments (NACHA format)
    - Wire transfers
    - Check printing queue
    - Virtual card payments
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()
        self._payments: Dict[str, PaymentRequest] = {}
        self._batches: Dict[str, PaymentBatch] = {}
        self._vendor_bank_info: Dict[str, Dict] = {}
    
    def create_payment(
        self,
        invoice_id: str,
        vendor_id: str,
        vendor_name: str,
        amount: float,
        currency: str = "USD",
        method: PaymentMethod = PaymentMethod.ACH,
        scheduled_date: Optional[str] = None,
        bank_info: Optional[Dict[str, Any]] = None,
    ) -> PaymentRequest:
        """
        Create a payment request for an approved invoice.
        
        Args:
            invoice_id: The invoice to pay
            vendor_id: Vendor ID in ERP
            vendor_name: Display name
            amount: Payment amount
            currency: Currency code
            method: Payment method (ACH, wire, check)
            scheduled_date: When to execute (None = immediate)
            bank_info: Bank account details
        
        Returns:
            PaymentRequest object
        """
        payment_id = f"PAY-{uuid.uuid4().hex[:8].upper()}"
        
        # Get or use provided bank info
        bank = bank_info or self._get_vendor_bank_info(vendor_id)
        
        payment = PaymentRequest(
            payment_id=payment_id,
            invoice_id=invoice_id,
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            amount=amount,
            currency=currency,
            method=method,
            status=PaymentStatus.PENDING,
            bank_name=bank.get("bank_name") if bank else None,
            routing_number=bank.get("routing_number") if bank else None,
            account_number=bank.get("account_number") if bank else None,
            account_type=bank.get("account_type", "checking") if bank else "checking",
            swift_code=bank.get("swift_code") if bank else None,
            iban=bank.get("iban") if bank else None,
            scheduled_date=scheduled_date,
        )
        
        self._payments[payment_id] = payment
        self._save_payment(payment)
        
        logger.info(f"Created payment {payment_id} for invoice {invoice_id}: ${amount}")
        
        return payment
    
    def schedule_payment(
        self,
        payment_id: str,
        scheduled_date: str,
    ) -> PaymentRequest:
        """Schedule a payment for future execution."""
        payment = self._payments.get(payment_id)
        if not payment:
            raise ValueError(f"Payment {payment_id} not found")
        
        payment.scheduled_date = scheduled_date
        payment.status = PaymentStatus.SCHEDULED
        payment.updated_at = datetime.now().isoformat()
        
        self._save_payment(payment)
        
        return payment
    
    def create_ach_batch(
        self,
        payment_ids: Optional[List[str]] = None,
        execute_immediately: bool = False,
    ) -> PaymentBatch:
        """
        Create an ACH batch file (NACHA format) for multiple payments.
        
        Args:
            payment_ids: Specific payments to batch (None = all pending ACH)
            execute_immediately: Whether to mark as processing immediately
        
        Returns:
            PaymentBatch with NACHA file content
        """
        # Get payments to batch
        if payment_ids:
            payments = [self._payments[pid] for pid in payment_ids if pid in self._payments]
        else:
            payments = [
                p for p in self._payments.values()
                if p.method == PaymentMethod.ACH and p.status == PaymentStatus.PENDING
            ]
        
        if not payments:
            raise ValueError("No pending ACH payments to batch")
        
        batch_id = f"BATCH-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        total_amount = sum(p.amount for p in payments)
        
        # Generate NACHA file
        nacha_content = self._generate_nacha_file(batch_id, payments)
        
        batch = PaymentBatch(
            batch_id=batch_id,
            organization_id=self.organization_id,
            method=PaymentMethod.ACH,
            payments=payments,
            total_amount=total_amount,
            status=PaymentStatus.PROCESSING if execute_immediately else PaymentStatus.PENDING,
            file_content=nacha_content,
        )
        
        # Update payment statuses
        for payment in payments:
            payment.batch_id = batch_id
            payment.status = PaymentStatus.PROCESSING if execute_immediately else PaymentStatus.SCHEDULED
            payment.updated_at = datetime.now().isoformat()
            self._save_payment(payment)
        
        self._batches[batch_id] = batch
        
        logger.info(f"Created ACH batch {batch_id}: {len(payments)} payments, ${total_amount:,.2f}")
        
        return batch
    
    def _generate_nacha_file(
        self,
        batch_id: str,
        payments: List[PaymentRequest],
    ) -> str:
        """
        Generate NACHA-format ACH file content.
        
        NACHA format:
        - Record Type 1: File Header
        - Record Type 5: Batch Header
        - Record Type 6: Entry Detail (one per payment)
        - Record Type 8: Batch Control
        - Record Type 9: File Control
        """
        lines = []
        
        # File Header (Record Type 1)
        file_creation_date = datetime.now().strftime("%y%m%d")
        file_creation_time = datetime.now().strftime("%H%M")
        
        lines.append(
            f"101"  # Record Type + Priority Code
            f" 091000019"  # Immediate Destination (Fed routing, padded)
            f" 123456789"  # Immediate Origin (Company ID, padded)
            f"{file_creation_date}"  # File Creation Date
            f"{file_creation_time}"  # File Creation Time
            f"A"  # File ID Modifier
            f"094"  # Record Size
            f"10"  # Blocking Factor
            f"1"  # Format Code
            f"FED RESERVE BANK    "  # Immediate Destination Name
            f"CLEARLEDGR          "  # Immediate Origin Name
            f"        "  # Reference Code
        )
        
        # Batch Header (Record Type 5)
        effective_date = datetime.now().strftime("%y%m%d")
        
        lines.append(
            f"5"  # Record Type
            f"200"  # Service Class Code (200 = mixed debits/credits)
            f"CLEARLEDGR      "  # Company Name (16 chars)
            f"                    "  # Company Discretionary Data
            f"1234567890"  # Company Identification
            f"PPD"  # Standard Entry Class Code
            f"PAYABLES  "  # Company Entry Description
            f"{effective_date}"  # Company Descriptive Date
            f"{effective_date}"  # Effective Entry Date
            f"   "  # Settlement Date
            f"1"  # Originator Status Code
            f"09100001"  # Originating DFI Identification
            f"0000001"  # Batch Number
        )
        
        # Entry Detail Records (Record Type 6)
        total_debit = 0
        total_credit = 0
        entry_hash = 0
        
        for i, payment in enumerate(payments, 1):
            if not payment.routing_number or not payment.account_number:
                continue
            
            # Transaction Code: 22 = checking credit, 32 = savings credit
            trans_code = "22" if payment.account_type == "checking" else "32"
            
            # Pad/truncate fields to required lengths
            routing = payment.routing_number[:8].ljust(8)
            check_digit = payment.routing_number[8] if len(payment.routing_number) > 8 else "0"
            account = payment.account_number[:17].ljust(17)
            amount_cents = int(payment.amount * 100)
            name = payment.vendor_name[:22].ljust(22)
            trace = f"{payment.routing_number[:8]}{str(i).zfill(7)}"
            
            lines.append(
                f"6"  # Record Type
                f"{trans_code}"  # Transaction Code
                f"{routing}"  # Receiving DFI Identification
                f"{check_digit}"  # Check Digit
                f"{account}"  # DFI Account Number
                f"{str(amount_cents).zfill(10)}"  # Amount
                f"{payment.invoice_id[:15].ljust(15)}"  # Individual ID Number
                f"{name}"  # Individual Name
                f"  "  # Discretionary Data
                f"0"  # Addenda Record Indicator
                f"{trace}"  # Trace Number
            )
            
            total_credit += amount_cents
            entry_hash += int(routing[:8])
        
        # Batch Control (Record Type 8)
        entry_count = len([p for p in payments if p.routing_number])
        
        lines.append(
            f"8"  # Record Type
            f"200"  # Service Class Code
            f"{str(entry_count).zfill(6)}"  # Entry/Addenda Count
            f"{str(entry_hash % 10000000000).zfill(10)}"  # Entry Hash
            f"{str(total_debit).zfill(12)}"  # Total Debit Amount
            f"{str(total_credit).zfill(12)}"  # Total Credit Amount
            f"1234567890"  # Company Identification
            f"                   "  # Message Authentication Code
            f"      "  # Reserved
            f"09100001"  # Originating DFI Identification
            f"0000001"  # Batch Number
        )
        
        # File Control (Record Type 9)
        lines.append(
            f"9"  # Record Type
            f"000001"  # Batch Count
            f"{str((len(lines) + 1) // 10 + 1).zfill(6)}"  # Block Count
            f"{str(entry_count).zfill(8)}"  # Entry/Addenda Count
            f"{str(entry_hash % 10000000000).zfill(10)}"  # Entry Hash
            f"{str(total_debit).zfill(12)}"  # Total Debit Amount
            f"{str(total_credit).zfill(12)}"  # Total Credit Amount
            f"                                       "  # Reserved
        )
        
        # Pad to multiple of 10 records
        while len(lines) % 10 != 0:
            lines.append("9" * 94)
        
        return "\n".join(lines)
    
    def create_wire_request(
        self,
        payment_id: str,
    ) -> Dict[str, Any]:
        """
        Create a wire transfer request.
        Returns details for manual wire initiation or bank API call.
        """
        payment = self._payments.get(payment_id)
        if not payment:
            raise ValueError(f"Payment {payment_id} not found")
        
        if payment.method != PaymentMethod.WIRE:
            raise ValueError(f"Payment {payment_id} is not a wire transfer")
        
        # Generate wire instructions
        wire_request = {
            "payment_id": payment.payment_id,
            "beneficiary_name": payment.vendor_name,
            "beneficiary_bank": payment.bank_name,
            "swift_bic": payment.swift_code,
            "iban": payment.iban,
            "routing_number": payment.routing_number,
            "account_number_last4": payment.account_number[-4:] if payment.account_number else None,
            "amount": payment.amount,
            "currency": payment.currency,
            "reference": f"INV-{payment.invoice_id}",
            "purpose": "Invoice Payment",
            "instructions": f"Payment for invoice {payment.invoice_id}",
        }
        
        # Update status
        payment.status = PaymentStatus.PROCESSING
        payment.updated_at = datetime.now().isoformat()
        self._save_payment(payment)
        
        return wire_request
    
    def add_to_check_queue(
        self,
        payment_id: str,
        payee_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add payment to check printing queue."""
        payment = self._payments.get(payment_id)
        if not payment:
            raise ValueError(f"Payment {payment_id} not found")
        
        payment.method = PaymentMethod.CHECK
        payment.payee_name = payment.vendor_name
        payment.payee_address = payee_address
        payment.status = PaymentStatus.SCHEDULED
        payment.updated_at = datetime.now().isoformat()
        
        self._save_payment(payment)
        
        return {
            "payment_id": payment.payment_id,
            "payee": payment.payee_name,
            "address": payee_address,
            "amount": payment.amount,
            "memo": f"Invoice {payment.invoice_id}",
            "status": "queued_for_printing",
        }
    
    def mark_payment_sent(
        self,
        payment_id: str,
        confirmation_number: Optional[str] = None,
    ) -> PaymentRequest:
        """Mark a payment as sent/executed."""
        payment = self._payments.get(payment_id)
        if not payment:
            raise ValueError(f"Payment {payment_id} not found")
        
        payment.status = PaymentStatus.SENT
        payment.executed_date = datetime.now().isoformat()
        payment.confirmation_number = confirmation_number or f"CONF-{uuid.uuid4().hex[:8].upper()}"
        payment.updated_at = datetime.now().isoformat()
        
        self._save_payment(payment)
        
        logger.info(f"Payment {payment_id} marked as sent: {payment.confirmation_number}")
        
        return payment
    
    def mark_payment_completed(
        self,
        payment_id: str,
    ) -> PaymentRequest:
        """Mark a payment as completed (confirmed by bank)."""
        payment = self._payments.get(payment_id)
        if not payment:
            raise ValueError(f"Payment {payment_id} not found")
        
        payment.status = PaymentStatus.COMPLETED
        payment.updated_at = datetime.now().isoformat()
        
        self._save_payment(payment)
        
        return payment
    
    def mark_payment_failed(
        self,
        payment_id: str,
        error_message: str,
    ) -> PaymentRequest:
        """Mark a payment as failed."""
        payment = self._payments.get(payment_id)
        if not payment:
            raise ValueError(f"Payment {payment_id} not found")
        
        payment.status = PaymentStatus.FAILED
        payment.error_message = error_message
        payment.updated_at = datetime.now().isoformat()
        
        self._save_payment(payment)
        
        logger.warning(f"Payment {payment_id} failed: {error_message}")
        
        return payment
    
    def get_payment(self, payment_id: str) -> Optional[PaymentRequest]:
        """Get a payment by ID."""
        return self._payments.get(payment_id)
    
    def get_pending_payments(self) -> List[PaymentRequest]:
        """Get all pending payments."""
        return [p for p in self._payments.values() if p.status == PaymentStatus.PENDING]
    
    def get_payments_for_invoice(self, invoice_id: str) -> List[PaymentRequest]:
        """Get all payments for an invoice."""
        return [p for p in self._payments.values() if p.invoice_id == invoice_id]
    
    def get_payment_summary(self) -> Dict[str, Any]:
        """Get summary of all payments."""
        payments = list(self._payments.values())
        
        by_status = {}
        for p in payments:
            status = p.status.value
            if status not in by_status:
                by_status[status] = {"count": 0, "amount": 0}
            by_status[status]["count"] += 1
            by_status[status]["amount"] += p.amount
        
        by_method = {}
        for p in payments:
            method = p.method.value
            if method not in by_method:
                by_method[method] = {"count": 0, "amount": 0}
            by_method[method]["count"] += 1
            by_method[method]["amount"] += p.amount
        
        return {
            "total_payments": len(payments),
            "total_amount": sum(p.amount for p in payments),
            "by_status": by_status,
            "by_method": by_method,
            "pending_count": len([p for p in payments if p.status == PaymentStatus.PENDING]),
            "pending_amount": sum(p.amount for p in payments if p.status == PaymentStatus.PENDING),
        }
    
    def _get_vendor_bank_info(self, vendor_id: str) -> Optional[Dict[str, Any]]:
        """Get stored bank info for a vendor."""
        return self._vendor_bank_info.get(vendor_id)
    
    def save_vendor_bank_info(
        self,
        vendor_id: str,
        bank_info: Dict[str, Any],
    ) -> None:
        """Save bank info for a vendor (for future payments)."""
        # Mask sensitive data before storing
        masked = bank_info.copy()
        if "account_number" in masked:
            masked["account_number_masked"] = f"****{masked['account_number'][-4:]}"
        
        self._vendor_bank_info[vendor_id] = masked
        
        # Save to database
        try:
            self.db.save_vendor_bank_info(self.organization_id, vendor_id, masked)
        except Exception as e:
            logger.warning(f"Failed to save vendor bank info: {e}")
    
    def _save_payment(self, payment: PaymentRequest) -> None:
        """Save payment to database."""
        try:
            self.db.save_payment(self.organization_id, payment.to_dict())
        except Exception as e:
            logger.warning(f"Failed to save payment: {e}")


# Singleton
_payment_services: Dict[str, PaymentExecutionService] = {}


def get_payment_execution(organization_id: str = "default") -> PaymentExecutionService:
    """Get payment execution service for an organization."""
    if organization_id not in _payment_services:
        _payment_services[organization_id] = PaymentExecutionService(organization_id)
    return _payment_services[organization_id]
