"""Approval delegation service — OOO fallback and auto-reassignment.

When an approver is unavailable (OOO flag or SLA timeout), pending
approvals are automatically reassigned to their configured delegate.

Delegation rules are per-org, per-user, with optional date range
(starts_at/ends_at) for scheduled OOO periods.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DelegationService:
    """Manage approval delegation rules and auto-reassignment."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    # ------------------------------------------------------------------
    # Rule CRUD
    # ------------------------------------------------------------------

    def create_rule(
        self,
        delegator_id: str,
        delegator_email: str,
        delegate_id: str,
        delegate_email: str,
        reason: str = "",
        starts_at: Optional[str] = None,
        ends_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a delegation rule (approver A → delegate B)."""
        self.db.initialize()
        now = datetime.now(timezone.utc).isoformat()
        rule_id = f"dlg_{uuid.uuid4().hex[:12]}"

        sql = self.db._prepare_sql("""
            INSERT INTO delegation_rules
            (id, organization_id, delegator_id, delegator_email, delegate_id,
             delegate_email, is_active, reason, starts_at, ends_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                organization_id = EXCLUDED.organization_id,
                delegator_id = EXCLUDED.delegator_id,
                delegator_email = EXCLUDED.delegator_email,
                delegate_id = EXCLUDED.delegate_id,
                delegate_email = EXCLUDED.delegate_email,
                is_active = EXCLUDED.is_active,
                reason = EXCLUDED.reason,
                starts_at = EXCLUDED.starts_at,
                ends_at = EXCLUDED.ends_at,
                updated_at = EXCLUDED.updated_at
        """)
        params = (
            rule_id, self.organization_id, delegator_id, delegator_email,
            delegate_id, delegate_email, reason, starts_at, ends_at, now, now,
        )

        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

        logger.info(
            "[Delegation] Created rule: %s → %s for org=%s",
            delegator_email, delegate_email, self.organization_id,
        )

        return {
            "id": rule_id,
            "organization_id": self.organization_id,
            "delegator_email": delegator_email,
            "delegate_email": delegate_email,
            "is_active": True,
            "reason": reason,
            "starts_at": starts_at,
            "ends_at": ends_at,
        }

    def deactivate_rule(self, rule_id: str) -> bool:
        """Deactivate a delegation rule (approver returns from OOO)."""
        self.db.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = self.db._prepare_sql(
            "UPDATE delegation_rules SET is_active = 0, updated_at = ? WHERE id = ? AND organization_id = ?"
        )
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, rule_id, self.organization_id))
            conn.commit()
            return cur.rowcount > 0

    def list_rules(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """List delegation rules for this org."""
        self.db.initialize()
        if active_only:
            sql = self.db._prepare_sql(
                "SELECT * FROM delegation_rules WHERE organization_id = ? AND is_active = 1 ORDER BY created_at DESC"
            )
        else:
            sql = self.db._prepare_sql(
                "SELECT * FROM delegation_rules WHERE organization_id = ? ORDER BY created_at DESC"
            )

        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (self.organization_id,))
            rows = [dict(r) for r in cur.fetchall()]

        for row in rows:
            row["is_active"] = bool(row.get("is_active"))
        return rows

    def get_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        self.db.initialize()
        sql = self.db._prepare_sql("SELECT * FROM delegation_rules WHERE id = ?")
        with self.db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (rule_id,))
            row = cur.fetchone()
        if not row:
            return None
        result = dict(row)
        result["is_active"] = bool(result.get("is_active"))
        return result

    # ------------------------------------------------------------------
    # Delegate resolution
    # ------------------------------------------------------------------

    def get_delegate_for(self, approver_email: str) -> Optional[str]:
        """Find the active delegate for an approver, if any.

        Checks date range (starts_at/ends_at) if configured.
        Returns delegate email or None.
        """
        rules = self.list_rules(active_only=True)
        now = datetime.now(timezone.utc)

        for rule in rules:
            if rule["delegator_email"] != approver_email:
                continue

            # Check date range
            starts = rule.get("starts_at")
            ends = rule.get("ends_at")
            if starts:
                try:
                    starts_dt = datetime.fromisoformat(starts.replace("Z", "+00:00"))
                    if now < starts_dt:
                        continue
                except (ValueError, TypeError):
                    pass
            if ends:
                try:
                    ends_dt = datetime.fromisoformat(ends.replace("Z", "+00:00"))
                    if now > ends_dt:
                        continue
                except (ValueError, TypeError):
                    pass

            return rule["delegate_email"]

        return None

    def resolve_approvers(self, approver_emails: List[str]) -> List[str]:
        """Resolve a list of approver emails, replacing any with active delegates.

        Returns the resolved list with delegated approvers swapped out.
        """
        resolved = []
        for email in approver_emails:
            delegate = self.get_delegate_for(email)
            resolved.append(delegate if delegate else email)
        return resolved

    # ------------------------------------------------------------------
    # Auto-reassign on escalation
    # ------------------------------------------------------------------

    def auto_reassign_pending_approvals(self) -> int:
        """Find pending approval chains and reassign to delegates where applicable.

        Called from the background loop during escalation checks.
        Returns count of chains reassigned.
        """
        rules = self.list_rules(active_only=True)
        if not rules:
            return 0

        # Build delegator → delegate map (filtered by date range)
        delegate_map: Dict[str, str] = {}
        now = datetime.now(timezone.utc)
        for rule in rules:
            starts = rule.get("starts_at")
            ends = rule.get("ends_at")
            in_range = True
            if starts:
                try:
                    if now < datetime.fromisoformat(starts.replace("Z", "+00:00")):
                        in_range = False
                except (ValueError, TypeError):
                    pass
            if ends:
                try:
                    if now > datetime.fromisoformat(ends.replace("Z", "+00:00")):
                        in_range = False
                except (ValueError, TypeError):
                    pass
            if in_range:
                delegate_map[rule["delegator_email"]] = rule["delegate_email"]

        if not delegate_map:
            return 0

        reassigned = 0
        # Find pending chains for each delegator
        for delegator_email, delegate_email in delegate_map.items():
            try:
                chains = self.db.db_list_pending_chains_for_user(
                    self.organization_id, delegator_email,
                )
                for chain in chains:
                    chain_id = chain.get("id")
                    if not chain_id:
                        continue
                    try:
                        self.db.db_reassign_pending_step_approvers(
                            chain_id,
                            [delegate_email],
                            f"Auto-delegated from {delegator_email} (OOO)",
                        )
                        reassigned += 1
                        logger.info(
                            "[Delegation] Reassigned chain %s from %s to %s",
                            chain_id, delegator_email, delegate_email,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[Delegation] Failed to reassign chain %s: %s",
                            chain_id, exc,
                        )
            except Exception as exc:
                logger.warning(
                    "[Delegation] Failed to list pending chains for %s: %s",
                    delegator_email, exc,
                )

        return reassigned


def get_delegation_service(organization_id: str = "default") -> DelegationService:
    return DelegationService(organization_id=organization_id)
