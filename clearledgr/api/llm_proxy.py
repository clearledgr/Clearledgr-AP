"""
Clearledgr LLM Proxy

AI-powered endpoints for:
- Exception explanations
- Email classification
- Document analysis
"""

from typing import Dict, List, Optional
import anyio
import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from clearledgr.api.deps import get_llm_service
from clearledgr.services.llm_multimodal import MultiModalLLMService

router = APIRouter(prefix="/llm", tags=["llm-proxy"])

class ExceptionContext(BaseModel):
    """Full context about an exception."""
    exception_type: str = Field(..., description="Type: no_match, amount_variance, date_mismatch, etc.")
    source_type: Optional[str] = Field(None, description="Source: gateway, bank, internal")
    vendor: Optional[str] = Field(None, description="Vendor or counterparty")
    transaction_id: Optional[str] = Field(None, description="Transaction identifier")
    amount: Optional[float] = Field(None, description="Transaction amount")
    currency: Optional[str] = Field(None, description="Currency code, e.g. EUR")
    date: Optional[str] = Field(None, description="Transaction date")
    reference: Optional[str] = Field(None, description="Reference or memo")
    description: Optional[str] = Field(None, description="Narrative description")
    has_near_amount_match: Optional[bool] = Field(False, description="Is there a near match by amount?")
    has_near_date_match: Optional[bool] = Field(False, description="Is there a near match by date?")
    nearest_amount_diff_pct: Optional[float] = Field(None, description="Nearest match amount diff %")
    nearest_days_diff: Optional[int] = Field(None, description="Nearest match days difference")
    tolerance_pct: Optional[float] = Field(0.5, description="Configured amount tolerance %")
    date_window_days: Optional[int] = Field(3, description="Configured date window")

    model_config = {"extra": "allow"}


class ExplainExceptionRequest(BaseModel):
    """Request for exception explanation."""
    exception: ExceptionContext
    context: Optional[str] = Field(None, description="Additional context")


class ExplanationResponse(BaseModel):
    """LLM-generated explanation."""
    explanation: str = Field(..., description="Explanation text")
    suggested_action: str = Field(..., description="Recommended action")
    confidence: float = Field(0.9, description="Confidence score")


@router.post("/explain-exception", response_model=ExplanationResponse)
async def explain_exception(
    request: ExplainExceptionRequest,
    llm: MultiModalLLMService = Depends(get_llm_service),
):
    """
    Generate explanation for a reconciliation exception.
    """
    exception = request.exception

    prompt = build_exception_prompt(exception, request.context)

    try:
        explanation = await call_llm(prompt, llm)
        return ExplanationResponse(
            explanation=explanation.get("explanation", generate_fallback_exception_explanation(exception)),
            suggested_action=explanation.get("suggested_action", generate_fallback_exception_action(exception)),
            confidence=0.9
        )
    except Exception as e:
        # Fallback to rule-based explanation
        return ExplanationResponse(
            explanation=generate_fallback_exception_explanation(exception),
            suggested_action=generate_fallback_exception_action(exception),
            confidence=0.7
        )


@router.post("/explain-batch")
async def explain_batch(
    exceptions: List[ExceptionContext],
    llm: MultiModalLLMService = Depends(get_llm_service),
):
    """
    Generate explanations for multiple exceptions.
    More efficient than calling explain-exception multiple times.
    """
    results = []
    
    llm_available = bool(getattr(llm, "anthropic_key", None) or getattr(llm, "mistral_key", None))

    for exception in exceptions[:20]:  # Limit batch size
        try:
            if llm_available:
                prompt = build_exception_prompt(exception, None)
                explanation = await call_llm(prompt, llm)
                results.append({
                    "explanation": explanation.get("explanation", generate_fallback_exception_explanation(exception)),
                    "suggested_action": explanation.get("suggested_action", generate_fallback_exception_action(exception)),
                    "confidence": 0.85
                })
            else:
                results.append({
                    "explanation": generate_fallback_exception_explanation(exception),
                    "suggested_action": generate_fallback_exception_action(exception),
                    "confidence": 0.75
                })
        except Exception:
            results.append({
                "explanation": generate_fallback_exception_explanation(exception),
                "suggested_action": generate_fallback_exception_action(exception),
                "confidence": 0.6
            })
    
    return {"explanations": results}


async def call_llm(prompt: str, llm: MultiModalLLMService) -> Dict:
    """Call shared LLM service and parse JSON response."""
    return await anyio.to_thread.run_sync(llm.generate_json, prompt, None)


def build_exception_prompt(exception: ExceptionContext, context: Optional[str]) -> str:
    amount_label = format_amount(exception.amount, exception.currency)
    details = [
        f"Exception Type: {exception.exception_type}",
        f"Source: {exception.source_type or 'unknown'}",
    ]
    if exception.vendor:
        details.append(f"Vendor: {exception.vendor}")
    if exception.transaction_id:
        details.append(f"Transaction ID: {exception.transaction_id}")
    if amount_label:
        details.append(f"Amount: {amount_label}")
    if exception.date:
        details.append(f"Date: {exception.date}")
    if exception.reference:
        details.append(f"Reference: {exception.reference}")
    if exception.description:
        details.append(f"Description: {exception.description}")
    if exception.nearest_amount_diff_pct is not None:
        details.append(
            f"Nearest Amount Difference: {exception.nearest_amount_diff_pct}% "
            f"(tolerance is {exception.tolerance_pct}%)"
        )
    if exception.nearest_days_diff is not None:
        details.append(
            f"Nearest Date Difference: {exception.nearest_days_diff} days "
            f"(window is {exception.date_window_days} days)"
        )
    if exception.has_near_amount_match is not None:
        details.append(f"Near Amount Match Found: {exception.has_near_amount_match}")
    if exception.has_near_date_match is not None:
        details.append(f"Near Date Match Found: {exception.has_near_date_match}")
    if context:
        details.append(f"Context: {context}")

    detail_block = "\n".join(f"- {item}" for item in details)

    return (
        "You are a financial reconciliation expert. Generate an explanation for this exception.\n\n"
        "EXCEPTION DETAILS:\n"
        f"{detail_block}\n\n"
        "Generate:\n"
        "1. A clear explanation of why this transaction couldn't be matched.\n"
        "2. A specific suggested action.\n\n"
        "Format your response as JSON:\n"
        '{"explanation": "...", "suggested_action": "..."}'
    )


def format_amount(amount: Optional[float], currency: Optional[str]) -> Optional[str]:
    if amount is None:
        return None
    try:
        numeric = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    if currency:
        return f"{currency} {numeric:,.2f}"
    return f"{numeric:,.2f}"


def generate_fallback_exception_explanation(exception: ExceptionContext) -> str:
    """Generate rule-based explanation when LLM unavailable."""
    amount_label = format_amount(exception.amount, exception.currency) or "an unknown amount"
    tx_label = exception.transaction_id or "this transaction"
    source_label = exception.source_type or "source system"

    tolerance = exception.tolerance_pct if exception.tolerance_pct is not None else 0.5
    date_window = exception.date_window_days if exception.date_window_days is not None else 3
    amount_diff = exception.nearest_amount_diff_pct
    days_diff = exception.nearest_days_diff
    amount_diff_label = f"{amount_diff:.1f}" if amount_diff is not None else "unknown"
    days_diff_label = str(days_diff) if days_diff is not None else "unknown"

    if exception.exception_type in {"no_match", "unmatched"}:
        if exception.has_near_amount_match and not exception.has_near_date_match:
            return (
                f"Transaction {tx_label} for {amount_label} could not be matched. "
                f"A potential match was found with a similar amount "
                f"(within {amount_diff_label}% difference) "
                f"but the dates are {days_diff_label} days apart, "
                f"exceeding the {date_window}-day window."
            )
        elif exception.has_near_date_match and not exception.has_near_amount_match:
            return (
                f"Transaction {tx_label} for {amount_label} could not be matched. "
                f"A potential match was found within the date window, but the amount differs by "
                f"{amount_diff_label}%, exceeding the "
                f"{tolerance}% tolerance."
            )
        elif exception.has_near_amount_match and exception.has_near_date_match:
            return (
                f"Transaction {tx_label} for {amount_label} could not be matched. "
                f"Near matches exist but fall outside configured tolerances "
                f"(amount: {tolerance}%, date: {date_window} days)."
            )
        else:
            return (
                f"Transaction {tx_label} for {amount_label} from {source_label} "
                f"has no matching entry in the other systems within the configured tolerances."
            )
    
    return f"Exception detected for {tx_label}: {exception.exception_type}."


def generate_fallback_exception_action(exception: ExceptionContext) -> str:
    """Generate rule-based action when LLM unavailable."""

    if exception.has_near_amount_match and not exception.has_near_date_match:
        return "Verify transaction dates in both systems. The amounts match but dates differ."
    
    if exception.has_near_date_match and not exception.has_near_amount_match:
        return "Check for partial payments, adjustments, or data entry errors. Dates match but amounts differ."
    
    if exception.source_type == "gateway":
        return "Verify the payment was received and recorded in bank statement and internal ledger."
    
    if exception.source_type == "bank":
        return "Check if this bank transaction corresponds to a different gateway transaction or internal entry."
    
    if exception.source_type == "internal":
        return "Review journal entry source documentation and verify against gateway and bank records."
    
    return "Review transaction and investigate discrepancy with source systems."


# --------------------------
# EMAIL CLASSIFICATION
# --------------------------

class EmailClassifyRequest(BaseModel):
    """Request to classify an email."""
    sender: str = Field(..., description="Email sender address")
    subject: str = Field(..., description="Email subject line")
    snippet: Optional[str] = Field(None, description="Email body preview/snippet")


class EmailClassification(BaseModel):
    """AI-generated email classification."""
    isFinance: bool = Field(..., description="Is this a financial document?")
    type: Optional[str] = Field(None, description="Document type: invoice, receipt, statement, etc.")
    confidence: float = Field(..., description="Confidence score 0.0-1.0")
    reason: str = Field(..., description="Explanation for classification")


EMAIL_CLASSIFICATION_PROMPT = """You are an expert at identifying financial documents in email.

Your task: Determine if this email IS or CONTAINS a financial document.

FINANCIAL DOCUMENTS (classify as finance):
- Invoice with invoice number (e.g., "Invoice #INV-2024-001")
- Receipt for a completed transaction
- Bank/Account Statement
- Payment confirmation for an actual transaction
- Expense report
- Payroll/Pay stub

NOT FINANCIAL DOCUMENTS (do NOT classify as finance):
- Marketing emails (even from Stripe, Ramp, PayPal, etc.)
- Product announcements, feature updates
- Newsletters, tips, guides, "how to" content
- Promotional offers, discounts, "save X%"
- Webinar invitations, event announcements
- Account security alerts, password resets
- Welcome emails, onboarding content
- Emails with "unsubscribe" links are usually marketing

KEY INSIGHT: Companies like Stripe, Ramp, Brex send BOTH financial documents AND marketing.
The sender alone does NOT determine if it's a financial document.
Look at the CONTENT of the email.

EMAIL TO CLASSIFY:
From: {sender}
Subject: {subject}
Preview: {snippet}

Respond with ONLY valid JSON (no markdown, no explanation):
{{"isFinance": true/false, "type": "invoice"|"receipt"|"statement"|"payment"|"expense"|"payroll"|null, "confidence": 0.0-1.0, "reason": "brief explanation"}}"""


@router.post("/classify", response_model=EmailClassification)
async def classify_email(
    request: EmailClassifyRequest,
    llm: MultiModalLLMService = Depends(get_llm_service),
):
    """
    Classify an email as financial document or not using AI.
    
    This uses LLM to understand context and intent, not hardcoded patterns.
    Marketing emails from financial companies are correctly identified as non-finance.
    Falls back to rule-based classification if no LLM API keys are configured.
    """
    # Check if LLM is available (has API keys configured)
    llm_available = bool(getattr(llm, "anthropic_key", None) or getattr(llm, "mistral_key", None))
    
    if llm_available:
        prompt = EMAIL_CLASSIFICATION_PROMPT.format(
            sender=request.sender or "Unknown",
            subject=request.subject or "No subject",
            snippet=(request.snippet or "")[:500],
        )
        
        try:
            # Try AI classification
            result = await anyio.to_thread.run_sync(llm.generate_json, prompt, None)
            
            if result and isinstance(result, dict):
                return EmailClassification(
                    isFinance=result.get("isFinance", False),
                    type=result.get("type"),
                    confidence=float(result.get("confidence", 0.5)),
                    reason=result.get("reason", "AI classification"),
                )
        except Exception as e:
            print(f"[LLM Classify] AI classification failed: {e}")
    
    # Fallback to simple heuristics (no LLM keys or LLM failed)
    return fallback_email_classification(request.subject, request.sender, request.snippet)


def fallback_email_classification(
    subject: str, 
    sender: str, 
    snippet: Optional[str]
) -> EmailClassification:
    """
    Simple fallback classification when AI is unavailable.
    Intentionally conservative - when in doubt, don't classify.
    """
    subject_lower = (subject or "").lower()
    snippet_lower = (snippet or "").lower()
    combined = subject_lower + " " + snippet_lower
    
    # Marketing signals - if present, NOT a financial document
    marketing_signals = [
        "unsubscribe", "view in browser", "email preferences",
        "just got easier", "without the headache", "in minutes", "made easy",
        "tips", "tricks", "guide", "how to", "learn how", "best practices",
        "new feature", "introducing", "announcing", "product update",
        "webinar", "join us", "register now", "sign up", "free trial",
        "newsletter", "digest", "roundup", "highlights",
        "save", "discount", "% off", "promo", "deal", "offer",
        "welcome to", "thanks for joining", "get started",
    ]
    
    for signal in marketing_signals:
        if signal in combined:
            return EmailClassification(
                isFinance=False,
                type=None,
                confidence=0.85,
                reason=f"Marketing signal detected: '{signal}'",
            )
    
    # Clear financial document patterns
    import re
    
    # Invoice with number
    if re.search(r'\binvoice\s*#?\s*[A-Z0-9-]{3,}', subject, re.IGNORECASE):
        return EmailClassification(
            isFinance=True,
            type="invoice",
            confidence=0.85,
            reason="Invoice number in subject",
        )
    
    # Receipt with transaction context
    if re.search(r'\b(receipt|payment\s+received)\s+(for|from)\b', subject_lower):
        return EmailClassification(
            isFinance=True,
            type="receipt",
            confidence=0.80,
            reason="Receipt/payment notification",
        )
    
    # Bank statement
    if re.search(r'\b(bank|account)\s*statement\b', subject_lower):
        return EmailClassification(
            isFinance=True,
            type="statement",
            confidence=0.85,
            reason="Statement in subject",
        )
    
    # Your X statement is ready
    if re.search(r'\byour\s+\w+\s+statement\s+is\s+(ready|available)\b', subject_lower):
        return EmailClassification(
            isFinance=True,
            type="statement",
            confidence=0.80,
            reason="Statement ready notification",
        )
    
    # Default: don't classify as finance
    return EmailClassification(
        isFinance=False,
        type=None,
        confidence=0.6,
        reason="No clear financial document indicators",
    )
