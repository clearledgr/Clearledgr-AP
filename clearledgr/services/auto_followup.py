"""
Auto Follow-up Service

Automatically drafts follow-up emails when invoice information is missing:
- Missing PO number
- Unclear amount
- Missing due date
- Incomplete vendor details

Also provides:
- Response detection — check if a vendor replied to a follow-up
- Escalation logic — resend or escalate when vendor is unresponsive

This handles the "invisible work" problem by automating clarification requests.
"""

import json
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class MissingInfoType(Enum):
    """Types of missing information."""
    PO_NUMBER = "po_number"
    AMOUNT = "amount"
    DUE_DATE = "due_date"
    VENDOR_NAME = "vendor_name"
    INVOICE_NUMBER = "invoice_number"
    TAX_INFO = "tax_info"
    PAYMENT_TERMS = "payment_terms"
    BANK_DETAILS = "bank_details"


@dataclass
class FollowUpDraft:
    """A draft follow-up email."""
    to: str
    subject: str
    body: str
    original_thread_id: str
    missing_info: List[MissingInfoType]
    created_at: datetime


class AutoFollowUpService:
    """
    Service for automatically drafting follow-up emails
    when invoice information is missing or unclear.
    """
    
    # Email templates for different missing info types
    TEMPLATES = {
        MissingInfoType.PO_NUMBER: {
            "subject_prefix": "Re: {original_subject} - PO Number Required",
            "body": """Hi,

Thank you for sending the invoice. Before we can process payment, we need the Purchase Order (PO) number associated with this invoice.

Could you please reply with the PO number or update the invoice to include it?

Original Invoice Details:
- Vendor: {vendor}
- Amount: {amount}
- Invoice #: {invoice_number}

Best regards"""
        },
        MissingInfoType.AMOUNT: {
            "subject_prefix": "Re: {original_subject} - Amount Clarification Needed",
            "body": """Hi,

We received your invoice but the total amount is unclear or missing. Could you please confirm the exact amount due?

If there are multiple line items or taxes, please provide a breakdown.

Best regards"""
        },
        MissingInfoType.DUE_DATE: {
            "subject_prefix": "Re: {original_subject} - Payment Due Date Required",
            "body": """Hi,

Thank you for the invoice. We noticed the payment due date is not specified.

Could you please confirm when payment is due so we can prioritize accordingly?

Invoice Details:
- Vendor: {vendor}
- Amount: {amount}

Best regards"""
        },
        MissingInfoType.INVOICE_NUMBER: {
            "subject_prefix": "Re: {original_subject} - Invoice Number Missing",
            "body": """Hi,

We received your payment request but it doesn't include an invoice number for our records.

Could you please provide an invoice number or send a formal invoice document?

Best regards"""
        },
        MissingInfoType.TAX_INFO: {
            "subject_prefix": "Re: {original_subject} - Tax Information Needed",
            "body": """Hi,

For compliance purposes, we need the tax breakdown for this invoice. Please provide:
- Tax rate applied
- Tax amount
- Your tax identification number (if applicable)

Best regards"""
        },
        MissingInfoType.BANK_DETAILS: {
            "subject_prefix": "Re: {original_subject} - Bank Details Required",
            "body": """Hi,

To process payment, we need your banking information:
- Bank name
- Account number
- Routing number (for ACH) or SWIFT code (for wire)

Please send this via a secure method.

Best regards"""
        },
        MissingInfoType.VENDOR_NAME: {
            "subject_prefix": "Re: {original_subject} - Vendor Information Needed",
            "body": """Hi,

We need to verify the vendor/company name for this invoice. Could you please confirm:
- Legal business name
- Business address
- Contact information

Best regards"""
        },
        MissingInfoType.PAYMENT_TERMS: {
            "subject_prefix": "Re: {original_subject} - Payment Terms Clarification",
            "body": """Hi,

Could you please clarify the payment terms for this invoice? Specifically:
- Net payment days (Net 30, Net 60, etc.)
- Early payment discount (if any)
- Accepted payment methods

Best regards"""
        }
    }
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self._drafts: Dict[str, FollowUpDraft] = {}
    
    def detect_missing_info(
        self,
        invoice_data: Dict[str, Any]
    ) -> List[MissingInfoType]:
        """
        Analyze invoice data and detect what information is missing.
        
        Args:
            invoice_data: Extracted invoice data
        
        Returns:
            List of missing information types
        """
        missing = []
        
        # Check for missing PO number (often required by companies)
        if not invoice_data.get("po_number"):
            # Only flag if this vendor usually requires PO
            if self._vendor_requires_po(invoice_data.get("vendor")):
                missing.append(MissingInfoType.PO_NUMBER)
        
        # Check for missing/unclear amount
        amount = invoice_data.get("amount")
        if amount is None or amount == 0:
            missing.append(MissingInfoType.AMOUNT)
        
        # Check for missing due date
        if not invoice_data.get("due_date"):
            missing.append(MissingInfoType.DUE_DATE)
        
        # Check for missing invoice number
        if not invoice_data.get("invoice_number"):
            missing.append(MissingInfoType.INVOICE_NUMBER)
        
        # Check vendor information
        vendor = invoice_data.get("vendor", "").strip()
        if not vendor or vendor.lower() in ["unknown", "n/a", ""]:
            missing.append(MissingInfoType.VENDOR_NAME)
        
        return missing
    
    def create_followup_draft(
        self,
        original_thread_id: str,
        original_subject: str,
        sender_email: str,
        invoice_data: Dict[str, Any],
        missing_info: List[MissingInfoType],
        company_name: str = "Clearledgr"
    ) -> Optional[FollowUpDraft]:
        """
        Create a draft follow-up email for missing information.
        
        Args:
            original_thread_id: Gmail thread ID
            original_subject: Original email subject
            sender_email: Email to reply to
            invoice_data: Extracted invoice data
            missing_info: List of missing info types
            company_name: Your company name for signature
        
        Returns:
            FollowUpDraft or None if no follow-up needed
        """
        if not missing_info:
            return None
        
        # Use the first missing item for the primary email (most important)
        primary_missing = missing_info[0]
        template = self.TEMPLATES.get(primary_missing)
        
        if not template:
            return None
        
        # Format subject
        subject = template["subject_prefix"].format(
            original_subject=original_subject
        )
        
        # Format body with available data
        body = template["body"].format(
            vendor=invoice_data.get("vendor", "N/A"),
            amount=f"${invoice_data.get('amount', 0):,.2f}" if invoice_data.get("amount") else "Not specified",
            invoice_number=invoice_data.get("invoice_number", "N/A"),
            due_date=invoice_data.get("due_date", "Not specified"),
            company_name=company_name
        )
        
        # Add note about other missing items
        if len(missing_info) > 1:
            other_items = [self._format_missing_type(m) for m in missing_info[1:]]
            body += f"\n\nAdditionally, please also provide:\n" + "\n".join([f"• {item}" for item in other_items])
        
        draft = FollowUpDraft(
            to=sender_email,
            subject=subject,
            body=body,
            original_thread_id=original_thread_id,
            missing_info=missing_info,
            created_at=datetime.now(timezone.utc)
        )
        
        self._drafts[original_thread_id] = draft

        # E3: Persist to AP item metadata so drafts survive restarts
        try:
            from clearledgr.core.database import get_db
            db = get_db()
            if hasattr(db, "get_ap_item_by_thread"):
                ap_item = db.get_ap_item_by_thread(self.organization_id, original_thread_id)
                if ap_item:
                    metadata = json.loads(ap_item.get("metadata") or "{}")
                    metadata["pending_followup"] = {
                        "to": draft.to,
                        "subject": draft.subject,
                        "created_at": draft.created_at.isoformat(),
                        "missing_info": [m.value for m in draft.missing_info],
                    }
                    db.update_ap_item(ap_item["id"], metadata=json.dumps(metadata))
        except Exception as exc:
            logger.warning("Could not persist follow-up draft: %s", exc)

        logger.info(f"Created follow-up draft for thread {original_thread_id}: {subject}")

        return draft
    
    def get_draft(self, thread_id: str) -> Optional[FollowUpDraft]:
        """Get a draft by thread ID."""
        return self._drafts.get(thread_id)
    
    def get_all_drafts(self) -> List[FollowUpDraft]:
        """Get all pending drafts."""
        return list(self._drafts.values())
    
    def delete_draft(self, thread_id: str) -> bool:
        """Delete a draft after it's been sent or dismissed."""
        if thread_id in self._drafts:
            del self._drafts[thread_id]
            return True
        return False

    async def create_gmail_draft(
        self,
        gmail_client: Any,
        ap_item_id: str,
        thread_id: str,
        to_email: str,
        invoice_data: Dict[str, Any],
        question: Optional[str] = None,
    ) -> Optional[str]:
        """Create a real Gmail draft for a needs_info follow-up.

        Uses ``create_followup_draft()`` to build the email body, then
        calls ``gmail_client.create_draft()`` to persist it in Gmail.
        Returns the Gmail draft ID (or None on failure).

        The draft is intentionally **not** auto-sent — the finance user
        reviews it in Gmail and hits Send when ready.
        """
        try:
            original_subject = invoice_data.get("subject") or "Invoice follow-up"
            vendor = invoice_data.get("vendor_name") or invoice_data.get("vendor") or "Unknown vendor"
            amount = invoice_data.get("amount") or 0
            invoice_number = invoice_data.get("invoice_number") or "N/A"

            if question:
                # Use the Claude-generated question directly as the body
                body = (
                    f"Hi,\n\n"
                    f"We are reviewing invoice #{invoice_number} from {vendor} "
                    f"(${amount:,.2f}) and need the following information before we can process payment:\n\n"
                    f"{question}\n\n"
                    f"Please reply at your earliest convenience.\n\n"
                    f"Best regards"
                )
                subject = f"Re: {original_subject} - Clarification Needed"
            else:
                # Fall back to template-based draft
                missing_types = [MissingInfoType.PO_NUMBER]  # default
                draft = self.create_followup_draft(
                    original_thread_id=thread_id,
                    original_subject=original_subject,
                    sender_email=to_email,
                    invoice_data=invoice_data,
                    missing_info=missing_types,
                )
                if not draft:
                    return None
                body = draft.body
                subject = draft.subject

            draft_id = await gmail_client.create_draft(
                thread_id=thread_id,
                to=to_email,
                subject=subject,
                body=body,
            )
            logger.info(
                "Gmail draft created for ap_item_id=%s thread=%s draft_id=%s",
                ap_item_id,
                thread_id,
                draft_id,
            )
            return draft_id or None
        except Exception as exc:
            logger.error("create_gmail_draft failed for ap_item_id=%s: %s", ap_item_id, exc)
            return None
    
    async def check_vendor_response(
        self,
        gmail_client: Any,
        ap_item_id: str,
        thread_id: str,
        followup_sent_at: str,
        vendor_email: str,
    ) -> Optional[Dict[str, Any]]:
        """Check if a vendor has replied to a follow-up email.

        Inspects the Gmail thread for messages arriving after
        *followup_sent_at* whose sender matches *vendor_email* (or
        the vendor's domain).

        Returns a dict with response info if found, or ``None``.
        """
        try:
            messages = await gmail_client.get_thread(thread_id)
        except Exception as exc:
            logger.warning(
                "check_vendor_response: could not fetch thread %s: %s",
                thread_id, exc,
            )
            return None

        # Parse the sent-at cutoff
        try:
            cutoff = datetime.fromisoformat(
                followup_sent_at.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            cutoff = datetime.min.replace(tzinfo=timezone.utc)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)

        vendor_lower = (vendor_email or "").strip().lower()
        vendor_domain = vendor_lower.split("@")[-1] if "@" in vendor_lower else ""

        for msg in messages:
            msg_date = getattr(msg, "date", None)
            if msg_date is None:
                continue
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            if msg_date <= cutoff:
                continue

            sender = (getattr(msg, "sender", "") or "").strip().lower()
            # Match on exact email or domain
            if vendor_lower and vendor_lower in sender:
                return {
                    "response_detected": True,
                    "message_id": getattr(msg, "id", ""),
                    "sender": sender,
                    "date": msg_date.isoformat(),
                    "snippet": getattr(msg, "snippet", "")[:200],
                    "ap_item_id": ap_item_id,
                }
            if vendor_domain and vendor_domain in sender:
                return {
                    "response_detected": True,
                    "message_id": getattr(msg, "id", ""),
                    "sender": sender,
                    "date": msg_date.isoformat(),
                    "snippet": getattr(msg, "snippet", "")[:200],
                    "ap_item_id": ap_item_id,
                }

        return None

    def check_followup_escalation(
        self,
        ap_item_id: str,
        followup_sent_at: str,
        escalation_days: int = 3,
        max_followups: int = 3,
        followup_attempt_count: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """Determine whether to resend or escalate a pending follow-up.

        Returns:
        - ``{"action": "resend", "attempt": N+1}`` if time to re-send
        - ``{"action": "escalate", "reason": "vendor_unresponsive"}`` if
          max follow-ups exceeded
        - ``None`` if not yet due for escalation
        """
        try:
            sent_at = datetime.fromisoformat(
                followup_sent_at.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            return None
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        elapsed = now - sent_at
        if elapsed < timedelta(days=escalation_days):
            return None  # not yet overdue

        if followup_attempt_count >= max_followups:
            return {
                "action": "escalate",
                "reason": "vendor_unresponsive",
                "ap_item_id": ap_item_id,
                "attempts": followup_attempt_count,
                "days_waiting": elapsed.days,
            }

        return {
            "action": "resend",
            "attempt": followup_attempt_count + 1,
            "ap_item_id": ap_item_id,
            "days_waiting": elapsed.days,
        }

    def _vendor_requires_po(self, vendor: str) -> bool:
        """
        Check if this vendor typically requires PO numbers.
        In production, this would check historical data.
        """
        # For MVP, assume enterprise vendors require PO
        enterprise_indicators = [
            "inc", "corp", "llc", "ltd", "enterprise",
            "technologies", "services", "solutions"
        ]
        if vendor:
            vendor_lower = vendor.lower()
            return any(ind in vendor_lower for ind in enterprise_indicators)
        return False
    
    def _format_missing_type(self, missing_type: MissingInfoType) -> str:
        """Format missing type for display."""
        return {
            MissingInfoType.PO_NUMBER: "Purchase Order (PO) number",
            MissingInfoType.AMOUNT: "Invoice amount",
            MissingInfoType.DUE_DATE: "Payment due date",
            MissingInfoType.VENDOR_NAME: "Vendor/company name",
            MissingInfoType.INVOICE_NUMBER: "Invoice number",
            MissingInfoType.TAX_INFO: "Tax information",
            MissingInfoType.PAYMENT_TERMS: "Payment terms",
            MissingInfoType.BANK_DETAILS: "Bank/payment details"
        }.get(missing_type, str(missing_type))


# Singleton instance
_instances: Dict[str, AutoFollowUpService] = {}


def get_auto_followup_service(organization_id: str = "default") -> AutoFollowUpService:
    """Get or create AutoFollowUpService instance."""
    if organization_id not in _instances:
        _instances[organization_id] = AutoFollowUpService(organization_id)
    return _instances[organization_id]


async def create_followup_for_invoice(
    thread_id: str,
    subject: str,
    sender_email: str,
    invoice_data: Dict[str, Any],
    organization_id: str = "default"
) -> Optional[Dict[str, Any]]:
    """
    Convenience function to create a follow-up draft for an invoice.
    
    Returns the draft data or None if no follow-up needed.
    """
    service = get_auto_followup_service(organization_id)
    
    # Detect missing info
    missing = service.detect_missing_info(invoice_data)
    
    if not missing:
        return None
    
    # Create draft
    draft = service.create_followup_draft(
        original_thread_id=thread_id,
        original_subject=subject,
        sender_email=sender_email,
        invoice_data=invoice_data,
        missing_info=missing
    )
    
    if draft:
        return {
            "to": draft.to,
            "subject": draft.subject,
            "body": draft.body,
            "thread_id": draft.original_thread_id,
            "missing_info": [m.value for m in draft.missing_info]
        }
    
    return None
