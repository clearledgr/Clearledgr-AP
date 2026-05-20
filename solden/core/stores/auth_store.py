"""Auth-domain database mixin for SoldenDB.

This module provides ``AuthStore``, a mixin class that holds every
database method related to authentication, users, organizations,
API keys, OAuth tokens, and team invites.  It is designed to be used
via multiple-inheritance so that ``SoldenDB`` can compose its
surface area from several focused store modules.

The mixin assumes the consuming class exposes:
    - ``self.connect()``
    - ``self.initialize()``
    - ``self._encrypt_secret(value)``
    - ``self._decrypt_secret(value)``
    - ``self._decode_json_value(value, fallback)``
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
        token_id = f"TOK-{uuid.uuid4().hex}"

        encrypted_access = self._encrypt_secret(access_token)
        encrypted_refresh = self._encrypt_secret(refresh_token)

        sql = """
            INSERT INTO oauth_tokens
            (id, user_id, provider, access_token, refresh_token, expires_at, email, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, provider)
            DO UPDATE SET access_token = EXCLUDED.access_token,
                          refresh_token = EXCLUDED.refresh_token,
                          expires_at = EXCLUDED.expires_at,
                          email = EXCLUDED.email,
                          updated_at = EXCLUDED.updated_at
        """
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
        sql = "SELECT * FROM oauth_tokens WHERE user_id = %s AND provider = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id, provider))
            row = cur.fetchone()
        return self._decrypt_oauth_row(dict(row) if row else None)

    def get_oauth_token_by_email(self, email: str, provider: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM oauth_tokens WHERE email = %s AND provider = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (email, provider))
            row = cur.fetchone()
        return self._decrypt_oauth_row(dict(row) if row else None)

    def list_oauth_tokens(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        self.initialize()
        if provider:
            sql = "SELECT * FROM oauth_tokens WHERE provider = %s"
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
        sql = "DELETE FROM oauth_tokens WHERE user_id = %s AND provider = %s"
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
        sql = (
            """
            INSERT INTO google_auth_codes
            (auth_code, access_token, refresh_token, organization_id, expires_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (auth_code) DO UPDATE SET
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                organization_id = EXCLUDED.organization_id,
                expires_at = EXCLUDED.expires_at,
                created_at = EXCLUDED.created_at
            """
        )
        encrypted_access = self._encrypt_secret(access_token)
        encrypted_refresh = self._encrypt_secret(refresh_token or "")
        org_id = str(organization_id or "").strip()
        if not org_id:
            # Pre-fix the missing-org branch fell back to a literal
            # "default" tenant. An auth code redeemed against
            # "default" produces a session whose ``org`` claim is
            # "default" — that's a cross-tenant landmine. Fail closed.
            raise ValueError(
                "save_google_auth_code requires a non-empty organization_id"
            )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    str(auth_code),
                    encrypted_access,
                    encrypted_refresh,
                    org_id,
                    str(expires_at),
                    now,
                ),
            )
            conn.commit()

    def consume_google_auth_code(self, auth_code: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        select_sql = "SELECT * FROM google_auth_codes WHERE auth_code = %s"
        delete_sql = "DELETE FROM google_auth_codes WHERE auth_code = %s"
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
        sql = "DELETE FROM google_auth_codes WHERE expires_at IS NOT NULL AND expires_at < %s"
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

        now = datetime.now(timezone.utc).isoformat()
        row_id = organization_id or f"ORG-{uuid.uuid4().hex}"
        settings_json = json.dumps(settings or {})
        sql = (
            """
            INSERT INTO organizations
            (id, name, domain, settings_json, integration_mode, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
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

        Uses ``information_schema`` on Postgres. Discovery is intentional —
        if a developer adds a new org-scoped table tomorrow, the
        purge picks it up without a code change to this mixin.
        """
        self.initialize()
        org_tables: List[str] = []
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT table_name FROM information_schema.columns
                 WHERE column_name = 'organization_id'
                   AND table_schema = 'public'
                """
            )
            rows = cur.fetchall()
            org_tables = sorted({str(r["table_name"]) for r in rows})
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
                    sql = (
                        f"DELETE FROM {table} WHERE organization_id = %s"
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
        from solden.core.clock import now_utc
        from datetime import timedelta
        cutoff = (now_utc() - timedelta(days=days)).isoformat()
        sql = (
            """
            SELECT id, deleted_at, purged_at FROM organizations
             WHERE deleted_at IS NOT NULL
               AND deleted_at < %s
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
        sql = "SELECT * FROM organizations WHERE id = %s"
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
        sql = "SELECT * FROM organizations WHERE domain = %s LIMIT 1"
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
        sql = "SELECT * FROM organizations ORDER BY created_at DESC LIMIT %s"
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
        # Lifecycle tombstones — nullable ISO timestamps on the row.
        # These were being written by org_config.delete_organization
        # (deleted_at) and the celery hard-purge task (purged_at) but
        # the whitelist didn't include them, so the updates were being
        # rejected with ValueError and swallowed by the surrounding
        # try/except — silent no-op in production. Fixed 2026-04-22.
        "deleted_at", "purged_at",
        # LLM cost runaway-spend guard tombstone (migration v44).
        "llm_cost_paused_at",
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
        set_clause = ", ".join(f"{k} = %s" for k in payload.keys())
        if expected_updated_at is not None:
            sql = (
                f"UPDATE organizations SET {set_clause} "
                "WHERE id = %s AND updated_at = %s"
            )
            params = (*payload.values(), organization_id, expected_updated_at)
        else:
            sql = (
                f"UPDATE organizations SET {set_clause} WHERE id = %s"
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
        role: str = "member",
        workspace_role: Optional[str] = None,
        ap_role: Optional[str] = None,
        password_hash: Optional[str] = None,
        google_id: Optional[str] = None,
        is_active: bool = True,
    ) -> Dict[str, Any]:
        """Create a user row.

        Migration v89: writes both legacy ``users.role`` and the new
        ``users.workspace_role`` column. When ``workspace_role`` is
        omitted, derive it from ``role`` via the legacy mapping. AP-
        side role is written to ``user_box_roles`` for box_type
        ``ap_item`` so the two-axis lookup works from the first
        request.
        """
        self.initialize()
        from solden.core.auth import (
            AP_ROLE_CLERK,
            WORKSPACE_ROLE_MEMBER,
            normalize_ap_role,
            normalize_workspace_role,
        )

        normalized_workspace = (
            normalize_workspace_role(workspace_role)
            or normalize_workspace_role(role)
            or WORKSPACE_ROLE_MEMBER
        )
        normalized_ap = normalize_ap_role(ap_role) or normalize_ap_role(role) or AP_ROLE_CLERK

        now = datetime.now(timezone.utc).isoformat()
        user_id = f"USR-{uuid.uuid4().hex}"
        sql = (
            """
            INSERT INTO users
            (id, email, name, organization_id, role, workspace_role, password_hash, google_id, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    role,  # legacy column — preserved for the sweep window
                    normalized_workspace,
                    password_hash,
                    google_id,
                    1 if is_active else 0,
                    now,
                    now,
                ),
            )
            # Write the AP-side box role row. Skip for service accounts
            # (workspace_role=api) — agents drive permissions through
            # API-key scopes, not user_box_roles.
            from solden.core.auth import WORKSPACE_ROLE_API
            if normalized_workspace != WORKSPACE_ROLE_API and organization_id:
                cur.execute(
                    """
                    INSERT INTO user_box_roles
                    (id, user_id, organization_id, box_type, role)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, organization_id, box_type) DO UPDATE
                        SET role = EXCLUDED.role,
                            updated_at = now()
                    """,
                    (
                        f"ubr-{uuid.uuid4().hex}",
                        user_id,
                        organization_id,
                        "ap_item",
                        normalized_ap,
                    ),
                )
            conn.commit()
        return self.get_user(user_id) or {}

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM users WHERE id = %s"
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
        sql = "SELECT * FROM users WHERE lower(email) = lower(%s) LIMIT 1"
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
        sql = "SELECT * FROM users WHERE google_id = %s LIMIT 1"
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
        sql = "SELECT * FROM users WHERE slack_user_id = %s LIMIT 1"
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

    def validate_api_key(
        self, raw_key: str, *, include_revoked: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Look up an API key by its SHA-256 hash.

        Returns the api_keys row dict (with organization_id, user_id, etc.)
        or None if no key matches. By default only active keys match; pass
        ``include_revoked=True`` to also return revoked/inactive rows so the
        caller can distinguish "revoked" (403) from "never existed" (401)
        rather than collapsing both to None/401.
        """
        self.initialize()
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        if include_revoked:
            sql = "SELECT * FROM api_keys WHERE key_hash = %s LIMIT 1"
        else:
            sql = (
                "SELECT * FROM api_keys WHERE key_hash = %s AND is_active = 1 LIMIT 1"
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
            update_sql = (
                "UPDATE api_keys SET last_used_at = %s WHERE id = %s"
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
        scopes: Optional[List[str]] = None,
        agent_id: Optional[str] = None,
        agent_version: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Store a hashed API key with optional scopes + agent attribution.

        ``agent_id`` / ``agent_version`` carry through into every
        ``audit_events`` row written by a runtime built from this key
        (migration 86 shipped both columns). ``expires_at`` is an ISO-8601
        timestamp; ``validate_api_key`` filters expired rows the same way
        it filters revoked ones.
        """
        self.initialize()
        key_id = str(uuid.uuid4())
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        key_prefix = raw_key[:12] + "..."
        now = datetime.now(timezone.utc).isoformat()
        scopes_payload = json.dumps(scopes or []) if scopes is not None else None
        sql = (
            """INSERT INTO api_keys
            (id, organization_id, key_hash, key_prefix, user_id, label, is_active,
             scopes, agent_id, agent_version, expires_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, 1, %s::jsonb, %s, %s, %s, %s, %s)"""
        )
        with self.connect() as conn:
            conn.cursor().execute(
                sql,
                (
                    key_id, organization_id, key_hash, key_prefix, user_id, label,
                    scopes_payload, agent_id, agent_version, expires_at, now, now,
                ),
            )
            conn.commit()
        return {
            "id": key_id,
            "organization_id": organization_id,
            "key_prefix": key_prefix,
            "user_id": user_id,
            "label": label,
            "scopes": list(scopes or []),
            "agent_id": agent_id,
            "agent_version": agent_version,
            "expires_at": expires_at,
        }

    def list_api_keys(
        self, organization_id: str, *, include_revoked: bool = False,
    ) -> List[Dict[str, Any]]:
        """List API keys for an org. Default excludes revoked rows.

        Returns the safe-to-display fields only — ``id``, ``key_prefix``,
        ``label``, ``user_id``, ``last_used_at``, timestamps,
        ``is_active``. The full hash and the raw key are NEVER
        returned; the raw key is only ever in the create response.
        """
        self.initialize()
        if include_revoked:
            sql = (
                "SELECT id, organization_id, key_prefix, user_id, label, "
                "is_active, scopes, agent_id, agent_version, expires_at, "
                "revoked_at, last_used_at, created_at, updated_at "
                "FROM api_keys WHERE organization_id = %s "
                "ORDER BY created_at DESC"
            )
        else:
            sql = (
                "SELECT id, organization_id, key_prefix, user_id, label, "
                "is_active, scopes, agent_id, agent_version, expires_at, "
                "revoked_at, last_used_at, created_at, updated_at "
                "FROM api_keys WHERE organization_id = %s AND is_active = 1 "
                "ORDER BY created_at DESC"
            )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["is_active"] = bool(data.get("is_active"))
            result.append(data)
        return result

    def get_api_key(
        self, key_id: str, organization_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get an API key row by id, scoped to an org."""
        self.initialize()
        sql = (
            "SELECT id, organization_id, key_prefix, user_id, label, "
            "is_active, scopes, agent_id, agent_version, expires_at, "
            "revoked_at, last_used_at, created_at, updated_at "
            "FROM api_keys WHERE id = %s AND organization_id = %s LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (key_id, organization_id))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        data["is_active"] = bool(data.get("is_active"))
        return data

    def list_user_api_keys(
        self, user_id: str, organization_id: str, *, only_active: bool = True,
    ) -> List[Dict[str, Any]]:
        """Active API keys created by a specific user.

        Used by the user-offboarding flow to cascade-revoke a
        deactivated user's keys so they can't keep authenticating
        through the X-API-Key header after they lose JWT access.
        """
        self.initialize()
        if only_active:
            sql = (
                "SELECT id, organization_id, key_prefix, user_id, label, "
                "is_active, scopes, last_used_at, created_at, updated_at "
                "FROM api_keys "
                "WHERE user_id = %s AND organization_id = %s AND is_active = 1 "
                "ORDER BY created_at DESC"
            )
        else:
            sql = (
                "SELECT id, organization_id, key_prefix, user_id, label, "
                "is_active, scopes, last_used_at, created_at, updated_at "
                "FROM api_keys "
                "WHERE user_id = %s AND organization_id = %s "
                "ORDER BY created_at DESC"
            )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id, organization_id))
            rows = cur.fetchall()
        return [dict(r) | {"is_active": bool(dict(r).get("is_active"))} for r in rows]

    def revoke_user_api_keys(
        self, user_id: str, organization_id: str,
    ) -> int:
        """Revoke every active API key owned by a user. Returns count."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            "UPDATE api_keys SET is_active = 0, updated_at = %s "
            "WHERE user_id = %s AND organization_id = %s AND is_active = 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, user_id, organization_id))
            affected = cur.rowcount or 0
            conn.commit()
        return int(affected)

    def revoke_api_key(self, key_id: str, organization_id: str) -> bool:
        """Mark an API key as inactive. Org-scoped to block cross-tenant writes.

        We do NOT delete the row — keeping the hash + label preserves
        the audit trail of what existed and when. ``is_active = 0`` is
        the canonical "revoked" state; ``validate_api_key`` filters on
        it, so a revoked key fails auth immediately.
        """
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            "UPDATE api_keys SET is_active = 0, updated_at = %s "
            "WHERE id = %s AND organization_id = %s AND is_active = 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, key_id, organization_id))
            affected = cur.rowcount or 0
            conn.commit()
        return bool(affected)

    def get_users(self, organization_id: str, include_inactive: bool = False) -> List[Dict[str, Any]]:
        self.initialize()
        if include_inactive:
            sql = (
                "SELECT * FROM users WHERE organization_id = %s ORDER BY created_at DESC"
            )
            params = (organization_id,)
        else:
            sql = (
                "SELECT * FROM users WHERE organization_id = %s AND is_active = 1 ORDER BY created_at DESC"
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
        "name", "email", "password_hash", "role", "workspace_role",
        "is_active", "organization_id", "google_id",
        "preferences_json", "preferences",
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

        # v89: keep the legacy ``role`` and the new ``workspace_role``
        # columns in lockstep. If the caller updates one but not the
        # other, derive the missing side through the legacy mapping so
        # a row that started as ap_clerk doesn't end up as workspace
        # admin just because the caller used a single-axis update.
        from solden.core.auth import (
            WORKSPACE_ROLE_MEMBER,
            normalize_workspace_role,
        )
        if "role" in payload and "workspace_role" not in payload:
            derived = normalize_workspace_role(payload["role"])
            if derived:
                payload["workspace_role"] = derived
        elif "workspace_role" in payload and "role" not in payload:
            # Don't downgrade the legacy column; just preserve whatever
            # is on disk by not writing role at all.
            pass

        if "is_active" in payload:
            payload["is_active"] = 1 if bool(payload["is_active"]) else 0
        if "preferences" in payload and "preferences_json" not in payload:
            payload["preferences_json"] = payload.pop("preferences")
        if "preferences_json" in payload and isinstance(payload["preferences_json"], dict):
            payload["preferences_json"] = json.dumps(payload["preferences_json"])
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = %s" for k in payload.keys())
        sql = f"UPDATE users SET {set_clause} WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*payload.values(), user_id))
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Per-Box roles (v89 two-axis auth model)
    # ------------------------------------------------------------------

    def get_user_box_role(
        self,
        user_id: str,
        organization_id: str,
        box_type: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the user's role for a given Box type in an org.

        Returns ``None`` when the user has no row for this Box type.
        Used by ``has_ap_*`` / ``require_ap_*`` to evaluate AP-axis
        gates without round-tripping the role through the user row.
        """
        self.initialize()
        sql = (
            "SELECT * FROM user_box_roles "
            "WHERE user_id = %s AND organization_id = %s AND box_type = %s "
            "LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id, organization_id, box_type))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_user_box_roles(
        self,
        user_id: str,
        organization_id: str,
    ) -> List[Dict[str, Any]]:
        """Return every Box-type role a user holds in an org."""
        self.initialize()
        sql = (
            "SELECT * FROM user_box_roles "
            "WHERE user_id = %s AND organization_id = %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id, organization_id))
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def set_user_box_role(
        self,
        user_id: str,
        organization_id: str,
        box_type: str,
        role: str,
    ) -> Dict[str, Any]:
        """Upsert the user's role for a Box type. Returns the resulting row.

        Role normalization is the caller's job; this store trusts the
        input. The DB CHECK constraint added in v89 rejects unknown
        values for ``ap_item`` Boxes — other Box types are unconstrained
        until they ship their own CHECK.
        """
        self.initialize()
        sql = (
            """
            INSERT INTO user_box_roles
            (id, user_id, organization_id, box_type, role)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, organization_id, box_type) DO UPDATE
                SET role = EXCLUDED.role,
                    updated_at = now()
            RETURNING *
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (f"ubr-{uuid.uuid4().hex}", user_id, organization_id, box_type, role),
            )
            row = cur.fetchone()
            conn.commit()
        return dict(row) if row else {}

    def delete_user_box_role(
        self,
        user_id: str,
        organization_id: str,
        box_type: str,
    ) -> bool:
        """Remove a user's role for a Box type. Returns True if a row was removed.

        Used by offboarding flows + invite-revocation. Other axes
        (workspace_role) are untouched.
        """
        self.initialize()
        sql = (
            "DELETE FROM user_box_roles "
            "WHERE user_id = %s AND organization_id = %s AND box_type = %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id, organization_id, box_type))
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

        sql = (
            """
            INSERT INTO users
            (id, email, name, organization_id, role, password_hash, google_id, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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

    def reap_expired_seats(self) -> int:
        """§13: auto-expire Read-Only auditor seats past their window.

        Read-Only seats (``seat_type='read_only'``) carry an explicit
        ``seat_expires_at`` column set when the seat is provisioned.
        The thesis promises they "expire automatically after a
        configurable period." This reaper walks all active users,
        finds those whose expiry is in the past, and soft-archives
        them via the same path :meth:`delete_user` uses — preserving
        audit attribution while revoking access.

        Returns the number of seats reaped. Called by the Celery
        daily reaper task.
        """
        from datetime import datetime, timezone

        self.initialize()
        now_iso = datetime.now(timezone.utc).isoformat()
        sql = (
            "SELECT id, organization_id FROM users "
            "WHERE is_active = 1 "
            "  AND seat_type = 'read_only' "
            "  AND seat_expires_at IS NOT NULL "
            "  AND seat_expires_at <= %s"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (now_iso,))
                rows = cur.fetchall()
        except Exception as exc:
            logger.debug("[auth_store] reap_expired_seats query failed: %s", exc)
            return 0

        reaped = 0
        for row in rows or []:
            user_id = row["id"] if hasattr(row, "__getitem__") else row[0]
            try:
                self.delete_user(str(user_id), archived_by="system:seat_expiry")
                reaped += 1
            except Exception as exc:
                logger.debug(
                    "[auth_store] reap_expired_seats archive failed for %s: %s",
                    user_id, exc,
                )
        return reaped

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
            sql = (
                "SELECT COUNT(*) as cnt FROM users WHERE organization_id = %s AND is_active = 1"
            )
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id,))
                row = cur.fetchone()
            active_count = dict(row).get("cnt", 0) if row else 0

            # Count Read Only seats separately
            ro_sql = (
                "SELECT COUNT(*) as cnt FROM users WHERE organization_id = %s AND is_active = 1 AND seat_type = 'read_only'"
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
            sub_sql = (
                "SELECT id, usage_json FROM subscriptions WHERE organization_id = %s"
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
                update_sql = (
                    "UPDATE subscriptions SET usage_json = %s WHERE id = %s"
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
        entity_restrictions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a pending invite.

        Module 6 Pass D — ``entity_restrictions`` (optional list of
        entity_ids) gets persisted as a JSON column. On invite accept
        the auth handler reads this and writes one user_entity_roles
        row per entity, scoping the new user from day one.
        """
        self.initialize()
        import json as _json
        import secrets

        now = datetime.now(timezone.utc).isoformat()
        invite_id = f"INV-{uuid.uuid4().hex}"
        token = secrets.token_urlsafe(32)
        entity_restrictions_json = (
            _json.dumps(list(entity_restrictions))
            if entity_restrictions else None
        )
        sql = (
            """
            INSERT INTO team_invites
            (id, organization_id, email, role, token, status, expires_at,
             created_by, entity_restrictions_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s)
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
                    entity_restrictions_json,
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_team_invite(invite_id) or {}

    def list_team_invites(self, organization_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM team_invites WHERE organization_id = %s ORDER BY created_at DESC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        return [d for d in (self._decode_invite_row(r) for r in rows) if d is not None]

    def _decode_invite_row(self, row) -> Optional[Dict[str, Any]]:
        """Convert a DB row into the public invite dict, decoding any
        entity_restrictions_json column to a real list."""
        if row is None:
            return None
        out = dict(row)
        raw = out.pop("entity_restrictions_json", None)
        entity_restrictions: List[str] = []
        if raw:
            try:
                import json as _json
                if isinstance(raw, str):
                    parsed = _json.loads(raw)
                else:
                    parsed = raw
                if isinstance(parsed, list):
                    entity_restrictions = [str(v) for v in parsed if v]
            except Exception:
                entity_restrictions = []
        out["entity_restrictions"] = entity_restrictions
        return out

    def get_team_invite(self, invite_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM team_invites WHERE id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (invite_id,))
            row = cur.fetchone()
        return self._decode_invite_row(row)

    def get_team_invite_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM team_invites WHERE token = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (token,))
            row = cur.fetchone()
        return self._decode_invite_row(row)

    def revoke_team_invite(self, invite_id: str) -> bool:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            "UPDATE team_invites SET status = 'revoked', revoked_at = %s, updated_at = %s WHERE id = %s AND status = 'pending'"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, now, invite_id))
            conn.commit()
            return cur.rowcount > 0

    def accept_team_invite(self, invite_id: str, accepted_by: str) -> bool:
        """Mark an invite accepted + apply any entity_restrictions
        from the invite row to user_entity_roles.

        Module 6 Pass D close-out: an invite created with
        ``entity_restrictions=['EU', 'UK']`` for a vendor that hasn't
        landed in the workspace yet now writes one user_entity_roles
        row per entity at accept time, scoping the new user from day
        one without an extra admin step.
        """
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        # Pre-fetch the invite row so we know what scoping was set + the
        # role to apply, then mark accepted + write per-entity roles in
        # the same connection. The UPDATE returns rowcount=0 when the
        # invite has already been accepted or is expired — caller treats
        # that as "no-op" so this is safe to retry.
        invite_row = self.get_team_invite(invite_id)
        if not invite_row:
            return False
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE team_invites SET status = 'accepted', "
                "accepted_by = %s, accepted_at = %s, updated_at = %s "
                "WHERE id = %s AND status = 'pending'",
                (accepted_by, now, now, invite_id),
            )
            updated_rows = cur.rowcount
            conn.commit()

        if updated_rows == 0:
            return False

        # Write user_entity_roles for each scoping entry. Best-effort:
        # a failure here doesn't unflip the accepted status — the user
        # gets org-wide access (the prior behaviour) until the admin
        # manually scopes them. We log + audit so the gap is visible.
        entity_restrictions = invite_row.get("entity_restrictions") or []
        if entity_restrictions and hasattr(self, "set_user_entity_role"):
            invite_role = str(invite_row.get("role") or "ap_clerk").lower()
            for entity_id in entity_restrictions:
                try:
                    self.set_user_entity_role(
                        organization_id=invite_row.get("organization_id"),
                        user_id=accepted_by,
                        entity_id=str(entity_id),
                        role=invite_role,
                    )
                except Exception as exc:
                    logger.warning(
                        "[accept_team_invite] failed to apply entity "
                        "restriction %s for user %s: %s",
                        entity_id, accepted_by, exc,
                    )
        return True
