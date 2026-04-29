"""Per-tenant custom roles (Module 6 Pass A).

Custom roles are composed from the bounded ``PERMISSION_CATALOG`` and
override the standard role taxonomy when assigned to a user. Limit
is 10 per organization (per scope spec) — enforced here rather than
via a DB CHECK so the API can return a structured ``custom_role_limit``
error instead of a bare integrity violation.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional

from clearledgr.core.permissions import (
    CUSTOM_ROLES_PER_ORG_LIMIT,
    normalize_permission_set,
)

logger = logging.getLogger(__name__)


class CustomRoleLimitExceeded(Exception):
    """Raised by ``create_custom_role`` when the org has 10 roles already."""


class CustomRoleNameTaken(Exception):
    """Raised when an org already has a role with the same case-insensitive name."""


class CustomRolesStore:
    """Mixin: CRUD for ``custom_roles``."""

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def list_custom_roles(self, organization_id: str) -> List[Dict[str, Any]]:
        """Return every custom role for the org, name-sorted ASC."""
        self.initialize()
        sql = (
            "SELECT * FROM custom_roles "
            "WHERE organization_id = %s "
            "ORDER BY LOWER(name) ASC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            row["permissions"] = self._decode_permissions(row.get("permissions_json"))
            row.pop("permissions_json", None)
        return rows

    def get_custom_role(self, role_id: str) -> Optional[Dict[str, Any]]:
        """Return a single role by id, or None."""
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM custom_roles WHERE id = %s", (role_id,))
            row = cur.fetchone()
        if not row:
            return None
        out = dict(row)
        out["permissions"] = self._decode_permissions(out.get("permissions_json"))
        out.pop("permissions_json", None)
        return out

    def count_custom_roles(self, organization_id: str) -> int:
        """Count custom roles for an org. Used for quota enforcement."""
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS n FROM custom_roles WHERE organization_id = %s",
                (organization_id,),
            )
            row = cur.fetchone()
        if not row:
            return 0
        # psycopg dict_row returns {"n": N}; tuple cursor returns (N,)
        if isinstance(row, dict):
            return int(row.get("n") or 0)
        return int(row[0] or 0)

    # ------------------------------------------------------------------
    # Write paths
    # ------------------------------------------------------------------

    def create_custom_role(
        self,
        *,
        organization_id: str,
        name: str,
        permissions: List[str],
        description: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a custom role. Enforces the 10-per-org limit and
        the (org, lower(name)) uniqueness invariant.

        Permissions are normalized through the catalog — unknown
        strings are silently dropped before persisting so the row
        never contains junk.
        """
        self.initialize()

        if self.count_custom_roles(organization_id) >= CUSTOM_ROLES_PER_ORG_LIMIT:
            raise CustomRoleLimitExceeded(
                f"organization has reached the {CUSTOM_ROLES_PER_ORG_LIMIT}-role limit"
            )

        clean_perms = sorted(normalize_permission_set(permissions))
        if not clean_perms:
            raise ValueError("permissions must include at least one valid entry")

        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("name must not be empty")

        role_id = f"cr_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()

        sql = (
            "INSERT INTO custom_roles "
            "(id, organization_id, name, description, permissions_json, "
            " created_by, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    sql,
                    (
                        role_id, organization_id, clean_name,
                        (description or None),
                        json.dumps(clean_perms),
                        (created_by or None), now, now,
                    ),
                )
                conn.commit()
        except Exception as exc:
            # Postgres IntegrityError on the unique index → friendly
            # exception so the API can return 409 cleanly.
            msg = str(exc).lower()
            if "idx_custom_roles_org_name" in msg or "unique" in msg:
                raise CustomRoleNameTaken(
                    f"a custom role named {clean_name!r} already exists"
                ) from exc
            raise

        return {
            "id": role_id,
            "organization_id": organization_id,
            "name": clean_name,
            "description": description,
            "permissions": clean_perms,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }

    def update_custom_role(
        self,
        role_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        permissions: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update a role in place. Returns the new row or None if
        the id doesn't exist. Permissions list, if supplied, must
        contain at least one valid entry — empty after normalization
        raises ``ValueError`` so we never persist a permissionless
        role that silently denies everything to its assignees.
        """
        self.initialize()
        existing = self.get_custom_role(role_id)
        if not existing:
            return None

        updates: Dict[str, Any] = {}
        if name is not None:
            clean = name.strip()
            if not clean:
                raise ValueError("name must not be empty")
            updates["name"] = clean
        if description is not None:
            updates["description"] = description.strip() or None
        if permissions is not None:
            clean_perms = sorted(normalize_permission_set(permissions))
            if not clean_perms:
                raise ValueError("permissions must include at least one valid entry")
            updates["permissions_json"] = json.dumps(clean_perms)

        if not updates:
            return existing  # caller passed nothing — no-op

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        set_clause = ", ".join(f"{k} = %s" for k in updates)
        params = list(updates.values()) + [role_id]
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"UPDATE custom_roles SET {set_clause} WHERE id = %s",
                    tuple(params),
                )
                conn.commit()
        except Exception as exc:
            if "idx_custom_roles_org_name" in str(exc).lower():
                raise CustomRoleNameTaken(
                    f"a custom role named {updates.get('name')!r} already exists"
                ) from exc
            raise

        return self.get_custom_role(role_id)

    def delete_custom_role(self, role_id: str) -> bool:
        """Delete a role. Returns True if a row was actually deleted."""
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM custom_roles WHERE id = %s", (role_id,))
            conn.commit()
            return (cur.rowcount or 0) > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _decode_permissions(self, raw: Any) -> List[str]:
        """Decode the persisted permissions_json into a sorted list.

        The DB column is TEXT for cross-engine portability. Decode
        failures collapse to ``[]`` so a corrupted row never breaks
        the list endpoint — better to surface "no permissions" than
        500 the dashboard.
        """
        if raw is None:
            return []
        if isinstance(raw, list):
            return sorted(normalize_permission_set(raw))
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return sorted(normalize_permission_set(parsed))
            except Exception:
                logger.warning(
                    "[custom_roles] could not decode permissions_json: %r", raw[:80]
                )
        return []

    def resolve_custom_role_permissions(
        self, role_id: Optional[str]
    ) -> FrozenSet[str]:
        """Resolve a custom role id to its permission set.

        Used by ``has_permission(custom_role_permissions=...)``. Bad /
        deleted ids return the empty set so a stale assignment can
        never accidentally grant permissions.
        """
        if not role_id or not str(role_id).startswith("cr_"):
            return frozenset()
        row = self.get_custom_role(role_id)
        if not row:
            return frozenset()
        return frozenset(row.get("permissions") or [])
