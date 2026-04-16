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
        self._observer_failure_count: int = 0

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
                self._observer_failure_count += 1
                logger.error(
                    "Observer %s failed on event %s->%s (ap_item=%s, org=%s, source=%s): %s",
                    type(obs).__name__,
                    event.old_state,
                    event.new_state,
                    event.ap_item_id,
                    event.organization_id,
                    event.source,
                    exc,
                    exc_info=True,
                )


# ---------------------------------------------------------------------------
# Concrete observers
# ---------------------------------------------------------------------------


class AuditTrailObserver(StateObserver):
    """Records an audit event for every state transition."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # §4 Principle 03: DID-WHY-NEXT for every state transition
    _NEXT_ACTION_MAP = {
        "received": "Extraction and validation in progress.",
        "validated": "Routing to approval or auto-posting based on confidence.",
        "needs_approval": "Waiting for human approval via Slack or Gmail.",
        "needs_info": "Vendor follow-up required before processing can continue.",
        "approved": "Queued for ERP posting.",
        "ready_to_post": "Posting to ERP.",
        "posted_to_erp": "Override window open. Payment scheduled per terms.",
        "failed_post": "Retry scheduled or manual resolution required.",
        "reversed": "ERP post reversed. Item closed out as reversed (no payment executed).",
        "snoozed": "Snoozed. Will return to queue when timer expires.",
        "rejected": "No further action.",
        "closed": "Lifecycle complete.",
    }

    async def on_transition(self, event: StateTransitionEvent) -> None:
        if not hasattr(self._db, "append_ap_audit_event"):
            return
        next_action = self._NEXT_ACTION_MAP.get(event.new_state, "")
        self._db.append_ap_audit_event({
            "ap_item_id": event.ap_item_id,
            "organization_id": event.organization_id,
            "event_type": "state_transition",
            "source": event.source,
            "actor": event.actor_id or "system",
            "correlation_id": event.correlation_id,
            "next_action": next_action,
            "details": {
                "old_state": event.old_state,
                "new_state": event.new_state,
                "next_action": next_action,
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


class GmailLabelObserver(StateObserver):
    """Synchronize Gmail labels to match the canonical finance record."""

    def __init__(self, db: Any) -> None:
        self._db = db

    @staticmethod
    def _record_value(record: Any, key: str) -> Any:
        if isinstance(record, dict):
            return record.get(key)
        return getattr(record, key, None)

    async def on_transition(self, event: StateTransitionEvent) -> None:
        if not event.gmail_id or not hasattr(self._db, "get_invoice_status"):
            return

        row = self._db.get_invoice_status(event.gmail_id)
        if not isinstance(row, dict):
            return

        message_id = str(row.get("message_id") or "").strip() or str(event.gmail_id or "").strip()
        user_id = str(row.get("user_id") or "").strip()
        finance_email = None

        if hasattr(self._db, "get_finance_email_by_gmail_id") and message_id:
            try:
                finance_email = self._db.get_finance_email_by_gmail_id(message_id)
            except Exception:
                finance_email = None

        if not user_id and finance_email is not None:
            user_id = str(self._record_value(finance_email, "user_id") or "").strip()
        if not user_id:
            return

        try:
            from clearledgr.services.gmail_api import GmailAPIClient
            from clearledgr.services.gmail_labels import sync_finance_labels

            client = GmailAPIClient(user_id)
            if not await client.ensure_authenticated():
                return

            await sync_finance_labels(
                client,
                message_id,
                ap_item=row,
                finance_email=finance_email,
                user_email=user_id,
            )
        except Exception as exc:
            logger.warning("GmailLabelObserver: %s", exc)


class OverrideWindowObserver(StateObserver):
    """Open an override window + post the Slack undo card on posted_to_erp.

    Per DESIGN_THESIS.md §8, every autonomous ERP post opens a time-bounded
    reversal window. This observer is the canonical hook point: when an AP
    item transitions into ``posted_to_erp``, it creates the
    ``override_windows`` row via OverrideWindowService, then posts the
    Slack undo card and stores the message ts back on the row so the
    background reaper and the action handler can find it later.

    The observer is fire-and-forget — failures here MUST NOT roll back
    the post, because the post itself already succeeded at the ERP level.
    Any failure simply means there is no undo card / no override window
    for this item, which the customer can recover by reposting the card
    via the ops surface (Phase 1.4 also exposes
    POST /ap-items/{id}/reverse for the API path).
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def on_transition(self, event: StateTransitionEvent) -> None:
        if event.new_state != "posted_to_erp":
            return
        if not event.ap_item_id:
            return

        # Resolve the AP item to get the persisted erp_reference + erp_type
        try:
            ap_item = self._db.get_ap_item(event.ap_item_id) or {}
        except Exception as exc:
            logger.warning(
                "[OverrideWindowObserver] Could not load AP item %s: %s",
                event.ap_item_id, exc,
            )
            return

        erp_reference = ap_item.get("erp_reference")
        if not erp_reference:
            logger.debug(
                "[OverrideWindowObserver] AP item %s has no erp_reference yet — skipping",
                event.ap_item_id,
            )
            return

        # erp_type comes from metadata (sync_token persistence wrote it)
        metadata = ap_item.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                import json as _json
                metadata = _json.loads(metadata)
            except Exception:
                metadata = {}
        erp_type = (
            (metadata or {}).get("erp_type")
            or (event.metadata or {}).get("erp_type")
        )

        # Open the window via the service. action_type is "erp_post"
        # because this observer reacts to the posted_to_erp transition
        # specifically. Future autonomous actions (payment_execution,
        # vendor_onboarding) get their own observers with their own
        # action_type strings.
        try:
            from clearledgr.services.override_window import (
                get_override_window_service,
            )
            service = get_override_window_service(
                event.organization_id, db=self._db
            )
            # §7.4: pass confidence so medium-confidence posts get a shorter window
            item_confidence = float(ap_item.get("confidence") or 0.99)

            window = service.open_window(
                ap_item_id=event.ap_item_id,
                erp_reference=str(erp_reference),
                erp_type=erp_type,
                action_type="erp_post",
                confidence=item_confidence,
            )
        except Exception as exc:
            logger.warning(
                "[OverrideWindowObserver] open_window failed for ap_item=%s: %s",
                event.ap_item_id, exc,
            )
            return

        # Post the Slack undo card (best-effort)
        try:
            from clearledgr.services.slack_cards import post_undo_card_for_window
            slack_refs = await post_undo_card_for_window(
                organization_id=event.organization_id,
                ap_item=ap_item,
                window=window,
                db=self._db,
            )
            if slack_refs:
                self._db.update_override_window_slack_refs(
                    window["id"],
                    slack_channel=slack_refs.get("channel"),
                    slack_message_ts=slack_refs.get("message_ts"),
                )
        except Exception as exc:
            logger.warning(
                "[OverrideWindowObserver] Slack undo card post failed: %s", exc,
            )


class VendorDomainTrackingObserver(StateObserver):
    """Record a vendor's sender domain on first successful post.

    Phase 2.2 (DESIGN_THESIS.md §8 — vendor domain lock). This
    observer implements the TOFU (trust on first use) side of the
    vendor domain lock: the first invoice from a brand-new vendor
    was already blocked by ``first_payment_hold`` and routed to human
    review. When the human approves and the AP item reaches
    ``posted_to_erp``, we record the sender domain as trusted so
    future invoices for the same vendor are checked against it.

    The observer only fires when the vendor has NO existing trusted
    domains. Once a vendor has at least one trusted domain, only the
    CFO can add or remove entries via the
    ``/api/vendors/{vendor}/trusted-domains`` API — no automatic
    expansion of the allowlist on subsequent posts. This prevents an
    adversary from silently adding their domain to an established
    vendor's allowlist by pushing a later invoice through.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def on_transition(self, event: StateTransitionEvent) -> None:
        if event.new_state != "posted_to_erp":
            return
        if not event.ap_item_id:
            return

        try:
            ap_item = self._db.get_ap_item(event.ap_item_id) or {}
        except Exception as exc:
            logger.debug(
                "[VendorDomainTrackingObserver] get_ap_item failed: %s", exc
            )
            return

        vendor_name = ap_item.get("vendor_name")
        sender = ap_item.get("sender")
        if not vendor_name or not sender:
            return

        try:
            from clearledgr.services.vendor_domain_lock import (
                get_vendor_domain_lock_service,
            )
            lock_svc = get_vendor_domain_lock_service(
                event.organization_id, db=self._db
            )
            lock_svc.record_domain_on_first_post(
                vendor_name=vendor_name,
                sender=sender,
            )
        except Exception as exc:
            logger.warning(
                "[VendorDomainTrackingObserver] record_domain_on_first_post failed: %s",
                exc,
            )
