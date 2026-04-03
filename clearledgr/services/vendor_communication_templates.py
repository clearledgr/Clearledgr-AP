"""Vendor Communication Templates

Pre-built email templates for vendor follow-ups.  Each template has a
subject and body with ``{placeholder}`` variables that are filled by
``render_template()``.

Template rendering sanitises inputs (strips HTML-like tags, limits field
length) to prevent injection of arbitrary content into outbound emails.
"""

import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Maximum length for any single template variable value after sanitisation.
_MAX_FIELD_LEN = 500

VENDOR_TEMPLATES: Dict[str, Dict[str, str]] = {
    "missing_po": {
        "subject": "Re: {original_subject} — Purchase Order Number Required",
        "body": (
            "Hi,\n\n"
            "Thank you for sending the invoice. Before we can process payment, "
            "we need the Purchase Order (PO) number associated with this invoice.\n\n"
            "Invoice: {invoice_number}\n"
            "Amount: {currency} {amount}\n\n"
            "Could you please provide the PO number at your earliest convenience?\n\n"
            "Thank you,\n"
            "{company_name}"
        ),
    },
    "missing_amount": {
        "subject": "Re: {original_subject} — Amount Clarification Needed",
        "body": (
            "Hi,\n\n"
            "We received your invoice but the total amount is unclear or missing. "
            "Could you please confirm the exact amount due?\n\n"
            "Invoice: {invoice_number}\n"
            "Vendor: {vendor_name}\n\n"
            "Thank you,\n"
            "{company_name}"
        ),
    },
    "missing_due_date": {
        "subject": "Re: {original_subject} — Payment Due Date Required",
        "body": (
            "Hi,\n\n"
            "Thank you for the invoice. We noticed the payment due date is not "
            "specified. Could you please confirm when payment is due?\n\n"
            "Invoice: {invoice_number}\n"
            "Amount: {currency} {amount}\n\n"
            "Thank you,\n"
            "{company_name}"
        ),
    },
    "bank_details_verification": {
        "subject": "Re: {original_subject} — Bank Details Verification",
        "body": (
            "Hi,\n\n"
            "We noticed the banking details on this invoice differ from what we "
            "have on file. For security purposes, could you please confirm your "
            "current bank details?\n\n"
            "Invoice: {invoice_number}\n"
            "Amount: {currency} {amount}\n\n"
            "Please confirm via a separate channel if possible.\n\n"
            "Thank you,\n"
            "{company_name}"
        ),
    },
    "general_inquiry": {
        "subject": "Re: {original_subject} — Additional Information Required",
        "body": (
            "Hi,\n\n"
            "{question}\n\n"
            "Invoice: {invoice_number}\n"
            "Amount: {currency} {amount}\n\n"
            "Thank you,\n"
            "{company_name}"
        ),
    },
    "followup_reminder": {
        "subject": "Re: {original_subject} — Follow-up: Information Still Required",
        "body": (
            "Hi,\n\n"
            "This is a follow-up regarding our previous request. We are still "
            "awaiting the following information to process your invoice:\n\n"
            "{original_question}\n\n"
            "Invoice: {invoice_number}\n"
            "Amount: {currency} {amount}\n\n"
            "Could you please respond at your earliest convenience?\n\n"
            "Thank you,\n"
            "{company_name}"
        ),
    },
}

# Regex to strip anything that looks like an HTML tag.
_TAG_RE = re.compile(r"<[^>]{1,200}>")


def _sanitize(value: str) -> str:
    """Strip HTML-like tags and truncate to prevent injection."""
    cleaned = _TAG_RE.sub("", str(value))
    if len(cleaned) > _MAX_FIELD_LEN:
        cleaned = cleaned[:_MAX_FIELD_LEN] + "..."
    return cleaned


def render_template(template_id: str, context: Dict[str, Any]) -> Dict[str, str]:
    """Render a template with context variables.

    Returns ``{"subject": "...", "body": "..."}``.

    Raises ``KeyError`` if *template_id* is not found.  Missing context
    variables are replaced with empty strings rather than raising, so
    callers can provide a partial context safely.
    """
    template = VENDOR_TEMPLATES.get(template_id)
    if not template:
        raise KeyError(f"Unknown vendor communication template: {template_id!r}")

    # Sanitise every context value.
    safe_ctx: Dict[str, str] = {}
    for key, val in context.items():
        safe_ctx[key] = _sanitize(str(val)) if val is not None else ""

    # Use a default-dict style approach: missing keys become "".
    class _DefaultDict(dict):
        def __missing__(self, key: str) -> str:
            return ""

    fmt_ctx = _DefaultDict(safe_ctx)

    return {
        "subject": template["subject"].format_map(fmt_ctx),
        "body": template["body"].format_map(fmt_ctx),
    }
