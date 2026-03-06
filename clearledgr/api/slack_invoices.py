"""Slack interactive handlers for AP invoice approvals."""
from __future__ import annotations

import json
import hashlib
import logging
import urllib.parse
from typing import Any, Dict

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from clearledgr.core.approval_action_contract import (
    ApprovalActionContractError,
    NormalizedApprovalAction,
    is_stale_action,
    normalize_slack_action,
    validate_action_state_preflight,
)
from clearledgr.core.database import get_db
from clearledgr.core.launch_controls import get_channel_action_block_reason
from clearledgr.core.slack_verify import require_slack_signature
from clearledgr.services.invoice_workflow import get_invoice_workflow

router = APIRouter(prefix="/slack/invoices", tags=["slack-invoices"])
logger = logging.getLogger(__name__)


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
    payload = json.loads(urllib.parse.unquote_plus(payload_str))
    return payload


def _audit_callback_event(
    db,
    *,
    event_type: str,
    source: str,
    organization_id: str = "default",
    ap_item_id: str | None = None,
    actor_id: str | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    reason: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> None:
    resolved_ap_item_id = ap_item_id or f"channel_callback:slack:{organization_id}"
    try:
        db.append_ap_audit_event(
            {
                "ap_item_id": resolved_ap_item_id,
                "event_type": event_type,
                "actor_type": "user" if actor_id else "system",
                "actor_id": actor_id or f"{source}_callback",
                "source": source,
                "idempotency_key": idempotency_key,
                "correlation_id": correlation_id,
                "reason": reason,
                "metadata": metadata or {},
                "organization_id": organization_id,
            }
        )
    except Exception as exc:  # pragma: no cover - best effort
        logger.error("Could not audit %s callback event: %s", source, exc)


def _resolve_ap_context(db, organization_id: str, gmail_id: str) -> tuple[str, str | None]:
    if not gmail_id:
        return organization_id or "default", None
    row = db.get_invoice_status(gmail_id) if hasattr(db, "get_invoice_status") else None
    org_id = str((row or {}).get("organization_id") or organization_id or "default")
    ap_item_id = None
    if hasattr(db, "get_ap_item_by_thread"):
        try:
            ap_row = db.get_ap_item_by_thread(org_id, gmail_id)
            if ap_row and ap_row.get("id"):
                ap_item_id = str(ap_row["id"])
        except Exception:
            ap_item_id = None
    return org_id, ap_item_id


def _resolve_correlation_id(db, ap_item_id: str | None, org_id: str, gmail_id: str) -> str | None:
    try:
        row = None
        if ap_item_id and hasattr(db, "get_ap_item"):
            row = db.get_ap_item(ap_item_id)
        if row is None and gmail_id and hasattr(db, "get_invoice_status"):
            row = db.get_invoice_status(gmail_id)
        raw_meta = (row or {}).get("metadata")
        if isinstance(raw_meta, dict):
            metadata = raw_meta
        elif isinstance(raw_meta, str) and raw_meta.strip():
            metadata = json.loads(raw_meta)
        else:
            metadata = {}
        corr = str(metadata.get("correlation_id") or "").strip()
        return corr or None
    except Exception:
        return None


async def _post_to_response_url(response_url: str, payload: Dict[str, Any]) -> None:
    """Post a follow-up message to Slack's response_url (best-effort)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(response_url, json=payload)
    except Exception as exc:
        logger.error("Failed to post Slack response_url follow-up: %s", exc)


def _slack_duplicate_response() -> Dict[str, str]:
    return {
        "response_type": "ephemeral",
        "text": "Duplicate action ignored. This approval action was already processed.",
    }


def _slack_stale_response() -> Dict[str, str]:
    return {
        "response_type": "ephemeral",
        "text": "This approval action is stale/expired. Refresh the card and try again.",
    }


async def _dispatch_slack_action(
    workflow,
    action: NormalizedApprovalAction,
) -> Dict[str, Any]:
    common_kwargs = {
        "source_channel": "slack",
        "source_channel_id": action.source_channel_id,
        "source_message_ref": action.source_message_ref,
        "slack_channel": action.source_channel_id,
        "slack_ts": action.source_message_ref,
        "actor_display": action.actor_display,
        "action_run_id": action.run_id,
        "decision_request_ts": action.request_ts,
        "decision_idempotency_key": action.idempotency_key,
        "correlation_id": action.correlation_id,
    }
    if action.action == "approve":
        result = await workflow.approve_invoice(
            gmail_id=action.gmail_id,
            approved_by=action.actor_id,
            allow_budget_override=bool(action.action_variant == "budget_override"),
            override_justification=action.reason if action.action_variant == "budget_override" else None,
            **common_kwargs,
        )
        if result.get("status") == "needs_budget_decision":
            return {
                "response_type": "ephemeral",
                "text": "Budget decision required. Use Approve override, Request info, or Reject.",
                "result": result,
            }
        if result.get("status") == "needs_field_review":
            return {
                "response_type": "ephemeral",
                "text": "Field review required before posting. Open the invoice to review critical fields.",
                "result": result,
            }
        if result.get("status") != "approved":
            return {
                "response_type": "ephemeral",
                "text": f"Approve failed: {result.get('reason', result.get('status', 'unknown'))}",
                "result": result,
            }
        erp_result = result.get("erp_result") or {}
        doc_num = erp_result.get("doc_num") or erp_result.get("document_number") or erp_result.get("erp_document")
        bill_id = erp_result.get("bill_id")
        detail = f"Bill ID: {bill_id}" if bill_id else "Posted"
        if doc_num:
            detail += f" | Doc #: {doc_num}"
        prefix = "Budget override approved and posted." if action.action_variant == "budget_override" else "Posted to ERP."
        return {"response_type": "ephemeral", "text": f"{prefix} {detail}", "result": result}

    if action.action == "request_info":
        result = await workflow.request_budget_adjustment(
            gmail_id=action.gmail_id,
            requested_by=action.actor_id,
            reason=action.reason or "request_info_in_slack",
            **common_kwargs,
        )
        if result.get("status") == "needs_info":
            return {"response_type": "ephemeral", "text": "Request for info recorded. Invoice moved to Needs info.", "result": result}
        return {"response_type": "ephemeral", "text": f"Request failed: {result.get('reason', result.get('status', 'unknown'))}", "result": result}

    if action.action == "reject":
        result = await workflow.reject_invoice(
            gmail_id=action.gmail_id,
            reason=action.reason or "rejected_in_slack",
            rejected_by=action.actor_id,
            **common_kwargs,
        )
        if result.get("status") == "rejected":
            return {"response_type": "ephemeral", "text": "Invoice rejected.", "result": result}
        return {"response_type": "ephemeral", "text": f"Reject failed: {result.get('reason', result.get('status', 'unknown'))}", "result": result}

    raise HTTPException(status_code=400, detail="unsupported_action")


@router.post("/interactive")
async def handle_invoice_interactive(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack interactive actions for invoice approvals."""
    db = get_db()
    try:
        body = await require_slack_signature(request)
    except HTTPException as exc:
        raw_body = await request.body()
        body_hash = hashlib.sha256(raw_body or b"").hexdigest()[:16]
        _audit_callback_event(
            db,
            event_type="channel_callback_unauthorized",
            source="slack",
            idempotency_key=f"slack:unauthorized:{body_hash}",
            reason=str(exc.detail),
            metadata={"status_code": exc.status_code},
        )
        raise

    try:
        payload = _extract_payload(body)
    except HTTPException as exc:
        body_hash = hashlib.sha256(body or b"").hexdigest()[:16]
        _audit_callback_event(
            db,
            event_type="channel_action_invalid",
            source="slack",
            idempotency_key=f"slack:invalid:{body_hash}",
            reason="invalid_payload",
            metadata={"detail": str(exc.detail), "status_code": exc.status_code},
        )
        raise
    except Exception:
        body_hash = hashlib.sha256(body or b"").hexdigest()[:16]
        _audit_callback_event(
            db,
            event_type="channel_action_invalid",
            source="slack",
            idempotency_key=f"slack:invalid:{body_hash}",
            reason="invalid_payload",
            metadata={"detail": "malformed_payload", "status_code": 400},
        )
        raise HTTPException(status_code=400, detail="invalid_payload")
    raw_action = (payload.get("actions") or [{}])[0] if isinstance((payload.get("actions") or [{}])[0], dict) else {}
    gmail_candidate = ""
    value = str(raw_action.get("value") or "")
    if value.startswith("{"):
        try:
            parsed_value = json.loads(value)
            if isinstance(parsed_value, dict):
                gmail_candidate = str(parsed_value.get("gmail_id") or parsed_value.get("email_id") or parsed_value.get("invoice_id") or "")
        except Exception:
            gmail_candidate = ""
    if not gmail_candidate:
        action_id = str(raw_action.get("action_id") or "")
        if "_" in action_id:
            gmail_candidate = action_id.rsplit("_", 1)[-1]
    organization_id, ap_item_id = _resolve_ap_context(db, "default", gmail_candidate)

    try:
        normalized = normalize_slack_action(
            payload,
            request_ts=request.headers.get("x-slack-request-timestamp"),
            organization_id=organization_id,
        )
    except ApprovalActionContractError as exc:
        _audit_callback_event(
            db,
            event_type="channel_action_invalid",
            source="slack",
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            reason=exc.code,
            metadata={"message": exc.message, "gmail_id": gmail_candidate or None},
        )
        return {"response_type": "ephemeral", "text": f"Action rejected: {exc.message}"}

    normalized.ap_item_id = ap_item_id
    normalized.correlation_id = _resolve_correlation_id(db, ap_item_id, organization_id, normalized.gmail_id)

    blocked_reason = get_channel_action_block_reason(
        normalized.organization_id,
        "slack",
        db=db,
    )
    if blocked_reason:
        _audit_callback_event(
            db,
            event_type="channel_action_blocked",
            source="slack",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:blocked",
            reason=blocked_reason,
            metadata={"action": normalized.to_dict()},
            correlation_id=normalized.correlation_id,
        )
        return {
            "response_type": "ephemeral",
            "text": f"Slack approval actions are temporarily disabled. Reason: {blocked_reason}",
        }

    if is_stale_action(normalized):
        _audit_callback_event(
            db,
            event_type="channel_action_stale",
            source="slack",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:stale",
            reason="stale_action",
            metadata={"action": normalized.to_dict()},
            correlation_id=normalized.correlation_id,
        )
        return _slack_stale_response()

    processed_key = f"{normalized.idempotency_key}:processed"
    if db.get_ap_audit_event_by_key(processed_key):
        _audit_callback_event(
            db,
            event_type="channel_action_duplicate",
            source="slack",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:duplicate",
            reason="duplicate_callback",
            metadata={"action": normalized.to_dict()},
            correlation_id=normalized.correlation_id,
        )
        return _slack_duplicate_response()

    # H18: Pre-flight state check — reject actions invalid for current AP state.
    ap_item_row = None
    if normalized.ap_item_id and hasattr(db, "get_ap_item"):
        try:
            ap_item_row = db.get_ap_item(normalized.ap_item_id)
        except Exception:
            pass
    preflight_block = validate_action_state_preflight(normalized, ap_item_row)
    if preflight_block:
        _audit_callback_event(
            db,
            event_type="channel_action_blocked",
            source="slack",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:preflight_blocked",
            reason=preflight_block,
            metadata={"action": normalized.to_dict()},
            correlation_id=normalized.correlation_id,
        )
        return {
            "response_type": "ephemeral",
            "text": f"Action not allowed: {preflight_block}",
        }

    _audit_callback_event(
        db,
        event_type="channel_action_received",
        source="slack",
        organization_id=normalized.organization_id,
        ap_item_id=normalized.ap_item_id,
        actor_id=normalized.actor_id,
        idempotency_key=f"{normalized.idempotency_key}:received",
        metadata={"action": normalized.to_dict()},
        correlation_id=normalized.correlation_id,
    )

    response_url = str(payload.get("response_url") or "").strip()

    workflow = get_invoice_workflow(normalized.organization_id)
    try:
        response = await _dispatch_slack_action(workflow, normalized)
    except HTTPException as exc:
        _audit_callback_event(
            db,
            event_type="channel_action_failed",
            source="slack",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:failed",
            reason=str(exc.detail),
            metadata={"action": normalized.to_dict(), "status_code": exc.status_code},
            correlation_id=normalized.correlation_id,
        )
        raise

    _audit_callback_event(
        db,
        event_type="channel_action_processed",
        source="slack",
        organization_id=normalized.organization_id,
        ap_item_id=normalized.ap_item_id,
        actor_id=normalized.actor_id,
        idempotency_key=processed_key,
        metadata={
            "action": normalized.to_dict(),
            "response_type": response.get("response_type"),
            "text": response.get("text"),
            "result_status": (response.get("result") or {}).get("status"),
        },
        correlation_id=normalized.correlation_id,
    )

    final_reply = {"response_type": response.get("response_type", "ephemeral"), "text": response.get("text", "Action received.")}
    if response_url:
        background_tasks.add_task(_post_to_response_url, response_url, final_reply)
        return {"response_type": "ephemeral", "text": "Processing..."}
    return final_reply
