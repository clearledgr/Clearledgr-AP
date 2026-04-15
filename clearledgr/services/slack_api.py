"""
Slack API Client for Clearledgr

Provides server-side access to Slack for:
- Sending notifications
- Updating threads
- Interactive messages with buttons
- Slash command responses
- Direct messages

Uses Bot Token for API access.
"""

import os
import json
import hmac
import hashlib
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import httpx
from clearledgr.core.http_client import get_http_client

logger = logging.getLogger(__name__)

# Configuration
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_API_BASE = "https://slack.com/api"


def _is_placeholder_slack_token(token: Optional[str]) -> bool:
    value = str(token or "").strip()
    if not value:
        return True
    lowered = value.lower()
    return lowered in {
        "xoxb-your-bot-token",
        "your-slack-bot-token",
        "placeholder",
    }


@dataclass
class SlackMessage:
    """Represents a Slack message."""
    channel: str
    ts: str  # Message timestamp (ID)
    text: str
    user: Optional[str] = None
    thread_ts: Optional[str] = None
    blocks: Optional[List[Dict]] = None


class SlackAPIClient:
    """
    Slack API client for sending messages and managing interactions.
    
    Usage:
        client = SlackAPIClient()
        await client.send_message("#finance", "Invoice processed!")
    """
    
    def __init__(self, bot_token: Optional[str] = None, user_lookup_token: Optional[str] = None):
        self.bot_token = bot_token or SLACK_BOT_TOKEN
        self.user_lookup_token = user_lookup_token or None
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        token_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Make authenticated API request."""
        token = token_override or self.bot_token
        if not token:
            raise ValueError("Slack bot token not configured")
        
        url = f"{SLACK_API_BASE}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        
        client = get_http_client()
        if method == "GET":
            response = await client.get(url, headers=headers, params=params, timeout=30)
        else:
            response = await client.post(url, headers=headers, json=data, timeout=30)

        result = response.json()

        if not result.get("ok"):
            error = result.get("error", "Unknown error")
            raise SlackAPIError(error, result)

        return result

    async def auth_test(self) -> Dict[str, Any]:
        """Validate the bot token and return the Slack auth context."""
        return await self._request("POST", "auth.test", data={})
    
    # ==================== MESSAGING ====================
    
    async def send_message(
        self,
        channel: str,
        text: str,
        blocks: Optional[List[Dict]] = None,
        thread_ts: Optional[str] = None,
        reply_broadcast: bool = False,
        unfurl_links: bool = True,
        unfurl_media: bool = True
    ) -> SlackMessage:
        """
        Send a message to a channel.
        
        Args:
            channel: Channel ID or name (e.g., "#finance" or "C1234567")
            text: Fallback text (required for accessibility)
            blocks: Block Kit blocks for rich formatting
            thread_ts: Reply to a specific thread
            reply_broadcast: Also post to channel when replying to thread
        
        Returns:
            SlackMessage with the sent message info
        """
        data = {
            "channel": channel,
            "text": text,
            "unfurl_links": unfurl_links,
            "unfurl_media": unfurl_media,
        }
        
        if blocks:
            data["blocks"] = blocks
        if thread_ts:
            data["thread_ts"] = thread_ts
            data["reply_broadcast"] = reply_broadcast
        
        result = await self._request("POST", "chat.postMessage", data)

        ts = result.get("ts", "")
        if not ts:
            logger.warning(
                "Slack chat.postMessage returned empty ts for channel=%s; message updates will fail",
                channel,
            )

        return SlackMessage(
            channel=result.get("channel", channel),
            ts=ts,
            text=text,
            blocks=blocks,
            thread_ts=thread_ts
        )
    
    async def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: Optional[List[Dict]] = None
    ) -> SlackMessage:
        """Update an existing message."""
        data = {
            "channel": channel,
            "ts": ts,
            "text": text,
        }
        
        if blocks:
            data["blocks"] = blocks
        
        result = await self._request("POST", "chat.update", data)
        
        return SlackMessage(
            channel=result.get("channel", channel),
            ts=result.get("ts", ts),
            text=text,
            blocks=blocks
        )
    
    async def delete_message(self, channel: str, ts: str) -> bool:
        """Delete a message."""
        data = {"channel": channel, "ts": ts}
        await self._request("POST", "chat.delete", data)
        return True
    
    async def add_reaction(self, channel: str, ts: str, emoji: str) -> bool:
        """Add a reaction to a message."""
        data = {"channel": channel, "timestamp": ts, "name": emoji}
        await self._request("POST", "reactions.add", data)
        return True
    
    async def remove_reaction(self, channel: str, ts: str, emoji: str) -> bool:
        """Remove a reaction from a message."""
        data = {"channel": channel, "timestamp": ts, "name": emoji}
        await self._request("POST", "reactions.remove", data)
        return True
    
    # ==================== SEARCH ====================
    
    async def search_messages(
        self,
        query: str,
        count: int = 20,
        sort: str = "timestamp",
        sort_dir: str = "desc"
    ) -> List[Dict[str, Any]]:
        """
        Search for messages.
        
        Args:
            query: Search query (e.g., "from:@clearledgr invoice")
            count: Number of results
            sort: Sort by "timestamp" or "score"
        """
        params = {
            "query": query,
            "count": count,
            "sort": sort,
            "sort_dir": sort_dir,
        }
        
        result = await self._request("GET", "search.messages", params=params)
        return result.get("messages", {}).get("matches", [])
    
    async def find_thread_by_text(
        self,
        channel: str,
        search_text: str,
        limit: int = 10
    ) -> Optional[str]:
        """Find a thread containing specific text."""
        messages = await self.search_messages(
            f"in:{channel} {search_text}",
            count=limit
        )
        
        for msg in messages:
            if search_text.lower() in msg.get("text", "").lower():
                return msg.get("ts")
        
        return None
    
    # ==================== CHANNELS ====================
    
    async def get_channel_info(self, channel: str) -> Dict[str, Any]:
        """Get information about a channel."""
        result = await self._request("GET", "conversations.info", params={"channel": channel})
        return result.get("channel", {})

    async def resolve_channel(self, channel: str) -> Optional[Dict[str, Any]]:
        """Resolve a channel id or #name into Slack channel metadata."""
        token = str(channel or "").strip()
        if not token:
            return None

        normalized_name = token[1:] if token.startswith("#") else token
        if token[:1] in {"C", "G"}:
            try:
                return await self.get_channel_info(token)
            except SlackAPIError:
                return None

        channels = await self.list_channels(limit=1000)
        lowered = normalized_name.lower()
        for row in channels:
            row_name = str(row.get("name") or "").strip()
            row_id = str(row.get("id") or "").strip()
            if lowered and row_name.lower() == lowered:
                return row
            if token and row_id == token:
                return row
        return None
    
    async def list_channels(
        self,
        types: str = "public_channel,private_channel",
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List channels the bot has access to."""
        result = await self._request(
            "GET", 
            "conversations.list",
            params={"types": types, "limit": limit}
        )
        return result.get("channels", [])
    
    async def get_channel_history(
        self,
        channel: str,
        limit: int = 100,
        oldest: Optional[str] = None,
        latest: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get message history from a channel."""
        params = {"channel": channel, "limit": limit}
        if oldest:
            params["oldest"] = oldest
        if latest:
            params["latest"] = latest
        
        result = await self._request("GET", "conversations.history", params=params)
        return result.get("messages", [])
    
    async def get_thread_replies(
        self,
        channel: str,
        thread_ts: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get replies in a thread."""
        params = {"channel": channel, "ts": thread_ts, "limit": limit}
        result = await self._request("GET", "conversations.replies", params=params)
        return result.get("messages", [])
    
    # ==================== USERS ====================
    
    async def get_user_info(self, user_id: str, *, prefer_user_token: bool = False) -> Dict[str, Any]:
        """Get information about a user."""
        token_override = self.user_lookup_token if prefer_user_token and self.user_lookup_token else None
        result = await self._request("GET", "users.info", params={"user": user_id}, token_override=token_override)
        return result.get("user", {})
    
    async def lookup_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Find a user by email address."""
        try:
            result = await self._request(
                "GET",
                "users.lookupByEmail",
                params={"email": email},
                token_override=self.user_lookup_token or None,
            )
            return result.get("user")
        except SlackAPIError as e:
            if e.error == "users_not_found":
                return None
            raise

    @staticmethod
    def normalize_user_reference(reference: Any) -> str:
        token = str(reference or "").strip()
        if token.startswith("<@") and token.endswith(">"):
            token = token[2:-1].strip()
        return token

    @staticmethod
    def is_probable_user_id(reference: Any) -> bool:
        token = SlackAPIClient.normalize_user_reference(reference)
        return bool(token) and token[0] in {"U", "W"} and token[1:].isalnum()

    @staticmethod
    def format_user_mention(user_id: str) -> str:
        token = SlackAPIClient.normalize_user_reference(user_id)
        return f"<@{token}>" if token else ""

    async def resolve_user_targets(self, references: List[Any]) -> Dict[str, List[str]]:
        """Resolve mixed emails / Slack user IDs into delivery targets and mentions."""
        from clearledgr.core.database import get_db

        db = get_db()
        delivery_ids: List[str] = []
        mentions: List[str] = []
        labels: List[str] = []
        unresolved: List[str] = []
        seen_ids = set()
        seen_labels = set()

        for raw in references or []:
            token = self.normalize_user_reference(raw)
            if not token:
                continue

            if token not in seen_labels:
                labels.append(token)
                seen_labels.add(token)

            resolved_id = ""
            if self.is_probable_user_id(token):
                resolved_id = token
            elif "@" in token:
                cached_user = None
                try:
                    cached_user = db.get_user_by_email(token)
                except Exception:
                    cached_user = None
                cached_id = str((cached_user or {}).get("slack_user_id") or "").strip()
                if self.is_probable_user_id(cached_id):
                    resolved_id = self.normalize_user_reference(cached_id)
                else:
                    slack_user = await self.lookup_user_by_email(token)
                    slack_id = self.normalize_user_reference((slack_user or {}).get("id"))
                    if self.is_probable_user_id(slack_id):
                        resolved_id = slack_id
                        if cached_user:
                            try:
                                db.update_user(cached_user["id"], slack_user_id=slack_id)
                            except Exception:
                                pass

            if resolved_id and resolved_id not in seen_ids:
                seen_ids.add(resolved_id)
                delivery_ids.append(resolved_id)
                mentions.append(self.format_user_mention(resolved_id))
            elif not resolved_id:
                unresolved.append(token)

        return {
            "delivery_ids": delivery_ids,
            "mentions": mentions,
            "labels": labels,
            "unresolved": unresolved,
        }
    
    # ==================== DIRECT MESSAGES ====================
    
    async def open_dm(self, user_id: str) -> str:
        """Open a DM channel with a user. Returns channel ID."""
        result = await self._request("POST", "conversations.open", {"users": user_id})
        return result.get("channel", {}).get("id", "")
    
    async def send_dm(
        self,
        user_id: str,
        text: str,
        blocks: Optional[List[Dict]] = None
    ) -> SlackMessage:
        """Send a direct message to a user."""
        channel = await self.open_dm(user_id)
        return await self.send_message(channel, text, blocks=blocks)
    
    # ==================== BLOCK KIT BUILDERS ====================
    
    @staticmethod
    def _kpi_percent(metric: Any) -> float:
        """Normalize KPI metric shapes into a display percentage."""
        if isinstance(metric, dict):
            raw = metric.get("value", metric.get("rate"))
        else:
            raw = metric
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0
        if 0 <= value <= 1:
            return value * 100.0
        return value

    @staticmethod
    def _kpi_hours(metric: Any) -> float:
        if isinstance(metric, dict):
            raw = metric.get("avg_hours", metric.get("avg"))
        else:
            raw = metric
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def build_ap_kpi_digest_text(kpis: Dict[str, Any], organization_id: str) -> str:
        """Build compact AP KPI digest text with AX6 agentic telemetry."""
        payload = kpis or {}
        agentic = payload.get("agentic_telemetry") if isinstance(payload, dict) else {}
        agentic = agentic if isinstance(agentic, dict) else {}

        touchless = SlackAPIClient._kpi_percent(payload.get("touchless_rate"))
        exception_rate = SlackAPIClient._kpi_percent(payload.get("exception_rate"))
        accepted_rate = SlackAPIClient._kpi_percent(agentic.get("agent_suggestion_acceptance"))
        manual_override_rate = SlackAPIClient._kpi_percent(agentic.get("agent_actions_requiring_manual_override"))
        awaiting_hours = SlackAPIClient._kpi_hours(agentic.get("awaiting_approval_time_hours"))
        return (
            f"AP KPI digest ({organization_id}) · "
            f"touchless {touchless:.1f}% · exceptions {exception_rate:.1f}% · "
            f"agent accepted {accepted_rate:.1f}% · "
            f"manual override {manual_override_rate:.1f}% · awaiting approval {awaiting_hours:.1f}h"
        )

    @staticmethod
    def build_ap_kpi_digest_blocks(kpis: Dict[str, Any], organization_id: str) -> List[Dict[str, Any]]:
        payload = kpis or {}
        agentic = payload.get("agentic_telemetry") if isinstance(payload, dict) else {}
        agentic = agentic if isinstance(agentic, dict) else {}

        touchless = SlackAPIClient._kpi_percent(payload.get("touchless_rate"))
        exception_rate = SlackAPIClient._kpi_percent(payload.get("exception_rate"))
        cycle_time = SlackAPIClient._kpi_hours(payload.get("cycle_time_hours"))
        on_time = SlackAPIClient._kpi_percent(payload.get("on_time_approvals"))
        straight_through = SlackAPIClient._kpi_percent(agentic.get("straight_through_rate"))
        human_intervention = SlackAPIClient._kpi_percent(agentic.get("human_intervention_rate"))
        suggestion_acceptance = SlackAPIClient._kpi_percent(agentic.get("agent_suggestion_acceptance"))
        manual_override = SlackAPIClient._kpi_percent(agentic.get("agent_actions_requiring_manual_override"))
        awaiting_hours = SlackAPIClient._kpi_hours(agentic.get("awaiting_approval_time_hours"))
        window_hours = int(agentic.get("window_hours") or 0)

        blocker_reasons = []
        top_blockers = agentic.get("top_blocker_reasons") if isinstance(agentic, dict) else {}
        if isinstance(top_blockers, dict):
            rows = top_blockers.get("top_reasons")
            if isinstance(rows, list):
                for entry in rows[:3]:
                    if not isinstance(entry, dict):
                        continue
                    reason = str(entry.get("reason") or "").replace("_", " ").strip()
                    count = int(entry.get("count") or 0)
                    if reason:
                        blocker_reasons.append(f"{reason} ({count})")
        blockers_text = " · ".join(blocker_reasons) if blocker_reasons else "No blocker telemetry yet"

        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"AP KPI Digest ({organization_id})"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Touchless:*\n{touchless:.1f}%"},
                    {"type": "mrkdwn", "text": f"*Exceptions:*\n{exception_rate:.1f}%"},
                    {"type": "mrkdwn", "text": f"*Cycle time:*\n{cycle_time:.1f}h"},
                    {"type": "mrkdwn", "text": f"*On-time approvals:*\n{on_time:.1f}%"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Agentic telemetry*"
                        + (f" ({window_hours}h window)" if window_hours > 0 else "")
                        + "\n"
                        f"Straight-through: {straight_through:.1f}% · Human intervention: {human_intervention:.1f}%\n"
                        f"Agent accepted: {suggestion_acceptance:.1f}% · "
                        f"Manual override required: {manual_override:.1f}% · Awaiting approval: {awaiting_hours:.1f}h"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Top blockers: {blockers_text}"},
                ],
            },
        ]

    @staticmethod
    def build_approval_blocks(
        title: str,
        details: Dict[str, str],
        approve_action_id: str,
        reject_action_id: str,
        item_id: str,
        request_info_action_id: str = "request_info",
    ) -> List[Dict]:
        """Build Block Kit blocks for an approval request."""
        fields = [
            {"type": "mrkdwn", "text": f"*{k}:*\n{v}"}
            for k, v in details.items()
        ]
        
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": title}
            },
            {
                "type": "section",
                "fields": fields[:10]  # Slack limit
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": f"{approve_action_id}_{item_id}",
                        "value": item_id
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "action_id": f"{reject_action_id}_{item_id}",
                        "value": item_id
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Request info"},
                        "action_id": f"{request_info_action_id}_{item_id}",
                        "value": item_id
                    }
                ]
            }
        ]
    
    @staticmethod
    def build_exception_blocks(
        exception: Dict[str, Any],
        resolve_action_id: str = "resolve"
    ) -> List[Dict]:
        """Build Block Kit blocks for an exception notification."""
        exc_id = exception.get("id", "unknown")
        priority = exception.get("priority", "MEDIUM").upper()
        
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*[{priority}] Exception Requires Review*"
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Vendor:*\n{exception.get('vendor', 'Unknown')}"},
                    {"type": "mrkdwn", "text": f"*Amount:*\n{exception.get('currency', 'EUR')} {exception.get('amount', 0):,.2f}"},
                    {"type": "mrkdwn", "text": f"*Type:*\n{exception.get('type', 'Unknown')}"},
                    {"type": "mrkdwn", "text": f"*ID:*\n{exc_id}"},
                ]
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Resolve"},
                        "style": "primary",
                        "action_id": f"{resolve_action_id}_{exc_id}",
                        "value": exc_id
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Details"},
                        "action_id": f"view_{exc_id}",
                        "value": exc_id
                    }
                ]
            }
        ]
    
    @staticmethod
    def build_reconciliation_summary_blocks(
        matches: int,
        exceptions: int,
        match_rate: float,
        run_id: Optional[str] = None
    ) -> List[Dict]:
        """Build Block Kit blocks for a reconciliation summary."""
        status_label = "OK" if exceptions == 0 else "Attention" if exceptions < 5 else "Critical"
        
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Reconciliation Complete"}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Matches:*\n{matches}"},
                    {"type": "mrkdwn", "text": f"*Exceptions:*\n{exceptions}"},
                    {"type": "mrkdwn", "text": f"*Match Rate:*\n{match_rate:.1f}%"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{status_label}"},
                ]
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Run ID: `{run_id}`" if run_id else "Manual run"}
                ]
            }
        ]


class SlackAPIError(Exception):
    """Raised when Slack API returns an error."""
    
    def __init__(self, error: str, response: Dict[str, Any]):
        self.error = error
        self.response = response
        super().__init__(f"Slack API error: {error}")


# Signature verification
def verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    signing_secret: Optional[str] = None
) -> bool:
    """Verify that a request came from Slack."""
    secret = signing_secret or SLACK_SIGNING_SECRET
    if not secret:
        return True  # Skip in dev
    
    if abs(time.time() - int(timestamp)) > 300:
        return False  # Request too old
    
    sig_base = f"v0:{timestamp}:{body.decode()}"
    computed = "v0=" + hmac.new(
        secret.encode(),
        sig_base.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(computed, signature)


def resolve_slack_runtime(organization_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Resolve Slack token/channel runtime based on org integration mode.

    Modes:
    - shared: use env token + env/default channel
    - per_org: use org installation token; fallback to shared when explicitly enabled
    """
    shared_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    shared_token_configured = not _is_placeholder_slack_token(shared_token)
    shared_channel = (
        os.getenv("SLACK_APPROVAL_CHANNEL", "").strip()
        or os.getenv("SLACK_DEFAULT_CHANNEL", "").strip()
        or "#finance-approvals"
    )
    shared_secret = os.getenv("SLACK_SIGNING_SECRET", "").strip()
    default_mode = os.getenv("SLACK_INTEGRATION_MODE", "shared").strip().lower() or "shared"
    allow_shared_fallback = str(
        os.getenv("SLACK_ALLOW_SHARED_FALLBACK", "true")
    ).strip().lower() not in {"0", "false", "no", "off"}

    if not organization_id:
        logger.warning("resolve_slack_runtime called without organization_id, falling back to 'default'")
    runtime: Dict[str, Any] = {
        "organization_id": organization_id or "default",
        "mode": default_mode,
        "bot_token": shared_token if shared_token_configured else None,
        "signing_secret": shared_secret or None,
        "approval_channel": shared_channel,
        "connected": shared_token_configured,
        "source": "shared_env" if shared_token_configured else "shared_env_unconfigured",
        "team_id": None,
        "team_name": None,
    }

    if not organization_id:
        return runtime

    try:
        from clearledgr.core.database import get_db

        db = get_db()
        org = db.get_organization(organization_id) or {}
        mode = str(org.get("integration_mode") or default_mode or "shared").lower()
        runtime["mode"] = mode

        settings = org.get("settings_json") or org.get("settings") or {}
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except json.JSONDecodeError:
                settings = {}
        if isinstance(settings, dict):
            channels = settings.get("slack_channels")
            if isinstance(channels, dict):
                runtime["approval_channel"] = channels.get("invoices") or runtime["approval_channel"]

        if mode == "per_org":
            install = db.get_slack_installation(organization_id, include_secrets=True)
            token = (install or {}).get("bot_token")
            if token:
                runtime.update(
                    {
                        "bot_token": token,
                        "connected": True,
                        "source": "org_installation",
                        "team_id": (install or {}).get("team_id"),
                        "team_name": (install or {}).get("team_name"),
                    }
                )
            elif allow_shared_fallback and shared_token_configured:
                runtime.update({"bot_token": shared_token, "connected": True, "source": "shared_fallback"})
            else:
                runtime.update({"bot_token": None, "connected": False, "source": "missing_org_installation"})
    except Exception:
        # Keep runtime non-fatal and preserve shared defaults.
        pass

    return runtime


def get_slack_client(
    bot_token: Optional[str] = None,
    organization_id: Optional[str] = None,
    token_kind: str = "bot",
) -> SlackAPIClient:
    """Get a Slack API client instance, optionally org-scoped."""
    resolved_token = bot_token
    user_lookup_token: Optional[str] = None
    if resolved_token is None:
        if organization_id:
            try:
                from clearledgr.core.database import get_db

                install = get_db().get_slack_installation(organization_id, include_secrets=True) or {}
                user_lookup_token = install.get("user_token")
                if token_kind == "user":
                    resolved_token = user_lookup_token or install.get("bot_token")
            except Exception:
                resolved_token = None
        if resolved_token is None:
            resolved_token = resolve_slack_runtime(organization_id).get("bot_token")
    return SlackAPIClient(resolved_token, user_lookup_token=user_lookup_token)
