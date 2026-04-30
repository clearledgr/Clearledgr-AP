"""Dispute service — structured dispute lifecycle management.

Wraps the DisputeStore with business logic for opening, updating,
resolving, and escalating disputes linked to AP items.

Integrates with:
- AP item state machine (needs_info state)
- Vendor communication (follow-up emails)
- Audit trail (dispute events)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class DisputeService:
    """Manage disputes for a single tenant."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    def open_dispute(
        self,
        ap_item_id: str,
        dispute_type: str,
        description: str = "",
        vendor_name: str = "",
        vendor_email: str = "",
    ) -> Dict[str, Any]:
        """Open a new dispute for an AP item."""
        # Auto-fill vendor info from AP item if not provided
        if not vendor_name or not vendor_email:
            item = self.db.get_ap_item(ap_item_id)
            if item:
                vendor_name = vendor_name or item.get("vendor_name", "")

        dispute = self.db.create_dispute(
            ap_item_id=ap_item_id,
            organization_id=self.organization_id,
            dispute_type=dispute_type,
            vendor_name=vendor_name,
            vendor_email=vendor_email,
            description=description,
        )

        logger.info(
            "[Dispute] Opened %s for ap_item=%s type=%s",
            dispute["id"], ap_item_id, dispute_type,
        )
        return dispute

    def mark_vendor_contacted(
        self,
        dispute_id: str,
        followup_thread_id: str = "",
    ) -> bool:
        """Mark that the vendor has been contacted about this dispute."""
        now = datetime.now(timezone.utc).isoformat()
        dispute = self.db.get_dispute(dispute_id)
        if not dispute:
            return False

        count = (dispute.get("followup_count") or 0) + 1
        return self.db.update_dispute(
            dispute_id,
            status="vendor_contacted",
            vendor_contacted_at=now,
            followup_thread_id=followup_thread_id,
            followup_count=count,
        )

    def mark_response_received(
        self, dispute_id: str,
    ) -> bool:
        """Mark that a vendor response was received."""
        now = datetime.now(timezone.utc).isoformat()
        return self.db.update_dispute(
            dispute_id,
            status="response_received",
            response_received_at=now,
        )

    def resolve_dispute(
        self,
        dispute_id: str,
        resolution: str,
    ) -> bool:
        """Resolve a dispute with a resolution description."""
        now = datetime.now(timezone.utc).isoformat()
        return self.db.update_dispute(
            dispute_id,
            status="resolved",
            resolution=resolution,
            resolved_at=now,
        )

    def escalate_dispute(
        self, dispute_id: str,
    ) -> bool:
        """Escalate a dispute (vendor unresponsive, needs manager attention)."""
        now = datetime.now(timezone.utc).isoformat()
        return self.db.update_dispute(
            dispute_id,
            status="escalated",
            escalated_at=now,
        )

    def close_dispute(
        self, dispute_id: str, resolution: str = "closed",
    ) -> bool:
        """Close a dispute without resolution (e.g., duplicate, no longer relevant)."""
        now = datetime.now(timezone.utc).isoformat()
        return self.db.update_dispute(
            dispute_id,
            status="closed",
            resolution=resolution,
            resolved_at=now,
        )

    def list_open(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List open/active disputes (not resolved or closed)."""
        all_disputes = self.db.list_disputes(self.organization_id, limit=limit)
        return [
            d for d in all_disputes
            if d.get("status") not in ("resolved", "closed")
        ]

    def get_dispute_summary(self) -> Dict[str, Any]:
        """Summary stats for the org's disputes."""
        all_disputes = self.db.list_disputes(self.organization_id, limit=10000)
        by_status: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        for d in all_disputes:
            status = d.get("status", "unknown")
            dtype = d.get("dispute_type", "unknown")
            by_status[status] = by_status.get(status, 0) + 1
            by_type[dtype] = by_type.get(dtype, 0) + 1

        return {
            "organization_id": self.organization_id,
            "total": len(all_disputes),
            "by_status": by_status,
            "by_type": by_type,
            "open_count": sum(
                v for k, v in by_status.items() if k not in ("resolved", "closed")
            ),
        }


def get_dispute_service(organization_id: str = "default") -> DisputeService:
    return DisputeService(organization_id=organization_id)
