"""API-first ERP posting router with browser-agent fallback.

This module gives Clearledgr a concrete migration pattern:
- Try native ERP API connector first.
- Fall back to browser-agent macro dispatch when API posting fails or is unavailable.
- Emit audit telemetry so fallback rate can be tracked over time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import hashlib
import json

from clearledgr.core.database import ClearledgrDB, get_db
from clearledgr.core.launch_controls import (
    get_browser_fallback_block_reason,
    get_erp_posting_block_reason,
)
from clearledgr.integrations.erp_router import Bill, get_erp_connection, post_bill
from clearledgr.services.browser_agent import BrowserAgentService, get_browser_agent_service
from clearledgr.services.erp_connector_strategy import get_erp_connector_strategy


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(parts: Dict[str, Any]) -> str:
    payload = "|".join(str(parts.get(key) or "") for key in sorted(parts.keys()))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def _resolve_ap_item_id(
    db: ClearledgrDB,
    *,
    organization_id: str,
    ap_item_id: Optional[str] = None,
    email_id: Optional[str] = None,
    invoice_number: Optional[str] = None,
    vendor_name: Optional[str] = None,
) -> Optional[str]:
    if ap_item_id:
        item = db.get_ap_item(ap_item_id)
        if item:
            return str(item.get("id"))

    if email_id:
        by_message = db.get_ap_item_by_message_id(organization_id, email_id)
        if by_message:
            return str(by_message.get("id"))
        by_thread = db.get_ap_item_by_thread(organization_id, email_id)
        if by_thread:
            return str(by_thread.get("id"))

    if vendor_name and invoice_number:
        by_vendor_invoice = db.get_ap_item_by_vendor_invoice(organization_id, vendor_name, invoice_number)
        if by_vendor_invoice:
            return str(by_vendor_invoice.get("id"))

    return None


def _audit(
    db: ClearledgrDB,
    *,
    ap_item_id: Optional[str],
    organization_id: str,
    event_type: str,
    actor_id: str,
    reason: str,
    payload: Dict[str, Any],
    idempotency_key: str,
    correlation_id: Optional[str] = None,
) -> None:
    if not ap_item_id:
        return
    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": event_type,
            "from_state": None,
            "to_state": None,
            "actor_type": "system",
            "actor_id": actor_id,
            "reason": reason,
            "payload_json": payload,
            "organization_id": organization_id,
            "source": "erp_api_first",
            "correlation_id": correlation_id,
            "idempotency_key": idempotency_key,
        }
    )


def _parse_json_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _merge_session_metadata(
    db: ClearledgrDB,
    *,
    session_id: str,
    patch: Dict[str, Any],
) -> Dict[str, Any]:
    session = db.get_agent_session(session_id) or {}
    metadata = _parse_json_dict(session.get("metadata"))
    metadata.update(patch or {})
    db.update_agent_session(session_id, metadata=metadata)
    return metadata


def _normalize_completion_status(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value in {"success", "completed", "ok"}:
        return "success"
    if value in {"failed", "error", "denied", "cancelled", "canceled"}:
        return "failed"
    raise ValueError("invalid_completion_status")


def _derive_erp_reference(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    for key in (
        "erp_reference",
        "reference_id",
        "bill_id",
        "doc_num",
        "doc_number",
        "invoice_number",
        "tran_id",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _redact_raw_response(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    safe: Dict[str, Any] = {}
    for key in (
        "status",
        "erp",
        "erp_type",
        "reason",
        "needs_reauth",
        "bill_id",
        "reference_id",
        "doc_num",
        "doc_number",
        "invoice_number",
        "tran_id",
        "idempotency_key",
    ):
        if key in raw:
            safe[key] = raw.get(key)
    if "details" in raw:
        safe["details_redacted"] = True
    return safe


def _normalize_error_code(payload: Dict[str, Any]) -> Optional[str]:
    status = str(payload.get("status") or "").strip().lower()
    if status in {"success", "already_posted"}:
        return None
    reason = str(payload.get("reason") or payload.get("error_message") or "").strip().lower()
    fallback = payload.get("fallback") if isinstance(payload.get("fallback"), dict) else {}
    fallback_reason = str(fallback.get("reason") or "").strip().lower()

    if status == "blocked":
        return "posting_blocked"
    if status == "pending_browser_fallback":
        if "timeout" in reason:
            return "api_timeout"
        if payload.get("needs_reauth") or "token" in reason or "auth" in reason:
            return "auth_expired"
        return "fallback_queued_after_api_failure"
    if payload.get("needs_reauth") or "token expired" in reason or "authentication failed" in reason:
        return "auth_expired"
    if "no erp connected" in reason:
        return "erp_not_connected"
    if "not properly configured" in reason or "not configured" in reason:
        return "erp_not_configured"
    if "unknown erp type" in reason:
        return "erp_type_unsupported"
    if "timeout" in reason:
        return "api_timeout"
    if "fallback_disabled" in fallback_reason:
        return "fallback_disabled"
    if status == "skipped":
        return "erp_post_skipped"
    return "erp_post_failed"


def _finalize_erp_response_contract(
    response: Dict[str, Any],
    *,
    detected_erp_type: str,
    route_plan: Dict[str, Any],
    raw_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = dict(response or {})
    candidate = str(payload.get("erp_type") or payload.get("erp") or "").strip().lower()
    if not candidate or candidate in {"unknown", "unconfigured"}:
        route_candidate = str(route_plan.get("erp_type") or "").strip().lower()
        detected_candidate = str(detected_erp_type or "").strip().lower()
        if detected_candidate and detected_candidate not in {"unknown", "unconfigured"}:
            candidate = detected_candidate
        elif route_candidate:
            candidate = route_candidate
    erp_type = candidate or "unconfigured"
    payload["erp_type"] = erp_type
    payload["erp_reference"] = (
        payload.get("erp_reference")
        or _derive_erp_reference(payload)
        or _derive_erp_reference(raw_result)
    )
    payload["error_code"] = payload.get("error_code") or _normalize_error_code(payload)
    if payload.get("error_code") is None:
        payload["error_message"] = None
    else:
        payload["error_message"] = payload.get("error_message") or str(payload.get("reason") or "") or None
    payload["raw_response_redacted"] = payload.get("raw_response_redacted") or _redact_raw_response(raw_result or payload)
    return payload


def reconcile_browser_fallback_completion(
    *,
    session_id: str,
    macro_name: str,
    status: str,
    actor_id: str,
    erp_reference: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    error_code: Optional[str] = None,
    error_message_redacted: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    correlation_id: Optional[str] = None,
    db: Optional[ClearledgrDB] = None,
) -> Dict[str, Any]:
    """Finalize a browser fallback macro and reconcile the AP item state.

    Success path:
        failed_post -> ready_to_post -> posted_to_erp
        ready_to_post -> posted_to_erp

    Failure path:
        ready_to_post -> failed_post
        failed_post (remains failed_post)
    """
    from clearledgr.core.ap_states import normalize_state

    resolved_db = db or get_db()
    session = resolved_db.get_agent_session(session_id)
    if not session:
        raise ValueError("session_not_found")

    organization_id = str(session.get("organization_id") or "default")
    ap_item_id = str(session.get("ap_item_id") or "").strip()
    if not ap_item_id:
        raise ValueError("fallback_ap_item_not_found")

    session_metadata = _parse_json_dict(session.get("metadata"))
    correlation_id = correlation_id or (str(session_metadata.get("correlation_id") or "").strip() or None)
    workflow_id = str(session_metadata.get("workflow_id") or "").strip().lower()
    normalized_macro = str(macro_name or "").strip() or "post_invoice_to_erp"
    if workflow_id and workflow_id != "erp_posting_fallback":
        raise ValueError("not_fallback_session")
    if normalized_macro != "post_invoice_to_erp":
        raise ValueError("unsupported_fallback_macro")

    ap_item = resolved_db.get_ap_item(ap_item_id)
    if not ap_item:
        raise ValueError("fallback_ap_item_not_found")

    normalized_status = _normalize_completion_status(status)
    current_state = normalize_state(str(ap_item.get("state") or ""))
    now = _utcnow()
    completion_key = (
        idempotency_key
        or f"browser_fallback_completion:{session_id}:{normalized_macro}:{normalized_status}:{erp_reference or ''}"
    )

    existing_completion = resolved_db.get_ap_audit_event_by_key(completion_key)
    if existing_completion:
        latest_item = resolved_db.get_ap_item(ap_item_id) or ap_item
        latest_state = normalize_state(str(latest_item.get("state") or current_state))
        return {
            "status": normalized_status,
            "duplicate": True,
            "session_id": session_id,
            "ap_item_id": ap_item_id,
            "ap_item_state": latest_state,
            "erp_reference": latest_item.get("erp_reference") or erp_reference,
            "idempotency_key": completion_key,
        }

    if normalized_status == "success":
        if current_state == "failed_post":
            resolved_db.update_ap_item(
                ap_item_id,
                state="ready_to_post",
                _actor_type="system",
                _actor_id=actor_id,
            )
            current_state = normalize_state(str((resolved_db.get_ap_item(ap_item_id) or {}).get("state") or ""))
        if current_state == "approved":
            resolved_db.update_ap_item(
                ap_item_id,
                state="ready_to_post",
                _actor_type="system",
                _actor_id=actor_id,
            )
            current_state = normalize_state(str((resolved_db.get_ap_item(ap_item_id) or {}).get("state") or ""))
        if current_state == "ready_to_post":
            resolved_db.update_ap_item(
                ap_item_id,
                state="posted_to_erp",
                erp_reference=erp_reference,
                erp_posted_at=now,
                post_attempted_at=now,
                last_error=None,
                _actor_type="system",
                _actor_id=actor_id,
            )
            current_state = "posted_to_erp"
        elif current_state == "posted_to_erp":
            pass
        else:
            raise ValueError(f"invalid_state_for_fallback_success:{current_state or 'unknown'}")

        _merge_session_metadata(
            resolved_db,
            session_id=session_id,
            patch={
                "fallback_completion": {
                    "status": "success",
                    "macro_name": normalized_macro,
                    "erp_reference": erp_reference,
                    "completed_at": now,
                    "completed_by": actor_id,
                    "evidence": evidence or {},
                    "correlation_id": correlation_id,
                }
            },
        )
        resolved_db.update_agent_session(session_id, state="completed")
        _audit(
            resolved_db,
            ap_item_id=ap_item_id,
            organization_id=organization_id,
            event_type="erp_browser_fallback_completed",
            actor_id=actor_id,
            reason="fallback_completed",
            payload={
                "session_id": session_id,
                "macro_name": normalized_macro,
                "status": "success",
                "erp_reference": erp_reference,
                "evidence": evidence or {},
                "correlation_id": correlation_id,
            },
            idempotency_key=completion_key,
            correlation_id=correlation_id,
        )
        return {
            "status": "success",
            "duplicate": False,
            "session_id": session_id,
            "ap_item_id": ap_item_id,
            "ap_item_state": current_state,
            "erp_reference": erp_reference,
            "idempotency_key": completion_key,
        }

    # Failure path
    if current_state == "ready_to_post":
        resolved_db.update_ap_item(
            ap_item_id,
            state="failed_post",
            last_error=error_message_redacted or error_code or "browser_fallback_failed",
            post_attempted_at=now,
            _actor_type="system",
            _actor_id=actor_id,
        )
        current_state = "failed_post"
    elif current_state == "failed_post":
        resolved_db.update_ap_item(
            ap_item_id,
            last_error=error_message_redacted or error_code or "browser_fallback_failed",
            post_attempted_at=now,
            _actor_type="system",
            _actor_id=actor_id,
        )
    elif current_state == "posted_to_erp":
        raise ValueError("fallback_failure_after_posted")
    else:
        raise ValueError(f"invalid_state_for_fallback_failure:{current_state or 'unknown'}")

    _merge_session_metadata(
        resolved_db,
        session_id=session_id,
        patch={
            "fallback_completion": {
                "status": "failed",
                "macro_name": normalized_macro,
                "completed_at": now,
                "completed_by": actor_id,
                "error_code": error_code,
                "error_message_redacted": error_message_redacted,
                "evidence": evidence or {},
                "correlation_id": correlation_id,
            }
        },
    )
    resolved_db.update_agent_session(session_id, state="failed")
    _audit(
        resolved_db,
        ap_item_id=ap_item_id,
        organization_id=organization_id,
        event_type="erp_browser_fallback_failed",
        actor_id=actor_id,
        reason=error_code or "fallback_failed",
        payload={
            "session_id": session_id,
            "macro_name": normalized_macro,
            "status": "failed",
            "error_code": error_code,
            "error_message_redacted": error_message_redacted,
            "evidence": evidence or {},
            "correlation_id": correlation_id,
        },
        idempotency_key=completion_key,
        correlation_id=correlation_id,
    )
    return {
        "status": "failed",
        "duplicate": False,
        "session_id": session_id,
        "ap_item_id": ap_item_id,
        "ap_item_state": current_state,
        "erp_reference": None,
        "idempotency_key": completion_key,
    }


async def _dispatch_browser_fallback(
    *,
    db: ClearledgrDB,
    service: BrowserAgentService,
    organization_id: str,
    actor_id: str,
    ap_item_id: Optional[str],
    email_id: Optional[str],
    invoice_number: Optional[str],
    vendor_name: Optional[str],
    amount: Optional[float],
    currency: Optional[str],
    vendor_portal_url: Optional[str],
    erp_url: Optional[str],
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_item_id = _resolve_ap_item_id(
        db,
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        email_id=email_id,
        invoice_number=invoice_number,
        vendor_name=vendor_name,
    )
    if not resolved_item_id:
        return {
            "requested": False,
            "eligible": False,
            "reason": "ap_item_not_found_for_fallback",
        }

    try:
        macro_name = "post_invoice_to_erp"
        macro_params = {
            "invoice_number": invoice_number,
            "vendor_name": vendor_name,
            "amount": amount,
            "currency": currency,
            "vendor_portal_url": vendor_portal_url,
            "erp_url": erp_url,
            "email_id": email_id,
        }
        session = service.create_session(
            organization_id=organization_id,
            ap_item_id=resolved_item_id,
            created_by=actor_id,
            metadata={
                "workflow_id": "erp_posting_fallback",
                "actor_role": "ap_operator",
                "email_id": email_id,
                "invoice_number": invoice_number,
                "correlation_id": correlation_id,
            },
        )
        session_id = str(session.get("id"))
        preview = service.dispatch_macro(
            session_id=session_id,
            macro_name=macro_name,
            actor_id=actor_id,
            actor_role="ap_operator",
            workflow_id="erp_posting_fallback",
            correlation_id=correlation_id,
            params=macro_params,
            dry_run=True,
        )
        preview_commands = [
            entry for entry in (preview.get("commands") or []) if isinstance(entry, dict)
        ]
        requires_confirmation_count = 0
        preview_command_map: Dict[str, Dict[str, Any]] = {}
        for entry in preview_commands:
            decision = entry.get("decision") if isinstance(entry.get("decision"), dict) else {}
            command = entry.get("command") if isinstance(entry.get("command"), dict) else {}
            command_id = str(command.get("command_id") or "").strip()
            if command_id:
                preview_command_map[command_id] = command
            if bool(decision.get("requires_confirmation")):
                requires_confirmation_count += 1

        _audit(
            db,
            ap_item_id=resolved_item_id,
            organization_id=organization_id,
            event_type="erp_api_fallback_preview_created",
            actor_id=actor_id,
            reason="fallback_preview_created",
            payload={
                "session_id": session_id,
                "macro_name": macro_name,
                "preview_status": preview.get("status"),
                "command_count": len(preview_commands),
                "requires_confirmation_count": requires_confirmation_count,
                "correlation_id": correlation_id,
            },
            idempotency_key=f"erp_api_fallback_preview:{session_id}:{_stable_hash({'macro_name': macro_name, 'command_count': len(preview_commands)})}",
            correlation_id=correlation_id,
        )

        macro = service.dispatch_macro(
            session_id=session_id,
            macro_name=macro_name,
            actor_id=actor_id,
            actor_role="ap_operator",
            workflow_id="erp_posting_fallback",
            correlation_id=correlation_id,
            params=macro_params,
            dry_run=False,
        )
        dispatch_events = [entry for entry in (macro.get("events") or []) if isinstance(entry, dict)]
        status_by_command_id: Dict[str, str] = {}
        for event in dispatch_events:
            command_id = str(event.get("command_id") or "").strip()
            if command_id:
                status_by_command_id[command_id] = str(event.get("status") or "")

        confirmed_commands: List[str] = []
        for event in dispatch_events:
            if str(event.get("status") or "") != "blocked_for_approval":
                continue
            command_id = str(event.get("command_id") or "").strip()
            command_payload = preview_command_map.get(command_id)
            if not command_payload:
                continue
            try:
                if correlation_id and isinstance(command_payload, dict) and not command_payload.get("correlation_id"):
                    command_payload = dict(command_payload)
                    command_payload["correlation_id"] = correlation_id
                confirmed = service.enqueue_command(
                    session_id=session_id,
                    command=command_payload,
                    actor_id=actor_id,
                    confirm=True,
                    confirmed_by=actor_id,
                    actor_role="ap_operator",
                    workflow_id="erp_posting_fallback",
                )
                if isinstance(confirmed, dict):
                    status_by_command_id[command_id] = str(confirmed.get("status") or status_by_command_id.get(command_id) or "")
                    if str(confirmed.get("status") or "").lower() == "queued":
                        confirmed_commands.append(command_id)
            except Exception:
                # Keep fallback dispatch resilient; confirmation failures remain visible as blocked.
                continue

        if requires_confirmation_count or confirmed_commands:
            _audit(
                db,
                ap_item_id=resolved_item_id,
                organization_id=organization_id,
                event_type="erp_api_fallback_confirmation_captured",
                actor_id=actor_id,
                reason="fallback_preview_confirmed",
                payload={
                    "session_id": session_id,
                    "macro_name": macro_name,
                    "required_count": requires_confirmation_count,
                    "confirmed_count": len(confirmed_commands),
                    "confirmed_command_ids": confirmed_commands,
                    "correlation_id": correlation_id,
                },
                idempotency_key=f"erp_api_fallback_confirmation:{session_id}:{_stable_hash({'required': requires_confirmation_count, 'confirmed': len(confirmed_commands)})}",
                correlation_id=correlation_id,
            )

        final_statuses = list(status_by_command_id.values()) or [
            str(event.get("status") or "") for event in dispatch_events
        ]
        queued_count = len([status for status in final_statuses if status == "queued"])
        blocked_count = len([status for status in final_statuses if status == "blocked_for_approval"])
        denied_count = len([status for status in final_statuses if status == "denied_policy"])
        return {
            "requested": True,
            "eligible": True,
            "reason": "fallback_preview_confirmed_and_dispatched",
            "ap_item_id": resolved_item_id,
            "session_id": session.get("id"),
            "macro_name": macro_name,
            "dispatch_status": macro.get("status"),
            "queued": int(queued_count),
            "blocked": int(blocked_count),
            "denied": int(denied_count),
            "preview": {
                "status": preview.get("status"),
                "command_count": len(preview_commands),
                "requires_confirmation_count": requires_confirmation_count,
            },
            "confirmation": {
                "required_count": requires_confirmation_count,
                "confirmed_count": len(confirmed_commands),
                "pending_count": max(0, blocked_count),
                "confirmed_command_ids": confirmed_commands,
                "actor_id": actor_id,
            },
            "correlation_id": correlation_id,
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "requested": False,
            "eligible": True,
            "reason": f"fallback_dispatch_error:{exc}",
            "ap_item_id": resolved_item_id,
        }


async def post_bill_api_first(
    *,
    organization_id: str,
    bill: Bill,
    actor_id: str = "erp_router",
    ap_item_id: Optional[str] = None,
    email_id: Optional[str] = None,
    invoice_number: Optional[str] = None,
    vendor_name: Optional[str] = None,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
    vendor_portal_url: Optional[str] = None,
    erp_url: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    correlation_id: Optional[str] = None,
    db: Optional[ClearledgrDB] = None,
    browser_service: Optional[BrowserAgentService] = None,
) -> Dict[str, Any]:
    """Post a bill API-first and request browser fallback on failure.

    Returns ERP response fields plus:
    - execution_mode: "api" | "browser_fallback" | "api_failed"
    - fallback: structured fallback dispatch details
    """
    resolved_db = db or get_db()
    resolved_service = browser_service or get_browser_agent_service()
    strategy = get_erp_connector_strategy()

    connection = get_erp_connection(organization_id)
    connection_present = connection is not None
    detected_erp_type = str((connection.type if connection else "unconfigured") or "unconfigured").strip().lower()
    route_plan = strategy.build_route_plan(
        erp_type=detected_erp_type,
        connection_present=connection_present,
    )
    connector_capability = strategy.resolve(str(route_plan.get("erp_type") or detected_erp_type))

    resolved_ap_item_id = _resolve_ap_item_id(
        resolved_db,
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        email_id=email_id,
        invoice_number=invoice_number,
        vendor_name=vendor_name,
    )
    attempt_key_seed = {
        "organization_id": organization_id,
        "ap_item_id": resolved_ap_item_id,
        "email_id": email_id,
        "invoice_number": invoice_number,
        "vendor_name": vendor_name,
        "action_idempotency_key": idempotency_key,
        "timestamp_bucket": None if idempotency_key else _utcnow()[:16],  # minute granularity fallback
    }
    attempt_key = f"erp_api_attempt:{_stable_hash(attempt_key_seed)}"

    rollout_block_reason = get_erp_posting_block_reason(
        organization_id,
        erp_type=detected_erp_type,
        db=resolved_db,
    )
    if rollout_block_reason:
        _audit(
            resolved_db,
            ap_item_id=resolved_ap_item_id,
            organization_id=organization_id,
            event_type="erp_api_blocked",
            actor_id=actor_id,
            reason=rollout_block_reason,
            payload={
                "invoice_number": invoice_number,
                "vendor_name": vendor_name,
                "amount": amount,
                "currency": currency,
                "email_id": email_id,
                "route_plan": route_plan,
                "control": "rollback_controls",
            },
            idempotency_key=f"erp_api_blocked:{_stable_hash(attempt_key_seed)}",
            correlation_id=correlation_id,
        )
        response = {
            "status": "blocked",
            "erp": route_plan.get("erp_type"),
            "reason": rollout_block_reason,
            "idempotency_key": idempotency_key or attempt_key,
            "execution_mode": "blocked",
            "routing": route_plan,
            "fallback": {
                "requested": False,
                "eligible": False,
                "reason": "erp_posting_disabled_by_rollout_control",
                "ap_item_id": resolved_ap_item_id,
            },
        }
        return _finalize_erp_response_contract(
            response,
            detected_erp_type=detected_erp_type,
            route_plan=route_plan,
            raw_result=response,
        )

    _audit(
        resolved_db,
        ap_item_id=resolved_ap_item_id,
        organization_id=organization_id,
        event_type="erp_api_attempt",
        actor_id=actor_id,
        reason="api_first_attempt",
        payload={
            "invoice_number": invoice_number,
            "vendor_name": vendor_name,
            "amount": amount,
            "currency": currency,
            "email_id": email_id,
            "route_plan": route_plan,
        },
        idempotency_key=attempt_key,
        correlation_id=correlation_id,
    )

    if connector_capability.supports_api_post_bill and connection_present:
        api_result = await post_bill(
            organization_id,
            bill,
            ap_item_id=resolved_ap_item_id,
            idempotency_key=idempotency_key or attempt_key,
        )
    else:
        api_result = {
            "status": "skipped",
            "erp": route_plan.get("erp_type"),
            "reason": "api_not_available_for_connector",
            "route_plan": route_plan,
            "idempotency_key": idempotency_key or attempt_key,
        }

    api_status = str(api_result.get("status") or "")
    api_success = api_status in {"success", "already_posted"}

    if api_success:
        _audit(
            resolved_db,
            ap_item_id=resolved_ap_item_id,
            organization_id=organization_id,
            event_type="erp_api_success",
            actor_id=actor_id,
            reason="api_posted" if api_status == "success" else "api_already_posted",
            payload={
                "api_status": api_status,
                "bill_id": api_result.get("bill_id"),
                "reference_id": api_result.get("reference_id"),
                "doc_num": api_result.get("doc_num"),
                "erp": api_result.get("erp"),
                "route_plan": route_plan,
            },
            idempotency_key=f"erp_api_success:{_stable_hash({**attempt_key_seed, 'bill_id': api_result.get('bill_id')})}",
            correlation_id=correlation_id,
        )
        response = {
            **api_result,
            "idempotency_key": api_result.get("idempotency_key") or idempotency_key or attempt_key,
            "execution_mode": "api",
            "routing": route_plan,
            "fallback": {
                "requested": False,
                "eligible": False,
                "reason": "not_needed",
                "ap_item_id": resolved_ap_item_id,
            },
        }
        return _finalize_erp_response_contract(
            response,
            detected_erp_type=detected_erp_type,
            route_plan=route_plan,
            raw_result=api_result,
        )

    if not connector_capability.browser_fallback_enabled:
        fallback_disabled = {
            "requested": False,
            "eligible": False,
            "reason": "fallback_disabled_for_connector",
            "ap_item_id": resolved_ap_item_id,
        }
        _audit(
            resolved_db,
            ap_item_id=resolved_ap_item_id,
            organization_id=organization_id,
            event_type="erp_api_failed",
            actor_id=actor_id,
            reason="fallback_disabled_for_connector",
            payload={
                "api_status": api_status,
                "api_reason": api_result.get("reason"),
                "fallback": fallback_disabled,
                "route_plan": route_plan,
            },
            idempotency_key=f"erp_api_failed:{_stable_hash(attempt_key_seed)}",
            correlation_id=correlation_id,
        )
        response = {
            **api_result,
            "idempotency_key": api_result.get("idempotency_key") or idempotency_key or attempt_key,
            "execution_mode": "api_failed",
            "routing": route_plan,
            "fallback": fallback_disabled,
        }
        return _finalize_erp_response_contract(
            response,
            detected_erp_type=detected_erp_type,
            route_plan=route_plan,
            raw_result=api_result,
        )

    fallback_rollout_block_reason = get_browser_fallback_block_reason(
        organization_id,
        db=resolved_db,
    )
    if fallback_rollout_block_reason:
        fallback_disabled = {
            "requested": False,
            "eligible": False,
            "reason": "fallback_disabled_by_rollout_control",
            "control_reason": fallback_rollout_block_reason,
            "ap_item_id": resolved_ap_item_id,
        }
        _audit(
            resolved_db,
            ap_item_id=resolved_ap_item_id,
            organization_id=organization_id,
            event_type="erp_api_failed",
            actor_id=actor_id,
            reason=fallback_rollout_block_reason,
            payload={
                "api_status": api_status,
                "api_reason": api_result.get("reason"),
                "fallback": fallback_disabled,
                "route_plan": route_plan,
            },
            idempotency_key=f"erp_api_failed:{_stable_hash(attempt_key_seed)}",
            correlation_id=correlation_id,
        )
        response = {
            **api_result,
            "idempotency_key": api_result.get("idempotency_key") or idempotency_key or attempt_key,
            "execution_mode": "api_failed",
            "routing": route_plan,
            "fallback": fallback_disabled,
        }
        return _finalize_erp_response_contract(
            response,
            detected_erp_type=detected_erp_type,
            route_plan=route_plan,
            raw_result=api_result,
        )

    fallback = await _dispatch_browser_fallback(
        db=resolved_db,
        service=resolved_service,
        organization_id=organization_id,
        actor_id=actor_id,
        ap_item_id=resolved_ap_item_id,
        email_id=email_id,
        invoice_number=invoice_number,
        vendor_name=vendor_name,
        amount=amount,
        currency=currency,
        vendor_portal_url=vendor_portal_url,
        erp_url=erp_url,
        correlation_id=correlation_id,
    )

    if fallback.get("requested"):
        _audit(
            resolved_db,
            ap_item_id=str(fallback.get("ap_item_id") or resolved_ap_item_id or ""),
            organization_id=organization_id,
            event_type="erp_api_fallback_requested",
            actor_id=actor_id,
            reason=fallback.get("reason") or "fallback_requested",
            payload={
                "api_status": api_status,
                "api_reason": api_result.get("reason"),
                "fallback": fallback,
                "route_plan": route_plan,
            },
            idempotency_key=f"erp_api_fallback_requested:{_stable_hash({**attempt_key_seed, 'session_id': fallback.get('session_id')})}",
            correlation_id=correlation_id,
        )
        response = {
            **api_result,
            "status": "pending_browser_fallback",
            "idempotency_key": api_result.get("idempotency_key") or idempotency_key or attempt_key,
            "execution_mode": "browser_fallback",
            "routing": route_plan,
            "fallback": fallback,
        }
        return _finalize_erp_response_contract(
            response,
            detected_erp_type=detected_erp_type,
            route_plan=route_plan,
            raw_result=api_result,
        )

    _audit(
        resolved_db,
        ap_item_id=resolved_ap_item_id,
        organization_id=organization_id,
        event_type="erp_api_failed",
        actor_id=actor_id,
        reason=fallback.get("reason") or "api_failed_no_fallback",
        payload={
            "api_status": api_status,
            "api_reason": api_result.get("reason"),
            "fallback": fallback,
            "route_plan": route_plan,
        },
        idempotency_key=f"erp_api_failed:{_stable_hash(attempt_key_seed)}",
        correlation_id=correlation_id,
    )
    response = {
        **api_result,
        "idempotency_key": api_result.get("idempotency_key") or idempotency_key or attempt_key,
        "execution_mode": "api_failed",
        "routing": route_plan,
        "fallback": fallback,
    }
    return _finalize_erp_response_contract(
        response,
        detected_erp_type=detected_erp_type,
        route_plan=route_plan,
        raw_result=api_result,
    )
