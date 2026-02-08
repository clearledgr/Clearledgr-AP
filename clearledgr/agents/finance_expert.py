"""
Clearledgr Finance Expert Agent

This is NOT a chatbot. This is a highly skilled finance AI that:
- Reasons like a senior accountant / CFO
- Understands context, timing, materiality
- Provides actionable recommendations with rationale
- Takes action with confidence, escalates with context
- Learns your business patterns over time

The difference:
- Chatbot: "Here are your exceptions"
- Finance Expert: "Two of these are timing differences that'll clear Friday. 
  The third is a billing cycle change - I recommend an accrual entry."
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class InsightType(Enum):
    """Types of financial insights the agent can provide."""
    TIMING_DIFFERENCE = "timing_difference"
    AMOUNT_VARIANCE = "amount_variance"
    PATTERN_DETECTED = "pattern_detected"
    RISK_ALERT = "risk_alert"
    RECOMMENDATION = "recommendation"
    ACTION_TAKEN = "action_taken"
    LEARNING = "learning"


class ActionConfidence(Enum):
    """Confidence levels for recommended actions."""
    CERTAIN = "certain"          # 95%+ - Will execute unless stopped
    HIGH = "high"                # 85-94% - Recommends strongly
    MODERATE = "moderate"        # 70-84% - Suggests with caveats
    LOW = "low"                  # 50-69% - Presents options
    UNCERTAIN = "uncertain"      # <50% - Asks for guidance


@dataclass
class FinanceInsight:
    """A financial insight with reasoning and recommendation."""
    insight_type: InsightType
    title: str
    reasoning: str  # The "why" - this is what makes it expert-level
    recommendation: str
    action: Optional[str] = None  # What the agent can do
    confidence: ActionConfidence = ActionConfidence.MODERATE
    materiality: str = "low"  # low, medium, high, critical
    timing_impact: Optional[str] = None  # "affects Q3 close", "clears by Friday"
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExpertResponse:
    """Response from the finance expert agent."""
    summary: str  # Brief, actionable summary
    insights: List[FinanceInsight]
    recommended_actions: List[Dict[str, Any]]
    context: str  # Business context and reasoning
    confidence: ActionConfidence
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "insights": [
                {
                    "type": self.insight_type.value,
                    "title": i.title,
                    "reasoning": i.reasoning,
                    "recommendation": i.recommendation,
                    "action": i.action,
                    "confidence": i.confidence.value,
                    "materiality": i.materiality,
                    "timing_impact": i.timing_impact,
                }
                for i in self.insights
            ],
            "recommended_actions": self.recommended_actions,
            "context": self.context,
            "confidence": self.confidence.value,
        }


class FinanceExpertAgent:
    """
    A highly skilled finance AI agent that reasons like a CFO.
    
    This agent:
    1. Analyzes financial data with domain expertise
    2. Understands timing, materiality, and business impact
    3. Provides actionable recommendations with clear reasoning
    4. Takes autonomous action when confident
    5. Escalates with full context when uncertain
    """
    
    def __init__(self):
        self.business_context: Dict[str, Any] = {}
        self.learned_patterns: List[Dict] = []
        self.materiality_threshold = 5000  # EUR - configurable per org
        self.period_end_dates: List[datetime] = []  # Month/quarter ends
        
    def set_business_context(
        self,
        company_name: str,
        fiscal_year_end: str,  # "December" or "March" etc
        materiality_threshold: float,
        typical_vendors: List[str],
        settlement_patterns: Dict[str, int],  # vendor -> typical days to settle
    ) -> None:
        """Configure business-specific context."""
        self.business_context = {
            "company": company_name,
            "fiscal_year_end": fiscal_year_end,
            "materiality": materiality_threshold,
            "vendors": typical_vendors,
            "settlement_days": settlement_patterns,
        }
        self.materiality_threshold = materiality_threshold
    
    async def analyze_exceptions(
        self,
        exceptions: List[Dict[str, Any]],
    ) -> ExpertResponse:
        """
        Analyze exceptions with expert-level reasoning.
        
        Doesn't just list exceptions - explains what they mean,
        why they happened, and what to do about them.
        """
        if not exceptions:
            return ExpertResponse(
                summary="No exceptions to review. All transactions matched successfully.",
                insights=[],
                recommended_actions=[],
                context="Your reconciliation is clean. No action needed.",
                confidence=ActionConfidence.CERTAIN,
            )
        
        insights = []
        actions = []
        
        # Analyze each exception with expert reasoning
        for exc in exceptions:
            insight = self._analyze_single_exception(exc)
            insights.append(insight)
            
            if insight.action:
                actions.append({
                    "action": insight.action,
                    "for": exc.get("id"),
                    "confidence": insight.confidence.value,
                    "reason": insight.reasoning,
                })
        
        # Group and prioritize insights
        critical = [i for i in insights if i.materiality == "critical"]
        high = [i for i in insights if i.materiality == "high"]
        timing_clears = [i for i in insights if i.insight_type == InsightType.TIMING_DIFFERENCE]
        
        # Build expert summary
        summary = self._build_expert_summary(insights, critical, high, timing_clears)
        context = self._build_business_context(insights)
        
        return ExpertResponse(
            summary=summary,
            insights=insights,
            recommended_actions=actions,
            context=context,
            confidence=self._overall_confidence(insights),
        )
    
    def _analyze_single_exception(self, exc: Dict[str, Any]) -> FinanceInsight:
        """Analyze a single exception with expert reasoning."""
        amount = abs(float(exc.get("amount", 0)))
        reason = exc.get("reason", "").lower()
        vendor = exc.get("vendor", exc.get("description", ""))
        date = exc.get("date")
        
        # Determine materiality
        materiality = self._assess_materiality(amount)
        
        # Check if it's a timing difference
        if self._is_likely_timing_difference(exc):
            settlement_date = self._estimate_settlement_date(vendor)
            return FinanceInsight(
                insight_type=InsightType.TIMING_DIFFERENCE,
                title=f"Timing difference: {vendor}",
                reasoning=f"This looks like a settlement timing issue. {vendor} typically settles in {self._get_settlement_days(vendor)} days. The bank hasn't recorded it yet, but it should clear by {settlement_date}.",
                recommendation="No action needed - monitor and it should auto-resolve.",
                action=None,  # Don't act on timing differences
                confidence=ActionConfidence.HIGH,
                materiality=materiality,
                timing_impact=f"Expected to clear by {settlement_date}",
                data=exc,
            )
        
        # Check if it's an amount variance
        if "variance" in reason or "amount" in reason or "mismatch" in reason:
            variance_analysis = self._analyze_variance(exc)
            return FinanceInsight(
                insight_type=InsightType.AMOUNT_VARIANCE,
                title=f"Amount variance: €{amount:,.2f}",
                reasoning=variance_analysis["reasoning"],
                recommendation=variance_analysis["recommendation"],
                action=variance_analysis.get("action"),
                confidence=variance_analysis["confidence"],
                materiality=materiality,
                timing_impact=self._assess_period_impact(date),
                data=exc,
            )
        
        # Check for fee-related issues
        if self._looks_like_fee(exc):
            return FinanceInsight(
                insight_type=InsightType.PATTERN_DETECTED,
                title=f"Processing fee detected: {vendor}",
                reasoning=f"This appears to be a processing fee or bank charge. Amount (€{amount:,.2f}) is consistent with typical fee patterns. These should be recorded as expense, not matched to revenue.",
                recommendation="Create a journal entry to record as bank fees expense.",
                action="create_fee_entry",
                confidence=ActionConfidence.HIGH,
                materiality=materiality,
                data=exc,
            )
        
        # Default: needs review
        return FinanceInsight(
            insight_type=InsightType.RISK_ALERT,
            title=f"Requires review: {vendor}",
            reasoning=f"I couldn't automatically categorize this exception. Amount: €{amount:,.2f}. {reason}. This needs human judgment.",
            recommendation="Review manually and provide guidance so I can learn from this.",
            action=None,
            confidence=ActionConfidence.UNCERTAIN,
            materiality=materiality,
            data=exc,
        )
    
    def _is_likely_timing_difference(self, exc: Dict) -> bool:
        """Determine if exception is likely a timing difference."""
        reason = exc.get("reason", "").lower()
        
        timing_keywords = [
            "not found in bank",
            "no matching",
            "missing",
            "pending",
            "in transit",
            "settlement",
        ]
        
        return any(kw in reason for kw in timing_keywords)
    
    def _analyze_variance(self, exc: Dict) -> Dict[str, Any]:
        """Analyze an amount variance with expert reasoning."""
        amount = abs(float(exc.get("amount", 0)))
        expected = exc.get("expected_amount", 0)
        variance = amount - expected if expected else amount
        variance_pct = abs(variance / expected * 100) if expected else 0
        
        # Small variance - likely rounding or FX
        if abs(variance) < 10:
            return {
                "reasoning": f"This is a minor variance of €{variance:,.2f} ({variance_pct:.1f}%). Likely rounding or FX rate difference. Within tolerance.",
                "recommendation": "Auto-match with variance recorded.",
                "action": "auto_match_with_variance",
                "confidence": ActionConfidence.HIGH,
            }
        
        # Fee-sized variance
        if 10 <= abs(variance) <= 500:
            return {
                "reasoning": f"Variance of €{variance:,.2f} is consistent with a processing fee or bank charge. This often happens with payment processors who deduct fees from payouts.",
                "recommendation": "Match and record the difference as bank fees.",
                "action": "match_with_fee_entry",
                "confidence": ActionConfidence.HIGH,
            }
        
        # Material variance
        if abs(variance) > self.materiality_threshold:
            return {
                "reasoning": f"Material variance of €{variance:,.2f}. This exceeds your materiality threshold and needs investigation. Could be: partial payment, duplicate charge, or data entry error.",
                "recommendation": "Do not auto-match. Investigate the source transaction.",
                "action": None,
                "confidence": ActionConfidence.UNCERTAIN,
            }
        
        # Moderate variance
        return {
            "reasoning": f"Variance of €{variance:,.2f} ({variance_pct:.1f}%). Not trivial but below materiality. Review the source documents to confirm.",
            "recommendation": "Review and confirm, then match with adjustment.",
            "action": "queue_for_review",
            "confidence": ActionConfidence.MODERATE,
        }
    
    def _looks_like_fee(self, exc: Dict) -> bool:
        """Determine if exception looks like a fee/charge."""
        amount = abs(float(exc.get("amount", 0)))
        description = (exc.get("description", "") + " " + exc.get("reason", "")).lower()
        
        fee_keywords = ["fee", "charge", "commission", "processing", "service"]
        fee_patterns = amount < 500 and any(kw in description for kw in fee_keywords)
        
        return fee_patterns
    
    def _assess_materiality(self, amount: float) -> str:
        """Assess materiality of an amount."""
        if amount >= self.materiality_threshold * 10:
            return "critical"
        elif amount >= self.materiality_threshold:
            return "high"
        elif amount >= self.materiality_threshold * 0.1:
            return "medium"
        return "low"
    
    def _get_settlement_days(self, vendor: str) -> int:
        """Get typical settlement days for a vendor."""
        vendor_lower = vendor.lower()
        
        # Known settlement patterns
        patterns = {
            "stripe": 2,
            "paypal": 3,
            "wise": 1,
            "mercury": 1,
            "brex": 2,
            "adyen": 3,
        }
        
        for name, days in patterns.items():
            if name in vendor_lower:
                return days
        
        return 3  # Default
    
    def _estimate_settlement_date(self, vendor: str) -> str:
        """Estimate when a transaction will settle."""
        days = self._get_settlement_days(vendor)
        settlement = datetime.now() + timedelta(days=days)
        
        # Skip weekends
        while settlement.weekday() >= 5:
            settlement += timedelta(days=1)
        
        return settlement.strftime("%A, %B %d")
    
    def _assess_period_impact(self, date: Any) -> Optional[str]:
        """Assess if transaction affects period close."""
        if not date:
            return None
        
        try:
            if isinstance(date, str):
                date = datetime.fromisoformat(date.replace("Z", "+00:00"))
            
            now = datetime.now(timezone.utc)
            
            # Check month end
            month_end = now.replace(day=28) + timedelta(days=4)
            month_end = month_end.replace(day=1) - timedelta(days=1)
            
            days_to_month_end = (month_end.date() - now.date()).days
            
            if days_to_month_end <= 3:
                return "Affects this month's close"
            
            # Check quarter end
            quarter_end_months = [3, 6, 9, 12]
            if month_end.month in quarter_end_months and days_to_month_end <= 5:
                return "Affects quarter-end close"
            
        except Exception:
            pass
        
        return None
    
    def _build_expert_summary(
        self,
        insights: List[FinanceInsight],
        critical: List[FinanceInsight],
        high: List[FinanceInsight],
        timing: List[FinanceInsight],
    ) -> str:
        """Build an expert-level summary."""
        parts = []
        
        total = len(insights)
        
        if critical:
            parts.append(f"{len(critical)} critical item(s) need immediate attention")
        
        if high:
            parts.append(f"{len(high)} high-priority item(s) to review")
        
        if timing:
            parts.append(f"{len(timing)} timing difference(s) that should self-clear")
        
        remaining = total - len(critical) - len(high) - len(timing)
        if remaining > 0:
            parts.append(f"{remaining} other item(s)")
        
        if not parts:
            return "All clear - no exceptions to review."
        
        summary = f"You have {total} exception(s): " + ", ".join(parts) + "."
        
        # Add actionable insight
        if timing and len(timing) == total:
            summary += " These are all timing differences - I'd recommend waiting before taking action."
        elif critical:
            summary += f" Focus on the critical items first - they total €{sum(float(i.data.get('amount', 0)) for i in critical):,.2f}."
        
        return summary
    
    def _build_business_context(self, insights: List[FinanceInsight]) -> str:
        """Build business context for the response."""
        now = datetime.now()
        
        # Check if we're near period end
        days_to_month_end = (now.replace(day=28) + timedelta(days=4)).replace(day=1) - now
        
        context_parts = []
        
        if days_to_month_end.days <= 5:
            context_parts.append(f"We're {days_to_month_end.days} days from month-end close.")
        
        # Check for patterns
        timing_count = sum(1 for i in insights if i.insight_type == InsightType.TIMING_DIFFERENCE)
        if timing_count > 2:
            context_parts.append(f"Multiple timing differences suggest settlement delays - possibly a bank holiday or processor issue.")
        
        return " ".join(context_parts) if context_parts else ""
    
    def _overall_confidence(self, insights: List[FinanceInsight]) -> ActionConfidence:
        """Determine overall confidence for the response."""
        if not insights:
            return ActionConfidence.CERTAIN
        
        uncertain = sum(1 for i in insights if i.confidence == ActionConfidence.UNCERTAIN)
        
        if uncertain > len(insights) / 2:
            return ActionConfidence.UNCERTAIN
        elif uncertain > 0:
            return ActionConfidence.MODERATE
        
        return ActionConfidence.HIGH
    
    async def provide_guidance(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> ExpertResponse:
        """
        Respond to a query with expert financial guidance.
        
        Unlike a chatbot that just answers questions, this:
        - Understands the financial implications
        - Considers business context
        - Provides actionable recommendations
        - Explains the "why" not just the "what"
        """
        query_lower = query.lower()
        
        # Route to specific expert functions based on intent
        if any(w in query_lower for w in ["exception", "variance", "mismatch"]):
            exceptions = context.get("exceptions", []) if context else []
            return await self.analyze_exceptions(exceptions)
        
        if any(w in query_lower for w in ["approve", "post", "journal"]):
            return await self._provide_approval_guidance(query, context)
        
        if any(w in query_lower for w in ["reconcil", "match"]):
            return await self._provide_reconciliation_guidance(query, context)
        
        if any(w in query_lower for w in ["close", "period", "month", "quarter"]):
            return await self._provide_close_guidance(query, context)
        
        # General financial guidance
        return ExpertResponse(
            summary="How can I help with your financial operations?",
            insights=[],
            recommended_actions=[],
            context="I can help with: reconciliation analysis, exception review, journal entry guidance, and period close preparation. What would you like to focus on?",
            confidence=ActionConfidence.HIGH,
        )
    
    async def _provide_approval_guidance(
        self,
        query: str,
        context: Optional[Dict],
    ) -> ExpertResponse:
        """Provide guidance on approvals."""
        drafts = context.get("drafts", []) if context else []
        
        if not drafts:
            return ExpertResponse(
                summary="No draft journal entries pending approval.",
                insights=[],
                recommended_actions=[],
                context="All entries have been processed. Run reconciliation to generate new drafts from matched transactions.",
                confidence=ActionConfidence.CERTAIN,
            )
        
        # Analyze drafts
        high_confidence = [d for d in drafts if float(d.get("confidence", 0)) >= 0.95]
        needs_review = [d for d in drafts if float(d.get("confidence", 0)) < 0.95]
        
        insights = []
        actions = []
        
        if high_confidence:
            total = sum(float(d.get("amount", 0)) for d in high_confidence)
            insights.append(FinanceInsight(
                insight_type=InsightType.RECOMMENDATION,
                title=f"{len(high_confidence)} entries ready for bulk approval",
                reasoning=f"These entries have 95%+ confidence and total €{total:,.2f}. They've been auto-matched with high certainty.",
                recommendation="Approve all high-confidence entries in one action.",
                action="bulk_approve_high_confidence",
                confidence=ActionConfidence.CERTAIN,
                materiality="medium" if total > self.materiality_threshold else "low",
            ))
            actions.append({
                "action": "bulk_approve",
                "count": len(high_confidence),
                "total": total,
            })
        
        if needs_review:
            insights.append(FinanceInsight(
                insight_type=InsightType.RISK_ALERT,
                title=f"{len(needs_review)} entries need review before approval",
                reasoning="These entries have lower confidence and should be reviewed individually. Common issues: amount variance, timing differences, or new vendor patterns.",
                recommendation="Review each entry before approving.",
                action=None,
                confidence=ActionConfidence.MODERATE,
                materiality="medium",
            ))
        
        return ExpertResponse(
            summary=f"{len(drafts)} draft entries: {len(high_confidence)} ready for approval, {len(needs_review)} need review.",
            insights=insights,
            recommended_actions=actions,
            context="Tip: Entries posted today will be included in this period's close.",
            confidence=ActionConfidence.HIGH,
        )
    
    async def _provide_reconciliation_guidance(
        self,
        query: str,
        context: Optional[Dict],
    ) -> ExpertResponse:
        """Provide guidance on reconciliation."""
        status = context.get("status", {}) if context else {}
        
        match_rate = status.get("match_rate", 0)
        unmatched = status.get("unmatched", 0)
        
        insights = []
        
        if match_rate >= 95:
            insights.append(FinanceInsight(
                insight_type=InsightType.ACTION_TAKEN,
                title=f"Excellent match rate: {match_rate:.1f}%",
                reasoning="Your reconciliation is performing well above typical benchmarks (85-90%). The unmatched items are likely timing differences or new patterns.",
                recommendation="Review the few remaining exceptions and consider adding matching rules for recurring patterns.",
                confidence=ActionConfidence.HIGH,
                materiality="low",
            ))
        elif match_rate >= 80:
            insights.append(FinanceInsight(
                insight_type=InsightType.RECOMMENDATION,
                title=f"Good match rate: {match_rate:.1f}%",
                reasoning="Match rate is acceptable but there's room for improvement. Review the unmatched items for common patterns that could be automated.",
                recommendation="I can learn from manual matches to improve future runs.",
                action="analyze_unmatched_patterns",
                confidence=ActionConfidence.MODERATE,
                materiality="medium",
            ))
        else:
            insights.append(FinanceInsight(
                insight_type=InsightType.RISK_ALERT,
                title=f"Low match rate: {match_rate:.1f}%",
                reasoning="This is below expected performance. Possible causes: data format issues, missing transactions, or significant business changes.",
                recommendation="Review data quality and consider adjusting match tolerances.",
                confidence=ActionConfidence.LOW,
                materiality="high",
            ))
        
        return ExpertResponse(
            summary=f"Reconciliation status: {match_rate:.1f}% match rate, {unmatched} unmatched transactions.",
            insights=insights,
            recommended_actions=[],
            context="Run reconciliation to process new transactions.",
            confidence=ActionConfidence.HIGH,
        )
    
    async def _provide_close_guidance(
        self,
        query: str,
        context: Optional[Dict],
    ) -> ExpertResponse:
        """Provide guidance on period close."""
        now = datetime.now()
        month_end = (now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        days_to_close = (month_end - now).days
        
        insights = []
        actions = []
        
        # Check open items
        open_exceptions = context.get("open_exceptions", 0) if context else 0
        pending_drafts = context.get("pending_drafts", 0) if context else 0
        
        insights.append(FinanceInsight(
            insight_type=InsightType.TIMING_DIFFERENCE,
            title=f"{days_to_close} days to month-end close",
            reasoning=f"You have {open_exceptions} open exceptions and {pending_drafts} pending journal entries. These should be resolved before close.",
            recommendation="Prioritize resolving exceptions and posting approved entries.",
            confidence=ActionConfidence.HIGH,
            materiality="high" if days_to_close <= 3 else "medium",
            timing_impact=f"Close deadline: {month_end.strftime('%B %d')}",
        ))
        
        if days_to_close <= 3 and (open_exceptions > 0 or pending_drafts > 0):
            insights.append(FinanceInsight(
                insight_type=InsightType.RISK_ALERT,
                title="Close deadline approaching",
                reasoning="With the close deadline in {days_to_close} days, unresolved items risk carrying into next period.",
                recommendation="Focus on material items first. Consider accruing for known but unposted transactions.",
                action="prepare_close_summary",
                confidence=ActionConfidence.HIGH,
                materiality="critical",
            ))
            actions.append({"action": "generate_close_checklist"})
        
        return ExpertResponse(
            summary=f"Period close in {days_to_close} days. {open_exceptions} exceptions and {pending_drafts} drafts outstanding.",
            insights=insights,
            recommended_actions=actions,
            context="I can help prioritize items by materiality and timing impact.",
            confidence=ActionConfidence.HIGH,
        )


# Singleton
_expert_agent: Optional[FinanceExpertAgent] = None


def get_finance_expert() -> FinanceExpertAgent:
    """Get the finance expert agent singleton."""
    global _expert_agent
    if _expert_agent is None:
        _expert_agent = FinanceExpertAgent()
    return _expert_agent
