"""
Persistent shared state store for agent coordination.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from clearledgr.state.db import db


def init_shared_state_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_shared_state(
                organization_id TEXT NOT NULL,
                state_key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (organization_id, state_key)
            )
            """
        )


def set_shared_state(organization_id: str, key: str, value: Dict[str, Any]) -> None:
    init_shared_state_db()
    timestamp = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO agent_shared_state(organization_id, state_key, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(organization_id, state_key)
            DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (organization_id, key, json.dumps(value), timestamp),
        )


def get_shared_state(organization_id: str, key: Optional[str] = None) -> Dict[str, Any]:
    init_shared_state_db()
    with db() as conn:
        if key:
            row = conn.execute(
                "SELECT value FROM agent_shared_state WHERE organization_id = ? AND state_key = ?",
                (organization_id, key),
            ).fetchone()
            return json.loads(row["value"]) if row else {}

        rows = conn.execute(
            "SELECT state_key, value FROM agent_shared_state WHERE organization_id = ?",
            (organization_id,),
        ).fetchall()
        return {row["state_key"]: json.loads(row["value"]) for row in rows}
