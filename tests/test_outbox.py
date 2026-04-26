"""Tests for the transactional outbox + observer integration (Gap 4).

Covers:

* Event serialise/deserialise round-trip
* Handler registry: observer-prefix handler registers at import,
  unknown prefix returns None
* OutboxWriter.enqueue inserts a row, returns existing id on
  dedupe-key collision (idempotency)
* OutboxWorker.run_once: claims due rows, dispatches to handler,
  marks succeeded on success, retry on transient failure with
  exponential backoff, dead-letter at max attempts
* Replay re-enqueues with parent_event_id linkage and a stripped
  dedupe_key (so the replay isn't itself deduped)
* StateObserverRegistry.notify in outbox mode enqueues one row per
  observer; in inline mode runs them in-process (legacy behaviour)
* Outbox handler resolves target='observer:<ClassName>' to the
  registered observer instance and forwards on_transition

No Postgres / Docker dependency — uses an in-memory dict-backed
fake DB.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── In-memory fake DB ─────────────────────────────────────────────


class _FakeOutboxDB:
    """Minimal in-memory shape that mimics the parts of get_db() the
    outbox module touches: connect()/cursor()/execute()/fetchone()/fetchall()."""

    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def initialize(self):
        pass

    def connect(self):
        return self._FakeConn(self)

    class _FakeConn:
        def __init__(self, parent):
            self.parent = parent

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return self.parent._FakeCursor(self.parent)

        def commit(self):
            pass

    class _FakeCursor:
        def __init__(self, parent):
            self.parent = parent
            self._last: List[Dict[str, Any]] = []

        def execute(self, sql: str, params=None):
            sql_lower = " ".join(sql.split()).lower()
            params = list(params or [])

            if sql_lower.startswith("select * from outbox_events where dedupe_key"):
                dedupe_key = params[0]
                self._last = [r for r in self.parent.rows if r.get("dedupe_key") == dedupe_key]
            elif sql_lower.startswith("select * from outbox_events where id"):
                event_id = params[0]
                self._last = [r for r in self.parent.rows if r.get("id") == event_id]
            elif sql_lower.startswith("insert into outbox_events"):
                # Order matches the INSERT in OutboxWriter.enqueue
                (id_, org, evt, target, payload, dedupe, parent_id,
                 status, attempts, max_attempts,
                 next_at, last_at, succ_at,
                 error_log, created_at, updated_at, created_by) = params
                self.parent.rows.append({
                    "id": id_, "organization_id": org, "event_type": evt,
                    "target": target, "payload_json": payload,
                    "dedupe_key": dedupe, "parent_event_id": parent_id,
                    "status": status, "attempts": attempts, "max_attempts": max_attempts,
                    "next_attempt_at": next_at, "last_attempted_at": last_at,
                    "succeeded_at": succ_at, "error_log_json": error_log,
                    "created_at": created_at, "updated_at": updated_at,
                    "created_by": created_by,
                })
                self._last = []
            elif sql_lower.startswith("update outbox_events set status = 'processing'"):
                # Worker claim — flip pending/failed rows whose
                # next_attempt_at is null or past
                now = params[0]
                limit = params[3]
                claimed: List[Dict[str, Any]] = []
                for r in self.parent.rows:
                    if r.get("status") not in {"pending", "failed"}:
                        continue
                    nxt = r.get("next_attempt_at")
                    if nxt is None or nxt <= now:
                        r["status"] = "processing"
                        r["last_attempted_at"] = now
                        r["updated_at"] = now
                        claimed.append(dict(r))
                        if len(claimed) >= limit:
                            break
                self._last = claimed
            elif sql_lower.startswith("update outbox_events set status = 'succeeded'"):
                now = params[0]
                event_id = params[2]
                for r in self.parent.rows:
                    if r["id"] == event_id:
                        r["status"] = "succeeded"
                        r["succeeded_at"] = now
                        r["updated_at"] = now
                        r["attempts"] = (r.get("attempts") or 0) + 1
                        break
                self._last = []
            elif sql_lower.startswith("update outbox_events set status = %s, attempts ="):
                # _mark_failed
                next_status, attempts, next_at, now, error_log_json, event_id = params
                for r in self.parent.rows:
                    if r["id"] == event_id:
                        r["status"] = next_status
                        r["attempts"] = attempts
                        r["next_attempt_at"] = next_at
                        r["updated_at"] = now
                        r["error_log_json"] = error_log_json
                        break
                self._last = []
            elif sql_lower.startswith("update outbox_events set status = 'dead'"):
                now, error_log, attempts, event_id = params
                for r in self.parent.rows:
                    if r["id"] == event_id:
                        r["status"] = "dead"
                        r["updated_at"] = now
                        r["error_log_json"] = error_log
                        r["attempts"] = attempts
                        break
                self._last = []
            elif sql_lower.startswith("update outbox_events set status = 'pending'"):
                # retry_event
                next_at, now, event_id = params
                for r in self.parent.rows:
                    if r["id"] == event_id and r["status"] in {"failed", "dead"}:
                        r["status"] = "pending"
                        r["next_attempt_at"] = next_at
                        r["updated_at"] = now
                        self._last = [dict(r)]
                        return
                self._last = []
            elif sql_lower.startswith("select * from outbox_events where"):
                # ops list_events query
                self._last = [
                    dict(r) for r in self.parent.rows
                    if (not params or r.get("organization_id") == params[0])
                ][:25]
            else:
                self._last = []

        def fetchone(self):
            return self._last[0] if self._last else None

        def fetchall(self):
            return list(self._last)


# ─── Serialise/deserialise ─────────────────────────────────────────


def test_state_event_round_trip():
    from clearledgr.services.state_observers import (
        StateTransitionEvent, _deserialize_event, _serialize_event,
    )
    event = StateTransitionEvent(
        ap_item_id="AP-1", organization_id="org-1",
        old_state="received", new_state="validated",
        actor_id="alice", correlation_id="cor-1",
        source="workflow", gmail_id="msg-1",
        metadata={"note": "x"},
        source_type="netsuite", erp_native=True,
    )
    payload = _serialize_event(event)
    rebuilt = _deserialize_event(payload)
    assert rebuilt.ap_item_id == "AP-1"
    assert rebuilt.erp_native is True
    assert rebuilt.source_type == "netsuite"
    assert rebuilt.metadata == {"note": "x"}


# ─── Handler registry ──────────────────────────────────────────────


def test_observer_handler_registered_at_import():
    """Importing state_observers triggers _register_outbox_handler()."""
    import clearledgr.services.state_observers  # noqa: F401
    from clearledgr.services.outbox import list_handlers
    assert "observer" in list_handlers()


def test_unknown_prefix_resolves_to_none():
    from clearledgr.services.outbox import _resolve_handler
    assert _resolve_handler("nonexistent:foo") is None
    assert _resolve_handler("") is None


# ─── OutboxWriter ──────────────────────────────────────────────────


def test_outbox_writer_inserts_row():
    from clearledgr.services.outbox import OutboxWriter
    db = _FakeOutboxDB()
    with patch("clearledgr.services.outbox.get_db", return_value=db):
        writer = OutboxWriter("org-1")
        eid = writer.enqueue(
            event_type="state.posted_to_erp",
            target="observer:GmailLabelObserver",
            payload={"ap_item_id": "AP-1"},
            dedupe_key="state:AP-1:posted",
            actor="alice",
        )
    assert eid is not None
    assert eid.startswith("OE-")
    assert len(db.rows) == 1
    row = db.rows[0]
    assert row["status"] == "pending"
    assert row["dedupe_key"] == "state:AP-1:posted"


def test_outbox_writer_idempotent_on_dedupe_key():
    """Second enqueue with same dedupe_key returns existing id, no
    new row inserted."""
    from clearledgr.services.outbox import OutboxWriter
    db = _FakeOutboxDB()
    with patch("clearledgr.services.outbox.get_db", return_value=db):
        writer = OutboxWriter("org-1")
        first_id = writer.enqueue(
            event_type="state.posted_to_erp",
            target="observer:GmailLabelObserver",
            payload={"ap_item_id": "AP-1"},
            dedupe_key="state:AP-1:posted",
        )
        second_id = writer.enqueue(
            event_type="state.posted_to_erp",
            target="observer:GmailLabelObserver",
            payload={"ap_item_id": "AP-1"},
            dedupe_key="state:AP-1:posted",
        )
    assert first_id == second_id
    assert len(db.rows) == 1


# ─── OutboxWorker ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_dispatches_to_handler_and_marks_succeeded():
    from clearledgr.services.outbox import (
        OutboxWorker, OutboxWriter, register_handler, _HANDLERS,
    )
    db = _FakeOutboxDB()
    handled: List[str] = []

    async def fake_handler(ev):
        handled.append(ev.id)

    # Save + restore registry so we don't pollute the global state
    saved = dict(_HANDLERS)
    try:
        _HANDLERS.clear()
        register_handler("observer", fake_handler)
        with patch("clearledgr.services.outbox.get_db", return_value=db):
            writer = OutboxWriter("org-1")
            writer.enqueue(
                event_type="state.posted_to_erp",
                target="observer:Foo",
                payload={"x": 1},
            )
            worker = OutboxWorker(batch_size=10)
            stats = await worker.run_once()
    finally:
        _HANDLERS.clear()
        _HANDLERS.update(saved)

    assert stats.polled == 1
    assert stats.succeeded == 1
    assert stats.failed == 0
    assert len(handled) == 1
    assert db.rows[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_worker_retries_on_failure_then_dead_letters():
    """Simulate handler that always raises. After max_attempts the
    row should land in status=dead."""
    from clearledgr.services.outbox import (
        OutboxWorker, OutboxWriter, register_handler, _HANDLERS,
    )
    db = _FakeOutboxDB()
    saved = dict(_HANDLERS)

    async def always_fails(ev):
        raise RuntimeError("intentional failure for test")

    try:
        _HANDLERS.clear()
        register_handler("observer", always_fails)
        with patch("clearledgr.services.outbox.get_db", return_value=db):
            writer = OutboxWriter("org-1")
            writer.enqueue(
                event_type="state.x", target="observer:Foo",
                payload={}, max_attempts=3,
            )
            worker = OutboxWorker(batch_size=10)
            # First attempt: pending → processing → failed (attempts=1)
            stats1 = await worker.run_once()
            # Force the next_attempt_at into the past so the worker
            # picks up the failed row again
            db.rows[0]["next_attempt_at"] = "2000-01-01T00:00:00+00:00"
            stats2 = await worker.run_once()
            db.rows[0]["next_attempt_at"] = "2000-01-01T00:00:00+00:00"
            stats3 = await worker.run_once()
    finally:
        _HANDLERS.clear()
        _HANDLERS.update(saved)

    assert stats1.failed == 1
    assert stats2.failed == 1
    # Third attempt hits max and dead-letters
    assert stats3.dead == 1
    assert db.rows[0]["status"] == "dead"
    assert db.rows[0]["attempts"] == 3
    # Each failure logged
    import json
    log = json.loads(db.rows[0]["error_log_json"])
    assert len(log) == 3
    assert all("intentional failure" in entry["error"] for entry in log)


@pytest.mark.asyncio
async def test_worker_skips_when_no_handler_registered():
    """Row whose target prefix has no registered handler goes
    straight to dead — no retries, no thrashing."""
    from clearledgr.services.outbox import (
        OutboxWorker, OutboxWriter, _HANDLERS,
    )
    db = _FakeOutboxDB()
    saved = dict(_HANDLERS)
    try:
        _HANDLERS.clear()  # no handlers
        with patch("clearledgr.services.outbox.get_db", return_value=db):
            writer = OutboxWriter("org-1")
            writer.enqueue(
                event_type="state.x",
                target="not_a_known_prefix:Foo",
                payload={},
            )
            worker = OutboxWorker(batch_size=10)
            stats = await worker.run_once()
    finally:
        _HANDLERS.clear()
        _HANDLERS.update(saved)

    assert stats.skipped_no_handler == 1
    assert db.rows[0]["status"] == "dead"


# ─── Retry / skip / replay ─────────────────────────────────────────


def test_retry_event_resets_failed_to_pending():
    from clearledgr.services.outbox import OutboxWriter, retry_event
    db = _FakeOutboxDB()
    with patch("clearledgr.services.outbox.get_db", return_value=db):
        writer = OutboxWriter("org-1")
        eid = writer.enqueue(
            event_type="state.x", target="observer:Foo", payload={},
        )
        # Simulate failure
        db.rows[0]["status"] = "failed"
        result = retry_event(eid)
    assert result is not None
    assert result.status == "pending"
    assert db.rows[0]["status"] == "pending"


def test_retry_event_noop_for_succeeded_rows():
    from clearledgr.services.outbox import OutboxWriter, retry_event
    db = _FakeOutboxDB()
    with patch("clearledgr.services.outbox.get_db", return_value=db):
        writer = OutboxWriter("org-1")
        eid = writer.enqueue(
            event_type="state.x", target="observer:Foo", payload={},
        )
        db.rows[0]["status"] = "succeeded"
        result = retry_event(eid)
    # retry_event returns None when the row isn't in failed/dead
    assert result is None
    assert db.rows[0]["status"] == "succeeded"


def test_replay_strips_dedupe_key_and_adds_parent_link():
    from clearledgr.services.outbox import OutboxWriter, replay_events
    db = _FakeOutboxDB()
    with patch("clearledgr.services.outbox.get_db", return_value=db):
        writer = OutboxWriter("org-1")
        original_id = writer.enqueue(
            event_type="state.posted",
            target="observer:GmailLabelObserver",
            payload={"x": 1},
            dedupe_key="orig-key-1",
        )
        # Mark original as succeeded so it's not in flight when we replay
        db.rows[0]["status"] = "succeeded"
        count = replay_events(
            organization_id="org-1",
            event_type="state.posted",
            actor="ops-alice",
        )
    assert count == 1
    # New row added with parent_event_id linking to original
    new_rows = [r for r in db.rows if r["id"] != original_id]
    assert len(new_rows) == 1
    new_row = new_rows[0]
    assert new_row["parent_event_id"] == original_id
    # Replay strips dedupe_key so the new row IS a fresh intent
    assert new_row["dedupe_key"] is None
    assert new_row["status"] == "pending"
    assert new_row["created_by"].startswith("replay:")


# ─── StateObserverRegistry: outbox vs inline ───────────────────────


@pytest.mark.asyncio
async def test_observer_registry_outbox_mode_enqueues_per_observer():
    """In outbox mode, notify enqueues one row per registered
    observer instead of running them inline."""
    from clearledgr.services.state_observers import (
        StateObserver, StateObserverRegistry, StateTransitionEvent,
    )

    class FakeObserver(StateObserver):
        async def on_transition(self, event):
            raise AssertionError("should not be called in outbox mode")

    class AnotherObserver(StateObserver):
        async def on_transition(self, event):
            raise AssertionError("should not be called in outbox mode")

    db = _FakeOutboxDB()
    enqueued: List[Dict[str, Any]] = []

    def fake_enqueue(self, **kwargs):
        enqueued.append(kwargs)
        return f"OE-{len(enqueued)}"

    with patch("clearledgr.services.outbox.OutboxWriter.enqueue", fake_enqueue), \
         patch("clearledgr.services.outbox.get_db", return_value=db):
        registry = StateObserverRegistry()
        registry.register(FakeObserver())
        registry.register(AnotherObserver())
        event = StateTransitionEvent(
            ap_item_id="AP-1", organization_id="org-1",
            old_state="received", new_state="validated",
            actor_id="alice", correlation_id="cor-1",
        )
        await registry.notify(event)

    assert len(enqueued) == 2
    targets = sorted(e["target"] for e in enqueued)
    assert targets == ["observer:AnotherObserver", "observer:FakeObserver"]
    for e in enqueued:
        assert e["event_type"] == "state.validated"
        assert e["payload"]["ap_item_id"] == "AP-1"


@pytest.mark.asyncio
async def test_observer_registry_inline_mode_runs_observers_directly():
    """Inline mode preserves the legacy behaviour for tests + paths
    that need synchronous side-effects."""
    from clearledgr.services.state_observers import (
        StateObserver, StateObserverRegistry, StateTransitionEvent,
    )

    calls: List[str] = []

    class FakeObserver(StateObserver):
        async def on_transition(self, event):
            calls.append(f"fake:{event.ap_item_id}")

    class FailingObserver(StateObserver):
        async def on_transition(self, event):
            raise RuntimeError("boom")

    registry = StateObserverRegistry(inline=True)
    registry.register(FakeObserver())
    registry.register(FailingObserver())
    event = StateTransitionEvent(
        ap_item_id="AP-1", organization_id="org-1",
        old_state="x", new_state="y",
    )
    # Failing observer's exception is logged + swallowed; doesn't
    # propagate. Other observers still run.
    await registry.notify(event)
    assert calls == ["fake:AP-1"]


@pytest.mark.asyncio
async def test_outbox_handler_dispatches_to_registered_observer():
    """End-to-end: handler resolves target='observer:<Cls>' to a
    registered observer instance and forwards the rebuilt event."""
    from clearledgr.services.state_observers import (
        StateObserver, _OBSERVER_DISPATCH, _outbox_handler_observer,
        register_observer_for_outbox_dispatch,
    )

    received: List[Any] = []

    class TargetObserver(StateObserver):
        async def on_transition(self, event):
            received.append(event)

    saved = dict(_OBSERVER_DISPATCH)
    try:
        _OBSERVER_DISPATCH.clear()
        obs = TargetObserver()
        register_observer_for_outbox_dispatch(obs)

        from clearledgr.services.outbox import OutboxEvent
        ev = OutboxEvent(
            id="OE-1", organization_id="org-1",
            event_type="state.validated",
            target="observer:TargetObserver",
            payload={
                "ap_item_id": "AP-1", "organization_id": "org-1",
                "old_state": "received", "new_state": "validated",
                "actor_id": "alice", "correlation_id": "cor-1",
                "source": "workflow", "gmail_id": "msg-1",
                "metadata": {}, "source_type": "gmail", "erp_native": False,
            },
            dedupe_key=None, parent_event_id=None,
            status="processing", attempts=0, max_attempts=5,
            next_attempt_at=None, last_attempted_at=None, succeeded_at=None,
            error_log=[], created_at="2026-04-26T00:00:00Z",
            updated_at="2026-04-26T00:00:00Z", created_by="system",
        )
        await _outbox_handler_observer(ev)
    finally:
        _OBSERVER_DISPATCH.clear()
        _OBSERVER_DISPATCH.update(saved)

    assert len(received) == 1
    assert received[0].ap_item_id == "AP-1"
    assert received[0].new_state == "validated"


@pytest.mark.asyncio
async def test_outbox_handler_raises_on_unknown_observer_class():
    """An outbox row pointing at an observer not registered in the
    worker process should raise — the row will retry + eventually
    dead-letter."""
    from clearledgr.services.outbox import OutboxEvent
    from clearledgr.services.state_observers import (
        _OBSERVER_DISPATCH, _outbox_handler_observer,
    )
    saved = dict(_OBSERVER_DISPATCH)
    try:
        _OBSERVER_DISPATCH.clear()
        ev = OutboxEvent(
            id="OE-1", organization_id="org-1",
            event_type="state.validated",
            target="observer:NotRegistered",
            payload={"ap_item_id": "AP-1", "organization_id": "org-1",
                     "old_state": "received", "new_state": "validated"},
            dedupe_key=None, parent_event_id=None,
            status="processing", attempts=0, max_attempts=5,
            next_attempt_at=None, last_attempted_at=None, succeeded_at=None,
            error_log=[], created_at="", updated_at="", created_by="system",
        )
        with pytest.raises(LookupError):
            await _outbox_handler_observer(ev)
    finally:
        _OBSERVER_DISPATCH.clear()
        _OBSERVER_DISPATCH.update(saved)
