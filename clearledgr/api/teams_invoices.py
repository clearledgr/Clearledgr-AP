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
    normalize_teams_action,
    resolve_action_precedence,
)
from clearledgr.core.ap_item_resolution import (
    resolve_ap_context as resolve_shared_ap_context,
    resolve_ap_correlation_id,
)
from clearledgr.core.database import get_db
from clearledgr.core.launch_controls import get_channel_action_block_reason
from clearledgr.core.teams_verify import verify_teams_token
from clearledgr.services.agent_command_dispatch import (
    build_channel_runtime,
    dispatch_runtime_intent,
)


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
    _, row = resolve_shared_ap_context(db, organization_id, email_id)
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
    org_id, ap_item = resolve_shared_ap_context(db, organization_id, email_id)
    ap_item_id = str((ap_item or {}).get("id") or "").strip() or None
    return org_id, ap_item_id


def _resolve_correlation_id(db, organization_id: str, ap_item_id: Optional[str], email_id: str) -> Optional[str]:
    return resolve_ap_correlation_id(
        db,
        organization_id,
        ap_item_id=ap_item_id,
        reference_id=email_id,
    )


async def _dispatch_teams_action(action: NormalizedApprovalAction) -> Dict[str, Any]:
    runtime = build_channel_runtime(
        organization_id=action.organization_id or "default",
        actor_id=action.actor_id or "teams_user",
        actor_email=action.actor_display or action.actor_id or "teams_user",
        db=get_db(),
        fallback_actor="teams_user",
    )

    if action.action == "approve":
        return await dispatch_runtime_intent(
            runtime,
            "approve_invoice",
            {
                "ap_item_id": action.ap_item_id,
                "email_id": action.gmail_id,
                "reason": action.reason,
                "source_channel": "teams",
                "source_channel_id": action.source_channel_id,
                "source_message_ref": action.source_message_ref,
                "actor_id": action.actor_id,
                "actor_display": action.actor_display,
                "action_run_id": action.run_id,
                "decision_request_ts": action.request_ts,
                "correlation_id": action.correlation_id,
                "action_variant": action.action_variant,
            },
            idempotency_key=action.idempotency_key,
        )
    if action.action == "request_info":
        return await dispatch_runtime_intent(
            runtime,
            "request_info",
            {
                "ap_item_id": action.ap_item_id,
                "email_id": action.gmail_id,
                "reason": action.reason or "budget_adjustment_requested_in_teams",
                "source_channel": "teams",
                "source_channel_id": action.source_channel_id,
                "source_message_ref": action.source_message_ref,
                "actor_id": action.actor_id,
                "actor_display": action.actor_display,
                "action_run_id": action.run_id,
                "decision_request_ts": action.request_ts,
                "correlation_id": action.correlation_id,
                "action_variant": action.action_variant,
            },
            idempotency_key=action.idempotency_key,
        )
    if action.action == "reject":
        return await dispatch_runtime_intent(
            runtime,
            "reject_invoice",
            {
                "ap_item_id": action.ap_item_id,
                "email_id": action.gmail_id,
                "reason": action.reason or "rejected_in_teams",
                "source_channel": "teams",
                "source_channel_id": action.source_channel_id,
                "source_message_ref": action.source_message_ref,
                "actor_id": action.actor_id,
                "actor_display": action.actor_display,
                "action_run_id": action.run_id,
                "decision_request_ts": action.request_ts,
                "correlation_id": action.correlation_id,
                "action_variant": action.action_variant,
            },
            idempotency_key=action.idempotency_key,
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

    if not ap_item_id and normalized.gmail_id:
        organization_id, ap_item_id = _resolve_ap_context(db, organization_id, normalized.gmail_id)
    normalized.organization_id = organization_id
    normalized.ap_item_id = ap_item_id
    normalized.correlation_id = _resolve_correlation_id(
        db,
        organization_id,
        ap_item_id,
        normalized.gmail_id,
    )

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

    processed_key = f"{normalized.idempotency_key}:processed"
    ap_item_row = None
    if normalized.ap_item_id and hasattr(db, "get_ap_item"):
        try:
            ap_item_row = db.get_ap_item(normalized.ap_item_id)
        except Exception:
            pass

    precedence = resolve_action_precedence(
        normalized,
        ap_item_row,
        already_processed=bool(db.get_ap_audit_event_by_key(processed_key)),
    )
    if precedence.status == "duplicate":
        _audit_callback_event(
            db,
            event_type="channel_action_duplicate",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:duplicate",
            reason=precedence.reason,
            metadata={"action": normalized.to_dict()},
            correlation_id=normalized.correlation_id,
        )
        return {
            "status": "duplicate",
            "action": normalized.action,
            "email_id": normalized.gmail_id,
            "reason": precedence.reason,
        }

    if precedence.status == "stale":
        _audit_callback_event(
            db,
            event_type="channel_action_stale",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:stale",
            reason=precedence.reason,
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
            reason=precedence.reason,
        )
        return {
            "status": "stale",
            "action": normalized.action,
            "email_id": normalized.gmail_id,
            "reason": precedence.reason,
        }

    if precedence.status == "blocked":
        _audit_callback_event(
            db,
            event_type="channel_action_blocked",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:preflight_blocked",
            reason=precedence.reason,
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
            reason=precedence.reason,
        )
        return {
            "status": "blocked",
            "action": normalized.action,
            "email_id": normalized.gmail_id,
            "reason": precedence.reason,
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

    # H5: Wrap dispatch in try/except to emit channel_action_failed audit event
    # on any exception — parity with Slack handler (PLAN.md §5.3-5).
    try:
        result = await _dispatch_teams_action(normalized)
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
            logger.warning("Teams card update failed, enqueueing for retry: %s", _upd_exc)
            try:
                _db = get_db()
                _db.enqueue_notification(
                    organization_id="system",
                    channel="teams_card_update",
                    payload={
                        "service_url": service_url,
                        "conversation_id": normalized.source_channel_id,
                        "activity_id": activity_id,
                        "result_status": result_status,
                        "actor_display": normalized.actor_display or normalized.actor_id or "unknown",
                        "action": normalized.action,
                        "reason": result_reason or None,
                    },
                )
            except Exception as _enq_exc:
                logger.error("Failed to enqueue Teams callback retry: %s", _enq_exc)

    return {
        "status": result_status,
        "action": normalized.action,
        "email_id": normalized.gmail_id,
        "result": result,
    }
