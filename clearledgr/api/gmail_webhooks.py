"""
Gmail Pub/Sub Webhook Handler

Receives push notifications from Google Cloud Pub/Sub when new emails arrive.
This enables 24/7 autonomous email processing without requiring the browser to be open.
"""

import base64
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel

from clearledgr.services.gmail_api import (
    GmailAPIClient,
    GmailWatchService,
    token_store,
    exchange_code_for_tokens,
    generate_auth_url,
)
from clearledgr.core.engine import get_engine
from clearledgr.core.database import get_db
from clearledgr.services.ai_enhanced import EnhancedAIService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gmail", tags=["gmail"])


class PubSubMessage(BaseModel):
    """Google Cloud Pub/Sub message format."""
    message: Dict[str, Any]
    subscription: str


class GmailAuthRequest(BaseModel):
    """Request to initiate Gmail OAuth."""
    user_id: str
    redirect_url: Optional[str] = None


class GmailCallbackRequest(BaseModel):
    """OAuth callback data."""
    code: str
    state: Optional[str] = None


# ============================================================================
# WEBHOOK ENDPOINT - Receives Pub/Sub notifications
# ============================================================================

@router.post("/push")
async def gmail_push_notification(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Receive Gmail push notifications from Google Cloud Pub/Sub.
    
    This endpoint is called by Google whenever a new email arrives in a watched inbox.
    Processing happens in the background to respond quickly to Google.
    """
    try:
        body = await request.json()
        logger.info(f"Received Gmail push notification")
        
        # Decode the Pub/Sub message
        message_data = body.get("message", {}).get("data", "")
        if message_data:
            decoded = base64.urlsafe_b64decode(message_data).decode("utf-8")
            notification = json.loads(decoded)
            
            email_address = notification.get("emailAddress")
            history_id = notification.get("historyId")
            
            logger.info(f"Gmail notification for {email_address}, historyId: {history_id}")
            
            # Process in background
            background_tasks.add_task(
                process_gmail_notification,
                email_address,
                history_id,
            )
        
        # Always return 200 to acknowledge receipt
        return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"Error processing Gmail push: {e}")
        # Still return 200 to prevent retries
        return {"status": "error", "message": str(e)}


async def process_gmail_notification(email_address: str, history_id: str):
    """
    Process a Gmail notification in the background.
    
    This is where the autonomous magic happens:
    1. Fetch new emails since last history ID
    2. Classify each email (finance vs marketing)
    3. Parse attachments (bank statements)
    4. Run reconciliation
    5. Store results for user to review
    """
    try:
        # Find the user's token by email
        token = token_store.get_by_email(email_address)
        if not token:
            logger.warning(f"No token found for {email_address}")
            return
        
        # Initialize Gmail client
        client = GmailAPIClient(token.user_id)
        if not await client.ensure_authenticated():
            logger.error(f"Failed to authenticate for {email_address}")
            return
        
        # Track autopilot state
        db = get_db()
        db.save_gmail_autopilot_state(
            user_id=token.user_id,
            email=token.email,
            last_history_id=history_id,
            last_scan_at=datetime.utcnow().isoformat(),
            last_error=None,
        )

        # Get history since last notification
        # In production, store last_history_id per user
        history = await client.get_history(history_id)
        
        if history.get("needsFullSync"):
            logger.info(f"Full sync needed for {email_address}")
            # For now, just get recent messages
            messages_response = await client.list_messages(
                query="newer_than:1d",
                max_results=50,
            )
            message_ids = [m["id"] for m in messages_response.get("messages", [])]
        else:
            # Extract new message IDs from history
            message_ids = []
            for record in history.get("history", []):
                for added in record.get("messagesAdded", []):
                    message_ids.append(added["message"]["id"])
        
        if not message_ids:
            logger.info(f"No new messages for {email_address}")
            return
        
        logger.info(f"Processing {len(message_ids)} new messages for {email_address}")
        
        # Get engine and AI service
        engine = get_engine()
        ai_service = EnhancedAIService()
        
        # Process each message
        for message_id in message_ids:
            try:
                await process_single_email(
                    client=client,
                    message_id=message_id,
                    user_id=token.user_id,
                    engine=engine,
                    ai_service=ai_service,
                )
            except Exception as e:
                logger.error(f"Error processing message {message_id}: {e}")
                continue
        
        logger.info(f"Finished processing emails for {email_address}")
    
    except Exception as e:
        logger.error(f"Error in process_gmail_notification: {e}")
        try:
            db = get_db()
            db.save_gmail_autopilot_state(
                user_id=token.user_id if token else "unknown",
                email=email_address,
                last_error=str(e),
            )
        except Exception:
            pass


async def process_single_email(
    client: GmailAPIClient,
    message_id: str,
    user_id: str,
    engine,
    ai_service: EnhancedAIService,
):
    """
    Process a single email autonomously.
    """
    # Fetch the full message
    message = await client.get_message(message_id)
    
    # Skip if already processed (check labels)
    if engine.db.get_finance_email_by_gmail_id(message.id):
        return
    if "CLEARLEDGR_PROCESSED" in message.labels:
        return
    
    # Classify the email for AP workflow
    classification = await classify_email_with_llm(
        subject=message.subject,
        sender=message.sender,
        snippet=message.snippet,
        body=message.body_text[:2000],  # Limit for LLM
        attachments=message.attachments or [],
        ai_service=ai_service,
    )
    
    logger.info(
        "Email '%s' classified as: %s (%.2f)",
        message.subject,
        classification.get("type"),
        classification.get("confidence", 0.0),
    )
    
    # Skip non-AP emails
    if classification.get("type") == "NOISE" or classification.get("confidence", 0) < 0.7:
        logger.info("Skipping non-AP email: %s", message.subject)
        return
    
    category = str(classification.get("type") or "").lower()
    if category not in {"invoice", "payment_request"}:
        logger.info("Skipping non-AP email: %s (%s)", message.subject, category or "unknown")
        return

    # Store as detected finance email
    finance_email = engine.detect_finance_email(
        email_id=message.id,
        subject=message.subject,
        sender=message.sender,
        category=category,
        confidence=classification.get("confidence", 0.0),
        received_at=message.date,
        user_id=user_id,
    )
    
    # Process invoices through the invoice workflow
    if category == "invoice":
        await process_invoice_email(
            client=client,
            message=message,
            user_id=user_id,
            confidence=classification.get("confidence", 0.0),
        )
    
    # Process payment requests (non-invoice payment asks)
    elif category == "payment_request":
        await process_payment_request_email(
            client=client,
            message=message,
            user_id=user_id,
            confidence=classification.get("confidence", 0.0),
        )
    
    # Add processed label
    try:
        # First ensure the label exists
        labels = await client.list_labels()
        clearledgr_label = next(
            (l for l in labels if l["name"] == "Clearledgr/Processed"),
            None
        )
        
        if not clearledgr_label:
            clearledgr_label = await client.create_label("Clearledgr/Processed")
        
        await client.add_label(message.id, [clearledgr_label["id"]])
    except Exception as e:
        logger.warning(f"Could not add label: {e}")


async def classify_email_with_llm(
    subject: str,
    sender: str,
    snippet: str,
    body: str,
    attachments: Optional[list] = None,
    ai_service: EnhancedAIService = None,
) -> Dict[str, Any]:
    """
    Classify an email for AP workflow.
    
    Returns:
        Dict with 'type' (INVOICE | PAYMENT_REQUEST | NOISE) and 'confidence'
    """
    from clearledgr.services.ap_classifier import classify_ap_email

    return classify_ap_email(
        subject=subject or "",
        sender=sender or "",
        snippet=snippet or "",
        body=body or "",
        attachments=attachments or [],
    )


def classify_email_heuristic(subject: str, sender: str, snippet: str) -> Dict[str, Any]:
    """Deprecated: retained for backward compatibility."""
    from clearledgr.services.ap_classifier import classify_ap_email

    return classify_ap_email(subject=subject, sender=sender, snippet=snippet, body="")


async def process_invoice_email(
    client: GmailAPIClient,
    message,
    user_id: str,
    confidence: float,
):
    """
    Process an invoice email through the invoice workflow.
    
    This is the main entry point for invoice processing from Gmail Pub/Sub.
    
    Flow:
    1. Extract invoice data using Claude Vision (for PDFs) or LLM (for text)
    2. Submit to invoice workflow
    3. Workflow handles: auto-approve (high confidence) or route to Slack (low confidence)
    """
    from clearledgr.services.invoice_workflow import InvoiceWorkflowService, InvoiceData
    from clearledgr.workflows.gmail_activities import extract_email_data_activity
    
    logger.info(f"Processing invoice email: {message.subject}")
    
    # Extract data from email + attachments
    attachments_with_content = []
    
    # Fetch attachment content for PDFs/images (for Claude Vision)
    for attachment in message.attachments or []:
        try:
            content_type = (
                attachment.get("mime_type")
                or attachment.get("mimeType")
                or attachment.get("content_type")
                or ""
            ).lower()
            filename = (attachment.get("filename") or attachment.get("name") or "").lower()
            
            # Only fetch PDFs and images for vision extraction
            if (
                "pdf" in content_type
                or filename.endswith(".pdf")
                or "image" in content_type
                or any(filename.endswith(ext) for ext in [".png", ".jpg", ".jpeg"])
                or filename.endswith(".docx")
                or "wordprocessingml" in content_type
            ):
                
                # Fetch the attachment content
                attachment_bytes = await client.get_attachment(
                    message_id=message.id,
                    attachment_id=attachment.get("attachmentId") or attachment.get("id"),
                )
                
                if attachment_bytes:
                    # Convert bytes to base64 for Claude Vision
                    import base64
                    content_base64 = base64.b64encode(attachment_bytes).decode("utf-8")
                    
                    attachments_with_content.append({
                        "filename": attachment.get("filename") or attachment.get("name"),
                        "content_type": content_type,
                        "content_base64": content_base64,
                    })
                    logger.info(f"Fetched attachment for vision: {attachment.get('filename')}")
        except Exception as e:
            logger.warning(f"Failed to fetch attachment {attachment.get('filename')}: {e}")
    
    # Extract invoice data using deterministic parser + LLM fallback
    extraction: Dict[str, Any] = {}
    try:
        extraction = await extract_email_data_activity({
            "subject": message.subject,
            "sender": message.sender,
            "snippet": message.snippet,
            "body": message.body_text or "",
            "attachments": attachments_with_content,
        })
    except Exception as e:
        logger.warning(f"Extraction failed, continuing with sender fallback: {e}")
        extraction = {}

    def _safe_date(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)).date().isoformat()
        except Exception:
            return None
    
    # Combine all text for discount detection
    invoice_text = f"{message.subject}\n{message.snippet}\n{message.body_text or ''}"
    
    # Build invoice data object
    invoice = InvoiceData(
        gmail_id=message.id,
        subject=message.subject,
        sender=message.sender,
        vendor_name=extraction.get("vendor") or _extract_vendor_from_sender(message.sender),
        amount=extraction.get("amount") or extraction.get("total_amount") or 0,
        currency=extraction.get("currency") or "USD",
        invoice_number=extraction.get("invoice_number"),
        due_date=_safe_date(extraction.get("due_date")),
        confidence=extraction.get("confidence", confidence),
        user_id=user_id,
        organization_id="default",  # TODO: Get from user
        invoice_text=invoice_text,  # For discount detection
    )
    
    # Submit to invoice workflow
    try:
        workflow = InvoiceWorkflowService(organization_id=invoice.organization_id)
        result = await workflow.process_new_invoice(invoice)
        logger.info(f"Invoice workflow result: {result.get('status')}")
        return result
    except Exception as e:
        logger.error(f"Invoice workflow failed: {e}")
        return {"status": "error", "error": str(e)}


def _extract_vendor_from_sender(sender: str) -> str:
    """Extract vendor name from email sender."""
    import re
    # Try to get name part: "Stripe <billing@stripe.com>"
    name_match = re.match(r"^([^<]+)", sender)
    if name_match:
        return name_match.group(1).strip()
    # Fall back to domain: "billing@stripe.com" -> "stripe"
    if "@" in sender:
        domain = sender.split("@")[1].split(".")[0]
        return domain.title()
    return sender


async def process_payment_request_email(
    client: GmailAPIClient,
    message,
    user_id: str,
    confidence: float,
):
    """
    Process a payment request email (non-invoice).
    
    These are emails like:
    - "Please pay $500 to John for consulting"
    - "Expense reimbursement request: $250"
    - "Contractor payment needed"
    
    Flow:
    1. Extract payment details from email
    2. Create payment request
    3. Route to appropriate approver via Slack
    """
    from clearledgr.services.payment_request import get_payment_request_service
    from clearledgr.services.slack_notifications import send_payment_request_notification
    
    logger.info(f"Processing payment request email: {message.subject}")
    
    # Get sender info
    sender_name = _extract_vendor_from_sender(message.sender)
    sender_email = message.sender
    if "<" in sender_email:
        import re
        email_match = re.search(r'<([^>]+)>', sender_email)
        if email_match:
            sender_email = email_match.group(1)
    
    # Create payment request
    service = get_payment_request_service("default")
    
    try:
        request = service.create_from_email(
            email_id=message.id,
            sender_email=sender_email,
            sender_name=sender_name,
            subject=message.subject,
            body=message.body_text or message.snippet or "",
        )
        
        logger.info(f"Created payment request {request.request_id}: ${request.amount} to {request.payee_name}")
        
        # Send Slack notification for approval
        try:
            await send_payment_request_notification(request)
        except Exception as e:
            logger.warning(f"Failed to send Slack notification: {e}")
        
        return {
            "status": "created",
            "request_id": request.request_id,
            "amount": request.amount,
            "payee": request.payee_name,
        }
    
    except Exception as e:
        logger.error(f"Payment request creation failed: {e}")
        return {"status": "error", "error": str(e)}


async def process_bank_statement_attachment(
    client: GmailAPIClient,
    message,
    user_id: str,
    engine,
):
    """
    Process bank statement attachments.
    """
    from clearledgr.services.bank_statement_parser import BankStatementParser
    
    parser = BankStatementParser()
    
    for attachment in message.attachments:
        try:
            # Download attachment
            content = await client.get_attachment(message.id, attachment["id"])
            
            # Parse based on type
            filename = attachment["filename"].lower()
            
            if filename.endswith(".csv"):
                transactions = parser.parse_csv(content.decode("utf-8"))
            elif filename.endswith(".pdf"):
                # For PDF, we'd need OCR - for now, skip
                logger.info(f"PDF parsing not yet implemented: {filename}")
                continue
            else:
                logger.info(f"Unsupported attachment type: {filename}")
                continue
            
            logger.info(f"Parsed {len(transactions)} transactions from {filename}")
            
            # Add transactions to engine
            for txn in transactions:
                engine.add_transaction(
                    source="bank",
                    reference=txn.reference or f"BANK-{txn.date}-{abs(hash(txn.description))%10000}",
                    amount=txn.amount,
                    currency=txn.currency or "EUR",
                    date=txn.date,
                    description=txn.description,
                    metadata={
                        "email_id": message.id,
                        "filename": attachment["filename"],
                        "user_id": user_id,
                    },
                )
            
            # Run reconciliation
            if transactions:
                result = engine.run_reconciliation(user_id=user_id)
                logger.info(f"Reconciliation result: {result['matches_found']} matches, {result['exceptions_created']} exceptions")
        
        except Exception as e:
            logger.error(f"Error processing attachment {attachment['filename']}: {e}")


# ============================================================================
# OAUTH ENDPOINTS - For user authorization
# ============================================================================

@router.get("/authorize")
async def gmail_authorize(user_id: str, redirect_url: Optional[str] = None):
    """
    Initiate Gmail OAuth flow.
    
    Returns URL to redirect user to for authorization.
    """
    state = json.dumps({"user_id": user_id, "redirect_url": redirect_url or ""})
    state_encoded = base64.urlsafe_b64encode(state.encode()).decode()
    
    try:
        auth_url = generate_auth_url(state=state_encoded)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    
    return {"auth_url": auth_url}


@router.get("/callback")
async def gmail_callback(code: str, state: Optional[str] = None):
    """
    Handle OAuth callback from Google.
    """
    try:
        # Decode state
        user_id = None
        redirect_url = None
        oauth_redirect_uri = None
        
        if state:
            try:
                state_decoded = json.loads(base64.urlsafe_b64decode(state).decode())
                user_id = state_decoded.get("user_id")
                redirect_url = state_decoded.get("redirect_url")
                oauth_redirect_uri = state_decoded.get("oauth_redirect_uri")
            except Exception:
                pass
        
        # Exchange code for tokens
        token = await exchange_code_for_tokens(code, redirect_uri=oauth_redirect_uri)
        
        # Override user_id if provided in state
        if user_id:
            token = token.__class__(
                user_id=user_id,
                access_token=token.access_token,
                refresh_token=token.refresh_token,
                expires_at=token.expires_at,
                email=token.email,
            )
        
        # Store token
        token_store.store(token)
        
        # Set up watch for push notifications
        watch_service = GmailWatchService(token.user_id)
        watch_result = await watch_service.setup_watch()

        logger.info(f"Gmail watch set up for {token.email}, expires: {watch_result.get('expiration')}")

        # Mark autopilot connected immediately after OAuth + watch setup succeeds.
        db = get_db()
        db.save_gmail_autopilot_state(
            user_id=token.user_id,
            email=token.email,
            last_history_id=watch_result.get("historyId"),
            watch_expiration=watch_result.get("expiration"),
            last_watch_at=datetime.utcnow().isoformat(),
            last_error=None,
        )
        
        # Return success or redirect
        if redirect_url:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=f"{redirect_url}?success=true")
        
        return {
            "status": "success",
            "email": token.email,
            "message": "Gmail autopilot enabled. Clearledgr will now process your emails automatically.",
            "watch_expiration": watch_result.get("expiration"),
        }
    
    except ValueError as e:
        logger.error(f"Gmail callback config error: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Gmail callback error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/disconnect")
async def gmail_disconnect(user_id: str):
    """
    Disconnect Gmail integration for a user.
    """
    try:
        # Stop watch
        watch_service = GmailWatchService(user_id)
        await watch_service.stop_watch()
        
        # Remove token
        token_store.delete(user_id)
        
        return {"status": "success", "message": "Gmail disconnected"}
    
    except Exception as e:
        logger.error(f"Gmail disconnect error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status/{user_id}")
async def gmail_status(user_id: str):
    """
    Check Gmail integration status for a user.
    """
    token = token_store.get(user_id)
    state = get_db().get_gmail_autopilot_state(user_id) or {}
    
    if not token:
        return {
            "connected": False,
            "message": "Gmail not connected",
            "autopilot": {
                "last_scan_at": state.get("last_scan_at"),
                "last_error": state.get("last_error"),
            },
        }
    
    return {
        "connected": True,
        "email": token.email,
        "expires_at": token.expires_at.isoformat(),
        "is_expired": token.is_expired(),
        "autopilot": {
            "last_scan_at": state.get("last_scan_at"),
            "watch_expiration": state.get("watch_expiration"),
            "last_watch_at": state.get("last_watch_at"),
            "last_error": state.get("last_error"),
        },
    }
