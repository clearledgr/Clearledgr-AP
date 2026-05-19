"""Rules store — Module 3 (workspace approval rule engine).

Mixed into ``SoldenDB``. Owns:

  - CRUD over ``rules`` rows.
  - Version snapshots on every change (``rule_versions`` ledger),
    keyed by (rule_id, version_number) with a UNIQUE constraint so a
    second writer racing on a save can't produce a duplicate.
  - One-click revert: writes a new version with the body of an older
    snapshot.

Why not piggyback on ``policy_versions`` (v45)?
  policy_versions is policy-kind-keyed (approval_thresholds,
  gl_account_map, etc.), versioned monotonically per kind, designed
  for "snapshot of the org-wide policy at point T." Per-rule history
  is finer-grained — each rule has its own version stream — and a
  dedicated table is the natural fit.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


VALID_RULE_STATUSES = frozenset({"active", "paused", "archived"})
VALID_WORKFLOWS = frozenset({"ap"})


class RulesStoreMixin:

    def create_rule(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        rule_id = payload.get("id") or f"rule-{uuid.uuid4().hex[:16]}"
        status = (payload.get("status") or "active").strip().lower()
        if status not in VALID_RULE_STATUSES:
            raise ValueError(f"invalid rule status: {status!r}")

        workflow = (payload.get("workflow") or "ap").strip().lower()
        if workflow not in VALID_WORKFLOWS:
            raise ValueError(f"invalid workflow: {workflow!r}")

        priority = int(payload.get("priority") or 100)
        if priority < 0 or priority > 9999:
            raise ValueError("priority must be between 0 and 9999")

        conditions = payload.get("conditions") or payload.get("conditions_json") or {}
        conditions_json = (
            json.dumps(conditions) if isinstance(conditions, (dict, list)) else conditions
        )

        actions = payload.get("actions") or payload.get("actions_json") or []
        actions_json = (
            json.dumps(actions) if isinstance(actions, (list, dict)) else actions
        )

        org_id = payload["organization_id"]
        actor = payload.get("created_by") or ""

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO rules
                  (id, organization_id, name, description, entity_id, workflow,
                   priority, conditions_json, actions_json, status, version,
                   created_by, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
                """,
                (
                    rule_id, org_id,
                    payload.get("name") or "Untitled rule",
                    payload.get("description"),
                    payload.get("entity_id"),
                    workflow, priority,
                    conditions_json, actions_json, status,
                    actor, actor,
                ),
            )
            conn.commit()

        # First version snapshot (immutable record of this initial body).
        self._snapshot_rule_version(
            rule_id=rule_id,
            organization_id=org_id,
            version_number=1,
            payload={
                "name": payload.get("name") or "Untitled rule",
                "description": payload.get("description"),
                "entity_id": payload.get("entity_id"),
                "workflow": workflow,
                "priority": priority,
                "conditions_json": conditions_json,
                "actions_json": actions_json,
                "status": status,
                "changed_by": actor,
                "change_note": payload.get("change_note") or "Initial rule",
            },
        )
        return self.get_rule(rule_id) or {}

    def get_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM rules WHERE id = %s", (rule_id,))
            row = cur.fetchone()
        return _row_to_rule(row) if row else None

    def list_rules(
        self,
        organization_id: str,
        *,
        workflow: Optional[str] = None,
        entity_id: Optional[str] = None,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        clauses = ["organization_id = %s"]
        args: List[Any] = [organization_id]
        if workflow:
            clauses.append("workflow = %s")
            args.append(workflow)
        if entity_id is not None:
            # entity_id can be the empty string for org-wide rules; pass
            # None explicitly to get only org-wide rules, or a real id
            # to filter to that entity. Empty kwarg = no filter.
            clauses.append("entity_id IS NOT DISTINCT FROM %s")
            args.append(entity_id)
        if not include_inactive:
            clauses.append("status = 'active'")

        sql = (
            f"SELECT * FROM rules WHERE {' AND '.join(clauses)} "
            "ORDER BY priority ASC, created_at ASC"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(args))
            return [_row_to_rule(r) for r in cur.fetchall()]

    def update_rule(
        self, rule_id: str, organization_id: str, *,
        change_note: Optional[str] = None,
        actor: Optional[str] = None,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_rule(rule_id)
        if not existing or existing.get("organization_id") != organization_id:
            return None

        allowed = {
            "name", "description", "entity_id", "priority",
            "conditions", "actions", "status",
        }
        sets: List[str] = []
        args: List[Any] = []

        for key, value in list(fields.items()):
            if key not in allowed:
                continue
            if key == "conditions":
                sets.append("conditions_json = %s")
                args.append(
                    json.dumps(value) if isinstance(value, (dict, list)) else value,
                )
                continue
            if key == "actions":
                sets.append("actions_json = %s")
                args.append(
                    json.dumps(value) if isinstance(value, (list, dict)) else value,
                )
                continue
            if key == "status":
                status = (value or "").strip().lower()
                if status not in VALID_RULE_STATUSES:
                    raise ValueError(f"invalid rule status: {value!r}")
                sets.append("status = %s")
                args.append(status)
                continue
            if key == "priority":
                priority = int(value or 0)
                if priority < 0 or priority > 9999:
                    raise ValueError("priority must be between 0 and 9999")
                sets.append("priority = %s")
                args.append(priority)
                continue
            sets.append(f"{key} = %s")
            args.append(value)

        if not sets:
            return existing

        new_version = int(existing.get("version") or 1) + 1
        sets.append("version = %s")
        args.append(new_version)
        sets.append("updated_by = %s")
        args.append(actor or existing.get("updated_by") or "")
        sets.append("updated_at = NOW()")

        sql = (
            f"UPDATE rules SET {', '.join(sets)} "
            "WHERE id = %s AND organization_id = %s"
        )
        args.extend([rule_id, organization_id])

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(args))
            conn.commit()

        # Version snapshot of the new state
        updated = self.get_rule(rule_id)
        if updated:
            self._snapshot_rule_version(
                rule_id=rule_id,
                organization_id=organization_id,
                version_number=new_version,
                payload={
                    "name": updated["name"],
                    "description": updated.get("description"),
                    "entity_id": updated.get("entity_id"),
                    "workflow": updated["workflow"],
                    "priority": updated["priority"],
                    "conditions_json": json.dumps(updated.get("conditions") or {}),
                    "actions_json": json.dumps(updated.get("actions") or []),
                    "status": updated["status"],
                    "changed_by": actor or "",
                    "change_note": change_note or "",
                },
            )
        return updated

    def delete_rule(self, rule_id: str, organization_id: str) -> bool:
        """Soft-delete: status='archived'. Hard-delete is reserved for
        operator tooling so the audit trail of what existed is
        preserved by default."""
        return bool(self.update_rule(
            rule_id, organization_id,
            status="archived",
            change_note="Rule archived (soft delete)",
        ))

    def list_rule_versions(
        self, rule_id: str, organization_id: str, *, limit: int = 50,
    ) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM rule_versions "
                "WHERE rule_id = %s AND organization_id = %s "
                "ORDER BY version_number DESC LIMIT %s",
                (rule_id, organization_id, int(limit)),
            )
            return [_row_to_version(r) for r in cur.fetchall()]

    def get_rule_version(
        self, rule_id: str, version_number: int, organization_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM rule_versions "
                "WHERE rule_id = %s AND version_number = %s "
                "AND organization_id = %s",
                (rule_id, int(version_number), organization_id),
            )
            row = cur.fetchone()
        return _row_to_version(row) if row else None

    def revert_rule_to_version(
        self, rule_id: str, version_number: int, organization_id: str, *,
        actor: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Apply an older version's body as a NEW version. Audit trail
        preserved end-to-end: the snapshot the operator reverted to
        and the new "reverted to vN" version both live in the ledger."""
        prior = self.get_rule_version(rule_id, version_number, organization_id)
        if not prior:
            return None

        return self.update_rule(
            rule_id, organization_id,
            actor=actor,
            change_note=f"Reverted to version {version_number}",
            name=prior.get("name"),
            description=prior.get("description"),
            entity_id=prior.get("entity_id"),
            priority=prior.get("priority"),
            conditions=prior.get("conditions"),
            actions=prior.get("actions"),
            status=prior.get("status"),
        )

    # ------------------------------------------------------------
    # Internal: snapshot helpers
    # ------------------------------------------------------------

    def _snapshot_rule_version(
        self, *, rule_id: str, organization_id: str,
        version_number: int, payload: Dict[str, Any],
    ) -> None:
        version_id = f"rv-{uuid.uuid4().hex[:16]}"
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO rule_versions
                      (id, rule_id, organization_id, version_number, name,
                       description, entity_id, workflow, priority,
                       conditions_json, actions_json, status,
                       changed_by, change_note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (rule_id, version_number) DO NOTHING
                    """,
                    (
                        version_id, rule_id, organization_id, version_number,
                        payload.get("name") or "",
                        payload.get("description"),
                        payload.get("entity_id"),
                        payload.get("workflow") or "ap",
                        int(payload.get("priority") or 100),
                        payload.get("conditions_json") or "{}",
                        payload.get("actions_json") or "[]",
                        payload.get("status") or "active",
                        payload.get("changed_by") or "",
                        payload.get("change_note") or "",
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.warning(
                "[rules] _snapshot_rule_version failed for %s v%s: %s",
                rule_id, version_number, exc,
            )


def _row_to_rule(row: Any) -> Dict[str, Any]:
    if hasattr(row, "_asdict"):
        d = row._asdict()
    elif hasattr(row, "keys"):
        d = dict(row)
    else:
        keys = (
            "id", "organization_id", "name", "description", "entity_id",
            "workflow", "priority", "conditions_json", "actions_json",
            "status", "version", "created_by", "updated_by",
            "created_at", "updated_at",
        )
        d = dict(zip(keys, row))

    raw_cond = d.get("conditions_json")
    if isinstance(raw_cond, str) and raw_cond:
        try:
            d["conditions"] = json.loads(raw_cond)
        except (json.JSONDecodeError, TypeError):
            d["conditions"] = {}
    else:
        d["conditions"] = raw_cond if isinstance(raw_cond, dict) else {}

    raw_act = d.get("actions_json")
    if isinstance(raw_act, str) and raw_act:
        try:
            d["actions"] = json.loads(raw_act)
        except (json.JSONDecodeError, TypeError):
            d["actions"] = []
    else:
        d["actions"] = raw_act if isinstance(raw_act, list) else []

    for key in ("created_at", "updated_at"):
        value = d.get(key)
        if isinstance(value, datetime):
            d[key] = value.astimezone(timezone.utc).isoformat()
    return d


def _row_to_version(row: Any) -> Dict[str, Any]:
    if hasattr(row, "_asdict"):
        d = row._asdict()
    elif hasattr(row, "keys"):
        d = dict(row)
    else:
        keys = (
            "id", "rule_id", "organization_id", "version_number",
            "name", "description", "entity_id", "workflow", "priority",
            "conditions_json", "actions_json", "status",
            "changed_by", "change_note", "changed_at",
        )
        d = dict(zip(keys, row))

    raw_cond = d.get("conditions_json")
    if isinstance(raw_cond, str) and raw_cond:
        try:
            d["conditions"] = json.loads(raw_cond)
        except (json.JSONDecodeError, TypeError):
            d["conditions"] = {}
    else:
        d["conditions"] = raw_cond if isinstance(raw_cond, dict) else {}

    raw_act = d.get("actions_json")
    if isinstance(raw_act, str) and raw_act:
        try:
            d["actions"] = json.loads(raw_act)
        except (json.JSONDecodeError, TypeError):
            d["actions"] = []
    else:
        d["actions"] = raw_act if isinstance(raw_act, list) else []

    if isinstance(d.get("changed_at"), datetime):
        d["changed_at"] = d["changed_at"].astimezone(timezone.utc).isoformat()
    return d
