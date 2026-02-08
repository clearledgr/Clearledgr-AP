"""
Gmail Watcher Agent

Monitors Gmail for finance-related emails and publishes events when detected.
This enables the autonomous flow: email arrives → agent detects → agent processes.

Integration approaches:
1. Gmail API with push notifications (production)
2. Webhook from Gmail extension (current)
3. Polling (fallback)
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clearledgr.agents.runtime import (
    AutonomousAgent,
    AgentDecision,
    Event,
    EventBus,
    EventType,
)

logger = logging.getLogger(__name__)


# Finance email detection patterns
FINANCE_PATTERNS = {
    "invoice": {
        "subject_patterns": [
            r"invoice\s*#?\s*\d+",
            r"inv[\-_]?\d+",
            r"invoice\s+available",
            r"your\s+invoice",
            r"bill\s*from",
            r"payment\s*due",
            r"amount\s*due",
        ],
        "sender_domains": [],  # Any domain can send invoices
        "attachment_types": [".pdf", ".png", ".jpg", ".jpeg"],
    },
    "payment_request": {
        "subject_patterns": [
            r"payment\s+request",
            r"please\s+pay",
            r"request\s+for\s+payment",
            r"transfer\s+to",
            r"wire\s+to",
        ],
        "sender_domains": [],
        "attachment_types": [".pdf", ".png", ".jpg", ".jpeg"],
    },
}


class GmailWatcherAgent(AutonomousAgent):
    """
    Agent that watches Gmail for finance emails.
    
    This agent doesn't subscribe to other events - it generates events
    based on incoming emails detected via webhook or polling.
    """
    
    def __init__(self, event_bus: EventBus):
        super().__init__("GmailWatcher", event_bus)
        self._processed_emails: set = set()  # Track processed email IDs
        self._max_processed_cache = 10000
    
    def get_subscribed_events(self) -> List[EventType]:
        """Gmail watcher generates events, doesn't subscribe to many."""
        return [
            EventType.APPROVAL_GRANTED,  # For any Gmail-related approvals
        ]
    
    async def handle_event(self, event: Event) -> Optional[AgentDecision]:
        """Handle events (minimal for this agent)."""
        return None
    
    async def execute_decision(self, decision: AgentDecision, event: Event) -> None:
        """Execute decisions (minimal for this agent)."""
        pass
    
    async def process_incoming_email(self, email_data: Dict[str, Any]) -> None:
        """
        Process an incoming email (called from webhook or polling).
        
        This is the main entry point for email processing.
        """
        email_id = email_data.get("id") or email_data.get("message_id")
        
        # Skip if already processed
        if email_id and email_id in self._processed_emails:
            return
        
        # Detect email type
        detection = self._detect_finance_email(email_data)
        
        if not detection["is_finance"]:
            return  # Not a finance email, ignore
        
        # Mark as processed
        if email_id:
            self._processed_emails.add(email_id)
            if len(self._processed_emails) > self._max_processed_cache:
                # Clear oldest entries (simple approach)
                self._processed_emails = set(list(self._processed_emails)[-self._max_processed_cache // 2:])
        
        # Publish finance email detected event
        await self.event_bus.publish(Event(
            event_type=EventType.GMAIL_FINANCE_EMAIL_DETECTED,
            payload={
                "email_id": email_id,
                "email_type": detection["type"],
                "confidence": detection["confidence"],
                "sender": email_data.get("sender") or email_data.get("from"),
                "subject": email_data.get("subject"),
                "snippet": email_data.get("snippet") or email_data.get("body_preview"),
                "attachments": email_data.get("attachments", []),
                "received_at": email_data.get("date") or datetime.now(timezone.utc).isoformat(),
                "detection_reasons": detection["reasons"],
                "priority": detection["priority"],
                "suggested_action": detection["suggested_action"],
            },
            source=self.name,
            confidence=detection["confidence"],
        ))
        
        logger.info(
            f"[{self.name}] Finance email detected: {detection['type']} "
            f"(confidence: {detection['confidence']:.0%}) from {email_data.get('sender')}"
        )
    
    def _detect_finance_email(self, email_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect if an email is finance-related and classify it.
        
        Returns detection result with type, confidence, and reasons.
        """
        subject = (email_data.get("subject") or "").lower()
        sender = (email_data.get("sender") or email_data.get("from") or "").lower()
        body = (email_data.get("body") or email_data.get("snippet") or "").lower()
        attachments = email_data.get("attachments") or []
        
        # Extract sender domain
        sender_domain = ""
        if "@" in sender:
            sender_domain = sender.split("@")[-1].split(">")[0].strip()
        
        # Check each finance type
        best_match = {
            "is_finance": False,
            "type": None,
            "confidence": 0.0,
            "reasons": [],
            "priority": "medium",
            "suggested_action": "review",
        }
        
        for finance_type, patterns in FINANCE_PATTERNS.items():
            score = 0.0
            reasons = []
            
            # Check subject patterns
            for pattern in patterns.get("subject_patterns", []):
                if re.search(pattern, subject, re.IGNORECASE):
                    score += 0.4
                    reasons.append(f"Subject matches '{pattern}'")
                    break
            
            # Check sender domain
            if sender_domain in patterns.get("sender_domains", []):
                score += 0.3
                reasons.append(f"Sender domain '{sender_domain}' is known finance provider")
            
            # Check attachments
            attachment_names = " ".join([
                (a.get("name") or a.get("filename") or "").lower() 
                for a in attachments
            ])
            for ext in patterns.get("attachment_types", []):
                if ext in attachment_names:
                    score += 0.2
                    reasons.append(f"Has {ext} attachment")
                    break
            
            # Check body for keywords
            if finance_type == "bank_statement":
                if any(kw in body for kw in ["balance", "transaction", "debit", "credit"]):
                    score += 0.1
                    reasons.append("Body contains banking keywords")
            elif finance_type == "invoice":
                if any(kw in body for kw in ["amount due", "total", "payment", "due date"]):
                    score += 0.1
                    reasons.append("Body contains invoice keywords")
            
            # Update best match
            if score > best_match["confidence"]:
                best_match = {
                    "is_finance": score >= 0.3,
                    "type": finance_type,
                    "confidence": min(score, 0.99),
                    "reasons": reasons,
                    "priority": self._get_priority(finance_type, email_data),
                    "suggested_action": self._get_suggested_action(finance_type),
                }
        
        return best_match
    
    def _get_priority(self, finance_type: str, email_data: Dict[str, Any]) -> str:
        """Determine priority based on type and content."""
        if finance_type == "invoice":
            # Check for urgency indicators
            subject = (email_data.get("subject") or "").lower()
            if any(w in subject for w in ["urgent", "overdue", "final notice"]):
                return "high"
            return "medium"
        elif finance_type == "payment_request":
            return "medium"
        return "medium"
    
    def _get_suggested_action(self, finance_type: str) -> str:
        """Get suggested action for finance type."""
        actions = {
            "invoice": "process",
            "payment_request": "review",
        }
        return actions.get(finance_type, "review")
    
    async def scan_inbox(self, emails: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Scan a batch of emails for finance content.
        
        Called from Gmail extension or API to process multiple emails.
        """
        results = {
            "total_scanned": len(emails),
            "finance_emails": 0,
            "by_type": {},
            "emails": [],
        }
        
        for email in emails:
            detection = self._detect_finance_email(email)
            
            if detection["is_finance"]:
                results["finance_emails"] += 1
                email_type = detection["type"]
                results["by_type"][email_type] = results["by_type"].get(email_type, 0) + 1
                
                results["emails"].append({
                    "id": email.get("id"),
                    "subject": email.get("subject"),
                    "sender": email.get("sender"),
                    "type": email_type,
                    "confidence": detection["confidence"],
                    "priority": detection["priority"],
                    "suggested_action": detection["suggested_action"],
                })
                
                # Publish event for each detected email
                await self.process_incoming_email(email)
        
        return results


class GmailWebhookHandler:
    """
    Handler for Gmail webhook events.
    
    This class processes incoming webhook calls and routes them
    to the Gmail watcher agent.
    """
    
    def __init__(self, watcher: GmailWatcherAgent):
        self.watcher = watcher
    
    async def handle_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle incoming Gmail webhook.
        
        Payload format (from Gmail extension or Gmail API push):
        {
            "type": "email_received" | "inbox_scan",
            "email": { ... } | "emails": [ ... ]
        }
        """
        event_type = payload.get("type")
        
        if event_type == "email_received":
            email_data = payload.get("email", {})
            await self.watcher.process_incoming_email(email_data)
            return {"status": "processed", "email_id": email_data.get("id")}
        
        elif event_type == "inbox_scan":
            emails = payload.get("emails", [])
            results = await self.watcher.scan_inbox(emails)
            return {"status": "scanned", **results}
        
        return {"status": "unknown_type", "type": event_type}
