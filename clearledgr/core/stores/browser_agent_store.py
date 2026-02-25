"""Browser-agent data-access mixin for ClearledgrDB.

``BrowserAgentStore`` is a **mixin class** -- it has no ``__init__`` of its own
and expects the concrete class that inherits it to provide:

* ``self.connect()``      -- returns a DB connection (context manager)
* ``self._prepare_sql()`` -- adapts ``?`` placeholders for the active engine
* ``self.initialize()``   -- ensures tables exist
* ``self.use_postgres``   -- bool flag for Postgres vs SQLite dialect

All methods are copied verbatim from ``clearledgr/core/database.py`` so that
``ClearledgrDB(BrowserAgentStore, ...)`` inherits them without any behavioural change.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BrowserAgentStore:
    # ------------------------------------------------------------------
    # Browser agent sessions
    # ------------------------------------------------------------------

    def create_agent_session(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        session_id = payload.get("id") or f"AGS-{uuid.uuid4().hex}"
        sql = self._prepare_sql(
            """
            INSERT INTO agent_sessions
            (id, organization_id, ap_item_id, state, created_by, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
        )
        values = (
            session_id,
            payload.get("organization_id"),
            payload.get("ap_item_id"),
            payload.get("state") or "running",
            payload.get("created_by"),
            payload.get("created_at") or now,
            payload.get("updated_at") or now,
            json.dumps(payload.get("metadata") or {}),
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, values)
            conn.commit()
        return self.get_agent_session(session_id) or {}

    def update_agent_session(self, session_id: str, **kwargs) -> bool:
        self.initialize()
        if not kwargs:
            return False
        now = datetime.now(timezone.utc).isoformat()
        kwargs["updated_at"] = now
        if "metadata" in kwargs and isinstance(kwargs["metadata"], dict):
            kwargs["metadata"] = json.dumps(kwargs["metadata"])
        set_clause = ", ".join(f"{key} = ?" for key in kwargs.keys())
        sql = self._prepare_sql(f"UPDATE agent_sessions SET {set_clause} WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*kwargs.values(), session_id))
            conn.commit()
            return cur.rowcount > 0

    def get_agent_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM agent_sessions WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (session_id,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        metadata = data.get("metadata")
        if isinstance(metadata, str):
            try:
                data["metadata"] = json.loads(metadata)
            except json.JSONDecodeError:
                data["metadata"] = {}
        return data

    def get_agent_session_by_item(self, organization_id: str, ap_item_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM agent_sessions WHERE organization_id = ? AND ap_item_id = ? ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, ap_item_id))
            row = cur.fetchone()
        if not row:
            return None
        return self.get_agent_session(str(dict(row).get("id")))

    # ------------------------------------------------------------------
    # Browser action events
    # ------------------------------------------------------------------

    def upsert_browser_action_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        import uuid

        session_id = str(payload.get("session_id") or "")
        command_id = str(payload.get("command_id") or "")
        if not session_id or not command_id:
            raise ValueError("session_id and command_id are required")

        existing = self.get_browser_action_event(session_id, command_id)
        now = datetime.now(timezone.utc).isoformat()
        event_id = payload.get("id") or (existing or {}).get("id") or f"BAE-{uuid.uuid4().hex}"
        request_payload = payload.get("request_payload")
        result_payload = payload.get("result_payload")
        if isinstance(request_payload, dict):
            request_payload = json.dumps(request_payload)
        if isinstance(result_payload, dict):
            result_payload = json.dumps(result_payload)

        if existing:
            update_fields: Dict[str, Any] = {}
            for key in (
                "status",
                "requires_confirmation",
                "approved_by",
                "approved_at",
                "policy_reason",
                "idempotency_key",
                "correlation_id",
            ):
                if key in payload:
                    update_fields[key] = payload.get(key)
            if request_payload is not None:
                update_fields["request_payload"] = request_payload
            if result_payload is not None:
                update_fields["result_payload"] = result_payload
            if update_fields:
                update_fields["updated_at"] = now
                set_clause = ", ".join(f"{k} = ?" for k in update_fields.keys())
                sql = self._prepare_sql(
                    f"UPDATE browser_action_events SET {set_clause} WHERE session_id = ? AND command_id = ?"
                )
                with self.connect() as conn:
                    cur = conn.cursor()
                    cur.execute(sql, (*update_fields.values(), session_id, command_id))
                    conn.commit()
            return self.get_browser_action_event(session_id, command_id) or {}

        sql = self._prepare_sql(
            """
            INSERT INTO browser_action_events
            (id, organization_id, ap_item_id, session_id, command_id, tool_name, status,
             requires_confirmation, approved_by, approved_at, policy_reason, request_payload,
             result_payload, idempotency_key, correlation_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        )
        values = (
            event_id,
            payload.get("organization_id"),
            payload.get("ap_item_id"),
            session_id,
            command_id,
            payload.get("tool_name"),
            payload.get("status") or "queued",
            1 if payload.get("requires_confirmation") else 0,
            payload.get("approved_by"),
            payload.get("approved_at"),
            payload.get("policy_reason"),
            request_payload if request_payload is not None else json.dumps(payload.get("request_payload") or {}),
            result_payload if result_payload is not None else json.dumps(payload.get("result_payload") or {}),
            payload.get("idempotency_key"),
            payload.get("correlation_id"),
            payload.get("created_at") or now,
            payload.get("updated_at") or now,
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, values)
            conn.commit()
        return self.get_browser_action_event(session_id, command_id) or {}

    def get_browser_action_event(self, session_id: str, command_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM browser_action_events WHERE session_id = ? AND command_id = ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (session_id, command_id))
            row = cur.fetchone()
        if not row:
            return None
        return self._deserialize_browser_action_event(dict(row))

    def list_browser_action_events(
        self,
        session_id: str,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        if status:
            sql = self._prepare_sql(
                "SELECT * FROM browser_action_events WHERE session_id = ? AND status = ? ORDER BY created_at ASC"
            )
            params = (session_id, status)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM browser_action_events WHERE session_id = ? ORDER BY created_at ASC"
            )
            params = (session_id,)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [self._deserialize_browser_action_event(dict(row)) for row in rows]

    def get_browser_action_event_by_idempotency_key(self, idempotency_key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM browser_action_events WHERE idempotency_key = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (idempotency_key,))
            row = cur.fetchone()
        if not row:
            return None
        return self._deserialize_browser_action_event(dict(row))

    # ------------------------------------------------------------------
    # Agent policies
    # ------------------------------------------------------------------

    def upsert_agent_policy(
        self,
        organization_id: str,
        policy_name: str,
        config: Dict[str, Any],
        updated_by: str = "system",
        enabled: bool = True,
    ) -> Dict[str, Any]:
        self.initialize()
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_agent_policy(organization_id, policy_name)
        policy_id = (existing or {}).get("id") or f"POL-{uuid.uuid4().hex}"

        if self.use_postgres:
            sql = self._prepare_sql(
                """
                INSERT INTO agent_policies
                (id, organization_id, policy_name, enabled, config_json, updated_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (organization_id, policy_name)
                DO UPDATE SET enabled = EXCLUDED.enabled,
                              config_json = EXCLUDED.config_json,
                              updated_by = EXCLUDED.updated_by,
                              updated_at = EXCLUDED.updated_at
                """
            )
        else:
            sql = self._prepare_sql(
                """
                INSERT OR REPLACE INTO agent_policies
                (id, organization_id, policy_name, enabled, config_json, updated_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                    1 if enabled else 0,
                    json.dumps(config or {}),
                    updated_by,
                    (existing or {}).get("created_at") or now,
                    now,
                ),
            )
            conn.commit()
        return self.get_agent_policy(organization_id, policy_name) or {}

    def get_agent_policy(self, organization_id: str, policy_name: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM agent_policies WHERE organization_id = ? AND policy_name = ?"
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
        return policy

    # ------------------------------------------------------------------
    # Deserialization helpers
    # ------------------------------------------------------------------

    def _deserialize_browser_action_event(self, row: Dict[str, Any]) -> Dict[str, Any]:
        for field in ("request_payload", "result_payload"):
            raw = row.get(field)
            if isinstance(raw, str):
                try:
                    row[field] = json.loads(raw)
                except json.JSONDecodeError:
                    row[field] = {}
        row["requires_confirmation"] = bool(row.get("requires_confirmation"))
        return row
