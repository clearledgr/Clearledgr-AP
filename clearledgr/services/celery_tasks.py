"""Celery Tasks — Agent Design Specification §11.2.1.

Task definitions for the Celery worker fleet. Each task consumes an
AgentEvent from the Redis Streams queue and dispatches it to the
planning engine with workspace concurrency enforcement.
"""
from __future__ import annotations

import logging
import os
import socket

from clearledgr.services.celery_app import app

logger = logging.getLogger(__name__)

_CONSUMER_NAME = f"worker-{socket.gethostname()}-{os.getpid()}"


@app.task(bind=True, max_retries=3, default_retry_delay=5)
def process_agent_event(self, event_data: dict) -> dict:
    """Process a single agent event with workspace concurrency enforcement.

    §11.2.1: Worker acquires a semaphore slot before processing.
    If at capacity, the task retries with 5-second backoff.
    §5: Event is dispatched to the planning engine for execution.
    """
    from clearledgr.core.events import AgentEvent
    from clearledgr.services.workspace_semaphore import WorkspaceSemaphore

    # Parse defensively. A malformed payload (missing keys, wrong
    # types, non-dict) would otherwise raise inside the main try
    # block AFTER the except clause has captured `event` — so the
    # except fallback that references `event.id` and `event.type`
    # would itself raise NameError and obscure the root cause.
    # Worse, Celery would retry the parse 3× at 5s intervals before
    # giving up. A poison payload is never going to parse on retry,
    # so we ack it immediately with a structured failure result and
    # don't waste workspace-semaphore slots or API quota on retries.
    try:
        event = AgentEvent.from_dict(event_data)
    except Exception as exc:
        logger.error(
            "[CeleryTask] poison payload dropped (parse failed): %s | event_data keys=%s",
            exc,
            sorted(list((event_data or {}).keys()))[:10] if isinstance(event_data, dict) else type(event_data).__name__,
        )
        return {
            "event_id": None,
            "event_type": None,
            "organization_id": None,
            "status": "poison_payload",
            "error": str(exc),
        }
    org_id = event.organization_id

    # §11: Record queue_to_planning SLA latency
    try:
        from datetime import datetime, timezone as _tz
        from clearledgr.core.sla_tracker import get_sla_tracker
        if event.created_at:
            created = datetime.fromisoformat(event.created_at.replace("Z", "+00:00"))
            queue_latency_ms = int((datetime.now(_tz.utc) - created).total_seconds() * 1000)
            get_sla_tracker().record(
                "queue_to_planning", queue_latency_ms,
                ap_item_id=event.payload.get("message_id") or event.payload.get("box_id"),
                organization_id=org_id,
            )
    except Exception:
        pass

    # §11.2.2: Acquire workspace concurrency slot
    semaphore = WorkspaceSemaphore(org_id)
    if not semaphore.acquire():
        logger.info(
            "[CeleryTask] Workspace %s at concurrency limit, retrying in 5s",
            org_id,
        )
        raise self.retry(countdown=5)

    try:
        result = _dispatch_event(event)
        return {
            "event_id": event.id,
            "event_type": event.type.value,
            "organization_id": org_id,
            "status": "completed",
            "result": result,
        }
    except Exception as exc:
        logger.error(
            "[CeleryTask] Event %s (%s) failed: %s",
            event.id, event.type.value, exc,
        )
        return {
            "event_id": event.id,
            "event_type": event.type.value,
            "organization_id": org_id,
            "status": "failed",
            "error": str(exc),
        }
    finally:
        semaphore.release()


def _dispatch_event(event) -> dict:
    """§4 + §5: Planning Engine produces Plan, Execution Engine runs it.

    This is the canonical event processing path. Every event goes through:
    1. DeterministicPlanningEngine.plan(event, box_state) → Plan
    2. ExecutionEngine.execute(plan) → ExecutionResult
    """
    import asyncio
    from clearledgr.core.database import get_db
    from clearledgr.core.planning_engine import get_planning_engine
    from clearledgr.core.execution_engine import ExecutionEngine

    db = get_db()
    box_state = _load_box_state(event, db)

    # §4: Planning engine produces the Plan (deterministic, no Claude)
    planner = get_planning_engine(db)
    plan = planner.plan(event, box_state)

    if plan.is_empty:
        return {"status": "no_plan", "event_type": event.type.value}

    # Set box_id from existing state if available
    if box_state.get("id"):
        plan.box_id = box_state["id"]

    # §5: Execution engine runs the Plan (mechanical, one action at a time)
    engine = ExecutionEngine(db, event.organization_id)
    result = asyncio.run(engine.execute(plan))

    return result.to_dict()


def _load_box_state(event, db) -> dict:
    """Load existing Box state for the event (if any)."""
    payload = event.payload or {}
    box_id = payload.get("box_id") or payload.get("ap_item_id")
    if box_id:
        try:
            item = db.get_ap_item(box_id)
            if item:
                return dict(item)
        except Exception:
            pass
    # Try by thread_id / message_id
    thread_id = payload.get("thread_id") or payload.get("message_id")
    if thread_id:
        try:
            item = db.get_ap_item_by_thread(event.organization_id, thread_id)
            if item:
                return dict(item)
        except Exception:
            pass
    return {}



# Note: the legacy event dispatcher (_dispatch_event_legacy + a tree of
# _handle_* per-event-type helpers) used to live here. It was the pre-
# planning-engine code path; once _dispatch_event was wired into
# process_agent_event the whole dispatcher table became dead code, but
# the broken bits stayed live as landmines — _handle_iban_change in
# particular would have silently no-op'd the IBAN-change fraud freeze
# (it called update_ap_item with the vendor name as the ap_item_id, and
# its outer guard was `hasattr(db, "freeze_vendor_payments")` which is
# always False because that method only exists as an Action verb on the
# execution engine). Deleted to remove the tripwire — the planning +
# execution engine path (_dispatch_event above) is the canonical and
# only event flow.

# ---------------------------------------------------------------------------
# Scheduled tasks (Celery Beat)
# ---------------------------------------------------------------------------


@app.task
def drain_event_stream() -> dict:
    """§2: Consume events from Redis Streams and dispatch to workers.

    Runs every 2 seconds via Celery Beat. Claims up to 10 events per
    tick and dispatches each to a process_agent_event Celery task.
    This is the ONLY consumer — Gmail webhooks and Slack callbacks
    enqueue to the stream, this task drains it.

    Also writes the Beat heartbeat key that the ops health endpoint
    reads — if this stops ticking, Beat is dead.
    """
    from clearledgr.core.event_queue import get_event_queue

    try:
        queue = get_event_queue()
        # Beat heartbeat (cheap: one SET with TTL per tick, ~2s cadence).
        try:
            from datetime import datetime, timezone
            queue._redis.set(
                "clearledgr:beat:last-tick",
                datetime.now(timezone.utc).isoformat(),
                ex=300,  # expire after 5min so absence = dead
            )
        except Exception:
            pass

        dispatched = 0
        for _ in range(10):  # Max 10 events per tick
            claimed = queue.claim_next(_CONSUMER_NAME, block_ms=0)
            if not claimed:
                break
            stream, entry_id, event = claimed
            # Dispatch to Celery worker for processing
            process_agent_event.delay(event.to_dict())
            # Ack the stream entry — worker handles retries via Celery
            queue.ack(stream, entry_id)
            dispatched += 1
        return {"status": "ok", "dispatched": dispatched}
    except Exception as exc:
        logger.debug("[CeleryBeat] drain_event_stream: %s", exc)
        return {"status": "error", "error": str(exc)}


@app.task
def fire_pending_timers() -> dict:
    """§4.3: Check for timer-fired events and enqueue them.

    Runs every 60 seconds via Celery Beat (vs old 15-min polling).
    Checks: snooze reaper, override window reaper, vendor chases,
    approval timeouts, ERP retry drain.
    """
    import asyncio
    results = {}

    # Snooze reaper
    try:
        from clearledgr.services.agent_background import _reap_expired_snoozes
        from clearledgr.core.database import get_db
        db = get_db()
        db.initialize()
        # Get all org IDs with active items
        org_ids = []
        try:
            with db.connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT DISTINCT organization_id FROM ap_items WHERE state = 'snoozed' LIMIT 100")
                org_ids = [r[0] for r in cur.fetchall()]
        except Exception:
            pass
        if org_ids:
            unsnoozed = asyncio.run(_reap_expired_snoozes(org_ids))
            results["snooze_reaped"] = sum(len(v) for v in unsnoozed.values())
    except Exception as exc:
        results["snooze_error"] = str(exc)

    # Override window reaper
    try:
        from clearledgr.services.agent_background import reap_expired_override_windows
        count = asyncio.run(reap_expired_override_windows())
        results["override_reaped"] = count
    except Exception as exc:
        results["override_error"] = str(exc)

    # ERP retry drain
    try:
        from clearledgr.services.agent_background import _drain_erp_post_retry_queue
        asyncio.run(_drain_erp_post_retry_queue())
        results["erp_retry_drained"] = True
    except Exception as exc:
        results["erp_retry_error"] = str(exc)

    # §11.2.4: Queue depth + workspace concurrency back-pressure monitoring
    try:
        from clearledgr.services.agent_background import _check_queue_depth_and_concurrency
        bp_result = asyncio.run(_check_queue_depth_and_concurrency())
        results["back_pressure"] = {
            "queue_pending": bp_result.get("queue_pending"),
            "queue_depth_sustained_min": bp_result.get("queue_depth_sustained_min"),
            "workspaces_at_limit": len(bp_result.get("workspaces_at_limit", [])),
        }
    except Exception as exc:
        results["back_pressure_error"] = str(exc)

    # §12.2: Fire erp_recheck timers for paused items whose expected_by has passed
    try:
        from clearledgr.services.agent_background import _fire_erp_recheck_timers
        from clearledgr.core.database import get_db
        _db = get_db()
        _db.initialize()
        org_ids = []
        try:
            with _db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT DISTINCT organization_id FROM ap_items "
                    "WHERE waiting_condition IS NOT NULL LIMIT 100"
                )
                org_ids = [r[0] for r in cur.fetchall() if r[0]]
        except Exception:
            pass
        total_fired = 0
        for oid in org_ids:
            total_fired += asyncio.run(_fire_erp_recheck_timers(oid))
        results["erp_recheck_fired"] = total_fired
    except Exception as exc:
        results["erp_recheck_error"] = str(exc)

    return {"status": "ok", **results}


@app.task
def reclaim_stale_events() -> dict:
    """§12.1: Reclaim events from dead workers.

    Runs every 30 seconds. Takes over events that have been pending
    longer than the visibility timeout (60s).
    """
    from clearledgr.core.event_queue import get_event_queue

    try:
        queue = get_event_queue()
        reclaimed = queue.reclaim_stale(_CONSUMER_NAME)
        for stream, entry_id, event in reclaimed:
            process_agent_event.delay(event.to_dict())
        return {"status": "ok", "reclaimed": len(reclaimed)}
    except Exception as exc:
        logger.error("[CeleryBeat] reclaim_stale_events failed: %s", exc)
        return {"status": "error", "error": str(exc)}


@app.task
def purge_soft_deleted_orgs() -> dict:
    """Hard-purge tenant data for orgs past their legal-hold window.

    Soft-delete (organizations.deleted_at) marks an org as dead but
    leaves every ap_item, vendor, OAuth token, ERP credential in
    place for a legal-hold window so compliance / legal can export
    data and confirm nothing live is still using it. After that
    window, this task runs the destructive purge:

      1. DELETE FROM every org-scoped table WHERE organization_id = ?
         (audit_events and ap_policy_audit_events are excluded — they
          have append-only triggers and a separate 7-year regulatory
          retention obligation that outlives the tenant).
      2. Stamp organizations.purged_at so we don't re-purge.
      3. Emit an `organization_hard_purged` audit event so the data
         destruction itself lives in the audit trail.

    Window controlled by ORG_LEGAL_HOLD_DAYS (default 30). Runs daily.
    Idempotent: re-running on an already-purged org is a no-op
    (caught by the purged_at filter in list_orgs_eligible_for_purge).
    """
    import os
    from clearledgr.core.clock import now_utc_iso
    from clearledgr.core.database import get_db

    try:
        legal_hold_days = int(os.getenv("ORG_LEGAL_HOLD_DAYS", "30"))
        db = get_db()
        eligible = db.list_orgs_eligible_for_purge(legal_hold_days=legal_hold_days)
        if not eligible:
            return {"status": "ok", "purged": 0, "legal_hold_days": legal_hold_days}

        total_orgs = 0
        total_rows = 0
        for org_row in eligible:
            org_id = str(org_row.get("id") or "").strip()
            if not org_id:
                continue
            counts = db.purge_organization_data(org_id)
            rows_deleted = sum(counts.values())
            total_orgs += 1
            total_rows += rows_deleted
            purged_at = now_utc_iso()
            try:
                db.update_organization(org_id, purged_at=purged_at)
            except Exception as exc:
                logger.warning(
                    "[purge] stamping purged_at failed for org=%s: %s", org_id, exc
                )
            try:
                db.append_ap_audit_event({
                    "event_type": "organization_hard_purged",
                    "actor_type": "system",
                    "actor_id": "retention_job",
                    "organization_id": org_id,
                    "source": "retention",
                    "payload_json": {
                        "legal_hold_days": legal_hold_days,
                        "deleted_at": org_row.get("deleted_at"),
                        "purged_at": purged_at,
                        "rows_deleted": rows_deleted,
                        "tables_touched": sorted(counts.keys()),
                    },
                })
            except Exception as exc:
                logger.warning(
                    "[purge] audit write failed for org=%s: %s", org_id, exc
                )
        return {
            "status": "ok",
            "orgs_purged": total_orgs,
            "rows_deleted": total_rows,
            "legal_hold_days": legal_hold_days,
        }
    except Exception as exc:
        logger.error("[CeleryBeat] purge_soft_deleted_orgs failed: %s", exc)
        return {"status": "error", "error": str(exc)}


@app.task
def reap_completed_retry_jobs() -> dict:
    """Daily reaper for terminal agent_retry_jobs rows.

    The agent_retry_jobs table carries a UNIQUE index on
    idempotency_key. Without retention, the index grows for the life
    of the deployment and the get_agent_retry_job_by_key lookup
    degrades. Audit history lives in the (append-only) audit_events
    table — agent_retry_jobs is a transient queue, not an audit log,
    so it's safe to drop terminal rows after the retention window.
    Default 90 days (override via RETRY_JOB_RETENTION_DAYS env var).
    """
    import os
    from clearledgr.core.database import get_db

    try:
        days = int(os.getenv("RETRY_JOB_RETENTION_DAYS", "90"))
        deleted = get_db().reap_completed_agent_retry_jobs(older_than_days=days)
        return {"status": "ok", "deleted": int(deleted), "older_than_days": days}
    except Exception as exc:
        logger.error("[CeleryBeat] reap_completed_retry_jobs failed: %s", exc)
        return {"status": "error", "error": str(exc)}


@app.task
def reap_expired_seats_task() -> dict:
    """§13 Read-Only seat auto-expiry.

    Walks all users with seat_type='read_only' and seat_expires_at in
    the past; soft-archives them via the same path as manual removal
    so audit attribution is preserved and billing seat count is
    adjusted. Safe to run daily — idempotent via is_active guard.
    """
    from clearledgr.core.database import get_db

    try:
        reaped = get_db().reap_expired_seats()
        return {"status": "ok", "reaped": int(reaped)}
    except Exception as exc:
        logger.error("[CeleryBeat] reap_expired_seats failed: %s", exc)
        return {"status": "error", "error": str(exc)}


## §13 Agent Activity retention is enforced as a query-time filter in
## clearledgr/api/ap_audit.py, not a reaper — audit_events is
## architecturally append-only (§7.6 audit trail as evidence of trust).
## See list_recent_ap_audit_events_with_retention on the AP store.
