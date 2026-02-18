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
import time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import httpx

logger = logging.getLogger(__name__)

# Configuration
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_API_BASE = "https://slack.com/api"


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
    
    def __init__(self, bot_token: Optional[str] = None):
        self.bot_token = bot_token or SLACK_BOT_TOKEN
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make authenticated API request."""
        if not self.bot_token:
            raise ValueError("Slack bot token not configured")
        
        url = f"{SLACK_API_BASE}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        
        async with httpx.AsyncClient() as client:
            if method == "GET":
                response = await client.get(url, headers=headers, params=params, timeout=30)
            else:
                response = await client.post(url, headers=headers, json=data, timeout=30)
            
            result = response.json()
            
            if not result.get("ok"):
                error = result.get("error", "Unknown error")
                raise SlackAPIError(error, result)
            
            return result
    
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
        
        return SlackMessage(
            channel=result.get("channel", channel),
            ts=result.get("ts", ""),
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
    
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """Get information about a user."""
        result = await self._request("GET", "users.info", params={"user": user_id})
        return result.get("user", {})
    
    async def lookup_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Find a user by email address."""
        try:
            result = await self._request("GET", "users.lookupByEmail", params={"email": email})
            return result.get("user")
        except SlackAPIError as e:
            if e.error == "users_not_found":
                return None
            raise
    
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
    def build_approval_blocks(
        title: str,
        details: Dict[str, str],
        approve_action_id: str,
        reject_action_id: str,
        item_id: str
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
    def build_ap_kpi_digest_text(kpis: Dict[str, Any], organization_id: str) -> str:
        touchless = float((kpis.get("touchless_rate") or {}).get("rate") or 0.0) * 100.0
        exceptions = float((kpis.get("exception_rate") or {}).get("rate") or 0.0) * 100.0
        on_time = float((kpis.get("on_time_approvals") or {}).get("rate") or 0.0) * 100.0
        cycle_avg = float((kpis.get("cycle_time_hours") or {}).get("avg") or 0.0)
        return (
            f"AP KPI digest for `{organization_id}`: touchless {touchless:.1f}%, "
            f"exceptions {exceptions:.1f}%, on-time approvals {on_time:.1f}%, "
            f"avg cycle {cycle_avg:.1f}h."
        )

    @staticmethod
    def build_ap_kpi_digest_blocks(kpis: Dict[str, Any], organization_id: str) -> List[Dict]:
        touchless = float((kpis.get("touchless_rate") or {}).get("rate") or 0.0) * 100.0
        exceptions = float((kpis.get("exception_rate") or {}).get("rate") or 0.0) * 100.0
        on_time = float((kpis.get("on_time_approvals") or {}).get("rate") or 0.0) * 100.0
        cycle = kpis.get("cycle_time_hours") or {}
        friction = kpis.get("approval_friction") or {}
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"AP KPI Digest · {organization_id}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Touchless rate*\n{touchless:.1f}%"},
                    {"type": "mrkdwn", "text": f"*Exception rate*\n{exceptions:.1f}%"},
                    {"type": "mrkdwn", "text": f"*On-time approvals*\n{on_time:.1f}%"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Cycle time (avg/p95)*\n{float(cycle.get('avg') or 0.0):.1f}h / {float(cycle.get('p95') or 0.0):.1f}h",
                    },
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Approval friction*\n"
                        f"SLA breaches: {int(friction.get('sla_breach_count') or 0)} · "
                        f"Avg handoffs: {float(friction.get('avg_handoffs') or 0.0):.2f} · "
                        f"P95 wait: {float(friction.get('p95_wait_minutes') or 0.0):.1f}m"
                    ),
                },
            },
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
        logger.warning("Slack signature verification failed: signing secret is not configured")
        return False
    
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False

    if abs(time.time() - ts) > 300:
        return False  # Request too old
    
    sig_base = f"v0:{timestamp}:{body.decode()}"
    computed = "v0=" + hmac.new(
        secret.encode(),
        sig_base.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(computed, signature)


# Helper function
def get_slack_client(bot_token: Optional[str] = None) -> SlackAPIClient:
    """Get a Slack API client instance."""
    return SlackAPIClient(bot_token)
