"""
Payment Request API Endpoints

Handles payment requests from multiple sources:
- Email requests (processed by gmail_webhooks)
- Slack commands
- UI form submissions
- Direct API calls

These are distinct from invoice processing - payment requests are 
ad-hoc asks for payment without formal invoicing.
"""

from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import logging
import json
import hmac
import hashlib
import time

from clearledgr.services.payment_request import (
    get_payment_request_service,
    RequestSource,
    RequestStatus,
    RequestType,
)
from clearledgr.services.slack_notifications import send_payment_request_notification
from clearledgr.services.payment_execution import get_payment_execution, PaymentMethod

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payment-requests", tags=["payment-requests"])


# ============================================================================
# REQUEST MODELS
# ============================================================================

class CreatePaymentRequestUI(BaseModel):
    """Create payment request from UI."""
    user_email: str
    user_name: str
    payee_name: str
    payee_email: Optional[str] = None
    amount: float
    currency: str = "USD"
    description: str
    request_type: str = "other"  # vendor_payment, reimbursement, contractor, refund, advance, other
    gl_code: Optional[str] = None
    organization_id: str = "default"


class ApproveRequestBody(BaseModel):
    """Approve a payment request."""
    approved_by: str
    gl_code: Optional[str] = None
    organization_id: str = "default"


class RejectRequestBody(BaseModel):
    """Reject a payment request."""
    rejected_by: str
    reason: str
    organization_id: str = "default"


class ExecutePaymentBody(BaseModel):
    """Execute payment for approved request."""
    payment_method: str = "ach"  # ach, wire, check
    scheduled_date: Optional[str] = None
    organization_id: str = "default"


class MarkPaidBody(BaseModel):
    """Mark a payment request as paid (external payment)."""
    marked_by: str
    organization_id: str = "default"
    paid_at: Optional[str] = None
    payment_reference: Optional[str] = None  # External payment reference


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.post("/create")
async def create_payment_request(request: CreatePaymentRequestUI):
    """
    Create a new payment request from the UI.
    
    This is for manual payment requests - employee reimbursements,
    contractor payments, vendor payments without invoices, etc.
    """
    service = get_payment_request_service(request.organization_id)
    
    payment_request = service.create_from_ui(
        user_email=request.user_email,
        user_name=request.user_name,
        payee_name=request.payee_name,
        payee_email=request.payee_email,
        amount=request.amount,
        description=request.description,
        request_type=request.request_type,
        gl_code=request.gl_code,
    )
    
    # Send Slack notification
    try:
        await send_payment_request_notification(payment_request)
    except Exception as e:
        logger.warning(f"Failed to send Slack notification: {e}")
    
    return payment_request.to_dict()


@router.get("/pending")
async def get_pending_requests(organization_id: str = "default"):
    """Get all pending payment requests."""
    service = get_payment_request_service(organization_id)
    requests = service.get_pending_requests()
    return [r.to_dict() for r in requests]


@router.get("/all")
async def get_all_requests(
    organization_id: str = "default",
    source: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
):
    """Get all payment requests with optional filters."""
    service = get_payment_request_service(organization_id)
    
    # Get all requests
    requests = list(service._requests.values())
    
    # Filter by source
    if source:
        requests = [r for r in requests if r.source.value == source]
    
    # Filter by status
    if status:
        requests = [r for r in requests if r.status.value == status]
    
    # Sort by created_at descending
    requests.sort(key=lambda r: r.created_at, reverse=True)
    
    return [r.to_dict() for r in requests[:limit]]


@router.get("/{request_id}")
async def get_request(request_id: str, organization_id: str = "default"):
    """Get a specific payment request."""
    service = get_payment_request_service(organization_id)
    
    request = service.get_request(request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Payment request not found")
    
    return request.to_dict()


@router.post("/{request_id}/approve")
async def approve_request(request_id: str, body: ApproveRequestBody):
    """Approve a payment request."""
    service = get_payment_request_service(body.organization_id)
    
    try:
        request = service.approve_request(
            request_id=request_id,
            approved_by=body.approved_by,
            gl_code=body.gl_code,
        )
        return request.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{request_id}/reject")
async def reject_request(request_id: str, body: RejectRequestBody):
    """Reject a payment request."""
    service = get_payment_request_service(body.organization_id)
    
    try:
        request = service.reject_request(
            request_id=request_id,
            rejected_by=body.rejected_by,
            reason=body.reason,
        )
        return request.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{request_id}/execute")
async def execute_payment(request_id: str, body: ExecutePaymentBody):
    """
    Execute payment for an approved request.
    
    This creates an actual payment through the payment execution service.
    """
    request_service = get_payment_request_service(body.organization_id)
    payment_service = get_payment_execution(body.organization_id)
    
    # Get the request
    request = request_service.get_request(request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Payment request not found")
    
    if request.status != RequestStatus.APPROVED:
        raise HTTPException(status_code=400, detail="Request must be approved before payment")
    
    # Create payment
    payment = payment_service.create_payment(
        invoice_id=request.request_id,  # Use request_id as reference
        vendor_id=request.payee_name.lower().replace(" ", "-"),
        vendor_name=request.payee_name,
        amount=request.amount,
        currency=request.currency,
        method=PaymentMethod(body.payment_method),
        scheduled_date=body.scheduled_date,
    )
    
    # Mark request as paid
    request_service.mark_paid(request_id, payment.payment_id)
    
    return {
        "status": "payment_created",
        "request_id": request_id,
        "payment_id": payment.payment_id,
        "payment": payment.to_dict(),
    }


@router.post("/{request_id}/mark-paid")
async def mark_request_paid(request_id: str, body: MarkPaidBody):
    """
    Manually mark a payment request as paid.
    
    Use this when payment was made outside the system (e.g., manual bank transfer,
    check, or payment via another platform).
    """
    service = get_payment_request_service(body.organization_id)
    
    # Get the request
    request = service.get_request(request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Payment request not found")
    
    # Only allow marking approved or processing requests as paid
    if request.status not in [RequestStatus.APPROVED, RequestStatus.PENDING]:
        if request.status == RequestStatus.PAID:
            raise HTTPException(status_code=400, detail="Request is already marked as paid")
        raise HTTPException(status_code=400, detail=f"Cannot mark {request.status.value} request as paid")
    
    # Generate a reference if not provided
    payment_ref = body.payment_reference or f"manual-{request_id[:8]}"
    
    # Mark as paid
    request = service.mark_paid(request_id, payment_ref)
    
    # Add metadata about manual payment
    request.metadata["manually_marked_paid"] = True
    request.metadata["marked_paid_by"] = body.marked_by
    request.metadata["paid_at"] = body.paid_at or datetime.now().isoformat()
    
    logger.info(f"Payment request {request_id} manually marked as paid by {body.marked_by}")
    
    return {
        "status": "marked_paid",
        "request_id": request_id,
        "marked_by": body.marked_by,
        "paid_at": request.metadata.get("paid_at"),
    }


@router.get("/summary/stats")
async def get_summary(organization_id: str = "default"):
    """Get payment request statistics."""
    service = get_payment_request_service(organization_id)
    return service.get_summary()


# ============================================================================
# SLACK INTEGRATION
# ============================================================================

SLACK_SIGNING_SECRET = None  # Set from environment

@router.post("/slack/command")
async def handle_slack_command(request: Request):
    """
    Handle Slack slash command for payment requests.
    
    Supports commands like:
    - /clearledgr pay @john $500 for consulting work
    - /clearledgr pay 1000 to Acme Corp for services
    - /pay $250 to @jane reimbursement for supplies
    """
    try:
        body = await request.body()
        form_data = {}
        for item in body.decode().split("&"):
            if "=" in item:
                key, value = item.split("=", 1)
                form_data[key] = value.replace("+", " ").replace("%40", "@")
        
        # Verify Slack signature in production
        # (skipped for development)
        
        command = form_data.get("command", "")
        text = form_data.get("text", "")
        user_id = form_data.get("user_id", "")
        user_name = form_data.get("user_name", "Unknown")
        channel_id = form_data.get("channel_id", "")
        
        logger.info(f"Slack command from {user_name}: {command} {text}")
        
        # Parse the payment request from text
        import re
        
        # Extract amount
        amount_match = re.search(r'\$?([\d,]+(?:\.\d{2})?)', text)
        amount = float(amount_match.group(1).replace(",", "")) if amount_match else 0
        
        # Extract payee (after "to" or @mention)
        payee = "Unknown"
        mention_match = re.search(r'<@([A-Z0-9]+)>', text)
        to_match = re.search(r'to\s+([A-Za-z\s]+?)(?:\s+for|\s+\$|\s*$)', text, re.IGNORECASE)
        
        if mention_match:
            payee = f"@{mention_match.group(1)}"
        elif to_match:
            payee = to_match.group(1).strip()
        
        # Extract description (after "for")
        description = text
        for_match = re.search(r'for\s+(.+)$', text, re.IGNORECASE)
        if for_match:
            description = for_match.group(1).strip()
        
        if amount == 0:
            return {
                "response_type": "ephemeral",
            "text": "Could not parse amount. Try: `/clearledgr pay $500 to John for consulting`"
            }
        
        # Create payment request
        service = get_payment_request_service("default")
        payment_request = service.create_from_slack(
            channel_id=channel_id,
            user_id=user_id,
            user_name=user_name,
            message_ts=str(time.time()),
            text=text,
            parsed_command={
                "amount": amount,
                "payee": payee,
                "description": description,
                "type": "other",
            }
        )
        
        # Send notification
        try:
            await send_payment_request_notification(payment_request)
        except:
            pass
        
        return {
            "response_type": "in_channel",
            "text": f"Payment request created.\n"
                    f"*Request ID:* {payment_request.request_id}\n"
                    f"*Amount:* ${amount:,.2f}\n"
                    f"*To:* {payee}\n"
                    f"*Description:* {description}\n\n"
                    f"Sent to #finance-approvals for approval."
        }
    
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return {
            "response_type": "ephemeral",
            "text": f"Error: {str(e)}"
        }


@router.post("/slack/interactive")
async def handle_slack_interactive(request: Request):
    """
    Handle Slack interactive component callbacks.
    
    This handles button clicks from payment request notifications.
    """
    try:
        body = await request.body()
        
        # Parse form data
        form_data = {}
        for item in body.decode().split("&"):
            if "=" in item:
                key, value = item.split("=", 1)
                form_data[key] = value
        
        payload_str = form_data.get("payload", "")
        if not payload_str:
            raise HTTPException(status_code=400, detail="No payload")
        
        # URL decode and parse JSON
        import urllib.parse
        payload = json.loads(urllib.parse.unquote(payload_str))
        
        action = payload.get("actions", [{}])[0]
        action_id = action.get("action_id", "")
        request_id = action.get("value", "")
        user = payload.get("user", {})
        user_name = user.get("username", "Unknown")
        
        logger.info(f"Slack interactive: {action_id} by {user_name}")
        
        service = get_payment_request_service("default")
        
        if action_id.startswith("approve_payment_request_"):
            try:
                payment_request = service.approve_request(request_id, approved_by=user_name)
                return {
                    "response_type": "in_channel",
                    "replace_original": True,
                    "text": f"Payment request {request_id} approved by @{user_name}\n"
                            f"Amount: ${payment_request.amount:,.2f} to {payment_request.payee_name}"
                }
            except ValueError as e:
                return {"response_type": "ephemeral", "text": f"{str(e)}"}
        
        elif action_id.startswith("reject_payment_request_"):
            # For rejection, we'd normally show a modal for reason
            # For now, use a default reason
            try:
                payment_request = service.reject_request(
                    request_id, 
                    rejected_by=user_name,
                    reason="Rejected via Slack"
                )
                return {
                    "response_type": "in_channel",
                    "replace_original": True,
                    "text": f"Payment request {request_id} rejected by @{user_name}"
                }
            except ValueError as e:
                return {"response_type": "ephemeral", "text": f"{str(e)}"}
        
        elif action_id.startswith("view_payment_request_"):
            payment_request = service.get_request(request_id)
            if payment_request:
                return {
                    "response_type": "ephemeral",
                    "text": (
                        f"*Payment Request Details*\n"
                        f"ID: {payment_request.request_id}\n"
                        f"From: {payment_request.requester_name}\n"
                        f"To: {payment_request.payee_name}\n"
                        f"Amount: ${payment_request.amount:,.2f}\n"
                        f"Type: {payment_request.request_type.value}\n"
                        f"Status: {payment_request.status.value}\n"
                        f"Description: {payment_request.description}"
                    )
                }
            else:
                return {"response_type": "ephemeral", "text": "Request not found"}
        
        return {"response_type": "ephemeral", "text": "Unknown action"}
    
    except Exception as e:
        logger.error(f"Slack interactive error: {e}")
        return {"response_type": "ephemeral", "text": f"Error: {str(e)}"}
