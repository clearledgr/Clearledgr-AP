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


@migration(7, "User last_seen_at column for approver health checks")
def _m007_user_last_seen_at(cur, db):
    columns = db._table_columns(cur, "users")
    if "last_seen_at" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN last_seen_at TEXT")


@migration(8, "User slack_user_id column for approver identity resolution")
def _m008_user_slack_user_id(cur, db):
    columns = db._table_columns(cur, "users")
    if "slack_user_id" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN slack_user_id TEXT")


@migration(9, "Performance indexes on high-query tables")
def _m009_performance_indexes(cur, db):
    """Add indexes for query performance on ap_items, approval_steps,
    ap_audit_events, and users tables."""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_ap_items_vendor_name ON ap_items(vendor_name)",
        "CREATE INDEX IF NOT EXISTS idx_ap_items_organization_state ON ap_items(organization_id, state)",
        "CREATE INDEX IF NOT EXISTS idx_ap_items_due_date ON ap_items(due_date)",
        "CREATE INDEX IF NOT EXISTS idx_approval_steps_status ON approval_steps(status)",
        "CREATE INDEX IF NOT EXISTS idx_audit_events_ap_item_id ON ap_audit_events(ap_item_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_events_event_type ON ap_audit_events(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_users_organization ON users(organization_id)",
        "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
    ]
    for ddl in indexes:
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v9] Index skipped (%s): %s", ddl.split("ON")[1].strip(), exc)


@migration(10, "ERP OAuth state table for multi-worker support")
def _m010_erp_oauth_state(cur, db):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS erp_oauth_states (
            state TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            return_url TEXT,
            erp_type TEXT,
            created_at TEXT NOT NULL
        )
    """)


@migration(11, "Override window tracking (DESIGN_THESIS.md §8)")
def _m011_override_windows(cur, db):
    """Create the override_windows table + indexes.

    Phase 1.4: Every autonomous ERP post opens a time-bounded window
    during which a human can reverse the post via Slack or the API.
    This table tracks those windows so the background reaper knows
    when to finalize them and so action handlers can verify the
    window hasn't already expired before calling reverse_bill.
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS override_windows (
            id TEXT PRIMARY KEY,
            ap_item_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            erp_reference TEXT NOT NULL,
            erp_type TEXT,
            posted_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            slack_channel TEXT,
            slack_message_ts TEXT,
            reversed_at TEXT,
            reversed_by TEXT,
            reversal_reason TEXT,
            reversal_ref TEXT,
            failure_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_override_windows_state_expiry "
        "ON override_windows(state, expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_override_windows_ap_item "
        "ON override_windows(ap_item_id)",
        "CREATE INDEX IF NOT EXISTS idx_override_windows_org "
        "ON override_windows(organization_id)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v11] Index skipped (%s): %s",
                ddl.split("ON")[1].strip(),
                exc,
            )


@migration(12, "Override window per-action tiers (DESIGN_THESIS.md §8)")
def _m012_override_window_action_type(cur, db):
    """Add action_type column to override_windows.

    Phase 1.4 supplement: the thesis says override windows are
    "configurable per action type" — the same dataset needs to track
    different action types (erp_post, payment_execution, etc.) with
    independent durations. This column lets the reaper and the
    duration lookup branch on action type without parsing metadata.

    Defaults to 'erp_post' so existing rows (the only action type that
    Phase 1.4 actually emits) classify correctly.
    """
    try:
        cur.execute(
            "ALTER TABLE override_windows ADD COLUMN action_type TEXT NOT NULL DEFAULT 'erp_post'"
        )
    except Exception as exc:
        # Postgres + SQLite both error if the column already exists.
        # We treat that as a no-op so re-running the migration is safe.
        msg = str(exc).lower()
        if "already exists" in msg or "duplicate column" in msg:
            logger.info("[Migration v12] action_type column already present, skipping")
        else:
            raise

    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_override_windows_action_type "
            "ON override_windows(action_type, state, expires_at)"
        )
    except Exception as exc:
        logger.warning(
            "[Migration v12] action_type index skipped: %s", exc
        )


@migration(13, "Bank details tokenisation (DESIGN_THESIS.md §19)")
def _m013_bank_details_encryption(cur, db):
    """Add Fernet-encrypted bank-details columns; backfill any plaintext.

    Phase 2.1.a — IBAN tokenisation.

    Adds ``bank_details_encrypted`` columns to both ``ap_items`` and
    ``vendor_profiles``. Reads any existing plaintext bank details from
    the ``metadata`` JSON blob, encrypts via the DB's Fernet helper, and
    writes them to the new column. Strips the plaintext key from
    metadata in the same transaction so a database dump no longer
    contains raw IBANs / account numbers.

    Hard cutover (no backcompat shim): after this migration runs, code
    paths read bank data only via the new typed accessors. Any future
    code that tries to put plaintext into ``metadata.bank_details`` is
    a regression.
    """
    import json as _json

    # ---- Add columns ----
    for table in ("ap_items", "vendor_profiles"):
        try:
            cur.execute(
                f"ALTER TABLE {table} ADD COLUMN bank_details_encrypted TEXT"
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "duplicate column" in msg:
                logger.info(
                    "[Migration v13] %s.bank_details_encrypted already present, skipping",
                    table,
                )
            else:
                raise

    def _backfill(table_name: str) -> int:
        try:
            cur.execute(
                f"SELECT id, metadata FROM {table_name} "
                "WHERE metadata IS NOT NULL AND metadata != '' AND metadata != '{}'"
            )
            rows = cur.fetchall()
        except Exception as exc:
            logger.warning(
                "[Migration v13] %s backfill SELECT failed: %s", table_name, exc
            )
            return 0

        backfilled = 0
        for row in rows:
            try:
                row_dict = dict(row) if not isinstance(row, dict) else row
            except Exception:
                row_dict = {"id": row[0], "metadata": row[1]}
            row_id = row_dict.get("id")
            metadata_raw = row_dict.get("metadata")
            if not row_id or not metadata_raw:
                continue
            try:
                metadata = (
                    _json.loads(metadata_raw)
                    if isinstance(metadata_raw, str)
                    else metadata_raw
                )
            except (_json.JSONDecodeError, TypeError):
                continue
            if not isinstance(metadata, dict):
                continue
            bank_details = metadata.get("bank_details")
            if not bank_details:
                continue
            try:
                payload = _json.dumps(
                    bank_details, sort_keys=True, separators=(",", ":")
                )
                ciphertext = db._encrypt_secret(payload)
            except Exception as enc_exc:
                logger.warning(
                    "[Migration v13] %s %s bank_details encryption failed: %s",
                    table_name, row_id, enc_exc,
                )
                continue
            metadata.pop("bank_details", None)
            new_metadata = _json.dumps(metadata)
            try:
                cur.execute(
                    f"UPDATE {table_name} SET bank_details_encrypted = ?, metadata = ? "
                    "WHERE id = ?",
                    (ciphertext, new_metadata, row_id),
                )
                backfilled += 1
            except Exception as upd_exc:
                logger.warning(
                    "[Migration v13] %s %s UPDATE failed: %s",
                    table_name, row_id, upd_exc,
                )
        return backfilled

    ap_items_count = _backfill("ap_items")
    vendor_count = _backfill("vendor_profiles")
    if ap_items_count or vendor_count:
        logger.info(
            "[Migration v13] Backfilled bank details: ap_items=%d vendor_profiles=%d",
            ap_items_count, vendor_count,
        )


@migration(14, "IBAN change freeze state (DESIGN_THESIS.md §8)")
def _m014_iban_change_freeze(cur, db):
    """Add IBAN-change-freeze columns to vendor_profiles.

    Phase 2.1.b — IBAN change freeze + three-factor verification.

    When an incoming invoice presents bank details that differ from the
    vendor's verified details, we freeze the vendor: any further
    invoices for that vendor are blocked until a human completes the
    three-factor verification flow.

    Columns:
      - ``pending_bank_details_encrypted`` — Fernet ciphertext of the
        NEW (unverified) details that triggered the freeze. The
        verified ``bank_details_encrypted`` column stays untouched
        until verification completes.
      - ``iban_change_pending`` — boolean flag checked by the
        validation gate. When true, the gate blocks every invoice for
        the vendor with reason code ``iban_change_pending`` (error).
      - ``iban_change_detected_at`` — ISO timestamp of the freeze start.
      - ``iban_change_verification_state`` — JSON dict tracking the
        three factors:
            {
              "email_domain_factor": {
                "verified": bool,
                "sender_domain": str,
                "matched_known_domain": bool,
                "recorded_at": iso
              },
              "phone_factor": {
                "verified": bool,
                "verified_phone_number": str,
                "caller_name_at_vendor": str,
                "verified_by": str,
                "verified_at": iso,
                "notes": str
              },
              "sign_off_factor": {
                "verified": bool,
                "verified_by": str,
                "verified_at": iso
              }
            }
    """
    for ddl in (
        "ALTER TABLE vendor_profiles ADD COLUMN pending_bank_details_encrypted TEXT",
        "ALTER TABLE vendor_profiles ADD COLUMN iban_change_pending INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE vendor_profiles ADD COLUMN iban_change_detected_at TEXT",
        "ALTER TABLE vendor_profiles ADD COLUMN iban_change_verification_state TEXT",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "duplicate column" in msg:
                logger.info(
                    "[Migration v14] column already present, skipping: %s",
                    ddl.split("ADD COLUMN")[1].strip(),
                )
            else:
                raise

    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_vendor_profiles_iban_change_pending "
            "ON vendor_profiles(organization_id, iban_change_pending)"
        )
    except Exception as exc:
        logger.warning(
            "[Migration v14] iban_change_pending index skipped: %s", exc
        )
