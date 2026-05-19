"""Teams interactive handlers for AP invoice approvals.

§12 / §6.8 — Teams is not shipped in V1. All routes in this module
are gated behind ``FEATURE_TEAMS_ENABLED``; without it, every endpoint
returns 404 with a reason code pointing at the V1 boundary. Slack is
the V1 approval surface; Teams lights up post-launch. The handlers
stay intact because the adaptive-card contracts are worth preserving
— this is a deployment boundary, not a rewrite.
"""
from __future__ import annotations

import json
import hashlib
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from solden.core.ap_item_resolution import (
    resolve_ap_context as resolve_shared_ap_context,
    resolve_ap_correlation_id,
)
from solden.core.database import get_db
from solden.core.feature_flags import is_teams_enabled, teams_disabled_payload


def _require_teams_enabled() -> None:
    """Dependency applied to every Teams route — 404s the whole
    surface when the V1 flag is off. Runs before any handler body so
    no Teams interactive callback can be processed in a V1
    deployment.
    """
    if not is_teams_enabled():
        raise HTTPException(status_code=404, detail=teams_disabled_payload())


router = APIRouter(
    prefix="/teams/invoices",
    tags=["teams-invoices"],
    dependencies=[Depends(_require_teams_enabled)],
)
logger = logging.getLogger(__name__)


def _approval_action_error_type():
    from solden.core.approval_action_contract import ApprovalActionContractError

    return ApprovalActionContractError


def _normalize_teams_action(*args, **kwargs):
    from solden.core.approval_action_contract import normalize_teams_action

    return normalize_teams_action(*args, **kwargs)


def _resolve_action_precedence(*args, **kwargs):
    from solden.core.approval_action_contract import resolve_action_precedence

    return resolve_action_precedence(*args, **kwargs)


def _get_channel_action_block_reason(*args, **kwargs):
    from solden.core.launch_controls import get_channel_action_block_reason

    return get_channel_action_block_reason(*args, **kwargs)


def _verify_teams_token(auth_header: str):
    from solden.core.teams_verify import verify_teams_token

    return verify_teams_token(auth_header)


def _build_channel_runtime(*args, **kwargs):
    from solden.services.agent_command_dispatch import build_channel_runtime

    return build_channel_runtime(*args, **kwargs)


async def _dispatch_runtime_intent(*args, **kwargs):
    from solden.services.agent_command_dispatch import dispatch_runtime_intent

    return await dispatch_runtime_intent(*args, **kwargs)


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
        db.append_audit_event(
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


async def _dispatch_teams_action(action: Any) -> Dict[str, Any]:
    # M9 made action.organization_id load-bearing — it's set from the
    # AAD installation row at line 352, so any path that reaches this
    # dispatcher has a non-empty org. The pre-fix ``or "default"``
    # fallback was dead code armed: any future caller that constructed
    # an ``action`` without org would silently route to the legacy
    # "default" tenant. Fail closed instead.
    org = str(getattr(action, "organization_id", "") or "").strip()
    if not org:
        raise ValueError(
            "_dispatch_teams_action: action.organization_id is empty; "
            "every Teams action must be bound to an org via the "
            "AAD installation lookup before dispatch"
        )
    runtime = _build_channel_runtime(
        organization_id=org,
        actor_id=action.actor_id or "teams_user",
        actor_email=action.actor_display or action.actor_id or "teams_user",
        db=get_db(),
        fallback_actor="teams_user",
    )

    if action.action == "approve":
        return await _dispatch_runtime_intent(
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
        return await _dispatch_runtime_intent(
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
        return await _dispatch_runtime_intent(
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
    ApprovalActionContractError = _approval_action_error_type()
    try:
        claims = _verify_teams_token(auth_header)
    except HTTPException as exc:
        raw_body = await request.body()
        body_hash = hashlib.sha256(raw_body or b"").hexdigest()[:16]
        _audit_callback_event(
            db,
            event_type="channel_callback_unauthorized",
            organization_id="_unauthenticated",
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
            organization_id="_unauthenticated",
            idempotency_key=f"teams:invalid:{body_hash}",
            reason="invalid_payload",
            metadata={"status_code": 400},
        )
        raise HTTPException(status_code=400, detail="invalid_payload")
    email_candidate = str(payload.get("email_id") or payload.get("gmail_id") or "").strip()
    # Resolve the caller's Solden organization from the AAD ``tid`` claim
    # via the per-org installation table, BEFORE any AP-item lookup.
    # Pre-fix this either trusted the body ``organization_id`` (a
    # cross-tenant approval surface — anyone holding a valid bot token
    # could approve invoices in any tenant by setting the body field),
    # or relied on resolving org from the AP item itself (which still
    # let an AAD-tenant-A bot token act on AP items the AP-item-
    # resolution happened to surface for tenant B if email_id collided).
    #
    # M9 closes the loop: the AAD ``tid`` MUST map to an active
    # ``teams_installations`` row, and the resulting Solden org is
    # the only org this callback can act on. A token whose AAD tenant
    # has no installation refused entirely.
    aad_tid = str((claims or {}).get("tid") or "").strip()
    install = (
        db.get_teams_installation_by_aad_tenant(aad_tid)
        if aad_tid and hasattr(db, "get_teams_installation_by_aad_tenant")
        else None
    )
    organization_id_from_install = str((install or {}).get("organization_id") or "").strip()
    if not organization_id_from_install:
        body_hash = hashlib.sha256(raw_body or b"").hexdigest()[:16]
        _audit_callback_event(
            db,
            event_type="channel_callback_unauthorized",
            organization_id="_unauthenticated",
            idempotency_key=f"teams:no_installation:{body_hash}",
            reason="aad_tenant_not_provisioned",
            metadata={"aad_tid": aad_tid or None},
        )
        # 403 not 404: the bot token verified but the AAD tenant has
        # no Solden installation — that's an authorization failure
        # against the Solden multi-tenant boundary, not a missing
        # resource on the surface.
        raise HTTPException(status_code=403, detail="aad_tenant_not_provisioned")

    organization_id, ap_item_id = _resolve_ap_context(
        db,
        organization_id_from_install,
        email_candidate,
    )
    # ``_resolve_ap_context`` returns the org of the AP item it
    # resolved; if that diverges from the install-derived org, the
    # AP item belongs to a different tenant than the AAD caller —
    # refuse.
    if organization_id and organization_id != organization_id_from_install:
        body_hash = hashlib.sha256(raw_body or b"").hexdigest()[:16]
        _audit_callback_event(
            db,
            event_type="channel_callback_unauthorized",
            organization_id=organization_id_from_install,
            idempotency_key=f"teams:org_mismatch:{body_hash}",
            reason="ap_item_org_mismatch",
            metadata={
                "aad_tid": aad_tid or None,
                "install_org": organization_id_from_install,
                "ap_item_org": organization_id,
                "email_id": email_candidate or None,
            },
        )
        raise HTTPException(status_code=403, detail="ap_item_org_mismatch")
    organization_id = organization_id_from_install
    if not ap_item_id:
        body_hash = hashlib.sha256(raw_body or b"").hexdigest()[:16]
        _audit_callback_event(
            db,
            event_type="channel_action_invalid",
            organization_id=organization_id,
            idempotency_key=f"teams:no_ap_item:{body_hash}",
            reason="no_ap_item_resolution",
            metadata={
                "email_id": email_candidate or None,
                "aad_tid": aad_tid or None,
            },
        )
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    try:
        normalized = _normalize_teams_action(payload, claims=claims, organization_id=organization_id)
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

    blocked_reason = _get_channel_action_block_reason(
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
        except Exception as exc:
            logger.debug("AP item pre-fetch failed: %s", exc)

    # Teams actor_id is often the email — set actor_email for authorization
    _teams_email = str(
        payload.get("user_email") or payload.get("actor") or normalized.actor_id or ""
    ).strip()
    if "@" in _teams_email:
        normalized.actor_email = _teams_email

    # Load pending step approvers from approval chain
    pending_step_approvers = None
    try:
        from solden.api.slack_invoices import _get_pending_step_approvers
        pending_step_approvers = _get_pending_step_approvers(db, normalized.gmail_id, normalized.organization_id)
    except Exception as exc:
        logger.debug("Pending step approvers lookup failed: %s", exc)

    precedence = _resolve_action_precedence(
        normalized,
        ap_item_row,
        already_processed=bool(db.get_ap_audit_event_by_key(processed_key)),
        pending_step_approvers=pending_step_approvers,
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
            from solden.services.teams_api import TeamsAPIClient
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
            logger.warning("Teams card update failed for ap_item=%s, enqueueing for retry: %s", normalized.ap_item_id, _upd_exc)
            try:
                _db = get_db()
                _db.enqueue_notification(
                    # By the time we reach this enqueue, ``normalized.organization_id``
                    # was set from the AAD installation lookup (M9). The pre-fix
                    # ``or "default"`` was dead code that would silently bind a
                    # retry notification to the legacy "default" tenant if the
                    # field ever went missing.
                    organization_id=normalized.organization_id,
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
                    ap_item_id=normalized.ap_item_id,
                )
            except Exception as _enq_exc:
                logger.error(
                    "CRITICAL: Teams card update AND enqueue both failed for ap_item=%s org=%s: %s",
                    normalized.ap_item_id, normalized.organization_id, _enq_exc,
                )

    return {
        "status": result_status,
        "action": normalized.action,
        "email_id": normalized.gmail_id,
        "result": result,
    }
