"""
Clearledgr AP v1 Database

Single source of truth for AP items, approvals, audit events, Gmail OAuth tokens,
Gmail autopilot state, and ERP connections.

Domain methods live in ``clearledgr.core.stores.*`` mixins.  This module
provides the shared infrastructure (connection management, schema init,
encryption helpers) and composes the final ``ClearledgrDB`` class via
multiple inheritance.

Threading model
~~~~~~~~~~~~~~~
All DB calls are **synchronous**.  When calling from an ``async`` context
(e.g. a FastAPI route), use ``asyncio.get_event_loop().run_in_executor(None, ...)``
to avoid blocking the event loop.  FastAPI's default thread-pool executor is
sufficient for the expected AP workload.
"""
from __future__ import annotations

import json
import logging
import os
import base64
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from clearledgr.core.utils import safe_float

import psycopg


class HybridRow(dict):
    """Row that supports BOTH column-name access (``row["col"]``) and
    positional access (``row[0]``).

    sqlite3.Row supports both styles natively, and the codebase mixes
    them freely (~60 ``row[0]`` sites vs. ~600 ``row["col"]`` sites).
    psycopg's stock ``dict_row`` returns plain dicts, which raise
    ``KeyError(0)`` on positional access — that was the
    widest-reaching dialect bug under C.1.

    Rather than audit every call site, we install this one row factory
    and let both styles keep working. ``__getitem__`` dispatches on
    the key type: ``int`` → positional, anything else → dict-style.
    Slice access (``row[:2]``) uses the stored values list so e.g.
    3-tuple unpacking (``a, b, c = row``) preserves the column order
    instead of silently unpacking dict *keys*.
    """
    __slots__ = ("_values",)

    def __init__(self, items):
        # items is an iterable of (key, value) pairs from dict_row
        # (we feed it the zipped names-values).
        super().__init__(items)
        self._values = list(self.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        if isinstance(key, slice):
            return self._values[key]
        return super().__getitem__(key)

    def __iter__(self):
        # dict's default __iter__ yields keys; for unpacking
        # (``a, b, c = row``) we want values so tuple-style unpacking
        # matches the positional contract.
        return iter(self._values)


def dict_row(cursor):
    """Row factory that returns HybridRow instances (dict + positional)."""
    desc = cursor.description
    if desc is None:
        return lambda values: values
    names = [c.name for c in desc]
    def make_row(values):
        return HybridRow(zip(names, values))
    return make_row

logger = logging.getLogger(__name__)


AP_RUNTIME_COMPAT_TABLES: tuple[str, ...] = ()
_CLEARLEDGR_DB_IMPL = None
# Process-global pool registry keyed by DSN. See ClearledgrDB.connect()
# for rationale: multiple ClearledgrDB instances targeting the same
# DSN share one pool, instead of each spawning min_size=2 fresh
# connections on first .connect().
_PG_POOLS_BY_DSN: dict = {}


def _close_all_pools_atexit():
    """Close every shared pool cleanly at interpreter exit.

    Without this, psycopg_pool's ConnectionPool.__del__ runs during
    finalization and tries to join its worker threads — which can hit
    PythonFinalizationError("cannot join thread at interpreter
    shutdown") and leave TCP connections in TIME_WAIT against the
    server. That state survives across pytest process boundaries (OS-
    level) and manifests as the mysterious "pytest hangs during
    collection" behaviour on back-to-back runs. Explicitly closing
    the pool while the interpreter is still alive avoids the
    finalizer race entirely.
    """
    for pool in list(_PG_POOLS_BY_DSN.values()):
        try:
            pool.close()
        except Exception:
            pass
    _PG_POOLS_BY_DSN.clear()


import atexit as _atexit
_atexit.register(_close_all_pools_atexit)


def _load_store_symbols() -> None:
    global APStore
    global APRuntimeStore
    global AP_RUNTIME_COMPAT_TABLES
    global ApprovalChainStore
    global AuthStore
    global IntegrationStore
    global MetricsStore
    global PolicyStore
    global TaskStore
    global VendorStore
    global ReconStore
    global EntityStore
    global PaymentStore
    global WebhookStore
    global DisputeStore
    global OverrideWindowStore
    global OnboardingTokenStore
    global PipelineStore
    global PurchaseOrderStore
    global BoxLifecycleStore

    if "APStore" in globals():
        return

    from clearledgr.core.stores.ap_store import APStore as _APStore
    from clearledgr.core.stores.ap_runtime_store import (
        APRuntimeStore as _APRuntimeStore,
        AP_RUNTIME_COMPAT_TABLES as _AP_RUNTIME_COMPAT_TABLES,
    )
    from clearledgr.core.stores.approval_chain_store import ApprovalChainStore as _ApprovalChainStore
    from clearledgr.core.stores.auth_store import AuthStore as _AuthStore
    from clearledgr.core.stores.integration_store import IntegrationStore as _IntegrationStore
    from clearledgr.core.stores.metrics_store import MetricsStore as _MetricsStore
    from clearledgr.core.stores.policy_store import PolicyStore as _PolicyStore
    from clearledgr.core.stores.task_store import TaskStore as _TaskStore
    from clearledgr.core.stores.vendor_store import VendorStore as _VendorStore
    from clearledgr.core.stores.recon_store import ReconStore as _ReconStore
    from clearledgr.core.stores.entity_store import EntityStore as _EntityStore
    from clearledgr.core.stores.payment_store import PaymentStore as _PaymentStore
    from clearledgr.core.stores.webhook_store import WebhookStore as _WebhookStore
    from clearledgr.core.stores.dispute_store import DisputeStore as _DisputeStore
    from clearledgr.core.stores.override_window_store import (
        OverrideWindowStore as _OverrideWindowStore,
    )
    from clearledgr.core.stores.onboarding_token_store import (
        OnboardingTokenStore as _OnboardingTokenStore,
    )
    from clearledgr.core.stores.pipeline_store import PipelineStore as _PipelineStore
    from clearledgr.core.stores.purchase_order_store import (
        PurchaseOrderStore as _PurchaseOrderStore,
    )
    from clearledgr.core.stores.box_lifecycle_store import (
        BoxLifecycleStore as _BoxLifecycleStore,
    )

    APStore = _APStore
    APRuntimeStore = _APRuntimeStore
    AP_RUNTIME_COMPAT_TABLES = _AP_RUNTIME_COMPAT_TABLES
    ApprovalChainStore = _ApprovalChainStore
    AuthStore = _AuthStore
    IntegrationStore = _IntegrationStore
    MetricsStore = _MetricsStore
    PolicyStore = _PolicyStore
    TaskStore = _TaskStore
    VendorStore = _VendorStore
    ReconStore = _ReconStore
    EntityStore = _EntityStore
    PaymentStore = _PaymentStore
    WebhookStore = _WebhookStore
    DisputeStore = _DisputeStore
    OverrideWindowStore = _OverrideWindowStore
    OnboardingTokenStore = _OnboardingTokenStore
    PipelineStore = _PipelineStore
    PurchaseOrderStore = _PurchaseOrderStore
    BoxLifecycleStore = _BoxLifecycleStore


class _ClearledgrDBBase:
    def __init__(self, db_path: str = "clearledgr.db"):
        self.dsn = os.getenv("DATABASE_URL")
        self.db_path = db_path
        if not self.dsn:
            raise RuntimeError(
                "DATABASE_URL is required. Clearledgr no longer supports SQLite."
            )
        dsn_lower = self.dsn.strip().lower()
        if not (dsn_lower.startswith("postgres://") or dsn_lower.startswith("postgresql://")):
            raise RuntimeError(
                f"DATABASE_URL must point at Postgres, got: {self.dsn!r}"
            )
        from clearledgr.core.secrets import require_secret
        self._secret_key = require_secret("CLEARLEDGR_SECRET_KEY")
        self._fernet = None
        self._initialized = False
        self._pg_pool = None

    def _postgres_connect_timeout_seconds(self) -> int:
        raw_value = str(os.getenv("DB_CONNECT_TIMEOUT", "2")).strip()
        try:
            timeout_seconds = int(raw_value)
        except (TypeError, ValueError):
            timeout_seconds = 2
        return max(1, timeout_seconds)

    @contextmanager
    def connect(self):
        connect_timeout = self._postgres_connect_timeout_seconds()
        if self._pg_pool is None:
            # Share a single pool per DSN across all ClearledgrDB
            # instances in the process. Without this, every test
            # fixture that constructs ClearledgrDB directly would
            # spawn its own pool (min_size=2 connections each); a full
            # 2400-test suite then racks up dozens of pools against a
            # PG with max_connections=100 default.
            # Keyed by DSN so prod (single singleton) and tests
            # (many instances, same session DSN) both work right.
            # max_size bumped to 30 so bursts (multiple async tasks +
            # the TRUNCATE fixture's direct connect) don't serialize.
            pool = _PG_POOLS_BY_DSN.get(self.dsn)
            if pool is None:
                try:
                    from psycopg_pool import ConnectionPool
                    pool = ConnectionPool(
                        self.dsn,
                        min_size=2,
                        max_size=int(os.getenv("DB_POOL_MAX_SIZE", "30")),
                        kwargs={
                            "row_factory": dict_row,
                            "connect_timeout": connect_timeout,
                        },
                    )
                    _PG_POOLS_BY_DSN[self.dsn] = pool
                    logger.info("Postgres connection pool initialized (max_size=%s)", os.getenv("DB_POOL_MAX_SIZE", "30"))
                except ImportError:
                    logger.warning("psycopg_pool not installed — using unpooled Postgres connections")
            self._pg_pool = pool
        if self._pg_pool is not None:
            # Pool can hand back a closed/broken connection — in
            # particular after migrations where the pool's idle
            # workers were cancelled. Retry up to 3 times so a
            # single BAD conn doesn't bubble up as a user-visible
            # OperationalError("the connection is closed");
            # psycopg_pool discards bad conns on putconn.
            conn = None
            for _attempt in range(3):
                candidate = self._pg_pool.getconn()
                if candidate.closed:
                    try:
                        self._pg_pool.putconn(candidate)
                    except Exception:
                        try:
                            candidate.close()
                        except Exception:
                            pass
                    continue
                conn = candidate
                break
            if conn is None:
                raise psycopg.OperationalError(
                    "pool returned closed connections on every attempt",
                )
        else:
            conn = psycopg.connect(
                self.dsn,
                row_factory=dict_row,
                connect_timeout=connect_timeout,
            )
        try:
            yield conn
        finally:
            # Defensive autocommit reset before the pool reclaims the
            # connection. The migration runner (and a handful of tests
            # that exec migration bodies directly) flip autocommit=True
            # to avoid the "current transaction is aborted" cascade on
            # idempotent DDL. If a caller forgets to flip it back — or
            # raises mid-block — psycopg_pool happily returns the
            # poisoned conn to the pool. The next consumer's read-
            # modify-write then auto-commits per statement, defeating
            # the rollback semantics that tests like
            # test_audit_insert_failure_rolls_back_state_update assert.
            # The pool's own putconn() rolls back INTRANS connections
            # but does NOT reset autocommit, so we have to do it here.
            try:
                if conn.autocommit:
                    conn.autocommit = False
            except Exception:
                pass
            if self._pg_pool is not None:
                try:
                    self._pg_pool.putconn(conn)
                except Exception:
                    conn.close()
            else:
                conn.close()

    def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> None:
        """Execute a DML/DDL statement and commit. Thin wrapper around connect()."""
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

    def fetchone(self, sql: str, params: Tuple[Any, ...] = ()):
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.fetchone()

    def fetchall(self, sql: str, params: Tuple[Any, ...] = ()):
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.fetchall()

    def fetchone_dict(self, sql: str, params: Tuple[Any, ...] = ()):
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return None
            if isinstance(row, dict):
                return dict(row)
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))

    def fetchall_dict(self, sql: str, params: Tuple[Any, ...] = ()):
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return []
            if isinstance(rows[0], dict):
                return [dict(r) for r in rows]
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in rows]

    def _table_columns(self, cur, table: str) -> set[str]:
        sql = (
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s"
        )
        cur.execute(sql, (table,))
        rows = cur.fetchall()
        return {str(row["column_name"]) for row in rows}

    def _ensure_column(self, cur, table: str, column: str, definition: str) -> None:
        columns = self._table_columns(cur, table)
        if column in columns:
            return
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _install_audit_append_only_guards(self, cur) -> None:
        """Install append-only protections for audit history tables.

        A shared plpgsql trigger function raises on UPDATE/DELETE against
        the audit tables, enforcing append-only semantics at the DB level.
        """
        cur.execute(
            """
            CREATE OR REPLACE FUNCTION clearledgr_prevent_append_only_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        triggers = (
            ("audit_events", "trg_audit_events_no_update", "UPDATE"),
            ("audit_events", "trg_audit_events_no_delete", "DELETE"),
            ("ap_policy_audit_events", "trg_ap_policy_audit_events_no_update", "UPDATE"),
            ("ap_policy_audit_events", "trg_ap_policy_audit_events_no_delete", "DELETE"),
        )
        for table, trigger_name, operation in triggers:
            cur.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table}")
            cur.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE {operation} ON {table}
                FOR EACH ROW
                EXECUTE FUNCTION clearledgr_prevent_append_only_mutation()
                """
            )

    def _install_ap_state_guard(self, cur) -> None:
        """Enforce valid AP item states at the DB level.

        Prevents direct SQL from setting an invalid state value.
        Application-level transition validation (ap_states.py) remains
        the primary guard; this is a defence-in-depth measure.
        """
        from clearledgr.core.ap_states import VALID_STATE_VALUES

        states_list = ", ".join(f"'{s}'" for s in sorted(VALID_STATE_VALUES))
        cur.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname = 'enforce_valid_ap_state'
                ) THEN
                    CREATE OR REPLACE FUNCTION clearledgr_check_ap_state()
                    RETURNS TRIGGER AS $t$
                    BEGIN
                        IF NEW.state NOT IN ({states_list}) THEN
                            RAISE EXCEPTION 'Invalid AP item state: %', NEW.state;
                        END IF;
                        RETURN NEW;
                    END;
                    $t$ LANGUAGE plpgsql;

                    CREATE TRIGGER enforce_valid_ap_state
                    BEFORE INSERT OR UPDATE OF state ON ap_items
                    FOR EACH ROW
                    EXECUTE FUNCTION clearledgr_check_ap_state();
                END IF;
            END
            $$;
        """)

    def _get_fernet(self):
        if self._fernet is None:
            from cryptography.fernet import Fernet

            digest = hashlib.sha256(self._secret_key.encode("utf-8")).digest()
            key = base64.urlsafe_b64encode(digest)
            self._fernet = Fernet(key)
        return self._fernet

    def _encrypt_secret(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        token = self._get_fernet().encrypt(text.encode("utf-8"))
        return token.decode("utf-8")

    def _decrypt_secret(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            plain = self._get_fernet().decrypt(text.encode("utf-8"))
            return plain.decode("utf-8")
        except Exception as e:
            # If legacy/plain data exists, keep behavior non-breaking.
            logger.warning("Fernet decryption failed (legacy/plain data assumed): %s", e)
            return text

    # ------------------------------------------------------------------
    # Shared utility helpers (used by multiple store mixins)
    # ------------------------------------------------------------------

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
    def _decode_json_value(value: Any, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        fallback = default or {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else fallback
            except json.JSONDecodeError:
                return fallback
        return fallback

    # ------------------------------------------------------------------
    # Schema initialization
    # ------------------------------------------------------------------

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

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS google_auth_codes (
                    auth_code TEXT PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    organization_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT
                )
                """
            )

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
                CREATE TABLE IF NOT EXISTS outlook_autopilot_state (
                    user_id TEXT PRIMARY KEY,
                    email TEXT,
                    subscription_id TEXT,
                    subscription_expiration TEXT,
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

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    domain TEXT,
                    settings_json TEXT,
                    integration_mode TEXT DEFAULT 'shared',
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    name TEXT,
                    organization_id TEXT NOT NULL,
                    role TEXT DEFAULT 'user',
                    password_hash TEXT,
                    google_id TEXT,
                    preferences_json TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS team_invites (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    role TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    expires_at TEXT,
                    created_by TEXT,
                    accepted_by TEXT,
                    accepted_at TEXT,
                    revoked_at TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS slack_installations (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    team_name TEXT,
                    bot_user_id TEXT,
                    bot_token_encrypted TEXT,
                    scope_csv TEXT,
                    mode TEXT DEFAULT 'per_org',
                    is_active INTEGER DEFAULT 1,
                    metadata_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(organization_id, team_id)
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS organization_integrations (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    integration_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT,
                    last_sync_at TEXT,
                    metadata_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(organization_id, integration_type)
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL UNIQUE,
                    plan TEXT NOT NULL,
                    status TEXT NOT NULL,
                    trial_started_at TEXT,
                    trial_ends_at TEXT,
                    trial_days_remaining INTEGER DEFAULT 0,
                    billing_cycle TEXT DEFAULT 'monthly',
                    current_period_start TEXT,
                    current_period_end TEXT,
                    stripe_customer_id TEXT,
                    stripe_subscription_id TEXT,
                    limits_json TEXT,
                    features_json TEXT,
                    usage_json TEXT,
                    onboarding_completed INTEGER DEFAULT 0,
                    onboarding_step INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )

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
                    supersedes_ap_item_id TEXT,
                    supersedes_invoice_key TEXT,
                    superseded_by_ap_item_id TEXT,
                    resubmission_reason TEXT,
                    erp_reference TEXT,
                    erp_posted_at TEXT,
                    workflow_id TEXT,
                    run_id TEXT,
                    approval_surface TEXT DEFAULT 'hybrid',
                    approval_policy_version TEXT,
                    post_attempted_at TEXT,
                    last_error TEXT,
                    po_number TEXT,
                    attachment_url TEXT,
                    slack_channel_id TEXT,
                    slack_thread_id TEXT,
                    slack_message_ts TEXT,
                    organization_id TEXT,
                    user_id TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    metadata TEXT,
                    document_type TEXT DEFAULT 'invoice',
                    -- Phase 2.1.a: Fernet-encrypted bank details
                    -- (DESIGN_THESIS.md §19). Never store plaintext IBANs
                    -- or account numbers in metadata.
                    bank_details_encrypted TEXT,
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
                    box_id TEXT NOT NULL,
                    box_type TEXT NOT NULL,
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
                CREATE TABLE IF NOT EXISTS pending_notifications (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    box_id TEXT,
                    box_type TEXT,
                    channel TEXT NOT NULL DEFAULT 'slack',
                    payload_json TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 5,
                    next_retry_at TEXT NOT NULL,
                    last_error TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT,
                    updated_at TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS webhook_subscriptions (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    event_types TEXT NOT NULL DEFAULT '[]',
                    secret TEXT,
                    is_active INTEGER DEFAULT 1,
                    description TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(organization_id, url)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS disputes (
                    id TEXT PRIMARY KEY,
                    ap_item_id TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    dispute_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    vendor_name TEXT,
                    vendor_email TEXT,
                    description TEXT,
                    resolution TEXT,
                    followup_thread_id TEXT,
                    followup_count INTEGER DEFAULT 0,
                    opened_at TEXT NOT NULL,
                    vendor_contacted_at TEXT,
                    response_received_at TEXT,
                    resolved_at TEXT,
                    escalated_at TEXT,
                    updated_at TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS delegation_rules (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    delegator_id TEXT NOT NULL,
                    delegator_email TEXT NOT NULL,
                    delegate_id TEXT NOT NULL,
                    delegate_email TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    reason TEXT,
                    starts_at TEXT,
                    ends_at TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(organization_id, delegator_email, delegate_email)
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

            # agent_sessions table removed (browser agent fallback removed)
            # workflow_runs table removed (TemporalRuntime ripped out — see migration v32)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_retry_jobs (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    ap_item_id TEXT NOT NULL,
                    gmail_id TEXT,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    next_retry_at TEXT NOT NULL,
                    last_attempt_at TEXT,
                    last_error TEXT,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    idempotency_key TEXT UNIQUE,
                    correlation_id TEXT,
                    locked_by TEXT,
                    locked_at TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    completed_at TEXT
                )
            """)

            # browser_action_events table removed (browser agent fallback removed)

            # agent_policies table removed (browser agent fallback removed)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_profiles (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(organization_id, skill_id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_memory_events (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    ap_item_id TEXT,
                    thread_id TEXT,
                    event_type TEXT NOT NULL,
                    channel TEXT,
                    actor_id TEXT,
                    correlation_id TEXT,
                    source TEXT,
                    summary TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_belief_states (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    ap_item_id TEXT NOT NULL,
                    thread_id TEXT,
                    current_state TEXT,
                    status TEXT,
                    belief_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    uncertainties_json TEXT NOT NULL,
                    next_action_json TEXT NOT NULL,
                    memory_summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(organization_id, skill_id, ap_item_id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_episode_summaries (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    ap_item_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT,
                    outcome_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(organization_id, skill_id, ap_item_id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_patterns (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    pattern_type TEXT NOT NULL,
                    pattern_key TEXT NOT NULL,
                    pattern_json TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    last_seen_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(organization_id, skill_id, pattern_type, pattern_key)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS finance_learning_events (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    ap_item_id TEXT,
                    event_type TEXT NOT NULL,
                    actor_id TEXT,
                    vendor_name TEXT,
                    action_status TEXT,
                    learning_summary TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    key_prefix TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    label TEXT,
                    is_active INTEGER DEFAULT 1,
                    last_used_at TEXT,
                    created_at TEXT,
                    updated_at TEXT
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

            cur.execute("""
                CREATE TABLE IF NOT EXISTS ap_policy_audit_events (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    policy_name TEXT NOT NULL,
                    version INTEGER,
                    action TEXT NOT NULL,
                    actor_id TEXT,
                    payload_json TEXT,
                    created_at TEXT
                )
            """)

            # Backward-compatible column evolution for pre-existing admin tables.
            self._ensure_column(cur, "organizations", "name", "TEXT")
            self._ensure_column(cur, "organizations", "domain", "TEXT")
            self._ensure_column(cur, "organizations", "settings_json", "TEXT")
            self._ensure_column(cur, "organizations", "integration_mode", "TEXT DEFAULT 'shared'")
            self._ensure_column(cur, "organizations", "created_at", "TEXT")
            self._ensure_column(cur, "organizations", "updated_at", "TEXT")

            self._ensure_column(cur, "users", "name", "TEXT")
            self._ensure_column(cur, "users", "role", "TEXT DEFAULT 'user'")
            self._ensure_column(cur, "users", "password_hash", "TEXT")
            self._ensure_column(cur, "users", "google_id", "TEXT")
            self._ensure_column(cur, "users", "preferences_json", "TEXT")
            self._ensure_column(cur, "users", "is_active", "INTEGER DEFAULT 1")
            self._ensure_column(cur, "users", "created_at", "TEXT")
            self._ensure_column(cur, "users", "updated_at", "TEXT")

            self._ensure_column(cur, "team_invites", "accepted_by", "TEXT")
            self._ensure_column(cur, "team_invites", "accepted_at", "TEXT")
            self._ensure_column(cur, "team_invites", "revoked_at", "TEXT")
            self._ensure_column(cur, "team_invites", "created_at", "TEXT")
            self._ensure_column(cur, "team_invites", "updated_at", "TEXT")

            self._ensure_column(cur, "slack_installations", "metadata_json", "TEXT")
            self._ensure_column(cur, "slack_installations", "is_active", "INTEGER DEFAULT 1")
            self._ensure_column(cur, "slack_installations", "created_at", "TEXT")
            self._ensure_column(cur, "slack_installations", "updated_at", "TEXT")

            self._ensure_column(cur, "organization_integrations", "metadata_json", "TEXT")
            self._ensure_column(cur, "organization_integrations", "created_at", "TEXT")
            self._ensure_column(cur, "organization_integrations", "updated_at", "TEXT")

            self._ensure_column(cur, "subscriptions", "limits_json", "TEXT")
            self._ensure_column(cur, "subscriptions", "features_json", "TEXT")
            self._ensure_column(cur, "subscriptions", "usage_json", "TEXT")
            self._ensure_column(cur, "subscriptions", "onboarding_completed", "INTEGER DEFAULT 0")
            self._ensure_column(cur, "subscriptions", "onboarding_step", "INTEGER DEFAULT 0")
            self._ensure_column(cur, "subscriptions", "created_at", "TEXT")
            self._ensure_column(cur, "subscriptions", "updated_at", "TEXT")

            cur.execute("CREATE INDEX IF NOT EXISTS idx_oauth_user ON oauth_tokens(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_oauth_provider ON oauth_tokens(provider)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_google_auth_codes_expires_at ON google_auth_codes(expires_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_autopilot_email ON gmail_autopilot_state(email)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_erp_org ON erp_connections(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_org_domain ON organizations(domain)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_org ON users(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_team_invites_org ON team_invites(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_team_invites_token ON team_invites(token)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_slack_installations_org ON slack_installations(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_slack_installations_team ON slack_installations(team_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_org_integrations_org ON organization_integrations(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_org ON subscriptions(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_org ON ap_items(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_state ON ap_items(state)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_thread ON ap_items(thread_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_org_message ON ap_items(organization_id, message_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_org_state_updated ON ap_items(organization_id, state, updated_at)")
            self._ensure_column(cur, "ap_items", "supersedes_ap_item_id", "TEXT")
            self._ensure_column(cur, "ap_items", "supersedes_invoice_key", "TEXT")
            self._ensure_column(cur, "ap_items", "superseded_by_ap_item_id", "TEXT")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_erp_ref ON ap_items(organization_id, erp_reference)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_org_invoice_num ON ap_items(organization_id, invoice_number)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_supersedes ON ap_items(supersedes_ap_item_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_superseded_by ON ap_items(superseded_by_ap_item_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_item_sources_item ON ap_item_sources(ap_item_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_item_sources_type_ref ON ap_item_sources(source_type, source_ref)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_box ON audit_events(box_type, box_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_events(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_org_event_ts ON audit_events(organization_id, event_type, ts)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_approvals_item ON approvals(ap_item_id)")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_item_decision_key ON approvals(ap_item_id, decision_idempotency_key)")
            # idx_agent_sessions_org_item removed (browser agent fallback removed)
            # idx_workflow_runs_* removed (TemporalRuntime ripped out — see migration v32)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_retry_jobs_org_status_next ON agent_retry_jobs(organization_id, status, next_retry_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_retry_jobs_ap_item ON agent_retry_jobs(ap_item_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_retry_jobs_job_type_status ON agent_retry_jobs(job_type, status, next_retry_at)")
            # browser_action_events and agent_policies indexes removed (browser agent fallback removed)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_profiles_org_skill ON agent_profiles(organization_id, skill_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_memory_events_org_item_created ON agent_memory_events(organization_id, ap_item_id, created_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_memory_events_org_event ON agent_memory_events(organization_id, event_type, created_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_belief_states_org_item ON agent_belief_states(organization_id, ap_item_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_episode_summaries_org_item ON agent_episode_summaries(organization_id, ap_item_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_patterns_org_type ON agent_patterns(organization_id, skill_id, pattern_type, updated_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_finance_learning_events_org_type ON finance_learning_events(organization_id, event_type, created_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_finance_learning_events_org_item ON finance_learning_events(organization_id, ap_item_id, created_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_policy_versions_org_name ON ap_policy_versions(organization_id, policy_name, version)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_policy_audit_org_name ON ap_policy_audit_events(organization_id, policy_name, created_at)")
            self._install_audit_append_only_guards(cur)
            self._install_ap_state_guard(cur)

            # Evolve existing DBs without external migration dependency.
            self._ensure_column(cur, "ap_items", "workflow_id", "TEXT")
            self._ensure_column(cur, "ap_items", "run_id", "TEXT")
            self._ensure_column(cur, "ap_items", "approval_surface", "TEXT DEFAULT 'hybrid'")
            self._ensure_column(cur, "ap_items", "approval_policy_version", "TEXT")
            self._ensure_column(cur, "ap_items", "post_attempted_at", "TEXT")
            self._ensure_column(cur, "ap_items", "resubmission_reason", "TEXT")

            self._ensure_column(cur, "audit_events", "source", "TEXT")
            self._ensure_column(cur, "audit_events", "correlation_id", "TEXT")
            self._ensure_column(cur, "audit_events", "workflow_id", "TEXT")
            self._ensure_column(cur, "audit_events", "run_id", "TEXT")
            self._ensure_column(cur, "audit_events", "decision_reason", "TEXT")

            self._ensure_column(cur, "approvals", "source_channel", "TEXT")
            self._ensure_column(cur, "approvals", "source_message_ref", "TEXT")
            self._ensure_column(cur, "approvals", "decision_idempotency_key", "TEXT")
            self._ensure_column(cur, "approvals", "decision_payload", "TEXT")

            # browser_action_events _ensure_column calls removed (browser agent fallback removed)
            self._ensure_column(cur, "agent_retry_jobs", "gmail_id", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "job_type", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "status", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "retry_count", "INTEGER DEFAULT 0")
            self._ensure_column(cur, "agent_retry_jobs", "max_retries", "INTEGER DEFAULT 3")
            self._ensure_column(cur, "agent_retry_jobs", "next_retry_at", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "last_attempt_at", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "last_error", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "payload_json", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "result_json", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "idempotency_key", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "correlation_id", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "locked_by", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "locked_at", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "created_at", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "updated_at", "TEXT")
            self._ensure_column(cur, "agent_retry_jobs", "completed_at", "TEXT")
            self._ensure_column(cur, "organizations", "integration_mode", "TEXT DEFAULT 'shared'")
            self._ensure_column(cur, "slack_installations", "metadata_json", "TEXT")
            self._ensure_column(cur, "subscriptions", "onboarding_completed", "INTEGER DEFAULT 0")
            self._ensure_column(cur, "subscriptions", "onboarding_step", "INTEGER DEFAULT 0")

            # AP columns added for PO tracking, attachments, and Slack thread state.
            self._ensure_column(cur, "ap_items", "po_number", "TEXT")
            self._ensure_column(cur, "ap_items", "attachment_url", "TEXT")
            self._ensure_column(cur, "ap_items", "slack_channel_id", "TEXT")
            self._ensure_column(cur, "ap_items", "slack_thread_id", "TEXT")
            self._ensure_column(cur, "ap_items", "slack_message_ts", "TEXT")

            # Extraction confidence: field-level scores stored as JSON blob so accuracy
            # trends are queryable per-field without parsing audit events.
            self._ensure_column(cur, "ap_items", "field_confidences", "TEXT")

            # Gap #10 — exception_code / exception_severity as first-class indexed columns.
            # Previously these were buried in the metadata JSON blob, making them
            # impossible to query efficiently.  New writes populate both the columns
            # and metadata for backward-compat; reads prefer the column values.
            self._ensure_column(cur, "ap_items", "exception_code", "TEXT")
            self._ensure_column(cur, "ap_items", "exception_severity", "TEXT")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_exception_code ON ap_items(organization_id, exception_code)")

            # Gap #11 — dedicated channel_threads table for Teams (and Slack) so
            # both channels store their thread/card state symmetrically instead of
            # Teams writing into the AP item metadata JSON blob.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channel_threads (
                    id TEXT PRIMARY KEY,
                    ap_item_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    conversation_id TEXT,
                    message_id TEXT,
                    activity_id TEXT,
                    service_url TEXT,
                    state TEXT,
                    last_action TEXT,
                    updated_by TEXT,
                    reason TEXT,
                    organization_id TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(ap_item_id, channel, conversation_id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_channel_threads_ap_item ON channel_threads(ap_item_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_channel_threads_channel ON channel_threads(ap_item_id, channel)")

            # Phase 1.4: Override-window tracking for ERP post reversals.
            cur.execute(OverrideWindowStore.OVERRIDE_WINDOWS_TABLE_SQL)
            for ddl in OverrideWindowStore.OVERRIDE_WINDOWS_INDEXES_SQL:
                try:
                    cur.execute(ddl)
                except Exception as idx_exc:
                    logger.warning(
                        "[DB init] override_windows index skipped: %s", idx_exc
                    )

            # Vendor intelligence tables (AP reasoning layer)
            cur.execute(VendorStore.VENDOR_PROFILE_TABLE_SQL)
            cur.execute(VendorStore.VENDOR_INVOICE_HISTORY_TABLE_SQL)
            cur.execute(VendorStore.VENDOR_DECISION_FEEDBACK_TABLE_SQL)
            # Phase 3.1.a: vendor onboarding workflow sessions (DESIGN_THESIS.md §9)
            cur.execute(VendorStore.VENDOR_ONBOARDING_SESSIONS_TABLE_SQL)
            # Phase 3.1.b: one-time onboarding magic-link tokens
            cur.execute(OnboardingTokenStore.VENDOR_ONBOARDING_TOKENS_TABLE_SQL)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vendor_profiles_org_name "
                "ON vendor_profiles(organization_id, vendor_name)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vendor_invoice_history_org_vendor "
                "ON vendor_invoice_history(organization_id, vendor_name, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vendor_decision_feedback_org_vendor "
                "ON vendor_decision_feedback(organization_id, vendor_name, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vendor_onboarding_active "
                "ON vendor_onboarding_sessions(organization_id, vendor_name, is_active)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vendor_onboarding_state_activity "
                "ON vendor_onboarding_sessions(state, last_activity_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vendor_onboarding_tokens_session "
                "ON vendor_onboarding_tokens(session_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vendor_onboarding_tokens_expiry "
                "ON vendor_onboarding_tokens(expires_at)"
            )

            # Approval chain persistence tables
            cur.execute(ApprovalChainStore.APPROVAL_CHAINS_TABLE_SQL)
            cur.execute(ApprovalChainStore.APPROVAL_STEPS_TABLE_SQL)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_approval_chains_invoice "
                "ON approval_chains(organization_id, invoice_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_approval_steps_chain "
                "ON approval_steps(chain_id, step_index)"
            )

            # Agent task run checkpoint table (durable planning loop)
            cur.execute(TaskStore.TASK_RUNS_TABLE_SQL)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_runs_org_status "
                "ON task_runs(organization_id, status)"
            )

            # AP runtime compatibility tables (legacy reconciliation stack removed).
            for table_sql in AP_RUNTIME_COMPAT_TABLES:
                cur.execute(table_sql)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_org_status ON transactions(organization_id, status)")
            self._ensure_column(cur, "finance_emails", "metadata", "TEXT DEFAULT '{}'")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_finance_emails_org ON finance_emails(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_finance_emails_gmail_id ON finance_emails(gmail_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gl_corrections_org ON gl_corrections(organization_id, corrected_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gl_accounts_org_code ON gl_accounts(organization_id, code)")

            # Reconciliation tables
            for sql in ReconStore.RECON_TABLES_SQL:
                cur.execute(sql)

            # Payment tracking (informational — agent never executes payments)
            cur.execute(PaymentStore.PAYMENT_TABLE_SQL)
            # Evolve legacy payments table (old schema: id, organization_id, payment_data, created_at, updated_at)
            self._ensure_column(cur, "payments", "ap_item_id", "TEXT")
            self._ensure_column(cur, "payments", "vendor_name", "TEXT")
            self._ensure_column(cur, "payments", "amount", "REAL")
            self._ensure_column(cur, "payments", "currency", "TEXT DEFAULT 'USD'")
            self._ensure_column(cur, "payments", "status", "TEXT DEFAULT 'ready_for_payment'")
            self._ensure_column(cur, "payments", "payment_method", "TEXT")
            self._ensure_column(cur, "payments", "payment_reference", "TEXT")
            self._ensure_column(cur, "payments", "due_date", "TEXT")
            self._ensure_column(cur, "payments", "scheduled_date", "TEXT")
            self._ensure_column(cur, "payments", "completed_date", "TEXT")
            self._ensure_column(cur, "payments", "erp_reference", "TEXT")
            self._ensure_column(cur, "payments", "notes", "TEXT")
            self._ensure_column(cur, "payments", "paid_amount", "REAL")
            self._ensure_column(cur, "payments", "overdue_alerted", "TEXT")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_org ON payments(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_ap_item ON payments(ap_item_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_org_status ON payments(organization_id, status)")

            # Payment events (append-only history — multiple payments per bill)
            cur.execute(PaymentStore.PAYMENT_EVENTS_TABLE_SQL)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_events_payment ON payment_events(payment_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_payment_events_org ON payment_events(organization_id)")

            # Multi-entity support (P0: Cowrywise has entities in Africa and US)
            cur.execute(EntityStore.ENTITIES_TABLE_SQL)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_entities_org ON entities(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_entities_org_code ON entities(organization_id, code)")
            # Add entity_id to ap_items for entity-level routing
            self._ensure_column(cur, "ap_items", "entity_id", "TEXT")
            self._ensure_column(cur, "ap_items", "document_type", "TEXT DEFAULT 'invoice'")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_entity ON ap_items(organization_id, entity_id)")
            # Add entity_id to erp_connections so each entity can have its own connection
            self._ensure_column(cur, "erp_connections", "entity_id", "TEXT")

            conn.commit()

        self._initialized = True

        # Run numbered migrations (new schema changes go here, not _ensure_column)
        try:
            from clearledgr.core.migrations import run_migrations
            run_migrations(self)
        except Exception as exc:
            logger.error("Database migrations failed: %s", exc)


# ARCHITECTURE NOTE: ClearledgrDB uses mixin inheritance for store methods.
# Each mixin (APStore, AuthStore, IntegrationStore, PolicyStore, etc.) adds
# query methods to the DB class.  The final class is assembled dynamically in
# _get_db_impl_class() below via multiple inheritance.
# Future migration: replace mixins with composition (db.ap.list_items()).
# See docs/TIER4_AUDIT_2026_04.md section I5/I6 for details.
class ClearledgrDB:
    def __new__(cls, *args, **kwargs):
        if cls is ClearledgrDB:
            impl_cls = _get_db_impl_class()
            instance = object.__new__(impl_cls)
            impl_cls.__init__(instance, *args, **kwargs)
            return instance
        return object.__new__(cls)


def _get_db_impl_class():
    global _CLEARLEDGR_DB_IMPL
    if _CLEARLEDGR_DB_IMPL is None:
        _load_store_symbols()

        class _ClearledgrDBImpl(
            ClearledgrDB,
            APStore,
            APRuntimeStore,
            ApprovalChainStore,
            AuthStore,
            EntityStore,
            IntegrationStore,
            PolicyStore,
            MetricsStore,
            TaskStore,
            VendorStore,
            OnboardingTokenStore,
            ReconStore,
            PaymentStore,
            WebhookStore,
            DisputeStore,
            OverrideWindowStore,
            PipelineStore,
            PurchaseOrderStore,
            BoxLifecycleStore,
            _ClearledgrDBBase,
        ):
            pass

        _CLEARLEDGR_DB_IMPL = _ClearledgrDBImpl
    return _CLEARLEDGR_DB_IMPL


_DB_INSTANCE: Optional[ClearledgrDB] = None


def get_db() -> ClearledgrDB:
    global _DB_INSTANCE
    if _DB_INSTANCE is None:
        _DB_INSTANCE = ClearledgrDB(db_path=os.getenv("CLEARLEDGR_DB_PATH", "clearledgr.db"))
        # E10: Verify database connectivity on first creation.
        # In prod-like envs we fail loud: raise so the worker crashes
        # and the container restarts, rather than silently returning
        # a broken instance that 500s every request until someone
        # notices. In dev we log + continue (SQLite creation is best-
        # effort, tests and local runs shouldn't blow up on a
        # transient file-system hiccup).
        prod_like = str(os.getenv("ENV", "dev")).strip().lower() in {
            "prod", "production", "staging", "stage",
        }
        try:
            with _DB_INSTANCE.connect() as conn:
                conn.execute("SELECT 1")
        except Exception as exc:
            logger.error("Database connectivity check failed: %s", exc)
            if prod_like:
                # Reset the singleton so a subsequent call retries
                # instead of reusing the broken instance.
                _DB_INSTANCE = None
                raise
    return _DB_INSTANCE
