"""
Real-time Sync Service for Clearledgr

Pushes updates to all connected surfaces when data changes:
- Slack notifications
- Sheets refresh triggers
- WebSocket/SSE for live updates

This enables the "While you were away" experience across all surfaces.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
import os
import httpx

logger = logging.getLogger(__name__)

# Slack configuration
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_DEFAULT_CHANNEL = os.getenv("SLACK_DEFAULT_CHANNEL", "#finance")


@dataclass
class SyncEvent:
    """Represents a sync event to push to surfaces."""
    event_type: str
    organization_id: str
    user_id: Optional[str]
    data: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }


class RealtimeSyncService:
    """
    Manages real-time synchronization across all Clearledgr surfaces.
    
    When the backend processes something (email, webhook, etc.),
    this service notifies all connected surfaces.
    """
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._slack_client = None
        self._sheets_webhooks: Dict[str, str] = {}  # org_id -> webhook_url
        
    # ==================== EVENT PUBLISHING ====================
    
    async def publish(self, event: SyncEvent):
        """
        Publish an event to all surfaces.
        
        This is called by the engine when something changes.
        """
        logger.info(f"Publishing sync event: {event.event_type} for org {event.organization_id}")
        
        # Notify all registered subscribers (WebSocket connections, etc.)
        await self._notify_subscribers(event)
        
        # Push to Slack if configured
        await self._notify_slack(event)
        
        # Trigger Sheets refresh if configured
        await self._notify_sheets(event)
    
    # ==================== SLACK NOTIFICATIONS ====================
    
    async def _notify_slack(self, event: SyncEvent):
        """Send notification to Slack."""
        if not SLACK_BOT_TOKEN:
            return
        
        # Build Slack message based on event type
        message = self._build_slack_message(event)
        if not message:
            return
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "channel": SLACK_DEFAULT_CHANNEL,
                        **message,
                    },
                )
                
                if not response.json().get("ok"):
                    logger.warning(f"Slack notification failed: {response.json()}")
        except Exception as e:
            logger.error(f"Slack notification error: {e}")
    
    def _build_slack_message(self, event: SyncEvent) -> Optional[Dict[str, Any]]:
        """Build Slack message based on event type."""
        data = event.data
        
        if event.event_type == "email.processed":
            return {
                "text": f"Bank statement processed: {data.get('matched', 0)} matched, {data.get('exceptions', 0)} exceptions",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Bank Statement Processed*\n{data.get('filename', 'Bank statement')} from {data.get('sender', 'unknown')}"
                        }
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Transactions:* {data.get('total', 0)}"},
                            {"type": "mrkdwn", "text": f"*Matched:* {data.get('matched', 0)}"},
                            {"type": "mrkdwn", "text": f"*Exceptions:* {data.get('exceptions', 0)}"},
                            {"type": "mrkdwn", "text": f"*Amount:* {data.get('currency', 'EUR')} {data.get('amount', 0):,.2f}"},
                        ]
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Review Exceptions"},
                                "style": "primary" if data.get('exceptions', 0) > 0 else None,
                                "action_id": "review_exceptions",
                                "value": json.dumps({"org_id": event.organization_id}),
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Approve All"},
                                "action_id": "approve_all",
                                "value": json.dumps({"org_id": event.organization_id}),
                            },
                        ]
                    }
                ]
            }
        
        elif event.event_type == "gateway.settled":
            return {
                "text": f"Payment settlement received: {data.get('currency', 'EUR')} {data.get('amount', 0):,.2f}",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*{data.get('gateway', 'Payment').title()} Settlement*\n"
                                    f"Amount: {data.get('currency', 'EUR')} {data.get('amount', 0):,.2f}\n"
                                    f"Reference: `{data.get('reference', 'N/A')}`"
                        }
                    },
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": f"Auto-matching with bank transactions..."}
                        ]
                    }
                ]
            }
        
        elif event.event_type == "exception.created":
            priority_label = {
                "critical": "[CRITICAL]",
                "high": "[HIGH]",
                "medium": "[MEDIUM]",
                "low": "[LOW]",
            }.get(data.get("priority", "medium"), "")
            
            return {
                "text": f"{priority_label} New exception: {data.get('description', 'Unknown')}",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*{priority_label} Exception Requires Review*\n"
                                    f"{data.get('description', 'No description')}\n"
                                    f"Amount: {data.get('currency', 'EUR')} {data.get('amount', 0):,.2f}"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Resolve"},
                                "style": "primary",
                                "action_id": "resolve_exception",
                                "value": data.get("exception_id", ""),
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Assign"},
                                "action_id": "assign_exception",
                                "value": data.get("exception_id", ""),
                            },
                        ]
                    }
                ]
            }
        
        elif event.event_type == "draft.ready":
            return {
                "text": f"Journal entry ready for approval: {data.get('count', 0)} entries",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Journal Entries Ready*\n"
                                    f"{data.get('count', 0)} draft entries ready for approval\n"
                                    f"Total: {data.get('currency', 'EUR')} {data.get('total_amount', 0):,.2f}"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Review in Sheets"},
                                "url": data.get("sheets_url", ""),
                                "action_id": "open_sheets",
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Approve & Post"},
                                "style": "primary",
                                "action_id": "approve_and_post",
                                "value": json.dumps({"org_id": event.organization_id}),
                            },
                        ]
                    }
                ]
            }
        
        return None
    
    # ==================== SHEETS NOTIFICATIONS ====================
    
    def register_sheets_webhook(self, organization_id: str, webhook_url: str):
        """Register a Sheets webhook URL for an organization."""
        self._sheets_webhooks[organization_id] = webhook_url
        logger.info(f"Registered Sheets webhook for org {organization_id}")
    
    async def _notify_sheets(self, event: SyncEvent):
        """Trigger Sheets refresh via webhook."""
        webhook_url = self._sheets_webhooks.get(event.organization_id)
        if not webhook_url:
            return
        
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    webhook_url,
                    json={
                        "event": event.event_type,
                        "data": event.data,
                        "timestamp": event.timestamp.isoformat(),
                    },
                    timeout=5.0,
                )
            logger.info(f"Sheets webhook triggered for org {event.organization_id}")
        except Exception as e:
            logger.warning(f"Sheets webhook failed: {e}")
    
    # ==================== WEBSOCKET SUBSCRIBERS ====================
    
    def subscribe(self, organization_id: str, callback: Callable):
        """Subscribe to events for an organization (for WebSocket connections)."""
        if organization_id not in self._subscribers:
            self._subscribers[organization_id] = []
        self._subscribers[organization_id].append(callback)
    
    def unsubscribe(self, organization_id: str, callback: Callable):
        """Unsubscribe from events."""
        if organization_id in self._subscribers:
            self._subscribers[organization_id] = [
                cb for cb in self._subscribers[organization_id] if cb != callback
            ]
    
    async def _notify_subscribers(self, event: SyncEvent):
        """Notify all WebSocket subscribers."""
        subscribers = self._subscribers.get(event.organization_id, [])
        
        for callback in subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event.to_dict())
                else:
                    callback(event.to_dict())
            except Exception as e:
                logger.error(f"Subscriber notification failed: {e}")


# Global instance
_sync_service: Optional[RealtimeSyncService] = None


def get_sync_service() -> RealtimeSyncService:
    """Get the global sync service instance."""
    global _sync_service
    if _sync_service is None:
        _sync_service = RealtimeSyncService()
    return _sync_service


# ==================== CONVENIENCE FUNCTIONS ====================

async def notify_email_processed(
    organization_id: str,
    user_id: str,
    filename: str,
    sender: str,
    total: int,
    matched: int,
    exceptions: int,
    amount: float,
    currency: str = "EUR",
):
    """Convenience function to notify when an email is processed."""
    service = get_sync_service()
    await service.publish(SyncEvent(
        event_type="email.processed",
        organization_id=organization_id,
        user_id=user_id,
        data={
            "filename": filename,
            "sender": sender,
            "total": total,
            "matched": matched,
            "exceptions": exceptions,
            "amount": amount,
            "currency": currency,
        }
    ))


async def notify_gateway_settled(
    organization_id: str,
    gateway: str,
    amount: float,
    reference: str,
    currency: str = "EUR",
):
    """Convenience function to notify when a gateway settlement arrives."""
    service = get_sync_service()
    await service.publish(SyncEvent(
        event_type="gateway.settled",
        organization_id=organization_id,
        user_id=None,
        data={
            "gateway": gateway,
            "amount": amount,
            "reference": reference,
            "currency": currency,
        }
    ))


async def notify_exception_created(
    organization_id: str,
    exception_id: str,
    description: str,
    amount: float,
    priority: str = "medium",
    currency: str = "EUR",
):
    """Convenience function to notify when an exception is created."""
    service = get_sync_service()
    await service.publish(SyncEvent(
        event_type="exception.created",
        organization_id=organization_id,
        user_id=None,
        data={
            "exception_id": exception_id,
            "description": description,
            "amount": amount,
            "priority": priority,
            "currency": currency,
        }
    ))


async def notify_drafts_ready(
    organization_id: str,
    count: int,
    total_amount: float,
    sheets_url: str = "",
    currency: str = "EUR",
):
    """Convenience function to notify when draft JEs are ready."""
    service = get_sync_service()
    await service.publish(SyncEvent(
        event_type="draft.ready",
        organization_id=organization_id,
        user_id=None,
        data={
            "count": count,
            "total_amount": total_amount,
            "sheets_url": sheets_url,
            "currency": currency,
        }
    ))
