"""Regression coverage for the approval-dispatch outbox pattern in
``InvoiceWorkflowService._send_for_approval``.

Before this refactor, the function wrapped Slack delivery + the
load-bearing post-delivery DB writes (save_slack_thread + state
transition + metadata) under a single 163-line try/except. Any failure
returned ``status=error`` and the caller retried — fail-safe-by-accident
on the assumption that re-running everything was always correct. With
no native idempotency on Slack ``chat.postMessage`` that assumption
risked duplicate messages.

The new shape:

* Pre-write a ``pending`` row to ``ap_items.metadata.approval_dispatch``
  before the Slack call.
* Acquire a per-box advisory lock so two workers don't both call Slack
  for the same AP item.
* Slack delivery in its own narrow try → on failure mark dispatch
  ``failed`` and return ``status=error``.
* Critical post-delivery DB writes in their own narrow try → on
  failure log CRITICAL with the slack_ts and mark dispatch ``orphan``;
  return ``status=error_orphan_dispatch``.
* Best-effort post-dispatch work (snapshot, metadata, teams, audit,
  wait condition) in individual narrow tries → log warning, never
  unwind the dispatch.
* Idempotent re-entry: a second call after ``status=dispatched``
  returns the cached thread_ts without touching Slack.

These tests pin every one of those branches.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from clearledgr.services.invoice_models import InvoiceData
from clearledgr.services.invoice_workflow import InvoiceWorkflowService


# ────────────────────────────────────────────────────────────────────
# Shared fakes
# ────────────────────────────────────────────────────────────────────


class _FakeDB:
    """Hand-rolled stand-in for ``ClearledgrDB`` that captures every
    call. Each test seeds the relevant slice of state and asserts on
    the captured calls. Avoids a Postgres roundtrip — the lock
    helpers fall through to ``no_infra`` when there's no ``_pg_pool``,
    which is exactly the test-fixture path."""

    def __init__(self, *, ap_item: Optional[Dict[str, Any]] = None) -> None:
        self.ap_item = ap_item
        self.organization_id = "org_outbox_test"
        # No _pg_pool attribute → lock helpers return no_infra.
        # Capture buckets:
        self.audit_events: List[Dict[str, Any]] = []
        self.update_calls: List[Dict[str, Any]] = []
        self.thread_writes: List[Dict[str, Any]] = []
        self.invoice_status_updates: List[Dict[str, Any]] = []
        self.timeline_entries: List[Dict[str, Any]] = []
        self.approval_chains: List[Any] = []

    # Reads
    def get_organization(self, _org_id: str) -> Optional[Dict[str, Any]]:
        return {"id": "org_outbox_test", "name": "Test", "settings_json": "{}"}

    def get_ap_item(self, ap_item_id: str) -> Optional[Dict[str, Any]]:
        return dict(self.ap_item) if self.ap_item else None

    def get_invoice_status(self, gmail_id: str) -> Optional[Dict[str, Any]]:
        return {"state": "validated", "id": "ap-1"} if self.ap_item else None

    def get_slack_thread(self, gmail_id: str) -> Optional[Dict[str, Any]]:
        return None

    def get_ap_item_by_thread(self, org_id: str, gmail_id: str) -> Optional[Dict[str, Any]]:
        return self.ap_item

    def get_ap_item_by_vendor_invoice(self, org_id, vendor, inv_no):
        return self.ap_item

    # Writes
    def update_ap_item(self, ap_item_id: str, **kwargs: Any) -> bool:
        self.update_calls.append({"ap_item_id": ap_item_id, **kwargs})
        # When metadata is being merged, fold it into the seeded row so
        # the next get_ap_item read sees the latest outbox state.
        if "metadata" in kwargs and self.ap_item is not None:
            import json
            new_meta = kwargs["metadata"]
            if isinstance(new_meta, str):
                try:
                    new_meta = json.loads(new_meta)
                except Exception:
                    new_meta = {}
            existing_meta = self.ap_item.get("metadata") or {}
            if isinstance(existing_meta, str):
                try:
                    existing_meta = json.loads(existing_meta)
                except Exception:
                    existing_meta = {}
            merged = {**existing_meta, **(new_meta or {})}
            self.ap_item["metadata"] = merged
        return True

    def update_ap_item_metadata_merge(self, ap_item_id: str, patch: Dict[str, Any]) -> bool:
        if self.ap_item is not None:
            existing_meta = self.ap_item.get("metadata") or {}
            if isinstance(existing_meta, str):
                import json
                try:
                    existing_meta = json.loads(existing_meta)
                except Exception:
                    existing_meta = {}
            self.ap_item["metadata"] = {**existing_meta, **patch}
        return True

    def update_invoice_status(self, *, gmail_id: str, **kwargs: Any) -> bool:
        self.invoice_status_updates.append({"gmail_id": gmail_id, **kwargs})
        return True

    def save_slack_thread(self, **kwargs: Any) -> str:
        self.thread_writes.append(kwargs)
        return "thread-saved-id"

    def append_audit_event(self, payload: Dict[str, Any]) -> None:
        self.audit_events.append(payload)

    def append_ap_item_timeline_entry(self, ap_item_id: str, entry: Dict[str, Any]) -> None:
        self.timeline_entries.append({"ap_item_id": ap_item_id, **entry})

    def db_create_approval_chain(self, chain: Any) -> None:
        self.approval_chains.append(chain)

    def list_ap_audit_events(self, ap_item_id, limit=None, order=None):
        return self.audit_events

    def get_correlation_id_for_ap_item(self, ap_item_id):
        return "test-correlation-id"


def _make_workflow(db: _FakeDB) -> InvoiceWorkflowService:
    """Build a workflow service without going through the full __init__
    (which would touch the real DB pool + register observers). All we
    need is a service with self.db pointing at our fake."""
    svc = InvoiceWorkflowService.__new__(InvoiceWorkflowService)
    svc.db = db  # type: ignore[attr-defined]
    svc.organization_id = "org_outbox_test"
    svc._observer_registry = None  # type: ignore[attr-defined]
    svc._slack_channel = "#approvals"  # type: ignore[attr-defined]
    svc._teams_client = None  # type: ignore[attr-defined]
    svc._slack_client = None  # type: ignore[attr-defined]
    svc._cached_org_settings = None  # type: ignore[attr-defined]
    svc._cached_org_settings_at = None  # type: ignore[attr-defined]
    # Pretend settings are already loaded so _load_settings short-circuits.
    svc._settings_loaded = True  # type: ignore[attr-defined]
    svc._settings = {}  # type: ignore[attr-defined]
    svc._auto_approve_threshold = 0.95  # type: ignore[attr-defined]
    return svc


def _make_invoice() -> InvoiceData:
    return InvoiceData(
        gmail_id="gmail-outbox-1",
        subject="Bill",
        sender="vendor@example.com",
        vendor_name="Vendor Co",
        amount=1234.0,
        currency="USD",
        invoice_number="INV-001",
        confidence=0.99,
        organization_id="org_outbox_test",
        user_id="alice@co",
    )


def _seed_ap_item_with_dispatch(status: str, **extra: Any) -> Dict[str, Any]:
    """Build an AP item row whose metadata.approval_dispatch carries
    the given status — used to test idempotent re-entry."""
    return {
        "id": "ap-1",
        "vendor_name": "Vendor Co",
        "invoice_number": "INV-001",
        "amount": 1234.0,
        "thread_id": "gmail-outbox-1",
        "metadata": {
            "approval_dispatch": {
                "dispatch_id": "disp-existing",
                "status": status,
                "channel": "C-CACHED",
                "thread_ts": "1700000000.000100",
                "started_at": "2026-05-07T10:00:00+00:00",
                "completed_at": "2026-05-07T10:00:01+00:00",
                "error": None,
                **extra,
            },
        },
    }


# ────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idempotent_reentry_returns_cached_thread_without_calling_slack():
    """A call where the outbox status is already ``dispatched`` must
    short-circuit with the cached channel + thread_ts; Slack is never
    called, no DB writes are attempted past the read."""
    db = _FakeDB(ap_item=_seed_ap_item_with_dispatch("dispatched"))
    svc = _make_workflow(db)

    deliver_calls: List[Dict[str, Any]] = []

    async def _record_deliver(**kwargs):
        deliver_calls.append(kwargs)
        return {"channel": "should-never-fire", "ts": "0"}

    with patch(
        "clearledgr.services.slack_notifications.deliver_approval_with_routing",
        side_effect=_record_deliver,
    ):
        result = await svc._send_for_approval(_make_invoice())

    assert result["status"] == "pending_approval"
    assert result["existing"] is True
    assert result["slack_channel"] == "C-CACHED"
    assert result["slack_ts"] == "1700000000.000100"
    assert result["dispatch_id"] == "disp-existing"
    assert deliver_calls == [], (
        "Idempotent re-entry must not call Slack; got %r" % deliver_calls
    )
    assert db.thread_writes == [], (
        "Idempotent re-entry must not re-write the slack_thread row"
    )


@pytest.mark.asyncio
async def test_slack_delivery_failure_marks_dispatch_failed_and_returns_error():
    """When ``deliver_approval_with_routing`` raises, the outbox row
    is flipped to ``failed`` (not left at ``pending``) so a future
    re-run knows the previous attempt did not deliver. The function
    returns ``status=error`` with ``step=slack_delivery``."""
    db = _FakeDB(ap_item={"id": "ap-1", "vendor_name": "Vendor Co",
                           "invoice_number": "INV-001", "amount": 1234.0,
                           "thread_id": "gmail-outbox-1", "metadata": {}})
    svc = _make_workflow(db)

    async def _explode(**kwargs):
        raise RuntimeError("slack workspace unreachable")

    with patch(
        "clearledgr.services.slack_notifications.deliver_approval_with_routing",
        side_effect=_explode,
    ):
        result = await svc._send_for_approval(_make_invoice())

    assert result["status"] == "error"
    assert result["step"] == "slack_delivery"
    assert "slack workspace unreachable" in result["error"]

    # The outbox should have been written twice: pending, then failed.
    dispatch_writes = [
        u["metadata"]["approval_dispatch"]
        for u in db.update_calls
        if isinstance(u.get("metadata"), dict) and "approval_dispatch" in u["metadata"]
    ]
    statuses = [d["status"] for d in dispatch_writes]
    assert "pending" in statuses, f"expected a pending write, got {statuses}"
    assert "failed" in statuses, f"expected a failed write, got {statuses}"
    failed = [d for d in dispatch_writes if d["status"] == "failed"][-1]
    assert "slack workspace unreachable" in (failed.get("error") or "")
    # Critically: thread row was NEVER written — Slack didn't deliver.
    assert db.thread_writes == []


@pytest.mark.asyncio
async def test_post_delivery_db_failure_marks_dispatch_orphan_with_slack_breadcrumbs():
    """When Slack succeeds but ``save_slack_thread`` fails, the outbox
    is flipped to ``orphan`` carrying the slack_ts so an operator can
    reconcile. Return is ``status=error_orphan_dispatch``."""
    db = _FakeDB(ap_item={"id": "ap-1", "vendor_name": "Vendor Co",
                           "invoice_number": "INV-001", "amount": 1234.0,
                           "thread_id": "gmail-outbox-1", "metadata": {}})

    # Make save_slack_thread blow up.
    def _exploding_save_thread(**kwargs):
        raise RuntimeError("psycopg connection refused")

    db.save_slack_thread = _exploding_save_thread  # type: ignore[assignment]
    svc = _make_workflow(db)

    async def _ok_deliver(**kwargs):
        return {"channel": "C-LIVE", "ts": "1700000000.000999", "routing_rule": "org-test", "dm_sent": False}

    with patch(
        "clearledgr.services.slack_notifications.deliver_approval_with_routing",
        side_effect=_ok_deliver,
    ):
        result = await svc._send_for_approval(_make_invoice())

    assert result["status"] == "error_orphan_dispatch"
    assert result["slack_channel"] == "C-LIVE"
    assert result["slack_ts"] == "1700000000.000999"
    assert result["step"] == "post_delivery_state_transition"
    assert "psycopg connection refused" in result["error"]

    # Outbox should record orphan with the live slack info.
    dispatch_writes = [
        u["metadata"]["approval_dispatch"]
        for u in db.update_calls
        if isinstance(u.get("metadata"), dict) and "approval_dispatch" in u["metadata"]
    ]
    statuses = [d["status"] for d in dispatch_writes]
    assert "orphan" in statuses, (
        f"expected orphan in {statuses} (operator-reconciliation breadcrumb)"
    )
    orphan = [d for d in dispatch_writes if d["status"] == "orphan"][-1]
    assert orphan["channel"] == "C-LIVE"
    assert orphan["thread_ts"] == "1700000000.000999"
    assert "post_delivery_state_transition_failed" in (orphan.get("error") or "")


@pytest.mark.asyncio
async def test_happy_path_writes_pending_then_dispatched_outbox():
    """On a clean dispatch the outbox transitions pending → dispatched.
    The dispatched row carries the actual channel + thread_ts from
    Slack's response, not the configured default channel."""
    db = _FakeDB(ap_item={"id": "ap-1", "vendor_name": "Vendor Co",
                           "invoice_number": "INV-001", "amount": 1234.0,
                           "thread_id": "gmail-outbox-1", "metadata": {}})
    svc = _make_workflow(db)

    async def _ok_deliver(**kwargs):
        return {"channel": "C-CHOSEN", "ts": "1700000000.000444", "routing_rule": "org-test", "dm_sent": False}

    with patch(
        "clearledgr.services.slack_notifications.deliver_approval_with_routing",
        side_effect=_ok_deliver,
    ):
        result = await svc._send_for_approval(_make_invoice())

    assert result["status"] == "pending_approval"
    assert result["slack_channel"] == "C-CHOSEN"
    assert result["slack_ts"] == "1700000000.000444"
    assert result["dispatch_id"], "happy path must include the dispatch_id breadcrumb"

    dispatch_writes = [
        u["metadata"]["approval_dispatch"]
        for u in db.update_calls
        if isinstance(u.get("metadata"), dict) and "approval_dispatch" in u["metadata"]
    ]
    statuses_in_order = [d["status"] for d in dispatch_writes]
    # First we write pending, then dispatched. Best-effort metadata writes
    # (approval_requested_at, approval_channel, etc.) come after but don't
    # touch approval_dispatch.
    assert statuses_in_order[0] == "pending"
    assert "dispatched" in statuses_in_order, (
        f"expected dispatched in {statuses_in_order}"
    )
    final_dispatched = [d for d in dispatch_writes if d["status"] == "dispatched"][-1]
    assert final_dispatched["channel"] == "C-CHOSEN"
    assert final_dispatched["thread_ts"] == "1700000000.000444"
    # The slack_thread row was written, with the same channel + ts.
    assert db.thread_writes, "slack_thread row must be persisted on success"
    assert db.thread_writes[-1]["channel_id"] == "C-CHOSEN"
    assert db.thread_writes[-1]["thread_ts"] == "1700000000.000444"


# ────────────────────────────────────────────────────────────────────
# Reaper tests
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reaper_recovers_orphan_by_replaying_post_delivery_writes(monkeypatch):
    """An orphan row carries the slack channel + ts; the reaper re-runs
    save_slack_thread + state transition, then flips the outbox to
    ``dispatched``. On the next sweep the row is no longer in the
    orphan list (status filter), so recovery is a one-shot.
    """
    from clearledgr.services.agent_background import reap_orphan_approval_dispatches

    orphan_row = {
        "id": "ap-orphan-1",
        "organization_id": "org_orphan",
        "thread_id": "gmail-orphan-1",
        "metadata": {
            "approval_dispatch": {
                "dispatch_id": "disp-orphan",
                "status": "orphan",
                "channel": "C-LIVE",
                "thread_ts": "1700000000.999000",
                "started_at": "2026-05-07T09:00:00+00:00",
                "completed_at": "2026-05-07T09:00:01+00:00",
                "error": "post_delivery_state_transition_failed: psycopg.OperationalError",
            },
        },
    }

    captured: Dict[str, Any] = {
        "save_slack_thread_calls": [],
        "update_invoice_status_calls": [],
        "metadata_merge_calls": [],
    }

    class _ReaperFakeDB:
        # No _pg_pool → acquire_box_lock returns no_infra; reaper proceeds unguarded.
        def list_orphan_approval_dispatches(self, *, min_age_seconds=60, limit=200):
            return [orphan_row]

        def save_slack_thread(self, **kwargs):
            captured["save_slack_thread_calls"].append(kwargs)
            return kwargs.get("thread_ts", "")

        def update_invoice_status(self, *, gmail_id, **kwargs):
            captured["update_invoice_status_calls"].append({"gmail_id": gmail_id, **kwargs})
            return True

        def update_ap_item_metadata_merge(self, ap_item_id, patch):
            captured["metadata_merge_calls"].append({"ap_item_id": ap_item_id, **patch})
            return True

        def update_ap_item(self, ap_item_id, **kwargs):
            captured["metadata_merge_calls"].append({"ap_item_id": ap_item_id, **kwargs})
            return True

    fake_db = _ReaperFakeDB()
    monkeypatch.setattr(
        "clearledgr.core.database.get_db", lambda: fake_db,
    )

    recovered = await reap_orphan_approval_dispatches()

    assert recovered == 1
    # save_slack_thread was re-run with the cached channel + ts (idempotent).
    assert captured["save_slack_thread_calls"], "save_slack_thread must be re-run"
    last_thread = captured["save_slack_thread_calls"][-1]
    assert last_thread["channel_id"] == "C-LIVE"
    assert last_thread["thread_ts"] == "1700000000.999000"
    # State transition to needs_approval was attempted.
    state_targets = [c.get("status") for c in captured["update_invoice_status_calls"]]
    assert "needs_approval" in state_targets
    # Outbox flipped to dispatched with the recovered_by breadcrumb.
    merges = captured["metadata_merge_calls"]
    assert merges, "outbox must be flipped via metadata write"
    last_merge = merges[-1]
    flipped_dispatch = last_merge.get("approval_dispatch") or {}
    assert flipped_dispatch.get("status") == "dispatched"
    assert flipped_dispatch.get("recovered_by") == "orphan_dispatch_reaper"
    # The cached identifiers are preserved on the dispatched row so an
    # auditor can trace the recovery back to the original delivery.
    assert flipped_dispatch.get("channel") == "C-LIVE"
    assert flipped_dispatch.get("thread_ts") == "1700000000.999000"
    assert flipped_dispatch.get("dispatch_id") == "disp-orphan"


@pytest.mark.asyncio
async def test_reaper_skips_orphan_rows_missing_channel_or_ts(monkeypatch):
    """A malformed orphan row (missing channel or thread_ts) cannot
    be auto-recovered — there's no Slack message to reconcile against.
    The reaper logs a warning and skips it; subsequent sweeps will
    see the same row and emit the same warning, surfacing the issue
    in observability instead of silently dropping it."""
    from clearledgr.services.agent_background import reap_orphan_approval_dispatches

    bad_orphan = {
        "id": "ap-orphan-2",
        "organization_id": "org_orphan",
        "thread_id": "gmail-orphan-2",
        "metadata": {
            "approval_dispatch": {
                "dispatch_id": "disp-no-ts",
                "status": "orphan",
                "channel": None,
                "thread_ts": None,
                "completed_at": "2026-05-07T09:00:01+00:00",
                "error": "weird state",
            },
        },
    }

    captured: Dict[str, Any] = {"writes": 0}

    class _ReaperFakeDB:
        def list_orphan_approval_dispatches(self, *, min_age_seconds=60, limit=200):
            return [bad_orphan]

        def save_slack_thread(self, **kwargs):
            captured["writes"] += 1
            return ""

        def update_invoice_status(self, *, gmail_id, **kwargs):
            captured["writes"] += 1
            return True

        def update_ap_item_metadata_merge(self, ap_item_id, patch):
            captured["writes"] += 1
            return True

        def update_ap_item(self, ap_item_id, **kwargs):
            captured["writes"] += 1
            return True

    fake_db = _ReaperFakeDB()
    monkeypatch.setattr(
        "clearledgr.core.database.get_db", lambda: fake_db,
    )

    recovered = await reap_orphan_approval_dispatches()

    assert recovered == 0, "recovery without slack_ts must not be claimed as success"
    assert captured["writes"] == 0, (
        "reaper must NOT write anything when the orphan row lacks channel/ts"
    )
