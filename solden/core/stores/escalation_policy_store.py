"""Escalation policy store — Module 11 (org-level stuck-exception escalation).

Mixed into ``SoldenDB``. Owns three primitives:

  - CRUD over ``escalation_policies`` rows.
  - The worker-pickup query
    (``find_unescalated_due_exceptions``) that joins
    ``box_exceptions`` against ``escalation_events`` to surface
    only the exceptions that have crossed the threshold for a
    given policy AND haven't been escalated yet for that policy.
  - The idempotency-safe insert of ``escalation_events`` rows so a
    second worker tick during the same minute can't re-fire.

Why join-against-not-exists vs. a "last_fired_at" cursor:
    Cursor-based "fire if last_fired_at + threshold < now" works for
    a single policy + recurring schedule, but escalations are
    per-(policy, exception) — every stuck exception is a distinct
    firing target. The UNIQUE(policy_id, exception_id) constraint on
    ``escalation_events`` is the right idempotency mechanism; the
    worker-side query just needs to find rows where the constraint
    hasn't yet been hit.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


VALID_ACTIONS = frozenset({"notify_email"})


class EscalationPolicyStoreMixin:

    def create_escalation_policy(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        action = (payload.get("action") or "notify_email").strip().lower()
        if action not in VALID_ACTIONS:
            raise ValueError(f"unsupported escalation action: {action!r}")

        threshold = int(payload.get("threshold_hours") or 0)
        if threshold <= 0 or threshold > 720:
            raise ValueError("threshold_hours must be between 1 and 720")

        policy_id = payload.get("id") or f"esc-{uuid.uuid4().hex[:16]}"
        recipients = payload.get("recipients") or payload.get("recipients_json") or []
        if isinstance(recipients, list):
            recipients_json = json.dumps(recipients)
        else:
            recipients_json = recipients

        exception_types = payload.get("exception_types")
        exception_types_json = (
            json.dumps(exception_types) if isinstance(exception_types, list)
            else exception_types
        )

        severity_filter = payload.get("severity_filter")
        severity_filter_json = (
            json.dumps(severity_filter) if isinstance(severity_filter, list)
            else severity_filter
        )

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO escalation_policies
                  (id, organization_id, name, threshold_hours, exception_types,
                   severity_filter, action, recipients_json, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    policy_id,
                    payload["organization_id"],
                    payload.get("name") or "Untitled escalation",
                    threshold,
                    exception_types_json,
                    severity_filter_json,
                    action,
                    recipients_json,
                    payload.get("created_by") or "",
                ),
            )
            conn.commit()
        return self.get_escalation_policy(policy_id) or {}

    def get_escalation_policy(self, policy_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM escalation_policies WHERE id = %s",
                (policy_id,),
            )
            row = cur.fetchone()
        return _row_to_policy(row) if row else None

    def list_escalation_policies(
        self, organization_id: str, *, include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.cursor()
            if include_inactive:
                cur.execute(
                    "SELECT * FROM escalation_policies WHERE organization_id = %s "
                    "ORDER BY created_at DESC",
                    (organization_id,),
                )
            else:
                cur.execute(
                    "SELECT * FROM escalation_policies "
                    "WHERE organization_id = %s AND is_active = 1 "
                    "ORDER BY created_at DESC",
                    (organization_id,),
                )
            return [_row_to_policy(r) for r in cur.fetchall()]

    def update_escalation_policy(
        self, policy_id: str, organization_id: str, **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        allowed = {
            "name", "threshold_hours", "exception_types", "severity_filter",
            "action", "recipients_json", "is_active",
        }
        if "recipients" in fields:
            value = fields.pop("recipients")
            fields["recipients_json"] = (
                json.dumps(value) if isinstance(value, list) else value
            )
        if "exception_types" in fields and isinstance(fields["exception_types"], list):
            fields["exception_types"] = json.dumps(fields["exception_types"])
        if "severity_filter" in fields and isinstance(fields["severity_filter"], list):
            fields["severity_filter"] = json.dumps(fields["severity_filter"])
        if "action" in fields:
            action = (fields["action"] or "").strip().lower()
            if action not in VALID_ACTIONS:
                raise ValueError(f"unsupported escalation action: {action!r}")
            fields["action"] = action
        if "threshold_hours" in fields:
            threshold = int(fields["threshold_hours"] or 0)
            if threshold <= 0 or threshold > 720:
                raise ValueError("threshold_hours must be between 1 and 720")
            fields["threshold_hours"] = threshold
        if "is_active" in fields:
            fields["is_active"] = 1 if fields["is_active"] else 0

        sets: List[str] = []
        args: List[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = %s")
            args.append(value)
        if not sets:
            return self.get_escalation_policy(policy_id)

        sets.append("updated_at = NOW()")
        sql = (
            f"UPDATE escalation_policies SET {', '.join(sets)} "
            "WHERE id = %s AND organization_id = %s"
        )
        args.extend([policy_id, organization_id])

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(args))
            conn.commit()
        return self.get_escalation_policy(policy_id)

    def delete_escalation_policy(self, policy_id: str, organization_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM escalation_policies "
                "WHERE id = %s AND organization_id = %s",
                (policy_id, organization_id),
            )
            deleted = cur.rowcount or 0
            conn.commit()
        return bool(deleted)

    # ------------------------------------------------------------
    # Worker-pickup query + event recording
    # ------------------------------------------------------------

    def find_unescalated_due_exceptions(
        self, policy: Dict[str, Any], *, limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """For one policy, return box_exceptions that have crossed
        the threshold and not yet been escalated under this policy.

        Filters:
          - organization_id matches the policy's org
          - resolved_at IS NULL (only unresolved exceptions)
          - raised_at < now() - threshold_hours
          - exception_type matches the policy's filter (if set)
          - severity matches the policy's filter (if set)
          - LEFT JOIN escalation_events filtered to this policy_id;
            return rows where the join produces NULL (no event yet)
        """
        threshold_hours = int(policy.get("threshold_hours") or 24)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=threshold_hours)
        cutoff_iso = cutoff.isoformat()

        clauses = [
            "be.organization_id = %s",
            "be.resolved_at IS NULL",
            "be.raised_at::timestamptz < %s",
        ]
        args: List[Any] = [policy.get("organization_id"), cutoff_iso]

        exception_types = policy.get("exception_types") or []
        if isinstance(exception_types, str):
            try:
                exception_types = json.loads(exception_types)
            except (json.JSONDecodeError, TypeError):
                exception_types = []
        if exception_types:
            placeholders = ", ".join(["%s"] * len(exception_types))
            clauses.append(f"be.exception_type IN ({placeholders})")
            args.extend(exception_types)

        severity_filter = policy.get("severity_filter") or []
        if isinstance(severity_filter, str):
            try:
                severity_filter = json.loads(severity_filter)
            except (json.JSONDecodeError, TypeError):
                severity_filter = []
        if severity_filter:
            placeholders = ", ".join(["%s"] * len(severity_filter))
            clauses.append(f"be.severity IN ({placeholders})")
            args.extend(severity_filter)

        where = " AND ".join(clauses)
        sql = f"""
            SELECT be.*
            FROM box_exceptions be
            LEFT JOIN escalation_events ee
              ON ee.policy_id = %s AND ee.exception_id = be.id
            WHERE {where} AND ee.id IS NULL
            ORDER BY be.raised_at ASC
            LIMIT %s
        """
        # Bind args order: policy_id (for LEFT JOIN), then WHERE args, then limit
        bind_args = [policy.get("id")] + args + [int(limit)]

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(bind_args))
            return [dict(r) for r in cur.fetchall()]

    def record_escalation_event(
        self, *, policy_id: str, exception_id: str, organization_id: str,
        delivered: bool, delivery_error: Optional[str] = None,
    ) -> bool:
        """Insert an escalation_events row. Returns True if the row was
        inserted, False if the UNIQUE(policy_id, exception_id)
        constraint blocked it (race / re-tick).

        ``delivered`` records whether the action succeeded — failed
        deliveries still get a row so the (policy, exception) pair
        is recognised as "we tried" and we don't keep re-firing every
        minute. The retry policy is operator-driven: if delivery
        failed, the leader sees the failure on the policy detail page
        and decides what to do.
        """
        event_id = f"ev-{uuid.uuid4().hex[:16]}"
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO escalation_events
                      (id, policy_id, exception_id, organization_id,
                       delivered, delivery_error)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (policy_id, exception_id) DO NOTHING
                    """,
                    (
                        event_id, policy_id, exception_id, organization_id,
                        1 if delivered else 0,
                        delivery_error[:500] if delivery_error else None,
                    ),
                )
                affected = cur.rowcount or 0
                conn.commit()
            return bool(affected)
        except Exception as exc:
            logger.warning(
                "[escalation] record_escalation_event failed for policy=%s exc=%s: %s",
                policy_id, exception_id, exc,
            )
            return False

    def list_escalation_events(
        self, organization_id: str, *, limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM escalation_events "
                "WHERE organization_id = %s ORDER BY fired_at DESC LIMIT %s",
                (organization_id, int(limit)),
            )
            rows = []
            for row in cur.fetchall():
                d = dict(row)
                d["delivered"] = bool(d.get("delivered"))
                rows.append(d)
            return rows


def _row_to_policy(row: Any) -> Dict[str, Any]:
    if hasattr(row, "_asdict"):
        d = row._asdict()
    elif hasattr(row, "keys"):
        d = dict(row)
    else:
        keys = (
            "id", "organization_id", "name", "threshold_hours",
            "exception_types", "severity_filter", "action",
            "recipients_json", "is_active", "created_by",
            "created_at", "updated_at",
        )
        d = dict(zip(keys, row))

    d["is_active"] = bool(d.get("is_active"))

    for key in ("recipients_json", "exception_types", "severity_filter"):
        raw = d.get(key)
        if isinstance(raw, str) and raw:
            try:
                d[key.replace("_json", "")] = json.loads(raw) if key == "recipients_json" else json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass

    if "recipients" not in d:
        d["recipients"] = []
    if "exception_types" not in d or d.get("exception_types") is None:
        d["exception_types"] = []
    if "severity_filter" not in d or d.get("severity_filter") is None:
        d["severity_filter"] = []

    for key in ("created_at", "updated_at"):
        value = d.get(key)
        if isinstance(value, datetime):
            d[key] = value.astimezone(timezone.utc).isoformat()
    return d
