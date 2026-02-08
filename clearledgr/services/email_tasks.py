"""
Clearledgr Email Tasks Service

Turns email threads into actionable tasks:
- Creates close tasks from emails
- Tracks status and assignments
- Syncs with close checklist
- Maintains audit trail
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
import os
from clearledgr.services.db import DB


# Database path
TASKS_DB_PATH = os.environ.get("TASKS_DB_PATH", "email_tasks.sqlite3")
db = DB(sqlite_path=TASKS_DB_PATH)


def _make_id(prefix: str) -> str:
    ts = datetime.now(timezone.utc).isoformat()
    return f"{prefix}_{ts.replace(':', '').replace('-', '').replace('.', '')}"


def init_tasks_db():
    """Initialize tasks database."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS email_tasks (
            task_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            task_type TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            priority TEXT DEFAULT 'medium',
            assignee_email TEXT,
            created_by TEXT NOT NULL,
            source_email_id TEXT,
            source_email_subject TEXT,
            source_email_sender TEXT,
            source_thread_id TEXT,
            due_date TEXT,
            related_entity_type TEXT,
            related_entity_id TEXT,
            related_amount REAL,
            related_vendor TEXT,
            tags TEXT,
            metadata TEXT,
            organization_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            completed_at TEXT
        )
    """)
    
    db.execute("""
        CREATE TABLE IF NOT EXISTS task_comments (
            comment_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            user_email TEXT NOT NULL,
            comment TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES email_tasks(task_id)
        )
    """)
    
    db.execute("""
        CREATE TABLE IF NOT EXISTS task_status_history (
            history_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (task_id) REFERENCES email_tasks(task_id)
        )
    """)
    
    # Indexes
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_status 
        ON email_tasks(status, due_date)
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_assignee 
        ON email_tasks(assignee_email, status)
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_source 
        ON email_tasks(source_email_id)
    """)


def create_task_from_email(
    email_id: str,
    email_subject: str,
    email_sender: str,
    thread_id: str,
    created_by: str,
    task_type: str,
    title: str = None,
    description: str = None,
    assignee_email: str = None,
    due_date: str = None,
    priority: str = "medium",
    related_entity_type: str = None,
    related_entity_id: str = None,
    related_amount: float = None,
    related_vendor: str = None,
    tags: List[str] = None,
    organization_id: str = None
) -> Dict[str, Any]:
    """
    Create a task from an email.
    
    Args:
        email_id: Unique email ID
        email_subject: Email subject
        email_sender: Sender email
        thread_id: Email thread ID
        created_by: User who created the task
        task_type: Type of task (collect_docs, chase_approver, reconcile_item, etc.)
        title: Task title (defaults to email subject)
        description: Task description
        assignee_email: Who should do the task
        due_date: Due date (ISO format)
        priority: low, medium, high, urgent
        related_entity_type: invoice, transaction, reconciliation, etc.
        related_entity_id: ID of related entity
        related_amount: Amount involved
        related_vendor: Related vendor name
        tags: Task tags
        organization_id: Organization
        
    Returns:
        Created task data
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    task_id = _make_id("task")
    
    # Default title from email subject
    if not title:
        title = f"[{task_type}] {email_subject}"
    
    # Default due date based on task type
    if not due_date:
        due_date = _calculate_default_due_date(task_type)
    
    db.execute("""
        INSERT INTO email_tasks (
            task_id, title, description, task_type, status, priority,
            assignee_email, created_by, source_email_id, source_email_subject,
            source_email_sender, source_thread_id, due_date,
            related_entity_type, related_entity_id, related_amount, related_vendor,
            tags, organization_id, created_at
        ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        task_id, title, description, task_type, priority,
        assignee_email, created_by, email_id, email_subject,
        email_sender, thread_id, due_date,
        related_entity_type, related_entity_id, related_amount, related_vendor,
        json.dumps(tags) if tags else None, organization_id, timestamp
    ))
    
    # Record initial status
    db.execute("""
        INSERT INTO task_status_history (history_id, task_id, to_status, changed_by, notes)
        VALUES (?, ?, 'open', ?, ?)
    """, (_make_id("history"), task_id, created_by, None))
    
    return get_task(task_id) or {
        "task_id": task_id,
        "title": title,
        "task_type": task_type,
        "status": "open",
        "priority": priority,
        "assignee_email": assignee_email,
        "created_by": created_by,
        "source_email_id": email_id,
        "due_date": due_date,
        "created_at": timestamp
    }


def update_task_status(
    task_id: str,
    new_status: str,
    changed_by: str,
    notes: str = None
) -> Dict[str, Any]:
    """
    Update task status.
    
    Args:
        task_id: Task ID
        new_status: New status (open, in_progress, pending_approval, completed, cancelled)
        changed_by: User making the change
        notes: Optional notes
        
    Returns:
        Updated task
    """
    # Get current status
    row = db.fetchone_dict("SELECT status FROM email_tasks WHERE task_id = ?", (task_id,))
    
    if not row:
        return {"error": "Task not found"}
    
    old_status = row.get("status")
    timestamp = datetime.now(timezone.utc).isoformat()
    
    # Update task
    update_fields = ["status = ?", "updated_at = ?"]
    params = [new_status, timestamp]
    
    if new_status == "completed":
        update_fields.append("completed_at = ?")
        params.append(timestamp)
    
    params.append(task_id)
    
    db.execute(f"""
        UPDATE email_tasks 
        SET {', '.join(update_fields)}
        WHERE task_id = ?
    """, tuple(params))
    
    # Record history
    db.execute("""
        INSERT INTO task_status_history (history_id, task_id, from_status, to_status, changed_by, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (_make_id("history"), task_id, old_status, new_status, changed_by, notes))
    
    return get_task(task_id)


def assign_task(
    task_id: str,
    assignee_email: str,
    assigned_by: str
) -> Dict[str, Any]:
    """Assign task to user."""
    timestamp = datetime.now(timezone.utc).isoformat()
    
    db.execute("""
        UPDATE email_tasks 
        SET assignee_email = ?, updated_at = ?
        WHERE task_id = ?
    """, (assignee_email, timestamp, task_id))
    
    return get_task(task_id)


def add_comment(
    task_id: str,
    user_email: str,
    comment: str
) -> Dict[str, Any]:
    """Add comment to task."""
    timestamp = datetime.now(timezone.utc).isoformat()
    comment_id = _make_id("comment")
    
    db.execute("""
        INSERT INTO task_comments (comment_id, task_id, user_email, comment, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (comment_id, task_id, user_email, comment, timestamp))
    
    # Update task timestamp
    db.execute("""
        UPDATE email_tasks SET updated_at = ? WHERE task_id = ?
    """, (timestamp, task_id))
    
    return {
        "comment_id": comment_id,
        "task_id": task_id,
        "user_email": user_email,
        "comment": comment,
        "created_at": timestamp
    }


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Get task by ID."""
    row = db.fetchone_dict("SELECT * FROM email_tasks WHERE task_id = ?", (task_id,))
    
    if not row:
        return None
    
    task = dict(row)
    if task.get('tags'):
        task['tags'] = json.loads(task['tags'])
    if task.get('metadata'):
        task['metadata'] = json.loads(task['metadata'])
    
    # Get comments
    task['comments'] = db.fetchall_dict("""
        SELECT * FROM task_comments 
        WHERE task_id = ? 
        ORDER BY created_at DESC
    """, (task_id,))
    
    # Get history
    task['status_history'] = db.fetchall_dict("""
        SELECT * FROM task_status_history 
        WHERE task_id = ? 
        ORDER BY changed_at DESC
    """, (task_id,))
    
    return task


def get_tasks(
    status: str = None,
    assignee_email: str = None,
    task_type: str = None,
    organization_id: str = None,
    include_completed: bool = False,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """Get tasks with filters."""
    query = "SELECT * FROM email_tasks WHERE 1=1"
    params = []
    
    if status:
        query += " AND status = ?"
        params.append(status)
    elif not include_completed:
        query += " AND status NOT IN ('completed', 'cancelled')"
    
    if assignee_email:
        query += " AND assignee_email = ?"
        params.append(assignee_email)
    
    if task_type:
        query += " AND task_type = ?"
        params.append(task_type)
    
    if organization_id:
        query += " AND organization_id = ?"
        params.append(organization_id)
    
    query += " ORDER BY CASE priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, due_date ASC LIMIT ?"
    params.append(limit)
    
    rows = db.fetchall_dict(query, tuple(params))
    
    tasks = []
    for row in rows:
        task = dict(row)
        if task.get('tags'):
            task['tags'] = json.loads(task['tags'])
        tasks.append(task)
    
    return tasks


def get_tasks_for_email(email_id: str) -> List[Dict[str, Any]]:
    """Get all tasks created from an email."""
    return db.fetchall_dict("""
        SELECT * FROM email_tasks 
        WHERE source_email_id = ?
        ORDER BY created_at DESC
    """, (email_id,))


def get_overdue_tasks(organization_id: str = None) -> List[Dict[str, Any]]:
    """Get overdue tasks."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    query = """
        SELECT * FROM email_tasks 
        WHERE status NOT IN ('completed', 'cancelled')
        AND due_date < ?
    """
    params = [today]
    
    if organization_id:
        query += " AND organization_id = ?"
        params.append(organization_id)
    
    query += " ORDER BY due_date ASC"
    
    return db.fetchall_dict(query, tuple(params))


def _calculate_default_due_date(task_type: str) -> str:
    """Calculate default due date based on task type."""
    now = datetime.now(timezone.utc)
    
    # Different due dates for different task types
    days_map = {
        "collect_docs": 3,
        "chase_approver": 2,
        "reconcile_item": 2,
        "verify_payment": 1,
        "follow_up": 5,
        "close_task": 1
    }
    
    days = days_map.get(task_type, 3)
    due = now + timedelta(days=days)
    return due.strftime('%Y-%m-%d')


# Task types
class TaskTypes:
    COLLECT_DOCS = "collect_docs"
    CHASE_APPROVER = "chase_approver"
    RECONCILE_ITEM = "reconcile_item"
    VERIFY_PAYMENT = "verify_payment"
    FOLLOW_UP = "follow_up"
    CLOSE_TASK = "close_task"
    INVESTIGATE = "investigate"
    APPROVE = "approve"


# Status values
class TaskStatus:
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    PENDING_APPROVAL = "pending_approval"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# Initialize on import
init_tasks_db()
