"""Pydantic request/response models for the Gmail extension API.

Extracted from gmail_extension.py to reduce file size and improve
navigability. All models are re-imported by gmail_extension.py so
existing code continues to work unchanged.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ==================== REQUEST MODELS ====================


class EmailTriageRequest(BaseModel):
    """Request to triage a single email."""
    email_id: str
    subject: Optional[str] = None
    sender: Optional[str] = None
    snippet: Optional[str] = None
    body: Optional[str] = None  # Full email body for better extraction
    attachments: Optional[List[Dict[str, Any]]] = None  # With content_base64 for Claude Vision
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class EmailProcessRequest(BaseModel):
    """Request to fully process an email (triage + match + action)."""
    email_id: str
    subject: Optional[str] = None
    sender: Optional[str] = None
    snippet: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    auto_approve: bool = False
    approval_threshold: float = 1000.0


class BulkScanRequest(BaseModel):
    """Request to scan multiple emails."""
    email_ids: List[str]
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class HistoricalInvoiceRepairRequest(BaseModel):
    """Replay historical invoice emails into the live AP record store."""
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    gmail_ids: List[str] = Field(default_factory=list)
    limit: int = 100
    before_created_at: Optional[str] = None
    only_unrepaired: bool = True


class GmailLabelCleanupRequest(BaseModel):
    """Migrate and delete obsolete Gmail labels from a Clearledgr mailbox."""
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    dry_run: bool = False
    max_messages_per_label: int = Field(default=1000, ge=1, le=5000)


class PostToErpRequest(BaseModel):
    """Request to execute post-to-ERP for an already-approved invoice.

    The `override` flag allows forcing a post despite a low-confidence
    extraction — justified cases only, captured in the override audit
    trail, not a general-purpose bypass.
    """
    email_id: str
    ap_item_id: Optional[str] = None
    extraction: Dict[str, Any]
    bank_match: Optional[Dict[str, Any]] = None
    erp_match: Optional[Dict[str, Any]] = None
    override: bool = False  # Force post despite low confidence
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    idempotency_key: Optional[str] = None


class VerifyConfidenceRequest(BaseModel):
    """Request to verify match confidence (HITL check)."""
    email_id: str
    extraction: Dict[str, Any]
    bank_match: Optional[Dict[str, Any]] = None
    erp_match: Optional[Dict[str, Any]] = None
    organization_id: Optional[str] = None


class EscalateRequest(BaseModel):
    """Request to escalate to manager via Slack."""
    email_id: str
    vendor: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "USD"
    confidence: Optional[float] = None
    mismatches: List[Dict[str, Any]] = []
    message: Optional[str] = None
    channel: str = "#finance-escalations"
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class MatchBankRequest(BaseModel):
    """Request to match against bank feed."""
    extraction: Dict[str, Any]
    organization_id: Optional[str] = None


class MatchERPRequest(BaseModel):
    """Request to match against ERP."""
    extraction: Dict[str, Any]
    organization_id: Optional[str] = None


class RegisterGmailTokenRequest(BaseModel):
    """Register OAuth token acquired by the Gmail extension."""
    access_token: str
    expires_in: Optional[int] = 3600
    email: Optional[str] = None
    organization_id: Optional[str] = None


class ExchangeCodeRequest(BaseModel):
    code: str
    redirect_uri: str
    organization_id: Optional[str] = "default"


class SubmitForApprovalRequest(BaseModel):
    """Request to submit invoice for Slack approval with intelligence."""
    email_id: str
    subject: str
    sender: str
    vendor: str
    amount: float
    currency: str = "USD"
    invoice_number: Optional[str] = None
    due_date: Optional[str] = None
    po_number: Optional[str] = None
    confidence: float = 0.0
    field_confidences: Optional[Dict[str, Any]] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    slack_channel: Optional[str] = None
    email_body: Optional[str] = None  # For discount detection
    # Intelligence data (from triage)
    vendor_intelligence: Optional[Dict[str, Any]] = None
    policy_compliance: Optional[Dict[str, Any]] = None
    priority: Optional[Dict[str, Any]] = None
    budget_impact: Optional[List[Dict[str, Any]]] = None
    potential_duplicates: int = 0
    insights: Optional[List[Dict[str, Any]]] = None
    # Agent reasoning + decision payload
    agent_decision: Optional[Dict[str, Any]] = None
    agent_confidence: Optional[float] = None
    reasoning_summary: Optional[str] = None
    reasoning_factors: Optional[List[Dict[str, Any]]] = None
    reasoning_risks: Optional[List[str]] = None
    idempotency_key: Optional[str] = None


class RejectInvoiceRequest(BaseModel):
    """Request to reject an invoice from Gmail sidebar."""
    email_id: str
    ap_item_id: Optional[str] = None
    reason: str
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    idempotency_key: Optional[str] = None


class BudgetDecisionRequest(BaseModel):
    """Budget decision from Gmail/embedded approval surfaces."""
    email_id: str
    ap_item_id: Optional[str] = None
    decision: str  # approve_override | request_budget_adjustment | reject
    justification: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class ApprovalNudgeRequest(BaseModel):
    """Request to nudge pending approvers for an invoice."""
    email_id: str
    ap_item_id: Optional[str] = None
    message: Optional[str] = None
    idempotency_key: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class VendorFollowupRequest(BaseModel):
    """Request to prepare/refresh a vendor follow-up draft for needs_info items."""
    email_id: str
    reason: Optional[str] = None
    force: bool = False
    idempotency_key: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class FinanceSummaryShareRequest(BaseModel):
    """Request to prepare/share a finance-lead exception summary."""
    email_id: str
    ap_item_id: Optional[str] = None
    target: str = "email_draft"  # email_draft | slack_thread | teams_reply
    preview_only: bool = False
    recipient_email: Optional[str] = None
    note: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class RouteLowRiskApprovalRequest(BaseModel):
    """Batch route low-risk validated item into approval surfaces."""
    email_id: str
    ap_item_id: Optional[str] = None
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class RetryRecoverableFailureRequest(BaseModel):
    """Batch retry for recoverable failed_post AP items."""
    email_id: str
    ap_item_id: Optional[str] = None
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class FieldCorrectionRequest(BaseModel):
    """Payload sent by the Gmail extension when an operator edits an extracted field."""
    ap_item_id: str
    field: str  # e.g. "vendor", "amount", "invoice_number", "due_date"
    original_value: Optional[Any] = None
    corrected_value: Any
    actor_id: Optional[str] = None  # email of the operator; falls back to token identity
    feedback: Optional[str] = None  # optional free-text reason
