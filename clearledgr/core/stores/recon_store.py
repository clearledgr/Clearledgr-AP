"""Reconciliation Store mixin — DB persistence for reconciliation items.

Added to ClearledgrDB via mixin inheritance, following the same pattern
as APStore, VendorStore, TaskStore, etc.
"""
from __future__ import annotations

import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ReconStore:
    """Mixin providing DB persistence for reconciliation sessions and items."""

    RECON_TABLES_SQL = [
        """CREATE TABLE IF NOT EXISTS recon_sessions (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'created',
            source_type TEXT NOT NULL DEFAULT 'google_sheets',
            spreadsheet_id TEXT,
            sheet_range TEXT,
            total_rows INTEGER DEFAULT 0,
            matched_count INTEGER DEFAULT 0,
            exception_count INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS recon_items (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'imported',
            row_index INTEGER,
            transaction_date TEXT,
            description TEXT,
            amount REAL,
            reference TEXT,
            matched_ap_item_id TEXT,
            match_confidence REAL,
            exception_reason TEXT,
            resolution TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES recon_sessions(id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_recon_items_session ON recon_items(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_recon_items_state ON recon_items(state)",
        "CREATE INDEX IF NOT EXISTS idx_recon_sessions_org ON recon_sessions(organization_id)",
    ]

    def create_recon_session(
        self,
        organization_id: str,
        source_type: str = "google_sheets",
        spreadsheet_id: Optional[str] = None,
        sheet_range: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new reconciliation session."""
        now = datetime.now(timezone.utc).isoformat()
        session_id = f"RECON-{uuid.uuid4().hex[:24]}"
        with self.connect() as conn:
            conn.execute(
                self._prepare_sql(
                    """INSERT INTO recon_sessions
                       (id, organization_id, state, source_type, spreadsheet_id, sheet_range, created_at, updated_at)
                       VALUES (?, ?, 'created', ?, ?, ?, ?, ?)"""
                ),
                (session_id, organization_id, source_type, spreadsheet_id, sheet_range, now, now),
            )
            conn.commit()
        return {"id": session_id, "organization_id": organization_id, "state": "created"}

    def create_recon_item(
        self,
        session_id: str,
        organization_id: str,
        row_index: int,
        transaction_date: Optional[str] = None,
        description: Optional[str] = None,
        amount: Optional[float] = None,
        reference: Optional[str] = None,
    ) -> str:
        """Create a reconciliation item (one imported transaction row)."""
        now = datetime.now(timezone.utc).isoformat()
        item_id = f"RI-{uuid.uuid4().hex[:20]}"
        with self.connect() as conn:
            conn.execute(
                self._prepare_sql(
                    """INSERT INTO recon_items
                       (id, session_id, organization_id, state, row_index,
                        transaction_date, description, amount, reference, created_at, updated_at)
                       VALUES (?, ?, ?, 'imported', ?, ?, ?, ?, ?, ?, ?)"""
                ),
                (item_id, session_id, organization_id, row_index,
                 transaction_date, description, amount, reference, now, now),
            )
            conn.commit()
        return item_id

    def update_recon_item(self, item_id: str, **kwargs) -> bool:
        """Update a reconciliation item (state, match, exception, etc.)."""
        allowed = {"state", "matched_ap_item_id", "match_confidence",
                    "exception_reason", "resolution", "metadata"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        with self.connect() as conn:
            cursor = conn.execute(
                self._prepare_sql(
                    f"UPDATE recon_items SET {set_clause} WHERE id = ?"
                ),
                (*updates.values(), item_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def get_recon_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get a reconciliation session by ID."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM recon_sessions WHERE id = %s",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_recon_items(self, session_id: str, state: Optional[str] = None) -> List[Dict[str, Any]]:
        """List reconciliation items for a session, optionally filtered by state."""
        with self.connect() as conn:
            if state:
                rows = conn.execute(
                    self._prepare_sql(
                        "SELECT * FROM recon_items WHERE session_id = ? AND state = ? ORDER BY row_index"
                    ),
                    (session_id, state),
                ).fetchall()
            else:
                rows = conn.execute(
                    self._prepare_sql(
                        "SELECT * FROM recon_items WHERE session_id = ? ORDER BY row_index"
                    ),
                    (session_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def update_recon_session_counts(self, session_id: str) -> None:
        """Recalculate matched/exception counts for a session."""
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                self._prepare_sql(
                    """UPDATE recon_sessions SET
                        total_rows = (SELECT COUNT(*) FROM recon_items WHERE session_id = ?),
                        matched_count = (SELECT COUNT(*) FROM recon_items WHERE session_id = ? AND state IN ('matched', 'resolved', 'posted')),
                        exception_count = (SELECT COUNT(*) FROM recon_items WHERE session_id = ? AND state IN ('exception', 'review')),
                        updated_at = ?
                       WHERE id = ?"""
                ),
                (session_id, session_id, session_id, now, session_id),
            )
            conn.commit()
