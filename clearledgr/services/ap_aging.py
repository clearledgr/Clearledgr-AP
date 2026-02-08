"""
AP Aging Reports Service

Provides aging analysis for accounts payable:
- Standard aging buckets (Current, 1-30, 31-60, 61-90, 90+)
- Vendor-level aging
- Department/GL code aging
- Trend analysis
- Export capabilities
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import csv
import io
import json

logger = logging.getLogger(__name__)


class AgingBucket(Enum):
    """Standard AP aging buckets."""
    CURRENT = "current"       # Not yet due
    DAYS_1_30 = "1-30"        # 1-30 days overdue
    DAYS_31_60 = "31-60"      # 31-60 days overdue
    DAYS_61_90 = "61-90"      # 61-90 days overdue
    DAYS_90_PLUS = "90+"      # Over 90 days overdue


@dataclass
class Invoice:
    """Invoice for aging analysis."""
    invoice_id: str
    vendor_name: str
    vendor_id: Optional[str] = None
    invoice_number: Optional[str] = None
    amount: float = 0.0
    currency: str = "USD"
    invoice_date: datetime = field(default_factory=datetime.now)
    due_date: datetime = field(default_factory=datetime.now)
    gl_code: Optional[str] = None
    department: Optional[str] = None
    status: str = "open"  # open, paid, partial
    paid_amount: float = 0.0
    
    @property
    def balance(self) -> float:
        """Outstanding balance."""
        return self.amount - self.paid_amount
    
    @property
    def days_outstanding(self) -> int:
        """Days since due date (negative if not yet due)."""
        delta = datetime.now() - self.due_date
        return delta.days
    
    @property
    def aging_bucket(self) -> AgingBucket:
        """Determine which aging bucket this invoice falls into."""
        days = self.days_outstanding
        if days <= 0:
            return AgingBucket.CURRENT
        elif days <= 30:
            return AgingBucket.DAYS_1_30
        elif days <= 60:
            return AgingBucket.DAYS_31_60
        elif days <= 90:
            return AgingBucket.DAYS_61_90
        else:
            return AgingBucket.DAYS_90_PLUS
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "vendor_name": self.vendor_name,
            "vendor_id": self.vendor_id,
            "invoice_number": self.invoice_number,
            "amount": self.amount,
            "currency": self.currency,
            "invoice_date": self.invoice_date.isoformat(),
            "due_date": self.due_date.isoformat(),
            "gl_code": self.gl_code,
            "department": self.department,
            "status": self.status,
            "paid_amount": self.paid_amount,
            "balance": self.balance,
            "days_outstanding": self.days_outstanding,
            "aging_bucket": self.aging_bucket.value,
        }


@dataclass
class AgingBucketSummary:
    """Summary for a single aging bucket."""
    bucket: AgingBucket
    count: int = 0
    total_amount: float = 0.0
    invoices: List[Invoice] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "bucket": self.bucket.value,
            "count": self.count,
            "total_amount": round(self.total_amount, 2),
            "percentage": 0,  # Calculated at report level
        }


@dataclass
class VendorAgingSummary:
    """Aging summary for a specific vendor."""
    vendor_name: str
    vendor_id: Optional[str] = None
    total_balance: float = 0.0
    invoice_count: int = 0
    buckets: Dict[AgingBucket, float] = field(default_factory=dict)
    oldest_invoice_days: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "vendor_name": self.vendor_name,
            "vendor_id": self.vendor_id,
            "total_balance": round(self.total_balance, 2),
            "invoice_count": self.invoice_count,
            "current": round(self.buckets.get(AgingBucket.CURRENT, 0), 2),
            "days_1_30": round(self.buckets.get(AgingBucket.DAYS_1_30, 0), 2),
            "days_31_60": round(self.buckets.get(AgingBucket.DAYS_31_60, 0), 2),
            "days_61_90": round(self.buckets.get(AgingBucket.DAYS_61_90, 0), 2),
            "days_90_plus": round(self.buckets.get(AgingBucket.DAYS_90_PLUS, 0), 2),
            "oldest_invoice_days": self.oldest_invoice_days,
        }


class APAgingService:
    """
    Service for generating AP aging reports.
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self._invoices: Dict[str, Invoice] = {}
    
    def add_invoice(self, invoice: Invoice):
        """Add an invoice for aging analysis."""
        self._invoices[invoice.invoice_id] = invoice
    
    def add_invoices(self, invoices: List[Invoice]):
        """Add multiple invoices."""
        for inv in invoices:
            self._invoices[inv.invoice_id] = inv
    
    def load_from_queue(self, queue_data: List[Dict]) -> int:
        """
        Load invoices from the Gmail extension queue format.
        Returns count of invoices loaded.
        """
        count = 0
        for item in queue_data:
            if item.get("status") in ["posted", "approved", "pending"]:
                invoice = Invoice(
                    invoice_id=item.get("id") or item.get("gmail_id", ""),
                    vendor_name=item.get("vendor") or item.get("detected", {}).get("vendor", "Unknown"),
                    invoice_number=item.get("invoiceNumber") or item.get("detected", {}).get("invoiceNumber"),
                    amount=float(item.get("amount") or item.get("detected", {}).get("amount") or 0),
                    invoice_date=datetime.fromisoformat(item.get("date", datetime.now().isoformat()).replace("Z", "")),
                    due_date=datetime.fromisoformat(item.get("dueDate", datetime.now().isoformat()).replace("Z", "")) if item.get("dueDate") else datetime.now() + timedelta(days=30),
                    gl_code=item.get("glCode"),
                    department=item.get("department"),
                    status="open" if item.get("status") != "paid" else "paid",
                )
                self._invoices[invoice.invoice_id] = invoice
                count += 1
        
        logger.info(f"Loaded {count} invoices for aging analysis")
        return count
    
    def get_aging_summary(self, as_of_date: datetime = None) -> Dict[str, Any]:
        """
        Generate aging summary report.
        """
        as_of_date = as_of_date or datetime.now()
        
        # Initialize bucket summaries
        buckets = {bucket: AgingBucketSummary(bucket=bucket) for bucket in AgingBucket}
        
        # Calculate totals
        total_amount = 0.0
        open_invoices = [inv for inv in self._invoices.values() if inv.status == "open"]
        
        for invoice in open_invoices:
            bucket = invoice.aging_bucket
            buckets[bucket].count += 1
            buckets[bucket].total_amount += invoice.balance
            buckets[bucket].invoices.append(invoice)
            total_amount += invoice.balance
        
        # Calculate percentages
        bucket_summaries = []
        for bucket in AgingBucket:
            summary = buckets[bucket].to_dict()
            summary["percentage"] = round(
                (summary["total_amount"] / total_amount * 100) if total_amount > 0 else 0, 1
            )
            bucket_summaries.append(summary)
        
        return {
            "as_of_date": as_of_date.isoformat(),
            "organization_id": self.organization_id,
            "total_open_invoices": len(open_invoices),
            "total_ap_balance": round(total_amount, 2),
            "buckets": bucket_summaries,
            "summary": {
                "current": round(buckets[AgingBucket.CURRENT].total_amount, 2),
                "overdue_1_30": round(buckets[AgingBucket.DAYS_1_30].total_amount, 2),
                "overdue_31_60": round(buckets[AgingBucket.DAYS_31_60].total_amount, 2),
                "overdue_61_90": round(buckets[AgingBucket.DAYS_61_90].total_amount, 2),
                "overdue_90_plus": round(buckets[AgingBucket.DAYS_90_PLUS].total_amount, 2),
                "total_overdue": round(
                    total_amount - buckets[AgingBucket.CURRENT].total_amount, 2
                ),
            },
        }
    
    def get_vendor_aging(self, min_balance: float = 0) -> List[VendorAgingSummary]:
        """
        Generate aging summary by vendor.
        """
        vendor_data: Dict[str, VendorAgingSummary] = {}
        
        for invoice in self._invoices.values():
            if invoice.status != "open":
                continue
            
            vendor_name = invoice.vendor_name
            if vendor_name not in vendor_data:
                vendor_data[vendor_name] = VendorAgingSummary(
                    vendor_name=vendor_name,
                    vendor_id=invoice.vendor_id,
                )
            
            summary = vendor_data[vendor_name]
            summary.total_balance += invoice.balance
            summary.invoice_count += 1
            
            bucket = invoice.aging_bucket
            summary.buckets[bucket] = summary.buckets.get(bucket, 0) + invoice.balance
            
            if invoice.days_outstanding > summary.oldest_invoice_days:
                summary.oldest_invoice_days = invoice.days_outstanding
        
        # Filter by minimum balance and sort
        result = [v for v in vendor_data.values() if v.total_balance >= min_balance]
        result.sort(key=lambda v: -v.total_balance)
        
        return result
    
    def get_department_aging(self) -> Dict[str, Dict[str, Any]]:
        """
        Generate aging summary by department.
        """
        dept_data: Dict[str, Dict[str, Any]] = {}
        
        for invoice in self._invoices.values():
            if invoice.status != "open":
                continue
            
            dept = invoice.department or "Unassigned"
            if dept not in dept_data:
                dept_data[dept] = {
                    "department": dept,
                    "total_balance": 0,
                    "invoice_count": 0,
                    "buckets": {b.value: 0 for b in AgingBucket},
                }
            
            dept_data[dept]["total_balance"] += invoice.balance
            dept_data[dept]["invoice_count"] += 1
            dept_data[dept]["buckets"][invoice.aging_bucket.value] += invoice.balance
        
        return dept_data
    
    def get_gl_code_aging(self) -> Dict[str, Dict[str, Any]]:
        """
        Generate aging summary by GL code.
        """
        gl_data: Dict[str, Dict[str, Any]] = {}
        
        for invoice in self._invoices.values():
            if invoice.status != "open":
                continue
            
            gl = invoice.gl_code or "Unassigned"
            if gl not in gl_data:
                gl_data[gl] = {
                    "gl_code": gl,
                    "total_balance": 0,
                    "invoice_count": 0,
                    "buckets": {b.value: 0 for b in AgingBucket},
                }
            
            gl_data[gl]["total_balance"] += invoice.balance
            gl_data[gl]["invoice_count"] += 1
            gl_data[gl]["buckets"][invoice.aging_bucket.value] += invoice.balance
        
        return gl_data
    
    def get_overdue_invoices(self, min_days_overdue: int = 1) -> List[Invoice]:
        """
        Get list of overdue invoices.
        """
        overdue = [
            inv for inv in self._invoices.values()
            if inv.status == "open" and inv.days_outstanding >= min_days_overdue
        ]
        overdue.sort(key=lambda i: -i.days_outstanding)
        return overdue
    
    def get_critical_invoices(self, days_threshold: int = 60) -> List[Invoice]:
        """
        Get invoices that are critically overdue.
        """
        return [
            inv for inv in self.get_overdue_invoices(days_threshold)
        ]
    
    def export_to_csv(self, include_details: bool = True) -> str:
        """
        Export aging report to CSV format.
        """
        output = io.StringIO()
        
        if include_details:
            # Detailed invoice listing
            writer = csv.writer(output)
            writer.writerow([
                "Invoice ID", "Vendor", "Invoice Number", "Amount", "Balance",
                "Invoice Date", "Due Date", "Days Outstanding", "Aging Bucket",
                "GL Code", "Department", "Status"
            ])
            
            for invoice in sorted(self._invoices.values(), key=lambda i: -i.days_outstanding):
                writer.writerow([
                    invoice.invoice_id,
                    invoice.vendor_name,
                    invoice.invoice_number or "",
                    invoice.amount,
                    invoice.balance,
                    invoice.invoice_date.strftime("%Y-%m-%d"),
                    invoice.due_date.strftime("%Y-%m-%d"),
                    invoice.days_outstanding,
                    invoice.aging_bucket.value,
                    invoice.gl_code or "",
                    invoice.department or "",
                    invoice.status,
                ])
        else:
            # Summary only
            summary = self.get_aging_summary()
            writer = csv.writer(output)
            writer.writerow(["Aging Report Summary"])
            writer.writerow(["As of Date", summary["as_of_date"]])
            writer.writerow(["Total Open Invoices", summary["total_open_invoices"]])
            writer.writerow(["Total AP Balance", f"${summary['total_ap_balance']:,.2f}"])
            writer.writerow([])
            writer.writerow(["Aging Bucket", "Count", "Amount", "Percentage"])
            for bucket in summary["buckets"]:
                writer.writerow([
                    bucket["bucket"],
                    bucket["count"],
                    f"${bucket['total_amount']:,.2f}",
                    f"{bucket['percentage']}%"
                ])
        
        return output.getvalue()
    
    def export_to_json(self) -> str:
        """
        Export aging report to JSON format.
        """
        return json.dumps({
            "summary": self.get_aging_summary(),
            "vendor_aging": [v.to_dict() for v in self.get_vendor_aging()],
            "department_aging": self.get_department_aging(),
            "invoices": [inv.to_dict() for inv in self._invoices.values()],
        }, indent=2)
    
    def get_trend_analysis(self, weeks: int = 12) -> List[Dict[str, Any]]:
        """
        Analyze aging trend over time (simulated based on current data).
        In production, this would query historical snapshots.
        """
        # For now, return current state
        # In production, you'd store weekly snapshots and compare
        current = self.get_aging_summary()
        return [{
            "week": datetime.now().isocalendar()[1],
            "date": datetime.now().isoformat(),
            "total_balance": current["total_ap_balance"],
            "total_overdue": current["summary"]["total_overdue"],
        }]
    
    def clear(self):
        """Clear all invoice data."""
        self._invoices.clear()


# Singleton instance cache
_instances: Dict[str, APAgingService] = {}


def get_ap_aging_service(organization_id: str = "default") -> APAgingService:
    """Get or create AP aging service for organization."""
    if organization_id not in _instances:
        _instances[organization_id] = APAgingService(organization_id)
    return _instances[organization_id]
