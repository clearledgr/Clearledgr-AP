"""Paddle billing API surface — Module 11.

Endpoints:
  POST /api/workspace/billing/checkout       -- start a Paddle hosted-checkout flow
  GET  /api/workspace/billing/portal         -- redirect to the Paddle customer portal
  PATCH /api/workspace/billing/collection-mode  -- flip card ↔ invoice
  GET  /api/workspace/billing/invoices       -- in-app invoice history
  POST /api/webhooks/paddle                  -- Paddle event sink (HMAC-signed, no auth)

The webhook is the only route that's NOT auth-gated — Paddle calls
it from their servers; HMAC signature verification is the auth.
Everything else is admin-only.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from pydantic import BaseModel, Field

from solden.core.auth import TokenData, get_current_user
from solden.core.database import get_db
from solden.core.org_utils import require_org
from solden.services import paddle_billing as paddle_svc

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspace/billing", tags=["billing"])
webhook_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


# ─── Helpers ───────────────────────────────────────────────────────


def _require_admin(user: TokenData) -> None:
    from solden.core.auth import has_admin_access
    if not has_admin_access(user.role):
        raise HTTPException(status_code=403, detail="admin_role_required")


def _get_subscription_paddle_state(db, org_id: str) -> Dict[str, Any]:
    """Pull the org's current paddle state from the subscriptions row."""
    if not hasattr(db, "get_subscription_row"):
        return {}
    row = db.get_subscription_row(org_id) or {}
    return {
        "paddle_subscription_id": row.get("paddle_subscription_id"),
        "paddle_customer_id": row.get("paddle_customer_id"),
        "billing_collection_mode": row.get("billing_collection_mode") or "card",
        "billing_status": row.get("billing_status"),
        "next_billed_at": row.get("next_billed_at"),
        "plan": row.get("plan"),
    }


# ─── Customer-facing endpoints ────────────────────────────────────


class CheckoutRequest(BaseModel):
    plan: str = Field(..., pattern="^(free|starter|professional|enterprise)$")
    collection_mode: str = Field(default="card", pattern="^(card|invoice)$")
    return_url: Optional[str] = None


@router.post("/checkout")
def create_checkout(
    request: CheckoutRequest,
    user: TokenData = Depends(get_current_user),
):
    """Start a Paddle hosted-checkout flow.

    Returns ``{checkout_url}``; the SPA redirects the customer to it.
    On completion Paddle redirects to ``return_url`` and fires
    ``subscription.created`` to our webhook.
    """
    _require_admin(user)
    if not paddle_svc.is_configured():
        raise HTTPException(
            status_code=503,
            detail={"reason": "paddle_not_configured",
                    "message": "Paddle is not yet configured. Reach out to support to enable card billing."},
        )
    org_id = require_org(user)
    email = getattr(user, "email", None) or ""
    if not email:
        raise HTTPException(status_code=400, detail="email_required")
    result = paddle_svc.create_checkout_url(
        organization_id=org_id,
        customer_email=email,
        plan=request.plan,
        collection_mode=request.collection_mode,
        return_url=request.return_url,
    )
    if result.get("status") != "ok":
        raise HTTPException(status_code=502, detail=result)
    return result


@router.get("/portal")
def get_portal_url(
    user: TokenData = Depends(get_current_user),
):
    """Redirect URL into Paddle's hosted customer portal."""
    _require_admin(user)
    org_id = require_org(user)
    state = _get_subscription_paddle_state(get_db(), org_id)
    paddle_customer_id = state.get("paddle_customer_id")
    if not paddle_customer_id:
        raise HTTPException(
            status_code=404,
            detail={"reason": "no_paddle_customer",
                    "message": "No Paddle customer linked yet. Subscribe first."},
        )
    result = paddle_svc.get_billing_portal_url(paddle_customer_id)
    if result.get("status") != "ok":
        raise HTTPException(status_code=502, detail=result)
    return result


class CollectionModeRequest(BaseModel):
    mode: str = Field(..., pattern="^(card|invoice)$")


@router.patch("/collection-mode")
def patch_collection_mode(
    request: CollectionModeRequest,
    user: TokenData = Depends(get_current_user),
):
    """Flip card ↔ invoice billing on the org's active subscription.

    Module 11 spec: "ability to invoice for those who don't want
    card payment." Effective on next billing cycle; current cycle
    isn't reissued.
    """
    _require_admin(user)
    org_id = require_org(user)
    state = _get_subscription_paddle_state(get_db(), org_id)
    paddle_sub_id = state.get("paddle_subscription_id")
    if not paddle_sub_id:
        raise HTTPException(
            status_code=404,
            detail={"reason": "no_active_subscription",
                    "message": "No active Paddle subscription to update."},
        )
    result = paddle_svc.update_collection_mode(paddle_sub_id, mode=request.mode)
    if result.get("status") != "ok":
        raise HTTPException(status_code=502, detail=result)
    return {"status": "ok", "mode": request.mode}


@router.get("/price-preview")
def get_price_preview(
    request: Request,
    user: TokenData = Depends(get_current_user),
):
    """Localized plan prices for the buyer's location, via Paddle.

    Read-only and available to any authenticated workspace user (a member may
    want to see what an upgrade costs before asking an admin). Geolocates from
    the buyer's IP so EU/UK customers see their own currency instead of a
    hardcoded ``$``. Returns ``status != "ok"`` (handled gracefully by the SPA,
    which then renders its static labels) when Paddle isn't configured.
    """
    # First hop of X-Forwarded-For is the real client behind the proxy; fall
    # back to the direct peer.
    ip = None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        ip = xff.split(",")[0].strip()
    elif request.client:
        ip = request.client.host
    return paddle_svc.preview_plan_prices(
        ["starter", "professional", "enterprise"],
        ip_address=ip,
    )


@router.get("/invoices")
def list_billing_invoices(
    user: TokenData = Depends(get_current_user),
):
    """Return the org's invoice history snapshot (last 50)."""
    _require_admin(user)
    org_id = require_org(user)
    state = _get_subscription_paddle_state(get_db(), org_id)
    paddle_sub_id = state.get("paddle_subscription_id")
    if not paddle_sub_id:
        return {"invoices": [], "configured": paddle_svc.is_configured(),
                "billing_collection_mode": state.get("billing_collection_mode")}
    raw = paddle_svc.list_invoices(paddle_sub_id)
    # Slim the payload — we only render a few fields in the SPA.
    invoices = [
        {
            "id": tx.get("id"),
            "billed_at": tx.get("billed_at") or tx.get("created_at"),
            "status": tx.get("status"),
            "currency": tx.get("currency_code"),
            "amount": ((tx.get("details") or {}).get("totals") or {}).get("grand_total"),
            "invoice_number": tx.get("invoice_number"),
            "invoice_pdf_url": tx.get("invoice_pdf_url"),
        }
        for tx in raw[:50]
    ]
    return {
        "invoices": invoices,
        "configured": True,
        "billing_collection_mode": state.get("billing_collection_mode"),
        "billing_status": state.get("billing_status"),
        "next_billed_at": state.get("next_billed_at"),
    }


# ─── Paddle webhook sink (no auth — HMAC-signed) ──────────────────


@webhook_router.post("/paddle")
async def paddle_webhook(
    request: Request,
    paddle_signature: Optional[str] = Header(default=None, alias="Paddle-Signature"),
):
    """Receive Paddle event POSTs.

    Paddle signs the body with HMAC-SHA256 keyed on PADDLE_WEBHOOK_SECRET.
    We verify the signature, parse the event, dispatch to the
    handler in paddle_billing.handle_webhook_event. Always return
    200 on signature match (Paddle retries on non-200 for hours);
    handler errors are logged + counted, not 500'd back.
    """
    raw = await request.body()
    if not paddle_svc.verify_webhook_signature(
        raw_body=raw, signature_header=paddle_signature or "",
    ):
        # Refuse the call; Paddle will surface signature mismatches in
        # their dashboard so misconfiguration is visible.
        logger.warning("[paddle.webhook] signature mismatch")
        raise HTTPException(status_code=401, detail="invalid_signature")
    import json as _json
    try:
        event = _json.loads(raw)
    except Exception as exc:
        logger.warning("[paddle.webhook] body parse failed: %s", exc)
        raise HTTPException(status_code=400, detail="bad_body")
    db = get_db()
    try:
        result = paddle_svc.handle_webhook_event(db, event)
    except Exception as exc:
        logger.exception("[paddle.webhook] handler failed: %s", exc)
        result = {"status": "error", "message": str(exc)}
    return result
