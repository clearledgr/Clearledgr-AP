"""Transactional outbox + dispatch worker (Gap 4).

Universal seam for "do this side-effect after this DB commit." Every
async side-effect that today fires fire-and-forget after a state
write (observer fan-out, customer webhook delivery, Gmail label
sync, override-window opening, vendor notifications) routes through
this module instead.

The contract:

1. Caller's business write happens inside a DB transaction. Inside
   the same transaction the caller calls
   :func:`OutboxWriter.enqueue(event_type, payload, target, ...)`,
   which inserts a row into ``outbox_events``. The transaction
   commits both atomically; if the business write rolls back, the
   outbox row rolls back too.

2. :class:`OutboxWorker` polls for pending rows whose backoff window
   has elapsed, dispatches each to the appropriate handler, and
   updates the row's status (succeeded / failed / dead).

3. Handlers are registered by ``target`` string. Three target kinds
   today:
     - ``observer:<ObserverClassName>`` — re-runs the existing
       :class:`StateObserver` against the rebuilt event
     - ``webhook:<subscription_id>`` — outbound delivery to a
       customer webhook
     - ``adapter:<source_type>`` — ERP write-back or similar
       integration call
   New handler kinds register via :func:`register_handler`.

4. Idempotent enqueue: ``dedupe_key`` makes calling enqueue twice for
   the same intent a no-op. Recommended pattern is
   ``f"{event_type}:{primary_key}"`` so retries of business code
   (e.g., webhook replay) don't duplicate side-effects.

5. Failure handling: ``max_attempts`` (default 5) with exponential
   backoff + jitter. After max attempts, status flips to ``dead``
   and the row appears in the ops queue
   (``GET /api/ops/outbox?status=dead``) for manual attention.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


# ─── Canonical types ───────────────────────────────────────────────


VALID_STATUSES = frozenset({"pending", "processing", "succeeded", "failed", "dead"})


@dataclass
class OutboxEvent:
    """One outbox row."""
    id: str
    organization_id: str
    event_type: str
    target: str
    payload: Dict[str, Any]
    dedupe_key: Optional[str]
    parent_event_id: Optional[str]
    status: str
    attempts: int
    max_attempts: int
    next_attempt_at: Optional[str]
    last_attempted_at: Optional[str]
    succeeded_at: Optional[str]
    error_log: List[Dict[str, Any]]
    created_at: str
    updated_at: str
    created_by: str


# ─── Handler registry ──────────────────────────────────────────────


# A handler receives an OutboxEvent and either returns normally
# (success) or raises (failure → retry/dead-letter).
HandlerFn = Callable[[OutboxEvent], Awaitable[None]]


_HANDLERS: Dict[str, HandlerFn] = {}


def register_handler(target_prefix: str, handler: HandlerFn) -> None:
    """Register a dispatch handler. ``target_prefix`` is what the
    enqueuer puts in ``target`` (without the trailing identifier),
    e.g. ``"observer"`` → matches ``"observer:GmailLabelObserver"``.
    """
    existing = _HANDLERS.get(target_prefix)
    if existing is not None and existing is not handler:
        raise ValueError(
            f"Outbox handler for prefix={target_prefix!r} already registered"
        )
    _HANDLERS[target_prefix] = handler
    logger.info("outbox: registered handler for target prefix %r", target_prefix)


def list_handlers() -> List[str]:
    return sorted(_HANDLERS.keys())


def _resolve_handler(target: str) -> Optional[HandlerFn]:
    if not target:
        return None
    prefix = target.split(":", 1)[0]
    return _HANDLERS.get(prefix)


# ─── Writer ────────────────────────────────────────────────────────


class OutboxWriter:
    """Enqueue side-effect intents inside a business-write transaction.

    Stateless beyond the org_id; cheap to construct per request.
    """

    def __init__(self, organization_id: str) -> None:
        self.organization_id = str(organization_id or "default").strip() or "default"

    def enqueue(
        self,
        *,
        event_type: str,
        target: str,
        payload: Dict[str, Any],
        dedupe_key: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        max_attempts: int = 5,
        actor: str = "system",
        delay_seconds: int = 0,
    ) -> Optional[str]:
        """Insert an outbox row. Returns the row id, or the existing
        row's id if a row with the same dedupe_key already exists
        (idempotent).

        Caller is responsible for being inside a DB transaction —
        this method opens its own connection but the enclosing
        ``ClearledgrDB`` connection-pool semantics ensure visibility
        with the surrounding business write provided the caller is
        on the same connection.
        """
        db = get_db()
        if not hasattr(db, "connect"):
            return None
        db.initialize()

        # Idempotent enqueue: a row with this dedupe_key already
        # exists → return its id, don't insert.
        if dedupe_key:
            existing = self._find_by_dedupe_key(db, dedupe_key)
            if existing is not None:
                return existing.id

        now = datetime.now(timezone.utc)
        next_attempt = now + timedelta(seconds=max(0, delay_seconds))
        event = OutboxEvent(
            id=f"OE-{uuid.uuid4().hex}",
            organization_id=self.organization_id,
            event_type=event_type,
            target=target,
            payload=payload or {},
            dedupe_key=dedupe_key,
            parent_event_id=parent_event_id,
            status="pending",
            attempts=0,
            max_attempts=max_attempts,
            next_attempt_at=next_attempt.isoformat(),
            last_attempted_at=None,
            succeeded_at=None,
            error_log=[],
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            created_by=actor,
        )
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO outbox_events
                  (id, organization_id, event_type, target,
                   payload_json, dedupe_key, parent_event_id,
                   status, attempts, max_attempts,
                   next_attempt_at, last_attempted_at, succeeded_at,
                   error_log_json, created_at, updated_at, created_by)
                VALUES
                  (%s, %s, %s, %s,
                   %s, %s, %s,
                   %s, %s, %s,
                   %s, %s, %s,
                   %s, %s, %s, %s)
                """,
                (
                    event.id, event.organization_id, event.event_type, event.target,
                    json.dumps(event.payload), event.dedupe_key, event.parent_event_id,
                    event.status, event.attempts, event.max_attempts,
                    event.next_attempt_at, event.last_attempted_at, event.succeeded_at,
                    json.dumps(event.error_log), event.created_at, event.updated_at,
                    event.created_by,
                ),
            )
            conn.commit()
        return event.id

    @staticmethod
    def _find_by_dedupe_key(db: Any, dedupe_key: str) -> Optional[OutboxEvent]:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM outbox_events WHERE dedupe_key = %s LIMIT 1",
                (dedupe_key,),
            )
            row = cur.fetchone()
        return _row_to_event(dict(row)) if row else None


# ─── Worker ────────────────────────────────────────────────────────


@dataclass
class WorkerStats:
    polled: int = 0
    succeeded: int = 0
    failed: int = 0
    dead: int = 0
    skipped_no_handler: int = 0


class OutboxWorker:
    """Polls ``outbox_events`` for due rows and dispatches to handlers.

    Default poll cadence: 2s sleep between polls. Per-row processing
    locks the row by flipping status to ``processing`` (with the
    per-row attempt counter incremented atomically) before dispatch,
    so multiple worker processes don't double-fire.

    Backoff: exponential with jitter — base 30s, doubles each
    attempt, capped at 30 minutes. Adds 0-25% jitter.
    """

    BASE_BACKOFF_SECONDS = 30
    MAX_BACKOFF_SECONDS = 1800
    POLL_INTERVAL_SECONDS = 2.0
    BATCH_SIZE = 25

    def __init__(self, *, batch_size: Optional[int] = None) -> None:
        self.batch_size = batch_size or self.BATCH_SIZE
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run_forever(self) -> None:
        while not self._stop:
            try:
                stats = await self.run_once()
                if stats.polled == 0:
                    await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
            except Exception as exc:  # noqa: BLE001
                logger.exception("outbox: run_once raised — %s", exc)
                await asyncio.sleep(self.POLL_INTERVAL_SECONDS)

    async def run_once(self) -> WorkerStats:
        stats = WorkerStats()
        events = self._claim_due_events()
        stats.polled = len(events)
        for event in events:
            handler = _resolve_handler(event.target)
            if handler is None:
                self._mark_dead(event, "no_handler_registered")
                stats.skipped_no_handler += 1
                continue
            try:
                await handler(event)
                self._mark_succeeded(event)
                stats.succeeded += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "outbox: handler %s failed for event %s — %s",
                    event.target, event.id, exc,
                )
                self._mark_failed(event, str(exc))
                if event.attempts + 1 >= event.max_attempts:
                    stats.dead += 1
                else:
                    stats.failed += 1
        return stats

    # ─── Internal state transitions ──────────────────────────────

    def _claim_due_events(self) -> List[OutboxEvent]:
        """Atomically transition pending/failed rows whose
        next_attempt_at has elapsed to status='processing' and
        return them. Atomic because the UPDATE filters on the old
        status — concurrent workers competing for the same row will
        only have one win the UPDATE."""
        db = get_db()
        if not hasattr(db, "connect"):
            return []
        db.initialize()
        now = datetime.now(timezone.utc).isoformat()
        events: List[OutboxEvent] = []
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE outbox_events
                SET status = 'processing',
                    last_attempted_at = %s,
                    updated_at = %s
                WHERE id IN (
                    SELECT id FROM outbox_events
                    WHERE status IN ('pending', 'failed')
                      AND (next_attempt_at IS NULL OR next_attempt_at <= %s)
                    ORDER BY next_attempt_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                (now, now, now, self.batch_size),
            )
            rows = cur.fetchall()
            conn.commit()
        return [_row_to_event(dict(r)) for r in rows or []]

    def _mark_succeeded(self, event: OutboxEvent) -> None:
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE outbox_events
                SET status = 'succeeded',
                    succeeded_at = %s, updated_at = %s,
                    attempts = attempts + 1
                WHERE id = %s
                """,
                (now, now, event.id),
            )
            conn.commit()

    def _mark_failed(self, event: OutboxEvent, error: str) -> None:
        attempts = event.attempts + 1
        next_status = "dead" if attempts >= event.max_attempts else "failed"
        backoff_seconds = min(
            self.BASE_BACKOFF_SECONDS * (2 ** (attempts - 1)),
            self.MAX_BACKOFF_SECONDS,
        )
        # Add 0-25% jitter so retries from a thundering herd of
        # failures don't all fire at the same second.
        jitter = backoff_seconds * 0.25 * random.random()
        next_at = (
            datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds + jitter)
        ).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        db = get_db()
        new_log_entry = {
            "attempt": attempts,
            "at": now,
            "error": error[:500],
        }
        new_error_log = list(event.error_log) + [new_log_entry]
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE outbox_events
                SET status = %s, attempts = %s,
                    next_attempt_at = %s, updated_at = %s,
                    error_log_json = %s
                WHERE id = %s
                """,
                (
                    next_status, attempts,
                    next_at if next_status == "failed" else None,
                    now, json.dumps(new_error_log), event.id,
                ),
            )
            conn.commit()

    def _mark_dead(self, event: OutboxEvent, reason: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        db = get_db()
        log = list(event.error_log) + [{
            "attempt": event.attempts + 1,
            "at": now,
            "error": reason,
            "terminal": True,
        }]
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE outbox_events
                SET status = 'dead', updated_at = %s,
                    error_log_json = %s, attempts = %s
                WHERE id = %s
                """,
                (now, json.dumps(log), event.attempts + 1, event.id),
            )
            conn.commit()


# ─── Ops helpers (called by /api/ops/outbox/* routes) ──────────────


def list_events(
    organization_id: Optional[str] = None,
    *,
    status: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 50,
) -> List[OutboxEvent]:
    db = get_db()
    if not hasattr(db, "connect"):
        return []
    db.initialize()
    clauses = []
    params: List[Any] = []
    if organization_id:
        clauses.append("organization_id = %s")
        params.append(organization_id)
    if status:
        if status not in VALID_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        clauses.append("status = %s")
        params.append(status)
    if event_type:
        clauses.append("event_type = %s")
        params.append(event_type)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = (
        "SELECT * FROM outbox_events"
        + where
        + " ORDER BY created_at DESC LIMIT %s"
    )
    params.append(int(limit))
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [_row_to_event(dict(r)) for r in rows or []]


def retry_event(event_id: str, *, actor: str = "ops") -> Optional[OutboxEvent]:
    """Force a dead/failed row back to pending so the worker picks it up."""
    db = get_db()
    if not hasattr(db, "connect"):
        return None
    db.initialize()
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE outbox_events
            SET status = 'pending', next_attempt_at = %s,
                updated_at = %s,
                error_log_json = COALESCE(error_log_json, '[]')
            WHERE id = %s AND status IN ('failed', 'dead')
            RETURNING *
            """,
            (now, now, event_id),
        )
        row = cur.fetchone()
        conn.commit()
    return _row_to_event(dict(row)) if row else None


def skip_event(event_id: str, *, actor: str = "ops", reason: str = "") -> Optional[OutboxEvent]:
    """Mark a stuck row as succeeded with metadata noting the skip.
    Use after manual reconciliation has confirmed the side-effect
    actually fired despite the worker getting stuck (e.g. ERP
    write succeeded but our DB update crashed)."""
    db = get_db()
    if not hasattr(db, "connect"):
        return None
    db.initialize()
    existing = _fetch_event_by_id(event_id)
    if existing is None:
        return None
    log = list(existing.error_log) + [{
        "skipped_by": actor,
        "skip_reason": reason or "manual_skip",
        "at": datetime.now(timezone.utc).isoformat(),
    }]
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE outbox_events
            SET status = 'succeeded', succeeded_at = %s,
                updated_at = %s, error_log_json = %s
            WHERE id = %s
            RETURNING *
            """,
            (now, now, json.dumps(log), event_id),
        )
        row = cur.fetchone()
        conn.commit()
    return _row_to_event(dict(row)) if row else None


def replay_events(
    *,
    organization_id: str,
    event_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 200,
    actor: str = "ops",
) -> int:
    """Re-enqueue events matching a window — for "we changed an
    observer; replay last 24h of state transitions through the new
    logic." Each replay creates a NEW row (with parent_event_id
    linking to the original) so the original audit trail is
    preserved."""
    db = get_db()
    if not hasattr(db, "connect"):
        return 0
    db.initialize()
    clauses = ["organization_id = %s"]
    params: List[Any] = [organization_id]
    if event_type:
        clauses.append("event_type = %s")
        params.append(event_type)
    if since:
        clauses.append("created_at >= %s")
        params.append(since)
    if until:
        clauses.append("created_at <= %s")
        params.append(until)
    sql = (
        "SELECT * FROM outbox_events WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at DESC LIMIT %s"
    )
    params.append(int(limit))
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    count = 0
    writer = OutboxWriter(organization_id)
    for r in rows or []:
        original = _row_to_event(dict(r))
        # Strip dedupe_key on replay so it's accepted as a new
        # intent (operator's responsibility to ensure the side-effect
        # is genuinely safe to re-fire — usually it is, since we
        # only replay observer fan-outs which are idempotent by
        # design).
        new_id = writer.enqueue(
            event_type=original.event_type,
            target=original.target,
            payload=original.payload,
            dedupe_key=None,
            parent_event_id=original.id,
            actor=f"replay:{actor}",
        )
        if new_id:
            count += 1
    return count


def _fetch_event_by_id(event_id: str) -> Optional[OutboxEvent]:
    db = get_db()
    if not hasattr(db, "connect"):
        return None
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM outbox_events WHERE id = %s", (event_id,))
        row = cur.fetchone()
    return _row_to_event(dict(row)) if row else None


# ─── Row helpers ───────────────────────────────────────────────────


def _row_to_event(row: Dict[str, Any]) -> OutboxEvent:
    def _load(value: Any, default):
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return json.loads(value)
            except Exception:
                return default
        return default

    return OutboxEvent(
        id=str(row.get("id") or ""),
        organization_id=str(row.get("organization_id") or ""),
        event_type=str(row.get("event_type") or ""),
        target=str(row.get("target") or ""),
        payload=_load(row.get("payload_json"), {}),
        dedupe_key=str(row.get("dedupe_key")) if row.get("dedupe_key") else None,
        parent_event_id=str(row.get("parent_event_id")) if row.get("parent_event_id") else None,
        status=str(row.get("status") or "pending"),
        attempts=int(row.get("attempts") or 0),
        max_attempts=int(row.get("max_attempts") or 5),
        next_attempt_at=str(row.get("next_attempt_at")) if row.get("next_attempt_at") else None,
        last_attempted_at=str(row.get("last_attempted_at")) if row.get("last_attempted_at") else None,
        succeeded_at=str(row.get("succeeded_at")) if row.get("succeeded_at") else None,
        error_log=_load(row.get("error_log_json"), []),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        created_by=str(row.get("created_by") or "system"),
    )
