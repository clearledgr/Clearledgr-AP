"""Teams interactive handlers for AP invoice approvals."""
from __future__ import annotations

import json
import hashlib
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from clearledgr.core.approval_action_contract import (
    ApprovalActionContractError,
    NormalizedApprovalAction,
    is_stale_action,
    normalize_teams_action,
    validate_action_state_preflight,
)
from clearledgr.core.database import get_db
from clearledgr.core.launch_controls import get_channel_action_block_reason
from clearledgr.core.teams_verify import verify_teams_token
from clearledgr.services.invoice_workflow import get_invoice_workflow


router = APIRouter(prefix="/teams/invoices", tags=["teams-invoices"])
logger = logging.getLogger(__name__)


def _parse_payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _lookup_ap_item_id(organization_id: str, email_id: str, invoice_number: Optional[str] = None) -> Optional[str]:
    db = get_db()
    row = db.get_ap_item_by_thread(organization_id, email_id) if hasattr(db, "get_ap_item_by_thread") else None
    if row and row.get("id"):
        return str(row.get("id"))
    if invoice_number and hasattr(db, "get_ap_item_by_vendor_invoice"):
        # Vendor is optional here; teams callbacks often only carry email_id.
        return None
    return None


def _upsert_teams_metadata(
    organization_id: str,
    email_id: str,
    *,
    conversation_id: Optional[str],
    message_id: Optional[str],
    actor: str,
    action: str,
    status: str,
    reason: Optional[str] = None,
    activity_id: Optional[str] = None,
    service_url: Optional[str] = None,
) -> None:
    """Persist Teams channel thread state to the dedicated channel_threads table.

    Replaces the previous approach of writing Teams state into the AP item
    metadata JSON blob (Gap #11).  Uses ``upsert_channel_thread()`` for
    idempotent writes so repeated callbacks are safe.
    """
    db = get_db()
    row = db.get_ap_item_by_thread(organization_id, email_id) if hasattr(db, "get_ap_item_by_thread") else None
    if not row:
        return
    ap_item_id = str(row.get("id") or "")
    if not ap_item_id:
        return

    if hasattr(db, "upsert_channel_thread"):
        try:
            db.upsert_channel_thread(
                ap_item_id=ap_item_id,
                channel="teams",
                conversation_id=conversation_id or "",
                message_id=message_id,
                activity_id=activity_id,
                service_url=service_url,
                state=status,
                last_action=action,
                updated_by=actor,
                reason=reason,
                organization_id=organization_id,
            )
        except Exception as exc:
            logger.error("upsert_channel_thread failed: %s", exc)


def _audit_callback_event(
    db,
    *,
    event_type: str,
    organization_id: str,
    ap_item_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    correlation_id: Optional[str] = None,
    reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    resolved_ap_item_id = ap_item_id or f"channel_callback:teams:{organization_id}"
    try:
        db.append_ap_audit_event(
            {
                "ap_item_id": resolved_ap_item_id,
                "event_type": event_type,
                "actor_type": "user" if actor_id else "system",
                "actor_id": actor_id or "teams_callback",
                "source": "teams",
                "idempotency_key": idempotency_key,
                "correlation_id": correlation_id,
                "reason": reason,
                "metadata": metadata or {},
                "organization_id": organization_id,
            }
        )
    except Exception as exc:  # pragma: no cover - best effort
        logger.error("Could not audit teams callback event: %s", exc)


def _resolve_ap_context(db, organization_id: str, email_id: str) -> tuple[str, Optional[str]]:
    org_id = str(organization_id or "default")
    row = None
    if email_id and hasattr(db, "get_invoice_status"):
        row = db.get_invoice_status(email_id)
    if row and row.get("organization_id"):
        org_id = str(row.get("organization_id"))
    ap_item_id = None
    if email_id and hasattr(db, "get_ap_item_by_thread"):
        try:
            ap_row = db.get_ap_item_by_thread(org_id, email_id)
            if ap_row and ap_row.get("id"):
                ap_item_id = str(ap_row["id"])
        except Exception:
            ap_item_id = None
    return org_id, ap_item_id


def _resolve_correlation_id(db, ap_item_id: Optional[str], email_id: str) -> Optional[str]:
    try:
        row = None
        if ap_item_id and hasattr(db, "get_ap_item"):
            row = db.get_ap_item(ap_item_id)
        if row is None and email_id and hasattr(db, "get_invoice_status"):
            row = db.get_invoice_status(email_id)
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


async def _dispatch_teams_action(workflow, action: NormalizedApprovalAction) -> Dict[str, Any]:
    kwargs = {
        "source_channel": "teams",
        "source_channel_id": action.source_channel_id,
        "source_message_ref": action.source_message_ref,
        "actor_display": action.actor_display,
        "action_run_id": action.run_id,
        "decision_request_ts": action.request_ts,
        "decision_idempotency_key": action.idempotency_key,
        "correlation_id": action.correlation_id,
    }
    if action.action == "approve":
        return await workflow.approve_invoice(
            gmail_id=action.gmail_id,
            approved_by=action.actor_id,
            allow_budget_override=bool(action.action_variant == "budget_override"),
            override_justification=action.reason if action.action_variant == "budget_override" else None,
            **kwargs,
        )
    if action.action == "request_info":
        return await workflow.request_budget_adjustment(
            gmail_id=action.gmail_id,
            requested_by=action.actor_id,
            reason=action.reason or "request_info_in_teams",
            **kwargs,
        )
    if action.action == "reject":
        return await workflow.reject_invoice(
            gmail_id=action.gmail_id,
            reason=action.reason or "rejected_in_teams",
            rejected_by=action.actor_id,
            **kwargs,
        )
    raise HTTPException(status_code=400, detail="unsupported_action")


@router.post("/interactive")
async def handle_teams_interactive(request: Request) -> Dict[str, Any]:
    """Handle Teams approval/budget actions for AP invoices."""
    db = get_db()
    auth_header = request.headers.get("Authorization", "")
    try:
        claims = verify_teams_token(auth_header)
    except HTTPException as exc:
        raw_body = await request.body()
        body_hash = hashlib.sha256(raw_body or b"").hexdigest()[:16]
        _audit_callback_event(
            db,
            event_type="channel_callback_unauthorized",
            organization_id="default",
            idempotency_key=f"teams:unauthorized:{body_hash}",
            reason=str(exc.detail),
            metadata={"status_code": exc.status_code},
        )
        raise

    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8") if raw_body else ""
    except UnicodeDecodeError:
        body_text = ""
    payload = _parse_payload(body_text)
    if not payload:
        body_hash = hashlib.sha256(raw_body or b"").hexdigest()[:16]
        _audit_callback_event(
            db,
            event_type="channel_action_invalid",
            organization_id="default",
            idempotency_key=f"teams:invalid:{body_hash}",
            reason="invalid_payload",
            metadata={"status_code": 400},
        )
        raise HTTPException(status_code=400, detail="invalid_payload")
    email_candidate = str(payload.get("email_id") or payload.get("gmail_id") or "").strip()
    organization_id, ap_item_id = _resolve_ap_context(
        db,
        str(payload.get("organization_id") or "default"),
        email_candidate,
    )
    try:
        normalized = normalize_teams_action(payload, claims=claims, organization_id=organization_id)
    except ApprovalActionContractError as exc:
        _audit_callback_event(
            db,
            event_type="channel_action_invalid",
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            reason=exc.code,
            metadata={"message": exc.message, "email_id": email_candidate or None},
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.code)

    normalized.ap_item_id = ap_item_id
    normalized.correlation_id = _resolve_correlation_id(db, ap_item_id, normalized.gmail_id)

    blocked_reason = get_channel_action_block_reason(
        normalized.organization_id,
        "teams",
        db=db,
    )
    if blocked_reason:
        _audit_callback_event(
            db,
            event_type="channel_action_blocked",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:blocked",
            reason=blocked_reason,
            metadata={"action": normalized.to_dict()},
            correlation_id=normalized.correlation_id,
        )
        _upsert_teams_metadata(
            normalized.organization_id,
            normalized.gmail_id,
            conversation_id=normalized.source_channel_id,
            message_id=normalized.source_message_ref,
            actor=normalized.actor_display,
            action=normalized.raw_action or normalized.action,
            status="blocked",
            reason=blocked_reason,
        )
        return {
            "status": "blocked",
            "action": normalized.action,
            "email_id": normalized.gmail_id,
            "reason": blocked_reason,
        }

    if is_stale_action(normalized):
        _audit_callback_event(
            db,
            event_type="channel_action_stale",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:stale",
            reason="stale_action",
            metadata={"action": normalized.to_dict()},
            correlation_id=normalized.correlation_id,
        )
        _upsert_teams_metadata(
            normalized.organization_id,
            normalized.gmail_id,
            conversation_id=normalized.source_channel_id,
            message_id=normalized.source_message_ref,
            actor=normalized.actor_display,
            action=normalized.raw_action or normalized.action,
            status="stale",
            reason="stale_action",
        )
        return {
            "status": "stale",
            "action": normalized.action,
            "email_id": normalized.gmail_id,
            "reason": "stale_action",
        }

    processed_key = f"{normalized.idempotency_key}:processed"
    if db.get_ap_audit_event_by_key(processed_key):
        _audit_callback_event(
            db,
            event_type="channel_action_duplicate",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:duplicate",
            reason="duplicate_callback",
            metadata={"action": normalized.to_dict()},
            correlation_id=normalized.correlation_id,
        )
        return {
            "status": "duplicate",
            "action": normalized.action,
            "email_id": normalized.gmail_id,
            "reason": "duplicate_callback",
        }

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
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:preflight_blocked",
            reason=preflight_block,
            metadata={"action": normalized.to_dict()},
            correlation_id=normalized.correlation_id,
        )
        _upsert_teams_metadata(
            normalized.organization_id,
            normalized.gmail_id,
            conversation_id=normalized.source_channel_id,
            message_id=normalized.source_message_ref,
            actor=normalized.actor_display,
            action=normalized.raw_action or normalized.action,
            status="blocked",
            reason=preflight_block,
        )
        return {
            "status": "blocked",
            "action": normalized.action,
            "email_id": normalized.gmail_id,
            "reason": preflight_block,
        }

    _audit_callback_event(
        db,
        event_type="channel_action_received",
        organization_id=normalized.organization_id,
        ap_item_id=normalized.ap_item_id,
        actor_id=normalized.actor_id,
        idempotency_key=f"{normalized.idempotency_key}:received",
        metadata={"action": normalized.to_dict()},
        correlation_id=normalized.correlation_id,
    )

    workflow = get_invoice_workflow(normalized.organization_id)
    # H5: Wrap dispatch in try/except to emit channel_action_failed audit event
    # on any exception — parity with Slack handler (PLAN.md §5.3-5).
    try:
        result = await _dispatch_teams_action(workflow, normalized)
    except Exception as dispatch_exc:
        _audit_callback_event(
            db,
            event_type="channel_action_failed",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:failed",
            metadata={
                "action": normalized.to_dict(),
                "error": type(dispatch_exc).__name__,
            },
            correlation_id=normalized.correlation_id,
        )
        raise

    result_status = str(result.get("status") or "unknown")
    result_reason = str(result.get("reason") or "")

    _upsert_teams_metadata(
        normalized.organization_id,
        normalized.gmail_id,
        conversation_id=normalized.source_channel_id,
        message_id=normalized.source_message_ref,
        actor=normalized.actor_display,
        action=normalized.raw_action or normalized.action,
        status=result_status,
        reason=result_reason,
    )
    _audit_callback_event(
        db,
        event_type="channel_action_processed",
        organization_id=normalized.organization_id,
        ap_item_id=normalized.ap_item_id,
        actor_id=normalized.actor_id,
        idempotency_key=processed_key,
        metadata={
            "action": normalized.to_dict(),
            "result_status": result_status,
            "result_reason": result_reason,
        },
        correlation_id=normalized.correlation_id,
    )

    # Update the original Teams approval card with the decision result so
    # the approver sees immediate confirmation instead of a stale card.
    service_url = str(payload.get("serviceUrl") or payload.get("service_url") or "").strip()
    activity_id = str(payload.get("activityId") or payload.get("activity_id") or "").strip()
    if service_url and activity_id and normalized.source_channel_id:
        try:
            from clearledgr.services.teams_api import TeamsAPIClient
            teams_client = TeamsAPIClient()
            teams_client.update_activity(
                service_url=service_url,
                conversation_id=normalized.source_channel_id,
                activity_id=activity_id,
                result_status=result_status,
                actor_display=normalized.actor_display or normalized.actor_id or "unknown",
                action=normalized.action,
                reason=result_reason or None,
            )
        except Exception as _upd_exc:
            logger.error("Non-fatal: Teams card update failed: %s", _upd_exc)

    return {
        "status": result_status,
        "action": normalized.action,
        "email_id": normalized.gmail_id,
        "result": result,
    }
