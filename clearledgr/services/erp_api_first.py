"""API-first ERP posting router.

This module implements the ERP posting pipeline:
- Try native ERP API connector first.
- When API posting fails, return api_failed status (no browser fallback).
- Emit audit telemetry so failure rate can be tracked over time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import hashlib

logger = logging.getLogger(__name__)

from clearledgr.core.database import ClearledgrDB, get_db
from clearledgr.core.launch_controls import (
    get_erp_posting_block_reason,
)
from clearledgr.integrations.erp_router import (
    Bill,
    CreditApplication,
    SettlementApplication,
    apply_credit_note,
    apply_settlement,
    get_erp_connection,
    post_bill,
)
from clearledgr.services.erp.contracts import (
    get_erp_bill_adapter,
    get_erp_finance_action_adapter,
)
from clearledgr.services.ap_agent_sync import sync_ap_execution_event
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
    audit_row = db.append_ap_audit_event(
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
    sync_ap_execution_event(
        db=db,
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        event_type=event_type,
        reason=reason,
        response={
            **dict(payload or {}),
            "audit_event_id": (audit_row or {}).get("id"),
        },
        metadata=dict(payload or {}),
        actor_id=actor_id,
        correlation_id=correlation_id,
        skill_id="ap_v1",
        source="erp_api_first",
    )


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


def _normalize_follow_on_error_code(payload: Dict[str, Any], *, action_key: str) -> Optional[str]:
    status = str(payload.get("status") or "").strip().lower()
    if status in {"success", "already_applied", "completed"}:
        return None
    reason = str(payload.get("reason") or payload.get("error_message") or "").strip().lower()
    fallback = payload.get("fallback") if isinstance(payload.get("fallback"), dict) else {}
    fallback_reason = str(fallback.get("reason") or "").strip().lower()

    if status == "blocked":
        return f"{action_key}_blocked"
    if "target_not_posted_to_erp" in reason:
        return f"{action_key}_target_not_posted"
    if "not_available_for_connector" in reason:
        return f"{action_key}_api_unavailable"
    if "no erp connected" in reason:
        return "erp_not_connected"
    if "not configured" in reason:
        return "erp_not_configured"
    if "timeout" in reason:
        return "api_timeout"
    if "fallback_disabled" in fallback_reason:
        return "fallback_disabled"
    if status == "skipped":
        return f"{action_key}_skipped"
    return f"{action_key}_failed"


def _finalize_follow_on_response_contract(
    response: Dict[str, Any],
    *,
    detected_erp_type: str,
    route_plan: Dict[str, Any],
    action_key: str,
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
    payload["erp_type"] = candidate or "unconfigured"
    payload["erp_reference"] = (
        payload.get("erp_reference")
        or payload.get("reference_id")
        or payload.get("target_erp_reference")
        or _derive_erp_reference(raw_result)
    )
    payload["error_code"] = payload.get("error_code") or _normalize_follow_on_error_code(
        payload,
        action_key=action_key,
    )
    payload["error_message"] = (
        None
        if payload.get("error_code") is None
        else payload.get("error_message") or str(payload.get("reason") or "") or None
    )
    payload["raw_response_redacted"] = payload.get("raw_response_redacted") or _redact_raw_response(raw_result or payload)
    return payload


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


async def post_bill_api_first(
    *,
    organization_id: str,
    bill: Bill,
    actor_id: str = "erp_router",
    ap_item_id: Optional[str] = None,
    entity_id: Optional[str] = None,
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
) -> Dict[str, Any]:
    """Post a bill via the ERP API.

    Returns ERP response fields plus:
    - execution_mode: "api" | "api_failed"

    §3 Multi-entity: entity_id resolves entity-specific ERP connection
    with org-level fallback.
    """
    resolved_db = db or get_db()
    strategy = get_erp_connector_strategy()

    connection = get_erp_connection(organization_id, entity_id=entity_id)
    connection_present = connection is not None
    detected_erp_type = str((connection.type if connection else "unconfigured") or "unconfigured").strip().lower()
    route_plan = strategy.build_route_plan(
        erp_type=detected_erp_type,
        connection_present=connection_present,
    )
    connector_capability = strategy.resolve(str(route_plan.get("erp_type") or detected_erp_type))
    adapter = get_erp_bill_adapter(
        erp_type=str(route_plan.get("erp_type") or detected_erp_type),
        post_handler=post_bill,
    )

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
        validation = adapter.validate(
            {
                "invoice_number": invoice_number or bill.invoice_number,
                "vendor_name": vendor_name or bill.vendor_name,
                "amount": amount if amount is not None else bill.amount,
                "currency": currency or bill.currency,
                "ap_item_id": resolved_ap_item_id,
            }
        )
        if not validation.get("ok"):
            api_result = {
                "status": "error",
                "erp": route_plan.get("erp_type"),
                "reason": str(validation.get("reason") or "validation_failed"),
                "validation": validation,
                "idempotency_key": idempotency_key or attempt_key,
            }
        else:
            api_result = await adapter.post(
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

    # API failed — no browser fallback, return failure directly
    _audit(
        resolved_db,
        ap_item_id=resolved_ap_item_id,
        organization_id=organization_id,
        event_type="erp_api_failed",
        actor_id=actor_id,
        reason=str(api_result.get("reason") or "api_failed"),
        payload={
            "api_status": api_status,
            "api_reason": api_result.get("reason"),
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
        "fallback": {
            "requested": False,
            "eligible": False,
            "reason": "browser_fallback_removed",
            "ap_item_id": resolved_ap_item_id,
        },
    }
    return _finalize_erp_response_contract(
        response,
        detected_erp_type=detected_erp_type,
        route_plan=route_plan,
        raw_result=api_result,
    )


async def _erp_follow_on_api_first(
    *,
    action_key: str,
    organization_id: str,
    actor_id: str,
    target_ap_item_id: str,
    source_ap_item_id: str,
    target_erp_reference: str,
    target_invoice_number: Optional[str],
    amount: float,
    currency: str,
    source_reference: Optional[str],
    source_document_type: Optional[str],
    note: Optional[str],
    email_id: Optional[str],
    correlation_id: Optional[str],
    db: Optional[ClearledgrDB] = None,
) -> Dict[str, Any]:
    resolved_db = db or get_db()
    strategy = get_erp_connector_strategy()

    connection = get_erp_connection(organization_id)
    connection_present = connection is not None
    detected_erp_type = str((connection.type if connection else "unconfigured") or "unconfigured").strip().lower()
    route_action = "apply_credit" if action_key == "apply_credit_note" else "apply_settlement"
    route_plan = strategy.build_route_plan(
        erp_type=detected_erp_type,
        connection_present=connection_present,
        action=route_action,
    )
    connector_capability = strategy.resolve(str(route_plan.get("erp_type") or detected_erp_type))
    adapter = get_erp_finance_action_adapter(
        erp_type=str(route_plan.get("erp_type") or detected_erp_type),
        credit_handler=apply_credit_note,
        settlement_handler=apply_settlement,
    )

    target_erp_reference = str(target_erp_reference or "").strip()
    amount = round(float(amount or 0.0), 2)
    attempt_key_seed = {
        "organization_id": organization_id,
        "target_ap_item_id": target_ap_item_id,
        "source_ap_item_id": source_ap_item_id,
        "target_erp_reference": target_erp_reference,
        "source_reference": source_reference,
        "source_document_type": source_document_type,
        "action": action_key,
        "timestamp_bucket": _utcnow()[:16],
    }
    attempt_key = f"{action_key}_attempt:{_stable_hash(attempt_key_seed)}"
    event_prefix = "erp_credit_application" if action_key == "apply_credit_note" else "erp_settlement_application"

    _audit(
        resolved_db,
        ap_item_id=target_ap_item_id,
        organization_id=organization_id,
        event_type=f"{event_prefix}_attempt",
        actor_id=actor_id,
        reason="api_first_attempt",
        payload={
            "source_ap_item_id": source_ap_item_id,
            "target_erp_reference": target_erp_reference,
            "target_invoice_number": target_invoice_number,
            "source_reference": source_reference,
            "source_document_type": source_document_type,
            "amount": amount,
            "currency": currency,
            "email_id": email_id,
            "route_plan": route_plan,
        },
        idempotency_key=attempt_key,
        correlation_id=correlation_id,
    )

    if action_key == "apply_credit_note":
        validation = adapter.validate_credit(
            {
                "target_erp_reference": target_erp_reference,
                "amount": amount,
                "currency": currency,
            }
        )
        if connector_capability.supports_api_apply_credit and connection_present and validation.get("ok"):
            api_result = await adapter.apply_credit(
                organization_id,
                CreditApplication(
                    target_erp_reference=target_erp_reference,
                    amount=amount,
                    currency=currency,
                    credit_note_number=source_reference,
                    target_invoice_number=target_invoice_number,
                    note=note,
                    source_ap_item_id=source_ap_item_id,
                    related_ap_item_id=target_ap_item_id,
                ),
                ap_item_id=target_ap_item_id,
                idempotency_key=attempt_key,
            )
        elif not validation.get("ok"):
            api_result = {
                "status": "error",
                "reason": str(validation.get("reason") or "validation_failed"),
                "validation": validation,
                "idempotency_key": attempt_key,
                "target_erp_reference": target_erp_reference,
            }
        else:
            api_result = {
                "status": "skipped",
                "reason": "api_not_available_for_connector",
                "idempotency_key": attempt_key,
                "target_erp_reference": target_erp_reference,
            }
    else:
        validation = adapter.validate_settlement(
            {
                "target_erp_reference": target_erp_reference,
                "amount": amount,
                "currency": currency,
            }
        )
        if connector_capability.supports_api_apply_settlement and connection_present and validation.get("ok"):
            api_result = await adapter.apply_settlement(
                organization_id,
                SettlementApplication(
                    target_erp_reference=target_erp_reference,
                    amount=amount,
                    currency=currency,
                    source_reference=source_reference,
                    source_document_type=source_document_type,
                    target_invoice_number=target_invoice_number,
                    note=note,
                    source_ap_item_id=source_ap_item_id,
                    related_ap_item_id=target_ap_item_id,
                ),
                ap_item_id=target_ap_item_id,
                idempotency_key=attempt_key,
            )
        elif not validation.get("ok"):
            api_result = {
                "status": "error",
                "reason": str(validation.get("reason") or "validation_failed"),
                "validation": validation,
                "idempotency_key": attempt_key,
                "target_erp_reference": target_erp_reference,
            }
        else:
            api_result = {
                "status": "skipped",
                "reason": "api_not_available_for_connector",
                "idempotency_key": attempt_key,
                "target_erp_reference": target_erp_reference,
            }

    api_status = str(api_result.get("status") or "")
    if api_status in {"success", "already_applied"}:
        _audit(
            resolved_db,
            ap_item_id=target_ap_item_id,
            organization_id=organization_id,
            event_type=f"{event_prefix}_success",
            actor_id=actor_id,
            reason="api_applied",
            payload={
                "api_status": api_status,
                "source_ap_item_id": source_ap_item_id,
                "target_erp_reference": target_erp_reference,
                "erp_reference": api_result.get("erp_reference"),
                "route_plan": route_plan,
            },
            idempotency_key=f"{event_prefix}_success:{_stable_hash({**attempt_key_seed, 'erp_reference': api_result.get('erp_reference')})}",
            correlation_id=correlation_id,
        )
        return _finalize_follow_on_response_contract(
            {
                **api_result,
                "execution_mode": "api",
                "routing": route_plan,
                "fallback": {"requested": False, "eligible": False, "reason": "not_needed", "ap_item_id": target_ap_item_id},
                "target_erp_reference": target_erp_reference,
            },
            detected_erp_type=detected_erp_type,
            route_plan=route_plan,
            action_key=action_key,
            raw_result=api_result,
        )

    # API failed — no browser fallback, return failure directly
    _audit(
        resolved_db,
        ap_item_id=target_ap_item_id,
        organization_id=organization_id,
        event_type=f"{event_prefix}_failed",
        actor_id=actor_id,
        reason=str(api_result.get("reason") or "api_failed"),
        payload={
            "api_status": api_status,
            "api_reason": api_result.get("reason"),
            "route_plan": route_plan,
            "source_ap_item_id": source_ap_item_id,
            "target_erp_reference": target_erp_reference,
        },
        idempotency_key=f"{event_prefix}_failed:{_stable_hash(attempt_key_seed)}",
        correlation_id=correlation_id,
    )
    return _finalize_follow_on_response_contract(
        {
            **api_result,
            "execution_mode": "api_failed",
            "routing": route_plan,
            "fallback": {
                "requested": False,
                "eligible": False,
                "reason": "browser_fallback_removed",
                "ap_item_id": target_ap_item_id,
            },
            "target_erp_reference": target_erp_reference,
        },
        detected_erp_type=detected_erp_type,
        route_plan=route_plan,
        action_key=action_key,
        raw_result=api_result,
    )


async def apply_credit_note_api_first(
    *,
    organization_id: str,
    target_ap_item_id: str,
    source_ap_item_id: str,
    actor_id: str = "erp_router",
    target_erp_reference: str,
    target_invoice_number: Optional[str] = None,
    credit_note_number: Optional[str] = None,
    amount: float,
    currency: str,
    note: Optional[str] = None,
    email_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    db: Optional[ClearledgrDB] = None,
) -> Dict[str, Any]:
    return await _erp_follow_on_api_first(
        action_key="apply_credit_note",
        organization_id=organization_id,
        actor_id=actor_id,
        target_ap_item_id=target_ap_item_id,
        source_ap_item_id=source_ap_item_id,
        target_erp_reference=target_erp_reference,
        target_invoice_number=target_invoice_number,
        amount=amount,
        currency=currency,
        source_reference=credit_note_number,
        source_document_type="credit_note",
        note=note,
        email_id=email_id,
        correlation_id=correlation_id,
        db=db,
    )


async def apply_settlement_api_first(
    *,
    organization_id: str,
    target_ap_item_id: str,
    source_ap_item_id: str,
    actor_id: str = "erp_router",
    source_document_type: str,
    target_erp_reference: str,
    target_invoice_number: Optional[str] = None,
    source_reference: Optional[str] = None,
    amount: float,
    currency: str,
    note: Optional[str] = None,
    email_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    db: Optional[ClearledgrDB] = None,
) -> Dict[str, Any]:
    return await _erp_follow_on_api_first(
        action_key="apply_settlement",
        organization_id=organization_id,
        actor_id=actor_id,
        target_ap_item_id=target_ap_item_id,
        source_ap_item_id=source_ap_item_id,
        target_erp_reference=target_erp_reference,
        target_invoice_number=target_invoice_number,
        amount=amount,
        currency=currency,
        source_reference=source_reference,
        source_document_type=source_document_type,
        note=note,
        email_id=email_id,
        correlation_id=correlation_id,
        db=db,
    )
