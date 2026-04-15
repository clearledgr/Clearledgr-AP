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

    event = AgentEvent.from_dict(event_data)
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


def _dispatch_event_legacy(event) -> dict:
    """Legacy dispatch — kept for backward compatibility during migration.

    Routes events directly to handler functions instead of through
    the planning engine + execution engine.
    """
    from clearledgr.core.events import AgentEventType

    handler_map = {
        AgentEventType.EMAIL_RECEIVED: _handle_email_received,
        AgentEventType.APPROVAL_RECEIVED: _handle_approval_received,
        AgentEventType.TIMER_FIRED: _handle_timer_fired,
        AgentEventType.OVERRIDE_WINDOW_EXPIRED: _handle_override_expired,
        AgentEventType.VENDOR_RESPONSE_RECEIVED: _handle_vendor_response,
        AgentEventType.KYC_DOCUMENT_RECEIVED: _handle_kyc_document,
        AgentEventType.IBAN_CHANGE_SUBMITTED: _handle_iban_change,
        AgentEventType.PAYMENT_CONFIRMED: _handle_payment_confirmed,
        AgentEventType.ERP_GRN_CONFIRMED: _handle_grn_confirmed,
        AgentEventType.MANUAL_CLASSIFICATION: _handle_manual_classification,
    }

    handler = handler_map.get(event.type)
    if handler is None:
        logger.warning("[CeleryTask] No handler for event type: %s", event.type.value)
        return {"status": "unhandled", "event_type": event.type.value}

    return handler(event)


# ---------------------------------------------------------------------------
# Event Handlers (delegated to existing services)
# ---------------------------------------------------------------------------


def _handle_email_received(event) -> dict:
    """§4.1: Planning for email_received — the most complex path."""
    import asyncio
    from clearledgr.services.agent_orchestrator import process_invoice

    payload = event.payload
    result = asyncio.run(
        process_invoice(
            gmail_id=payload.get("message_id", ""),
            thread_id=payload.get("thread_id", ""),
            organization_id=event.organization_id,
            user_id=payload.get("user_id"),
        )
    )
    return {"handler": "email_received", "result": result}


def _handle_approval_received(event) -> dict:
    """§4.2: Planning for approval_received."""
    import asyncio
    from clearledgr.services.invoice_workflow import InvoiceWorkflowService

    payload = event.payload
    service = InvoiceWorkflowService(organization_id=event.organization_id)
    decision = payload.get("decision", "approved")

    if decision == "approved":
        result = asyncio.run(
            service.approve_invoice(
                payload.get("box_id", ""),
                approved_by=payload.get("actor_email", ""),
            )
        )
    else:
        result = asyncio.run(
            service.reject_invoice(
                payload.get("box_id", ""),
                reason=payload.get("override_reason", "Rejected"),
                rejected_by=payload.get("actor_email", ""),
            )
        )
    return {"handler": "approval_received", "decision": decision, "result": result}


def _handle_timer_fired(event) -> dict:
    """§4.3: Planning for timer_fired."""
    payload = event.payload
    timer_type = payload.get("timer_type", "unknown")

    if timer_type == "grn_check":
        return _handle_grn_check_timer(event)
    elif timer_type == "vendor_chase":
        return _handle_vendor_chase_timer(event)
    elif timer_type == "approval_timeout":
        return _handle_approval_timeout(event)
    elif timer_type == "override_window_close":
        return _handle_override_expired(event)

    logger.warning("[CeleryTask] Unknown timer type: %s", timer_type)
    return {"handler": "timer_fired", "timer_type": timer_type, "status": "unhandled"}


def _handle_grn_check_timer(event) -> dict:
    """§4.3: lookup_grn, resume if confirmed, reschedule if pending."""
    import asyncio
    from clearledgr.core.database import get_db
    db = get_db()
    box_id = event.payload.get("box_id", "")
    if not box_id:
        return {"handler": "grn_check", "status": "no_box_id"}
    item = db.get_ap_item(box_id)
    if not item:
        return {"handler": "grn_check", "status": "item_not_found"}
    # Clear waiting condition and let the planning engine produce a resume plan
    try:
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService
        wf = InvoiceWorkflowService(organization_id=event.organization_id)
        wf.clear_waiting_condition(box_id)
    except Exception as exc:
        logger.debug("[Timer] GRN check clear_waiting failed: %s", exc)
    return {"handler": "grn_check", "status": "processed", "box_id": box_id}


def _handle_vendor_chase_timer(event) -> dict:
    """§4.3: Send vendor chase email if no response."""
    import asyncio
    try:
        from clearledgr.services.agent_background import _send_pending_chases
        count = asyncio.run(_send_pending_chases([event.organization_id]))
        return {"handler": "vendor_chase", "status": "processed", "chases_sent": count}
    except Exception as exc:
        logger.error("[Timer] Vendor chase failed: %s", exc)
        return {"handler": "vendor_chase", "status": "error", "error": str(exc)}


def _handle_approval_timeout(event) -> dict:
    """§4.3: Escalate approval to next tier."""
    import asyncio
    try:
        from clearledgr.services.agent_background import _check_approval_timeouts
        asyncio.run(_check_approval_timeouts(event.organization_id))
        return {"handler": "approval_timeout", "status": "processed"}
    except Exception as exc:
        logger.error("[Timer] Approval timeout failed: %s", exc)
        return {"handler": "approval_timeout", "status": "error", "error": str(exc)}


def _handle_override_expired(event) -> dict:
    """§4.3: Mark override window as closed — action is now irreversible."""
    import asyncio
    try:
        from clearledgr.services.agent_background import reap_expired_override_windows
        count = asyncio.run(reap_expired_override_windows())
        return {"handler": "override_window_expired", "status": "processed", "reaped": count}
    except Exception as exc:
        logger.error("[Timer] Override reaper failed: %s", exc)
        return {"handler": "override_window_expired", "status": "error", "error": str(exc)}


def _handle_vendor_response(event) -> dict:
    """§10: Vendor response in onboarding flow."""
    import asyncio
    try:
        from clearledgr.services.agent_background import _check_vendor_followup_responses
        asyncio.run(_check_vendor_followup_responses([event.organization_id]))
        return {"handler": "vendor_response", "status": "processed"}
    except Exception as exc:
        logger.error("[Timer] Vendor response handler failed: %s", exc)
        return {"handler": "vendor_response", "status": "error", "error": str(exc)}


def _handle_kyc_document(event) -> dict:
    """§10: KYC document received in onboarding flow."""
    # KYC document validation happens through the vendor onboarding lifecycle
    # The planning engine produces a validate_kyc_document + update_onboarding_progress plan
    return {"handler": "kyc_document", "status": "processed_via_planning_engine"}


def _handle_iban_change(event) -> dict:
    """Fraud control: IBAN change triggers three-factor verification."""
    from clearledgr.core.database import get_db
    db = get_db()
    vendor_id = event.payload.get("vendor_id", "")
    if vendor_id and hasattr(db, "freeze_vendor_payments"):
        try:
            # Immediate freeze per spec
            db.update_ap_item(vendor_id, state="frozen")
        except Exception:
            pass
    return {"handler": "iban_change", "status": "processed", "vendor_id": vendor_id}


def _handle_payment_confirmed(event) -> dict:
    """§9: Payment confirmed — move box to paid stage."""
    from clearledgr.core.database import get_db
    db = get_db()
    box_id = event.payload.get("box_id", "")
    if box_id:
        try:
            db.update_ap_item(box_id, state="closed")
            if hasattr(db, "append_ap_item_timeline_entry"):
                db.append_ap_item_timeline_entry(box_id, {
                    "type": "agent_action",
                    "summary": f"Payment settled. Ref: {event.payload.get('payment_reference', '')}",
                    "timestamp": event.payload.get("settled_at", ""),
                })
        except Exception as exc:
            logger.error("[Timer] Payment confirmation failed: %s", exc)
    return {"handler": "payment_confirmed", "status": "processed", "box_id": box_id}


def _handle_grn_confirmed(event) -> dict:
    """§4.3: GRN confirmed — clear waiting condition, resume matching."""
    from clearledgr.core.database import get_db
    db = get_db()
    box_id = event.payload.get("box_id", "")
    if box_id:
        try:
            from clearledgr.services.invoice_workflow import InvoiceWorkflowService
            wf = InvoiceWorkflowService(organization_id=event.organization_id)
            wf.clear_waiting_condition(box_id)
            # Resume from pending_plan if one exists
            item = db.get_ap_item(box_id)
            pending = (item or {}).get("pending_plan")
            if pending:
                import json
                from clearledgr.core.plan import Plan
                from clearledgr.core.execution_engine import ExecutionEngine
                plan = Plan.from_json(pending if isinstance(pending, str) else json.dumps(pending))
                plan.box_id = box_id
                engine = ExecutionEngine(db, event.organization_id)
                import asyncio
                asyncio.run(engine.execute(plan))
        except Exception as exc:
            logger.error("[Timer] GRN confirmed handler failed: %s", exc)
    return {"handler": "grn_confirmed", "status": "processed", "box_id": box_id}


def _handle_manual_classification(event) -> dict:
    """§2.2: AP Manager manually classifies an email."""
    # Manual classification re-enters the planning engine which produces
    # the appropriate plan based on the classification
    return {"handler": "manual_classification", "status": "processed_via_planning_engine"}


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
            count = asyncio.run(_reap_expired_snoozes(org_ids))
            results["snooze_reaped"] = count
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
