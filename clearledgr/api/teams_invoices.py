"""Teams interactive handlers for AP invoice approvals (PRD v1)."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from clearledgr.core.database import get_db
from clearledgr.services.invoice_workflow import get_invoice_workflow
from clearledgr.services.teams_api import (
    parse_teams_action_payload,
    verify_teams_request,
)

router = APIRouter(tags=["teams-invoices"])


def _extract_ap_item_id(payload: Dict[str, Any]) -> str:
    return str(payload.get("ap_item_id") or payload.get("value") or "").strip()


def _extract_run_id(payload: Dict[str, Any], ap_item_id: str) -> str:
    return str(payload.get("run_id") or ap_item_id)


def _audit_rejected_callback(payload: Dict[str, Any], reason: str) -> None:
    ap_item_id = _extract_ap_item_id(payload)
    if not ap_item_id:
        return
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        return
    state = item.get("state")
    actor = str(payload.get("actor_id") or payload.get("actor") or "teams_callback")
    message_ref = str(payload.get("message_ref") or payload.get("activity_id") or "")
    channel = str(payload.get("channel") or payload.get("conversation_id") or "teams")
    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "approval_callback_rejected",
            "from_state": state,
            "to_state": state,
            "actor_type": "system",
            "actor_id": "teams_callback",
            "reason": reason,
            "metadata": {
                "source": "teams",
                "actor": actor,
                "message_ref": message_ref,
            },
            "idempotency_key": f"approval_callback_rejected:teams:{ap_item_id}:{message_ref or 'na'}:{actor}",
            "external_refs": {
                "source_channel": f"teams:{channel}",
                "teams_message_id": message_ref or None,
                "gmail_thread_id": item.get("thread_id"),
                "gmail_message_id": item.get("message_id"),
            },
            "organization_id": item.get("organization_id") or "default",
        }
    )


@router.post("/teams/invoices/interactive")
@router.post("/api/teams/actions")
async def handle_teams_invoice_actions(request: Request):
    body = await request.body()
    rejected_payload: Dict[str, Any] = {}
    try:
        rejected_payload = parse_teams_action_payload(body)
    except Exception:
        rejected_payload = {}

    if not await verify_teams_request(body, request.headers):
        if rejected_payload:
            _audit_rejected_callback(rejected_payload, reason="invalid_signature")
        raise HTTPException(status_code=401, detail="Invalid Teams signature")

    try:
        payload = parse_teams_action_payload(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    action = str(payload.get("action") or payload.get("action_id") or "").strip().lower()
    ap_item_id = _extract_ap_item_id(payload)
    run_id = _extract_run_id(payload, ap_item_id)
    if not ap_item_id:
        raise HTTPException(status_code=400, detail="Missing ap_item_id")

    db = get_db()
    ap_item = db.get_ap_item(ap_item_id)
    if not ap_item:
        raise HTTPException(status_code=404, detail="AP item not found")

    org_id = str(payload.get("organization_id") or ap_item.get("organization_id") or "default")
    workflow = get_invoice_workflow(org_id)

    actor_id = str(payload.get("actor_id") or payload.get("actor") or "teams_user")
    actor_display = str(payload.get("actor_display") or actor_id)
    message_ref = str(payload.get("message_ref") or payload.get("activity_id") or "na")
    teams_channel = str(payload.get("channel") or payload.get("conversation_id") or "teams")

    if action in {"approve", "approve_ap", "approve_invoice"}:
        result = await workflow.approve_ap_item(
            ap_item_id=ap_item_id,
            approved_by=actor_display,
            source_channel=f"teams:{teams_channel}",
            source_message_ref=message_ref,
            idempotency_key=f"teams_approve:{run_id}:{message_ref}:{actor_id}",
        )
        if result.get("status") == "invalid_state":
            return {"status": "ignored", "detail": "Approval not allowed in current state."}
        if result.get("status") == "rejected_terminal":
            return {"status": "ignored", "detail": "Rejected AP item cannot be approved."}
        erp_ref = result.get("erp_reference_id") or result.get("erp_reference")
        return {"status": "approved", "erp_reference_id": erp_ref}

    if action in {"reject", "rejected", "reject_ap", "reject_invoice"}:
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            raise HTTPException(status_code=422, detail="Reject reason is required")
        result = await workflow.reject_ap_item(
            ap_item_id=ap_item_id,
            rejected_by=actor_display,
            reason=reason,
            source_channel=f"teams:{teams_channel}",
            source_message_ref=message_ref,
            idempotency_key=f"teams_reject:{run_id}:{message_ref}:{actor_id}",
        )
        if result.get("status") == "invalid_state":
            return {"status": "ignored", "detail": "Rejection not allowed in current state."}
        if result.get("status") == "conflict_post_started":
            return {"status": "conflict", "detail": "Posting already started. Rejection blocked."}
        return {"status": "rejected"}

    raise HTTPException(status_code=400, detail="Unsupported action")
