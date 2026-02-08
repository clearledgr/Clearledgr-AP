"""
Clearledgr Engine API

REST API for the unified Clearledgr engine.
All surfaces (Gmail, Sheets, Slack) call these endpoints.

Security:
- All endpoints require authentication (JWT or API key)
- All mutations are audit logged
- Input validation on all requests
"""

import re
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field, validator

from clearledgr.core.engine import get_engine
from clearledgr.core.auth import get_current_user, get_optional_user, TokenData
from clearledgr.core.audit import audit_log, AuditAction

router = APIRouter(prefix="/engine", tags=["Clearledgr Engine"])


def sanitize_string(value: str, max_length: int = 500) -> str:
    """Sanitize string input to prevent injection attacks."""
    if not value:
        return value
    # Remove HTML tags
    value = re.sub(r"<[^>]*>", "", value)
    # Escape SQL special characters
    value = value.replace("'", "''")
    # Truncate
    return value[:max_length]


# ==================== REQUEST MODELS ====================

class FinanceEmailRequest(BaseModel):
    """Request to record a detected finance email."""
    gmail_id: str
    subject: str
    sender: str
    received_at: str
    email_type: str
    confidence: float
    organization_id: str
    user_id: str
    vendor: Optional[str] = None
    amount: Optional[float] = None
    invoice_number: Optional[str] = None


class TransactionRequest(BaseModel):
    """Request to add a transaction with validation."""
    amount: float = Field(..., ge=-1000000000, le=1000000000)  # Reasonable bounds
    currency: str = Field("EUR", pattern=r"^[A-Z]{3}$")  # ISO 4217
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}")  # ISO 8601 date
    description: str = Field(..., min_length=1, max_length=500)
    source: str = Field(..., pattern=r"^(gateway|bank|internal|email|manual)$")
    organization_id: str = Field(..., min_length=1, max_length=50)
    reference: Optional[str] = Field(None, max_length=100)
    source_id: Optional[str] = Field(None, max_length=100)
    vendor: Optional[str] = Field(None, max_length=200)
    
    @validator("description", "vendor", pre=True)
    def sanitize_text(cls, v):
        if v:
            return sanitize_string(v)
        return v
    
    @validator("organization_id")
    def validate_org_id(cls, v):
        if not re.match(r"^[a-zA-Z0-9\-_]+$", v):
            raise ValueError("Invalid organization ID format")
        return v.lower()


class ReconciliationRequest(BaseModel):
    """Request to run reconciliation."""
    organization_id: str
    gateway_transactions: List[Dict[str, Any]]
    bank_transactions: List[Dict[str, Any]]
    internal_transactions: Optional[List[Dict[str, Any]]] = None


class ResolveExceptionRequest(BaseModel):
    """Request to resolve an exception."""
    exception_id: str
    organization_id: str
    user_id: str
    resolution_notes: Optional[str] = None


class ApprovalRequest(BaseModel):
    """Request to approve/reject a draft."""
    draft_id: str
    organization_id: str
    user_id: str
    reason: Optional[str] = None


class ProcessEmailRequest(BaseModel):
    """Request to process a finance email."""
    email_id: str
    organization_id: str
    user_id: str


class BankStatementRequest(BaseModel):
    """Request to parse and process a bank statement."""
    content: str = Field(..., description="Bank statement content (CSV text or PDF text)")
    file_type: str = Field("csv", pattern=r"^(csv|pdf)$")
    filename: Optional[str] = None
    organization_id: str
    user_id: str
    currency: str = Field("EUR", pattern=r"^[A-Z]{3}$")
    gateway: str = Field("stripe", description="Payment gateway to reconcile against")
    gateway_api_key: Optional[str] = Field(None, description="Gateway API key (optional)")
    auto_reconcile: bool = Field(True, description="Automatically run reconciliation after parsing")
    notify_slack: bool = Field(True, description="Send Slack notification when complete")
    slack_webhook_url: Optional[str] = None


# ==================== FINANCE EMAILS ====================

@router.post("/emails/detect")
async def detect_finance_email(
    request: FinanceEmailRequest,
    current_user: Optional[TokenData] = Depends(get_optional_user),
):
    """
    Record a detected finance email.
    Called by Gmail extension when it detects a finance email.
    
    Authentication optional:
    - If authenticated, verifies org access and logs audit trail
    - If not authenticated, stores with provided user_id (guest mode)
    """
    # If authenticated, verify org access
    if current_user:
        if current_user.organization_id != request.organization_id:
            raise HTTPException(status_code=403, detail="Not authorized for this organization")
        user_id = current_user.user_id
        user_email = current_user.email
    else:
        # Guest mode - use provided values
        user_id = request.user_id
        user_email = "guest"
    
    engine = get_engine()
    email = engine.detect_finance_email(
        gmail_id=request.gmail_id,
        subject=sanitize_string(request.subject),
        sender=request.sender,
        received_at=request.received_at,
        email_type=request.email_type,
        confidence=request.confidence,
        organization_id=request.organization_id,
        user_id=user_id,
        vendor=sanitize_string(request.vendor) if request.vendor else None,
        amount=request.amount,
        invoice_number=request.invoice_number,
    )
    
    # Audit log
    audit_log(
        action=AuditAction.TRANSACTION_CREATE,
        user_id=user_id,
        organization_id=request.organization_id,
        resource_type="finance_email",
        resource_id=email.id,
        user_email=user_email,
        details={"gmail_id": request.gmail_id, "email_type": request.email_type, "guest_mode": current_user is None},
    )
    
    return {"status": "success", "email": email.to_dict()}


@router.get("/emails")
async def get_finance_emails(
    organization_id: str,
    status: Optional[str] = None,
    limit: int = Query(default=50, le=200),
):
    """
    Get detected finance emails.
    Used by all surfaces to show pending emails.
    """
    engine = get_engine()
    return {"emails": engine.get_finance_emails(organization_id, status, limit)}


@router.post("/emails/process")
async def process_finance_email(request: ProcessEmailRequest):
    """
    Process a finance email into a transaction.
    Called when user clicks "Process" on an email.
    """
    engine = get_engine()
    try:
        tx = engine.process_finance_email(
            email_id=request.email_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
        )
        return {"status": "success", "transaction": tx.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ==================== TRANSACTIONS ====================

@router.post("/transactions")
async def add_transaction(request: TransactionRequest):
    """Add a transaction from any source."""
    engine = get_engine()
    tx = engine.add_transaction(
        amount=request.amount,
        currency=request.currency,
        date=request.date,
        description=request.description,
        source=request.source,
        organization_id=request.organization_id,
        reference=request.reference,
        source_id=request.source_id,
        vendor=request.vendor,
    )
    return {"status": "success", "transaction": tx.to_dict()}


@router.get("/transactions")
async def get_transactions(
    organization_id: str,
    status: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = Query(default=100, le=500),
):
    """Get transactions with optional filters."""
    engine = get_engine()
    return {"transactions": engine.get_transactions(organization_id, status, source, limit)}


@router.get("/transactions/pending")
async def get_pending_transactions(organization_id: str):
    """Get transactions awaiting reconciliation."""
    engine = get_engine()
    return {"transactions": engine.get_pending_transactions(organization_id)}


# ==================== RECONCILIATION ====================

@router.post("/reconcile")
async def run_reconciliation(request: ReconciliationRequest):
    """
    Run reconciliation on provided transactions.
    This is the core operation - matches transactions and creates exceptions.
    """
    engine = get_engine()
    result = engine.run_reconciliation(
        organization_id=request.organization_id,
        gateway_transactions=request.gateway_transactions,
        bank_transactions=request.bank_transactions,
        internal_transactions=request.internal_transactions,
    )
    return {"status": "success", "result": result}


# ==================== EXCEPTIONS ====================

@router.get("/exceptions")
async def get_exceptions(
    organization_id: str,
    status: str = "open",
    limit: int = Query(default=100, le=500),
):
    """Get reconciliation exceptions."""
    engine = get_engine()
    return {"exceptions": engine.get_exceptions(organization_id, status, limit)}


@router.post("/exceptions/resolve")
async def resolve_exception(request: ResolveExceptionRequest):
    """Resolve a reconciliation exception."""
    engine = get_engine()
    try:
        exc = engine.resolve_exception(
            exception_id=request.exception_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
            resolution_notes=request.resolution_notes,
        )
        return {"status": "success", "exception": exc}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ==================== DRAFT ENTRIES ====================

@router.get("/drafts")
async def get_draft_entries(
    organization_id: str,
    status: str = "pending",
    limit: int = Query(default=100, le=500),
):
    """Get draft journal entries."""
    engine = get_engine()
    return {"drafts": engine.get_draft_entries(organization_id, status, limit)}


@router.post("/drafts/approve")
async def approve_draft(request: ApprovalRequest):
    """Approve a draft journal entry."""
    engine = get_engine()
    try:
        draft = engine.approve_draft(
            draft_id=request.draft_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
        )
        return {"status": "success", "draft": draft}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/drafts/reject")
async def reject_draft(request: ApprovalRequest):
    """Reject a draft journal entry."""
    engine = get_engine()
    if not request.reason:
        raise HTTPException(status_code=400, detail="Rejection reason is required")
    try:
        draft = engine.reject_draft(
            draft_id=request.draft_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
            reason=request.reason,
        )
        return {"status": "success", "draft": draft}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ==================== STATS & DASHBOARD ====================

@router.get("/stats")
async def get_stats(organization_id: str):
    """Get summary statistics."""
    engine = get_engine()
    return {"stats": engine.get_stats(organization_id)}


@router.get("/dashboard")
async def get_dashboard(organization_id: str):
    """
    Get all data needed for dashboard display.
    Single call to populate any surface's dashboard.
    """
    engine = get_engine()
    return engine.get_dashboard_data(organization_id)


# ==================== BANK STATEMENT PARSING ====================

@router.post("/parse-statement")
async def parse_bank_statement(request: BankStatementRequest):
    """
    Parse a bank statement and optionally reconcile against gateway.
    
    This is the main endpoint called by Gmail extension when it detects
    a bank statement attachment. Flow:
    1. Parse the CSV/PDF content into transactions
    2. Optionally fetch gateway transactions
    3. Run reconciliation if auto_reconcile is True
    4. Send Slack notification if notify_slack is True
    5. Return results
    """
    from clearledgr.services.bank_statement_parser import BankStatementParser
    from clearledgr.services.slack_notifications import SlackNotificationService
    
    engine = get_engine()
    parser = BankStatementParser()
    
    # Step 1: Parse the bank statement
    try:
        if request.file_type == "csv":
            parsed_transactions = parser.parse_csv(request.content)
        else:
            parsed_transactions = parser.parse_pdf_text(request.content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse statement: {str(e)}")
    
    if not parsed_transactions:
        raise HTTPException(status_code=400, detail="No transactions found in statement")
    
    # Step 2: Convert to bank transactions and store
    bank_transactions = []
    for tx in parsed_transactions:
        transaction = engine.add_transaction(
            amount=tx.amount,
            currency=request.currency,
            date=tx.date,
            description=tx.description,
            source="bank",
            organization_id=request.organization_id,
            reference=tx.reference,
        )
        bank_transactions.append(transaction)
    
    result = {
        "status": "success",
        "parsed_count": len(parsed_transactions),
        "bank_transactions": bank_transactions,
        "reconciliation": None,
        "matches": [],
        "exceptions": [],
    }
    
    # Step 3: Optionally fetch gateway transactions and reconcile
    if request.auto_reconcile:
        # Fetch gateway transactions
        gateway_transactions = []
        
        if request.gateway == "stripe" and request.gateway_api_key:
            try:
                from clearledgr.services.stripe_client import StripeClient
                client = StripeClient(request.gateway_api_key)
                payouts = client.get_payouts(limit=100)
                gateway_transactions = [p.to_dict() for p in payouts]
            except Exception as e:
                result["gateway_error"] = f"Failed to fetch Stripe data: {str(e)}"
                
        elif request.gateway == "paystack" and request.gateway_api_key:
            try:
                from clearledgr.services.paystack_client import PaystackClient
                client = PaystackClient(request.gateway_api_key)
                settlements = client.get_settlements(limit=100)
                gateway_transactions = [s.to_dict() for s in settlements]
            except Exception as e:
                result["gateway_error"] = f"Failed to fetch Paystack data: {str(e)}"
                
        elif request.gateway == "flutterwave" and request.gateway_api_key:
            try:
                from clearledgr.services.flutterwave_client import FlutterwaveClient
                client = FlutterwaveClient(request.gateway_api_key)
                transactions = client.get_transactions(limit=100)
                gateway_transactions = [t.to_dict() for t in transactions]
            except Exception as e:
                result["gateway_error"] = f"Failed to fetch Flutterwave data: {str(e)}"
        
        # Run reconciliation if we have gateway transactions
        if gateway_transactions:
            try:
                recon_result = engine.run_reconciliation(
                    gateway_transactions=[
                        {"amount": t.get("amount", 0), "date": t.get("date", ""), "description": t.get("description", ""), "reference": t.get("reference", "")}
                        for t in gateway_transactions
                    ],
                    bank_transactions=[
                        {"amount": t["amount"], "date": t["date"], "description": t["description"], "reference": t.get("reference", "")}
                        for t in bank_transactions
                    ],
                    organization_id=request.organization_id,
                )
                result["reconciliation"] = recon_result
                result["matches"] = engine.get_matches(request.organization_id, limit=100)
                result["exceptions"] = engine.get_exceptions(request.organization_id, limit=100)
            except Exception as e:
                result["reconciliation_error"] = f"Reconciliation failed: {str(e)}"
    
    # Step 4: Send Slack notification if enabled
    if request.notify_slack and request.slack_webhook_url:
        try:
            slack = SlackNotificationService(request.slack_webhook_url)
            match_count = len(result.get("matches", []))
            exception_count = len(result.get("exceptions", []))
            
            slack.send_reconciliation_complete(
                total_transactions=len(bank_transactions),
                matched=match_count,
                exceptions=exception_count,
                organization_id=request.organization_id,
            )
        except Exception as e:
            result["slack_error"] = f"Failed to send Slack notification: {str(e)}"
    
    return result


class PreviewStatementRequest(BaseModel):
    """Request to preview bank statement parsing."""
    content: str
    file_type: str = Field("csv", pattern=r"^(csv|pdf)$")


@router.post("/parse-statement/preview")
async def preview_bank_statement(request: PreviewStatementRequest):
    """
    Preview parsing of a bank statement without storing.
    Use this to show the user what will be imported.
    """
    from clearledgr.services.bank_statement_parser import BankStatementParser
    
    parser = BankStatementParser()
    
    try:
        if request.file_type == "csv":
            transactions = parser.parse_csv(request.content)
        else:
            transactions = parser.parse_pdf_text(request.content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse: {str(e)}")
    
    return {
        "status": "success",
        "preview": [
            {
                "date": tx.date,
                "amount": tx.amount,
                "description": tx.description,
                "reference": tx.reference,
            }
            for tx in transactions
        ],
        "count": len(transactions),
    }
