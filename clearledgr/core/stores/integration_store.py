"""Integration-domain data-access mixin for ClearledgrDB.

``IntegrationStore`` is a **mixin class** -- it has no ``__init__`` of its own
and expects the concrete class that inherits it to provide:

* ``self.connect()``               -- returns a DB connection (context manager)
* ``self._prepare_sql()``          -- adapts ``?`` placeholders for the active engine
* ``self.initialize()``            -- ensures tables exist
* ``self._decode_json_value()``    -- safely parses a JSON string or returns ``{}``
* ``self._encrypt_secret()``       -- Fernet-encrypts a plaintext secret
* ``self._decrypt_secret()``       -- Fernet-decrypts an encrypted secret
* ``self.use_postgres``            -- bool flag for Postgres vs SQLite dialect

All methods are copied verbatim from ``clearledgr/core/database.py`` so that
``ClearledgrDB(IntegrationStore, ...)`` inherits them without any behavioural change.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class IntegrationStore:
    # ------------------------------------------------------------------
    # Gmail autopilot state
    # ------------------------------------------------------------------

    def get_gmail_autopilot_state(self, user_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM gmail_autopilot_state WHERE user_id = %s"
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

        sql = """
            INSERT INTO gmail_autopilot_state
            (user_id, email, last_history_id, watch_expiration, last_watch_at, last_scan_at, last_error, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET email = EXCLUDED.email,
                          last_history_id = EXCLUDED.last_history_id,
                          watch_expiration = EXCLUDED.watch_expiration,
                          last_watch_at = EXCLUDED.last_watch_at,
                          last_scan_at = EXCLUDED.last_scan_at,
                          last_error = EXCLUDED.last_error,
                          updated_at = EXCLUDED.updated_at
        """
        params = (user_id, email, last_history_id, watch_expiration, last_watch_at, last_scan_at, last_error, now)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

    # ------------------------------------------------------------------
    # Outlook autopilot state
    # ------------------------------------------------------------------

    def get_outlook_autopilot_state(self, user_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM outlook_autopilot_state WHERE user_id = %s"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_outlook_autopilot_states(self) -> List[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM outlook_autopilot_state"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
        return [dict(row) for row in rows]

    def save_outlook_autopilot_state(
        self,
        user_id: str,
        email: Optional[str] = None,
        subscription_id: Optional[str] = None,
        subscription_expiration: Optional[str] = None,
        last_scan_at: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()

        sql = """
            INSERT INTO outlook_autopilot_state
            (user_id, email, subscription_id, subscription_expiration, last_scan_at, last_error, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET email = EXCLUDED.email,
                          subscription_id = EXCLUDED.subscription_id,
                          subscription_expiration = EXCLUDED.subscription_expiration,
                          last_scan_at = EXCLUDED.last_scan_at,
                          last_error = EXCLUDED.last_error,
                          updated_at = EXCLUDED.updated_at
        """
        params = (user_id, email, subscription_id, subscription_expiration, last_scan_at, last_error, now)

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
        # Encrypt sensitive fields at rest
        encrypted_access = self._encrypt_secret(access_token) if access_token else None
        encrypted_refresh = self._encrypt_secret(refresh_token) if refresh_token else None
        encrypted_creds = self._encrypt_secret(credentials_json) if credentials_json else None

        sql = """
            INSERT INTO erp_connections
            (id, organization_id, erp_type, access_token, refresh_token, realm_id, tenant_id, base_url,
             credentials, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
            ON CONFLICT (organization_id, erp_type)
            DO UPDATE SET access_token = EXCLUDED.access_token,
                          refresh_token = EXCLUDED.refresh_token,
                          realm_id = EXCLUDED.realm_id,
                          tenant_id = EXCLUDED.tenant_id,
                          base_url = EXCLUDED.base_url,
                          credentials = EXCLUDED.credentials,
                          is_active = 1,
                          updated_at = EXCLUDED.updated_at
        """
        params = (connection_id, organization_id, erp_type, encrypted_access, encrypted_refresh, realm_id,
                  tenant_id, base_url, encrypted_creds, now, now)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

    def _decrypt_erp_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Decrypt ERP connection credentials with legacy unencrypted fallback."""
        result = dict(row)
        for field in ("access_token", "refresh_token"):
            if result.get(field):
                try:
                    result[field] = self._decrypt_secret(result[field])
                except Exception:
                    pass  # Legacy unencrypted data — return as-is
        if result.get("credentials"):
            try:
                decrypted = self._decrypt_secret(result["credentials"])
                result["credentials"] = decrypted
            except Exception:
                pass  # Legacy unencrypted — return as-is
        return result

    def get_erp_connections(self, organization_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM erp_connections WHERE organization_id = %s AND is_active = 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        return [self._decrypt_erp_row(dict(row)) for row in rows]

    def get_erp_connection_for_entity(
        self,
        organization_id: str,
        entity_id: str,
        erp_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """§3 Multi-entity: get ERP connection scoped to a specific entity.

        Falls back to the org-level connection if no entity-specific one exists.
        """
        self.initialize()
        # Try entity-specific first
        if erp_type:
            sql = (
                "SELECT * FROM erp_connections WHERE organization_id = %s AND entity_id = %s AND erp_type = %s AND is_active = 1 LIMIT 1"
            )
            params = (organization_id, entity_id, erp_type)
        else:
            sql = (
                "SELECT * FROM erp_connections WHERE organization_id = %s AND entity_id = %s AND is_active = 1 LIMIT 1"
            )
            params = (organization_id, entity_id)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
        if row:
            return self._decrypt_erp_row(dict(row))

        # Fallback: org-level connection (entity_id IS NULL or empty)
        if erp_type:
            sql = (
                "SELECT * FROM erp_connections WHERE organization_id = %s AND (entity_id IS NULL OR entity_id = '') AND erp_type = %s AND is_active = 1 LIMIT 1"
            )
            params = (organization_id, erp_type)
        else:
            sql = (
                "SELECT * FROM erp_connections WHERE organization_id = %s AND (entity_id IS NULL OR entity_id = '') AND is_active = 1 LIMIT 1"
            )
            params = (organization_id,)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
        if row:
            return self._decrypt_erp_row(dict(row))
        return None

    def get_erp_connection_by_id(self, connection_id: str) -> Optional[Dict[str, Any]]:
        """Get a single ERP connection by its ID."""
        self.initialize()
        sql = "SELECT * FROM erp_connections WHERE id = %s AND is_active = 1"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (connection_id,))
            row = cur.fetchone()
        if not row:
            return None
        return self._decrypt_erp_row(dict(row))

    def save_erp_connection_for_entity(
        self,
        organization_id: str,
        erp_type: str,
        entity_id: str,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        realm_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        base_url: Optional[str] = None,
        credentials: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Save an ERP connection scoped to a specific entity. Returns the connection ID."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        import uuid
        connection_id = f"ERP-{uuid.uuid4().hex}"
        credentials_json = json.dumps(credentials) if credentials else None
        encrypted_access = self._encrypt_secret(access_token) if access_token else None
        encrypted_refresh = self._encrypt_secret(refresh_token) if refresh_token else None
        encrypted_creds = self._encrypt_secret(credentials_json) if credentials_json else None

        sql = """
            INSERT INTO erp_connections
            (id, organization_id, erp_type, entity_id, access_token, refresh_token, realm_id, tenant_id, base_url,
             credentials, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
        """
        params = (connection_id, organization_id, erp_type, entity_id, encrypted_access, encrypted_refresh, realm_id,
                  tenant_id, base_url, encrypted_creds, now, now)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
        return connection_id

    def delete_erp_connection(self, organization_id: str, erp_type: str) -> bool:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            "UPDATE erp_connections SET is_active = 0, updated_at = %s WHERE organization_id = %s AND erp_type = %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, organization_id, erp_type))
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Slack installations
    # ------------------------------------------------------------------

    def upsert_slack_installation(
        self,
        organization_id: str,
        team_id: str,
        team_name: Optional[str],
        bot_user_id: Optional[str],
        bot_token: Optional[str],
        scope_csv: Optional[str],
        user_scope_csv: Optional[str] = None,
        user_token: Optional[str] = None,
        mode: str = "per_org",
        is_active: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.initialize()
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_slack_installation(organization_id)
        row_id = (existing or {}).get("id") or f"SLK-{uuid.uuid4().hex}"
        token_encrypted = self._encrypt_secret(bot_token) if bot_token else None
        metadata_payload = dict(metadata or {})
        if user_scope_csv is not None:
            metadata_payload["user_scope_csv"] = user_scope_csv
        if user_token:
            metadata_payload["user_token_encrypted"] = self._encrypt_secret(user_token)

        sql = (
            """
            INSERT INTO slack_installations
            (id, organization_id, team_id, team_name, bot_user_id, bot_token_encrypted, scope_csv, mode,
             is_active, metadata_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, team_id)
            DO UPDATE SET team_name = EXCLUDED.team_name,
                          bot_user_id = EXCLUDED.bot_user_id,
                          bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                          scope_csv = EXCLUDED.scope_csv,
                          mode = EXCLUDED.mode,
                          is_active = EXCLUDED.is_active,
                          metadata_json = EXCLUDED.metadata_json,
                          updated_at = EXCLUDED.updated_at
            """
        )

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    row_id,
                    organization_id,
                    team_id,
                    team_name,
                    bot_user_id,
                    token_encrypted,
                    scope_csv,
                    mode or "per_org",
                    1 if is_active else 0,
                    json.dumps(metadata_payload),
                    (existing or {}).get("created_at") or now,
                    now,
                ),
            )
            conn.commit()
        self.upsert_organization_integration(
            organization_id=organization_id,
            integration_type="slack",
            status="connected",
            mode=mode or "per_org",
            metadata={
                "team_id": team_id,
                "team_name": team_name,
                "scope_csv": scope_csv,
                "user_scope_csv": user_scope_csv,
            },
            last_sync_at=now,
        )
        return self.get_slack_installation(organization_id) or {}

    def get_slack_installation(
        self,
        organization_id: str,
        include_secrets: bool = False,
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM slack_installations WHERE organization_id = %s AND is_active = 1 ORDER BY updated_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        data["is_active"] = bool(data.get("is_active"))
        data["metadata"] = self._decode_json_value(data.get("metadata_json"), {})
        if isinstance(data["metadata"], dict):
            encrypted_user_token = data["metadata"].pop("user_token_encrypted", None)
        else:
            encrypted_user_token = None
        if include_secrets:
            data["bot_token"] = self._decrypt_secret(data.get("bot_token_encrypted"))
            data["user_token"] = self._decrypt_secret(encrypted_user_token) if encrypted_user_token else None
        else:
            data["bot_token"] = None
            data["user_token"] = None
        return data

    def get_slack_installation_by_team(
        self,
        team_id: str,
        include_secrets: bool = False,
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM slack_installations WHERE team_id = %s AND is_active = 1 ORDER BY updated_at DESC LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (team_id,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        data["is_active"] = bool(data.get("is_active"))
        data["metadata"] = self._decode_json_value(data.get("metadata_json"), {})
        if isinstance(data["metadata"], dict):
            encrypted_user_token = data["metadata"].pop("user_token_encrypted", None)
        else:
            encrypted_user_token = None
        if include_secrets:
            data["bot_token"] = self._decrypt_secret(data.get("bot_token_encrypted"))
            data["user_token"] = self._decrypt_secret(encrypted_user_token) if encrypted_user_token else None
        else:
            data["bot_token"] = None
            data["user_token"] = None
        return data

    def deactivate_slack_installation(self, team_id: str) -> int:
        """Mark every installation row for a Slack team as inactive.

        Called when Slack fires `app_uninstalled` or `tokens_revoked`
        — at that point the bot token is dead and any outbound Slack
        call will 401 forever. Leaving the row is_active=1 keeps our
        notification retry loop hammering a dead endpoint. Flipping
        is_active=0 is enough; we keep the row for audit so we can
        see "this workspace was installed and uninstalled on these
        dates" without a tombstone table.

        Returns the number of rows touched (usually 1; 0 if we never
        had an installation for this team).
        """
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = (
            """
            UPDATE slack_installations
               SET is_active = 0, updated_at = %s
             WHERE team_id = %s
            """
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, team_id))
            affected = int(cur.rowcount or 0)
            conn.commit()
        return affected

    # ------------------------------------------------------------------
    # Organization integrations
    # ------------------------------------------------------------------

    def upsert_organization_integration(
        self,
        organization_id: str,
        integration_type: str,
        status: str,
        mode: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        last_sync_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.initialize()
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_organization_integration(organization_id, integration_type)
        row_id = (existing or {}).get("id") or f"INT-{uuid.uuid4().hex}"

        sql = (
            """
            INSERT INTO organization_integrations
            (id, organization_id, integration_type, status, mode, last_sync_at, metadata_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, integration_type)
            DO UPDATE SET status = EXCLUDED.status,
                          mode = EXCLUDED.mode,
                          last_sync_at = EXCLUDED.last_sync_at,
                          metadata_json = EXCLUDED.metadata_json,
                          updated_at = EXCLUDED.updated_at
            """
        )

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    row_id,
                    organization_id,
                    integration_type,
                    status,
                    mode,
                    last_sync_at,
                    json.dumps(metadata or {}),
                    (existing or {}).get("created_at") or now,
                    now,
                ),
            )
            conn.commit()
        return self.get_organization_integration(organization_id, integration_type) or {}

    def get_organization_integration(
        self,
        organization_id: str,
        integration_type: str,
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM organization_integrations WHERE organization_id = %s AND integration_type = %s LIMIT 1"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, integration_type))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        data["metadata"] = self._decode_json_value(data.get("metadata_json"), {})
        return data

    def list_organization_integrations(self, organization_id: str) -> List[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM organization_integrations WHERE organization_id = %s ORDER BY integration_type ASC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["metadata"] = self._decode_json_value(data.get("metadata_json"), {})
            result.append(data)
        return result

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def get_subscription_record(self, organization_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM subscriptions WHERE organization_id = %s LIMIT 1"
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        data["limits_json"] = self._decode_json_value(data.get("limits_json"), {})
        data["features_json"] = self._decode_json_value(data.get("features_json"), {})
        data["usage_json"] = self._decode_json_value(data.get("usage_json"), {})
        data["onboarding_completed"] = bool(data.get("onboarding_completed"))
        return data

    def upsert_subscription_record(self, organization_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_subscription_record(organization_id)
        row_id = (existing or {}).get("id") or f"SUB-{uuid.uuid4().hex}"

        merged = dict(existing or {})
        # Payload keys (limits / features / usage) are the canonical
        # names the service layer writes; DB column names add the
        # _json suffix. When the payload carries a fresh `limits`
        # value, it MUST override the existing `limits_json` read
        # from the DB — otherwise a tier upgrade never persists
        # because the read-merge-write cycle keeps picking the old
        # limits_json dict out of the merge. Same for features and
        # usage. Drop the stale _json keys before merging so the
        # payload always wins.
        payload_patch = dict(payload or {})
        if "limits" in payload_patch:
            merged.pop("limits_json", None)
        if "features" in payload_patch:
            merged.pop("features_json", None)
        if "usage" in payload_patch:
            merged.pop("usage_json", None)
        merged.update(payload_patch)
        merged.setdefault("plan", "free")
        merged.setdefault("status", "active")
        merged.setdefault("billing_cycle", "monthly")
        merged.setdefault("trial_days_remaining", 0)
        merged.setdefault("onboarding_completed", False)
        merged.setdefault("onboarding_step", 0)

        limits_json = merged.get("limits_json") or merged.get("limits") or {}
        features_json = merged.get("features_json") or merged.get("features") or {}
        usage_json = merged.get("usage_json") or merged.get("usage") or {}

        if isinstance(limits_json, dict):
            limits_json = json.dumps(limits_json)
        if isinstance(features_json, dict):
            features_json = json.dumps(features_json)
        if isinstance(usage_json, dict):
            usage_json = json.dumps(usage_json)

        sql = (
            """
            INSERT INTO subscriptions
            (id, organization_id, plan, status, trial_started_at, trial_ends_at, trial_days_remaining,
             billing_cycle, current_period_start, current_period_end, stripe_customer_id, stripe_subscription_id,
             limits_json, features_json, usage_json, onboarding_completed, onboarding_step, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id)
            DO UPDATE SET plan = EXCLUDED.plan,
                          status = EXCLUDED.status,
                          trial_started_at = EXCLUDED.trial_started_at,
                          trial_ends_at = EXCLUDED.trial_ends_at,
                          trial_days_remaining = EXCLUDED.trial_days_remaining,
                          billing_cycle = EXCLUDED.billing_cycle,
                          current_period_start = EXCLUDED.current_period_start,
                          current_period_end = EXCLUDED.current_period_end,
                          stripe_customer_id = EXCLUDED.stripe_customer_id,
                          stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                          limits_json = EXCLUDED.limits_json,
                          features_json = EXCLUDED.features_json,
                          usage_json = EXCLUDED.usage_json,
                          onboarding_completed = EXCLUDED.onboarding_completed,
                          onboarding_step = EXCLUDED.onboarding_step,
                          updated_at = EXCLUDED.updated_at
            """
        )

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    row_id,
                    organization_id,
                    merged.get("plan"),
                    merged.get("status"),
                    merged.get("trial_started_at"),
                    merged.get("trial_ends_at"),
                    int(merged.get("trial_days_remaining") or 0),
                    merged.get("billing_cycle") or "monthly",
                    merged.get("current_period_start"),
                    merged.get("current_period_end"),
                    merged.get("stripe_customer_id"),
                    merged.get("stripe_subscription_id"),
                    limits_json,
                    features_json,
                    usage_json,
                    1 if bool(merged.get("onboarding_completed")) else 0,
                    int(merged.get("onboarding_step") or 0),
                    (existing or {}).get("created_at") or now,
                    now,
                ),
            )
            conn.commit()
        return self.get_subscription_record(organization_id) or {}
