"""Slack interactive handlers for AP invoice approvals."""
from __future__ import annotations

import json
import hashlib
import logging
import urllib.parse
from typing import Any, Dict, Optional

from solden.core.http_client import get_http_client
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

from solden.core.ap_item_resolution import (
    resolve_ap_context as resolve_shared_ap_context,
    resolve_ap_correlation_id,
)
from solden.core.database import get_db
from solden.core.org_utils import assert_org_id

router = APIRouter(prefix="/slack/invoices", tags=["slack-invoices"])
legacy_router = APIRouter(prefix="/slack", tags=["slack-invoices"])
logger = logging.getLogger(__name__)


def _approval_action_error_type():
    from solden.core.approval_action_contract import ApprovalActionContractError

    return ApprovalActionContractError


def _normalize_slack_action(*args, **kwargs):
    from solden.core.approval_action_contract import normalize_slack_action

    return normalize_slack_action(*args, **kwargs)


def _resolve_action_precedence(*args, **kwargs):
    from solden.core.approval_action_contract import resolve_action_precedence

    return resolve_action_precedence(*args, **kwargs)


def _get_channel_action_block_reason(*args, **kwargs):
    from solden.core.launch_controls import get_channel_action_block_reason

    return get_channel_action_block_reason(*args, **kwargs)


def _slack_display_name_from_user(user_row: Optional[Dict[str, Any]], slack_user: Optional[Dict[str, Any]], fallback: str) -> str:
    profile = (slack_user or {}).get("profile") if isinstance(slack_user, dict) else {}
    candidates = [
        (profile or {}).get("real_name_normalized"),
        (profile or {}).get("real_name"),
        (profile or {}).get("display_name_normalized"),
        (profile or {}).get("display_name"),
        (slack_user or {}).get("real_name") if isinstance(slack_user, dict) else None,
        (slack_user or {}).get("name") if isinstance(slack_user, dict) else None,
        (user_row or {}).get("name") if isinstance(user_row, dict) else None,
        (user_row or {}).get("email") if isinstance(user_row, dict) else None,
        fallback,
    ]
    for value in candidates:
        token = str(value or "").strip()
        if token:
            return token
    return fallback


async def _resolve_slack_actor_identity(db, slack_user_id: str, organization_id: str) -> Dict[str, str]:
    """Resolve Slack callback actor into a durable identity object."""
    slack_user_id = str(slack_user_id or "").strip()
    if not slack_user_id:
        return {"email": "", "display_name": "", "slack_user_id": ""}

    cached_user = db.get_user_by_slack_id(slack_user_id)
    cached_email = str((cached_user or {}).get("email") or "").strip()
    identity = {
        "email": cached_email,
        "display_name": _slack_display_name_from_user(cached_user, None, slack_user_id),
        "slack_user_id": slack_user_id,
    }

    try:
        from solden.services.slack_api import get_slack_client

        client = get_slack_client(organization_id=organization_id)
        slack_user = await client.get_user_info(slack_user_id, prefer_user_token=True)
        profile = slack_user.get("profile", {}) if isinstance(slack_user, dict) else {}
        resolved_email = str(profile.get("email") or cached_email or "").strip()
        display_name = _slack_display_name_from_user(cached_user, slack_user, slack_user_id)
        identity = {
            "email": resolved_email,
            "display_name": display_name,
            "slack_user_id": slack_user_id,
        }

        existing = db.get_user_by_email(resolved_email) if resolved_email else None
        if existing:
            updates: Dict[str, Any] = {"slack_user_id": slack_user_id}
            if display_name and not str(existing.get("name") or "").strip():
                updates["name"] = display_name
            try:
                db.update_user(existing["id"], **updates)
            except Exception:
                pass
    except Exception as exc:
        logger.debug("Slack user identity resolution failed for %s: %s", slack_user_id, exc)

    return identity


def _get_pending_step_approvers(db, gmail_id: str, organization_id: str) -> Optional[list]:
    """Get the approvers list from the pending approval step for an invoice."""
    try:
        chain = db.db_get_chain_by_invoice(organization_id, gmail_id)
        if not chain or chain.get("status") != "pending":
            return None
        for step in (chain.get("steps") or []):
            if step.get("status") == "pending":
                raw = step.get("approvers") or "[]"
                if isinstance(raw, str):
                    return json.loads(raw)
                return raw
    except Exception as exc:
        logger.debug("Pending step approvers lookup failed: %s", exc)
    return None


async def _require_slack_signature(request: Request) -> bytes:
    from solden.core.slack_verify import require_slack_signature

    return await require_slack_signature(request)


def _build_channel_runtime(*args, **kwargs):
    from solden.services.agent_command_dispatch import build_channel_runtime

    return build_channel_runtime(*args, **kwargs)


async def _dispatch_runtime_intent(*args, **kwargs):
    from solden.services.agent_command_dispatch import dispatch_runtime_intent

    return await dispatch_runtime_intent(*args, **kwargs)


# Map common runtime-reason codes to plain English the AP Manager can act
# on directly. Thesis §6.8 voice: say what's wrong, not what the state
# machine called it. Any code not in the map gets Title Case snake-
# to-space as a safe fallback (still better than raw `vendor_iban_
# unverified`).
_REASON_LABELS: Dict[str, str] = {
    "vendor_iban_unverified": "The vendor's bank details haven't been verified yet. Payment can't be scheduled until they complete IBAN verification in the portal.",
    "match_tolerance_exceeded": "The 3-way match delta exceeds your policy tolerance.",
    "erp_connector_offline": "The ERP connector is temporarily offline. Retry in a few minutes — the invoice is safe in the queue.",
    "field_review_required": "A reviewer must confirm the critical fields before this can post.",
    "budget_exceeded": "This invoice would push a budget over its limit.",
    "needs_budget_decision": "This invoice needs a budget decision before posting.",
    "no_po_linked": "No Purchase Order is linked to this invoice yet.",
    "duplicate_detected": "A similar invoice was already recorded this period — please confirm it's not a duplicate.",
    "policy_violation": "This action breaks one of the AP policy rules.",
    "invoice_not_found": "This invoice is no longer in the active queue.",
    "invoice_already_posted": "This invoice has already been posted to the ERP.",
    "invoice_already_rejected": "This invoice has already been rejected.",
    "actor_not_authorized": "You're not on the approver list for this invoice.",
    "approval_window_expired": "The approval window for this invoice has expired.",
    "waiting_on_vendor": "The agent is still waiting on a response from the vendor.",
    "override_window_closed": "The override window on this invoice has closed.",
    "sap_validation_failed": "SAP rejected this bill during pre-flight validation — please review vendor, amount, or company code.",
    "network_error": "We couldn't reach the ERP right now. Please retry in a moment.",
}


def _humanize_reason(reason: Any, fallback: str = "This action couldn't be completed.") -> str:
    """Turn a runtime reason code into an AP-manager-friendly sentence.

    Matches the map first; otherwise converts `snake_case` → `Title Case`
    so the user still gets something readable instead of the raw code.
    """
    raw = str(reason or "").strip()
    if not raw:
        return fallback
    if raw in _REASON_LABELS:
        return _REASON_LABELS[raw]
    # Safe fallback: snake_case → Title Case with a trailing period.
    pretty = raw.replace("_", " ").strip()
    if not pretty:
        return fallback
    pretty = pretty[:1].upper() + pretty[1:]
    if not pretty.endswith("."):
        pretty += "."
    return pretty


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
    organization_id: str = "_unauthenticated",
    ap_item_id: str | None = None,
    actor_id: str | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    reason: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> None:
    resolved_ap_item_id = ap_item_id or f"channel_callback:slack:{organization_id}"
    try:
        db.append_audit_event(
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


def _try_acquire_received_lock(
    db,
    *,
    idempotency_key: str,
    organization_id: str,
    ap_item_id: str | None,
    actor_id: str | None,
    correlation_id: str | None,
    source: str,
    metadata: Dict[str, Any] | None = None,
) -> bool:
    """Pre-dispatch dedup primitive for Slack/Teams approval webhooks.

    Slack retries an interactivity payload after a 3s timeout. Two
    near-simultaneous deliveries of the same click would both pass
    the audit-row precedence check ``already_processed=False``,
    both dispatch, and both fire the underlying intent — the
    runtime then sees two ``approve_invoice`` calls for the same
    AP item.

    This helper writes a ``received_key`` audit row BEFORE dispatch
    via the canonical ``append_audit_event`` path. The audit_events
    table has ``UNIQUE(idempotency_key)`` so only one writer wins
    per key. The store handles the unique-conflict by returning the
    *winner's* row (ap_store.py ~L2118). We then compare the
    winner's ``correlation_id`` to ours: match → we won, return
    True (caller proceeds to dispatch). Mismatch → another worker
    is mid-flight on this same key, return False (caller short-
    circuits with ``duplicate``).

    Returns False on any failure (DB outage, missing helpers) — fail
    closed. The cost of one false-positive "duplicate" is a single
    Slack message saying "Action already received"; the cost of
    failing open here is a duplicate ERP post.
    """
    if not idempotency_key or not correlation_id:
        return False
    resolved_ap_item_id = ap_item_id or f"channel_callback:{source}:{organization_id}"
    payload = {
        "ap_item_id": resolved_ap_item_id,
        "event_type": "channel_action_received",
        "actor_type": "user" if actor_id else "system",
        "actor_id": actor_id or f"{source}_callback",
        "source": source,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "metadata": metadata or {},
        "organization_id": organization_id,
    }
    try:
        db.append_audit_event(payload)
    except Exception as exc:
        logger.error(
            "[%s] received-lock write failed for key=%s: %s — failing closed",
            source, idempotency_key, exc,
        )
        return False
    try:
        winner = db.get_ap_audit_event_by_key(idempotency_key)
    except Exception as exc:
        logger.error(
            "[%s] received-lock readback failed for key=%s: %s — failing closed",
            source, idempotency_key, exc,
        )
        return False
    if not winner:
        logger.error(
            "[%s] received-lock readback returned no row for key=%s — failing closed",
            source, idempotency_key,
        )
        return False
    winner_corr = str(winner.get("correlation_id") or "").strip()
    if winner_corr != str(correlation_id or "").strip():
        # Another worker already wrote this received_key with a
        # different correlation_id — treat as duplicate.
        return False
    return True


def _resolve_ap_context(db, organization_id: str, gmail_id: str) -> tuple[str, str | None]:
    org_id, ap_item = resolve_shared_ap_context(db, organization_id, gmail_id)
    ap_item_id = str((ap_item or {}).get("id") or "").strip() or None
    return org_id, ap_item_id


def _resolve_correlation_id(db, ap_item_id: str | None, org_id: str, gmail_id: str) -> str | None:
    return resolve_ap_correlation_id(
        db,
        org_id,
        ap_item_id=ap_item_id,
        reference_id=gmail_id,
    )


async def _post_to_response_url(
    response_url: str,
    payload: Dict[str, Any],
    *,
    organization_id: str = "_unauthenticated",
    ap_item_id: str | None = None,
) -> bool:
    """Post a follow-up message to Slack's response_url with inline retry enqueue.

    Returns True on success, False when POST failed (enqueue attempted).
    """
    try:
        client = get_http_client()
        resp = await client.post(response_url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Slack response_url POST failed for ap_item=%s: %s", ap_item_id, exc)
        try:
            db = get_db()
            db.enqueue_notification(
                organization_id=organization_id,
                channel="slack_response_url",
                payload={"response_url": response_url, "body": payload},
                box_id=ap_item_id,
                box_type="ap_item" if ap_item_id else None,
            )
        except Exception as enq_exc:
            logger.error(
                "CRITICAL: Slack response_url POST AND enqueue both failed for ap_item=%s: %s",
                ap_item_id, enq_exc,
            )
        return False


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


async def _dispatch_slack_action(action: Any) -> Dict[str, Any]:
    runtime = _build_channel_runtime(
        organization_id=assert_org_id(
            action.organization_id, context="_dispatch_slack_action"
        ),
        actor_id=action.actor_id or "slack_user",
        actor_email=action.actor_email or action.actor_id or "slack_user",
        db=get_db(),
        fallback_actor="slack_user",
    )
    actor_identity = {
        "platform": "slack",
        "platform_user_id": str(action.actor_id or "").strip(),
        "email": str(action.actor_email or "").strip(),
        "display_name": str(action.actor_display or "").strip(),
    }

    if action.action == "approve":
        result = await _dispatch_runtime_intent(
            runtime,
            "approve_invoice",
            {
                "ap_item_id": action.ap_item_id,
                "email_id": action.gmail_id,
                "reason": action.reason,
                "source_channel": "slack",
                "source_channel_id": action.source_channel_id,
                "source_message_ref": action.source_message_ref,
                "actor_id": action.actor_id,
                "actor_display": action.actor_display,
                "actor_email": action.actor_email,
                "actor_identity": actor_identity,
                "action_run_id": action.run_id,
                "decision_request_ts": action.request_ts,
                "correlation_id": action.correlation_id,
                "action_variant": action.action_variant,
            },
            idempotency_key=action.idempotency_key,
        )
        workflow_result = result.get("result") if isinstance(result.get("result"), dict) else result
        status = str(workflow_result.get("status") or result.get("status") or "").strip().lower()
        if status == "needs_budget_decision":
            return {
                "response_type": "ephemeral",
                "text": "Budget decision required. Use Approve override, Request info, or Reject.",
                "result": result,
            }
        blocked_reason = str(workflow_result.get("reason") or result.get("reason") or "").strip().lower()
        if status == "needs_field_review" or (status == "blocked" and blocked_reason == "field_review_required"):
            return {
                "response_type": "ephemeral",
                "text": "Field review required before posting. Open the invoice to review critical fields.",
                "result": result,
            }
        if status not in {"approved", "posted", "posted_to_erp"}:
            return {
                "response_type": "ephemeral",
                "text": f"Can't approve: {_humanize_reason(workflow_result.get('reason') or result.get('reason') or status)}",
                "result": result,
            }
        erp_result = workflow_result.get("erp_result") or {}
        doc_num = erp_result.get("doc_num") or erp_result.get("document_number") or erp_result.get("erp_document")
        bill_id = erp_result.get("bill_id")
        detail = f"Bill ID: {bill_id}" if bill_id else "Posted"
        if doc_num:
            detail += f" | Doc #: {doc_num}"
        prefix = "Budget override approved and posted." if action.action_variant == "budget_override" else "Posted to ERP."
        return {"response_type": "ephemeral", "text": f"{prefix} {detail}", "result": result}

    if action.action == "request_info":
        result = await _dispatch_runtime_intent(
            runtime,
            "request_info",
            {
                "ap_item_id": action.ap_item_id,
                "email_id": action.gmail_id,
                "reason": action.reason or "budget_adjustment_requested_in_slack",
                "source_channel": "slack",
                "source_channel_id": action.source_channel_id,
                "source_message_ref": action.source_message_ref,
                "actor_id": action.actor_id,
                "actor_display": action.actor_display,
                "actor_email": action.actor_email,
                "actor_identity": actor_identity,
                "action_run_id": action.run_id,
                "decision_request_ts": action.request_ts,
                "correlation_id": action.correlation_id,
                "action_variant": action.action_variant,
            },
            idempotency_key=action.idempotency_key,
        )
        status = str(result.get("status") or "").strip().lower()
        if status == "needs_info":
            return {
                "response_type": "ephemeral",
                "text": "Request for info recorded. Invoice moved to Needs info.",
                "result": result,
            }
        return {
            "response_type": "ephemeral",
            "text": f"Couldn't send the info request: {_humanize_reason(result.get('reason') or status)}",
            "result": result,
        }

    if action.action == "reject":
        result = await _dispatch_runtime_intent(
            runtime,
            "reject_invoice",
            {
                "ap_item_id": action.ap_item_id,
                "email_id": action.gmail_id,
                "reason": action.reason or "rejected_in_slack",
                "source_channel": "slack",
                "source_channel_id": action.source_channel_id,
                "source_message_ref": action.source_message_ref,
                "actor_id": action.actor_id,
                "actor_display": action.actor_display,
                "actor_email": action.actor_email,
                "actor_identity": actor_identity,
                "action_run_id": action.run_id,
                "decision_request_ts": action.request_ts,
                "correlation_id": action.correlation_id,
                "action_variant": action.action_variant,
            },
            idempotency_key=action.idempotency_key,
        )
        if str(result.get("status") or "").strip().lower() == "rejected":
            return {"response_type": "ephemeral", "text": "Invoice rejected.", "result": result}
        return {
            "response_type": "ephemeral",
            "text": f"Can't reject: {_humanize_reason(result.get('reason') or result.get('status'))}",
            "result": result,
        }

    if action.action == "undo_post":
        # Phase 1.4 override-window reversal (DESIGN_THESIS.md §8).
        # The action's gmail_id field carries the override_window id
        # because the contract has no dedicated lookup-key field — see
        # the comment in approval_action_contract._extract_slack_gmail_id.
        return await _handle_undo_post_action(action, actor_identity)

    raise HTTPException(status_code=400, detail="unsupported_action")


async def _handle_undo_post_action(
    action: Any, actor_identity: Dict[str, Any]
) -> Dict[str, Any]:
    """Process a Slack ``undo_post`` button click.

    Loads the override_window referenced by ``action.gmail_id`` (which
    is actually the window id — see the contract comment), calls the
    OverrideWindowService to attempt the reversal, then updates the
    Slack card to reflect the new state.
    """
    window_id = str(action.gmail_id or "").strip()
    if not window_id:
        return {
            "response_type": "ephemeral",
            "text": "Cannot reverse: override window reference is missing.",
        }

    db = get_db()
    window = db.get_override_window(window_id)
    if not window:
        return {
            "response_type": "ephemeral",
            "text": "This undo button no longer points to a valid override window.",
        }

    organization_id = str(
        action.organization_id
        or window.get("organization_id")
        or ""
    ).strip()
    if not organization_id:
        return {
            "response_type": "ephemeral",
            "text": "Cannot identify the org for this undo. Please refresh and try again.",
        }
    actor_label = (
        action.actor_display
        or action.actor_email
        or action.actor_id
        or "slack_user"
    )

    from solden.services.override_window import get_override_window_service

    service = get_override_window_service(organization_id, db=db)
    outcome = await service.attempt_reversal(
        window_id=window_id,
        actor_id=str(actor_label),
        reason=action.reason or "human_override_via_slack",
    )

    ap_item_id = window.get("ap_item_id")
    ap_item = db.get_ap_item(ap_item_id) if ap_item_id else {}
    fresh_window = db.get_override_window(window_id) or window

    from solden.services import slack_cards

    if outcome.status in {"reversed", "already_reversed"}:
        await slack_cards.update_card_to_reversed(
            organization_id=organization_id,
            ap_item=ap_item or {},
            window=fresh_window,
            actor_id=str(actor_label),
            reversal_ref=outcome.reversal_ref,
            reversal_method=outcome.reversal_method,
        )
        msg = (
            "Bill reversed at the ERP."
            if outcome.status == "reversed"
            else "Bill was already reversed; nothing to do."
        )
        return {"response_type": "ephemeral", "text": msg, "result": outcome.to_dict()}

    if outcome.status == "expired":
        await slack_cards.update_card_to_finalized(
            organization_id=organization_id,
            ap_item=ap_item or {},
            window=fresh_window,
        )
        return {
            "response_type": "ephemeral",
            "text": "The override window has expired — this post is final.",
            "result": outcome.to_dict(),
        }

    # failed | not_found | skipped — surface a clear escalation message
    await slack_cards.update_card_to_reversal_failed(
        organization_id=organization_id,
        ap_item=ap_item or {},
        window=fresh_window,
        actor_id=str(actor_label),
        failure_reason=outcome.reason or "unknown_error",
        failure_message=outcome.message,
    )
    return {
        "response_type": "ephemeral",
        "text": (
            f"Couldn't reverse the posting: {_humanize_reason(outcome.reason or outcome.status)} "
            "Manual intervention may be required at the ERP level."
        ),
        "result": outcome.to_dict(),
    }


async def _run_and_record_slack_action(normalized: Any, processed_key: str) -> Dict[str, Any]:
    db = get_db()
    try:
        response = await _dispatch_slack_action(normalized)
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
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.exception("Unhandled Slack interactive action error")
        _audit_callback_event(
            db,
            event_type="channel_action_failed",
            source="slack",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:failed",
            reason=str(exc),
            metadata={"action": normalized.to_dict(), "status_code": 500},
            correlation_id=normalized.correlation_id,
        )
        raise HTTPException(status_code=500, detail="slack_action_failed")

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
    return response


async def _complete_slack_action_via_response_url(normalized: Any, processed_key: str, response_url: str) -> None:
    try:
        response = await _run_and_record_slack_action(normalized, processed_key)
        final_reply = {
            "response_type": response.get("response_type", "ephemeral"),
            "text": response.get("text", "Action received."),
            "replace_original": False,
        }
    except HTTPException as exc:
        final_reply = {
            "response_type": "ephemeral",
            "text": str(exc.detail or "Action failed. Open the invoice in Solden and try again."),
            "replace_original": False,
        }
    await _post_to_response_url(
        response_url,
        final_reply,
        organization_id=assert_org_id(
            normalized.organization_id,
            context="_complete_slack_action_via_response_url",
        ),
        ap_item_id=normalized.ap_item_id,
    )


@router.post("/interactive")
async def handle_invoice_interactive(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack interactive actions for invoice approvals."""
    db = get_db()
    try:
        body = await _require_slack_signature(request)
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
    _chase_action_id = str(raw_action.get("action_id") or "")
    _chase_session_id = str(raw_action.get("value") or "")

    # ERP-native bill approvals (NetSuite-arrived bills with payment
    # holds — see solden/services/erp_native_approval.py). Action
    # IDs are prefixed cl_erp_approve_<ap_item_id> / cl_erp_reject_<ap_item_id>.
    # Dispatched to a separate module so the Gmail-bound approve_invoice
    # handler below stays unchanged.
    if _chase_action_id.startswith("cl_erp_approve_") or _chase_action_id.startswith("cl_erp_reject_"):
        from solden.services.erp_native_approval import (
            SLACK_ACTION_APPROVE,
            SLACK_ACTION_REJECT,
            handle_slack_decision,
        )
        decision = "approve" if _chase_action_id.startswith(SLACK_ACTION_APPROVE) else "reject"
        ap_item_id_from_action = _chase_action_id.replace(f"{SLACK_ACTION_APPROVE}_", "").replace(f"{SLACK_ACTION_REJECT}_", "").strip()
        if not ap_item_id_from_action and _chase_session_id.startswith("{"):
            try:
                parsed = json.loads(_chase_session_id)
                ap_item_id_from_action = str(parsed.get("ap_item_id") or "").strip()
            except Exception:
                ap_item_id_from_action = ""
        slack_user = (payload.get("user") or {}) if isinstance(payload.get("user"), dict) else {}
        actor_payload = {
            "actor_id": str(slack_user.get("id") or "").strip() or "slack_user",
            "actor_email": str(slack_user.get("email") or slack_user.get("username") or "").strip() or None,
        }
        result = await handle_slack_decision(
            ap_item_id=ap_item_id_from_action,
            decision=decision,
            actor=actor_payload,
        )
        if result.get("ok"):
            source = str(result.get("source") or "").strip()
            erp_label = "SAP" if source == "sap_native" else "NetSuite"
            verb = "approved" if decision == "approve" else "rejected"
            extra = (
                f" · payment block released in {erp_label}"
                if decision == "approve"
                else f" · bill cancelled in {erp_label}"
            )
            return {"response_type": "ephemeral", "text": f"Bill {verb}.{extra}"}
        # On failure, surface whether the ERP was already mutated so the
        # operator knows whether to retry or whether they need a manual
        # reconciliation. The reject path can leave the ERP voided but
        # the Solden Box stuck (rare; only on DB write failure
        # between void and state transition).
        reason = str(result.get("reason") or "action_failed")
        if result.get("erp_voided") and decision == "reject":
            erp_label = "SAP" if str(result.get("source") or "") == "sap_native" else "NetSuite"
            return {
                "response_type": "ephemeral",
                "text": (
                    f"Bill voided in {erp_label} but Solden state update failed ({reason}). "
                    "Engineering should reconcile."
                ),
            }
        return {"response_type": "ephemeral", "text": f"Action could not complete: {reason}"}

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

    # M18: derive org from the VERIFIED Slack team_id BEFORE resolving
    # AP context. Pre-fix this called ``_resolve_ap_context(db,
    # "default", gmail_candidate)`` — passing the literal "default"
    # string as the requested org. ``_resolve_ap_context`` then
    # adopted whatever tenant the gmail_id resolved to (the post-M16
    # tightening rejects org swaps but the unscoped ``get_invoice_status``
    # fallback still let "default"-tenant rows pass through). The
    # downstream team→org guard caught some cases but not all —
    # particularly when the Slack workspace had no per-org install,
    # a tenant whose org id was the legacy "default" literal, or
    # when the team→org guard's ``if install and bound_org`` short-
    # circuit skipped the check on missing installation rows.
    #
    # Now: AAD-style binding. Team_id maps to a Solden org via
    # ``get_slack_installation_by_team``. No install → refuse.
    _slack_team_id = str(((payload.get("team") or {}) if isinstance(payload.get("team"), dict) else {}).get("id") or "").strip()
    bound_org = ""
    if _slack_team_id and hasattr(db, "get_slack_installation_by_team"):
        try:
            install = db.get_slack_installation_by_team(_slack_team_id)
        except Exception as exc:
            install = None
            logger.warning(
                "[slack/interactive] team→org lookup failed for team=%s: %s",
                _slack_team_id, exc,
            )
        bound_org = str((install or {}).get("organization_id") or "").strip()

    if not bound_org:
        # No team→org binding — refuse. The Slack signing secret is
        # workspace-shared so a verified signature alone doesn't
        # establish tenancy. Fail closed.
        _body_hash = hashlib.sha256(body or b"").hexdigest()[:16]
        _audit_callback_event(
            db,
            event_type="channel_callback_unauthorized",
            source="slack",
            organization_id="_unauthenticated",
            idempotency_key=f"slack:no_installation:{_body_hash}",
            reason="slack_team_not_provisioned",
            metadata={"slack_team_id": _slack_team_id or None},
        )
        return JSONResponse(
            status_code=403,
            content={"response_type": "ephemeral", "text": "This Slack workspace isn't connected to any Solden tenant."},
        )

    # Procurement PO approval clicks (po_approve_<id> / po_reject_<id>).
    # Isolated from the AP normalization path; reuses the verified
    # team->org binding above for tenancy. Feature-flagged.
    if _chase_action_id.startswith("po_approve_") or _chase_action_id.startswith("po_reject_"):
        from solden.core.feature_flags import is_procurement_chat_enabled
        from solden.services.procurement_chat import dispatch_po_chat_decision

        if not is_procurement_chat_enabled():
            return {"response_type": "ephemeral", "text": "Procurement chat approvals aren't enabled."}
        po_decision = "approve" if _chase_action_id.startswith("po_approve_") else "reject"
        po_id = ""
        if value.startswith("{"):
            try:
                _pv = json.loads(value)
                if isinstance(_pv, dict) and _pv.get("box_type") == "purchase_order":
                    po_id = str(_pv.get("po_id") or "")
            except Exception:
                po_id = ""
        if not po_id:
            po_id = _chase_action_id.replace("po_approve_", "").replace("po_reject_", "").strip()
        po_row = db.get_purchase_order(po_id) if (po_id and hasattr(db, "get_purchase_order")) else None
        if not po_row or str(po_row.get("organization_id") or "") != bound_org:
            _audit_callback_event(
                db, event_type="channel_action_invalid", source="slack",
                organization_id=bound_org, idempotency_key=f"slack:po_tenant:{po_id}",
                reason="po_not_found_or_cross_tenant", metadata={"po_id": po_id},
            )
            return JSONResponse(
                status_code=404,
                content={"response_type": "ephemeral", "text": "Purchase order not found."},
            )
        _slack_user = (payload.get("user") or {}) if isinstance(payload.get("user"), dict) else {}
        _actor_id = str(_slack_user.get("id") or "").strip() or "slack_user"
        _actor_email = str(_slack_user.get("email") or _slack_user.get("username") or "").strip() or None
        result = await dispatch_po_chat_decision(
            bound_org, po_id, po_decision, actor_id=_actor_id, actor_email=_actor_email,
        )
        if str(result.get("status") or "") == "ok":
            verb = "approved" if po_decision == "approve" else "rejected"
            return {"response_type": "ephemeral", "text": f"PO {po_row.get('po_number') or po_id} {verb}."}
        return {
            "response_type": "ephemeral",
            "text": f"Couldn't {po_decision} the PO: {result.get('error') or result.get('status') or 'blocked'}",
        }

    organization_id, ap_item_id = _resolve_ap_context(db, bound_org, gmail_candidate)

    # Defense in depth: if AP-item resolution returned a different
    # tenant's org (it shouldn't, post-M16 — but pin the invariant),
    # refuse.
    if str(organization_id or "").strip() and str(organization_id) != bound_org:
        _body_hash = hashlib.sha256(body or b"").hexdigest()[:16]
        _audit_callback_event(
            db,
            event_type="channel_action_invalid",
            source="slack",
            organization_id=bound_org,
            idempotency_key=f"slack:tenant_mismatch:{_body_hash}",
            reason="tenant_mismatch",
            metadata={
                "slack_team_id": _slack_team_id,
                "team_bound_org": bound_org,
                "ap_item_org": organization_id,
                "ap_item_id": ap_item_id,
            },
        )
        raise HTTPException(status_code=403, detail="tenant_mismatch")

    ApprovalActionContractError = _approval_action_error_type()
    try:
        normalized = _normalize_slack_action(
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

    if not ap_item_id and normalized.gmail_id:
        organization_id, ap_item_id = _resolve_ap_context(db, organization_id, normalized.gmail_id)
    normalized.organization_id = organization_id
    normalized.ap_item_id = ap_item_id
    normalized.correlation_id = _resolve_correlation_id(db, ap_item_id, organization_id, normalized.gmail_id)

    blocked_reason = _get_channel_action_block_reason(
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

    processed_key = f"{normalized.idempotency_key}:processed"
    received_key = f"{normalized.idempotency_key}:received"
    ap_item_row = None
    if normalized.ap_item_id and hasattr(db, "get_ap_item"):
        try:
            ap_item_row = db.get_ap_item(normalized.ap_item_id)
        except Exception as exc:
            logger.debug("AP item pre-fetch failed: %s", exc)

    # Resolve Slack actor email for approver authorization
    pending_step_approvers = None
    try:
        actor_identity = await _resolve_slack_actor_identity(db, normalized.actor_id, normalized.organization_id)
        normalized.actor_email = str(actor_identity.get("email") or "").strip() or None
        resolved_display = str(actor_identity.get("display_name") or "").strip()
        if resolved_display:
            normalized.actor_display = resolved_display
        raw_payload = dict(normalized.raw_payload or {})
        raw_payload.update(
            {
                "actor_email": normalized.actor_email,
                "actor_display": normalized.actor_display,
                "actor_identity": actor_identity,
            }
        )
        normalized.raw_payload = raw_payload
    except Exception as exc:
        logger.debug("Slack actor email resolution failed: %s", exc)

    # Load pending step approvers from approval chain
    try:
        pending_step_approvers = _get_pending_step_approvers(db, normalized.gmail_id, normalized.organization_id)
    except Exception as exc:
        logger.debug("Pending step approvers lookup failed: %s", exc)

    precedence = _resolve_action_precedence(
        normalized,
        ap_item_row,
        already_processed=bool(db.get_ap_audit_event_by_key(processed_key) or db.get_ap_audit_event_by_key(received_key)),
        pending_step_approvers=pending_step_approvers,
    )
    if precedence.status == "duplicate":
        _audit_callback_event(
            db,
            event_type="channel_action_duplicate",
            source="slack",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:duplicate",
            reason=precedence.reason,
            metadata={"action": normalized.to_dict()},
            correlation_id=normalized.correlation_id,
        )
        return _slack_duplicate_response()

    # Pre-dispatch race-free lock. Slack retries deliver the same
    # interactivity payload after 3s; two near-simultaneous workers
    # would both pass the precedence check above (TOCTOU on the
    # audit-row read) and both dispatch. The received-lock primitive
    # writes a sentinel via ``append_audit_event`` whose UNIQUE
    # constraint on ``idempotency_key`` ensures only ONE writer wins
    # per (channel, message_ts, action_id). Loser short-circuits.
    if not _try_acquire_received_lock(
        db,
        idempotency_key=received_key,
        organization_id=normalized.organization_id,
        ap_item_id=normalized.ap_item_id,
        actor_id=normalized.actor_id,
        correlation_id=normalized.correlation_id,
        source="slack",
        metadata={"action": normalized.to_dict()},
    ):
        return _slack_duplicate_response()

    if precedence.status == "stale":
        _audit_callback_event(
            db,
            event_type="channel_action_stale",
            source="slack",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:stale",
            reason=precedence.reason,
            metadata={"action": normalized.to_dict()},
            correlation_id=normalized.correlation_id,
        )
        return _slack_stale_response()

    if precedence.status == "blocked":
        _audit_callback_event(
            db,
            event_type="channel_action_blocked",
            source="slack",
            organization_id=normalized.organization_id,
            ap_item_id=normalized.ap_item_id,
            actor_id=normalized.actor_id,
            idempotency_key=f"{normalized.idempotency_key}:preflight_blocked",
            reason=precedence.reason,
            metadata={"action": normalized.to_dict()},
            correlation_id=normalized.correlation_id,
        )
        return {
            "response_type": "ephemeral",
            "text": f"Action not allowed: {precedence.reason}",
        }

    # ``channel_action_received`` was written above by
    # ``_try_acquire_received_lock`` with the same payload shape — no
    # second write needed here. Pre-fix this site wrote the same
    # row a second time, which collided on the UNIQUE
    # idempotency_key constraint and was silently swallowed by
    # ``_audit_callback_event``'s try/except.

    # §2: Enqueue approval event to durable queue
    try:
        from solden.core.events import AgentEvent, AgentEventType
        from solden.core.event_queue import get_event_queue
        approval_event = AgentEvent(
            type=AgentEventType.APPROVAL_RECEIVED,
            source="slack_callback",
            payload={
                "box_id": normalized.ap_item_id or "",
                "decision": normalized.action_type or "approved",
                "actor_email": normalized.actor_email or "",
                "actor_id": normalized.actor_id or "",
                "override_reason": normalized.override_reason if hasattr(normalized, "override_reason") else "",
                "gmail_id": normalized.gmail_id or "",
            },
            organization_id=assert_org_id(
                normalized.organization_id,
                context="slack_callback_event_enqueue",
            ),
            idempotency_key=normalized.idempotency_key,
        )
        get_event_queue().enqueue(approval_event)
    except Exception as eq_exc:
        logger.debug("[Slack] Event queue enqueue failed (non-fatal): %s", eq_exc)

    response_url = str(payload.get("response_url") or "").strip()
    if response_url:
        background_tasks.add_task(
            _complete_slack_action_via_response_url,
            normalized,
            processed_key,
            response_url,
        )
        return {
            "response_type": "ephemeral",
            "text": "Solden is processing this action…",
            "replace_original": False,
        }

    response = await _run_and_record_slack_action(normalized, processed_key)
    return {"response_type": response.get("response_type", "ephemeral"), "text": response.get("text", "Action received.")}


@legacy_router.post("/interactions")
async def handle_legacy_slack_interactions(request: Request, background_tasks: BackgroundTasks):
    """Backward-compatible alias for Slack apps configured from older manifests."""
    return await handle_invoice_interactive(request, background_tasks)


# ==================== CONVERSATIONAL QUERIES (§6.8) ====================


@router.post("/events")
async def handle_slack_events(request: Request, background_tasks: BackgroundTasks):
    """§6.8 Conversational Queries — AP team asks the agent questions in Slack.

    "What's our outstanding with AWS this month?" — agent returns live data.
    No slash commands — plain English. Agent responds in thread.

    All events MUST be signed by Slack. Pre-fix this route accepted
    unsigned JSON, letting any attacker fan out to ``_handle_mention_reply_sync``
    (writes ap_item timeline) and ``_handle_conversational_query``
    (sends messages on the org's bot token). The signature check is
    the same primitive ``/interactive`` already uses.
    """
    raw_body = await _require_slack_signature(request)
    try:
        body = json.loads(raw_body.decode("utf-8") if isinstance(raw_body, (bytes, bytearray)) else raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")

    # Slack URL verification challenge — Slack signs this request too,
    # so the verification above already gates it.
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    event = body.get("event") or {}
    event_type = event.get("type", "")

    # Only handle message events (not bot messages, not edits)
    if event_type != "message" or event.get("subtype") or event.get("bot_id"):
        return {"ok": True}

    text = str(event.get("text", "")).strip()
    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts") or event.get("ts", "")
    user_id = event.get("user", "")

    if not text or not channel:
        return {"ok": True}

    # §5.3: Check if this is a reply to an @mention DM — sync back to Box timeline
    message_metadata = event.get("metadata") or {}
    if (
        message_metadata.get("event_type") == "clearledgr_mention"
        or event.get("thread_ts")  # Reply in a thread
    ):
        background_tasks.add_task(
            _handle_mention_reply_sync,
            text=text,
            channel=channel,
            thread_ts=thread_ts,
            user_id=user_id,
            team_id=body.get("team_id", ""),
        )

    # Process the query in the background to respond within 3s
    background_tasks.add_task(
        _handle_conversational_query,
        text=text,
        channel=channel,
        thread_ts=thread_ts,
        user_id=user_id,
        team_id=body.get("team_id", ""),
    )

    return {"ok": True}


async def _handle_mention_reply_sync(
    *,
    text: str,
    channel: str,
    thread_ts: str,
    user_id: str,
    team_id: str,
):
    """§5.3: Sync Slack DM replies back to the Box timeline.

    "Their reply from Slack posts back to the Box timeline. Gmail and Slack
    stay in sync without requiring the user to check both platforms."
    """
    try:
        from solden.services.slack_api import resolve_slack_runtime

        # Find the parent message to get the ap_item_id from metadata.
        # M21 tenant-rename: previously fell back to ``resolve_slack_
        # runtime("default")`` when no org's team_id matched — silently
        # routing the reply to the legacy bucket. Fail closed instead:
        # if we can't resolve the team_id to a real tenant, drop the
        # reply on the floor (logged for ops follow-up).
        runtime = None
        db = get_db()
        orgs = db.list_organizations() if hasattr(db, "list_organizations") else []
        for org in orgs:
            org_id = str(org.get("id") or "").strip()
            if not org_id:
                continue
            rt = resolve_slack_runtime(org_id)
            if rt and rt.get("team_id") == team_id:
                runtime = rt
                break

        if not runtime or not runtime.get("token"):
            logger.warning(
                "[slack_thread_reply] no Solden tenant bound to "
                "team_id=%s — dropping reply (no fallback to legacy "
                "default bucket post-M20)",
                team_id,
            )
            return

        # Fetch the parent message to find the ap_item_id
        headers = {"Authorization": f"Bearer {runtime['token']}", "Content-Type": "application/json"}
        client = get_http_client()
        # Get conversation history for the thread parent
        resp = await client.get(
            "https://slack.com/api/conversations.history",
            params={"channel": channel, "latest": thread_ts, "inclusive": "true", "limit": "1"},
            headers=headers,
        )
        data = resp.json()
        messages = data.get("messages", [])
        if not messages:
            return

        parent = messages[0]
        metadata = parent.get("metadata") or {}
        event_payload = metadata.get("event_payload") or {}
        ap_item_id = event_payload.get("ap_item_id")

        if not ap_item_id:
            return

        # Look up user email for attribution
        user_email = user_id
        try:
            client = get_http_client()
            user_resp = await client.get(
                "https://slack.com/api/users.info",
                params={"user": user_id},
                headers=headers,
            )
            user_data = user_resp.json()
            if user_data.get("ok"):
                user_email = user_data["user"].get("profile", {}).get("email") or user_id
        except Exception:
            pass

        # Post the reply to the Box timeline
        from datetime import datetime, timezone
        db.append_ap_item_timeline_entry(ap_item_id, {
            "event_type": "slack_mention_reply",
            "summary": f"{user_email} replied via Slack: {text[:500]}",
            "actor": user_email,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("[mention_reply] synced reply from %s to ap_item %s", user_email, ap_item_id)

    except Exception as exc:
        logger.debug("[mention_reply] sync failed: %s", exc)


async def _handle_conversational_query(
    *,
    text: str,
    channel: str,
    thread_ts: str,
    user_id: str,
    team_id: str,
):
    """Process a natural language query from Slack and respond in thread."""
    try:
        from solden.services.slack_api import resolve_slack_runtime

        # Find the org for this Slack team. Pre-fix this iterated all
        # orgs, then fell back to the legacy "default" org if no
        # team→org match. M19 sweep: prefer the canonical
        # ``slack_installations`` index (single SQL lookup keyed by
        # team_id) and refuse — not fall back — when no install
        # exists. The fallback was a cross-tenant landmine: a Slack
        # workspace probing the bot would silently bind to the
        # platform tenant's runtime.
        runtime = None
        org_id = ""
        db = get_db()
        if hasattr(db, "get_slack_installation_by_team"):
            try:
                install = db.get_slack_installation_by_team(team_id)
                if install:
                    candidate_org = str(install.get("organization_id") or "").strip()
                    if candidate_org:
                        rt = resolve_slack_runtime(candidate_org)
                        if rt and rt.get("token"):
                            runtime = rt
                            org_id = candidate_org
            except Exception as exc:
                logger.warning("[conversational] team→org lookup failed for team=%s: %s", team_id, exc)

        if not runtime or not runtime.get("token") or not org_id:
            logger.warning(
                "[conversational] no Slack runtime / installation for team=%s; refusing query",
                team_id,
            )
            return

        # Build context from AP data + onboarding + audit trail
        items = db.list_ap_items(organization_id=org_id, limit=500)

        # Add vendor onboarding sessions for onboarding queries
        onboarding_sessions = []
        try:
            if hasattr(db, "list_pending_onboarding_sessions"):
                onboarding_sessions = db.list_pending_onboarding_sessions(org_id)
        except Exception:
            pass

        # Add recent audit events for timeline queries
        audit_events = []
        try:
            if hasattr(db, "list_recent_audit_events"):
                audit_events = db.list_recent_audit_events(org_id, limit=50)
            elif hasattr(db, "list_ap_audit_events"):
                audit_events = db.list_ap_audit_events(org_id, limit=50)
        except Exception:
            pass

        answer = await _answer_query_with_context(
            text, items, org_id,
            onboarding_sessions=onboarding_sessions,
            audit_events=audit_events,
        )

        # Post reply in thread
        headers = {"Authorization": f"Bearer {runtime['token']}", "Content-Type": "application/json"}
        payload = {
            "channel": channel,
            "thread_ts": thread_ts,
            "text": answer,
        }
        client = get_http_client()
        resp = await client.post("https://slack.com/api/chat.postMessage", json=payload, headers=headers, timeout=15)
        data = resp.json()
        if not data.get("ok"):
            logger.warning("[conversational] Slack reply failed: %s", data.get("error"))

    except Exception as exc:
        logger.error("[conversational] query handling failed: %s", exc)


async def _answer_query_with_context(
    query: str,
    items: list,
    org_id: str,
    onboarding_sessions: list = None,
    audit_events: list = None,
) -> str:
    """Use the model to answer a natural language AP query with full context."""
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _answer_query_rule_based(query, items)

    # Build rich AP context — thesis examples show individual invoice detail
    summary_lines = []
    for item in items[:100]:
        vendor = item.get("vendor_name") or item.get("vendor") or "Unknown"
        amount = float(item.get("amount") or 0)
        currency = item.get("currency") or "USD"
        state = item.get("state", "")
        due = item.get("due_date", "")[:10] if item.get("due_date") else ""
        ref = item.get("invoice_number", "")
        match = item.get("match_status") or ""
        exception = item.get("exception_reason") or item.get("exception_code") or ""
        erp_ref = item.get("erp_reference") or ""
        summary_lines.append(
            f"{vendor} | {ref} | {state} | {currency} {amount:.2f} | due:{due}"
            + (f" | match:{match}" if match else "")
            + (f" | exception:{exception}" if exception else "")
            + (f" | erp:{erp_ref}" if erp_ref else "")
        )

    context = "\n".join(summary_lines) if summary_lines else "No AP items found."

    # Add onboarding context for vendor status queries
    onboarding_context = ""
    if onboarding_sessions:
        ob_lines = []
        for s in (onboarding_sessions or [])[:20]:
            vn = s.get("vendor_name") or "Unknown"
            st = s.get("state") or ""
            inv_at = (s.get("invited_at") or "")[:10]
            chase = s.get("chase_count") or 0
            ob_lines.append(f"{vn} | {st} | invited:{inv_at} | chases:{chase}")
        if ob_lines:
            onboarding_context = "\n\nVENDOR ONBOARDING SESSIONS:\n" + "\n".join(ob_lines)

    # Add audit trail for timeline queries
    audit_context = ""
    if audit_events:
        ae_lines = []
        for e in (audit_events or [])[:30]:
            ts = (e.get("ts") or e.get("timestamp") or e.get("created_at") or "")[:19]
            etype = e.get("event_type") or ""
            actor = e.get("actor_id") or e.get("actor") or "agent"
            summary = e.get("summary") or e.get("reason") or ""
            vendor = e.get("vendor_name") or ""
            ae_lines.append(f"{ts} | {etype} | {actor} | {vendor} | {summary[:80]}")
        if ae_lines:
            audit_context = "\n\nRECENT AGENT ACTIONS:\n" + "\n".join(ae_lines)

    full_context = context + onboarding_context + audit_context

    try:
        from solden.core.llm_gateway import get_llm_gateway, LLMAction

        system_prompt = (
            "You are Solden's AP agent answering finance questions from the AP team in Slack.\n\n"
            "DATA FORMAT:\n"
            "AP ITEMS: vendor | invoice_ref | state | currency amount | due:date | match:status | exception:reason | erp:ref\n"
            "ONBOARDING: vendor | state | invited:date | chases:count\n"
            "AGENT ACTIONS: timestamp | event_type | actor | vendor | summary\n\n"
            "STATES: received, validated, needs_approval, approved, ready_to_post, posted_to_erp, "
            "closed, needs_info, failed_post, rejected, snoozed, reversed\n\n"
            "RESPONSE FORMAT — match these exact examples:\n"
            "- Outstanding query: 'AWS EMEA has 2 open invoices this month: INV-2840 (£8,922 — exception, awaiting your review) "
            "and INV-2843 (£4,200 — matched, due 18 April). Total outstanding: £13,122.'\n"
            "- Due query: '3 invoices due Friday 11 April: Deel HR BV £31,200 (approved, SEPA scheduled), "
            "Notion Labs £1,450 (pending your approval), Linear App £890 (matched, ready to approve).'\n"
            "- Onboarding query: 'Brex Inc. is at KYC stage — their certificate of incorporation has not been received. "
            "The agent chased them yesterday at 09:12. No response yet. Want me to escalate to their finance director?'\n"
            "- Timeline query: 'A condensed timeline of all autonomous actions in that window. "
            "Each line: timestamp, action, invoice or vendor, outcome.'\n\n"
            "RULES:\n"
            "- List EACH invoice individually with ref, amount, state description, and due date\n"
            "- Include currency symbols (£, $, €) from the data\n"
            "- Sum totals for outstanding/open queries\n"
            "- For onboarding: include stage, what's missing, last agent action, and offer to help\n"
            "- For timeline: list each action on its own line with timestamp\n"
            "- Be specific. Never say 'some invoices' — name them"
        )
        user_message = f"AP data ({len(summary_lines)} items):\n{full_context}\n\nQuestion: {query}"

        gateway = get_llm_gateway()
        llm_resp = await gateway.call(
            LLMAction.SLACK_QUERY,
            messages=[{"role": "user", "content": user_message}],
            system_prompt=system_prompt,
        )
        return str(llm_resp.content) if llm_resp.content else ""
    except Exception as exc:
        logger.warning("[conversational] the model call failed: %s", exc)
        return _answer_query_rule_based(query, items)


def _answer_query_rule_based(query: str, items: list) -> str:
    """Fallback rule-based answer when the model is unavailable.

    Produces thesis-quality responses with individual invoice detail.
    """
    q = query.lower()
    from datetime import timedelta
    from solden.core.clock import now_utc

    _state_labels = {
        "needs_approval": "pending your approval",
        "pending_approval": "pending your approval",
        "needs_info": "exception, awaiting review",
        "approved": "approved",
        "ready_to_post": "matched, ready to approve",
        "posted_to_erp": "posted to ERP",
        "closed": "closed",
        "failed_post": "ERP posting failed",
    }

    if "outstanding" in q or "open" in q:
        from solden.core.money import money_sum, to_decimal
        # Find vendor if mentioned
        for item in items:
            vendor = (item.get("vendor_name") or "").lower()
            if vendor and vendor in q:
                vendor_items = [i for i in items if (i.get("vendor_name") or "").lower() == vendor and i.get("state") not in ("closed", "rejected", "posted_to_erp")]
                if not vendor_items:
                    return f"No open items with {item.get('vendor_name')} this month."
                total = money_sum(i.get("amount") for i in vendor_items)
                currency = vendor_items[0].get("currency") or "USD"
                lines = [f"{item.get('vendor_name')} has {len(vendor_items)} open invoice(s) this month:"]
                for vi in vendor_items[:5]:
                    ref = vi.get("invoice_number") or "N/A"
                    amt = to_decimal(vi.get("amount"))
                    state_desc = _state_labels.get(vi.get("state"), vi.get("state", ""))
                    due = vi.get("due_date", "")[:10]
                    lines.append(f"  {ref} ({currency} {amt:,.2f} — {state_desc}" + (f", due {due}" if due else "") + ")")
                lines.append(f"Total outstanding: {currency} {total:,.2f}.")
                return "\n".join(lines)

        open_items = [i for i in items if i.get("state") not in ("closed", "rejected", "posted_to_erp")]
        total = money_sum(i.get("amount") for i in open_items)
        return f"{len(open_items)} open items totalling {total:,.0f}."

    if "due" in q:
        week_end_iso = (now_utc() + timedelta(days=7)).isoformat()[:10]
        due_items = [
            i for i in items
            if i.get("due_date")
            and i.get("due_date")[:10] <= week_end_iso
            and i.get("state") not in ("closed", "rejected")
        ]
        due_items.sort(key=lambda i: i.get("due_date") or "")
        if not due_items:
            return "No invoices with upcoming due dates."
        lines = [f"{len(due_items)} invoices with due dates:"]
        for di in due_items[:5]:
            vendor = di.get("vendor_name") or "Unknown"
            amt = float(di.get("amount") or 0)
            currency = di.get("currency") or "USD"
            due = di.get("due_date", "")[:10]
            state_desc = _state_labels.get(di.get("state"), di.get("state", ""))
            lines.append(f"  {vendor} {currency} {amt:,.2f} ({state_desc}" + (f", due {due}" if due else "") + ")")
        return "\n".join(lines)

    if "onboarding" in q:
        return "Check the Vendor Onboarding pipeline for current onboarding status. I can answer more specifically if you name the vendor."

    return f"I found {len(items)} AP items. Try asking about a specific vendor, due dates, or outstanding amounts."
