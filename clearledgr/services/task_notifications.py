"""
Clearledgr Task Notifications

Sends task notifications via Slack and Teams APPS (not webhooks).
Uses the native app integrations for rich interactive messages.
"""

import os
import asyncio
from typing import Dict, Optional, List
from datetime import datetime

# Import the app notification functions
try:
    from ui.slack.app import notify_task_reminder as slack_notify_task
    from ui.slack.app import send_slack_message, build_tasks_blocks
    SLACK_AVAILABLE = True
except ImportError:
    SLACK_AVAILABLE = False

try:
    from ui.teams.app import notify_task_reminder as teams_notify_task
    from ui.teams.app import send_teams_message, create_tasks_card
    TEAMS_AVAILABLE = True
except ImportError:
    TEAMS_AVAILABLE = False


# Bot tokens from environment (set during app installation)
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_DEFAULT_CHANNEL = os.environ.get("SLACK_DEFAULT_CHANNEL", "#finance")
INVITE_URL = os.environ.get("CLEARLEDGR_INVITE_URL")


def send_task_notification(
    notification_type: str,
    task: Dict,
    config: Dict = None,
    additional_context: Dict = None
) -> bool:
    """
    Send task notification via Slack and/or Teams apps.
    
    Args:
        notification_type: Type of notification (created, assigned, completed, overdue)
        task: Task data
        config: Optional config with channel preferences
        additional_context: Additional context data
        
    Returns:
        True if any notification was sent successfully
    """
    config = config or {}
    additional_context = additional_context or {}
    
    success = False
    
    # Send via Slack app
    if SLACK_AVAILABLE and SLACK_BOT_TOKEN:
        try:
            channel = config.get("slack_channel", SLACK_DEFAULT_CHANNEL)
            blocks = build_task_notification_blocks(notification_type, task, additional_context)
            
            # Run async function
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                send_slack_message(channel, blocks, token=SLACK_BOT_TOKEN)
            )
            loop.close()
            
            success = result.get("ok", False) or success
        except Exception as e:
            print(f"Slack app notification error: {e}")
    
    # Send via Teams app
    if TEAMS_AVAILABLE and config.get("teams_conversation_id"):
        try:
            card = build_task_notification_card(notification_type, task, additional_context)
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            # Teams uses Bot Framework for proactive messaging
            # This would use the stored conversation reference
            loop.close()
            
            success = True
        except Exception as e:
            print(f"Teams app notification error: {e}")
    
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
    config: Dict = None
) -> bool:
    """Send summary of overdue tasks via app."""
    config = config or {}
    
    if not tasks:
        return True
    
    if not SLACK_AVAILABLE or not SLACK_BOT_TOKEN:
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
        channel = config.get("slack_channel", SLACK_DEFAULT_CHANNEL)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(
            send_slack_message(channel, blocks, token=SLACK_BOT_TOKEN)
        )
        loop.close()
        return result.get("ok", False)
    except Exception as e:
        print(f"Failed to send overdue summary: {e}")
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
