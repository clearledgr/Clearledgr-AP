"""
Gmail API Client for Clearledgr

Provides server-side access to Gmail for autonomous email processing.
Uses OAuth 2.0 for authorization and supports:
- Fetching emails
- Reading attachments
- Setting up watch notifications (Pub/Sub)
- Managing labels
"""

import base64
import json
import os
import logging
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import httpx
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)

# Configuration
DEFAULT_GOOGLE_REDIRECT_URI = "http://localhost:8000/gmail/callback"
PUBSUB_TOPIC = os.getenv("GMAIL_PUBSUB_TOPIC", "projects/clearledgr/topics/gmail-push")
TOKEN_KEY_FILE = os.getenv("TOKEN_ENCRYPTION_KEY_FILE", ".clearledgr_token_key")


def get_google_oauth_config() -> Dict[str, str]:
    """Return current Google OAuth config from environment."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", DEFAULT_GOOGLE_REDIRECT_URI).strip()
    if not redirect_uri:
        redirect_uri = DEFAULT_GOOGLE_REDIRECT_URI
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }


def validate_google_oauth_config(require_secret: bool = False) -> Dict[str, str]:
    """Validate Gmail OAuth env config and return it."""
    config = get_google_oauth_config()
    missing: List[str] = []
    if not config["client_id"]:
        missing.append("GOOGLE_CLIENT_ID")
    if not config["redirect_uri"]:
        missing.append("GOOGLE_REDIRECT_URI")
    if require_secret and not config["client_secret"]:
        missing.append("GOOGLE_CLIENT_SECRET")
    if missing:
        raise ValueError(
            "Gmail OAuth is not configured: missing "
            + ", ".join(missing)
            + ". Set these env vars and restart backend."
        )
    return config


def _load_or_create_key(path: str) -> str:
    """Load encryption key from disk, or create and persist it if missing."""
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                key = fh.read().strip()
                if key:
                    return key
        # Create and persist a new key (dev-friendly, still stable across restarts)
        key = Fernet.generate_key().decode()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(key)
        return key
    except Exception as exc:
        logger.warning("Failed to persist token encryption key: %s", exc)
        return Fernet.generate_key().decode()


ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    ENCRYPTION_KEY = _load_or_create_key(TOKEN_KEY_FILE)

# Gmail API endpoints
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

# Scopes needed for autonomous processing
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",  # Read emails
    "https://www.googleapis.com/auth/gmail.modify",    # Manage labels
]


@dataclass
class GmailToken:
    """Represents stored Gmail OAuth tokens for a user."""
    user_id: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    email: str
    
    def is_expired(self) -> bool:
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return utc_now() >= expires_at - timedelta(minutes=5)


@dataclass
class GmailMessage:
    """Represents a Gmail message."""
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


class GmailTokenStore:
    """
    Secure storage for Gmail OAuth tokens using database persistence.
    Tokens are encrypted before storage.
    """
    
    def __init__(self):
        self._fernet = Fernet(ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY)
        self._db = None
    
    @property
    def db(self):
        """Lazy load database to avoid circular imports."""
        if self._db is None:
            from clearledgr.core.database import get_db
            self._db = get_db()
        return self._db
    
    def _encrypt(self, value: str) -> str:
        """Encrypt a token value."""
        return self._fernet.encrypt(value.encode()).decode()
    
    def _decrypt(self, value: str) -> str:
        """Decrypt a token value."""
        return self._fernet.decrypt(value.encode()).decode()
    
    def store(self, token: GmailToken) -> None:
        """Store a token securely in the database."""
        self.db.save_oauth_token(
            user_id=token.user_id,
            provider="gmail",
            access_token=self._encrypt(token.access_token),
            refresh_token=self._encrypt(token.refresh_token) if token.refresh_token else None,
            expires_at=token.expires_at.isoformat() if token.expires_at else None,
            email=token.email
        )
    
    def get(self, user_id: str) -> Optional[GmailToken]:
        """Retrieve a token for a user from the database."""
        row = self.db.get_oauth_token(user_id, "gmail")
        if not row:
            return None
        return self._row_to_token(row)
    
    def delete(self, user_id: str) -> None:
        """Remove a token from the database."""
        self.db.delete_oauth_token(user_id, "gmail")
    
    def get_by_email(self, email: str) -> Optional[GmailToken]:
        """Find token by email address."""
        row = self.db.get_oauth_token_by_email(email, "gmail")
        if not row:
            return None
        return self._row_to_token(row)
    
    def list_all(self) -> List[GmailToken]:
        """List all stored Gmail tokens."""
        rows = self.db.list_oauth_tokens("gmail")
        return [self._row_to_token(row) for row in rows]
    
    def _row_to_token(self, row: Dict) -> GmailToken:
        """Convert database row to GmailToken."""
        expires_at = None
        if row.get('expires_at'):
            try:
                expires_at = datetime.fromisoformat(row['expires_at'].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                expires_at = utc_now() + timedelta(hours=1)
        
        return GmailToken(
            user_id=row['user_id'],
            access_token=self._decrypt(row['access_token']),
            refresh_token=self._decrypt(row['refresh_token']) if row.get('refresh_token') else "",
            expires_at=expires_at or utc_now() + timedelta(hours=1),
            email=row.get('email', '')
        )


# Global token store instance
token_store = GmailTokenStore()


class GmailAPIClient:
    """
    Gmail API client for server-side email access.
    
    Usage:
        client = GmailAPIClient(user_id="user123")
        await client.ensure_authenticated()
        messages = await client.list_messages(query="has:attachment invoice")
    """
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self._token: Optional[GmailToken] = None
    
    async def ensure_authenticated(self) -> bool:
        """Ensure we have valid credentials for this user."""
        self._token = token_store.get(self.user_id)
        
        if not self._token:
            return False
        
        if self._token.is_expired():
            await self._refresh_token()
        
        return True
    
    async def _refresh_token(self) -> None:
        """Refresh an expired access token."""
        if not self._token:
            raise ValueError("No token to refresh")
        oauth = validate_google_oauth_config(require_secret=False)
        payload = {
            "client_id": oauth["client_id"],
            "refresh_token": self._token.refresh_token,
            "grant_type": "refresh_token",
        }
        # Keep optional for public clients where secret may not be used.
        if oauth["client_secret"]:
            payload["client_secret"] = oauth["client_secret"]
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                OAUTH_TOKEN_URL,
                data=payload
            )
            response.raise_for_status()
            data = response.json()
        
        # Update token
        self._token = GmailToken(
            user_id=self._token.user_id,
            access_token=data["access_token"],
            refresh_token=self._token.refresh_token,  # Keep existing refresh token
            expires_at=utc_now() + timedelta(seconds=data["expires_in"]),
            email=self._token.email,
        )
        token_store.store(self._token)
    
    def _headers(self) -> Dict[str, str]:
        """Get authorization headers."""
        if not self._token:
            raise ValueError("Not authenticated")
        return {
            "Authorization": f"Bearer {self._token.access_token}",
            "Accept": "application/json",
        }
    
    async def list_messages(
        self,
        query: str = "",
        max_results: int = 100,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List messages matching a query.
        
        Args:
            query: Gmail search query (e.g., "has:attachment invoice")
            max_results: Maximum number of messages to return
            page_token: Token for pagination
        
        Returns:
            Dict with 'messages' list and optional 'nextPageToken'
        """
        params = {
            "maxResults": max_results,
        }
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/messages",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
            return response.json()
    
    async def get_message(self, message_id: str, format: str = "full") -> GmailMessage:
        """
        Get a specific message by ID.
        
        Args:
            message_id: The message ID
            format: 'full', 'metadata', 'minimal', or 'raw'
        
        Returns:
            GmailMessage object
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/messages/{message_id}",
                headers=self._headers(),
                params={"format": format},
            )
            response.raise_for_status()
            data = response.json()
        
        return self._parse_message(data)
    
    def _parse_message(self, data: Dict[str, Any]) -> GmailMessage:
        """Parse Gmail API response into GmailMessage."""
        headers = {h["name"].lower(): h["value"] for h in data.get("payload", {}).get("headers", [])}
        
        # Extract body
        attachments = []
        
        payload = data.get("payload", {})
        body_text, body_html, attachments = self._extract_parts(payload, "", "", attachments)
        
        # Parse date
        date_str = headers.get("date", "")
        try:
            # Try common date formats
            from email.utils import parsedate_to_datetime
            date = parsedate_to_datetime(date_str)
        except Exception:
            date = datetime.utcnow()
        
        return GmailMessage(
            id=data["id"],
            thread_id=data["threadId"],
            subject=headers.get("subject", "(No Subject)"),
            sender=headers.get("from", ""),
            recipient=headers.get("to", ""),
            date=date,
            snippet=data.get("snippet", ""),
            body_text=body_text,
            body_html=body_html,
            labels=data.get("labelIds", []),
            attachments=attachments,
        )
    
    def _extract_parts(
        self,
        payload: Dict[str, Any],
        body_text: str,
        body_html: str,
        attachments: List[Dict[str, Any]],
    ) -> tuple:
        """Recursively extract body and attachments from message parts."""
        mime_type = payload.get("mimeType", "")
        
        # Check for body data
        body = payload.get("body", {})
        if body.get("data"):
            decoded = base64.urlsafe_b64decode(body["data"]).decode("utf-8", errors="ignore")
            if "text/plain" in mime_type:
                body_text = decoded
            elif "text/html" in mime_type:
                body_html = decoded
        
        # Check for attachments
        if body.get("attachmentId"):
            attachments.append({
                "id": body["attachmentId"],
                "filename": payload.get("filename", "attachment"),
                "mime_type": mime_type,
                "size": body.get("size", 0),
            })
        
        # Recurse into parts
        for part in payload.get("parts", []):
            body_text, body_html, attachments = self._extract_parts(part, body_text, body_html, attachments)
        
        return body_text, body_html, attachments
    
    async def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """
        Download an attachment.
        
        Args:
            message_id: The message ID
            attachment_id: The attachment ID
        
        Returns:
            Raw attachment bytes
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/messages/{message_id}/attachments/{attachment_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
        
        # Decode base64url encoded data
        return base64.urlsafe_b64decode(data["data"])
    
    async def get_history(self, start_history_id: str) -> Dict[str, Any]:
        """
        Get history of changes since a history ID.
        Used to process only new emails after a Pub/Sub notification.
        
        Args:
            start_history_id: The history ID to start from
        
        Returns:
            Dict with history records
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/history",
                headers=self._headers(),
                params={
                    "startHistoryId": start_history_id,
                    "historyTypes": ["messageAdded"],
                },
            )
            
            if response.status_code == 404:
                # History ID too old, need full sync
                return {"history": [], "needsFullSync": True}
            
            response.raise_for_status()
            return response.json()
    
    async def add_label(self, message_id: str, label_ids: List[str]) -> None:
        """Add labels to a message."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/messages/{message_id}/modify",
                headers=self._headers(),
                json={"addLabelIds": label_ids},
            )
            response.raise_for_status()

    async def send_thread_note(self, thread_id: str, to_email: str, subject: str, body: str) -> None:
        """Send a short note into an existing thread (idempotency handled upstream)."""
        if not thread_id:
            return
        message = "\r\n".join([
            f"To: {to_email}",
            f"Subject: {subject}",
            "Content-Type: text/plain; charset=\"UTF-8\"",
            "",
            body,
        ])
        raw = base64.urlsafe_b64encode(message.encode("utf-8")).decode("utf-8")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/messages/send",
                headers=self._headers(),
                json={"raw": raw, "threadId": thread_id},
            )
            response.raise_for_status()
    
    async def remove_label(self, message_id: str, label_ids: List[str]) -> None:
        """Remove labels from a message."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/messages/{message_id}/modify",
                headers=self._headers(),
                json={"removeLabelIds": label_ids},
            )
            response.raise_for_status()
    
    async def create_label(self, name: str) -> Dict[str, Any]:
        """Create a new label."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/labels",
                headers=self._headers(),
                json={
                    "name": name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            response.raise_for_status()
            return response.json()
    
    async def list_labels(self) -> List[Dict[str, Any]]:
        """List all labels."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/labels",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json().get("labels", [])


class GmailWatchService:
    """
    Manages Gmail watch subscriptions for Pub/Sub notifications.
    """
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.client = GmailAPIClient(user_id)
    
    async def setup_watch(self) -> Dict[str, Any]:
        """
        Set up a watch on the user's inbox.
        
        Returns:
            Dict with historyId and expiration
        """
        if not await self.client.ensure_authenticated():
            raise ValueError("User not authenticated")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/watch",
                headers=self.client._headers(),
                json={
                    "topicName": PUBSUB_TOPIC,
                    "labelIds": ["INBOX"],
                    "labelFilterBehavior": "INCLUDE",
                },
            )
            response.raise_for_status()
            return response.json()
    
    async def stop_watch(self) -> None:
        """Stop watching the user's inbox."""
        if not await self.client.ensure_authenticated():
            return
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/stop",
                headers=self.client._headers(),
            )
            # 404 is OK - means no watch was active
            if response.status_code not in [200, 204, 404]:
                response.raise_for_status()


async def exchange_code_for_tokens(code: str, redirect_uri: Optional[str] = None) -> GmailToken:
    """
    Exchange an authorization code for tokens.
    
    Args:
        code: The authorization code from OAuth callback
    
    Returns:
        GmailToken with access and refresh tokens
    """
    oauth = validate_google_oauth_config(require_secret=False)
    resolved_redirect_uri = (redirect_uri or oauth["redirect_uri"]).strip() or oauth["redirect_uri"]
    payload = {
        "client_id": oauth["client_id"],
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": resolved_redirect_uri,
    }
    if oauth["client_secret"]:
        payload["client_secret"] = oauth["client_secret"]

    async with httpx.AsyncClient() as client:
        response = await client.post(
            OAUTH_TOKEN_URL,
            data=payload,
        )
        response.raise_for_status()
        data = response.json()
    
    # Get user info to get email
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        response.raise_for_status()
        user_info = response.json()
    
    return GmailToken(
        user_id=user_info["id"],
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_at=utc_now() + timedelta(seconds=data["expires_in"]),
        email=user_info["email"],
    )


def generate_auth_url(state: str = "") -> str:
    """
    Generate the OAuth authorization URL.
    
    Args:
        state: Optional state parameter for CSRF protection
    
    Returns:
        URL to redirect user to for authorization
    """
    oauth = validate_google_oauth_config(require_secret=False)
    params = {
        "client_id": oauth["client_id"],
        "redirect_uri": oauth["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "access_type": "offline",  # Get refresh token
        "prompt": "consent",  # Always show consent screen
    }
    if state:
        params["state"] = state
    
    query = urlencode(params)
    return f"{OAUTH_AUTH_URL}?{query}"
