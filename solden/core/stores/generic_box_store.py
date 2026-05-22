"""GenericBoxStore — CRUD for declarative WorkflowSpec Box types.

The three original Box types each own a bespoke table. Declarative types
(defined by a :class:`solden.core.workflow_spec.WorkflowSpec`) instead share
the single ``boxes`` table: ``box_type`` names the type, ``state`` carries the
lifecycle position, ``data`` (JSONB) holds the type's declared fields, and
``spec_version`` pins the Box to the spec version it was created under.

State transitions go through :func:`update_generic_box_state`, which resolves
the Box's *pinned* spec version, validates the edge against that spec's
transition graph, and appends a typed ``audit_events`` row through the same
funnel AP / bank_match / purchase_order use — so the audit hash-chain, the
exception queue, and the export graph treat a declared Box like any other.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from solden.core.workflow_spec import (
    RESERVED_DATA_KEYS,
    IllegalWorkflowTransitionError,
    resolve_spec,
    validate_spec_transition,
)

logger = logging.getLogger(__name__)


class GenericBoxStore:
    """Mixin: declarative-Box CRUD over the ``boxes`` table. Combined into SoldenDB."""

    def create_generic_box(
        self,
        box_type: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Insert a new declarative Box in its spec's initial state.

        ``payload`` must carry ``organization_id``; everything not a reserved
        column becomes the Box's ``data``. The Box always enters at the spec's
        ``initial_state`` (a caller-supplied ``state`` is ignored), mirroring
        the controlled-entry rule the bespoke stores enforce.
        """
        self.initialize()
        organization_id = payload.get("organization_id")
        if not organization_id:
            raise ValueError("create_generic_box requires organization_id")

        spec = resolve_spec(box_type, organization_id)
        if spec is None:
            raise ValueError(f"No WorkflowSpec registered for box_type={box_type!r}")

        box_id = payload.get("id") or f"BX-{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        state = spec.initial_state
        # Pin to the version the active spec resolved at (1 for code built-ins).
        spec_version = int(payload.get("spec_version") or getattr(spec, "version", 1) or 1)

        data: Dict[str, Any] = {}
        if isinstance(payload.get("data"), dict):
            data.update(payload["data"])
        for k, v in payload.items():
            if k not in RESERVED_DATA_KEYS:
                data[k] = v

        sql = """
            INSERT INTO boxes
            (id, organization_id, box_type, state, spec_version, data,
             created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        """
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                box_id, organization_id, box_type, state, spec_version,
                json.dumps(data), now, now,
            ))
            conn.commit()

        if hasattr(self, "append_audit_event"):
            self.append_audit_event({
                "box_id": box_id,
                "box_type": box_type,
                "event_type": f"{box_type}_created",
                "to_state": state,
                "actor_type": "system",
                "actor_id": payload.get("created_by") or "system",
                "organization_id": organization_id,
                "policy_version": spec.policy_version,
                "payload_json": {"spec_version": spec_version},
                "idempotency_key": f"box-created:{box_id}",
            })

        return self.get_generic_box(box_type, box_id)  # type: ignore[return-value]

    def get_generic_box(
        self,
        box_type: str,
        box_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return one declarative Box by id, or None.

        The ``data`` JSONB is merged up into the row dict so generic readers
        (engine, box_summary) can read declared fields by key — but the
        authoritative native columns always win, so a ``data`` payload can
        never shadow ``state`` / ``id`` / ``organization_id`` etc.
        """
        self.initialize()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM boxes WHERE id = %s", (box_id,))
            row = cur.fetchone()
        if not row:
            return None
        return self._deserialize_generic_box(dict(row))

    def list_generic_boxes(
        self,
        box_type: str,
        organization_id: str,
        *,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """All Boxes of *box_type* in an org, newest first."""
        self.initialize()
        sql = (
            "SELECT * FROM boxes "
            "WHERE box_type = %s AND organization_id = %s "
            "ORDER BY created_at DESC LIMIT %s"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (box_type, organization_id, int(limit)))
            rows = [dict(r) for r in cur.fetchall()]
        return [self._deserialize_generic_box(r) for r in rows]

    def update_generic_box_state(
        self,
        box_type: str,
        box_id: str,
        target_state: str,
        *,
        actor_id: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Advance a declarative Box, validating against its *pinned* spec.

        Raises :class:`IllegalWorkflowTransitionError` if the edge is invalid.
        Validation uses the spec version the Box was created under, not the
        org's currently-active version — so activating a new spec version
        never strands or illegally-moves in-flight Boxes.
        """
        if not str(actor_id or "").strip():
            raise ValueError(
                "update_generic_box_state requires a non-empty actor_id "
                "for audit-trail integrity"
            )
        existing = self.get_generic_box(box_type, box_id)
        if not existing:
            raise IllegalWorkflowTransitionError(f"box {box_id!r} not found")

        organization_id = existing.get("organization_id")
        spec_version = existing.get("spec_version")
        spec = resolve_spec(box_type, organization_id, spec_version)
        if spec is None:
            raise IllegalWorkflowTransitionError(
                f"No WorkflowSpec resolvable for box_type={box_type!r} "
                f"version={spec_version!r}"
            )

        current_state = str(existing.get("state") or "")
        if not validate_spec_transition(spec, current_state, target_state):
            raise IllegalWorkflowTransitionError(
                f"Illegal {box_type} transition: {current_state!r} -> "
                f"{target_state!r} (box_id={box_id})"
            )

        # Hooks/conditions/effects — a complete no-op unless FEATURE_WORKFLOW_HOOKS
        # is on. A deny here vetoes the transition; an allow may carry a
        # whitelisted data patch (reserved columns can never be patched).
        from solden.core.hooks.dispatcher import HookDenied, run_transition_hooks
        decision = run_transition_hooks(
            spec, existing, current_state, target_state, actor=actor_id,
        )
        if not decision.allow:
            raise HookDenied(decision.deny_reason or "hook_denied")
        patch = {
            k: v for k, v in (decision.data_patch or {}).items()
            if k not in RESERVED_DATA_KEYS
        }

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            if patch:
                merged = {**(existing.get("data") or {}), **patch}
                cur.execute(
                    "UPDATE boxes SET state = %s, data = %s::jsonb, updated_at = %s "
                    "WHERE id = %s",
                    (target_state, json.dumps(merged), now, box_id),
                )
            else:
                cur.execute(
                    "UPDATE boxes SET state = %s, updated_at = %s WHERE id = %s",
                    (target_state, now, box_id),
                )
            conn.commit()

        if hasattr(self, "append_audit_event"):
            self.append_audit_event({
                "box_id": box_id,
                "box_type": box_type,
                "event_type": f"{box_type}_{target_state}",
                "from_state": current_state,
                "to_state": target_state,
                "actor_type": "user",
                "actor_id": actor_id,
                "organization_id": organization_id,
                "policy_version": spec.policy_version,
                "decision_reason": reason or None,
                "idempotency_key": f"box-{target_state}:{box_id}:{now}",
            })

        # §8 lifecycle parity: entering the spec's declared exception_state
        # raises a first-class box_exception, mirroring how AP raises one when
        # an ap_item lands in needs_info/failed_post. Same audit + webhook
        # funnel, so declarative Boxes show up in the exception queue too. The
        # mirror is best-effort: a failure here must not unwind the committed
        # transition.
        if (
            spec.exception_state
            and target_state == spec.exception_state
            and hasattr(self, "raise_box_exception")
        ):
            try:
                self.raise_box_exception(
                    box_id=box_id,
                    box_type=box_type,
                    organization_id=organization_id,
                    exception_type=f"{box_type}_exception",
                    reason=reason or f"Box entered exception state {target_state!r}",
                    metadata={"source": "update_generic_box_state", "state": target_state},
                    raised_by=str(actor_id or "system"),
                    raised_actor_type="user",
                    idempotency_key=f"{box_type}-excp:{box_id}:{target_state}:{now}",
                )
            except Exception as mirror_exc:
                logger.warning(
                    "[GenericBoxStore] box_exception mirror failed for %s: %s",
                    box_id, mirror_exc,
                )

        return self.get_generic_box(box_type, box_id)  # type: ignore[return-value]

    def record_hook_run(
        self,
        *,
        organization_id: str,
        box_type: str,
        box_id: str,
        hook_key: str,
        outcome: str,
        deny_reason: str = "",
        duration_ms: Optional[int] = None,
        error: str = "",
    ) -> None:
        """Append an audit row for one hook execution (best-effort)."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO workflow_hook_runs "
                "(id, organization_id, box_type, box_id, hook_key, outcome, "
                " deny_reason, duration_ms, error, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"HR-{uuid.uuid4().hex[:16]}", organization_id, box_type,
                    box_id, hook_key, outcome, deny_reason or None,
                    duration_ms, error or None, now,
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _deserialize_generic_box(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = row.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = {}
        if not isinstance(data, dict):
            data = {}
        # Merge data FIRST, then let the authoritative native columns win.
        merged: Dict[str, Any] = dict(data)
        for key in (
            "id", "organization_id", "box_type", "state",
            "spec_version", "created_at", "updated_at",
        ):
            if key in row:
                merged[key] = row[key]
        merged["data"] = data
        return merged
