"""Agent Event System — Agent Design Specification §2.

Formal event types and the AgentEvent data class. Every event that enters
the system has a type, a source, and a payload. The planning engine
dispatches on event type.

Adding a new event type means:
1. Add the enum value here
2. Add a handler in the planning engine
No other part of the system changes.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class AgentEventType(str, Enum):
    """§2.2: Every event type the agent can process."""

    # Core invoice lifecycle
    EMAIL_RECEIVED = "email_received"
    APPROVAL_RECEIVED = "approval_received"
    ERP_GRN_CONFIRMED = "erp_grn_confirmed"
    PAYMENT_CONFIRMED = "payment_confirmed"

    # Vendor onboarding
    VENDOR_RESPONSE_RECEIVED = "vendor_response_received"
    KYC_DOCUMENT_RECEIVED = "kyc_document_received"
    IBAN_CHANGE_SUBMITTED = "iban_change_submitted"

    # Timer-based resumption
    TIMER_FIRED = "timer_fired"

    # Human-in-the-loop
    MANUAL_CLASSIFICATION = "manual_classification"

    # Override window
    OVERRIDE_WINDOW_EXPIRED = "override_window_expired"

    # Bidirectional Gmail label sync — user applies a Clearledgr/* label
    # in Gmail and the agent reacts (approve / reject / snooze / review).
    # Phase 2 of the Gmail-labels-as-AP-pipeline workstream.
    LABEL_CHANGED = "label_changed"


@dataclass
class AgentEvent:
    """A single event entering the agent system.

    Events are immutable after creation. They are enqueued into Redis Streams
    and consumed by Celery workers.
    """

    type: AgentEventType
    source: str  # "gmail_pubsub", "slack_callback", "timer", "manual", etc.
    payload: Dict[str, Any]
    organization_id: str
    id: str = field(default_factory=lambda: f"EVT-{uuid.uuid4().hex[:12]}")
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    priority: str = "standard"  # "standard" | "high_priority"
    idempotency_key: Optional[str] = None  # Gmail message ID for dedup

    def to_dict(self) -> Dict[str, str]:
        """Serialize for Redis Streams (all values must be strings)."""
        import json

        return {
            "id": self.id,
            "type": self.type.value,
            "source": self.source,
            "payload": json.dumps(self.payload),
            "organization_id": self.organization_id,
            "created_at": self.created_at,
            "priority": self.priority,
            "idempotency_key": self.idempotency_key or "",
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> AgentEvent:
        """Deserialize from Redis Streams."""
        import json

        return cls(
            id=data.get("id", f"EVT-{uuid.uuid4().hex[:12]}"),
            type=AgentEventType(data["type"]),
            source=data.get("source", "unknown"),
            payload=json.loads(data.get("payload", "{}")),
            organization_id=data.get("organization_id", "default"),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            priority=data.get("priority", "standard"),
            idempotency_key=data.get("idempotency_key") or None,
        )
