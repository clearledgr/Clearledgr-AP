"""
Slack Notifications

Sends reconciliation results to Slack.
Following the spec: Exception-only notifications with one-click approval.
"""

import os
import logging
import httpx
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class SlackNotifier:
    """
    Sends formatted notifications to Slack.
    
    Users only see:
    1. Summary metrics
    2. Exceptions that need attention
    3. Action buttons
    
    Users don't see:
    - Successfully matched transactions (invisible)
    - Intermediate processing steps
    """
    
    def __init__(self, webhook_url: Optional[str] = None):
        """
        Initialize Slack notifier.
        
        Args:
            webhook_url: Slack webhook URL. If not provided, uses SLACK_WEBHOOK_URL env var.
        """
        self.webhook_url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
        if not self.webhook_url:
            logger.warning("Slack webhook URL not configured")
    
    async def send_reconciliation_complete(
        self,
        result: Dict[str, Any],
        sheets_url: Optional[str] = None,
    ) -> bool:
        """
        Send reconciliation complete notification.
        
        Format from spec:
        Bank Reconciliation Complete - January 15, 2026
        
        Summary:
        - 2,847 transactions processed
        - 2,801 matched automatically (98.4%)
        - 46 exceptions need review
        
        Actions:
        [Review Exceptions] [Approve & Post]
        """
        if not self.webhook_url:
            logger.warning("Cannot send Slack notification - no webhook configured")
            return False
        
        summary = result.get("summary", {})
        amounts = result.get("amounts", {})
        exceptions = result.get("exceptions", [])
        
        # Build exception breakdown
        exception_breakdown = self._build_exception_breakdown(exceptions)
        
        # Format the message
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Bank Reconciliation Complete - {self._get_date()}",
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Summary:*\n"
                        f"- {summary.get('gateway_transactions', 0):,} transactions processed\n"
                        f"- {summary.get('matched', 0):,} matched automatically ({summary.get('match_rate', 0):.1f}%)\n"
                        f"- {summary.get('exceptions', 0)} exceptions need review"
                    )
                }
            },
        ]
        
        # Add exception breakdown if there are exceptions
        if exceptions:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Exception Breakdown:*\n{exception_breakdown}"
                }
            })
        
        # Add amounts
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Amounts:*\n"
                    f"- Total processed: EUR {amounts.get('total_gateway', 0):,.2f}\n"
                    f"- Matched: EUR {amounts.get('matched', 0):,.2f}\n"
                    f"- Unmatched: EUR {amounts.get('unmatched', 0):,.2f}"
                )
            }
        })
        
        # Add draft entries info
        draft_count = len(result.get("draft_entries", []))
        if draft_count > 0:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Ready to Post:*\n{draft_count} draft journal entries awaiting approval"
                }
            })
        
        # Add action buttons
        actions = []
        
        if exceptions:
            actions.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Review Exceptions"},
                "style": "primary",
                "url": sheets_url or "https://docs.google.com/spreadsheets",
                "action_id": "review_exceptions",
            })
        
        if draft_count > 0:
            actions.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Approve & Post to SAP"},
                "style": "primary" if not exceptions else "default",
                "action_id": "approve_and_post",
            })
        
        if actions:
            blocks.append({
                "type": "actions",
                "elements": actions,
            })
        
        # Add time saved estimate
        time_saved = self._estimate_time_saved(summary.get("matched", 0))
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Time saved today: ~{time_saved}"
                }
            ]
        })
        
        # Send to Slack
        payload = {"blocks": blocks}
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.webhook_url, json=payload)
                response.raise_for_status()
            
            logger.info("Sent reconciliation notification to Slack")
            return True
        
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")
            return False
    
    async def send_exception_alert(
        self,
        exception: Dict[str, Any],
        organization_id: str,
    ) -> bool:
        """
        Send alert for critical exception.
        
        Only sent for high-value or critical exceptions.
        """
        if not self.webhook_url:
            return False
        
        priority = exception.get("priority", "low")
        if priority not in ["critical", "high"]:
            return True  # Don't send for low priority
        
        amount = exception.get("amount", 0)
        tx = exception.get("transaction", {})
        
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*[{priority.upper()}] Reconciliation Exception*\n\n"
                        f"*Amount:* EUR {amount:,.2f}\n"
                        f"*Description:* {tx.get('description', 'Unknown')}\n"
                        f"*Reason:* {exception.get('reason', 'No match found')}"
                    )
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Review Now"},
                        "style": "danger",
                        "action_id": f"review_exception_{exception.get('transaction', {}).get('id', 'unknown')}",
                    }
                ]
            }
        ]
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.webhook_url, json={"blocks": blocks})
                response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to send exception alert: {e}")
            return False
    
    def _build_exception_breakdown(self, exceptions: List[Dict[str, Any]]) -> str:
        """Build formatted exception breakdown."""
        if not exceptions:
            return "No exceptions"
        
        # Group by priority
        by_priority = {"critical": [], "high": [], "medium": [], "low": []}
        for exc in exceptions:
            priority = exc.get("priority", "low")
            by_priority[priority].append(exc)
        
        lines = []
        
        if by_priority["critical"]:
            total = sum(e.get("amount", 0) for e in by_priority["critical"])
            lines.append(f"- {len(by_priority['critical'])} critical (EUR {total:,.2f})")
        
        if by_priority["high"]:
            total = sum(e.get("amount", 0) for e in by_priority["high"])
            lines.append(f"- {len(by_priority['high'])} high priority (EUR {total:,.2f})")
        
        if by_priority["medium"]:
            lines.append(f"- {len(by_priority['medium'])} medium priority")
        
        if by_priority["low"]:
            lines.append(f"- {len(by_priority['low'])} low priority (timing differences)")
        
        return "\n".join(lines) if lines else "No exceptions"
    
    def _get_date(self) -> str:
        """Get formatted current date."""
        from datetime import datetime
        return datetime.now().strftime("%B %d, %Y")
    
    def _estimate_time_saved(self, matched_count: int) -> str:
        """Estimate time saved based on matched transactions."""
        # Assume ~1 minute per manual match
        minutes = matched_count
        
        if minutes < 60:
            return f"{minutes} minutes"
        else:
            hours = minutes / 60
            return f"{hours:.1f} hours"


async def notify_reconciliation_complete(
    result: Dict[str, Any],
    webhook_url: Optional[str] = None,
    sheets_url: Optional[str] = None,
) -> bool:
    """
    Convenience function to send reconciliation notification.
    
    This is called after reconciliation workflow completes.
    """
    notifier = SlackNotifier(webhook_url=webhook_url)
    return await notifier.send_reconciliation_complete(result, sheets_url)


async def send_payment_request_notification(request) -> bool:
    """
    Send Slack notification for a new payment request.
    
    Payment requests need approval before payment can be made.
    This sends an interactive message with Approve/Reject buttons.
    
    Args:
        request: PaymentRequest object
    
    Returns:
        True if sent successfully
    """
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("Cannot send payment request notification - no webhook configured")
        return False
    
    # Determine channel based on amount
    amount = request.amount
    if amount >= 10000:
        channel_note = "#executive-approvals"
    elif amount >= 1000:
        channel_note = "#finance-approvals"
    else:
        channel_note = "#finance"
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Payment Request",
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*From:* {request.requester_name}"
                    + (f" ({request.requester_email})" if request.requester_email else "")
                    + f"\n*To:* {request.payee_name}"
                    + f"\n*Amount:* ${request.amount:,.2f} {request.currency}"
                    + f"\n*Type:* {request.request_type.value.replace('_', ' ').title()}"
                )
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Description:*\n{request.description[:500]}"
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": f"approve_payment_request_{request.request_id}",
                    "value": request.request_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": f"reject_payment_request_{request.request_id}",
                    "value": request.request_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Details"},
                    "action_id": f"view_payment_request_{request.request_id}",
                    "value": request.request_id,
                }
            ]
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Request ID: {request.request_id} | Source: {request.source.value} | {channel_note}"
                }
            ]
        }
    ]
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(webhook_url, json={"blocks": blocks})
            response.raise_for_status()
        
        logger.info(f"Sent payment request notification for {request.request_id}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to send payment request notification: {e}")
        return False


async def send_invoice_approval_notification(
    invoice_id: str,
    gmail_thread_id: str,
    vendor: str,
    amount: float,
    due_date: Optional[str] = None,
    user_email: Optional[str] = None,
    exceptions: Optional[List[str]] = None,
) -> bool:
    """
    Send Slack notification for invoice requiring approval.
    
    Includes DEEP LINK to Gmail thread with sidebar auto-open.
    
    Args:
        invoice_id: Invoice ID
        gmail_thread_id: Gmail thread ID for deep linking
        vendor: Vendor name
        amount: Invoice amount
        due_date: Due date string
        user_email: User's email for Gmail deep link
        exceptions: List of issues requiring attention
    
    Returns:
        True if sent successfully
    """
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("Cannot send invoice notification - no webhook configured")
        return False
    
    # Build Gmail deep link that opens directly to the thread
    # Format: https://mail.google.com/mail/u/0/#inbox/{thread_id}
    gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{gmail_thread_id}"
    
    # Determine urgency color
    if exceptions:
        color = "#E65100"  # Orange for exceptions
    elif due_date:
        # Check if overdue
        try:
            from datetime import datetime
            due = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
            if due < datetime.now(due.tzinfo or None):
                color = "#C62828"  # Red for overdue
            else:
                color = "#1565C0"  # Blue for normal
        except:
            color = "#1565C0"
    else:
        color = "#1565C0"
    
    # Build exception text
    exception_text = ""
    if exceptions:
        exception_text = "\n*Issues:*\n" + "\n".join([f"• {e}" for e in exceptions])
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Invoice Needs Approval",
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Vendor:* {vendor}\n"
                    f"*Amount:* ${amount:,.2f}\n"
                    + (f"*Due:* {due_date}\n" if due_date else "")
                    + exception_text
                )
            },
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "View in Gmail"},
                "url": gmail_link,
                "action_id": f"view_gmail_{invoice_id}",
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": f"approve_invoice_{invoice_id}",
                    "value": invoice_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": f"reject_invoice_{invoice_id}",
                    "value": invoice_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Flag for Review"},
                    "action_id": f"flag_invoice_{invoice_id}",
                    "value": invoice_id,
                }
            ]
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Invoice ID: {invoice_id} | <{gmail_link}|Open in Gmail →>"
                }
            ]
        }
    ]
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(webhook_url, json={"blocks": blocks})
            response.raise_for_status()
        
        logger.info(f"Sent invoice approval notification for {invoice_id}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to send invoice notification: {e}")
        return False


async def send_invoice_posted_notification(
    invoice_id: str,
    vendor: str,
    amount: float,
    erp_system: str,
    erp_reference: str,
    approved_by: str,
) -> bool:
    """
    Send confirmation that invoice was posted to ERP.
    """
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return False
    
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Invoice Posted*\n\n"
                    f"*{vendor}* — ${amount:,.2f}\n"
                    f"Posted to {erp_system} (Ref: {erp_reference})\n"
                    f"Approved by: {approved_by}"
                )
            }
        }
    ]
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(webhook_url, json={"blocks": blocks})
            response.raise_for_status()
        return True
    except:
        return False


async def send_task_created_notification(*args, **kwargs):
    """Placeholder for task created notification."""
    pass


async def send_task_assigned_notification(*args, **kwargs):
    """Placeholder for task assigned notification."""
    pass


async def send_task_completed_notification(*args, **kwargs):
    """Placeholder for task completed notification."""
    pass


async def send_task_comment_notification(*args, **kwargs):
    """Placeholder for task comment notification."""
    pass


async def send_overdue_summary(*args, **kwargs):
    """Placeholder for overdue summary notification."""
    pass


class SlackNotificationService:
    """
    Synchronous Slack notification service for use in API endpoints.
    Simpler interface for sending notifications from the engine.
    """
    
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    def send_reconciliation_complete(
        self,
        total_transactions: int,
        matched: int,
        exceptions: int,
        organization_id: str,
    ) -> bool:
        """
        Send a simple reconciliation complete notification.
        
        Args:
            total_transactions: Total bank transactions processed
            matched: Number of matched transactions
            exceptions: Number of exceptions
            organization_id: Organization identifier
        
        Returns:
            True if sent successfully
        """
        from datetime import datetime
        
        match_rate = (matched / total_transactions * 100) if total_transactions > 0 else 0
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Bank Statement Imported - {datetime.now().strftime('%B %d, %Y')}",
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Summary:*\n"
                        f"- {total_transactions:,} bank transactions imported\n"
                        f"- {matched:,} matched automatically ({match_rate:.1f}%)\n"
                        f"- {exceptions} exceptions need review"
                    )
                }
            },
        ]
        
        if exceptions > 0:
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Review Exceptions"},
                        "style": "primary",
                        "action_id": "review_exceptions",
                    }
                ]
            })
        else:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "All transactions matched. No action needed."
                }
            })
        
        # Time saved estimate (~1 min per manual match)
        time_saved = matched
        time_str = f"{time_saved} minutes" if time_saved < 60 else f"{time_saved / 60:.1f} hours"
        
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Organization: {organization_id} | Time saved: ~{time_str}"}
            ]
        })
        
        try:
            import requests
            response = requests.post(
                self.webhook_url,
                json={"blocks": blocks},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")
            return False
