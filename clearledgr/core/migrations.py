"""Lightweight database migration framework.

No Alembic, no SQLAlchemy — just numbered migration functions that
run raw SQL, matching the existing database pattern.

Usage:
    from clearledgr.core.migrations import run_migrations
    run_migrations(db)  # call after db.initialize()

Each migration is a function that receives a cursor and the db instance.
Migrations run in order, only once, tracked by a schema_versions table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Registry of all migrations: (version, description, function)
_MIGRATIONS: List[Tuple[int, str, Callable]] = []


def migration(version: int, description: str):
    """Decorator to register a migration function."""
    def decorator(fn):
        _MIGRATIONS.append((version, description, fn))
        return fn
    return decorator


def run_migrations(db) -> int:
    """Run all pending migrations. Returns count of migrations applied."""
    db.initialize()

    # Ensure schema_versions table exists
    with db.connect() as conn:
        cur = conn.cursor()
        if db.use_postgres:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT,
                    applied_at TEXT NOT NULL
                )
            """)
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT,
                    applied_at TEXT NOT NULL
                )
            """)
        conn.commit()

    # Get current version
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(version) FROM schema_versions")
        row = cur.fetchone()
        current_version = row[0] if row and row[0] is not None else 0

    # Sort and run pending migrations
    sorted_migrations = sorted(_MIGRATIONS, key=lambda m: m[0])
    applied = 0

    for version, description, fn in sorted_migrations:
        if version <= current_version:
            continue

        logger.info("[Migration] Applying v%d: %s", version, description)
        try:
            with db.connect() as conn:
                cur = conn.cursor()
                fn(cur, db)
                cur.execute(
                    "INSERT INTO schema_versions (version, description, applied_at) VALUES (?, ?, ?)",
                    (version, description, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            applied += 1
            logger.info("[Migration] v%d applied successfully", version)
        except Exception as exc:
            logger.error("[Migration] v%d FAILED: %s", version, exc)
            raise  # Don't continue if a migration fails

    if applied:
        logger.info("[Migration] %d migration(s) applied. Schema at v%d",
                     applied, sorted_migrations[-1][0] if sorted_migrations else 0)
    return applied


def get_schema_version(db) -> int:
    """Get the current schema version."""
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT MAX(version) FROM schema_versions")
            row = cur.fetchone()
            return row[0] if row and row[0] is not None else 0
    except Exception:
        return 0


# =====================================================================
# MIGRATIONS
# =====================================================================
# Each migration is additive. Never modify a previous migration.
# To fix a mistake, add a new migration.
# =====================================================================

@migration(1, "Initial schema — document_type column on ap_items")
def _m001_document_type_column(cur, db):
    """Add document_type column if it doesn't exist."""
    columns = db._table_columns(cur, "ap_items")
    if "document_type" not in columns:
        cur.execute("ALTER TABLE ap_items ADD COLUMN document_type TEXT DEFAULT 'invoice'")


@migration(2, "Disputes table")
def _m002_disputes_table(cur, db):
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


@migration(3, "Webhook subscriptions table")
def _m003_webhooks_table(cur, db):
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


@migration(4, "Delegation rules table")
def _m004_delegation_rules(cur, db):
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


@migration(5, "Outlook autopilot state table")
def _m005_outlook_autopilot(cur, db):
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


@migration(6, "Payment overdue_alerted column")
def _m006_payment_overdue_alerted(cur, db):
    columns = db._table_columns(cur, "payments")
    if "overdue_alerted" not in columns:
        cur.execute("ALTER TABLE payments ADD COLUMN overdue_alerted TEXT")
