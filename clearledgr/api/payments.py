"""
Payment Management API

Handles:
- Payment reminders
- Due date management
- Payment scheduling
- Overdue alerts
"""

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

from clearledgr.services.payment_scheduler import get_payment_scheduler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


# ==================== REQUEST MODELS ====================

class SendDigestRequest(BaseModel):
    """Request to send payment digest."""
    organization_id: str
    slack_channel: Optional[str] = None


# ==================== ENDPOINTS ====================

@router.get("/upcoming/{organization_id}")
async def get_upcoming_payments(
    organization_id: str,
    days: int = Query(default=7, ge=1, le=90),
):
    """
    Get upcoming payments due in the next N days.
    
    Includes overdue invoices.
    """
    scheduler = get_payment_scheduler(organization_id)
    reminders = scheduler.get_upcoming_payments(days_ahead=days)
    
    return {
        "organization_id": organization_id,
        "days_ahead": days,
        "payments": [
            {
                "invoice_id": r.invoice_id,
                "vendor": r.vendor,
                "amount": r.amount,
                "currency": r.currency,
                "due_date": r.due_date,
                "days_until_due": r.days_until_due,
                "is_overdue": r.is_overdue,
            }
            for r in reminders
        ],
        "total_count": len(reminders),
        "total_amount": sum(r.amount for r in reminders),
        "overdue_count": len([r for r in reminders if r.is_overdue]),
    }


@router.get("/overdue/{organization_id}")
async def get_overdue_payments(organization_id: str):
    """
    Get all overdue payments.
    """
    scheduler = get_payment_scheduler(organization_id)
    reminders = scheduler.get_overdue_payments()
    
    return {
        "organization_id": organization_id,
        "overdue": [
            {
                "invoice_id": r.invoice_id,
                "vendor": r.vendor,
                "amount": r.amount,
                "currency": r.currency,
                "due_date": r.due_date,
                "days_overdue": abs(r.days_until_due),
            }
            for r in reminders
        ],
        "total_count": len(reminders),
        "total_amount": sum(r.amount for r in reminders),
    }


@router.get("/forecast/{organization_id}")
async def get_payment_forecast(
    organization_id: str,
    days: int = Query(default=30, ge=7, le=90),
):
    """
    Get payment forecast grouped by week.
    
    Useful for cash flow planning.
    """
    scheduler = get_payment_scheduler(organization_id)
    return scheduler.get_payment_forecast(days=days)


@router.post("/send-digest")
async def send_payment_digest(
    request: SendDigestRequest,
    background_tasks: BackgroundTasks,
):
    """
    Send daily payment digest to Slack.
    
    Can be called manually or via scheduled job.
    """
    scheduler = get_payment_scheduler(
        request.organization_id,
        slack_channel=request.slack_channel or "#finance-payments",
    )
    
    result = await scheduler.send_daily_digest()
    
    return result


@router.post("/send-overdue-alerts/{organization_id}")
async def send_overdue_alerts(organization_id: str):
    """
    Send alerts for all overdue payments.
    """
    scheduler = get_payment_scheduler(organization_id)
    reminders = scheduler.get_overdue_payments()
    
    sent = []
    for reminder in reminders[:10]:  # Limit to 10 alerts
        result = await scheduler.send_overdue_alert(reminder)
        sent.append({
            "vendor": reminder.vendor,
            "amount": reminder.amount,
            "sent": result.get("sent", False),
        })
    
    return {
        "organization_id": organization_id,
        "alerts_sent": len([s for s in sent if s["sent"]]),
        "details": sent,
    }
