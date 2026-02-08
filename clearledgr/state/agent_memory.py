"""
Agent Memory System for Clearledgr Reconciliation v1

Stores and retrieves agent learning, preferences, and adaptation data.

Moved to the shared DB helper so Postgres is used when configured, while
still working in local SQLite.
"""
import json
import os
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from clearledgr.services.db import DB


STATE_DB_PATH = os.getenv("STATE_DB_PATH", "state.sqlite3")
db = DB(sqlite_path=STATE_DB_PATH)


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_agent_memory_db():
    """Initialize agent memory database tables."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_schedules(
            id TEXT PRIMARY KEY,
            tool_type TEXT,
            tool_id TEXT,
            schedule_type TEXT,
            schedule_config TEXT,
            is_active INTEGER DEFAULT 1,
            last_run_date TEXT,
            created_at TEXT,
            updated_at TEXT
        )"""
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_memory(
            id TEXT PRIMARY KEY,
            organization_id TEXT,
            tool_type TEXT,
            memory_type TEXT,
            key TEXT,
            value TEXT,
            confidence REAL DEFAULT 0.5,
            usage_count INTEGER DEFAULT 0,
            success_rate REAL DEFAULT 0.0,
            created_at TEXT,
            updated_at TEXT
        )"""
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_feedback(
            id TEXT PRIMARY KEY,
            run_id TEXT,
            feedback_type TEXT,
            original_result TEXT,
            corrected_result TEXT,
            user_notes TEXT,
            created_at TEXT
        )"""
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_events(
            id TEXT PRIMARY KEY,
            event_type TEXT,
            source_agent TEXT,
            source_id TEXT,
            organization_id TEXT,
            payload TEXT,
            created_at TEXT
        )"""
    )

    # Indexes
    db.execute("CREATE INDEX IF NOT EXISTS idx_agent_schedules_tool ON agent_schedules(tool_type, tool_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_agent_memory_org ON agent_memory(organization_id, tool_type)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_agent_feedback_run ON agent_feedback(run_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_agent_events_org ON agent_events(organization_id, created_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_agent_events_type ON agent_events(event_type, created_at DESC)")


def save_agent_schedule(
    schedule_id: str,
    tool_type: str,
    tool_id: str,
    schedule_type: str,
    schedule_config: Dict,
    is_active: bool = True
) -> str:
    """Save or update an agent schedule."""
    existing = db.fetchone("SELECT id FROM agent_schedules WHERE id = ?", (schedule_id,))
    if existing:
        db.execute(
            """
            UPDATE agent_schedules
            SET schedule_type = ?, schedule_config = ?, is_active = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                schedule_type,
                json.dumps(schedule_config),
                1 if is_active else 0,
                _now(),
                schedule_id,
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO agent_schedules(
                id, tool_type, tool_id, schedule_type, schedule_config, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                schedule_id,
                tool_type,
                tool_id,
                schedule_type,
                json.dumps(schedule_config),
                1 if is_active else 0,
                _now(),
                _now(),
            ),
        )

    return schedule_id


def get_agent_schedules(tool_type: str, tool_id: str) -> List[Dict]:
    """Get all schedules for a tool."""
    rows = db.fetchall_dict(
        """
        SELECT id, schedule_type, schedule_config, is_active, last_run_date, created_at, updated_at
        FROM agent_schedules
        WHERE tool_type = ? AND tool_id = ?
        ORDER BY created_at DESC
        """,
        (tool_type, tool_id),
    )

    return [
        {
            "id": row["id"],
            "schedule_type": row["schedule_type"],
            "schedule_config": json.loads(row["schedule_config"] or "{}"),
            "is_active": bool(row.get("is_active", 0)),
            "last_run_date": row.get("last_run_date"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        for row in rows
    ]


def list_agent_schedules() -> List[Dict]:
    """List all schedules across tools."""
    rows = db.fetchall_dict(
        """
        SELECT id, tool_type, tool_id, schedule_type, schedule_config, is_active,
               last_run_date, created_at, updated_at
        FROM agent_schedules
        ORDER BY created_at DESC
        """
    )

    return [
        {
            "id": row["id"],
            "tool_type": row["tool_type"],
            "tool_id": row["tool_id"],
            "schedule_type": row["schedule_type"],
            "schedule_config": json.loads(row["schedule_config"] or "{}"),
            "is_active": bool(row.get("is_active", 0)),
            "last_run_date": row.get("last_run_date"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        for row in rows
    ]


def update_schedule_last_run(schedule_id: str, run_date: str):
    """Update last run date for a schedule."""
    db.execute(
        """
        UPDATE agent_schedules
        SET last_run_date = ?, updated_at = ?
        WHERE id = ?
        """,
        (run_date, _now(), schedule_id),
    )


def save_agent_memory(
    memory_id: str,
    organization_id: str,
    tool_type: str,
    memory_type: str,
    key: str,
    value: Any,
    confidence: float = 0.5
) -> str:
    """Save or update agent memory."""
    existing = db.fetchone_dict(
        "SELECT id, usage_count, success_rate FROM agent_memory WHERE id = ?",
        (memory_id,),
    )

    if existing:
        usage_count = (existing.get("usage_count") or 0) + 1
        db.execute(
            """
            UPDATE agent_memory
            SET value = ?, confidence = ?, usage_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(value),
                confidence,
                usage_count,
                _now(),
                memory_id,
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO agent_memory(
                id, organization_id, tool_type, memory_type, key, value, confidence, usage_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                organization_id,
                tool_type,
                memory_type,
                key,
                json.dumps(value),
                confidence,
                1,
                _now(),
                _now(),
            ),
        )

    return memory_id


def get_agent_memory(
    organization_id: str,
    tool_type: Optional[str] = None,
    memory_type: Optional[str] = None,
    key: Optional[str] = None
) -> List[Dict]:
    """Get agent memory matching criteria."""
    query = "SELECT * FROM agent_memory WHERE organization_id = ?"
    params: List[Any] = [organization_id]

    if tool_type:
        query += " AND tool_type = ?"
        params.append(tool_type)

    if memory_type:
        query += " AND memory_type = ?"
        params.append(memory_type)

    if key:
        query += " AND key = ?"
        params.append(key)

    query += " ORDER BY confidence DESC, usage_count DESC"
    rows = db.fetchall_dict(query, tuple(params))

    return [
        {
            "id": row["id"],
            "organization_id": row["organization_id"],
            "tool_type": row["tool_type"],
            "memory_type": row["memory_type"],
            "key": row["key"],
            "value": json.loads(row["value"] or "{}"),
            "confidence": row.get("confidence", 0.0),
            "usage_count": row.get("usage_count", 0),
            "success_rate": row.get("success_rate", 0.0),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        for row in rows
    ]


def save_agent_feedback(
    feedback_id: str,
    run_id: str,
    feedback_type: str,
    original_result: Dict,
    corrected_result: Optional[Dict] = None,
    user_notes: Optional[str] = None
) -> str:
    """Save user feedback for learning."""
    db.execute(
        """
        INSERT INTO agent_feedback(
            id, run_id, feedback_type, original_result, corrected_result, user_notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feedback_id,
            run_id,
            feedback_type,
            json.dumps(original_result),
            json.dumps(corrected_result) if corrected_result else None,
            user_notes,
            _now(),
        ),
    )

    return feedback_id


def record_agent_event(
    event_id: str,
    event_type: str,
    source_agent: str,
    source_id: str,
    organization_id: str,
    payload: Dict
) -> str:
    """Record an agent event for coordination."""
    db.execute(
        """
        INSERT INTO agent_events(
            id, event_type, source_agent, source_id, organization_id, payload, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            source_agent,
            source_id,
            organization_id,
            json.dumps(payload),
            _now(),
        ),
    )

    return event_id


def get_recent_agent_events(
    organization_id: str,
    event_type: Optional[str] = None,
    limit: int = 50
) -> List[Dict]:
    """Get recent agent events."""
    query = "SELECT * FROM agent_events WHERE organization_id = ?"
    params: List[Any] = [organization_id]

    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = db.fetchall_dict(query, tuple(params))

    return [
        {
            "id": row["id"],
            "event_type": row["event_type"],
            "source_agent": row["source_agent"],
            "source_id": row["source_id"],
            "organization_id": row["organization_id"],
            "payload": json.loads(row["payload"]) if row.get("payload") else None,
            "created_at": row.get("created_at"),
        }
        for row in rows
    ]
