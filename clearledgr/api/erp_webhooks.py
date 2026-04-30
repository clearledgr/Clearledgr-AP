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

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, Request, status
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
# Intake-dispatch fan-out helpers (QB + Xero)
# ---------------------------------------------------------------------------
#
# QB and Xero webhooks batch many entity events into one POST. The
# IntakeAdapter contract is per-event, so the route extracts each
# Bill / INVOICE entity and calls handle_intake_event once per event
# with a synthetic single-entity payload that the adapter's
# parse_envelope expects.


async def _dispatch_quickbooks_bill_intake(
    *,
    organization_id: str,
    raw: bytes,
    headers: Dict[str, str],
    secret: str,
) -> None:
    """Walk a QBO eventNotifications envelope, fan out per Bill
    entity. Non-Bill entities (BillPayment, etc.) are handled by the
    payment-tracking dispatcher in the route below this one — we
    only forward Bill events here.
    """
    try:
        envelope = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        return
    if not isinstance(envelope, dict):
        return

    from clearledgr.services.intake_adapter import handle_intake_event
    # Side-effect import: register the adapter the first time the
    # webhook fires for this process.
    import clearledgr.integrations.erp_quickbooks_intake_adapter  # noqa: F401

    for note in envelope.get("eventNotifications") or []:
        if not isinstance(note, dict):
            continue
        realm_id = str(note.get("realmId") or "").strip()
        change = note.get("dataChangeEvent") or {}
        for ent in change.get("entities") or []:
            if not isinstance(ent, dict):
                continue
            if str(ent.get("name") or "") != "Bill":
                continue
            entity_id = str(ent.get("id") or "").strip()
            operation = str(ent.get("operation") or "").strip()
            if not entity_id or not operation:
                continue
            synthetic = {
                "realmId": realm_id,
                "entity_id": entity_id,
                "operation": operation,
                "event_id": f"qb:{realm_id}:{entity_id}:{operation}",
            }
            try:
                result = await handle_intake_event(
                    source_type="quickbooks",
                    organization_id=organization_id,
                    raw=json.dumps(synthetic).encode("utf-8"),
                    headers=headers,
                    secret=secret,
                    signature_already_verified=True,
                )
                logger.info(
                    "qb intake dispatch: org=%s bill=%s op=%s result=%s",
                    organization_id, entity_id, operation, result,
                )
            except Exception:
                logger.exception(
                    "qb intake dispatch raised: org=%s bill=%s op=%s",
                    organization_id, entity_id, operation,
                )


async def _dispatch_xero_invoice_intake(
    *,
    organization_id: str,
    raw: bytes,
    headers: Dict[str, str],
    secret: str,
) -> None:
    """Walk a Xero events envelope, fan out per INVOICE event.

    The XeroIntakeAdapter's enrich step fetches the invoice and
    filters ACCPAY only — ACCREC sales invoices are turned into
    a marker that the universal dispatcher short-circuits without
    creating a phantom AP item.
    """
    try:
        envelope = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        return
    if not isinstance(envelope, dict):
        return

    from clearledgr.services.intake_adapter import handle_intake_event
    import clearledgr.integrations.erp_xero_intake_adapter  # noqa: F401

    tenant_id_top = str(envelope.get("tenantId") or "").strip()
    for evt in envelope.get("events") or []:
        if not isinstance(evt, dict):
            continue
        category = str(evt.get("eventCategory") or "").upper()
        if category != "INVOICE":
            continue
        resource_id = str(evt.get("resourceId") or "").strip()
        event_type = str(evt.get("eventType") or "").strip().upper()
        if not resource_id or not event_type:
            continue
        synthetic = {
            "tenant_id": str(evt.get("tenantId") or tenant_id_top),
            "resource_id": resource_id,
            "event_type": event_type,
            "event_category": category,
            "event_id": str(evt.get("eventId") or f"xero:{resource_id}:{event_type}"),
            "event_date_utc": str(evt.get("eventDateUtc") or ""),
        }
        try:
            result = await handle_intake_event(
                source_type="xero",
                organization_id=organization_id,
                raw=json.dumps(synthetic).encode("utf-8"),
                headers=headers,
                secret=secret,
                signature_already_verified=True,
            )
            logger.info(
                "xero intake dispatch: org=%s invoice=%s op=%s result=%s",
                organization_id, resource_id, event_type, result,
            )
        except Exception:
            logger.exception(
                "xero intake dispatch raised: org=%s invoice=%s op=%s",
                organization_id, resource_id, event_type,
            )


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

    # QBO envelopes batch entity events. Walk eventNotifications and
    # fan out per-entity:
    #   - Bill.Create / Update / Delete → IntakeAdapter dispatch
    #     (creates / refreshes / closes the AP item).
    #   - BillPayment.Create / Update → payment-tracking dispatcher
    #     (updates payment state on existing AP items — Wave 2 / C3).
    # Failures log + swallow so we always return 200 to QBO and
    # avoid retry storms.
    try:
        await _dispatch_quickbooks_bill_intake(
            organization_id=organization_id,
            raw=raw,
            headers=dict(request.headers),
            secret=verifier_token,
        )
    except Exception:
        logger.exception(
            "qb intake dispatch raised for org=%s", organization_id,
        )

    try:
        from clearledgr.services.erp_payment_dispatcher import (
            dispatch_quickbooks_payment_webhook,
        )
        result = await dispatch_quickbooks_payment_webhook(
            organization_id=organization_id, raw_body=raw,
        )
        logger.info(
            "qb payment dispatch: org=%s result=%s",
            organization_id, result,
        )
    except Exception:
        logger.exception(
            "qb payment dispatch raised for org=%s", organization_id,
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

    # Xero envelopes batch INVOICE events. Walk events[] and fan
    # out per-resource:
    #   - INVOICE.CREATE / UPDATE / DELETE (filtered to ACCPAY in
    #     the adapter's enrich) → IntakeAdapter dispatch.
    #   - Same envelope drives the payment-tracking dispatcher,
    #     which keys off paid status.
    # The Intent-to-Receive handshake (events: []) skips both.
    if not is_itr:
        try:
            await _dispatch_xero_invoice_intake(
                organization_id=organization_id,
                raw=raw,
                headers=dict(request.headers),
                secret=webhook_key,
            )
        except Exception:
            logger.exception(
                "xero intake dispatch raised for org=%s", organization_id,
            )

        try:
            from clearledgr.services.erp_payment_dispatcher import (
                dispatch_xero_payment_webhook,
            )
            result = await dispatch_xero_payment_webhook(
                organization_id=organization_id, raw_body=raw,
            )
            logger.info(
                "xero payment dispatch: org=%s result=%s",
                organization_id, result,
            )
        except Exception:
            logger.exception(
                "xero payment dispatch raised for org=%s", organization_id,
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

    # Delegate to the universal IntakeAdapter dispatch. The
    # signature was already verified above; we re-verify inside the
    # handler too (idempotent, defence-in-depth) and skip the audit
    # event we already wrote. NetSuite-specific dispatch logic
    # (envelope parsing, enrichment, state derivation) lives in the
    # NetSuiteIntakeAdapter — see clearledgr/integrations/erp_netsuite_intake_adapter.py.
    _record_webhook_event(
        organization_id=organization_id,
        erp_type="netsuite",
        event_type="erp_webhook_received",
        payload_preview=_preview_json(raw),
    )
    try:
        from clearledgr.services.intake_adapter import handle_intake_event
        # Ensure the adapter is registered (import side-effect).
        import clearledgr.integrations.erp_netsuite_intake_adapter  # noqa: F401
        result = await handle_intake_event(
            source_type="netsuite",
            organization_id=organization_id,
            raw=raw,
            headers=dict(request.headers),
            secret=secret,
        )
        logger.info(
            "netsuite webhook dispatch: org=%s result=%s",
            organization_id, result,
        )
    except Exception as dispatch_exc:  # noqa: BLE001
        logger.warning(
            "netsuite webhook dispatch raised for org=%s — %s",
            organization_id, dispatch_exc,
        )

    # Wave 2 / C3: payment-tracking dispatch. NetSuite SuiteScript
    # pushes the full payment payload, so this is sync (no follow-up
    # REST call). Tolerant of payloads that carry only intake events.
    try:
        from clearledgr.services.erp_payment_dispatcher import (
            dispatch_netsuite_payment_webhook,
        )
        pay_result = dispatch_netsuite_payment_webhook(
            organization_id=organization_id, raw_body=raw,
        )
        if pay_result.get("events_parsed"):
            logger.info(
                "netsuite payment dispatch: org=%s result=%s",
                organization_id, pay_result,
            )
    except Exception:
        logger.exception(
            "netsuite payment dispatch raised for org=%s", organization_id,
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

    # Delegate to the universal IntakeAdapter dispatch. SAP S/4HANA
    # specific event-shape normalization (CloudEvents vs ABAP-BAdI),
    # enrichment, and state derivation live in
    # SapS4HanaIntakeAdapter — see
    # clearledgr/integrations/erp_sap_s4hana_intake_adapter.py.
    _record_webhook_event(
        organization_id=organization_id,
        erp_type="sap",
        event_type="erp_webhook_received",
        payload_preview=_preview_json(raw),
    )
    try:
        from clearledgr.services.intake_adapter import handle_intake_event
        import clearledgr.integrations.erp_sap_s4hana_intake_adapter  # noqa: F401
        result = await handle_intake_event(
            source_type="sap_s4hana",
            organization_id=organization_id,
            raw=raw,
            headers=dict(request.headers),
            secret=secret,
        )
        logger.info(
            "sap webhook dispatch: org=%s result=%s",
            organization_id, result,
        )
    except Exception as dispatch_exc:  # noqa: BLE001
        logger.warning(
            "sap webhook dispatch raised for org=%s — %s",
            organization_id, dispatch_exc,
        )

    # Wave 2 / C3 + S/4HANA carry-over: route CPI payment events
    # (cleared / paid / cancelled) through the C2 payment-tracking
    # lifecycle instead of letting the intake adapter shortcut the
    # AP item to CLOSED. Sync (no REST roundtrip — CloudEvents
    # payload carries the cleared amount + reference).
    try:
        from clearledgr.services.erp_payment_dispatcher import (
            dispatch_sap_s4hana_payment_webhook,
        )
        pay_result = dispatch_sap_s4hana_payment_webhook(
            organization_id=organization_id, raw_body=raw,
        )
        if pay_result.get("events_parsed"):
            logger.info(
                "sap s/4hana payment dispatch: org=%s result=%s",
                organization_id, pay_result,
            )
    except Exception:
        logger.exception(
            "sap s/4hana payment dispatch raised for org=%s",
            organization_id,
        )

    return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": True})
