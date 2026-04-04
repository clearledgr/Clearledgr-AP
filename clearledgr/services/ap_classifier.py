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
            prompt = f"""Classify this email for AP workflow. Return ONLY valid JSON.

Allowed types:
- INVOICE — actual vendor bill with amount due that requires approval and payment
- PAYMENT_REQUEST — non-invoice payment request (reimbursement, wire transfer, contractor)
- SUBSCRIPTION_NOTIFICATION — SaaS/cloud billing notification where card was already charged (Google Cloud, AWS, Slack, etc). NOT a payable — just a record of a charge that already happened.
- RECEIPT — payment confirmation or receipt for a completed transaction
- NOISE — everything else (marketing, security alerts, newsletters, promotions)

Key distinction: If a SaaS provider (Google, AWS, Microsoft, Slack) sends an "invoice" for a subscription that auto-charges a card, that is SUBSCRIPTION_NOTIFICATION, not INVOICE. A real INVOICE is from a vendor who expects you to initiate payment.

Subject: {subject}
Sender: {sender}
Preview: {snippet}

Return JSON:
{{"type": "...", "confidence": 0.0-1.0, "reasoning": "..."}}
"""
            result = llm.generate_json(prompt)
            doc_type = str(result.get("type", "NOISE")).upper()
            if doc_type not in {"INVOICE", "PAYMENT_REQUEST", "SUBSCRIPTION_NOTIFICATION", "RECEIPT", "NOISE"}:
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
