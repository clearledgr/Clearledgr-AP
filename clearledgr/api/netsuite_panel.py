"""Backend endpoint for the NetSuite SuiteApp panel.

The SuiteApp under ``integrations/netsuite-suiteapp/`` injects an iframe
into the NetSuite Vendor Bill record. The iframe loads a Suitelet which
mints a short-lived HMAC-signed JWT and embeds it in a ``<meta>`` tag.
The panel JS reads the JWT and calls back here to fetch the Box state
(state + timeline + exceptions + outcome) for that bill.

Two auth paths are supported:

1. **HMAC-signed JWT (Phase 3, production):** Suitelet signs the panel
   token with a per-tenant ``panel_secret`` stored in the NetSuite
   custom record ``customrecord_cl_settings`` AND in the corresponding
   Clearledgr ``erp_connections.credentials`` row. We verify the
   signature server-side, check ``exp``, extract claims (``account_id``,
   ``user_email``, ``bill_id``), and resolve a Clearledgr user.

2. **Dev token (Phase 1-2 bootstrap):** if the env var
   ``NETSUITE_PANEL_DEV_TOKEN`` is set and the Bearer header matches
   it exactly, we accept the request and resolve the org via the
   ``account_id`` query param against ``erp_connections``. Useful for
   shipping a working demo before the per-tenant secret is provisioned.
   This path is disabled in production by default — set the env var
   only in dev/staging.

The endpoint reuses the existing Box read logic by looking up the AP
item via ``erp_reference == ns_internal_id`` (the NetSuite bill's
internal ID, persisted by ``post_bill_to_netsuite`` when Clearledgr
posts the bill).
"""
from __future__ import annotations

import base64
import hmac
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from clearledgr.api.deps import verify_org_access
from clearledgr.core.auth import TokenData
from clearledgr.core.database import get_db as _get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extension", tags=["netsuite-panel"])


# Canonical ui_surface token for actions originating in the NetSuite
# Vendor Bill panel. Phase 1's decision_context auto-build records this
# value on every state_transition audit row driven from inside NetSuite,
# distinguishing NetSuite-rendered approvals from Slack / Teams / web.
NETSUITE_PANEL_SOURCE_CHANNEL = "erp_native_netsuite"
_security = HTTPBearer(auto_error=False)


# ─── JWT verification ───────────────────────────────────────────────

def _b64url_decode(value: str) -> bytes:
    """Decode a URL-safe base64 string with padding tolerance."""
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _verify_panel_jwt(token: str, secret: str) -> Optional[Dict[str, Any]]:
    """HMAC-SHA256 verify a Suitelet-minted panel JWT.

    Returns the decoded payload dict on success; None on any failure.
    Logs the specific failure reason at WARNING so prod debugging isn't
    a black box.
    """
    if not token or not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        logger.warning("netsuite_panel_jwt: malformed (expected 3 parts, got %d)", len(parts))
        return None
    header_b64, payload_b64, signature_b64 = parts
    try:
        signing_input = (header_b64 + "." + payload_b64).encode("ascii")
        expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        signature = _b64url_decode(signature_b64)
        if not hmac.compare_digest(expected, signature):
            logger.warning("netsuite_panel_jwt: signature mismatch")
            return None
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:  # noqa: BLE001
        logger.warning("netsuite_panel_jwt: decode failed (%s)", exc)
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        logger.warning("netsuite_panel_jwt: missing/invalid exp claim")
        return None
    if datetime.now(timezone.utc).timestamp() >= float(exp):
        logger.warning("netsuite_panel_jwt: expired")
        return None
    return payload


def _resolve_org_for_account_id(db, account_id: str) -> Optional[str]:
    """Find the Clearledgr organization whose NetSuite connection matches
    this account_id. Returns the org_id, or None if no active NetSuite
    connection has this account.
    """
    if not account_id:
        return None
    account_id_normalized = str(account_id).strip().upper()
    for org_id in _candidate_org_ids(db):
        for conn in db.get_erp_connections(org_id):
            if str(conn.get("erp_type") or "").lower() != "netsuite":
                continue
            creds = conn.get("credentials") or {}
            if isinstance(creds, str):
                try:
                    creds = json.loads(creds)
                except Exception:
                    creds = {}
            stored = str((creds or {}).get("account_id") or "").strip().upper()
            if stored and stored == account_id_normalized:
                return org_id
    return None


def _candidate_org_ids(db) -> list:
    """Walk all orgs with active NetSuite connections.

    For Phase 1-3 demo this is fine because the deployed account has at
    most a handful of orgs. Phase 4 adds an indexed lookup
    (``get_erp_connection_by_account_id``) — not done here to keep the
    diff thin.
    """
    try:
        rows = db.list_organizations()
    except Exception:
        return []
    # Skip rows whose ``id`` is empty rather than coercing to the
    # legacy ``"default"`` literal — a synthetic "default" id would
    # then be treated as a real account candidate by downstream
    # callers, which is the M4 landmine in a different shape.
    return [
        rid
        for rid in (str(row.get("id") or "").strip() for row in (rows or []) if row)
        if rid
    ]


def _resolve_panel_user(
    credentials: Optional[HTTPAuthorizationCredentials],
    account_id: str,
    bill_id: str,
) -> TokenData:
    """Validate the panel's auth and resolve to a Clearledgr TokenData.

    Raises HTTPException(401) on any failure. Tries dev-token first
    (cheap), then JWT (requires DB lookup). Both paths require
    ``account_id`` to map to an active Clearledgr ``erp_connections``
    row of type netsuite.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="netsuite_panel: missing bearer token")
    token = credentials.credentials.strip()
    db = _get_db()

    # Path A — dev token bootstrap (Phase 1-2)
    dev_token = os.getenv("NETSUITE_PANEL_DEV_TOKEN", "").strip()
    if dev_token and hmac.compare_digest(token, dev_token):
        org_id = _resolve_org_for_account_id(db, account_id)
        if not org_id:
            raise HTTPException(
                status_code=401,
                detail="netsuite_panel: dev token accepted but account_id has no active NetSuite connection",
            )
        return TokenData(
            user_id="netsuite_panel_dev",
            email=f"netsuite-panel-dev@{account_id or 'unknown'}",
            organization_id=org_id,
            role="netsuite_panel",
            exp=datetime.now(timezone.utc),
        )

    # Path B — HMAC-signed JWT (Phase 3)
    org_id = _resolve_org_for_account_id(db, account_id)
    if not org_id:
        raise HTTPException(
            status_code=401,
            detail="netsuite_panel: account_id has no active NetSuite connection",
        )
    conn = next(
        (c for c in db.get_erp_connections(org_id) if str(c.get("erp_type") or "").lower() == "netsuite"),
        None,
    )
    creds = conn.get("credentials") if conn else {}
    if isinstance(creds, str):
        try:
            creds = json.loads(creds)
        except Exception:
            creds = {}
    # The same `webhook_secret` provisioned for outbound vendor-bill webhooks
    # signs the panel JWT — single secret in `customrecord_cl_settings`,
    # rotatable as one unit. If we ever need to rotate independently, split
    # into `panel_secret` here without touching the SuiteScript side.
    bundle_secret = str((creds or {}).get("webhook_secret") or "").strip()
    if not bundle_secret:
        raise HTTPException(
            status_code=401,
            detail="netsuite_panel: tenant has no webhook_secret provisioned (Phase 3 setup pending)",
        )
    payload = _verify_panel_jwt(token, bundle_secret)
    if not payload:
        raise HTTPException(status_code=401, detail="netsuite_panel: invalid or expired token")
    # Cross-check claims against query params — JWT must be issued for the
    # bill we're being asked about.
    if str(payload.get("billId") or "") != str(bill_id):
        raise HTTPException(status_code=401, detail="netsuite_panel: bill_id mismatch in JWT")
    if str(payload.get("accountId") or "").upper() != str(account_id or "").upper():
        raise HTTPException(status_code=401, detail="netsuite_panel: account_id mismatch in JWT")

    user_email = str(payload.get("userEmail") or "").strip().lower()
    user_id = "netsuite_panel"
    if user_email:
        try:
            user_row = db.get_user_by_email(user_email)
            if user_row and str(user_row.get("organization_id") or "").strip() == org_id:
                user_id = str(user_row.get("id") or user_id)
        except Exception:
            pass
    return TokenData(
        user_id=user_id,
        email=user_email or f"netsuite-panel@{account_id}",
        organization_id=org_id,
        role="netsuite_panel",
        exp=datetime.now(timezone.utc),
    )


# ─── Endpoint ───────────────────────────────────────────────────────

@router.get("/ap-items/by-netsuite-bill/{ns_internal_id}")
def get_ap_item_by_netsuite_bill(
    ns_internal_id: str,
    account_id: str = Query(..., min_length=1, description="NetSuite account ID (e.g. '1234567' or '1234567_SB1')"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> Dict[str, Any]:
    """Return the Clearledgr Box for an AP item linked to a NetSuite bill.

    Auth: requires either a Suitelet-minted HMAC JWT (Phase 3) or the
    dev-token env-var (Phase 1-2).

    Lookup: ``erp_reference = ns_internal_id`` against ``ap_items``,
    scoped to the org resolved from ``account_id``.

    Response shape mirrors ``GET /api/ap/items/{ap_item_id}/box`` plus
    a ``summary`` block the panel renders without a second round-trip,
    plus the ``ap_item_id`` (so the panel can deep-link to the
    Clearledgr app).
    """
    user = _resolve_panel_user(credentials, account_id, ns_internal_id)
    db = _get_db()
    item = db.get_ap_item_by_erp_reference(user.organization_id, ns_internal_id)
    if not item:
        raise HTTPException(
            status_code=404,
            detail={"reason": "no_clearledgr_item_for_bill", "ns_internal_id": ns_internal_id},
        )
    verify_org_access(item.get("organization_id") or "default", user)
    ap_item_id = str(item.get("id") or "").strip()

    timeline = []
    try:
        from clearledgr.services.ap_operator_audit import normalize_operator_audit_events
        timeline = normalize_operator_audit_events(db.list_ap_audit_events(ap_item_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("netsuite_panel: timeline fetch failed for %s — %s", ap_item_id, exc)

    exceptions: list = []
    if hasattr(db, "list_box_exceptions"):
        try:
            exceptions = db.list_box_exceptions(box_type="ap_item", box_id=ap_item_id)
        except Exception:
            exceptions = []

    outcome = None
    if hasattr(db, "get_box_outcome"):
        try:
            outcome = db.get_box_outcome(box_type="ap_item", box_id=ap_item_id)
        except Exception:
            outcome = None

    summary = {
        "vendor_name": item.get("vendor_name"),
        "amount": item.get("amount"),
        "currency": item.get("currency"),
        "invoice_number": item.get("invoice_number"),
        "due_date": item.get("due_date"),
    }

    return {
        "ap_item_id": ap_item_id,
        "box_id": ap_item_id,
        "box_type": "ap_item",
        "state": item.get("state"),
        "summary": summary,
        "timeline": timeline,
        "exceptions": exceptions,
        "outcome": outcome,
    }


# ─── Action endpoints ───────────────────────────────────────────────
#
# Phase 3 (audit-trail compose): the panel calls these instead of the
# generic ``/extension/route-low-risk-approval`` etc., because those
# bake ``source_channel="slack"`` by default. Routing through dedicated
# NetSuite endpoints means the dispatch carries
# ``source_channel="erp_native_netsuite"`` and Phase 1's decision_context
# auto-build records ``ui_surface="erp_native_netsuite"`` on the
# resulting state_transition audit row — preserving the SoR claim that
# the audit chain identifies *which surface* the operator used.


class NetSuitePanelActionRequest(BaseModel):
    """Body for POST actions from the NetSuite Vendor Bill panel.

    The panel JWT carries the bill_id + account_id in path / query,
    so the body only needs the optional reason text + idempotency key.
    """

    reason: Optional[str] = Field(default=None, max_length=4000)
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


async def _dispatch_netsuite_panel_action(
    *,
    intent: str,
    ns_internal_id: str,
    account_id: str,
    credentials: Optional[HTTPAuthorizationCredentials],
    request: NetSuitePanelActionRequest,
    default_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Shared pre-flight + runtime dispatch for the three panel actions.

    The pre-flight is identical to the GET endpoint's: validate the
    panel auth, resolve the org, look up the AP item by NetSuite bill
    id. The dispatch wraps ``dispatch_runtime_intent`` with the canonical
    NetSuite source channel + the panel user's identity so the audit row
    records the human approver, not ``actor_type="system"``.
    """
    from clearledgr.services.agent_command_dispatch import (
        build_channel_runtime,
        dispatch_runtime_intent,
    )

    user = _resolve_panel_user(credentials, account_id, ns_internal_id)
    db = _get_db()
    item = db.get_ap_item_by_erp_reference(user.organization_id, ns_internal_id)
    if not item:
        raise HTTPException(
            status_code=404,
            detail={"reason": "no_clearledgr_item_for_bill", "ns_internal_id": ns_internal_id},
        )
    verify_org_access(item.get("organization_id") or "default", user)
    ap_item_id = str(item.get("id") or "").strip()

    actor_id = user.user_id or user.email or "netsuite_panel"
    actor_email = user.email or actor_id

    runtime = build_channel_runtime(
        organization_id=user.organization_id,
        actor_id=actor_id,
        actor_email=actor_email,
        db=db,
        fallback_actor="netsuite_panel",
    )
    reason_text = (request.reason or default_reason or "").strip() or None
    payload = {
        "ap_item_id": ap_item_id,
        "email_id": str(item.get("thread_id") or item.get("message_id") or ap_item_id),
        "reason": reason_text,
        "source_channel": NETSUITE_PANEL_SOURCE_CHANNEL,
        "source_channel_id": account_id,
        "source_message_ref": ns_internal_id,
        "actor_id": actor_id,
        "actor_email": actor_email,
        "actor_display": actor_email,
    }
    try:
        result = await dispatch_runtime_intent(
            runtime, intent, payload, idempotency_key=request.idempotency_key,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "netsuite_panel_action_failed: intent=%s ap_item_id=%s err=%s",
            intent, ap_item_id, exc,
        )
        raise HTTPException(status_code=500, detail="netsuite_panel: action dispatch failed")

    return {
        "ap_item_id": ap_item_id,
        "ns_internal_id": ns_internal_id,
        "intent": intent,
        "result": result,
    }


@router.post("/ap-items/by-netsuite-bill/{ns_internal_id}/approve")
async def approve_netsuite_bill(
    ns_internal_id: str,
    account_id: str = Query(..., min_length=1),
    body: NetSuitePanelActionRequest = Body(default_factory=NetSuitePanelActionRequest),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> Dict[str, Any]:
    """Approve the AP item linked to this NetSuite bill from inside the
    Vendor Bill panel. Dispatches the ``approve_invoice`` runtime intent
    with ``source_channel="erp_native_netsuite"`` so the audit chain
    records the operator's NetSuite-side click.
    """
    return await _dispatch_netsuite_panel_action(
        intent="approve_invoice",
        ns_internal_id=ns_internal_id,
        account_id=account_id,
        credentials=credentials,
        request=body,
        default_reason="approved_in_netsuite_panel",
    )


@router.post("/ap-items/by-netsuite-bill/{ns_internal_id}/reject")
async def reject_netsuite_bill(
    ns_internal_id: str,
    account_id: str = Query(..., min_length=1),
    body: NetSuitePanelActionRequest = Body(default_factory=NetSuitePanelActionRequest),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> Dict[str, Any]:
    """Reject the AP item linked to this NetSuite bill from inside the
    Vendor Bill panel.
    """
    return await _dispatch_netsuite_panel_action(
        intent="reject_invoice",
        ns_internal_id=ns_internal_id,
        account_id=account_id,
        credentials=credentials,
        request=body,
        default_reason="rejected_in_netsuite_panel",
    )


@router.post("/ap-items/by-netsuite-bill/{ns_internal_id}/request-info")
async def request_info_netsuite_bill(
    ns_internal_id: str,
    account_id: str = Query(..., min_length=1),
    body: NetSuitePanelActionRequest = Body(default_factory=NetSuitePanelActionRequest),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> Dict[str, Any]:
    """Move the AP item to ``needs_info`` from inside the Vendor Bill
    panel. Used when the operator wants more documentation or vendor
    clarification before approving.
    """
    return await _dispatch_netsuite_panel_action(
        intent="request_info",
        ns_internal_id=ns_internal_id,
        account_id=account_id,
        credentials=credentials,
        request=body,
        default_reason="info_requested_from_netsuite_panel",
    )
