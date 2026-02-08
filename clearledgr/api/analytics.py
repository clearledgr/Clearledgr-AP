"""
Analytics & Dashboard API

Provides metrics and insights:
- Invoice processing stats
- Spend by vendor/category
- Processing time metrics
- Exception rates
- Pipeline overview
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ==================== DATA MODELS ====================

class DashboardStats(BaseModel):
    """Summary statistics for dashboard."""
    total_invoices: int = 0
    pending_approval: int = 0
    approved_today: int = 0
    posted_today: int = 0
    rejected_today: int = 0
    auto_approved_rate: float = 0.0
    avg_processing_time_hours: float = 0.0
    total_amount_pending: float = 0.0
    total_amount_posted_today: float = 0.0


class VendorSpend(BaseModel):
    """Spend summary for a vendor."""
    vendor: str
    total_amount: float
    invoice_count: int
    currency: str = "USD"
    last_invoice_date: Optional[str] = None


class ProcessingMetrics(BaseModel):
    """Processing time metrics."""
    avg_time_to_approval_hours: float
    avg_time_to_post_hours: float
    auto_approve_rate: float
    exception_rate: float
    invoices_processed: int


# ==================== ENDPOINTS ====================


def _starts_with_day(value: Any, day_prefix: str) -> bool:
    """Safely check if a timestamp-like value begins with YYYY-MM-DD."""
    return isinstance(value, str) and value.startswith(day_prefix)

@router.get("/dashboard/{organization_id}", response_model=DashboardStats)
async def get_dashboard(organization_id: str):
    """
    Get dashboard summary statistics.
    """
    db = get_db()
    
    # Get invoice pipeline
    pipeline = db.get_invoice_pipeline(organization_id)
    
    # Calculate stats
    today = datetime.now(timezone.utc).date().isoformat()
    
    total_invoices = sum(len(invoices) for invoices in pipeline.values())
    pending_approval = len(pipeline.get("pending_approval", []))
    
    # Count today's activity
    approved_today = 0
    posted_today = 0
    rejected_today = 0
    total_amount_pending = 0.0
    total_amount_posted_today = 0.0
    auto_approved_count = 0
    
    for status, invoices in pipeline.items():
        for inv in invoices:
            amount = inv.get("amount", 0) or 0
            
            if status == "pending_approval":
                total_amount_pending += amount
            
            # Check if action was today
            if _starts_with_day(inv.get("approved_at"), today):
                approved_today += 1
                approved_by = inv.get("approved_by")
                if isinstance(approved_by, str) and approved_by.startswith("clearledgr-auto"):
                    auto_approved_count += 1
            
            if _starts_with_day(inv.get("posted_at"), today):
                posted_today += 1
                total_amount_posted_today += amount
            
            if status == "rejected" and _starts_with_day(inv.get("updated_at"), today):
                rejected_today += 1
    
    # Calculate auto-approve rate
    total_approved = approved_today + posted_today
    auto_approve_rate = (auto_approved_count / total_approved * 100) if total_approved > 0 else 0
    
    # Calculate avg processing time (simplified)
    avg_processing_time = _calculate_avg_processing_time(pipeline)
    
    return DashboardStats(
        total_invoices=total_invoices,
        pending_approval=pending_approval,
        approved_today=approved_today,
        posted_today=posted_today,
        rejected_today=rejected_today,
        auto_approved_rate=auto_approve_rate,
        avg_processing_time_hours=avg_processing_time,
        total_amount_pending=total_amount_pending,
        total_amount_posted_today=total_amount_posted_today,
    )


@router.get("/spend-by-vendor/{organization_id}")
async def get_spend_by_vendor(
    organization_id: str,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=20, ge=1, le=100),
):
    """
    Get spend breakdown by vendor.
    """
    db = get_db()
    
    # Get posted invoices
    invoices = db.get_invoices_by_status(organization_id, status="posted", limit=1000)
    
    # Filter by date range
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    
    # Aggregate by vendor
    vendor_spend: Dict[str, Dict] = defaultdict(lambda: {
        "total_amount": 0,
        "invoice_count": 0,
        "currency": "USD",
        "last_invoice_date": None,
    })
    
    for inv in invoices:
        if inv.get("posted_at", "") < cutoff:
            continue
        
        vendor = inv.get("vendor", "Unknown")
        amount = inv.get("amount", 0) or 0
        
        vendor_spend[vendor]["total_amount"] += amount
        vendor_spend[vendor]["invoice_count"] += 1
        vendor_spend[vendor]["currency"] = inv.get("currency", "USD")
        
        # Track most recent invoice
        posted_at = inv.get("posted_at", "")
        if not vendor_spend[vendor]["last_invoice_date"] or posted_at > vendor_spend[vendor]["last_invoice_date"]:
            vendor_spend[vendor]["last_invoice_date"] = posted_at
    
    # Sort by total amount
    sorted_vendors = sorted(
        vendor_spend.items(),
        key=lambda x: x[1]["total_amount"],
        reverse=True
    )[:limit]
    
    return {
        "organization_id": organization_id,
        "period_days": days,
        "vendors": [
            VendorSpend(
                vendor=vendor,
                total_amount=data["total_amount"],
                invoice_count=data["invoice_count"],
                currency=data["currency"],
                last_invoice_date=data["last_invoice_date"],
            )
            for vendor, data in sorted_vendors
        ],
        "total_spend": sum(d["total_amount"] for _, d in sorted_vendors),
    }


@router.get("/spend-by-category/{organization_id}")
async def get_spend_by_category(
    organization_id: str,
    days: int = Query(default=30, ge=1, le=365),
):
    """
    Get spend breakdown by category.
    
    Categories are inferred from vendor names if not explicitly tagged.
    """
    db = get_db()
    
    # Category detection patterns
    CATEGORY_PATTERNS = {
        "Software & SaaS": ["stripe", "aws", "google", "microsoft", "github", "slack", "notion", "figma", "datadog", "sentry"],
        "Travel": ["uber", "lyft", "airbnb", "hotel", "airline", "flight", "delta", "united", "american"],
        "Office & Supplies": ["amazon", "staples", "office depot", "costco"],
        "Meals & Entertainment": ["doordash", "uber eats", "grubhub", "restaurant"],
        "Professional Services": ["consulting", "legal", "accounting", "lawyer"],
        "Marketing": ["google ads", "facebook", "linkedin", "mailchimp", "hubspot"],
        "Utilities": ["utility", "electric", "water", "internet", "phone"],
    }
    
    invoices = db.get_invoices_by_status(organization_id, status="posted", limit=1000)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    
    category_spend: Dict[str, float] = defaultdict(float)
    category_count: Dict[str, int] = defaultdict(int)
    
    for inv in invoices:
        if inv.get("posted_at", "") < cutoff:
            continue
        
        vendor = (inv.get("vendor", "") or "").lower()
        amount = inv.get("amount", 0) or 0
        
        # Detect category
        category = "Other"
        for cat, patterns in CATEGORY_PATTERNS.items():
            if any(p in vendor for p in patterns):
                category = cat
                break
        
        category_spend[category] += amount
        category_count[category] += 1
    
    # Sort by spend
    sorted_categories = sorted(
        category_spend.items(),
        key=lambda x: x[1],
        reverse=True
    )
    
    return {
        "organization_id": organization_id,
        "period_days": days,
        "categories": [
            {
                "category": cat,
                "total_amount": amount,
                "invoice_count": category_count[cat],
                "percentage": (amount / sum(category_spend.values()) * 100) if category_spend else 0,
            }
            for cat, amount in sorted_categories
        ],
        "total_spend": sum(category_spend.values()),
    }


@router.get("/processing-metrics/{organization_id}", response_model=ProcessingMetrics)
async def get_processing_metrics(
    organization_id: str,
    days: int = Query(default=30, ge=1, le=365),
):
    """
    Get invoice processing metrics.
    """
    db = get_db()
    
    invoices = db.get_invoices_by_status(organization_id, limit=1000)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    
    approval_times = []
    post_times = []
    auto_approved = 0
    exceptions = 0
    total_processed = 0
    
    for inv in invoices:
        created = inv.get("created_at", "")
        if created < cutoff:
            continue
        
        status = inv.get("status", "")
        
        if status in ["approved", "posted"]:
            total_processed += 1
            
            # Calculate time to approval
            approved_at = inv.get("approved_at", "")
            if created and approved_at:
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    approved_dt = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
                    hours = (approved_dt - created_dt).total_seconds() / 3600
                    approval_times.append(hours)
                except:
                    pass
            
            # Calculate time to post
            posted_at = inv.get("posted_at", "")
            if created and posted_at:
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    posted_dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
                    hours = (posted_dt - created_dt).total_seconds() / 3600
                    post_times.append(hours)
                except:
                    pass
            
            # Check if auto-approved
            if inv.get("approved_by", "").startswith("clearledgr-auto"):
                auto_approved += 1
        
        elif status == "rejected":
            exceptions += 1
            total_processed += 1
    
    avg_approval = sum(approval_times) / len(approval_times) if approval_times else 0
    avg_post = sum(post_times) / len(post_times) if post_times else 0
    auto_rate = (auto_approved / total_processed * 100) if total_processed else 0
    exception_rate = (exceptions / total_processed * 100) if total_processed else 0
    
    return ProcessingMetrics(
        avg_time_to_approval_hours=round(avg_approval, 2),
        avg_time_to_post_hours=round(avg_post, 2),
        auto_approve_rate=round(auto_rate, 1),
        exception_rate=round(exception_rate, 1),
        invoices_processed=total_processed,
    )


@router.get("/trend/{organization_id}")
async def get_invoice_trend(
    organization_id: str,
    days: int = Query(default=30, ge=7, le=90),
):
    """
    Get invoice volume trend over time.
    """
    db = get_db()
    
    invoices = db.get_invoices_by_status(organization_id, limit=1000)
    
    # Group by date
    daily_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: {
        "received": 0,
        "approved": 0,
        "posted": 0,
        "rejected": 0,
    })
    
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    
    for inv in invoices:
        created = inv.get("created_at", "")
        if created < cutoff:
            continue
        
        # Extract date
        date = created[:10] if created else ""
        if not date:
            continue
        
        daily_counts[date]["received"] += 1
        
        status = inv.get("status", "")
        if status == "posted":
            posted_date = (inv.get("posted_at", "") or "")[:10]
            if posted_date:
                daily_counts[posted_date]["posted"] += 1
        elif status == "approved":
            approved_date = (inv.get("approved_at", "") or "")[:10]
            if approved_date:
                daily_counts[approved_date]["approved"] += 1
        elif status == "rejected":
            daily_counts[date]["rejected"] += 1
    
    # Sort by date
    sorted_dates = sorted(daily_counts.items())
    
    return {
        "organization_id": organization_id,
        "period_days": days,
        "trend": [
            {
                "date": date,
                "received": counts["received"],
                "approved": counts["approved"],
                "posted": counts["posted"],
                "rejected": counts["rejected"],
            }
            for date, counts in sorted_dates
        ],
    }


@router.get("/pipeline/{organization_id}")
async def get_pipeline_overview(organization_id: str):
    """
    Get invoice pipeline overview.
    
    Returns counts and amounts at each stage.
    """
    db = get_db()
    pipeline = db.get_invoice_pipeline(organization_id)
    
    result = {
        "organization_id": organization_id,
        "stages": {},
        "total_count": 0,
        "total_amount": 0,
    }
    
    for status, invoices in pipeline.items():
        count = len(invoices)
        amount = sum(inv.get("amount", 0) or 0 for inv in invoices)
        
        result["stages"][status] = {
            "count": count,
            "amount": amount,
            "invoices": [
                {
                    "id": inv.get("id"),
                    "gmail_id": inv.get("gmail_id"),
                    "vendor": inv.get("vendor"),
                    "amount": inv.get("amount"),
                    "currency": inv.get("currency", "USD"),
                    "due_date": inv.get("due_date"),
                    "created_at": inv.get("created_at"),
                }
                for inv in invoices[:10]  # Limit to 10 per stage
            ],
        }
        
        result["total_count"] += count
        result["total_amount"] += amount
    
    return result


# ==================== HELPER FUNCTIONS ====================

def _calculate_avg_processing_time(pipeline: Dict[str, List]) -> float:
    """Calculate average processing time in hours."""
    times = []
    
    for status in ["approved", "posted"]:
        for inv in pipeline.get(status, []):
            created = inv.get("created_at", "")
            posted = inv.get("posted_at", "") or inv.get("approved_at", "")
            
            if created and posted:
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    posted_dt = datetime.fromisoformat(posted.replace("Z", "+00:00"))
                    hours = (posted_dt - created_dt).total_seconds() / 3600
                    times.append(hours)
                except:
                    pass
    
    return round(sum(times) / len(times), 2) if times else 0
