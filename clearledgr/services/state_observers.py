"""Observer pattern for AP state transitions.

Decouples side effects (audit trail, vendor feedback, notifications) from
the core state transition logic in invoice_workflow.py.  Observers are
fire-and-forget: errors are logged but never block the transition.

Usage:
    registry = StateObserverRegistry()
    registry.register(AuditTrailObserver(db))
    registry.register(VendorFeedbackObserver(db))

    # After a successful DB state change:
    await registry.notify(StateTransitionEvent(
        ap_item_id="ap-123",
        organization_id="acme",
        old_state="needs_approval",
        new_state="approved",
        actor_id="user@acme.com",
    ))
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StateTransitionEvent:
    """Immutable record of a state transition."""

    ap_item_id: str
    organization_id: str
    old_state: str
    new_state: str
    actor_id: Optional[str] = None
    correlation_id: Optional[str] = None
    source: str = "invoice_workflow"
    gmail_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class StateObserver(ABC):
    """Base class for state transition observers."""

    @abstractmethod
    async def on_transition(self, event: StateTransitionEvent) -> None:
        """React to a state transition.  Must not raise."""


class StateObserverRegistry:
    """Fan-out dispatcher for state transition events."""

    def __init__(self) -> None:
        self._observers: List[StateObserver] = []

    def register(self, observer: StateObserver) -> None:
        self._observers.append(observer)

    async def notify(self, event: StateTransitionEvent) -> None:
        """Dispatch *event* to all registered observers.

        Each observer runs independently; a failure in one does not affect
        the others or the caller.
        """
        for obs in self._observers:
            try:
                await obs.on_transition(event)
            except Exception as exc:
                logger.error(
                    "Observer %s failed on %s -> %s (ap_item=%s): %s",
                    type(obs).__name__,
                    event.old_state,
                    event.new_state,
                    event.ap_item_id,
                    exc,
                )


# ---------------------------------------------------------------------------
# Concrete observers
# ---------------------------------------------------------------------------


class AuditTrailObserver(StateObserver):
    """Records an audit event for every state transition."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def on_transition(self, event: StateTransitionEvent) -> None:
        if not hasattr(self._db, "append_ap_audit_event"):
            return
        self._db.append_ap_audit_event({
            "ap_item_id": event.ap_item_id,
            "organization_id": event.organization_id,
            "event_type": "state_transition",
            "source": event.source,
            "actor": event.actor_id or "system",
            "correlation_id": event.correlation_id,
            "details": {
                "old_state": event.old_state,
                "new_state": event.new_state,
                **(event.metadata or {}),
            },
        })


class VendorFeedbackObserver(StateObserver):
    """Updates vendor profile when an invoice reaches a terminal posting state."""

    _OUTCOME_STATES = frozenset({"posted_to_erp", "failed_post"})

    def __init__(self, db: Any) -> None:
        self._db = db

    async def on_transition(self, event: StateTransitionEvent) -> None:
        if event.new_state not in self._OUTCOME_STATES:
            return
        if not hasattr(self._db, "update_vendor_profile_from_outcome"):
            return

        vendor_name = (event.metadata or {}).get("vendor_name")
        if not vendor_name:
            return

        try:
            self._db.update_vendor_profile_from_outcome(
                organization_id=event.organization_id,
                vendor_name=vendor_name,
                outcome=event.new_state,
            )
        except Exception as exc:
            logger.warning("VendorFeedbackObserver: %s", exc)


class NotificationObserver(StateObserver):
    """Enqueues a notification when the state requires human attention."""

    _NOTIFY_STATES = frozenset({"needs_approval", "needs_info", "approved", "rejected"})

    def __init__(self, db: Any) -> None:
        self._db = db

    async def on_transition(self, event: StateTransitionEvent) -> None:
        if event.new_state not in self._NOTIFY_STATES:
            return
        if not hasattr(self._db, "enqueue_notification"):
            return

        self._db.enqueue_notification(
            organization_id=event.organization_id,
            channel="state_change",
            payload={
                "ap_item_id": event.ap_item_id,
                "new_state": event.new_state,
                "old_state": event.old_state,
                "actor_id": event.actor_id,
                "correlation_id": event.correlation_id,
            },
            ap_item_id=event.ap_item_id,
        )
