"""
Clearledgr Core Data Models

This is the SINGLE SOURCE OF TRUTH for all data in Clearledgr.
All surfaces (Gmail, Sheets, Slack) read and write through these models.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
import uuid


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


class ExceptionType(str, Enum):
    """Type of reconciliation exception."""
    NO_MATCH = "no_match"
    AMOUNT_VARIANCE = "amount_variance"
    DATE_MISMATCH = "date_mismatch"
    DUPLICATE = "duplicate"
    MISSING_DATA = "missing_data"


class ExceptionPriority(str, Enum):
    """Exception priority level."""
    CRITICAL = "critical"    # >€10,000 or regulatory
    HIGH = "high"            # >€5,000
    MEDIUM = "medium"        # >€1,000
    LOW = "low"              # <€1,000


class ApprovalStatus(str, Enum):
    """Approval workflow status."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


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
class Match:
    """A reconciliation match between transactions."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # Matched transactions
    gateway_id: Optional[str] = None
    bank_id: Optional[str] = None
    internal_id: Optional[str] = None
    
    # Match quality
    score: int = 0  # 0-100
    confidence: float = 0.0
    match_type: str = "auto"  # auto, manual, ai
    
    # Score breakdown
    amount_score: int = 0
    date_score: int = 0
    description_score: int = 0
    reference_score: int = 0
    
    # Status
    is_three_way: bool = False
    is_approved: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    
    # Metadata
    organization_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass 
class Exception:
    """A reconciliation exception requiring review."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # Transaction reference
    transaction_id: str = ""
    transaction_source: TransactionSource = TransactionSource.MANUAL
    
    # Exception details
    type: ExceptionType = ExceptionType.NO_MATCH
    priority: ExceptionPriority = ExceptionPriority.MEDIUM
    
    # Financial context
    amount: float = 0.0
    currency: str = "EUR"
    vendor: Optional[str] = None
    
    # Near matches
    near_matches: List[str] = field(default_factory=list)
    nearest_amount_diff: Optional[float] = None
    nearest_date_diff: Optional[int] = None
    
    # AI analysis
    ai_explanation: Optional[str] = None
    ai_suggested_action: Optional[str] = None
    
    # Resolution
    status: str = "open"  # open, resolved, ignored
    resolved_by: Optional[str] = None
    resolved_at: Optional[str] = None
    resolution_notes: Optional[str] = None
    
    # Routing
    assigned_to: Optional[str] = None
    escalated_to: Optional[str] = None
    
    # Metadata
    organization_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['type'] = self.type.value
        data['priority'] = self.priority.value
        data['transaction_source'] = self.transaction_source.value
        return data


@dataclass
class DraftEntry:
    """A draft journal entry awaiting approval."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # Match reference
    match_id: str = ""
    
    # Entry details
    debit_account: str = ""
    credit_account: str = ""
    amount: float = 0.0
    currency: str = "EUR"
    description: str = ""
    posting_date: str = ""
    
    # Confidence
    confidence: float = 0.0
    auto_generated: bool = True
    
    # Approval
    status: ApprovalStatus = ApprovalStatus.PENDING
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    rejection_reason: Optional[str] = None
    
    # ERP posting
    posted_to_erp: bool = False
    erp_document_id: Optional[str] = None
    posted_at: Optional[str] = None
    
    # Metadata
    organization_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
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
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AuditLog:
    """Audit trail for compliance."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # Action
    action: str = ""  # created, updated, approved, reconciled, etc.
    entity_type: str = ""  # transaction, match, exception, draft
    entity_id: str = ""
    
    # Actor
    user_id: str = ""
    user_email: Optional[str] = None
    surface: str = ""  # gmail, sheets, slack, api
    
    # Details
    changes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Timestamp
    organization_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
