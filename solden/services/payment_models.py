"""Payment tracking models — informational records for payment readiness.

The agent NEVER executes payments. It tracks readiness and status.
Humans trigger payments in the ERP; humans update payment status
via the API or workspace UI.

Lifecycle:
  1. Invoice posted to ERP  ->  PaymentRecord created (status=ready_for_payment)
  2. Human schedules payment ->  status=scheduled  (via PATCH API)
  3. Human triggers payment  ->  status=processing  (via PATCH API)
  4. Payment completes       ->  status=completed   (via PATCH API)
  5. Payment fails           ->  status=failed       (via PATCH API)
  6. Payment cancelled       ->  status=cancelled    (via PATCH API)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


PAYMENT_STATUSES = frozenset({
    "ready_for_payment",
    "scheduled",
    "processing",
    "completed",
    "failed",
    "cancelled",
})

PAYMENT_METHODS = frozenset({
    "ach",
    "wire",
    "check",
    "virtual_card",
})


@dataclass
class PaymentRecord:
    """Informational payment tracking record."""

    id: str
    ap_item_id: str
    organization_id: str
    vendor_name: str
    amount: float
    currency: str = "USD"
    status: str = "ready_for_payment"
    payment_method: Optional[str] = None
    payment_reference: Optional[str] = None
    due_date: Optional[str] = None
    scheduled_date: Optional[str] = None
    completed_date: Optional[str] = None
    erp_reference: Optional[str] = None
    notes: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ap_item_id": self.ap_item_id,
            "organization_id": self.organization_id,
            "vendor_name": self.vendor_name,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status,
            "payment_method": self.payment_method,
            "payment_reference": self.payment_reference,
            "due_date": self.due_date,
            "scheduled_date": self.scheduled_date,
            "completed_date": self.completed_date,
            "erp_reference": self.erp_reference,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
