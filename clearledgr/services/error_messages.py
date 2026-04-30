"""
Error State Messages — DESIGN_THESIS.md §18

"The principle governing all error states: the agent must tell the
finance team exactly what happened, exactly why it stopped, and
exactly what is needed to resolve it."

Each error produces a structured message with:
- WHAT happened (specific, names the entity)
- WHY the agent stopped
- WHAT to do next (actionable resolution)
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def format_error_message(
    error_type: str,
    *,
    vendor_name: str = "",
    invoice_number: str = "",
    amount: float = 0,
    currency: str = "USD",
    erp_type: str = "",
    po_number: str = "",
    detail: str = "",
    timestamp: str = "",
) -> Dict[str, str]:
    """Format a thesis-quality error message for a known error type.

    Returns {"summary": ..., "detail": ..., "resolution": ...}
    following the DID-WHY-NEXT pattern for errors.
    """
    erp_display = {"quickbooks": "QuickBooks", "xero": "Xero", "netsuite": "NetSuite", "sap": "SAP"}.get(
        (erp_type or "").lower(), erp_type or "ERP"
    )

    messages = {
        # ── ERP Connectivity Errors ──
        "erp_unreachable": {
            "summary": f"{erp_display} connection lost at {timestamp or 'now'}.",
            "detail": (
                "Invoices are queued and will process automatically when connection restores. "
                "No payments have been delayed."
            ),
            "resolution": "Retries every 5 minutes. AP Manager alerted if outage exceeds 30 minutes.",
        },
        "erp_insufficient_permissions": {
            "summary": f"Cannot post to {erp_display} — missing permission.",
            "detail": detail or "The specific action requires a permission not currently granted.",
            "resolution": f"Grant the required permission in {erp_display} and retry. Other actions not requiring this permission continue normally.",
        },
        "po_not_found_in_erp": {
            "summary": f"PO {po_number or 'referenced'} does not exist in {erp_display}.",
            "detail": (
                f"Invoice {invoice_number or 'N/A'} from {vendor_name or 'vendor'} references "
                f"PO {po_number or 'unknown'}. This may be a typo, an unapproved PO, or a "
                f"PO from a different entity."
            ),
            "resolution": "Routed to AP Manager for resolution.",
        },
        "grn_not_matched": {
            "summary": "GRN partial match — found receipt but amounts differ.",
            "detail": detail or (
                f"Invoice {invoice_number or 'N/A'} from {vendor_name or 'vendor'}: "
                f"goods receipt found but quantities or amounts do not match exactly."
            ),
            "resolution": "Does not block approval. AP Manager can override if the discrepancy is acceptable.",
        },

        # ── Invoice Processing Errors ──
        "unreadable_pdf": {
            "summary": "Invoice attachment could not be parsed.",
            "detail": (
                f"The PDF from {vendor_name or 'vendor'} appears to be "
                f"image-only, corrupted, or in an unsupported format."
            ),
            "resolution": "Request a text-based PDF or typed invoice from the vendor. Flag for manual entry.",
        },
        "no_attachment": {
            "summary": f"Invoice email from {vendor_name or 'vendor'} has no attachment.",
            "detail": "The email body does not contain a structured invoice and no PDF/XML is attached.",
            "resolution": "Ask the vendor to resend with the invoice attached, or manually enter the invoice details.",
        },
        "duplicate_detected": {
            "summary": "Possible duplicate of a previous invoice.",
            "detail": (
                f"Invoice {invoice_number or 'N/A'} from {vendor_name or 'vendor'} "
                f"({currency} {amount:,.2f}) matches a previously processed invoice "
                f"within the duplicate detection window."
            ),
            "resolution": "Confirm this is a new invoice before processing. Original invoice linked in the timeline.",
        },
        "amount_extraction_conflict": {
            "summary": "Amount conflict between email subject and body.",
            "detail": detail or "The extracted amount differs from amounts mentioned elsewhere in the email.",
            "resolution": "Review the original email and confirm the correct amount before approving.",
        },

        # ── Vendor / Onboarding Errors ──
        "vendor_not_in_master": {
            "summary": f"Invoice from {vendor_name or 'unknown sender'} — vendor not in master.",
            "detail": "Sender is not an active vendor. No invoice Box created.",
            "resolution": "Initiate vendor onboarding or reject. Invoice will not be processed until vendor is activated.",
        },
        "iban_validation_failed": {
            "summary": f"IBAN verification failed for {vendor_name or 'vendor'}.",
            "detail": "The micro-deposit amounts provided by the vendor do not match.",
            "resolution": "Vendor can retry (up to 3 attempts) or re-enter their bank details.",
        },
        "kyc_document_rejected": {
            "summary": f"KYC document from {vendor_name or 'vendor'} is incomplete or invalid.",
            "detail": detail or "Required document not meeting completeness requirements.",
            "resolution": "Specific missing items flagged. Vendor notified via the onboarding portal.",
        },
        "vendor_unresponsive": {
            "summary": f"{vendor_name or 'Vendor'} has not responded to onboarding request.",
            "detail": "Auto-chase emails sent at 24h and 48h. No response received.",
            "resolution": "Escalated to AP Manager. Consider direct contact or alternative vendor.",
        },
    }

    msg = messages.get(error_type, {
        "summary": detail or f"Error: {error_type}",
        "detail": "",
        "resolution": "Review the error details and take appropriate action.",
    })

    return msg


def format_error_for_timeline(error_type: str, **kwargs) -> Dict[str, Any]:
    """Format an error as a DID-WHY-NEXT timeline entry."""
    msg = format_error_message(error_type, **kwargs)
    return {
        "event_type": f"error_{error_type}",
        "summary": msg["summary"],
        "reason": msg["detail"],
        "next_action": msg["resolution"],
        "actor": "agent",
    }


def format_error_for_slack(error_type: str, **kwargs) -> str:
    """Format an error as a single Slack mrkdwn string."""
    msg = format_error_message(error_type, **kwargs)
    return f"*{msg['summary']}*\n{msg['detail']}\n_{msg['resolution']}_"
