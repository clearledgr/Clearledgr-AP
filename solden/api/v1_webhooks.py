"""Public ``/v1/webhooks`` router — customer-facing webhook CRUD.

Outbound webhooks are how customer agents stay in step with the Box.
A customer agent registers a URL + a set of event types; when one of
those events fires on the customer's tenant, Solden POSTs the event
to the URL with an HMAC-SHA256 signature header so the receiver can
verify authenticity.

Endpoints:

* ``GET /v1/webhooks`` — list every webhook subscription the caller's
  org owns.
* ``POST /v1/webhooks`` — register a new subscription. The server
  generates a 32-byte URL-safe ``secret`` and returns it **once** in
  the response body. Subsequent reads only show a 4-char preview.
  This mirrors the Stripe / GitHub / the model provider webhook pattern —
  the secret never leaves the database in plaintext after creation,
  so even a compromised list endpoint can't leak signing keys.
* ``GET /v1/webhooks/{id}`` — read one subscription.
* ``PATCH /v1/webhooks/{id}`` — update URL, event_types, description,
  or active flag. The secret cannot be changed in place — use the
  dedicated ``rotate-secret`` endpoint.
* ``DELETE /v1/webhooks/{id}`` — remove a subscription.
* ``POST /v1/webhooks/{id}/rotate-secret`` — generate a new secret
  and return it once. The old secret is invalidated immediately.
* ``POST /v1/webhooks/{id}/test`` — fire a ``webhook.test`` event
  through the normal delivery pipeline. Useful for verifying the
  signature check in the customer's receiver before live events
  start arriving.
* ``GET /v1/webhooks/{id}/deliveries`` — recent delivery attempts
  for this subscription (status, response code, attempted_at).

All endpoints require ``webhooks:manage`` scope.

Tenant isolation: every store call is pinned to ``agent.organization_id``;
the SQL itself filters by org so a known webhook id from another tenant
returns 404, not a leaked row. Same defence-in-depth contract as the
rest of the /v1 surface.

Signing contract: deliveries include both ``X-Solden-Signature``
(canonical, brand-aligned) and ``X-Solden-Signature`` (legacy
header — kept during the deprecation window for any receiver wired
under the old brand). Both carry the same ``sha256=<hex>`` value.
"""

from __future__ import annotations

import logging
import secrets as _secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, HttpUrl

from solden.api.v1_auth import AgentIdentity, require_agent_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhooks", tags=["v1-webhooks"])


# ─── Event-type allowlist ──────────────────────────────────────────


# Customers can subscribe to any of these. ``*`` is a special token
# that subscribes to every event (handy for audit-log relays).
_ALLOWED_EVENTS = frozenset({
    "*",
    # Invoice / AP lifecycle
    "invoice.received",
    "invoice.validated",
    "invoice.needs_approval",
    "invoice.approved",
    "invoice.rejected",
    "invoice.ready_to_post",
    "invoice.posted_to_erp",
    "invoice.closed",
    "invoice.needs_info",
    "invoice.failed_post",
    # Payment lifecycle
    "payment.completed",
    "payment.failed",
    "payment.reversed",
    # Billing guard-rails
    "billing.llm_budget_exceeded",
    # Test fire
    "webhook.test",
})


# ─── Request / response models ─────────────────────────────────────


class CreateWebhookRequest(BaseModel):
    url: HttpUrl = Field(
        ...,
        description="HTTPS endpoint Solden POSTs to. HTTP not accepted.",
    )
    event_types: List[str] = Field(
        ...,
        min_length=1,
        description=(
            "Event names to subscribe to. Use '*' to subscribe to all "
            "events. See the docs for the full list."
        ),
    )
    description: str = Field(
        default="",
        max_length=500,
        description="Free-form label so customers can identify the hook",
    )


class UpdateWebhookRequest(BaseModel):
    url: Optional[HttpUrl] = None
    event_types: Optional[List[str]] = Field(default=None, min_length=1)
    description: Optional[str] = Field(default=None, max_length=500)
    is_active: Optional[bool] = None


# ─── Helpers ───────────────────────────────────────────────────────


def _error(
    *,
    status_code: int,
    error_code: str,
    message: str,
    request: Optional[Request] = None,
) -> JSONResponse:
    body: Dict[str, Any] = {"error_code": error_code, "message": message}
    rid = getattr(request.state, "correlation_id", None) if request else None
    if rid:
        body["request_id"] = rid
    return JSONResponse(status_code=status_code, content=body)


def _generate_secret() -> str:
    """32-byte URL-safe secret. ``secrets.token_urlsafe(32)`` returns
    ~43 chars of high-entropy random — same shape as Stripe's
    ``whsec_...`` keys minus the brand prefix."""
    return f"whsec_{_secrets.token_urlsafe(32)}"


def _redact_secret(secret: str) -> str:
    """Show only the last 4 chars after the ``whsec_`` prefix so
    customers can disambiguate keys in the UI without leaking the
    full secret. ``whsec_abc...XYZ4`` → ``whsec_***XYZ4``."""
    if not secret:
        return ""
    if len(secret) <= 10:
        return "whsec_***"
    return f"whsec_***{secret[-4:]}"


def _shape_subscription(
    row: Dict[str, Any], *, reveal_secret: bool = False
) -> Dict[str, Any]:
    """Public-facing webhook record. ``reveal_secret=True`` is used
    exactly twice — at create and at rotate-secret — and nowhere else.
    Every other code path sees the redacted preview."""
    raw_secret = str(row.get("secret") or "")
    return {
        "id": row.get("id"),
        "url": row.get("url"),
        "event_types": row.get("event_types") or [],
        "description": row.get("description") or "",
        "is_active": bool(row.get("is_active")),
        "secret": raw_secret if reveal_secret else None,
        "secret_preview": _redact_secret(raw_secret),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _validate_events(event_types: List[str]) -> Optional[str]:
    """Returns the first invalid event name (or None if all pass).
    The error path quotes which name was wrong so a customer typo
    fails loud instead of registering a hook that never fires."""
    for e in event_types:
        if e not in _ALLOWED_EVENTS:
            return e
    return None


def _validate_https(url: str) -> bool:
    """Webhooks ride over the public internet; HTTP is unsafe.
    Reject http:// at the boundary so we don't ship plaintext events."""
    return url.lower().startswith("https://")


# ─── Endpoints ─────────────────────────────────────────────────────


@router.get("")
async def list_webhooks(
    request: Request,
    active_only: bool = Query(
        default=False,
        description="If true, only return subscriptions with is_active=true",
    ),
    agent: AgentIdentity = Depends(require_agent_key("webhooks:manage")),
):
    """List every webhook subscription the caller's organisation owns."""
    from solden.core.database import get_db

    db = get_db()
    try:
        rows = db.list_webhook_subscriptions(
            agent.organization_id, active_only=active_only
        )
    except Exception:
        logger.exception("v1.webhooks list failure")
        return _error(
            status_code=500,
            error_code="internal_error",
            message="internal_error",
            request=request,
        )
    return {
        "webhooks": [_shape_subscription(r) for r in rows],
        "count": len(rows),
    }


@router.post("", status_code=201)
async def create_webhook(
    payload: CreateWebhookRequest,
    request: Request,
    agent: AgentIdentity = Depends(require_agent_key("webhooks:manage")),
):
    """Register a new webhook subscription.

    The server generates the signing secret and returns it once in
    ``secret``. The customer must capture it now — subsequent reads
    only return ``secret_preview`` (last 4 chars). To replace a lost
    secret, use ``POST /v1/webhooks/{id}/rotate-secret``.
    """
    url_str = str(payload.url)
    if not _validate_https(url_str):
        return _error(
            status_code=400,
            error_code="invalid_url",
            message="Webhook URL must use https://",
            request=request,
        )

    bad = _validate_events(payload.event_types)
    if bad is not None:
        return _error(
            status_code=400,
            error_code="invalid_event_type",
            message=(
                f"event_types contains unknown event {bad!r}. "
                f"See the docs for the supported list."
            ),
            request=request,
        )

    secret = _generate_secret()
    from solden.core.database import get_db

    db = get_db()
    try:
        row = db.create_webhook_subscription(
            organization_id=agent.organization_id,
            url=url_str,
            event_types=payload.event_types,
            secret=secret,
            description=payload.description,
        )
    except Exception:
        logger.exception("v1.webhooks create failure")
        return _error(
            status_code=500,
            error_code="internal_error",
            message="internal_error",
            request=request,
        )

    return _shape_subscription(row, reveal_secret=True)


@router.get("/{webhook_id}")
async def read_webhook(
    webhook_id: str = Path(..., min_length=1),
    *,
    request: Request,
    agent: AgentIdentity = Depends(require_agent_key("webhooks:manage")),
):
    """Read a single subscription. Returns 404 if the id doesn't exist
    OR belongs to a different tenant — the two cases are
    indistinguishable to the caller by design (no tenant probe)."""
    from solden.core.database import get_db

    db = get_db()
    row = db.get_webhook_subscription(webhook_id, agent.organization_id)
    if row is None:
        return _error(
            status_code=404,
            error_code="not_found",
            message=f"webhook:{webhook_id} not found",
            request=request,
        )
    return _shape_subscription(row)


@router.patch("/{webhook_id}")
async def update_webhook(
    webhook_id: str = Path(..., min_length=1),
    *,
    payload: UpdateWebhookRequest,
    request: Request,
    agent: AgentIdentity = Depends(require_agent_key("webhooks:manage")),
):
    """Update url / event_types / description / is_active. The
    secret is intentionally not updateable here — use rotate-secret."""
    updates: Dict[str, Any] = {}
    if payload.url is not None:
        url_str = str(payload.url)
        if not _validate_https(url_str):
            return _error(
                status_code=400,
                error_code="invalid_url",
                message="Webhook URL must use https://",
                request=request,
            )
        updates["url"] = url_str
    if payload.event_types is not None:
        bad = _validate_events(payload.event_types)
        if bad is not None:
            return _error(
                status_code=400,
                error_code="invalid_event_type",
                message=f"event_types contains unknown event {bad!r}",
                request=request,
            )
        updates["event_types"] = payload.event_types
    if payload.description is not None:
        updates["description"] = payload.description
    if payload.is_active is not None:
        updates["is_active"] = payload.is_active

    if not updates:
        return _error(
            status_code=400,
            error_code="empty_update",
            message="Provide at least one field to update",
            request=request,
        )

    from solden.core.database import get_db

    db = get_db()
    ok = db.update_webhook_subscription(
        webhook_id, agent.organization_id, **updates
    )
    if not ok:
        return _error(
            status_code=404,
            error_code="not_found",
            message=f"webhook:{webhook_id} not found",
            request=request,
        )
    row = db.get_webhook_subscription(webhook_id, agent.organization_id)
    return _shape_subscription(row or {})


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: str = Path(..., min_length=1),
    *,
    request: Request,
    agent: AgentIdentity = Depends(require_agent_key("webhooks:manage")),
):
    """Remove a subscription. No body on 204 success; 404 on miss."""
    from solden.core.database import get_db

    db = get_db()
    ok = db.delete_webhook_subscription(webhook_id, agent.organization_id)
    if not ok:
        return _error(
            status_code=404,
            error_code="not_found",
            message=f"webhook:{webhook_id} not found",
            request=request,
        )
    return JSONResponse(status_code=204, content=None)


@router.post("/{webhook_id}/rotate-secret")
async def rotate_secret(
    webhook_id: str = Path(..., min_length=1),
    *,
    request: Request,
    agent: AgentIdentity = Depends(require_agent_key("webhooks:manage")),
):
    """Generate a new signing secret. The old secret is invalidated
    immediately; in-flight deliveries already signed with the old key
    will succeed at the receiver only if it still trusts both during
    the rotation window — Solden does not double-sign."""
    from solden.core.database import get_db

    db = get_db()
    row = db.get_webhook_subscription(webhook_id, agent.organization_id)
    if row is None:
        return _error(
            status_code=404,
            error_code="not_found",
            message=f"webhook:{webhook_id} not found",
            request=request,
        )

    new_secret = _generate_secret()
    db.update_webhook_subscription(
        webhook_id, agent.organization_id, secret=new_secret
    )
    refreshed = db.get_webhook_subscription(
        webhook_id, agent.organization_id
    )
    return _shape_subscription(refreshed or {}, reveal_secret=True)


@router.post("/{webhook_id}/test")
async def test_webhook(
    webhook_id: str = Path(..., min_length=1),
    *,
    request: Request,
    agent: AgentIdentity = Depends(require_agent_key("webhooks:manage")),
):
    """Fire a ``webhook.test`` event to this subscription's URL.

    Goes through the same delivery pipeline live events use, so a
    successful test exercises the full signature path the customer's
    receiver will see in production.
    """
    from solden.core.database import get_db

    db = get_db()
    row = db.get_webhook_subscription(webhook_id, agent.organization_id)
    if row is None:
        return _error(
            status_code=404,
            error_code="not_found",
            message=f"webhook:{webhook_id} not found",
            request=request,
        )

    from solden.services.webhook_delivery import deliver_webhook

    test_payload = {
        "webhook_id": webhook_id,
        "triggered_by": agent.actor_label,
        "message": "If you can read this, the signature check works.",
    }
    delivered = await deliver_webhook(
        url=row["url"],
        event_type="webhook.test",
        payload=test_payload,
        secret=row.get("secret", ""),
        webhook_id=webhook_id,
    )
    return {
        "delivered": delivered,
        "url": row["url"],
        "event": "webhook.test",
    }


@router.get("/{webhook_id}/deliveries")
async def list_deliveries(
    webhook_id: str = Path(..., min_length=1),
    *,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    agent: AgentIdentity = Depends(require_agent_key("webhooks:manage")),
):
    """Recent delivery attempts for this subscription.

    Backed by the ``webhook_deliveries`` table (migration 52). One row
    per attempt: status, response code, latency, attempted_at.
    """
    from solden.core.database import get_db

    db = get_db()
    # Tenant-pin the lookup by joining on the subscription row.
    sub = db.get_webhook_subscription(webhook_id, agent.organization_id)
    if sub is None:
        return _error(
            status_code=404,
            error_code="not_found",
            message=f"webhook:{webhook_id} not found",
            request=request,
        )

    sql = (
        "SELECT id, event_type, status, response_status, "
        "response_body_truncated, latency_ms, attempted_at, attempt_number "
        "FROM webhook_deliveries "
        "WHERE webhook_subscription_id = %s AND organization_id = %s "
        "ORDER BY attempted_at DESC LIMIT %s"
    )
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (webhook_id, agent.organization_id, limit))
            rows = [dict(r) for r in (cur.fetchall() or [])]
    except Exception:
        logger.exception("v1.webhooks deliveries query failure")
        return _error(
            status_code=500,
            error_code="internal_error",
            message="internal_error",
            request=request,
        )

    return {"deliveries": rows, "count": len(rows)}
