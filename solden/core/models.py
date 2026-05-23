"""
Solden Core Data Models

Shared dataclasses for a couple of intake shapes — ``Transaction`` and
``FinanceEmail`` — plus the identifier type aliases below. These are NOT the
canonical store: the authoritative AP record is the ``ap_items`` table accessed
through ``SoldenDB`` (and the generic ``boxes`` table for declarative types).
Treat these as convenience shapes, not "the single source of truth."

(Reconciliation-era ``Match`` / ``Exception`` / ``DraftEntry`` / ``AuditLog``
dataclasses were removed 2026-05-23 — they had zero consumers; the real audit
trail is the ``audit_events`` hash chain, not an ``AuditLog`` dataclass.)

Naming conventions for key identifiers:

* **APItemId** — UUID primary key of an ``ap_items`` row.  Every DB lookup
  and update uses this.
* **InvoiceKey** — Natural composite key (org + vendor + number + date).
  Uniqueness check during ingestion uses this.
* **InvoiceNumber** — Raw vendor-provided invoice number extracted from the
  email or attachment.
* **OrganizationId** — UUID identifying a tenant / organization row.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import NewType, Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
import uuid

# ---------------------------------------------------------------------------
# Semantic type aliases — use these in new code to clarify intent.
# Existing call-sites are *not* updated yet; these are for gradual adoption.
# ---------------------------------------------------------------------------
APItemId = NewType("APItemId", str)
"""UUID primary key of an ``ap_items`` row."""

InvoiceKey = NewType("InvoiceKey", str)
"""Natural composite key (org + vendor + number + date)."""

InvoiceNumber = NewType("InvoiceNumber", str)
"""Raw vendor-provided invoice number."""

OrganizationId = NewType("OrganizationId", str)
"""UUID identifying a tenant / organization row."""

__all__ = [
    # Type aliases
    "APItemId",
    "InvoiceKey",
    "InvoiceNumber",
    "OrganizationId",
    # Enums
    "TransactionSource",
    "TransactionStatus",
    # Dataclasses
    "Transaction",
    "FinanceEmail",
]


class TransactionSource(str, Enum):
    """Where the transaction came from."""
    GATEWAY = "gateway"      # Stripe, Adyen, PayPal, etc.
    BANK = "bank"            # Bank statement
    INTERNAL = "internal"    # Internal ledger/ERP
    EMAIL = "email"          # Extracted from email
    MANUAL = "manual"        # Manual entry


class TransactionStatus(str, Enum):
    """Transaction reconciliation status."""
    PENDING = "pending"           # Not yet processed
    MATCHED = "matched"           # Successfully matched
    PARTIAL_MATCH = "partial"     # Partially matched
    EXCEPTION = "exception"       # Requires review
    RESOLVED = "resolved"         # Exception resolved
    IGNORED = "ignored"           # User chose to ignore


@dataclass
class Transaction:
    """A financial transaction from any source."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # Core fields
    amount: float = 0.0
    currency: str = "EUR"
    date: str = ""  # ISO format
    description: str = ""
    reference: Optional[str] = None
    
    # Source info
    source: TransactionSource = TransactionSource.MANUAL
    source_id: Optional[str] = None  # ID in source system
    vendor: Optional[str] = None
    
    # Status
    status: TransactionStatus = TransactionStatus.PENDING
    
    # Matching
    matched_with: List[str] = field(default_factory=list)  # IDs of matched transactions
    match_confidence: float = 0.0
    match_score: int = 0
    
    # Metadata
    organization_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['source'] = self.source.value
        data['status'] = self.status.value
        return data


@dataclass
class FinanceEmail:
    """A detected finance email from Gmail."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # Email info
    gmail_id: str = ""
    subject: str = ""
    sender: str = ""
    received_at: str = ""
    
    # Classification
    email_type: str = ""  # invoice, statement, receipt, etc.
    confidence: float = 0.0
    
    # Extracted data
    vendor: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "EUR"
    invoice_number: Optional[str] = None
    
    # Processing
    status: str = "detected"  # detected, processing, processed, ignored
    processed_at: Optional[str] = None
    transaction_id: Optional[str] = None  # Link to created transaction
    
    # Metadata
    organization_id: Optional[str] = None
    user_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
