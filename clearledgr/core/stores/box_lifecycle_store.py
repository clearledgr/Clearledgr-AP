"""Box-lifecycle records: first-class exceptions + outcomes.

The deck's core promise:

    "Every workflow instance becomes a persistent, attributable record:
     state, timeline, exceptions, outcome."

State and timeline already have first-class homes (state-field on the
source table; ``audit_events`` keyed on ``(box_id, box_type)``).

This mixin makes the other two first-class too:

* **Exceptions** — ``box_exceptions`` rows. Multiple per Box. Each
  records type, reason, severity, raised_at/by, resolved_at/by,
  resolution_note. Queryable across Box types. "Humans decide on the
  exceptions" (deck) means a human-actionable queue; this is the row
  shape that powers it.

* **Outcomes** — ``box_outcomes`` rows. UNIQUE on ``(box_type, box_id)``
  — one terminal outcome per Box. Records outcome_type
  (``posted_to_erp`` / ``rejected`` / ``vendor_activated`` /
  ``closed_unsuccessful`` / ``reversed``) with attributable context.

Both writes emit an audit_events row through the canonical funnel so
the timeline narrates the lifecycle faithfully: "exception raised" →
"exception resolved" → "outcome recorded."

Schema owned by migration v43.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_row(row: Any) -> Dict[str, Any]:
    out = dict(row)
    for col in ("metadata_json", "data_json"):
        if col in out and isinstance(out[col], str):
            try:
                out[col] = json.loads(out[col]) if out[col].strip() else {}
            except (ValueError, TypeError):
                out[col] = {}
    return out


_VALID_SEVERITIES = frozenset({"low", "medium", "high", "critical"})
_VALID_ACTOR_TYPES = frozenset({"agent", "user", "system"})


class BoxLifecycleStore:
    """Mixin providing Box-exception + Box-outcome CRUD.

    Composed into :class:`ClearledgrDB`. Every mutating method emits an
    audit row through ``append_audit_event`` (which this mixin assumes
    is available on ``self`` — it is, via :class:`APStore`).
    """

    def _emit_box_webhook(
        self,
        *,
        event_type: str,
        organization_id: str,
        payload: Dict[str, Any],
    ) -> None:
        """Fire-and-forget Backoffice webhook for a Box lifecycle change.

        Customers subscribe to `box.exception_raised`,
        `box.exception_resolved`, `box.outcome_recorded` via the
        admin /webhooks surface. Delivery is async with retry via the
        notification queue; this helper schedules the emit on the
        running loop when present, or enqueues a notification for the
        background loop otherwise. Failures are swallowed — webhook
        delivery is observational, not part of the write contract.
        """
        try:
            import asyncio

            from clearledgr.services.webhook_delivery import emit_webhook_event

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    emit_webhook_event(
                        organization_id=organization_id,
                        event_type=event_type,
                        payload=payload,
                    )
                )
                return
            except RuntimeError:
                pass  # No running loop — fall through to queue enqueue.

            if hasattr(self, "enqueue_notification"):
                try:
                    self.enqueue_notification(
                        organization_id=organization_id,
                        channel="webhook",
                        payload={
                            "event_type": event_type,
                            "data": payload,
                        },
                        box_id=payload.get("box_id"),
                        box_type=payload.get("box_type"),
                    )
                except Exception as enq_exc:
                    logger.debug(
                        "[BoxLifecycleStore] webhook enqueue fallback failed: %s",
                        enq_exc,
                    )
        except Exception as wh_exc:
            logger.warning(
                "[BoxLifecycleStore] webhook emission failed for %s: %s",
                event_type, wh_exc,
            )

    # ------------------------------------------------------------------
    # Exceptions
    # ------------------------------------------------------------------

    def raise_box_exception(
        self,
        *,
        box_id: str,
        box_type: str,
        organization_id: str,
        exception_type: str,
        reason: str,
        raised_by: str,
        severity: str = "medium",
        raised_actor_type: str = "agent",
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Raise a new exception on a Box.

        Idempotent via ``idempotency_key``: re-raising with the same key
        returns the existing row rather than creating a duplicate. This
        matches the ``audit_events.idempotency_key`` pattern — callers
        that might retry (webhook delivery, event replay) pass a stable
        key so the second attempt is a no-op.

        The severity is validated against ``_VALID_SEVERITIES``;
        anything else is silently coerced to ``medium``.
        """
        if severity not in _VALID_SEVERITIES:
            severity = "medium"
        if raised_actor_type not in _VALID_ACTOR_TYPES:
            raised_actor_type = "agent"

        # Idempotency pre-check
        if idempotency_key:
            existing = self._get_box_exception_by_key(idempotency_key)
            if existing:
                return existing

        self.initialize()
        exception_id = f"EXC-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        metadata_json = json.dumps(metadata or {})

        sql = (
            "INSERT INTO box_exceptions "
            "(id, box_id, box_type, organization_id, exception_type, "
            " severity, reason, metadata_json, raised_at, raised_by, "
            " raised_actor_type, idempotency_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    sql,
                    (
                        exception_id,
                        box_id,
                        box_type,
                        organization_id,
                        exception_type,
                        severity,
                        reason,
                        metadata_json,
                        now,
                        raised_by,
                        raised_actor_type,
                        idempotency_key,
                    ),
                )
                conn.commit()
        except Exception as exc:
            # Idempotency race — another caller won the UNIQUE insert.
            # Return their winning row rather than raising.
            if idempotency_key:
                winner = self._get_box_exception_by_key(idempotency_key)
                if winner:
                    return winner
            logger.warning("[BoxLifecycleStore] raise exception failed: %s", exc)
            raise

        # Narrate to the timeline.
        if hasattr(self, "append_audit_event"):
            try:
                self.append_audit_event({
                    "event_type": "box_exception_raised",
                    "actor_type": raised_actor_type,
                    "actor_id": raised_by,
                    "box_id": box_id,
                    "box_type": box_type,
                    "organization_id": organization_id,
                    "decision_reason": f"{exception_type}: {reason}",
                    "payload_json": {
                        "exception_id": exception_id,
                        "exception_type": exception_type,
                        "severity": severity,
                        "metadata": metadata or {},
                    },
                })
            except Exception as audit_exc:
                logger.warning(
                    "[BoxLifecycleStore] raise-exception audit emission "
                    "failed (non-fatal): %s",
                    audit_exc,
                )

        # Emit to Backoffice webhook subscribers.
        self._emit_box_webhook(
            event_type="box.exception_raised",
            organization_id=organization_id,
            payload={
                "box_id": box_id,
                "box_type": box_type,
                "organization_id": organization_id,
                "exception": {
                    "id": exception_id,
                    "exception_type": exception_type,
                    "severity": severity,
                    "reason": reason,
                    "raised_at": now,
                    "raised_by": raised_by,
                    "raised_actor_type": raised_actor_type,
                    "metadata": metadata or {},
                },
            },
        )

        return self.get_box_exception(exception_id)

    def resolve_box_exception(
        self,
        exception_id: str,
        *,
        resolved_by: str,
        resolution_note: str = "",
        resolved_actor_type: str = "user",
    ) -> Optional[Dict[str, Any]]:
        """Mark an exception resolved.

        Idempotent: re-resolving an already-resolved exception is a
        no-op that returns the current row unchanged. We do NOT
        overwrite the original resolved_at/resolved_by — first writer
        wins. That preserves the attribution record the deck promises.
        """
        if resolved_actor_type not in _VALID_ACTOR_TYPES:
            resolved_actor_type = "user"

        existing = self.get_box_exception(exception_id)
        if existing is None:
            return None
        if existing.get("resolved_at"):
            return existing  # Already resolved — idempotent

        self.initialize()
        now = _now_iso()
        sql = (
            "UPDATE box_exceptions "
            "SET resolved_at = %s, resolved_by = %s, resolved_actor_type = %s, "
            "    resolution_note = %s "
            "WHERE id = %s AND resolved_at IS NULL"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (now, resolved_by, resolved_actor_type, resolution_note, exception_id),
            )
            conn.commit()

        # Narrate.
        if hasattr(self, "append_audit_event"):
            try:
                self.append_audit_event({
                    "event_type": "box_exception_resolved",
                    "actor_type": resolved_actor_type,
                    "actor_id": resolved_by,
                    "box_id": existing["box_id"],
                    "box_type": existing["box_type"],
                    "organization_id": existing["organization_id"],
                    "decision_reason": resolution_note or "resolved",
                    "payload_json": {
                        "exception_id": exception_id,
                        "exception_type": existing.get("exception_type"),
                        "resolution_note": resolution_note,
                    },
                })
            except Exception as audit_exc:
                logger.warning(
                    "[BoxLifecycleStore] resolve-exception audit emission "
                    "failed (non-fatal): %s",
                    audit_exc,
                )

        # Emit to Backoffice webhook subscribers.
        self._emit_box_webhook(
            event_type="box.exception_resolved",
            organization_id=existing["organization_id"],
            payload={
                "box_id": existing["box_id"],
                "box_type": existing["box_type"],
                "organization_id": existing["organization_id"],
                "exception": {
                    "id": exception_id,
                    "exception_type": existing.get("exception_type"),
                    "resolved_at": now,
                    "resolved_by": resolved_by,
                    "resolved_actor_type": resolved_actor_type,
                    "resolution_note": resolution_note,
                },
            },
        )

        return self.get_box_exception(exception_id)

    def get_box_exception(self, exception_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM box_exceptions WHERE id = %s"
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (exception_id,))
                row = cur.fetchone()
        except Exception:
            return None
        return _decode_row(row) if row else None

    def _get_box_exception_by_key(
        self, idempotency_key: str
    ) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        self.initialize()
        sql = (
            "SELECT * FROM box_exceptions WHERE idempotency_key = %s"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (idempotency_key,))
                row = cur.fetchone()
        except Exception:
            return None
        return _decode_row(row) if row else None

    def list_box_exceptions(
        self,
        *,
        box_type: str,
        box_id: str,
        only_unresolved: bool = False,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        if only_unresolved:
            sql = (
                "SELECT * FROM box_exceptions "
                "WHERE box_type = %s AND box_id = %s AND resolved_at IS NULL "
                "ORDER BY raised_at ASC"
            )
        else:
            sql = (
                "SELECT * FROM box_exceptions "
                "WHERE box_type = %s AND box_id = %s "
                "ORDER BY raised_at ASC"
            )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (box_type, box_id))
                rows = cur.fetchall()
        except Exception:
            return []
        return [_decode_row(r) for r in rows]

    def list_unresolved_exceptions(
        self,
        organization_id: str,
        *,
        box_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Organization-wide unresolved-exception queue. Powers the
        operator-facing "what needs my attention" view across Box types.
        """
        self.initialize()
        if box_type:
            sql = (
                "SELECT * FROM box_exceptions "
                "WHERE organization_id = %s AND box_type = %s "
                "AND resolved_at IS NULL "
                "ORDER BY severity DESC, raised_at ASC "
                "LIMIT %s"
            )
            params = (organization_id, box_type, limit)
        else:
            sql = (
                "SELECT * FROM box_exceptions "
                "WHERE organization_id = %s AND resolved_at IS NULL "
                "ORDER BY severity DESC, raised_at ASC "
                "LIMIT %s"
            )
            params = (organization_id, limit)
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                rows = cur.fetchall()
        except Exception:
            return []
        return [_decode_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Outcomes
    # ------------------------------------------------------------------

    def record_box_outcome(
        self,
        *,
        box_id: str,
        box_type: str,
        organization_id: str,
        outcome_type: str,
        recorded_by: str,
        recorded_actor_type: str = "agent",
        data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Record the terminal outcome for a Box. Exactly one per Box.

        Idempotent via the UNIQUE (box_type, box_id) constraint:
        re-recording a second outcome returns the first. Terminal is
        terminal — if the Box gets re-opened and produces a new
        outcome later, that's a new Box concern, not an outcome
        overwrite.
        """
        if recorded_actor_type not in _VALID_ACTOR_TYPES:
            recorded_actor_type = "agent"

        # Idempotency pre-check: one outcome per Box.
        existing = self.get_box_outcome(box_type=box_type, box_id=box_id)
        if existing is not None:
            return existing

        self.initialize()
        outcome_id = f"OUT-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        data_json = json.dumps(data or {})

        sql = (
            "INSERT INTO box_outcomes "
            "(id, box_id, box_type, organization_id, outcome_type, "
            " data_json, recorded_at, recorded_by, recorded_actor_type) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    sql,
                    (
                        outcome_id,
                        box_id,
                        box_type,
                        organization_id,
                        outcome_type,
                        data_json,
                        now,
                        recorded_by,
                        recorded_actor_type,
                    ),
                )
                conn.commit()
        except Exception as exc:
            # UNIQUE race — someone else wrote first. Return theirs.
            winner = self.get_box_outcome(box_type=box_type, box_id=box_id)
            if winner is not None:
                return winner
            logger.warning("[BoxLifecycleStore] record outcome failed: %s", exc)
            raise

        # Narrate.
        if hasattr(self, "append_audit_event"):
            try:
                self.append_audit_event({
                    "event_type": "box_outcome_recorded",
                    "actor_type": recorded_actor_type,
                    "actor_id": recorded_by,
                    "box_id": box_id,
                    "box_type": box_type,
                    "organization_id": organization_id,
                    "decision_reason": outcome_type,
                    "payload_json": {
                        "outcome_id": outcome_id,
                        "outcome_type": outcome_type,
                        "data": data or {},
                    },
                })
            except Exception as audit_exc:
                logger.warning(
                    "[BoxLifecycleStore] record-outcome audit emission "
                    "failed (non-fatal): %s",
                    audit_exc,
                )

        # Emit to Backoffice webhook subscribers.
        self._emit_box_webhook(
            event_type="box.outcome_recorded",
            organization_id=organization_id,
            payload={
                "box_id": box_id,
                "box_type": box_type,
                "organization_id": organization_id,
                "outcome": {
                    "id": outcome_id,
                    "outcome_type": outcome_type,
                    "recorded_at": now,
                    "recorded_by": recorded_by,
                    "recorded_actor_type": recorded_actor_type,
                    "data": data or {},
                },
            },
        )

        return self._get_box_outcome_by_id(outcome_id)

    def get_box_outcome(
        self,
        *,
        box_type: str,
        box_id: str,
    ) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = (
            "SELECT * FROM box_outcomes WHERE box_type = %s AND box_id = %s"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (box_type, box_id))
                row = cur.fetchone()
        except Exception:
            return None
        return _decode_row(row) if row else None

    def _get_box_outcome_by_id(self, outcome_id: str) -> Optional[Dict[str, Any]]:
        self.initialize()
        sql = "SELECT * FROM box_outcomes WHERE id = %s"
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (outcome_id,))
                row = cur.fetchone()
        except Exception:
            return None
        return _decode_row(row) if row else None

    def list_outcomes_by_type(
        self,
        organization_id: str,
        *,
        box_type: str,
        outcome_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        self.initialize()
        if outcome_type:
            sql = (
                "SELECT * FROM box_outcomes "
                "WHERE organization_id = %s AND box_type = %s AND outcome_type = %s "
                "ORDER BY recorded_at DESC LIMIT %s"
            )
            params = (organization_id, box_type, outcome_type, limit)
        else:
            sql = (
                "SELECT * FROM box_outcomes "
                "WHERE organization_id = %s AND box_type = %s "
                "ORDER BY recorded_at DESC LIMIT %s"
            )
            params = (organization_id, box_type, limit)
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                rows = cur.fetchall()
        except Exception:
            return []
        return [_decode_row(r) for r in rows]
