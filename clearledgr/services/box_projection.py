"""Read-side projections for Boxes (Gap 6).

The §8 Box read contract — state + timeline + exceptions + outcome —
used to be assembled live on every read by joining ``ap_items``,
``audit_events``, ``box_exceptions``, and ``box_outcomes``. That works
for low traffic; it does not work for the surfaces that hit it on
every Gmail thread render, every Slack card refresh, every admin
list pageload. It also makes time-travel queries ("what did this
Box look like last Friday at 3pm?") a multi-table archaeological dig.

This module replaces the live join with materialised projections:

* ``box_summary`` — current snapshot per Box, primary key
  ``(box_type, box_id)``. The primary read path
  (``GET /api/ap/items/{id}/box``) becomes a single-row lookup with a
  fall-through to live composition when the projection is stale or
  missing.
* ``box_summary_history`` — append-only snapshot per state transition,
  enabling ``GET /api/ap/items/{id}/history?at=<ts>`` time-travel.
* ``vendor_summary`` — per-vendor rollup (BlackLine-style), keyed
  ``(organization_id, vendor_name_normalized)``. Backs the vendor
  detail page + ``GET /api/vendors/{name}/summary``.

Architecture: the projection is updated by a :class:`BoxProjector`
listening to state-transition outbox events. Same durability seam as
the rest of Gap 4 — the projector is just another outbox handler with
target prefix ``projection:``. When the audit_events table is the
source of truth, the projection is eventually consistent through the
outbox; if the projection row is missing or its ``last_event_id``
doesn't match the audit-events tip, the read path falls through and
re-composes live.

The :class:`BoxProjector` protocol lets future Box types
(``po`` / ``payment`` / ``budget``) plug in their own projectors
without touching the outbox handler or the observer registry.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ─── Canonical types ───────────────────────────────────────────────


@dataclass
class ProjectionContext:
    """Input to a projector's ``project`` method.

    Built from a state-transition outbox event. Carries the Box
    coordinates + the new state + actor + correlation so projectors
    can decide whether they care about this transition and what to
    write.
    """
    organization_id: str
    box_type: str
    box_id: str
    old_state: str
    new_state: str
    actor_id: Optional[str]
    correlation_id: Optional[str]
    source_type: str
    erp_native: bool
    metadata: Dict[str, Any]
    transition_event_id: Optional[str]


@dataclass
class ProjectionResult:
    """What ``project`` returns. Persisted into the projector's own
    target tables; this dataclass is mostly informational, used by
    tests + ops to confirm what changed."""
    rows_upserted: int = 0
    rows_inserted: int = 0
    skip_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ─── Protocol ──────────────────────────────────────────────────────


@runtime_checkable
class BoxProjector(Protocol):
    """Every read-side projection that wants to be kept fresh by the
    state-transition outbox implements this."""

    projector_name: str
    """Canonical identifier for the projector. Becomes the suffix of
    the outbox target string: ``projection:<projector_name>``."""

    box_types: tuple
    """Box types this projector cares about. ``("ap_item",)`` for
    BoxSummaryProjector; ``("ap_item",)`` for VendorSummaryProjector
    (since vendor rollups live off AP outcomes today). Future
    projectors for ``po`` / ``payment`` declare their own here."""

    async def project(self, context: ProjectionContext) -> ProjectionResult:
        """Update the projection's target tables for this transition.

        Implementations should be idempotent — same context replayed
        twice should not double-count. Use UPSERT semantics with
        ``last_event_id`` guards where possible. Raise on transient
        failures so the outbox retries.
        """


# ─── Registry ──────────────────────────────────────────────────────


_PROJECTOR_REGISTRY: Dict[str, BoxProjector] = {}


def register_projector(projector: BoxProjector) -> None:
    """Register a projector at module-import time. Idempotent for
    identical re-registration."""
    existing = _PROJECTOR_REGISTRY.get(projector.projector_name)
    if existing is not None:
        if existing is projector:
            return
        raise ValueError(
            f"Projector for name={projector.projector_name!r} already registered "
            f"({type(existing).__name__}); refusing to overwrite with "
            f"{type(projector).__name__}."
        )
    _PROJECTOR_REGISTRY[projector.projector_name] = projector
    logger.info("box_projection: registered projector %s", projector.projector_name)


def get_projector(name: str) -> Optional[BoxProjector]:
    return _PROJECTOR_REGISTRY.get(name)


def list_registered_projectors() -> List[str]:
    return sorted(_PROJECTOR_REGISTRY.keys())


# ─── Observer ─────────────────────────────────────────────────────


class BoxProjectionObserver:
    """Slots into :class:`StateObserverRegistry` alongside the audit,
    annotation-dispatch, override-window observers. On every state
    transition, enqueues one outbox row per registered projector that
    declares this Box type. Each projector then runs as an outbox
    handler (target prefix ``projection:``) so it gets retry +
    dead-letter for free from Gap 4.

    Why "observer" not just inline projection update:
    The observer registry is the canonical fan-out seam for state
    transitions. Slot the projector dispatcher in there so it shares
    the same lifecycle as audit/annotation. Outbox mode keeps the
    projection update durable across worker restarts.
    """

    def __init__(self, db: Any, *, box_type: str = "ap_item") -> None:
        self._db = db
        self._box_type = box_type

    async def on_transition(self, event) -> None:
        # event is a StateTransitionEvent — duck-typed to avoid
        # circular imports.
        from clearledgr.services.outbox import OutboxWriter

        if not _PROJECTOR_REGISTRY:
            return

        writer = OutboxWriter(event.organization_id)
        payload_base = {
            "box_type": self._box_type,
            "box_id": event.ap_item_id,
            "old_state": event.old_state,
            "new_state": event.new_state,
            "actor_id": event.actor_id,
            "correlation_id": event.correlation_id,
            "source_type": getattr(event, "source_type", "gmail"),
            "erp_native": getattr(event, "erp_native", False),
            "metadata": dict(event.metadata or {}),
        }
        for name, projector in _PROJECTOR_REGISTRY.items():
            if self._box_type not in (projector.box_types or ()):
                continue
            dedupe_key = (
                f"projection:{name}:{event.ap_item_id}:"
                f"{event.old_state}->{event.new_state}:"
                f"{event.correlation_id or ''}"
            )
            try:
                writer.enqueue(
                    event_type=f"projection.{event.new_state}",
                    target=f"projection:{name}",
                    payload={**payload_base, "projector_name": name},
                    dedupe_key=dedupe_key,
                    actor=event.actor_id or "system",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "box_projection: enqueue failed projector=%s box=%s — %s",
                    name, event.ap_item_id, exc,
                )


# ─── Outbox handler ────────────────────────────────────────────────


async def _outbox_handler_projection(outbox_event) -> None:
    """Resolve ``target='projection:<projector_name>'`` to the
    registered projector, build the ProjectionContext, call
    ``project``. Raised exceptions trigger the outbox's retry/
    dead-letter logic."""
    target_str = outbox_event.target
    if not target_str.startswith("projection:"):
        raise ValueError(f"unexpected target {target_str!r}")
    projector_name = target_str.split(":", 1)[1]
    projector = get_projector(projector_name)
    if projector is None:
        raise LookupError(
            f"no projector registered for {projector_name!r} — "
            f"workers must import clearledgr.services.box_projection on boot"
        )
    payload = outbox_event.payload or {}
    context = ProjectionContext(
        organization_id=outbox_event.organization_id,
        box_type=str(payload.get("box_type") or "ap_item"),
        box_id=str(payload.get("box_id") or ""),
        old_state=str(payload.get("old_state") or ""),
        new_state=str(payload.get("new_state") or ""),
        actor_id=payload.get("actor_id"),
        correlation_id=payload.get("correlation_id"),
        source_type=str(payload.get("source_type") or "gmail"),
        erp_native=bool(payload.get("erp_native") or False),
        metadata=dict(payload.get("metadata") or {}),
        transition_event_id=payload.get("transition_event_id"),
    )
    await projector.project(context)


def _register_outbox_handler() -> None:
    """One-shot registration of the projection-prefix handler with
    the outbox."""
    try:
        from clearledgr.services.outbox import register_handler
        register_handler("projection", _outbox_handler_projection)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "box_projection: outbox handler registration failed — %s", exc,
        )


# ─── BoxSummaryProjector ───────────────────────────────────────────


class BoxSummaryProjector:
    """Maintains ``box_summary`` (current snapshot) +
    ``box_summary_history`` (append-only).

    Re-uses the existing :func:`build_box_summary` helper to assemble
    the summary content, then UPSERTS one ``box_summary`` row keyed
    on ``(box_type, box_id)`` and INSERTS one ``box_summary_history``
    row keyed on the transition.

    The history insert is the load-bearing piece for time-travel
    queries; the summary upsert is the load-bearing piece for the
    primary read path.
    """

    projector_name = "box_summary"
    box_types = ("ap_item",)

    def __init__(self, db: Any = None) -> None:
        self._db = db

    @property
    def db(self) -> Any:
        if self._db is not None:
            return self._db
        from clearledgr.core.database import get_db
        return get_db()

    async def project(self, context: ProjectionContext) -> ProjectionResult:
        from clearledgr.core.box_summary import build_box_summary

        if not context.box_id:
            return ProjectionResult(skip_reason="missing_box_id")

        db = self.db
        # Cache for the rest of this projection run so all helpers
        # share the same handle.
        self._db = db
        try:
            summary = build_box_summary(context.box_id, db=db)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "box_summary: build_box_summary failed for %s — %s",
                context.box_id, exc,
            )
            raise

        item = self._db.get_ap_item(context.box_id) or {}
        timeline_preview = self._timeline_preview(context.box_id)
        exceptions = self._exceptions(context.box_type, context.box_id)
        outcome = self._outcome(context.box_type, context.box_id)
        event_count = self._count_events(context.box_id)
        last_event_id = self._last_event_id(context.box_id)

        now = datetime.now(timezone.utc).isoformat()
        state = item.get("state") or context.new_state or "unknown"
        organization_id = (
            context.organization_id
            or item.get("organization_id")
            or "default"
        )

        upserted = self._upsert_summary(
            organization_id=organization_id,
            box_type=context.box_type,
            box_id=context.box_id,
            state=str(state),
            summary_json=json.dumps(summary.to_dict()),
            timeline_preview_json=json.dumps(timeline_preview),
            exceptions_json=json.dumps(exceptions),
            outcome_json=json.dumps(outcome) if outcome else None,
            event_count=event_count,
            last_event_id=last_event_id,
            last_state_at=now,
            updated_at=now,
        )
        inserted = self._insert_history(
            organization_id=organization_id,
            box_type=context.box_type,
            box_id=context.box_id,
            state=str(state),
            summary_json=json.dumps(summary.to_dict()),
            snapshot_at=now,
            transition_event_id=context.transition_event_id or last_event_id,
            triggered_by=context.actor_id or "system",
        )
        return ProjectionResult(rows_upserted=upserted, rows_inserted=inserted)

    # ─── Helpers ─────────────────────────────────────────────────

    def _timeline_preview(self, box_id: str, *, limit: int = 5) -> List[Dict[str, Any]]:
        """Last *limit* audit events, condensed for the preview row."""
        if not hasattr(self._db, "list_ap_audit_events"):
            return []
        try:
            events = self._db.list_ap_audit_events(box_id, limit=limit, order="desc")
        except Exception:
            return []
        preview: List[Dict[str, Any]] = []
        for ev in events or []:
            preview.append({
                "id": ev.get("id"),
                "event_type": ev.get("event_type"),
                "ts": ev.get("ts"),
                "actor": ev.get("actor"),
                "prev_state": ev.get("prev_state"),
                "new_state": ev.get("new_state"),
                "decision_reason": ev.get("decision_reason"),
            })
        return preview

    def _exceptions(self, box_type: str, box_id: str) -> List[Dict[str, Any]]:
        if not hasattr(self._db, "list_box_exceptions"):
            return []
        try:
            return list(self._db.list_box_exceptions(box_type=box_type, box_id=box_id) or [])
        except Exception:
            return []

    def _outcome(self, box_type: str, box_id: str) -> Optional[Dict[str, Any]]:
        if not hasattr(self._db, "get_box_outcome"):
            return None
        try:
            return self._db.get_box_outcome(box_type=box_type, box_id=box_id)
        except Exception:
            return None

    def _count_events(self, box_id: str) -> int:
        if not hasattr(self._db, "connect"):
            return 0
        try:
            with self._db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*) AS c FROM audit_events WHERE box_id = %s",
                    (box_id,),
                )
                row = cur.fetchone()
            if row is None:
                return 0
            value = row["c"] if isinstance(row, dict) or hasattr(row, "__getitem__") else row
            return int(value or 0)
        except Exception:
            return 0

    def _last_event_id(self, box_id: str) -> Optional[str]:
        if not hasattr(self._db, "connect"):
            return None
        try:
            with self._db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id FROM audit_events
                    WHERE box_id = %s
                    ORDER BY ts DESC LIMIT 1
                    """,
                    (box_id,),
                )
                row = cur.fetchone()
            if not row:
                return None
            return str(row["id"]) if hasattr(row, "__getitem__") else str(row[0])
        except Exception:
            return None

    def _upsert_summary(
        self,
        *,
        organization_id: str,
        box_type: str,
        box_id: str,
        state: str,
        summary_json: str,
        timeline_preview_json: str,
        exceptions_json: str,
        outcome_json: Optional[str],
        event_count: int,
        last_event_id: Optional[str],
        last_state_at: str,
        updated_at: str,
    ) -> int:
        if not hasattr(self._db, "connect"):
            return 0
        with self._db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO box_summary
                  (box_type, box_id, organization_id, state,
                   summary_json, timeline_preview_json, exceptions_json,
                   outcome_json, event_count, last_event_id,
                   last_state_at, updated_at)
                VALUES
                  (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (box_type, box_id) DO UPDATE SET
                  organization_id = EXCLUDED.organization_id,
                  state = EXCLUDED.state,
                  summary_json = EXCLUDED.summary_json,
                  timeline_preview_json = EXCLUDED.timeline_preview_json,
                  exceptions_json = EXCLUDED.exceptions_json,
                  outcome_json = EXCLUDED.outcome_json,
                  event_count = EXCLUDED.event_count,
                  last_event_id = EXCLUDED.last_event_id,
                  last_state_at = EXCLUDED.last_state_at,
                  updated_at = EXCLUDED.updated_at
                """,
                (
                    box_type, box_id, organization_id, state,
                    summary_json, timeline_preview_json, exceptions_json,
                    outcome_json, event_count, last_event_id,
                    last_state_at, updated_at,
                ),
            )
            conn.commit()
        return 1

    def _insert_history(
        self,
        *,
        organization_id: str,
        box_type: str,
        box_id: str,
        state: str,
        summary_json: str,
        snapshot_at: str,
        transition_event_id: Optional[str],
        triggered_by: str,
    ) -> int:
        if not hasattr(self._db, "connect"):
            return 0
        with self._db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO box_summary_history
                  (id, box_type, box_id, organization_id,
                   snapshot_at, state, summary_json,
                   transition_event_id, triggered_by)
                VALUES
                  (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    f"BSH-{uuid.uuid4().hex}",
                    box_type, box_id, organization_id,
                    snapshot_at, state, summary_json,
                    transition_event_id, triggered_by,
                ),
            )
            conn.commit()
        return 1


# ─── VendorSummaryProjector ────────────────────────────────────────


class VendorSummaryProjector:
    """Maintains ``vendor_summary`` — per-vendor rollup keyed on
    ``(organization_id, vendor_name_normalized)``. BlackLine-style:
    one row per vendor per org, recomputed on every AP outcome.

    Recompute is full-scan over the vendor's AP items (bounded — we
    only fire on terminal state transitions and rate-limit by
    ``last_activity_at``). The full scan is correct across every
    schema change and avoids drift; for a v1 it's the right
    tradeoff. v2 can replace it with incremental deltas.
    """

    projector_name = "vendor_summary"
    box_types = ("ap_item",)

    # State transitions that change a vendor's rollup.
    _RELEVANT_STATES = frozenset({
        "posted_to_erp", "paid", "rejected", "failed_post",
        "needs_info", "needs_approval", "reversed", "closed",
    })

    def __init__(self, db: Any = None) -> None:
        self._db = db

    @property
    def db(self) -> Any:
        if self._db is not None:
            return self._db
        from clearledgr.core.database import get_db
        return get_db()

    async def project(self, context: ProjectionContext) -> ProjectionResult:
        if context.new_state not in self._RELEVANT_STATES:
            return ProjectionResult(skip_reason=f"state_not_relevant:{context.new_state}")

        db = self.db
        self._db = db
        item = db.get_ap_item(context.box_id) or {}
        vendor_name = (item.get("vendor_name") or "").strip()
        if not vendor_name:
            return ProjectionResult(skip_reason="no_vendor_name")

        organization_id = (
            context.organization_id
            or item.get("organization_id")
            or "default"
        )
        normalized = self._normalize(vendor_name)

        rollup = self._compute_rollup(organization_id, vendor_name, normalized)
        if rollup is None:
            return ProjectionResult(skip_reason="no_items_found")

        upserted = self._upsert(rollup)
        return ProjectionResult(
            rows_upserted=upserted,
            metadata={
                "vendor": vendor_name,
                "total_bills": rollup["total_bills"],
                "exception_rate": rollup["exception_rate"],
            },
        )

    @staticmethod
    def _normalize(vendor_name: str) -> str:
        """Lowercase + collapse whitespace. Same canonical form the
        vendor_store uses so the rollup PK lines up with vendor
        lookups elsewhere."""
        return " ".join(vendor_name.lower().split())

    def _compute_rollup(
        self, organization_id: str, vendor_name: str, normalized: str,
    ) -> Optional[Dict[str, Any]]:
        if not hasattr(self._db, "connect"):
            return None
        with self._db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, state, amount, currency, created_at, updated_at,
                       posted_at
                FROM ap_items
                WHERE organization_id = %s
                  AND LOWER(TRIM(vendor_name)) = %s
                """,
                (organization_id, normalized),
            )
            rows = cur.fetchall() or []
        if not rows:
            return None

        total_bills = 0
        amount_by_currency: Dict[str, float] = {}
        days_to_pay: List[float] = []
        posted = 0
        paid = 0
        rejected = 0
        exceptions = 0
        last_activity_at: Optional[str] = None

        for raw in rows:
            row = dict(raw) if not isinstance(raw, dict) else raw
            total_bills += 1
            state = str(row.get("state") or "")
            try:
                amount = float(row.get("amount") or 0)
            except Exception:
                amount = 0.0
            currency = str(row.get("currency") or "USD") or "USD"
            amount_by_currency[currency] = amount_by_currency.get(currency, 0.0) + amount

            if state == "posted_to_erp":
                posted += 1
            if state == "paid":
                paid += 1
                created = self._parse_iso(row.get("created_at"))
                paid_at = self._parse_iso(row.get("posted_at") or row.get("updated_at"))
                if created and paid_at and paid_at >= created:
                    days_to_pay.append((paid_at - created).total_seconds() / 86400.0)
            if state in ("rejected", "failed_post"):
                rejected += 1
            if state in ("needs_info", "failed_post"):
                exceptions += 1

            updated = str(row.get("updated_at") or row.get("created_at") or "")
            if updated and (last_activity_at is None or updated > last_activity_at):
                last_activity_at = updated

        avg_days = (sum(days_to_pay) / len(days_to_pay)) if days_to_pay else None
        exception_rate = (exceptions / total_bills) if total_bills else 0.0

        return {
            "organization_id": organization_id,
            "vendor_name_normalized": normalized,
            "vendor_display_name": vendor_name,
            "total_bills": total_bills,
            "total_amount_by_currency_json": json.dumps(amount_by_currency),
            "avg_days_to_pay": avg_days,
            "exception_rate": exception_rate,
            "last_activity_at": last_activity_at,
            "posted_count": posted,
            "paid_count": paid,
            "rejected_count": rejected,
            "recomputed_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _parse_iso(value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            text = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def _upsert(self, rollup: Dict[str, Any]) -> int:
        with self._db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO vendor_summary
                  (organization_id, vendor_name_normalized, vendor_display_name,
                   total_bills, total_amount_by_currency_json,
                   avg_days_to_pay, exception_rate, last_activity_at,
                   posted_count, paid_count, rejected_count, recomputed_at)
                VALUES
                  (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (organization_id, vendor_name_normalized) DO UPDATE SET
                  vendor_display_name = EXCLUDED.vendor_display_name,
                  total_bills = EXCLUDED.total_bills,
                  total_amount_by_currency_json = EXCLUDED.total_amount_by_currency_json,
                  avg_days_to_pay = EXCLUDED.avg_days_to_pay,
                  exception_rate = EXCLUDED.exception_rate,
                  last_activity_at = EXCLUDED.last_activity_at,
                  posted_count = EXCLUDED.posted_count,
                  paid_count = EXCLUDED.paid_count,
                  rejected_count = EXCLUDED.rejected_count,
                  recomputed_at = EXCLUDED.recomputed_at
                """,
                (
                    rollup["organization_id"], rollup["vendor_name_normalized"],
                    rollup["vendor_display_name"], rollup["total_bills"],
                    rollup["total_amount_by_currency_json"],
                    rollup["avg_days_to_pay"], rollup["exception_rate"],
                    rollup["last_activity_at"], rollup["posted_count"],
                    rollup["paid_count"], rollup["rejected_count"],
                    rollup["recomputed_at"],
                ),
            )
            conn.commit()
        return 1


# ─── Read helpers (called by api/ap_items_read_routes + new endpoints) ──


def get_box_summary_row(box_type: str, box_id: str, *, db: Any = None) -> Optional[Dict[str, Any]]:
    """Single-row lookup on box_summary. Returns None if missing.

    Caller is expected to fall through to live composition when this
    returns None (or when staleness_check_enabled and the row is
    behind the audit-events tip).
    """
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()
    if not hasattr(db, "connect"):
        return None
    db.initialize() if hasattr(db, "initialize") else None
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM box_summary WHERE box_type = %s AND box_id = %s",
            (box_type, box_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _hydrate_summary_row(dict(row))


def get_box_history(
    box_type: str,
    box_id: str,
    *,
    at: Optional[str] = None,
    limit: int = 50,
    db: Any = None,
) -> List[Dict[str, Any]]:
    """Time-travel query. With ``at=<ISO ts>`` returns the latest
    snapshot at or before ``at`` (1 row). Without ``at``, returns the
    *limit* most recent snapshots in descending order."""
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()
    if not hasattr(db, "connect"):
        return []
    db.initialize() if hasattr(db, "initialize") else None
    with db.connect() as conn:
        cur = conn.cursor()
        if at:
            cur.execute(
                """
                SELECT * FROM box_summary_history
                WHERE box_type = %s AND box_id = %s
                  AND snapshot_at <= %s
                ORDER BY snapshot_at DESC
                LIMIT 1
                """,
                (box_type, box_id, at),
            )
        else:
            cur.execute(
                """
                SELECT * FROM box_summary_history
                WHERE box_type = %s AND box_id = %s
                ORDER BY snapshot_at DESC
                LIMIT %s
                """,
                (box_type, box_id, int(limit)),
            )
        rows = cur.fetchall() or []
    return [_hydrate_history_row(dict(r)) for r in rows]


def get_vendor_summary_row(
    organization_id: str, vendor_name: str, *, db: Any = None,
) -> Optional[Dict[str, Any]]:
    """Single-row lookup on vendor_summary by normalized vendor name."""
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()
    if not hasattr(db, "connect"):
        return None
    db.initialize() if hasattr(db, "initialize") else None
    normalized = " ".join(vendor_name.lower().split())
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM vendor_summary
            WHERE organization_id = %s
              AND vendor_name_normalized = %s
            """,
            (organization_id, normalized),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _hydrate_vendor_row(dict(row))


def list_vendor_summaries(
    organization_id: str,
    *,
    order_by: str = "last_activity_at",
    limit: int = 100,
    db: Any = None,
) -> List[Dict[str, Any]]:
    """List rollups for an org. order_by must be in a small allowlist
    to prevent SQL injection."""
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()
    if not hasattr(db, "connect"):
        return []
    db.initialize() if hasattr(db, "initialize") else None
    allowed = {
        "last_activity_at": "last_activity_at DESC NULLS LAST",
        "exception_rate": "exception_rate DESC",
        "total_bills": "total_bills DESC",
        "vendor_display_name": "vendor_display_name ASC",
    }
    order_clause = allowed.get(order_by, allowed["last_activity_at"])
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT * FROM vendor_summary
            WHERE organization_id = %s
            ORDER BY {order_clause}
            LIMIT %s
            """,
            (organization_id, int(limit)),
        )
        rows = cur.fetchall() or []
    return [_hydrate_vendor_row(dict(r)) for r in rows]


# ─── Ops helper: rebuild ───────────────────────────────────────────


async def rebuild_projections(
    organization_id: str,
    *,
    box_type: str = "ap_item",
    db: Any = None,
    limit: int = 5000,
) -> Dict[str, int]:
    """Recompute every projection from scratch for an org. Used after
    schema migrations or projector logic changes — the cheap way to
    bring rollups back in sync without bouncing the worker.

    Walks ap_items for the org, builds a synthetic ProjectionContext
    per item, calls each registered projector. Returns counts per
    projector for the ops endpoint to display.
    """
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()

    items = db.list_ap_items(organization_id, limit=limit) or []
    counts = {name: 0 for name in _PROJECTOR_REGISTRY}
    skipped = {name: 0 for name in _PROJECTOR_REGISTRY}

    for item in items:
        ap_item_id = str(item.get("id") or "").strip()
        if not ap_item_id:
            continue
        ctx = ProjectionContext(
            organization_id=organization_id,
            box_type=box_type,
            box_id=ap_item_id,
            old_state="",
            new_state=str(item.get("state") or ""),
            actor_id="ops:rebuild",
            correlation_id=None,
            source_type=str(item.get("source_type") or "gmail"),
            erp_native=bool(item.get("erp_native") or False),
            metadata={},
            transition_event_id=None,
        )
        for name, projector in _PROJECTOR_REGISTRY.items():
            if box_type not in (projector.box_types or ()):
                continue
            try:
                result = await projector.project(ctx)
                if result.skip_reason:
                    skipped[name] += 1
                else:
                    counts[name] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "rebuild_projections: %s failed on %s — %s",
                    name, ap_item_id, exc,
                )
    return {
        "items_processed": len(items),
        **{f"{k}_applied": v for k, v in counts.items()},
        **{f"{k}_skipped": v for k, v in skipped.items()},
    }


# ─── Row hydration ─────────────────────────────────────────────────


def _hydrate_summary_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "box_type": row.get("box_type"),
        "box_id": row.get("box_id"),
        "organization_id": row.get("organization_id"),
        "state": row.get("state"),
        "summary": _safe_json(row.get("summary_json"), {}),
        "timeline_preview": _safe_json(row.get("timeline_preview_json"), []),
        "exceptions": _safe_json(row.get("exceptions_json"), []),
        "outcome": _safe_json(row.get("outcome_json"), None),
        "event_count": row.get("event_count") or 0,
        "last_event_id": row.get("last_event_id"),
        "last_state_at": row.get("last_state_at"),
        "updated_at": row.get("updated_at"),
    }


def _hydrate_history_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "box_type": row.get("box_type"),
        "box_id": row.get("box_id"),
        "organization_id": row.get("organization_id"),
        "snapshot_at": row.get("snapshot_at"),
        "state": row.get("state"),
        "summary": _safe_json(row.get("summary_json"), {}),
        "transition_event_id": row.get("transition_event_id"),
        "triggered_by": row.get("triggered_by"),
    }


def _hydrate_vendor_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "organization_id": row.get("organization_id"),
        "vendor_name_normalized": row.get("vendor_name_normalized"),
        "vendor_display_name": row.get("vendor_display_name"),
        "total_bills": row.get("total_bills") or 0,
        "total_amount_by_currency": _safe_json(
            row.get("total_amount_by_currency_json"), {},
        ),
        "avg_days_to_pay": row.get("avg_days_to_pay"),
        "exception_rate": row.get("exception_rate") or 0.0,
        "last_activity_at": row.get("last_activity_at"),
        "posted_count": row.get("posted_count") or 0,
        "paid_count": row.get("paid_count") or 0,
        "rejected_count": row.get("rejected_count") or 0,
        "recomputed_at": row.get("recomputed_at"),
    }


def _safe_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return default
    return default


# ─── Module-level registration ─────────────────────────────────────


_register_outbox_handler()


# Register the two built-in projectors at import time. Concrete db
# binding happens lazily — projectors call ``get_db()`` themselves
# the first time ``project()`` runs. Importing this module never
# touches the DB, so it's safe to run before the test fixtures
# install a Postgres harness.
def _register_default_projectors() -> None:
    try:
        register_projector(BoxSummaryProjector())
    except Exception as exc:  # noqa: BLE001
        logger.warning("box_projection: BoxSummaryProjector registration — %s", exc)
    try:
        register_projector(VendorSummaryProjector())
    except Exception as exc:  # noqa: BLE001
        logger.warning("box_projection: VendorSummaryProjector registration — %s", exc)


_register_default_projectors()
