"""
Batch Intelligence Service

Optimize bulk invoice operations:
- Intelligent batching for approvals
- Suggested groupings
- End-of-month processing
- Efficiency recommendations

Architecture: Part of the REASONING LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict

from clearledgr.services.priority_detection import get_priority_detection, PriorityLevel
from clearledgr.services.vendor_intelligence import get_vendor_intelligence

logger = logging.getLogger(__name__)


@dataclass
class BatchCategory:
    """A category of invoices for batch processing."""
    category_id: str
    name: str
    description: str
    invoices: List[Dict[str, Any]]
    total_amount: float
    recommended_action: str
    confidence: float
    risk_level: str  # "low", "medium", "high"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "category_id": self.category_id,
            "name": self.name,
            "description": self.description,
            "invoice_count": len(self.invoices),
            "total_amount": self.total_amount,
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "invoices": [
                {
                    "id": i.get("id"),
                    "vendor": i.get("vendor"),
                    "amount": i.get("amount"),
                }
                for i in self.invoices[:10]  # Limit detail
            ],
        }
    
    def to_slack_block(self) -> Dict[str, Any]:
        """Format for Slack display."""
        invoice_preview = "\n".join([
            f"  • {i.get('vendor', 'Unknown')}: ${i.get('amount', 0):,.2f}"
            for i in self.invoices[:5]
        ])
        
        if len(self.invoices) > 5:
            invoice_preview += f"\n  _...and {len(self.invoices) - 5} more_"
        
        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{self.name}* ({len(self.invoices)} invoices)\n"
                    f"Total: ${self.total_amount:,.2f}\n"
                    f"_{self.description}_\n"
                    f"{invoice_preview}"
                )
            },
            "accessory": {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": self.recommended_action
                },
                "value": f"batch_{self.category_id}",
                "action_id": f"batch_action_{self.category_id}",
            }
        }


@dataclass
class BatchPlan:
    """A batch processing plan."""
    plan_id: str
    created_at: str
    total_invoices: int
    total_amount: float
    categories: List[BatchCategory]
    efficiency_score: float  # 0-100, how much time this saves
    summary: str
    recommendations: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "total_invoices": self.total_invoices,
            "total_amount": self.total_amount,
            "efficiency_score": self.efficiency_score,
            "summary": self.summary,
            "categories": [c.to_dict() for c in self.categories],
            "recommendations": self.recommendations,
        }
    
    def to_slack_blocks(self) -> List[Dict[str, Any]]:
        """Format for Slack display."""
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Batch Processing Plan"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{self.total_invoices} invoices* totaling *${self.total_amount:,.2f}*\n"
                        f"_{self.summary}_\n\n"
                        f"Efficiency: {self.efficiency_score:.0f}% faster than individual review"
                    )
                }
            },
            {"type": "divider"},
        ]
        
        # Add each category
        for category in self.categories:
            blocks.append(category.to_slack_block())
        
        # Add recommendations
        if self.recommendations:
            rec_text = "\n".join([f"• {r}" for r in self.recommendations])
            blocks.append({
                "type": "section",
                "text": {
                "type": "mrkdwn",
                "text": f"*Recommendations:*\n{rec_text}"
            }
        })
        
        # Add action buttons
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Process All"
                    },
                    "style": "primary",
                    "value": f"process_all_{self.plan_id}",
                    "action_id": "batch_process_all",
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Review Individually"
                    },
                    "value": f"review_individual_{self.plan_id}",
                    "action_id": "batch_review_individual",
                },
            ]
        })
        
        return blocks


class BatchIntelligenceService:
    """
    Intelligently batches invoices for efficient processing.
    
    Usage:
        service = BatchIntelligenceService("org_123")
        
        # Create batch plan
        plan = service.create_batch_plan(invoices)
        
        # Show in Slack
        blocks = plan.to_slack_blocks()
        
        # Process a batch
        results = await service.process_batch("auto_approve")
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.priority_service = get_priority_detection(organization_id)
        self.vendor_intel = get_vendor_intelligence()
    
    def create_batch_plan(
        self,
        invoices: List[Dict[str, Any]],
    ) -> BatchPlan:
        """
        Create an intelligent batch processing plan.
        """
        # Enrich invoices with priority and vendor info
        enriched = self._enrich_invoices(invoices)
        
        # Categorize invoices
        categories = self._categorize_invoices(enriched)
        
        # Calculate totals
        total_amount = sum(i.get("amount", 0) for i in invoices)
        
        # Calculate efficiency score
        # Higher score = more invoices can be batch-processed
        auto_processable = sum(
            len(c.invoices) for c in categories 
            if c.recommended_action in ["Auto-approve", "Approve All"]
        )
        efficiency = (auto_processable / len(invoices) * 100) if invoices else 0
        
        # Generate summary
        summary = self._generate_summary(categories)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(categories, enriched)
        
        plan = BatchPlan(
            plan_id=f"batch_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            created_at=datetime.now().isoformat(),
            total_invoices=len(invoices),
            total_amount=total_amount,
            categories=categories,
            efficiency_score=efficiency,
            summary=summary,
            recommendations=recommendations,
        )
        
        logger.info(f"Created batch plan: {len(invoices)} invoices, {len(categories)} categories")
        
        return plan
    
    def _enrich_invoices(
        self,
        invoices: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Enrich invoices with priority and vendor intelligence."""
        enriched = []
        
        for invoice in invoices:
            inv = invoice.copy()
            
            # Add priority
            priority = self.priority_service.assess(invoice)
            inv["priority_level"] = priority.priority
            inv["priority_score"] = priority.score
            
            # Add vendor intelligence
            vendor = invoice.get("vendor", "")
            intel = self.vendor_intel.identify(vendor)
            if intel:
                inv["known_vendor"] = True
                inv["vendor_category"] = intel.category
            else:
                inv["known_vendor"] = False
            
            enriched.append(inv)
        
        return enriched
    
    def _categorize_invoices(
        self,
        invoices: List[Dict[str, Any]],
    ) -> List[BatchCategory]:
        """Categorize invoices for batch processing."""
        categories = []
        
        # Category 1: Auto-approve (recurring, low amount, known vendor)
        auto_approve = [
            i for i in invoices
            if (i.get("is_recurring", False) or 
                (i.get("known_vendor", False) and i.get("amount", 0) < 500))
            and i.get("priority_level") not in [PriorityLevel.CRITICAL]
        ]
        
        if auto_approve:
            categories.append(BatchCategory(
                category_id="auto_approve",
                name="Auto-Approve",
                description="Recurring invoices and small amounts from known vendors",
                invoices=auto_approve,
                total_amount=sum(i.get("amount", 0) for i in auto_approve),
                recommended_action="Auto-approve",
                confidence=0.95,
                risk_level="low",
            ))
        
        # Category 2: Quick review (known vendors, normal amounts)
        remaining = [i for i in invoices if i not in auto_approve]
        quick_review = [
            i for i in remaining
            if i.get("known_vendor", False) 
            and i.get("amount", 0) < 5000
            and i.get("priority_level") not in [PriorityLevel.CRITICAL]
        ]
        
        if quick_review:
            categories.append(BatchCategory(
                category_id="quick_review",
                name="Quick Review",
                description="Known vendors with normal amounts",
                invoices=quick_review,
                total_amount=sum(i.get("amount", 0) for i in quick_review),
                recommended_action="Approve All",
                confidence=0.85,
                risk_level="low",
            ))
        
        # Category 3: Standard review (needs attention)
        remaining = [i for i in remaining if i not in quick_review]
        standard_review = [
            i for i in remaining
            if i.get("priority_level") not in [PriorityLevel.CRITICAL, PriorityLevel.HIGH]
        ]
        
        if standard_review:
            categories.append(BatchCategory(
                category_id="standard_review",
                name="Standard Review",
                description="Requires individual review",
                invoices=standard_review,
                total_amount=sum(i.get("amount", 0) for i in standard_review),
                recommended_action="Review",
                confidence=0.7,
                risk_level="medium",
            ))
        
        # Category 4: Manual review (new vendors, large amounts, anomalies)
        manual_review = [i for i in remaining if i not in standard_review]
        
        if manual_review:
            categories.append(BatchCategory(
                category_id="manual_review",
                name="Manual Review Required",
                description="New vendors, large amounts, or anomalies detected",
                invoices=manual_review,
                total_amount=sum(i.get("amount", 0) for i in manual_review),
                recommended_action="Review Carefully",
                confidence=0.5,
                risk_level="high",
            ))
        
        # Category 5: Urgent (overdue or due very soon)
        urgent = [
            i for i in invoices
            if i.get("priority_level") in [PriorityLevel.CRITICAL]
        ]
        
        if urgent:
            # Insert at beginning
            categories.insert(0, BatchCategory(
                category_id="urgent",
                name="URGENT",
                description="Overdue or due within 24 hours",
                invoices=urgent,
                total_amount=sum(i.get("amount", 0) for i in urgent),
                recommended_action="Process Now",
                confidence=1.0,
                risk_level="high",
            ))
        
        return categories
    
    def _generate_summary(self, categories: List[BatchCategory]) -> str:
        """Generate a summary of the batch plan."""
        auto_count = sum(
            len(c.invoices) for c in categories 
            if c.category_id in ["auto_approve", "quick_review"]
        )
        manual_count = sum(
            len(c.invoices) for c in categories 
            if c.category_id in ["manual_review", "urgent"]
        )
        
        if manual_count == 0:
            return "All invoices can be batch-processed"
        elif auto_count > manual_count:
            return f"{auto_count} can be auto-processed, {manual_count} need review"
        else:
            return f"{manual_count} invoices need individual attention"
    
    def _generate_recommendations(
        self,
        categories: List[BatchCategory],
        invoices: List[Dict[str, Any]],
    ) -> List[str]:
        """Generate recommendations for the batch."""
        recommendations = []
        
        # Check for urgent items
        urgent = next((c for c in categories if c.category_id == "urgent"), None)
        if urgent:
            recommendations.append(
                f"Process {len(urgent.invoices)} urgent invoices first to avoid late fees"
            )
        
        # Check for new vendors
        new_vendors = [i for i in invoices if not i.get("known_vendor", True)]
        if new_vendors:
            recommendations.append(
                f"Verify {len(new_vendors)} new vendor(s) before approval"
            )
        
        # Check for large amounts
        large = [i for i in invoices if i.get("amount", 0) > 10000]
        if large:
            recommendations.append(
                f"Review {len(large)} invoice(s) over $10,000 individually"
            )
        
        # Group by vendor suggestion
        vendor_groups = defaultdict(list)
        for inv in invoices:
            vendor_groups[inv.get("vendor", "Unknown")].append(inv)
        
        for vendor, group in vendor_groups.items():
            if len(group) >= 3:
                recommendations.append(
                    f"Consider consolidated billing with {vendor} ({len(group)} invoices)"
                )
        
        return recommendations[:5]  # Limit to 5
    
    async def process_batch(
        self,
        category_id: str,
        invoices: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Process a batch of invoices.
        """
        results = {
            "processed": 0,
            "approved": 0,
            "flagged": 0,
            "errors": 0,
            "details": [],
        }
        
        for invoice in invoices:
            try:
                if category_id in ["auto_approve", "quick_review"]:
                    # Auto-approve logic
                    results["approved"] += 1
                    results["details"].append({
                        "invoice_id": invoice.get("id"),
                        "action": "approved",
                        "vendor": invoice.get("vendor"),
                    })
                else:
                    # Flag for individual review
                    results["flagged"] += 1
                    results["details"].append({
                        "invoice_id": invoice.get("id"),
                        "action": "flagged",
                        "vendor": invoice.get("vendor"),
                    })
                
                results["processed"] += 1
                
            except Exception as e:
                results["errors"] += 1
                logger.error(f"Error processing invoice {invoice.get('id')}: {e}")
        
        return results
    
    def get_end_of_month_summary(
        self,
        invoices: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Generate end-of-month processing summary."""
        plan = self.create_batch_plan(invoices)
        
        return {
            "title": "End of Month AP Summary",
            "total_invoices": plan.total_invoices,
            "total_amount": plan.total_amount,
            "ready_to_process": sum(
                len(c.invoices) for c in plan.categories
                if c.risk_level == "low"
            ),
            "needs_review": sum(
                len(c.invoices) for c in plan.categories
                if c.risk_level in ["medium", "high"]
            ),
            "efficiency_score": plan.efficiency_score,
            "estimated_time_saved": f"{plan.efficiency_score * 0.5:.0f} minutes",
            "plan": plan.to_dict(),
        }


# Convenience function
def get_batch_intelligence(organization_id: str = "default") -> BatchIntelligenceService:
    """Get a batch intelligence service instance."""
    return BatchIntelligenceService(organization_id=organization_id)
