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
                "UPDATE users SET role = ? WHERE role = ?",
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
        "INSERT OR IGNORE INTO pipelines (id, organization_id, name, slug, box_type, source_table, created_at) "
        "VALUES (?, '__default__', 'AP Invoices', 'ap-invoices', 'invoice', 'ap_items', ?)",
        (ap_pipeline_id, now),
    )

    ap_stages = [
        ("received", "Received", "#94A3B8", ["received"], 0),
        ("matching", "Matching", "#CA8A04", ["validated", "needs_approval", "pending_approval"], 1),
        ("exception", "Exception", "#DC2626", ["needs_info", "failed_post", "reversed", "snoozed"], 2),
        ("approved", "Approved", "#2563EB", ["approved", "ready_to_post"], 3),
        ("paid", "Paid", "#16A34A", ["posted_to_erp", "closed"], 4),
    ]
    for slug, label, color, states, order in ap_stages:
        cur.execute(
            "INSERT OR IGNORE INTO pipeline_stages (id, pipeline_id, slug, label, color, source_states, stage_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"STG-{_uuid.uuid4().hex[:12]}", ap_pipeline_id, slug, label, color, _json.dumps(states), order),
        )

    ap_columns = [
        ("invoice_amount", "Invoice Amount", "amount", None, 0),
        ("po_reference", "PO Reference", "po_number", None, 1),
        ("match_status", "Match Status", None, "match_status", 2),
        ("exception_reason", "Exception Reason", "exception_code", None, 3),
        ("days_to_due", "Days to Due Date", None, "days_to_due", 4),
        ("iban_verified", "IBAN Verified", None, "iban_verified", 5),
        ("erp_posted", "ERP Posted", "erp_posted_at", None, 6),
    ]
    for slug, label, source_field, computed_fn, order in ap_columns:
        cur.execute(
            "INSERT OR IGNORE INTO pipeline_columns (id, pipeline_id, slug, label, source_field, computed_fn, display_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"COL-{_uuid.uuid4().hex[:12]}", ap_pipeline_id, slug, label, source_field, computed_fn, order),
        )

    # --- Seed: Vendor Onboarding pipeline (thesis §9) ---
    vo_pipeline_id = f"PL-{_uuid.uuid4().hex[:12]}"
    cur.execute(
        "INSERT OR IGNORE INTO pipelines (id, organization_id, name, slug, box_type, source_table, created_at) "
        "VALUES (?, '__default__', 'Vendor Onboarding', 'vendor-onboarding', 'vendor_onboarding', 'vendor_onboarding_sessions', ?)",
        (vo_pipeline_id, now),
    )

    vo_stages = [
        ("invited", "Invited", "#94A3B8", ["invited"], 0),
        ("kyc", "KYC", "#CA8A04", ["awaiting_kyc"], 1),
        ("bank_verify", "Bank Verify", "#2563EB", ["awaiting_bank", "microdeposit_pending"], 2),
        ("active", "Active", "#16A34A", ["bank_verified", "ready_for_erp", "active"], 3),
    ]
    for slug, label, color, states, order in vo_stages:
        cur.execute(
            "INSERT OR IGNORE INTO pipeline_stages (id, pipeline_id, slug, label, color, source_states, stage_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"STG-{_uuid.uuid4().hex[:12]}", vo_pipeline_id, slug, label, color, _json.dumps(states), order),
        )

    # --- Seed: 3 thesis saved views (thesis §6.2) ---
    for name, filter_json, is_default in [
        ("Exceptions", _json.dumps({"stage": "exception"}), 1),
        ("Awaiting Approval", _json.dumps({"source_states": ["needs_approval", "pending_approval"]}), 1),
        ("Due This Week", _json.dumps({"days_to_due_lte": 5}), 1),
    ]:
        cur.execute(
            "INSERT OR IGNORE INTO saved_views (id, organization_id, pipeline_id, name, filter_json, is_default, show_in_inbox, created_at) "
            "VALUES (?, '__default__', ?, ?, ?, ?, 1, ?)",
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
    ]:
        try:
            cur.execute(f"ALTER TABLE ap_items ADD COLUMN {col} {col_type}")
        except Exception:
            pass


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
