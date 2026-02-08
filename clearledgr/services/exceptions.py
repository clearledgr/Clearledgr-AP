"""Lightweight exception store for reconciliation exceptions."""
from __future__ import annotations

from typing import List, Optional, Dict, Any
import os
from clearledgr.services.db import DB


DB_PATH = os.getenv("CLEARLEDGR_STATE_DB", os.path.join(os.getcwd(), "state.sqlite3"))


class ExceptionStore:
    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db = DB(sqlite_path=db_path)
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS exceptions (
                exception_id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'Pending',
                priority TEXT DEFAULT 'Medium',
                amount REAL,
                reason TEXT,
                description TEXT,
                source TEXT,
                metadata TEXT
            )
            """
        )

    def upsert_exceptions(self, items: List[Dict[str, Any]]) -> None:
        if not items:
            return
        for exc in items:
            exc_id = exc.get("exception_id") or exc.get("tx_id") or exc.get("id")
            if not exc_id:
                continue
            self.db.execute(
                """
                INSERT INTO exceptions (exception_id, status, priority, amount, reason, description, source, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exception_id) DO UPDATE SET
                    status=excluded.status,
                    priority=excluded.priority,
                    amount=excluded.amount,
                    reason=excluded.reason,
                    description=excluded.description,
                    source=excluded.source,
                    metadata=excluded.metadata
                """,
                (
                    exc_id,
                    exc.get("status") or "Pending",
                    exc.get("priority") or "Medium",
                    exc.get("amount") if exc.get("amount") is not None else None,
                    exc.get("reason") or exc.get("description") or "",
                    exc.get("description") or "",
                    exc.get("source") or "",
                    None,
                ),
            )

    def list_exceptions(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.db.fetchall_dict(
            """
            SELECT exception_id, status, priority, amount, reason, description, source
            FROM exceptions
            ORDER BY CASE priority WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Medium' THEN 3 ELSE 4 END,
                     exception_id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return rows

    def resolve_exception(self, exception_id: str, status: str = "Resolved") -> bool:
        self.db.execute(
            """
            UPDATE exceptions SET status = ? WHERE exception_id = ?
            """,
            (status, exception_id),
        )
        return True
