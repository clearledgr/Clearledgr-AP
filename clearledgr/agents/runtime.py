"""
Clearledgr Autonomous Agent Runtime

This is the core runtime that enables true autonomous operation:
- Event-driven architecture with pub/sub event bus
- Background agents that monitor and act without user triggers
- Confidence-based auto-execution vs escalation
- Continuous learning from outcomes

The runtime transforms Clearledgr from "user triggers action" to 
"agent acts autonomously, escalates only when uncertain."
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from collections import defaultdict
import uuid

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Event types in the Clearledgr ecosystem."""
    # Gmail events
    GMAIL_EMAIL_RECEIVED = "gmail.email.received"
    GMAIL_FINANCE_EMAIL_DETECTED = "gmail.finance_email.detected"
    GMAIL_ATTACHMENT_PARSED = "gmail.attachment.parsed"
    
    # Sheets events
    SHEETS_DATA_UPDATED = "sheets.data.updated"
    SHEETS_RECONCILIATION_REQUESTED = "sheets.reconciliation.requested"
    SHEETS_EXCEPTION_CREATED = "sheets.exception.created"
    SHEETS_DRAFT_CREATED = "sheets.draft.created"
    
    # Reconciliation events
    RECON_STARTED = "recon.started"
    RECON_MATCH_FOUND = "recon.match.found"
    RECON_EXCEPTION_FOUND = "recon.exception.found"
    RECON_COMPLETED = "recon.completed"
    RECON_AUTO_MATCHED = "recon.auto_matched"
    
    # Approval events
    APPROVAL_NEEDED = "approval.needed"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_AUTO_APPROVED = "approval.auto_approved"
    
    # SAP events
    SAP_POSTING_REQUESTED = "sap.posting.requested"
    SAP_POSTING_COMPLETED = "sap.posting.completed"
    SAP_POSTING_FAILED = "sap.posting.failed"
    
    # Anomaly events
    ANOMALY_DETECTED = "anomaly.detected"
    ANOMALY_RESOLVED = "anomaly.resolved"
    
    # Learning events
    LEARNING_CORRECTION_RECEIVED = "learning.correction.received"
    LEARNING_PATTERN_LEARNED = "learning.pattern.learned"
    
    # Agent lifecycle
    AGENT_STARTED = "agent.started"
    AGENT_STOPPED = "agent.stopped"
    AGENT_ERROR = "agent.error"


class ConfidenceLevel(Enum):
    """Confidence levels for autonomous decisions."""
    AUTO_EXECUTE = "auto_execute"      # >= 95% - Execute without human
    HIGH = "high"                       # 85-94% - Execute, notify after
    MEDIUM = "medium"                   # 70-84% - Ask for confirmation
    LOW = "low"                         # 50-69% - Require human review
    UNCERTAIN = "uncertain"             # < 50% - Escalate immediately


@dataclass
class Event:
    """An event in the Clearledgr event bus."""
    event_type: EventType
    payload: Dict[str, Any]
    source: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: Optional[str] = None  # Links related events
    confidence: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "payload": self.payload,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": self.correlation_id,
            "confidence": self.confidence,
        }


@dataclass
class AgentDecision:
    """A decision made by an autonomous agent."""
    action: str
    confidence: float
    reasoning: str
    should_auto_execute: bool
    requires_approval: bool
    escalate_to: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def confidence_level(self) -> ConfidenceLevel:
        if self.confidence >= 0.95:
            return ConfidenceLevel.AUTO_EXECUTE
        elif self.confidence >= 0.85:
            return ConfidenceLevel.HIGH
        elif self.confidence >= 0.70:
            return ConfidenceLevel.MEDIUM
        elif self.confidence >= 0.50:
            return ConfidenceLevel.LOW
        else:
            return ConfidenceLevel.UNCERTAIN


class EventBus:
    """
    Central event bus for agent communication.
    
    Enables loose coupling between agents - agents publish events
    and subscribe to events they care about.
    """
    
    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = defaultdict(list)
        self._event_history: List[Event] = []
        self._max_history = 10000
        self._running = False
        self._queue: asyncio.Queue = None
    
    def subscribe(self, event_type: EventType, handler: Callable[[Event], Any]) -> None:
        """Subscribe to an event type."""
        self._subscribers[event_type].append(handler)
        logger.debug(f"Subscribed handler to {event_type.value}")
    
    def unsubscribe(self, event_type: EventType, handler: Callable) -> None:
        """Unsubscribe from an event type."""
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)
    
    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers."""
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history:]
        
        logger.info(f"Event published: {event.event_type.value} from {event.source}")
        
        handlers = self._subscribers.get(event.event_type, [])
        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Handler error for {event.event_type.value}: {e}")
    
    def get_recent_events(
        self, 
        event_types: Optional[List[EventType]] = None,
        limit: int = 100
    ) -> List[Event]:
        """Get recent events, optionally filtered by type."""
        events = self._event_history
        if event_types:
            events = [e for e in events if e.event_type in event_types]
        return events[-limit:]
    
    def get_correlated_events(self, correlation_id: str) -> List[Event]:
        """Get all events with the same correlation ID."""
        return [e for e in self._event_history if e.correlation_id == correlation_id]


class AutonomousAgent(ABC):
    """
    Base class for autonomous agents.
    
    Agents:
    - Subscribe to relevant events
    - Make decisions based on events
    - Execute actions or escalate based on confidence
    - Learn from outcomes
    """
    
    def __init__(self, name: str, event_bus: EventBus):
        self.name = name
        self.event_bus = event_bus
        self.is_running = False
        self._subscribed_events: Set[EventType] = set()
        
        # Confidence thresholds (configurable per agent)
        self.auto_execute_threshold = 0.95
        self.notify_after_threshold = 0.85
        self.ask_confirmation_threshold = 0.70
    
    @abstractmethod
    def get_subscribed_events(self) -> List[EventType]:
        """Return list of event types this agent subscribes to."""
        pass
    
    @abstractmethod
    async def handle_event(self, event: Event) -> Optional[AgentDecision]:
        """
        Handle an incoming event.
        
        Returns an AgentDecision if action should be taken, None otherwise.
        """
        pass
    
    @abstractmethod
    async def execute_decision(self, decision: AgentDecision, event: Event) -> None:
        """Execute an approved decision."""
        pass
    
    async def start(self) -> None:
        """Start the agent."""
        self.is_running = True
        
        # Subscribe to events
        for event_type in self.get_subscribed_events():
            self.event_bus.subscribe(event_type, self._on_event)
            self._subscribed_events.add(event_type)
        
        await self.event_bus.publish(Event(
            event_type=EventType.AGENT_STARTED,
            payload={"agent": self.name},
            source=self.name,
        ))
        
        logger.info(f"Agent {self.name} started")
    
    async def stop(self) -> None:
        """Stop the agent."""
        self.is_running = False
        
        # Unsubscribe from events
        for event_type in self._subscribed_events:
            self.event_bus.unsubscribe(event_type, self._on_event)
        
        await self.event_bus.publish(Event(
            event_type=EventType.AGENT_STOPPED,
            payload={"agent": self.name},
            source=self.name,
        ))
        
        logger.info(f"Agent {self.name} stopped")
    
    async def _on_event(self, event: Event) -> None:
        """Internal event handler that manages decision flow."""
        if not self.is_running:
            return
        
        try:
            decision = await self.handle_event(event)
            
            if decision is None:
                return
            
            # Determine action based on confidence
            if decision.should_auto_execute:
                if decision.confidence >= self.auto_execute_threshold:
                    # Auto-execute without asking
                    await self.execute_decision(decision, event)
                    await self._notify_action_taken(decision, event)
                    
                elif decision.confidence >= self.notify_after_threshold:
                    # Execute and notify after
                    await self.execute_decision(decision, event)
                    await self._notify_action_taken(decision, event)
                    
                elif decision.confidence >= self.ask_confirmation_threshold:
                    # Ask for confirmation before executing
                    await self._request_confirmation(decision, event)
                    
                else:
                    # Escalate to human
                    await self._escalate(decision, event)
            else:
                # Decision explicitly requires approval
                await self._request_approval(decision, event)
                
        except Exception as e:
            logger.error(f"Agent {self.name} error handling event: {e}")
            await self.event_bus.publish(Event(
                event_type=EventType.AGENT_ERROR,
                payload={"agent": self.name, "error": str(e), "event_id": event.event_id},
                source=self.name,
                correlation_id=event.correlation_id,
            ))
    
    async def _notify_action_taken(self, decision: AgentDecision, event: Event) -> None:
        """Notify that an action was taken autonomously."""
        # This will trigger Slack/email notifications
        logger.info(f"Agent {self.name} auto-executed: {decision.action} (confidence: {decision.confidence:.0%})")
    
    async def _request_confirmation(self, decision: AgentDecision, event: Event) -> None:
        """Request confirmation before executing."""
        await self.event_bus.publish(Event(
            event_type=EventType.APPROVAL_NEEDED,
            payload={
                "agent": self.name,
                "decision": decision.action,
                "confidence": decision.confidence,
                "reasoning": decision.reasoning,
                "payload": decision.payload,
                "original_event": event.to_dict(),
            },
            source=self.name,
            correlation_id=event.correlation_id or event.event_id,
            confidence=decision.confidence,
        ))
    
    async def _request_approval(self, decision: AgentDecision, event: Event) -> None:
        """Request approval for a decision."""
        await self._request_confirmation(decision, event)
    
    async def _escalate(self, decision: AgentDecision, event: Event) -> None:
        """Escalate a low-confidence decision to human."""
        logger.info(f"Agent {self.name} escalating: {decision.action} (confidence: {decision.confidence:.0%})")
        
        await self.event_bus.publish(Event(
            event_type=EventType.APPROVAL_NEEDED,
            payload={
                "agent": self.name,
                "decision": decision.action,
                "confidence": decision.confidence,
                "reasoning": decision.reasoning,
                "escalate_to": decision.escalate_to,
                "requires_human": True,
                "payload": decision.payload,
                "original_event": event.to_dict(),
            },
            source=self.name,
            correlation_id=event.correlation_id or event.event_id,
            confidence=decision.confidence,
        ))


class AgentRuntime:
    """
    The main runtime that manages all autonomous agents.
    
    Provides:
    - Agent lifecycle management
    - Shared event bus
    - Configuration management
    - Health monitoring
    """
    
    def __init__(self):
        self.event_bus = EventBus()
        self.agents: Dict[str, AutonomousAgent] = {}
        self.is_running = False
        self._config: Dict[str, Any] = {}
    
    def register_agent(self, agent: AutonomousAgent) -> None:
        """Register an agent with the runtime."""
        self.agents[agent.name] = agent
        logger.info(f"Registered agent: {agent.name}")
    
    def unregister_agent(self, name: str) -> None:
        """Unregister an agent."""
        if name in self.agents:
            del self.agents[name]
            logger.info(f"Unregistered agent: {name}")
    
    async def start(self) -> None:
        """Start the runtime and all agents."""
        self.is_running = True
        logger.info("Starting Clearledgr Agent Runtime...")
        
        for agent in self.agents.values():
            await agent.start()
        
        logger.info(f"Agent Runtime started with {len(self.agents)} agents")
    
    async def stop(self) -> None:
        """Stop all agents and the runtime."""
        logger.info("Stopping Clearledgr Agent Runtime...")
        
        for agent in self.agents.values():
            await agent.stop()
        
        self.is_running = False
        logger.info("Agent Runtime stopped")
    
    async def publish_event(self, event: Event) -> None:
        """Publish an event to the bus."""
        await self.event_bus.publish(event)
    
    def get_status(self) -> Dict[str, Any]:
        """Get runtime status."""
        return {
            "is_running": self.is_running,
            "agents": {
                name: {
                    "is_running": agent.is_running,
                    "subscribed_events": [e.value for e in agent._subscribed_events],
                }
                for name, agent in self.agents.items()
            },
            "recent_events": len(self.event_bus._event_history),
        }
    
    def configure(self, config: Dict[str, Any]) -> None:
        """Configure the runtime."""
        self._config.update(config)
        
        # Apply agent-specific config
        for agent_name, agent_config in config.get("agents", {}).items():
            if agent_name in self.agents:
                agent = self.agents[agent_name]
                if "auto_execute_threshold" in agent_config:
                    agent.auto_execute_threshold = agent_config["auto_execute_threshold"]
                if "notify_after_threshold" in agent_config:
                    agent.notify_after_threshold = agent_config["notify_after_threshold"]


# Global runtime instance
_runtime: Optional[AgentRuntime] = None


def get_runtime() -> AgentRuntime:
    """Get or create the global agent runtime."""
    global _runtime
    if _runtime is None:
        _runtime = AgentRuntime()
    return _runtime


async def start_runtime() -> AgentRuntime:
    """Start the global runtime."""
    runtime = get_runtime()
    await runtime.start()
    return runtime


async def stop_runtime() -> None:
    """Stop the global runtime."""
    global _runtime
    if _runtime:
        await _runtime.stop()
        _runtime = None
