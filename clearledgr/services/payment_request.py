"""
Payment Request Service

Handles payment requests from multiple sources:
- Email requests ("Please pay $500 to John")
- Slack requests (/clearledgr pay @vendor $1000)
- Internal requests (employee reimbursements)
- Manual requests via UI

Different from invoices - these are ad-hoc payment requests without formal invoicing.
"""

import re
import uuid
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class RequestSource(Enum):
    """Source of payment request."""
    EMAIL = "email"
    SLACK = "slack"
    UI = "ui"
    API = "api"


class RequestStatus(Enum):
    """Status of payment request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"
    CANCELLED = "cancelled"


class RequestType(Enum):
    """Type of payment request."""
    VENDOR_PAYMENT = "vendor_payment"
    REIMBURSEMENT = "reimbursement"
    CONTRACTOR = "contractor"
    REFUND = "refund"
    ADVANCE = "advance"
    OTHER = "other"


@dataclass
class PaymentRequest:
    """A payment request from any source."""
    request_id: str
    source: RequestSource
    source_id: str  # email_id, slack_ts, etc.
    
    # Request details
    requester_name: str
    requester_email: Optional[str]
    request_type: RequestType
    
    # Payment details
    payee_name: str
    payee_email: Optional[str] = None
    amount: float = 0.0
    currency: str = "USD"
    description: str = ""
    
    # Categorization
    gl_code: Optional[str] = None
    cost_center: Optional[str] = None
    
    # Status
    status: RequestStatus = RequestStatus.PENDING
    
    # Approval
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    
    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    # Organization
    organization_id: str = "default"
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "source": self.source.value,
            "source_id": self.source_id,
            "requester_name": self.requester_name,
            "requester_email": self.requester_email,
            "request_type": self.request_type.value,
            "payee_name": self.payee_name,
            "payee_email": self.payee_email,
            "amount": self.amount,
            "currency": self.currency,
            "description": self.description,
            "gl_code": self.gl_code,
            "cost_center": self.cost_center,
            "status": self.status.value,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "organization_id": self.organization_id,
            "metadata": self.metadata,
        }


class PaymentRequestService:
    """
    Service for managing payment requests from all sources.
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self._requests: Dict[str, PaymentRequest] = {}
    
    # =========================================================================
    # CREATE REQUESTS
    # =========================================================================
    
    def create_from_email(
        self,
        email_id: str,
        sender_email: str,
        sender_name: str,
        subject: str,
        body: str,
        extracted_data: Optional[Dict] = None,
    ) -> PaymentRequest:
        """
        Create payment request from an email.
        
        Parses the email content to extract payment details.
        """
        # Extract payment details from email
        amount = 0.0
        payee = "Unknown"
        description = subject
        request_type = RequestType.OTHER
        
        if extracted_data:
            amount = extracted_data.get("amount", 0.0)
            payee = extracted_data.get("payee", sender_name)
            description = extracted_data.get("description", subject)
        else:
            # Parse from body
            amount = self._extract_amount(body) or self._extract_amount(subject)
            payee = self._extract_payee(body) or sender_name
            request_type = self._detect_request_type(subject, body)
        
        request = PaymentRequest(
            request_id=f"REQ-{uuid.uuid4().hex[:8].upper()}",
            source=RequestSource.EMAIL,
            source_id=email_id,
            requester_name=sender_name,
            requester_email=sender_email,
            request_type=request_type,
            payee_name=payee,
            amount=amount,
            description=description,
            organization_id=self.organization_id,
            metadata={
                "subject": subject,
                "body_preview": body[:500],
            }
        )
        
        self._requests[request.request_id] = request
        logger.info(f"Created payment request {request.request_id} from email: {subject}")
        
        return request
    
    def create_from_slack(
        self,
        channel_id: str,
        user_id: str,
        user_name: str,
        message_ts: str,
        text: str,
        parsed_command: Optional[Dict] = None,
    ) -> PaymentRequest:
        """
        Create payment request from Slack message or command.
        
        Supports formats like:
        - /clearledgr pay @john $500 for consulting
        - /pay 1000 to Acme Corp for services
        - @clearledgr please pay $250 to vendor
        """
        # Parse the slack message
        if parsed_command:
            amount = parsed_command.get("amount", 0.0)
            payee = parsed_command.get("payee", "Unknown")
            description = parsed_command.get("description", text)
            request_type = RequestType(parsed_command.get("type", "other"))
        else:
            amount = self._extract_amount(text)
            payee = self._extract_slack_payee(text)
            description = self._clean_slack_text(text)
            request_type = self._detect_request_type(text, "")
        
        request = PaymentRequest(
            request_id=f"REQ-{uuid.uuid4().hex[:8].upper()}",
            source=RequestSource.SLACK,
            source_id=f"{channel_id}:{message_ts}",
            requester_name=user_name,
            requester_email=None,  # Will be filled from Slack profile
            request_type=request_type,
            payee_name=payee,
            amount=amount,
            description=description,
            organization_id=self.organization_id,
            metadata={
                "channel_id": channel_id,
                "user_id": user_id,
                "message_ts": message_ts,
                "original_text": text,
            }
        )
        
        self._requests[request.request_id] = request
        logger.info(f"Created payment request {request.request_id} from Slack: {description[:50]}")
        
        return request
    
    def create_from_ui(
        self,
        user_email: str,
        user_name: str,
        payee_name: str,
        amount: float,
        description: str,
        request_type: str = "other",
        gl_code: Optional[str] = None,
        payee_email: Optional[str] = None,
    ) -> PaymentRequest:
        """
        Create payment request from UI form.
        """
        request = PaymentRequest(
            request_id=f"REQ-{uuid.uuid4().hex[:8].upper()}",
            source=RequestSource.UI,
            source_id=f"ui-{datetime.now().timestamp()}",
            requester_name=user_name,
            requester_email=user_email,
            request_type=RequestType(request_type),
            payee_name=payee_name,
            payee_email=payee_email,
            amount=amount,
            currency="USD",
            description=description,
            gl_code=gl_code,
            organization_id=self.organization_id,
        )
        
        self._requests[request.request_id] = request
        logger.info(f"Created payment request {request.request_id} from UI")
        
        return request
    
    # =========================================================================
    # MANAGE REQUESTS
    # =========================================================================
    
    def get_request(self, request_id: str) -> Optional[PaymentRequest]:
        """Get a payment request by ID."""
        return self._requests.get(request_id)
    
    def get_pending_requests(self) -> List[PaymentRequest]:
        """Get all pending payment requests."""
        return [r for r in self._requests.values() if r.status == RequestStatus.PENDING]
    
    def get_requests_by_source(self, source: RequestSource) -> List[PaymentRequest]:
        """Get requests from a specific source."""
        return [r for r in self._requests.values() if r.source == source]
    
    def get_requests_by_requester(self, email: str) -> List[PaymentRequest]:
        """Get requests from a specific requester."""
        return [r for r in self._requests.values() if r.requester_email == email]
    
    def approve_request(
        self,
        request_id: str,
        approved_by: str,
        gl_code: Optional[str] = None,
    ) -> PaymentRequest:
        """
        Approve a payment request.
        
        This doesn't execute the payment - just marks it approved.
        Use PaymentExecutionService to actually pay.
        """
        request = self._requests.get(request_id)
        if not request:
            raise ValueError(f"Request {request_id} not found")
        
        if request.status != RequestStatus.PENDING:
            raise ValueError(f"Request {request_id} is not pending")
        
        request.status = RequestStatus.APPROVED
        request.approved_by = approved_by
        request.approved_at = datetime.now()
        request.updated_at = datetime.now()
        
        if gl_code:
            request.gl_code = gl_code
        
        logger.info(f"Payment request {request_id} approved by {approved_by}")
        
        return request
    
    def reject_request(
        self,
        request_id: str,
        rejected_by: str,
        reason: str,
    ) -> PaymentRequest:
        """Reject a payment request."""
        request = self._requests.get(request_id)
        if not request:
            raise ValueError(f"Request {request_id} not found")
        
        request.status = RequestStatus.REJECTED
        request.rejection_reason = reason
        request.updated_at = datetime.now()
        request.metadata["rejected_by"] = rejected_by
        
        logger.info(f"Payment request {request_id} rejected: {reason}")
        
        return request
    
    def mark_paid(self, request_id: str, payment_id: str) -> PaymentRequest:
        """Mark request as paid after payment execution."""
        request = self._requests.get(request_id)
        if not request:
            raise ValueError(f"Request {request_id} not found")
        
        request.status = RequestStatus.PAID
        request.updated_at = datetime.now()
        request.metadata["payment_id"] = payment_id
        
        return request
    
    # =========================================================================
    # PARSING HELPERS
    # =========================================================================
    
    def _extract_amount(self, text: str) -> float:
        """Extract amount from text."""
        # Match patterns like: $500, $1,500.00, 500 USD, €500
        patterns = [
            r'\$[\d,]+(?:\.\d{2})?',  # $500, $1,500.00
            r'[\d,]+(?:\.\d{2})?\s*(?:USD|EUR|GBP)',  # 500 USD
            r'€[\d,]+(?:\.\d{2})?',  # €500
            r'£[\d,]+(?:\.\d{2})?',  # £500
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                # Clean and parse
                amount_str = re.sub(r'[^\d.]', '', match.group())
                try:
                    return float(amount_str)
                except ValueError:
                    continue
        
        return 0.0
    
    def _extract_payee(self, text: str) -> str:
        """Extract payee name from text."""
        # Common patterns
        patterns = [
            r'pay\s+(?:to\s+)?([A-Z][a-zA-Z\s]+?)(?:\s+for|\s+\$|\s+\d|$)',
            r'payment\s+(?:to|for)\s+([A-Z][a-zA-Z\s]+?)(?:\s+for|\s+\$|\s+\d|$)',
            r'reimburse\s+([A-Z][a-zA-Z\s]+?)(?:\s+for|\s+\$|\s+\d|$)',
            r'to\s+([A-Z][a-zA-Z\s]+?)\s+for',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        return "Unknown"
    
    def _extract_slack_payee(self, text: str) -> str:
        """Extract payee from Slack message (handles @mentions)."""
        # Match @mentions or plain text
        mention_match = re.search(r'<@([A-Z0-9]+)>', text)
        if mention_match:
            return f"@{mention_match.group(1)}"  # Will be resolved later
        
        return self._extract_payee(text)
    
    def _clean_slack_text(self, text: str) -> str:
        """Clean Slack message text."""
        # Remove Slack formatting
        text = re.sub(r'<@[A-Z0-9]+>', '', text)  # Remove mentions
        text = re.sub(r'<#[A-Z0-9]+\|([^>]+)>', r'\1', text)  # Channel links
        text = re.sub(r'<([^|>]+)\|([^>]+)>', r'\2', text)  # URL links
        text = re.sub(r'<([^>]+)>', r'\1', text)  # Plain URLs
        return text.strip()
    
    def _detect_request_type(self, subject: str, body: str) -> RequestType:
        """Detect the type of payment request."""
        text = f"{subject} {body}".lower()
        
        if any(kw in text for kw in ["reimburse", "reimbursement", "expense", "out of pocket"]):
            return RequestType.REIMBURSEMENT
        
        if any(kw in text for kw in ["contractor", "freelancer", "consulting", "consultant"]):
            return RequestType.CONTRACTOR
        
        if any(kw in text for kw in ["refund", "credit", "return"]):
            return RequestType.REFUND
        
        if any(kw in text for kw in ["advance", "prepay", "deposit"]):
            return RequestType.ADVANCE
        
        if any(kw in text for kw in ["vendor", "supplier", "invoice"]):
            return RequestType.VENDOR_PAYMENT
        
        return RequestType.OTHER
    
    # =========================================================================
    # STATISTICS
    # =========================================================================
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics."""
        requests = list(self._requests.values())
        
        pending = [r for r in requests if r.status == RequestStatus.PENDING]
        approved = [r for r in requests if r.status == RequestStatus.APPROVED]
        paid = [r for r in requests if r.status == RequestStatus.PAID]
        
        return {
            "total_requests": len(requests),
            "pending": len(pending),
            "pending_amount": sum(r.amount for r in pending),
            "approved": len(approved),
            "approved_amount": sum(r.amount for r in approved),
            "paid": len(paid),
            "paid_amount": sum(r.amount for r in paid),
            "by_source": {
                "email": len([r for r in requests if r.source == RequestSource.EMAIL]),
                "slack": len([r for r in requests if r.source == RequestSource.SLACK]),
                "ui": len([r for r in requests if r.source == RequestSource.UI]),
            },
            "by_type": {
                t.value: len([r for r in requests if r.request_type == t])
                for t in RequestType
            }
        }


# Singleton instances per organization
_instances: Dict[str, PaymentRequestService] = {}


def get_payment_request_service(organization_id: str = "default") -> PaymentRequestService:
    """Get or create PaymentRequestService instance."""
    if organization_id not in _instances:
        _instances[organization_id] = PaymentRequestService(organization_id)
    return _instances[organization_id]
