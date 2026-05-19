"""Tests for Group 7: vendor-onboarding planner deprecation.

The vendor-onboarding subsystem is dormant per the 2026-04-30
product call. The ``vendor_onboarding_session`` Box type is no
longer registered in ``BoxRegistry`` (only ``ap_item`` is live).

Without this gate, deprecated event types like
``IBAN_CHANGE_SUBMITTED`` would still drive the planner, which
would emit real fraud-control actions (``check_iban_change``,
``freeze_vendor_payments``, ``initiate_iban_verification``)
that write to the vendor_profiles table without a Box anchor —
no audit timeline, no Rule 1 pre-write coverage, no operator
queue surface.

The gate lives at the top of ``plan()``: if the event type is in
``_DEPRECATED_VO_EVENTS``, ``plan()`` raises
``RuntimeError`` and (when a Box is named in the payload) records
a ``deprecated_vo_event`` box exception so the operator queue
sees any accidental reactivation.

What's tested here:

  1. Each of the 9 deprecated event types raises on plan().
  2. The exception message references the deprecation date so
     anyone hitting it can find the memory entry.
  3. A plan() call with a Box-attached deprecated event records
     a box_exception with type ``deprecated_vo_event``.
  4. Deprecated events do NOT call the underlying ``_plan_*``
     handler (so no real actions get queued).
  5. Live event types (``EMAIL_RECEIVED``, ``APPROVAL_RECEIVED``,
     ``TIMER_FIRED``) are unaffected — drift fence so future
     deprecations don't accidentally widen the gate.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core.events import AgentEvent, AgentEventType  # noqa: E402
from solden.core.planning_engine import (  # noqa: E402
    DeterministicPlanningEngine,
    _DEPRECATED_VO_EVENTS,
)


# ─── Deprecated event types ────────────────────────────────────────


DEPRECATED_TYPES = [
    AgentEventType.KYC_DOCUMENT_RECEIVED,
    AgentEventType.IBAN_CHANGE_SUBMITTED,
    AgentEventType.ONBOARDING_INITIATED,
    AgentEventType.VENDOR_PORTAL_ACCESSED,
    AgentEventType.VENDOR_SUBMISSION_RECEIVED,
    AgentEventType.KYC_CHECK_COMPLETED,
    AgentEventType.OPEN_BANKING_VERIFICATION_COMPLETED,
    AgentEventType.AP_MANAGER_DECISION_RECEIVED,
    AgentEventType.VENDOR_ACTIVATED,
]


@pytest.fixture()
def planner():
    db = MagicMock()
    db.raise_box_exception = MagicMock(return_value=None)
    return DeterministicPlanningEngine(db=db)


class TestDeprecatedVoEventsAreGated:
    @pytest.mark.parametrize("event_type", DEPRECATED_TYPES)
    def test_each_deprecated_type_raises_on_plan(self, planner, event_type):
        event = AgentEvent(
            type=event_type,
            source="test",
            payload={"vendor_id": "vendor-1"},
            organization_id="orgVO",
        )
        with pytest.raises(RuntimeError) as exc_info:
            planner.plan(event)
        message = str(exc_info.value)
        # Reference the deprecation date so future readers can
        # find the memory entry from the error alone.
        assert "2026-04-30" in message
        assert "dormant" in message.lower()
        assert event_type.value in message

    def test_deprecation_set_matches_audit_inventory(self):
        """Drift fence: the set must contain exactly the 9 event
        types the audit identified as VO-subsystem. New live event
        types must not accidentally be added; existing entries
        must not be silently removed."""
        assert _DEPRECATED_VO_EVENTS == frozenset(DEPRECATED_TYPES)
        assert len(_DEPRECATED_VO_EVENTS) == 9


class TestBoxExceptionRecorded:
    def test_box_attached_deprecated_event_records_box_exception(self, planner):
        """When a deprecated event names a Box in the payload, the
        planner records a ``deprecated_vo_event`` box exception so
        operator-queue surfaces see the accidental reactivation."""
        event = AgentEvent(
            type=AgentEventType.IBAN_CHANGE_SUBMITTED,
            source="test",
            payload={
                "vendor_id": "vendor-2",
                "ap_item_id": "AP-vo-1",  # implies box_type=ap_item
            },
            organization_id="orgVO",
        )
        with pytest.raises(RuntimeError):
            planner.plan(event)

        planner._db.raise_box_exception.assert_called_once()
        call = planner._db.raise_box_exception.call_args
        kwargs = call.kwargs
        assert kwargs["exception_type"] == "deprecated_vo_event"
        assert kwargs["severity"] == "high"
        assert kwargs["box_id"] == "AP-vo-1"
        assert kwargs["box_type"] == "ap_item"
        assert kwargs["organization_id"] == "orgVO"
        # Metadata carries the event identity so the box exception
        # is debuggable post-hoc.
        metadata = kwargs.get("metadata") or {}
        assert metadata["event_type"] == "iban_change_submitted"
        assert metadata.get("deprecation_phase") == "vendor_onboarding_dormant"

    def test_no_box_attached_deprecated_event_still_raises(self, planner):
        """Even without a Box anchor in the payload, the planner
        must raise. Recording the box_exception is best-effort —
        the raise is the load-bearing guarantee."""
        event = AgentEvent(
            type=AgentEventType.ONBOARDING_INITIATED,
            source="test",
            payload={"vendor_email": "vendor@example.com"},
            organization_id="orgVO",
        )
        with pytest.raises(RuntimeError):
            planner.plan(event)
        # No box → raise_box_exception NOT called.
        planner._db.raise_box_exception.assert_not_called()

    def test_handler_dispatcher_not_invoked_for_deprecated_events(self, planner):
        """The deprecated ``_plan_*`` methods must NOT run when the
        gate fires. Otherwise a deprecated path could still queue
        the fraud-control actions before the raise propagates."""
        # Sentinel: monkeypatch a deprecated _plan_* method to fail
        # if invoked. The gate runs before the dispatcher dict is
        # built, so this method is never called.
        original_plan_iban = planner._plan_iban_change

        def fail_if_called(event, box_state):
            raise AssertionError(
                "deprecated _plan_iban_change must not run; gate failed"
            )

        planner._plan_iban_change = fail_if_called
        try:
            event = AgentEvent(
                type=AgentEventType.IBAN_CHANGE_SUBMITTED,
                source="test",
                payload={"vendor_id": "v-1"},
                organization_id="orgVO",
            )
            with pytest.raises(RuntimeError):
                planner.plan(event)
        finally:
            planner._plan_iban_change = original_plan_iban


class TestLiveEventsUnaffected:
    """Drift fence: the gate must not widen to live event types.
    These three are the canonical live AP path; if any of them
    accidentally land in ``_DEPRECATED_VO_EVENTS`` the AP pipeline
    breaks loudly and these tests catch it."""

    def test_email_received_is_not_gated(self):
        # Don't actually run the email-received planner (which has
        # many side effects) — just assert membership.
        assert AgentEventType.EMAIL_RECEIVED not in _DEPRECATED_VO_EVENTS

    def test_approval_received_is_not_gated(self):
        assert AgentEventType.APPROVAL_RECEIVED not in _DEPRECATED_VO_EVENTS

    def test_timer_fired_is_not_gated(self):
        assert AgentEventType.TIMER_FIRED not in _DEPRECATED_VO_EVENTS

    def test_payment_confirmed_is_not_gated(self):
        assert AgentEventType.PAYMENT_CONFIRMED not in _DEPRECATED_VO_EVENTS

    def test_override_window_expired_is_not_gated(self):
        assert AgentEventType.OVERRIDE_WINDOW_EXPIRED not in _DEPRECATED_VO_EVENTS

    def test_label_changed_is_not_gated(self):
        assert AgentEventType.LABEL_CHANGED not in _DEPRECATED_VO_EVENTS
