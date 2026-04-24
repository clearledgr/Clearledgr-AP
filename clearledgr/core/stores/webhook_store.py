"""WebhookStore mixin — CRUD for outgoing webhook subscriptions."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class WebhookStore:
    """Mixin for webhook subscription persistence."""

    def create_webhook_subscription(
        self,
        organization_id: str,
        url: str,
        event_types: List[str],
        secret: str = "",
        description: str = "",
    ) -> Dict[str, Any]:
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sub_id = f"wh_{uuid.uuid4().hex[:12]}"

        sql = self._prepare_sql("""
            INSERT INTO webhook_subscriptions
            (id, organization_id, url, event_types, secret, is_active, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        """)
        params = (sub_id, organization_id, url, json.dumps(event_types), secret, description, now, now)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

        return {
            "id": sub_id,
            "organization_id": organization_id,
            "url": url,
            "event_types": event_types,
            "secret": secret,
            "is_active": True,
            "description": description,
            "created_at": now,
        }

    def list_webhook_subscriptions(
        self, organization_id: str, active_only: bool = True,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        if active_only:
            sql = self._prepare_sql(
                "SELECT * FROM webhook_subscriptions WHERE organization_id = ? AND is_active = 1"
            )
        else:
            sql = self._prepare_sql(
                "SELECT * FROM webhook_subscriptions WHERE organization_id = ?"
            )

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = [dict(r) for r in cur.fetchall()]

        for row in rows:
            try:
                row["event_types"] = json.loads(row.get("event_types") or "[]")
            except (json.JSONDecodeError, TypeError):
                row["event_types"] = []
            row["is_active"] = bool(row.get("is_active"))

        return rows

    def get_webhook_subscription(self, subscription_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM webhook_subscriptions WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (subscription_id,))
            row = cur.fetchone()

        if not row:
            return None
        result = dict(row)
        try:
            result["event_types"] = json.loads(result.get("event_types") or "[]")
        except (json.JSONDecodeError, TypeError):
            result["event_types"] = []
        result["is_active"] = bool(result.get("is_active"))
        return result

    def update_webhook_subscription(
        self, subscription_id: str, **kwargs,
    ) -> bool:
        self.initialize()
        allowed = {"url", "event_types", "secret", "is_active", "description"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        if "event_types" in updates and isinstance(updates["event_types"], list):
            updates["event_types"] = json.dumps(updates["event_types"])
        if "is_active" in updates:
            updates["is_active"] = 1 if updates["is_active"] else 0

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        sql = self._prepare_sql(
            f"UPDATE webhook_subscriptions SET {set_clause} WHERE id = ?"
        )
        params = list(updates.values()) + [subscription_id]

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount > 0

    def delete_webhook_subscription(self, subscription_id: str) -> bool:
        self.initialize()
        sql = self._prepare_sql("DELETE FROM webhook_subscriptions WHERE id = ?")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (subscription_id,))
            conn.commit()
            return cur.rowcount > 0

    def get_active_webhooks_for_event(
        self, organization_id: str, event_type: str,
    ) -> List[Dict[str, Any]]:
        """Return all active subscriptions that subscribe to this event type."""
        subs = self.list_webhook_subscriptions(organization_id, active_only=True)
        return [
            s for s in subs
            if event_type in s.get("event_types", []) or "*" in s.get("event_types", [])
        ]
