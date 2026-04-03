"""Payment tracking data-access mixin for ClearledgrDB.

``PaymentStore`` is a **mixin class** — it has no ``__init__`` of its own and
expects the concrete class that inherits it to provide the standard DB
infrastructure (``connect()``, ``_prepare_sql()``, ``initialize()``).

The ``payments`` table is purely informational.  Clearledgr NEVER executes
payments — it tracks readiness and status.  Humans trigger payments in the ERP.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


PAYMENT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    ap_item_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    vendor_name TEXT,
    amount REAL,
    currency TEXT DEFAULT 'USD',
    status TEXT DEFAULT 'ready_for_payment',
    payment_method TEXT,
    payment_reference TEXT,
    due_date TEXT,
    scheduled_date TEXT,
    completed_date TEXT,
    erp_reference TEXT,
    notes TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""


class PaymentStore:
    """Mixin providing payment tracking persistence methods."""

    PAYMENT_TABLE_SQL = PAYMENT_TABLE_SQL

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_payment(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a payment tracking record.

        Returns the inserted row as a dict.
        """
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        payment_id = payload.get("id") or f"PAY-{uuid.uuid4().hex[:12]}"

        sql = self._prepare_sql("""
            INSERT INTO payments
            (id, ap_item_id, organization_id, vendor_name, amount, currency,
             status, payment_method, payment_reference, due_date, scheduled_date,
             completed_date, erp_reference, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        values = (
            payment_id,
            payload.get("ap_item_id"),
            payload.get("organization_id", "default"),
            payload.get("vendor_name"),
            payload.get("amount"),
            payload.get("currency", "USD"),
            payload.get("status", "ready_for_payment"),
            payload.get("payment_method"),
            payload.get("payment_reference"),
            payload.get("due_date"),
            payload.get("scheduled_date"),
            payload.get("completed_date"),
            payload.get("erp_reference"),
            payload.get("notes"),
            now,
            now,
        )
        with self.connect() as conn:
            conn.execute(sql, values)
            conn.commit()

        return {
            "id": payment_id,
            "ap_item_id": payload.get("ap_item_id"),
            "organization_id": payload.get("organization_id", "default"),
            "vendor_name": payload.get("vendor_name"),
            "amount": payload.get("amount"),
            "currency": payload.get("currency", "USD"),
            "status": payload.get("status", "ready_for_payment"),
            "payment_method": payload.get("payment_method"),
            "payment_reference": payload.get("payment_reference"),
            "due_date": payload.get("due_date"),
            "scheduled_date": payload.get("scheduled_date"),
            "completed_date": payload.get("completed_date"),
            "erp_reference": payload.get("erp_reference"),
            "notes": payload.get("notes"),
            "created_at": now,
            "updated_at": now,
        }

    def get_payment(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single payment record by ID."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM payments WHERE id = ?")
        with self.connect() as conn:
            cur = conn.execute(sql, (payment_id,))
            row = cur.fetchone()
        if not row:
            return None
        return dict(row) if hasattr(row, "keys") else self._payment_row_to_dict(row, cur.description)

    def get_payment_by_ap_item(self, ap_item_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the payment record linked to an AP item."""
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM payments WHERE ap_item_id = ? ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        if not row:
            return None
        return dict(row) if hasattr(row, "keys") else self._payment_row_to_dict(row, cur.description)

    _PAYMENT_ALLOWED_COLUMNS = frozenset({
        "status", "payment_method", "payment_reference", "due_date",
        "scheduled_date", "completed_date", "erp_reference", "notes",
        "updated_at",
    })

    def update_payment(self, payment_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Update a payment record.  Only whitelisted columns are accepted."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        kwargs["updated_at"] = now

        safe_cols = {k: v for k, v in kwargs.items() if k in self._PAYMENT_ALLOWED_COLUMNS}
        if not safe_cols:
            return self.get_payment(payment_id)

        set_clause = ", ".join(f"{col} = ?" for col in safe_cols)
        values = list(safe_cols.values()) + [payment_id]
        sql = self._prepare_sql(f"UPDATE payments SET {set_clause} WHERE id = ?")
        with self.connect() as conn:
            conn.execute(sql, values)
            conn.commit()
        return self.get_payment(payment_id)

    def list_payments_by_org(
        self,
        organization_id: str,
        *,
        status: Optional[str] = None,
        vendor: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List payment records for an organization with optional filters."""
        self.initialize()
        clauses = ["organization_id = ?"]
        params: list = [organization_id]

        if status:
            clauses.append("status = ?")
            params.append(status)
        if vendor:
            clauses.append("vendor_name = ?")
            params.append(vendor)

        where = " AND ".join(clauses)
        sql = self._prepare_sql(
            f"SELECT * FROM payments WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        with self.connect() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
        return [
            dict(r) if hasattr(r, "keys") else self._payment_row_to_dict(r, cur.description)
            for r in rows
        ]

    def list_payments_by_status(
        self,
        organization_id: str,
        status: str,
        *,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List payment records for an org filtered by status."""
        return self.list_payments_by_org(organization_id, status=status, limit=limit)

    def get_payment_summary(self, organization_id: str) -> Dict[str, int]:
        """Return counts grouped by status for an organization."""
        self.initialize()
        sql = self._prepare_sql(
            "SELECT status, COUNT(*) as cnt FROM payments "
            "WHERE organization_id = ? GROUP BY status"
        )
        with self.connect() as conn:
            cur = conn.execute(sql, (organization_id,))
            rows = cur.fetchall()
        summary: Dict[str, int] = {}
        for row in rows:
            if hasattr(row, "keys"):
                summary[row["status"]] = row["cnt"]
            else:
                summary[row[0]] = row[1]
        return summary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _payment_row_to_dict(row, description) -> Dict[str, Any]:
        """Convert a positional row tuple to dict using cursor description."""
        if not description:
            return {}
        cols = [d[0] for d in description]
        return dict(zip(cols, row))
