"""
Enhanced AI Services for Clearledgr v1

Leverages foundation models (Anthropic Claude, OpenAI GPT, Mistral) for:
1. Intelligent GL Categorization - Few-shot classification
2. Anomaly Detection - Pattern analysis for outliers
3. Pattern Generalization - Learn and apply matching patterns
4. Contextual Match Confidence - LLM-adjusted scoring
5. Intelligent Exception Routing - Triage and assignment

No fine-tuning required. Uses prompt engineering and in-context learning.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

from clearledgr.services.llm_multimodal import MultiModalLLMService


class AnomalySeverity(Enum):
    """Anomaly severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class ExceptionPriority(Enum):
    """Exception priority for routing."""
    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class CategorizationResult:
    """Result of GL categorization."""
    gl_code: str
    gl_name: str
    confidence: float
    reasoning: str
    alternative_codes: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class AnomalyResult:
    """Result of anomaly detection."""
    is_anomaly: bool
    severity: AnomalySeverity
    anomaly_type: Optional[str]
    explanation: str
    suggested_action: str
    historical_context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PatternMatchResult:
    """Result of pattern-based matching."""
    matched_transaction_id: Optional[str]
    confidence: float
    pattern_used: str
    reasoning: str
    is_generalized: bool  # True if pattern was generalized from examples


@dataclass
class ConfidenceAdjustment:
    """Result of LLM confidence adjustment."""
    original_score: float
    adjusted_score: float
    should_match: bool
    reasoning: str
    factors: List[str] = field(default_factory=list)


@dataclass
class RoutingDecision:
    """Result of exception routing."""
    assignee: str
    escalate_to: Optional[str]
    priority: ExceptionPriority
    reasoning: str
    estimated_resolution_time: str
    suggested_actions: List[str] = field(default_factory=list)


class EnhancedAIService:
    """
    Enhanced AI service using foundation models for intelligent finance operations.
    
    All methods gracefully fall back to rule-based logic if LLM is unavailable.
    """
    
    def __init__(self, llm: Optional[MultiModalLLMService] = None):
        self.llm = llm or MultiModalLLMService()
        self._has_llm = bool(
            getattr(self.llm, "anthropic_key", None) or 
            getattr(self.llm, "mistral_key", None) or
            os.getenv("OPENAI_API_KEY")
        )
    
    # =========================================================================
    # 1. INTELLIGENT GL CATEGORIZATION
    # =========================================================================
    
    def categorize_transaction(
        self,
        description: str,
        vendor: str,
        amount: float,
        gl_accounts: List[Dict[str, Any]],
        historical_examples: Optional[List[Dict[str, Any]]] = None,
    ) -> CategorizationResult:
        """
        Categorize a transaction to the appropriate GL account using LLM.
        
        Args:
            description: Transaction description
            vendor: Vendor/counterparty name
            amount: Transaction amount
            gl_accounts: Available GL accounts with codes and names
            historical_examples: Past categorizations for few-shot learning
            
        Returns:
            CategorizationResult with GL code, confidence, and reasoning
        """
        if not self._has_llm:
            return self._rule_based_categorization(description, vendor, gl_accounts)
        
        try:
            prompt = self._build_categorization_prompt(
                description, vendor, amount, gl_accounts, historical_examples or []
            )
            result = self.llm.generate_json(prompt)
            
            return CategorizationResult(
                gl_code=result.get("gl_code", "6900"),
                gl_name=result.get("gl_name", "Other Expenses"),
                confidence=min(1.0, float(result.get("confidence", 0.5))),
                reasoning=result.get("reasoning", ""),
                alternative_codes=result.get("alternatives", []),
            )
        except Exception as e:
            return self._rule_based_categorization(description, vendor, gl_accounts)
    
    def _build_categorization_prompt(
        self,
        description: str,
        vendor: str,
        amount: float,
        gl_accounts: List[Dict[str, Any]],
        examples: List[Dict[str, Any]],
    ) -> str:
        """Build the categorization prompt with few-shot examples."""
        accounts_text = "\n".join([
            f"- {acc.get('code')}: {acc.get('name')}" + 
            (f" (keywords: {', '.join(acc.get('keywords', []))})" if acc.get('keywords') else "")
            for acc in gl_accounts[:20]  # Limit to avoid token overflow
        ])
        
        examples_text = ""
        if examples:
            examples_text = "\n\nHistorical categorizations:\n" + "\n".join([
                f"- \"{ex.get('description')}\" from {ex.get('vendor')} → {ex.get('gl_code')} ({ex.get('gl_name')})"
                for ex in examples[:10]  # Limit examples
            ])
        
        return f"""You are an expert finance categorization assistant.

Available GL Accounts:
{accounts_text}
{examples_text}

Categorize this transaction:
- Description: {description}
- Vendor: {vendor}
- Amount: €{amount:,.2f}

Return JSON with:
- gl_code: The best matching GL code
- gl_name: The GL account name
- confidence: Your confidence 0.0-1.0
- reasoning: Brief explanation (1-2 sentences)
- alternatives: Array of up to 2 alternative codes if uncertain

Return only valid JSON."""
    
    def _rule_based_categorization(
        self,
        description: str,
        vendor: str,
        gl_accounts: List[Dict[str, Any]],
    ) -> CategorizationResult:
        """Fallback rule-based categorization."""
        text = f"{description} {vendor}".lower()
        best_match = None
        best_score = 0
        
        for account in gl_accounts:
            score = 0
            for keyword in account.get("keywords", []):
                if keyword.lower() in text:
                    score += 1
            if score > best_score:
                best_score = score
                best_match = account
        
        if best_match:
            return CategorizationResult(
                gl_code=best_match.get("code", "6900"),
                gl_name=best_match.get("name", "Other Expenses"),
                confidence=min(0.5 + best_score * 0.1, 0.85),
                reasoning=f"Matched {best_score} keywords",
                alternative_codes=[],
            )
        
        return CategorizationResult(
            gl_code="6900",
            gl_name="Other Expenses",
            confidence=0.3,
            reasoning="No keyword matches, defaulting to Other Expenses",
            alternative_codes=[],
        )
    
    # =========================================================================
    # 2. ANOMALY DETECTION
    # =========================================================================
    
    def detect_anomaly(
        self,
        transaction: Dict[str, Any],
        historical_transactions: List[Dict[str, Any]],
        vendor_history: Optional[Dict[str, Any]] = None,
    ) -> AnomalyResult:
        """
        Detect if a transaction is anomalous based on historical patterns.
        
        Args:
            transaction: Current transaction to analyze
            historical_transactions: Past transactions for context
            vendor_history: Aggregated vendor statistics (avg, range, frequency)
            
        Returns:
            AnomalyResult with severity and explanation
        """
        if not self._has_llm:
            return self._rule_based_anomaly_detection(transaction, historical_transactions)
        
        try:
            prompt = self._build_anomaly_prompt(
                transaction, historical_transactions, vendor_history
            )
            result = self.llm.generate_json(prompt)
            
            severity_map = {
                "critical": AnomalySeverity.CRITICAL,
                "high": AnomalySeverity.HIGH,
                "medium": AnomalySeverity.MEDIUM,
                "low": AnomalySeverity.LOW,
            }
            
            return AnomalyResult(
                is_anomaly=result.get("is_anomaly", False),
                severity=severity_map.get(
                    result.get("severity", "none").lower(), 
                    AnomalySeverity.NONE
                ),
                anomaly_type=result.get("anomaly_type"),
                explanation=result.get("explanation", ""),
                suggested_action=result.get("suggested_action", "Review transaction"),
                historical_context=result.get("context", {}),
            )
        except Exception as e:
            return self._rule_based_anomaly_detection(transaction, historical_transactions)
    
    def _build_anomaly_prompt(
        self,
        transaction: Dict[str, Any],
        history: List[Dict[str, Any]],
        vendor_history: Optional[Dict[str, Any]],
    ) -> str:
        """Build the anomaly detection prompt."""
        vendor = transaction.get("vendor") or transaction.get("counterparty") or "Unknown"
        amount = transaction.get("amount", 0)
        date_str = transaction.get("date", "Unknown")
        description = transaction.get("description", "")
        
        # Build historical context
        vendor_txns = [t for t in history if 
                       (t.get("vendor") or t.get("counterparty", "")).lower() == vendor.lower()]
        
        if vendor_history:
            hist_context = f"""
Vendor historical statistics:
- Average amount: €{vendor_history.get('avg_amount', 0):,.2f}
- Typical range: €{vendor_history.get('min_amount', 0):,.2f} - €{vendor_history.get('max_amount', 0):,.2f}
- Typical frequency: {vendor_history.get('frequency', 'Unknown')}
- Last 6 transactions: {', '.join([f"€{t.get('amount', 0):,.2f}" for t in vendor_txns[:6]])}"""
        elif vendor_txns:
            amounts = [t.get("amount", 0) for t in vendor_txns]
            hist_context = f"""
Vendor transaction history (last {len(vendor_txns)} transactions):
- Amounts: {', '.join([f"€{a:,.2f}" for a in amounts[:6]])}
- Average: €{sum(amounts)/len(amounts):,.2f}
- Range: €{min(amounts):,.2f} - €{max(amounts):,.2f}"""
        else:
            hist_context = "\nNo previous transactions from this vendor."
        
        return f"""You are a financial anomaly detection expert.

Analyze this transaction for anomalies:
- Vendor: {vendor}
- Amount: €{amount:,.2f}
- Date: {date_str}
- Description: {description}
{hist_context}

Check for:
1. Amount anomalies (significantly higher/lower than typical)
2. Timing anomalies (unexpected frequency or timing)
3. Pattern anomalies (different from usual transaction type)
4. Duplicate indicators (similar recent transaction)

Return JSON with:
- is_anomaly: boolean
- severity: "critical" | "high" | "medium" | "low" | "none"
- anomaly_type: "amount_spike" | "amount_drop" | "timing" | "frequency" | "duplicate" | "new_vendor" | null
- explanation: Clear explanation of why this is/isn't anomalous
- suggested_action: What the finance team should do
- context: Any relevant context used for the decision

Return only valid JSON."""
    
    def _rule_based_anomaly_detection(
        self,
        transaction: Dict[str, Any],
        history: List[Dict[str, Any]],
    ) -> AnomalyResult:
        """Fallback rule-based anomaly detection."""
        amount = abs(float(transaction.get("amount", 0)))
        vendor = (transaction.get("vendor") or transaction.get("counterparty") or "").lower()
        
        # Get vendor history
        vendor_txns = [t for t in history if 
                       (t.get("vendor") or t.get("counterparty", "")).lower() == vendor]
        
        if not vendor_txns:
            return AnomalyResult(
                is_anomaly=True,
                severity=AnomalySeverity.MEDIUM,
                anomaly_type="new_vendor",
                explanation="First transaction from this vendor",
                suggested_action="Verify vendor is legitimate and approved",
            )
        
        amounts = [abs(float(t.get("amount", 0))) for t in vendor_txns]
        avg_amount = sum(amounts) / len(amounts)
        max_amount = max(amounts)
        
        # Check for amount spike
        if amount > avg_amount * 3:
            severity = AnomalySeverity.HIGH if amount > avg_amount * 5 else AnomalySeverity.MEDIUM
            return AnomalyResult(
                is_anomaly=True,
                severity=severity,
                anomaly_type="amount_spike",
                explanation=f"Amount is {amount/avg_amount:.1f}x higher than average (€{avg_amount:,.2f})",
                suggested_action="Verify transaction and check for duplicate charges",
            )
        
        return AnomalyResult(
            is_anomaly=False,
            severity=AnomalySeverity.NONE,
            anomaly_type=None,
            explanation="Transaction appears normal based on historical patterns",
            suggested_action="No action required",
        )
    
    # =========================================================================
    # 3. PATTERN GENERALIZATION
    # =========================================================================
    
    def find_pattern_match(
        self,
        unmatched_transaction: Dict[str, Any],
        candidate_transactions: List[Dict[str, Any]],
        learned_patterns: List[Dict[str, Any]],
    ) -> PatternMatchResult:
        """
        Use LLM to generalize learned patterns and find matches.
        
        Args:
            unmatched_transaction: Transaction to find match for
            candidate_transactions: Potential matches
            learned_patterns: Historical successful match patterns
            
        Returns:
            PatternMatchResult with matched transaction and reasoning
        """
        if not self._has_llm or not candidate_transactions:
            return self._rule_based_pattern_match(
                unmatched_transaction, candidate_transactions, learned_patterns
            )
        
        try:
            prompt = self._build_pattern_match_prompt(
                unmatched_transaction, candidate_transactions, learned_patterns
            )
            result = self.llm.generate_json(prompt)
            
            return PatternMatchResult(
                matched_transaction_id=result.get("matched_id"),
                confidence=min(1.0, float(result.get("confidence", 0))),
                pattern_used=result.get("pattern_used", ""),
                reasoning=result.get("reasoning", ""),
                is_generalized=result.get("is_generalized", False),
            )
        except Exception as e:
            return self._rule_based_pattern_match(
                unmatched_transaction, candidate_transactions, learned_patterns
            )
    
    def _build_pattern_match_prompt(
        self,
        unmatched: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        patterns: List[Dict[str, Any]],
    ) -> str:
        """Build the pattern matching prompt."""
        # Format learned patterns
        patterns_text = ""
        if patterns:
            patterns_text = "\n\nLearned matching patterns:\n" + "\n".join([
                f"- Bank: \"{p.get('bank_pattern')}\" ↔ Gateway: \"{p.get('gateway_pattern')}\" (confidence: {p.get('confidence', 0):.0%})"
                for p in patterns[:10]
            ])
        
        # Format candidates
        candidates_text = "\n".join([
            f"{i+1}. ID: {c.get('id') or c.get('transaction_id')}, "
            f"Amount: €{c.get('amount', 0):,.2f}, "
            f"Date: {c.get('date')}, "
            f"Desc: \"{c.get('description', '')[:50]}\""
            for i, c in enumerate(candidates[:10])
        ])
        
        return f"""You are a transaction matching expert. Use learned patterns to find matches.
{patterns_text}

Unmatched transaction:
- Source: {unmatched.get('source', 'bank')}
- ID: {unmatched.get('id') or unmatched.get('transaction_id')}
- Amount: €{unmatched.get('amount', 0):,.2f}
- Date: {unmatched.get('date')}
- Description: "{unmatched.get('description', '')}"

Candidate transactions to match against:
{candidates_text}

Based on the learned patterns, which candidate (if any) matches the unmatched transaction?
Look for:
- Similar naming conventions
- Date patterns (e.g., bank dates are typically 1-2 days after gateway)
- Amount patterns (e.g., bank might include fees)
- Reference/ID patterns

Return JSON with:
- matched_id: The ID of the matching candidate, or null if no match
- confidence: Your confidence 0.0-1.0
- pattern_used: Description of the pattern you applied
- reasoning: Why this is/isn't a match
- is_generalized: true if you extended a pattern beyond exact match

Return only valid JSON."""
    
    def _rule_based_pattern_match(
        self,
        unmatched: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        patterns: List[Dict[str, Any]],
    ) -> PatternMatchResult:
        """Fallback rule-based pattern matching."""
        unmatched_desc = (unmatched.get("description") or "").lower()
        unmatched_amount = abs(float(unmatched.get("amount", 0)))
        
        for pattern in patterns:
            bank_pat = (pattern.get("bank_pattern") or "").lower()
            gateway_pat = (pattern.get("gateway_pattern") or "").lower()
            
            if bank_pat and bank_pat in unmatched_desc:
                # Look for gateway pattern in candidates
                for candidate in candidates:
                    cand_desc = (candidate.get("description") or "").lower()
                    cand_amount = abs(float(candidate.get("amount", 0)))
                    
                    if gateway_pat in cand_desc:
                        # Amount within 5%
                        if abs(unmatched_amount - cand_amount) / max(unmatched_amount, 0.01) < 0.05:
                            return PatternMatchResult(
                                matched_transaction_id=candidate.get("id") or candidate.get("transaction_id"),
                                confidence=pattern.get("confidence", 0.8),
                                pattern_used=f"Bank: '{bank_pat}' ↔ Gateway: '{gateway_pat}'",
                                reasoning="Matched using learned pattern",
                                is_generalized=False,
                            )
        
        return PatternMatchResult(
            matched_transaction_id=None,
            confidence=0.0,
            pattern_used="",
            reasoning="No pattern match found",
            is_generalized=False,
        )
    
    # =========================================================================
    # 4. CONTEXTUAL MATCH CONFIDENCE
    # =========================================================================
    
    def adjust_match_confidence(
        self,
        source_txn: Dict[str, Any],
        target_txn: Dict[str, Any],
        algorithmic_score: float,
        score_breakdown: Dict[str, Any],
    ) -> ConfidenceAdjustment:
        """
        Use LLM to adjust match confidence based on contextual analysis.
        
        Args:
            source_txn: Source transaction (e.g., gateway)
            target_txn: Target transaction (e.g., bank)
            algorithmic_score: Score from multi-factor algorithm (0-100)
            score_breakdown: Detailed breakdown of algorithmic scoring
            
        Returns:
            ConfidenceAdjustment with adjusted score and reasoning
        """
        if not self._has_llm:
            return ConfidenceAdjustment(
                original_score=algorithmic_score,
                adjusted_score=algorithmic_score,
                should_match=algorithmic_score >= 80,
                reasoning="No LLM available, using algorithmic score",
                factors=[],
            )
        
        try:
            prompt = self._build_confidence_prompt(
                source_txn, target_txn, algorithmic_score, score_breakdown
            )
            result = self.llm.generate_json(prompt)
            
            adjusted = min(100.0, max(0.0, float(result.get("adjusted_score", algorithmic_score))))
            
            return ConfidenceAdjustment(
                original_score=algorithmic_score,
                adjusted_score=adjusted,
                should_match=result.get("should_match", adjusted >= 80),
                reasoning=result.get("reasoning", ""),
                factors=result.get("factors", []),
            )
        except Exception as e:
            return ConfidenceAdjustment(
                original_score=algorithmic_score,
                adjusted_score=algorithmic_score,
                should_match=algorithmic_score >= 80,
                reasoning=f"LLM error, using algorithmic score: {e}",
                factors=[],
            )
    
    def _build_confidence_prompt(
        self,
        source: Dict[str, Any],
        target: Dict[str, Any],
        score: float,
        breakdown: Dict[str, Any],
    ) -> str:
        """Build the confidence adjustment prompt."""
        return f"""You are a financial reconciliation expert. Evaluate this potential match.

Source Transaction (Gateway):
- ID: {source.get('id') or source.get('transaction_id')}
- Amount: €{source.get('amount', 0):,.2f}
- Date: {source.get('date')}
- Description: "{source.get('description', '')}"
- Reference: {source.get('reference', 'N/A')}

Target Transaction (Bank):
- ID: {target.get('id') or target.get('transaction_id')}
- Amount: €{target.get('amount', 0):,.2f}
- Date: {target.get('date')}
- Description: "{target.get('description', '')}"
- Reference: {target.get('reference', 'N/A')}

Algorithmic Score: {score}/100
Breakdown:
- Amount: {breakdown.get('amount', {}).get('score', 'N/A')}/40 - {breakdown.get('amount', {}).get('detail', '')}
- Date: {breakdown.get('date', {}).get('score', 'N/A')}/30 - {breakdown.get('date', {}).get('detail', '')}
- Description: {breakdown.get('description', {}).get('score', 'N/A')}/20 - {breakdown.get('description', {}).get('detail', '')}
- Reference: {breakdown.get('reference', {}).get('score', 'N/A')}/10 - {breakdown.get('reference', {}).get('detail', '')}

Consider contextual factors the algorithm might miss:
- Small amount differences might be fees (0.25%, 2.9%+€0.30, etc.)
- 1-3 day delays are normal for bank settlement
- Different description formats may represent same transaction
- Reference IDs might be embedded differently

Return JSON with:
- adjusted_score: Your confidence score 0-100
- should_match: boolean - should these be matched?
- reasoning: Why you adjusted (or didn't adjust) the score
- factors: Array of factors you considered

Return only valid JSON."""
    
    # =========================================================================
    # 5. INTELLIGENT EXCEPTION ROUTING
    # =========================================================================
    
    def route_exception(
        self,
        exception: Dict[str, Any],
        team_members: List[Dict[str, Any]],
        historical_resolutions: Optional[List[Dict[str, Any]]] = None,
    ) -> RoutingDecision:
        """
        Intelligently route an exception to the right team member.
        
        Args:
            exception: Exception details (type, amount, description)
            team_members: Available team members with roles and expertise
            historical_resolutions: Past exception resolutions for learning
            
        Returns:
            RoutingDecision with assignee and reasoning
        """
        if not self._has_llm:
            return self._rule_based_routing(exception, team_members)
        
        try:
            prompt = self._build_routing_prompt(
                exception, team_members, historical_resolutions or []
            )
            result = self.llm.generate_json(prompt)
            
            priority_map = {
                "urgent": ExceptionPriority.URGENT,
                "high": ExceptionPriority.HIGH,
                "medium": ExceptionPriority.MEDIUM,
                "low": ExceptionPriority.LOW,
            }
            
            return RoutingDecision(
                assignee=result.get("assignee", team_members[0].get("name", "Unassigned") if team_members else "Unassigned"),
                escalate_to=result.get("escalate_to"),
                priority=priority_map.get(
                    result.get("priority", "medium").lower(),
                    ExceptionPriority.MEDIUM
                ),
                reasoning=result.get("reasoning", ""),
                estimated_resolution_time=result.get("estimated_time", "1 business day"),
                suggested_actions=result.get("suggested_actions", []),
            )
        except Exception as e:
            return self._rule_based_routing(exception, team_members)
    
    def _build_routing_prompt(
        self,
        exception: Dict[str, Any],
        team: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
    ) -> str:
        """Build the exception routing prompt."""
        team_text = "\n".join([
            f"- {m.get('name')} ({m.get('role')}): {m.get('expertise', 'General finance')}"
            for m in team[:10]
        ])
        
        history_text = ""
        if history:
            history_text = "\n\nHistorical resolutions:\n" + "\n".join([
                f"- {h.get('exception_type')}: Resolved by {h.get('resolved_by')} in {h.get('resolution_time')}"
                for h in history[:10]
            ])
        
        return f"""You are an intelligent exception routing system for finance operations.

Exception to route:
- Type: {exception.get('type', 'Unknown')}
- Amount: €{exception.get('amount', 0):,.2f}
- Description: {exception.get('description', '')}
- Reason: {exception.get('reason', '')}
- Source: {exception.get('source', 'Unknown')}
- Priority hint: {exception.get('priority', 'medium')}

Team members:
{team_text}
{history_text}

Routing considerations:
- Match expertise to exception type
- Consider workload and availability
- Escalate large amounts or complex issues
- Route vendor issues to AP, customer issues to AR
- Treasury handles bank/wire items
- Controllers handle unusual or complex items

Return JSON with:
- assignee: Name of team member to assign
- escalate_to: Name of person to escalate to (or null)
- priority: "urgent" | "high" | "medium" | "low"
- reasoning: Why this routing decision
- estimated_time: Estimated resolution time
- suggested_actions: Array of recommended next steps

Return only valid JSON."""
    
    def _rule_based_routing(
        self,
        exception: Dict[str, Any],
        team: List[Dict[str, Any]],
    ) -> RoutingDecision:
        """Fallback rule-based routing."""
        amount = abs(float(exception.get("amount", 0)))
        exc_type = (exception.get("type") or "").lower()
        description = (exception.get("description") or "").lower()
        
        # Default assignee
        assignee = team[0].get("name", "Unassigned") if team else "Unassigned"
        escalate_to = None
        priority = ExceptionPriority.MEDIUM
        
        # Amount-based priority
        if amount > 25000:
            priority = ExceptionPriority.URGENT
            escalate_to = next(
                (m.get("name") for m in team if "controller" in m.get("role", "").lower() or "cfo" in m.get("role", "").lower()),
                None
            )
        elif amount > 10000:
            priority = ExceptionPriority.HIGH
        elif amount < 1000:
            priority = ExceptionPriority.LOW
        
        # Type-based routing
        if "vendor" in description or "invoice" in description:
            assignee = next(
                (m.get("name") for m in team if "ap" in m.get("role", "").lower() or "payable" in m.get("expertise", "").lower()),
                assignee
            )
        elif "customer" in description or "payment" in description:
            assignee = next(
                (m.get("name") for m in team if "ar" in m.get("role", "").lower() or "receivable" in m.get("expertise", "").lower()),
                assignee
            )
        elif "wire" in description or "transfer" in description:
            assignee = next(
                (m.get("name") for m in team if "treasury" in m.get("role", "").lower()),
                assignee
            )
        
        return RoutingDecision(
            assignee=assignee,
            escalate_to=escalate_to,
            priority=priority,
            reasoning="Routed based on amount thresholds and keywords",
            estimated_resolution_time="1 business day" if priority != ExceptionPriority.URGENT else "4 hours",
            suggested_actions=["Review exception details", "Check related transactions"],
        )
    
    # =========================================================================
    # BATCH OPERATIONS
    # =========================================================================
    
    def categorize_batch(
        self,
        transactions: List[Dict[str, Any]],
        gl_accounts: List[Dict[str, Any]],
        historical_examples: Optional[List[Dict[str, Any]]] = None,
    ) -> List[CategorizationResult]:
        """Categorize multiple transactions."""
        results = []
        for txn in transactions:
            result = self.categorize_transaction(
                description=txn.get("description", ""),
                vendor=txn.get("vendor") or txn.get("counterparty", ""),
                amount=float(txn.get("amount", 0)),
                gl_accounts=gl_accounts,
                historical_examples=historical_examples,
            )
            results.append(result)
        return results
    
    def detect_anomalies_batch(
        self,
        transactions: List[Dict[str, Any]],
        historical_transactions: List[Dict[str, Any]],
    ) -> List[Tuple[Dict[str, Any], AnomalyResult]]:
        """Detect anomalies in multiple transactions."""
        results = []
        for txn in transactions:
            result = self.detect_anomaly(txn, historical_transactions)
            results.append((txn, result))
        return results


# Convenience function for quick access
def get_enhanced_ai_service() -> EnhancedAIService:
    """Get an instance of the enhanced AI service."""
    return EnhancedAIService()
