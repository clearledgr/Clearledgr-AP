"""
LLM Service for Clearledgr Reconciliation v1

Provides LLM-powered variance explanations for reconciliation exceptions.
"""
from typing import Dict, Optional
from clearledgr.services.llm_multimodal import MultiModalLLMService


def generate_variance_explanation(facts: Dict) -> Dict[str, str]:
    """
    Generate an explanation for why a transaction couldn't reconcile using LLM.
    
    Args:
        facts: Dict containing transaction facts, e.g.:
              {
                  "source": "gateway",
                  "txn_id": "txn_001",
                  "amount": 100.0,
                  "date": "2025-11-05",
                  "status": "failed",
                  "reason": "no_match"  # or "amount_mismatch", "date_mismatch", etc.
              }
    
    Returns:
        Dict with:
        - "reason": str - Machine-readable reason code (e.g., "no_counterparty", "amount_mismatch")
        - "llm_explanation": str - Human-readable explanation from LLM
        - "suggested_action": str - Suggested action to resolve the issue
    """
    llm = MultiModalLLMService()
    llm_available = bool(getattr(llm, "anthropic_key", None) or getattr(llm, "mistral_key", None))

    if not llm_available:
        return _rule_based_explanation(facts)
    
    try:
        prompt = _build_explanation_prompt(facts)
        result = llm.generate_json(prompt)

        explanation = (
            result.get("explanation")
            or result.get("llm_explanation")
            or result.get("reason")
            or ""
        )
        suggested_action = result.get("suggested_action") or "Review transaction details"

        reason_code = result.get("reason_code") or result.get("reason") or facts.get("reason", "no_match")
        reason_code = _normalize_reason_code(reason_code, explanation)

        return {
            "reason": reason_code,
            "llm_explanation": explanation,
            "suggested_action": suggested_action,
        }
    
    except Exception as e:
        # On error, fall back to rule-based explanation
        return _rule_based_explanation(facts)


def _build_explanation_prompt(facts: Dict) -> str:
    """Build the prompt for LLM explanation."""
    source = facts.get("source", "unknown")
    txn_id = facts.get("txn_id") or facts.get("bank_txn_id") or facts.get("internal_id", "N/A")
    amount = facts.get("amount") or facts.get("net_amount", "N/A")
    date = facts.get("date", "N/A")
    status = facts.get("status", "")
    reason = facts.get("reason", "no_match")
    
    prompt = f"""You are a financial reconciliation expert. Explain why this transaction failed to reconcile and suggest a fix.

Transaction Details:
- Source: {source}
- Transaction ID: {txn_id}
- Amount: {amount}
- Date: {date}
- Status: {status}
- Failure Reason: {reason}

Provide JSON with:
- explanation: clear reason why this transaction couldn't be matched
- suggested_action: best next step for the finance team
- reason_code: one of [no_match, amount_mismatch, date_mismatch, timing_difference, no_counterparty]

Return only valid JSON."""
    
    return prompt


def _normalize_reason_code(reason_code: Optional[str], explanation: str) -> str:
    base = (reason_code or "").lower().strip()
    explanation_text = (explanation or "").lower()

    if "amount" in explanation_text or "mismatch" in explanation_text:
        return "amount_mismatch"
    if "date" in explanation_text or "timing" in explanation_text:
        return "timing_difference"
    if "counterparty" in explanation_text or "no match" in explanation_text:
        return "no_counterparty"

    if base in {"amount_mismatch", "date_mismatch", "timing_difference", "no_counterparty", "no_match"}:
        return base

    return "no_match"


def _rule_based_explanation(facts: Dict) -> Dict[str, str]:
    """Generate a rule-based explanation when LLM is unavailable."""
    source = facts.get("source", "unknown")
    reason = facts.get("reason", "no_match")
    status = facts.get("status", "")
    amount = facts.get("amount") or facts.get("net_amount")
    
    # Build reason based on failure type
    if reason == "amount_mismatch":
        reason_text = f"Amount mismatch: Transaction amount ({amount}) does not match any counterparty transaction within tolerance."
        action = "Verify the transaction amount and check for fees or adjustments that may affect the net amount."
    elif reason == "date_mismatch":
        reason_text = f"Date mismatch: Transaction date is outside the allowed matching window."
        action = "Check if the transaction date is correct and verify the date window configuration."
    elif status == "failed":
        reason_text = f"Transaction failed: Status is '{status}', so it may not have completed successfully."
        action = "Verify transaction status and check if the transaction was actually processed."
    elif reason == "no_match":
        reason_text = f"No matching transaction found in counterparty data for this {source} transaction."
        action = "Verify that the corresponding transaction exists in the other systems and check for timing delays."
    else:
        reason_text = f"Unable to reconcile {source} transaction: {reason}"
        action = "Review transaction details and verify all fields match expected values."
    
    # Map to machine-readable reason codes
    reason_code = facts.get("reason", "no_match")
    if reason == "amount_mismatch":
        reason_code = "amount_mismatch"
    elif reason == "date_mismatch":
        reason_code = "timing_difference"
    elif reason == "no_match":
        reason_code = "no_counterparty"
    
    return {
        "reason": reason_code,
        "llm_explanation": reason_text,  # Human-readable explanation
        "suggested_action": action
    }
