"""
Accruals Management Service

Handles AP accruals and month-end processes:
- Automatic accrual generation
- Accrual reversal
- Goods received not invoiced (GRNI)
- Invoice received not goods (IRNG)
- Month-end cut-off
"""

import logging
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
import uuid

logger = logging.getLogger(__name__)


class AccrualType(Enum):
    """Types of accruals."""
    GRNI = "grni"           # Goods Received Not Invoiced
    IRNG = "irng"           # Invoice Received Not Goods (prepayment)
    EXPENSE = "expense"     # Expense accrual
    UTILITY = "utility"     # Utility accrual (estimated)
    PAYROLL = "payroll"     # Payroll accrual
    INTEREST = "interest"   # Interest accrual
    RENT = "rent"           # Rent accrual
    INSURANCE = "insurance" # Insurance accrual
    CUSTOM = "custom"       # Custom accrual


class AccrualStatus(Enum):
    """Status of an accrual."""
    DRAFT = "draft"
    POSTED = "posted"
    REVERSED = "reversed"
    ADJUSTED = "adjusted"
    CANCELLED = "cancelled"


@dataclass
class AccrualLine:
    """Line item in an accrual entry."""
    line_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    account_code: str = ""
    account_name: str = ""
    debit: float = 0.0
    credit: float = 0.0
    description: str = ""
    department: str = ""
    cost_center: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "line_id": self.line_id,
            "account_code": self.account_code,
            "account_name": self.account_name,
            "debit": self.debit,
            "credit": self.credit,
            "description": self.description,
            "department": self.department,
            "cost_center": self.cost_center,
        }


@dataclass
class AccrualEntry:
    """Accrual journal entry."""
    accrual_id: str = field(default_factory=lambda: f"ACC-{uuid.uuid4().hex[:8].upper()}")
    accrual_type: AccrualType = AccrualType.EXPENSE
    
    # Reference
    reference_id: str = ""      # PO ID, Invoice ID, etc.
    reference_type: str = ""    # po, invoice, gr, estimate
    vendor_id: str = ""
    vendor_name: str = ""
    
    # Period
    accrual_date: date = field(default_factory=date.today)
    period_month: int = 0
    period_year: int = 0
    
    # Amount
    amount: float = 0.0
    currency: str = "USD"
    
    # Journal entry lines
    lines: List[AccrualLine] = field(default_factory=list)
    
    # Status
    status: AccrualStatus = AccrualStatus.DRAFT
    
    # Reversal tracking
    auto_reverse: bool = True
    reversal_date: Optional[date] = None
    reversed_by_id: str = ""     # ID of reversing entry
    reverses_id: str = ""        # ID of entry this reverses
    
    # Posting
    posted_at: Optional[datetime] = None
    posted_by: str = ""
    erp_journal_id: str = ""
    
    # Metadata
    description: str = ""
    notes: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    organization_id: str = "default"
    
    def total_debits(self) -> float:
        return sum(line.debit for line in self.lines)
    
    def total_credits(self) -> float:
        return sum(line.credit for line in self.lines)
    
    def is_balanced(self) -> bool:
        return abs(self.total_debits() - self.total_credits()) < 0.01
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "accrual_id": self.accrual_id,
            "accrual_type": self.accrual_type.value,
            "reference_id": self.reference_id,
            "reference_type": self.reference_type,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "accrual_date": self.accrual_date.isoformat(),
            "period": f"{self.period_year}-{self.period_month:02d}",
            "amount": self.amount,
            "currency": self.currency,
            "lines": [line.to_dict() for line in self.lines],
            "status": self.status.value,
            "auto_reverse": self.auto_reverse,
            "reversal_date": self.reversal_date.isoformat() if self.reversal_date else None,
            "is_balanced": self.is_balanced(),
            "description": self.description,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class AccrualSchedule:
    """Schedule for recurring accruals."""
    schedule_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    accrual_type: AccrualType = AccrualType.EXPENSE
    
    # Amount
    monthly_amount: float = 0.0
    currency: str = "USD"
    
    # Accounts
    expense_account: str = ""
    accrual_account: str = ""
    
    # Reference
    vendor_id: str = ""
    vendor_name: str = ""
    description: str = ""
    
    # Schedule
    start_date: date = field(default_factory=date.today)
    end_date: Optional[date] = None
    is_active: bool = True
    
    # Tracking
    last_run_date: Optional[date] = None
    total_accrued: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "name": self.name,
            "accrual_type": self.accrual_type.value,
            "monthly_amount": self.monthly_amount,
            "expense_account": self.expense_account,
            "accrual_account": self.accrual_account,
            "vendor_name": self.vendor_name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "is_active": self.is_active,
            "total_accrued": self.total_accrued,
        }


class AccrualsService:
    """
    Service for managing AP accruals and month-end processes.
    """
    
    # Default GL accounts (configurable)
    DEFAULT_ACCOUNTS = {
        "accrued_expenses": "2100",
        "accrued_liabilities": "2110",
        "grni_clearing": "2120",
        "expense_accrual": "6900",
    }
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self._accruals: Dict[str, AccrualEntry] = {}
        self._schedules: Dict[str, AccrualSchedule] = {}
        self._accounts = dict(self.DEFAULT_ACCOUNTS)
    
    def create_grni_accrual(
        self,
        po_id: str,
        po_number: str,
        vendor_name: str,
        amount: float,
        expense_account: str,
        department: str = "",
        accrual_date: date = None,
    ) -> AccrualEntry:
        """
        Create GRNI (Goods Received Not Invoiced) accrual.
        
        Debit:  Expense Account
        Credit: GRNI Clearing
        """
        accrual_date = accrual_date or date.today()
        
        entry = AccrualEntry(
            accrual_type=AccrualType.GRNI,
            reference_id=po_id,
            reference_type="po",
            vendor_name=vendor_name,
            amount=amount,
            accrual_date=accrual_date,
            period_month=accrual_date.month,
            period_year=accrual_date.year,
            description=f"GRNI Accrual - PO {po_number} - {vendor_name}",
            auto_reverse=True,
            reversal_date=self._get_first_of_next_month(accrual_date),
            organization_id=self.organization_id,
        )
        
        # Debit expense
        entry.lines.append(AccrualLine(
            account_code=expense_account,
            debit=amount,
            description=f"GRNI - {vendor_name}",
            department=department,
        ))
        
        # Credit GRNI clearing
        entry.lines.append(AccrualLine(
            account_code=self._accounts["grni_clearing"],
            credit=amount,
            description=f"GRNI Clearing - PO {po_number}",
        ))
        
        self._accruals[entry.accrual_id] = entry
        logger.info(f"Created GRNI accrual: {entry.accrual_id} for ${amount:.2f}")
        
        return entry
    
    def create_expense_accrual(
        self,
        vendor_name: str,
        amount: float,
        expense_account: str,
        description: str,
        department: str = "",
        accrual_date: date = None,
    ) -> AccrualEntry:
        """
        Create general expense accrual.
        
        Debit:  Expense Account
        Credit: Accrued Expenses
        """
        accrual_date = accrual_date or date.today()
        
        entry = AccrualEntry(
            accrual_type=AccrualType.EXPENSE,
            reference_type="estimate",
            vendor_name=vendor_name,
            amount=amount,
            accrual_date=accrual_date,
            period_month=accrual_date.month,
            period_year=accrual_date.year,
            description=description,
            auto_reverse=True,
            reversal_date=self._get_first_of_next_month(accrual_date),
            organization_id=self.organization_id,
        )
        
        # Debit expense
        entry.lines.append(AccrualLine(
            account_code=expense_account,
            debit=amount,
            description=description,
            department=department,
        ))
        
        # Credit accrued expenses
        entry.lines.append(AccrualLine(
            account_code=self._accounts["accrued_expenses"],
            credit=amount,
            description=f"Accrued - {vendor_name}",
        ))
        
        self._accruals[entry.accrual_id] = entry
        logger.info(f"Created expense accrual: {entry.accrual_id} for ${amount:.2f}")
        
        return entry
    
    def create_utility_accrual(
        self,
        utility_type: str,  # electric, gas, water, internet, phone
        estimated_amount: float,
        expense_account: str,
        accrual_date: date = None,
    ) -> AccrualEntry:
        """Create utility accrual based on estimate."""
        return self.create_expense_accrual(
            vendor_name=f"{utility_type.title()} Provider",
            amount=estimated_amount,
            expense_account=expense_account,
            description=f"{utility_type.title()} Accrual - Estimated",
            accrual_date=accrual_date,
        )

    def create_payroll_accrual(
        self,
        payroll_period: str,
        amount: float,
        expense_account: str = "6200",
        accrual_date: date = None,
        department: str = "",
        vendor_name: str = "Payroll",
    ) -> AccrualEntry:
        """
        Create a payroll accrual.

        Debit: Payroll expense
        Credit: Accrued expenses
        """
        accrual_date = accrual_date or date.today()
        description = f"Payroll Accrual - {payroll_period}"
        entry = AccrualEntry(
            accrual_type=AccrualType.PAYROLL,
            reference_id=payroll_period,
            reference_type="payroll",
            vendor_name=vendor_name,
            amount=amount,
            accrual_date=accrual_date,
            period_month=accrual_date.month,
            period_year=accrual_date.year,
            description=description,
            auto_reverse=True,
            reversal_date=self._get_first_of_next_month(accrual_date),
            organization_id=self.organization_id,
        )

        entry.lines.append(AccrualLine(
            account_code=expense_account,
            debit=amount,
            description=description,
            department=department,
        ))
        entry.lines.append(AccrualLine(
            account_code=self._accounts["accrued_expenses"],
            credit=amount,
            description=f"Accrued payroll - {payroll_period}",
            department=department,
        ))

        self._accruals[entry.accrual_id] = entry
        logger.info(f"Created payroll accrual: {entry.accrual_id} for ${amount:.2f}")
        return entry
    
    def post_accrual(self, accrual_id: str, posted_by: str) -> AccrualEntry:
        """Post an accrual entry."""
        entry = self._accruals.get(accrual_id)
        if not entry:
            raise ValueError(f"Accrual {accrual_id} not found")
        
        if not entry.is_balanced():
            raise ValueError("Accrual entry is not balanced")
        
        entry.status = AccrualStatus.POSTED
        entry.posted_at = datetime.now()
        entry.posted_by = posted_by
        entry.updated_at = datetime.now()
        
        logger.info(f"Posted accrual: {accrual_id}")
        return entry
    
    def reverse_accrual(
        self,
        accrual_id: str,
        reversal_date: date = None,
    ) -> AccrualEntry:
        """
        Create reversing entry for an accrual.
        """
        original = self._accruals.get(accrual_id)
        if not original:
            raise ValueError(f"Accrual {accrual_id} not found")
        
        reversal_date = reversal_date or original.reversal_date or date.today()
        
        # Create reversing entry (swap debits and credits)
        reversal = AccrualEntry(
            accrual_type=original.accrual_type,
            reference_id=original.reference_id,
            reference_type=original.reference_type,
            vendor_name=original.vendor_name,
            amount=-original.amount,
            accrual_date=reversal_date,
            period_month=reversal_date.month,
            period_year=reversal_date.year,
            description=f"Reversal: {original.description}",
            auto_reverse=False,
            reverses_id=original.accrual_id,
            status=AccrualStatus.POSTED,
            posted_at=datetime.now(),
            organization_id=self.organization_id,
        )
        
        # Reverse the lines
        for line in original.lines:
            reversal.lines.append(AccrualLine(
                account_code=line.account_code,
                account_name=line.account_name,
                debit=line.credit,  # Swap
                credit=line.debit,  # Swap
                description=f"Reversal: {line.description}",
                department=line.department,
                cost_center=line.cost_center,
            ))
        
        self._accruals[reversal.accrual_id] = reversal
        
        # Update original
        original.status = AccrualStatus.REVERSED
        original.reversed_by_id = reversal.accrual_id
        original.updated_at = datetime.now()
        
        logger.info(f"Reversed accrual {accrual_id} with {reversal.accrual_id}")
        return reversal
    
    def _get_first_of_next_month(self, d: date) -> date:
        """Get first day of next month."""
        if d.month == 12:
            return date(d.year + 1, 1, 1)
        return date(d.year, d.month + 1, 1)
    
    def create_schedule(
        self,
        name: str,
        monthly_amount: float,
        expense_account: str,
        accrual_account: str = "",
        vendor_name: str = "",
        accrual_type: AccrualType = AccrualType.EXPENSE,
        **kwargs
    ) -> AccrualSchedule:
        """Create a recurring accrual schedule."""
        schedule = AccrualSchedule(
            name=name,
            accrual_type=accrual_type,
            monthly_amount=monthly_amount,
            expense_account=expense_account,
            accrual_account=accrual_account or self._accounts["accrued_expenses"],
            vendor_name=vendor_name,
            **kwargs
        )
        
        self._schedules[schedule.schedule_id] = schedule
        logger.info(f"Created accrual schedule: {name}")
        return schedule
    
    def run_scheduled_accruals(self, for_date: date = None) -> List[AccrualEntry]:
        """
        Run all active scheduled accruals for a given date.
        Returns list of created accrual entries.
        """
        for_date = for_date or date.today()
        created = []
        
        for schedule in self._schedules.values():
            if not schedule.is_active:
                continue
            
            if schedule.end_date and for_date > schedule.end_date:
                continue
            
            if for_date < schedule.start_date:
                continue
            
            # Check if already run for this month
            if (schedule.last_run_date and 
                schedule.last_run_date.year == for_date.year and
                schedule.last_run_date.month == for_date.month):
                continue
            
            # Create accrual
            entry = self.create_expense_accrual(
                vendor_name=schedule.vendor_name or schedule.name,
                amount=schedule.monthly_amount,
                expense_account=schedule.expense_account,
                description=f"Scheduled: {schedule.name}",
                accrual_date=for_date,
            )
            
            # Update schedule
            schedule.last_run_date = for_date
            schedule.total_accrued += schedule.monthly_amount
            
            created.append(entry)
        
        logger.info(f"Created {len(created)} scheduled accruals for {for_date}")
        return created
    
    def run_month_end(
        self,
        month: int,
        year: int,
        post_entries: bool = False,
        posted_by: str = "system",
    ) -> Dict[str, Any]:
        """
        Run month-end accrual process.
        
        1. Generate scheduled accruals
        2. Reverse prior month auto-reverse accruals
        3. Optionally post all entries
        """
        results = {
            "period": f"{year}-{month:02d}",
            "scheduled_created": 0,
            "reversals_created": 0,
            "posted": 0,
            "errors": [],
        }
        
        month_end_date = date(year, month, 1) + timedelta(days=32)
        month_end_date = date(month_end_date.year, month_end_date.month, 1) - timedelta(days=1)
        
        # 1. Run scheduled accruals
        try:
            scheduled = self.run_scheduled_accruals(month_end_date)
            results["scheduled_created"] = len(scheduled)
        except Exception as e:
            results["errors"].append(f"Scheduled accruals: {e}")
        
        # 2. Create reversals for prior month entries with auto_reverse
        prior_month = month - 1 if month > 1 else 12
        prior_year = year if month > 1 else year - 1
        
        for entry in self._accruals.values():
            if (entry.auto_reverse and 
                entry.status == AccrualStatus.POSTED and
                entry.period_month == prior_month and
                entry.period_year == prior_year and
                not entry.reversed_by_id):
                try:
                    reversal_date = date(year, month, 1)
                    self.reverse_accrual(entry.accrual_id, reversal_date)
                    results["reversals_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Reversal {entry.accrual_id}: {e}")
        
        # 3. Post entries if requested
        if post_entries:
            for entry in self._accruals.values():
                if (entry.status == AccrualStatus.DRAFT and
                    entry.period_month == month and
                    entry.period_year == year):
                    try:
                        self.post_accrual(entry.accrual_id, posted_by)
                        results["posted"] += 1
                    except Exception as e:
                        results["errors"].append(f"Post {entry.accrual_id}: {e}")
        
        logger.info(f"Month-end completed for {year}-{month:02d}: {results}")
        return results
    
    def get_accruals_for_period(
        self,
        month: int,
        year: int,
        status: AccrualStatus = None,
    ) -> List[AccrualEntry]:
        """Get accruals for a specific period."""
        results = [
            e for e in self._accruals.values()
            if e.period_month == month and e.period_year == year
        ]
        
        if status:
            results = [e for e in results if e.status == status]
        
        return results
    
    def get_accrual(self, accrual_id: str) -> Optional[AccrualEntry]:
        """Get an accrual by ID."""
        return self._accruals.get(accrual_id)

    def list_accruals(
        self,
        accrual_type: Optional[AccrualType] = None,
        vendor_name: Optional[str] = None,
        limit: int = 200,
    ) -> List[AccrualEntry]:
        """List accrual entries with optional type/vendor filters."""
        entries = list(self._accruals.values())
        if accrual_type:
            entries = [entry for entry in entries if entry.accrual_type == accrual_type]
        if vendor_name:
            vendor_lower = str(vendor_name).strip().lower()
            entries = [entry for entry in entries if vendor_lower in str(entry.vendor_name).lower()]
        entries.sort(key=lambda entry: entry.created_at, reverse=True)
        safe_limit = max(1, min(int(limit or 200), 5000))
        return entries[:safe_limit]
    
    def get_pending_reversals(self) -> List[AccrualEntry]:
        """Get accruals pending reversal."""
        today = date.today()
        return [
            e for e in self._accruals.values()
            if e.auto_reverse and
               e.status == AccrualStatus.POSTED and
               e.reversal_date and
               e.reversal_date <= today and
               not e.reversed_by_id
        ]
    
    def get_summary(self) -> Dict[str, Any]:
        """Get accruals summary."""
        entries = list(self._accruals.values())
        current_month = date.today().month
        current_year = date.today().year
        
        current_period = [
            e for e in entries
            if e.period_month == current_month and e.period_year == current_year
        ]
        
        return {
            "total_entries": len(entries),
            "by_status": {
                status.value: len([e for e in entries if e.status == status])
                for status in AccrualStatus
            },
            "by_type": {
                at.value: len([e for e in entries if e.accrual_type == at])
                for at in AccrualType
            },
            "current_period": {
                "count": len(current_period),
                "total_amount": sum(e.amount for e in current_period if e.amount > 0),
            },
            "pending_reversals": len(self.get_pending_reversals()),
            "active_schedules": len([s for s in self._schedules.values() if s.is_active]),
        }


# Singleton instance cache
_instances: Dict[str, AccrualsService] = {}


def get_accruals_service(organization_id: str = "default") -> AccrualsService:
    """Get or create accruals service for organization."""
    if organization_id not in _instances:
        _instances[organization_id] = AccrualsService(organization_id)
    return _instances[organization_id]
