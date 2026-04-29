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


# Advisory-lock id for the migration runner. Postgres will serialize every
# caller of pg_advisory_lock(MIGRATION_LOCK_KEY) across the whole cluster,
# which is what we need when api/worker/beat processes boot simultaneously
# and each try to apply pending migrations.
MIGRATION_LOCK_KEY = 0x0C11_8D61  # arbitrary 32-bit constant, "clearledgr" vibe


def run_migrations(db) -> int:
    """Run all pending migrations. Returns count of migrations applied.

    Safe to call concurrently from multiple processes (Railway runs api +
    worker + beat, and gunicorn runs multiple api workers). The first
    caller to acquire the advisory lock runs the pending migrations; the
    others wait, then find current_version updated and do nothing.
    """
    db.initialize()

    # Ensure schema_versions table exists (idempotent, safe to race).
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_versions (
                version INTEGER PRIMARY KEY,
                description TEXT,
                applied_at TEXT NOT NULL
            )
        """)
        conn.commit()

    # Acquire the cluster-wide advisory lock. Released automatically
    # when the connection closes.
    lock_conn = None
    try:
        lock_conn = db.connect().__enter__()
        lock_conn.cursor().execute(
            "SELECT pg_advisory_lock(%s)",
            (MIGRATION_LOCK_KEY,),
        )
        lock_conn.commit()
    except Exception as exc:
        logger.warning(
            "[Migration] advisory lock not acquired (%s); continuing without cluster serialization",
            exc,
        )
        lock_conn = None

    try:
        # Get current version AFTER acquiring the lock so we see any
        # versions that a racing process just applied. The ``AS v`` alias
        # is load-bearing: psycopg's dict_row factory keys fetchone()
        # rows by column label, not position — a bare ``MAX(version)``
        # would land as ``row["max"]`` and break the ``row["v"]`` access
        # below.
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT MAX(version) AS v FROM schema_versions")
            row = cur.fetchone()
            if row is None:
                current_version = 0
            else:
                val = row["v"] if isinstance(row, dict) else row[0]
                current_version = val if val is not None else 0

        sorted_migrations = sorted(_MIGRATIONS, key=lambda m: m[0])
        applied = 0

        for version, description, fn in sorted_migrations:
            if version <= current_version:
                continue

            logger.info("[Migration] Applying v%d: %s", version, description)
            try:
                with db.connect() as conn:
                    # Migrations run in autocommit mode so each DDL
                    # statement commits on its own. Without autocommit,
                    # a single failing statement (e.g. a
                    # `CREATE INDEX IF NOT EXISTS` guarded by try/except
                    # inside the migration body) poisons the entire
                    # transaction with "current transaction is aborted,
                    # commands ignored until end of transaction block"
                    # and every subsequent statement fails.
                    # All migrations here use idempotent DDL
                    # (CREATE ... IF NOT EXISTS, INSERT ... ON CONFLICT
                    # DO NOTHING) so partial-failure semantics on re-run
                    # are safe.
                    autocommit_was_toggled = False
                    try:
                        if not conn.autocommit:
                            conn.autocommit = True
                            autocommit_was_toggled = True
                    except Exception as exc:
                        logger.debug(
                            "[Migration] v%d: could not set autocommit (%s); proceeding", version, exc,
                        )
                    cur = conn.cursor()
                    try:
                        fn(cur, db)
                        # Belt-and-braces: if another process raced past
                        # the lock, ON CONFLICT DO NOTHING keeps the
                        # INSERT harmless.
                        cur.execute(
                            (
                                "INSERT INTO schema_versions (version, description, applied_at) "
                                "VALUES (%s, %s, %s) ON CONFLICT (version) DO NOTHING"
                            ),
                            (version, description, datetime.now(timezone.utc).isoformat()),
                        )
                    finally:
                        # Restore autocommit so the pool's next consumer
                        # gets a connection with default transactional
                        # semantics, not the migration's per-statement
                        # mode.
                        if autocommit_was_toggled:
                            try:
                                conn.autocommit = False
                            except Exception:
                                pass
                applied += 1
                logger.info("[Migration] v%d applied successfully", version)
            except Exception as exc:
                logger.error("[Migration] v%d FAILED: %s", version, exc)
                raise  # Don't continue if a migration fails

        if applied:
            logger.info("[Migration] %d migration(s) applied. Schema at v%d",
                         applied, sorted_migrations[-1][0] if sorted_migrations else 0)
        return applied
    finally:
        if lock_conn is not None:
            try:
                lock_conn.cursor().execute(
                    "SELECT pg_advisory_unlock(%s)",
                    (MIGRATION_LOCK_KEY,),
                )
                lock_conn.commit()
            except Exception:
                pass
            try:
                lock_conn.__exit__(None, None, None)
            except Exception:
                pass


def get_schema_version(db) -> int:
    """Get the current schema version."""
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            # AS alias is load-bearing: psycopg's dict_row factory keys
            # rows by column label; positional `row[0]` raises KeyError
            # on dicts. See run_migrations() ~L86 for the same pattern.
            cur.execute("SELECT MAX(version) AS v FROM schema_versions")
            row = cur.fetchone()
            if row is None:
                return 0
            val = row["v"] if isinstance(row, dict) else row[0]
            return val if val is not None else 0
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
                    (
                        f"UPDATE {table_name} SET bank_details_encrypted = %s, metadata = %s "
                        "WHERE id = %s"
                    ),
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


@migration(15, "Role taxonomy cutover to thesis five roles (DESIGN_THESIS.md §17)")
def _m015_role_taxonomy_cutover(cur, db):
    """Rewrite ``users.role`` in place from legacy values to thesis roles.

    Phase 2.3 — five-role thesis taxonomy.

    Legacy → canonical mapping:

        user     → ap_clerk
        member   → ap_clerk
        operator → ap_manager
        admin    → financial_controller
        viewer   → read_only
        cfo      → cfo                      (unchanged)
        owner    → owner                    (unchanged)
        api      → api                      (unchanged)

    The mapping is applied as a set of UPDATE statements — each legacy
    value is rewritten in a single SQL statement, atomic per value.
    Any stored value not in this map is left alone (including unknown
    garbage, which the predicates will reject at the auth layer).

    This is a hard cutover: after this migration runs, the database
    contains only canonical thesis role strings (plus any unknown
    values that were never on the legacy list). There is no
    backward-compatibility shim — ``normalize_user_role`` at the auth
    layer is an additional safety net for stale JWTs still in flight,
    not a preservation mechanism.
    """
    mapping = {
        "user": "ap_clerk",
        "member": "ap_clerk",
        "operator": "ap_manager",
        "admin": "financial_controller",
        "viewer": "read_only",
    }
    total_updated = 0
    for legacy, canonical in mapping.items():
        try:
            cur.execute(
                "UPDATE users SET role = %s WHERE role = %s",
                (canonical, legacy),
            )
            rows = cur.rowcount or 0
            if rows > 0:
                logger.info(
                    "[Migration v15] Upgraded %d users from %r to %r",
                    rows, legacy, canonical,
                )
                total_updated += rows
        except Exception as exc:
            logger.warning(
                "[Migration v15] UPDATE users SET role = %r WHERE role = %r failed: %s",
                canonical, legacy, exc,
            )
    if total_updated:
        logger.info(
            "[Migration v15] Role taxonomy cutover complete — %d users updated",
            total_updated,
        )


@migration(16, "Vendor KYC schema (DESIGN_THESIS.md §3)")
def _m016_vendor_kyc_columns(cur, db):
    """Add KYC fields to vendor_profiles.

    Phase 2.4 — vendor KYC schema.

    Adds six new columns to vendor_profiles:
      - registration_number     — company registration id
      - vat_number              — tax identity
      - registered_address      — legal address
      - director_names          — JSON array of director names
      - kyc_completion_date     — ISO date when KYC was completed
      - vendor_kyc_updated_at   — audit timestamp bumped on every KYC write

    These are first-class typed columns (not JSON metadata) so
    operational queries — "all vendors with stale KYC", "all vendors
    missing a VAT number" — are simple SQL.

    ``iban_verified`` / ``iban_verified_at`` / ``ytd_spend`` /
    ``risk_score`` from the thesis §3 spec are NOT stored columns:
      - iban_verified is derived from existing bank_details_encrypted
        + iban_change_pending state (Phase 2.1.a + 2.1.b)
      - iban_verified_at is derived from bank_details_changed_at
      - ytd_spend is computed at read time from vendor_invoice_history
      - risk_score is computed at read time by VendorRiskScoreService
    """
    for ddl in (
        "ALTER TABLE vendor_profiles ADD COLUMN registration_number TEXT",
        "ALTER TABLE vendor_profiles ADD COLUMN vat_number TEXT",
        "ALTER TABLE vendor_profiles ADD COLUMN registered_address TEXT",
        "ALTER TABLE vendor_profiles ADD COLUMN director_names TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE vendor_profiles ADD COLUMN kyc_completion_date TEXT",
        "ALTER TABLE vendor_profiles ADD COLUMN vendor_kyc_updated_at TEXT",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "duplicate column" in msg:
                logger.info(
                    "[Migration v16] column already present, skipping: %s",
                    ddl.split("ADD COLUMN")[1].strip(),
                )
            else:
                raise

    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_vendor_profiles_kyc_completion "
        "ON vendor_profiles(organization_id, kyc_completion_date)",
        "CREATE INDEX IF NOT EXISTS idx_vendor_profiles_kyc_updated "
        "ON vendor_profiles(organization_id, vendor_kyc_updated_at)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v16] index skipped: %s", exc)


@migration(17, "Vendor onboarding sessions table (DESIGN_THESIS.md §9)")
def _m017_vendor_onboarding_sessions(cur, db):
    """Create vendor_onboarding_sessions table for Phase 3.1.a.

    Greenfield table — no backfill, no plaintext-strip, no rename. The
    in-memory `VendorManagementService._vendors` dict that this replaces
    was never persisted, so there is nothing to migrate. Sessions begin
    accumulating from the first invite-vendor call after this migration
    runs.

    Schema mirrors :data:`VendorStore.VENDOR_ONBOARDING_SESSIONS_TABLE_SQL`.
    The state column is enforced by
    :class:`clearledgr.core.vendor_onboarding_states.VendorOnboardingState`
    at the application layer — there is no SQL CHECK constraint because
    SQLite versions and Postgres dialects diverge on enum support and
    we want the same migration body to run on both.
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vendor_onboarding_sessions (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            vendor_name TEXT NOT NULL,
            state TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            invited_at TEXT NOT NULL,
            invited_by TEXT NOT NULL,
            last_activity_at TEXT NOT NULL,
            last_chase_at TEXT,
            chase_count INTEGER NOT NULL DEFAULT 0,
            kyc_submitted_at TEXT,
            bank_submitted_at TEXT,
            microdeposit_initiated_at TEXT,
            microdeposit_initiated_by TEXT,
            bank_verified_at TEXT,
            erp_activated_at TEXT,
            erp_vendor_id TEXT,
            completed_at TEXT,
            escalated_at TEXT,
            escalated_reason TEXT,
            rejected_at TEXT,
            rejected_by TEXT,
            rejection_reason TEXT,
            abandoned_at TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_vendor_onboarding_active "
        "ON vendor_onboarding_sessions(organization_id, vendor_name, is_active)",
        "CREATE INDEX IF NOT EXISTS idx_vendor_onboarding_state_activity "
        "ON vendor_onboarding_sessions(state, last_activity_at)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v17] index skipped: %s", exc)


@migration(18, "Vendor onboarding magic-link tokens (DESIGN_THESIS.md §9)")
def _m018_vendor_onboarding_tokens(cur, db):
    """Create vendor_onboarding_tokens table for Phase 3.1.b.

    Greenfield table — there were no pre-existing magic-link tokens to
    backfill. The token table is intentionally separate from
    vendor_onboarding_sessions because the token is the auth primitive,
    not the workflow primitive: a session can have multiple tokens over
    its lifetime if the customer re-issues, and we want to keep the
    revocation history for audit.

    Token storage rules:
      - Only the SHA-256 hash of the raw token is persisted (column
        ``token_hash``). The raw token is returned exactly once at
        issue time, then discarded.
      - ``UNIQUE(token_hash)`` enforces collision-free hashing.
      - ``revoked_at`` flips a token to dead state — the auth
        dependency rejects revoked tokens with a 410 Gone.
      - ``expires_at`` defaults to ``issued_at + 14 days`` and is
        enforced at the application layer (no SQL trigger).
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vendor_onboarding_tokens (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            vendor_name TEXT NOT NULL,
            session_id TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            purpose TEXT NOT NULL DEFAULT 'full_onboarding',
            issued_at TEXT NOT NULL,
            issued_by TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_accessed_at TEXT,
            access_count INTEGER NOT NULL DEFAULT 0,
            revoked_at TEXT,
            revoked_by TEXT,
            revoke_reason TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            UNIQUE(token_hash)
        )
    """)
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_vendor_onboarding_tokens_session "
        "ON vendor_onboarding_tokens(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_vendor_onboarding_tokens_expiry "
        "ON vendor_onboarding_tokens(expires_at)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v18] index skipped: %s", exc)


@migration(19, "Archived users + snooze columns (DESIGN_THESIS.md §5.4, §3)")
def _v19_archived_users_and_snooze(cur, db):
    """§5.4: Add archived_at to users. §3: Add snoozed_until to ap_items."""
    for col, table, col_type in [
        ("archived_at", "users", "TEXT"),
        ("archived_by", "users", "TEXT"),
        ("snoozed_until", "ap_items", "TEXT"),
    ]:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        except Exception:
            pass  # Column may already exist


@migration(20, "Vendor primary AP contact email (DESIGN_THESIS.md §3)")
def _v20_vendor_contact_email(cur, db):
    """§3: 'primary AP contact email' on the Vendor record."""
    try:
        cur.execute("ALTER TABLE vendor_profiles ADD COLUMN primary_contact_email TEXT")
    except Exception:
        pass


@migration(21, "Parent account hierarchy (DESIGN_THESIS.md §3 Multi-Entity)")
def _v21_parent_account_hierarchy(cur, db):
    """§3: Organizations can be children of a parent account."""
    for stmt in [
        "ALTER TABLE organizations ADD COLUMN parent_organization_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_org_parent ON organizations(parent_organization_id)",
    ]:
        try:
            cur.execute(stmt)
        except Exception:
            pass


@migration(22, "Vendor entity overrides (DESIGN_THESIS.md §3 Multi-Entity)")
def _v22_vendor_entity_overrides(cur, db):
    """§3: Entity-specific payment terms and IBANs per vendor."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vendor_entity_overrides (
            id TEXT PRIMARY KEY,
            vendor_profile_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            payment_terms TEXT,
            bank_details_encrypted TEXT,
            default_currency TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(vendor_profile_id, entity_id)
        )
    """)
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_vendor_entity_overrides_vendor "
            "ON vendor_entity_overrides(vendor_profile_id)"
        )
    except Exception:
        pass


@migration(23, "Approval chain entity_id (DESIGN_THESIS.md §3 Multi-Entity)")
def _v23_approval_chain_entity(cur, db):
    """§3: Approval chains scoped to entity."""
    try:
        cur.execute("ALTER TABLE approval_chains ADD COLUMN entity_id TEXT")
    except Exception:
        pass


@migration(25, "Object Model — Box/Pipeline/Stage/Column/SavedView (DESIGN_THESIS.md §5.1)")
def _v25_object_model(cur, db):
    """§5.1: First-class Pipeline, Stage, Column, SavedView, BoxLink objects."""
    import json as _json
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz

    now = _dt.now(_tz.utc).isoformat()

    # --- Tables ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pipelines (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            name TEXT NOT NULL,
            slug TEXT NOT NULL,
            box_type TEXT NOT NULL,
            source_table TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            UNIQUE(organization_id, slug)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_stages (
            id TEXT PRIMARY KEY,
            pipeline_id TEXT NOT NULL,
            slug TEXT NOT NULL,
            label TEXT NOT NULL,
            color TEXT,
            source_states TEXT NOT NULL DEFAULT '[]',
            stage_order INTEGER NOT NULL DEFAULT 0,
            UNIQUE(pipeline_id, slug)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_columns (
            id TEXT PRIMARY KEY,
            pipeline_id TEXT NOT NULL,
            slug TEXT NOT NULL,
            label TEXT NOT NULL,
            source_field TEXT,
            computed_fn TEXT,
            display_order INTEGER NOT NULL DEFAULT 0,
            visible_default INTEGER DEFAULT 1,
            UNIQUE(pipeline_id, slug)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saved_views (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            pipeline_id TEXT NOT NULL,
            name TEXT NOT NULL,
            filter_json TEXT NOT NULL DEFAULT '{}',
            sort_json TEXT DEFAULT '{}',
            show_in_inbox INTEGER DEFAULT 0,
            created_by TEXT,
            is_default INTEGER DEFAULT 0,
            created_at TEXT,
            UNIQUE(organization_id, pipeline_id, name)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS box_links (
            id TEXT PRIMARY KEY,
            source_box_id TEXT NOT NULL,
            source_box_type TEXT NOT NULL,
            target_box_id TEXT NOT NULL,
            target_box_type TEXT NOT NULL,
            link_type TEXT NOT NULL DEFAULT 'related',
            created_at TEXT
        )
    """)
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_pipeline_stages_pipeline ON pipeline_stages(pipeline_id)",
        "CREATE INDEX IF NOT EXISTS idx_pipeline_columns_pipeline ON pipeline_columns(pipeline_id)",
        "CREATE INDEX IF NOT EXISTS idx_saved_views_org ON saved_views(organization_id, pipeline_id)",
        "CREATE INDEX IF NOT EXISTS idx_box_links_source ON box_links(source_box_id, source_box_type)",
        "CREATE INDEX IF NOT EXISTS idx_box_links_target ON box_links(target_box_id, target_box_type)",
    ]:
        try:
            cur.execute(idx_sql)
        except Exception:
            pass

    # --- Seed: AP Invoices pipeline (thesis §6.7) ---
    ap_pipeline_id = f"PL-{_uuid.uuid4().hex[:12]}"
    cur.execute(
        (
            "INSERT INTO pipelines (id, organization_id, name, slug, box_type, source_table, created_at) "
            "VALUES (%s, '__default__', 'AP Invoices', 'ap-invoices', 'invoice', 'ap_items', %s) "
            "ON CONFLICT DO NOTHING"
        ),
        (ap_pipeline_id, now),
    )

    # AP Kanban stages. Posted and Paid are deliberately distinct:
    #   Posted = bill is in the ledger, payment not yet executed
    #   Paid   = lifecycle complete, money has left the account
    # Collapsing the two hides the window finance teams care about most.
    # ``reversed`` lives in Exception (and is terminal — see
    # clearledgr/core/ap_states.py) so a reversed-then-closed item does
    # not leak into Paid.
    ap_stages = [
        ("received", "Received", "#94A3B8", ["received"], 0),
        ("matching", "Matching", "#CA8A04", ["validated", "needs_approval", "pending_approval"], 1),
        ("exception", "Exception", "#DC2626", ["needs_info", "failed_post", "reversed", "snoozed"], 2),
        ("approved", "Approved", "#2563EB", ["approved", "ready_to_post"], 3),
        ("posted", "Posted", "#8B5CF6", ["posted_to_erp"], 4),
        ("paid", "Paid", "#16A34A", ["closed"], 5),
    ]
    for slug, label, color, states, order in ap_stages:
        cur.execute(
            (
                "INSERT INTO pipeline_stages (id, pipeline_id, slug, label, color, source_states, stage_order) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING"
            ),
            (f"STG-{_uuid.uuid4().hex[:12]}", ap_pipeline_id, slug, label, color, _json.dumps(states), order),
        )

    # DESIGN_THESIS.md §5.5 Agent Columns — the thesis enumerates eight
    # auto-populated columns. GRN Reference sits between PO Reference
    # and Match Status because 3-way match ordering is PO → GRN →
    # invoice, and the match status reads from both references.
    ap_columns = [
        ("invoice_amount", "Invoice Amount", "amount", None, 0),
        ("po_reference", "PO Reference", "po_number", None, 1),
        ("grn_reference", "GRN Reference", "grn_number", None, 2),
        ("match_status", "Match Status", None, "match_status", 3),
        ("exception_reason", "Exception Reason", "exception_code", None, 4),
        ("days_to_due", "Days to Due Date", None, "days_to_due", 5),
        ("iban_verified", "IBAN Verified", None, "iban_verified", 6),
        ("erp_posted", "ERP Posted", "erp_posted_at", None, 7),
    ]
    for slug, label, source_field, computed_fn, order in ap_columns:
        cur.execute(
            (
                "INSERT INTO pipeline_columns (id, pipeline_id, slug, label, source_field, computed_fn, display_order) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING"
            ),
            (f"COL-{_uuid.uuid4().hex[:12]}", ap_pipeline_id, slug, label, source_field, computed_fn, order),
        )

    # --- Seed: Vendor Onboarding pipeline (thesis §9) ---
    vo_pipeline_id = f"PL-{_uuid.uuid4().hex[:12]}"
    cur.execute(
        (
            "INSERT INTO pipelines (id, organization_id, name, slug, box_type, source_table, created_at) "
            "VALUES (%s, '__default__', 'Vendor Onboarding', 'vendor-onboarding', 'vendor_onboarding', 'vendor_onboarding_sessions', %s) "
            "ON CONFLICT DO NOTHING"
        ),
        (vo_pipeline_id, now),
    )

    # Vendor Onboarding Kanban stages — vendor-onboarding-spec §2.1.
    # The user-facing pipeline is four forward stages + one "blocked"
    # holding column + one terminal "closed unsuccessful" column. The
    # internal bank_verified + ready_for_erp sub-states surface under
    # bank_verify on the Kanban — they are retry resume points, not
    # user-visible stages.
    vo_stages = [
        ("invited", "Invited", "#94A3B8", ["invited"], 0),
        ("kyc", "KYC", "#CA8A04", ["kyc"], 1),
        ("bank_verify", "Bank Verify", "#2563EB", ["bank_verify", "bank_verified", "ready_for_erp"], 2),
        ("active", "Active", "#16A34A", ["active"], 3),
        ("blocked", "Blocked", "#DC2626", ["blocked"], 4),
        ("closed_unsuccessful", "Closed Unsuccessful", "#6B7280", ["closed_unsuccessful"], 5),
    ]
    for slug, label, color, states, order in vo_stages:
        cur.execute(
            (
                "INSERT INTO pipeline_stages (id, pipeline_id, slug, label, color, source_states, stage_order) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING"
            ),
            (f"STG-{_uuid.uuid4().hex[:12]}", vo_pipeline_id, slug, label, color, _json.dumps(states), order),
        )

    # --- Seed: 3 thesis saved views (thesis §6.2) ---
    for name, filter_json, is_default in [
        ("Exceptions", _json.dumps({"stage": "exception"}), 1),
        ("Awaiting Approval", _json.dumps({"source_states": ["needs_approval", "pending_approval"]}), 1),
        ("Due This Week", _json.dumps({"days_to_due_lte": 5}), 1),
    ]:
        cur.execute(
            (
                "INSERT INTO saved_views (id, organization_id, pipeline_id, name, filter_json, is_default, show_in_inbox, created_at) "
                "VALUES (%s, '__default__', %s, %s, %s, %s, 1, %s) "
                "ON CONFLICT DO NOTHING"
            ),
            (f"SV-{_uuid.uuid4().hex[:12]}", ap_pipeline_id, name, filter_json, is_default, now),
        )


@migration(26, "Agent Columns as first-class fields (DESIGN_THESIS.md §5.5)")
def _v26_agent_columns(cur, db):
    """§5.5: GRN Reference, Match Status, Exception Reason as stored columns."""
    for col, col_type in [
        ("grn_reference", "TEXT"),
        ("match_status", "TEXT"),       # 'passed' | 'exception' | 'failed'
        ("exception_reason", "TEXT"),   # plain-language reason
    ]:
        try:
            cur.execute(f"ALTER TABLE ap_items ADD COLUMN {col} {col_type}")
        except Exception:
            pass
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_items_match_status ON ap_items(organization_id, match_status)")
    except Exception:
        pass


@migration(27, "Read Only seat type + expiry (DESIGN_THESIS.md §13)")
def _v27_seat_type(cur, db):
    """§13: Read Only seats at reduced rate, expire after configurable period."""
    for col, col_type in [
        ("seat_type", "TEXT DEFAULT 'full'"),       # 'full' | 'read_only'
        ("seat_expires_at", "TEXT"),                  # ISO timestamp for Read Only expiry
    ]:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
        except Exception:
            pass


@migration(28, "LLM Gateway call log (AGENT_DESIGN_SPECIFICATION.md §7)")
def _v28_llm_call_log(cur, db):
    """§7: Centralized LLM Gateway tracks every Claude call with cost and latency."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS llm_call_log (
            id TEXT PRIMARY KEY,
            organization_id TEXT,
            action TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            latency_ms INTEGER,
            cost_estimate_usd REAL,
            truncated INTEGER DEFAULT 0,
            error TEXT,
            created_at TEXT
        )
    """)
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_call_log_org_action "
            "ON llm_call_log(organization_id, action)"
        )
    except Exception:
        pass


@migration(30, "SLA metrics table (AGENT_DESIGN_SPECIFICATION.md §11)")
def _v30_sla_metrics(cur, db):
    """§11: Per-step latency tracking for SLA compliance monitoring."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ap_sla_metrics (
            id TEXT PRIMARY KEY,
            ap_item_id TEXT,
            organization_id TEXT NOT NULL,
            step_name TEXT NOT NULL,
            latency_ms INTEGER NOT NULL,
            breached INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sla_metrics_org_step "
            "ON ap_sla_metrics(organization_id, step_name, created_at)"
        )
    except Exception:
        pass


@migration(29, "Box state fields (AGENT_DESIGN_SPECIFICATION.md §6)")
def _v29_box_state_fields(cur, db):
    """§6: pending_plan, waiting_condition, fraud_flags on ap_items for agent state management."""
    for col, col_type in [
        ("pending_plan", "TEXT"),        # JSON: remaining plan actions
        ("waiting_condition", "TEXT"),    # JSON: {type, expected_by, context}
        ("fraud_flags", "TEXT"),          # JSON: [{flag_type, detected_at, ...}]
        ("payment_reference", "TEXT"),   # §6.1: payment ref from ERP after schedule_payment
    ]:
        try:
            cur.execute(f"ALTER TABLE ap_items ADD COLUMN {col} {col_type}")
        except Exception:
            pass


@migration(31, "Prevent duplicate Box creation on same thread (AGENT_DESIGN_SPECIFICATION.md §11.2.5)")
def _v31_thread_unique_index(cur, db):
    """§11.2.5: UNIQUE partial index on (organization_id, thread_id).

    If two workers simultaneously receive events for the same Gmail
    thread (duplicate Pub/Sub notification), only one can create the
    Box. The second gets a UNIQUE violation and the handler routes
    to the existing Box.

    Uses a partial index (WHERE thread_id IS NOT NULL AND thread_id != '')
    because thread_id can be NULL or empty string for non-Gmail sources
    (manual creation, API imports) — those rows must not collide.

    Backfill first: normalize empty-string thread_ids to NULL so they
    are excluded from the uniqueness check.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    # Backfill: empty string → NULL so the partial index predicate excludes them
    cur.execute("UPDATE ap_items SET thread_id = NULL WHERE thread_id = ''")

    try:
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uniq_ap_items_org_thread "
            "ON ap_items(organization_id, thread_id) "
            "WHERE thread_id IS NOT NULL"
        )
    except Exception as exc:
        # Real duplicates in the data — surface loudly instead of silently continuing.
        # Collect the offending (org, thread) pairs so operators can clean them up.
        try:
            cur.execute(
                "SELECT organization_id, thread_id, COUNT(*) FROM ap_items "
                "WHERE thread_id IS NOT NULL "
                "GROUP BY organization_id, thread_id HAVING COUNT(*) > 1"
            )
            dupes = cur.fetchall()
        except Exception:
            dupes = []
        _log.error(
            "[Migration v31] UNIQUE index creation failed: %s. "
            "Duplicate (org_id, thread_id) rows must be resolved manually: %s",
            exc, [tuple(r) for r in dupes][:20],
        )
        raise


@migration(32, "Drop workflow_runs table (TemporalRuntime ripped out)")
def _v32_drop_workflow_runs(cur, db):
    """Remove the workflow_runs table and its indexes.

    The TemporalRuntime class was a local DB-backed fallback for a
    Temporal deployment that never materialised. Celery + Redis Streams
    + task_runs cover every requirement (durability, retry, status
    polling). The table is dropped; any residual rows were never used
    by production paths.
    """
    cur.execute("DROP INDEX IF EXISTS idx_workflow_runs_org_status")
    cur.execute("DROP INDEX IF EXISTS idx_workflow_runs_ap_item")
    cur.execute("DROP TABLE IF EXISTS workflow_runs")


@migration(33, "DB-backed PO / GR / 3-way match tables (§6.6 + thesis match primitive)")
def _v33_purchase_orders(cur, db):
    """Persist Purchase Orders, Goods Receipts, and 3-way matches.

    The original PurchaseOrderService kept these in process-local dicts,
    so nothing survived a deploy and multi-worker setups couldn't share
    state. These three tables back the new PurchaseOrderStore mixin
    which the service now delegates to.

    Line items are stored as JSON text on the parent row. PO line items
    are always queried with the PO (no standalone PO-line queries we
    care about), and JSON keeps the schema tight. Indexes cover the
    two access patterns the service actually uses:
      - get PO by (org, number)
      - list open POs for a vendor
    """
    # Purchase Orders
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_orders (
            po_id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            po_number TEXT,
            vendor_id TEXT,
            vendor_name TEXT,
            order_date TEXT,
            expected_delivery TEXT,
            line_items_json TEXT NOT NULL DEFAULT '[]',
            subtotal REAL NOT NULL DEFAULT 0,
            tax_amount REAL NOT NULL DEFAULT 0,
            total_amount REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            status TEXT NOT NULL DEFAULT 'draft',
            requested_by TEXT,
            approved_by TEXT,
            approved_at TEXT,
            notes TEXT,
            department TEXT,
            project TEXT,
            ship_to_address TEXT,
            erp_po_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_po_org_number ON purchase_orders(organization_id, po_number)",
        "CREATE INDEX IF NOT EXISTS idx_po_org_vendor ON purchase_orders(organization_id, vendor_name)",
        "CREATE INDEX IF NOT EXISTS idx_po_org_status ON purchase_orders(organization_id, status)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v33] PO index skipped: %s", exc)

    # Goods Receipts
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS goods_receipts (
            gr_id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            gr_number TEXT,
            po_id TEXT,
            po_number TEXT,
            vendor_id TEXT,
            vendor_name TEXT,
            receipt_date TEXT,
            received_by TEXT,
            delivery_note TEXT,
            carrier TEXT,
            line_items_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_gr_po ON goods_receipts(po_id)",
        "CREATE INDEX IF NOT EXISTS idx_gr_org_vendor ON goods_receipts(organization_id, vendor_name)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v33] GR index skipped: %s", exc)

    # 3-Way Matches
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS three_way_matches (
            match_id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            invoice_id TEXT,
            po_id TEXT,
            gr_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            exceptions_json TEXT NOT NULL DEFAULT '[]',
            po_amount REAL NOT NULL DEFAULT 0,
            gr_amount REAL NOT NULL DEFAULT 0,
            invoice_amount REAL NOT NULL DEFAULT 0,
            price_variance REAL NOT NULL DEFAULT 0,
            quantity_variance REAL NOT NULL DEFAULT 0,
            override_by TEXT,
            override_reason TEXT,
            matched_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_match_invoice ON three_way_matches(invoice_id)",
        "CREATE INDEX IF NOT EXISTS idx_match_po ON three_way_matches(po_id)",
        "CREATE INDEX IF NOT EXISTS idx_match_org_status ON three_way_matches(organization_id, status)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v33] Match index skipped: %s", exc)


@migration(24, "Migration from Existing Tools (DESIGN_THESIS.md §3)")
def _v24_migration_state(cur, db):
    """§3 Migration: parallel running mode + cutover decision tracking."""
    for col, col_type in [
        ("migration_status", "TEXT DEFAULT 'live'"),
        ("parallel_start_date", "TEXT"),
        ("cutover_decision_at", "TEXT"),
        ("cutover_decision_by", "TEXT"),
    ]:
        try:
            cur.execute(f"ALTER TABLE organizations ADD COLUMN {col} {col_type}")
        except Exception:
            pass


@migration(38, "Rename vendor onboarding states to spec: awaiting_kyc→kyc, awaiting_bank→bank_verify, escalated→blocked, rejected+abandoned→closed_unsuccessful")
def _v38_rename_vendor_onboarding_states(cur, db):
    """Align vendor_onboarding_sessions.state values with
    vendor-onboarding-spec §2.1 canonical names.

    Rewrites historical rows in place so the code's new enum values
    match what the DB actually stores. The prior values (awaiting_kyc,
    awaiting_bank, escalated, rejected, abandoned) are retired from the
    enum in the same change.

    Two of the renames are pure cosmetic (awaiting_kyc→kyc,
    awaiting_bank→bank_verify). Two are semantic consolidations:
      - escalated → blocked (same behaviour, spec-canonical name)
      - rejected + abandoned → closed_unsuccessful (both are
        "onboarding ended without activation" terminals; the specific
        reason moves to closed_unsuccessful_reason so audit context is
        preserved).

    The closed_unsuccessful_reason column is added here too if it
    doesn't already exist, and backfilled from the prior terminal
    value so nothing is lost.
    """
    # 1. Add closed_unsuccessful_reason column (idempotent on re-run).
    try:
        cur.execute(
            "ALTER TABLE vendor_onboarding_sessions "
            "ADD COLUMN closed_unsuccessful_reason TEXT"
        )
    except Exception:
        pass  # Column already exists

    # 2. Backfill closed_unsuccessful_reason for terminal rows that are
    #    about to be renamed. Do this BEFORE the UPDATE so we capture
    #    the old state value as the reason.
    try:
        cur.execute(
            "UPDATE vendor_onboarding_sessions "
            "SET closed_unsuccessful_reason = state "
            "WHERE state IN ('rejected', 'abandoned') "
            "AND (closed_unsuccessful_reason IS NULL OR closed_unsuccessful_reason = '')"
        )
    except Exception as exc:
        # Table may not exist yet on a very fresh install — that's fine,
        # migration v17 creates it and later v38 runs will rewrite no rows.
        logger.debug("[Migration v38] backfill skipped: %s", exc)

    # 3. Rename state values in place.
    renames = [
        ("awaiting_kyc", "kyc"),
        ("awaiting_bank", "bank_verify"),
        ("escalated", "blocked"),
        ("rejected", "closed_unsuccessful"),
        ("abandoned", "closed_unsuccessful"),
    ]
    for old, new in renames:
        try:
            cur.execute(
                "UPDATE vendor_onboarding_sessions SET state = %s WHERE state = %s",
                (new, old),
            )
        except Exception as exc:
            logger.debug("[Migration v38] rename %s→%s skipped: %s", old, new, exc)

    # 4. Update the AP onboarding pipeline stage map (pipeline_stages
    #    rows seeded in the initial migration used the old state names
    #    in source_states). Rewrite them to the new names.
    import json as _json
    try:
        cur.execute(
            "SELECT id, slug, source_states FROM pipeline_stages "
            "WHERE pipeline_id IN (SELECT id FROM pipelines WHERE slug = 'vendor-onboarding')"
        )
        rows = cur.fetchall()
    except Exception:
        rows = []
    state_name_map = {
        "awaiting_kyc": "kyc",
        "awaiting_bank": "bank_verify",
        "escalated": "blocked",
        "rejected": "closed_unsuccessful",
        "abandoned": "closed_unsuccessful",
    }
    for row in rows:
        try:
            stage_id, slug, source_states_raw = row[0], row[1], row[2]
            states = _json.loads(source_states_raw or "[]")
            remapped = []
            for s in states:
                remapped.append(state_name_map.get(s, s))
            # Dedup while preserving order (closed_unsuccessful may
            # appear twice after merging rejected+abandoned).
            seen = set()
            deduped = []
            for s in remapped:
                if s not in seen:
                    seen.add(s)
                    deduped.append(s)
            cur.execute(
                "UPDATE pipeline_stages SET source_states = %s WHERE id = %s",
                (_json.dumps(deduped), stage_id),
            )
        except Exception as exc:
            logger.debug("[Migration v38] pipeline_stages rewrite skipped: %s", exc)


@migration(39, "Backfill GRN Reference agent column on AP pipeline (DESIGN_THESIS.md §5.5)")
def _v39_backfill_grn_reference_column(cur, db):
    """Add the missing ``grn_reference`` column to the AP Invoices
    pipeline_columns seed on existing databases.

    Background: DESIGN_THESIS.md §5.5 lists eight auto-populated Agent
    Columns for AP — one of them, GRN Reference, was absent from the
    initial pipeline_columns seed. Fresh DBs now get it via the updated
    seed in the initial migration; this migration patches DBs that were
    initialised before that change landed so the thesis column set is
    complete across the fleet.

    Idempotent: INSERT OR IGNORE + slug lookup, and the display_order
    is shifted on neighboring rows only if grn_reference was missing.
    """
    import uuid as _uuid

    try:
        cur.execute("SELECT id FROM pipelines WHERE slug = 'ap-invoices'")
        row = cur.fetchone()
    except Exception:
        row = None
    if not row:
        return
    ap_pipeline_id = row[0]

    try:
        cur.execute(
            "SELECT slug FROM pipeline_columns WHERE pipeline_id = %s",
            (ap_pipeline_id,),
        )
        existing_slugs = {r[0] for r in cur.fetchall()}
    except Exception:
        existing_slugs = set()

    if "grn_reference" in existing_slugs:
        return  # Already patched.

    # Shift display_order on columns that should sit AFTER grn_reference
    # so the kanban column ordering stays meaningful.
    columns_after_grn = ("match_status", "exception_reason", "days_to_due",
                         "iban_verified", "erp_posted")
    try:
        for slug in columns_after_grn:
            cur.execute(
                (
                    "UPDATE pipeline_columns SET display_order = display_order + 1 "
                    "WHERE pipeline_id = %s AND slug = %s"
                ),
                (ap_pipeline_id, slug),
            )
    except Exception as exc:
        logger.debug("[Migration v39] display_order shift skipped: %s", exc)

    try:
        cur.execute(
            (
                "INSERT INTO pipeline_columns "
                "(id, pipeline_id, slug, label, source_field, computed_fn, display_order) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING"
            ),
            (f"COL-{_uuid.uuid4().hex[:12]}", ap_pipeline_id,
             "grn_reference", "GRN Reference", "grn_number", None, 2),
        )
    except Exception as exc:
        logger.debug("[Migration v39] grn_reference insert skipped: %s", exc)


@migration(40, "Agent-action credit ledger (DESIGN_THESIS.md §13 pre-purchased pool)")
def _v40_agent_credit_ledger(cur, db):
    """§13 billing model: agent action credits as a pre-purchased pool.

    Previous shape stored a monthly running counter
    (``ai_credits_this_month``) with a per-tier allowance
    (``ai_credits_per_month``). The thesis specifies a different
    model: "A pooled credit system for compute-intensive agent
    actions... purchased in advance, and consumed per action. Failed
    actions do not consume credits. A confirmation prompt appears
    before any action that would consume a significant number of
    credits."

    The ledger is the source of truth for the pool balance:

      balance = sum(credits where entry_type in {grant, refund})
              - sum(credits where entry_type = consume)

    Grants come from two places: the monthly tier allowance (recorded
    as an "auto_grant" entry on first activity each billing period)
    and admin top-ups (recorded as "purchase"). Consume entries are
    recorded when an action succeeds. Refund entries reverse a
    consume when the action fails, per thesis "failed actions do not
    consume credits".
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_credit_ledger (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            credits INTEGER NOT NULL,
            action_type TEXT,
            ap_item_id TEXT,
            related_entry_id TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL,
            created_by TEXT
        )
        """
    )
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_credit_ledger_org "
            "ON agent_credit_ledger(organization_id, created_at DESC)"
        )
    except Exception:
        pass
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_credit_ledger_ap_item "
            "ON agent_credit_ledger(ap_item_id)"
        )
    except Exception:
        pass


@migration(41, "LLM call log ↔ Box audit trail link (Box reconstructability invariant)")
def _v41_llm_call_log_ap_item_link(cur, db):
    """Add ap_item_id + correlation_id columns to llm_call_log.

    Reconstructability invariant: an auditor with access to a Box's
    audit_events rows must be able to rebuild the full history,
    including what the LLM saw and said for each agent action.
    Previously llm_call_log recorded every Claude call (tokens,
    cost, latency, model) but carried no foreign key to the Box —
    so the audit trail could name the extraction outcome but not
    the specific call that produced it. Adding the link makes the
    cross-join auditor-friendly: for any Box, join audit_events →
    llm_call_log on correlation_id to see every model interaction
    that shaped its state.

    Columns are nullable so existing rows (which pre-date the
    link) remain readable; new calls populate them when the caller
    passes them through the gateway.
    """
    for col in ("ap_item_id", "correlation_id"):
        try:
            cur.execute(f"ALTER TABLE llm_call_log ADD COLUMN {col} TEXT")
        except Exception:
            pass  # Column may already exist (re-run after v41).
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_call_log_ap_item "
            "ON llm_call_log(ap_item_id)"
        )
    except Exception:
        pass
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_call_log_correlation "
            "ON llm_call_log(correlation_id)"
        )
    except Exception:
        pass


@migration(42, "Box-keyed audit primitives: drop ap_item_id on audit_events, llm_call_log, pending_notifications after backfilling box_id/box_type")
def _v42_box_keyed_audit(cur, db):
    """Make the Box abstraction first-class on shared primitives.

    Three shared audit-adjacent tables (``audit_events``,
    ``llm_call_log``, ``pending_notifications``) used ``ap_item_id``
    as their primary foreign key since inception. That name was
    accurate when AP was the only workflow; with vendor onboarding
    and forthcoming commission-clawback Boxes, it's a semantic lie
    — vendor onboarding events already pass empty string for
    ``ap_item_id`` because there's no AP item to point at.

    This migration:

    1. Adds ``box_id`` + ``box_type`` columns.
    2. Backfills existing rows (AP rows → box_type='ap_item',
       vendor-onboarding rows → extracts session_id from
       payload_json).
    3. Drops the ``ap_item_id`` column entirely. There is no
       back-compat layer — the Box-keyed columns are the only
       identifier going forward.

    Backfill strategy per row:

    * AP rows (``ap_item_id`` non-empty) → ``box_id = ap_item_id``,
      ``box_type = 'ap_item'``.
    * Vendor-onboarding rows (``event_type LIKE
      'vendor_onboarding%'`` and ``ap_item_id`` empty) → ``box_id``
      extracted from ``payload_json`` ``session_id`` field,
      ``box_type = 'vendor_onboarding_session'``.
    """
    def _column_exists(table: str, column: str) -> bool:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            (table, column),
        )
        return cur.fetchone() is not None

    # Step 1: add box_id + box_type if absent.
    for col in ("box_id", "box_type"):
        for tbl in ("audit_events", "llm_call_log", "pending_notifications"):
            if not _column_exists(tbl, col):
                cur.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} TEXT")

    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_audit_events_box "
        "ON audit_events(box_type, box_id)",
        "CREATE INDEX IF NOT EXISTS idx_llm_call_log_box "
        "ON llm_call_log(box_type, box_id)",
        "CREATE INDEX IF NOT EXISTS idx_pending_notifications_box "
        "ON pending_notifications(box_type, box_id)",
    ):
        try:
            cur.execute(idx_sql)
        except Exception:
            pass

    json_session = "(payload_json::jsonb ->> 'session_id')"

    # Step 2: backfill. audit_events has an append-only trigger, so
    # drop it for the duration of the backfill and reinstate it
    # after. llm_call_log + pending_notifications are not guarded.
    has_ap_col_ae = _column_exists("audit_events", "ap_item_id")
    has_ap_col_llm = _column_exists("llm_call_log", "ap_item_id")
    has_ap_col_pn = _column_exists("pending_notifications", "ap_item_id")

    if has_ap_col_ae:
        cur.execute(
            "DROP TRIGGER IF EXISTS trg_audit_events_no_update ON audit_events"
        )
        try:
            cur.execute(
                "UPDATE audit_events "
                "SET box_id = ap_item_id, box_type = 'ap_item' "
                "WHERE ap_item_id IS NOT NULL AND ap_item_id != '' "
                "  AND box_id IS NULL"
            )
        except Exception as exc:
            logger.warning("[Migration v42] audit_events AP backfill skipped: %s", exc)
        try:
            cur.execute(
                "UPDATE audit_events "
                f"SET box_id = {json_session}, "
                "    box_type = 'vendor_onboarding_session' "
                "WHERE event_type LIKE 'vendor_onboarding%' "
                "  AND (ap_item_id IS NULL OR ap_item_id = '') "
                "  AND box_id IS NULL"
            )
        except Exception as exc:
            logger.warning(
                "[Migration v42] audit_events vendor backfill skipped: %s", exc,
            )

    if has_ap_col_llm:
        try:
            cur.execute(
                "UPDATE llm_call_log "
                "SET box_id = ap_item_id, box_type = 'ap_item' "
                "WHERE ap_item_id IS NOT NULL AND ap_item_id != '' "
                "  AND box_id IS NULL"
            )
        except Exception as exc:
            logger.warning("[Migration v42] llm_call_log backfill skipped: %s", exc)

    if has_ap_col_pn:
        try:
            cur.execute(
                "UPDATE pending_notifications "
                "SET box_id = ap_item_id, box_type = 'ap_item' "
                "WHERE ap_item_id IS NOT NULL AND ap_item_id != '' "
                "  AND box_id IS NULL"
            )
        except Exception as exc:
            logger.warning(
                "[Migration v42] pending_notifications backfill skipped: %s", exc,
            )

    # Step 3: drop the legacy ap_item_id column. SQLite's automatic
    # index drop on DROP COLUMN doesn't kick in when an index
    # references the column by name, so we drop the known indexes
    # first (they're all idx_*ap_item*) and then DROP COLUMN.
    legacy_indexes = (
        "idx_audit_item",
        "idx_audit_events_ap_item_id",
        "idx_llm_call_log_ap_item",
    )
    for idx in legacy_indexes:
        try:
            cur.execute(f"DROP INDEX IF EXISTS {idx}")
        except Exception:
            pass

    for tbl, has_col in (
        ("audit_events", has_ap_col_ae),
        ("llm_call_log", has_ap_col_llm),
        ("pending_notifications", has_ap_col_pn),
    ):
        if has_col:
            try:
                cur.execute(f"ALTER TABLE {tbl} DROP COLUMN ap_item_id")
            except Exception as exc:
                logger.warning(
                    "[Migration v42] %s.ap_item_id DROP skipped: %s", tbl, exc,
                )

    # Reinstate the audit_events append-only UPDATE trigger.
    if has_ap_col_ae:
        cur.execute(
            "CREATE TRIGGER trg_audit_events_no_update "
            "BEFORE UPDATE ON audit_events "
            "FOR EACH ROW "
            "EXECUTE FUNCTION clearledgr_prevent_append_only_mutation()"
        )


@migration(37, "Split AP Kanban: Posted + Paid; add source_filter_json to pipeline_stages")
def _v37_split_ap_posted_and_paid(cur, db):
    """Kanban correctness fix.

    The AP Invoices pipeline used to collapse ``posted_to_erp`` and
    ``closed`` into a single "Paid" stage, which is wrong: posted-to-ERP
    means the bill exists in the ledger but money hasn't left the
    account yet, while closed means the lifecycle is fully complete
    (typically after payment execution). For an AP Manager these are
    the two most distinct stages in the pipeline.

    This migration does two things:

    1. Adds a ``source_filter_json`` column to pipeline_stages. The
       column holds optional predicates (e.g. ``{"payment_status":
       ["completed", "closed_by_credit"]}``) that the stage-query
       applies on top of the state-IN filter. Keys are whitelisted
       at query time against the source table's schema to prevent
       SQL injection via crafted config.

    2. Splits the existing "paid" stage on every AP Invoices pipeline
       row into two stages:
         - "posted" (order 4): source_states = ["posted_to_erp"]
         - "paid"   (order 5): source_states = ["closed"]
       Existing reversed-then-closed items previously flipped from
       Exception to Paid as the state machine hopped through ``closed``.
       Coupled with the v37 state-machine change that makes REVERSED
       terminal, Paid now strictly means "successfully completed."
    """
    import json as _json
    import uuid as _uuid

    # 1. Add the source_filter_json column. Safe to re-run: ALTER
    #    TABLE ADD COLUMN is idempotent via the try/except.
    try:
        cur.execute(
            "ALTER TABLE pipeline_stages ADD COLUMN source_filter_json TEXT DEFAULT '{}'"
        )
    except Exception:
        pass  # Column already exists on re-run

    # 2. Find every AP Invoices pipeline (one per org) and rewrite the
    #    Paid stage into Posted + Paid. Do NOT touch pipelines that
    #    have been customized by the operator — we detect the default
    #    seed shape by checking that "paid" exists with exactly
    #    ["posted_to_erp", "closed"] as source_states.
    cur.execute("SELECT id FROM pipelines WHERE slug = 'ap-invoices'")
    ap_pipeline_ids = [r[0] for r in cur.fetchall()]

    for pl_id in ap_pipeline_ids:
        cur.execute(
            (
                "SELECT id, source_states, stage_order FROM pipeline_stages "
                "WHERE pipeline_id = %s AND slug = 'paid'"
            ),
            (pl_id,),
        )
        row = cur.fetchone()
        if not row:
            continue
        paid_id, source_states_raw, _ = row
        try:
            current_states = _json.loads(source_states_raw or "[]")
        except (TypeError, ValueError):
            current_states = []
        # Only migrate the default shape. Customer-customized pipelines
        # keep whatever the operator set.
        if sorted(current_states) != sorted(["posted_to_erp", "closed"]):
            continue

        # Rewrite the existing "paid" row to be the new, stricter
        # Paid stage: state=closed only, order 5.
        cur.execute(
            (
                "UPDATE pipeline_stages "
                "SET source_states = %s, stage_order = 5 "
                "WHERE id = %s"
            ),
            (_json.dumps(["closed"]), paid_id),
        )

        # Insert the new Posted stage at order 4.
        cur.execute(
            (
                "INSERT INTO pipeline_stages "
                "(id, pipeline_id, slug, label, color, source_states, stage_order, source_filter_json) "
                "VALUES (%s, %s, 'posted', 'Posted', '#8B5CF6', %s, 4, '{}') "
                "ON CONFLICT DO NOTHING"
            ),
            (
                f"STG-{_uuid.uuid4().hex[:12]}",
                pl_id,
                _json.dumps(["posted_to_erp"]),
            ),
        )


@migration(36, "Hard-purge marker on organizations (purged_at)")
def _v36_organizations_purged_at(cur, db):
    """Marker for "soft-deleted + legal-hold expired + data purged".

    Pairs with deleted_at from v35. The retention job runs daily,
    finds orgs where deleted_at is older than ORG_LEGAL_HOLD_DAYS,
    calls purge_organization_data (drops every org-scoped row
    except the append-only audit trails), and stamps purged_at.
    The organizations row itself stays — it's the tombstone that
    anchors the still-retained audit events.
    """
    try:
        cur.execute("ALTER TABLE organizations ADD COLUMN purged_at TEXT")
    except Exception:
        pass


@migration(35, "Soft-delete organizations (deleted_at)")
def _v35_organizations_deleted_at(cur, db):
    """Soft-delete support for organizations.

    The old DELETE /organizations/{id} endpoint only removed the
    org_config key from settings — it claimed "Organization deleted"
    but left every ap_item, vendor profile, audit event, OAuth token
    and Gmail token behind, orphaned but still queryable. That's a
    compliance problem (right-to-be-forgotten), a hygiene problem
    (data grows forever), and a re-use hazard (if the same org_id
    were ever reissued, the new tenant would inherit the old
    tenant's data).

    Real cascading delete across 15+ tables is risky for a one-shot
    endpoint. Soft-delete is safer: set deleted_at, block further
    auth + API access for the org, hand off hard-purge to an async
    retention job. This migration adds the column; the endpoint
    change + auth guard are in this same PR.
    """
    try:
        cur.execute("ALTER TABLE organizations ADD COLUMN deleted_at TEXT")
    except Exception:
        pass  # Already exists on a re-run or older schema


@migration(43, "Box-lifecycle records: box_exceptions + box_outcomes tables")
def _v43_box_lifecycle_records(cur, db):
    """Make exceptions and outcomes first-class per Box, not implicit.

    The deck promises every workflow instance becomes "a persistent,
    attributable record: state, timeline, exceptions, outcome." Today,
    state and timeline are first-class (ap_items/vo_sessions state
    field + audit_events). Exceptions and outcomes are not — they're
    implicit in state enums and scattered across ad-hoc fields
    (last_error, metadata.fraud_flags, erp_reference, rejected_reason,
    completed_at).

    This migration adds two tables that make both pieces first-class,
    Box-keyed, and queryable across workflow types:

    - ``box_exceptions`` — one row per raised exception. Carries
      raised_at, raised_by, resolved_at, resolved_by, resolution_note.
      Multiple exceptions per Box allowed. An unresolved exception is
      one with resolved_at IS NULL. idempotency_key UNIQUE so replays
      don't duplicate.

    - ``box_outcomes`` — one row per Box (UNIQUE on (box_type, box_id)).
      Records the terminal outcome (posted_to_erp, rejected,
      vendor_activated, closed_unsuccessful, reversed) with who
      recorded it, when, and structured data (erp_reference, reason,
      etc.).

    Both tables are additive. Existing fields (state enum, erp_ref,
    rejected_reason, last_error) are left alone; Phase-2 writers
    populate the new tables on new transitions, and a later backfill
    can populate historical Boxes if needed.
    """
    # box_exceptions ------------------------------------------------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS box_exceptions (
            id TEXT PRIMARY KEY,
            box_id TEXT NOT NULL,
            box_type TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            exception_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'medium',
            reason TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            raised_at TEXT NOT NULL,
            raised_by TEXT NOT NULL,
            raised_actor_type TEXT NOT NULL DEFAULT 'agent',
            resolved_at TEXT,
            resolved_by TEXT,
            resolved_actor_type TEXT,
            resolution_note TEXT,
            idempotency_key TEXT UNIQUE
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_box_exceptions_box "
        "ON box_exceptions(box_type, box_id)",
        "CREATE INDEX IF NOT EXISTS idx_box_exceptions_unresolved "
        "ON box_exceptions(box_type, box_id) WHERE resolved_at IS NULL",
        "CREATE INDEX IF NOT EXISTS idx_box_exceptions_org_raised "
        "ON box_exceptions(organization_id, raised_at)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v43] box_exceptions index skipped: %s", exc
            )

    # box_outcomes --------------------------------------------------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS box_outcomes (
            id TEXT PRIMARY KEY,
            box_id TEXT NOT NULL,
            box_type TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            outcome_type TEXT NOT NULL,
            data_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL,
            recorded_by TEXT NOT NULL,
            recorded_actor_type TEXT NOT NULL DEFAULT 'agent',
            UNIQUE(box_type, box_id)
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_box_outcomes_type "
        "ON box_outcomes(box_type, outcome_type)",
        "CREATE INDEX IF NOT EXISTS idx_box_outcomes_org_recorded "
        "ON box_outcomes(organization_id, recorded_at)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v43] box_outcomes index skipped: %s", exc
            )


@migration(44, "Tombstone: llm_cost_paused_at on organizations")
def _v44_llm_cost_paused_at(cur, db):
    """Pause marker for the monthly LLM cost hard-cap.

    Runaway spend on Claude (bug, retry loop, prompt injection) is
    the real cost risk at pilot scale — not a customer going over
    their billed plan tier. This column is set by the LLM gateway's
    pre-flight check when a workspace crosses its tier cap, and
    every subsequent call fast-fails against it without re-querying
    cost. An override (customer CFO or CS ops) clears it. A new
    billing month auto-clears it on the next call.

    Follows the same tombstone pattern as ``deleted_at`` (v35) and
    ``purged_at`` (v36): a nullable ISO timestamp on the
    ``organizations`` row. Null = not paused. Set = paused at the
    recorded time.
    """
    try:
        cur.execute("ALTER TABLE organizations ADD COLUMN llm_cost_paused_at TEXT")
    except Exception:
        pass  # Already exists on a re-run or older schema


@migration(45, "policy_versions table — append-only snapshots for the 5 policy kinds that drive AP coordination outcomes")
def _v45_policy_versions(cur, db):
    """Append-only versioned policy storage.

    Five policy kinds today:
      - approval_thresholds (per-amount routing + approver_targets)
      - gl_account_map (vendor → ERP account code)
      - confidence_gate (auto-approve confidence floor)
      - autonomy_policy (agent action scope)
      - vendor_master_gate (whether unknown-vendor bills create Boxes)

    Every change to any of these creates a new immutable row.
    Rollbacks are new versions linking via ``parent_version_id``.
    AP items reference the version they were evaluated under via
    ``ap_items.approval_policy_version`` (already exists from v33-ish).

    The replay endpoint (``POST /api/policies/replay``) takes a
    historical version_id + a date range and returns the deltas:
    'these N bills would have routed to a different approver / hit
    a different threshold band / been blocked / been auto-approved'.
    Auditable answer to 'we changed thresholds two weeks ago — what
    bills would have routed differently?'.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS policy_versions (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            policy_kind TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            content_json TEXT NOT NULL DEFAULT '{}',
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            parent_version_id TEXT,
            is_rollback INTEGER NOT NULL DEFAULT 0,
            UNIQUE (organization_id, policy_kind, version_number)
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_policy_versions_org_kind "
        "ON policy_versions(organization_id, policy_kind, version_number DESC)",
        "CREATE INDEX IF NOT EXISTS idx_policy_versions_hash "
        "ON policy_versions(organization_id, policy_kind, content_hash)",
        "CREATE INDEX IF NOT EXISTS idx_policy_versions_created "
        "ON policy_versions(organization_id, created_at DESC)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v45] policy_versions index skipped: %s", exc
            )


@migration(46, "match_records — persistent matching primitive (Gap 3): one row per match attempt across AP/AR/Recon/intercompany")
def _v46_match_records(cur, db):
    """Match-as-a-Box: every match attempt becomes a persistent
    auditable row. Left/right references identify what was matched
    against what; match_type names the matching variant
    ('ap_three_way' / 'bank_reconciliation' / 'ar_cash_application' /
    'vendor_statement_recon' / 'intercompany'); status mirrors the
    Box state-machine pattern. Tolerance_version_id links to a
    ``policy_versions`` row (kind=match_tolerances) so we can
    audit + replay matches against a different tolerance set later.

    Index priorities:
      - (org, left_type, left_id) — find all matches involving a
        given AP item / bank line
      - (org, right_type, right_id) — find all matches involving a
        given PO / GL transaction / counterparty
      - (org, match_type, status) — Q4 dashboard "all unmatched
        bank-recon items"
      - (org, created_at DESC) — recent activity timeline
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS match_records (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            match_type TEXT NOT NULL,
            status TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0,
            left_type TEXT NOT NULL,
            left_id TEXT NOT NULL,
            right_type TEXT NOT NULL,
            right_id TEXT,
            extra_refs_json TEXT NOT NULL DEFAULT '[]',
            tolerance_version_id TEXT,
            variance_json TEXT NOT NULL DEFAULT '{}',
            exceptions_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            box_id TEXT,
            box_type TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            override_of_match_id TEXT
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_match_records_left "
        "ON match_records(organization_id, left_type, left_id)",
        "CREATE INDEX IF NOT EXISTS idx_match_records_right "
        "ON match_records(organization_id, right_type, right_id)",
        "CREATE INDEX IF NOT EXISTS idx_match_records_status "
        "ON match_records(organization_id, match_type, status)",
        "CREATE INDEX IF NOT EXISTS idx_match_records_created "
        "ON match_records(organization_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_match_records_box "
        "ON match_records(organization_id, box_type, box_id)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v46] match_records index skipped: %s", exc
            )


@migration(47, "outbox_events table — universal seam for transactional side-effect dispatch (Gap 4)")
def _v47_outbox_events(cur, db):
    """Transactional outbox: every async side-effect (observer
    callback, customer webhook delivery, ERP write-back, Slack
    notification, Gmail label sync) writes a row into this table
    inside the same DB transaction as the business write that
    triggered it. A worker drains the table; observers become
    consumers of outbox events instead of in-process callbacks.

    Closes the silent-failure race: today a state transition can
    commit while the observer fan-out crashes mid-flight, leaving
    the Box in a state where the Slack card was never posted /
    Gmail label never applied / override window never opened. With
    the outbox, the side-effect intent is durable; the worker
    retries with exponential backoff until the side-effect succeeds
    or hits dead-letter.

    Status lifecycle: pending → processing → succeeded | failed
    (will retry) | dead (max attempts hit, ops attention needed).
    The ``dedupe_key`` makes enqueue idempotent — calling
    ``enqueue('state.posted_to_erp', ..., dedupe_key='override-window:AP-1')``
    twice for the same AP item produces one row, not two.

    Index priorities:
      - (status, next_attempt_at) — worker poll: 'give me pending
        rows whose backoff window has elapsed'
      - (organization_id, event_type, created_at DESC) — ops view
        and the replay endpoint
      - (organization_id, dedupe_key) — idempotent enqueue lookup
      - (parent_event_id) — chained side-effect tracing
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS outbox_events (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            target TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            dedupe_key TEXT,
            parent_event_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            next_attempt_at TEXT,
            last_attempted_at TEXT,
            succeeded_at TEXT,
            error_log_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'system'
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_outbox_pending_due "
        "ON outbox_events(status, next_attempt_at) "
        "WHERE status IN ('pending', 'failed')",
        "CREATE INDEX IF NOT EXISTS idx_outbox_org_type_created "
        "ON outbox_events(organization_id, event_type, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_outbox_dedupe "
        "ON outbox_events(organization_id, dedupe_key) "
        "WHERE dedupe_key IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_outbox_parent "
        "ON outbox_events(parent_event_id) "
        "WHERE parent_event_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_outbox_dead "
        "ON outbox_events(organization_id, status) "
        "WHERE status = 'dead'",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v47] outbox_events index skipped: %s", exc
            )


@migration(48, "annotation_attempts table — audit trail of external state propagation (Gap 5)")
def _v48_annotation_attempts(cur, db):
    """Every external annotation write (Gmail label, NetSuite custom
    field, SAP Z-field, customer webhook, Slack card update) creates
    a row here. Distinct from outbox_events: that's the dispatch
    mechanism's audit (queued / processing / succeeded), this is the
    business-level audit (what value was applied to which target,
    what the external system responded).

    Tied 1:1 to an outbox_event via outbox_event_id when the write
    flowed through the outbox; standalone when written via direct
    inline path.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS annotation_attempts (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            box_type TEXT NOT NULL,
            box_id TEXT NOT NULL,
            target_type TEXT NOT NULL,
            old_state TEXT,
            new_state TEXT NOT NULL,
            applied_value TEXT,
            external_id TEXT,
            status TEXT NOT NULL DEFAULT 'attempted',
            response_code INTEGER,
            response_body_preview TEXT,
            outbox_event_id TEXT,
            attempted_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_annotation_box "
        "ON annotation_attempts(organization_id, box_type, box_id, attempted_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_annotation_target "
        "ON annotation_attempts(organization_id, target_type, status)",
        "CREATE INDEX IF NOT EXISTS idx_annotation_outbox "
        "ON annotation_attempts(outbox_event_id) "
        "WHERE outbox_event_id IS NOT NULL",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v48] annotation_attempts index skipped: %s", exc
            )


@migration(49, "box_summary + box_summary_history + vendor_summary — read-side projections (Gap 6)")
def _v49_projections(cur, db):
    """Three materialised projections. The primary read path
    (``GET /api/ap/items/{id}/box``) becomes a single-row lookup
    instead of 3-4 separate queries. Time-travel queries become
    O(1) per snapshot, vendor rollups become O(1) per vendor.

    The audit_events table remains the source of truth — these
    projections are eventually-consistent caches updated by the
    BoxProjectionObserver via the Gap 4 outbox. Stale rows are
    detected by ``last_event_id`` not matching the audit_events tip;
    the read path falls through to live composition in that case.
    """

    # ── box_summary: current snapshot, one row per Box ──
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS box_summary (
            box_type TEXT NOT NULL,
            box_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            state TEXT NOT NULL,
            summary_json TEXT NOT NULL DEFAULT '{}',
            timeline_preview_json TEXT NOT NULL DEFAULT '[]',
            exceptions_json TEXT NOT NULL DEFAULT '[]',
            outcome_json TEXT,
            event_count INTEGER NOT NULL DEFAULT 0,
            last_event_id TEXT,
            last_state_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (box_type, box_id)
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_box_summary_org_state "
        "ON box_summary(organization_id, box_type, state)",
        "CREATE INDEX IF NOT EXISTS idx_box_summary_updated "
        "ON box_summary(organization_id, updated_at DESC)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v49] box_summary index skipped: %s", exc)

    # ── box_summary_history: append-only snapshots for time-travel ──
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS box_summary_history (
            id TEXT PRIMARY KEY,
            box_type TEXT NOT NULL,
            box_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            snapshot_at TEXT NOT NULL,
            state TEXT NOT NULL,
            summary_json TEXT NOT NULL DEFAULT '{}',
            transition_event_id TEXT,
            triggered_by TEXT
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_box_history_box_at "
        "ON box_summary_history(box_type, box_id, snapshot_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_box_history_org_at "
        "ON box_summary_history(organization_id, snapshot_at DESC)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v49] box_summary_history index skipped: %s", exc)

    # ── vendor_summary: per-vendor rollup ──
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_summary (
            organization_id TEXT NOT NULL,
            vendor_name_normalized TEXT NOT NULL,
            vendor_display_name TEXT,
            total_bills INTEGER NOT NULL DEFAULT 0,
            total_amount_by_currency_json TEXT NOT NULL DEFAULT '{}',
            avg_days_to_pay REAL,
            exception_rate REAL NOT NULL DEFAULT 0.0,
            last_activity_at TEXT,
            posted_count INTEGER NOT NULL DEFAULT 0,
            paid_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0,
            recomputed_at TEXT NOT NULL,
            PRIMARY KEY (organization_id, vendor_name_normalized)
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_vendor_summary_activity "
        "ON vendor_summary(organization_id, last_activity_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_vendor_summary_exception_rate "
        "ON vendor_summary(organization_id, exception_rate DESC)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v49] vendor_summary index skipped: %s", exc)


@migration(50, "agent decision reasoning columns on audit_events (governance_verdict, agent_confidence)")
def _v50_agent_decision_reasoning(cur, db):
    """Lift agent decision reasoning out of payload_json into queryable columns.

    Audit P4 finding (docs/AGENT_HARNESS_AUDIT_2026_04_28.md): the
    agent's reasoning — whether governance vetoed an action, the
    confidence score behind the decision — was buried inside
    ``audit_events.payload_json`` blobs. That answers a single-invoice
    "what happened?" trace, but it's useless for product analytics or
    post-hoc model evaluation. "How many decisions did doctrine block
    last week?" required JSON-extract per row instead of a SQL WHERE.

    This migration adds two structured columns:

    * ``governance_verdict`` (TEXT, nullable) — short canonical token
      from the deliberation: ``should_execute`` / ``vetoed`` /
      ``warned`` / NULL when not applicable. Populated by
      ``finance_agent_loop._emit_plan_observed`` and by
      ``runtime._append_runtime_audit`` when a deliberation context
      is in scope.

    * ``agent_confidence`` (REAL, nullable) — agent's confidence in
      the decision at the time it was recorded, on [0, 1]. NULL for
      events that don't carry a confidence (state transitions
      driven by humans, system events, etc).

    Both nullable so existing rows are valid; backfill is unnecessary
    because the columns describe new analytics surface, not a
    correctness invariant. The ap_items.confidence column already
    exists for the per-row "best confidence" view; this is the
    per-event audit confidence which is a strictly different signal.
    """
    for ddl in (
        "ALTER TABLE audit_events ADD COLUMN governance_verdict TEXT",
        "ALTER TABLE audit_events ADD COLUMN agent_confidence REAL",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            # Already exists on a re-run, or the column landed via an older path.
            logger.debug("[Migration v50] column-add skipped: %s", exc)

    # Index governance_verdict so analytics queries can scan
    # quickly. Partial index keeps the index small (most events
    # don't carry a verdict).
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_governance_verdict "
            "ON audit_events(organization_id, governance_verdict, ts) "
            "WHERE governance_verdict IS NOT NULL"
        )
    except Exception as exc:
        logger.warning("[Migration v50] governance_verdict index skipped: %s", exc)


@migration(51, "audit_exports table — async CSV export jobs (Module 7 v1 Pass 2)")
def _v51_audit_exports(cur, db):
    """Async-export job tracking + content storage for the audit log.

    Module 7 Pass 2 (docs/Clearledgr_Workspace_Scope_GA.md): the
    leader hits "Export" with the same filters the search bar has,
    a Celery worker streams the matching ``audit_events`` rows into
    a CSV, and stores the rendered file inline on this table for the
    SPA to download.

    Why bytea + 24h retention rather than S3 / R2:
      * No object-storage env wired in the project today (no bucket,
        no IAM, no boto3). Adding it for a single feature is over-
        engineering for v1.
      * CSVs at typical scale stay small — 10K events × 200 bytes
        each = 2 MB. Postgres handles this comfortably; bytea is
        TOASTed transparently.
      * 24h retention via ``expires_at`` + a reaper means we don't
        balloon the DB. Customers re-export if they need the file
        later.
      * If/when a customer hits a multi-million-row year-export, the
        same job table + Celery task swap content storage from
        bytea to a presigned URL field — same wire contract for the
        SPA.

    Status lifecycle: ``queued`` → ``running`` → ``done`` | ``failed``.
    Terminal rows stay until expires_at; the reaper deletes after.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_exports (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            filters_json TEXT NOT NULL DEFAULT '{}',
            format TEXT NOT NULL DEFAULT 'csv',
            status TEXT NOT NULL DEFAULT 'queued',
            total_rows INTEGER,
            content BYTEA,
            content_filename TEXT,
            content_size_bytes INTEGER,
            error_message TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            expires_at TEXT NOT NULL
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_audit_exports_org_created "
        "ON audit_exports(organization_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_audit_exports_expiry "
        "ON audit_exports(expires_at) WHERE status IN ('done', 'failed')",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v51] index skipped: %s", exc)


@migration(52, "webhook_deliveries table — per-attempt delivery log (Module 7 v1 Pass 3)")
def _v52_webhook_deliveries(cur, db):
    """Append-only-ish delivery attempt log for outbound webhooks.

    Module 7 Pass 3 (audit-log SIEM streaming) needs a queryable
    delivery log so the leader can answer "did Splunk receive last
    Tuesday's failed-post events?" and "why did our SIEM miss this
    delivery?"  Every webhook attempt — success OR failure —
    inserts a row here. Retries on a failed attempt insert a NEW
    row (not UPDATE), so the chain of attempts for a given
    (webhook, event) pair is reconstructable.

    Fan-out architecture: ``append_audit_event`` enqueues a Celery
    task ``dispatch_audit_webhooks(event_id)`` after the canonical
    audit_events INSERT commits. The task looks up matching
    webhook_subscriptions, calls ``deliver_webhook`` on each, and
    writes one row here per attempt. Decouples the audit-write
    latency from webhook delivery latency.

    Status values: 'success' | 'failed' | 'retrying'.
    Retention: indefinite for the customer-facing dashboard; ops can
    add a reaper for >90-day rows when the table grows past comfort.

    Indexed for the most common dashboard queries:
      * recent deliveries for a given webhook (org + webhook_id + ts)
      * recent deliveries for a given audit event (audit_event_id)
      * failure scan (status='failed' partial index)
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            webhook_subscription_id TEXT NOT NULL,
            audit_event_id TEXT,
            event_type TEXT NOT NULL,
            attempt_number INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL,
            http_status_code INTEGER,
            response_snippet TEXT,
            error_message TEXT,
            request_url TEXT NOT NULL,
            request_signature_prefix TEXT,
            payload_size_bytes INTEGER,
            duration_ms INTEGER,
            attempted_at TEXT NOT NULL,
            next_retry_at TEXT
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_recent "
        "ON webhook_deliveries(organization_id, webhook_subscription_id, attempted_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_by_event "
        "ON webhook_deliveries(audit_event_id) WHERE audit_event_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_failures "
        "ON webhook_deliveries(organization_id, attempted_at DESC) WHERE status = 'failed'",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v52] index skipped: %s", exc)


@migration(53, "custom_roles table — per-tenant role composition (Module 6 Pass A)")
def _v53_custom_roles(cur, db):
    """Per-tenant custom-role table for Module 6 Pass A.

    The scope spec (``Clearledgr_Workspace_Scope_GA.md`` §Module 6)
    permits up to 10 custom roles per customer, each composed from the
    bounded permission catalog in ``clearledgr/core/permissions.py``.
    Standard roles stay code-defined; custom roles persist here.

    A user's role binding can reference either:
      * a standard role token (``owner``, ``cfo``, ``ap_clerk`` ...) —
        permission set comes from ``permissions.ROLE_PERMISSIONS``;
      * a custom role id (``cr_<hex>``) — permission set is the JSON
        array on this row.

    The 10-per-org limit is enforced at create time in the store
    layer, not via a DB CHECK, so the operator's UX can return a
    structured error rather than the bare DB violation.

    Indexes:
      * primary key on id (lookup);
      * (organization_id, name) UNIQUE so the SPA can show "name
        already taken" without a race window;
      * organization_id alone for "list custom roles for this org".
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_roles (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            permissions_json TEXT NOT NULL,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    for ddl in (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_custom_roles_org_name "
        "ON custom_roles(organization_id, LOWER(name))",
        "CREATE INDEX IF NOT EXISTS idx_custom_roles_org "
        "ON custom_roles(organization_id)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v53] index skipped: %s", exc)


@migration(54, "user_entity_roles table — per-entity role + approval ceiling (Module 6 Pass B)")
def _v54_user_entity_roles(cur, db):
    """Per-(user, entity) role assignment + per-amount approval ceiling.

    Per scope spec §Module 6 §217-218:
      * "A user can have different roles in different legal entities
        (Sara is AP Manager in EU entity, Read-only in US entity)."
      * "Per-amount scoping: composes with rules — 'Sara can approve
        up to $50K'."

    A row here overrides the org-level ``user.role`` for the named
    entity. Absent a row, the user's org-level role applies (so this
    table is purely additive — existing tenants behave identically
    until they explicitly assign a per-entity role).

    The ``role`` column accepts either:
      * a standard role token (``owner`` / ``cfo`` / ... / ``read_only``);
      * a custom role id (``cr_<hex>``) referencing custom_roles.id.

    No FK on entity_id or user_id — the resolver is tolerant of stale
    references (returns the org-level fallback) so a deleted entity
    or user doesn't 500 the dashboard.

    ``approval_ceiling`` is NULL by default = no ceiling (the role's
    permissions decide). When set, ``can_approve`` enforces
    ``amount <= ceiling``.

    Indexed for the most common reads:
      * primary key (user_id, entity_id) — point lookup at approve time;
      * (organization_id) — list-by-org for the admin UI.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_entity_roles (
            user_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            role TEXT NOT NULL,
            approval_ceiling NUMERIC(18,2),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, entity_id)
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_user_entity_roles_org "
        "ON user_entity_roles(organization_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_entity_roles_user "
        "ON user_entity_roles(user_id, organization_id)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v54] index skipped: %s", exc)


@migration(
    56,
    "vendor_profiles: status + status_reason for allowlist/blocklist (Module 4 Pass B)"
)
def _v56_vendor_profiles_status(cur, db):
    """Add status columns to vendor_profiles for allowlist/blocklist.

    Per scope §Module 4: customer admins can mark a vendor as blocked
    (no new invoices accepted) or active (default). Stored as a
    plain TEXT column with check at the application layer rather
    than a CHECK constraint — keeps the value space extensible
    (future ``archived`` / ``under_review``) without a schema migration
    each time. Default ``active`` so existing rows stay functional.
    """
    try:
        cur.execute(
            "ALTER TABLE vendor_profiles ADD COLUMN IF NOT EXISTS "
            "status TEXT NOT NULL DEFAULT 'active'"
        )
    except Exception as exc:
        logger.warning("[Migration v56] status add skipped: %s", exc)
    try:
        cur.execute(
            "ALTER TABLE vendor_profiles ADD COLUMN IF NOT EXISTS "
            "status_reason TEXT"
        )
    except Exception as exc:
        logger.warning("[Migration v56] status_reason add skipped: %s", exc)
    try:
        cur.execute(
            "ALTER TABLE vendor_profiles ADD COLUMN IF NOT EXISTS "
            "status_changed_at TEXT"
        )
    except Exception as exc:
        logger.warning("[Migration v56] status_changed_at add skipped: %s", exc)
    try:
        cur.execute(
            "ALTER TABLE vendor_profiles ADD COLUMN IF NOT EXISTS "
            "status_changed_by TEXT"
        )
    except Exception as exc:
        logger.warning("[Migration v56] status_changed_by add skipped: %s", exc)
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_vendor_profiles_status "
            "ON vendor_profiles(organization_id, status) "
            "WHERE status != 'active'"
        )
    except Exception as exc:
        logger.warning("[Migration v56] status index skipped: %s", exc)


@migration(
    55,
    "team_invites: entity_restrictions_json column for entity-scoped invites (Module 6 Pass D)"
)
def _v55_team_invites_entity_restrictions(cur, db):
    """Per scope §Module 6 §219: 'optional restriction to specific
    entities or workflows'. The invite carries a JSON array of
    entity_ids; on accept, the workspace creates a user_entity_roles
    row for each, scoping the user from day one rather than relying on
    a follow-up admin pass.

    The column is nullable (existing tenants don't need backfill —
    NULL = "no restriction"), and we don't index on it because reads
    are always by invite id / token (the existing primary key + token
    indexes serve them).
    """
    try:
        cur.execute(
            "ALTER TABLE team_invites ADD COLUMN IF NOT EXISTS "
            "entity_restrictions_json TEXT"
        )
    except Exception as exc:
        logger.warning("[Migration v55] column add skipped: %s", exc)


@migration(
    57,
    "invoice_originals table — SOX-immutable original-PDF storage (Wave 1 A1)"
)
def _v57_invoice_originals(cur, db):
    """Content-addressed storage for the original invoice file.

    Per AP cycle reference doc Stage 1: 'For audit purposes, the
    original invoice file must be retained immutably. Tampering with
    the original is a SOX violation in scope and an audit finding in
    any jurisdiction.'

    Today: an AP item carries ``attachment_url`` pointing back to the
    customer's Gmail. If the user revokes OAuth or Gmail enforces
    retention, the original is unreachable and the audit chain breaks.

    This table is the immutable archive:
      * ``content_hash`` is SHA-256 of the file bytes — primary key
        AND deduplicator (same PDF arriving twice → one row).
      * ``content`` is BYTEA. Postgres handles up to 1GB; AP invoices
        are <10MB in practice. When the operator wants S3 they swap
        the storage backend in ``invoice_archive.py`` without an
        API change.
      * Triggers reject UPDATE + DELETE (registered in
        ``database._install_audit_append_only_guards``). Hard delete
        is reserved for the retention reaper after
        ``retention_until``.
      * ``retention_until`` defaults to 7 years post-upload (SOX).
        Per-tenant override via ``settings_json["retention_years"]``
        is read at archive time.

    Indexed for the read paths the dashboard actually uses:
      * (organization_id, content_hash) — the typical fetch
      * (organization_id, ap_item_id) — list originals for an item
      * (retention_until) WHERE retention_until < now — reaper scan
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS invoice_originals (
            content_hash TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            ap_item_id TEXT,
            content BYTEA NOT NULL,
            content_type TEXT NOT NULL,
            filename TEXT,
            size_bytes INTEGER NOT NULL,
            uploaded_at TEXT NOT NULL,
            uploaded_by TEXT,
            retention_until TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'gmail_intake',
            PRIMARY KEY (organization_id, content_hash)
        )
        """
    )
    for ddl in (
        # Per-AP-item lookup so the detail page can list every original
        # archived against this AP item (an item can have multiple
        # attachments — invoice + supporting docs).
        "CREATE INDEX IF NOT EXISTS idx_invoice_originals_org_item "
        "ON invoice_originals(organization_id, ap_item_id) "
        "WHERE ap_item_id IS NOT NULL",
        # Retention reaper scan — partial index keeps the hot scan
        # cheap when the table grows past a few million rows.
        "CREATE INDEX IF NOT EXISTS idx_invoice_originals_retention "
        "ON invoice_originals(retention_until) "
        "WHERE retention_until IS NOT NULL",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning("[Migration v57] index skipped: %s", exc)

    # Link from AP items to the archived original. Nullable for
    # legacy items (we only archive going forward, no backfill in
    # this migration; a separate one-shot script can backfill from
    # Gmail for existing items if the operator wants).
    try:
        cur.execute(
            "ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS "
            "attachment_content_hash TEXT"
        )
    except Exception as exc:
        logger.warning(
            "[Migration v57] attachment_content_hash column skipped: %s", exc,
        )

    # Trigger ownership: the SOX-immutable invoice_originals table
    # is append-only. Same plpgsql function as audit_events; we just
    # wire two more triggers against it. This must run AFTER the
    # CREATE TABLE above — that's why the trigger lives in this
    # migration rather than in `_install_audit_append_only_guards`
    # (which runs before migrations on first init).
    for trigger_name, operation in (
        ("trg_invoice_originals_no_update", "UPDATE"),
        ("trg_invoice_originals_no_delete", "DELETE"),
    ):
        try:
            cur.execute(
                f"""
                CREATE OR REPLACE TRIGGER {trigger_name}
                BEFORE {operation} ON invoice_originals
                FOR EACH ROW
                EXECUTE FUNCTION clearledgr_prevent_append_only_mutation()
                """
            )
        except Exception as exc:
            logger.warning(
                "[Migration v57] %s trigger skipped: %s", trigger_name, exc,
            )


@migration(
    58,
    "ap_items.erp_journal_entry_id — auditor JE traceability column (Wave 1 A2)"
)
def _v58_ap_items_journal_entry_id(cur, db):
    """Add the auditor-traceable journal-entry id column on ap_items.

    Per AP cycle reference doc Stage 8 + AICPA traceability assertion:
    every posted bill must be traceable to its general-ledger journal
    entry. ERPs differ on whether bill and JE are the same record:

      * QuickBooks Online — Bill IS the journal-creating transaction
        (no separate JE record). ``erp_journal_entry_id`` = bill id.
      * NetSuite — Vendor Bill IS the source transaction; GL JE is
        derived. ``erp_journal_entry_id`` = bill internalid.
      * SAP B1 — PurchaseInvoice creates a SEPARATE OJDT row. The
        POST response carries ``JournalEntry`` (the JE DocEntry).
        ``erp_journal_entry_id`` = that DocEntry, distinct from
        ``erp_reference``.
      * Xero — Invoice has a separate Journal entity with a
        ``JournalID`` retrievable via /Journals?invoiceID=<id>.
        ``erp_journal_entry_id`` = that JournalID.

    Nullable: legacy AP items (posted before this column existed)
    don't have the data and the field is best-effort even on new
    posts. The column going from NULL to populated marks the
    moment the JE-id back-fill ran.

    Indexed for the auditor query "find me the AP item for JE id X"
    via a partial index on the non-null subset.
    """
    try:
        cur.execute(
            "ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS "
            "erp_journal_entry_id TEXT"
        )
    except Exception as exc:
        logger.warning(
            "[Migration v58] erp_journal_entry_id column skipped: %s", exc,
        )
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ap_items_je_id "
            "ON ap_items(organization_id, erp_journal_entry_id) "
            "WHERE erp_journal_entry_id IS NOT NULL"
        )
    except Exception as exc:
        logger.warning("[Migration v58] JE-id index skipped: %s", exc)


@migration(
    59,
    "payment_confirmations table — payment-tracking ledger (Wave 2 C2)"
)
def _v59_payment_confirmations(cur, db):
    """One row per confirmed (or failed) payment event for an AP item.

    Per AP cycle reference doc Stages 7-9: the workflow needs a record
    of who paid this bill, when settlement cleared, on which rail, and
    against which payment reference. Critical for SOC 2 + AICPA
    completeness ("all incurred liabilities are recorded AND
    settled") + bank reconciliation matching.

    Sources of payment confirmations:
      * QBO webhook on Payment.create / BillPayment.create
      * Xero webhook on invoice payment
      * NetSuite SuiteScript on Bill-payment created
      * SAP B1 scheduled poll of cleared outgoing payments
      * Manual operator confirmation (offline cheques, bank-portal
        payments, reconciled bank-statement debits)

    The (organization_id, source, payment_id) compound key is unique
    so duplicate webhook deliveries from the same ERP for the same
    payment never create two rows. The webhook receivers + manual
    confirmation API both pre-check via ``get_payment_confirmation_by_external_id``
    + handle the unique-violation race symmetrically.

    Status: ``confirmed | failed | disputed``. Failed payments are
    captured as their own rows so the operator can see the chain
    (initial in-flight → failed → retried → executed). The terminal
    ``payment_executed`` AP item state derives from the most-recent
    confirmed row, NOT from any single confirmation event.

    Indexed for the dashboard's typical reads:
      * (organization_id, ap_item_id) — list confirmations per item
      * (organization_id, status) — failures + disputes scan
      * (organization_id, source, payment_id) — UNIQUE for idempotency
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_confirmations (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            ap_item_id TEXT NOT NULL,
            payment_id TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed',
            settlement_at TEXT,
            amount NUMERIC(18, 2),
            currency TEXT,
            method TEXT,
            payment_reference TEXT,
            bank_account_last4 TEXT,
            failure_reason TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            created_by TEXT,
            metadata_json TEXT
        )
        """
    )
    for ddl in (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_confirmations_external "
        "ON payment_confirmations(organization_id, source, payment_id)",
        "CREATE INDEX IF NOT EXISTS idx_payment_confirmations_ap_item "
        "ON payment_confirmations(organization_id, ap_item_id, settlement_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_payment_confirmations_failures "
        "ON payment_confirmations(organization_id, settlement_at DESC) "
        "WHERE status != 'confirmed'",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v59] payment_confirmations index skipped: %s", exc,
            )


@migration(
    60,
    "payment_confirmations: include ap_item_id in unique key (Wave 2 C3)",
)
def _v60_payment_confirmations_unique_per_ap_item(cur, db):
    """Relax the (org, source, payment_id) UNIQUE to also include
    ap_item_id.

    Why: a single ERP-native payment id can clear multiple bills in
    one transaction (one QuickBooks BillPayment with N Lines linked
    to N Bills, or one NetSuite VendorPayment crediting multiple
    vendor bills). Each bill is its own AP item — and thus needs its
    own ``payment_confirmations`` row — but they share the same
    payment_id.

    The original idempotency invariant ("one webhook redelivery
    yields one row, not two") is still preserved by the new compound
    key: redelivery hits the same (org, source, payment_id, ap_item_id)
    tuple every time.
    """
    cur.execute(
        "DROP INDEX IF EXISTS idx_payment_confirmations_external"
    )
    try:
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_payment_confirmations_external "
            "ON payment_confirmations"
            "(organization_id, source, payment_id, ap_item_id)"
        )
    except Exception as exc:
        logger.warning(
            "[Migration v60] payment_confirmations unique key replace skipped: %s",
            exc,
        )


@migration(
    65,
    "data_subject_requests + retention_policy_runs (Wave 3 E3)",
)
def _v65_gdpr_tables(cur, db):
    """GDPR Articles 15/17/20 + automated retention.

    ``data_subject_requests`` — every access / erasure / portability
    request a vendor (or their representative) lodges. The 'pending'
    -> 'in_progress' -> 'completed' / 'rejected' lifecycle leaves a
    timestamp trail so the org can prove they responded within the
    one-month statutory window.

    ``retention_policy_runs`` — history of automated purge/anonymize
    runs. Records counts per entity category + the cutoff used so an
    auditor can reconstruct what got reaped on which date.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS data_subject_requests (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            request_type TEXT NOT NULL,
            subject_kind TEXT NOT NULL,
            subject_identifier TEXT NOT NULL,
            requestor_email TEXT,
            requestor_relationship TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            received_at TEXT NOT NULL,
            due_at TEXT,
            processed_at TEXT,
            processed_by TEXT,
            processing_notes TEXT,
            outcome_summary_json TEXT,
            export_payload_json TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS retention_policy_runs (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            run_kind TEXT NOT NULL,
            cutoff_at TEXT NOT NULL,
            ap_items_anonymized INTEGER NOT NULL DEFAULT 0,
            vendor_profiles_anonymized INTEGER NOT NULL DEFAULT 0,
            attachments_purged INTEGER NOT NULL DEFAULT 0,
            errors_count INTEGER NOT NULL DEFAULT 0,
            run_at TEXT NOT NULL,
            run_by TEXT,
            details_json TEXT
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_dsr_org_status "
        "ON data_subject_requests(organization_id, status, received_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_dsr_org_due "
        "ON data_subject_requests(organization_id, due_at) "
        "WHERE status NOT IN ('completed', 'rejected')",
        "CREATE INDEX IF NOT EXISTS idx_retention_runs_org "
        "ON retention_policy_runs(organization_id, run_at DESC)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v65] gdpr index skipped: %s", exc,
            )


@migration(
    64,
    "ap_items VAT columns + vat_returns table (Wave 3 E2)",
)
def _v64_vat_modeling(cur, db):
    """Per-bill VAT split + periodic VAT return rollup.

    AP cycle Stage 5 (post bill) requires the correct net/VAT split
    on the journal entry — domestic bills get an input-VAT line,
    intra-EU B2B reverse-charge bills get self-assessed input + output
    VAT (net to zero, but both sides recorded so the VAT return
    boxes balance), zero-rated EU exports / out-of-scope post net-only.

    Columns added on ``ap_items``:
      * ``net_amount`` — exclusive of VAT
      * ``vat_amount`` — VAT line value (0 for reverse_charge in net,
        but the self-assessed value lands here for VAT box reporting)
      * ``vat_rate`` — applied rate (e.g. 19.0 for DE, 0.0 for
        zero-rated). Pulled from STANDARD_VAT_RATES at compute time.
      * ``vat_code`` — operator/auditor-facing code: T1 (standard
        rated), T0 (zero rated), T2 (exempt), RC (reverse charge),
        OO (out of scope)
      * ``tax_treatment`` — canonical disposition: domestic |
        reverse_charge | zero_rated | exempt | out_of_scope
      * ``bill_country`` — seller's country code (ISO 3166-1 alpha-2)
        used to derive the treatment relative to the org's home
        country.

    ``vat_returns`` is the periodic rollup. Each row is one period
    (HMRC quarterly / EU monthly) with the 9-box totals frozen.
    """
    for col, ddl in (
        ("net_amount",     "ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS net_amount NUMERIC(18, 2)"),
        ("vat_amount",     "ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS vat_amount NUMERIC(18, 2)"),
        ("vat_rate",       "ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS vat_rate NUMERIC(6, 3)"),
        ("vat_code",       "ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS vat_code TEXT"),
        ("tax_treatment",  "ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS tax_treatment TEXT"),
        ("bill_country",   "ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS bill_country TEXT"),
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v64] ap_items.%s column skipped: %s", col, exc,
            )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vat_returns (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            jurisdiction TEXT NOT NULL,
            box1_vat_due_on_sales NUMERIC(18, 2) NOT NULL DEFAULT 0,
            box2_vat_due_on_acquisitions NUMERIC(18, 2) NOT NULL DEFAULT 0,
            box3_total_vat_due NUMERIC(18, 2) NOT NULL DEFAULT 0,
            box4_vat_reclaimed NUMERIC(18, 2) NOT NULL DEFAULT 0,
            box5_net_vat_payable NUMERIC(18, 2) NOT NULL DEFAULT 0,
            box6_total_sales_ex_vat NUMERIC(18, 2) NOT NULL DEFAULT 0,
            box7_total_purchases_ex_vat NUMERIC(18, 2) NOT NULL DEFAULT 0,
            box8_total_eu_sales NUMERIC(18, 2) NOT NULL DEFAULT 0,
            box9_total_eu_purchases NUMERIC(18, 2) NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'GBP',
            status TEXT NOT NULL DEFAULT 'draft',
            computed_at TEXT NOT NULL,
            computed_by TEXT,
            submitted_at TEXT,
            submission_reference TEXT,
            metadata_json TEXT
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_vat_returns_org_period "
        "ON vat_returns(organization_id, period_start DESC, period_end DESC)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_vat_returns_period_unique "
        "ON vat_returns(organization_id, jurisdiction, period_start, period_end) "
        "WHERE status != 'superseded'",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v64] vat_returns index skipped: %s", exc,
            )


@migration(
    63,
    "vendor_sanctions_checks + vendor_profiles sanctions_status (Wave 3 E1)",
)
def _v63_sanctions_screening(cur, db):
    """Persistent sanctions screening history + per-vendor disposition.

    The AP cycle audit doc + EU 6AMLD + UK Money Laundering Regulations
    require a screening record per vendor onboarding AND ongoing
    monitoring (lists update; vendors that were clear yesterday can be
    hit today). Stored verbatim so the provider's raw payload is
    available for compliance audit.

    ``vendor_profiles.sanctions_status`` is the rolled-up disposition
    used by the pre-payment gate: clear / review / blocked /
    unscreened. ``last_sanctions_check_at`` drives the re-screen
    cadence (default: re-screen if older than 30 days).
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_sanctions_checks (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            vendor_name TEXT NOT NULL,
            check_type TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_reference TEXT,
            status TEXT NOT NULL,
            matches_json TEXT,
            evidence_json TEXT,
            raw_payload_json TEXT,
            checked_at TEXT NOT NULL,
            checked_by TEXT,
            review_status TEXT NOT NULL DEFAULT 'open',
            cleared_at TEXT,
            cleared_by TEXT,
            cleared_reason TEXT
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_vendor_sanctions_org_vendor "
        "ON vendor_sanctions_checks(organization_id, vendor_name, checked_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_vendor_sanctions_org_status "
        "ON vendor_sanctions_checks(organization_id, status, checked_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_vendor_sanctions_open_hits "
        "ON vendor_sanctions_checks(organization_id, vendor_name) "
        "WHERE status = 'hit' AND review_status = 'open'",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v63] sanctions index skipped: %s", exc,
            )

    cur.execute(
        "ALTER TABLE vendor_profiles "
        "ADD COLUMN IF NOT EXISTS sanctions_status TEXT NOT NULL DEFAULT 'unscreened'"
    )
    cur.execute(
        "ALTER TABLE vendor_profiles "
        "ADD COLUMN IF NOT EXISTS last_sanctions_check_at TEXT"
    )


@migration(
    62,
    "bank_statement_imports + bank_statement_lines (Wave 2 C6)",
)
def _v62_bank_statement_tables(cur, db):
    """Bank reconciliation auto-match tables.

    ``bank_statement_imports`` is one row per statement file the
    operator (or future bank-feed sync) brings in. Holds the
    metadata + raw filename + reconciled stats for the dashboard.

    ``bank_statement_lines`` is one row per statement transaction.
    Each line is matched against a ``payment_confirmations`` row via
    (amount, currency, settlement window). Match status flips
    unmatched -> matched -> reconciled as the matcher / operator
    confirms.

    Composite uniqueness on (organization_id, import_id, line_index)
    so re-importing the same file is idempotent at the line level.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bank_statement_imports (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            filename TEXT,
            format TEXT NOT NULL,
            statement_iban TEXT,
            statement_account TEXT,
            statement_currency TEXT,
            from_date TEXT,
            to_date TEXT,
            opening_balance NUMERIC(18, 2),
            closing_balance NUMERIC(18, 2),
            line_count INTEGER NOT NULL DEFAULT 0,
            matched_count INTEGER NOT NULL DEFAULT 0,
            uploaded_by TEXT,
            uploaded_at TEXT NOT NULL,
            metadata_json TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bank_statement_lines (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            import_id TEXT NOT NULL,
            line_index INTEGER NOT NULL,
            value_date TEXT,
            booking_date TEXT,
            amount NUMERIC(18, 2) NOT NULL,
            currency TEXT NOT NULL,
            description TEXT,
            counterparty TEXT,
            counterparty_iban TEXT,
            bank_reference TEXT,
            end_to_end_id TEXT,
            payment_confirmation_id TEXT,
            match_status TEXT NOT NULL DEFAULT 'unmatched',
            match_confidence REAL,
            match_reason TEXT,
            matched_at TEXT,
            matched_by TEXT,
            created_at TEXT NOT NULL,
            metadata_json TEXT
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_bank_statement_lines_org_status "
        "ON bank_statement_lines(organization_id, match_status, value_date DESC)",
        "CREATE INDEX IF NOT EXISTS idx_bank_statement_lines_import "
        "ON bank_statement_lines(import_id, line_index)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_bank_statement_lines_unique "
        "ON bank_statement_lines(organization_id, import_id, line_index)",
        "CREATE INDEX IF NOT EXISTS idx_bank_statement_lines_pcid "
        "ON bank_statement_lines(payment_confirmation_id) "
        "WHERE payment_confirmation_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_bank_statement_imports_org "
        "ON bank_statement_imports(organization_id, uploaded_at DESC)",
    ):
        try:
            cur.execute(ddl)
        except Exception as exc:
            logger.warning(
                "[Migration v62] bank statement index skipped: %s", exc,
            )


@migration(
    61,
    "vendor_profiles: remittance advice columns (Wave 2 C5)",
)
def _v61_remittance_columns(cur, db):
    """Per-vendor outbound remittance configuration.

    Two columns added on ``vendor_profiles``:

      * ``remittance_email`` — overrides ``primary_contact_email`` for
        AP remittance advices specifically. Some vendors prefer
        ``ap@vendor.com`` for remittance, while their general
        correspondence contact is the AE.
      * ``remittance_opt_out`` — INTEGER 0/1. When 1, Clearledgr does
        not auto-send a remittance advice on payment confirmation.
        Used by vendors who pull from their own bank statement /
        portal feed and treat outbound remittance emails as noise.

    Both are nullable; default behaviour is "send via remittance_email
    or primary_contact_email when available". Setting these requires
    an explicit operator action in the workspace.
    """
    cur.execute(
        "ALTER TABLE vendor_profiles "
        "ADD COLUMN IF NOT EXISTS remittance_email TEXT"
    )
    cur.execute(
        "ALTER TABLE vendor_profiles "
        "ADD COLUMN IF NOT EXISTS remittance_opt_out INTEGER NOT NULL DEFAULT 0"
    )
