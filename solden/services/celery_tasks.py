"""Celery Tasks — Agent Design Specification §11.2.1.

Task definitions for the Celery worker fleet. Each task consumes an
AgentEvent from the Redis Streams queue and dispatches it to the
planning engine with workspace concurrency enforcement.
"""
from __future__ import annotations

import logging
import os
import socket

from solden.core.org_utils import assert_org_id
from solden.services.celery_app import app

logger = logging.getLogger(__name__)

_CONSUMER_NAME = f"worker-{socket.gethostname()}-{os.getpid()}"


@app.task(bind=True, max_retries=3, default_retry_delay=5)
def process_agent_event(self, event_data: dict) -> dict:
    """Process a single agent event with workspace concurrency enforcement.

    §11.2.1: Worker acquires a semaphore slot before processing.
    If at capacity, the task retries with 5-second backoff.
    §5: Event is dispatched to the planning engine for execution.
    """
    from solden.core.events import AgentEvent
    from solden.services.workspace_semaphore import WorkspaceSemaphore

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
        from solden.core.sla_tracker import get_sla_tracker
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
    """§4 + §5: Planning Engine produces Plan, Coordination Engine runs it.

    This is the canonical event processing path. Every event goes through:
    1. DeterministicPlanningEngine.plan(event, box_state) → Plan
    2. CoordinationEngine.execute(plan) → CoordinationResult
    """
    import asyncio
    from solden.core.database import get_db
    from solden.core.planning_engine import get_planning_engine
    from solden.core.coordination_engine import CoordinationEngine

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

    # §5: Coordination engine runs the Plan (mechanical, one action at a time)
    engine = CoordinationEngine(db, event.organization_id)
    result = asyncio.run(engine.execute(plan))

    return result.to_dict()


def _load_box_state(event, db) -> dict:
    """Load existing Box state for the event (if any).

    Org-scoped at the data layer. Pre-fix the box_id path called
    ``db.get_ap_item(box_id)`` purely by primary key — if a Celery
    message somehow carried an ``organization_id`` of tenant A and a
    ``box_id`` from tenant B (poisoned event, message replay across
    tenants, or a queue-routing bug), the planner would receive
    tenant B's row as the box state and the coordination engine
    would then execute the event under tenant A's runtime against
    tenant B's data. The thread_id path was already scoped via
    ``get_ap_item_by_thread(organization_id, thread_id)``; the
    box_id path now mirrors it via a post-fetch organization_id
    check that fails closed (returns empty box state) on mismatch.
    """
    payload = event.payload or {}
    box_id = payload.get("box_id") or payload.get("ap_item_id")
    event_org = str(getattr(event, "organization_id", "") or "").strip()
    if box_id:
        try:
            item = db.get_ap_item(box_id)
            if item:
                row_org = str(item.get("organization_id") or "").strip()
                if event_org and row_org and row_org == event_org:
                    return dict(item)
                # Mismatch (or missing org on either side) → log and
                # fall through. Returning the foreign row would be a
                # cross-tenant box-state leak; surfacing the
                # mismatch explicitly is better than silently
                # treating the event as having no prior box state,
                # because that hides the queue-routing bug.
                if row_org and event_org and row_org != event_org:
                    logger.error(
                        "[CeleryTask] cross-tenant box-state mismatch: "
                        "event.org=%s box_id=%s row.org=%s — refusing to "
                        "load foreign box state",
                        event_org, box_id, row_org,
                    )
        except Exception:
            pass
    # Try by thread_id / message_id (already org-scoped at the SQL layer).
    thread_id = payload.get("thread_id") or payload.get("message_id")
    if thread_id and event_org:
        try:
            item = db.get_ap_item_by_thread(event_org, thread_id)
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
    from solden.core.event_queue import get_event_queue

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


@app.task(bind=True, max_retries=3, default_retry_delay=10)
def process_gmail_push(self, email_address: str, history_id: str) -> dict:
    """Process a Gmail Pub/Sub push notification on the worker fleet.

    The /gmail/push endpoint on the api service used to run this work
    inline via FastAPI BackgroundTasks. That blocked the uvicorn event
    loop on every push (synchronous LLM classification + Gmail history
    fetch + per-message processing), causing gunicorn WORKER TIMEOUT
    spirals and 502s on unrelated requests like /auth/google/callback.

    The right architecture: web enqueues, worker drains. The api now
    calls `process_gmail_push.delay(email_address, history_id)` and
    returns 200 to Google. This Celery task picks it up, fetches the
    history, classifies messages, and enqueues per-message events
    onto the same Redis stream that drain_event_stream consumes.
    """
    import asyncio

    from solden.api.gmail_webhooks import process_gmail_notification

    try:
        asyncio.run(process_gmail_notification(email_address, history_id))
        return {
            "status": "completed",
            "email_address": email_address,
            "history_id": history_id,
        }
    except Exception as exc:
        logger.error(
            "[CeleryTask] process_gmail_push failed (%s history=%s): %s",
            email_address, history_id, exc,
        )
        # Retry up to max_retries with exponential-ish backoff (default
        # 10s × attempt). A poison push (e.g., revoked Gmail token) will
        # error consistently; after 3 retries Celery drops it.
        raise self.retry(exc=exc)


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
        from solden.services.agent_background import _reap_expired_snoozes
        from solden.core.database import get_db
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
        from solden.services.agent_background import reap_expired_override_windows
        count = asyncio.run(reap_expired_override_windows())
        results["override_reaped"] = count
    except Exception as exc:
        results["override_error"] = str(exc)

    # ERP retry drain
    try:
        from solden.services.agent_background import _drain_erp_post_retry_queue
        asyncio.run(_drain_erp_post_retry_queue())
        results["erp_retry_drained"] = True
    except Exception as exc:
        results["erp_retry_error"] = str(exc)

    # §11.2.4: Queue depth + workspace concurrency back-pressure monitoring
    try:
        from solden.services.agent_background import _check_queue_depth_and_concurrency
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
        from solden.services.agent_background import _fire_erp_recheck_timers
        from solden.core.database import get_db
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
def reap_orphan_approval_dispatches_tick() -> dict:
    """Dedicated Celery beat task for the approval-dispatch outbox reaper.

    The outbox state machine in
    ``InvoiceWorkflowService._send_for_approval`` flips a row to
    ``orphan`` when Slack delivery succeeded but the post-delivery DB
    writes failed. The CRITICAL log line at the failure site carries
    the slack_ts so a human could reconcile manually; this task closes
    the loop by re-running the post-delivery writes against the
    cached slack_ts.

    Cadence is 60s (vs 30s for the override-window reaper) because
    orphans are expected to be very rare (Slack succeeded but a
    transient DB blip swallowed the next write); we don't need
    sub-minute recovery latency for the operator-noticeable case.
    """
    import asyncio
    try:
        from solden.services.agent_background import reap_orphan_approval_dispatches
        count = asyncio.run(reap_orphan_approval_dispatches())
        return {"status": "ok", "recovered": count}
    except Exception as exc:  # noqa: BLE001
        logger.error("[reap_orphan_approval_dispatches_tick] failed: %s", exc)
        return {"status": "error", "error": str(exc)}


@app.task
def reap_override_windows_tick() -> dict:
    """Dedicated Celery beat task for the override-window reaper.

    Group 6 (2026-05-07): the override reaper was historically
    bundled into ``fire_pending_timers`` alongside snooze reap +
    ERP retry drain + queue-depth checks. That made operational
    debugging fuzzy: a Datadog alert on
    ``fire_pending_timers.duration`` could be triggered by any of
    five different reapers. Splitting the override reaper into its
    own task means:

      * Per-task metrics (duration, count, error rate) attribute
        cleanly to the override-window subsystem.
      * Tighter cadence (30s vs 60s) reduces max time-from-expiry-
        to-reap. Override windows are 15 minutes by default and
        can shrink to 15-min minimum on medium-confidence posts;
        a 30s sweep keeps the tail latency under a minute.
      * Failures isolate: if the reaper raises, only override
        reaping is affected — snooze, ERP retry, queue depth all
        continue on their own task.

    Runs alongside the FastAPI ``_override_window_reaper_loop`` for
    redundancy: if the FastAPI process dies, Celery beat still
    sweeps; if Celery beat dies, FastAPI still sweeps. Both call
    the same idempotent reaper so concurrent execution is safe.
    """
    import asyncio
    try:
        from solden.services.agent_background import reap_expired_override_windows
        count = asyncio.run(reap_expired_override_windows())
        return {"status": "ok", "reaped": count}
    except Exception as exc:  # noqa: BLE001
        logger.error("[reap_override_windows_tick] failed: %s", exc)
        return {"status": "error", "error": str(exc)}


@app.task
def reclaim_stale_events() -> dict:
    """§12.1: Reclaim events from dead workers.

    Runs every 30 seconds. Takes over events that have been pending
    longer than the visibility timeout (60s).
    """
    from solden.core.event_queue import get_event_queue

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
    from solden.core.clock import now_utc_iso
    from solden.core.database import get_db

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
                db.append_audit_event({
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
    from solden.core.database import get_db

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
    from solden.core.database import get_db

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


@app.task
def post_month_end_accruals_all_orgs() -> dict:
    """Wave 5 / G5 carry-over — month-end close run.

    Runs once per month (1st of the month, 02:00 UTC) via Celery Beat.
    Walks every active org, builds the prior month's accrual JE
    proposal, posts to each org's connected ERP. Idempotent at the
    DB layer (partial unique index on accrual_je_runs blocks a
    second successful post for the same period)."""
    from datetime import datetime, timedelta, timezone

    from solden.core.database import get_db
    from solden.services.accrual_journal_entry_post import (
        run_month_end_close,
    )

    db = get_db()
    db.initialize()

    # Just-closed period = first day of current month minus 1
    # day = last day of prior month.
    today = datetime.now(timezone.utc).date()
    period_end_date = today.replace(day=1) - timedelta(days=1)
    period_start_date = period_end_date.replace(day=1)
    period_start = period_start_date.isoformat()
    period_end = period_end_date.isoformat()

    summary = {
        "period_start": period_start,
        "period_end": period_end,
        "orgs_processed": 0,
        "posted": 0,
        "failed": 0,
        "no_op": 0,
        "details": [],
    }

    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT organization_id FROM erp_connections"
            )
            org_ids = [dict(r)["organization_id"] for r in cur.fetchall()]
    except Exception as exc:
        logger.error("[CeleryBeat] month-end accrual: org enum failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    for org_id in org_ids:
        # Resolve the org's primary ERP for entry shape selection.
        erp_type = "xero"
        try:
            with db.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT erp_type FROM erp_connections "
                    "WHERE organization_id = %s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (org_id,),
                )
                row = cur.fetchone()
                if row:
                    erp_type = str(dict(row).get("erp_type") or "xero").lower()
        except Exception:
            pass

        try:
            outcome = run_month_end_close(
                db,
                organization_id=org_id,
                period_start=period_start,
                period_end=period_end,
                erp_type=erp_type,
                actor_id="celery_month_end_accrual",
            )
        except ValueError as exc:
            # Duplicate period run — skip without counting as failure.
            summary["details"].append({
                "org": org_id, "status": "skipped",
                "reason": str(exc)[:200],
            })
            continue
        except Exception as exc:
            logger.warning(
                "[CeleryBeat] month-end accrual failed org=%s: %s",
                org_id, exc,
            )
            summary["failed"] += 1
            summary["details"].append({
                "org": org_id, "status": "failed",
                "reason": str(exc)[:200],
            })
            continue

        summary["orgs_processed"] += 1
        if outcome.status == "posted" and not outcome.accrual_run_id:
            summary["no_op"] += 1
            summary["details"].append({
                "org": org_id, "status": "no_op",
                "reason": "no_received_not_billed_for_period",
            })
        elif outcome.status == "posted":
            summary["posted"] += 1
            summary["details"].append({
                "org": org_id, "status": "posted",
                "run_id": outcome.accrual_run_id,
                "provider_reference": outcome.provider_reference,
            })
        else:
            summary["failed"] += 1
            summary["details"].append({
                "org": org_id, "status": outcome.status,
                "error_reason": outcome.error_reason,
                "run_id": outcome.accrual_run_id,
            })
    return summary


@app.task
def post_pending_accrual_reversals() -> dict:
    """Wave 5 / G5 carry-over — daily reversal sweep.

    Walks accrual_je_runs WHERE status='posted' AND
    reversal_posted_at IS NULL AND reversal_date <= today; posts
    the reversal entry to the org's ERP."""
    from solden.core.database import get_db
    from solden.services.accrual_journal_entry_post import (
        post_pending_reversals,
    )

    db = get_db()
    try:
        result = post_pending_reversals(db)
    except Exception as exc:
        logger.error(
            "[CeleryBeat] accrual reversal sweep failed: %s", exc,
        )
        return {"status": "error", "error": str(exc)}
    return {
        "swept": result.swept,
        "reversed_ok": result.reversed_ok,
        "failed": result.failed,
        "details": result.details[:50],  # bound for log size
    }


@app.task
def poll_sap_b1_payments_all_orgs() -> dict:
    """Walk every org with a SAP connection and poll for cleared
    outgoing payments (Wave 2 / C3 carry-over).

    Handles BOTH SAP B1 and S/4HANA. The poll_sap_b1_payments
    dispatcher inspects the connection's base_url and routes to
    poll_sap_s4hana_payments when the URL doesn't match the B1
    Service Layer pattern (``/b1s/`` segment) — covering S/4HANA
    deployments where CPI Event Mesh isn't wired and the only
    payment signal is the OData IsCleared flag.

    Cadence: every 5 minutes via Celery Beat. Idempotent — the
    payment-tracking layer (C2) deduplicates redelivered payment
    events at the (org, source, payment_id, ap_item_id) compound
    key, so a missed-then-recovered run never double-records.
    """
    import asyncio
    from solden.core.database import get_db
    from solden.services.erp_payment_dispatcher import (
        poll_sap_b1_payments,
    )

    db = get_db()
    db.initialize()

    summary = {
        "orgs_polled": 0,
        "events_dispatched": 0,
        "duplicates": 0,
        "errors": 0,
        "per_org": [],
    }
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT organization_id FROM erp_connections "
                "WHERE erp_type = %s",
                ("sap",),
            )
            org_rows = cur.fetchall()
        org_ids = [dict(r)["organization_id"] for r in org_rows]
    except Exception as exc:
        logger.error("[CeleryBeat] sap b1 poll: org enum failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    for org_id in org_ids:
        try:
            result = asyncio.run(
                poll_sap_b1_payments(organization_id=org_id, db=db),
            )
        except Exception as exc:
            logger.warning(
                "[CeleryBeat] sap b1 poll failed for org=%s: %s",
                org_id, exc,
            )
            summary["errors"] += 1
            continue
        summary["orgs_polled"] += 1
        summary["events_dispatched"] += int(
            result.get("events_dispatched") or 0
        )
        summary["duplicates"] += int(result.get("duplicates") or 0)
        summary["errors"] += int(result.get("errors") or 0)
        summary["per_org"].append({"org": org_id, **result})

    return summary


# ---------------------------------------------------------------------------
# Module 7 v1 Pass 3 — audit-event SIEM webhook fan-out
#
# Fan-out path: ``append_audit_event`` enqueues
# ``dispatch_audit_webhooks(audit_event_id)`` after the canonical
# audit_events INSERT commits. The dispatch task looks up matching
# webhook_subscriptions for the org + event_type, and enqueues
# ``deliver_audit_webhook(audit_event_id, webhook_id, attempt)``
# for each. The deliver task does the actual POST, records one row
# in webhook_deliveries (success OR failure), and retries on failure
# with exponential backoff.
#
# Decoupling rationale: a slow SIEM endpoint should never slow the
# audit_events INSERT. The audit log is the canonical record; webhook
# delivery is downstream observability.
# ---------------------------------------------------------------------------


_AUDIT_WEBHOOK_MAX_ATTEMPTS = 6
# Backoff schedule in seconds: 30s, 2m, 10m, 30m, 2h, 6h.
# Drains a transient outage within minutes; gives a sustained outage
# half a day before the chain stops. Past max_attempts, the row stays
# visible in webhook_deliveries with status='failed' so the leader can
# triage manually.
_AUDIT_WEBHOOK_BACKOFF_SECONDS = (30, 120, 600, 1800, 7200, 21600)


@app.task(bind=True, max_retries=0)  # we manage retries ourselves so each attempt logs
def dispatch_audit_webhooks(self, audit_event_id: str) -> dict:
    """Fan an audit event out to every webhook_subscription that's
    subscribed to its event_type.

    Called from ``append_audit_event`` after the canonical INSERT
    commits. For each matching subscription, enqueues a
    ``deliver_audit_webhook`` task that handles the HTTP delivery +
    delivery-log write + retry chain.
    """
    from solden.core.database import get_db

    db = get_db()
    event = db.get_ap_audit_event(audit_event_id)
    if not event:
        # Reaped or never existed; nothing to fan out.
        return {"status": "skipped", "reason": "event_not_found"}
    organization_id = str(event.get("organization_id") or "")
    event_type = str(event.get("event_type") or "")
    if not organization_id or not event_type:
        return {"status": "skipped", "reason": "missing_org_or_event_type"}

    try:
        subs = db.get_active_webhooks_for_event(organization_id, event_type)
    except Exception as exc:
        logger.exception("[dispatch_audit_webhooks] subscription lookup failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    if not subs:
        return {"status": "noop", "subscribers": 0}

    dispatched = 0
    for sub in subs:
        sub_id = str(sub.get("id") or "")
        if not sub_id:
            continue
        try:
            deliver_audit_webhook.delay(audit_event_id, sub_id, 1)
            dispatched += 1
        except Exception as exc:
            logger.warning(
                "[dispatch_audit_webhooks] enqueue failed for sub=%s event=%s: %s",
                sub_id, audit_event_id, exc,
            )
    return {"status": "dispatched", "subscribers": dispatched, "audit_event_id": audit_event_id}


@app.task(bind=True, max_retries=0)
def deliver_audit_webhook(
    self,
    audit_event_id: str,
    webhook_subscription_id: str,
    attempt: int = 1,
) -> dict:
    """Deliver one audit event to one webhook subscription.

    Records exactly one row in ``webhook_deliveries`` per call —
    success OR failure. On failure with attempt < max, schedules a
    retry via ``deliver_audit_webhook.apply_async(countdown=...)``
    using the exponential backoff schedule above.
    """
    from solden.core.database import get_db
    from solden.services.webhook_delivery import deliver_webhook
    import asyncio
    import time as _time

    db = get_db()
    event = db.get_ap_audit_event(audit_event_id)
    if not event:
        return {"status": "skipped", "reason": "event_or_sub_not_found"}

    organization_id = str(event.get("organization_id") or "")
    # The store now requires organization_id on the by-id lookup
    # (M3 fix). Deriving the scope from the audit event itself means
    # a webhook subscription from a different tenant — even if its id
    # somehow leaked into a celery message — will not be readable
    # here.
    sub = (
        db.get_webhook_subscription(webhook_subscription_id, organization_id)
        if hasattr(db, "get_webhook_subscription") and organization_id
        else None
    )
    if not sub:
        return {"status": "skipped", "reason": "event_or_sub_not_found"}
    if sub.get("is_active") in (False, 0):
        return {"status": "skipped", "reason": "subscription_inactive"}
    event_type = str(event.get("event_type") or "")
    url = str(sub.get("url") or "")
    secret = str(sub.get("secret") or "")
    payload = {
        "audit_event": event,
        "organization_id": organization_id,
    }

    # Deliver. ``deliver_webhook`` is async, so spin a loop just for
    # this call. Each Celery worker invocation is its own thread,
    # so a fresh loop is safe + cheap.
    started_ms = int(_time.time() * 1000)
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ok = loop.run_until_complete(
            deliver_webhook(
                url=url,
                event_type=event_type,
                payload=payload,
                secret=secret,
                webhook_id=f"audit_{audit_event_id}_{webhook_subscription_id}",
            )
        )
        loop.close()
        duration_ms = int(_time.time() * 1000) - started_ms
        status = "success" if ok else "failed"
        error_message = None if ok else "delivery_returned_false"
        http_code = None
    except Exception as exc:
        duration_ms = int(_time.time() * 1000) - started_ms
        ok = False
        status = "failed"
        error_message = str(exc)
        http_code = None
        logger.warning(
            "[deliver_audit_webhook] exception delivering event=%s sub=%s: %s",
            audit_event_id, webhook_subscription_id, exc,
        )

    next_retry_at = None
    if not ok and attempt < _AUDIT_WEBHOOK_MAX_ATTEMPTS:
        backoff = _AUDIT_WEBHOOK_BACKOFF_SECONDS[
            min(attempt - 1, len(_AUDIT_WEBHOOK_BACKOFF_SECONDS) - 1)
        ]
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        next_retry_dt = _dt.now(_tz.utc) + _td(seconds=backoff)
        next_retry_at = next_retry_dt.isoformat()
        try:
            deliver_audit_webhook.apply_async(
                args=[audit_event_id, webhook_subscription_id, attempt + 1],
                countdown=backoff,
            )
        except Exception as enqueue_exc:
            logger.warning(
                "[deliver_audit_webhook] retry enqueue failed: %s", enqueue_exc,
            )

    # Always record the attempt — successful, retrying, or terminal-failed.
    record_status = "retrying" if (not ok and next_retry_at) else status
    try:
        db.insert_webhook_delivery(
            organization_id=organization_id,
            webhook_subscription_id=webhook_subscription_id,
            audit_event_id=audit_event_id,
            event_type=event_type,
            attempt_number=attempt,
            status=record_status,
            http_status_code=http_code,
            error_message=error_message,
            request_url=url,
            request_signature_prefix="sha256=" if secret else None,
            duration_ms=duration_ms,
            next_retry_at=next_retry_at,
        )
    except Exception as log_exc:
        logger.exception(
            "[deliver_audit_webhook] delivery log insert failed: %s", log_exc,
        )

    return {
        "status": record_status,
        "attempt": attempt,
        "audit_event_id": audit_event_id,
        "webhook_subscription_id": webhook_subscription_id,
        "duration_ms": duration_ms,
        "next_retry_at": next_retry_at,
    }


# ---------------------------------------------------------------------------
# Module 7 v1 Pass 2 — async audit-log CSV export
# ---------------------------------------------------------------------------


@app.task(bind=True, max_retries=2, default_retry_delay=10)
def generate_audit_export(self, export_id: str) -> dict:
    """Render the audit-log CSV for a queued export job.

    Pulled by ``POST /api/workspace/audit/export`` (which creates the
    audit_exports row with status='queued'). The task:
      1. Loads the row (fails-soft if it's been reaped).
      2. Flips status to 'running' + stamps started_at.
      3. Streams matching audit_events through ``search_audit_events``
         in pages of 500 — keeps memory bounded for large exports.
      4. Writes a CSV in-memory and stores it on the row's content
         column. Status flips to 'done' + stamps completed_at.
      5. On any exception: status 'failed' + error_message recorded.

    Retries on transient failures (DB pool blip etc) up to 2 times
    with 10s backoff. Hard failures past the retry budget land in
    'failed' state with the error captured for the SPA to show.
    """
    from solden.core.database import get_db
    from datetime import datetime, timezone
    import csv as _csv
    import io as _io
    import json as _json

    db = get_db()
    export = db.get_audit_export(export_id, include_content=False)
    if not export:
        # Reaped or never existed. Nothing to do; don't retry.
        logger.warning("[generate_audit_export] export %s not found", export_id)
        return {"status": "skipped", "export_id": export_id, "reason": "not_found"}

    # Defensive: if a retry fires after the row has already moved
    # past 'queued', don't re-render. The first attempt's content
    # stands.
    if export.get("status") not in ("queued", "running"):
        return {"status": "noop", "export_id": export_id, "current_status": export.get("status")}

    started_at = datetime.now(timezone.utc).isoformat()
    db.update_audit_export_status(export_id, status="running", started_at=started_at)

    try:
        filters = _json.loads(export.get("filters_json") or "{}")
        # CSV column order: stable contract for downstream consumers
        # (customer scripts, SIEM ingestors). Adding columns is fine;
        # reordering would silently break parsers.
        columns = [
            "id", "ts", "event_type", "box_type", "box_id",
            "prev_state", "new_state", "actor_type", "actor_id",
            "decision_reason", "governance_verdict", "agent_confidence",
            "source", "correlation_id", "workflow_id", "run_id",
            "idempotency_key", "organization_id",
            "payload_json", "external_refs",
        ]
        buf = _io.StringIO()
        writer = _csv.writer(buf, quoting=_csv.QUOTE_MINIMAL)
        writer.writerow(columns)

        total_rows = 0
        cursor = None
        page_size = 500
        # Hard cap so a misconfigured filter can't OOM the worker
        # exporting a year of org-wide events. 250K rows is plenty
        # for the demo + most enterprise spot-exports.
        max_rows = 250_000

        while True:
            page = db.search_audit_events(
                organization_id=export.get("organization_id"),
                from_ts=filters.get("from_ts") or None,
                to_ts=filters.get("to_ts") or None,
                event_types=filters.get("event_types") or None,
                actor_id=filters.get("actor_id") or None,
                box_type=filters.get("box_type") or None,
                box_id=filters.get("box_id") or None,
                limit=page_size,
                cursor=cursor,
                # Module 9 §300: the requesting user's entity scope
                # was baked into filters_json at submit time; the
                # worker has no direct auth context so we restore it
                # from the persisted filter. None = org-wide; list =
                # restricted; honor exactly what the submitter saw.
                entity_scope=filters.get("entity_scope"),
            )
            events = page.get("events") or []
            for evt in events:
                row = []
                for col in columns:
                    value = evt.get(col)
                    if isinstance(value, (dict, list)):
                        value = _json.dumps(value, separators=(",", ":"))
                    elif value is None:
                        value = ""
                    row.append(value)
                writer.writerow(row)
            total_rows += len(events)
            if total_rows >= max_rows:
                logger.warning(
                    "[generate_audit_export] export %s hit max_rows cap at %d",
                    export_id, max_rows,
                )
                break
            cursor = page.get("next_cursor")
            if not cursor:
                break

        org_id = assert_org_id(
            export.get("organization_id"),
            context="generate_audit_export",
        )
        date_part = started_at.replace(":", "").replace("-", "")[:15]
        export_format = str(export.get("export_format") or "csv").lower()
        if export_format == "pdf":
            # Re-fetch the events as dicts for the PDF renderer. We
            # already streamed them through the CSV writer above, but
            # the PDF helper takes a list of dicts so we re-page (cap
            # at 5K rows for PDF to keep filesize reasonable — the
            # spec calls PDF a "share with auditor" surface, not a
            # bulk-data dump; CSV remains the dump format).
            from solden.services.workspace_reports import audit_events_to_pdf
            pdf_events = []
            cursor2 = None
            pdf_cap = 5000
            while len(pdf_events) < pdf_cap:
                page = db.search_audit_events(
                    organization_id=export.get("organization_id"),
                    from_ts=filters.get("from_ts") or None,
                    to_ts=filters.get("to_ts") or None,
                    event_types=filters.get("event_types") or None,
                    actor_id=filters.get("actor_id") or None,
                    box_type=filters.get("box_type") or None,
                    box_id=filters.get("box_id") or None,
                    limit=500,
                    cursor=cursor2,
                    entity_scope=filters.get("entity_scope"),
                )
                ev = page.get("events") or []
                pdf_events.extend(ev)
                cursor2 = page.get("next_cursor")
                if not cursor2 or not ev:
                    break
            pdf_bytes = audit_events_to_pdf(
                pdf_events[:pdf_cap],
                org_id=org_id,
                params=filters,
            )
            filename = f"audit-{org_id}-{date_part}.pdf"
            db.set_audit_export_content(
                export_id, content=pdf_bytes, content_filename=filename,
            )
            db.update_audit_export_status(
                export_id,
                status="done",
                completed_at=datetime.now(timezone.utc).isoformat(),
                total_rows=total_rows,
            )
            return {
                "status": "done",
                "export_id": export_id,
                "rows": total_rows,
                "bytes": len(pdf_bytes),
            }

        csv_bytes = buf.getvalue().encode("utf-8")
        filename = f"audit-{org_id}-{date_part}.csv"
        db.set_audit_export_content(
            export_id, content=csv_bytes, content_filename=filename,
        )
        db.update_audit_export_status(
            export_id,
            status="done",
            completed_at=datetime.now(timezone.utc).isoformat(),
            total_rows=total_rows,
        )
        logger.info(
            "[generate_audit_export] export %s done: rows=%d size=%d",
            export_id, total_rows, len(csv_bytes),
        )
        return {
            "status": "done",
            "export_id": export_id,
            "rows": total_rows,
            "bytes": len(csv_bytes),
        }
    except Exception as exc:
        logger.exception("[generate_audit_export] export %s failed: %s", export_id, exc)
        try:
            db.update_audit_export_status(
                export_id,
                status="failed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                error_message=str(exc)[:500],
            )
        except Exception as inner:
            logger.exception("[generate_audit_export] also failed to mark failed: %s", inner)
        # Let Celery retry on transient errors; status 'failed' is
        # also set so the UI shows a clear failure even mid-retry.
        raise self.retry(exc=exc, countdown=10, max_retries=2) from exc


# ---------------------------------------------------------------------------
# Module 8 — scheduled report email delivery.
#
# The beat schedule fires this task hourly (15 min after the hour to
# avoid the busy top-of-hour window on the broker). Each invocation
# pulls up to 100 due rows from ``report_subscriptions`` and delivers
# them one at a time. The per-row failure handling is in the service
# layer; this task is the thin Celery wrapper.
# ---------------------------------------------------------------------------

@app.task
def deliver_due_report_subscriptions() -> dict:
    """Send any report subscriptions whose ``next_due_at`` has passed.

    Returns a summary dict with counts so beat-job logs surface at a
    glance whether the schedule is healthy.
    """
    from solden.core.database import get_db
    from solden.services.report_delivery import deliver_due_subscriptions

    db = get_db()
    try:
        results = deliver_due_subscriptions(db, limit=100)
    except Exception as exc:
        logger.exception("[deliver_due_report_subscriptions] batch failed: %s", exc)
        return {"status": "error", "error": str(exc), "delivered": 0}

    delivered = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    logger.info(
        "[deliver_due_report_subscriptions] processed=%d delivered=%d failed=%d skipped=%d",
        len(results), delivered, failed, skipped,
    )
    return {
        "status": "ok",
        "processed": len(results),
        "delivered": delivered,
        "failed": failed,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Module 11 — escalation policy worker.
#
# Runs every minute. Finds box_exceptions that have crossed any
# active policy's threshold, sends the configured action (email),
# records an escalation_events row for idempotency.
# Acceptance criterion §354: "fires within 1 minute of threshold
# breach" — schedule + per-tick processing latency keep us comfortably
# inside that bound.
# ---------------------------------------------------------------------------

@app.task
def fire_due_escalation_policies() -> dict:
    """Run one pass of the escalation worker."""
    from solden.core.database import get_db
    from solden.services.escalation_runner import run_escalation_tick

    db = get_db()
    try:
        summary = run_escalation_tick(db)
    except Exception as exc:
        logger.exception("[fire_due_escalation_policies] tick failed: %s", exc)
        return {"status": "error", "error": str(exc)}
    return {
        "status": "ok",
        "processed": summary.processed,
        "fired": summary.fired,
        "failed": summary.failed,
        "skipped": summary.skipped,
    }
