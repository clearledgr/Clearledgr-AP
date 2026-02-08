"""
Clearledgr Explainability Engine

Generates human-readable reasoning for all AI decisions.
Makes the AI's thinking visible to users, building trust and enabling review.
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime


class ReasoningType(str, Enum):
    """Types of reasoning steps."""
    MATCH = "match"
    NO_MATCH = "no_match"
    PARTIAL_MATCH = "partial_match"
    CATEGORIZATION = "categorization"
    EXCEPTION = "exception"
    PATTERN = "pattern"
    LEARNING = "learning"


class ConfidenceLevel(str, Enum):
    """Human-readable confidence levels."""
    HIGH = "high"        # 90-100%
    MEDIUM = "medium"    # 70-89%
    LOW = "low"          # 50-69%
    UNCERTAIN = "uncertain"  # <50%


@dataclass
class ReasoningStep:
    """A single step in the reasoning chain."""
    factor: str           # What was evaluated
    observation: str      # What was found
    impact: str           # How it affected the decision (+, -, neutral)
    weight: float         # How much this factor mattered (0-1)
    details: Optional[Dict[str, Any]] = None


@dataclass
class ReasoningChain:
    """Complete reasoning for a decision."""
    decision_type: ReasoningType
    decision: str                      # The final decision made
    confidence: float                  # 0-100
    confidence_level: ConfidenceLevel
    summary: str                       # One-line explanation
    steps: List[ReasoningStep]         # Detailed reasoning steps
    alternatives: Optional[List[str]] = None  # What else was considered
    timestamp: str = None
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    def to_display_text(self) -> str:
        """Generate human-readable display text."""
        lines = [
            f"Decision: {self.decision}",
            f"Confidence: {self.confidence:.0f}% ({self.confidence_level.value})",
            f"",
            f"Reasoning:",
        ]
        
        for i, step in enumerate(self.steps, 1):
            impact_symbol = "[+]" if step.impact == "positive" else "[-]" if step.impact == "negative" else "[o]"
            lines.append(f"  {impact_symbol} {step.factor}: {step.observation}")
        
        if self.alternatives:
            lines.append("")
            lines.append("Also considered:")
            for alt in self.alternatives:
                lines.append(f"  • {alt}")
        
        return "\n".join(lines)
    
    def to_tree_format(self) -> str:
        """Generate tree-style display (like the example shown)."""
        lines = [f"{self.decision}"]
        
        for i, step in enumerate(self.steps):
            is_last = i == len(self.steps) - 1
            prefix = "└──" if is_last else "├──"
            impact_symbol = "[+]" if step.impact == "positive" else "[-]" if step.impact == "negative" else "[o]"
            lines.append(f"{prefix} {step.factor}: {step.observation} {impact_symbol}")
        
        lines.append(f"└── Confidence: {self.confidence:.0f}%")
        
        return "\n".join(lines)


class ReconciliationExplainer:
    """Generates reasoning for reconciliation decisions."""
    
    @staticmethod
    def explain_match(
        source_txn: Dict,
        matched_txns: List[Dict],
        match_scores: Dict[str, float],
        config: Dict
    ) -> ReasoningChain:
        """Explain why transactions were matched."""
        steps = []
        total_confidence = 0
        
        # Amount matching
        amount_tolerance = config.get("amount_tolerance_pct", 0.5)
        source_amount = source_txn.get("amount", 0) or source_txn.get("net_amount", 0)
        
        for matched in matched_txns:
            matched_amount = matched.get("amount", 0)
            amount_diff_pct = abs(source_amount - matched_amount) / max(source_amount, 0.01) * 100
            
            if amount_diff_pct == 0:
                steps.append(ReasoningStep(
                    factor="Amount",
                    observation=f"${source_amount:,.2f} matches exactly",
                    impact="positive",
                    weight=0.4,
                    details={"source": source_amount, "matched": matched_amount, "diff_pct": 0}
                ))
                total_confidence += 40
            elif amount_diff_pct <= amount_tolerance:
                steps.append(ReasoningStep(
                    factor="Amount",
                    observation=f"${source_amount:,.2f} vs ${matched_amount:,.2f} ({amount_diff_pct:.1f}% diff, within {amount_tolerance}% tolerance)",
                    impact="positive",
                    weight=0.35,
                    details={"source": source_amount, "matched": matched_amount, "diff_pct": amount_diff_pct}
                ))
                total_confidence += 35
            break  # Only explain first match for now
        
        # Date matching
        date_window = config.get("date_window_days", 3)
        source_date = source_txn.get("date")
        
        for matched in matched_txns:
            matched_date = matched.get("date")
            if source_date and matched_date:
                try:
                    from datetime import datetime
                    if isinstance(source_date, str):
                        s_date = datetime.strptime(source_date, "%Y-%m-%d")
                    else:
                        s_date = source_date
                    if isinstance(matched_date, str):
                        m_date = datetime.strptime(matched_date, "%Y-%m-%d")
                    else:
                        m_date = matched_date
                    
                    days_diff = abs((s_date - m_date).days)
                    
                    if days_diff == 0:
                        steps.append(ReasoningStep(
                            factor="Date",
                            observation=f"Same date ({source_date})",
                            impact="positive",
                            weight=0.3,
                            details={"source": source_date, "matched": matched_date, "days_diff": 0}
                        ))
                        total_confidence += 30
                    elif days_diff <= date_window:
                        steps.append(ReasoningStep(
                            factor="Date",
                            observation=f"{days_diff} day(s) apart (within {date_window}-day window)",
                            impact="positive",
                            weight=0.25,
                            details={"source": source_date, "matched": matched_date, "days_diff": days_diff}
                        ))
                        total_confidence += 25
                except:
                    pass
            break
        
        # ID/Reference matching
        source_id = source_txn.get("txn_id") or source_txn.get("internal_id") or source_txn.get("bank_txn_id")
        for matched in matched_txns:
            matched_id = matched.get("txn_id") or matched.get("internal_id") or matched.get("bank_txn_id")
            if source_id and matched_id:
                # Check for partial ID match
                if source_id in str(matched_id) or str(matched_id) in source_id:
                    steps.append(ReasoningStep(
                        factor="Reference ID",
                        observation=f"IDs related: {source_id} ↔ {matched_id}",
                        impact="positive",
                        weight=0.2,
                        details={"source_id": source_id, "matched_id": matched_id}
                    ))
                    total_confidence += 20
            break
        
        # Vendor/Description matching
        source_desc = source_txn.get("description", "") or source_txn.get("vendor", "")
        for matched in matched_txns:
            matched_desc = matched.get("description", "") or matched.get("vendor", "")
            if source_desc and matched_desc:
                # Simple similarity check
                source_words = set(source_desc.lower().split())
                matched_words = set(matched_desc.lower().split())
                common_words = source_words & matched_words
                if common_words:
                    similarity = len(common_words) / max(len(source_words), len(matched_words)) * 100
                    steps.append(ReasoningStep(
                        factor="Description",
                        observation=f"'{source_desc}' ↔ '{matched_desc}' ({similarity:.0f}% word overlap)",
                        impact="positive" if similarity > 50 else "neutral",
                        weight=0.1,
                        details={"source": source_desc, "matched": matched_desc, "similarity": similarity}
                    ))
                    if similarity > 50:
                        total_confidence += 10
            break
        
        # Determine confidence level
        confidence = min(total_confidence, 100)
        if confidence >= 90:
            confidence_level = ConfidenceLevel.HIGH
        elif confidence >= 70:
            confidence_level = ConfidenceLevel.MEDIUM
        elif confidence >= 50:
            confidence_level = ConfidenceLevel.LOW
        else:
            confidence_level = ConfidenceLevel.UNCERTAIN
        
        # Build summary
        match_count = len(matched_txns)
        if match_count == 2:
            summary = f"3-way match: Gateway ↔ Bank ↔ Internal"
        elif match_count == 1:
            summary = f"2-way match found"
        else:
            summary = f"Matched with {match_count} transactions"
        
        return ReasoningChain(
            decision_type=ReasoningType.MATCH,
            decision=f"Matched transaction {source_id or 'N/A'}",
            confidence=confidence,
            confidence_level=confidence_level,
            summary=summary,
            steps=steps
        )
    
    @staticmethod
    def explain_exception(
        txn: Dict,
        exception_type: str,
        details: Dict
    ) -> ReasoningChain:
        """Explain why a transaction was flagged as an exception."""
        steps = []
        
        txn_id = txn.get("txn_id") or txn.get("internal_id") or txn.get("bank_txn_id") or "Unknown"
        amount = txn.get("amount", 0) or txn.get("net_amount", 0)
        
        if exception_type == "unmatched_gateway":
            steps.append(ReasoningStep(
                factor="Gateway Record",
                observation=f"Transaction {txn_id} (${amount:,.2f}) found in gateway",
                impact="neutral",
                weight=0.2
            ))
            steps.append(ReasoningStep(
                factor="Bank Match",
                observation="No matching bank transaction found",
                impact="negative",
                weight=0.4,
                details={"searched": "bank records", "result": "no match"}
            ))
            steps.append(ReasoningStep(
                factor="Internal Match",
                observation="No matching internal record found",
                impact="negative",
                weight=0.4,
                details={"searched": "internal records", "result": "no match"}
            ))
            summary = "Gateway transaction has no corresponding bank or internal record"
            
        elif exception_type == "unmatched_bank":
            steps.append(ReasoningStep(
                factor="Bank Record",
                observation=f"Transaction {txn_id} (${amount:,.2f}) found in bank",
                impact="neutral",
                weight=0.2
            ))
            steps.append(ReasoningStep(
                factor="Gateway Match",
                observation="No matching gateway transaction found",
                impact="negative",
                weight=0.4
            ))
            steps.append(ReasoningStep(
                factor="Internal Match",
                observation="No matching internal record found",
                impact="negative",
                weight=0.4
            ))
            summary = "Bank transaction has no corresponding gateway or internal record"
            
        elif exception_type == "amount_variance":
            variance = details.get("variance", 0)
            variance_pct = details.get("variance_pct", 0)
            steps.append(ReasoningStep(
                factor="Amount Comparison",
                observation=f"Variance of ${abs(variance):,.2f} ({variance_pct:.1f}%) detected",
                impact="negative",
                weight=0.5,
                details={"variance": variance, "variance_pct": variance_pct}
            ))
            steps.append(ReasoningStep(
                factor="Tolerance Check",
                observation=f"Exceeds configured tolerance",
                impact="negative",
                weight=0.3
            ))
            summary = f"Amount variance of {variance_pct:.1f}% exceeds tolerance"
            
        elif exception_type == "date_mismatch":
            days_diff = details.get("days_diff", 0)
            steps.append(ReasoningStep(
                factor="Date Comparison",
                observation=f"Dates are {days_diff} days apart",
                impact="negative",
                weight=0.4
            ))
            steps.append(ReasoningStep(
                factor="Window Check",
                observation="Exceeds configured date window",
                impact="negative",
                weight=0.3
            ))
            summary = f"Date difference of {days_diff} days exceeds window"
            
        else:
            steps.append(ReasoningStep(
                factor="Exception Type",
                observation=exception_type,
                impact="negative",
                weight=0.5
            ))
            summary = f"Exception: {exception_type}"
        
        return ReasoningChain(
            decision_type=ReasoningType.EXCEPTION,
            decision=f"Flagged as exception: {exception_type}",
            confidence=95,  # High confidence in the exception
            confidence_level=ConfidenceLevel.HIGH,
            summary=summary,
            steps=steps,
            alternatives=["Manual review required", "Check source systems"]
        )


class CategorizationExplainer:
    """Generates reasoning for categorization decisions."""
    
    @staticmethod
    def explain_categorization(
        txn: Dict,
        category: str,
        method: str,
        patterns_used: List[Dict] = None,
        keywords_matched: List[str] = None,
        historical_matches: List[Dict] = None
    ) -> ReasoningChain:
        """Explain why a transaction was categorized a certain way."""
        steps = []
        confidence = 0
        
        description = txn.get("description", "") or txn.get("vendor", "") or ""
        amount = txn.get("amount", 0)
        
        # Base observation
        steps.append(ReasoningStep(
            factor="Transaction",
            observation=f"'{description}' for ${amount:,.2f}",
            impact="neutral",
            weight=0.1
        ))
        
        if method == "keyword":
            if keywords_matched:
                steps.append(ReasoningStep(
                    factor="Keyword Match",
                    observation=f"Found keywords: {', '.join(keywords_matched)}",
                    impact="positive",
                    weight=0.4,
                    details={"keywords": keywords_matched}
                ))
                confidence += 70
            else:
                steps.append(ReasoningStep(
                    factor="Keyword Match",
                    observation="Matched category keywords in description",
                    impact="positive",
                    weight=0.4
                ))
                confidence += 60
                
        elif method == "pattern":
            if patterns_used:
                pattern_desc = patterns_used[0].get("pattern", "learned pattern")
                match_count = patterns_used[0].get("match_count", 1)
                steps.append(ReasoningStep(
                    factor="Pattern Recognition",
                    observation=f"Matches pattern '{pattern_desc}' (used {match_count}x before)",
                    impact="positive",
                    weight=0.5,
                    details={"pattern": pattern_desc, "historical_uses": match_count}
                ))
                confidence += 80
            else:
                steps.append(ReasoningStep(
                    factor="Pattern Recognition",
                    observation="Matches a learned pattern",
                    impact="positive",
                    weight=0.4
                ))
                confidence += 70
                
        elif method == "historical":
            if historical_matches:
                similar_txn = historical_matches[0]
                steps.append(ReasoningStep(
                    factor="Historical Match",
                    observation=f"Similar to previous transaction categorized as '{category}'",
                    impact="positive",
                    weight=0.5,
                    details={"similar_to": similar_txn}
                ))
                confidence += 85
            else:
                steps.append(ReasoningStep(
                    factor="Historical Match",
                    observation="Previously seen similar transactions",
                    impact="positive",
                    weight=0.4
                ))
                confidence += 75
                
        elif method == "default":
            steps.append(ReasoningStep(
                factor="Default Rule",
                observation="No specific pattern found, using default category",
                impact="neutral",
                weight=0.3
            ))
            confidence += 40
            
        elif method == "user_correction":
            steps.append(ReasoningStep(
                factor="User Correction",
                observation="Category assigned based on your previous correction",
                impact="positive",
                weight=0.6
            ))
            confidence += 95
        
        # Add amount-based reasoning if relevant
        if amount > 10000:
            steps.append(ReasoningStep(
                factor="Amount",
                observation=f"High-value transaction (${amount:,.2f})",
                impact="neutral",
                weight=0.1,
                details={"threshold": 10000}
            ))
        
        confidence = min(confidence, 100)
        
        if confidence >= 90:
            confidence_level = ConfidenceLevel.HIGH
        elif confidence >= 70:
            confidence_level = ConfidenceLevel.MEDIUM
        elif confidence >= 50:
            confidence_level = ConfidenceLevel.LOW
        else:
            confidence_level = ConfidenceLevel.UNCERTAIN
        
        return ReasoningChain(
            decision_type=ReasoningType.CATEGORIZATION,
            decision=f"Categorized as: {category}",
            confidence=confidence,
            confidence_level=confidence_level,
            summary=f"Assigned to '{category}' via {method} matching",
            steps=steps
        )
    
    @staticmethod
    def explain_needs_review(
        txn: Dict,
        reason: str,
        candidates: List[Dict] = None
    ) -> ReasoningChain:
        """Explain why a transaction needs manual review."""
        steps = []
        
        description = txn.get("description", "") or txn.get("vendor", "") or ""
        amount = txn.get("amount", 0)
        
        steps.append(ReasoningStep(
            factor="Transaction",
            observation=f"'{description}' for ${amount:,.2f}",
            impact="neutral",
            weight=0.1
        ))
        
        if reason == "low_confidence":
            steps.append(ReasoningStep(
                factor="Confidence",
                observation="Could not determine category with sufficient confidence",
                impact="negative",
                weight=0.4
            ))
            if candidates:
                candidate_str = ", ".join([f"{c['category']} ({c['confidence']:.0f}%)" for c in candidates[:3]])
                steps.append(ReasoningStep(
                    factor="Candidates",
                    observation=f"Possible categories: {candidate_str}",
                    impact="neutral",
                    weight=0.2
                ))
                
        elif reason == "ambiguous":
            steps.append(ReasoningStep(
                factor="Ambiguity",
                observation="Multiple categories match equally well",
                impact="negative",
                weight=0.4
            ))
            
        elif reason == "new_vendor":
            steps.append(ReasoningStep(
                factor="New Vendor",
                observation="First time seeing this vendor/description",
                impact="neutral",
                weight=0.3
            ))
            steps.append(ReasoningStep(
                factor="Learning",
                observation="Your selection will teach Clearledgr for future transactions",
                impact="positive",
                weight=0.2
            ))
            
        elif reason == "high_value":
            steps.append(ReasoningStep(
                factor="Amount",
                observation=f"High-value transaction (${amount:,.2f}) requires verification",
                impact="neutral",
                weight=0.4
            ))
        
        alternatives = []
        if candidates:
            alternatives = [f"{c['category']}" for c in candidates[:5]]
        
        return ReasoningChain(
            decision_type=ReasoningType.EXCEPTION,
            decision="Needs manual review",
            confidence=30,
            confidence_level=ConfidenceLevel.UNCERTAIN,
            summary=f"Manual review needed: {reason}",
            steps=steps,
            alternatives=alternatives
        )


def add_reasoning_to_outputs(outputs: Dict, config: Dict) -> Dict:
    """
    Add reasoning chains to reconciliation outputs.
    Enhances the output with explainability for each decision.
    """
    enhanced_outputs = outputs.copy()
    
    # Add reasoning to reconciled groups
    if "reconciled" in outputs:
        for group in enhanced_outputs["reconciled"]:
            # Create reasoning for this match
            source_txn = group.get("gateway") or group.get("bank") or group.get("internal") or {}
            matched_txns = []
            if group.get("bank"):
                matched_txns.append(group["bank"])
            if group.get("internal"):
                matched_txns.append(group["internal"])
            
            reasoning = ReconciliationExplainer.explain_match(
                source_txn=source_txn,
                matched_txns=matched_txns,
                match_scores={},
                config=config
            )
            
            group["reasoning"] = reasoning.to_dict()
            group["reasoning_display"] = reasoning.to_tree_format()
    
    # Add reasoning to exceptions
    if "exceptions" in outputs:
        for exc in enhanced_outputs["exceptions"]:
            exc_type = exc.get("exception_type", "unknown")
            txn = exc.get("transaction", {})
            
            reasoning = ReconciliationExplainer.explain_exception(
                txn=txn,
                exception_type=exc_type,
                details=exc
            )
            
            exc["reasoning"] = reasoning.to_dict()
            exc["reasoning_display"] = reasoning.to_tree_format()
    
    return enhanced_outputs

