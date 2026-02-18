"""Teams adapter and request verification helpers for AP approvals."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

import httpx
try:
    from jose import JWTError, jwt
    HAS_JOSE = True
except Exception:  # pragma: no cover
    JWTError = Exception
    jwt = None
    HAS_JOSE = False

logger = logging.getLogger(__name__)

TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL", "").strip()
TEAMS_SIGNING_SECRET = os.getenv("TEAMS_SIGNING_SECRET", "").strip()
TEAMS_BOT_APP_ID = os.getenv("TEAMS_BOT_APP_ID", "").strip()
TEAMS_OPENID_CONFIG_URL = os.getenv(
    "TEAMS_OPENID_CONFIG_URL",
    "https://login.botframework.com/v1/.well-known/openidconfiguration",
).strip()
TEAMS_LEGACY_HMAC_ALLOWED = str(
    os.getenv("TEAMS_LEGACY_HMAC_ALLOWED", "false")
).strip().lower() in {"1", "true", "yes", "on"}
TEAMS_ALLOWED_TENANT_IDS = {
    part.strip()
    for part in str(os.getenv("TEAMS_ALLOWED_TENANT_IDS", "")).split(",")
    if part.strip()
}
_OPENID_CACHE: Dict[str, Any] = {"expires_at": 0.0, "config": None, "jwks": None}
_OPENID_CACHE_TTL_SECONDS = int(os.getenv("TEAMS_OPENID_CACHE_TTL_SECONDS", "3600") or 3600)


@dataclass
class TeamsMessage:
    channel: str
    message_id: str
    text: str


class TeamsAPIClient:
    """Minimal Teams webhook client for AP approval notifications."""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = (webhook_url or TEAMS_WEBHOOK_URL or "").strip()

    async def send_approval_message(
        self,
        text: str,
        ap_item_id: str,
        vendor: str,
        amount: str,
        invoice_number: str,
        callback_url: Optional[str] = None,
        budget: Optional[Dict[str, Any]] = None,
    ) -> TeamsMessage:
        if not self.webhook_url:
            raise ValueError("Teams webhook URL not configured")

        actions_note = ""
        if callback_url:
            actions_note = (
                f"\nApproval actions are handled by your Teams bot and callback endpoint:\n"
                f"{callback_url}"
            )

        facts = [
            {"name": "AP Item", "value": ap_item_id},
            {"name": "Vendor", "value": vendor},
            {"name": "Amount", "value": amount},
            {"name": "Invoice", "value": invoice_number},
        ]
        budget_status = str((budget or {}).get("status") or "")
        if budget_status and budget_status != "not_requested":
            currency = str((budget or {}).get("currency") or "USD")
            remaining = (budget or {}).get("remaining")
            overage = (budget or {}).get("overage")
            if budget_status == "over_budget":
                facts.append({"name": "Budget", "value": f"Over by {currency} {overage or 0}"})
            elif budget_status == "within_budget":
                facts.append({"name": "Budget", "value": f"Remaining {currency} {remaining or 0}"})
            else:
                facts.append({"name": "Budget", "value": budget_status.replace("_", " ")})

        payload = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": "Clearledgr AP approval request",
            "title": "Clearledgr AP Approval Request",
            "sections": [
                {
                    "facts": facts,
                    "text": f"{text}{actions_note}",
                }
            ],
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(self.webhook_url, json=payload, timeout=20)
            response.raise_for_status()

        # Incoming webhook responses do not provide a stable message id.
        message_id = f"teams-webhook-{int(time.time())}"
        return TeamsMessage(channel="teams", message_id=message_id, text=text)

    @staticmethod
    def build_ap_kpi_digest_card(kpis: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
        touchless = float((kpis.get("touchless_rate") or {}).get("rate") or 0.0) * 100.0
        exceptions = float((kpis.get("exception_rate") or {}).get("rate") or 0.0) * 100.0
        on_time = float((kpis.get("on_time_approvals") or {}).get("rate") or 0.0) * 100.0
        cycle = kpis.get("cycle_time_hours") or {}
        friction = kpis.get("approval_friction") or {}
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": f"AP KPI digest for {organization_id}",
            "title": f"Clearledgr AP KPI Digest · {organization_id}",
            "sections": [
                {
                    "facts": [
                        {"name": "Touchless rate", "value": f"{touchless:.1f}%"},
                        {"name": "Exception rate", "value": f"{exceptions:.1f}%"},
                        {"name": "On-time approvals", "value": f"{on_time:.1f}%"},
                        {
                            "name": "Cycle time (avg/p95)",
                            "value": f"{float(cycle.get('avg') or 0.0):.1f}h / {float(cycle.get('p95') or 0.0):.1f}h",
                        },
                        {
                            "name": "Approval friction",
                            "value": (
                                f"SLA breaches {int(friction.get('sla_breach_count') or 0)}, "
                                f"avg handoffs {float(friction.get('avg_handoffs') or 0.0):.2f}, "
                                f"p95 wait {float(friction.get('p95_wait_minutes') or 0.0):.1f}m"
                            ),
                        },
                    ]
                }
            ],
        }


def verify_teams_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    signing_secret: Optional[str] = None,
    max_age_seconds: int = 300,
) -> bool:
    """
    Verify signed Teams callback requests.

    Uses an HMAC envelope:
      signature = v1=HMAC_SHA256(secret, "v1:{timestamp}:{raw_body}")
    """
    secret = (signing_secret or TEAMS_SIGNING_SECRET or "").strip()
    if not secret:
        logger.warning("Teams signature verification failed: signing secret is not configured")
        return False

    if not timestamp or not signature:
        return False

    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False

    if abs(time.time() - ts) > max_age_seconds:
        return False

    body_text = body.decode("utf-8", errors="replace")
    base = f"v1:{timestamp}:{body_text}"
    expected = "v1=" + hmac.new(secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _get_openid_materials() -> Dict[str, Any]:
    now = time.time()
    cached = _OPENID_CACHE
    if cached.get("config") and cached.get("jwks") and float(cached.get("expires_at") or 0) > now:
        return {"config": cached["config"], "jwks": cached["jwks"]}

    async with httpx.AsyncClient() as client:
        config_resp = await client.get(TEAMS_OPENID_CONFIG_URL, timeout=20)
        config_resp.raise_for_status()
        config = config_resp.json()
        jwks_uri = str(config.get("jwks_uri") or "").strip()
        if not jwks_uri:
            raise ValueError("Missing jwks_uri in Teams OpenID configuration")
        jwks_resp = await client.get(jwks_uri, timeout=20)
        jwks_resp.raise_for_status()
        jwks = jwks_resp.json()

    _OPENID_CACHE["config"] = config
    _OPENID_CACHE["jwks"] = jwks
    _OPENID_CACHE["expires_at"] = now + _OPENID_CACHE_TTL_SECONDS
    return {"config": config, "jwks": jwks}


def _extract_bearer_token(headers: Mapping[str, str]) -> str:
    auth = str(headers.get("authorization") or headers.get("Authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return ""
    return auth[7:].strip()


def _jwt_issuers(config_issuer: Optional[str]) -> set[str]:
    issuers = {
        "https://api.botframework.com",
        "https://sts.windows.net/d6d49420-f39b-4df7-a1dc-d59a935871db/",
    }
    raw = str(os.getenv("TEAMS_JWT_ISSUERS", "")).strip()
    if raw:
        issuers.update({part.strip() for part in raw.split(",") if part.strip()})
    if config_issuer:
        issuers.add(str(config_issuer).strip())
    return issuers


async def verify_teams_jwt(headers: Mapping[str, str]) -> bool:
    if not HAS_JOSE or jwt is None:
        logger.warning("Teams JWT verification failed: python-jose is not installed")
        return False
    token = _extract_bearer_token(headers)
    if not token:
        return False
    if not TEAMS_BOT_APP_ID:
        logger.warning("Teams JWT verification failed: TEAMS_BOT_APP_ID is not configured")
        return False

    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = str(unverified_header.get("kid") or "")
        if not kid:
            return False
    except Exception:
        return False

    try:
        materials = await _get_openid_materials()
        config = materials["config"] or {}
        jwks = materials["jwks"] or {}
        keys = jwks.get("keys") or []
        key = next((entry for entry in keys if entry.get("kid") == kid), None)
        if not key:
            return False

        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=TEAMS_BOT_APP_ID,
            options={"verify_at_hash": False},
        )
        issuer = str(claims.get("iss") or "")
        if issuer not in _jwt_issuers(config.get("issuer")):
            return False
        if TEAMS_ALLOWED_TENANT_IDS:
            tenant_id = str(claims.get("tid") or claims.get("tenantId") or "").strip()
            if not tenant_id or tenant_id not in TEAMS_ALLOWED_TENANT_IDS:
                return False
        return True
    except JWTError:
        return False
    except Exception as exc:
        logger.warning("Teams JWT verification failed: %s", exc)
        return False


async def verify_teams_request(body: bytes, headers: Mapping[str, str]) -> bool:
    """
    Verify Teams callbacks.

    Preferred: Bot Framework JWT verification.
    Optional fallback: legacy HMAC when TEAMS_LEGACY_HMAC_ALLOWED=true.
    """
    if await verify_teams_jwt(headers):
        return True

    if not TEAMS_LEGACY_HMAC_ALLOWED:
        return False

    timestamp = str(
        headers.get("x-teams-request-timestamp")
        or headers.get("X-Teams-Request-Timestamp")
        or ""
    )
    signature = str(
        headers.get("x-teams-signature")
        or headers.get("X-Teams-Signature")
        or ""
    )
    return verify_teams_signature(body, timestamp, signature)


def parse_teams_action_payload(body: bytes) -> Dict[str, Any]:
    """Parse JSON callback payload from Teams action bridge."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid Teams payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid Teams payload")
    return payload


def get_teams_client(webhook_url: Optional[str] = None) -> TeamsAPIClient:
    return TeamsAPIClient(webhook_url=webhook_url)
