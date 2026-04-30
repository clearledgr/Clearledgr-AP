"""DisputeStore mixin — CRUD for AP dispute/exception tracking."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Valid dispute statuses
DISPUTE_STATUSES = {"open", "vendor_contacted", "response_received", "resolved", "escalated", "closed"}

# Valid dispute types
DISPUTE_TYPES = {
    "missing_po", "wrong_amount", "vendor_mismatch", "missing_info",
    "duplicate", "bank_detail_change", "erp_sync_mismatch", "other",
}


class DisputeStore:
    """Mixin for dispute persistence."""

    def create_dispute(
        self,
        ap_item_id: str,
        organization_id: str,
        dispute_type: str,
        vendor_name: str = "",
        vendor_email: str = "",
        description: str = "",
        followup_thread_id: str = "",
    ) -> Dict[str, Any]:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        dispute_id = f"dsp_{uuid.uuid4().hex[:12]}"

        sql = """
            INSERT INTO disputes
            (id, ap_item_id, organization_id, dispute_type, status, vendor_name,
             vendor_email, description, followup_thread_id, opened_at, updated_at)
            VALUES (%s, %s, %s, %s, 'open', %s, %s, %s, %s, %s, %s)
        """
        params = (
            dispute_id, ap_item_id, organization_id, dispute_type,
            vendor_name, vendor_email, description, followup_thread_id,
            now, now,
        )

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

        return {
            "id": dispute_id,
            "ap_item_id": ap_item_id,
            "organization_id": organization_id,
            "dispute_type": dispute_type,
            "status": "open",
            "vendor_name": vendor_name,
            "description": description,
            "opened_at": now,
        }

    def get_dispute(self, dispute_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM disputes WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (dispute_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_disputes_for_item(self, ap_item_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM disputes WHERE ap_item_id = %s ORDER BY opened_at DESC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            return [dict(r) for r in cur.fetchall()]

    def list_disputes(
        self,
        organization_id: str,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        if status:
            sql = (
                "SELECT * FROM disputes WHERE organization_id = %s AND status = %s "
                "ORDER BY opened_at DESC LIMIT %s"
            )
            params = (organization_id, status, limit)
        else:
            sql = (
                "SELECT * FROM disputes WHERE organization_id = %s "
                "ORDER BY opened_at DESC LIMIT %s"
            )
            params = (organization_id, limit)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def update_dispute(self, dispute_id: str, **kwargs) -> bool:
        self.initialize()
        allowed = {
            "status", "description", "resolution", "vendor_contacted_at",
            "response_received_at", "resolved_at", "escalated_at",
            "followup_thread_id", "followup_count", "vendor_email",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        sql = (
            f"UPDATE disputes SET {set_clause} WHERE id = %s"
        )
        params = list(updates.values()) + [dispute_id]

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount > 0
