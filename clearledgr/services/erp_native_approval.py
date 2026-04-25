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

    target = _resolve_approval_target(db, organization_id, ap_item)
    channel = target.get("channel") or ""
    mentions = target.get("mentions") or []
    if not channel:
        logger.warning(
            "erp_native_approval: no Slack channel configured for org=%s — skipping route",
            organization_id,
        )
        return {"ok": False, "reason": "no_slack_channel"}

    blocks = _build_approval_blocks(ap_item, mentions=mentions)
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
        metadata={
            "channel_id": channel_id,
            "thread_ts": ts,
            "approver_count": len(mentions),
            "matched_threshold": target.get("threshold_label"),
        },
    )
    return {
        "ok": True,
        "channel": channel_id,
        "ts": ts,
        "approver_count": len(mentions),
    }


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

    # 1. ERP-side write-back — release the payment hold/block. Each
    # supported ERP gets its own write-back module; we dispatch by
    # source so the Slack handler is ERP-agnostic.
    if source == "netsuite_native":
        ns_internal_id = str(item.get("erp_reference") or "").strip()
        if not ns_internal_id:
            return {"ok": False, "reason": "missing_ns_internal_id", "ap_item_id": ap_item_id}
        release = await release_netsuite_payment_hold(
            organization_id=organization_id,
            ns_internal_id=ns_internal_id,
        )
    elif source == "sap_native":
        from clearledgr.integrations.erp_sap_s4hana import release_payment_block as release_sap_block
        cc = str(metadata.get("sap_company_code") or "").strip()
        doc = str(metadata.get("sap_supplier_invoice") or "").strip()
        fy = str(metadata.get("sap_fiscal_year") or "").strip()
        if not (cc and doc and fy):
            return {
                "ok": False,
                "reason": "missing_sap_composite_key",
                "ap_item_id": ap_item_id,
                "have": {"cc": cc, "doc": doc, "fy": fy},
            }
        release = await release_sap_block(
            organization_id=organization_id,
            company_code=cc,
            supplier_invoice=doc,
            fiscal_year=fy,
        )
    else:
        return {
            "ok": False,
            "reason": "unsupported_source",
            "ap_item_id": ap_item_id,
            "source": source,
        }

    if not release.get("ok"):
        # ERP rejected the hold release — leave the Box in
        # needs_approval and surface the reason. We don't transition to
        # needs_info because the operator's decision was valid; the
        # failure is on the ERP side and is retryable.
        _record_audit(
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            action=f"approve_failed_at_{source.replace('_native', '')}",
            metadata={"reason": release.get("reason"), "source": source, "detail": {k: v for k, v in release.items() if k != "ok"}},
        )
        return {
            "ok": False,
            "reason": f"{source}_release_failed",
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
            "erp_reference": item.get("erp_reference"),
            "final_state": last_state,
            "source": source,
            "release_op": release.get("op"),
        },
    )
    return {
        "ok": True,
        "ap_item_id": ap_item_id,
        "state": last_state,
        "erp_payment_block_released": True,
        "source": source,
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

    metadata = item.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    source = str(metadata.get("source") or "").strip().lower()

    actor_id = str(actor.get("actor_id") or actor.get("user_id") or "slack_user").strip()
    actor_email = str(actor.get("actor_email") or actor.get("email") or "").strip() or None
    void_memo = f"Voided via Clearledgr — rejected by {actor_email or actor_id}"

    # 1. Void/cancel in the ERP. Dispatch by source — same pattern as
    # _handle_approve.
    if source == "netsuite_native":
        ns_internal_id = str(item.get("erp_reference") or "").strip()
        if not ns_internal_id:
            return {"ok": False, "reason": "missing_ns_internal_id", "ap_item_id": ap_item_id}
        void_result = await void_netsuite_bill(
            organization_id=organization_id,
            ns_internal_id=ns_internal_id,
            memo=void_memo,
        )
    elif source == "sap_native":
        from clearledgr.integrations.erp_sap_s4hana import cancel_supplier_invoice as cancel_sap_invoice
        cc = str(metadata.get("sap_company_code") or "").strip()
        doc = str(metadata.get("sap_supplier_invoice") or "").strip()
        fy = str(metadata.get("sap_fiscal_year") or "").strip()
        if not (cc and doc and fy):
            return {
                "ok": False,
                "reason": "missing_sap_composite_key",
                "ap_item_id": ap_item_id,
                "have": {"cc": cc, "doc": doc, "fy": fy},
            }
        void_result = await cancel_sap_invoice(
            organization_id=organization_id,
            company_code=cc,
            supplier_invoice=doc,
            fiscal_year=fy,
            reason_text=void_memo,
        )
    else:
        return {"ok": False, "reason": "unsupported_source", "ap_item_id": ap_item_id, "source": source}

    if not void_result.get("ok"):
        _record_audit(
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            action=f"reject_failed_at_{source.replace('_native', '')}",
            metadata={
                "reason": void_result.get("reason"),
                "status_code": void_result.get("status_code"),
                "source": source,
                "actor_id": actor_id,
                "erp_reference": item.get("erp_reference"),
            },
        )
        return {
            "ok": False,
            "reason": f"{source}_void_failed",
            "ap_item_id": ap_item_id,
            "detail": void_result,
        }

    # 2. Walk: needs_approval → rejected → closed.
    last_state = current_state
    for target in [APState.REJECTED.value, APState.CLOSED.value]:
        if not validate_transition(last_state, target):
            return {"ok": False, "reason": "invalid_transition", "from": last_state, "to": target}
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
            # State transition failed AFTER NetSuite was already voided.
            # Surface this clearly — the AP team needs to know the bill
            # is voided in NetSuite even though Clearledgr's Box may be
            # stuck. Manual reconciliation step.
            _record_audit(
                organization_id=organization_id,
                ap_item_id=ap_item_id,
                action="reject_clearledgr_state_failed_after_netsuite_voided",
                metadata={
                    "from": last_state,
                    "to": target,
                    "error": str(exc),
                    "ns_internal_id": ns_internal_id,
                },
            )
            return {
                "ok": False,
                "reason": "state_transition_failed_after_void",
                "from": last_state,
                "to": target,
                "error": str(exc),
                "erp_voided": True,
                "source": source,
            }
        last_state = target

    _record_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        action="rejected",
        metadata={
            "actor_id": actor_id,
            "actor_email": actor_email,
            "source": source,
            "erp_voided": True,
            "erp_reference": item.get("erp_reference"),
            "void_op": void_result.get("op"),
        },
    )
    return {
        "ok": True,
        "ap_item_id": ap_item_id,
        "state": last_state,
        "erp_voided": True,
        "source": source,
    }


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
    return await _netsuite_bill_request(
        organization_id=organization_id,
        ns_internal_id=ns_internal_id,
        method="PATCH",
        path_suffix="",
        body={"paymentHold": False},
        op_label="payment_hold_release",
    )


async def void_netsuite_bill(
    *,
    organization_id: str,
    ns_internal_id: str,
    memo: Optional[str] = None,
) -> Dict[str, Any]:
    """Void a NetSuite Vendor Bill on Slack reject.

    NetSuite's REST API exposes a transaction-action endpoint at
    ``POST /services/rest/record/v1/vendorBill/<id>/!transform/void``
    that flips the bill to a voided state without hard-deleting it (the
    record stays visible to AP for audit + the GL impact reverses
    cleanly). This is preferred over PATCH-with-{voided:true} because
    NetSuite owns the void semantics — including reversing journal
    impact, cancelling pending payments, and writing the void in the
    transaction's status history.

    If the !transform/void action fails (common when the account doesn't
    expose it via REST), we fall back to PATCH with ``{voided: true}``.
    Either way: best-effort. If both fail we surface the error so the
    Slack handler can tell the operator the void didn't apply, and the
    Box still moves to ``rejected → closed`` on the Clearledgr side.
    """
    primary = await _netsuite_bill_request(
        organization_id=organization_id,
        ns_internal_id=ns_internal_id,
        method="POST",
        path_suffix="/!transform/void",
        body={"memo": memo} if memo else None,
        op_label="void_transform",
    )
    if primary.get("ok"):
        return primary

    # Fallback: direct PATCH on the record. Some NetSuite configurations
    # don't enable the !transform/void action over REST and only accept
    # the field-level write.
    fallback_body: Dict[str, Any] = {"voided": True}
    if memo:
        fallback_body["memo"] = memo
    fallback = await _netsuite_bill_request(
        organization_id=organization_id,
        ns_internal_id=ns_internal_id,
        method="PATCH",
        path_suffix="",
        body=fallback_body,
        op_label="void_patch",
    )
    if fallback.get("ok"):
        return fallback

    # Both failed — return the more informative of the two errors.
    return {
        "ok": False,
        "reason": "void_failed",
        "primary_error": {k: v for k, v in primary.items() if k != "ok"},
        "fallback_error": {k: v for k, v in fallback.items() if k != "ok"},
    }


async def _netsuite_bill_request(
    *,
    organization_id: str,
    ns_internal_id: str,
    method: str,
    path_suffix: str,
    body: Optional[Dict[str, Any]],
    op_label: str,
) -> Dict[str, Any]:
    """Shared NetSuite TBA REST helper for vendor-bill writes.

    All ERP-native write-back paths funnel through here so OAuth, error
    handling, and 4xx surfacing stay consistent.
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
        return {"ok": False, "op": op_label, "reason": "erp_connection_lookup_failed", "error": str(exc)}

    if connection is None:
        return {"ok": False, "op": op_label, "reason": "no_netsuite_connection"}
    if not connection.account_id:
        return {"ok": False, "op": op_label, "reason": "missing_account_id"}

    url = (
        f"https://{connection.account_id}.suitetalk.api.netsuite.com"
        f"/services/rest/record/v1/vendorBill/{ns_internal_id}{path_suffix}"
    )

    try:
        auth_header = build_netsuite_oauth_header(connection, method, url)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "op": op_label, "reason": "oauth_header_failed", "error": str(exc)}

    client = get_http_client()
    headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        if body is not None:
            response = await client.request(method, url, headers=headers, json=body, timeout=30)
        else:
            # !transform/void with no body — send empty object so NetSuite
            # parses it as JSON and not chunked text.
            response = await client.request(method, url, headers=headers, json={}, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "op": op_label, "reason": "request_failed", "error": str(exc)}

    if response.status_code >= 400:
        snippet = ""
        try:
            snippet = response.text[:500]
        except Exception:
            snippet = ""
        return {
            "ok": False,
            "op": op_label,
            "reason": "netsuite_error",
            "status_code": response.status_code,
            "body": snippet,
        }

    return {
        "ok": True,
        "op": op_label,
        "ns_internal_id": ns_internal_id,
        "status_code": response.status_code,
    }


# ─── Helpers ────────────────────────────────────────────────────────


def _slack_client() -> SlackAPIClient:
    return get_slack_client()


def _resolve_approval_target(
    db: Any,
    organization_id: str,
    ap_item: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve the right Slack channel + per-amount approvers for this bill.

    Reads from the org's ``settings_json.approval_thresholds`` — the same
    config the email-arrival path uses (see
    :meth:`InvoiceWorkflowService.get_approval_target_for_amount`). Each
    threshold has ``min_amount`` / ``max_amount`` and optionally
    ``vendors`` / ``entities`` filters plus an ``approver_targets`` list
    of ``{slack_user_id, email, display_name}``.

    For ERP-native bills we don't have GL code or department info on the
    payload (NetSuite doesn't always expose these), so amount + vendor +
    entity filters are the matching dimensions.

    Falls back through:

    1. The matching threshold's ``channel`` + ``approver_targets``
    2. The org's default ``settings_json.slack_channels.approvals``
    3. ``SLACK_APPROVAL_CHANNEL`` / ``SLACK_CHANNEL`` env vars

    Returns ``{channel, mentions, threshold_label}`` where ``mentions``
    is a list of ``"<@SLACK_USER_ID>"`` ready to drop into block text.
    """
    settings = _load_org_settings(db, organization_id)
    default_channel = _default_channel_from_settings(settings)

    amount = _coerce_amount(ap_item.get("amount"))
    vendor_lower = str(ap_item.get("vendor_name") or "").strip().lower()
    entity_lower = str(ap_item.get("entity_id") or "").strip().lower()

    thresholds = (settings or {}).get("approval_thresholds") or []
    matched: Optional[Dict[str, Any]] = None
    for rule in thresholds if isinstance(thresholds, list) else []:
        if not isinstance(rule, dict):
            continue
        try:
            min_amt = float(rule.get("min_amount") or 0)
        except (TypeError, ValueError):
            min_amt = 0.0
        max_amt_raw = rule.get("max_amount")
        try:
            max_amt = float(max_amt_raw) if max_amt_raw not in (None, "") else None
        except (TypeError, ValueError):
            max_amt = None
        if amount is None:
            # No amount → can only match a rule with no upper bound
            if max_amt is not None:
                continue
        else:
            if amount < min_amt:
                continue
            if max_amt is not None and amount >= max_amt:
                continue
        # Vendor filter
        rule_vendors = [str(v).strip().lower() for v in (rule.get("vendors") or []) if v]
        if rule_vendors and vendor_lower and vendor_lower not in rule_vendors:
            continue
        # Entity filter
        rule_entities = [str(e).strip().lower() for e in (rule.get("entities") or []) if e]
        if rule_entities and entity_lower and entity_lower not in rule_entities:
            continue
        matched = rule
        break

    if matched is None:
        return {
            "channel": default_channel,
            "mentions": [],
            "threshold_label": None,
        }

    channel = str(matched.get("channel") or "").strip() or default_channel
    raw_targets = matched.get("approver_targets") or []
    mentions: list = []
    for target in raw_targets if isinstance(raw_targets, list) else []:
        if not isinstance(target, dict):
            continue
        slack_user_id = str(target.get("slack_user_id") or "").strip()
        if slack_user_id:
            mentions.append(f"<@{slack_user_id}>")
    return {
        "channel": channel,
        "mentions": mentions,
        "threshold_label": str(matched.get("label") or matched.get("name") or "").strip() or None,
    }


def _load_org_settings(db: Any, organization_id: str) -> Dict[str, Any]:
    if not hasattr(db, "get_organization"):
        return {}
    try:
        org = db.get_organization(organization_id)
    except Exception:
        return {}
    if not org:
        return {}
    settings = org.get("settings_json") or org.get("settings")
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            return {}
    return settings if isinstance(settings, dict) else {}


def _default_channel_from_settings(settings: Dict[str, Any]) -> Optional[str]:
    import os
    channels = (settings or {}).get("slack_channels") or {}
    if isinstance(channels, dict):
        for key in ("approvals", "default", "ap"):
            value = str(channels.get(key) or "").strip()
            if value:
                return value
    return (
        os.getenv("SLACK_APPROVAL_CHANNEL")
        or os.getenv("SLACK_CHANNEL")
        or ""
    ).strip() or None


def _coerce_amount(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_approval_blocks(ap_item: Dict[str, Any], *, mentions: Optional[list] = None) -> list:
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
    erp_reference = str(ap_item.get("erp_reference") or "")
    metadata = ap_item.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    source = str((metadata or {}).get("source") or "").strip().lower()
    if source == "sap_native":
        erp_label = "SAP supplier invoice"
        ref_label = "Doc"
        block_label = "payment block"
    else:
        erp_label = "NetSuite bill"
        ref_label = "NetSuite ID"
        block_label = "payment hold"
    mentions = mentions or []

    blocks: list = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{erp_label} awaiting approval"},
        }
    ]

    # If specific approvers are configured for this amount band, mention
    # them at the top so Slack pings them directly. Falls through cleanly
    # when mentions is empty (channel-only routing).
    if mentions:
        mention_text = " ".join(mentions)
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{mention_text} — your approval is requested.",
            },
        })

    blocks.extend([
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
                        f"_{ref_label} `{erp_reference}` · {block_label} set in {erp_label.split()[0]}. "
                        f"Approve here to release the {block_label}; reject here to cancel the document._"
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
                    "text": {"type": "plain_text", "text": "Reject & void"},
                    "style": "danger",
                    "action_id": f"{SLACK_ACTION_REJECT}_{ap_item_id}",
                    "value": json.dumps({"ap_item_id": ap_item_id, "decision": "reject"}),
                },
            ],
        },
    ])
    return blocks


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
