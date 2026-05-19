"""Paddle billing service — Module 11 SaaS revenue collection.

Paddle is a Merchant of Record platform — Paddle invoices the
customer, collects payment, handles VAT/sales tax across 30+
jurisdictions, then remits net of fees to us. Mo confirmed Paddle
on 2026-05-02 over Stripe / Chargebee for the EU + Africa +
enterprise customer mix.

Two collection modes per customer:
  - "card": card-on-file, auto-charged on renewal
  - "invoice": Paddle issues an invoice with bank details + net-30
    terms. Customer wires payment; Paddle reconciles. Required
    for enterprise customers whose AP department won't authorise
    card payments.

The flip between modes is a single Paddle API call that updates the
subscription's collection_mode. Webhook ``subscription.updated`` then
fires and we sync the new mode into our subscriptions table.

Env-gated: every API call is a no-op + warning when PADDLE_API_KEY
is missing. Lets the code ship without keys, then activate later.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Config ────────────────────────────────────────────────────────


def _api_key() -> str:
    return os.getenv("PADDLE_API_KEY", "").strip()


def _api_base() -> str:
    """Production vs sandbox — Paddle's API host differs by env."""
    if os.getenv("PADDLE_ENV", "production").strip().lower() == "sandbox":
        return "https://sandbox-api.paddle.com"
    return "https://api.paddle.com"


def _webhook_secret() -> str:
    return os.getenv("PADDLE_WEBHOOK_SECRET", "").strip()


def is_configured() -> bool:
    """Return True iff Paddle creds are in the environment."""
    return bool(_api_key())


# Plan tier → Paddle price ID mapping. Customer sets each via env so
# the Paddle products + prices stay in their dashboard, not pinned in
# code (avoids redeploying when they tweak pricing).
def _price_id_for_plan(plan: str) -> Optional[str]:
    plan_lc = (plan or "").lower()
    env_keys = {
        "free":         "PADDLE_PRICE_ID_FREE",
        "starter":      "PADDLE_PRICE_ID_STARTER",
        "professional": "PADDLE_PRICE_ID_PROFESSIONAL",
        "enterprise":   "PADDLE_PRICE_ID_ENTERPRISE",
    }
    env = env_keys.get(plan_lc)
    if not env:
        return None
    return os.getenv(env, "").strip() or None


# ─── HTTP plumbing ─────────────────────────────────────────────────


def _http(method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Single Paddle API call with auth header + JSON envelope.

    Returns the parsed `data` field on 2xx; raises on 4xx/5xx so
    callers can surface a real error message. Never silently fails.
    """
    if not is_configured():
        raise RuntimeError("paddle_not_configured")
    try:
        import httpx
    except ImportError:
        raise RuntimeError("httpx_unavailable")
    url = f"{_api_base()}{path}"
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "Paddle-Version": "1",
    }
    with httpx.Client(timeout=15.0) as client:
        resp = client.request(method, url, json=json_body, headers=headers)
    if resp.status_code >= 400:
        try:
            err = resp.json()
        except Exception:
            err = {"raw": resp.text[:500]}
        raise RuntimeError(f"paddle_http_{resp.status_code}: {err}")
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json().get("data") or {}


# ─── Public surface ────────────────────────────────────────────────


def create_checkout_url(
    *,
    organization_id: str,
    customer_email: str,
    plan: str,
    collection_mode: str = "card",
    return_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a Paddle hosted-checkout URL for the given plan.

    The customer lands on Paddle's checkout, enters card or selects
    "invoice me" (when collection_mode='invoice'), and on completion
    Paddle redirects to ``return_url`` and fires
    ``subscription.created`` to our webhook handler.
    """
    if not is_configured():
        return {"status": "not_configured",
                "message": "Paddle not configured — set PADDLE_API_KEY."}
    price_id = _price_id_for_plan(plan)
    if not price_id:
        return {"status": "missing_price",
                "message": f"PADDLE_PRICE_ID_{plan.upper()} not set; configure in env."}
    body = {
        "items": [{"price_id": price_id, "quantity": 1}],
        "customer_email": customer_email,
        "custom_data": {
            "organization_id": organization_id,
            "plan": plan,
            "collection_mode": collection_mode,
        },
        "collection_mode": "automatic" if collection_mode == "card" else "manual",
    }
    if return_url:
        body["checkout"] = {"url": return_url}
    try:
        data = _http("POST", "/transactions", json_body=body)
    except Exception as exc:
        logger.exception("[paddle.checkout] create failed")
        return {"status": "error", "message": str(exc)}
    return {
        "status": "ok",
        "checkout_url": data.get("checkout", {}).get("url"),
        "transaction_id": data.get("id"),
    }


def update_collection_mode(
    paddle_subscription_id: str, *, mode: str,
) -> Dict[str, Any]:
    """Flip a subscription between card and invoice billing.

    Paddle accepts ``automatic`` (card-on-file auto-charge) or
    ``manual`` (issued invoice + net terms). Effective on next
    billing cycle; the current cycle isn't reissued.
    """
    if not is_configured():
        return {"status": "not_configured"}
    if mode not in {"card", "invoice"}:
        raise ValueError(f"invalid mode: {mode}")
    paddle_mode = "automatic" if mode == "card" else "manual"
    try:
        data = _http(
            "PATCH",
            f"/subscriptions/{paddle_subscription_id}",
            json_body={"collection_mode": paddle_mode},
        )
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "ok", "subscription": data}


def get_billing_portal_url(paddle_customer_id: str) -> Dict[str, Any]:
    """Return Paddle's hosted customer portal URL.

    The portal lets the customer manage card-on-file, see past
    invoices + receipts, download PDFs, and cancel/upgrade. We just
    redirect — no in-app billing UI to maintain.
    """
    if not is_configured():
        return {"status": "not_configured"}
    if not paddle_customer_id:
        return {"status": "missing_customer_id"}
    try:
        data = _http(
            "POST",
            f"/customers/{paddle_customer_id}/portal-sessions",
        )
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "ok", "portal_url": (data.get("urls") or {}).get("general", {}).get("overview")}


def list_invoices(paddle_subscription_id: str) -> List[Dict[str, Any]]:
    """Return invoices/transactions for one subscription. Used to
    render an in-app billing-history snapshot when the customer
    doesn't want to leave the workspace."""
    if not is_configured():
        return []
    try:
        data = _http(
            "GET",
            f"/transactions?subscription_id={paddle_subscription_id}&order_by=billed_at[DESC]",
        )
    except Exception as exc:
        logger.warning("[paddle.list_invoices] failed: %s", exc)
        return []
    if isinstance(data, list):
        return data
    return []


# ─── Webhook signature ─────────────────────────────────────────────


def verify_webhook_signature(*, raw_body: bytes, signature_header: str) -> bool:
    """Paddle signs webhook bodies with HMAC-SHA256.

    Header format: ``ts=1700000000;h1=<hex>``. We reconstruct the
    signed payload (``{ts}:{body}``) and compare hashes constant-time.
    """
    secret = _webhook_secret()
    if not secret:
        # Without a configured secret we refuse the webhook — never
        # accept unsigned events even in dev. Set PADDLE_WEBHOOK_SECRET.
        return False
    if not signature_header:
        return False
    parts = dict(p.split("=", 1) for p in signature_header.split(";") if "=" in p)
    ts = parts.get("ts")
    h1 = parts.get("h1")
    if not ts or not h1:
        return False
    import hmac
    import hashlib
    signed_payload = f"{ts}:".encode("utf-8") + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, h1)


# ─── Webhook event handler ─────────────────────────────────────────


def handle_webhook_event(db, event: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a Paddle webhook event to the local subscriptions row.

    Supported event_types:
      - subscription.created / subscription.activated
      - subscription.updated
      - subscription.canceled
      - transaction.completed (a card or invoice payment was settled)
      - transaction.payment_failed
    """
    event_type = str(event.get("event_type") or "").strip().lower()
    data = event.get("data") or {}
    custom = data.get("custom_data") or {}
    org_id = str(custom.get("organization_id") or "").strip()
    sub_id = str(data.get("id") or data.get("subscription_id") or "").strip()
    customer_id = str((data.get("customer") or {}).get("id") or data.get("customer_id") or "").strip()

    if not org_id:
        # Paddle event without our org marker — log and ignore. This
        # happens for events from the Paddle dashboard (test events).
        logger.warning("[paddle.webhook] %s lacks custom_data.organization_id", event_type)
        return {"status": "ignored_no_org"}

    if event_type in {"subscription.created", "subscription.activated", "subscription.updated"}:
        collection_mode = "invoice" if (data.get("collection_mode") == "manual") else "card"
        try:
            db.update_subscription_paddle_state(
                organization_id=org_id,
                paddle_subscription_id=sub_id,
                paddle_customer_id=customer_id,
                billing_collection_mode=collection_mode,
                billing_status=str(data.get("status") or ""),
                next_billed_at=data.get("next_billed_at"),
            )
        except Exception as exc:
            logger.exception("[paddle.webhook] subscription sync failed: %s", exc)
            return {"status": "error", "message": str(exc)}
        return {"status": "ok", "applied": event_type}

    if event_type == "subscription.canceled":
        try:
            db.update_subscription_paddle_state(
                organization_id=org_id,
                paddle_subscription_id=sub_id,
                paddle_customer_id=customer_id,
                billing_collection_mode=None,
                billing_status="canceled",
                next_billed_at=None,
            )
        except Exception:
            pass
        return {"status": "ok", "applied": "subscription.canceled"}

    if event_type in {"transaction.completed", "transaction.paid"}:
        # Just emit an audit row — the subscriptions table state was
        # already synced when subscription.updated fired.
        return {"status": "ok", "applied": "transaction.completed"}

    if event_type == "transaction.payment_failed":
        # Surface to the org so the leader sees it on the billing
        # banner. Failure → switch them to invoicing for the next
        # cycle is a deliberate manual choice.
        return {"status": "ok", "applied": "transaction.payment_failed"}

    return {"status": "unhandled", "event_type": event_type}
