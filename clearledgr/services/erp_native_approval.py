"""ERP-native bill approval — Slack routing + NetSuite write-back.

Phase 2 of the write-direction loop.

When an ERP-native bill arrives via the NetSuite SuiteScript webhook
with a payment hold (see :mod:`clearledgr.services.erp_webhook_dispatch`),
the AP item enters at ``needs_approval``. This module builds the Slack
approval card, posts it to the org's approval channel, and on approve:

1. Calls NetSuite REST API to clear the bill's ``paymentHold`` flag.
2. Transitions the Box from ``needs_approval`` through ``approved`` →
   ``ready_to_post`` → ``posted_to_erp`` (the bill is already in
   NetSuite, so we don't go through the actual ERP-post path; we just
   advance the state machine to reflect that the bill is now live).
3. Audits each step with ``erp_native_approval.*`` event types so the
   panel timeline shows the full lifecycle.

On reject: transition to ``rejected`` → ``closed``. Phase 3 (deferred)
will optionally void the NetSuite bill on reject; for now reject just
records the Clearledgr-side decision and leaves the bill in NetSuite
for the AP team to handle manually.

The Slack callback comes back through the existing
``/slack/invoices/actions`` endpoint with action IDs prefixed
``cl_erp_approve_`` / ``cl_erp_reject_``. The endpoint hands ERP-native
actions to :func:`handle_slack_decision` here; the existing Gmail-bound
``approve_invoice`` action handler is untouched.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from clearledgr.core.ap_states import APState, validate_transition
from clearledgr.core.database import get_db
from clearledgr.core.http_client import get_http_client
from clearledgr.integrations.erp_router import (
    ERPConnection,
    _erp_connection_from_row,
    build_netsuite_oauth_header,
)
from clearledgr.services.slack_api import SlackAPIClient, get_slack_client

logger = logging.getLogger(__name__)


SLACK_ACTION_APPROVE = "cl_erp_approve"
SLACK_ACTION_REJECT = "cl_erp_reject"


# ─── Public entrypoints ─────────────────────────────────────────────


async def route_for_approval(ap_item: Dict[str, Any]) -> Dict[str, Any]:
    """Post a Slack approval card for an ERP-native AP item.

    Idempotent: if a slack_thread already exists on this AP item, returns
    the existing thread without re-posting (prevents duplicate cards if
    the dispatcher re-runs).

    Returns ``{"ok": True, "channel": ..., "ts": ...}`` on success.
    """
    ap_item_id = str(ap_item.get("id") or "").strip()
    organization_id = str(ap_item.get("organization_id") or "default").strip() or "default"
    if not ap_item_id:
        return {"ok": False, "reason": "missing_ap_item_id"}

    db = get_db()
    if hasattr(db, "get_slack_thread"):
        try:
            existing = db.get_slack_thread(ap_item_id)
            if existing and existing.get("thread_ts"):
                return {
                    "ok": True,
                    "noop": "already_routed",
                    "channel": existing.get("channel_id"),
                    "ts": existing.get("thread_ts"),
                }
        except Exception:
            pass

    channel = _resolve_approval_channel(db, organization_id)
    if not channel:
        logger.warning(
            "erp_native_approval: no Slack channel configured for org=%s — skipping route",
            organization_id,
        )
        return {"ok": False, "reason": "no_slack_channel"}

    blocks = _build_approval_blocks(ap_item)
    fallback_text = _build_fallback_text(ap_item)

    client = _slack_client()
    try:
        message = await client.send_message(channel=channel, text=fallback_text, blocks=blocks)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "erp_native_approval: Slack send failed ap_item=%s — %s",
            ap_item_id, exc,
        )
        return {"ok": False, "reason": "slack_send_failed", "error": str(exc)}

    ts = getattr(message, "ts", None) or ""
    channel_id = getattr(message, "channel", None) or channel

    # Persist the slack thread linkage so the callback handler can look
    # up the AP item by message_ts later, and so re-runs of this routine
    # are no-ops.
    if hasattr(db, "save_slack_thread"):
        try:
            db.save_slack_thread(
                ap_item_id=ap_item_id,
                gmail_id=str(ap_item.get("thread_id") or ap_item_id),
                channel_id=channel_id,
                thread_ts=ts,
                organization_id=organization_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "erp_native_approval: save_slack_thread failed ap_item=%s — %s",
                ap_item_id, exc,
            )

    _record_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        action="routed",
        metadata={"channel_id": channel_id, "thread_ts": ts},
    )
    return {"ok": True, "channel": channel_id, "ts": ts}


async def handle_slack_decision(
    *,
    ap_item_id: str,
    decision: str,
    actor: Dict[str, Any],
) -> Dict[str, Any]:
    """Route a Slack approve/reject click into the ERP-native business logic.

    Called from the existing Slack actions endpoint when it sees an
    action_id prefixed ``cl_erp_approve_`` or ``cl_erp_reject_``.

    Always returns a dict — never raises. The Slack handler wraps the
    response in an ephemeral message back to the user.
    """
    db = get_db()
    item = db.get_ap_item(ap_item_id) if hasattr(db, "get_ap_item") else None
    if not item:
        return {"ok": False, "reason": "ap_item_not_found", "ap_item_id": ap_item_id}
    organization_id = str(item.get("organization_id") or "default").strip() or "default"
    decision_norm = str(decision or "").strip().lower()

    if decision_norm == "approve":
        return await _handle_approve(db, item, organization_id, actor)
    if decision_norm == "reject":
        return await _handle_reject(db, item, organization_id, actor)
    return {"ok": False, "reason": "unknown_decision", "decision": decision}


# ─── Approve / Reject internals ─────────────────────────────────────


async def _handle_approve(
    db: Any,
    item: Dict[str, Any],
    organization_id: str,
    actor: Dict[str, Any],
) -> Dict[str, Any]:
    ap_item_id = str(item.get("id") or "").strip()
    current_state = str(item.get("state") or "").strip().lower()
    if current_state != APState.NEEDS_APPROVAL.value:
        return {
            "ok": False,
            "reason": "not_in_needs_approval",
            "ap_item_id": ap_item_id,
            "state": current_state,
        }

    metadata = item.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    source = str(metadata.get("source") or "").strip().lower()
    if source != "netsuite_native":
        return {
            "ok": False,
            "reason": "not_erp_native",
            "ap_item_id": ap_item_id,
            "source": source,
        }

    ns_internal_id = str(item.get("erp_reference") or "").strip()
    if not ns_internal_id:
        return {"ok": False, "reason": "missing_ns_internal_id", "ap_item_id": ap_item_id}

    # 1. Release the NetSuite payment hold
    release = await release_netsuite_payment_hold(
        organization_id=organization_id,
        ns_internal_id=ns_internal_id,
    )
    if not release.get("ok"):
        # NetSuite rejected the hold release — leave the Box in
        # needs_approval and surface the reason. We don't transition to
        # needs_info because the operator's decision was valid; the
        # failure is on the ERP side and is retryable.
        _record_audit(
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            action="approve_failed_at_netsuite",
            metadata={"reason": release.get("reason"), "ns_internal_id": ns_internal_id},
        )
        return {
            "ok": False,
            "reason": "netsuite_hold_release_failed",
            "ap_item_id": ap_item_id,
            "detail": release,
        }

    # 2. Walk the Box state machine: needs_approval → approved →
    # ready_to_post → posted_to_erp. Each transition is a separate
    # update_ap_item call so each gets its own audit event for a clean
    # timeline. If any individual transition fails (validate_transition
    # rejects), stop and surface the offending step.
    actor_id = str(actor.get("actor_id") or actor.get("user_id") or "slack_user").strip()
    actor_email = str(actor.get("actor_email") or actor.get("email") or "").strip() or None
    chain = [APState.APPROVED.value, APState.READY_TO_POST.value, APState.POSTED_TO_ERP.value]
    last_state = current_state
    for target in chain:
        if not validate_transition(last_state, target):
            return {
                "ok": False,
                "reason": "invalid_transition",
                "ap_item_id": ap_item_id,
                "from": last_state,
                "to": target,
            }
        try:
            db.update_ap_item(
                ap_item_id,
                state=target,
                _actor_type="slack_approval",
                _actor_id=actor_id,
                **(
                    {"approved_by": actor_email or actor_id} if target == APState.APPROVED.value else {}
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "erp_native_approval: state transition %s → %s failed: %s",
                last_state, target, exc,
            )
            return {
                "ok": False,
                "reason": "state_transition_failed",
                "ap_item_id": ap_item_id,
                "from": last_state,
                "to": target,
                "error": str(exc),
            }
        last_state = target

    _record_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        action="approved",
        metadata={
            "actor_id": actor_id,
            "actor_email": actor_email,
            "ns_internal_id": ns_internal_id,
            "final_state": last_state,
            "source": "slack_erp_native",
        },
    )
    return {
        "ok": True,
        "ap_item_id": ap_item_id,
        "state": last_state,
        "ns_payment_hold_released": True,
    }


async def _handle_reject(
    db: Any,
    item: Dict[str, Any],
    organization_id: str,
    actor: Dict[str, Any],
) -> Dict[str, Any]:
    ap_item_id = str(item.get("id") or "").strip()
    current_state = str(item.get("state") or "").strip().lower()
    if current_state != APState.NEEDS_APPROVAL.value:
        return {"ok": False, "reason": "not_in_needs_approval", "ap_item_id": ap_item_id, "state": current_state}

    actor_id = str(actor.get("actor_id") or actor.get("user_id") or "slack_user").strip()
    actor_email = str(actor.get("actor_email") or actor.get("email") or "").strip() or None
    # Walk: needs_approval → rejected → closed (rejected is non-terminal
    # in our state machine; only `closed` is terminal for the rejected
    # branch).
    for target in [APState.REJECTED.value, APState.CLOSED.value]:
        if not validate_transition(current_state, target):
            return {"ok": False, "reason": "invalid_transition", "from": current_state, "to": target}
        try:
            db.update_ap_item(
                ap_item_id,
                state=target,
                _actor_type="slack_approval",
                _actor_id=actor_id,
                **(
                    {"rejected_by": actor_email or actor_id, "rejection_reason": "slack_reject_erp_native"}
                    if target == APState.REJECTED.value else {}
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "state_transition_failed", "from": current_state, "to": target, "error": str(exc)}
        current_state = target

    _record_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        action="rejected",
        metadata={"actor_id": actor_id, "actor_email": actor_email, "source": "slack_erp_native"},
    )
    return {"ok": True, "ap_item_id": ap_item_id, "state": current_state}


# ─── NetSuite hold release ──────────────────────────────────────────


async def release_netsuite_payment_hold(
    *,
    organization_id: str,
    ns_internal_id: str,
) -> Dict[str, Any]:
    """Clear the ``paymentHold`` flag on a NetSuite Vendor Bill.

    Uses the same TBA OAuth-1.0 path :mod:`erp_netsuite` already uses
    for journal-entry posting. The NetSuite REST endpoint for vendor
    bills accepts a PATCH against the record URL; we send the minimal
    body ``{"paymentHold": false}``.
    """
    db = get_db()
    connection: Optional[ERPConnection] = None
    try:
        if hasattr(db, "get_erp_connections"):
            for row in db.get_erp_connections(organization_id):
                if str(row.get("erp_type") or "").lower() == "netsuite":
                    connection = _erp_connection_from_row(row)
                    break
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "erp_connection_lookup_failed", "error": str(exc)}

    if connection is None:
        return {"ok": False, "reason": "no_netsuite_connection"}
    if not connection.account_id:
        return {"ok": False, "reason": "missing_account_id"}

    url = (
        f"https://{connection.account_id}.suitetalk.api.netsuite.com"
        f"/services/rest/record/v1/vendorBill/{ns_internal_id}"
    )
    body = {"paymentHold": False}

    try:
        auth_header = build_netsuite_oauth_header(connection, "PATCH", url)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "oauth_header_failed", "error": str(exc)}

    client = get_http_client()
    try:
        response = await client.request(
            "PATCH",
            url,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json=body,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "request_failed", "error": str(exc)}

    if response.status_code >= 400:
        snippet = ""
        try:
            snippet = response.text[:500]
        except Exception:
            snippet = ""
        return {
            "ok": False,
            "reason": "netsuite_error",
            "status_code": response.status_code,
            "body": snippet,
        }

    return {"ok": True, "ns_internal_id": ns_internal_id, "status_code": response.status_code}


# ─── Helpers ────────────────────────────────────────────────────────


def _slack_client() -> SlackAPIClient:
    return get_slack_client()


def _resolve_approval_channel(db: Any, organization_id: str) -> Optional[str]:
    """Look up the org's primary approval channel.

    Falls back to ``SLACK_APPROVAL_CHANNEL`` env var if the org's
    settings_json doesn't carry one, then to ``SLACK_CHANNEL``.
    """
    import os
    if hasattr(db, "get_organization"):
        try:
            org = db.get_organization(organization_id)
            if org:
                settings = org.get("settings_json") or org.get("settings")
                if isinstance(settings, str):
                    try:
                        settings = json.loads(settings)
                    except Exception:
                        settings = {}
                channels = (settings or {}).get("slack_channels") or {}
                approval = (channels.get("approvals") or channels.get("default") or "").strip()
                if approval:
                    return approval
        except Exception:
            pass
    return (
        os.getenv("SLACK_APPROVAL_CHANNEL")
        or os.getenv("SLACK_CHANNEL")
        or ""
    ).strip() or None


def _build_approval_blocks(ap_item: Dict[str, Any]) -> list:
    ap_item_id = str(ap_item.get("id") or "").strip()
    vendor = str(ap_item.get("vendor_name") or "Unknown vendor")
    invoice_no = str(ap_item.get("invoice_number") or "—")
    amount_raw = ap_item.get("amount")
    currency = str(ap_item.get("currency") or "USD").upper()
    try:
        amount = f"{currency} {float(amount_raw):,.2f}"
    except (TypeError, ValueError):
        amount = f"{currency} {amount_raw}"
    due_date = str(ap_item.get("due_date") or "—")
    ns_internal_id = str(ap_item.get("erp_reference") or "")

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "NetSuite bill awaiting approval"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Vendor*\n{vendor}"},
                {"type": "mrkdwn", "text": f"*Amount*\n{amount}"},
                {"type": "mrkdwn", "text": f"*Invoice #*\n{invoice_no}"},
                {"type": "mrkdwn", "text": f"*Due*\n{due_date}"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"_NetSuite ID `{ns_internal_id}` · payment hold set in NetSuite. "
                        "Approve here to release the hold and let the bill flow to payment._"
                    ),
                }
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": f"{SLACK_ACTION_APPROVE}_{ap_item_id}",
                    "value": json.dumps({"ap_item_id": ap_item_id, "decision": "approve"}),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": f"{SLACK_ACTION_REJECT}_{ap_item_id}",
                    "value": json.dumps({"ap_item_id": ap_item_id, "decision": "reject"}),
                },
            ],
        },
    ]


def _build_fallback_text(ap_item: Dict[str, Any]) -> str:
    vendor = ap_item.get("vendor_name") or "vendor"
    invoice_no = ap_item.get("invoice_number") or ap_item.get("erp_reference") or ""
    amount = ap_item.get("amount") or "?"
    currency = (ap_item.get("currency") or "USD").upper()
    return f"NetSuite bill from {vendor} ({invoice_no}) — {currency} {amount} — needs approval."


def _record_audit(
    *,
    organization_id: str,
    ap_item_id: str,
    action: str,
    metadata: Dict[str, Any],
) -> None:
    db = get_db()
    if not hasattr(db, "record_audit_event"):
        return
    try:
        db.record_audit_event(
            actor_id=metadata.get("actor_id") or "slack_erp_native",
            actor_type="slack_approval",
            action=f"erp_native_approval.{action}",
            box_id=ap_item_id,
            box_type="ap_item",
            entity_type="ap_item",
            entity_id=ap_item_id,
            organization_id=organization_id,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "erp_native_approval: audit write failed for %s — %s",
            ap_item_id, exc,
        )
