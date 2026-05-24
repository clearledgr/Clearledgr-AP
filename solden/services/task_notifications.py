"""
Solden Task Notifications

Sends task notifications via Slack and Teams APPS (not webhooks).
Uses the native app integrations for rich interactive messages.
"""

import os
import asyncio
from typing import Dict, List

import logging

_logger = logging.getLogger(__name__)

# Import the app notification functions
try:
    from ui.slack.app import send_slack_message
    SLACK_AVAILABLE = True
except ImportError as exc:
    _logger.warning("Slack notifications unavailable: %s", exc)
    SLACK_AVAILABLE = False

try:
    import ui.teams.app  # noqa: F401  # availability probe
    TEAMS_AVAILABLE = True
except ImportError as exc:
    _logger.warning("Teams notifications unavailable: %s", exc)
    TEAMS_AVAILABLE = False


from solden.core.secrets import optional_secret as _optional_secret  # noqa: E402

INVITE_URL = _optional_secret("SOLDEN_INVITE_URL") or None


def _run_coro(coro):
    """Run a coroutine to completion whether or not a loop is already running.

    Task notifications are sent from sync code that may be called either
    standalone or from inside the background async tick. ``asyncio.run``
    raises if a loop is already running, so fall back to a worker thread.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)


def _resolve_org_slack(organization_id, config):
    """Resolve this org's Slack bot token + channel.

    Returns ``(token, channel)`` or ``(None, None)`` when the org has no
    connected Slack. Routes per-org via ``resolve_slack_runtime`` — never a
    shared global channel — so one tenant's task details never land in
    another tenant's (or a platform-wide) channel.
    """
    if not organization_id:
        return None, None
    try:
        from solden.services.slack_api import resolve_slack_runtime
        runtime = resolve_slack_runtime(organization_id)
    except Exception as exc:
        _logger.warning("Slack runtime resolution failed for org=%s: %s", organization_id, exc)
        return None, None
    if not runtime.get("connected") or not runtime.get("bot_token"):
        return None, None
    channel = (config or {}).get("slack_channel") or runtime.get("approval_channel")
    return runtime.get("bot_token"), channel


def send_task_notification(
    notification_type: str,
    task: Dict,
    config: Dict = None,
    additional_context: Dict = None,
    organization_id: str = None,
) -> bool:
    """
    Send task notification via Slack and/or Teams apps.

    Routes to the task's own org Slack (per-org token + channel). If the org
    has no connected Slack, the notification is skipped (no global channel).

    Args:
        notification_type: Type of notification (created, assigned, completed, overdue)
        task: Task data
        config: Optional config with channel preferences
        additional_context: Additional context data
        organization_id: Org to route to; defaults to the task's organization_id

    Returns:
        True if any notification was sent successfully
    """
    config = config or {}
    additional_context = additional_context or {}

    success = False
    org = organization_id or task.get("organization_id")
    token, channel = _resolve_org_slack(org, config)

    # Send via Slack app (per-org token + channel)
    if SLACK_AVAILABLE and token and channel:
        try:
            blocks = build_task_notification_blocks(notification_type, task, additional_context)

            result = _run_coro(
                send_slack_message(channel, blocks, token=token)
            )

            success = result.get("ok", False) or success
        except Exception as e:
            _logger.warning("Slack app notification error (org=%s): %s", org, e)
    elif org:
        _logger.info(
            "Task notification skipped: org=%s has no connected Slack (type=%s)",
            org, notification_type,
        )

    # Send via Teams app
    if TEAMS_AVAILABLE and config.get("teams_conversation_id"):
        # Teams proactive messaging needs the Bot Framework adapter
        # plus a stored conversation reference; that wiring isn't here
        # yet, so we log instead of pretending the send succeeded.
        _logger.info(
            "Teams notification skipped (proactive-messaging not wired): type=%s task_id=%s",
            notification_type, task.get("id"),
        )
    
    return success


def build_task_notification_blocks(
    notification_type: str,
    task: Dict,
    context: Dict
) -> list:
    """Build Slack blocks for task notification."""
    
    title_map = {
        "created": "New Task Created",
        "assigned": "Task Assigned to You",
        "completed": "Task Completed",
        "overdue": "Task Overdue",
        "comment": "New Comment on Task",
        "reminder": "Task Reminder"
    }
    
    title = title_map.get(notification_type, "Task Update")
    
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title}
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{task.get('title', 'Untitled')}*"
            }
        },
        {
            "type": "section",
            "fields": []
        }
    ]
    
    # Add fields
    fields = blocks[2]["fields"]
    
    if task.get("priority"):
        priority_indicator = {"urgent": "[URGENT]", "high": "[HIGH]", "medium": "[MED]", "low": "[LOW]"}.get(task["priority"], "")
        fields.append({"type": "mrkdwn", "text": f"*Priority:* {priority_indicator} {task['priority'].title()}"})
    
    if task.get("due_date"):
        fields.append({"type": "mrkdwn", "text": f"*Due:* {task['due_date']}"})
    
    if task.get("assignee_email"):
        fields.append({"type": "mrkdwn", "text": f"*Assigned to:* {task['assignee_email']}"})
    
    if task.get("related_vendor"):
        fields.append({"type": "mrkdwn", "text": f"*Vendor:* {task['related_vendor']}"})
    
    if task.get("related_amount"):
        fields.append({"type": "mrkdwn", "text": f"*Amount:* €{task['related_amount']:,.2f}"})
    
    if context.get("assigned_by"):
        fields.append({"type": "mrkdwn", "text": f"*Assigned by:* {context['assigned_by']}"})
    
    if context.get("comment"):
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"Comment: _{context['comment']}_"}
        })
    
    # Add action buttons
    task_id = task.get("task_id") or task.get("id") or "unknown"
    action_elements = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Complete"},
            "style": "primary",
            "action_id": f"complete_task_{task_id}"
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "View Details"},
            "action_id": f"view_task_{task_id}"
        }
    ]
    if INVITE_URL:
        action_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Invite Approver"},
            "url": INVITE_URL,
            "action_id": "invite_approver"
        })

    blocks.append({
        "type": "actions",
        "elements": action_elements
    })
    
    return blocks


def build_task_notification_card(
    notification_type: str,
    task: Dict,
    context: Dict
) -> dict:
    """Build Teams Adaptive Card for task notification."""
    
    title_map = {
        "created": "New Task Created",
        "assigned": "Task Assigned to You",
        "completed": "Task Completed",
        "overdue": "Task Overdue",
        "comment": "New Comment on Task",
        "reminder": "Task Reminder"
    }
    
    color_map = {
        "created": "Good",
        "assigned": "Accent",
        "completed": "Good",
        "overdue": "Attention",
        "comment": "Accent",
        "reminder": "Warning"
    }
    
    title = title_map.get(notification_type, "Task Update")
    
    facts = [
        {"title": "Task", "value": task.get("title", "Untitled")}
    ]
    
    if task.get("priority"):
        facts.append({"title": "Priority", "value": task["priority"].title()})
    
    if task.get("due_date"):
        facts.append({"title": "Due", "value": task["due_date"]})
    
    if task.get("assignee_email"):
        facts.append({"title": "Assigned to", "value": task["assignee_email"]})
    
    if task.get("related_vendor"):
        facts.append({"title": "Vendor", "value": task["related_vendor"]})
    
    if task.get("related_amount"):
        facts.append({"title": "Amount", "value": f"€{task['related_amount']:,.2f}"})
    
    if context.get("assigned_by"):
        facts.append({"title": "Assigned by", "value": context["assigned_by"]})
    
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": title,
                "color": color_map.get(notification_type, "Default")
            },
            {
                "type": "FactSet",
                "facts": facts
            }
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Complete",
                "style": "positive",
                "data": {"action": "complete_task", "task_id": task.get("id")}
            },
            {
                "type": "Action.Submit",
                "title": "View Details",
                "data": {"action": "view_task", "task_id": task.get("id")}
            }
        ]
    }


def send_task_created_notification(task: Dict, config: Dict = None) -> bool:
    """Send notification when a task is created."""
    return send_task_notification("created", task, config)


def send_task_assigned_notification(
    task: Dict,
    assigned_by: str,
    config: Dict = None
) -> bool:
    """Send notification when a task is assigned."""
    return send_task_notification(
        "assigned",
        task,
        config,
        {"assigned_by": assigned_by}
    )


def send_task_completed_notification(task: Dict, config: Dict = None) -> bool:
    """Send notification when a task is completed."""
    return send_task_notification("completed", task, config)


def send_task_overdue_notification(task: Dict, config: Dict = None) -> bool:
    """Send notification for overdue task."""
    return send_task_notification("overdue", task, config)


def send_task_comment_notification(
    task: Dict,
    comment: str,
    commenter: str,
    config: Dict = None
) -> bool:
    """Send notification when a comment is added."""
    return send_task_notification(
        "comment",
        task,
        config,
        {"comment": comment, "commenter": commenter}
    )


def send_overdue_summary(
    tasks: List[Dict],
    config: Dict = None,
    organization_id: str = None,
) -> bool:
    """Send summary of overdue tasks to the org's own Slack channel."""
    config = config or {}

    if not tasks:
        return True

    org = organization_id or (tasks[0].get("organization_id") if tasks else None)
    token, channel = _resolve_org_slack(org, config)
    if not SLACK_AVAILABLE or not token or not channel:
        return False

    total = len(tasks)
    by_priority = {}
    for task in tasks:
        priority = task.get("priority", "medium")
        by_priority[priority] = by_priority.get(priority, 0) + 1
    
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"[ALERT] {total} Overdue Task(s)"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*[URGENT]:* {by_priority.get('urgent', 0)}"},
                {"type": "mrkdwn", "text": f"*[HIGH]:* {by_priority.get('high', 0)}"},
                {"type": "mrkdwn", "text": f"*[MED]:* {by_priority.get('medium', 0)}"},
                {"type": "mrkdwn", "text": f"*[LOW]:* {by_priority.get('low', 0)}"}
            ]
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View All Tasks"},
                    "style": "primary",
                    "action_id": "view_overdue_tasks"
                }
            ]
        }
    ]
    
    try:
        result = _run_coro(
            send_slack_message(channel, blocks, token=token)
        )
        return result.get("ok", False)
    except Exception as e:
        _logger.warning("Failed to send overdue summary: %s", e)
        return False


def format_task_type(task_type: str) -> str:
    """Format task type for display."""
    types = {
        "collect_docs": "📁 Collect Documents",
        "chase_approver": "🏃 Chase Approver",
        "reconcile_item": "Reconcile Item",
        "verify_payment": "Verify Payment",
        "follow_up": "Follow Up",
        "close_task": "Close Task",
        "investigate": "Investigate",
        "approve": "Approve"
    }
    return types.get(task_type, task_type)


def format_priority(priority: str) -> str:
    """Format priority for display."""
    priorities = {
        "urgent": "[URGENT]",
        "high": "[HIGH]",
        "medium": "[MED]",
        "low": "[LOW]"
    }
    return priorities.get(priority, priority)
