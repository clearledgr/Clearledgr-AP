"""Tests for state transition observer pattern."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from clearledgr.services.state_observers import (
    AuditTrailObserver,
    GmailLabelObserver,
    NotificationObserver,
    StateObserver,
    StateObserverRegistry,
    StateTransitionEvent,
    VendorFeedbackObserver,
)


def _run(coro):
    """Run an async coroutine synchronously (no pytest-asyncio needed)."""
    return asyncio.run(coro)


def _make_event(**overrides) -> StateTransitionEvent:
    defaults = {
        "ap_item_id": "ap-001",
        "organization_id": "org-test",
        "old_state": "needs_approval",
        "new_state": "approved",
        "actor_id": "user@test.com",
        "correlation_id": "corr-1",
        "source": "test",
        "gmail_id": "gmail-001",
    }
    defaults.update(overrides)
    return StateTransitionEvent(**defaults)


# ---------------------------------------------------------------------------
# StateTransitionEvent
# ---------------------------------------------------------------------------


def test_event_is_immutable():
    event = _make_event()
    with pytest.raises(AttributeError):
        event.new_state = "rejected"


def test_event_default_metadata_is_empty():
    event = _make_event()
    assert event.metadata == {}


# ---------------------------------------------------------------------------
# StateObserverRegistry
# ---------------------------------------------------------------------------


def test_registry_dispatches_to_all_observers():
    """All registered observers should receive the event.

    Uses inline mode — outbox mode (production default) enqueues to a
    DB-backed queue and the observers fire asynchronously through the
    OutboxWorker, which is a separate concern. Inline mode tests the
    fan-out semantics directly.
    """
    calls = []

    class TrackingObserver(StateObserver):
        def __init__(self, name):
            self.name = name

        async def on_transition(self, event):
            calls.append(self.name)

    registry = StateObserverRegistry(inline=True)
    registry.register(TrackingObserver("a"))
    registry.register(TrackingObserver("b"))
    registry.register(TrackingObserver("c"))

    _run(registry.notify(_make_event()))
    assert calls == ["a", "b", "c"]


def test_registry_isolates_observer_failures():
    """A failing observer must not prevent others from running.

    Same inline-mode rationale as ``test_registry_dispatches_to_all_observers``:
    outbox mode handles failure isolation via per-row retry, but the
    inline fan-out is what this test guards.
    """
    calls = []

    class GoodObserver(StateObserver):
        async def on_transition(self, event):
            calls.append("good")

    class BadObserver(StateObserver):
        async def on_transition(self, event):
            raise RuntimeError("boom")

    registry = StateObserverRegistry(inline=True)
    registry.register(GoodObserver())
    registry.register(BadObserver())
    registry.register(GoodObserver())

    _run(registry.notify(_make_event()))
    assert calls == ["good", "good"]


def test_registry_no_observers_is_noop():
    """Empty registry should not error."""
    registry = StateObserverRegistry(inline=True)
    _run(registry.notify(_make_event()))


# ---------------------------------------------------------------------------
# AuditTrailObserver
# ---------------------------------------------------------------------------


def test_audit_observer_records_event():
    db = MagicMock()
    obs = AuditTrailObserver(db)

    event = _make_event(old_state="validated", new_state="needs_approval")
    _run(obs.on_transition(event))

    db.append_audit_event.assert_called_once()
    call_arg = db.append_audit_event.call_args[0][0]
    assert call_arg["event_type"] == "state_transition"
    assert call_arg["details"]["old_state"] == "validated"
    assert call_arg["details"]["new_state"] == "needs_approval"


def test_audit_observer_skips_without_method():
    db = MagicMock(spec=[])  # no append_audit_event
    obs = AuditTrailObserver(db)
    _run(obs.on_transition(_make_event()))  # should not raise


# ---------------------------------------------------------------------------
# VendorFeedbackObserver
# ---------------------------------------------------------------------------


def test_vendor_observer_updates_on_posted():
    db = MagicMock()
    obs = VendorFeedbackObserver(db)

    event = _make_event(
        new_state="posted_to_erp",
        metadata={"vendor_name": "Acme Corp"},
    )
    _run(obs.on_transition(event))

    db.update_vendor_profile_from_outcome.assert_called_once_with(
        organization_id="org-test",
        vendor_name="Acme Corp",
        outcome="posted_to_erp",
    )


def test_vendor_observer_updates_on_failed_post():
    db = MagicMock()
    obs = VendorFeedbackObserver(db)

    event = _make_event(
        new_state="failed_post",
        metadata={"vendor_name": "Acme Corp"},
    )
    _run(obs.on_transition(event))

    db.update_vendor_profile_from_outcome.assert_called_once()


def test_vendor_observer_ignores_non_outcome_states():
    db = MagicMock()
    obs = VendorFeedbackObserver(db)

    _run(obs.on_transition(_make_event(new_state="approved")))
    db.update_vendor_profile_from_outcome.assert_not_called()


def test_vendor_observer_ignores_missing_vendor():
    db = MagicMock()
    obs = VendorFeedbackObserver(db)

    event = _make_event(new_state="posted_to_erp", metadata={})
    _run(obs.on_transition(event))
    db.update_vendor_profile_from_outcome.assert_not_called()


# ---------------------------------------------------------------------------
# NotificationObserver
# ---------------------------------------------------------------------------


def test_notification_observer_enqueues_on_needs_approval():
    db = MagicMock()
    obs = NotificationObserver(db)

    _run(obs.on_transition(_make_event(new_state="needs_approval")))
    db.enqueue_notification.assert_called_once()
    payload = db.enqueue_notification.call_args[1]["payload"]
    assert payload["new_state"] == "needs_approval"


def test_notification_observer_ignores_non_notify_states():
    db = MagicMock()
    obs = NotificationObserver(db)

    _run(obs.on_transition(_make_event(new_state="posted_to_erp")))
    db.enqueue_notification.assert_not_called()


def test_notification_observer_skips_without_method():
    db = MagicMock(spec=[])  # no enqueue_notification
    obs = NotificationObserver(db)
    _run(obs.on_transition(_make_event(new_state="needs_approval")))  # should not raise


# ---------------------------------------------------------------------------
# GmailLabelObserver
# ---------------------------------------------------------------------------


def test_gmail_label_observer_syncs_message_labels():
    db = MagicMock()
    db.get_invoice_status.return_value = {
        "id": "ap-001",
        "thread_id": "thread-001",
        "message_id": "msg-001",
        "user_id": "gmail-user-1",
        "state": "approved",
        "metadata": {"email_type": "invoice"},
    }
    db.get_finance_email_by_gmail_id.return_value = MagicMock(
        user_id="gmail-user-1",
        email_type="invoice",
        status="processed",
        metadata={},
    )

    fake_client = MagicMock()
    fake_client.ensure_authenticated = AsyncMock(return_value=True)
    sync_labels = AsyncMock(return_value={"processed", "invoices", "approved"})

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("clearledgr.services.gmail_api.GmailAPIClient", lambda _user_id: fake_client)
        mp.setattr("clearledgr.services.gmail_labels.sync_finance_labels", sync_labels)

        obs = GmailLabelObserver(db)
        _run(obs.on_transition(_make_event(gmail_id="thread-001", new_state="approved")))

    sync_labels.assert_awaited_once()
    assert sync_labels.await_args.args[0] is fake_client
    assert sync_labels.await_args.args[1] == "msg-001"


def test_gmail_label_observer_skips_without_user_context():
    db = MagicMock()
    db.get_invoice_status.return_value = {
        "id": "ap-001",
        "thread_id": "thread-001",
        "message_id": "msg-001",
        "user_id": "",
        "state": "approved",
        "metadata": {"email_type": "invoice"},
    }
    db.get_finance_email_by_gmail_id.return_value = None

    obs = GmailLabelObserver(db)
    _run(obs.on_transition(_make_event(gmail_id="thread-001", new_state="approved")))


# ---------------------------------------------------------------------------
# Integration: verify observers fire from InvoiceWorkflowService
# ---------------------------------------------------------------------------


def test_observer_fires_on_workflow_transition(postgres_test_db):
    """InvoiceWorkflowService._transition_invoice_state should dispatch to observers."""
    import os
    os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "test-key-for-observer-test")
    os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")

    from clearledgr.core import database as db_mod

    db = db_mod.get_db()
    db.initialize()

    # Create org + AP item
    import uuid
    org_id = f"obs-org-{uuid.uuid4().hex[:8]}"
    db.create_organization(org_id, "Observer Test Org", settings="{}")
    db.create_ap_item({
        "id": f"ap-obs-{uuid.uuid4().hex[:8]}",
        "thread_id": "gmail-obs-1",
        "organization_id": org_id,
        "state": "validated",
        "vendor": "TestVendor",
        "amount": 100.0,
    })

    from clearledgr.services.invoice_workflow import InvoiceWorkflowService
    svc = InvoiceWorkflowService(org_id)

    # Verify observer registry exists
    assert svc._observer_registry is not None
    assert len(svc._observer_registry._observers) >= 1

    # Force inline observer dispatch for this test. Production uses
    # outbox mode (durable + retried via OutboxWorker), but the
    # contract under test here is "transition fans out to observers" —
    # outbox round-trip is a separate concern handled in the outbox
    # tests. Without flipping inline=True the audit observer is
    # enqueued instead of called, and audit_calls stays empty.
    svc._observer_registry._inline = True

    # Patch the audit observer to track calls
    audit_calls = []
    original_on_transition = svc._observer_registry._observers[0].on_transition

    async def tracking_on_transition(event):
        audit_calls.append(event)
        await original_on_transition(event)

    svc._observer_registry._observers[0].on_transition = tracking_on_transition

    # Trigger transition
    result = svc._transition_invoice_state(
        gmail_id="gmail-obs-1",
        target_state="needs_approval",
    )
    assert result is True
    assert len(audit_calls) == 1
    assert audit_calls[0].old_state == "validated"
    assert audit_calls[0].new_state == "needs_approval"
