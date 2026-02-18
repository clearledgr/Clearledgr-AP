"""
Clearledgr AP v1 Database

Single source of truth for AP items, approvals, audit events, Gmail OAuth tokens,
Gmail autopilot state, and ERP connections.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    import psycopg
    from psycopg.rows import dict_row
    HAS_POSTGRES = True
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None
    HAS_POSTGRES = False


class ClearledgrDB:
    def __init__(self, db_path: str = "clearledgr.db"):
        self.dsn = os.getenv("DATABASE_URL")
        self.db_path = db_path
        dsn = (self.dsn or "").strip().lower()
        self.allow_sqlite_fallback = str(
            os.getenv("CLEARLEDGR_DB_FALLBACK_SQLITE", "true")
        ).strip().lower() not in {"0", "false", "no", "off"}
        self.use_postgres = bool(
            HAS_POSTGRES
            and dsn
            and (dsn.startswith("postgres://") or dsn.startswith("postgresql://"))
        )
        self._initialized = False
        self._fallback_warned = False

    def _sqlite_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def connect(self):
        if self.use_postgres:
            try:
                conn = psycopg.connect(self.dsn, row_factory=dict_row)
            except Exception as exc:
                if not self.allow_sqlite_fallback:
                    raise
                if not self._fallback_warned:
                    logging.getLogger(__name__).warning(
                        "Postgres unavailable (%s). Falling back to SQLite at %s. "
                        "Set CLEARLEDGR_DB_FALLBACK_SQLITE=false to disable fallback.",
                        exc,
                        self.db_path,
                    )
                    self._fallback_warned = True
                self.use_postgres = False
                conn = self._sqlite_connection()
        else:
            conn = self._sqlite_connection()
        try:
            yield conn
        finally:
            conn.close()

    def _prepare_sql(self, sql: str) -> str:
        if self.use_postgres:
            return sql.replace("?", "%s")
        return sql

    def _table_columns(self, cur, table: str) -> set[str]:
        if self.use_postgres:
            sql = self._prepare_sql(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?"
            )
            cur.execute(sql, (table,))
            rows = cur.fetchall()
            return {str(row["column_name"]) for row in rows}
        cur.execute(f"PRAGMA table_info({table})")
        rows = cur.fetchall()
        return {str(row["name"]) for row in rows}

    def _ensure_column(self, cur, table: str, column: str, definition: str) -> None:
        columns = self._table_columns(cur, table)
        if column in columns:
            return
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def initialize(self) -> None:
        if self._initialized:
            return
        with self.connect() as conn:
            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    expires_at TEXT,
                    email TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(user_id, provider)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS gmail_autopilot_state (
                    user_id TEXT PRIMARY KEY,
                    email TEXT,
                    last_history_id TEXT,
                    watch_expiration TEXT,
                    last_watch_at TEXT,
                    last_scan_at TEXT,
                    last_error TEXT,
                    updated_at TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS erp_connections (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    erp_type TEXT NOT NULL,
                    access_token TEXT,
                    refresh_token TEXT,
                    realm_id TEXT,
                    tenant_id TEXT,
                    base_url TEXT,
                    credentials TEXT,
                    is_active INTEGER DEFAULT 1,
                    last_sync_at TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(organization_id, erp_type)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS ap_items (
                    id TEXT PRIMARY KEY,
                    invoice_key TEXT,
                    thread_id TEXT,
                    message_id TEXT,
                    subject TEXT,
                    sender TEXT,
                    vendor_name TEXT,
                    amount REAL,
                    currency TEXT DEFAULT 'USD',
                    invoice_number TEXT,
                    invoice_date TEXT,
                    due_date TEXT,
                    state TEXT NOT NULL,
                    confidence REAL DEFAULT 0,
                    approval_required INTEGER DEFAULT 1,
                    approved_by TEXT,
                    approved_at TEXT,
                    rejected_by TEXT,
                    rejected_at TEXT,
                    rejection_reason TEXT,
                    erp_reference TEXT,
                    erp_posted_at TEXT,
                    workflow_id TEXT,
                    run_id TEXT,
                    approval_surface TEXT DEFAULT 'hybrid',
                    approval_policy_version TEXT,
                    post_attempted_at TEXT,
                    last_error TEXT,
                    organization_id TEXT,
                    user_id TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    metadata TEXT,
                    UNIQUE(organization_id, invoice_key)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS ap_item_sources (
                    id TEXT PRIMARY KEY,
                    ap_item_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    subject TEXT,
                    sender TEXT,
                    detected_at TEXT,
                    metadata TEXT,
                    created_at TEXT,
                    UNIQUE(ap_item_id, source_type, source_ref)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS ap_item_context_cache (
                    ap_item_id TEXT PRIMARY KEY,
                    context_json TEXT,
                    updated_at TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    ap_item_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    prev_state TEXT,
                    new_state TEXT,
                    actor_type TEXT,
                    actor_id TEXT,
                    payload_json TEXT,
                    external_refs TEXT,
                    idempotency_key TEXT UNIQUE,
                    source TEXT,
                    correlation_id TEXT,
                    workflow_id TEXT,
                    run_id TEXT,
                    decision_reason TEXT,
                    organization_id TEXT,
                    ts TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    ap_item_id TEXT NOT NULL,
                    channel_id TEXT,
                    message_ts TEXT,
                    source_channel TEXT,
                    source_message_ref TEXT,
                    decision_idempotency_key TEXT,
                    decision_payload TEXT,
                    status TEXT DEFAULT 'pending',
                    approved_by TEXT,
                    approved_at TEXT,
                    rejected_by TEXT,
                    rejected_at TEXT,
                    rejection_reason TEXT,
                    organization_id TEXT,
                    created_at TEXT,
                    UNIQUE(ap_item_id, channel_id, message_ts)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    ap_item_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_by TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    metadata TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS browser_action_events (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    ap_item_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requires_confirmation INTEGER DEFAULT 0,
                    approved_by TEXT,
                    approved_at TEXT,
                    policy_reason TEXT,
                    request_payload TEXT,
                    result_payload TEXT,
                    idempotency_key TEXT UNIQUE,
                    correlation_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_policies (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    policy_name TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    config_json TEXT,
                    updated_by TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(organization_id, policy_name)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS ap_policy_versions (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    policy_name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    config_json TEXT,
                    updated_by TEXT,
                    created_at TEXT,
                    UNIQUE(organization_id, policy_name, version)
                )
            """)

            cur.execute("CREATE INDEX IF NOT EXISTS idx_oauth_user ON oauth_tokens(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_oauth_provider ON oauth_tokens(provider)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_autopilot_email ON gmail_autopilot_state(email)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_erp_org ON erp_connections(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_org ON ap_items(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_state ON ap_items(state)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_thread ON ap_items(thread_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_org_message ON ap_items(organization_id, message_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_org_state_updated ON ap_items(organization_id, state, updated_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_item_sources_item ON ap_item_sources(ap_item_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_item_sources_type_ref ON ap_item_sources(source_type, source_ref)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_item ON audit_events(ap_item_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_events(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_org_event_ts ON audit_events(organization_id, event_type, ts)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_approvals_item ON approvals(ap_item_id)")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_item_decision_key ON approvals(ap_item_id, decision_idempotency_key)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_sessions_org_item ON agent_sessions(organization_id, ap_item_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_browser_actions_session ON browser_action_events(session_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_browser_actions_status ON browser_action_events(session_id, status)")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_browser_actions_session_command ON browser_action_events(session_id, command_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_policies_org ON agent_policies(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_policy_versions_org_name ON ap_policy_versions(organization_id, policy_name, version)")

            # Evolve existing DBs without external migration dependency.
            self._ensure_column(cur, "ap_items", "workflow_id", "TEXT")
            self._ensure_column(cur, "ap_items", "run_id", "TEXT")
            self._ensure_column(cur, "ap_items", "approval_surface", "TEXT DEFAULT 'hybrid'")
            self._ensure_column(cur, "ap_items", "approval_policy_version", "TEXT")
            self._ensure_column(cur, "ap_items", "post_attempted_at", "TEXT")

            self._ensure_column(cur, "audit_events", "source", "TEXT")
            self._ensure_column(cur, "audit_events", "correlation_id", "TEXT")
            self._ensure_column(cur, "audit_events", "workflow_id", "TEXT")
            self._ensure_column(cur, "audit_events", "run_id", "TEXT")
            self._ensure_column(cur, "audit_events", "decision_reason", "TEXT")

            self._ensure_column(cur, "approvals", "source_channel", "TEXT")
            self._ensure_column(cur, "approvals", "source_message_ref", "TEXT")
            self._ensure_column(cur, "approvals", "decision_idempotency_key", "TEXT")
            self._ensure_column(cur, "approvals", "decision_payload", "TEXT")

            self._ensure_column(cur, "browser_action_events", "requires_confirmation", "INTEGER DEFAULT 0")
            self._ensure_column(cur, "browser_action_events", "approved_by", "TEXT")
            self._ensure_column(cur, "browser_action_events", "approved_at", "TEXT")
            self._ensure_column(cur, "browser_action_events", "policy_reason", "TEXT")
            self._ensure_column(cur, "browser_action_events", "result_payload", "TEXT")
            self._ensure_column(cur, "browser_action_events", "idempotency_key", "TEXT")
            self._ensure_column(cur, "browser_action_events", "correlation_id", "TEXT")

            conn.commit()

        self._initialized = True

    # ------------------------------------------------------------------
    # OAuth tokens
    # ------------------------------------------------------------------

    def save_oauth_token(
        self,
        user_id: str,
        provider: str,
        access_token: str,
        refresh_token: Optional[str],
        expires_at: Optional[str],
        email: Optional[str],
    ) -> None:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        import uuid
        token_id = f"TOK-{uuid.uuid4().hex}"

        if self.use_postgres:
            sql = self._prepare_sql("""
                INSERT INTO oauth_tokens
                (id, user_id, provider, access_token, refresh_token, expires_at, email, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (user_id, provider)
                DO UPDATE SET access_token = EXCLUDED.access_token,
                              refresh_token = EXCLUDED.refresh_token,
                              expires_at = EXCLUDED.expires_at,
                              email = EXCLUDED.email,
                              updated_at = EXCLUDED.updated_at
            """)
            params = (token_id, user_id, provider, access_token, refresh_token, expires_at, email, now, now)
        else:
            sql = self._prepare_sql("""
                INSERT OR REPLACE INTO oauth_tokens
                (id, user_id, provider, access_token, refresh_token, expires_at, email, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """)
            params = (token_id, user_id, provider, access_token, refresh_token, expires_at, email, now, now)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

    def get_oauth_token(self, user_id: str, provider: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM oauth_tokens WHERE user_id = ? AND provider = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id, provider))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_oauth_token_by_email(self, email: str, provider: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM oauth_tokens WHERE email = ? AND provider = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (email, provider))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_oauth_tokens(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        self.initialize()
        if provider:
            sql = self._prepare_sql("SELECT * FROM oauth_tokens WHERE provider = ?")
            params = (provider,)
        else:
            sql = "SELECT * FROM oauth_tokens"
            params = ()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def delete_oauth_token(self, user_id: str, provider: str) -> None:
        self.initialize()
        sql = self._prepare_sql("DELETE FROM oauth_tokens WHERE user_id = ? AND provider = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id, provider))
            conn.commit()

    # ------------------------------------------------------------------
    # Gmail autopilot state
    # ------------------------------------------------------------------

    def get_gmail_autopilot_state(self, user_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM gmail_autopilot_state WHERE user_id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_gmail_autopilot_states(self) -> List[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM gmail_autopilot_state"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def save_gmail_autopilot_state(
        self,
        user_id: str,
        email: Optional[str] = None,
        last_history_id: Optional[str] = None,
        watch_expiration: Optional[str] = None,
        last_watch_at: Optional[str] = None,
        last_scan_at: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()

        if self.use_postgres:
            sql = self._prepare_sql("""
                INSERT INTO gmail_autopilot_state
                (user_id, email, last_history_id, watch_expiration, last_watch_at, last_scan_at, last_error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (user_id)
                DO UPDATE SET email = EXCLUDED.email,
                              last_history_id = EXCLUDED.last_history_id,
                              watch_expiration = EXCLUDED.watch_expiration,
                              last_watch_at = EXCLUDED.last_watch_at,
                              last_scan_at = EXCLUDED.last_scan_at,
                              last_error = EXCLUDED.last_error,
                              updated_at = EXCLUDED.updated_at
            """)
            params = (user_id, email, last_history_id, watch_expiration, last_watch_at, last_scan_at, last_error, now)
        else:
            sql = self._prepare_sql("""
                INSERT OR REPLACE INTO gmail_autopilot_state
                (user_id, email, last_history_id, watch_expiration, last_watch_at, last_scan_at, last_error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """)
            params = (user_id, email, last_history_id, watch_expiration, last_watch_at, last_scan_at, last_error, now)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

    # ------------------------------------------------------------------
    # ERP connections
    # ------------------------------------------------------------------

    def save_erp_connection(
        self,
        organization_id: str,
        erp_type: str,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        realm_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        base_url: Optional[str] = None,
        credentials: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        import uuid
        connection_id = f"ERP-{uuid.uuid4().hex}"
        credentials_json = json.dumps(credentials) if credentials else None

        if self.use_postgres:
            sql = self._prepare_sql("""
                INSERT INTO erp_connections
                (id, organization_id, erp_type, access_token, refresh_token, realm_id, tenant_id, base_url,
                 credentials, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT (organization_id, erp_type)
                DO UPDATE SET access_token = EXCLUDED.access_token,
                              refresh_token = EXCLUDED.refresh_token,
                              realm_id = EXCLUDED.realm_id,
                              tenant_id = EXCLUDED.tenant_id,
                              base_url = EXCLUDED.base_url,
                              credentials = EXCLUDED.credentials,
                              is_active = 1,
                              updated_at = EXCLUDED.updated_at
            """)
            params = (connection_id, organization_id, erp_type, access_token, refresh_token, realm_id,
                      tenant_id, base_url, credentials_json, now, now)
        else:
            sql = self._prepare_sql("""
                INSERT OR REPLACE INTO erp_connections
                (id, organization_id, erp_type, access_token, refresh_token, realm_id, tenant_id, base_url,
                 credentials, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """)
            params = (connection_id, organization_id, erp_type, access_token, refresh_token, realm_id,
                      tenant_id, base_url, credentials_json, now, now)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

    def get_erp_connections(self, organization_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM erp_connections WHERE organization_id = ? AND is_active = 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def delete_erp_connection(self, organization_id: str, erp_type: str) -> bool:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = self._prepare_sql(
            "UPDATE erp_connections SET is_active = 0, updated_at = ? WHERE organization_id = ? AND erp_type = ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, organization_id, erp_type))
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # AP items
    # ------------------------------------------------------------------

    def create_ap_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        import uuid
        now = datetime.now(timezone.utc).isoformat()
        item_id = payload.get("id") or f"AP-{uuid.uuid4().hex}"
        metadata = json.dumps(payload.get("metadata") or {})
        sql = self._prepare_sql("""
            INSERT INTO ap_items
            (id, invoice_key, thread_id, message_id, subject, sender, vendor_name, amount, currency,
             invoice_number, invoice_date, due_date, state, confidence, approval_required,
             approved_by, approved_at, rejected_by, rejected_at, rejection_reason, erp_reference,
             erp_posted_at, workflow_id, run_id, approval_surface, approval_policy_version, post_attempted_at,
             last_error, organization_id, user_id, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        values = (
            item_id,
            payload.get("invoice_key"),
            payload.get("thread_id"),
            payload.get("message_id"),
            payload.get("subject"),
            payload.get("sender"),
            payload.get("vendor_name"),
            payload.get("amount"),
            payload.get("currency") or "USD",
            payload.get("invoice_number"),
            payload.get("invoice_date"),
            payload.get("due_date"),
            payload.get("state"),
            payload.get("confidence") or 0,
            1 if payload.get("approval_required", True) else 0,
            payload.get("approved_by"),
            payload.get("approved_at"),
            payload.get("rejected_by"),
            payload.get("rejected_at"),
            payload.get("rejection_reason"),
            payload.get("erp_reference"),
            payload.get("erp_posted_at"),
            payload.get("workflow_id"),
            payload.get("run_id"),
            payload.get("approval_surface") or "hybrid",
            payload.get("approval_policy_version"),
            payload.get("post_attempted_at"),
            payload.get("last_error"),
            payload.get("organization_id"),
            payload.get("user_id"),
            now,
            now,
            metadata,
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, values)
            conn.commit()
        return self.get_ap_item(item_id)

    def update_ap_item(self, ap_item_id: str, **kwargs) -> bool:
        self.initialize()
        if not kwargs:
            return False
        now = datetime.now(timezone.utc).isoformat()
        kwargs["updated_at"] = now
        if "metadata" in kwargs and isinstance(kwargs["metadata"], dict):
            kwargs["metadata"] = json.dumps(kwargs["metadata"])  # type: ignore
        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        sql = self._prepare_sql(f"UPDATE ap_items SET {set_clause} WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*kwargs.values(), ap_item_id))
            conn.commit()
            return cur.rowcount > 0

    def get_ap_item(self, ap_item_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM ap_items WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_ap_item_by_invoice_key(self, organization_id: str, invoice_key: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE organization_id = ? AND invoice_key = ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, invoice_key))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_ap_items_by_invoice_key_prefix(
        self, organization_id: str, invoice_key_prefix: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        self.initialize()
        prefix = invoice_key_prefix.replace("%", "\\%").replace("_", "\\_")
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE organization_id = ? AND invoice_key LIKE ? ESCAPE '\\' "
            "ORDER BY created_at DESC LIMIT ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, f"{prefix}%", limit))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def get_ap_item_by_vendor_invoice(
        self, organization_id: str, vendor_name: str, invoice_number: str
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE organization_id = ? AND vendor_name = ? AND invoice_number = ? "
            "ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, vendor_name, invoice_number))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_rejected_ap_item_by_vendor_invoice(
        self, organization_id: str, vendor_name: str, invoice_number: str
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE organization_id = ? AND vendor_name = ? AND invoice_number = ? "
            "AND state = 'rejected' ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, vendor_name, invoice_number))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_ap_item_by_thread(self, organization_id: str, thread_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            """
            SELECT * FROM ap_items
            WHERE organization_id = ?
              AND (
                thread_id = ?
                OR id IN (
                  SELECT ap_item_id
                  FROM ap_item_sources
                  WHERE source_type = 'gmail_thread' AND source_ref = ?
                )
              )
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, thread_id, thread_id))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_ap_item_by_message_id(self, organization_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            """
            SELECT * FROM ap_items
            WHERE organization_id = ?
              AND (
                message_id = ?
                OR id IN (
                  SELECT ap_item_id
                  FROM ap_item_sources
                  WHERE source_type = 'gmail_message' AND source_ref = ?
                )
              )
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, message_id, message_id))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_ap_item_by_workflow_id(self, organization_id: str, workflow_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM ap_items WHERE organization_id = ? AND workflow_id = ? "
            "ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, workflow_id))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_ap_items_by_thread(self, organization_id: str, thread_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            """
            SELECT * FROM ap_items
            WHERE organization_id = ?
              AND (
                thread_id = ?
                OR id IN (
                  SELECT ap_item_id
                  FROM ap_item_sources
                  WHERE source_type = 'gmail_thread' AND source_ref = ?
                )
              )
            ORDER BY created_at DESC
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, thread_id, thread_id))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _decode_json(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _exception_severity_rank(value: Any) -> int:
        severity = str(value or "").strip().lower()
        if severity == "critical":
            return 4
        if severity == "high":
            return 3
        if severity == "medium":
            return 2
        if severity == "low":
            return 1
        return 0

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _worklist_priority_score(self, item: Dict[str, Any]) -> float:
        metadata = self._decode_json(item.get("metadata"))
        explicit = metadata.get("priority_score")
        if explicit is not None:
            return self._safe_float(explicit, 0.0)

        severity_rank = self._exception_severity_rank(
            metadata.get("exception_severity") or item.get("exception_severity")
        )
        score = float(severity_rank * 100)

        state = str(item.get("state") or "").strip().lower()
        if state == "failed_post":
            score += 45.0
        elif state == "needs_info":
            score += 40.0
        elif state == "needs_approval":
            score += 30.0
        elif state == "approved":
            score += 20.0

        due_date = self._parse_iso(item.get("due_date"))
        if due_date:
            now = datetime.now(timezone.utc)
            hours_to_due = (due_date - now).total_seconds() / 3600.0
            if hours_to_due <= 24:
                score += 25.0
            elif hours_to_due <= 72:
                score += 10.0
        return score

    def _worklist_sort_key(self, item: Dict[str, Any]) -> tuple:
        metadata = self._decode_json(item.get("metadata"))
        severity_rank = self._exception_severity_rank(
            metadata.get("exception_severity") or item.get("exception_severity")
        )
        priority_score = self._worklist_priority_score(item)
        created_at = self._parse_iso(item.get("created_at")) or self._parse_iso(item.get("updated_at"))
        created_ts = created_at.timestamp() if created_at else 0.0
        return (-priority_score, -severity_rank, -created_ts)

    def list_ap_items(
        self,
        organization_id: str,
        state: Optional[str] = None,
        limit: int = 200,
        prioritized: bool = False,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        safe_limit = max(1, min(int(limit or 200), 10000))

        if prioritized:
            # Pull a larger window before in-memory priority sort so older high-severity
            # exceptions can surface ahead of recent low-risk items.
            fetch_limit = max(500, safe_limit * 8)
            if state:
                sql = self._prepare_sql(
                    "SELECT * FROM ap_items WHERE organization_id = ? AND state = ? ORDER BY created_at DESC LIMIT ?"
                )
                params = (organization_id, state, fetch_limit)
            else:
                sql = self._prepare_sql(
                    "SELECT * FROM ap_items WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?"
                )
                params = (organization_id, fetch_limit)
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                rows = cur.fetchall()
            items = [dict(row) for row in rows]
            items.sort(key=self._worklist_sort_key)
            return items[:safe_limit]

        if state:
            sql = self._prepare_sql(
                "SELECT * FROM ap_items WHERE organization_id = ? AND state = ? ORDER BY created_at DESC LIMIT ?"
            )
            params = (organization_id, state, safe_limit)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM ap_items WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?"
            )
            params = (organization_id, safe_limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def list_ap_items_all(self, state: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        self.initialize()
        if state:
            sql = self._prepare_sql(
                "SELECT * FROM ap_items WHERE state = ? ORDER BY created_at DESC LIMIT ?"
            )
            params = (state, limit)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM ap_items ORDER BY created_at DESC LIMIT ?"
            )
            params = (limit,)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def link_ap_item_source(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        source_id = payload.get("id") or f"SRC-{uuid.uuid4().hex}"
        ap_item_id = payload.get("ap_item_id")
        source_type = str(payload.get("source_type") or "").strip()
        source_ref = str(payload.get("source_ref") or "").strip()
        if not source_type or not source_ref:
            raise ValueError("source_type_and_source_ref_required")

        if self.use_postgres:
            sql = self._prepare_sql(
                """
                INSERT INTO ap_item_sources
                (id, ap_item_id, source_type, source_ref, subject, sender, detected_at, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ap_item_id, source_type, source_ref) DO NOTHING
                """
            )
        else:
            sql = self._prepare_sql(
                """
                INSERT OR IGNORE INTO ap_item_sources
                (id, ap_item_id, source_type, source_ref, subject, sender, detected_at, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
            )

        detected_at = payload.get("detected_at") or now
        metadata_json = json.dumps(payload.get("metadata") or {})
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    source_id,
                    ap_item_id,
                    source_type,
                    source_ref,
                    payload.get("subject"),
                    payload.get("sender"),
                    detected_at,
                    metadata_json,
                    now,
                ),
            )
            row_sql = self._prepare_sql(
                "SELECT * FROM ap_item_sources WHERE ap_item_id = ? AND source_type = ? AND source_ref = ? LIMIT 1"
            )
            cur.execute(row_sql, (ap_item_id, source_type, source_ref))
            row = cur.fetchone()
            conn.commit()

        if row:
            data = dict(row)
            raw_metadata = data.get("metadata")
            if isinstance(raw_metadata, str):
                try:
                    data["metadata"] = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    data["metadata"] = {}
            return data

        # Fallback should be unreachable, but preserves prior return contract.
        return {
            "id": source_id,
            "ap_item_id": ap_item_id,
            "source_type": source_type,
            "source_ref": source_ref,
            "subject": payload.get("subject"),
            "sender": payload.get("sender"),
            "detected_at": detected_at,
            "metadata": payload.get("metadata") or {},
            "created_at": now,
        }

    def list_ap_item_sources(self, ap_item_id: str, source_type: Optional[str] = None) -> List[Dict[str, Any]]:
        self.initialize()
        if source_type:
            sql = self._prepare_sql(
                "SELECT * FROM ap_item_sources WHERE ap_item_id = ? AND source_type = ? ORDER BY detected_at ASC, created_at ASC"
            )
            params = (ap_item_id, source_type)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM ap_item_sources WHERE ap_item_id = ? ORDER BY detected_at ASC, created_at ASC"
            )
            params = (ap_item_id,)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        results: List[Dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            meta = data.get("metadata")
            if isinstance(meta, str):
                try:
                    data["metadata"] = json.loads(meta)
                except json.JSONDecodeError:
                    data["metadata"] = {}
            results.append(data)
        return results

    def list_ap_item_sources_by_ref(self, source_type: str, source_ref: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM ap_item_sources WHERE source_type = ? AND source_ref = ? ORDER BY created_at DESC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (source_type, source_ref))
            rows = cur.fetchall()
        results: List[Dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            meta = data.get("metadata")
            if isinstance(meta, str):
                try:
                    data["metadata"] = json.loads(meta)
                except json.JSONDecodeError:
                    data["metadata"] = {}
            results.append(data)
        return results

    def unlink_ap_item_source(self, ap_item_id: str, source_type: str, source_ref: str) -> bool:
        self.initialize()
        sql = self._prepare_sql(
            "DELETE FROM ap_item_sources WHERE ap_item_id = ? AND source_type = ? AND source_ref = ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id, source_type, source_ref))
            conn.commit()
            return cur.rowcount > 0

    def move_ap_item_source(
        self,
        from_ap_item_id: str,
        to_ap_item_id: str,
        source_type: str,
        source_ref: str,
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        source_type = str(source_type or "").strip()
        source_ref = str(source_ref or "").strip()
        if not source_type or not source_ref:
            return None

        current_rows = self.list_ap_item_sources(from_ap_item_id, source_type=source_type)
        current = next((row for row in current_rows if row.get("source_ref") == source_ref), None)
        if not current:
            return None

        moved = self.link_ap_item_source(
            {
                "ap_item_id": to_ap_item_id,
                "source_type": source_type,
                "source_ref": source_ref,
                "subject": current.get("subject"),
                "sender": current.get("sender"),
                "detected_at": current.get("detected_at"),
                "metadata": current.get("metadata") or {},
            }
        )
        self.unlink_ap_item_source(from_ap_item_id, source_type, source_ref)
        return moved

    def upsert_ap_item_context_cache(self, ap_item_id: str, context_json: Dict[str, Any]) -> None:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()

        if self.use_postgres:
            sql = self._prepare_sql(
                """
                INSERT INTO ap_item_context_cache (ap_item_id, context_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT (ap_item_id)
                DO UPDATE SET context_json = EXCLUDED.context_json, updated_at = EXCLUDED.updated_at
                """
            )
        else:
            sql = self._prepare_sql(
                """
                INSERT OR REPLACE INTO ap_item_context_cache (ap_item_id, context_json, updated_at)
                VALUES (?, ?, ?)
                """
            )

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id, json.dumps(context_json or {}), now))
            conn.commit()

    def get_ap_item_context_cache(self, ap_item_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM ap_item_context_cache WHERE ap_item_id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        raw = data.get("context_json")
        if isinstance(raw, str):
            try:
                data["context_json"] = json.loads(raw)
            except json.JSONDecodeError:
                data["context_json"] = {}
        return data

    def list_organizations_with_ap_items(self) -> List[str]:
        self.initialize()
        sql = "SELECT DISTINCT organization_id FROM ap_items WHERE organization_id IS NOT NULL AND organization_id != ''"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
        orgs = []
        for row in rows:
            if isinstance(row, dict):
                org = row.get("organization_id")
            elif isinstance(row, sqlite3.Row):
                org = row["organization_id"]
            else:
                org = row[0] if row else None
            if org:
                orgs.append(str(org))
        return orgs

    def _parse_iso(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                # Treat legacy naive timestamps as UTC for consistent comparisons.
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _percentile(values: List[float], percentile: float) -> Optional[float]:
        if not values:
            return None
        ordered = sorted(values)
        safe = max(0.0, min(1.0, float(percentile)))
        idx = max(0, min(len(ordered) - 1, int(round(safe * (len(ordered) - 1)))))
        return ordered[idx]

    @staticmethod
    def _p95(values: List[float]) -> Optional[float]:
        return ClearledgrDB._percentile(values, 0.95)

    def list_audit_events(
        self,
        organization_id: str,
        event_types: Optional[List[str]] = None,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        params: List[Any] = [organization_id]
        sql = "SELECT * FROM audit_events WHERE organization_id = ?"
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            sql += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        sql = self._prepare_sql(sql)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [self._deserialize_audit_event(dict(row)) for row in rows]

    def get_operational_metrics(
        self,
        organization_id: str,
        approval_sla_minutes: int = 240,
        workflow_stuck_minutes: int = 120,
    ) -> Dict[str, Any]:
        self.initialize()
        now = datetime.now(timezone.utc)
        items = self.list_ap_items(organization_id, limit=5000)
        approvals = self.list_approvals(organization_id, status="approved", limit=5000)
        post_events = self.list_audit_events(
            organization_id,
            event_types=["erp_post_attempted", "erp_post_failed"],
            limit=10000,
        )
        callback_events = self.list_audit_events(
            organization_id,
            event_types=["approval_callback_rejected"],
            limit=10000,
        )

        state_counts: Dict[str, int] = {}
        open_states = {"received", "validated", "needs_info", "needs_approval", "approved", "ready_to_post", "failed_post"}
        queue_lags: List[float] = []
        sla_breached_open = 0
        workflow_stuck_count = 0

        for item in items:
            state = str(item.get("state") or "received")
            state_counts[state] = state_counts.get(state, 0) + 1
            if state not in open_states:
                continue
            created_at = self._parse_iso(item.get("created_at")) or self._parse_iso(item.get("updated_at"))
            if not created_at:
                continue
            lag_min = max(0.0, (now - created_at).total_seconds() / 60.0)
            queue_lags.append(lag_min)
            if state == "needs_approval" and lag_min >= approval_sla_minutes:
                sla_breached_open += 1
            if lag_min >= workflow_stuck_minutes:
                workflow_stuck_count += 1

        approval_latencies: List[float] = []
        for approval in approvals:
            created_at = self._parse_iso(approval.get("created_at"))
            approved_at = self._parse_iso(approval.get("approved_at"))
            if not created_at or not approved_at:
                continue
            latency_min = (approved_at - created_at).total_seconds() / 60.0
            if latency_min >= 0:
                approval_latencies.append(latency_min)

        cutoff = now - timedelta(hours=24)
        attempted_24h = 0
        failed_24h = 0
        for event in post_events:
            ts = self._parse_iso(event.get("ts"))
            if not ts or ts < cutoff:
                continue
            if event.get("event_type") == "erp_post_attempted":
                attempted_24h += 1
            elif event.get("event_type") == "erp_post_failed":
                failed_24h += 1

        failure_rate_24h = (failed_24h / attempted_24h) if attempted_24h else 0.0
        callback_verification_failures_24h = 0
        for event in callback_events:
            ts = self._parse_iso(event.get("ts"))
            if not ts or ts < cutoff:
                continue
            callback_verification_failures_24h += 1

        return {
            "organization_id": organization_id,
            "generated_at": now.isoformat(),
            "states": state_counts,
            "queue_lag": {
                "open_items": len(queue_lags),
                "avg_minutes": round(sum(queue_lags) / len(queue_lags), 2) if queue_lags else 0.0,
                "max_minutes": round(max(queue_lags), 2) if queue_lags else 0.0,
                "p95_minutes": round(self._p95(queue_lags) or 0.0, 2),
            },
            "approval_latency": {
                "approved_count": len(approval_latencies),
                "avg_minutes": round(sum(approval_latencies) / len(approval_latencies), 2) if approval_latencies else 0.0,
                "p95_minutes": round(self._p95(approval_latencies) or 0.0, 2),
                "sla_minutes": int(approval_sla_minutes),
                "sla_breached_open_count": int(sla_breached_open),
            },
            "posting": {
                "attempted_24h": attempted_24h,
                "failed_24h": failed_24h,
                "failure_rate_24h": round(failure_rate_24h, 4),
            },
            "post_failure_rate": {
                "attempted_24h": attempted_24h,
                "failed_24h": failed_24h,
                "rate_24h": round(failure_rate_24h, 4),
            },
            "callback_verification_failures": {
                "window_hours": 24,
                "count": callback_verification_failures_24h,
            },
            "workflow_stuck_count": {
                "threshold_minutes": int(workflow_stuck_minutes),
                "count": int(workflow_stuck_count),
            },
        }

    def get_ap_kpis(
        self,
        organization_id: str,
        approval_sla_minutes: int = 240,
    ) -> Dict[str, Any]:
        self.initialize()
        now = datetime.now(timezone.utc)
        items = self.list_ap_items(organization_id, limit=10000)
        approvals = self.list_approvals(organization_id, limit=10000)

        approvals_by_item: Dict[str, List[Dict[str, Any]]] = {}
        for approval in approvals:
            ap_item_id = str(approval.get("ap_item_id") or "")
            if not ap_item_id:
                continue
            approvals_by_item.setdefault(ap_item_id, []).append(approval)

        completed_states = {"closed", "posted_to_erp"}
        completed_items = [item for item in items if str(item.get("state") or "") in completed_states]
        touchless_eligible = len(completed_items)
        touchless_count = 0
        cycle_times_hours: List[float] = []
        exception_count = 0
        discount_candidate_count = 0
        missed_discount_count = 0
        missed_discount_value = 0.0

        for item in items:
            metadata = self._decode_json(item.get("metadata"))
            item_id = str(item.get("id") or "")
            item_approvals = approvals_by_item.get(item_id, [])
            approval_required = bool(item.get("approval_required"))
            if str(item.get("state") or "") in completed_states:
                if (not approval_required) or not item_approvals:
                    touchless_count += 1
                created_at = self._parse_iso(item.get("created_at")) or self._parse_iso(item.get("updated_at"))
                completed_at = (
                    self._parse_iso(item.get("erp_posted_at"))
                    or self._parse_iso(item.get("updated_at"))
                    or now
                )
                if created_at and completed_at and completed_at >= created_at:
                    cycle_times_hours.append((completed_at - created_at).total_seconds() / 3600.0)

            if metadata.get("exception_code"):
                exception_count += 1

            discount = metadata.get("discount") or metadata.get("payment_discount") or {}
            if isinstance(discount, dict) and (
                discount.get("available") is True
                or discount.get("eligible") is True
                or discount.get("amount")
            ):
                discount_candidate_count += 1
                taken = bool(discount.get("taken"))
                deadline = self._parse_iso(discount.get("deadline") or discount.get("due_at"))
                missed = (not taken) and (
                    deadline is None
                    or deadline <= now
                    or str(item.get("state") or "") in completed_states
                )
                if missed:
                    missed_discount_count += 1
                    missed_discount_value += max(0.0, self._safe_float(discount.get("amount"), 0.0))

        approved_records = [record for record in approvals if str(record.get("status") or "") == "approved"]
        on_time_count = 0
        approval_latencies_hours: List[float] = []
        for approval in approved_records:
            created_at = self._parse_iso(approval.get("created_at"))
            approved_at = self._parse_iso(approval.get("approved_at"))
            if not created_at or not approved_at or approved_at < created_at:
                continue
            latency_hours = (approved_at - created_at).total_seconds() / 3600.0
            approval_latencies_hours.append(latency_hours)
            if latency_hours * 60.0 <= approval_sla_minutes:
                on_time_count += 1

        # Approval friction metrics (handoffs + wait + SLA breach pressure).
        handoff_counts: List[float] = []
        approval_wait_minutes: List[float] = []
        approval_population = 0
        sla_breach_count = 0
        channel_distribution: Dict[str, int] = {}

        for item in items:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue

            item_approvals = approvals_by_item.get(item_id, [])
            needs_approval = bool(item.get("approval_required")) or bool(item_approvals)
            if not needs_approval:
                continue

            approval_population += 1

            if item_approvals:
                ordered = sorted(
                    item_approvals,
                    key=lambda entry: (
                        self._parse_iso(entry.get("created_at")) or datetime.fromtimestamp(0, tz=timezone.utc)
                    ),
                )
                channel_path: List[str] = []
                for entry in ordered:
                    channel = str(entry.get("source_channel") or entry.get("channel_id") or "unknown").strip()
                    if channel:
                        channel_distribution[channel] = channel_distribution.get(channel, 0) + 1
                        if not channel_path or channel_path[-1] != channel:
                            channel_path.append(channel)

                    created_at = self._parse_iso(entry.get("created_at"))
                    resolved_at = (
                        self._parse_iso(entry.get("approved_at"))
                        or self._parse_iso(entry.get("rejected_at"))
                    )
                    if created_at and resolved_at and resolved_at >= created_at:
                        approval_wait_minutes.append((resolved_at - created_at).total_seconds() / 60.0)

                handoff_counts.append(float(max(0, len(channel_path) - 1)))

                latest = ordered[-1]
                latest_created = self._parse_iso(latest.get("created_at"))
                latest_resolved = (
                    self._parse_iso(latest.get("approved_at"))
                    or self._parse_iso(latest.get("rejected_at"))
                )
                anchor = latest_resolved or now
                if latest_created and anchor and (anchor - latest_created).total_seconds() / 60.0 > approval_sla_minutes:
                    sla_breach_count += 1
            else:
                handoff_counts.append(0.0)
                created_at = self._parse_iso(item.get("created_at")) or self._parse_iso(item.get("updated_at"))
                if created_at:
                    open_wait = max(0.0, (now - created_at).total_seconds() / 60.0)
                    approval_wait_minutes.append(open_wait)
                    if str(item.get("state") or "") == "needs_approval" and open_wait > approval_sla_minutes:
                        sla_breach_count += 1

        total_items = len(items)
        return {
            "organization_id": organization_id,
            "generated_at": now.isoformat(),
            "totals": {
                "items": total_items,
                "completed_items": touchless_eligible,
                "approved_records": len(approved_records),
            },
            "touchless_rate": {
                "eligible_count": touchless_eligible,
                "touchless_count": touchless_count,
                "rate": round((touchless_count / touchless_eligible) if touchless_eligible else 0.0, 4),
            },
            "cycle_time_hours": {
                "count": len(cycle_times_hours),
                "avg": round(sum(cycle_times_hours) / len(cycle_times_hours), 2) if cycle_times_hours else 0.0,
                "median": round(self._percentile(cycle_times_hours, 0.5) or 0.0, 2),
                "p95": round(self._p95(cycle_times_hours) or 0.0, 2),
            },
            "exception_rate": {
                "exception_count": exception_count,
                "rate": round((exception_count / total_items) if total_items else 0.0, 4),
            },
            "on_time_approvals": {
                "sla_minutes": int(approval_sla_minutes),
                "approved_count": len(approved_records),
                "on_time_count": on_time_count,
                "rate": round((on_time_count / len(approved_records)) if approved_records else 0.0, 4),
                "avg_latency_hours": round(sum(approval_latencies_hours) / len(approval_latencies_hours), 2)
                if approval_latencies_hours
                else 0.0,
            },
            "missed_discounts_baseline": {
                "candidate_count": discount_candidate_count,
                "missed_count": missed_discount_count,
                "missed_value": round(missed_discount_value, 2),
            },
            "approval_friction": {
                "population_count": int(approval_population),
                "avg_handoffs": round(sum(handoff_counts) / len(handoff_counts), 2) if handoff_counts else 0.0,
                "max_handoffs": int(max(handoff_counts) if handoff_counts else 0),
                "avg_wait_minutes": round(sum(approval_wait_minutes) / len(approval_wait_minutes), 2)
                if approval_wait_minutes
                else 0.0,
                "p95_wait_minutes": round(self._p95(approval_wait_minutes) or 0.0, 2),
                "sla_minutes": int(approval_sla_minutes),
                "sla_breach_count": int(sla_breach_count),
                "sla_breach_rate": round(
                    (sla_breach_count / approval_population) if approval_population else 0.0,
                    4,
                ),
                "channel_distribution": channel_distribution,
            },
        }

    def get_browser_agent_metrics(
        self,
        organization_id: str,
        window_hours: int = 24,
    ) -> Dict[str, Any]:
        self.initialize()
        now = datetime.now(timezone.utc)
        safe_window_hours = max(1, int(window_hours or 24))
        window_start = now - timedelta(hours=safe_window_hours)

        sql = self._prepare_sql(
            "SELECT * FROM browser_action_events WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?"
        )
        audit_sql = self._prepare_sql(
            "SELECT event_type, ts FROM audit_events WHERE organization_id = ? "
            "AND event_type IN ('erp_api_attempt', 'erp_api_success', 'erp_api_fallback_requested', 'erp_api_failed') "
            "ORDER BY ts DESC LIMIT ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, 5000))
            rows = cur.fetchall()
            cur.execute(audit_sql, (organization_id, 5000))
            audit_rows = cur.fetchall()

        status_counts: Dict[str, int] = {}
        tool_usage: Dict[str, int] = {}
        failure_reasons: Dict[str, int] = {}
        session_steps: Dict[str, int] = {}
        latencies: List[float] = []
        confirmation_required = 0
        high_risk_count = 0
        total_events = 0

        high_risk_fallback_tools = {"click", "type", "select", "open_tab", "upload_file", "drag_drop"}

        for raw_row in rows:
            row = self._deserialize_browser_action_event(dict(raw_row))
            ts = self._parse_iso(row.get("updated_at") or row.get("created_at"))
            if ts and ts < window_start:
                continue

            total_events += 1
            status = str(row.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1

            tool_name = str(row.get("tool_name") or "unknown")
            tool_usage[tool_name] = tool_usage.get(tool_name, 0) + 1

            session_id = str(row.get("session_id") or "")
            if session_id:
                session_steps[session_id] = session_steps.get(session_id, 0) + 1

            if bool(row.get("requires_confirmation")):
                confirmation_required += 1

            request_payload = row.get("request_payload") or {}
            if not isinstance(request_payload, dict):
                request_payload = {}
            tool_risk = str(request_payload.get("tool_risk") or "").strip().lower()
            if not tool_risk and tool_name in high_risk_fallback_tools:
                tool_risk = "high_risk"
            if tool_risk == "high_risk":
                high_risk_count += 1

            result_payload = row.get("result_payload") or {}
            if not isinstance(result_payload, dict):
                result_payload = {}
            if status in {"failed", "denied_policy"}:
                reason = (
                    str(result_payload.get("error") or "")
                    or str(row.get("policy_reason") or "")
                    or "unknown"
                )
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

            if status in {"completed", "failed"}:
                created_at = self._parse_iso(row.get("created_at"))
                updated_at = self._parse_iso(row.get("updated_at"))
                if created_at and updated_at and updated_at >= created_at:
                    latencies.append((updated_at - created_at).total_seconds())

        completed = status_counts.get("completed", 0)
        failed = status_counts.get("failed", 0)
        denied = status_counts.get("denied_policy", 0)
        terminal_count = completed + failed + denied
        session_step_values = [float(count) for count in session_steps.values()]

        routing_counts: Dict[str, int] = {
            "erp_api_attempt": 0,
            "erp_api_success": 0,
            "erp_api_fallback_requested": 0,
            "erp_api_failed": 0,
        }
        for raw_row in audit_rows:
            row = dict(raw_row)
            ts = self._parse_iso(row.get("ts"))
            if ts and ts < window_start:
                continue
            event_type = str(row.get("event_type") or "")
            if event_type in routing_counts:
                routing_counts[event_type] = routing_counts.get(event_type, 0) + 1

        attempt_count = int(routing_counts.get("erp_api_attempt") or 0)
        api_success_count = int(routing_counts.get("erp_api_success") or 0)
        fallback_requested_count = int(routing_counts.get("erp_api_fallback_requested") or 0)
        api_failed_count = int(routing_counts.get("erp_api_failed") or 0)

        return {
            "organization_id": organization_id,
            "generated_at": now.isoformat(),
            "window_hours": safe_window_hours,
            "window_start": window_start.isoformat(),
            "totals": {
                "events": int(total_events),
                "sessions": int(len(session_steps)),
                "terminal_events": int(terminal_count),
            },
            "status_counts": status_counts,
            "tool_usage": tool_usage,
            "policy": {
                "confirmation_required_count": int(confirmation_required),
                "high_risk_count": int(high_risk_count),
                "denied_policy_count": int(denied),
            },
            "execution": {
                "success_rate": round((completed / terminal_count) if terminal_count else 0.0, 4),
                "avg_steps_per_session": round(
                    (sum(session_step_values) / len(session_step_values)) if session_step_values else 0.0,
                    2,
                ),
                "p95_steps_per_session": round(self._p95(session_step_values) or 0.0, 2),
                "avg_latency_seconds": round((sum(latencies) / len(latencies)) if latencies else 0.0, 2),
                "p95_latency_seconds": round(self._p95(latencies) or 0.0, 2),
            },
            "api_first_routing": {
                "attempt_count": attempt_count,
                "api_success_count": api_success_count,
                "fallback_requested_count": fallback_requested_count,
                "api_failed_count": api_failed_count,
                "api_success_rate": round((api_success_count / attempt_count) if attempt_count else 0.0, 4),
                "fallback_rate": round((fallback_requested_count / attempt_count) if attempt_count else 0.0, 4),
            },
            "failure_reasons": failure_reasons,
        }

    # ------------------------------------------------------------------
    # Audit events
    # ------------------------------------------------------------------

    def append_ap_audit_event(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.initialize()
        import uuid
        now = payload.get("ts") or datetime.now(timezone.utc).isoformat()
        event_id = payload.get("id") or f"EVT-{uuid.uuid4().hex}"

        if payload.get("idempotency_key"):
            existing = self.get_ap_audit_event_by_key(payload.get("idempotency_key"))
            if existing:
                return existing

        payload_json = payload.get("payload_json")
        if payload_json is None:
            payload_json = {}
            reason = payload.get("reason")
            if reason:
                payload_json["reason"] = reason
            metadata = payload.get("metadata") or {}
            if isinstance(metadata, dict):
                payload_json.update(metadata)
        external_refs = payload.get("external_refs") or {}

        sql = self._prepare_sql("""
            INSERT INTO audit_events
            (id, ap_item_id, event_type, prev_state, new_state, actor_type, actor_id,
             payload_json, external_refs, idempotency_key, source, correlation_id, workflow_id, run_id,
             decision_reason, organization_id, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                event_id,
                payload.get("ap_item_id"),
                payload.get("event_type"),
                payload.get("from_state"),
                payload.get("to_state"),
                payload.get("actor_type"),
                payload.get("actor_id"),
                json.dumps(payload_json or {}),
                json.dumps(external_refs or {}),
                payload.get("idempotency_key"),
                payload.get("source"),
                payload.get("correlation_id"),
                payload.get("workflow_id"),
                payload.get("run_id"),
                payload.get("decision_reason") or payload.get("reason"),
                payload.get("organization_id"),
                now,
            ))
            conn.commit()
        return self.get_ap_audit_event(event_id)

    def get_ap_audit_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM audit_events WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (event_id,))
            row = cur.fetchone()
        return self._deserialize_audit_event(dict(row)) if row else None

    def get_ap_audit_event_by_key(self, idempotency_key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM audit_events WHERE idempotency_key = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (idempotency_key,))
            row = cur.fetchone()
        return self._deserialize_audit_event(dict(row)) if row else None

    def list_ap_audit_events(self, ap_item_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM audit_events WHERE ap_item_id = ? ORDER BY ts ASC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            rows = cur.fetchall()
        return [self._deserialize_audit_event(dict(row)) for row in rows]

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------

    def save_approval(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        import uuid
        now = datetime.now(timezone.utc).isoformat()
        approval_id = payload.get("id") or f"APR-{uuid.uuid4().hex}"

        if self.use_postgres:
            sql = self._prepare_sql("""
                INSERT INTO approvals
                (id, ap_item_id, channel_id, message_ts, source_channel, source_message_ref,
                 decision_idempotency_key, decision_payload, status, approved_by, approved_at,
                 rejected_by, rejected_at, rejection_reason, organization_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ap_item_id, channel_id, message_ts)
                DO UPDATE SET status = EXCLUDED.status,
                              source_channel = EXCLUDED.source_channel,
                              source_message_ref = EXCLUDED.source_message_ref,
                              decision_idempotency_key = EXCLUDED.decision_idempotency_key,
                              decision_payload = EXCLUDED.decision_payload,
                              approved_by = EXCLUDED.approved_by,
                              approved_at = EXCLUDED.approved_at,
                              rejected_by = EXCLUDED.rejected_by,
                              rejected_at = EXCLUDED.rejected_at,
                              rejection_reason = EXCLUDED.rejection_reason
            """)
        else:
            sql = self._prepare_sql("""
                INSERT OR REPLACE INTO approvals
                (id, ap_item_id, channel_id, message_ts, source_channel, source_message_ref,
                 decision_idempotency_key, decision_payload, status, approved_by, approved_at,
                 rejected_by, rejected_at, rejection_reason, organization_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                approval_id,
                payload.get("ap_item_id"),
                payload.get("channel_id"),
                payload.get("message_ts"),
                payload.get("source_channel"),
                payload.get("source_message_ref"),
                payload.get("decision_idempotency_key"),
                json.dumps(payload.get("decision_payload") or {}),
                payload.get("status") or "pending",
                payload.get("approved_by"),
                payload.get("approved_at"),
                payload.get("rejected_by"),
                payload.get("rejected_at"),
                payload.get("rejection_reason"),
                payload.get("organization_id"),
                payload.get("created_at") or now,
            ))
            conn.commit()
        return {"id": approval_id, **payload}

    def get_latest_approval(self, ap_item_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM approvals WHERE ap_item_id = ? ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_approval_by_decision_key(self, ap_item_id: str, decision_idempotency_key: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM approvals WHERE ap_item_id = ? AND decision_idempotency_key = ? ORDER BY created_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id, decision_idempotency_key))
            row = cur.fetchone()
        return dict(row) if row else None

    def update_approval_status(
        self,
        ap_item_id: str,
        status: str,
        approved_by: Optional[str] = None,
        approved_at: Optional[str] = None,
        rejected_by: Optional[str] = None,
        rejected_at: Optional[str] = None,
        rejection_reason: Optional[str] = None,
    ) -> None:
        self.initialize()
        latest = self.get_latest_approval(ap_item_id)
        if not latest:
            return
        sql = self._prepare_sql(
            """
            UPDATE approvals
            SET status = ?, approved_by = ?, approved_at = ?, rejected_by = ?,
                rejected_at = ?, rejection_reason = ?
            WHERE id = ?
            """
        )
        params = (
            status,
            approved_by,
            approved_at,
            rejected_by,
            rejected_at,
            rejection_reason,
            latest["id"],
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

    def list_approvals(self, organization_id: str, status: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        self.initialize()
        if status:
            sql = self._prepare_sql(
                "SELECT * FROM approvals WHERE organization_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?"
            )
            params = (organization_id, status, limit)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM approvals WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?"
            )
            params = (organization_id, limit)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def list_approvals_by_item(self, ap_item_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM approvals WHERE ap_item_id = ? ORDER BY created_at DESC LIMIT ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (ap_item_id, limit))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def list_ap_audit_events_by_thread(self, organization_id: str, thread_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            """
            SELECT ae.* FROM audit_events ae
            JOIN ap_items ai ON ae.ap_item_id = ai.id
            WHERE ai.organization_id = ? AND ai.thread_id = ?
            ORDER BY ae.ts ASC
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, thread_id))
            rows = cur.fetchall()
        return [self._deserialize_audit_event(dict(row)) for row in rows]

    def _deserialize_audit_event(self, row: Dict[str, Any]) -> Dict[str, Any]:
        payload = row.get("payload_json")
        refs = row.get("external_refs")
        if isinstance(payload, str):
            try:
                row["payload_json"] = json.loads(payload)
            except json.JSONDecodeError:
                row["payload_json"] = {}
        if isinstance(refs, str):
            try:
                row["external_refs"] = json.loads(refs)
            except json.JSONDecodeError:
                row["external_refs"] = {}
        if "prev_state" in row and "from_state" not in row:
            row["from_state"] = row.get("prev_state")
        if "new_state" in row and "to_state" not in row:
            row["to_state"] = row.get("new_state")
        return row

    # ------------------------------------------------------------------
    # Browser agent sessions, actions, and policies
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

    def list_ap_policy_versions(
        self,
        organization_id: str,
        policy_name: str = "ap_business_v1",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM ap_policy_versions WHERE organization_id = ? AND policy_name = ? "
            "ORDER BY version DESC LIMIT ?"
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
        sql = self._prepare_sql(
            "SELECT * FROM ap_policy_versions WHERE organization_id = ? AND policy_name = ? "
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

        sql = self._prepare_sql(
            """
            INSERT INTO ap_policy_versions
            (id, organization_id, policy_name, version, enabled, config_json, updated_by, created_at)
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
                    version,
                    1 if enabled else 0,
                    json.dumps(config or {}),
                    updated_by,
                    now,
                ),
            )
            conn.commit()

        return self.get_ap_policy(organization_id, policy_name) or {}

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


_DB_INSTANCE: Optional[ClearledgrDB] = None


def get_db() -> ClearledgrDB:
    global _DB_INSTANCE
    if _DB_INSTANCE is None:
        _DB_INSTANCE = ClearledgrDB(db_path=os.getenv("CLEARLEDGR_DB_PATH", "clearledgr.db"))
    return _DB_INSTANCE
