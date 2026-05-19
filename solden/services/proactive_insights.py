"""
Proactive Insights Service

The agent doesn't just process - it advises. Identifies:
- Spending trends and anomalies
- Optimization opportunities (consolidation, early payment discounts)
- Risk alerts (concentration, unusual patterns)
- Budget warnings

Architecture: Part of the REASONING LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import json
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from solden.core.money import money_sum, money_to_float

from solden.core.database import get_db

logger = logging.getLogger(__name__)


@dataclass
class Insight:
    """A proactive insight from the agent."""
    insight_id: str
    category: str  # "spending", "optimization", "risk", "budget", "pattern"
    severity: str  # "info", "warning", "alert"
    title: str
    description: str
    data: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    actionable: bool = True
    
    def to_slack_block(self) -> Dict[str, Any]:
        """Convert to Slack block."""
        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{self.title}*\n{self.description}"
            }
        }


@dataclass
class InsightReport:
    """Collection of insights for an organization."""
    organization_id: str
    generated_at: str
    insights: List[Insight]
    summary: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "insights": [
                {
                    "id": i.insight_id,
                    "category": i.category,
                    "severity": i.severity,
                    "title": i.title,
                    "description": i.description,
                    "recommendations": i.recommendations,
                }
                for i in self.insights
            ],
        }


class ProactiveInsightsService:
    """
    Generates proactive insights about AP/spending patterns.
    
    Usage:
        service = ProactiveInsightsService("org_123")
        
        # Get insights after processing an invoice
        insights = service.analyze_after_invoice(invoice_data)
        
        # Get weekly insights digest
        report = service.generate_weekly_digest()
        
        # Check for immediate alerts
        alerts = service.check_for_alerts(invoice_data)
    """
    
    def __init__(self, organization_id: Optional[str] = None):
        from solden.core.org_utils import assert_org_id

        self.organization_id = assert_org_id(
            organization_id, context="ProactiveInsightsService"
        )
        self.db = get_db()
    
    def analyze_after_invoice(
        self,
        invoice: Dict[str, Any],
    ) -> List[Insight]:
        """
        Generate insights after processing an invoice.
        Called after each invoice to provide immediate feedback.
        """
        insights = []
        
        vendor = invoice.get("vendor", "Unknown")
        amount = invoice.get("amount", 0)
        
        # Get vendor history
        vendor_history = self._get_vendor_history(vendor)
        
        # Insight: Spending spike
        spike_insight = self._check_spending_spike(vendor, amount, vendor_history)
        if spike_insight:
            insights.append(spike_insight)
        
        # Insight: Frequent vendor
        frequency_insight = self._check_vendor_frequency(vendor, vendor_history)
        if frequency_insight:
            insights.append(frequency_insight)
        
        # Insight: New vendor
        if not vendor_history:
            insights.append(Insight(
                insight_id=f"new_vendor_{vendor[:10]}",
                category="pattern",
                severity="info",
                title=f"New Vendor: {vendor}",
                description=f"This is your first invoice from {vendor}.",
                recommendations=[
                    "Verify payment details are correct",
                    "Confirm this vendor is approved",
                ],
            ))
        
        # Insight: Large invoice
        if amount > 5000:
            insights.append(Insight(
                insight_id=f"large_invoice_{vendor[:10]}",
                category="spending",
                severity="warning" if amount > 10000 else "info",
                title=f"Large Invoice: ${amount:,.2f}",
                description=f"Invoice from {vendor} is above your typical threshold.",
                data={"amount": amount, "vendor": vendor},
                recommendations=[
                    "Ensure proper approval workflow",
                    "Verify against PO if applicable",
                ],
            ))
        
        return insights
    
    def check_for_alerts(
        self,
        invoice: Dict[str, Any],
    ) -> List[Insight]:
        """
        Check for immediate alerts that need attention.
        These are higher-priority than regular insights.
        """
        alerts = []
        
        vendor = invoice.get("vendor", "Unknown")
        amount = invoice.get("amount", 0)
        
        # Get recent spending
        recent_spend = self._get_recent_spending(days=30)
        vendor_spend = recent_spend.get(vendor, 0)
        
        # Alert: Vendor concentration
        total_spend = sum(recent_spend.values())
        if total_spend > 0:
            vendor_pct = (vendor_spend + amount) / (total_spend + amount) * 100
            if vendor_pct > 30:
                alerts.append(Insight(
                    insight_id=f"concentration_{vendor[:10]}",
                    category="risk",
                    severity="warning",
                    title=f"Vendor Concentration: {vendor}",
                    description=f"{vendor} represents {vendor_pct:.0f}% of your AP spend this month.",
                    data={"vendor": vendor, "percentage": vendor_pct},
                    recommendations=[
                        "Consider vendor diversification",
                        "Review vendor contract terms",
                    ],
                ))
        
        # Alert: Spending velocity
        velocity = self._check_spending_velocity()
        if velocity.get("alert"):
            alerts.append(Insight(
                insight_id="spending_velocity",
                category="budget",
                severity="alert",
                title="Spending Velocity Alert",
                description=velocity.get("message", ""),
                data=velocity.get("data", {}),
                recommendations=[
                    "Review upcoming invoices",
                    "Check against budget",
                ],
            ))
        
        return alerts
    
    def generate_weekly_digest(self) -> InsightReport:
        """
        Generate a weekly insights digest.
        Called periodically to provide summary insights.
        """
        insights = []
        
        # Get spending data
        current_week = self._get_recent_spending(days=7)
        previous_week = self._get_spending_for_period(days_ago_start=14, days_ago_end=7)
        
        # Insight: Week-over-week change
        current_total = sum(current_week.values())
        previous_total = sum(previous_week.values())
        
        if previous_total > 0:
            change_pct = (current_total - previous_total) / previous_total * 100
            if abs(change_pct) > 20:
                direction = "increased" if change_pct > 0 else "decreased"
                insights.append(Insight(
                    insight_id="wow_change",
                    category="spending",
                    severity="warning" if change_pct > 30 else "info",
                    title=f"Spending {direction} {abs(change_pct):.0f}% this week",
                    description=f"${current_total:,.2f} this week vs ${previous_total:,.2f} last week",
                    data={
                        "current": current_total,
                        "previous": previous_total,
                        "change_pct": change_pct,
                    },
                ))
        
        # Insight: Top vendors
        top_vendors = sorted(current_week.items(), key=lambda x: x[1], reverse=True)[:5]
        if top_vendors:
            vendor_list = ", ".join([f"{v[0]} (${v[1]:,.0f})" for v in top_vendors[:3]])
            insights.append(Insight(
                insight_id="top_vendors",
                category="pattern",
                severity="info",
                title="Top Vendors This Week",
                description=vendor_list,
                data={"vendors": dict(top_vendors)},
                actionable=False,
            ))
        
        # Insight: Upcoming payments (if due dates available)
        upcoming = self._get_upcoming_due()
        if upcoming:
            total_upcoming = money_to_float(money_sum(u.get("amount") for u in upcoming))
            insights.append(Insight(
                insight_id="upcoming_payments",
                category="budget",
                severity="info" if total_upcoming < 10000 else "warning",
                title=f"${total_upcoming:,.2f} due in next 7 days",
                description=f"{len(upcoming)} invoices coming due",
                data={"count": len(upcoming), "total": total_upcoming},
                recommendations=[
                    "Ensure sufficient funds",
                    "Review for early payment discounts",
                ],
            ))
        
        # Insight: Optimization opportunity
        recurring = self._find_recurring_patterns()
        if recurring:
            savings = sum(r.get("potential_savings", 0) for r in recurring)
            if savings > 0:
                insights.append(Insight(
                    insight_id="optimization",
                    category="optimization",
                    severity="info",
                    title=f"Potential savings: ${savings:,.2f}/month",
                    description=f"Found {len(recurring)} recurring expenses that could be optimized",
                    recommendations=[
                        "Review for annual payment discounts",
                        "Check for unused subscriptions",
                    ],
                ))
        
        # Generate summary
        alert_count = len([i for i in insights if i.severity == "alert"])
        warning_count = len([i for i in insights if i.severity == "warning"])
        
        if alert_count > 0:
            summary = f"{alert_count} alerts require attention"
        elif warning_count > 0:
            summary = f"{warning_count} items to review, overall healthy"
        else:
            summary = "AP operations running smoothly"
        
        return InsightReport(
            organization_id=self.organization_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            insights=insights,
            summary=summary,
        )
    
    def generate_daily_digest(self) -> InsightReport:
        """Generate a focused daily digest scoped to the last 24 hours.

        Returns the same InsightReport shape as generate_weekly_digest() so
        callers are uniform.  Content is intentionally narrower: today's
        invoice volume + any alert-severity items that need same-day attention.
        """
        insights = []

        # Today's invoice count vs yesterday
        today = self._get_recent_spending(days=1)
        yesterday = self._get_spending_for_period(days_ago_start=2, days_ago_end=1)
        today_total = sum(today.values())
        yesterday_total = sum(yesterday.values())
        today_count = len(today)

        if today_total > 0:
            insights.append(Insight(
                insight_id="daily_volume",
                category="spending",
                severity="info",
                title=f"${today_total:,.2f} in invoices processed today",
                description=(
                    f"Yesterday: ${yesterday_total:,.2f}"
                    if yesterday_total > 0 else "No invoices yesterday for comparison."
                ),
                data={"today_total": today_total, "yesterday_total": yesterday_total, "today_count": today_count},
                actionable=False,
            ))

        # Upcoming due today / overdue
        upcoming = self._get_upcoming_due()
        overdue = [u for u in upcoming if u.get("days_until_due", 1) <= 0]
        due_today = [u for u in upcoming if u.get("days_until_due", 99) == 0]
        if overdue:
            total = money_to_float(money_sum(u.get("amount") for u in overdue))
            insights.append(Insight(
                insight_id="overdue_daily",
                category="budget",
                severity="alert",
                title=f"{len(overdue)} overdue invoice(s) — ${total:,.2f}",
                description="These invoices have passed their due date and require action.",
                data={"count": len(overdue), "total": total},
                recommendations=["Review and action overdue invoices immediately."],
            ))
        if due_today:
            total = money_to_float(money_sum(u.get("amount") for u in due_today))
            insights.append(Insight(
                insight_id="due_today",
                category="budget",
                severity="warning",
                title=f"{len(due_today)} invoice(s) due today — ${total:,.2f}",
                description="These invoices are due today.",
                data={"count": len(due_today), "total": total},
                actionable=True,
            ))

        alert_count = len([i for i in insights if i.severity == "alert"])
        warning_count = len([i for i in insights if i.severity == "warning"])
        if alert_count > 0:
            summary = f"{alert_count} alert(s) require immediate attention"
        elif warning_count > 0:
            summary = f"{warning_count} item(s) to review today"
        elif today_total > 0:
            summary = f"${today_total:,.2f} processed today — no alerts"
        else:
            summary = "No invoice activity today"

        return InsightReport(
            organization_id=self.organization_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            insights=insights,
            summary=summary,
        )

    def _get_vendor_history(self, vendor: str, days: int = 90) -> List[Dict[str, Any]]:
        """Get historical invoices for a vendor."""
        try:
            return self.db.get_ap_items_by_vendor(
                organization_id=self.organization_id,
                vendor_name=vendor,
                days=days,
            ) or []
        except Exception:
            return []

    def _get_recent_spending(self, days: int = 30) -> Dict[str, float]:
        """Get spending by vendor for recent period."""
        try:
            return self.db.get_spending_by_vendor(
                organization_id=self.organization_id,
                days=days,
            ) or {}
        except Exception:
            return {}

    def _get_spending_for_period(
        self,
        days_ago_start: int,
        days_ago_end: int,
    ) -> Dict[str, float]:
        """Get spending for a specific period."""
        try:
            return self.db.get_spending_for_period(
                organization_id=self.organization_id,
                days_ago_start=days_ago_start,
                days_ago_end=days_ago_end,
            ) or {}
        except Exception:
            return {}
    
    def _check_spending_spike(
        self,
        vendor: str,
        amount: float,
        vendor_history: List[Dict[str, Any]],
    ) -> Optional[Insight]:
        """Check if this invoice represents a spending spike."""
        if not vendor_history or amount <= 0:
            return None
        
        historical_amounts = [h.get("amount", 0) for h in vendor_history if h.get("amount", 0) > 0]
        if not historical_amounts:
            return None
        
        avg_amount = sum(historical_amounts) / len(historical_amounts)
        if avg_amount <= 0:
            return None
        
        change_pct = (amount - avg_amount) / avg_amount * 100
        
        if change_pct > 50:
            return Insight(
                insight_id=f"spike_{vendor[:10]}",
                category="spending",
                severity="warning" if change_pct > 100 else "info",
                title=f"Spending spike: {vendor}",
                description=f"${amount:,.2f} is {change_pct:.0f}% higher than typical ${avg_amount:,.2f}",
                data={
                    "current": amount,
                    "average": avg_amount,
                    "change_pct": change_pct,
                },
                recommendations=[
                    "Verify this increase is expected",
                    "Check for price changes or scope expansion",
                ],
            )
        
        return None
    
    def _check_vendor_frequency(
        self,
        vendor: str,
        vendor_history: List[Dict[str, Any]],
    ) -> Optional[Insight]:
        """Check if we're receiving invoices too frequently."""
        if len(vendor_history) < 3:
            return None
        
        # Count invoices in last 7 days
        recent_count = 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        
        for inv in vendor_history:
            created = inv.get("created_at")
            if created:
                try:
                    if isinstance(created, str):
                        created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if created.replace(tzinfo=None) >= cutoff:
                        recent_count += 1
                except Exception:
                    pass
        
        if recent_count >= 3:
            return Insight(
                insight_id=f"frequency_{vendor[:10]}",
                category="pattern",
                severity="warning",
                title=f"Frequent invoices: {vendor}",
                description=f"Received {recent_count} invoices from {vendor} in the past week",
                recommendations=[
                    "Check for duplicate invoices",
                    "Consider consolidated billing",
                ],
            )
        
        return None
    
    def _check_spending_velocity(self) -> Dict[str, Any]:
        """Check if spending is on track vs typical."""
        # Simplified - would need budget data for full implementation
        return {"alert": False}
    
    def _get_upcoming_due(self) -> List[Dict[str, Any]]:
        """Get invoices due in next 7 days."""
        try:
            return self.db.get_upcoming_due(
                organization_id=self.organization_id,
                days=7,
            ) or []
        except Exception:
            return []
    
    def _find_recurring_patterns(self) -> List[Dict[str, Any]]:
        """Find recurring expenses that could be optimized."""
        # Simplified - would analyze for annual payment discounts, etc.
        return []


# Convenience function
def get_proactive_insights(organization_id: Optional[str] = None) -> ProactiveInsightsService:
    """Get a proactive insights service instance."""
    return ProactiveInsightsService(organization_id=organization_id)


# ---------------------------------------------------------------------------
# LLM narration — augments rule-detected insights with business-context copy.
# Same split as agent_anomaly_detection: rules decide what's notable, LLM
# rewrites the operator-facing description. Never changes which insights are
# surfaced; never gates a routing decision; falls back to rule copy on any
# failure (no API key, gateway timeout, JSON parse error).
# ---------------------------------------------------------------------------


_INSIGHT_NARRATION_PROMPT = """You are rewriting a list of rule-detected AP insights for a finance manager. \
The rules decided WHAT is notable; your job is to rewrite the description so it reads like advice from \
a colleague who knows this vendor's pattern, not a dashboard ticker.

Context:
- Organization: {org}
- Reference period: {period}

Rule-detected insights:
{insights_json}

Return JSON only, in the same order as the input:
{{
  "insights": [
    {{
      "id": "<echo back the insight_id verbatim>",
      "title": "<rewritten short title — keep under 80 chars>",
      "description": "<one or two sentences explaining what's likely going on AND what to look at, tied to the specific numbers>",
      "recommendations": ["<2-3 short concrete actions, sharper than the input>"]
    }}
  ]
}}

Constraints:
- Echo every insight_id verbatim. If you can't improve an insight, echo the input title/description.
- No prose outside the JSON.
- Do not invent data. Use only the numbers in the input.
- Prefer concrete checks ("Compare line items to the prior monthly invoice") over generic phrases ("Verify proper approval workflow")."""


async def narrate_insights(
    insights: List[Insight],
    *,
    organization_id: Optional[str] = None,
    period: str = "current",
) -> List[Insight]:
    """Augment a list of rule-detected insights with LLM-rendered copy.

    Returns a new list with the same length and IDs as the input. Any
    insight the LLM fails to improve falls back to the rule copy
    verbatim. The rules layer always decides which insights surface;
    this function never adds or removes insights.
    """
    if not insights:
        return insights

    from solden.core.org_utils import assert_org_id

    organization_id = assert_org_id(organization_id, context="narrate_insights")

    # Project to the minimum the LLM needs — keep the prompt cheap and
    # guard against accidentally leaking data the operator hasn't seen.
    projection = [
        {
            "id": i.insight_id,
            "category": i.category,
            "severity": i.severity,
            "title": i.title,
            "description": i.description,
            "recommendations": list(i.recommendations or []),
            "data": i.data or {},
        }
        for i in insights
    ]

    try:
        from solden.core.llm_gateway import LLMAction, get_llm_gateway

        gateway = get_llm_gateway()
        prompt = _INSIGHT_NARRATION_PROMPT.format(
            org=organization_id,
            period=period,
            insights_json=json.dumps(projection, indent=2, default=str),
        )
        resp = await gateway.call(
            LLMAction.NARRATE_INSIGHT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content if isinstance(resp.content, str) else ""
        if not raw:
            return insights
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        parsed = json.loads(text)
    except Exception as exc:
        logger.debug(
            "[proactive_insights] narration skipped (rule copy preserved): %s", exc,
        )
        return insights

    rewrites = {}
    for raw_item in (parsed.get("insights") or []):
        if not isinstance(raw_item, dict):
            continue
        rid = str(raw_item.get("id") or "")
        if rid:
            rewrites[rid] = raw_item

    enriched: List[Insight] = []
    for original in insights:
        rewrite = rewrites.get(original.insight_id)
        if not isinstance(rewrite, dict):
            enriched.append(original)
            continue
        new_title = str(rewrite.get("title") or "").strip() or original.title
        new_desc = str(rewrite.get("description") or "").strip() or original.description
        recs = rewrite.get("recommendations")
        new_recs = (
            [str(r).strip() for r in recs if r]
            if isinstance(recs, list) and recs
            else original.recommendations
        )
        enriched.append(Insight(
            insight_id=original.insight_id,
            category=original.category,
            severity=original.severity,
            title=new_title[:120],  # hard cap defensively
            description=new_desc,
            data=original.data,
            recommendations=new_recs,
            actionable=original.actionable,
        ))

    return enriched
