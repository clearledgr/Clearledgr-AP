"""Slack interactive handlers for AP invoice approvals."""
from __future__ import annotations

import json
import urllib.parse
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from clearledgr.core.database import get_db
from clearledgr.services.invoice_workflow import get_invoice_workflow
from clearledgr.services.slack_api import verify_slack_signature

router = APIRouter(prefix="/slack/invoices", tags=["slack-invoices"])


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


def _get_gmail_id(action: Dict[str, Any]) -> str:
    value = action.get("value") or ""
    if value:
        if value.startswith("{"):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    candidate = parsed.get("gmail_id") or parsed.get("email_id") or parsed.get("invoice_id")
                    if candidate:
                        return str(candidate)
            except Exception:
                pass
        return value
    action_id = action.get("action_id", "")
    for prefix in (
        "approve_invoice_",
        "post_to_erp_",
        "post_to_sap_",
        "reject_invoice_",
        "approve_budget_override_",
        "request_budget_adjustment_",
        "reject_budget_",
        "flag_invoice_",
    ):
        if action_id.startswith(prefix):
            return action_id[len(prefix):]
    if "_" in action_id:
        return action_id.rsplit("_", 1)[-1]
    return action_id


@router.post("/interactive")
async def handle_invoice_interactive(request: Request):
    """Handle Slack interactive actions for invoice approvals."""
    body = await request.body()
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")

    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    payload = _extract_payload(body)
    action = (payload.get("actions") or [{}])[0]
    action_id = action.get("action_id", "")
    gmail_id = _get_gmail_id(action)

    user = payload.get("user", {})
    approved_by = user.get("username") or user.get("name") or user.get("id") or "slack_user"
    channel_id = (payload.get("channel") or {}).get("id")
    message_ts = (payload.get("message") or {}).get("ts")

    db = get_db()
    invoice_row = db.get_invoice_status(gmail_id) if gmail_id else None
    organization_id = (invoice_row or {}).get("organization_id") or "default"

    workflow = get_invoice_workflow(organization_id)

    if action_id.startswith(("approve_invoice_", "post_to_erp_", "post_to_sap_")):
        result = await workflow.approve_invoice(
            gmail_id=gmail_id,
            approved_by=approved_by,
            slack_channel=channel_id,
            slack_ts=message_ts,
        )
        if result.get("status") == "needs_budget_decision":
            return {
                "response_type": "ephemeral",
                "text": "Budget decision required. Use Approve override, Request budget adjustment, or Reject over budget.",
            }
        erp_result = result.get("erp_result") or {}
        doc_num = erp_result.get("doc_num") or erp_result.get("document_number") or erp_result.get("erp_document")
        bill_id = erp_result.get("bill_id")
        detail = f"Bill ID: {bill_id}" if bill_id else "Posted"
        if doc_num:
            detail += f" | Doc #: {doc_num}"
        return {
            "response_type": "ephemeral",
            "text": f"Posted to ERP. {detail}"
        }

    if action_id.startswith("approve_budget_override_"):
        justification = "Approved over budget in Slack"
        value = str(action.get("value") or "")
        if value.startswith("{"):
            try:
                payload_value = json.loads(value)
                if isinstance(payload_value, dict):
                    justification = str(payload_value.get("justification") or justification)
            except Exception:
                pass
        result = await workflow.approve_invoice(
            gmail_id=gmail_id,
            approved_by=approved_by,
            slack_channel=channel_id,
            slack_ts=message_ts,
            allow_budget_override=True,
            override_justification=justification,
        )
        if result.get("status") != "approved":
            return {
                "response_type": "ephemeral",
                "text": f"Budget override failed: {result.get('reason', result.get('status', 'unknown'))}",
            }
        erp_result = result.get("erp_result") or {}
        bill_id = erp_result.get("bill_id") or "n/a"
        return {
            "response_type": "ephemeral",
            "text": f"Budget override approved and posted. Bill ID: {bill_id}",
        }

    if action_id.startswith("request_budget_adjustment_"):
        result = await workflow.request_budget_adjustment(
            gmail_id=gmail_id,
            requested_by=approved_by,
            reason="budget_adjustment_requested_in_slack",
            slack_channel=channel_id,
            slack_ts=message_ts,
        )
        if result.get("status") == "needs_info":
            return {"response_type": "ephemeral", "text": "Budget adjustment requested. Invoice moved to Needs info."}
        return {"response_type": "ephemeral", "text": f"Request failed: {result.get('reason')}"}

    if action_id.startswith(("reject_invoice_", "reject_budget_")):
        reason = "rejected_over_budget_in_slack" if action_id.startswith("reject_budget_") else "rejected_in_slack"
        result = await workflow.reject_invoice(
            gmail_id=gmail_id,
            reason=reason,
            rejected_by=approved_by,
            slack_channel=channel_id,
            slack_ts=message_ts,
        )
        if result.get("status") == "rejected":
            return {"response_type": "ephemeral", "text": "Invoice rejected."}
        return {"response_type": "ephemeral", "text": f"Reject failed: {result.get('reason')}"}

    if action_id.startswith("flag_invoice_"):
        if gmail_id:
            db.update_invoice_status(gmail_id, status="pending_approval", rejection_reason="flagged_in_slack")
        return {"response_type": "ephemeral", "text": "Flagged for review."}

    return {"response_type": "ephemeral", "text": "Action received."}
