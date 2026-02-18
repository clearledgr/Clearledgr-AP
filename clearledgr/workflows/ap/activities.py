"""Temporal AP workflow activities.

Activities wrap existing AP services to keep one deterministic execution path.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from temporalio import activity

from clearledgr.core.database import get_db
from clearledgr.services.browser_agent import get_browser_agent_service
from clearledgr.services.invoice_workflow import get_invoice_workflow


@activity.defn(name="clearledgr.ap.append_audit")
async def append_audit_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    db = get_db()
    event = db.append_ap_audit_event(payload)
    return event or {}


@activity.defn(name="clearledgr.ap.request_approval")
async def request_approval_activity(organization_id: str, ap_item_id: str) -> Dict[str, Any]:
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        return {"status": "not_found"}
    workflow = get_invoice_workflow(organization_id)
    updated = await workflow.request_approval(item, reason="temporal_request_approval")
    return {"status": "needs_approval", "ap_item": updated}


@activity.defn(name="clearledgr.ap.approve")
async def approve_activity(
    organization_id: str,
    ap_item_id: str,
    approved_by: str,
    source_channel: str,
    source_message_ref: str,
    idempotency_key: str,
) -> Dict[str, Any]:
    workflow = get_invoice_workflow(organization_id)
    return await workflow.approve_ap_item(
        ap_item_id=ap_item_id,
        approved_by=approved_by,
        source_channel=source_channel,
        source_message_ref=source_message_ref,
        idempotency_key=idempotency_key,
    )


@activity.defn(name="clearledgr.ap.reject")
async def reject_activity(
    organization_id: str,
    ap_item_id: str,
    rejected_by: str,
    reason: str,
    source_channel: str,
    source_message_ref: str,
    idempotency_key: str,
) -> Dict[str, Any]:
    workflow = get_invoice_workflow(organization_id)
    return await workflow.reject_ap_item(
        ap_item_id=ap_item_id,
        rejected_by=rejected_by,
        reason=reason,
        source_channel=source_channel,
        source_message_ref=source_message_ref,
        idempotency_key=idempotency_key,
    )


@activity.defn(name="clearledgr.ap.retry_post")
async def retry_post_activity(organization_id: str, ap_item_id: str, actor_id: str) -> Dict[str, Any]:
    workflow = get_invoice_workflow(organization_id)
    return await workflow.retry_post(ap_item_id, actor_id=actor_id)


@activity.defn(name="clearledgr.ap.dispatch_browser_commands")
async def dispatch_browser_commands_activity(
    organization_id: str,
    ap_item_id: str,
    session_id: str,
    commands: list[Dict[str, Any]],
    actor_id: str = "workflow",
) -> Dict[str, Any]:
    service = get_browser_agent_service()
    events = []
    for command in commands or []:
        event = service.enqueue_command(
            session_id=session_id,
            command=command,
            actor_id=actor_id,
            confirm=bool(command.get("confirm")),
            confirmed_by=command.get("confirmed_by"),
        )
        events.append(event)
    return {
        "organization_id": organization_id,
        "ap_item_id": ap_item_id,
        "session_id": session_id,
        "queued": len([event for event in events if event.get("status") == "queued"]),
        "blocked": len([event for event in events if event.get("status") == "blocked_for_approval"]),
        "events": events,
    }


@activity.defn(name="clearledgr.ap.wait_browser_results")
async def wait_browser_results_activity(session_id: str, timeout_seconds: int = 30) -> Dict[str, Any]:
    db = get_db()
    waited = 0
    while waited < max(1, timeout_seconds):
        events = db.list_browser_action_events(session_id)
        pending = [event for event in events if event.get("status") in {"queued", "running", "blocked_for_approval"}]
        if not pending:
            return {
                "session_id": session_id,
                "status": "completed",
                "events": events,
            }
        await asyncio.sleep(1)
        waited += 1
    events = db.list_browser_action_events(session_id)
    return {
        "session_id": session_id,
        "status": "timeout",
        "pending": [event for event in events if event.get("status") in {"queued", "running", "blocked_for_approval"}],
        "events": events,
    }
