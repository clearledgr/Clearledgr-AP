"""
Payment Scheduler Service

Handles:
- Due date reminders
- Payment scheduling
- Overdue alerts
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

from clearledgr.core.database import get_db
from clearledgr.services.slack_api import get_slack_client

logger = logging.getLogger(__name__)


@dataclass
class PaymentReminder:
    """Payment reminder data."""
    invoice_id: str
    vendor: str
    amount: float
    currency: str
    due_date: str
    days_until_due: int
    is_overdue: bool
    gmail_id: Optional[str] = None


class PaymentSchedulerService:
    """
    Manages payment reminders and scheduling.
    
    Features:
    - Daily digest of upcoming payments
    - Overdue alerts
    - Payment scheduling suggestions
    """
    
    def __init__(self, organization_id: str, slack_channel: str = "#finance-payments"):
        self.organization_id = organization_id
        self.slack_channel = slack_channel
        self.db = get_db()
        self._slack = None
    
    @property
    def slack(self):
        if self._slack is None:
            self._slack = get_slack_client()
        return self._slack
    
    def get_upcoming_payments(self, days_ahead: int = 7) -> List[PaymentReminder]:
        """Get invoices due in the next N days."""
        invoices = self.db.get_invoices_by_status(
            self.organization_id, 
            status="posted", 
            limit=500
        )
        
        today = datetime.now(timezone.utc).date()
        cutoff = today + timedelta(days=days_ahead)
        
        reminders = []
        
        for inv in invoices:
            due_date_str = inv.get("due_date")
            if not due_date_str:
                continue
            
            try:
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            except:
                continue
            
            # Check if within window or overdue
            if due_date <= cutoff:
                days_until = (due_date - today).days
                
                reminders.append(PaymentReminder(
                    invoice_id=inv.get("id", ""),
                    vendor=inv.get("vendor", "Unknown"),
                    amount=inv.get("amount", 0) or 0,
                    currency=inv.get("currency", "USD"),
                    due_date=due_date_str,
                    days_until_due=days_until,
                    is_overdue=days_until < 0,
                    gmail_id=inv.get("gmail_id"),
                ))
        
        # Sort by due date (overdue first, then soonest)
        reminders.sort(key=lambda r: (not r.is_overdue, r.days_until_due))
        
        return reminders
    
    def get_overdue_payments(self) -> List[PaymentReminder]:
        """Get all overdue invoices."""
        reminders = self.get_upcoming_payments(days_ahead=0)
        return [r for r in reminders if r.is_overdue]
    
    async def send_daily_digest(self) -> Dict[str, Any]:
        """
        Send daily payment digest to Slack.
        
        Includes:
        - Overdue invoices (urgent)
        - Due today
        - Due this week
        """
        reminders = self.get_upcoming_payments(days_ahead=7)
        
        if not reminders:
            return {"sent": False, "reason": "No upcoming payments"}
        
        # Group by urgency
        overdue = [r for r in reminders if r.is_overdue]
        due_today = [r for r in reminders if r.days_until_due == 0]
        due_this_week = [r for r in reminders if 0 < r.days_until_due <= 7]
        
        # Build message blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Daily Payment Digest"
                }
            }
        ]
        
        # Overdue section
        if overdue:
            total_overdue = sum(r.amount for r in overdue)
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*OVERDUE ({len(overdue)} invoices - ${total_overdue:,.2f})*"
                }
            })
            
            for r in overdue[:5]:  # Show top 5
                blocks.append({
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": f"• *{r.vendor}* - {r.currency} {r.amount:,.2f} (overdue by {abs(r.days_until_due)} days)"
                    }]
                })
            
            if len(overdue) > 5:
                blocks.append({
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": f"_...and {len(overdue) - 5} more_"
                    }]
                })
        
        # Due today section
        if due_today:
            total_today = sum(r.amount for r in due_today)
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*DUE TODAY ({len(due_today)} invoices - ${total_today:,.2f})*"
                }
            })
            
            for r in due_today[:5]:
                blocks.append({
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": f"• *{r.vendor}* - {r.currency} {r.amount:,.2f}"
                    }]
                })
        
        # Due this week section
        if due_this_week:
            total_week = sum(r.amount for r in due_this_week)
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*DUE THIS WEEK ({len(due_this_week)} invoices - ${total_week:,.2f})*"
                }
            })
            
            for r in due_this_week[:5]:
                blocks.append({
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": f"• *{r.vendor}* - {r.currency} {r.amount:,.2f} (due in {r.days_until_due} days)"
                    }]
                })
        
        # Summary
        total_amount = sum(r.amount for r in reminders)
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Total: {len(reminders)} invoices, ${total_amount:,.2f}*"
            }
        })
        
        # Send to Slack
        try:
            message = await self.slack.send_message(
                channel=self.slack_channel,
                text=f"Daily Payment Digest: {len(reminders)} invoices, ${total_amount:,.2f} due",
                blocks=blocks,
            )
            
            return {
                "sent": True,
                "channel": message.channel,
                "ts": message.ts,
                "summary": {
                    "overdue": len(overdue),
                    "due_today": len(due_today),
                    "due_this_week": len(due_this_week),
                    "total_amount": total_amount,
                }
            }
        except Exception as e:
            logger.error(f"Failed to send payment digest: {e}")
            return {"sent": False, "error": str(e)}
    
    async def send_overdue_alert(self, reminder: PaymentReminder) -> Dict[str, Any]:
        """Send urgent alert for an overdue invoice."""
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*OVERDUE PAYMENT ALERT*\n\n"
                            f"*{reminder.vendor}*\n"
                            f"Amount: {reminder.currency} {reminder.amount:,.2f}\n"
                            f"Due: {reminder.due_date} ({abs(reminder.days_until_due)} days overdue)"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Mark as Paid"},
                        "style": "primary",
                        "action_id": f"mark_paid_{reminder.invoice_id}",
                        "value": reminder.invoice_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Snooze 3 Days"},
                        "action_id": f"snooze_payment_{reminder.invoice_id}",
                        "value": reminder.invoice_id,
                    },
                ]
            }
        ]
        
        try:
            message = await self.slack.send_message(
                channel=self.slack_channel,
                text=f"OVERDUE: {reminder.vendor} - {reminder.currency} {reminder.amount:,.2f}",
                blocks=blocks,
            )
            
            return {"sent": True, "ts": message.ts}
        except Exception as e:
            logger.error(f"Failed to send overdue alert: {e}")
            return {"sent": False, "error": str(e)}
    
    def get_payment_forecast(self, days: int = 30) -> Dict[str, Any]:
        """
        Get payment forecast for the next N days.
        
        Groups payments by week for cash flow planning.
        """
        reminders = self.get_upcoming_payments(days_ahead=days)
        
        # Group by week
        today = datetime.now(timezone.utc).date()
        weeks: Dict[str, List[PaymentReminder]] = {}
        
        for r in reminders:
            try:
                due_date = datetime.strptime(r.due_date, "%Y-%m-%d").date()
                # Get week start (Monday)
                week_start = due_date - timedelta(days=due_date.weekday())
                week_key = week_start.isoformat()
                
                if week_key not in weeks:
                    weeks[week_key] = []
                weeks[week_key].append(r)
            except:
                pass
        
        forecast = []
        for week_start, week_reminders in sorted(weeks.items()):
            total = sum(r.amount for r in week_reminders)
            forecast.append({
                "week_start": week_start,
                "invoice_count": len(week_reminders),
                "total_amount": total,
                "vendors": [r.vendor for r in week_reminders[:5]],
            })
        
        return {
            "organization_id": self.organization_id,
            "forecast_days": days,
            "weeks": forecast,
            "total_amount": sum(r.amount for r in reminders),
            "total_invoices": len(reminders),
        }


def get_payment_scheduler(
    organization_id: str,
    slack_channel: str = "#finance-payments",
) -> PaymentSchedulerService:
    """Get payment scheduler service instance."""
    return PaymentSchedulerService(organization_id, slack_channel)
