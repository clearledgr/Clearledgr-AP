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
    # Channel of origin — drives observer-side gating so Gmail-only
    # observers (label sync, email-fetch) short-circuit cleanly when
    # the AP item entered via NetSuite/SAP/portal/etc.
    source_type: str = "gmail"
    erp_native: bool = False


class StateObserver(ABC):
    """Base class for state transition observers."""

    @abstractmethod
    async def on_transition(self, event: StateTransitionEvent) -> None:
        """React to a state transition.  Must not raise."""


class StateObserverRegistry:
    """Fan-out dispatcher for state transition events.

    Two dispatch modes:

    * **Outbox mode (default)**: each registered observer becomes a
      consumer of outbox events. ``notify`` enqueues one outbox row
      per registered observer (with target ``observer:<class_name>``)
      inside whatever transaction is active. The :class:`OutboxWorker`
      drains the queue and calls each observer's ``on_transition``
      with the rebuilt :class:`StateTransitionEvent`. Closes the race
      where a transition could commit while the in-process fan-out
      crashed mid-flight.

    * **Inline mode**: legacy synchronous fan-out, used by
      tests + by paths that need observer side-effects to be visible
      before the caller returns. Set ``inline=True`` at construction
      time. The existing observer interface is unchanged in either
      mode.

    Outbox mode is the production default. Inline mode is opt-in.
    """

    def __init__(self, *, inline: bool = False) -> None:
        self._observers: List[StateObserver] = []
        self._observer_failure_count: int = 0
        self._inline = inline

    def register(self, observer: StateObserver) -> None:
        self._observers.append(observer)

    async def notify(self, event: StateTransitionEvent) -> None:
        """Dispatch *event* to all registered observers.

        In outbox mode (default), enqueues one outbox row per
        observer; the worker fans out durably + with retry.

        In inline mode, runs each observer in-process — failures are
        logged but isolated from each other and from the caller.
        """
        if self._inline:
            await self._notify_inline(event)
            return

        # Outbox mode: enqueue one row per observer. Failures here
        # would be DB connectivity issues — which we want to surface
        # rather than silently drop, since the side-effect is
        # supposed to be durable.
        from clearledgr.services.outbox import OutboxWriter
        writer = OutboxWriter(event.organization_id)
        payload = _serialize_event(event)
        for obs in self._observers:
            obs_name = type(obs).__name__
            # Dedupe key prevents duplicate enqueues when notify is
            # called twice for the same transition (e.g., retry path).
            dedupe_key = (
                f"observer:{obs_name}:{event.ap_item_id}:"
                f"{event.old_state}->{event.new_state}:"
                f"{event.correlation_id or event.metadata.get('idempotency_key', '')}"
            )
            try:
                writer.enqueue(
                    event_type=f"state.{event.new_state}",
                    target=f"observer:{obs_name}",
                    payload=payload,
                    dedupe_key=dedupe_key,
                    actor=event.actor_id or event.source,
                )
            except Exception as exc:  # noqa: BLE001
                # Don't sink the caller — fall back to inline for
                # this observer if the outbox enqueue failed.
                logger.warning(
                    "outbox enqueue failed for observer %s; falling back to inline — %s",
                    obs_name, exc,
                )
                try:
                    await obs.on_transition(event)
                except Exception as obs_exc:  # noqa: BLE001
                    self._observer_failure_count += 1
                    logger.error(
                        "Inline-fallback observer %s also failed on %s->%s: %s",
                        obs_name, event.old_state, event.new_state, obs_exc,
                    )

    async def _notify_inline(self, event: StateTransitionEvent) -> None:
        """Legacy inline fan-out — the original behavior, preserved
        for tests and for paths that must see side-effects before
        returning."""
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


# ─── Outbox integration ────────────────────────────────────────────


def _serialize_event(event: StateTransitionEvent) -> Dict[str, Any]:
    """Pack a StateTransitionEvent into the JSON payload the outbox
    worker will use to rebuild it before calling the observer."""
    return {
        "ap_item_id": event.ap_item_id,
        "organization_id": event.organization_id,
        "old_state": event.old_state,
        "new_state": event.new_state,
        "actor_id": event.actor_id,
        "correlation_id": event.correlation_id,
        "source": event.source,
        "gmail_id": event.gmail_id,
        "metadata": dict(event.metadata or {}),
        "source_type": event.source_type,
        "erp_native": event.erp_native,
    }


def _deserialize_event(payload: Dict[str, Any]) -> StateTransitionEvent:
    return StateTransitionEvent(
        ap_item_id=str(payload.get("ap_item_id") or ""),
        organization_id=str(payload.get("organization_id") or ""),
        old_state=str(payload.get("old_state") or ""),
        new_state=str(payload.get("new_state") or ""),
        actor_id=payload.get("actor_id"),
        correlation_id=payload.get("correlation_id"),
        source=str(payload.get("source") or "invoice_workflow"),
        gmail_id=payload.get("gmail_id"),
        metadata=dict(payload.get("metadata") or {}),
        source_type=str(payload.get("source_type") or "gmail"),
        erp_native=bool(payload.get("erp_native") or False),
    )


# Singleton-ish: keep the dispatch registry alive for the worker so
# we can resolve observer-class-name → instance at handler time.
_OBSERVER_DISPATCH: Dict[str, "StateObserver"] = {}


def register_observer_for_outbox_dispatch(observer: "StateObserver") -> None:
    """Register an observer instance so the outbox handler can
    resolve ``target='observer:<ClassName>'`` to the right callable.

    Called from each ``InvoiceWorkflowService.__init__`` so the
    worker process (which may be a different worker than the one
    that enqueued) can dispatch to the same observer types."""
    name = type(observer).__name__
    existing = _OBSERVER_DISPATCH.get(name)
    if existing is None:
        _OBSERVER_DISPATCH[name] = observer


async def _outbox_handler_observer(outbox_event) -> None:
    """Outbox handler for ``target = 'observer:<ClassName>'`` —
    resolves the observer instance + calls on_transition with the
    rebuilt StateTransitionEvent. Raised exceptions trigger the
    outbox's retry/dead-letter logic."""
    target = outbox_event.target
    if not target.startswith("observer:"):
        raise ValueError(f"unexpected target {target!r}")
    obs_name = target.split(":", 1)[1]
    observer = _OBSERVER_DISPATCH.get(obs_name)
    if observer is None:
        raise LookupError(
            f"no observer registered for {obs_name!r} — "
            f"workers must call register_observer_for_outbox_dispatch on boot"
        )
    state_event = _deserialize_event(outbox_event.payload)
    await observer.on_transition(state_event)


def _register_outbox_handler() -> None:
    """One-shot registration of the observer-prefix handler with the
    outbox. Safe to call repeatedly — outbox.register_handler is
    idempotent for the same callable."""
    try:
        from clearledgr.services.outbox import register_handler
        register_handler("observer", _outbox_handler_observer)
    except Exception as exc:  # noqa: BLE001
        logger.warning("state_observers: outbox handler registration failed — %s", exc)


_register_outbox_handler()


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
        if not hasattr(self._db, "append_audit_event"):
            return
        next_action = self._NEXT_ACTION_MAP.get(event.new_state, "")
        self._db.append_audit_event({
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
        # ERP-native intake (NetSuite SuiteScript, SAP Event Mesh /
        # BAdI) doesn't have a Gmail message — the synthetic gmail_id
        # is `f"<source>-bill:<id>"`. Calling the Gmail labels API
        # against that would 404 every time. Short-circuit early.
        if event.source_type != "gmail" or event.erp_native:
            return
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
        # Skip for ERP-native bills — the synthetic sender
        # `<netsuite@erp-native>` / `<sap-s4hana@erp-native>` is
        # not a real vendor domain to learn against. Phase B/C will
        # populate the real vendor email on the AP item from the
        # ERP-side vendor record; until then a domain match here
        # would poison the trusted-domain TOFU set.
        if event.source_type != "gmail" or event.erp_native:
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
