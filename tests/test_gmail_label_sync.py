"""Bidirectional Gmail label sync — Phase 2.

User applies a Clearledgr/* label in Gmail → agent reacts.

These tests lock in the contract:
  - Only labels in LABEL_TO_INTENT drive workflow; status-only labels
    (Matched, Paid) are ignored.
  - Only threads with an existing AP box trigger events (no box = noop).
  - Unknown labels → no event. Unknown intents → empty plan.
  - Idempotency key = label:{label_name}:{message_id} so a replayed
    notification does not double-apply the intent.
  - LABEL_CHANGED is registered in AgentEventType and the planning
    engine dispatches it to a real plan.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from clearledgr.api import gmail_webhooks
from clearledgr.core.events import AgentEvent, AgentEventType
from clearledgr.core.planning_engine import DeterministicPlanningEngine
from clearledgr.services.gmail_labels import (
    LABEL_TO_INTENT,
    intent_for_label,
)


# ---------------------------------------------------------------------------
# gmail_labels.intent_for_label
# ---------------------------------------------------------------------------


def test_intent_for_label_maps_only_action_verbs():
    # Action verbs
    assert intent_for_label("Clearledgr/Invoice/Approved") == "approve_invoice"
    assert intent_for_label("Clearledgr/Invoice/Exception") == "needs_info"
    assert intent_for_label("Clearledgr/Review Required") == "needs_info"
    assert intent_for_label("Clearledgr/Not Finance") == "reject_invoice"

    # Status-only labels the agent applies itself → MUST NOT trigger action
    assert intent_for_label("Clearledgr/Invoice/Received") is None
    assert intent_for_label("Clearledgr/Invoice/Matched") is None
    assert intent_for_label("Clearledgr/Invoice/Paid") is None

    # Off-brand labels
    assert intent_for_label("Work/Important") is None
    assert intent_for_label("") is None
    assert intent_for_label(None) is None


def test_intent_for_label_resolves_via_label_key():
    # Caller passes internal key, we still resolve
    assert intent_for_label("invoice_approved") == "approve_invoice"


def test_label_to_intent_set_is_narrow():
    # Guard against future expansion without product sign-off. If the
    # set grows, update this test deliberately.
    assert set(LABEL_TO_INTENT.keys()) == {
        "Clearledgr/Invoice/Approved",
        "Clearledgr/Invoice/Exception",
        "Clearledgr/Review Required",
        "Clearledgr/Not Finance",
    }


# ---------------------------------------------------------------------------
# DeterministicPlanningEngine — LABEL_CHANGED handler
# ---------------------------------------------------------------------------


def _make_label_event(intent: str, *, box_id: str = "AP-1",
                      label_name: str = "Clearledgr/Invoice/Approved"):
    return AgentEvent(
        type=AgentEventType.LABEL_CHANGED,
        source="gmail_label_sync",
        organization_id="default",
        payload={
            "box_id": box_id,
            "thread_id": "thread-1",
            "message_id": "msg-1",
            "label_name": label_name,
            "intent": intent,
            "actor_email": "ops@example.com",
        },
    )


def test_planning_engine_registers_label_changed_handler():
    engine = DeterministicPlanningEngine()
    # The dispatcher must include LABEL_CHANGED.
    plan = engine.plan(_make_label_event("approve_invoice"), {})
    assert plan.event_type == "label_changed"
    # Not empty — a real plan was generated.
    assert plan.step_count > 0


def test_label_changed_approve_plan_covers_full_approval_flow():
    engine = DeterministicPlanningEngine()
    plan = engine.plan(_make_label_event("approve_invoice"), {})
    names = [a.name for a in plan.actions]
    # The approval plan mirrors the Slack/sidebar approval flow:
    # validate → post_bill → move to approved → schedule payment →
    # record timeline → open override window.
    assert "pre_post_validate" in names
    assert "post_bill" in names
    assert "move_box_stage" in names
    assert "schedule_payment" in names
    assert "send_override_window" in names


def test_label_changed_reject_plan_is_exception_only():
    engine = DeterministicPlanningEngine()
    plan = engine.plan(
        _make_label_event("reject_invoice",
                          label_name="Clearledgr/Not Finance"),
        {},
    )
    names = [a.name for a in plan.actions]
    # Reject never touches ERP — just moves to exception and tags.
    assert "post_bill" not in names
    assert "schedule_payment" not in names
    assert "move_box_stage" in names
    # The Exception label gets applied
    assert any(a.name == "apply_label"
               and a.params.get("label") == "Clearledgr/Invoice/Exception"
               for a in plan.actions)


def test_label_changed_needs_info_plan_moves_to_review():
    engine = DeterministicPlanningEngine()
    plan = engine.plan(
        _make_label_event("needs_info",
                          label_name="Clearledgr/Review Required"),
        {},
    )
    names = [a.name for a in plan.actions]
    assert "post_bill" not in names
    assert "move_box_stage" in names
    assert "post_timeline_entry" in names


def test_label_changed_unknown_intent_returns_empty_plan():
    engine = DeterministicPlanningEngine()
    plan = engine.plan(_make_label_event("bogus_intent"), {})
    assert plan.is_empty


def test_label_changed_missing_box_id_returns_empty_plan():
    engine = DeterministicPlanningEngine()
    event = AgentEvent(
        type=AgentEventType.LABEL_CHANGED,
        source="gmail_label_sync",
        organization_id="default",
        payload={
            "label_name": "Clearledgr/Invoice/Approved",
            "intent": "approve_invoice",
        },
    )
    plan = engine.plan(event, {})
    assert plan.is_empty


# ---------------------------------------------------------------------------
# Webhook: _process_label_changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_label_changes_enqueues_for_action_label_on_known_thread():
    mock_client = MagicMock()
    mock_client.list_labels = AsyncMock(return_value=[
        {"id": "Label_APPROVED", "name": "Clearledgr/Invoice/Approved"},
        {"id": "Label_OTHER", "name": "Some Other Label"},
    ])

    mock_db = MagicMock()
    mock_db.get_ap_item_by_thread.return_value = {"id": "AP-42"}

    captured = []
    mock_queue = MagicMock()
    mock_queue.enqueue = MagicMock(side_effect=lambda ev: captured.append(ev) or "new")

    token = SimpleNamespace(email="ops@example.com", user_id="u1")

    await gmail_webhooks._process_label_changes(
        client=mock_client,
        token=token,
        organization_id="default",
        db=mock_db,
        queue=mock_queue,
        records=[
            {"message_id": "m1", "thread_id": "t1", "label_ids": ["Label_APPROVED"]},
        ],
    )

    assert len(captured) == 1
    ev = captured[0]
    assert ev.type == AgentEventType.LABEL_CHANGED
    assert ev.payload["intent"] == "approve_invoice"
    assert ev.payload["box_id"] == "AP-42"
    assert ev.payload["label_name"] == "Clearledgr/Invoice/Approved"
    assert ev.idempotency_key == "label:Clearledgr/Invoice/Approved:m1"


@pytest.mark.asyncio
async def test_process_label_changes_ignores_status_only_labels():
    mock_client = MagicMock()
    mock_client.list_labels = AsyncMock(return_value=[
        {"id": "Label_MATCHED", "name": "Clearledgr/Invoice/Matched"},
    ])
    mock_db = MagicMock()
    mock_db.get_ap_item_by_thread.return_value = {"id": "AP-42"}
    captured = []
    mock_queue = MagicMock()
    mock_queue.enqueue = MagicMock(side_effect=lambda ev: captured.append(ev) or "new")

    await gmail_webhooks._process_label_changes(
        client=mock_client,
        token=SimpleNamespace(email="ops@example.com", user_id="u1"),
        organization_id="default",
        db=mock_db,
        queue=mock_queue,
        records=[{"message_id": "m1", "thread_id": "t1", "label_ids": ["Label_MATCHED"]}],
    )
    assert captured == []


@pytest.mark.asyncio
async def test_process_label_changes_ignores_threads_without_ap_box():
    mock_client = MagicMock()
    mock_client.list_labels = AsyncMock(return_value=[
        {"id": "Label_APPROVED", "name": "Clearledgr/Invoice/Approved"},
    ])
    mock_db = MagicMock()
    mock_db.get_ap_item_by_thread.return_value = None  # no box
    captured = []
    mock_queue = MagicMock()
    mock_queue.enqueue = MagicMock(side_effect=lambda ev: captured.append(ev) or "new")

    await gmail_webhooks._process_label_changes(
        client=mock_client,
        token=SimpleNamespace(email="ops@example.com", user_id="u1"),
        organization_id="default",
        db=mock_db,
        queue=mock_queue,
        records=[{"message_id": "m1", "thread_id": "t1", "label_ids": ["Label_APPROVED"]}],
    )
    assert captured == []


@pytest.mark.asyncio
async def test_process_label_changes_dedupes_via_idempotency_key():
    """Two history records for the same (message, label) must produce
    the same idempotency key so the queue can drop duplicates."""
    mock_client = MagicMock()
    mock_client.list_labels = AsyncMock(return_value=[
        {"id": "Label_APPROVED", "name": "Clearledgr/Invoice/Approved"},
    ])
    mock_db = MagicMock()
    mock_db.get_ap_item_by_thread.return_value = {"id": "AP-42"}

    enqueued_keys = []
    def _enqueue(ev):
        enqueued_keys.append(ev.idempotency_key)
        return "new" if enqueued_keys.count(ev.idempotency_key) == 1 else "duplicate"
    mock_queue = MagicMock()
    mock_queue.enqueue = _enqueue

    records = [
        {"message_id": "m1", "thread_id": "t1", "label_ids": ["Label_APPROVED"]},
        {"message_id": "m1", "thread_id": "t1", "label_ids": ["Label_APPROVED"]},
    ]
    await gmail_webhooks._process_label_changes(
        client=mock_client,
        token=SimpleNamespace(email="ops@example.com", user_id="u1"),
        organization_id="default",
        db=mock_db,
        queue=mock_queue,
        records=records,
    )
    # Both records produce the same key so the queue's dedup handles it.
    assert len(enqueued_keys) == 2
    assert enqueued_keys[0] == enqueued_keys[1]
    assert enqueued_keys[0] == "label:Clearledgr/Invoice/Approved:m1"


@pytest.mark.asyncio
async def test_process_label_changes_picks_first_action_label_when_multiple():
    """If a record contains several Clearledgr labels + noise labels, we
    should fire on the first action label and ignore the rest."""
    mock_client = MagicMock()
    mock_client.list_labels = AsyncMock(return_value=[
        {"id": "Label_APPROVED", "name": "Clearledgr/Invoice/Approved"},
        {"id": "Label_MATCHED", "name": "Clearledgr/Invoice/Matched"},
        {"id": "Label_USER", "name": "User/Important"},
    ])
    mock_db = MagicMock()
    mock_db.get_ap_item_by_thread.return_value = {"id": "AP-42"}
    captured = []
    mock_queue = MagicMock()
    mock_queue.enqueue = MagicMock(side_effect=lambda ev: captured.append(ev) or "new")

    await gmail_webhooks._process_label_changes(
        client=mock_client,
        token=SimpleNamespace(email="ops@example.com", user_id="u1"),
        organization_id="default",
        db=mock_db,
        queue=mock_queue,
        records=[{
            "message_id": "m1",
            "thread_id": "t1",
            "label_ids": ["Label_MATCHED", "Label_USER", "Label_APPROVED"],
        }],
    )
    # Should fire exactly once on the first action label found
    assert len(captured) == 1
    assert captured[0].payload["label_name"] == "Clearledgr/Invoice/Approved"


@pytest.mark.asyncio
async def test_process_label_changes_noop_on_empty_records():
    mock_client = MagicMock()
    mock_client.list_labels = AsyncMock(return_value=[])
    await gmail_webhooks._process_label_changes(
        client=mock_client,
        token=SimpleNamespace(email="x", user_id="y"),
        organization_id="default",
        db=MagicMock(),
        queue=MagicMock(),
        records=[],
    )
    # list_labels should not even be called for empty records
    assert mock_client.list_labels.await_count == 0
