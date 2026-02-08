"""
Auto Follow-up Service

Automatically drafts follow-up emails when invoice information is missing:
- Missing PO number
- Unclear amount
- Missing due date
- Incomplete vendor details

This handles the "invisible work" problem by automating clarification requests.
"""

import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

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
            created_at=datetime.now()
        )
        
        self._drafts[original_thread_id] = draft
        
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
