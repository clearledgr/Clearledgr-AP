"""Durable task run persistence mixin for ClearledgrDB.

Follows the exact ApprovalChainStore mixin pattern:
- No __init__ of its own
- Expects host class to provide self.connect()
- One class-level SQL constant consumed by database.py:initialize()
- All write operations are atomic (checkpoint before + after each tool call)

Purpose: Step-level checkpointing for the FinanceAgentRuntime planning loop.
If the server crashes mid-workflow, resume_pending_tasks() picks up interrupted
runs from where they left off.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    """Mixin providing DB persistence for agent task runs."""

    TASK_RUNS_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS task_runs (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            current_step INTEGER NOT NULL DEFAULT 0,
            input_payload TEXT NOT NULL DEFAULT '{}',
            step_results TEXT NOT NULL DEFAULT '{}',
            idempotency_key TEXT UNIQUE,
            correlation_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            last_error TEXT,
            retry_count INTEGER DEFAULT 0
        )
    """

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_task_run(
        self,
        id: str,
        org_id: str,
        task_type: str,
        input_payload: str = "{}",
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new task run row, or return the existing row if idempotency_key matches."""
        self.initialize()
        now = _now()

        # Idempotency: return existing row if key already seen
        if idempotency_key:
            existing = self.get_task_run_by_idempotency_key(idempotency_key)
            if existing:
                if str(existing.get("status") or "").strip().lower() == "failed":
                    reset_sql = (
                        "UPDATE task_runs SET status = 'pending', current_step = 0, input_payload = %s, "
                        "step_results = '{}', correlation_id = %s, updated_at = %s, completed_at = NULL, "
                        "last_error = NULL, retry_count = COALESCE(retry_count, 0) + 1 WHERE id = %s"
                    )
                    try:
                        with self.connect() as conn:
                            cur = conn.cursor()
                            cur.execute(
                                reset_sql,
                                (input_payload, correlation_id, now, existing["id"]),
                            )
                            conn.commit()
                    except Exception as exc:
                        logger.warning("[TaskStore] reset failed task_run failed: %s", exc)
                        raise
                    existing = self.get_task_run(existing["id"]) or existing
                return existing

        sql = (
            "INSERT INTO task_runs "
            "(id, organization_id, task_type, status, current_step, input_payload, "
            " step_results, idempotency_key, correlation_id, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'pending', 0, %s, '{}', %s, %s, %s, %s)"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (
                    id, org_id, task_type, input_payload,
                    idempotency_key, correlation_id, now, now,
                ))
                conn.commit()
        except Exception as exc:
            logger.warning("[TaskStore] create_task_run failed: %s", exc)
            raise

        return self.get_task_run(id) or {
            "id": id,
            "organization_id": org_id,
            "task_type": task_type,
            "status": "pending",
            "current_step": 0,
            "input_payload": input_payload,
            "step_results": "{}",
            "idempotency_key": idempotency_key,
            "correlation_id": correlation_id,
            "created_at": now,
            "updated_at": now,
        }

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_task_run(self, task_run_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a task run by primary key."""
        self.initialize()
        sql = "SELECT * FROM task_runs WHERE id = %s"
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (task_run_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.warning("[TaskStore] get_task_run failed: %s", exc)
            return None

    def get_task_run_by_idempotency_key(self, key: str) -> Optional[Dict[str, Any]]:
        """Fetch a task run by idempotency key."""
        self.initialize()
        sql = "SELECT * FROM task_runs WHERE idempotency_key = %s"
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (key,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.warning("[TaskStore] get_task_run_by_idempotency_key failed: %s", exc)
            return None

    def list_pending_task_runs(
        self,
        organization_id: Optional[str] = None,
        statuses: tuple = ("pending", "running"),
    ) -> List[Dict[str, Any]]:
        """List task runs by status (used by resume_pending_tasks on startup)."""
        self.initialize()
        placeholders = ",".join(["%s"] * len(statuses))
        if organization_id:
            sql = (
                f"SELECT * FROM task_runs WHERE organization_id = %s AND status IN ({placeholders}) "
                "ORDER BY created_at ASC"
            )
            params = (organization_id, *statuses)
        else:
            sql = (
                f"SELECT * FROM task_runs WHERE status IN ({placeholders}) ORDER BY created_at ASC"
            )
            params = statuses
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("[TaskStore] list_pending_task_runs failed: %s", exc)
            return []

    def list_task_runs(
        self,
        organization_id: Optional[str] = None,
        *,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List recent task runs for memory/audit projection."""
        self.initialize()
        if organization_id:
            sql = (
                "SELECT * FROM task_runs WHERE organization_id = %s ORDER BY created_at DESC LIMIT %s"
            )
            params = (organization_id, max(1, int(limit or 50)))
        else:
            sql = (
                "SELECT * FROM task_runs ORDER BY created_at DESC LIMIT %s"
            )
            params = (max(1, int(limit or 50)),)
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                rows = cur.fetchall() or []
                return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("[TaskStore] list_task_runs failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Update (step checkpoint)
    # ------------------------------------------------------------------

    def update_task_run_step(
        self,
        task_run_id: str,
        step_index: int,
        tool_name: str,
        input_args: Dict[str, Any],
        output: Dict[str, Any],
        status: str = "running",
    ) -> None:
        """Atomically checkpoint one tool call step.

        Merges the step data into the step_results JSON column and advances
        current_step to step_index.
        """
        self.initialize()
        now = _now()

        # Read current step_results to merge
        existing = self.get_task_run(task_run_id) or {}
        step_results = {}
        try:
            step_results = json.loads(existing.get("step_results") or "{}")
        except Exception:
            pass

        step_results[str(step_index)] = {
            "tool": tool_name,
            "input": input_args,
            "output": output,
            "at": now,
        }

        sql = (
            "UPDATE task_runs SET current_step = %s, step_results = %s, status = %s, updated_at = %s "
            "WHERE id = %s"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (step_index, json.dumps(step_results), status, now, task_run_id))
                conn.commit()
        except Exception as exc:
            logger.warning("[TaskStore] update_task_run_step failed: %s", exc)

    # ------------------------------------------------------------------
    # Complete / Fail
    # ------------------------------------------------------------------

    def complete_task_run(
        self,
        task_run_id: str,
        outcome: Dict[str, Any],
        status: str = "completed",
    ) -> None:
        """Mark a task run as completed (or awaiting_human / max_steps_exceeded)."""
        self.initialize()
        now = _now()

        existing = self.get_task_run(task_run_id) or {}
        step_results = {}
        try:
            step_results = json.loads(existing.get("step_results") or "{}")
        except Exception:
            pass
        step_results["final"] = outcome

        sql = (
            "UPDATE task_runs SET status = %s, step_results = %s, completed_at = %s, updated_at = %s "
            "WHERE id = %s"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (status, json.dumps(step_results), now, now, task_run_id))
                conn.commit()
        except Exception as exc:
            logger.warning("[TaskStore] complete_task_run failed: %s", exc)

    def fail_task_run(
        self,
        task_run_id: str,
        error: str,
        retry_count: int = 0,
    ) -> None:
        """Mark a task run as failed."""
        self.initialize()
        now = _now()
        sql = (
            "UPDATE task_runs SET status = 'failed', last_error = %s, retry_count = %s, updated_at = %s "
            "WHERE id = %s"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (error, retry_count, now, task_run_id))
                conn.commit()
        except Exception as exc:
            logger.warning("[TaskStore] fail_task_run failed: %s", exc)
