"""Inbound ERP webhook endpoints.

One route per ERP. Each:

1. Reads the raw request body **before** any parsing. Signature
   verification is byte-exact — if FastAPI had already deserialized
   and re-serialized the JSON, the signature would never validate
   (trailing whitespace, key ordering, number formatting).
2. Extracts the ERP-specific signature header(s).
3. Resolves the per-tenant secret from the stored ``ERPConnection``.
   If no secret is configured, returns 503 (service not configured) —
   NOT 401 — so the caller knows to set up the webhook in the app
   settings rather than wondering whether they sent the wrong
   signature.
4. Verifies the signature via :mod:`clearledgr.core.erp_webhook_verify`
   (constant-time HMAC, fail-closed).
5. Returns 401 on any failure with an opaque error code. We never
   echo which check failed (signature, timestamp, etc.) so an
   attacker can't use our 401 to probe.
6. On success: enqueues a dispatch record, writes an audit event,
   and returns 200 quickly. Any heavy reconciliation happens in a
   background task — webhook sources retry on non-2xx.

The tenant lookup is driven by URL shape:
  POST /erp/webhooks/{erp}/{organization_id}

QBO and Xero don't carry tenant identity natively (they carry realm /
tenant IDs which we map to orgs at connection time), so URL-scoping
by org is the simplest trust-boundary.

Xero Intent-to-Receive handshake:
  Xero ships a first POST with ``events: []`` to the endpoint URL.
  If our verifier says the signature is valid → respond 200.
  If invalid → respond 401. Xero surfaces the failure in the app
  config page so customers can fix their webhook key.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response

from clearledgr.core.database import get_db
from clearledgr.core.erp_webhook_verify import (
    verify_netsuite_signature,
    verify_quickbooks_signature,
    verify_sap_signature,
    verify_xero_signature,
)
from clearledgr.integrations.erp_router import get_erp_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/erp/webhooks", tags=["erp-webhooks"])


# Reply bodies are deliberately small and non-leaky. The ERP senders
# only care about the status code; any reply body is for our own logs.
_UNAUTHORIZED_BODY = {"error": "invalid_signature"}
_NOT_CONFIGURED_BODY = {"error": "webhook_not_configured"}
_BAD_REQUEST_BODY = {"error": "malformed_request"}


def _resolve_webhook_secret(organization_id: str, erp_type: str) -> Optional[str]:
    """Load the per-tenant inbound webhook secret.

    Returns None if the org has no active connection of this type or
    no webhook_secret is configured on that connection. Callers must
    treat None as 503 "not configured", never as 200.
    """
    try:
        conn = get_erp_connection(organization_id, erp_type)
    except Exception:
        logger.exception(
            "ERP connection lookup failed for org=%s erp=%s",
            organization_id, erp_type,
        )
        return None
    if conn is None:
        return None
    return conn.webhook_secret


def _record_webhook_event(
    *,
    organization_id: str,
    erp_type: str,
    event_type: str,
    payload_preview: Dict[str, Any],
    idempotency_key: Optional[str] = None,
) -> None:
    """Persist an audit event so every accepted webhook is reconstructable.

    Best-effort: never let audit failures sink the webhook response
    (the ERP will retry on non-2xx, which would snowball). Payload
    preview is truncated so we don't balloon the DB on chatty ERPs.
    """
    try:
        db = get_db()
        db.append_audit_event({
            "event_type": event_type,
            "actor_type": "erp_webhook",
            "actor_id": erp_type,
            "box_id": f"erp_webhook:{erp_type}:{organization_id}",
            "box_type": "erp_webhook",
            "organization_id": organization_id,
            "idempotency_key": idempotency_key,
            "payload_json": {
                "erp": erp_type,
                "preview": payload_preview,
            },
        })
    except Exception:
        logger.exception(
            "audit write failed for %s webhook org=%s",
            erp_type, organization_id,
        )


def _preview_json(body: bytes, limit: int = 2048) -> Dict[str, Any]:
    """Small, bounded preview for audit payloads (never the full body)."""
    import json
    try:
        text = body[:limit].decode("utf-8", errors="replace")
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            return {"raw": str(parsed)[:256]}
        # Keep shallow — don't persist arbitrarily deep structures.
        return {k: parsed[k] for k in list(parsed.keys())[:16]}
    except Exception:
        return {"truncated": body[:256].decode("utf-8", errors="replace")}


# ---------------------------------------------------------------------------
# QuickBooks
# ---------------------------------------------------------------------------


@router.post("/quickbooks/{organization_id}")
async def quickbooks_webhook(
    organization_id: str,
    request: Request,
    intuit_signature: Optional[str] = Header(
        default=None, alias="intuit-signature",
    ),
) -> Response:
    """Intuit QBO webhook notification.

    Signature header: ``intuit-signature``
    Body: JSON with ``eventNotifications`` envelope.
    """
    verifier_token = _resolve_webhook_secret(organization_id, "quickbooks")
    if not verifier_token:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_NOT_CONFIGURED_BODY,
        )

    raw = await request.body()
    if not verify_quickbooks_signature(raw, intuit_signature, verifier_token):
        logger.warning(
            "QBO webhook signature failed for org=%s (bytes=%d)",
            organization_id, len(raw),
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_UNAUTHORIZED_BODY,
        )

    _record_webhook_event(
        organization_id=organization_id,
        erp_type="quickbooks",
        event_type="erp_webhook_received",
        payload_preview=_preview_json(raw),
    )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": True})


# ---------------------------------------------------------------------------
# Xero
# ---------------------------------------------------------------------------


@router.post("/xero/{organization_id}")
async def xero_webhook(
    organization_id: str,
    request: Request,
    x_xero_signature: Optional[str] = Header(
        default=None, alias="x-xero-signature",
    ),
) -> Response:
    """Xero webhook notification.

    Signature header: ``x-xero-signature``
    Also handles Xero's Intent-to-Receive handshake (the first POST
    Xero sends after a webhook URL is configured, carrying empty
    ``events: []``). If signature verifies, respond 200 so Xero
    activates the subscription; otherwise respond 401 so Xero
    surfaces the failure in the developer portal.
    """
    webhook_key = _resolve_webhook_secret(organization_id, "xero")
    if not webhook_key:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_NOT_CONFIGURED_BODY,
        )

    raw = await request.body()
    if not verify_xero_signature(raw, x_xero_signature, webhook_key):
        logger.warning(
            "Xero webhook signature failed for org=%s (bytes=%d)",
            organization_id, len(raw),
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_UNAUTHORIZED_BODY,
        )

    preview = _preview_json(raw)
    is_itr = isinstance(preview.get("events"), list) and not preview["events"]
    event_type = (
        "erp_webhook_intent_to_receive" if is_itr
        else "erp_webhook_received"
    )
    _record_webhook_event(
        organization_id=organization_id,
        erp_type="xero",
        event_type=event_type,
        payload_preview=preview,
    )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": True})


# ---------------------------------------------------------------------------
# NetSuite
# ---------------------------------------------------------------------------


@router.post("/netsuite/{organization_id}")
async def netsuite_webhook(
    organization_id: str,
    request: Request,
    x_netsuite_signature: Optional[str] = Header(
        default=None, alias="X-NetSuite-Signature",
    ),
    x_netsuite_timestamp: Optional[str] = Header(
        default=None, alias="X-NetSuite-Timestamp",
    ),
) -> Response:
    """NetSuite RESTlet / SuiteFlow outbound HTTP push.

    Signature header: ``X-NetSuite-Signature: v1=<hex>``
    Timestamp header: ``X-NetSuite-Timestamp: <unix seconds>``
    Covered body: ``"<timestamp>." + raw_body``
    """
    secret = _resolve_webhook_secret(organization_id, "netsuite")
    if not secret:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_NOT_CONFIGURED_BODY,
        )

    raw = await request.body()
    if not verify_netsuite_signature(
        raw, x_netsuite_signature, x_netsuite_timestamp, secret,
    ):
        logger.warning(
            "NetSuite webhook signature failed for org=%s (bytes=%d)",
            organization_id, len(raw),
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_UNAUTHORIZED_BODY,
        )

    _record_webhook_event(
        organization_id=organization_id,
        erp_type="netsuite",
        event_type="erp_webhook_received",
        payload_preview=_preview_json(raw),
    )

    # Dispatch the NetSuite payload into Clearledgr's coordination layer
    # (create/update/close the corresponding AP item Box). Best-effort —
    # any failure is logged + audited but does not change the 200 we
    # return: NetSuite retries on non-2xx, and the "received" audit
    # event above is enough to reconstruct what happened from logs.
    try:
        import json as _json
        from clearledgr.services.erp_webhook_dispatch import dispatch_netsuite_event
        try:
            payload_obj = _json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload_obj = {}
        if payload_obj:
            dispatch_result = dispatch_netsuite_event(organization_id, payload_obj)
            logger.info(
                "netsuite webhook dispatch: org=%s event=%s result=%s",
                organization_id,
                payload_obj.get("event_type"),
                dispatch_result,
            )
    except Exception as dispatch_exc:  # noqa: BLE001
        # Never let dispatch failures sink the webhook ACK.
        logger.warning(
            "netsuite webhook dispatch raised for org=%s — %s",
            organization_id, dispatch_exc,
        )

    return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": True})


# ---------------------------------------------------------------------------
# SAP
# ---------------------------------------------------------------------------


@router.post("/sap/{organization_id}")
async def sap_webhook(
    organization_id: str,
    request: Request,
    x_sap_signature: Optional[str] = Header(
        default=None, alias="X-SAP-Signature",
    ),
    x_sap_timestamp: Optional[str] = Header(
        default=None, alias="X-SAP-Timestamp",
    ),
) -> Response:
    """SAP S/4HANA CPI outbound HTTP push.

    Signature header: ``X-SAP-Signature: v1=<hex>``
    Timestamp header: ``X-SAP-Timestamp: <unix seconds>``
    Covered body: ``"<timestamp>." + raw_body``
    """
    secret = _resolve_webhook_secret(organization_id, "sap")
    if not secret:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_NOT_CONFIGURED_BODY,
        )

    raw = await request.body()
    if not verify_sap_signature(
        raw, x_sap_signature, x_sap_timestamp, secret,
    ):
        logger.warning(
            "SAP webhook signature failed for org=%s (bytes=%d)",
            organization_id, len(raw),
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_UNAUTHORIZED_BODY,
        )

    _record_webhook_event(
        organization_id=organization_id,
        erp_type="sap",
        event_type="erp_webhook_received",
        payload_preview=_preview_json(raw),
    )

    # Dispatch the SAP S/4HANA payload (BTP Event Mesh CloudEvent or
    # ABAP-BAdI HTTP push) into Clearledgr's coordination layer. Same
    # best-effort posture as the NetSuite handler — failures log + audit
    # but never sink the 200 ACK.
    try:
        import json as _json
        from clearledgr.services.sap_webhook_dispatch import dispatch_sap_event
        try:
            payload_obj = _json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload_obj = {}
        if payload_obj:
            dispatch_result = dispatch_sap_event(organization_id, payload_obj)
            logger.info(
                "sap webhook dispatch: org=%s event=%s result=%s",
                organization_id,
                payload_obj.get("type") or payload_obj.get("event_type"),
                dispatch_result,
            )
    except Exception as dispatch_exc:  # noqa: BLE001
        logger.warning(
            "sap webhook dispatch raised for org=%s — %s",
            organization_id, dispatch_exc,
        )

    return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": True})
