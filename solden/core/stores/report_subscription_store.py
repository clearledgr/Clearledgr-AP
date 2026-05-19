"""Report subscription store — persistence for Module 8 scheduled email.

Mixed into ``SoldenDB`` so callers use ``db.create_report_subscription``,
``db.list_report_subscriptions``, ``db.due_report_subscriptions``, etc.

Why a dedicated mixin:

  - Subscriptions are operator-configuration rows; not AP-flavoured
    enough to live in ap_store but also not generic enough to scatter
    SQL through the API layer.
  - Keeps the worker-pickup query (``due_report_subscriptions``)
    colocated with the row-shape helpers, so a future schema change
    touches one file.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


VALID_CADENCES = frozenset({"daily", "weekly", "monthly"})

# Standard-interval delivery time (per spec line 277: "no custom
# scheduling"). 09:00 UTC keeps emails out of overnight noise for both
# US and EU operators while staying ahead of the EU workday start.
_DELIVERY_HOUR_UTC = 9

# Auto-pause threshold — a misconfigured SMTP shouldn't keep spamming
# retries forever. Five consecutive failures pauses the row; the
# operator un-pauses after fixing the underlying issue.
_AUTO_PAUSE_AFTER_FAILURES = 5


def compute_next_due(cadence: str, anchor: Optional[datetime] = None) -> datetime:
    """The next firing time for a cadence, anchored to ``anchor`` (now by default).

    daily   → next 09:00 UTC after anchor
    weekly  → next Monday 09:00 UTC after anchor
    monthly → 1st of next month 09:00 UTC after anchor
    """
    cadence_norm = (cadence or "").strip().lower()
    if cadence_norm not in VALID_CADENCES:
        raise ValueError(f"unsupported cadence: {cadence!r}")

    base = (anchor or datetime.now(timezone.utc)).astimezone(timezone.utc)

    if cadence_norm == "daily":
        candidate = base.replace(
            hour=_DELIVERY_HOUR_UTC, minute=0, second=0, microsecond=0,
        )
        if candidate <= base:
            candidate += timedelta(days=1)
        return candidate

    if cadence_norm == "weekly":
        # Anchor to next Monday at 09:00 UTC. Monday = weekday() 0.
        days_ahead = (0 - base.weekday()) % 7
        candidate = base.replace(
            hour=_DELIVERY_HOUR_UTC, minute=0, second=0, microsecond=0,
        ) + timedelta(days=days_ahead)
        if candidate <= base:
            candidate += timedelta(days=7)
        return candidate

    # monthly — 1st of next month, 09:00 UTC.
    if base.month == 12:
        next_month_first = base.replace(
            year=base.year + 1, month=1, day=1,
            hour=_DELIVERY_HOUR_UTC, minute=0, second=0, microsecond=0,
        )
    else:
        next_month_first = base.replace(
            month=base.month + 1, day=1,
            hour=_DELIVERY_HOUR_UTC, minute=0, second=0, microsecond=0,
        )
    # If the anchor is already after the 1st-of-this-month delivery
    # window, ``next_month_first`` is correct. If we're ON the 1st
    # before 09:00 UTC, return today's 09:00 UTC instead.
    today_first = base.replace(
        day=1, hour=_DELIVERY_HOUR_UTC, minute=0, second=0, microsecond=0,
    )
    if today_first > base and today_first.month == base.month:
        return today_first
    return next_month_first


class ReportSubscriptionStoreMixin:
    """Mix into ``SoldenDB`` for report-subscription persistence."""

    def create_report_subscription(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        sub_id = payload.get("id") or f"sub-{uuid.uuid4().hex[:16]}"
        cadence = (payload.get("cadence") or "weekly").strip().lower()
        if cadence not in VALID_CADENCES:
            raise ValueError(f"unsupported cadence: {cadence!r}")

        params = payload.get("params") or payload.get("params_json") or {}
        if isinstance(params, dict):
            params_json = json.dumps(params)
        else:
            params_json = params or "{}"

        next_due = payload.get("next_due_at") or compute_next_due(cadence)
        if isinstance(next_due, datetime):
            next_due_iso = next_due.astimezone(timezone.utc).isoformat()
        else:
            next_due_iso = str(next_due)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO report_subscriptions
                  (id, organization_id, user_id, recipient_email, report_type,
                   cadence, params_json, next_due_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    sub_id,
                    payload["organization_id"],
                    payload["user_id"],
                    payload["recipient_email"],
                    payload["report_type"],
                    cadence,
                    params_json,
                    next_due_iso,
                ),
            )
            conn.commit()
        return self.get_report_subscription(sub_id)

    def get_report_subscription(self, sub_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM report_subscriptions WHERE id = %s",
                (sub_id,),
            )
            row = cur.fetchone()
        return _row_to_subscription(row) if row else None

    def list_report_subscriptions(
        self, organization_id: str, *, user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.cursor()
            if user_id:
                cur.execute(
                    "SELECT * FROM report_subscriptions "
                    "WHERE organization_id = %s AND user_id = %s "
                    "ORDER BY created_at DESC",
                    (organization_id, user_id),
                )
            else:
                cur.execute(
                    "SELECT * FROM report_subscriptions "
                    "WHERE organization_id = %s ORDER BY created_at DESC",
                    (organization_id,),
                )
            return [_row_to_subscription(r) for r in cur.fetchall()]

    def update_report_subscription(
        self, sub_id: str, organization_id: str, **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        """Patch a subscription. Org-scoped to prevent cross-tenant writes."""
        allowed = {
            "cadence", "recipient_email", "params_json", "paused_at",
            "next_due_at",
        }
        if "params" in fields:
            params = fields.pop("params")
            fields["params_json"] = (
                json.dumps(params) if isinstance(params, dict) else params
            )
        if "cadence" in fields:
            cadence = (fields["cadence"] or "").strip().lower()
            if cadence not in VALID_CADENCES:
                raise ValueError(f"unsupported cadence: {cadence!r}")
            fields["cadence"] = cadence
            # Recompute next_due_at if cadence changed and caller didn't override.
            if "next_due_at" not in fields:
                fields["next_due_at"] = compute_next_due(cadence)

        if "next_due_at" in fields and isinstance(fields["next_due_at"], datetime):
            fields["next_due_at"] = (
                fields["next_due_at"].astimezone(timezone.utc).isoformat()
            )
        if "paused_at" in fields and isinstance(fields["paused_at"], datetime):
            fields["paused_at"] = (
                fields["paused_at"].astimezone(timezone.utc).isoformat()
            )

        sets = []
        args: List[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = %s")
            args.append(value)
        if not sets:
            return self.get_report_subscription(sub_id)

        sets.append("updated_at = NOW()")
        sql = (
            f"UPDATE report_subscriptions SET {', '.join(sets)} "
            f"WHERE id = %s AND organization_id = %s"
        )
        args.extend([sub_id, organization_id])

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(args))
            conn.commit()
        return self.get_report_subscription(sub_id)

    def delete_report_subscription(self, sub_id: str, organization_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM report_subscriptions "
                "WHERE id = %s AND organization_id = %s",
                (sub_id, organization_id),
            )
            deleted = cur.rowcount or 0
            conn.commit()
        return bool(deleted)

    def due_report_subscriptions(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        """Subscriptions ripe for delivery — the worker-pickup query.

        Active (paused_at IS NULL) and next_due_at <= now(). Ordered
        by next_due_at so the oldest-due rows fire first when the
        worker is catching up after downtime.
        """
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM report_subscriptions "
                "WHERE paused_at IS NULL AND next_due_at <= NOW() "
                "ORDER BY next_due_at ASC LIMIT %s",
                (limit,),
            )
            return [_row_to_subscription(r) for r in cur.fetchall()]

    def record_subscription_delivery(
        self, sub_id: str, *, delivered_at: Optional[datetime] = None,
    ) -> None:
        """Mark a subscription as delivered + advance ``next_due_at``."""
        sub = self.get_report_subscription(sub_id)
        if not sub:
            return
        delivered = (delivered_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        next_due = compute_next_due(sub["cadence"], anchor=delivered)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE report_subscriptions SET "
                "  last_delivered_at = %s, next_due_at = %s, "
                "  failure_count = 0, last_failure_at = NULL, "
                "  last_error = NULL, updated_at = NOW() "
                "WHERE id = %s",
                (delivered.isoformat(), next_due.isoformat(), sub_id),
            )
            conn.commit()

    def record_subscription_failure(
        self, sub_id: str, *, error: str,
    ) -> Dict[str, Any]:
        """Increment failure count + auto-pause at the threshold."""
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE report_subscriptions SET "
                "  failure_count = failure_count + 1, "
                "  last_failure_at = NOW(), last_error = %s, "
                "  paused_at = CASE WHEN failure_count + 1 >= %s "
                "                   THEN NOW() ELSE paused_at END, "
                "  updated_at = NOW() "
                "WHERE id = %s",
                (error[:1000], _AUTO_PAUSE_AFTER_FAILURES, sub_id),
            )
            conn.commit()
        return self.get_report_subscription(sub_id) or {}


def _row_to_subscription(row: Any) -> Dict[str, Any]:
    """Normalise a row tuple/dict into the canonical subscription shape."""
    if hasattr(row, "_asdict"):
        d = row._asdict()
    elif hasattr(row, "keys"):
        d = dict(row)
    else:
        # Tuple ordering matches CREATE TABLE column order.
        keys = (
            "id", "organization_id", "user_id", "recipient_email",
            "report_type", "cadence", "params_json", "next_due_at",
            "last_delivered_at", "paused_at", "failure_count",
            "last_failure_at", "last_error", "created_at", "updated_at",
        )
        d = dict(zip(keys, row))

    raw = d.get("params_json")
    if isinstance(raw, str):
        try:
            d["params"] = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            d["params"] = {}
    elif isinstance(raw, dict):
        d["params"] = raw
    else:
        d["params"] = {}

    for key in ("next_due_at", "last_delivered_at", "paused_at",
                "last_failure_at", "created_at", "updated_at"):
        value = d.get(key)
        if isinstance(value, datetime):
            d[key] = value.astimezone(timezone.utc).isoformat()
    return d
