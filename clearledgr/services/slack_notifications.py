"""
Slack Notifications

Sends reconciliation results to Slack.
Following the spec: Exception-only notifications with one-click approval.
"""

import os
import logging
import httpx
from typing import Dict, Any, Optional, List
from clearledgr.services.slack_api import resolve_slack_runtime

logger = logging.getLogger(__name__)


def _build_approval_followup_blocks(
    *,
    ap_item: Dict[str, Any],
    vendor: str,
    amount: float,
    invoice_num: str,
    hours_pending: int,
    stage: str,
) -> List[Dict[str, Any]]:
    action_ref = str(ap_item.get("id") or invoice_num or "unknown").strip() or "unknown"
    currency = str(ap_item.get("currency") or "USD").strip() or "USD"
    details = {
        "Vendor": vendor,
        "Amount": f"{currency} {amount:,.2f}",
        "Invoice": invoice_num,
        "Waiting": f"{hours_pending}h",
    }
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Approval Escalation" if stage == "escalation" else "Approval Reminder",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{vendor}* invoice *#{invoice_num}* has been waiting for approval for *{hours_pending}h*.\n"
                    "Approve, reject, or request more information directly from Slack."
                ),
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*{key}:*\n{value}"}
                for key, value in details.items()
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": f"approve_invoice_{action_ref}",
                    "value": action_ref,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": f"reject_invoice_{action_ref}",
                    "value": action_ref,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Request info"},
                    "action_id": f"request_info_{action_ref}",
                    "value": action_ref,
                },
            ],
        },
    ]
    return blocks


async def _post_slack_blocks(
    blocks: List[Dict[str, Any]],
    text: str,
    preferred_channel: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> bool:
    """
    Send Slack blocks using webhook first, then bot token fallback.

    This supports both deployment styles:
    - Incoming webhook (SLACK_WEBHOOK_URL)
    - Bot token (SLACK_BOT_TOKEN + channel)
    """
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    runtime = resolve_slack_runtime(organization_id)
    bot_token = (runtime.get("bot_token") or "").strip()
    channel = (
        (preferred_channel or "").strip()
        or str(runtime.get("approval_channel") or "").strip()
        or "#finance-approvals"
    )

    if webhook_url:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    webhook_url,
                    json={"text": text, "blocks": blocks},
                    timeout=15,
                )
                response.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Slack webhook send failed, trying bot token fallback: {e}")

    if bot_token:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {bot_token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json={
                        "channel": channel,
                        "text": text,
                        "blocks": blocks,
                        "unfurl_links": False,
                        "unfurl_media": False,
                    },
                    timeout=15,
                )
            payload = response.json() if response.content else {}
            if response.status_code >= 400 or not payload.get("ok", False):
                logger.error(f"Slack bot send failed: status={response.status_code} payload={payload}")
                return False
            return True
        except Exception as e:
            logger.error(f"Slack bot token send failed: {e}")
            return False

    logger.warning(
        "No Slack delivery method configured (set Slack install or SLACK_BOT_TOKEN). org=%s mode=%s",
        organization_id or "default",
        runtime.get("mode"),
    )
    return False


async def send_with_retry(
    blocks: List[Dict[str, Any]],
    text: str,
    ap_item_id: Optional[str] = None,
    preferred_channel: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> bool:
    """Send Slack blocks, enqueueing for retry on failure."""
    try:
        ok = await _post_slack_blocks(blocks, text, preferred_channel, organization_id)
    except Exception as post_exc:
        logger.error("Slack _post_slack_blocks raised for ap_item=%s: %s", ap_item_id, post_exc)
        ok = False
    if ok:
        return True
    # Enqueue for retry
    try:
        from clearledgr.core.database import get_db
        db = get_db()
        db.enqueue_notification(
            organization_id=organization_id or "default",
            channel="slack",
            payload={
                "blocks": blocks,
                "text": text,
                "preferred_channel": preferred_channel,
            },
            ap_item_id=ap_item_id,
        )
        logger.info("Notification enqueued for retry (ap_item=%s)", ap_item_id)
    except Exception as e:
        logger.critical(
            "Slack send AND enqueue both failed for ap_item=%s channel=%s org=%s: %s",
            ap_item_id, preferred_channel, organization_id, e,
        )
    return False


async def _retry_slack_response_url(payload: dict) -> bool:
    """Retry a failed Slack response_url POST."""
    response_url = payload.get("response_url", "")
    body = payload.get("body", {})
    if not response_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(response_url, json=body)
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Slack response_url retry failed: %s", exc)
        return False


async def _retry_teams_card_update(payload: dict) -> bool:
    """Retry a failed Teams card update."""
    service_url = payload.get("service_url", "")
    conversation_id = payload.get("conversation_id", "")
    activity_id = payload.get("activity_id", "")
    if not (service_url and conversation_id and activity_id):
        return False
    try:
        from clearledgr.services.teams_api import TeamsAPIClient
        client = TeamsAPIClient()
        client.update_activity(
            service_url=service_url,
            conversation_id=conversation_id,
            activity_id=activity_id,
            result_status=payload.get("result_status", "unknown"),
            actor_display=payload.get("actor_display", "unknown"),
            action=payload.get("action", "unknown"),
            reason=payload.get("reason"),
        )
        return True
    except Exception as exc:
        logger.warning("Teams card update retry failed: %s", exc)
        return False


async def process_retry_queue() -> int:
    """Process pending notifications in the retry queue.

    Returns the number of notifications processed.
    Call this from a background task every 60 seconds.
    """
    from clearledgr.core.database import get_db
    db = get_db()
    pending = db.get_pending_notifications(limit=20)
    processed = 0
    for notif in pending:
        import json as _json
        payload = _json.loads(notif["payload_json"]) if isinstance(notif["payload_json"], str) else notif["payload_json"]
        channel = str(notif.get("channel") or "").strip()
        ok = False
        try:
            if channel == "slack_response_url":
                ok = await _retry_slack_response_url(payload)
            elif channel == "teams_card_update":
                ok = await _retry_teams_card_update(payload)
            else:
                ok = await _post_slack_blocks(
                    blocks=payload.get("blocks", []),
                    text=payload.get("text", ""),
                    preferred_channel=payload.get("preferred_channel"),
                    organization_id=notif.get("organization_id"),
                )
        except Exception as dispatch_exc:
            logger.warning("Retry dispatch error for %s: %s", notif["id"], dispatch_exc)
        if ok:
            db.mark_notification_sent(notif["id"])
            logger.info("Retry succeeded for notification %s", notif["id"])
        else:
            db.mark_notification_failed(notif["id"], "delivery failed")
            logger.warning(
                "Retry %d failed for notification %s",
                (notif.get("retry_count") or 0) + 1,
                notif["id"],
            )
        processed += 1
    return processed


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
    organization_id = getattr(request, "organization_id", None)
    preferred_channel = os.getenv("SLACK_APPROVAL_CHANNEL") or os.getenv("SLACK_DEFAULT_CHANNEL")
    
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
    
    sent = await send_with_retry(
        blocks=blocks,
        text=f"Payment request {request.request_id} requires approval",
        ap_item_id=request.request_id,
        preferred_channel=preferred_channel,
        organization_id=organization_id,
    )
    if sent:
        logger.info(f"Sent payment request notification for {request.request_id}")
    return sent


async def send_invoice_approval_notification(
    invoice_id: str,
    gmail_thread_id: str,
    vendor: str,
    amount: float,
    due_date: Optional[str] = None,
    user_email: Optional[str] = None,
    exceptions: Optional[List[str]] = None,
    organization_id: Optional[str] = None,
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
    preferred_channel = os.getenv("SLACK_APPROVAL_CHANNEL") or os.getenv("SLACK_DEFAULT_CHANNEL")
    
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
        except Exception:
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
    
    sent = await send_with_retry(
        blocks=blocks,
        text=f"Invoice {invoice_id} needs approval",
        ap_item_id=invoice_id,
        preferred_channel=preferred_channel,
        organization_id=organization_id,
    )
    if sent:
        logger.info(f"Sent invoice approval notification for {invoice_id}")
    return sent


async def send_invoice_posted_notification(
    invoice_id: str,
    vendor: str,
    amount: float,
    erp_system: str,
    erp_reference: str,
    approved_by: str,
    organization_id: Optional[str] = None,
) -> bool:
    """
    Send confirmation that invoice was posted to ERP.
    """
    preferred_channel = os.getenv("SLACK_APPROVAL_CHANNEL") or os.getenv("SLACK_DEFAULT_CHANNEL")
    
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
    
    return await _post_slack_blocks(
        blocks=blocks,
        text=f"Invoice {invoice_id} posted to {erp_system}",
        preferred_channel=preferred_channel,
        organization_id=organization_id,
    )


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


async def send_overdue_summary(
    overdue_items: List[Dict[str, Any]],
    stale_items: List[Dict[str, Any]],
    organization_id: str,
    preferred_channel: Optional[str] = None,
) -> bool:
    """Send a rich AP KPI dashboard to Slack with overdue highlights.

    Pulls the full KPI bundle from the DB (touchless rate, SLA breach %,
    missed discounts) then renders Slack blocks with:
      - Header KPI bar
      - Top-5 overdue items (vendor, amount, due date)
      - Top-3 stale items (vendor, stuck state)
      - Footer: pending count + missed discount value
    """
    try:
        from clearledgr.core.database import get_db

        db = get_db()
        kpis: Dict[str, Any] = {}
        try:
            kpis = db.get_ap_kpis(organization_id) or {}
        except Exception:
            pass

        # --- KPI summary line ---
        touchless = kpis.get("touchless_rate", {})
        touchless_pct = round((touchless.get("rate") or 0) * 100, 1)
        friction = kpis.get("approval_friction", {})
        sla_breach_pct = round((friction.get("sla_breach_rate") or 0) * 100, 1)
        missed = kpis.get("missed_discounts", {})
        missed_value = missed.get("missed_value") or 0
        totals = kpis.get("totals", {})
        pending_count = totals.get("items", 0) - totals.get("completed_items", 0)

        kpi_line = (
            f"Touchless: *{touchless_pct}%* | "
            f"SLA breach: *{sla_breach_pct}%* | "
            f"Pending: *{pending_count}*"
        )
        if missed_value:
            kpi_line += f" | Missed discounts: *${missed_value:,.2f}*"

        blocks: List[Dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":bar_chart: AP Status Dashboard"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": kpi_line},
            },
        ]

        # --- Overdue section ---
        if overdue_items:
            lines = [f"*{len(overdue_items)} overdue item(s):*"]
            for item in overdue_items[:5]:
                vendor = item.get("vendor_name") or "Unknown"
                amount = item.get("amount") or 0
                due = item.get("due_date") or "?"
                lines.append(f"  :red_circle: *{vendor}* — ${amount:,.2f} (due {due})")
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}
            )

        # --- Stale section ---
        if stale_items:
            lines = [f"*{len(stale_items)} stale item(s) needing attention:*"]
            for item in stale_items[:3]:
                vendor = item.get("vendor_name") or "Unknown"
                state = item.get("state") or "?"
                lines.append(f"  :warning: *{vendor}* — stuck in `{state}`")
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}
            )

        if not overdue_items and not stale_items:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": ":white_check_mark: No overdue or stale items."},
                }
            )

        blocks.append({"type": "divider"})

        channel = (
            preferred_channel
            or os.getenv("SLACK_APPROVAL_CHANNEL")
            or os.getenv("SLACK_DEFAULT_CHANNEL")
            or "#finance"
        )
        summary_text = f"AP Status: {len(overdue_items)} overdue, {len(stale_items)} stale"
        return await _post_slack_blocks(
            blocks=blocks,
            text=summary_text,
            preferred_channel=channel,
            organization_id=organization_id,
        )
    except Exception as exc:
        logger.error("send_overdue_summary failed: %s", exc)
        return False


async def send_approval_reminder(
    ap_item: Dict[str, Any],
    approver_ids: List[str],
    hours_pending: float,
    organization_id: Optional[str] = None,
    stage: str = "reminder",
    escalation_channel: Optional[str] = None,
) -> bool:
    """Send a reminder (or escalation) for an AP item stuck in needs_approval.

    - `stage="reminder"`: DM each pending approver
    - `stage="escalation"`: DM each pending approver and post to the approval channel
    """
    from clearledgr.services.slack_api import get_slack_client

    vendor = ap_item.get("vendor_name") or "Unknown vendor"
    amount = ap_item.get("amount") or 0
    invoice_num = ap_item.get("invoice_number") or "N/A"
    org_id = organization_id or ap_item.get("organization_id") or os.getenv("DEFAULT_ORGANIZATION_ID", "default")
    metadata = ap_item.get("metadata") if isinstance(ap_item.get("metadata"), dict) else {}

    is_escalation = str(stage or "").strip().lower() == "escalation"
    verb = "ESCALATION" if is_escalation else "Reminder"
    icon = ":rotating_light:" if is_escalation else ":bell:"
    h = int(hours_pending)
    dm_text = (
        f"{icon} *Approval {verb}* — {vendor} invoice #{invoice_num} "
        f"(${amount:,.2f}) has been waiting for approval for *{h}h*. "
        f"Please review and approve or reject."
    )
    try:
        amount_num = float(amount or 0)
    except (TypeError, ValueError):
        amount_num = 0.0

    reminder_blocks = _build_approval_followup_blocks(
        ap_item=ap_item,
        vendor=vendor,
        amount=amount_num,
        invoice_num=str(invoice_num),
        hours_pending=h,
        stage="escalation" if is_escalation else "reminder",
    )

    reminder_sent = False
    try:
        client = get_slack_client(organization_id=org_id)
        for uid in approver_ids:
            try:
                await client.send_dm(uid, dm_text, blocks=reminder_blocks)
                reminder_sent = True
            except Exception as dm_err:
                logger.error("Approval reminder DM to %s failed: %s", uid, dm_err)

        fallback_channel = (
            str(ap_item.get("slack_channel_id") or "").strip()
            or str(metadata.get("approval_channel") or "").strip()
            or str(escalation_channel or "").strip()
            or os.getenv("SLACK_APPROVAL_CHANNEL")
            or os.getenv("SLACK_DEFAULT_CHANNEL")
            or "#finance"
        )

        if not approver_ids and fallback_channel:
            reminder_sent = reminder_sent or bool(
                await _post_slack_blocks(
                    blocks=reminder_blocks,
                    text=dm_text,
                    preferred_channel=fallback_channel,
                    organization_id=org_id,
                )
            )

        if is_escalation:
            escalation_sent = await _post_slack_blocks(
                blocks=reminder_blocks,
                text=dm_text,
                preferred_channel=fallback_channel,
                organization_id=org_id,
            )
            reminder_sent = reminder_sent or bool(escalation_sent)
    except Exception as exc:
        logger.error("send_approval_reminder failed: %s", exc)
        return False
    return reminder_sent


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


# ---------------------------------------------------------------------------
# Payment readiness notification
# ---------------------------------------------------------------------------

async def send_payment_ready_notification(
    organization_id: str,
    ap_item_id: str,
    vendor_name: str,
    amount: float,
    currency: str,
    due_date: Optional[str],
    erp_reference: Optional[str],
) -> bool:
    """Notify the finance channel that an invoice is posted and ready for payment.

    This is a simple informational notification — it does NOT trigger any
    payment execution.  Humans decide when and how to pay.
    """
    due_str = due_date or "not specified"
    erp_str = erp_reference or "N/A"

    text = (
        f"Invoice from {vendor_name} for {currency} {amount:,.2f} is posted to ERP "
        f"and ready for payment. Due: {due_str}. ERP ref: {erp_str}."
    )

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":white_check_mark: *Payment Ready*\n"
                    f"Invoice from *{vendor_name}* for *{currency} {amount:,.2f}* "
                    f"is posted to ERP and ready for payment."
                ),
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Due Date:*\n{due_str}"},
                {"type": "mrkdwn", "text": f"*ERP Reference:*\n{erp_str}"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"AP Item: {ap_item_id}"},
            ],
        },
    ]

    return await send_with_retry(
        blocks=blocks,
        text=text,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
    )


# ---------------------------------------------------------------------------
# Payment status change notifications
# ---------------------------------------------------------------------------

async def send_payment_completed_notification(
    organization_id: str,
    vendor_name: str,
    amount: float,
    currency: str,
    payment_reference: Optional[str] = None,
    payment_method: Optional[str] = None,
    ap_item_id: Optional[str] = None,
) -> bool:
    """Notify that a payment has been detected as completed in the ERP."""
    ref_str = payment_reference or "N/A"
    method_str = payment_method or "ERP"

    text = (
        f"Payment completed: {vendor_name} {currency} {amount:,.2f} "
        f"via {method_str}. Ref: {ref_str}."
    )

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":white_check_mark: *Payment Completed*\n"
                    f"*{vendor_name}* — *{currency} {amount:,.2f}* via {method_str}."
                ),
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Reference:*\n{ref_str}"},
                {"type": "mrkdwn", "text": f"*Method:*\n{method_str}"},
            ],
        },
    ]

    return await send_with_retry(
        blocks=blocks,
        text=text,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
    )


async def send_payment_partial_notification(
    organization_id: str,
    vendor_name: str,
    amount: float,
    paid_amount: float,
    remaining: float,
    currency: str = "USD",
    ap_item_id: Optional[str] = None,
) -> bool:
    """Notify that a partial payment has been detected in the ERP."""
    text = (
        f"Partial payment: {vendor_name} — {currency} {paid_amount:,.2f} of "
        f"{currency} {amount:,.2f} paid. Remaining: {currency} {remaining:,.2f}."
    )

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":hourglass_flowing_sand: *Partial Payment Detected*\n"
                    f"*{vendor_name}* — *{currency} {paid_amount:,.2f}* of "
                    f"*{currency} {amount:,.2f}* paid."
                ),
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Remaining:*\n{currency} {remaining:,.2f}"},
            ],
        },
    ]

    return await send_with_retry(
        blocks=blocks,
        text=text,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
    )


async def send_payment_reversed_notification(
    organization_id: str,
    vendor_name: str,
    amount: float,
    currency: str = "USD",
    reference: Optional[str] = None,
    ap_item_id: Optional[str] = None,
) -> bool:
    """Notify that a payment was reversed/voided in the ERP."""
    ref_str = reference or "N/A"
    text = (
        f"Payment REVERSED: {vendor_name} {currency} {amount:,.2f}. "
        f"ERP ref: {ref_str}. "
        f"The payment was voided or returned. Manual review required."
    )

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":rotating_light: *Payment REVERSED*\n"
                    f"*{vendor_name}* — *{currency} {amount:,.2f}*\n"
                    f"The payment was voided or returned in the ERP. Manual review required."
                ),
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*ERP Reference:*\n{ref_str}"},
            ],
        },
    ]

    return await send_with_retry(
        blocks=blocks,
        text=text,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
    )


async def send_payment_overdue_notification(
    organization_id: str,
    vendor_name: str,
    amount: float,
    currency: str = "USD",
    due_date: Optional[str] = None,
    days_overdue: int = 0,
    ap_item_id: Optional[str] = None,
) -> bool:
    """Notify that a payment is overdue (past due_date but not yet paid)."""
    due_str = due_date or "unknown"
    text = (
        f"OVERDUE: {vendor_name} {currency} {amount:,.2f} was due {due_str} "
        f"({days_overdue} days ago). Payment not yet detected in ERP."
    )

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":warning: *Payment OVERDUE*\n"
                    f"*{vendor_name}* — *{currency} {amount:,.2f}*\n"
                    f"Due {due_str} ({days_overdue} days ago). "
                    f"Payment not yet detected in ERP."
                ),
            },
        },
    ]

    return await send_with_retry(
        blocks=blocks,
        text=text,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
    )


async def send_payment_failed_notification(
    organization_id: str,
    vendor_name: str,
    amount: float,
    currency: str = "USD",
    reason: Optional[str] = None,
    ap_item_id: Optional[str] = None,
) -> bool:
    """Notify that a payment failed in the ERP."""
    reason_str = reason or "unknown"
    text = (
        f"Payment FAILED: {vendor_name} {currency} {amount:,.2f}. "
        f"Reason: {reason_str}. Manual intervention required."
    )

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":x: *Payment FAILED*\n"
                    f"*{vendor_name}* — *{currency} {amount:,.2f}*\n"
                    f"Reason: {reason_str}. Manual intervention required."
                ),
            },
        },
    ]

    return await send_with_retry(
        blocks=blocks,
        text=text,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
    )


async def send_payment_credit_applied_notification(
    organization_id: str,
    vendor_name: str,
    amount: float,
    currency: str = "USD",
    closure_method: Optional[str] = None,
    reference: Optional[str] = None,
    ap_item_id: Optional[str] = None,
) -> bool:
    """Notify that an invoice was closed by credit/write-off instead of payment."""
    method_str = closure_method or "credit"
    ref_str = reference or ""
    text = (
        f"Invoice closed by credit: {vendor_name} {currency} {amount:,.2f}. "
        f"Credit/write-off applied in ERP."
    )

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":memo: *Invoice Closed by Credit*\n"
                    f"*{vendor_name}* — *{currency} {amount:,.2f}*\n"
                    f"Closure method: {method_str}. {ref_str}"
                ),
            },
        },
    ]

    return await send_with_retry(
        blocks=blocks,
        text=text,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
    )
