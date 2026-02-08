"""AP-specific email classifier (single source of truth).

Classifies emails into:
- INVOICE
- PAYMENT_REQUEST
- NOISE (everything else, including receipts, statements, confirmations)
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
    # Non-AP financial notices
    r"\b(payment\s+received|payment\s+confirmed|receipt|order\s+confirmation)\b",
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
- INVOICE (actual invoice/bill with amount due)
- PAYMENT_REQUEST (non-invoice pay request, reimbursement, transfer)
- NOISE (everything else: receipts, statements, marketing, confirmations)

Subject: {subject}
Sender: {sender}
Preview: {snippet}

Return JSON:
{{"type": "...", "confidence": 0.0-1.0, "reasoning": "..."}}
"""
            result = llm.generate_json(prompt)
            doc_type = str(result.get("type", "NOISE")).upper()
            if doc_type not in {"INVOICE", "PAYMENT_REQUEST", "NOISE"}:
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

    # Rule-based classification
    noise_hits = _count_matches(NOISE_PATTERNS, combined)
    if noise_hits >= 1:
        return {
            "type": "NOISE",
            "confidence": min(0.95, 0.7 + 0.1 * noise_hits),
            "reason": "noise_signals",
            "method": "rules",
        }

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

    attachment_names = " ".join([str(a.get("filename") or a.get("name") or "") for a in attachments]).lower()
    if attachment_names:
        if "invoice" in attachment_names or "bill" in attachment_names:
            invoice_score += 2
        invoice_score += 1

    if invoice_score >= 5 and invoice_score >= payment_score:
        return {
            "type": "INVOICE",
            "confidence": min(0.95, 0.6 + 0.05 * invoice_score),
            "reason": "invoice_signals",
            "method": "rules",
            "score": invoice_score,
        }

    if payment_score >= 4 and payment_score > invoice_score:
        return {
            "type": "PAYMENT_REQUEST",
            "confidence": min(0.9, 0.55 + 0.05 * payment_score),
            "reason": "payment_request_signals",
            "method": "rules",
            "score": payment_score,
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
