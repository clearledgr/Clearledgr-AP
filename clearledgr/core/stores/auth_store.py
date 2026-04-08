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

    def update_organization(self, organization_id: str, **kwargs) -> bool:
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
        sql = self._prepare_sql(f"UPDATE organizations SET {set_clause} WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*payload.values(), organization_id))
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
        role: str = "user",
        password_hash: Optional[str] = None,
        google_id: Optional[str] = None,
        is_active: bool = True,
    ) -> Dict[str, Any]:
        self.initialize()
        import uuid

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
                    role or "user",
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

    def delete_user(self, user_id: str) -> bool:
        return self.update_user(user_id, is_active=False)

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
