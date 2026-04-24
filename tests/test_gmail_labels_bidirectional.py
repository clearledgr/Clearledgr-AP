"""Integration test for Phase 2 Gmail labels bidirectional sync.

Trace: vendor/AP-clerk applies a Clearledgr action label in Gmail →
Gmail Pub/Sub delivers a ``labelsAdded`` history record → our
``_process_label_changes`` resolves the label ID to a display name,
looks up the AP box by thread, and enqueues an ``AgentEvent`` with
type ``LABEL_CHANGED`` carrying the matched intent.

Lock in the contract so a regression that silently drops the enqueue
call (the exact failure mode a previous audit falsely suspected)
would trip this test.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api.gmail_webhooks import _process_label_changes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.events import AgentEvent, AgentEventType  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _MockGmailClient:
    """Minimal Gmail client double — only implements list_labels."""

    def __init__(self, labels: Dict[str, str]) -> None:
        # {label_id: label_display_name}
        self._labels = labels
        self.list_labels_calls = 0

    async def list_labels(self) -> List[Dict[str, str]]:
        self.list_labels_calls += 1
        return [{"id": lid, "name": name} for lid, name in self._labels.items()]


class _MockQueue:
    """Captures enqueue calls and honours idempotency_key dedup so
    the replay-safety assertion has teeth."""

    def __init__(self) -> None:
        self.events: List[AgentEvent] = []
        self._seen_keys: set[str] = set()

    def enqueue(self, event: AgentEvent) -> str:
        key = event.idempotency_key or ""
        if key and key in self._seen_keys:
            return "duplicate"
        self.events.append(event)
        if key:
            self._seen_keys.add(key)
        return f"entry-{len(self.events)}"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


def _seed_ap_item(db, *, ap_item_id: str, thread_id: str, state: str = "needs_approval") -> dict:
    return db.create_ap_item({
        "id": ap_item_id,
        "invoice_key": f"inv-{ap_item_id}",
        "thread_id": thread_id,
        "message_id": f"msg-{ap_item_id}",
        "subject": "Invoice from Acme",
        "sender": "billing@acme.com",
        "vendor_name": "Acme Corp",
        "amount": 500.0,
        "currency": "USD",
        "invoice_number": f"INV-{ap_item_id}",
        "state": state,
        "organization_id": "default",
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLabelsBidirectionalSync:

    @pytest.mark.asyncio
    async def test_approved_label_enqueues_approve_invoice_intent(self, db):
        item = _seed_ap_item(db, ap_item_id="AP-BIDI-1", thread_id="thread-1")

        client = _MockGmailClient({
            "LABEL_APPROVED": "Clearledgr/Invoice/Approved",
            "LABEL_STATUS":   "Clearledgr/Matched",  # status-only; ignored
        })
        queue = _MockQueue()
        token = SimpleNamespace(email="ap-clerk@default.com")

        records = [{
            "message_id": "msg-gmail-42",
            "thread_id": "thread-1",
            "label_ids": ["LABEL_APPROVED"],
        }]

        await _process_label_changes(
            client=client,
            token=token,
            organization_id="default",
            db=db,
            queue=queue,
            records=records,
        )

        assert len(queue.events) == 1
        ev = queue.events[0]
        assert ev.type == AgentEventType.LABEL_CHANGED
        assert ev.organization_id == "default"
        assert ev.source == "gmail_label_sync"
        assert ev.idempotency_key == "label:Clearledgr/Invoice/Approved:msg-gmail-42"
        assert ev.payload["box_id"] == item["id"]
        assert ev.payload["thread_id"] == "thread-1"
        assert ev.payload["message_id"] == "msg-gmail-42"
        assert ev.payload["label_name"] == "Clearledgr/Invoice/Approved"
        assert ev.payload["intent"] == "approve_invoice"
        assert ev.payload["actor_email"] == "ap-clerk@default.com"

    @pytest.mark.asyncio
    async def test_exception_label_enqueues_needs_info_intent(self, db):
        _seed_ap_item(db, ap_item_id="AP-BIDI-2", thread_id="thread-2")
        client = _MockGmailClient({
            "LABEL_EXC": "Clearledgr/Invoice/Exception",
        })
        queue = _MockQueue()

        await _process_label_changes(
            client=client, token=SimpleNamespace(email="x@default.com"),
            organization_id="default", db=db, queue=queue,
            records=[{
                "message_id": "m2", "thread_id": "thread-2",
                "label_ids": ["LABEL_EXC"],
            }],
        )
        assert len(queue.events) == 1
        assert queue.events[0].payload["intent"] == "needs_info"

    @pytest.mark.asyncio
    async def test_not_finance_label_enqueues_reject_invoice_intent(self, db):
        _seed_ap_item(db, ap_item_id="AP-BIDI-3", thread_id="thread-3")
        client = _MockGmailClient({
            "LABEL_NF": "Clearledgr/Not Finance",
        })
        queue = _MockQueue()

        await _process_label_changes(
            client=client, token=SimpleNamespace(email="x@default.com"),
            organization_id="default", db=db, queue=queue,
            records=[{
                "message_id": "m3", "thread_id": "thread-3",
                "label_ids": ["LABEL_NF"],
            }],
        )
        assert len(queue.events) == 1
        assert queue.events[0].payload["intent"] == "reject_invoice"

    @pytest.mark.asyncio
    async def test_status_only_labels_are_ignored(self, db):
        _seed_ap_item(db, ap_item_id="AP-BIDI-4", thread_id="thread-4")
        client = _MockGmailClient({
            "LABEL_MATCHED": "Clearledgr/Matched",
            "LABEL_PAID":    "Clearledgr/Paid",
            "LABEL_RCVD":    "Clearledgr/Invoice/Received",
        })
        queue = _MockQueue()

        await _process_label_changes(
            client=client, token=SimpleNamespace(email="x@default.com"),
            organization_id="default", db=db, queue=queue,
            records=[{
                "message_id": "m4", "thread_id": "thread-4",
                "label_ids": ["LABEL_MATCHED", "LABEL_PAID", "LABEL_RCVD"],
            }],
        )
        assert queue.events == [], (
            "status-only labels must not trigger intents — only the "
            "four action verbs in LABEL_TO_INTENT should enqueue"
        )

    @pytest.mark.asyncio
    async def test_label_on_thread_without_ap_box_is_ignored(self, db):
        # No seeded AP item for thread-ghost — labels on threads we
        # don't track should no-op, not crash.
        client = _MockGmailClient({
            "LABEL_APPROVED": "Clearledgr/Invoice/Approved",
        })
        queue = _MockQueue()

        await _process_label_changes(
            client=client, token=SimpleNamespace(email="x@default.com"),
            organization_id="default", db=db, queue=queue,
            records=[{
                "message_id": "m5", "thread_id": "thread-ghost",
                "label_ids": ["LABEL_APPROVED"],
            }],
        )
        assert queue.events == []

    @pytest.mark.asyncio
    async def test_replayed_record_is_deduped_via_idempotency_key(self, db):
        _seed_ap_item(db, ap_item_id="AP-BIDI-6", thread_id="thread-6")
        client = _MockGmailClient({
            "LABEL_APPROVED": "Clearledgr/Invoice/Approved",
        })
        queue = _MockQueue()

        records = [{
            "message_id": "m6", "thread_id": "thread-6",
            "label_ids": ["LABEL_APPROVED"],
        }]

        # First delivery
        await _process_label_changes(
            client=client, token=SimpleNamespace(email="x@default.com"),
            organization_id="default", db=db, queue=queue,
            records=records,
        )
        # Replay (Gmail Pub/Sub redeliveries are common)
        await _process_label_changes(
            client=client, token=SimpleNamespace(email="x@default.com"),
            organization_id="default", db=db, queue=queue,
            records=records,
        )

        # The replay must be rejected as duplicate by the queue's
        # idempotency_key dedup. Exactly one event in the queue.
        assert len(queue.events) == 1

    @pytest.mark.asyncio
    async def test_list_labels_called_once_per_invocation(self, db):
        # Cache invariant: we must not hit list_labels() N times for
        # N records in one notification.
        _seed_ap_item(db, ap_item_id="AP-BIDI-7a", thread_id="thread-7a")
        _seed_ap_item(db, ap_item_id="AP-BIDI-7b", thread_id="thread-7b")
        client = _MockGmailClient({
            "LABEL_APPROVED": "Clearledgr/Invoice/Approved",
        })
        queue = _MockQueue()

        records = [
            {"message_id": "m7a", "thread_id": "thread-7a", "label_ids": ["LABEL_APPROVED"]},
            {"message_id": "m7b", "thread_id": "thread-7b", "label_ids": ["LABEL_APPROVED"]},
        ]
        await _process_label_changes(
            client=client, token=SimpleNamespace(email="x@default.com"),
            organization_id="default", db=db, queue=queue,
            records=records,
        )
        assert client.list_labels_calls == 1
        assert len(queue.events) == 2

    @pytest.mark.asyncio
    async def test_empty_records_is_noop(self, db):
        client = _MockGmailClient({})
        queue = _MockQueue()
        await _process_label_changes(
            client=client, token=SimpleNamespace(email="x@default.com"),
            organization_id="default", db=db, queue=queue, records=[],
        )
        assert client.list_labels_calls == 0
        assert queue.events == []
