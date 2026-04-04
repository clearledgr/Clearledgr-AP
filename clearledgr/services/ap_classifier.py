"""AP-specific email classifier (single source of truth).

Classifies emails into:
- INVOICE — vendor bill requiring approval and payment (real AP payable)
- PAYMENT_REQUEST — non-invoice payment request (reimbursement, wire, contractor)
- SUBSCRIPTION_NOTIFICATION — SaaS/recurring charge already billed to card (Google, AWS, Slack)
- RECEIPT — payment confirmation for a completed transaction
- NOISE — everything else (marketing, security alerts, newsletters)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from clearledgr.services.llm_multimodal import MultiModalLLMService

logger = logging.getLogger(__name__)


NOISE_PATTERNS = [
    # Marketing / promos / newsletters
    r"\b(unsubscribe|newsletter|promotion|promo|discount|offer|sale)\b",
    r"\b(webinar|event|conference|register|rsvp|summit|workshop)\b",
    r"\b(product\s+update|what'?s\s+new|announcement|release)\b",
    r"\b(tips?|guide|how\s+to|best\s+practices|insights?)\b",
    # Account/security
    r"\b(password|security\s+alert|verify|login\s+alert|account\s+alert)\b",
    # Non-AP financial (confirmations only — receipts are AP-relevant)
    r"\b(payment\s+received|payment\s+confirmed|order\s+confirmation)\b",
    r"\b(card\s+declined|payment\s+failed|chargeback|dispute|refund)\b",
]

INVOICE_PATTERNS = [
    r"\binvoice\b",
    r"\bbill\b",
    r"\bamount\s+due\b",
    r"\bbalance\s+due\b",
    r"\btotal\s+due\b",
    r"\bpayable\b",
    r"\binvoice\s+number\b",
    r"\bpayment\s+terms\b",
    r"\bnet\s+\d+\b",
    r"\bdue\s+date\b",
]

PAYMENT_REQUEST_PATTERNS = [
    r"\bpayment\s+request\b",
    r"\bplease\s+pay\b",
    r"\brequest(ing)?\s+payment\b",
    r"\breimburse(ment)?\b",
    r"\bexpense\s+report\b",
    r"\bwire\s+to\b",
    r"\btransfer\s+to\b",
    r"\bpay\s+to\b",
    r"\bcontractor\s+payment\b",
]

BILLING_SENDER_PATTERNS = [
    r"\bbilling@",
    r"\binvoices?@",
    r"\baccounts?@",
    r"\bap@",
    r"\baccounting@",
    r"\bfinance@",
]

# SaaS / subscription notification patterns — already charged, not a payable
SUBSCRIPTION_PATTERNS = [
    r"\byour\s+(?:monthly\s+)?(?:invoice|bill|statement)\s+is\s+(?:ready|available)\b",
    r"\bsubscription\s+(?:invoice|charge|billing|renewal)\b",
    r"\brecurring\s+(?:charge|payment|billing)\b",
    r"\bmonthly\s+(?:charge|statement|billing)\b",
    r"\bauto[\-\s]?(?:pay|charge|billed|debit)\b",
    r"\bcharged?\s+(?:to\s+)?(?:your\s+)?(?:card|account|payment\s+method)\b",
    r"\byour\s+(?:card|payment\s+method)\s+(?:was|has\s+been)\s+charged\b",
    r"\bpayment\s+(?:was\s+)?(?:processed|successful|completed)\b",
]

# Known SaaS/subscription senders (domain patterns)
KNOWN_SUBSCRIPTION_SENDERS = [
    r"@google\.com$",
    r"@amazon\.com$",
    r"@aws\.amazon\.com$",
    r"@cloud\.google\.com$",
    r"@microsoft\.com$",
    r"@slack\.com$",
    r"@github\.com$",
    r"@zoom\.us$",
    r"@atlassian\.com$",
    r"@dropbox\.com$",
    r"@hubspot\.com$",
    r"@salesforce\.com$",
    r"@twilio\.com$",
    r"@stripe\.com$",
    r"@heroku\.com$",
    r"@digitalocean\.com$",
    r"@netlify\.com$",
    r"@vercel\.com$",
    r"@notion\.so$",
    r"@figma\.com$",
    r"@linear\.app$",
    r"@intercom\.io$",
    r"@mixpanel\.com$",
    r"@datadog\.com$",
    r"@sentry\.io$",
    r"@cloudflare\.com$",
]

RECEIPT_PATTERNS = [
    r"\breceipt\s+(?:for|of)\b",
    r"\bpayment\s+receipt\b",
    r"\btransaction\s+receipt\b",
    r"\bthank\s+you\s+for\s+your\s+payment\b",
    r"\byour\s+payment\s+(?:of|for)\b",
    r"\bpayment\s+confirmation\b",
]

CREDIT_NOTE_PATTERNS = [
    r"\bcredit\s+(?:note|memo)\b",
    r"\bcredit\s+applied\b",
    r"\badjustment\s+(?:note|memo|credit)\b",
]

STATEMENT_PATTERNS = [
    r"\baccount\s+statement\b",
    r"\bstatement\s+of\s+account\b",
    r"\bvendor\s+statement\b",
    r"\bmonthly\s+statement\b",
    r"\bbalance\s+(?:brought|carried)\s+forward\b",
]

REMITTANCE_PATTERNS = [
    r"\bremittance\s+advice\b",
    r"\bpayment\s+advice\b",
    r"\bfunds?\s+(?:transfer|sent|remitted)\b",
]

BANK_NOTIFICATION_PATTERNS = [
    r"\bdirect\s+debit\b",
    r"\bbank\s+(?:charge|notification|alert)\b",
    r"\bforeign\s+exchange\b",
    r"\bfx\s+(?:confirmation|rate)\b",
]

PO_CONFIRMATION_PATTERNS = [
    r"\bpurchase\s+order\s+(?:confirmation|acknowledged?|accepted)\b",
    r"\border\s+(?:confirmation|acknowledged?|accepted)\b",
    r"\bpo\s+(?:confirmation|acknowledged?)\b",
]

TAX_DOCUMENT_PATTERNS = [
    r"\bvat\s+(?:invoice|receipt|certificate)\b",
    r"\bwithholding\s+tax\b",
    r"\bwht\s+certificate\b",
    r"\btax\s+(?:receipt|certificate|invoice)\b",
]

CONTRACT_PATTERNS = [
    r"\bcontract\s+(?:renewal|extension|amendment)\b",
    r"\brenewal\s+(?:notice|reminder)\b",
    r"\bservice\s+agreement\b",
]

DEBIT_NOTE_PATTERNS = [
    r"\bdebit\s+note\b",
    r"\bdebit\s+memo\b",
    r"\badditional\s+charge\b",
    r"\bsurcharge\b",
    r"\badjustment\s+(?:invoice|debit)\b",
]

DISPUTE_RESPONSE_PATTERNS = [
    r"\bre(?:garding|:\s*)\s*(?:your\s+)?(?:query|dispute|claim|complaint)\b",
    r"\bin\s+response\s+to\s+your\s+(?:query|dispute|email|request)\b",
    r"\bdispute\s+(?:resolution|response|update)\b",
    r"\bclaim\s+(?:update|response|resolution)\b",
]

AMOUNT_PATTERN = re.compile(
    r"(\$|€|£|USD|EUR|GBP)\s*[\d,]+(?:\.\d{2})?",
    re.IGNORECASE,
)

INVOICE_NUMBER_PATTERN = re.compile(
    r"\b(?:invoice|inv|bill)\s*(?:number|no\.?|#)?[:\s\-]*([A-Z0-9][\w\-\/]{3,})",
    re.IGNORECASE,
)


def classify_ap_email(
    subject: str,
    sender: str,
    snippet: str,
    body: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Classify an email for AP workflow."""
    subject = subject or ""
    sender = sender or ""
    snippet = snippet or ""
    body = body or ""
    attachments = attachments or []

    combined = f"{subject} {snippet} {body}".lower()
    sender_lower = sender.lower()

    # LLM classification (if configured)
    try:
        llm = MultiModalLLMService()
        if llm.is_available:
            prompt = f"""Classify this email for a finance team's AP workflow. Return ONLY valid JSON.

Allowed types:
- INVOICE — vendor bill requiring approval and payment (the vendor expects YOU to pay)
- PAYMENT_REQUEST — non-invoice payment request (reimbursement, wire, contractor payment)
- DEBIT_NOTE — additional charge from a vendor, linked to an original invoice
- CREDIT_NOTE — vendor credit that reduces what you owe
- SUBSCRIPTION_NOTIFICATION — SaaS/cloud billing where card was ALREADY charged (Google Cloud, AWS, Slack). Not a payable.
- RECEIPT — payment confirmation for a completed transaction
- REMITTANCE_ADVICE — proof that payment was sent to a vendor
- STATEMENT — vendor account summary (not a payable, used for reconciliation)
- BANK_NOTIFICATION — bank charge, direct debit, FX confirmation
- PO_CONFIRMATION — vendor confirming a purchase order
- TAX_DOCUMENT — VAT invoice, WHT certificate, tax receipt
- CONTRACT_RENEWAL — vendor contract or renewal notice
- DISPUTE_RESPONSE — vendor reply to an existing dispute/query
- REFUND — refund notification
- NOISE — not finance-related (marketing, security, newsletters)

Key distinctions:
- SaaS "invoice" that auto-charges a card = SUBSCRIPTION_NOTIFICATION
- Vendor bill that expects you to initiate payment = INVOICE
- "Your payment has been received" = RECEIPT
- "Credit memo" or "credit note" reducing balance = CREDIT_NOTE

Subject: {subject}
Sender: {sender}
Preview: {snippet}

Return JSON:
{{"type": "...", "confidence": 0.0-1.0, "reasoning": "..."}}
"""
            result = llm.generate_json(prompt)
            doc_type = str(result.get("type", "NOISE")).upper()
            from clearledgr.services.document_routing import VALID_DOCUMENT_TYPES
            if doc_type.lower() not in VALID_DOCUMENT_TYPES:
                doc_type = "NOISE"
            confidence = float(result.get("confidence", 0.6))
            return {
                "type": doc_type,
                "confidence": min(1.0, max(0.0, confidence)),
                "reason": result.get("reasoning", ""),
                "method": "llm",
                "provider": result.get("provider", "unknown"),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("AP LLM classification failed, using rules: %s", exc)

    # Rule-based classification — check subscription/receipt FIRST, then invoice/payment

    # --- Subscription notification detection (highest priority) ---
    subscription_score = 0
    subscription_hits = _count_matches(SUBSCRIPTION_PATTERNS, combined)
    subscription_score += subscription_hits * 2

    # Known SaaS sender is a strong subscription signal
    is_known_saas_sender = any(re.search(p, sender_lower) for p in KNOWN_SUBSCRIPTION_SENDERS)
    if is_known_saas_sender:
        subscription_score += 3

    # "invoice is available" + known SaaS sender = subscription, not payable
    if is_known_saas_sender and re.search(r"\binvoice\s+is\s+available\b", combined):
        subscription_score += 3

    # "charged to your card/account" is definitive
    if re.search(r"\bcharged?\s+(?:to\s+)?(?:your\s+)?(?:card|account|payment\s+method)\b", combined):
        subscription_score += 4

    if subscription_score >= 3:
        return {
            "type": "SUBSCRIPTION_NOTIFICATION",
            "confidence": min(0.95, 0.7 + 0.03 * subscription_score),
            "reason": "subscription_notification_signals" + (
                " (known_saas_sender)" if is_known_saas_sender else ""
            ),
            "method": "rules",
            "score": subscription_score,
        }

    # --- Receipt detection ---
    receipt_hits = _count_matches(RECEIPT_PATTERNS, combined)
    if receipt_hits >= 1 and re.search(r"\breceipt\b", combined):
        return {
            "type": "RECEIPT",
            "confidence": min(0.90, 0.65 + 0.05 * receipt_hits),
            "reason": "receipt_signals",
            "method": "rules",
            "score": receipt_hits,
        }

    # --- Credit note detection ---
    credit_hits = _count_matches(CREDIT_NOTE_PATTERNS, combined)
    if credit_hits >= 1:
        return {
            "type": "CREDIT_NOTE",
            "confidence": min(0.90, 0.70 + 0.05 * credit_hits),
            "reason": "credit_note_signals",
            "method": "rules",
            "score": credit_hits,
        }

    # --- Statement detection ---
    statement_hits = _count_matches(STATEMENT_PATTERNS, combined)
    if statement_hits >= 1:
        return {
            "type": "STATEMENT",
            "confidence": min(0.90, 0.70 + 0.05 * statement_hits),
            "reason": "statement_signals",
            "method": "rules",
            "score": statement_hits,
        }

    # --- Remittance advice ---
    remittance_hits = _count_matches(REMITTANCE_PATTERNS, combined)
    if remittance_hits >= 1:
        return {
            "type": "REMITTANCE_ADVICE",
            "confidence": min(0.90, 0.70 + 0.05 * remittance_hits),
            "reason": "remittance_signals",
            "method": "rules",
            "score": remittance_hits,
        }

    # --- Bank notification ---
    bank_hits = _count_matches(BANK_NOTIFICATION_PATTERNS, combined)
    if bank_hits >= 1:
        return {
            "type": "BANK_NOTIFICATION",
            "confidence": min(0.85, 0.65 + 0.05 * bank_hits),
            "reason": "bank_notification_signals",
            "method": "rules",
            "score": bank_hits,
        }

    # --- PO confirmation ---
    po_conf_hits = _count_matches(PO_CONFIRMATION_PATTERNS, combined)
    if po_conf_hits >= 1:
        return {
            "type": "PO_CONFIRMATION",
            "confidence": min(0.85, 0.65 + 0.05 * po_conf_hits),
            "reason": "po_confirmation_signals",
            "method": "rules",
            "score": po_conf_hits,
        }

    # --- Tax document ---
    tax_hits = _count_matches(TAX_DOCUMENT_PATTERNS, combined)
    if tax_hits >= 1:
        return {
            "type": "TAX_DOCUMENT",
            "confidence": min(0.85, 0.65 + 0.05 * tax_hits),
            "reason": "tax_document_signals",
            "method": "rules",
            "score": tax_hits,
        }

    # --- Contract renewal ---
    contract_hits = _count_matches(CONTRACT_PATTERNS, combined)
    if contract_hits >= 1:
        return {
            "type": "CONTRACT_RENEWAL",
            "confidence": min(0.80, 0.60 + 0.05 * contract_hits),
            "reason": "contract_signals",
            "method": "rules",
            "score": contract_hits,
        }

    # --- Debit note ---
    debit_hits = _count_matches(DEBIT_NOTE_PATTERNS, combined)
    if debit_hits >= 1:
        return {
            "type": "DEBIT_NOTE",
            "confidence": min(0.85, 0.65 + 0.05 * debit_hits),
            "reason": "debit_note_signals",
            "method": "rules",
            "score": debit_hits,
        }

    # --- Dispute response ---
    dispute_hits = _count_matches(DISPUTE_RESPONSE_PATTERNS, combined)
    if dispute_hits >= 1:
        return {
            "type": "DISPUTE_RESPONSE",
            "confidence": min(0.80, 0.60 + 0.05 * dispute_hits),
            "reason": "dispute_response_signals",
            "method": "rules",
            "score": dispute_hits,
        }

    # --- Standard AP classification ---
    noise_hits = _count_matches(NOISE_PATTERNS, combined)

    invoice_score = 0
    payment_score = 0

    invoice_hits = _count_matches(INVOICE_PATTERNS, combined)
    if invoice_hits:
        invoice_score += 2 + invoice_hits

    payment_hits = _count_matches(PAYMENT_REQUEST_PATTERNS, combined)
    if payment_hits:
        payment_score += 2 + payment_hits

    if AMOUNT_PATTERN.search(combined):
        invoice_score += 2
        payment_score += 1

    if INVOICE_NUMBER_PATTERN.search(combined):
        invoice_score += 2

    if any(re.search(p, sender_lower) for p in BILLING_SENDER_PATTERNS):
        invoice_score += 1

    # "receipt" without strong receipt patterns → treat as AP signal
    if re.search(r"\breceipt\b", combined) and receipt_hits == 0:
        invoice_score += 1

    # "your invoice is available" from non-SaaS sender = real invoice
    if re.search(r"\binvoice\s+is\s+available\b", combined) and not is_known_saas_sender:
        invoice_score += 3

    attachment_names = " ".join([str(a.get("filename") or a.get("name") or "") for a in attachments]).lower()
    if attachment_names:
        if "invoice" in attachment_names or "bill" in attachment_names:
            invoice_score += 2
        invoice_score += 1

    # Invoice/payment signals override noise when strong enough
    if invoice_score >= 3 and invoice_score >= payment_score:
        return {
            "type": "INVOICE",
            "confidence": min(0.95, 0.6 + 0.05 * invoice_score),
            "reason": "invoice_signals",
            "method": "rules",
            "score": invoice_score,
        }

    if payment_score >= 3 and payment_score > invoice_score:
        return {
            "type": "PAYMENT_REQUEST",
            "confidence": min(0.9, 0.55 + 0.05 * payment_score),
            "reason": "payment_request_signals",
            "method": "rules",
            "score": payment_score,
        }

    # Only classify as noise if no AP signals at all
    if noise_hits >= 1:
        return {
            "type": "NOISE",
            "confidence": min(0.95, 0.7 + 0.1 * noise_hits),
            "reason": "noise_signals",
            "method": "rules",
        }

    return {
        "type": "NOISE",
        "confidence": max(0.6, 0.8 - 0.05 * max(invoice_score, payment_score)),
        "reason": "insufficient_ap_signals",
        "method": "rules",
    }


def _count_matches(patterns: List[str], text: str) -> int:
    hits = 0
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            hits += 1
    return hits
