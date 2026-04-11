"""Entity-domain data-access mixin for ClearledgrDB.

``EntityStore`` is a **mixin class** -- it has no ``__init__`` of its own
and expects the concrete class that inherits it to provide:

* ``self.connect()``            -- returns a DB connection (context manager)
* ``self._prepare_sql()``       -- adapts ``?`` placeholders for the active engine
* ``self.initialize()``         -- ensures tables exist
* ``self._decode_json_value()`` -- safely parses a JSON string or returns ``{}``
* ``self.use_postgres``         -- bool flag for Postgres vs SQLite dialect

Multi-entity support
~~~~~~~~~~~~~~~~~~~~
Organizations like Cowrywise have "different entities in Africa and US".
Each entity can have its own ERP connection, GL mapping, approval rules,
and default currency.  When no entities are configured, everything works
as before (backward compatible).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EntityStore:
    """Mixin providing entity persistence methods."""

    ENTITIES_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            name TEXT NOT NULL,
            code TEXT,
            erp_connection_id TEXT,
            gl_mapping_json TEXT,
            approval_rules_json TEXT,
            default_currency TEXT DEFAULT 'USD',
            settings_json TEXT DEFAULT '{}',
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(organization_id, code)
        )
    """

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    def create_entity(
        self,
        organization_id: str,
        name: str,
        code: Optional[str] = None,
        erp_connection_id: Optional[str] = None,
        gl_mapping: Optional[Dict[str, Any]] = None,
        approval_rules: Optional[Dict[str, Any]] = None,
        currency: str = "USD",
    ) -> Dict[str, Any]:
        """Create a new entity within an organization."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        entity_id = f"ENT-{uuid.uuid4().hex}"
        gl_mapping_json = json.dumps(gl_mapping) if gl_mapping else None
        approval_rules_json = json.dumps(approval_rules) if approval_rules else None

        sql = self._prepare_sql("""
            INSERT INTO entities
            (id, organization_id, name, code, erp_connection_id,
             gl_mapping_json, approval_rules_json, default_currency,
             is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                entity_id, organization_id, name, code, erp_connection_id,
                gl_mapping_json, approval_rules_json, currency or "USD",
                now, now,
            ))
            conn.commit()
        return self.get_entity(entity_id) or {"id": entity_id}

    def get_entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get a single entity by ID."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM entities WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (entity_id,))
            row = cur.fetchone()
        if not row:
            return None
        return self._format_entity_row(dict(row))

    def list_entities(
        self,
        organization_id: str,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """List entities for an organization."""
        self.initialize()
        if include_inactive:
            sql = self._prepare_sql(
                "SELECT * FROM entities WHERE organization_id = ? ORDER BY name ASC"
            )
        else:
            sql = self._prepare_sql(
                "SELECT * FROM entities WHERE organization_id = ? AND is_active = 1 ORDER BY name ASC"
            )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        return [self._format_entity_row(dict(row)) for row in rows]

    def get_entity_by_code(
        self,
        organization_id: str,
        code: str,
    ) -> Optional[Dict[str, Any]]:
        """Look up an active entity by its code within an org."""
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM entities WHERE organization_id = ? AND code = ? AND is_active = 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, code))
            row = cur.fetchone()
        if not row:
            return None
        return self._format_entity_row(dict(row))

    _ENTITY_ALLOWED_COLUMNS = frozenset({
        "name", "code", "erp_connection_id", "gl_mapping_json",
        "approval_rules_json", "default_currency", "is_active", "updated_at",
    })

    def update_entity(self, entity_id: str, **kwargs) -> bool:
        """Update an entity. Only whitelisted columns are accepted."""
        self.initialize()
        # Accept gl_mapping / approval_rules as dicts and serialize
        if "gl_mapping" in kwargs:
            val = kwargs.pop("gl_mapping")
            kwargs["gl_mapping_json"] = json.dumps(val) if val is not None else None
        if "approval_rules" in kwargs:
            val = kwargs.pop("approval_rules")
            kwargs["approval_rules_json"] = json.dumps(val) if val is not None else None

        safe = {k: v for k, v in kwargs.items() if k in self._ENTITY_ALLOWED_COLUMNS}
        if not safe:
            return False
        safe["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{col} = ?" for col in safe)
        sql = self._prepare_sql(f"UPDATE entities SET {set_clause} WHERE id = ?")
        params = list(safe.values()) + [entity_id]
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount > 0

    def delete_entity(self, entity_id: str) -> bool:
        """Soft-delete an entity (set is_active=0)."""
        return self.update_entity(entity_id, is_active=0)

    # ------------------------------------------------------------------
    # §3 Multi-Entity: Parent/child organization hierarchy
    # ------------------------------------------------------------------

    def get_child_organizations(self, parent_org_id: str) -> List[Dict[str, Any]]:
        """List all child organizations of a parent account."""
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM organizations WHERE parent_organization_id = ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (parent_org_id,))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def get_parent_organization(self, org_id: str) -> Optional[Dict[str, Any]]:
        """Return the parent organization, or the org itself if it has no parent."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM organizations WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (org_id,))
            row = cur.fetchone()
            if not row:
                return None
            org = dict(row)
            parent_id = org.get("parent_organization_id")
            if not parent_id or parent_id == org_id:
                return org
            # Fetch parent within the same connection
            cur2 = conn.cursor()
            cur2.execute(sql, (parent_id,))
            parent_row = cur2.fetchone()
            return dict(parent_row) if parent_row else org

    def is_parent_account(self, org_id: str) -> bool:
        """True if this organization has child organizations."""
        children = self.get_child_organizations(org_id)
        return len(children) > 0

    def get_all_entity_org_ids(self, parent_org_id: str) -> List[str]:
        """Return org IDs for parent + all children (for consolidated queries)."""
        children = self.get_child_organizations(parent_org_id)
        return [parent_org_id] + [c["id"] for c in children]

    def get_effective_subscription(self, org_id: str) -> Optional[Dict[str, Any]]:
        """§3: Child orgs inherit parent's subscription.

        Uses ``get_parent_organization`` to walk up the hierarchy.
        """
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM subscriptions WHERE organization_id = ?")
        # Check own subscription first
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (org_id,))
            row = cur.fetchone()
        if row:
            return dict(row)
        # Walk up to parent via get_parent_organization
        parent = self.get_parent_organization(org_id)
        if parent:
            parent_id = parent.get("id")
            if parent_id and parent_id != org_id:
                with self.connect() as conn:
                    cur = conn.cursor()
                    cur.execute(sql, (parent_id,))
                    parent_row = cur.fetchone()
                if parent_row:
                    return dict(parent_row)
        return None

    def get_effective_agent_config(self, entity_id: str) -> Dict[str, Any]:
        """§3 Multi-entity: entity-specific agent config with org fallback.

        Reads entity.settings_json for override keys like autonomy_tier,
        override_window_minutes, auto_approve_threshold. Falls back to
        org-level settings for any key not overridden.
        """
        entity = self.get_entity(entity_id)
        if not entity:
            return {}
        org_id = entity.get("organization_id")
        entity_settings = self._decode_json_value(entity.get("settings_json"), {})

        # Get org-level settings
        org_settings = {}
        try:
            sql = self._prepare_sql("SELECT settings_json FROM organizations WHERE id = ?")
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (org_id,))
                row = cur.fetchone()
            if row:
                org_settings = self._decode_json_value(dict(row).get("settings_json"), {})
        except Exception:
            pass

        # Merge: entity overrides take precedence
        merged = {**org_settings, **entity_settings}
        merged["_source"] = "entity" if entity_settings else "organization"
        merged["entity_id"] = entity_id
        return merged

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_entity_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Deserialize JSON fields on an entity row."""
        row["gl_mapping"] = self._decode_json_value(row.get("gl_mapping_json"), {})
        row["approval_rules"] = self._decode_json_value(row.get("approval_rules_json"), {})
        row["settings"] = self._decode_json_value(row.get("settings_json"), {})
        row["is_active"] = bool(row.get("is_active"))
        return row
