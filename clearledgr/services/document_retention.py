"""
Document Retention Policy Service

Handles document lifecycle management:
- Retention policy configuration
- Document archival
- Compliance tracking (7-year rule, etc.)
- Document purging
"""

import logging
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
import uuid

logger = logging.getLogger(__name__)


class DocumentType(Enum):
    """Types of documents to retain."""
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    PURCHASE_ORDER = "purchase_order"
    GOODS_RECEIPT = "goods_receipt"
    PAYMENT_RECORD = "payment_record"
    RECEIPT = "receipt"
    CONTRACT = "contract"
    W9 = "w9"
    TAX_FORM = "tax_form"
    BANK_STATEMENT = "bank_statement"
    AUDIT_REPORT = "audit_report"
    EMAIL = "email"
    ATTACHMENT = "attachment"
    OTHER = "other"


class RetentionStatus(Enum):
    """Document retention status."""
    ACTIVE = "active"                  # In active use
    ARCHIVED = "archived"              # Archived but accessible
    PENDING_DELETION = "pending"       # Scheduled for deletion
    DELETED = "deleted"                # Deleted (record only)
    LEGAL_HOLD = "legal_hold"          # Cannot be deleted
    COMPLIANCE_HOLD = "compliance"     # Under compliance review


class RetentionReason(Enum):
    """Reason for retention requirement."""
    IRS_REQUIREMENT = "irs"            # IRS 7-year rule
    SOX_COMPLIANCE = "sox"             # Sarbanes-Oxley
    AUDIT_REQUIREMENT = "audit"        # Audit purposes
    LEGAL_HOLD = "legal"               # Litigation hold
    BUSINESS_NEED = "business"         # Business requirement
    REGULATORY = "regulatory"          # Other regulatory
    POLICY = "policy"                  # Company policy


@dataclass
class RetentionPolicy:
    """Defines retention requirements for a document type."""
    policy_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    document_type: DocumentType = DocumentType.OTHER
    name: str = ""
    
    # Retention period
    retention_years: int = 7           # Default 7 years for IRS
    retention_days: int = 0            # Additional days
    
    # Rules
    reason: RetentionReason = RetentionReason.IRS_REQUIREMENT
    description: str = ""
    
    # Actions
    archive_after_days: int = 365      # Move to archive after 1 year
    warn_before_deletion_days: int = 30
    auto_delete: bool = False          # Auto-delete after retention period
    
    # Scope
    applies_to_vendors: List[str] = field(default_factory=list)  # Empty = all
    applies_to_gl_codes: List[str] = field(default_factory=list)
    min_amount: float = 0.0
    
    is_active: bool = True
    
    def get_retention_end_date(self, document_date: date) -> date:
        """Calculate when retention period ends."""
        return document_date + timedelta(days=self.retention_years * 365 + self.retention_days)
    
    def get_archive_date(self, document_date: date) -> date:
        """Calculate when document should be archived."""
        return document_date + timedelta(days=self.archive_after_days)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "document_type": self.document_type.value,
            "name": self.name,
            "retention_years": self.retention_years,
            "retention_days": self.retention_days,
            "reason": self.reason.value,
            "description": self.description,
            "archive_after_days": self.archive_after_days,
            "auto_delete": self.auto_delete,
            "is_active": self.is_active,
        }


@dataclass
class DocumentRecord:
    """Record of a document subject to retention."""
    record_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    
    # Document identification
    document_id: str = ""              # Invoice ID, PO ID, etc.
    document_type: DocumentType = DocumentType.OTHER
    document_number: str = ""          # Invoice number, PO number, etc.
    
    # Reference
    vendor_id: str = ""
    vendor_name: str = ""
    amount: float = 0.0
    
    # Storage
    storage_location: str = ""         # File path, URL, or storage key
    storage_provider: str = "local"    # local, s3, gcs, azure
    file_size_bytes: int = 0
    mime_type: str = ""
    checksum: str = ""                 # For integrity verification
    
    # Dates
    document_date: date = field(default_factory=date.today)
    received_date: date = field(default_factory=date.today)
    retention_end_date: Optional[date] = None
    archive_date: Optional[date] = None
    deletion_date: Optional[date] = None
    
    # Status
    status: RetentionStatus = RetentionStatus.ACTIVE
    policy_id: str = ""
    
    # Legal hold
    legal_hold: bool = False
    legal_hold_reason: str = ""
    legal_hold_by: str = ""
    legal_hold_date: Optional[datetime] = None
    
    # Metadata
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    archived_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    
    organization_id: str = "default"
    
    @property
    def days_until_retention_end(self) -> int:
        """Days until retention period ends."""
        if not self.retention_end_date:
            return -1
        delta = self.retention_end_date - date.today()
        return delta.days
    
    @property
    def is_past_retention(self) -> bool:
        """Check if past retention period."""
        if not self.retention_end_date:
            return False
        return date.today() > self.retention_end_date
    
    @property
    def can_be_deleted(self) -> bool:
        """Check if document can be deleted."""
        if self.legal_hold:
            return False
        if self.status == RetentionStatus.LEGAL_HOLD:
            return False
        return self.is_past_retention
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "document_id": self.document_id,
            "document_type": self.document_type.value,
            "document_number": self.document_number,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "amount": self.amount,
            "storage_location": self.storage_location,
            "storage_provider": self.storage_provider,
            "file_size_bytes": self.file_size_bytes,
            "document_date": self.document_date.isoformat(),
            "retention_end_date": self.retention_end_date.isoformat() if self.retention_end_date else None,
            "days_until_retention_end": self.days_until_retention_end,
            "status": self.status.value,
            "policy_id": self.policy_id,
            "legal_hold": self.legal_hold,
            "legal_hold_reason": self.legal_hold_reason,
            "can_be_deleted": self.can_be_deleted,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
        }


class DocumentRetentionService:
    """
    Service for document retention management.
    """
    
    # Default retention policies by document type
    DEFAULT_POLICIES = {
        DocumentType.INVOICE: (7, RetentionReason.IRS_REQUIREMENT, "IRS 7-year rule for supporting documents"),
        DocumentType.CREDIT_NOTE: (7, RetentionReason.IRS_REQUIREMENT, "IRS 7-year rule"),
        DocumentType.PURCHASE_ORDER: (7, RetentionReason.IRS_REQUIREMENT, "IRS 7-year rule"),
        DocumentType.PAYMENT_RECORD: (7, RetentionReason.IRS_REQUIREMENT, "IRS 7-year rule"),
        DocumentType.RECEIPT: (7, RetentionReason.IRS_REQUIREMENT, "IRS 7-year rule"),
        DocumentType.TAX_FORM: (7, RetentionReason.IRS_REQUIREMENT, "IRS requirement for tax records"),
        DocumentType.W9: (4, RetentionReason.IRS_REQUIREMENT, "IRS 4 years after last 1099 filed"),
        DocumentType.BANK_STATEMENT: (7, RetentionReason.IRS_REQUIREMENT, "IRS 7-year rule"),
        DocumentType.CONTRACT: (10, RetentionReason.BUSINESS_NEED, "Retain 10 years after expiry"),
        DocumentType.AUDIT_REPORT: (7, RetentionReason.AUDIT_REQUIREMENT, "Audit requirement"),
        DocumentType.EMAIL: (7, RetentionReason.POLICY, "Company policy"),
    }
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self._policies: Dict[str, RetentionPolicy] = {}
        self._documents: Dict[str, DocumentRecord] = {}
        
        # Initialize default policies
        self._init_default_policies()
    
    def _init_default_policies(self):
        """Set up default retention policies."""
        for doc_type, (years, reason, desc) in self.DEFAULT_POLICIES.items():
            policy = RetentionPolicy(
                document_type=doc_type,
                name=f"{doc_type.value.replace('_', ' ').title()} Retention",
                retention_years=years,
                reason=reason,
                description=desc,
            )
            self._policies[policy.policy_id] = policy
    
    def create_policy(
        self,
        document_type: DocumentType,
        name: str,
        retention_years: int,
        reason: RetentionReason = RetentionReason.POLICY,
        description: str = "",
        **kwargs
    ) -> RetentionPolicy:
        """Create a custom retention policy."""
        policy = RetentionPolicy(
            document_type=document_type,
            name=name,
            retention_years=retention_years,
            reason=reason,
            description=description,
            **kwargs
        )
        self._policies[policy.policy_id] = policy
        logger.info(f"Created retention policy: {name}")
        return policy
    
    def get_policy_for_document(self, document_type: DocumentType) -> Optional[RetentionPolicy]:
        """Get the applicable policy for a document type."""
        for policy in self._policies.values():
            if policy.document_type == document_type and policy.is_active:
                return policy
        return None
    
    def register_document(
        self,
        document_id: str,
        document_type: DocumentType,
        document_number: str = "",
        vendor_name: str = "",
        amount: float = 0.0,
        document_date: date = None,
        storage_location: str = "",
        **kwargs
    ) -> DocumentRecord:
        """Register a document for retention tracking."""
        document_date = document_date or date.today()
        
        # Find applicable policy
        policy = self.get_policy_for_document(document_type)
        
        record = DocumentRecord(
            document_id=document_id,
            document_type=document_type,
            document_number=document_number,
            vendor_name=vendor_name,
            amount=amount,
            document_date=document_date,
            storage_location=storage_location,
            organization_id=self.organization_id,
            **kwargs
        )
        
        # Apply policy
        if policy:
            record.policy_id = policy.policy_id
            record.retention_end_date = policy.get_retention_end_date(document_date)
            record.archive_date = policy.get_archive_date(document_date)
        else:
            # Default 7-year retention
            record.retention_end_date = document_date + timedelta(days=7 * 365)
            record.archive_date = document_date + timedelta(days=365)
        
        self._documents[record.record_id] = record
        logger.info(f"Registered document for retention: {document_type.value} {document_number}")
        
        return record
    
    def archive_document(self, record_id: str, archived_by: str = "") -> DocumentRecord:
        """Move document to archive status."""
        record = self._documents.get(record_id)
        if not record:
            raise ValueError(f"Document record {record_id} not found")
        
        record.status = RetentionStatus.ARCHIVED
        record.archived_at = datetime.now()
        record.updated_at = datetime.now()
        
        logger.info(f"Archived document: {record.document_number}")
        return record
    
    def place_legal_hold(
        self,
        record_id: str,
        reason: str,
        placed_by: str,
    ) -> DocumentRecord:
        """Place a legal hold on a document."""
        record = self._documents.get(record_id)
        if not record:
            raise ValueError(f"Document record {record_id} not found")
        
        record.status = RetentionStatus.LEGAL_HOLD
        record.legal_hold = True
        record.legal_hold_reason = reason
        record.legal_hold_by = placed_by
        record.legal_hold_date = datetime.now()
        record.updated_at = datetime.now()
        
        logger.info(f"Legal hold placed on document: {record.document_number}")
        return record
    
    def release_legal_hold(self, record_id: str, released_by: str) -> DocumentRecord:
        """Release a legal hold."""
        record = self._documents.get(record_id)
        if not record:
            raise ValueError(f"Document record {record_id} not found")
        
        record.status = RetentionStatus.ACTIVE
        record.legal_hold = False
        record.updated_at = datetime.now()
        
        logger.info(f"Legal hold released on document: {record.document_number}")
        return record
    
    def schedule_deletion(self, record_id: str, deletion_date: date = None) -> DocumentRecord:
        """Schedule a document for deletion."""
        record = self._documents.get(record_id)
        if not record:
            raise ValueError(f"Document record {record_id} not found")
        
        if not record.can_be_deleted:
            raise ValueError(f"Document cannot be deleted: legal hold or retention active")
        
        record.status = RetentionStatus.PENDING_DELETION
        record.deletion_date = deletion_date or date.today() + timedelta(days=30)
        record.updated_at = datetime.now()
        
        logger.info(f"Scheduled deletion for document: {record.document_number}")
        return record
    
    def delete_document(self, record_id: str, deleted_by: str) -> DocumentRecord:
        """Delete a document (mark as deleted, actual file deletion separate)."""
        record = self._documents.get(record_id)
        if not record:
            raise ValueError(f"Document record {record_id} not found")
        
        if not record.can_be_deleted:
            raise ValueError(f"Document cannot be deleted")
        
        record.status = RetentionStatus.DELETED
        record.deleted_at = datetime.now()
        record.updated_at = datetime.now()
        record.metadata["deleted_by"] = deleted_by
        
        # Actual file deletion would happen here
        # In production, this would call storage service to delete the file
        
        logger.info(f"Deleted document: {record.document_number}")
        return record
    
    def get_documents_for_archival(self) -> List[DocumentRecord]:
        """Get documents that should be archived."""
        today = date.today()
        return [
            doc for doc in self._documents.values()
            if doc.status == RetentionStatus.ACTIVE
            and doc.archive_date
            and doc.archive_date <= today
        ]
    
    def get_documents_for_deletion(self) -> List[DocumentRecord]:
        """Get documents eligible for deletion."""
        return [
            doc for doc in self._documents.values()
            if doc.can_be_deleted
            and doc.status not in [RetentionStatus.DELETED, RetentionStatus.LEGAL_HOLD]
        ]
    
    def get_expiring_documents(self, days: int = 90) -> List[DocumentRecord]:
        """Get documents with retention expiring within X days."""
        cutoff = date.today() + timedelta(days=days)
        return [
            doc for doc in self._documents.values()
            if doc.retention_end_date
            and doc.retention_end_date <= cutoff
            and doc.status not in [RetentionStatus.DELETED, RetentionStatus.LEGAL_HOLD]
        ]
    
    def get_documents_on_legal_hold(self) -> List[DocumentRecord]:
        """Get all documents on legal hold."""
        return [
            doc for doc in self._documents.values()
            if doc.legal_hold or doc.status == RetentionStatus.LEGAL_HOLD
        ]
    
    def run_retention_job(self) -> Dict[str, Any]:
        """
        Run periodic retention maintenance job.
        Returns summary of actions taken.
        """
        results = {
            "archived": 0,
            "scheduled_for_deletion": 0,
            "deleted": 0,
            "errors": [],
        }
        
        # Archive documents
        for doc in self.get_documents_for_archival():
            try:
                self.archive_document(doc.record_id, "system")
                results["archived"] += 1
            except Exception as e:
                results["errors"].append(f"Archive {doc.document_number}: {e}")
        
        # Schedule deletion for expired documents
        for doc in self.get_documents_for_deletion():
            if doc.status == RetentionStatus.ARCHIVED:
                try:
                    self.schedule_deletion(doc.record_id)
                    results["scheduled_for_deletion"] += 1
                except Exception as e:
                    results["errors"].append(f"Schedule deletion {doc.document_number}: {e}")
        
        # Delete documents past their deletion date
        today = date.today()
        for doc in self._documents.values():
            if (doc.status == RetentionStatus.PENDING_DELETION 
                and doc.deletion_date 
                and doc.deletion_date <= today):
                try:
                    self.delete_document(doc.record_id, "system")
                    results["deleted"] += 1
                except Exception as e:
                    results["errors"].append(f"Delete {doc.document_number}: {e}")
        
        logger.info(f"Retention job completed: {results}")
        return results
    
    def get_compliance_report(self) -> Dict[str, Any]:
        """Generate compliance report."""
        documents = list(self._documents.values())
        
        # Group by status
        by_status = {}
        for status in RetentionStatus:
            docs = [d for d in documents if d.status == status]
            by_status[status.value] = {
                "count": len(docs),
                "total_size_mb": sum(d.file_size_bytes for d in docs) / (1024 * 1024),
            }
        
        # Group by document type
        by_type = {}
        for doc_type in DocumentType:
            docs = [d for d in documents if d.document_type == doc_type]
            by_type[doc_type.value] = len(docs)
        
        return {
            "report_date": datetime.now().isoformat(),
            "organization_id": self.organization_id,
            "total_documents": len(documents),
            "total_storage_mb": sum(d.file_size_bytes for d in documents) / (1024 * 1024),
            "by_status": by_status,
            "by_document_type": by_type,
            "on_legal_hold": len(self.get_documents_on_legal_hold()),
            "expiring_90_days": len(self.get_expiring_documents(90)),
            "pending_deletion": len([d for d in documents if d.status == RetentionStatus.PENDING_DELETION]),
            "policies": [p.to_dict() for p in self._policies.values()],
        }
    
    def get_summary(self) -> Dict[str, Any]:
        """Get retention summary."""
        documents = list(self._documents.values())
        
        return {
            "total_documents": len(documents),
            "active": len([d for d in documents if d.status == RetentionStatus.ACTIVE]),
            "archived": len([d for d in documents if d.status == RetentionStatus.ARCHIVED]),
            "legal_hold": len(self.get_documents_on_legal_hold()),
            "expiring_soon": len(self.get_expiring_documents(90)),
            "ready_for_deletion": len(self.get_documents_for_deletion()),
            "policies_count": len(self._policies),
        }


# Singleton instance cache
_instances: Dict[str, DocumentRetentionService] = {}


def get_document_retention_service(organization_id: str = "default") -> DocumentRetentionService:
    """Get or create document retention service for organization."""
    if organization_id not in _instances:
        _instances[organization_id] = DocumentRetentionService(organization_id)
    return _instances[organization_id]
