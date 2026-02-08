"""
Clearledgr Task Scheduler

Autonomous task follow-ups and reminders:
- Daily overdue check
- Reminder notifications
- Escalation for stale tasks
"""

import os
import uuid
from clearledgr.services.db import DB
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from clearledgr.services.email_tasks import get_overdue_tasks, get_tasks, get_task
from clearledgr.services.task_notifications import (
    send_task_notification, send_overdue_summary
)


# Database for scheduler state
SCHEDULER_DB_PATH = os.environ.get("SCHEDULER_DB_PATH", "task_scheduler.sqlite3")
db = DB(sqlite_path=SCHEDULER_DB_PATH)


def init_scheduler_db():
    """Initialize scheduler database."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS reminder_log (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            reminder_type TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            next_reminder TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_runs (
            id TEXT PRIMARY KEY,
            run_type TEXT NOT NULL,
            run_at TEXT NOT NULL,
            tasks_processed INTEGER,
            reminders_sent INTEGER
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_reminder_task 
        ON reminder_log(task_id, sent_at DESC)
    """)


def should_send_reminder(task_id: str, reminder_type: str, min_hours: int = 24) -> bool:
    """Check if we should send a reminder (avoid spamming)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=min_hours)).isoformat()
    row = db.fetchone(
        """
        SELECT COUNT(*) FROM reminder_log 
        WHERE task_id = ? AND reminder_type = ? AND sent_at > ?
        """,
        (task_id, reminder_type, cutoff),
    )
    count = row[0] if row else 0
    return count == 0


def log_reminder(task_id: str, reminder_type: str, next_reminder: str = None):
    """Log that a reminder was sent."""
    db.execute(
        """
        INSERT INTO reminder_log (id, task_id, reminder_type, sent_at, next_reminder)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            task_id,
            reminder_type,
            datetime.now(timezone.utc).isoformat(),
            next_reminder,
        ),
    )


def log_scheduler_run(run_type: str, tasks_processed: int, reminders_sent: int):
    """Log a scheduler run."""
    db.execute(
        """
        INSERT INTO scheduler_runs (id, run_type, run_at, tasks_processed, reminders_sent)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            run_type,
            datetime.now(timezone.utc).isoformat(),
            tasks_processed,
            reminders_sent,
        ),
    )


def run_overdue_check(config: Dict = None) -> Dict:
    """
    Check for overdue tasks and send reminders.
    
    This should be called periodically (e.g., daily via cron or scheduler).
    
    Returns:
        Summary of actions taken
    """
    config = config or {}
    
    overdue_tasks = get_overdue_tasks()
    reminders_sent = 0
    escalations = 0
    
    for task in overdue_tasks:
        task_id = task.get('task_id')
        days_overdue = calculate_days_overdue(task.get('due_date'))
        
        # Determine reminder type based on how overdue
        if days_overdue >= 7:
            reminder_type = "escalation"
        elif days_overdue >= 3:
            reminder_type = "urgent_reminder"
        else:
            reminder_type = "overdue_reminder"
        
        # Check if we should send (avoid spam)
        min_hours = 24 if reminder_type == "overdue_reminder" else 12
        
        if should_send_reminder(task_id, reminder_type, min_hours):
            # Send reminder
            success = send_task_reminder(task, reminder_type, days_overdue, config)
            
            if success:
                log_reminder(task_id, reminder_type)
                reminders_sent += 1
                
                if reminder_type == "escalation":
                    escalations += 1
    
    # Send summary if there are overdue tasks
    if overdue_tasks and should_send_reminder("daily_summary", "overdue_summary", 20):
        send_overdue_summary(overdue_tasks, config)
        log_reminder("daily_summary", "overdue_summary")
    
    log_scheduler_run("overdue_check", len(overdue_tasks), reminders_sent)
    
    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "overdue_tasks": len(overdue_tasks),
        "reminders_sent": reminders_sent,
        "escalations": escalations
    }


def run_approaching_deadline_check(config: Dict = None) -> Dict:
    """
    Check for tasks approaching deadline and send proactive reminders.
    
    Returns:
        Summary of actions taken
    """
    config = config or {}
    
    # Get all open tasks
    all_tasks = get_tasks(include_completed=False)
    
    approaching = []
    reminders_sent = 0
    
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%d')
    day_after = (datetime.now(timezone.utc) + timedelta(days=2)).strftime('%Y-%m-%d')
    
    for task in all_tasks:
        due_date = task.get('due_date')
        if not due_date:
            continue
        
        # Check if due tomorrow or day after
        if due_date == tomorrow or due_date == day_after:
            approaching.append(task)
            
            task_id = task.get('task_id')
            if should_send_reminder(task_id, "approaching_deadline", 24):
                days_until = 1 if due_date == tomorrow else 2
                success = send_approaching_deadline_reminder(task, days_until, config)
                
                if success:
                    log_reminder(task_id, "approaching_deadline")
                    reminders_sent += 1
    
    log_scheduler_run("approaching_deadline_check", len(approaching), reminders_sent)
    
    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "approaching_tasks": len(approaching),
        "reminders_sent": reminders_sent
    }


def run_stale_task_check(config: Dict = None, stale_days: int = 5) -> Dict:
    """
    Check for stale tasks (no activity) and send nudge.
    
    Returns:
        Summary of actions taken
    """
    config = config or {}
    
    all_tasks = get_tasks(include_completed=False)
    
    stale = []
    reminders_sent = 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()
    
    for task in all_tasks:
        # Check last update
        updated_at = task.get('updated_at') or task.get('created_at')
        if updated_at and updated_at < cutoff:
            stale.append(task)
            
            task_id = task.get('task_id')
            if should_send_reminder(task_id, "stale_task", 48):
                success = send_stale_task_reminder(task, stale_days, config)
                
                if success:
                    log_reminder(task_id, "stale_task")
                    reminders_sent += 1
    
    log_scheduler_run("stale_task_check", len(stale), reminders_sent)
    
    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "stale_tasks": len(stale),
        "reminders_sent": reminders_sent
    }


def send_task_reminder(task: Dict, reminder_type: str, days_overdue: int, config: Dict) -> bool:
    """Send a task reminder notification."""
    
    # Build context for notification
    context = {
        "days_overdue": days_overdue,
        "reminder_type": reminder_type
    }
    
    if reminder_type == "escalation":
        # Escalation - more urgent
        task_copy = dict(task)
        task_copy['title'] = f"ESCALATION: {task.get('title', '')}"
        return send_task_notification("overdue", task_copy, config, context)
    else:
        return send_task_notification("overdue", task, config, context)


def send_approaching_deadline_reminder(task: Dict, days_until: int, config: Dict) -> bool:
    """Send approaching deadline reminder."""
    
    context = {
        "days_until": days_until,
        "reminder_type": "approaching"
    }
    
    task_copy = dict(task)
    if days_until == 1:
        task_copy['title'] = f"[DUE TOMORROW] {task.get('title', '')}"
    else:
        task_copy['title'] = f"Due in {days_until} days: {task.get('title', '')}"
    
    return send_task_notification("overdue", task_copy, config, context)


def send_stale_task_reminder(task: Dict, stale_days: int, config: Dict) -> bool:
    """Send stale task nudge."""
    
    context = {
        "stale_days": stale_days,
        "reminder_type": "stale"
    }
    
    task_copy = dict(task)
    task_copy['title'] = f"💤 No activity ({stale_days}+ days): {task.get('title', '')}"
    
    return send_task_notification("overdue", task_copy, config, context)


def calculate_days_overdue(due_date: str) -> int:
    """Calculate how many days a task is overdue."""
    if not due_date:
        return 0
    
    try:
        due = datetime.strptime(due_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - due
        return max(0, delta.days)
    except ValueError:
        return 0


def run_all_checks(config: Dict = None) -> Dict:
    """
    Run all scheduled checks.
    
    This is the main entry point for scheduled execution.
    """
    config = config or {}
    
    results = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "checks": {}
    }
    
    # Run overdue check
    results["checks"]["overdue"] = run_overdue_check(config)
    
    # Run approaching deadline check
    results["checks"]["approaching"] = run_approaching_deadline_check(config)
    
    # Run stale task check
    results["checks"]["stale"] = run_stale_task_check(config)
    
    # Calculate totals
    results["total_reminders"] = sum(
        check.get("reminders_sent", 0) 
        for check in results["checks"].values()
    )
    
    return results


# Initialize on import
init_scheduler_db()
