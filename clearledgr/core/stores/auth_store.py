"""Auth-domain database mixin for ClearledgrDB.

This module provides ``AuthStore``, a mixin class that holds every
database method related to authentication, users, organizations,
API keys, OAuth tokens, and team invites.  It is designed to be used
via multiple-inheritance so that ``ClearledgrDB`` can compose its
surface area from several focused store modules.

The mixin assumes the consuming class exposes:
    - ``self.connect()``
    - ``self.initialize()``
    - ``self._prepare_sql(sql)``
    - ``self._encrypt_secret(value)``
    - ``self._decrypt_secret(value)``
    - ``self._decode_json_value(value, fallback)``
    - ``self.use_postgres``
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AuthStore:
    """Mixin providing auth-domain persistence methods.

    Not intended to be instantiated directly.  Mix into a class that
    provides the database primitives listed in the module docstring.
    """

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

        encrypted_access = self._encrypt_secret(access_token)
        encrypted_refresh = self._encrypt_secret(refresh_token)

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
            params = (token_id, user_id, provider, encrypted_access, encrypted_refresh, expires_at, email, now, now)
        else:
            sql = self._prepare_sql("""
                INSERT OR REPLACE INTO oauth_tokens
                (id, user_id, provider, access_token, refresh_token, expires_at, email, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """)
            params = (token_id, user_id, provider, encrypted_access, encrypted_refresh, expires_at, email, now, now)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

    def _decrypt_oauth_row(self, row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        result = dict(row)
        for field in ("access_token", "refresh_token"):
            if result.get(field):
                try:
                    result[field] = self._decrypt_secret(result[field])
                except Exception:
                    pass  # Return raw value if decryption fails (legacy unencrypted data)
        return result

    def get_oauth_token(self, user_id: str, provider: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM oauth_tokens WHERE user_id = ? AND provider = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id, provider))
            row = cur.fetchone()
        return self._decrypt_oauth_row(dict(row) if row else None)

    def get_oauth_token_by_email(self, email: str, provider: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM oauth_tokens WHERE email = ? AND provider = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (email, provider))
            row = cur.fetchone()
        return self._decrypt_oauth_row(dict(row) if row else None)

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
        return [self._decrypt_oauth_row(dict(row)) for row in rows]

    def delete_oauth_token(self, user_id: str, provider: str) -> None:
        self.initialize()
        sql = self._prepare_sql("DELETE FROM oauth_tokens WHERE user_id = ? AND provider = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id, provider))
            conn.commit()

    # ------------------------------------------------------------------
    # Google web OAuth auth-code exchange cache (durable)
    # ------------------------------------------------------------------

    def save_google_auth_code(
        self,
        auth_code: str,
        access_token: str,
        refresh_token: Optional[str],
        organization_id: str,
        expires_at: str,
    ) -> None:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = self._prepare_sql(
            """
            INSERT OR REPLACE INTO google_auth_codes
            (auth_code, access_token, refresh_token, organization_id, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """
        )
        encrypted_access = self._encrypt_secret(access_token)
        encrypted_refresh = self._encrypt_secret(refresh_token or "")
        if not organization_id:
            logger.warning("organization_id missing in save_google_auth_code, falling back to 'default'")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    str(auth_code),
                    encrypted_access,
                    encrypted_refresh,
                    str(organization_id or "default"),
                    str(expires_at),
                    now,
                ),
            )
            conn.commit()

    def consume_google_auth_code(self, auth_code: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        select_sql = self._prepare_sql("SELECT * FROM google_auth_codes WHERE auth_code = ?")
        delete_sql = self._prepare_sql("DELETE FROM google_auth_codes WHERE auth_code = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(select_sql, (str(auth_code),))
            row = cur.fetchone()
            cur.execute(delete_sql, (str(auth_code),))
            conn.commit()
        if not row:
            return None
        result = dict(row)
        for field in ("access_token", "refresh_token"):
            if result.get(field):
                try:
                    result[field] = self._decrypt_secret(result[field])
                except Exception:
                    pass  # Legacy unencrypted data — return as-is
        return result

    def purge_expired_google_auth_codes(self) -> int:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = self._prepare_sql("DELETE FROM google_auth_codes WHERE expires_at IS NOT NULL AND expires_at < ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now,))
            conn.commit()
            return int(cur.rowcount or 0)

    # ------------------------------------------------------------------
    # Organizations
    # ------------------------------------------------------------------

    def create_organization(
        self,
        organization_id: str,
        name: str,
        domain: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
        integration_mode: str = "shared",
    ) -> Dict[str, Any]:
        self.initialize()
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        row_id = organization_id or f"ORG-{uuid.uuid4().hex}"
        settings_json = json.dumps(settings or {})
        sql = self._prepare_sql(
            """
            INSERT INTO organizations
            (id, name, domain, settings_json, integration_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (row_id, name, (domain or None), settings_json, integration_mode or "shared", now, now),
            )
            conn.commit()
        return self.get_organization(row_id) or {}

    # ------------------------------------------------------------------
    # Organization hard purge (GDPR right-to-be-forgotten)
    # ------------------------------------------------------------------
    #
    # Audit tables are intentionally excluded. They carry an append-only
    # trigger (`trg_*_no_delete`) that RAISEs on any DELETE, and for a
    # finance product they have a separate 7-year regulatory retention
    # window that outlives a tenant's lifetime. The `organizations` row
    # itself is also kept — it's the tombstone other systems reference
    # via deleted_at / purged_at, and dropping it would orphan the
    # audit rows.
    # ------------------------------------------------------------------
    PURGE_EXCLUDED_TABLES: frozenset = frozenset({
        "audit_events",
        "ap_policy_audit_events",
        "organizations",
    })

    def list_org_scoped_tables(self) -> List[str]:
        """Discover tables with an ``organization_id`` column.

        Cross-dialect: uses information_schema on Postgres and the
        sqlite_master catalog on SQLite. Discovery is intentional —
        if a developer adds a new org-scoped table tomorrow, the
        purge picks it up without a code change to this mixin.
        """
        self.initialize()
        org_tables: List[str] = []
        with self.connect() as conn:
            cur = conn.cursor()
            if self.use_postgres:
                cur.execute(
                    """
                    SELECT table_name FROM information_schema.columns
                     WHERE column_name = 'organization_id'
                       AND table_schema = 'public'
                    """
                )
                rows = cur.fetchall()
                org_tables = sorted({str(r["table_name"]) for r in rows})
            else:
                cur.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
                tables = [str(r["name"]) for r in cur.fetchall()]
                for t in tables:
                    cols = self._table_columns(cur, t)
                    if "organization_id" in cols:
                        org_tables.append(t)
                org_tables.sort()
        return [t for t in org_tables if t not in self.PURGE_EXCLUDED_TABLES]

    def purge_organization_data(
        self,
        organization_id: str,
        *,
        extra_skip_tables: Optional[List[str]] = None,
    ) -> Dict[str, int]:
        """Hard-delete every row scoped to ``organization_id``.

        Runs across every discovered org-scoped table except the
        append-only audit logs (see PURGE_EXCLUDED_TABLES). Returns
        a {table: rows_deleted} map for the caller to log.

        Individual table deletes are independent — a trigger abort
        (e.g. a new audit-like table added without updating the
        exclude list) fails that table only. The rest still get
        purged, and the caller sees which tables tripped via the
        result dict (missing entries) + the warning log.

        Idempotent: re-running after a successful purge finds zero
        rows and returns an all-zeros map.
        """
        self.initialize()
        org_id = str(organization_id or "").strip()
        if not org_id:
            return {}
        skip = set(self.PURGE_EXCLUDED_TABLES)
        if extra_skip_tables:
            skip.update(str(t) for t in extra_skip_tables)

        tables = [t for t in self.list_org_scoped_tables() if t not in skip]
        counts: Dict[str, int] = {}
        with self.connect() as conn:
            cur = conn.cursor()
            for table in tables:
                try:
                    sql = self._prepare_sql(
                        f"DELETE FROM {table} WHERE organization_id = ?"
                    )
                    cur.execute(sql, (org_id,))
                    counts[table] = int(cur.rowcount or 0)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[purge] %s DELETE failed for org=%s: %s",
                        table, org_id, exc,
                    )
            conn.commit()
        total = sum(counts.values())
        logger.info(
            "[purge] org=%s purged %d rows across %d tables",
            org_id, total, len(counts),
        )
        return counts

    def list_orgs_eligible_for_purge(self, *, legal_hold_days: int) -> List[Dict[str, Any]]:
        """Orgs whose ``deleted_at`` is older than the legal-hold window
        and that have not yet been hard-purged.

        Ordered oldest-first so a run that processes N per tick always
        makes forward progress on the longest-tombstoned rows.
        """
        self.initialize()
        days = max(1, int(legal_hold_days))
        from clearledgr.core.clock import now_utc
        from datetime import timedelta
        cutoff = (now_utc() - timedelta(days=days)).isoformat()
        sql = self._prepare_sql(
            """
            SELECT id, deleted_at, purged_at FROM organizations
             WHERE deleted_at IS NOT NULL
               AND deleted_at < ?
               AND (purged_at IS NULL OR purged_at = '')
             ORDER BY deleted_at ASC
             LIMIT 50
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (cutoff,))
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def get_organization(self, organization_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM organizations WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        settings = self._decode_json_value(data.get("settings_json"), {})
        data["settings"] = settings
        data["settings_json"] = settings
        return data

    def get_organization_by_domain(self, domain: str) -> Optional[Dict[str, Any]]:
        """Look up an organization by its email domain."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM organizations WHERE domain = ? LIMIT 1")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (domain,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        settings = self._decode_json_value(data.get("settings_json"), {})
        data["settings"] = settings
        data["settings_json"] = settings
        return data

    def list_organizations(self, limit: int = 500) -> List[Dict[str, Any]]:
        self.initialize()
        safe_limit = max(1, min(int(limit or 500), 5000))
        sql = self._prepare_sql("SELECT * FROM organizations ORDER BY created_at DESC LIMIT ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (safe_limit,))
            rows = cur.fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            settings = self._decode_json_value(data.get("settings_json"), {})
            data["settings"] = settings
            data["settings_json"] = settings
            result.append(data)
        return result

    _ORGANIZATION_ALLOWED_COLUMNS = frozenset({
        "name", "domain", "settings_json", "settings", "integration_mode",
        "subscription_tier", "is_active", "updated_at",
    })

    def update_organization(
        self,
        organization_id: str,
        *,
        expected_updated_at: Optional[str] = None,
        **kwargs,
    ) -> bool:
        """Update an organization row.

        When ``expected_updated_at`` is provided, the UPDATE runs with
        a WHERE clause that includes the timestamp — optimistic CAS.
        If another process wrote between the caller's read and this
        write, the rowcount is 0 and we return False. The caller is
        expected to re-read and retry (or surface a 409 to the user).

        Without ``expected_updated_at``, behaviour matches the
        pre-CAS era (last-writer-wins). That keeps non-racing callers
        — migrations, single-shot admin actions, CLI tools — working
        without every call needing to thread the token through.
        """
        self.initialize()
        if not kwargs:
            return False
        bad_keys = set(kwargs.keys()) - self._ORGANIZATION_ALLOWED_COLUMNS
        if bad_keys:
            raise ValueError(f"Disallowed columns for organization update: {bad_keys}")
        payload = dict(kwargs)
        if "settings" in payload and "settings_json" not in payload:
            payload["settings_json"] = payload.pop("settings")
        if "settings_json" in payload and isinstance(payload["settings_json"], dict):
            payload["settings_json"] = json.dumps(payload["settings_json"])
        if "integration_mode" in payload and not payload["integration_mode"]:
            payload.pop("integration_mode")
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in payload.keys())
        if expected_updated_at is not None:
            sql = self._prepare_sql(
                f"UPDATE organizations SET {set_clause} "
                "WHERE id = ? AND updated_at = ?"
            )
            params = (*payload.values(), organization_id, expected_updated_at)
        else:
            sql = self._prepare_sql(
                f"UPDATE organizations SET {set_clause} WHERE id = ?"
            )
            params = (*payload.values(), organization_id)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount > 0

    def ensure_organization(
        self,
        organization_id: str,
        organization_name: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        existing = self.get_organization(organization_id)
        if existing:
            return existing
        name = (organization_name or organization_id or "Organization").strip()
        return self.create_organization(
            organization_id=organization_id,
            name=name,
            domain=domain,
            settings={},
            integration_mode="shared",
        )

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def create_user(
        self,
        email: str,
        name: str,
        organization_id: str,
        role: str = "ap_clerk",
        password_hash: Optional[str] = None,
        google_id: Optional[str] = None,
        is_active: bool = True,
    ) -> Dict[str, Any]:
        """Create a user row with a canonical Phase 2.3 thesis role.

        The ``role`` parameter is normalized via ``normalize_user_role``
        before persistence, so legacy values (``user``, ``member``,
        ``admin``, ``operator``, ``viewer``) are automatically upgraded
        to their thesis equivalents (``ap_clerk``, ``ap_clerk``,
        ``financial_controller``, ``ap_manager``, ``read_only``).
        Unknown values are preserved so predicates can reject them.
        Default seat is ``ap_clerk``.
        """
        self.initialize()
        import uuid
        from clearledgr.core.auth import normalize_user_role, ROLE_AP_CLERK

        normalized_role = normalize_user_role(role) or ROLE_AP_CLERK

        now = datetime.now(timezone.utc).isoformat()
        user_id = f"USR-{uuid.uuid4().hex}"
        sql = self._prepare_sql(
            """
            INSERT INTO users
            (id, email, name, organization_id, role, password_hash, google_id, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    user_id,
                    email.lower().strip(),
                    name,
                    organization_id,
                    normalized_role,
                    password_hash,
                    google_id,
                    1 if is_active else 0,
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_user(user_id) or {}

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM users WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        data["is_active"] = bool(data.get("is_active"))
        preferences = self._decode_json_value(data.get("preferences_json"), {})
        data["preferences"] = preferences
        data["preferences_json"] = preferences
        return data

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM users WHERE lower(email) = lower(?) LIMIT 1")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (email.strip(),))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        data["is_active"] = bool(data.get("is_active"))
        preferences = self._decode_json_value(data.get("preferences_json"), {})
        data["preferences"] = preferences
        data["preferences_json"] = preferences
        return data

    def get_user_by_google_id(self, google_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM users WHERE google_id = ? LIMIT 1")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (google_id,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        data["is_active"] = bool(data.get("is_active"))
        preferences = self._decode_json_value(data.get("preferences_json"), {})
        data["preferences"] = preferences
        data["preferences_json"] = preferences
        return data

    def get_user_by_slack_id(self, slack_user_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM users WHERE slack_user_id = ? LIMIT 1")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (slack_user_id,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        data["is_active"] = bool(data.get("is_active"))
        preferences = self._decode_json_value(data.get("preferences_json"), {})
        data["preferences"] = preferences
        data["preferences_json"] = preferences
        return data

    def validate_api_key(self, raw_key: str) -> Optional[Dict[str, Any]]:
        """Look up an API key by its SHA-256 hash.

        Returns the api_keys row dict (with organization_id, user_id, etc.)
        or None if no active key matches.
        """
        self.initialize()
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        sql = self._prepare_sql(
            "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1 LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (key_hash,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        # Touch last_used_at (best-effort, non-blocking)
        try:
            update_sql = self._prepare_sql(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?"
            )
            with self.connect() as conn2:
                conn2.cursor().execute(
                    update_sql, (datetime.now(timezone.utc).isoformat(), data["id"])
                )
                conn2.commit()
        except Exception as e:
            logger.warning("Failed to update API key last_used_at: %s", e)
        return data

    def create_api_key(
        self,
        organization_id: str,
        user_id: str,
        raw_key: str,
        label: str = "",
    ) -> Dict[str, Any]:
        """Store a hashed API key."""
        self.initialize()
        import uuid
        key_id = str(uuid.uuid4())
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        key_prefix = raw_key[:12] + "..."
        now = datetime.now(timezone.utc).isoformat()
        sql = self._prepare_sql(
            """INSERT INTO api_keys
            (id, organization_id, key_hash, key_prefix, user_id, label, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)"""
        )
        with self.connect() as conn:
            conn.cursor().execute(
                sql, (key_id, organization_id, key_hash, key_prefix, user_id, label, now, now)
            )
            conn.commit()
        return {
            "id": key_id,
            "organization_id": organization_id,
            "key_prefix": key_prefix,
            "user_id": user_id,
            "label": label,
        }

    def get_users(self, organization_id: str, include_inactive: bool = False) -> List[Dict[str, Any]]:
        self.initialize()
        if include_inactive:
            sql = self._prepare_sql(
                "SELECT * FROM users WHERE organization_id = ? ORDER BY created_at DESC"
            )
            params = (organization_id,)
        else:
            sql = self._prepare_sql(
                "SELECT * FROM users WHERE organization_id = ? AND is_active = 1 ORDER BY created_at DESC"
            )
            params = (organization_id,)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["is_active"] = bool(data.get("is_active"))
            preferences = self._decode_json_value(data.get("preferences_json"), {})
            data["preferences"] = preferences
            data["preferences_json"] = preferences
            result.append(data)
        return result

    _USER_ALLOWED_COLUMNS = frozenset({
        "name", "email", "password_hash", "role", "is_active",
        "organization_id", "google_id", "preferences_json", "preferences",
        "updated_at", "last_seen_at", "slack_user_id",
        # §5.4 Archived Users
        "archived_at", "archived_by",
        # §13 Subscription: seat type + expiry for Read Only auditors
        "seat_type", "seat_expires_at",
    })

    def update_user(self, user_id: str, **kwargs) -> bool:
        self.initialize()
        if not kwargs:
            return False
        bad_keys = set(kwargs.keys()) - self._USER_ALLOWED_COLUMNS
        if bad_keys:
            raise ValueError(f"Disallowed columns for user update: {bad_keys}")
        payload = dict(kwargs)
        if "is_active" in payload:
            payload["is_active"] = 1 if bool(payload["is_active"]) else 0
        if "preferences" in payload and "preferences_json" not in payload:
            payload["preferences_json"] = payload.pop("preferences")
        if "preferences_json" in payload and isinstance(payload["preferences_json"], dict):
            payload["preferences_json"] = json.dumps(payload["preferences_json"])
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in payload.keys())
        sql = self._prepare_sql(f"UPDATE users SET {set_clause} WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*payload.values(), user_id))
            conn.commit()
            return cur.rowcount > 0

    def get_user_preferences(self, user_id: str) -> Dict[str, Any]:
        user = self.get_user(user_id)
        if not user:
            return {}
        return dict(user.get("preferences") or {})

    def update_user_preferences(self, user_id: str, preferences: Dict[str, Any]) -> bool:
        return self.update_user(user_id, preferences=preferences or {})

    def upsert_google_user(
        self,
        email: str,
        google_id: str,
        organization_id: str,
        name: Optional[str] = None,
        role: str = "user",
    ) -> Dict[str, Any]:
        self.initialize()
        existing = self.get_user_by_email(email)
        display_name = (name or email.split("@")[0].replace(".", " ").title()).strip()
        if existing:
            updates: Dict[str, Any] = {"google_id": google_id, "is_active": True}
            if not existing.get("name"):
                updates["name"] = display_name
            self.update_user(str(existing.get("id")), **updates)
            return self.get_user(str(existing.get("id"))) or existing
        return self.create_user(
            email=email,
            name=display_name,
            organization_id=organization_id,
            role=role,
            password_hash=None,
            google_id=google_id,
            is_active=True,
        )

    def save_user(
        self,
        email: str,
        role: str,
        organization_id: str,
        user_id: Optional[str] = None,
        name: Optional[str] = None,
        password_hash: Optional[str] = None,
        google_id: Optional[str] = None,
        is_active: bool = True,
    ) -> str:
        """Backward-compatible helper used by legacy auth routes."""
        self.initialize()
        import uuid

        existing = self.get_user(user_id) if user_id else None
        row_id = user_id or f"USR-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()

        if existing:
            self.update_user(
                row_id,
                email=email.lower().strip(),
                role=role,
                organization_id=organization_id,
                name=name or existing.get("name") or "",
                password_hash=password_hash if password_hash is not None else existing.get("password_hash"),
                google_id=google_id if google_id is not None else existing.get("google_id"),
                is_active=is_active,
            )
            return row_id

        sql = self._prepare_sql(
            """
            INSERT INTO users
            (id, email, name, organization_id, role, password_hash, google_id, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    row_id,
                    email.lower().strip(),
                    name or email.split("@")[0],
                    organization_id,
                    role,
                    password_hash,
                    google_id,
                    1 if is_active else 0,
                    now,
                    now,
                ),
            )
            conn.commit()
        return row_id

    def delete_user(self, user_id: str, archived_by: Optional[str] = None) -> bool:
        """§5.4 Archived Users: soft-delete + record archival metadata.

        The person is gone from the product. Their record remains.
        All timeline contributions, approvals, overrides, and exception
        resolutions are permanently preserved in the audit trail.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        result = self.update_user(
            user_id,
            is_active=False,
            archived_at=now,
            archived_by=archived_by or "system",
        )

        # Update subscription seat count
        if result:
            try:
                user = self.get_user(user_id)
                org_id = (user or {}).get("organization_id")
                if org_id:
                    self._adjust_subscription_seat_count(org_id)
            except Exception:
                pass  # Non-fatal — billing adjustment is best-effort

        return result

    def _adjust_subscription_seat_count(self, organization_id: str) -> None:
        """§5.4 + §13: Adjust subscription seat count after user archival.

        Counts full seats and Read Only seats separately per §13
        pricing structure (Read Only at reduced rate).
        """
        try:
            sql = self._prepare_sql(
                "SELECT COUNT(*) as cnt FROM users WHERE organization_id = ? AND is_active = 1"
            )
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id,))
                row = cur.fetchone()
            active_count = dict(row).get("cnt", 0) if row else 0

            # Count Read Only seats separately
            ro_sql = self._prepare_sql(
                "SELECT COUNT(*) as cnt FROM users WHERE organization_id = ? AND is_active = 1 AND seat_type = 'read_only'"
            )
            ro_count = 0
            try:
                with self.connect() as conn2:
                    cur2 = conn2.cursor()
                    cur2.execute(ro_sql, (organization_id,))
                    ro_row = cur2.fetchone()
                ro_count = dict(ro_row).get("cnt", 0) if ro_row else 0
            except Exception:
                pass  # seat_type column may not exist yet

            # Update usage in subscription
            sub_sql = self._prepare_sql(
                "SELECT id, usage_json FROM subscriptions WHERE organization_id = ?"
            )
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sub_sql, (organization_id,))
                sub_row = cur.fetchone()
            if sub_row:
                import json
                sub = dict(sub_row)
                usage = json.loads(sub.get("usage_json") or "{}")
                usage["users_count"] = active_count - ro_count  # Full seats only
                usage["read_only_users_count"] = ro_count
                update_sql = self._prepare_sql(
                    "UPDATE subscriptions SET usage_json = ? WHERE id = ?"
                )
                with self.connect() as conn:
                    conn.execute(update_sql, (json.dumps(usage), sub["id"]))
                    conn.commit()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Team invites
    # ------------------------------------------------------------------

    def create_team_invite(
        self,
        organization_id: str,
        email: str,
        role: str,
        created_by: str,
        expires_at: Optional[str],
    ) -> Dict[str, Any]:
        self.initialize()
        import uuid
        import secrets

        now = datetime.now(timezone.utc).isoformat()
        invite_id = f"INV-{uuid.uuid4().hex}"
        token = secrets.token_urlsafe(32)
        sql = self._prepare_sql(
            """
            INSERT INTO team_invites
            (id, organization_id, email, role, token, status, expires_at, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    invite_id,
                    organization_id,
                    email.lower().strip(),
                    role,
                    token,
                    expires_at,
                    created_by,
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_team_invite(invite_id) or {}

    def list_team_invites(self, organization_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM team_invites WHERE organization_id = ? ORDER BY created_at DESC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def get_team_invite(self, invite_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM team_invites WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (invite_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_team_invite_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM team_invites WHERE token = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (token,))
            row = cur.fetchone()
        return dict(row) if row else None

    def revoke_team_invite(self, invite_id: str) -> bool:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = self._prepare_sql(
            "UPDATE team_invites SET status = 'revoked', revoked_at = ?, updated_at = ? WHERE id = ? AND status = 'pending'"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, now, invite_id))
            conn.commit()
            return cur.rowcount > 0

    def accept_team_invite(self, invite_id: str, accepted_by: str) -> bool:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = self._prepare_sql(
            "UPDATE team_invites SET status = 'accepted', accepted_by = ?, accepted_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'pending'"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (accepted_by, now, now, invite_id))
            conn.commit()
            return cur.rowcount > 0
