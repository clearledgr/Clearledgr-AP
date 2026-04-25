"""AP-policy data-access mixin for ClearledgrDB.

``PolicyStore`` is a **mixin class** -- it has no ``__init__`` of its own and
expects the concrete class that inherits it to provide:

* ``self.connect()``      -- returns a DB connection (context manager)
* ``self._prepare_sql()`` -- adapts ``?`` placeholders for the active engine
* ``self.initialize()``   -- ensures tables exist
* ``self.use_postgres``   -- bool flag for Postgres vs SQLite dialect

All methods are copied verbatim from ``clearledgr/core/database.py`` so that
``ClearledgrDB(PolicyStore, ...)`` inherits them without any behavioural change.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PolicyStore:
    # ------------------------------------------------------------------
    # AP policy versions
    # ------------------------------------------------------------------

    def list_ap_policy_versions(
        self,
        organization_id: str,
        policy_name: str = "ap_business_v1",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM ap_policy_versions WHERE organization_id = %s AND policy_name = %s "
            "ORDER BY version DESC LIMIT %s"
        )
        safe_limit = max(1, min(int(limit or 50), 500))
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, policy_name, safe_limit))
            rows = cur.fetchall()
        versions: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw = item.get("config_json")
            if isinstance(raw, str):
                try:
                    item["config_json"] = json.loads(raw)
                except json.JSONDecodeError:
                    item["config_json"] = {}
            item["enabled"] = bool(item.get("enabled"))
            versions.append(item)
        return versions

    def get_ap_policy(
        self,
        organization_id: str,
        policy_name: str = "ap_business_v1",
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM ap_policy_versions WHERE organization_id = %s AND policy_name = %s "
            "ORDER BY version DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, policy_name))
            row = cur.fetchone()
        if not row:
            return None
        policy = dict(row)
        raw = policy.get("config_json")
        if isinstance(raw, str):
            try:
                policy["config_json"] = json.loads(raw)
            except json.JSONDecodeError:
                policy["config_json"] = {}
        policy["enabled"] = bool(policy.get("enabled"))
        return policy

    def upsert_ap_policy_version(
        self,
        organization_id: str,
        policy_name: str,
        config: Dict[str, Any],
        updated_by: str = "system",
        enabled: bool = True,
    ) -> Dict[str, Any]:
        self.initialize()
        import uuid

        current = self.get_ap_policy(organization_id, policy_name) or {}
        version = int(current.get("version") or 0) + 1
        policy_id = f"APPOL-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()

        sql = (
            """
            INSERT INTO ap_policy_versions
            (id, organization_id, policy_name, version, enabled, config_json, updated_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    policy_id,
                    organization_id,
                    policy_name,
                    version,
                    1 if enabled else 0,
                    json.dumps(config or {}),
                    updated_by,
                    now,
                ),
            )
            conn.commit()

        updated_policy = self.get_ap_policy(organization_id, policy_name) or {}
        self.append_ap_policy_audit_event(
            organization_id=organization_id,
            policy_name=policy_name,
            version=int(updated_policy.get("version") or version),
            action="upsert",
            actor_id=updated_by,
            payload={
                "enabled": bool(enabled),
                "config": config or {},
                "previous_version": current.get("version"),
            },
        )
        return updated_policy

    # ------------------------------------------------------------------
    # AP policy audit events
    # ------------------------------------------------------------------

    def append_ap_policy_audit_event(
        self,
        organization_id: str,
        policy_name: str,
        version: Optional[int],
        action: str,
        actor_id: str = "system",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        import uuid

        event_id = f"APPOL-AUD-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            """
            INSERT INTO ap_policy_audit_events
            (id, organization_id, policy_name, version, action, actor_id, payload_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    event_id,
                    organization_id,
                    policy_name,
                    int(version) if version is not None else None,
                    action,
                    actor_id,
                    json.dumps(payload or {}),
                    now,
                ),
            )
            conn.commit()
            row_sql = "SELECT * FROM ap_policy_audit_events WHERE id = %s LIMIT 1"
            cur.execute(row_sql, (event_id,))
            row = cur.fetchone()
        return self._deserialize_ap_policy_audit_event(dict(row)) if row else None

    def list_ap_policy_audit_events(
        self,
        organization_id: str,
        policy_name: str = "ap_business_v1",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        safe_limit = max(1, min(int(limit or 100), 1000))
        sql = (
            "SELECT * FROM ap_policy_audit_events WHERE organization_id = %s AND policy_name = %s "
            "ORDER BY created_at DESC LIMIT %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, policy_name, safe_limit))
            rows = cur.fetchall()
        return [self._deserialize_ap_policy_audit_event(dict(row)) for row in rows]

    # ------------------------------------------------------------------
    # Deserialization helpers
    # ------------------------------------------------------------------

    def _deserialize_ap_policy_audit_event(self, row: Dict[str, Any]) -> Dict[str, Any]:
        raw = row.get("payload_json")
        if isinstance(raw, str):
            try:
                row["payload_json"] = json.loads(raw)
            except json.JSONDecodeError:
                row["payload_json"] = {}
        return row
