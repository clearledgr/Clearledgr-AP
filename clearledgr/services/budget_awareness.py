"""
Budget Awareness Service

Track spending against budgets:
- Category budgets
- Department budgets
- Project budgets
- Real-time alerts when approaching limits

Architecture: Part of the REASONING LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


class BudgetStatus(Enum):
    """Budget status levels."""
    HEALTHY = "healthy"       # < 70%
    WARNING = "warning"       # 70-85%
    CRITICAL = "critical"     # 85-100%
    EXCEEDED = "exceeded"     # > 100%


@dataclass
class Budget:
    """A budget definition."""
    budget_id: str
    name: str
    budget_type: str  # "category", "department", "project", "vendor"
    amount: float
    period: str  # "monthly", "quarterly", "annual"
    categories: List[str] = field(default_factory=list)
    departments: List[str] = field(default_factory=list)
    vendors: List[str] = field(default_factory=list)
    gl_codes: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "budget_id": self.budget_id,
            "name": self.name,
            "budget_type": self.budget_type,
            "amount": self.amount,
            "period": self.period,
            "categories": self.categories,
            "gl_codes": self.gl_codes,
        }


@dataclass
class BudgetCheck:
    """Result of checking invoice against budget."""
    budget: Budget
    spent: float
    remaining: float
    percent_used: float
    status: BudgetStatus
    invoice_amount: float
    after_approval: float
    after_approval_percent: float
    after_approval_status: BudgetStatus
    warning_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "budget_name": self.budget.name,
            "budget_amount": self.budget.amount,
            "spent": self.spent,
            "remaining": self.remaining,
            "percent_used": self.percent_used,
            "status": self.status.value,
            "invoice_amount": self.invoice_amount,
            "after_approval": self.after_approval,
            "after_approval_percent": self.after_approval_percent,
            "after_approval_status": self.after_approval_status.value,
            "warning_message": self.warning_message,
        }
    
    def to_slack_block(self) -> Dict[str, Any]:
        """Format for Slack display."""
        progress_bar = self._generate_progress_bar()
        
        text = (
            f"*{self.budget.name}* (Status: {self.after_approval_status.value.upper()})\n"
            f"Budget: ${self.budget.amount:,.0f}/mo\n"
            f"Spent: ${self.spent:,.0f} ({self.percent_used:.0f}%)\n"
            f"This invoice: ${self.invoice_amount:,.2f}\n"
            f"After approval: ${self.after_approval:,.0f} ({self.after_approval_percent:.0f}%)\n"
            f"{progress_bar}"
        )
        
        if self.warning_message:
            text += f"\nWarning: {self.warning_message}"
        
        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": text
            }
        }
    
    def _generate_progress_bar(self, width: int = 10) -> str:
        """Generate a text-based progress bar."""
        filled = int(self.after_approval_percent / 100 * width)
        filled = min(filled, width)
        
        bar = "█" * filled + "░" * (width - filled)
        return f"`[{bar}]` {self.after_approval_percent:.0f}%"


@dataclass
class BudgetReport:
    """Summary of all budgets for an organization."""
    organization_id: str
    period: str
    report_date: str
    budgets: List[BudgetCheck]
    total_budgeted: float
    total_spent: float
    overall_status: BudgetStatus
    alerts: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "period": self.period,
            "report_date": self.report_date,
            "total_budgeted": self.total_budgeted,
            "total_spent": self.total_spent,
            "overall_percent": (self.total_spent / self.total_budgeted * 100) if self.total_budgeted > 0 else 0,
            "overall_status": self.overall_status.value,
            "budgets": [b.to_dict() for b in self.budgets],
            "alerts": self.alerts,
        }


# Default budget categories
DEFAULT_BUDGETS = [
    Budget(
        budget_id="software",
        name="Software & Subscriptions",
        budget_type="category",
        amount=5000,
        period="monthly",
        categories=["software", "subscriptions", "saas"],
        gl_codes=["6150"],
    ),
    Budget(
        budget_id="cloud",
        name="Cloud & Infrastructure",
        budget_type="category",
        amount=10000,
        period="monthly",
        categories=["cloud", "hosting", "infrastructure"],
        gl_codes=["6200"],
    ),
    Budget(
        budget_id="marketing",
        name="Marketing & Advertising",
        budget_type="category",
        amount=15000,
        period="monthly",
        categories=["marketing", "advertising", "ads"],
        gl_codes=["6100"],
    ),
    Budget(
        budget_id="professional",
        name="Professional Services",
        budget_type="category",
        amount=8000,
        period="monthly",
        categories=["consulting", "legal", "professional services"],
        gl_codes=["6350"],
    ),
    Budget(
        budget_id="office",
        name="Office & Facilities",
        budget_type="category",
        amount=3000,
        period="monthly",
        categories=["office", "supplies", "facilities"],
        gl_codes=["6400", "6450"],
    ),
]


class BudgetAwarenessService:
    """
    Tracks spending against budgets.
    
    Usage:
        service = BudgetAwarenessService("org_123")
        
        # Check invoice against budgets
        checks = service.check_invoice(invoice_data)
        for check in checks:
            if check.status in [BudgetStatus.CRITICAL, BudgetStatus.EXCEEDED]:
                print(f"Warning: {check.warning_message}")
        
        # Get budget report
        report = service.get_report()
        print(f"Overall: {report.overall_status.value}")
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()
        self.budgets = self._load_budgets()
        
        # Track spending (would be from database in production)
        self._spending: Dict[str, float] = {}
    
    def _load_budgets(self) -> List[Budget]:
        """Load budgets for the organization."""
        try:
            if hasattr(self.db, 'get_budgets'):
                custom_budgets = self.db.get_budgets(self.organization_id)
                if custom_budgets:
                    return [self._dict_to_budget(b) for b in custom_budgets]
        except:
            pass
        
        return DEFAULT_BUDGETS.copy()
    
    def _dict_to_budget(self, data: Dict[str, Any]) -> Budget:
        """Convert dictionary to Budget."""
        return Budget(
            budget_id=data.get("budget_id", ""),
            name=data.get("name", ""),
            budget_type=data.get("budget_type", "category"),
            amount=data.get("amount", 0),
            period=data.get("period", "monthly"),
            categories=data.get("categories", []),
            departments=data.get("departments", []),
            vendors=data.get("vendors", []),
            gl_codes=data.get("gl_codes", []),
        )
    
    def check_invoice(self, invoice: Dict[str, Any]) -> List[BudgetCheck]:
        """
        Check an invoice against all applicable budgets.
        """
        checks = []
        
        invoice_amount = invoice.get("amount", 0)
        category = (invoice.get("category", "") or "").lower()
        gl_code = invoice.get("gl_code", "")
        vendor = (invoice.get("vendor", "") or "").lower()
        
        # Get vendor intelligence category if available
        vendor_intel = invoice.get("vendor_intelligence", {})
        intel_category = (vendor_intel.get("category", "") or "").lower()
        intel_subcategory = (vendor_intel.get("subcategory", "") or "").lower()
        suggested_gl = vendor_intel.get("suggested_gl", "")
        
        for budget in self.budgets:
            applicable = False
            
            # Check if budget applies to this invoice
            if budget.budget_type == "category":
                # Check categories
                for bc in budget.categories:
                    bc_lower = bc.lower()
                    if (bc_lower in category or 
                        bc_lower in intel_category or 
                        bc_lower in intel_subcategory):
                        applicable = True
                        break
                
                # Check GL codes
                if gl_code and gl_code in budget.gl_codes:
                    applicable = True
                if suggested_gl and suggested_gl in budget.gl_codes:
                    applicable = True
            
            elif budget.budget_type == "vendor":
                for bv in budget.vendors:
                    if bv.lower() in vendor:
                        applicable = True
                        break
            
            if applicable:
                check = self._check_budget(budget, invoice_amount)
                checks.append(check)
        
        return checks
    
    def _check_budget(
        self,
        budget: Budget,
        invoice_amount: float,
    ) -> BudgetCheck:
        """Check spending against a specific budget."""
        # Get current spending
        spent = self._get_budget_spending(budget.budget_id)
        
        # Calculate metrics
        remaining = budget.amount - spent
        percent_used = (spent / budget.amount * 100) if budget.amount > 0 else 0
        
        after_approval = spent + invoice_amount
        after_approval_percent = (after_approval / budget.amount * 100) if budget.amount > 0 else 0
        remaining_after = budget.amount - after_approval
        
        # Determine current status
        status = self._percent_to_status(percent_used)
        
        # Determine status after approval
        after_status = self._percent_to_status(after_approval_percent)
        
        # Generate warning if needed
        warning = None
        if after_status == BudgetStatus.EXCEEDED:
            warning = f"Will exceed budget by ${abs(remaining_after):,.2f}"
        elif after_status == BudgetStatus.CRITICAL:
            warning = f"Only ${remaining_after:,.2f} remaining after approval"
        elif after_status == BudgetStatus.WARNING and status == BudgetStatus.HEALTHY:
            warning = f"Will cross 70% threshold"
        
        return BudgetCheck(
            budget=budget,
            spent=spent,
            remaining=remaining,
            percent_used=percent_used,
            status=status,
            invoice_amount=invoice_amount,
            after_approval=after_approval,
            after_approval_percent=after_approval_percent,
            after_approval_status=after_status,
            warning_message=warning,
        )
    
    def _percent_to_status(self, percent: float) -> BudgetStatus:
        """Convert percentage to status."""
        if percent >= 100:
            return BudgetStatus.EXCEEDED
        elif percent >= 85:
            return BudgetStatus.CRITICAL
        elif percent >= 70:
            return BudgetStatus.WARNING
        else:
            return BudgetStatus.HEALTHY
    
    def _get_budget_spending(self, budget_id: str) -> float:
        """Get current spending for a budget."""
        # Check in-memory first
        if budget_id in self._spending:
            return self._spending[budget_id]
        
        # Try database
        try:
            if hasattr(self.db, 'get_budget_spending'):
                spent = self.db.get_budget_spending(
                    budget_id=budget_id,
                    organization_id=self.organization_id,
                    period="monthly",
                )
                if spent is not None:
                    self._spending[budget_id] = spent
                    return spent
        except:
            pass
        
        # Return 0 if no data
        return 0
    
    def record_spending(
        self,
        budget_id: str,
        amount: float,
    ) -> None:
        """Record spending against a budget."""
        current = self._get_budget_spending(budget_id)
        self._spending[budget_id] = current + amount
        
        # Persist to database
        try:
            if hasattr(self.db, 'record_budget_spending'):
                self.db.record_budget_spending(
                    budget_id=budget_id,
                    organization_id=self.organization_id,
                    amount=amount,
                )
        except:
            pass
    
    def get_report(self) -> BudgetReport:
        """
        Get a budget report for all budgets.
        """
        checks = []
        total_budgeted = 0
        total_spent = 0
        alerts = []
        
        for budget in self.budgets:
            spent = self._get_budget_spending(budget.budget_id)
            check = self._check_budget(budget, 0)  # No new invoice
            checks.append(check)
            
            total_budgeted += budget.amount
            total_spent += spent
            
            # Generate alerts
            if check.status == BudgetStatus.EXCEEDED:
                alerts.append(f"{budget.name}: EXCEEDED by ${spent - budget.amount:,.2f}")
            elif check.status == BudgetStatus.CRITICAL:
                alerts.append(f"{budget.name}: {check.percent_used:.0f}% used, ${check.remaining:,.2f} remaining")
            elif check.status == BudgetStatus.WARNING:
                alerts.append(f"{budget.name}: Approaching limit ({check.percent_used:.0f}%)")
        
        # Overall status
        overall_percent = (total_spent / total_budgeted * 100) if total_budgeted > 0 else 0
        overall_status = self._percent_to_status(overall_percent)
        
        return BudgetReport(
            organization_id=self.organization_id,
            period="monthly",
            report_date=datetime.now().isoformat(),
            budgets=checks,
            total_budgeted=total_budgeted,
            total_spent=total_spent,
            overall_status=overall_status,
            alerts=alerts,
        )
    
    def format_for_slack(self, invoice: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Format budget check for Slack."""
        checks = self.check_invoice(invoice)
        
        if not checks:
            return []
        
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Budget Impact*"
                }
            }
        ]
        
        for check in checks:
            blocks.append(check.to_slack_block())
        
        return blocks
    
    def format_report_slack(self) -> List[Dict[str, Any]]:
        """Format budget report for Slack."""
        report = self.get_report()
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Budget Report"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Period:* {report.period.capitalize()}\n"
                        f"*Total Budgeted:* ${report.total_budgeted:,.0f}\n"
                        f"*Total Spent:* ${report.total_spent:,.0f}\n"
                        f"*Status:* {report.overall_status.value.capitalize()}"
                    )
                }
            },
            {"type": "divider"},
        ]
        
        # Add each budget
        for check in report.budgets:
            blocks.append(check.to_slack_block())
        
        # Add alerts if any
        if report.alerts:
            alert_text = "\n".join(report.alerts)
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Alerts:*\n{alert_text}"
                }
            })
        
        return blocks


# Convenience function
def get_budget_awareness(organization_id: str = "default") -> BudgetAwarenessService:
    """Get a budget awareness service instance."""
    return BudgetAwarenessService(organization_id=organization_id)
