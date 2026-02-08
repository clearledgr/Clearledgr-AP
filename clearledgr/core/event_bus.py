"""
Clearledgr Event Bus

The heart of autonomous operation. Events drive everything:
- Bank statement arrives → Parse → Reconcile → Notify
- Gateway webhook fires → Add transaction → Try match → Notify
- Match found → Generate journal entry → Post to ERP
- Exception detected → Notify immediately

NO SCHEDULES. NO BATCHES. REAL-TIME.
"""

import asyncio
import logging
from typing import Callable, Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import uuid

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Events that drive Clearledgr's autonomous operation."""
    
    # Data ingestion events
    BANK_STATEMENT_RECEIVED = "bank_statement.received"
    GATEWAY_TRANSACTION_RECEIVED = "gateway.transaction.received"
    GATEWAY_WEBHOOK_RECEIVED = "gateway.webhook.received"
    INTERNAL_RECORD_ADDED = "internal.record.added"
    
    # Bank feed events (Plaid, Mono, QuickBooks Bank Feeds)
    BANK_TRANSACTION_RECEIVED = "bank.transaction.received"
    BANK_TRANSACTIONS_AVAILABLE = "bank.transactions.available"
    
    # ERP events
    ERP_GL_UPDATED = "erp.gl.updated"
    ERP_INVOICE_RECEIVED = "erp.invoice.received"
    ERP_JE_POSTED = "erp.je.posted"
    
    # Processing events
    RECONCILIATION_STARTED = "reconciliation.started"
    RECONCILIATION_COMPLETED = "reconciliation.completed"
    MATCH_FOUND = "match.found"
    MATCH_HIGH_CONFIDENCE = "match.high_confidence"
    
    # Exception events
    EXCEPTION_DETECTED = "exception.detected"
    EXCEPTION_RESOLVED = "exception.resolved"
    
    # Journal entry events
    JOURNAL_ENTRY_DRAFTED = "journal_entry.drafted"
    JOURNAL_ENTRY_APPROVED = "journal_entry.approved"
    JOURNAL_ENTRY_POSTED = "journal_entry.posted"
    JOURNAL_ENTRY_FAILED = "journal_entry.failed"
    
    # Notification events
    SLACK_NOTIFICATION_SENT = "notification.slack.sent"
    EMAIL_NOTIFICATION_SENT = "notification.email.sent"
    
    # User events
    USER_CORRECTION = "user.correction"
    USER_APPROVAL = "user.approval"
    USER_REJECTION = "user.rejection"
    
    # Gmail events
    GMAIL_EMAIL_RECEIVED = "gmail.email.received"
    GMAIL_EMAIL_PROCESSED = "gmail.email.processed"


@dataclass
class Event:
    """An event in the system."""
    type: EventType
    data: Dict[str, Any]
    organization_id: str
    user_id: Optional[str] = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type.value,
            "data": self.data,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "timestamp": self.timestamp,
        }


class EventBus:
    """
    Pub/Sub event bus for autonomous operation.
    
    Components subscribe to events they care about.
    When events fire, subscribers are notified immediately.
    """
    
    _instance: Optional["EventBus"] = None
    
    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._event_history: List[Event] = []
        self._max_history = 1000
    
    @classmethod
    def get_instance(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def subscribe(self, event_type: EventType, handler: Callable):
        """Subscribe to an event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
        logger.debug(f"Subscribed {handler.__name__} to {event_type.value}")
    
    def unsubscribe(self, event_type: EventType, handler: Callable):
        """Unsubscribe from an event type."""
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                h for h in self._subscribers[event_type] if h != handler
            ]
    
    async def publish(self, event: Event):
        """
        Publish an event. All subscribers are notified immediately.
        
        This is what makes Clearledgr autonomous - events trigger actions.
        """
        logger.info(f"Event: {event.type.value} | org={event.organization_id}")
        
        # Store in history
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history:]
        
        # Notify subscribers
        handlers = self._subscribers.get(event.type, [])
        if not handlers:
            logger.debug(f"No handlers for {event.type.value}")
            return
        
        # Run all handlers concurrently
        tasks = []
        for handler in handlers:
            if asyncio.iscoroutinefunction(handler):
                tasks.append(asyncio.create_task(handler(event)))
            else:
                # Wrap sync handler
                tasks.append(asyncio.create_task(asyncio.to_thread(handler, event)))
        
        # Wait for all handlers
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Log any errors
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Handler {handlers[i].__name__} failed: {result}")
    
    def get_history(
        self,
        organization_id: Optional[str] = None,
        event_type: Optional[EventType] = None,
        limit: int = 100,
    ) -> List[Event]:
        """Get event history."""
        events = self._event_history
        
        if organization_id:
            events = [e for e in events if e.organization_id == organization_id]
        
        if event_type:
            events = [e for e in events if e.type == event_type]
        
        return events[-limit:]


# Global event bus instance
def get_event_bus() -> EventBus:
    return EventBus.get_instance()


# ==================== EVENT HANDLERS ====================
# These are the autonomous reactions to events

async def on_bank_statement_received(event: Event):
    """
    AUTONOMOUS: When bank statement arrives, immediately reconcile.
    
    No waiting. No schedule. Just do it.
    """
    from clearledgr.workflows.reconciliation_workflow import ReconciliationWorkflow
    
    org_id = event.organization_id
    data = event.data
    
    logger.info(f"[AUTONOMOUS] Bank statement received for {org_id}, starting reconciliation...")
    
    workflow = ReconciliationWorkflow(org_id)
    result = await workflow.run(
        bank_statement_content=data.get("content"),
        bank_statement_type=data.get("file_type", "csv"),
        gateway=data.get("gateway", "stripe"),
        gateway_api_key=data.get("gateway_api_key"),
        currency=data.get("currency", "EUR"),
    )
    
    # Publish completion event
    bus = get_event_bus()
    await bus.publish(Event(
        type=EventType.RECONCILIATION_COMPLETED,
        data=result.to_dict(),
        organization_id=org_id,
    ))
    
    # If there are exceptions, publish exception events
    for exc in result.exception_details:
        await bus.publish(Event(
            type=EventType.EXCEPTION_DETECTED,
            data=exc,
            organization_id=org_id,
        ))


async def on_gateway_webhook_received(event: Event):
    """
    AUTONOMOUS: When gateway webhook fires, try to match immediately.
    
    Stripe/Paystack sends webhook → We try to match to bank → Notify if exception
    """
    from clearledgr.core.engine import get_engine
    from clearledgr.services.multi_factor_scoring import MultiFactorScorer
    
    org_id = event.organization_id
    data = event.data
    
    logger.info(f"[AUTONOMOUS] Gateway webhook received for {org_id}")
    
    engine = get_engine()
    scorer = MultiFactorScorer()
    
    # Get the transaction from webhook
    gateway_tx = {
        "amount": data.get("amount"),
        "date": data.get("date"),
        "description": data.get("description"),
        "reference": data.get("reference"),
        "currency": data.get("currency", "EUR"),
    }
    
    # Store the gateway transaction
    engine.add_transaction(
        amount=gateway_tx["amount"],
        currency=gateway_tx["currency"],
        date=gateway_tx["date"],
        description=gateway_tx["description"],
        source="gateway",
        organization_id=org_id,
        reference=gateway_tx["reference"],
    )
    
    # Try to match against existing bank transactions
    bank_txs = engine.get_transactions(org_id, source="bank", status="pending")
    
    best_match = None
    best_score = 0
    
    for bank_tx in bank_txs:
        score = scorer.score_match(gateway_tx, bank_tx)
        if score.total_score > best_score:
            best_score = score.total_score
            best_match = bank_tx
    
    bus = get_event_bus()
    
    if best_match and best_score >= 70:
        # Match found
        logger.info(f"[AUTONOMOUS] Match found! Score: {best_score}")
        await bus.publish(Event(
            type=EventType.MATCH_FOUND,
            data={
                "gateway_transaction": gateway_tx,
                "bank_transaction": best_match,
                "score": best_score,
            },
            organization_id=org_id,
        ))
        
        if best_score >= 90:
            # High confidence - auto-generate journal entry
            await bus.publish(Event(
                type=EventType.MATCH_HIGH_CONFIDENCE,
                data={
                    "gateway_transaction": gateway_tx,
                    "bank_transaction": best_match,
                    "score": best_score,
                },
                organization_id=org_id,
            ))
    else:
        # No match - but don't panic, bank statement might not be uploaded yet
        logger.info(f"[AUTONOMOUS] No match yet for gateway transaction, will try again when bank statement arrives")


async def on_exception_detected(event: Event):
    """
    AUTONOMOUS: When exception detected, notify immediately via Slack.
    
    No waiting until end of day. Finance team sees it NOW.
    """
    from clearledgr.services.slack_notifications import SlackNotifier
    
    org_id = event.organization_id
    exc = event.data
    
    amount = exc.get("amount", 0)
    priority = exc.get("priority", "low")
    
    # Only notify for high-value exceptions
    if priority in ["critical", "high"] or amount >= 1000:
        logger.info(f"[AUTONOMOUS] Notifying Slack about {priority} exception: {amount}")
        
        # Get Slack webhook from org settings (would come from database)
        # For now, use environment variable
        import os
        webhook_url = os.getenv("SLACK_WEBHOOK_URL")
        
        if webhook_url:
            notifier = SlackNotifier(webhook_url)
            await notifier.send_exception_alert(exc, org_id)


async def on_high_confidence_match(event: Event):
    """
    AUTONOMOUS: When high-confidence match found, auto-generate journal entry.
    """
    from clearledgr.core.engine import get_engine
    
    org_id = event.organization_id
    data = event.data
    
    logger.info(f"[AUTONOMOUS] High confidence match, generating journal entry...")
    
    engine = get_engine()
    
    gw_tx = data.get("gateway_transaction", {})
    bank_tx = data.get("bank_transaction", {})
    score = data.get("score", 0)
    
    # Calculate fee
    gw_amount = abs(gw_tx.get("amount", 0))
    bank_amount = abs(bank_tx.get("amount", 0))
    fee = gw_amount - bank_amount if gw_amount > bank_amount else 0
    
    # Draft journal entry
    entry = {
        "date": gw_tx.get("date"),
        "description": f"{gw_tx.get('description', 'Payment')} - Auto-matched ({score}%)",
        "confidence": score / 100,
        "lines": [
            {"account": "1010", "account_name": "Cash", "debit": bank_amount, "credit": 0},
        ],
        "status": "draft",
    }
    
    if fee > 0:
        entry["lines"].append({
            "account": "5250", 
            "account_name": "Payment Processing Fees", 
            "debit": fee, 
            "credit": 0
        })
    
    entry["lines"].append({
        "account": "1200", 
        "account_name": "Accounts Receivable", 
        "debit": 0, 
        "credit": gw_amount
    })
    
    bus = get_event_bus()
    await bus.publish(Event(
        type=EventType.JOURNAL_ENTRY_DRAFTED,
        data=entry,
        organization_id=org_id,
    ))


async def on_journal_entry_approved(event: Event):
    """
    AUTONOMOUS: When journal entry approved, post to ERP.
    """
    from clearledgr.integrations.erp_router import post_journal_entry
    
    org_id = event.organization_id
    entry = event.data
    
    logger.info(f"[AUTONOMOUS] Posting approved journal entry to ERP...")
    
    try:
        result = await post_journal_entry(org_id, entry)
        
        bus = get_event_bus()
        await bus.publish(Event(
            type=EventType.JOURNAL_ENTRY_POSTED,
            data={"entry": entry, "erp_response": result},
            organization_id=org_id,
        ))
    except Exception as e:
        logger.error(f"Failed to post to ERP: {e}")
        bus = get_event_bus()
        await bus.publish(Event(
            type=EventType.JOURNAL_ENTRY_FAILED,
            data={"entry": entry, "error": str(e)},
            organization_id=org_id,
        ))


async def on_reconciliation_completed(event: Event):
    """
    AUTONOMOUS: When reconciliation completes, notify Slack with summary.
    
    Only if there are exceptions. Don't bother team with "all good" messages.
    """
    from clearledgr.services.slack_notifications import SlackNotifier
    import os
    
    org_id = event.organization_id
    result = event.data
    
    exceptions = result.get("exceptions", 0)
    
    if exceptions > 0:
        logger.info(f"[AUTONOMOUS] Notifying Slack: {exceptions} exceptions found")
        
        webhook_url = os.getenv("SLACK_WEBHOOK_URL")
        if webhook_url:
            notifier = SlackNotifier(webhook_url)
            await notifier.send_reconciliation_complete(result)
    else:
        logger.info(f"[AUTONOMOUS] All transactions matched, no notification needed")


async def on_bank_transactions_available(event: Event):
    """
    AUTONOMOUS: When bank feed has new transactions (Plaid/Mono), fetch and process.
    """
    org_id = event.organization_id
    data = event.data
    source = data.get("source", "plaid")
    
    logger.info(f"[AUTONOMOUS] New bank transactions available from {source}")
    
    # Would fetch transactions from Plaid/Mono API and add to engine
    # For now, log the event
    
    bus = get_event_bus()
    await bus.publish(Event(
        type=EventType.RECONCILIATION_STARTED,
        data={"trigger": "bank_feed", "source": source},
        organization_id=org_id,
    ))


async def on_erp_gl_updated(event: Event):
    """
    AUTONOMOUS: When ERP GL accounts change, sync mappings.
    """
    org_id = event.organization_id
    data = event.data
    erp = data.get("erp")
    
    logger.info(f"[AUTONOMOUS] GL accounts updated in {erp}, syncing mappings...")
    
    # Would fetch new GL accounts from ERP and update local mappings
    # This ensures categorization uses latest account structure


async def on_erp_invoice_received(event: Event):
    """
    AUTONOMOUS: When invoice received from ERP, try to match to payments.
    """
    org_id = event.organization_id
    data = event.data
    
    logger.info(f"[AUTONOMOUS] Invoice received from ERP, checking for matching payments...")
    
    # Would fetch invoice details and try to match against payment records


# ==================== INITIALIZE EVENT SUBSCRIPTIONS ====================

def setup_event_handlers():
    """Wire up all the autonomous event handlers."""
    bus = get_event_bus()
    
    # Data events → Processing
    bus.subscribe(EventType.BANK_STATEMENT_RECEIVED, on_bank_statement_received)
    bus.subscribe(EventType.GATEWAY_WEBHOOK_RECEIVED, on_gateway_webhook_received)
    
    # Bank feed events → Processing
    bus.subscribe(EventType.BANK_TRANSACTIONS_AVAILABLE, on_bank_transactions_available)
    
    # ERP events → Sync
    bus.subscribe(EventType.ERP_GL_UPDATED, on_erp_gl_updated)
    bus.subscribe(EventType.ERP_INVOICE_RECEIVED, on_erp_invoice_received)
    
    # Processing events → Actions
    bus.subscribe(EventType.EXCEPTION_DETECTED, on_exception_detected)
    bus.subscribe(EventType.MATCH_HIGH_CONFIDENCE, on_high_confidence_match)
    bus.subscribe(EventType.RECONCILIATION_COMPLETED, on_reconciliation_completed)
    
    # Approval events → ERP posting
    bus.subscribe(EventType.JOURNAL_ENTRY_APPROVED, on_journal_entry_approved)
    
    logger.info("Event handlers initialized - Clearledgr is now autonomous")
