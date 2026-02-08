"""
Agent Reasoning Service

Implements chain-of-thought reasoning for invoice processing.
Instead of just extracting data, the agent reasons about the invoice
and explains its decisions.

Architecture: Part of the REASONING LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation with chain-of-thought prompts
- 2026-01-31: Integrated learning service into decision loop (closed feedback loop)
"""

import logging
import time
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps

from clearledgr.services.llm_multimodal import MultiModalLLMService
from clearledgr.services.learning import get_learning_service
from clearledgr.services.compounding_learning import get_learning_service as get_compounding_learning

logger = logging.getLogger(__name__)


# =============================================================================
# RETRY LOGIC WITH EXPONENTIAL BACKOFF
# =============================================================================

def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple = (Exception,),
):
    """
    Decorator for retrying functions with exponential backoff.
    
    Used for LLM calls that may fail transiently.
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_retries} failed for {func.__name__}: {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(f"All {max_retries} attempts failed for {func.__name__}: {e}")
            raise last_exception
        return wrapper
    return decorator


# =============================================================================
# CIRCUIT BREAKER FOR REPEATED FAILURES
# =============================================================================

class CircuitBreaker:
    """
    Circuit breaker to prevent repeated calls to failing services.
    
    States:
    - CLOSED: Normal operation, calls go through
    - OPEN: Service is failing, calls are blocked
    - HALF_OPEN: Testing if service recovered
    """
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"
    
    def can_proceed(self) -> bool:
        """Check if we should attempt the call."""
        if self.state == "CLOSED":
            return True
        elif self.state == "OPEN":
            # Check if recovery timeout has passed
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "HALF_OPEN"
                return True
            return False
        else:  # HALF_OPEN
            return True
    
    def record_success(self):
        """Record a successful call."""
        self.failures = 0
        self.state = "CLOSED"
    
    def record_failure(self):
        """Record a failed call."""
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            self.state = "OPEN"
            logger.warning(f"Circuit breaker OPEN after {self.failures} failures")


# Global circuit breaker for LLM service
_llm_circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)


@dataclass
class ReasoningFactor:
    """A single factor in the agent's reasoning."""
    factor: str
    score: float  # 0.0 to 1.0
    detail: str
    weight: float = 1.0


@dataclass
class AgentDecision:
    """The agent's decision with full reasoning."""
    decision: str  # "auto_approve", "send_for_approval", "flag_for_review", "reject"
    confidence: float
    summary: str  # Human-readable explanation
    factors: List[ReasoningFactor] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    extraction: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "confidence": self.confidence,
            "reasoning": {
                "summary": self.summary,
                "factors": [
                    {"factor": f.factor, "score": f.score, "detail": f.detail}
                    for f in self.factors
                ],
                "risks": self.risks,
                "recommendations": self.recommendations,
            },
            "extraction": self.extraction,
        }


class AgentReasoningService:
    """
    Chain-of-thought reasoning for invoice processing.
    
    Instead of:
        extract(email) -> {vendor, amount, ...}
    
    We do:
        reason(email, context) -> {
            extraction: {vendor, amount, ...},
            reasoning: {summary, factors, risks},
            decision: "auto_approve" | "send_for_approval" | ...
        }
    
    Learning Integration (2026-01-31):
    - Queries learned patterns during reasoning
    - Uses past corrections to improve confidence
    - Records pattern usage for feedback loop
    """
    
    # Confidence thresholds
    AUTO_APPROVE_THRESHOLD = 0.95
    APPROVAL_THRESHOLD = 0.75
    
    # Factor weights for confidence calculation (updated to include learned patterns)
    WEIGHTS = {
        "extraction_confidence": 0.25,
        "vendor_familiarity": 0.20,
        "pattern_match": 0.15,
        "learned_patterns": 0.20,  # NEW: Weight for learned patterns
        "amount_reasonableness": 0.10,
        "document_quality": 0.10,
    }
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.llm = MultiModalLLMService()
        self._learning = None
        self._compounding_learning = None
        self._used_patterns: List[str] = []  # Track patterns used for feedback
    
    @property
    def learning(self):
        """Lazy-load learning service."""
        if self._learning is None:
            self._learning = get_learning_service(self.organization_id)
        return self._learning
    
    @property
    def compounding_learning(self):
        """Lazy-load compounding learning service."""
        if self._compounding_learning is None:
            try:
                self._compounding_learning = get_compounding_learning()
            except Exception as e:
                logger.warning(f"Could not load compounding learning service: {e}")
        return self._compounding_learning
    
    def reason_about_invoice(
        self,
        text: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> AgentDecision:
        """
        Apply chain-of-thought reasoning to an invoice.
        
        Args:
            text: Email body text
            attachments: List of attachments with content_base64
            context: Additional context (vendor_history, recent_invoices, etc.)
        
        Returns:
            AgentDecision with extraction, reasoning, and decision
        """
        context = context or {}
        attachments = attachments or []
        
        # Step 1: Extract data with reasoning
        extraction = self._extract_with_reasoning(text, attachments)
        
        # Step 2: Gather context about vendor
        vendor = extraction.get("vendor", "Unknown")
        vendor_context = self._get_vendor_context(vendor)
        
        # Step 3: Calculate confidence factors
        factors = self._calculate_factors(extraction, vendor_context, context)
        
        # Step 4: Identify risks
        risks = self._identify_risks(extraction, vendor_context, context)
        
        # Step 5: Make decision
        confidence = self._calculate_confidence(factors)
        decision, summary = self._make_decision(confidence, factors, risks)
        
        # Step 6: Generate recommendations
        recommendations = self._generate_recommendations(decision, factors, risks)
        
        logger.info(
            f"Agent decision for {vendor}: {decision} "
            f"(confidence: {confidence:.2f}, factors: {len(factors)}, risks: {len(risks)})"
        )
        
        return AgentDecision(
            decision=decision,
            confidence=confidence,
            summary=summary,
            factors=factors,
            risks=risks,
            recommendations=recommendations,
            extraction=extraction,
        )
    
    def _extract_with_reasoning(
        self,
        text: str,
        attachments: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Extract invoice data using chain-of-thought prompt."""
        
        # Chain-of-thought prompt that asks LLM to reason step by step
        prompt = f"""You are a finance expert analyzing an invoice. Think step by step.

DOCUMENT:
{text[:3000]}

TASK: Extract invoice data and explain your reasoning.

Think through these steps:
1. IDENTIFY: What type of document is this? (Invoice, Receipt, Statement, Other)
2. VENDOR: Who is the sender/vendor? Look for company name, logo, letterhead.
3. AMOUNT: What is the total amount due? Look for "Total", "Amount Due", "Balance".
4. DATES: What are the invoice date and due date?
5. REFERENCE: What is the invoice number or reference?
6. CONFIDENCE: How confident are you in each extraction? Why?

Return JSON with this structure:
{{
  "document_type": "invoice|receipt|statement|other",
  "vendor": "Company Name",
  "total_amount": 123.45,
  "currency": "USD",
  "invoice_number": "INV-123",
  "invoice_date": "2026-01-15",
  "due_date": "2026-02-15",
  "line_items": [...],
  "extraction_confidence": 0.0-1.0,
  "reasoning": {{
    "document_type_reason": "Why you identified this document type",
    "vendor_reason": "How you identified the vendor",
    "amount_reason": "How you found the total amount",
    "confidence_reason": "Why your confidence is this level"
  }}
}}

Be precise. If uncertain about a field, say so in the reasoning."""

        # Check circuit breaker before calling LLM
        if not _llm_circuit_breaker.can_proceed():
            logger.warning("LLM circuit breaker OPEN - using fallback extraction")
            return self._fallback_extraction(text)
        
        try:
            result = self._extract_with_retry(text, attachments)
            _llm_circuit_breaker.record_success()
            
            # Add reasoning if not present
            if "reasoning" not in result:
                result["reasoning"] = {
                    "document_type_reason": "Extracted from document content",
                    "vendor_reason": f"Identified as {result.get('vendor', 'Unknown')}",
                    "amount_reason": f"Total: {result.get('total_amount', 'Unknown')}",
                    "confidence_reason": "Based on extraction quality",
                }
            
            return result
            
        except Exception as e:
            _llm_circuit_breaker.record_failure()
            logger.warning(f"LLM extraction failed after retries: {e}")
            return self._fallback_extraction(text, error=str(e))
    
    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=10.0)
    def _extract_with_retry(
        self,
        text: str,
        attachments: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """LLM extraction with retry logic."""
        return self.llm.extract_invoice(text, attachments)
    
    def _fallback_extraction(
        self,
        text: str,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Rule-based fallback extraction when LLM fails."""
        import re
        
        result = {
            "vendor": "Unknown",
            "total_amount": None,
            "extraction_confidence": 0.3,
            "reasoning": {"fallback": True, "error": error},
        }
        
        # Try to extract amount with regex
        amount_patterns = [
            r"total[:\s]+\$?([\d,]+\.?\d*)",
            r"amount due[:\s]+\$?([\d,]+\.?\d*)",
            r"balance[:\s]+\$?([\d,]+\.?\d*)",
            r"\$\s*([\d,]+\.?\d{2})",
        ]
        for pattern in amount_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    result["total_amount"] = float(match.group(1).replace(",", ""))
                    result["extraction_confidence"] = 0.5
                    break
                except ValueError:
                    pass
        
        # Try to extract invoice number
        inv_pattern = r"(?:invoice|inv|#)[:\s#]*([A-Z0-9-]+)"
        inv_match = re.search(inv_pattern, text, re.IGNORECASE)
        if inv_match:
            result["invoice_number"] = inv_match.group(1)
        
        return result
    
    def _original_extract_fallback(self, e):
        """Original fallback for backwards compatibility."""
        return {
            "vendor": "Unknown",
            "total_amount": None,
            "extraction_confidence": 0.3,
            "reasoning": {"error": str(e)},
            }
    
    def _get_vendor_context(self, vendor: str) -> Dict[str, Any]:
        """Get historical context about a vendor."""
        try:
            # Get GL suggestion (includes history)
            suggestion = self.learning.suggest_gl_code(vendor=vendor, amount=0)
            
            # Get vendor pattern
            pattern = self.learning.get_vendor_pattern(vendor) if hasattr(self.learning, 'get_vendor_pattern') else None
            
            return {
                "known_vendor": suggestion is not None and suggestion.get("confidence", 0) > 0.5,
                "gl_suggestion": suggestion,
                "invoice_count": suggestion.get("invoice_count", 0) if suggestion else 0,
                "typical_amount": pattern.get("typical_amount") if pattern else None,
                "pattern": pattern,
            }
        except Exception as e:
            logger.warning(f"Failed to get vendor context: {e}")
            return {"known_vendor": False}
    
    def _calculate_factors(
        self,
        extraction: Dict[str, Any],
        vendor_context: Dict[str, Any],
        additional_context: Dict[str, Any],
    ) -> List[ReasoningFactor]:
        """Calculate individual confidence factors."""
        factors = []
        
        # Factor 1: Extraction confidence
        extraction_conf = extraction.get("extraction_confidence", extraction.get("confidence", 0.5))
        factors.append(ReasoningFactor(
            factor="extraction_confidence",
            score=extraction_conf,
            detail=f"LLM extracted data with {extraction_conf*100:.0f}% confidence",
            weight=self.WEIGHTS["extraction_confidence"],
        ))
        
        # Factor 2: Vendor familiarity
        if vendor_context.get("known_vendor"):
            invoice_count = vendor_context.get("invoice_count", 0)
            familiarity = min(1.0, 0.5 + (invoice_count * 0.05))  # Max at 10+ invoices
            factors.append(ReasoningFactor(
                factor="vendor_familiarity",
                score=familiarity,
                detail=f"Known vendor with {invoice_count} previous invoices",
                weight=self.WEIGHTS["vendor_familiarity"],
            ))
        else:
            factors.append(ReasoningFactor(
                factor="vendor_familiarity",
                score=0.3,
                detail="New vendor - no previous history",
                weight=self.WEIGHTS["vendor_familiarity"],
            ))
        
        # Factor 3: Pattern match
        pattern = vendor_context.get("pattern")
        if pattern and extraction.get("total_amount"):
            typical = pattern.get("typical_amount", 0)
            current = extraction.get("total_amount", 0)
            if typical > 0 and current > 0:
                variance = abs(current - typical) / typical
                if variance <= 0.05:
                    pattern_score = 0.95
                    pattern_detail = f"Amount ${current:,.2f} matches typical ${typical:,.2f} (within 5%)"
                elif variance <= 0.20:
                    pattern_score = 0.7
                    pattern_detail = f"Amount ${current:,.2f} differs from typical ${typical:,.2f} by {variance*100:.0f}%"
                else:
                    pattern_score = 0.4
                    pattern_detail = f"Amount ${current:,.2f} significantly differs from typical ${typical:,.2f}"
            else:
                pattern_score = 0.5
                pattern_detail = "No amount pattern established"
        else:
            pattern_score = 0.5
            pattern_detail = "No historical pattern to compare"
        
        factors.append(ReasoningFactor(
            factor="pattern_match",
            score=pattern_score,
            detail=pattern_detail,
            weight=self.WEIGHTS["pattern_match"],
        ))
        
        # Factor 4: Amount reasonableness
        amount = extraction.get("total_amount")
        if amount is not None:
            if 0 < amount < 100000:  # Reasonable range
                amount_score = 0.9
                amount_detail = f"Amount ${amount:,.2f} is within normal range"
            elif amount >= 100000:
                amount_score = 0.5
                amount_detail = f"Large amount ${amount:,.2f} - may need extra review"
            else:
                amount_score = 0.3
                amount_detail = f"Unusual amount: ${amount:,.2f}"
        else:
            amount_score = 0.2
            amount_detail = "Could not extract amount"
        
        factors.append(ReasoningFactor(
            factor="amount_reasonableness",
            score=amount_score,
            detail=amount_detail,
            weight=self.WEIGHTS["amount_reasonableness"],
        ))
        
        # Factor 5: Document quality (based on extraction success)
        has_vendor = bool(extraction.get("vendor") and extraction.get("vendor") != "Unknown")
        has_amount = extraction.get("total_amount") is not None
        has_date = bool(extraction.get("due_date") or extraction.get("invoice_date"))
        has_number = bool(extraction.get("invoice_number"))
        
        fields_found = sum([has_vendor, has_amount, has_date, has_number])
        quality_score = fields_found / 4
        
        factors.append(ReasoningFactor(
            factor="document_quality",
            score=quality_score,
            detail=f"Extracted {fields_found}/4 key fields (vendor, amount, date, invoice#)",
            weight=self.WEIGHTS["document_quality"],
        ))
        
        # Factor 6: LEARNED PATTERNS (NEW - closes the feedback loop)
        # Query the compounding learning service for relevant patterns
        learned_score = 0.5  # Default neutral score
        learned_detail = "No learned patterns available"
        
        if self.compounding_learning:
            vendor = extraction.get("vendor", "")
            description = extraction.get("description", "") or extraction.get("invoice_number", "")
            
            try:
                # Get categorization hint from past corrections
                hint = self.compounding_learning.get_categorization_hint(vendor, description)
                
                if hint and hint.get("confidence", 0) > 0.3:
                    learned_score = min(0.95, 0.6 + hint["confidence"])
                    learned_detail = (
                        f"Learned pattern suggests GL code {hint.get('gl_code')} "
                        f"({hint.get('gl_name')}) with {hint['confidence']*100:.0f}% confidence"
                    )
                    # Track pattern for feedback
                    if hint.get("pattern_id"):
                        self._used_patterns.append(hint["pattern_id"])
                    
                    # Add suggested GL to extraction for downstream use
                    extraction["learned_gl_suggestion"] = {
                        "gl_code": hint.get("gl_code"),
                        "gl_name": hint.get("gl_name"),
                        "confidence": hint.get("confidence"),
                        "pattern_id": hint.get("pattern_id"),
                    }
                else:
                    # Check for matching patterns (for transaction matching)
                    patterns = self.compounding_learning.get_patterns_for_matching(
                        f"{vendor} {description}",
                        min_confidence=0.5
                    )
                    if patterns:
                        best_pattern = patterns[0]
                        learned_score = min(0.9, 0.5 + best_pattern.confidence * 0.4)
                        learned_detail = (
                            f"Found {len(patterns)} matching pattern(s), "
                            f"best confidence: {best_pattern.confidence*100:.0f}%"
                        )
                        self._used_patterns.append(best_pattern.pattern_id)
                    else:
                        learned_score = 0.4
                        learned_detail = "No matching patterns from prior corrections"
                        
            except Exception as e:
                logger.warning(f"Failed to query learned patterns: {e}")
                learned_detail = "Could not query learned patterns"
        
        factors.append(ReasoningFactor(
            factor="learned_patterns",
            score=learned_score,
            detail=learned_detail,
            weight=self.WEIGHTS["learned_patterns"],
        ))
        
        return factors
    
    def _calculate_confidence(self, factors: List[ReasoningFactor]) -> float:
        """Calculate overall confidence from weighted factors."""
        if not factors:
            return 0.5
        
        total_weight = sum(f.weight for f in factors)
        weighted_sum = sum(f.score * f.weight for f in factors)
        
        return weighted_sum / total_weight if total_weight > 0 else 0.5
    
    def _identify_risks(
        self,
        extraction: Dict[str, Any],
        vendor_context: Dict[str, Any],
        additional_context: Dict[str, Any],
    ) -> List[str]:
        """Identify potential risks or concerns."""
        risks = []
        
        # Risk: New vendor
        if not vendor_context.get("known_vendor"):
            risks.append("New vendor - no previous payment history")
        
        # Risk: Missing required fields
        if not extraction.get("total_amount"):
            risks.append("Could not extract invoice amount")
        if not extraction.get("vendor") or extraction.get("vendor") == "Unknown":
            risks.append("Could not identify vendor")
        
        # Risk: Large amount
        amount = extraction.get("total_amount", 0)
        if amount and amount > 10000:
            risks.append(f"Large invoice amount: ${amount:,.2f}")
        
        # Risk: Overdue
        due_date = extraction.get("due_date")
        if due_date:
            try:
                due = datetime.strptime(due_date, "%Y-%m-%d")
                if due < datetime.now():
                    days_overdue = (datetime.now() - due).days
                    risks.append(f"Invoice is {days_overdue} days overdue")
            except:
                pass
        
        # Risk: Amount variance
        pattern = vendor_context.get("pattern")
        if pattern and extraction.get("total_amount"):
            typical = pattern.get("typical_amount", 0)
            current = extraction.get("total_amount", 0)
            if typical > 0 and current > 0:
                variance = abs(current - typical) / typical
                if variance > 0.20:
                    risks.append(f"Amount differs {variance*100:.0f}% from typical ${typical:,.2f}")
        
        # Risk: Potential duplicate (from additional context)
        recent_invoices = additional_context.get("recent_invoices", [])
        for recent in recent_invoices:
            if (recent.get("vendor") == extraction.get("vendor") and
                recent.get("amount") == extraction.get("total_amount")):
                risks.append(f"Potential duplicate - similar invoice from {recent.get('date', 'recently')}")
                break
        
        return risks
    
    def _make_decision(
        self,
        confidence: float,
        factors: List[ReasoningFactor],
        risks: List[str],
    ) -> tuple[str, str]:
        """Make a decision based on confidence and risks."""
        
        # High-risk items always go for review
        high_risk_keywords = ["duplicate", "overdue", "Could not extract", "Could not identify"]
        has_high_risk = any(
            any(keyword in risk for keyword in high_risk_keywords)
            for risk in risks
        )
        
        if has_high_risk:
            summary = f"Flagged for review: {risks[0] if risks else 'Risk detected'}"
            return "flag_for_review", summary
        
        # Decision based on confidence
        if confidence >= self.AUTO_APPROVE_THRESHOLD:
            # Find the strongest factor for explanation
            strongest = max(factors, key=lambda f: f.score) if factors else None
            if strongest:
                summary = f"Auto-approved: {strongest.detail}"
            else:
                summary = f"Auto-approved: High confidence ({confidence*100:.0f}%)"
            return "auto_approve", summary
        
        elif confidence >= self.APPROVAL_THRESHOLD:
            # Find the weakest factor
            weakest = min(factors, key=lambda f: f.score) if factors else None
            if weakest:
                summary = f"Needs approval: {weakest.detail}"
            else:
                summary = f"Needs approval: Moderate confidence ({confidence*100:.0f}%)"
            return "send_for_approval", summary
        
        else:
            summary = f"Low confidence ({confidence*100:.0f}%) - manual review required"
            return "flag_for_review", summary
    
    def _generate_recommendations(
        self,
        decision: str,
        factors: List[ReasoningFactor],
        risks: List[str],
    ) -> List[str]:
        """Generate actionable recommendations."""
        recommendations = []
        
        if decision == "flag_for_review":
            recommendations.append("Verify invoice details manually before processing")
        
        # Low extraction confidence
        extraction_factor = next((f for f in factors if f.factor == "extraction_confidence"), None)
        if extraction_factor and extraction_factor.score < 0.7:
            recommendations.append("Consider requesting a clearer invoice copy from vendor")
        
        # New vendor
        familiarity_factor = next((f for f in factors if f.factor == "vendor_familiarity"), None)
        if familiarity_factor and familiarity_factor.score < 0.5:
            recommendations.append("Verify vendor details and payment information")
        
        # Large amount
        if any("Large" in risk for risk in risks):
            recommendations.append("Consider additional approval for large amount")
        
        # Recommend using learned pattern if available
        learned_factor = next((f for f in factors if f.factor == "learned_patterns"), None)
        if learned_factor and learned_factor.score >= 0.7:
            recommendations.append("Using learned pattern from prior corrections - verify if appropriate")
        
        return recommendations
    
    def record_decision_feedback(
        self,
        decision: AgentDecision,
        was_correct: bool,
        correction: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record feedback on a decision to close the learning loop.
        
        Called after user approves/rejects/corrects an agent decision.
        This is what makes the agent actually learn.
        
        Args:
            decision: The original agent decision
            was_correct: Whether the decision was correct
            correction: If incorrect, the corrected values
        """
        # Record pattern usage feedback
        for pattern_id in self._used_patterns:
            try:
                if self.compounding_learning:
                    self.compounding_learning.record_pattern_usage(pattern_id, was_correct)
                    logger.info(f"Recorded pattern usage: {pattern_id} -> {'success' if was_correct else 'failure'}")
            except Exception as e:
                logger.warning(f"Failed to record pattern usage: {e}")
        
        # Clear used patterns
        self._used_patterns = []
        
        # If there was a correction, record it for future learning
        if correction and self.compounding_learning:
            try:
                from uuid import uuid4
                correction_record = {
                    "correction_id": str(uuid4()),
                    "correction_type": "categorization",
                    "original_value": {
                        "gl_code": decision.extraction.get("learned_gl_suggestion", {}).get("gl_code"),
                        "decision": decision.decision,
                        "confidence": decision.confidence,
                    },
                    "corrected_value": correction,
                    "context": {
                        "vendor": decision.extraction.get("vendor"),
                        "amount": decision.extraction.get("total_amount"),
                        "description": decision.extraction.get("description"),
                    },
                }
                self.compounding_learning.record_correction(
                    correction_type="categorization",
                    original_value=correction_record["original_value"],
                    corrected_value=correction_record["corrected_value"],
                    user_email="system",
                    context=correction_record["context"],
                )
                logger.info(f"Recorded correction for learning: {correction}")
            except Exception as e:
                logger.warning(f"Failed to record correction: {e}")


# Convenience function
def get_agent(organization_id: str = "default") -> AgentReasoningService:
    """Get an agent reasoning service instance."""
    return AgentReasoningService(organization_id=organization_id)
