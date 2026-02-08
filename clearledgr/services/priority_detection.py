"""
Priority/Urgency Detection Service

Intelligently prioritize invoices based on:
- Due date proximity
- Vendor importance
- Amount significance
- Late payment penalties
- Relationship risk

Architecture: Part of the REASONING LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


class PriorityLevel(Enum):
    """Priority levels for invoices."""
    CRITICAL = "critical"  # Red - immediate action needed
    HIGH = "high"          # Orange - needs attention soon
    MEDIUM = "medium"      # Yellow - standard processing
    LOW = "low"            # Green - can wait

    @property
    def label(self) -> str:
        return {
            PriorityLevel.CRITICAL: "URGENT",
            PriorityLevel.HIGH: "SOON",
            PriorityLevel.MEDIUM: "NORMAL",
            PriorityLevel.LOW: "LOW",
        }.get(self, "UNKNOWN")


@dataclass
class PriorityFactor:
    """A factor contributing to priority score."""
    name: str
    score: float  # 0-1 contribution
    weight: float
    reason: str


@dataclass
class PriorityAssessment:
    """Priority assessment for an invoice."""
    invoice_id: str
    priority: PriorityLevel
    score: float  # 0-100
    factors: List[PriorityFactor]
    due_date: Optional[str]
    days_until_due: Optional[int]
    recommended_action: str
    alerts: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "priority": self.priority.value,
            "priority_label": self.priority.label,
            "score": self.score,
            "due_date": self.due_date,
            "days_until_due": self.days_until_due,
            "recommended_action": self.recommended_action,
            "factors": [
                {"name": f.name, "score": f.score, "reason": f.reason}
                for f in self.factors
            ],
            "alerts": self.alerts,
        }
    
    def to_slack_text(self) -> str:
        """Generate Slack text representation."""
        return f"*{self.priority.label}*: {self.recommended_action}"


class PriorityDetectionService:
    """
    Intelligently prioritizes invoices.
    
    Usage:
        service = PriorityDetectionService("org_123")
        
        # Assess single invoice
        assessment = service.assess(invoice_data)
        print(f"Priority: {assessment.priority.label}")
        print(f"Days until due: {assessment.days_until_due}")
        
        # Get prioritized queue
        queue = service.prioritize_queue(invoices)
        for invoice in queue:
            print(f"{invoice['priority_label']} {invoice['vendor']}: ${invoice['amount']}")
    """
    
    # Weight configuration
    WEIGHTS = {
        "due_date": 0.35,
        "amount": 0.20,
        "vendor_importance": 0.15,
        "penalty_risk": 0.15,
        "relationship_risk": 0.10,
        "age": 0.05,
    }
    
    # Strategic vendors (would be configurable per org)
    STRATEGIC_VENDORS = [
        "aws", "amazon web services",
        "gcp", "google cloud",
        "azure", "microsoft azure",
        "stripe",
        "salesforce",
    ]
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()
    
    def assess(self, invoice: Dict[str, Any]) -> PriorityAssessment:
        """
        Assess the priority of an invoice.
        """
        factors: List[PriorityFactor] = []
        alerts: List[str] = []
        
        invoice_id = invoice.get("id", "unknown")
        
        # Factor 1: Due date proximity
        due_factor, days_until_due, due_date = self._assess_due_date(invoice)
        factors.append(due_factor)
        
        if days_until_due is not None:
            if days_until_due < 0:
                alerts.append(f"OVERDUE by {abs(days_until_due)} days!")
            elif days_until_due == 0:
                alerts.append("Due TODAY!")
            elif days_until_due == 1:
                alerts.append("Due TOMORROW!")
        
        # Factor 2: Amount significance
        amount_factor = self._assess_amount(invoice)
        factors.append(amount_factor)
        
        # Factor 3: Vendor importance
        vendor_factor = self._assess_vendor_importance(invoice)
        factors.append(vendor_factor)
        
        # Factor 4: Late payment penalty risk
        penalty_factor = self._assess_penalty_risk(invoice)
        factors.append(penalty_factor)
        
        if penalty_factor.score > 0.5:
            alerts.append("Late payment penalty may apply")
        
        # Factor 5: Relationship risk
        relationship_factor = self._assess_relationship_risk(invoice)
        factors.append(relationship_factor)
        
        # Factor 6: Invoice age
        age_factor = self._assess_age(invoice)
        factors.append(age_factor)
        
        # Calculate overall score (0-100)
        score = sum(f.score * f.weight * 100 for f in factors)
        
        # Determine priority level
        priority = self._score_to_priority(score, days_until_due)
        
        # Generate recommended action
        recommended_action = self._generate_recommendation(priority, days_until_due, invoice)
        
        logger.info(f"Priority assessment for {invoice_id}: {priority.value} (score: {score:.1f})")
        
        return PriorityAssessment(
            invoice_id=invoice_id,
            priority=priority,
            score=score,
            factors=factors,
            due_date=due_date,
            days_until_due=days_until_due,
            recommended_action=recommended_action,
            alerts=alerts,
        )
    
    def _assess_due_date(
        self,
        invoice: Dict[str, Any],
    ) -> Tuple[PriorityFactor, Optional[int], Optional[str]]:
        """Assess urgency based on due date."""
        due_date_str = invoice.get("due_date")
        
        if not due_date_str:
            return PriorityFactor(
                name="Due Date",
                score=0.5,  # Unknown = medium priority
                weight=self.WEIGHTS["due_date"],
                reason="No due date specified",
            ), None, None
        
        try:
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            days_until = (due_date - today).days
            
            # Score based on days until due
            if days_until < 0:  # Overdue
                score = 1.0
                reason = f"Overdue by {abs(days_until)} days"
            elif days_until == 0:
                score = 1.0
                reason = "Due today"
            elif days_until == 1:
                score = 0.95
                reason = "Due tomorrow"
            elif days_until <= 3:
                score = 0.85
                reason = f"Due in {days_until} days"
            elif days_until <= 7:
                score = 0.7
                reason = f"Due in {days_until} days"
            elif days_until <= 14:
                score = 0.5
                reason = f"Due in {days_until} days"
            elif days_until <= 30:
                score = 0.3
                reason = f"Due in {days_until} days"
            else:
                score = 0.1
                reason = f"Due in {days_until} days"
            
            return PriorityFactor(
                name="Due Date",
                score=score,
                weight=self.WEIGHTS["due_date"],
                reason=reason,
            ), days_until, due_date_str
            
        except Exception as e:
            logger.warning(f"Error parsing due date: {e}")
            return PriorityFactor(
                name="Due Date",
                score=0.5,
                weight=self.WEIGHTS["due_date"],
                reason="Could not parse due date",
            ), None, due_date_str
    
    def _assess_amount(self, invoice: Dict[str, Any]) -> PriorityFactor:
        """Assess priority based on amount."""
        amount = invoice.get("amount", 0)
        
        # Score based on amount thresholds
        if amount >= 50000:
            score = 1.0
            reason = f"Large invoice: ${amount:,.2f}"
        elif amount >= 10000:
            score = 0.8
            reason = f"Significant amount: ${amount:,.2f}"
        elif amount >= 5000:
            score = 0.6
            reason = f"Medium-high amount: ${amount:,.2f}"
        elif amount >= 1000:
            score = 0.4
            reason = f"Standard amount: ${amount:,.2f}"
        elif amount >= 100:
            score = 0.2
            reason = f"Small amount: ${amount:,.2f}"
        else:
            score = 0.1
            reason = f"Minor amount: ${amount:,.2f}"
        
        return PriorityFactor(
            name="Amount",
            score=score,
            weight=self.WEIGHTS["amount"],
            reason=reason,
        )
    
    def _assess_vendor_importance(self, invoice: Dict[str, Any]) -> PriorityFactor:
        """Assess priority based on vendor strategic importance."""
        vendor = invoice.get("vendor", "").lower()
        
        # Check if strategic vendor
        is_strategic = any(sv in vendor for sv in self.STRATEGIC_VENDORS)
        
        if is_strategic:
            return PriorityFactor(
                name="Vendor Importance",
                score=0.9,
                weight=self.WEIGHTS["vendor_importance"],
                reason=f"Strategic vendor: {invoice.get('vendor')}",
            )
        
        # Check vendor intel if available
        vendor_intel = invoice.get("vendor_intelligence", {})
        if vendor_intel.get("known_vendor"):
            return PriorityFactor(
                name="Vendor Importance",
                score=0.5,
                weight=self.WEIGHTS["vendor_importance"],
                reason="Known vendor",
            )
        
        return PriorityFactor(
            name="Vendor Importance",
            score=0.3,
            weight=self.WEIGHTS["vendor_importance"],
            reason="Standard vendor priority",
        )
    
    def _assess_penalty_risk(self, invoice: Dict[str, Any]) -> PriorityFactor:
        """Assess late payment penalty risk."""
        # Check for explicit penalty terms
        terms = invoice.get("payment_terms", "").lower()
        notes = invoice.get("notes", "").lower()
        
        has_penalty_terms = any(
            term in terms or term in notes
            for term in ["penalty", "late fee", "interest", "1.5%", "2%"]
        )
        
        if has_penalty_terms:
            return PriorityFactor(
                name="Penalty Risk",
                score=0.9,
                weight=self.WEIGHTS["penalty_risk"],
                reason="Late payment penalty terms detected",
            )
        
        # Certain vendor types typically have penalties
        vendor = invoice.get("vendor", "").lower()
        penalty_vendors = ["utility", "insurance", "tax", "government", "lease"]
        
        if any(pv in vendor for pv in penalty_vendors):
            return PriorityFactor(
                name="Penalty Risk",
                score=0.7,
                weight=self.WEIGHTS["penalty_risk"],
                reason="Vendor type typically has penalties",
            )
        
        return PriorityFactor(
            name="Penalty Risk",
            score=0.2,
            weight=self.WEIGHTS["penalty_risk"],
            reason="No penalty terms detected",
        )
    
    def _assess_relationship_risk(self, invoice: Dict[str, Any]) -> PriorityFactor:
        """Assess risk to vendor relationship."""
        # Check payment history
        vendor = invoice.get("vendor", "")
        
        try:
            if hasattr(self.db, 'get_payment_history'):
                history = self.db.get_payment_history(
                    vendor=vendor,
                    organization_id=self.organization_id,
                ) or []
                
                # Check for recent late payments
                late_count = sum(1 for h in history if h.get("was_late", False))
                if late_count >= 2:
                    return PriorityFactor(
                        name="Relationship Risk",
                        score=0.9,
                        weight=self.WEIGHTS["relationship_risk"],
                        reason=f"Previous late payments to this vendor ({late_count})",
                    )
        except:
            pass
        
        return PriorityFactor(
            name="Relationship Risk",
            score=0.3,
            weight=self.WEIGHTS["relationship_risk"],
            reason="Standard relationship risk",
        )
    
    def _assess_age(self, invoice: Dict[str, Any]) -> PriorityFactor:
        """Assess priority based on how long invoice has been waiting."""
        created_at = invoice.get("created_at")
        
        if not created_at:
            return PriorityFactor(
                name="Age",
                score=0.3,
                weight=self.WEIGHTS["age"],
                reason="Unknown age",
            )
        
        try:
            if isinstance(created_at, str):
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            else:
                created = created_at
            
            age_days = (datetime.now(created.tzinfo) - created).days
            
            if age_days > 14:
                score = 0.9
                reason = f"Waiting {age_days} days (should be prioritized)"
            elif age_days > 7:
                score = 0.6
                reason = f"Waiting {age_days} days"
            elif age_days > 3:
                score = 0.4
                reason = f"Waiting {age_days} days"
            else:
                score = 0.2
                reason = f"Recently received ({age_days} days)"
            
            return PriorityFactor(
                name="Age",
                score=score,
                weight=self.WEIGHTS["age"],
                reason=reason,
            )
            
        except Exception as e:
            logger.warning(f"Error calculating age: {e}")
            return PriorityFactor(
                name="Age",
                score=0.3,
                weight=self.WEIGHTS["age"],
                reason="Could not determine age",
            )
    
    def _score_to_priority(
        self,
        score: float,
        days_until_due: Optional[int],
    ) -> PriorityLevel:
        """Convert score to priority level."""
        # Override with critical if overdue or due very soon
        if days_until_due is not None:
            if days_until_due < 0:
                return PriorityLevel.CRITICAL
            elif days_until_due <= 1:
                return PriorityLevel.CRITICAL
            elif days_until_due <= 3:
                return PriorityLevel.HIGH
        
        # Score-based priority
        if score >= 70:
            return PriorityLevel.CRITICAL
        elif score >= 50:
            return PriorityLevel.HIGH
        elif score >= 30:
            return PriorityLevel.MEDIUM
        else:
            return PriorityLevel.LOW
    
    def _generate_recommendation(
        self,
        priority: PriorityLevel,
        days_until_due: Optional[int],
        invoice: Dict[str, Any],
    ) -> str:
        """Generate action recommendation."""
        vendor = invoice.get("vendor", "Invoice")
        amount = invoice.get("amount", 0)
        
        if priority == PriorityLevel.CRITICAL:
            if days_until_due is not None and days_until_due < 0:
                return f"OVERDUE: Pay {vendor} (${amount:,.2f}) immediately"
            elif days_until_due == 0:
                return f"Due TODAY: Process {vendor} (${amount:,.2f}) now"
            elif days_until_due == 1:
                return f"Due TOMORROW: Approve {vendor} (${amount:,.2f}) today"
            else:
                return f"Process {vendor} (${amount:,.2f}) urgently"
        
        elif priority == PriorityLevel.HIGH:
            return f"Review {vendor} (${amount:,.2f}) - due in {days_until_due or 'few'} days"
        
        elif priority == PriorityLevel.MEDIUM:
            return f"Process {vendor} (${amount:,.2f}) this week"
        
        else:
            return f"Process {vendor} (${amount:,.2f}) when convenient"
    
    def prioritize_queue(
        self,
        invoices: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Sort invoices by priority.
        """
        assessed = []
        
        for invoice in invoices:
            assessment = self.assess(invoice)
            invoice_with_priority = invoice.copy()
            invoice_with_priority.update({
                "priority": assessment.priority.value,
                "priority_label": assessment.priority.label,
                "priority_score": assessment.score,
                "days_until_due": assessment.days_until_due,
                "recommended_action": assessment.recommended_action,
                "alerts": assessment.alerts,
            })
            assessed.append(invoice_with_priority)
        
        # Sort by priority score (descending)
        assessed.sort(key=lambda x: x["priority_score"], reverse=True)
        
        return assessed
    
    def get_urgent_summary(self, invoices: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get summary of urgent items."""
        prioritized = self.prioritize_queue(invoices)
        
        critical = [i for i in prioritized if i["priority"] == "critical"]
        high = [i for i in prioritized if i["priority"] == "high"]
        
        return {
            "critical_count": len(critical),
            "critical_total": sum(i.get("amount", 0) for i in critical),
            "high_count": len(high),
            "high_total": sum(i.get("amount", 0) for i in high),
            "critical_items": critical[:5],  # Top 5 most critical
            "high_items": high[:5],
        }
    
    def format_priority_slack(self, invoices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Format prioritized queue for Slack."""
        prioritized = self.prioritize_queue(invoices)
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Invoice Priority Queue"
                }
            }
        ]
        
        # Group by priority
        by_priority = {}
        for inv in prioritized:
            p = inv["priority"]
            if p not in by_priority:
                by_priority[p] = []
            by_priority[p].append(inv)
        
        for priority in ["critical", "high", "medium", "low"]:
            if priority not in by_priority:
                continue
            
            items = by_priority[priority]
            
            item_text = "\n".join([
                f"• {i.get('vendor', 'Unknown')}: ${i.get('amount', 0):,.2f} "
                f"({'due ' + str(i['days_until_due']) + 'd' if i.get('days_until_due') is not None else 'no due date'})"
                for i in items[:5]
            ])
            
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{priority.upper()}* ({len(items)})\n{item_text}"
                }
            })
        
        return blocks


# Convenience function
def get_priority_detection(organization_id: str = "default") -> PriorityDetectionService:
    """Get a priority detection service instance."""
    return PriorityDetectionService(organization_id=organization_id)
