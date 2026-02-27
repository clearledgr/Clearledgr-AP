"""
Microsoft Outlook / Graph API Client for Clearledgr

Mirrors the Gmail API client pattern exactly.  Uses Microsoft Graph API
with OAuth 2.0 for autonomous email processing.

Supports:
- Fetching messages via Microsoft Graph
- Reading attachments
- Setting up change notifications (Graph subscriptions)
- Marking messages as processed
"""

import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MS_AUTH_BASE = "https://login.microsoftonline.com"

OUTLOOK_SCOPES = ["Mail.Read", "Mail.ReadWrite", "offline_access"]

DEFAULT_OUTLOOK_REDIRECT_URI = "http://localhost:8000/outlook/callback"
CLEARLEDGR_CATEGORY = "Clearledgr/Processed"


def get_ms_oauth_config() -> Dict[str, str]:
    """Return current Microsoft OAuth config from environment."""
    tenant = os.getenv("MICROSOFT_TENANT_ID", "common").strip() or "common"
    return {
        "client_id": os.getenv("MICROSOFT_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("MICROSOFT_CLIENT_SECRET", "").strip(),
        "redirect_uri": os.getenv(
            "MICROSOFT_REDIRECT_URI", DEFAULT_OUTLOOK_REDIRECT_URI
        ).strip() or DEFAULT_OUTLOOK_REDIRECT_URI,
        "tenant": tenant,
        "auth_url": f"{MS_AUTH_BASE}/{tenant}/oauth2/v2.0/authorize",
        "token_url": f"{MS_AUTH_BASE}/{tenant}/oauth2/v2.0/token",
    }


def _is_placeholder(value: str) -> bool:
    normalized = (value or "").strip().lower()
    if not normalized:
        return True
    for marker in ("your-", "example", "changeme", "placeholder"):
        if marker in normalized:
            return True
    return False


def validate_ms_oauth_config(require_secret: bool = False) -> Dict[str, str]:
    """Validate Microsoft OAuth env config."""
    cfg = get_ms_oauth_config()
    missing = []
    if _is_placeholder(cfg["client_id"]):
        missing.append("MICROSOFT_CLIENT_ID")
    if _is_placeholder(cfg["redirect_uri"]):
        missing.append("MICROSOFT_REDIRECT_URI")
    if require_secret and _is_placeholder(cfg["client_secret"]):
        missing.append("MICROSOFT_CLIENT_SECRET")
    if missing:
        raise ValueError(
            "Outlook OAuth is not configured: missing "
            + ", ".join(missing)
            + ". Set these env vars and restart backend."
        )
    return cfg


# ---------------------------------------------------------------------------
# Encryption (reuses same pattern as GmailTokenStore)
# ---------------------------------------------------------------------------


def _load_encryption_key() -> bytes:
    from clearledgr.core.secrets import require_secret
    raw = require_secret("TOKEN_ENCRYPTION_KEY")
    derived = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(derived)


ENCRYPTION_KEY = _load_encryption_key()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class OutlookToken:
    """Stored Outlook OAuth tokens for a user."""
    user_id: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    email: str

    def is_expired(self) -> bool:
        return _utc_now() >= _to_utc(self.expires_at) - timedelta(minutes=5)


@dataclass
class OutlookMessage:
    """Normalized Outlook message (same shape as GmailMessage)."""
    id: str
    thread_id: str
    subject: str
    sender: str
    recipient: str
    date: datetime
    snippet: str
    body_text: str
    body_html: str
    labels: List[str]
    attachments: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Token store — reuses oauth_tokens table with provider="outlook"
# ---------------------------------------------------------------------------


class OutlookTokenStore:
    """Secure storage for Outlook OAuth tokens using the shared oauth_tokens table."""

    def __init__(self):
        self._fernet = Fernet(ENCRYPTION_KEY)
        self._db = None

    @property
    def db(self):
        if self._db is None:
            from clearledgr.core.database import get_db
            self._db = get_db()
        return self._db

    def _encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def _decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode()).decode()

    def store(self, token: OutlookToken) -> None:
        self.db.save_oauth_token(
            user_id=token.user_id,
            provider="outlook",
            access_token=self._encrypt(token.access_token),
            refresh_token=self._encrypt(token.refresh_token) if token.refresh_token else None,
            expires_at=token.expires_at.isoformat() if token.expires_at else None,
            email=token.email,
        )

    def get(self, user_id: str) -> Optional[OutlookToken]:
        row = self.db.get_oauth_token(user_id, "outlook")
        if not row:
            return None
        return self._row_to_token(row)

    def get_by_email(self, email: str) -> Optional[OutlookToken]:
        """Find token by email address (for webhook routing)."""
        row = self.db.get_oauth_token_by_email(email, "outlook")
        if not row:
            return None
        return self._row_to_token(row)

    def delete(self, user_id: str) -> None:
        self.db.delete_oauth_token(user_id, "outlook")

    def _row_to_token(self, row: Dict[str, Any]) -> OutlookToken:
        try:
            access_token = self._decrypt(row["access_token"])
        except Exception:
            access_token = row["access_token"]
        try:
            refresh_token = self._decrypt(row["refresh_token"]) if row.get("refresh_token") else ""
        except Exception:
            refresh_token = row.get("refresh_token", "")
        expires_at = datetime.fromisoformat(row["expires_at"]) if row.get("expires_at") else _utc_now() + timedelta(hours=1)
        return OutlookToken(
            user_id=row["user_id"],
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            email=row.get("email", ""),
        )


# Shared singleton
token_store = OutlookTokenStore()


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------


def generate_outlook_auth_url(user_id: str, redirect_uri: Optional[str] = None) -> str:
    """Generate Microsoft OAuth authorization URL."""
    cfg = validate_ms_oauth_config()
    params = {
        "client_id": cfg["client_id"],
        "response_type": "code",
        "redirect_uri": redirect_uri or cfg["redirect_uri"],
        "scope": " ".join(OUTLOOK_SCOPES),
        "state": user_id,
        "response_mode": "query",
        "prompt": "consent",
    }
    return f"{cfg['auth_url']}?{urlencode(params)}"


async def exchange_outlook_code_for_tokens(code: str, user_id: str, redirect_uri: Optional[str] = None) -> OutlookToken:
    """Exchange authorization code for access/refresh tokens."""
    cfg = validate_ms_oauth_config(require_secret=True)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            cfg["token_url"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri or cfg["redirect_uri"],
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "scope": " ".join(OUTLOOK_SCOPES),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()

    expires_in = data.get("expires_in", 3600)
    expires_at = _utc_now() + timedelta(seconds=expires_in)

    # Fetch user email from Graph
    async with httpx.AsyncClient(timeout=15) as client:
        me_resp = await client.get(
            f"{GRAPH_BASE}/me",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        me_data = me_resp.json() if me_resp.status_code == 200 else {}

    email = me_data.get("mail") or me_data.get("userPrincipalName") or user_id

    token = OutlookToken(
        user_id=user_id,
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_at=expires_at,
        email=email,
    )
    token_store.store(token)
    return token


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


class OutlookAPIClient:
    """Microsoft Graph API client for reading Outlook/Exchange mail."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._token: Optional[OutlookToken] = None

    async def ensure_authenticated(self) -> bool:
        """Load token and refresh if expired. Returns True if ready."""
        token = token_store.get(self.user_id)
        if not token:
            logger.warning("No Outlook token for user %s", self.user_id)
            return False
        if token.is_expired():
            try:
                token = await self._refresh_token(token)
            except Exception as exc:
                logger.error("Outlook token refresh failed for %s: %s", self.user_id, exc)
                return False
        self._token = token
        return True

    async def _refresh_token(self, token: OutlookToken) -> OutlookToken:
        """Refresh an expired access token via refresh_token grant."""
        cfg = get_ms_oauth_config()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                cfg["token_url"],
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": token.refresh_token,
                    "client_id": cfg["client_id"],
                    "client_secret": cfg.get("client_secret", ""),
                    "scope": " ".join(OUTLOOK_SCOPES),
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()

        expires_at = _utc_now() + timedelta(seconds=data.get("expires_in", 3600))
        new_token = OutlookToken(
            user_id=token.user_id,
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token") or token.refresh_token,
            expires_at=expires_at,
            email=token.email,
        )
        token_store.store(new_token)
        return new_token

    def _headers(self) -> Dict[str, str]:
        assert self._token, "Call ensure_authenticated() first"
        return {
            "Authorization": f"Bearer {self._token.access_token}",
            "Accept": "application/json",
        }

    async def list_messages(
        self,
        filter_query: str = "",
        max_results: int = 50,
        skip_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List messages from the inbox. Returns Graph API page."""
        params: Dict[str, Any] = {
            "$top": max_results,
            "$select": "id,subject,from,receivedDateTime,bodyPreview,hasAttachments,conversationId",
            "$orderby": "receivedDateTime DESC",
        }
        if filter_query:
            params["$filter"] = filter_query
        if skip_token:
            params["$skipToken"] = skip_token

        url = f"{GRAPH_BASE}/me/messages"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def get_message(self, message_id: str) -> OutlookMessage:
        """Fetch a full message with attachment metadata."""
        url = f"{GRAPH_BASE}/me/messages/{message_id}"
        params = {
            "$expand": "attachments",
            "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,body,hasAttachments,conversationId,categories",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        return self._parse_message(data)

    async def get_attachment(self, message_id: str, attachment_id: str) -> Dict[str, Any]:
        """Fetch attachment bytes. Returns dict with contentBytes (base64), name, contentType."""
        url = f"{GRAPH_BASE}/me/messages/{message_id}/attachments/{attachment_id}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def mark_as_processed(self, message_id: str) -> None:
        """Tag message with Clearledgr category so it's not reprocessed."""
        url = f"{GRAPH_BASE}/me/messages/{message_id}"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.patch(
                url,
                json={"categories": [CLEARLEDGR_CATEGORY]},
                headers={**self._headers(), "Content-Type": "application/json"},
            )
            if resp.status_code not in (200, 204):
                logger.warning("Could not mark message %s as processed: %s", message_id, resp.status_code)

    def _parse_message(self, data: Dict[str, Any]) -> OutlookMessage:
        """Normalize Graph API message shape to match GmailMessage structure."""
        from_field = data.get("from") or {}
        sender_addr = (from_field.get("emailAddress") or {})
        sender = sender_addr.get("address") or ""
        sender_name = sender_addr.get("name") or sender

        to_list = data.get("toRecipients") or []
        recipient = ""
        if to_list:
            recipient = (to_list[0].get("emailAddress") or {}).get("address", "")

        received_raw = data.get("receivedDateTime", "")
        try:
            date = datetime.fromisoformat(received_raw.replace("Z", "+00:00"))
        except Exception:
            date = _utc_now()

        body = data.get("body") or {}
        body_html = body.get("content", "") if body.get("contentType") == "html" else ""
        body_text = body.get("content", "") if body.get("contentType") == "text" else ""
        # If HTML, try stripping tags for plain text
        if body_html and not body_text:
            import re
            body_text = re.sub(r"<[^>]+>", " ", body_html).strip()

        attachments = []
        for att in data.get("attachments") or []:
            if att.get("@odata.type") == "#microsoft.graph.fileAttachment":
                attachments.append({
                    "id": att.get("id", ""),
                    "filename": att.get("name", "attachment"),
                    "mimeType": att.get("contentType", "application/octet-stream"),
                    "size": att.get("size", 0),
                })

        categories = data.get("categories") or []

        return OutlookMessage(
            id=data["id"],
            thread_id=data.get("conversationId", data["id"]),
            subject=data.get("subject") or "(no subject)",
            sender=f"{sender_name} <{sender}>" if sender_name != sender else sender,
            recipient=recipient,
            date=date,
            snippet=data.get("bodyPreview", "")[:500],
            body_text=body_text,
            body_html=body_html,
            labels=categories,
            attachments=attachments,
        )


# ---------------------------------------------------------------------------
# Subscription service (Graph change notifications)
# ---------------------------------------------------------------------------


class OutlookSubscriptionService:
    """Manage Microsoft Graph change notification subscriptions."""

    SUBSCRIPTION_EXPIRY_MINUTES = 4230  # ~3 days (Graph max is 4320 for mail)

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._client = OutlookAPIClient(user_id)

    async def create_subscription(self, notification_url: str, client_state: str) -> Dict[str, Any]:
        """Subscribe to new mail events for this user."""
        await self._client.ensure_authenticated()
        expiry = _utc_now() + timedelta(minutes=self.SUBSCRIPTION_EXPIRY_MINUTES)
        payload = {
            "changeType": "created",
            "notificationUrl": notification_url,
            "resource": "/me/messages",
            "expirationDateTime": expiry.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
            "clientState": client_state,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/subscriptions",
                json=payload,
                headers={**self._client._headers(), "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

    async def renew_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Extend a subscription's expiry."""
        await self._client.ensure_authenticated()
        expiry = _utc_now() + timedelta(minutes=self.SUBSCRIPTION_EXPIRY_MINUTES)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.patch(
                f"{GRAPH_BASE}/subscriptions/{subscription_id}",
                json={"expirationDateTime": expiry.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")},
                headers={**self._client._headers(), "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_subscription(self, subscription_id: str) -> None:
        """Remove a subscription."""
        await self._client.ensure_authenticated()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                f"{GRAPH_BASE}/subscriptions/{subscription_id}",
                headers=self._client._headers(),
            )
            if resp.status_code not in (200, 204):
                logger.warning("Failed to delete subscription %s: %s", subscription_id, resp.status_code)
