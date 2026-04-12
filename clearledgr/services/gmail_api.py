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

# Configuration
def _default_google_redirect_uri() -> str:
    base = os.getenv("API_BASE_URL", "http://127.0.0.1:8010").strip().rstrip("/")
    if not base:
        base = "http://127.0.0.1:8010"
    return f"{base}/gmail/callback"
PUBSUB_TOPIC = os.getenv("GMAIL_PUBSUB_TOPIC", "projects/clearledgr/topics/gmail-push")
TOKEN_KEY_FILE = os.getenv("TOKEN_ENCRYPTION_KEY_FILE", ".clearledgr_token_key")


def get_google_oauth_config() -> Dict[str, str]:
    """Return current Google OAuth config from environment."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redirect_uri = (
        os.getenv("GOOGLE_GMAIL_REDIRECT_URI", "").strip()
        or os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    )
    if redirect_uri.rstrip("/").endswith("/auth/google/callback"):
        logger.warning(
            "GOOGLE_REDIRECT_URI points at /auth/google/callback, which is the workspace auth callback. "
            "Falling back to the Gmail callback path instead."
        )
        redirect_uri = ""
    if not redirect_uri:
        redirect_uri = _default_google_redirect_uri()
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }


def _is_placeholder(value: str) -> bool:
    normalized = (value or "").strip().lower()
    if not normalized:
        return True
    placeholder_markers = (
        "your-google-oauth-client-id",
        "your-google-oauth-client-secret",
        "your-project",
        "example",
        "changeme",
    )
    return any(marker in normalized for marker in placeholder_markers)


def validate_google_oauth_config(require_secret: bool = False) -> Dict[str, str]:
    """Validate Gmail OAuth env config and return it."""
    config = get_google_oauth_config()
    missing: List[str] = []
    if _is_placeholder(config["client_id"]):
        missing.append("GOOGLE_CLIENT_ID")
    if _is_placeholder(config["redirect_uri"]):
        missing.append("GOOGLE_GMAIL_REDIRECT_URI or GOOGLE_REDIRECT_URI")
    if require_secret and _is_placeholder(config["client_secret"]):
        missing.append("GOOGLE_CLIENT_SECRET")
    if missing:
        raise ValueError(
            "Gmail OAuth is not configured: missing "
            + ", ".join(missing)
            + ". Set these env vars and restart backend."
        )
    return config


def _load_encryption_key() -> bytes:
    """Load and derive a Fernet-compatible encryption key.

    In production, TOKEN_ENCRYPTION_KEY must be set.  In dev mode a key
    is generated once per process (tokens won't survive restarts unless
    the env var is set, which is acceptable for local development).

    The raw secret is hashed to produce a 32-byte key that Fernet accepts
    regardless of the original secret's format.
    """
    import hashlib
    from clearledgr.core.secrets import require_secret
    raw = require_secret("TOKEN_ENCRYPTION_KEY")
    derived = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(derived)


ENCRYPTION_KEY = _load_encryption_key()

# Gmail API endpoints
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GMAIL_PROFILE_URL = f"{GMAIL_API_BASE}/users/me/profile"

# Scopes needed for autonomous processing.
# Sheets scope enables reconciliation workflows (read bank statements, write results).
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",   # Read emails
    "https://www.googleapis.com/auth/gmail.modify",     # Manage labels, create drafts
    "https://www.googleapis.com/auth/gmail.send",       # Send emails (vendor follow-ups)
    "https://www.googleapis.com/auth/spreadsheets",     # Read/write Google Sheets (reconciliation)
]


def _utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _to_utc(value: datetime) -> datetime:
    """Normalize naive/aware datetime values to UTC-aware."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class GmailToken:
    """Represents stored Gmail OAuth tokens for a user."""
    user_id: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    email: str
    
    def is_expired(self) -> bool:
        return _utc_now() >= _to_utc(self.expires_at) - timedelta(minutes=5)


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
        self._fernet = Fernet(ENCRYPTION_KEY)
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
        """List all stored Gmail tokens, silently skipping tokens that cannot be decrypted."""
        rows = self.db.list_oauth_tokens("gmail")
        tokens = []
        for row in rows:
            try:
                tokens.append(self._row_to_token(row))
            except Exception:
                # Token was encrypted with a different key (e.g. after key rotation or test reset)
                pass
        return tokens
    
    def _row_to_token(self, row: Dict) -> GmailToken:
        """Convert database row to GmailToken."""
        expires_at = None
        if row.get('expires_at'):
            try:
                expires_at = datetime.fromisoformat(row['expires_at'].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                expires_at = _utc_now() + timedelta(hours=1)
        
        return GmailToken(
            user_id=row['user_id'],
            access_token=self._decrypt(row['access_token']),
            refresh_token=self._decrypt(row['refresh_token']) if row.get('refresh_token') else "",
            expires_at=_to_utc(expires_at) if expires_at else _utc_now() + timedelta(hours=1),
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
        messages = await client.list_messages(query="from:bank.com")
    """
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self._token: Optional[GmailToken] = None
    
    async def ensure_authenticated(self) -> bool:
        """Ensure we have valid credentials for this user.

        Returns False if no token exists or refresh fails.
        Distinguishes between "not authorized" and "refresh failed" via logging.
        """
        self._token = token_store.get(self.user_id)

        if not self._token:
            return False

        if self._token.is_expired():
            try:
                await self._refresh_token()
            except ValueError as exc:
                logger.error("Gmail token refresh failed for %s (re-auth required): %s", self.user_id, exc)
                return False
            except Exception as exc:
                logger.error("Gmail token refresh unexpected error for %s: %s", self.user_id, exc)
                return False

        return True
    
    async def _refresh_token(self) -> None:
        """Refresh an expired access token."""
        if not self._token:
            raise ValueError("No token to refresh")
        if not str(self._token.refresh_token or "").strip():
            raise ValueError("gmail_reconnect_required_missing_refresh_token")
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
            expires_at=_utc_now() + timedelta(seconds=data["expires_in"]),
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
        label_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        List messages matching a query.
        
        Args:
            query: Gmail search query (e.g., "from:bank.com has:attachment")
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
        if label_ids:
            params["labelIds"] = [str(label_id).strip() for label_id in label_ids if str(label_id).strip()]
        
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

    async def get_thread(self, thread_id: str, format: str = "full") -> List[GmailMessage]:
        """Get all messages in a Gmail thread."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/threads/{thread_id}",
                headers=self._headers(),
                params={"format": format},
            )
            response.raise_for_status()
            data = response.json()

        messages = data.get("messages", []) or []
        return [
            self._parse_message(message)
            for message in messages
            if isinstance(message, dict) and message.get("id")
        ]

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
            date = _utc_now()
        
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

        We subscribe to both ``messageAdded`` (new mail → agent intake)
        and ``labelAdded`` (bidirectional label sync: user drags a
        thread into Clearledgr/Invoice/Approved → the agent approves).

        Args:
            start_history_id: The history ID to start from

        Returns:
            Dict with history records (may contain messagesAdded and/or
            labelsAdded arrays per record)
        """
        async with httpx.AsyncClient() as client:
            # Gmail returns multiple historyTypes via repeated params
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/history",
                headers=self._headers(),
                params=[
                    ("startHistoryId", start_history_id),
                    ("historyTypes", "messageAdded"),
                    ("historyTypes", "labelAdded"),
                ],
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

    async def delete_label(self, label_id: str) -> None:
        """Delete a Gmail label."""
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{GMAIL_API_BASE}/users/me/labels/{label_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
    
    async def create_draft(
        self,
        thread_id: str,
        to: str,
        subject: str,
        body: str,
    ) -> str:
        """Create a Gmail draft as a reply to an existing thread.

        Uses the ``users.drafts.create`` endpoint which is covered by the
        existing ``gmail.modify`` OAuth scope — no scope changes required.

        Returns the draft ID (e.g. ``r123456789``) so the sidebar can link
        directly to ``https://mail.google.com/#drafts/<draft_id>``.
        """
        import base64
        import email.mime.text

        mime = email.mime.text.MIMEText(body, "plain")
        mime["To"] = to
        mime["Subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()

        payload: Dict[str, Any] = {
            "message": {
                "raw": raw,
                "threadId": thread_id,
            }
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/drafts",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("id", "")

    async def send_draft(self, draft_id: str) -> Dict[str, Any]:
        """Send an existing draft. Returns the sent message metadata.

        Requires the ``gmail.send`` OAuth scope.  After a successful send the
        draft is removed from the user's Drafts folder by Gmail automatically.
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/drafts/send",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"id": draft_id},
            )
            response.raise_for_status()
            return response.json()

    async def send_message(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: Optional[str] = None,
        in_reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send an email directly (no draft). Returns the sent message metadata.

        Requires the ``gmail.send`` OAuth scope.  Used for automated vendor
        follow-ups where the agent sends on behalf of the connected account.

        §11.1: Respects Gmail sendEmail quota (250/day for Google Workspace).
        Rate limiter caps at 10/hour per user — well within quota.
        """
        # §11.1: Gmail send rate limit check
        try:
            from clearledgr.integrations.erp_rate_limiter import get_erp_rate_limiter, ERPRateLimitError
            get_erp_rate_limiter().check_and_consume(self.user_id, "gmail_send")
        except ERPRateLimitError as exc:
            logger.warning("Gmail send rate limit hit for %s: %s", self.user_id, exc)
            return {"status": "rate_limited", "retry_after": exc.retry_after}
        except Exception:
            pass  # Rate limiter errors are non-fatal

        import email.mime.text

        msg = email.mime.text.MIMEText(body, "plain")
        msg["To"] = to
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        payload: Dict[str, Any] = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/messages/send",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def get_draft(self, draft_id: str, format: str = "raw") -> Dict[str, Any]:
        """Retrieve a draft by ID.

        Args:
            draft_id: The Gmail draft ID.
            format: Response format — ``raw`` returns the full MIME in
                ``message.raw``; ``full`` returns parsed payload.

        Returns:
            The draft resource dict from the Gmail API.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API_BASE}/users/me/drafts/{draft_id}",
                headers=self._headers(),
                params={"format": format},
            )
            response.raise_for_status()
            return response.json()

    async def schedule_draft_send(
        self,
        draft_id: str,
        send_at_unix_ms: int,
    ) -> Dict[str, Any]:
        """Send a draft as a scheduled message.

        Gmail does not expose a first-class "schedule" verb in its public
        REST API.  The supported server-side mechanism is:

        1. Retrieve the draft in ``raw`` format.
        2. Decode the MIME, inject the ``X-Google-Delayed-Sending`` header
           with the target timestamp (seconds since epoch).
        3. Delete the original draft.
        4. Create a *new* draft carrying the modified MIME — Gmail's
           backend honours the delay header and holds the message until the
           requested time.

        This keeps the message in the user's Drafts folder with a visible
        "Scheduled" chip in Gmail, matching the behaviour of the Gmail web
        UI "Schedule send" feature.

        Args:
            draft_id: The Gmail draft ID to schedule.
            send_at_unix_ms: Target send time as milliseconds since epoch.

        Returns:
            The new draft resource dict (with the scheduled header baked in).
        """
        import email as email_lib

        # 1. Fetch the raw MIME content of the existing draft.
        draft_data = await self.get_draft(draft_id, format="raw")
        raw_b64 = (draft_data.get("message") or {}).get("raw", "")
        thread_id = (draft_data.get("message") or {}).get("threadId")
        if not raw_b64:
            raise ValueError("draft_empty_or_missing_raw")

        raw_bytes = base64.urlsafe_b64decode(raw_b64)
        msg = email_lib.message_from_bytes(raw_bytes)

        # 2. Inject (or replace) the scheduled-send header.
        #    Gmail interprets this as "hold until <epoch-seconds>".
        send_at_secs = send_at_unix_ms // 1000
        header_name = "X-Google-Delayed-Sending"
        if header_name in msg:
            del msg[header_name]
        msg[header_name] = str(send_at_secs)

        new_raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

        # 3. Delete the original draft so we don't leave duplicates.
        async with httpx.AsyncClient() as client:
            await client.delete(
                f"{GMAIL_API_BASE}/users/me/drafts/{draft_id}",
                headers=self._headers(),
            )
            # Ignore 404 — draft may already have been removed.

        # 4. Create a replacement draft with the schedule header.
        payload: Dict[str, Any] = {"message": {"raw": new_raw}}
        if thread_id:
            payload["message"]["threadId"] = thread_id

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GMAIL_API_BASE}/users/me/drafts",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
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
    
    # Resolve user identity from OAuth token. Prefer Gmail profile because
    # Gmail scopes are guaranteed, while userinfo scopes may not be present.
    user_info: Dict[str, Any] = {}
    async with httpx.AsyncClient() as client:
        profile_response = await client.get(
            GMAIL_PROFILE_URL,
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        if profile_response.status_code < 400:
            profile = profile_response.json()
            user_info = {
                "id": profile.get("emailAddress") or "gmail-user",
                "email": profile.get("emailAddress", ""),
            }
        else:
            # Fallback for environments where Gmail profile endpoint is blocked.
            response = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {data['access_token']}"},
            )
            response.raise_for_status()
            user_info = response.json()
    
    return GmailToken(
        user_id=user_info["id"],
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_at=_utc_now() + timedelta(seconds=data["expires_in"]),
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
        "include_granted_scopes": "true",
    }
    if state:
        params["state"] = state
    
    query = urlencode(params)
    return f"{OAUTH_AUTH_URL}?{query}"
