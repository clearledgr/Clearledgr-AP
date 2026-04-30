"""
Microsoft Graph API client for Outlook email access.

Mirrors gmail_api.py — provides token management, email listing/reading,
and OAuth helpers for Microsoft 365 / Outlook integration.

Microsoft Graph API docs: https://learn.microsoft.com/en-us/graph/api/overview
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.http_client import get_http_client
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Encryption (shared with gmail_api.py pattern)
# ---------------------------------------------------------------------------

def _load_encryption_key() -> bytes:
    raw = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    if not raw:
        from clearledgr.core.secrets import require_secret
        raw = require_secret("TOKEN_ENCRYPTION_KEY")
    derived = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(derived)


ENCRYPTION_KEY = _load_encryption_key()

# ---------------------------------------------------------------------------
# Microsoft Graph endpoints
# ---------------------------------------------------------------------------

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
MS_AUTH_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
MS_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

# Scopes for Outlook email processing
OUTLOOK_SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",            # Needed for refresh tokens
    "Mail.Read",                 # Read emails
    "Mail.ReadWrite",            # Manage folders/categories
    "Mail.Send",                 # Send emails (vendor follow-ups)
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------

def validate_microsoft_oauth_config() -> Dict[str, str]:
    """Return Microsoft OAuth config from env, raising if essential vars are missing."""
    client_id = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
    client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("MICROSOFT_REDIRECT_URI", "").strip()
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "common").strip()

    if not client_id:
        raise ValueError("MICROSOFT_CLIENT_ID not set")
    if not client_secret:
        raise ValueError("MICROSOFT_CLIENT_SECRET not set")
    if not redirect_uri:
        raise ValueError("MICROSOFT_REDIRECT_URI not set")

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "tenant_id": tenant_id,
    }


def is_outlook_configured() -> bool:
    """Check if Microsoft OAuth is configured (non-empty client ID + secret)."""
    return bool(
        os.getenv("MICROSOFT_CLIENT_ID", "").strip()
        and os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
    )


# ---------------------------------------------------------------------------
# Token dataclass + store
# ---------------------------------------------------------------------------

@dataclass
class OutlookToken:
    """Stored Microsoft OAuth tokens for a user."""
    user_id: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    email: str

    def is_expired(self) -> bool:
        return _utc_now() >= _to_utc(self.expires_at) - timedelta(minutes=5)


@dataclass
class OutlookMessage:
    """Represents an Outlook email message."""
    id: str
    conversation_id: str
    subject: str
    sender: str
    recipient: str
    date: datetime
    snippet: str
    body_text: str
    body_html: str
    categories: List[str]
    attachments: List[Dict[str, Any]]
    has_attachments: bool


class OutlookTokenStore:
    """Secure storage for Microsoft OAuth tokens.  Mirrors GmailTokenStore."""

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

    def delete(self, user_id: str) -> None:
        self.db.delete_oauth_token(user_id, "outlook")

    def get_by_email(self, email: str) -> Optional[OutlookToken]:
        row = self.db.get_oauth_token_by_email(email, "outlook")
        if not row:
            return None
        return self._row_to_token(row)

    def list_all(self) -> List[OutlookToken]:
        rows = self.db.list_oauth_tokens("outlook")
        tokens = []
        for row in rows:
            try:
                tokens.append(self._row_to_token(row))
            except Exception:
                pass
        return tokens

    def _row_to_token(self, row: Dict) -> OutlookToken:
        expires_at = None
        if row.get("expires_at"):
            try:
                expires_at = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                expires_at = _utc_now() + timedelta(hours=1)

        return OutlookToken(
            user_id=row["user_id"],
            access_token=self._decrypt(row["access_token"]),
            refresh_token=self._decrypt(row["refresh_token"]) if row.get("refresh_token") else "",
            expires_at=_to_utc(expires_at) if expires_at else _utc_now() + timedelta(hours=1),
            email=row.get("email", ""),
        )


# Global token store instance
outlook_token_store = OutlookTokenStore()


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def generate_auth_url(state: str = "") -> str:
    """Generate Microsoft OAuth authorization URL."""
    config = validate_microsoft_oauth_config()
    tenant = config["tenant_id"]
    url = MS_AUTH_URL.format(tenant=tenant)
    params = {
        "client_id": config["client_id"],
        "response_type": "code",
        "redirect_uri": config["redirect_uri"],
        "response_mode": "query",
        "scope": " ".join(OUTLOOK_SCOPES),
        "state": state,
        "prompt": "select_account",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{url}?{query}"


async def exchange_code_for_tokens(code: str) -> OutlookToken:
    """Exchange authorization code for access + refresh tokens."""
    config = validate_microsoft_oauth_config()
    tenant = config["tenant_id"]
    token_url = MS_TOKEN_URL.format(tenant=tenant)

    client = get_http_client()
    response = await client.post(
        token_url,
        data={
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "code": code,
            "redirect_uri": config["redirect_uri"],
            "grant_type": "authorization_code",
            "scope": " ".join(OUTLOOK_SCOPES),
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    access_token = data["access_token"]
    refresh_token = data.get("refresh_token", "")
    expires_in = data.get("expires_in", 3600)

    # Resolve user identity
    client = get_http_client()
    profile_resp = await client.get(
        f"{GRAPH_API_BASE}/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    profile_resp.raise_for_status()
    profile = profile_resp.json()

    email = profile.get("mail") or profile.get("userPrincipalName") or ""
    user_id = profile.get("id") or email

    return OutlookToken(
        user_id=user_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=_utc_now() + timedelta(seconds=expires_in),
        email=email,
    )


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class OutlookAPIClient:
    """Microsoft Graph API client for server-side email access.

    Usage:
        client = OutlookAPIClient(user_id="user123")
        await client.ensure_authenticated()
        messages = await client.list_messages(folder="Inbox")
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._token: Optional[OutlookToken] = None

    async def ensure_authenticated(self) -> bool:
        self._token = outlook_token_store.get(self.user_id)
        if not self._token:
            return False
        if self._token.is_expired():
            try:
                await self._refresh_token()
            except Exception as exc:
                logger.warning("Outlook token refresh failed for %s: %s", self.user_id, exc)
                return False
        return True

    async def _refresh_token(self) -> None:
        if not self._token:
            raise ValueError("No token to refresh")
        if not str(self._token.refresh_token or "").strip():
            raise ValueError("outlook_reconnect_required_missing_refresh_token")

        config = validate_microsoft_oauth_config()
        tenant = config["tenant_id"]
        token_url = MS_TOKEN_URL.format(tenant=tenant)

        client = get_http_client()
        response = await client.post(
            token_url,
            data={
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "refresh_token": self._token.refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(OUTLOOK_SCOPES),
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        self._token = OutlookToken(
            user_id=self._token.user_id,
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", self._token.refresh_token),
            expires_at=_utc_now() + timedelta(seconds=data.get("expires_in", 3600)),
            email=self._token.email,
        )
        outlook_token_store.store(self._token)

    def _headers(self) -> Dict[str, str]:
        if not self._token:
            raise ValueError("Not authenticated")
        return {
            "Authorization": f"Bearer {self._token.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def list_messages(
        self,
        folder: str = "Inbox",
        filter_query: str = "",
        top: int = 50,
        skip: int = 0,
        select: str = "id,conversationId,subject,from,toRecipients,receivedDateTime,bodyPreview,hasAttachments,categories",
    ) -> Dict[str, Any]:
        """List messages from a mail folder.

        Uses OData query parameters ($filter, $top, $skip, $select).
        """
        params: Dict[str, Any] = {
            "$top": top,
            "$skip": skip,
            "$select": select,
            "$orderby": "receivedDateTime desc",
        }
        if filter_query:
            params["$filter"] = filter_query

        url = f"{GRAPH_API_BASE}/me/mailFolders/{folder}/messages"
        client = get_http_client()
        response = await client.get(
            url, headers=self._headers(), params=params, timeout=30,
        )
        response.raise_for_status()
        return response.json()

    async def get_message(self, message_id: str) -> OutlookMessage:
        """Get a specific message by ID with full body and attachment metadata."""
        url = f"{GRAPH_API_BASE}/me/messages/{message_id}"
        params = {"$expand": "attachments"}

        client = get_http_client()
        response = await client.get(
            url, headers=self._headers(), params=params, timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        return self._parse_message(data)

    async def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Download an attachment's raw bytes."""
        url = f"{GRAPH_API_BASE}/me/messages/{message_id}/attachments/{attachment_id}"
        client = get_http_client()
        response = await client.get(
            url, headers=self._headers(), timeout=60,
        )
        response.raise_for_status()
        data = response.json()

        content_bytes = data.get("contentBytes", "")
        return base64.b64decode(content_bytes) if content_bytes else b""

    async def send_message(
        self,
        to: str,
        subject: str,
        body: str,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send an email via Microsoft Graph."""
        message = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        }
        if conversation_id:
            message["conversationId"] = conversation_id

        url = f"{GRAPH_API_BASE}/me/sendMail"
        client = get_http_client()
        response = await client.post(
            url,
            headers=self._headers(),
            json={"message": message, "saveToSentItems": True},
            timeout=30,
        )
        response.raise_for_status()
        return {"status": "sent", "to": to}

    async def create_category(self, name: str, color: str = "preset0") -> Dict[str, Any]:
        """Create an Outlook category (equivalent of Gmail label)."""
        url = f"{GRAPH_API_BASE}/me/outlook/masterCategories"
        client = get_http_client()
        response = await client.post(
            url,
            headers=self._headers(),
            json={"displayName": name, "color": color},
            timeout=15,
        )
        if response.status_code == 409:
            return {"displayName": name, "exists": True}
        response.raise_for_status()
        return response.json()

    async def add_category(self, message_id: str, category_name: str) -> None:
        """Add a category to a message (equivalent of Gmail add_label)."""
        msg = await self.get_message(message_id)
        categories = list(set(msg.categories + [category_name]))
        url = f"{GRAPH_API_BASE}/me/messages/{message_id}"
        client = get_http_client()
        response = await client.patch(
            url,
            headers=self._headers(),
            json={"categories": categories},
            timeout=15,
        )
        response.raise_for_status()

    # ------------------------------------------------------------------
    # Change notification subscription (equivalent of Gmail Watch)
    # ------------------------------------------------------------------

    async def create_subscription(
        self,
        webhook_url: str,
        client_state: str = "",
        expiration_minutes: int = 4230,  # Max 3 days for mail
    ) -> Dict[str, Any]:
        """Create a Graph change notification subscription for mail."""
        url = f"{GRAPH_API_BASE}/subscriptions"
        payload = {
            "changeType": "created",
            "notificationUrl": webhook_url,
            "resource": "me/mailFolders('Inbox')/messages",
            "expirationDateTime": (
                _utc_now() + timedelta(minutes=expiration_minutes)
            ).strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
            "clientState": client_state,
        }
        client = get_http_client()
        response = await client.post(
            url, headers=self._headers(), json=payload, timeout=30,
        )
        response.raise_for_status()
        return response.json()

    async def renew_subscription(self, subscription_id: str, expiration_minutes: int = 4230) -> Dict[str, Any]:
        """Renew an existing subscription."""
        url = f"{GRAPH_API_BASE}/subscriptions/{subscription_id}"
        payload = {
            "expirationDateTime": (
                _utc_now() + timedelta(minutes=expiration_minutes)
            ).strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
        }
        client = get_http_client()
        response = await client.patch(
            url, headers=self._headers(), json=payload, timeout=30,
        )
        response.raise_for_status()
        return response.json()

    async def delete_subscription(self, subscription_id: str) -> None:
        """Delete a subscription."""
        url = f"{GRAPH_API_BASE}/subscriptions/{subscription_id}"
        client = get_http_client()
        response = await client.delete(
            url, headers=self._headers(), timeout=15,
        )
        if response.status_code != 404:
            response.raise_for_status()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_message(data: Dict[str, Any]) -> OutlookMessage:
        sender_data = data.get("from", {}).get("emailAddress", {})
        recipients = data.get("toRecipients") or []
        to_email = ""
        if recipients:
            to_email = recipients[0].get("emailAddress", {}).get("address", "")

        received = data.get("receivedDateTime", "")
        try:
            date_obj = datetime.fromisoformat(received.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            date_obj = _utc_now()

        body = data.get("body", {})
        body_text = ""
        body_html = ""
        if body.get("contentType") == "text":
            body_text = body.get("content", "")
        else:
            body_html = body.get("content", "")

        attachments = []
        for att in data.get("attachments", []):
            if att.get("@odata.type") == "#microsoft.graph.fileAttachment":
                attachments.append({
                    "id": att.get("id"),
                    "name": att.get("name"),
                    "contentType": att.get("contentType"),
                    "size": att.get("size", 0),
                })

        return OutlookMessage(
            id=data.get("id", ""),
            conversation_id=data.get("conversationId", ""),
            subject=data.get("subject", ""),
            sender=sender_data.get("address", ""),
            recipient=to_email,
            date=date_obj,
            snippet=data.get("bodyPreview", ""),
            body_text=body_text,
            body_html=body_html,
            categories=data.get("categories", []),
            attachments=attachments,
            has_attachments=data.get("hasAttachments", False),
        )
