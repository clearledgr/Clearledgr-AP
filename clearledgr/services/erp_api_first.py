"""API-first ERP posting router with browser-agent fallback.

This module gives Clearledgr a concrete migration pattern:
- Try native ERP API connector first.
- Fall back to browser-agent macro dispatch when API posting fails or is unavailable.
- Emit audit telemetry so fallback rate can be tracked over time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import hashlib

from clearledgr.core.database import ClearledgrDB, get_db
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
            "idempotency_key": idempotency_key,
        }
    )


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
        session = service.create_session(
            organization_id=organization_id,
            ap_item_id=resolved_item_id,
            created_by=actor_id,
            metadata={
                "workflow_id": "erp_posting_fallback",
                "actor_role": "ap_operator",
                "email_id": email_id,
                "invoice_number": invoice_number,
            },
        )
        macro = service.dispatch_macro(
            session_id=str(session.get("id")),
            macro_name="post_invoice_to_erp",
            actor_id=actor_id,
            actor_role="ap_operator",
            workflow_id="erp_posting_fallback",
            params={
                "invoice_number": invoice_number,
                "vendor_name": vendor_name,
                "amount": amount,
                "currency": currency,
                "vendor_portal_url": vendor_portal_url,
                "erp_url": erp_url,
                "email_id": email_id,
            },
            dry_run=False,
        )
        return {
            "requested": True,
            "eligible": True,
            "reason": "fallback_dispatched",
            "ap_item_id": resolved_item_id,
            "session_id": session.get("id"),
            "macro_name": "post_invoice_to_erp",
            "dispatch_status": macro.get("status"),
            "queued": int(macro.get("queued") or 0),
            "blocked": int(macro.get("blocked") or 0),
            "denied": int(macro.get("denied") or 0),
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
        "timestamp_bucket": _utcnow()[:16],  # minute granularity for dedupe
    }
    attempt_key = f"erp_api_attempt:{_stable_hash(attempt_key_seed)}"

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
    )

    if connector_capability.supports_api_post_bill and connection_present:
        api_result = await post_bill(organization_id, bill)
    else:
        api_result = {
            "status": "skipped",
            "erp": route_plan.get("erp_type"),
            "reason": "api_not_available_for_connector",
            "route_plan": route_plan,
        }

    api_status = str(api_result.get("status") or "")
    api_success = api_status == "success"

    if api_success:
        _audit(
            resolved_db,
            ap_item_id=resolved_ap_item_id,
            organization_id=organization_id,
            event_type="erp_api_success",
            actor_id=actor_id,
            reason="api_posted",
            payload={
                "api_status": api_status,
                "bill_id": api_result.get("bill_id"),
                "doc_num": api_result.get("doc_num"),
                "erp": api_result.get("erp"),
                "route_plan": route_plan,
            },
            idempotency_key=f"erp_api_success:{_stable_hash({**attempt_key_seed, 'bill_id': api_result.get('bill_id')})}",
        )
        return {
            **api_result,
            "execution_mode": "api",
            "routing": route_plan,
            "fallback": {
                "requested": False,
                "eligible": False,
                "reason": "not_needed",
                "ap_item_id": resolved_ap_item_id,
            },
        }

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
        )
        return {
            **api_result,
            "execution_mode": "api_failed",
            "routing": route_plan,
            "fallback": fallback_disabled,
        }

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
        )
        return {
            **api_result,
            "status": "pending_browser_fallback",
            "execution_mode": "browser_fallback",
            "routing": route_plan,
            "fallback": fallback,
        }

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
    )
    return {
        **api_result,
        "execution_mode": "api_failed",
        "routing": route_plan,
        "fallback": fallback,
    }
