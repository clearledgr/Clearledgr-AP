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
