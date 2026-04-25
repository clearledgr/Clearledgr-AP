"""Approval chain persistence mixin for ClearledgrDB.

Follows the exact VendorStore mixin pattern:
- No __init__ of its own
- Expects host class to provide self.connect()
- Two class-level SQL constants consumed by database.py:initialize()
- All methods prefix with db_ to avoid collision with ApprovalChainService methods
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ApprovalChainStore:
    """Mixin providing DB persistence for approval chains and steps."""

    APPROVAL_CHAINS_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS approval_chains (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            invoice_id TEXT NOT NULL,
            vendor_name TEXT,
            amount REAL,
            gl_code TEXT,
            department TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            current_step INTEGER NOT NULL DEFAULT 0,
            chain_type TEXT NOT NULL DEFAULT 'sequential',
            requester_id TEXT,
            requester_name TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            metadata TEXT DEFAULT '{}'
        )
    """

    APPROVAL_STEPS_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS approval_steps (
            id TEXT PRIMARY KEY,
            chain_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            level TEXT NOT NULL,
            approvers TEXT NOT NULL DEFAULT '[]',
            approval_type TEXT NOT NULL DEFAULT 'any',
            status TEXT NOT NULL DEFAULT 'pending',
            approved_by TEXT,
            approved_at TEXT,
            rejection_reason TEXT,
            comments TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """

    # ------------------------------------------------------------------
    # Chain CRUD
    # ------------------------------------------------------------------

    def db_create_approval_chain(self, chain: Any) -> None:
        """Persist a new ApprovalChain (and all its steps) to the DB."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        chain_sql = """
            INSERT INTO approval_chains
            (id, organization_id, invoice_id, vendor_name, amount, gl_code, department,
             status, current_step, requester_id, requester_name, created_at, completed_at, metadata, entity_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"""
        step_sql = """
            INSERT INTO approval_steps
            (id, chain_id, step_index, level, approvers, approval_type,
             status, approved_by, approved_at, rejection_reason, comments, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"""
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(chain_sql, (
                chain.chain_id,
                chain.organization_id,
                chain.invoice_id,
                chain.vendor_name,
                chain.amount,
                chain.gl_code,
                chain.department,
                chain.status.value if hasattr(chain.status, "value") else str(chain.status),
                chain.current_step,
                chain.requester_id,
                chain.requester_name,
                chain.created_at.isoformat() if hasattr(chain.created_at, "isoformat") else str(chain.created_at),
                chain.completed_at.isoformat() if chain.completed_at and hasattr(chain.completed_at, "isoformat") else chain.completed_at,
                "{}",
                getattr(chain, "entity_id", None),
            ))
            for idx, step in enumerate(chain.steps):
                step_id = getattr(step, "step_id", None) or f"step-{uuid.uuid4().hex}"
                cur.execute(step_sql, (
                    step_id,
                    chain.chain_id,
                    idx,
                    step.level.value if hasattr(step.level, "value") else str(step.level),
                    json.dumps(step.approvers),
                    step.approval_type.value if hasattr(step.approval_type, "value") else str(step.approval_type),
                    step.status.value if hasattr(step.status, "value") else str(step.status),
                    step.approved_by,
                    step.approved_at.isoformat() if step.approved_at and hasattr(step.approved_at, "isoformat") else step.approved_at,
                    step.rejection_reason,
                    step.comments or "",
                    now,
                    now,
                ))
            conn.commit()

    def db_get_approval_chain(self, chain_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a chain row + its step rows. Returns None if not found."""
        self.initialize()
        chain_sql = "SELECT * FROM approval_chains WHERE id = %s"
        steps_sql = "SELECT * FROM approval_steps WHERE chain_id = %s ORDER BY step_index ASC"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(chain_sql, (chain_id,))
            chain_row = cur.fetchone()
            if not chain_row:
                return None
            cur.execute(steps_sql, (chain_id,))
            step_rows = cur.fetchall()
        result = dict(chain_row)
        result["steps"] = [dict(r) for r in step_rows]
        return result

    def db_get_chain_by_invoice(self, organization_id: str, invoice_id: str) -> Optional[Dict[str, Any]]:
        """Find the most recent chain for an invoice in an org."""
        self.initialize()
        sql = (
            "SELECT id FROM approval_chains WHERE organization_id = %s AND invoice_id = %s "
            "ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, invoice_id))
            row = cur.fetchone()
        if not row:
            return None
        return self.db_get_approval_chain(row[0])

    def db_update_chain_step(
        self,
        chain_id: str,
        step_index: int,
        status: str,
        approved_by: Optional[str] = None,
        approved_at: Optional[str] = None,
        comments: str = "",
        rejection_reason: Optional[str] = None,
    ) -> None:
        """Update a single step's status and outcome fields."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = """
            UPDATE approval_steps
            SET status = %s, approved_by = %s, approved_at = %s,
                comments = %s, rejection_reason = %s, updated_at = %s
            WHERE chain_id = %s AND step_index = %s
        """
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (status, approved_by, approved_at, comments, rejection_reason, now, chain_id, step_index))
            conn.commit()

    def db_reassign_pending_step_approvers(
        self,
        chain_id: str,
        approvers: List[str],
        *,
        comments: str = "",
    ) -> bool:
        """Replace approvers on pending steps for the active approval chain."""
        self.initialize()
        normalized_approvers = [str(value).strip() for value in (approvers or []) if str(value).strip()]
        if not chain_id or not normalized_approvers:
            return False

        now = datetime.now(timezone.utc).isoformat()
        sql = """
            UPDATE approval_steps
            SET approvers = %s, comments = %s, updated_at = %s
            WHERE chain_id = %s AND status = 'pending'
        """
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    json.dumps(normalized_approvers),
                    str(comments or "").strip(),
                    now,
                    chain_id,
                ),
            )
            conn.commit()
            return bool(getattr(cur, "rowcount", 0))

    def db_update_chain_status(
        self,
        chain_id: str,
        status: str,
        current_step: int,
        completed_at: Optional[str] = None,
    ) -> None:
        """Update chain-level status and current_step pointer."""
        self.initialize()
        sql = """
            UPDATE approval_chains SET status = %s, current_step = %s, completed_at = %s
            WHERE id = %s
        """
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (status, current_step, completed_at, chain_id))
            conn.commit()

    def db_check_parallel_chain_complete(self, chain_id: str) -> Dict[str, Any]:
        """Check if all steps in a parallel approval chain are resolved.

        Returns {complete: bool, approved: bool, pending_count: int, approved_count: int, rejected: bool}
        """
        chain = self.db_get_approval_chain(chain_id)
        if not chain:
            return {"complete": False, "approved": False, "pending_count": 0, "approved_count": 0, "rejected": False}

        steps = chain.get("steps") or []
        pending = sum(1 for s in steps if s.get("status") == "pending")
        approved = sum(1 for s in steps if s.get("status") == "approved")
        rejected = any(s.get("status") == "rejected" for s in steps)

        # Parallel chain: complete when all steps resolved (no pending)
        # Or when any step is rejected (reject entire chain)
        complete = pending == 0 or rejected
        all_approved = approved == len(steps) and not rejected

        return {
            "complete": complete,
            "approved": all_approved,
            "rejected": rejected,
            "pending_count": pending,
            "approved_count": approved,
            "total_steps": len(steps),
        }

    def db_list_pending_chains_for_user(
        self, organization_id: str, user_id: str
    ) -> List[Dict[str, Any]]:
        """Return all PENDING chains where the user appears in a pending step's approvers."""
        self.initialize()
        # Fetch all pending chains for the org
        chains_sql = (
            "SELECT id FROM approval_chains WHERE organization_id = %s AND status = 'pending'"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(chains_sql, (organization_id,))
            chain_ids = [row[0] for row in cur.fetchall()]

        result = []
        for cid in chain_ids:
            chain_data = self.db_get_approval_chain(cid)
            if not chain_data:
                continue
            # Check if user_id appears in any pending step's approvers list
            for step in chain_data.get("steps", []):
                if step.get("status") != "pending":
                    continue
                try:
                    approvers = json.loads(step.get("approvers") or "[]")
                except Exception:
                    approvers = []
                if user_id in approvers:
                    result.append(chain_data)
                    break
        return result
