"""Slack interactive handlers for AP invoice approvals (PRD v1)."""
from __future__ import annotations

import json
import urllib.parse
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from clearledgr.core.database import get_db
from clearledgr.services.invoice_workflow import get_invoice_workflow
from clearledgr.services.slack_api import verify_slack_signature

router = APIRouter(tags=["slack-invoices"])


def _parse_form(body: bytes) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for item in body.decode().split("&"):
        if "=" in item:
            key, value = item.split("=", 1)
            data[key] = value
    return data


def _extract_payload(body: bytes) -> Dict[str, Any]:
    form = _parse_form(body)
    payload_str = form.get("payload")
    if not payload_str:
        raise HTTPException(status_code=400, detail="Missing payload")
    payload = json.loads(urllib.parse.unquote(payload_str))
    return payload


def _get_ap_item_id(action: Dict[str, Any]) -> str:
    value = action.get("value") or ""
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return str(parsed.get("ap_item_id") or "").strip()
        except Exception:
            pass
    if value:
        return value
    action_id = action.get("action_id", "")
    if "_" in action_id:
        return action_id.split("_")[-1]
    return action_id


def _get_run_id(action: Dict[str, Any], ap_item_id: str) -> str:
    value = action.get("value") or ""
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return str(parsed.get("run_id") or ap_item_id)
        except Exception:
            pass
    return ap_item_id


def _audit_rejected_callback(
    ap_item_id: str,
    reason: str,
    timestamp: str,
    signature: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    if not ap_item_id:
        return
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        return
    state = item.get("state")
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    channel = payload.get("channel", {}) if isinstance(payload, dict) else {}
    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "approval_callback_rejected",
            "from_state": state,
            "to_state": state,
            "actor_type": "system",
            "actor_id": "slack_callback",
            "reason": reason,
            "metadata": {
                "source": "slack",
                "timestamp": timestamp,
                "signature_prefix": signature[:12] if signature else "",
            },
            "idempotency_key": f"approval_callback_rejected:slack:{ap_item_id}:{timestamp}:{signature[:16] if signature else 'missing'}",
            "external_refs": {
                "source_channel": f"slack:{channel.get('id')}" if isinstance(channel, dict) and channel.get("id") else "slack",
                "slack_message_ts": message.get("ts") if isinstance(message, dict) else None,
                "gmail_thread_id": item.get("thread_id"),
                "gmail_message_id": item.get("message_id"),
            },
            "organization_id": item.get("organization_id") or "default",
        }
    )


@router.post("/slack/invoices/interactive")
@router.post("/api/slack/actions")
async def handle_invoice_interactive(request: Request):
    """Handle Slack interactive actions for invoice approvals."""
    body = await request.body()
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")

    rejected_payload: Dict[str, Any] = {}
    rejected_ap_item_id = ""
    try:
        rejected_payload = _extract_payload(body)
        rejected_action = (rejected_payload.get("actions") or [{}])[0]
        rejected_ap_item_id = _get_ap_item_id(rejected_action)
    except Exception:
        rejected_payload = {}
        rejected_ap_item_id = ""

    if not verify_slack_signature(body, timestamp, signature):
        _audit_rejected_callback(
            rejected_ap_item_id,
            reason="invalid_signature",
            timestamp=timestamp,
            signature=signature,
            payload=rejected_payload,
        )
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    payload = _extract_payload(body)
    action = (payload.get("actions") or [{}])[0]
    action_id = action.get("action_id", "")
    ap_item_id = _get_ap_item_id(action)
    run_id = _get_run_id(action, ap_item_id)

    user = payload.get("user", {})
    approved_by = user.get("username") or user.get("name") or user.get("id") or "slack_user"
    channel_id = (payload.get("channel") or {}).get("id")
    message_ts = (payload.get("message") or {}).get("ts")

    db = get_db()
    ap_item = db.get_ap_item(ap_item_id) if ap_item_id else None
    organization_id = (ap_item or {}).get("organization_id") or "default"

    workflow = get_invoice_workflow(organization_id)

    if action_id in {"approve_ap", "approve_invoice", "post_to_erp"} or action_id.startswith(("approve_ap_", "approve_invoice_", "post_to_erp_")):
        result = await workflow.approve_ap_item(
            ap_item_id=ap_item_id,
            approved_by=approved_by,
            source_channel=f"slack:{channel_id}" if channel_id else "slack",
            source_message_ref=message_ts,
            idempotency_key=f"slack_approve:{run_id}:{message_ts}:{approved_by}",
        )
        if result.get("status") == "invalid_state":
            return {"response_type": "ephemeral", "text": "Approval not allowed in current state."}
        erp_ref = result.get("erp_reference_id") or result.get("erp_reference")
        detail = f"Posted to ERP: {erp_ref}" if erp_ref else "Posted to ERP"
        return {"response_type": "ephemeral", "text": detail}

    if action_id in {"reject_ap", "reject_invoice"} or action_id.startswith(("reject_ap_", "reject_invoice_")):
        reason = "rejected_in_slack"
        result = await workflow.reject_ap_item(
            ap_item_id=ap_item_id,
            reason=reason,
            rejected_by=approved_by,
            source_channel=f"slack:{channel_id}" if channel_id else "slack",
            source_message_ref=message_ts,
            idempotency_key=f"slack_reject:{run_id}:{message_ts}:{approved_by}",
        )
        if result.get("status") == "invalid_state":
            return {"response_type": "ephemeral", "text": "Rejection not allowed in current state."}
        if result.get("status") == "rejected":
            return {"response_type": "ephemeral", "text": "Invoice rejected."}
        return {"response_type": "ephemeral", "text": "Reject failed."}

    if action_id.startswith("flag_invoice_"):
        return {"response_type": "ephemeral", "text": "Flagged for review."}

    return {"response_type": "ephemeral", "text": "Action received."}
