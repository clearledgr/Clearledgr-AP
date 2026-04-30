"""Tests for read-side projections (Gap 6).

Covers:

* Projector registry: BoxSummaryProjector + VendorSummaryProjector
  register at import; the projection-prefix outbox handler registers
  too.
* BoxProjector protocol is honored — both built-in projectors satisfy
  the runtime-checkable interface.
* BoxSummaryProjector UPSERTs box_summary + INSERTs box_summary_history
  on a state-transition projection.
* VendorSummaryProjector recomputes vendor_summary rollup correctly
  (counts, exception_rate, last_activity_at, currency split).
* VendorSummaryProjector skips non-relevant states (e.g. ``received``).
* BoxProjectionObserver enqueues exactly one outbox row per
  registered projector that declares the box_type.
* Outbox handler resolves ``projection:<name>`` to the registered
  projector instance.
* Read helpers (``get_box_summary_row`` / ``get_box_history`` /
  ``get_vendor_summary_row`` / ``list_vendor_summaries``) hydrate
  rows correctly.
* ``rebuild_projections`` walks ap_items and fans out to every
  registered projector.

No Postgres / Docker — pure logic + mocks.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest


# ─── In-memory fake DB ─────────────────────────────────────────────


class _FakeProjectionDB:
    """Captures the SQL the projection module emits and lets tests
    inspect what landed in box_summary / box_summary_history /
    vendor_summary. Also shapes ap_items + audit_events lookups
    used by the projector."""

    def __init__(self):
        self.box_summary: List[Dict[str, Any]] = []
        self.box_summary_history: List[Dict[str, Any]] = []
        self.vendor_summary: List[Dict[str, Any]] = []
        self.ap_items: Dict[str, Dict[str, Any]] = {}
        self.audit_events: List[Dict[str, Any]] = []
        self.exceptions_by_box: Dict[str, List[Dict[str, Any]]] = {}
        self.outcomes_by_box: Dict[str, Dict[str, Any]] = {}
        self.list_ap_items_calls: List[tuple] = []

    # ── ClearledgrDB-compatible helpers used by box_summary.py ──
    def initialize(self):
        pass

    def get_ap_item(self, ap_item_id: str) -> Optional[Dict[str, Any]]:
        return self.ap_items.get(ap_item_id)

    def list_ap_audit_events(self, ap_item_id: str, limit: int = 50, order: str = "asc"):
        rows = [e for e in self.audit_events if e.get("box_id") == ap_item_id]
        rows.sort(key=lambda r: r.get("ts") or "", reverse=(order == "desc"))
        return rows[:limit]

    def list_box_exceptions(self, box_type: str, box_id: str):
        return self.exceptions_by_box.get(f"{box_type}:{box_id}", [])

    def get_box_outcome(self, box_type: str, box_id: str):
        return self.outcomes_by_box.get(f"{box_type}:{box_id}")

    def list_ap_items(self, organization_id: str, limit: int = 1000, **kwargs):
        self.list_ap_items_calls.append((organization_id, limit))
        return [
            v for v in self.ap_items.values()
            if v.get("organization_id") == organization_id
        ][:limit]

    # ── connect()/cursor() shape that hits the SQL paths in box_projection ──
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

            if sql_lower.startswith("insert into box_summary "):
                (box_type, box_id, org, state,
                 summary_json, timeline_preview_json, exceptions_json,
                 outcome_json, event_count, last_event_id,
                 last_state_at, updated_at) = params
                # ON CONFLICT — replace if PK collision
                key = (box_type, box_id)
                self.parent.box_summary = [
                    r for r in self.parent.box_summary
                    if (r["box_type"], r["box_id"]) != key
                ]
                self.parent.box_summary.append({
                    "box_type": box_type, "box_id": box_id,
                    "organization_id": org, "state": state,
                    "summary_json": summary_json,
                    "timeline_preview_json": timeline_preview_json,
                    "exceptions_json": exceptions_json,
                    "outcome_json": outcome_json,
                    "event_count": event_count,
                    "last_event_id": last_event_id,
                    "last_state_at": last_state_at,
                    "updated_at": updated_at,
                })
                self._last = []
            elif sql_lower.startswith("insert into box_summary_history"):
                (id_, box_type, box_id, org, snapshot_at, state,
                 summary_json, transition_event_id, triggered_by) = params
                self.parent.box_summary_history.append({
                    "id": id_, "box_type": box_type, "box_id": box_id,
                    "organization_id": org, "snapshot_at": snapshot_at,
                    "state": state, "summary_json": summary_json,
                    "transition_event_id": transition_event_id,
                    "triggered_by": triggered_by,
                })
                self._last = []
            elif sql_lower.startswith("insert into vendor_summary"):
                (org, normalized, display, total_bills, amount_json,
                 avg_days, exception_rate, last_activity, posted, paid,
                 rejected, recomputed_at) = params
                key = (org, normalized)
                self.parent.vendor_summary = [
                    r for r in self.parent.vendor_summary
                    if (r["organization_id"], r["vendor_name_normalized"]) != key
                ]
                self.parent.vendor_summary.append({
                    "organization_id": org,
                    "vendor_name_normalized": normalized,
                    "vendor_display_name": display,
                    "total_bills": total_bills,
                    "total_amount_by_currency_json": amount_json,
                    "avg_days_to_pay": avg_days,
                    "exception_rate": exception_rate,
                    "last_activity_at": last_activity,
                    "posted_count": posted,
                    "paid_count": paid,
                    "rejected_count": rejected,
                    "recomputed_at": recomputed_at,
                })
                self._last = []
            elif sql_lower.startswith("select count(*) as c from audit_events"):
                box_id = params[0]
                count = sum(
                    1 for e in self.parent.audit_events
                    if e.get("box_id") == box_id
                )
                self._last = [{"c": count}]
            elif sql_lower.startswith("select id from audit_events where box_id = %s order by ts desc"):
                box_id = params[0]
                events = sorted(
                    [e for e in self.parent.audit_events if e.get("box_id") == box_id],
                    key=lambda r: r.get("ts") or "", reverse=True,
                )
                self._last = [{"id": events[0]["id"]}] if events else []
            elif sql_lower.startswith("select id, state, amount, currency"):
                org, normalized = params
                self._last = [
                    {
                        "id": v.get("id"),
                        "state": v.get("state"),
                        "amount": v.get("amount"),
                        "currency": v.get("currency"),
                        "created_at": v.get("created_at"),
                        "updated_at": v.get("updated_at"),
                        "posted_at": v.get("posted_at"),
                    }
                    for v in self.parent.ap_items.values()
                    if v.get("organization_id") == org
                    and " ".join(str(v.get("vendor_name") or "").lower().split()) == normalized
                ]
            elif sql_lower.startswith("select * from box_summary where box_type = %s and box_id"):
                box_type, box_id = params
                self._last = [
                    r for r in self.parent.box_summary
                    if r["box_type"] == box_type and r["box_id"] == box_id
                ]
            elif "from box_summary_history" in sql_lower and "snapshot_at <= %s" in sql_lower:
                box_type, box_id, at = params
                rows = sorted(
                    [
                        r for r in self.parent.box_summary_history
                        if r["box_type"] == box_type
                        and r["box_id"] == box_id
                        and (r["snapshot_at"] or "") <= at
                    ],
                    key=lambda r: r["snapshot_at"] or "", reverse=True,
                )
                self._last = rows[:1]
            elif sql_lower.startswith("select * from box_summary_history"):
                box_type, box_id, limit = params
                rows = sorted(
                    [
                        r for r in self.parent.box_summary_history
                        if r["box_type"] == box_type and r["box_id"] == box_id
                    ],
                    key=lambda r: r["snapshot_at"] or "", reverse=True,
                )
                self._last = rows[: int(limit)]
            elif sql_lower.startswith("select * from vendor_summary where organization_id = %s and vendor_name_normalized"):
                org, normalized = params
                self._last = [
                    r for r in self.parent.vendor_summary
                    if r["organization_id"] == org
                    and r["vendor_name_normalized"] == normalized
                ]
            elif sql_lower.startswith("select * from vendor_summary"):
                org = params[0]
                limit = params[1] if len(params) > 1 else 100
                rows = [r for r in self.parent.vendor_summary if r["organization_id"] == org]
                self._last = rows[: int(limit)]
            else:
                self._last = []

        def fetchone(self):
            return self._last[0] if self._last else None

        def fetchall(self):
            return list(self._last)


# ─── Registry / handler ────────────────────────────────────────────


def test_default_projectors_registered_at_import():
    import clearledgr.services.box_projection  # noqa: F401
    from clearledgr.services.box_projection import list_registered_projectors
    names = list_registered_projectors()
    assert "box_summary" in names
    assert "vendor_summary" in names


def test_projection_outbox_handler_registered():
    import clearledgr.services.box_projection  # noqa: F401
    from clearledgr.services.outbox import list_handlers
    assert "projection" in list_handlers()


def test_default_projectors_satisfy_protocol():
    from clearledgr.services.box_projection import (
        BoxProjector, BoxSummaryProjector, VendorSummaryProjector,
    )
    db = _FakeProjectionDB()
    assert isinstance(BoxSummaryProjector(db), BoxProjector)
    assert isinstance(VendorSummaryProjector(db), BoxProjector)


def test_register_projector_idempotent_for_same_instance():
    from clearledgr.services.box_projection import (
        register_projector, _PROJECTOR_REGISTRY,
    )

    class _Probe:
        projector_name = "idempotent_probe"
        box_types = ("ap_item",)

        async def project(self, ctx):
            pass

    saved = dict(_PROJECTOR_REGISTRY)
    try:
        p = _Probe()
        register_projector(p)
        register_projector(p)  # same instance — no raise
    finally:
        _PROJECTOR_REGISTRY.clear()
        _PROJECTOR_REGISTRY.update(saved)


def test_register_projector_rejects_collision():
    from clearledgr.services.box_projection import (
        register_projector, _PROJECTOR_REGISTRY,
    )

    class _DoppelgangerA:
        projector_name = "collision_probe"
        box_types = ("ap_item",)

        async def project(self, ctx):
            pass

    class _DoppelgangerB:
        projector_name = "collision_probe"
        box_types = ("ap_item",)

        async def project(self, ctx):
            pass

    saved = dict(_PROJECTOR_REGISTRY)
    try:
        _PROJECTOR_REGISTRY["collision_probe"] = _DoppelgangerA()
        with pytest.raises(ValueError):
            register_projector(_DoppelgangerB())
    finally:
        _PROJECTOR_REGISTRY.clear()
        _PROJECTOR_REGISTRY.update(saved)


# ─── BoxSummaryProjector ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_box_summary_projector_upserts_and_appends_history():
    from clearledgr.services.box_projection import (
        BoxSummaryProjector, ProjectionContext,
    )
    db = _FakeProjectionDB()
    db.ap_items["AP-1"] = {
        "id": "AP-1", "organization_id": "org-1",
        "state": "validated",
        "vendor_name": "Acme Inc",
        "amount": 100.0, "currency": "USD",
        "invoice_number": "INV-1", "due_date": "2026-05-01",
    }
    db.audit_events.append({
        "id": "AE-1", "box_id": "AP-1", "ts": "2026-04-25T12:00:00+00:00",
        "event_type": "state_transition",
    })

    projector = BoxSummaryProjector(db)
    ctx = ProjectionContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="received", new_state="validated",
        actor_id="alice", correlation_id="cor-1",
        source_type="gmail", erp_native=False,
        metadata={}, transition_event_id="AE-1",
    )
    result = await projector.project(ctx)
    assert result.rows_upserted == 1
    assert result.rows_inserted == 1
    assert len(db.box_summary) == 1
    assert db.box_summary[0]["state"] == "validated"
    assert db.box_summary[0]["last_event_id"] == "AE-1"
    assert db.box_summary[0]["event_count"] == 1
    assert len(db.box_summary_history) == 1
    assert db.box_summary_history[0]["transition_event_id"] == "AE-1"


@pytest.mark.asyncio
async def test_box_summary_projector_skips_missing_box_id():
    from clearledgr.services.box_projection import (
        BoxSummaryProjector, ProjectionContext,
    )
    db = _FakeProjectionDB()
    projector = BoxSummaryProjector(db)
    ctx = ProjectionContext(
        organization_id="org-1", box_type="ap_item", box_id="",
        old_state="", new_state="validated",
        actor_id=None, correlation_id=None,
        source_type="gmail", erp_native=False,
        metadata={}, transition_event_id=None,
    )
    result = await projector.project(ctx)
    assert result.skip_reason == "missing_box_id"
    assert db.box_summary == []


@pytest.mark.asyncio
async def test_box_summary_projector_replaces_on_subsequent_transitions():
    """Same Box transitioning again — UPSERT replaces row, history
    grows by one."""
    from clearledgr.services.box_projection import (
        BoxSummaryProjector, ProjectionContext,
    )
    db = _FakeProjectionDB()
    db.ap_items["AP-1"] = {
        "id": "AP-1", "organization_id": "org-1",
        "state": "validated",
        "vendor_name": "Acme",
        "amount": 1, "currency": "USD",
    }
    projector = BoxSummaryProjector(db)
    base = ProjectionContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="received", new_state="validated",
        actor_id="a", correlation_id="c",
        source_type="gmail", erp_native=False,
        metadata={}, transition_event_id=None,
    )
    await projector.project(base)
    db.ap_items["AP-1"]["state"] = "approved"
    await projector.project(ProjectionContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="validated", new_state="approved",
        actor_id="a", correlation_id="c2",
        source_type="gmail", erp_native=False,
        metadata={}, transition_event_id=None,
    ))
    assert len(db.box_summary) == 1
    assert db.box_summary[0]["state"] == "approved"
    assert len(db.box_summary_history) == 2


# ─── VendorSummaryProjector ────────────────────────────────────────


@pytest.mark.asyncio
async def test_vendor_summary_skips_irrelevant_states():
    from clearledgr.services.box_projection import (
        VendorSummaryProjector, ProjectionContext,
    )
    db = _FakeProjectionDB()
    db.ap_items["AP-1"] = {
        "id": "AP-1", "organization_id": "org-1",
        "vendor_name": "Acme", "state": "received",
    }
    projector = VendorSummaryProjector(db)
    ctx = ProjectionContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="ingested", new_state="received",
        actor_id=None, correlation_id=None,
        source_type="gmail", erp_native=False,
        metadata={}, transition_event_id=None,
    )
    result = await projector.project(ctx)
    assert result.skip_reason == "state_not_relevant:received"
    assert db.vendor_summary == []


@pytest.mark.asyncio
async def test_vendor_summary_recomputes_rollup():
    """Three bills for one vendor: posted + paid + rejected. Verify
    counts + exception rate + currency split."""
    from clearledgr.services.box_projection import (
        VendorSummaryProjector, ProjectionContext,
    )
    db = _FakeProjectionDB()
    base_org = "org-1"
    now = "2026-04-25T12:00:00+00:00"
    earlier = "2026-04-20T12:00:00+00:00"
    db.ap_items["AP-1"] = {
        "id": "AP-1", "organization_id": base_org, "vendor_name": "Acme Inc",
        "state": "posted_to_erp", "amount": 100.0, "currency": "USD",
        "created_at": earlier, "updated_at": now, "posted_at": now,
    }
    db.ap_items["AP-2"] = {
        "id": "AP-2", "organization_id": base_org, "vendor_name": "Acme Inc",
        "state": "paid", "amount": 50.0, "currency": "USD",
        "created_at": earlier, "updated_at": now, "posted_at": now,
    }
    db.ap_items["AP-3"] = {
        "id": "AP-3", "organization_id": base_org, "vendor_name": "Acme Inc",
        "state": "failed_post", "amount": 25.0, "currency": "EUR",
        "created_at": earlier, "updated_at": now, "posted_at": now,
    }
    db.ap_items["AP-4"] = {
        "id": "AP-4", "organization_id": base_org, "vendor_name": "Acme Inc",
        "state": "rejected", "amount": 5.0, "currency": "USD",
        "created_at": earlier, "updated_at": now, "posted_at": now,
    }

    projector = VendorSummaryProjector(db)
    ctx = ProjectionContext(
        organization_id=base_org, box_type="ap_item", box_id="AP-1",
        old_state="ready_to_post", new_state="posted_to_erp",
        actor_id="alice", correlation_id=None,
        source_type="gmail", erp_native=False,
        metadata={}, transition_event_id=None,
    )
    result = await projector.project(ctx)
    assert result.rows_upserted == 1
    assert len(db.vendor_summary) == 1
    row = db.vendor_summary[0]
    assert row["vendor_name_normalized"] == "acme inc"
    assert row["vendor_display_name"] == "Acme Inc"
    assert row["total_bills"] == 4
    assert row["posted_count"] == 1
    assert row["paid_count"] == 1
    assert row["rejected_count"] == 2  # rejected + failed_post both bump rejected_count
    # Exception rate: 1/4 — only failed_post is an exception (needs_info
    # is the other but we have none here). Rejected is a clean outcome.
    assert row["exception_rate"] == pytest.approx(1.0 / 4.0)
    import json
    amount_split = json.loads(row["total_amount_by_currency_json"])
    assert amount_split["USD"] == pytest.approx(155.0)
    assert amount_split["EUR"] == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_vendor_summary_skips_when_no_vendor_name():
    from clearledgr.services.box_projection import (
        VendorSummaryProjector, ProjectionContext,
    )
    db = _FakeProjectionDB()
    db.ap_items["AP-1"] = {
        "id": "AP-1", "organization_id": "org-1",
        "vendor_name": "", "state": "posted_to_erp",
    }
    projector = VendorSummaryProjector(db)
    ctx = ProjectionContext(
        organization_id="org-1", box_type="ap_item", box_id="AP-1",
        old_state="ready_to_post", new_state="posted_to_erp",
        actor_id=None, correlation_id=None,
        source_type="gmail", erp_native=False,
        metadata={}, transition_event_id=None,
    )
    result = await projector.project(ctx)
    assert result.skip_reason == "no_vendor_name"


def test_vendor_summary_normalization():
    from clearledgr.services.box_projection import VendorSummaryProjector
    assert VendorSummaryProjector._normalize("  Acme   Inc ") == "acme inc"
    assert VendorSummaryProjector._normalize("ACME inc") == "acme inc"


# ─── BoxProjectionObserver ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_observer_enqueues_one_row_per_matching_projector():
    """Observer should fan out one outbox row per projector that
    declares the box_type. Both built-ins are ap_item, so we expect 2."""
    from clearledgr.services.box_projection import (
        BoxProjectionObserver, _PROJECTOR_REGISTRY,
    )

    enqueue_calls: List[Dict[str, Any]] = []

    class _FakeWriter:
        def __init__(self, org_id):
            self.organization_id = org_id

        def enqueue(self, **kwargs):
            enqueue_calls.append(kwargs)
            return f"OE-{len(enqueue_calls)}"

    class _Event:
        ap_item_id = "AP-1"
        organization_id = "org-1"
        old_state = "received"
        new_state = "validated"
        actor_id = "alice"
        correlation_id = "cor-1"
        source_type = "gmail"
        erp_native = False
        metadata = {"vendor_name": "Acme"}

    db = _FakeProjectionDB()
    observer = BoxProjectionObserver(db, box_type="ap_item")
    with patch("clearledgr.services.outbox.OutboxWriter", _FakeWriter):
        await observer.on_transition(_Event())

    assert len(enqueue_calls) == len([
        n for n, p in _PROJECTOR_REGISTRY.items()
        if "ap_item" in (p.box_types or ())
    ])
    targets = {c["target"] for c in enqueue_calls}
    assert "projection:box_summary" in targets
    assert "projection:vendor_summary" in targets


@pytest.mark.asyncio
async def test_observer_no_op_when_registry_empty():
    """If no projectors are registered for the box_type, observer
    must enqueue nothing."""
    from clearledgr.services.box_projection import (
        BoxProjectionObserver,
    )

    enqueue_calls: List[Dict[str, Any]] = []

    class _FakeWriter:
        def __init__(self, org_id):
            self.organization_id = org_id

        def enqueue(self, **kwargs):
            enqueue_calls.append(kwargs)
            return "OE-1"

    class _Event:
        ap_item_id = "AP-1"
        organization_id = "org-1"
        old_state = "received"
        new_state = "validated"
        actor_id = None
        correlation_id = None
        source_type = "gmail"
        erp_native = False
        metadata = {}

    db = _FakeProjectionDB()
    observer = BoxProjectionObserver(db, box_type="po_item")  # no projector for po_item
    with patch("clearledgr.services.outbox.OutboxWriter", _FakeWriter):
        await observer.on_transition(_Event())
    assert enqueue_calls == []


# ─── Outbox handler dispatch ───────────────────────────────────────


@pytest.mark.asyncio
async def test_outbox_handler_dispatches_to_named_projector():
    from clearledgr.services.box_projection import (
        _outbox_handler_projection, _PROJECTOR_REGISTRY,
    )

    captured: List[Any] = []

    class _Probe:
        projector_name = "test_probe"
        box_types = ("ap_item",)

        async def project(self, ctx):
            captured.append(ctx)
            from clearledgr.services.box_projection import ProjectionResult
            return ProjectionResult()

    saved = dict(_PROJECTOR_REGISTRY)
    try:
        _PROJECTOR_REGISTRY["test_probe"] = _Probe()

        class _Outbox:
            id = "OE-1"
            organization_id = "org-1"
            target = "projection:test_probe"
            payload = {
                "box_type": "ap_item", "box_id": "AP-1",
                "old_state": "x", "new_state": "y",
                "actor_id": "a", "correlation_id": "c",
                "source_type": "gmail", "erp_native": False,
                "metadata": {},
            }
        await _outbox_handler_projection(_Outbox())
    finally:
        _PROJECTOR_REGISTRY.clear()
        _PROJECTOR_REGISTRY.update(saved)
    assert len(captured) == 1
    assert captured[0].box_id == "AP-1"


@pytest.mark.asyncio
async def test_outbox_handler_raises_for_unknown_target():
    from clearledgr.services.box_projection import _outbox_handler_projection

    class _Outbox:
        id = "OE-1"
        organization_id = "org-1"
        target = "projection:nonexistent_projector_zz"
        payload = {}

    with pytest.raises(LookupError):
        await _outbox_handler_projection(_Outbox())


@pytest.mark.asyncio
async def test_outbox_handler_rejects_wrong_prefix():
    from clearledgr.services.box_projection import _outbox_handler_projection

    class _Outbox:
        id = "OE-1"
        organization_id = "org-1"
        target = "observer:Foo"
        payload = {}

    with pytest.raises(ValueError):
        await _outbox_handler_projection(_Outbox())


# ─── Read helpers ──────────────────────────────────────────────────


def test_get_box_summary_row_returns_none_when_missing():
    from clearledgr.services.box_projection import get_box_summary_row
    db = _FakeProjectionDB()
    row = get_box_summary_row("ap_item", "AP-NOTHERE", db=db)
    assert row is None


def test_get_box_summary_row_hydrates_json_columns():
    from clearledgr.services.box_projection import get_box_summary_row
    db = _FakeProjectionDB()
    db.box_summary.append({
        "box_type": "ap_item", "box_id": "AP-1",
        "organization_id": "org-1", "state": "validated",
        "summary_json": '{"current_stage": "validated"}',
        "timeline_preview_json": '[{"id": "AE-1"}]',
        "exceptions_json": '[]',
        "outcome_json": None,
        "event_count": 3,
        "last_event_id": "AE-3",
        "last_state_at": "2026-04-25T12:00:00+00:00",
        "updated_at": "2026-04-25T12:00:00+00:00",
    })
    row = get_box_summary_row("ap_item", "AP-1", db=db)
    assert row is not None
    assert row["state"] == "validated"
    assert row["summary"] == {"current_stage": "validated"}
    assert row["timeline_preview"] == [{"id": "AE-1"}]
    assert row["last_event_id"] == "AE-3"


def test_get_box_history_filters_by_at():
    from clearledgr.services.box_projection import get_box_history
    db = _FakeProjectionDB()
    db.box_summary_history.extend([
        {
            "id": f"BSH-{i}", "box_type": "ap_item", "box_id": "AP-1",
            "organization_id": "org-1",
            "snapshot_at": f"2026-04-{20+i:02d}T12:00:00+00:00",
            "state": s, "summary_json": "{}",
            "transition_event_id": f"AE-{i}", "triggered_by": "system",
        }
        for i, s in enumerate(["received", "validated", "approved"])
    ])
    snapshots = get_box_history(
        "ap_item", "AP-1", at="2026-04-21T13:00:00+00:00", db=db,
    )
    assert len(snapshots) == 1
    # Latest snapshot at-or-before 2026-04-21T13 → index 1 (validated, 2026-04-21)
    assert snapshots[0]["state"] == "validated"


def test_get_box_history_returns_recent_when_no_at():
    from clearledgr.services.box_projection import get_box_history
    db = _FakeProjectionDB()
    db.box_summary_history.extend([
        {
            "id": f"BSH-{i}", "box_type": "ap_item", "box_id": "AP-1",
            "organization_id": "org-1",
            "snapshot_at": f"2026-04-{20+i:02d}T12:00:00+00:00",
            "state": "validated", "summary_json": "{}",
            "transition_event_id": None, "triggered_by": "system",
        }
        for i in range(5)
    ])
    snapshots = get_box_history("ap_item", "AP-1", limit=3, db=db)
    assert len(snapshots) == 3
    # Sorted descending by snapshot_at
    assert snapshots[0]["snapshot_at"] > snapshots[1]["snapshot_at"]


def test_get_vendor_summary_row_normalizes_lookup():
    from clearledgr.services.box_projection import get_vendor_summary_row
    db = _FakeProjectionDB()
    db.vendor_summary.append({
        "organization_id": "org-1",
        "vendor_name_normalized": "acme inc",
        "vendor_display_name": "Acme Inc",
        "total_bills": 3,
        "total_amount_by_currency_json": '{"USD": 150.0}',
        "avg_days_to_pay": 5.0, "exception_rate": 0.33,
        "last_activity_at": "2026-04-25T12:00:00+00:00",
        "posted_count": 1, "paid_count": 1, "rejected_count": 1,
        "recomputed_at": "2026-04-25T12:00:00+00:00",
    })
    row = get_vendor_summary_row("org-1", "  ACME    Inc  ", db=db)
    assert row is not None
    assert row["vendor_display_name"] == "Acme Inc"
    assert row["total_amount_by_currency"] == {"USD": 150.0}
    assert row["total_bills"] == 3


def test_list_vendor_summaries_returns_rows():
    from clearledgr.services.box_projection import list_vendor_summaries
    db = _FakeProjectionDB()
    db.vendor_summary.extend([
        {
            "organization_id": "org-1",
            "vendor_name_normalized": f"vendor {i}",
            "vendor_display_name": f"Vendor {i}",
            "total_bills": i, "total_amount_by_currency_json": "{}",
            "avg_days_to_pay": None, "exception_rate": 0.0,
            "last_activity_at": None,
            "posted_count": 0, "paid_count": 0, "rejected_count": 0,
            "recomputed_at": "2026-04-25T12:00:00+00:00",
        }
        for i in range(3)
    ])
    rows = list_vendor_summaries("org-1", limit=10, db=db)
    assert len(rows) == 3


# ─── Rebuild ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_projections_walks_all_items():
    from clearledgr.services.box_projection import (
        rebuild_projections, _PROJECTOR_REGISTRY,
    )

    handled: List[str] = []

    class _Probe:
        projector_name = "rebuild_probe"
        box_types = ("ap_item",)

        async def project(self, ctx):
            handled.append(ctx.box_id)
            from clearledgr.services.box_projection import ProjectionResult
            return ProjectionResult(rows_upserted=1)

    db = _FakeProjectionDB()
    db.ap_items.update({
        "AP-1": {"id": "AP-1", "organization_id": "org-1", "state": "validated"},
        "AP-2": {"id": "AP-2", "organization_id": "org-1", "state": "approved"},
        "AP-3": {"id": "AP-3", "organization_id": "org-2", "state": "validated"},
    })
    saved = dict(_PROJECTOR_REGISTRY)
    try:
        _PROJECTOR_REGISTRY.clear()
        _PROJECTOR_REGISTRY["rebuild_probe"] = _Probe()
        result = await rebuild_projections("org-1", db=db)
    finally:
        _PROJECTOR_REGISTRY.clear()
        _PROJECTOR_REGISTRY.update(saved)
    assert result["items_processed"] == 2
    assert result["rebuild_probe_applied"] == 2
    assert sorted(handled) == ["AP-1", "AP-2"]


# ─── Stale-projection fallthrough on the read endpoint ─────────────


def test_is_projection_stale_returns_true_when_audit_has_newer_event():
    from clearledgr.api.ap_items_read_routes import _is_projection_stale
    db = _FakeProjectionDB()
    db.audit_events.append({
        "id": "AE-NEW", "box_id": "AP-1",
        "ts": "2026-04-26T12:00:00+00:00",
    })
    projection = {"last_event_id": "AE-OLD"}
    assert _is_projection_stale(db, "AP-1", projection) is True


def test_is_projection_stale_returns_false_when_in_sync():
    from clearledgr.api.ap_items_read_routes import _is_projection_stale
    db = _FakeProjectionDB()
    db.audit_events.append({
        "id": "AE-1", "box_id": "AP-1",
        "ts": "2026-04-25T12:00:00+00:00",
    })
    projection = {"last_event_id": "AE-1"}
    assert _is_projection_stale(db, "AP-1", projection) is False


def test_is_projection_stale_returns_false_when_no_audit_events():
    """Projection exists but audit_events has nothing — treat as
    fresh (we wrote the projection after the transition the projector
    saw; nothing newer to invalidate it)."""
    from clearledgr.api.ap_items_read_routes import _is_projection_stale
    db = _FakeProjectionDB()
    projection = {"last_event_id": "AE-1"}
    assert _is_projection_stale(db, "AP-1", projection) is False
