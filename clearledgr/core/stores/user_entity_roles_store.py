"""Per-(user, entity) role assignments + approval ceilings (Module 6 Pass B).

A row here overrides the user's org-level role for the named entity.
The ``role`` column accepts either a standard role token (``owner`` /
``cfo`` / ... / ``read_only``) OR a custom role id (``cr_<hex>``)
referencing ``custom_roles.id``. The resolver is tolerant of stale
custom-role ids — the row stays, but the resolver collapses unknown
custom ids to an empty permission set (callers fall back to standard
role permissions).

Reads happen on every approval gate; the (user_id, entity_id) primary
key keeps lookups index-served regardless of org size.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class UserEntityRolesStore:
    """Mixin: read/write the user_entity_roles table."""

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def get_user_entity_role(
        self, user_id: str, entity_id: str
    ) -> Optional[Dict[str, Any]]:
        """Return the row for one (user, entity) pair, or None.

        Hot path — called on every approval gate. Indexed by primary
        key (user_id, entity_id).
        """
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM user_entity_roles WHERE user_id = %s AND entity_id = %s",
                (user_id, entity_id),
            )
            row = cur.fetchone()
        return self._row_to_dict(row)

    def list_user_entity_roles(self, user_id: str) -> List[Dict[str, Any]]:
        """Every per-entity assignment for one user, entity-id ASC."""
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM user_entity_roles "
                "WHERE user_id = %s ORDER BY entity_id ASC",
                (user_id,),
            )
            rows = cur.fetchall()
        return [d for d in (self._row_to_dict(r) for r in rows) if d]

    def list_user_entity_roles_for_org(
        self, organization_id: str
    ) -> List[Dict[str, Any]]:
        """Every per-entity assignment for one tenant.

        Used by the admin UI to render the cross-user matrix. Limited
        to a few hundred rows in the typical mid-market customer (10
        entities × 50 users), so a single-pass scan is fine.
        """
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM user_entity_roles "
                "WHERE organization_id = %s "
                "ORDER BY user_id, entity_id",
                (organization_id,),
            )
            rows = cur.fetchall()
        return [d for d in (self._row_to_dict(r) for r in rows) if d]

    # ------------------------------------------------------------------
    # Write paths
    # ------------------------------------------------------------------

    def set_user_entity_role(
        self,
        *,
        user_id: str,
        entity_id: str,
        organization_id: str,
        role: str,
        approval_ceiling: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """Upsert a per-(user, entity) role + ceiling.

        Returns the persisted row. Caller is responsible for validating
        ``role`` against the standard role taxonomy + custom_roles
        catalog — this method just stores what it's told.
        """
        self.initialize()

        clean_role = (role or "").strip().lower()
        if not clean_role:
            raise ValueError("role must not be empty")

        ceiling_normalized: Optional[Decimal]
        if approval_ceiling is None:
            ceiling_normalized = None
        else:
            try:
                ceiling_normalized = Decimal(str(approval_ceiling))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"approval_ceiling must be numeric: {exc}") from exc
            if ceiling_normalized < 0:
                raise ValueError("approval_ceiling must be non-negative")

        now = datetime.now(timezone.utc).isoformat()
        sql = (
            "INSERT INTO user_entity_roles "
            "(user_id, entity_id, organization_id, role, approval_ceiling, "
            " created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (user_id, entity_id) "
            "DO UPDATE SET role = EXCLUDED.role, "
            "              approval_ceiling = EXCLUDED.approval_ceiling, "
            "              organization_id = EXCLUDED.organization_id, "
            "              updated_at = EXCLUDED.updated_at"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    user_id, entity_id, organization_id, clean_role,
                    ceiling_normalized, now, now,
                ),
            )
            conn.commit()
        row = self.get_user_entity_role(user_id, entity_id)
        return row or {
            "user_id": user_id, "entity_id": entity_id,
            "organization_id": organization_id, "role": clean_role,
            "approval_ceiling": ceiling_normalized,
            "created_at": now, "updated_at": now,
        }

    def delete_user_entity_role(self, user_id: str, entity_id: str) -> bool:
        """Remove a per-entity assignment. Returns True on actual delete."""
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM user_entity_roles "
                "WHERE user_id = %s AND entity_id = %s",
                (user_id, entity_id),
            )
            conn.commit()
            return (cur.rowcount or 0) > 0

    def replace_user_entity_roles(
        self,
        *,
        user_id: str,
        organization_id: str,
        assignments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Idempotently replace ALL per-entity assignments for one user.

        ``assignments`` is a list of ``{entity_id, role, approval_ceiling?}``
        dicts. Behaviour:
          * any entity_id present → upsert
          * any entity_id missing from the request that previously had
            a row → delete (so the operator can clear an assignment by
            simply omitting it)

        The replace happens inside a single transaction so the user
        is never observed in a half-applied state.
        """
        self.initialize()
        existing = self.list_user_entity_roles(user_id)
        existing_ids = {r["entity_id"] for r in existing}
        incoming_ids = {a.get("entity_id") for a in assignments if a.get("entity_id")}
        to_delete = existing_ids - incoming_ids

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            for a in assignments:
                entity_id = a.get("entity_id")
                role = (a.get("role") or "").strip().lower()
                if not entity_id or not role:
                    continue
                ceiling = a.get("approval_ceiling")
                if ceiling is not None:
                    try:
                        ceiling = Decimal(str(ceiling))
                        if ceiling < 0:
                            raise ValueError("approval_ceiling must be non-negative")
                    except (InvalidOperation, ValueError) as exc:
                        raise ValueError(
                            f"invalid approval_ceiling for entity {entity_id}: {exc}"
                        ) from exc
                cur.execute(
                    "INSERT INTO user_entity_roles "
                    "(user_id, entity_id, organization_id, role, approval_ceiling, "
                    " created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (user_id, entity_id) "
                    "DO UPDATE SET role = EXCLUDED.role, "
                    "              approval_ceiling = EXCLUDED.approval_ceiling, "
                    "              organization_id = EXCLUDED.organization_id, "
                    "              updated_at = EXCLUDED.updated_at",
                    (
                        user_id, entity_id, organization_id, role,
                        ceiling, now, now,
                    ),
                )
            for entity_id in to_delete:
                cur.execute(
                    "DELETE FROM user_entity_roles "
                    "WHERE user_id = %s AND entity_id = %s",
                    (user_id, entity_id),
                )
            conn.commit()
        return self.list_user_entity_roles(user_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_dict(self, row) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        out = dict(row)
        # NUMERIC → Decimal in psycopg; serialise to str at API boundary
        # so JSON callers don't choke on Decimal.
        if out.get("approval_ceiling") is not None:
            out["approval_ceiling"] = Decimal(str(out["approval_ceiling"]))
        return out
