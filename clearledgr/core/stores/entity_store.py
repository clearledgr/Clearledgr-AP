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
    # Helpers
    # ------------------------------------------------------------------

    def _format_entity_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Deserialize JSON fields on an entity row."""
        row["gl_mapping"] = self._decode_json_value(row.get("gl_mapping_json"), {})
        row["approval_rules"] = self._decode_json_value(row.get("approval_rules_json"), {})
        row["is_active"] = bool(row.get("is_active"))
        return row
