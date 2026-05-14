"""Coverage for ``services/box_cas.update_box_with_cas`` (Sprint 4 Phase 1).

The CAS helper sits between specialist agents and the AP-item
store: read-mutate-write with optimistic-locking retry. Tests
exercise the helper against an in-memory fake DB so we can
deterministically trigger the conflict path (the real DB would
require concurrent writers to repro).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from clearledgr.services.box_cas import (
    BoxConflict,
    update_box_with_cas,
)


class _FakeDB:
    """In-memory AP-item store mimicking the surface
    ``update_box_with_cas`` consumes.

    Fault-injection hook ``_on_update_pre_gate`` fires on every
    ``update_ap_item`` BEFORE the optimistic-locking gate check —
    that's the realistic conflict window: caller has already read +
    mutated, the row gets bumped by a concurrent writer, then our
    gated write hits the bumped ``updated_at`` and fails.
    """

    def __init__(self) -> None:
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._update_calls: List[Dict[str, Any]] = []
        self._on_update_pre_gate: Optional[Any] = None

    def add_row(self, row: Dict[str, Any]) -> None:
        self._rows[row["id"]] = dict(row)

    def get_ap_item(self, ap_item_id: str) -> Optional[Dict[str, Any]]:
        row = self._rows.get(ap_item_id)
        return dict(row) if row else None

    def update_ap_item(self, ap_item_id: str, **kwargs) -> bool:
        self._update_calls.append({"ap_item_id": ap_item_id, **kwargs})
        # Fault-injection: simulate a concurrent writer between the
        # caller's read and our gate check.
        if self._on_update_pre_gate:
            self._on_update_pre_gate(ap_item_id)
        expected = kwargs.pop("_expected_updated_at", None)
        kwargs.pop("_actor_type", None)
        kwargs.pop("_actor_id", None)
        kwargs.pop("_decision_reason", None)
        row = self._rows.get(ap_item_id)
        if not row:
            return False
        if expected is not None and row.get("updated_at") != expected:
            return False
        for key, value in kwargs.items():
            row[key] = value
        row["updated_at"] = (row.get("updated_at") or "0000") + "+1"
        return True

    def simulate_concurrent_write(self, ap_item_id: str) -> None:
        row = self._rows[ap_item_id]
        row["updated_at"] = (row.get("updated_at") or "0000") + "-rival"


# ─── Happy path ─────────────────────────────────────────────────────


def test_cas_updates_box_when_no_conflict():
    db = _FakeDB()
    db.add_row({
        "id": "ap-1", "organization_id": "org-x",
        "state": "received", "updated_at": "T0",
        "vendor_name": "Stripe",
    })

    def mutator(box):
        return {**box, "state": "needs_approval"}

    out = update_box_with_cas(
        db,
        ap_item_id="ap-1",
        organization_id="org-x",
        mutator=mutator,
        actor_id="agent:ap",
    )
    assert out["state"] == "needs_approval"
    # One read + one write + one re-read post-update = 2 reads, 1 update.
    assert len(db._update_calls) == 1
    assert db._update_calls[0]["state"] == "needs_approval"
    assert db._update_calls[0]["_actor_id"] == "agent:ap"
    assert db._update_calls[0]["_actor_type"] == "agent"


def test_cas_no_op_when_mutator_returns_unchanged_state():
    db = _FakeDB()
    db.add_row({
        "id": "ap-1", "organization_id": "org-x",
        "state": "received", "updated_at": "T0",
    })
    out = update_box_with_cas(
        db,
        ap_item_id="ap-1",
        organization_id="org-x",
        mutator=lambda box: dict(box),  # identity
        actor_id="agent:ap",
    )
    assert out["state"] == "received"
    assert db._update_calls == []  # no write


def test_cas_only_writes_changed_columns():
    """Mutator returns the full box dict back; helper diffs and
    only writes columns that actually changed.
    """
    db = _FakeDB()
    db.add_row({
        "id": "ap-1", "organization_id": "org-x",
        "state": "received", "vendor_name": "Stripe",
        "updated_at": "T0",
    })
    update_box_with_cas(
        db,
        ap_item_id="ap-1",
        organization_id="org-x",
        mutator=lambda box: {**box, "state": "needs_approval"},
        actor_id="agent:ap",
    )
    write = db._update_calls[0]
    # Only state in the diff; vendor_name should NOT be in the
    # write payload because it didn't change.
    assert "state" in write
    assert "vendor_name" not in write


# ─── Conflict path ──────────────────────────────────────────────────


def test_cas_retries_on_concurrent_write_then_succeeds():
    """Mid-flight: between read and write, another writer bumps the
    row. The CAS helper detects the conflict, re-reads, re-runs the
    mutator, and writes successfully.
    """
    db = _FakeDB()
    db.add_row({
        "id": "ap-1", "organization_id": "org-x",
        "state": "received", "updated_at": "T0",
    })

    # On the first ``update_ap_item`` call, simulate a concurrent
    # writer bumping the row before our gate check — the caller
    # already read + mutated, the rival lands first, our gated
    # write sees the mismatch and fails. The retry then re-reads
    # cleanly.
    fired = {"count": 0}

    def race_hook(_ap_id):
        if fired["count"] == 0:
            fired["count"] += 1
            db.simulate_concurrent_write(_ap_id)

    db._on_update_pre_gate = race_hook

    out = update_box_with_cas(
        db,
        ap_item_id="ap-1",
        organization_id="org-x",
        mutator=lambda box: {**box, "state": "needs_approval"},
        actor_id="agent:ap",
        max_retries=3,
    )
    assert out["state"] == "needs_approval"
    # Two attempts: first one conflicted (write returned False),
    # second one succeeded.
    successful_writes = [c for c in db._update_calls
                          if c["state"] == "needs_approval"]
    assert len(successful_writes) >= 1


def test_cas_raises_box_conflict_when_retries_exhausted():
    db = _FakeDB()
    db.add_row({
        "id": "ap-1", "organization_id": "org-x",
        "state": "received", "updated_at": "T0",
    })

    # Every gated write hits a fresh concurrent-writer bump — CAS
    # can never win. After max_retries attempts, the helper raises
    # BoxConflict.
    def always_race(_ap_id):
        db.simulate_concurrent_write(_ap_id)

    db._on_update_pre_gate = always_race

    with pytest.raises(BoxConflict) as exc_info:
        update_box_with_cas(
            db,
            ap_item_id="ap-1",
            organization_id="org-x",
            mutator=lambda box: {**box, "state": "needs_approval"},
            actor_id="agent:ap",
            max_retries=2,
        )
    err = exc_info.value
    assert err.ap_item_id == "ap-1"
    assert err.attempts == 2
    assert err.expected_updated_at is not None
    assert err.observed_updated_at is not None


# ─── Tenancy + safety ──────────────────────────────────────────────


def test_cas_rejects_box_from_different_organization():
    db = _FakeDB()
    db.add_row({
        "id": "ap-1", "organization_id": "org-x",
        "state": "received", "updated_at": "T0",
    })

    with pytest.raises(LookupError, match="not found in organization"):
        update_box_with_cas(
            db,
            ap_item_id="ap-1",
            organization_id="org-y",  # wrong org
            mutator=lambda box: {**box, "state": "needs_approval"},
            actor_id="agent:ap",
        )
    assert db._update_calls == []


def test_cas_rejects_missing_box():
    db = _FakeDB()
    with pytest.raises(LookupError, match="not found"):
        update_box_with_cas(
            db,
            ap_item_id="ap-missing",
            organization_id="org-x",
            mutator=lambda box: {**box, "state": "x"},
            actor_id="agent:ap",
        )


def test_cas_rejects_mutator_returning_non_dict():
    db = _FakeDB()
    db.add_row({"id": "ap-1", "organization_id": "org-x",
                "state": "received", "updated_at": "T0"})
    with pytest.raises(ValueError, match="expected dict"):
        update_box_with_cas(
            db,
            ap_item_id="ap-1",
            organization_id="org-x",
            mutator=lambda box: "not-a-dict",
            actor_id="agent:ap",
        )


def test_cas_rejects_mutator_writing_unwhitelisted_column():
    """A buggy specialist returning an arbitrary column key must
    not silently sneak through. Mirrors the AP store's column
    whitelist as a service-layer defense.
    """
    db = _FakeDB()
    db.add_row({"id": "ap-1", "organization_id": "org-x",
                "state": "received", "updated_at": "T0"})
    with pytest.raises(ValueError, match="not in the CAS-allowed whitelist"):
        update_box_with_cas(
            db,
            ap_item_id="ap-1",
            organization_id="org-x",
            mutator=lambda box: {**box, "internal_secret_column": "leaked"},
            actor_id="agent:ap",
        )


def test_cas_records_decision_reason_when_provided():
    db = _FakeDB()
    db.add_row({"id": "ap-1", "organization_id": "org-x",
                "state": "received", "updated_at": "T0"})
    update_box_with_cas(
        db,
        ap_item_id="ap-1",
        organization_id="org-x",
        mutator=lambda box: {**box, "state": "needs_approval"},
        actor_id="agent:ap",
        decision_reason="Match-tolerance gate cleared",
    )
    assert db._update_calls[0].get("_decision_reason") == "Match-tolerance gate cleared"


def test_cas_drops_identity_columns_from_diff():
    """Mutator returning the full box dict (the ``{**box, ...}``
    pattern) shouldn't write back ``id`` / ``organization_id`` /
    timestamp columns even though they appear in the dict.
    """
    db = _FakeDB()
    db.add_row({"id": "ap-1", "organization_id": "org-x",
                "state": "received", "updated_at": "T0",
                "created_at": "T0"})
    update_box_with_cas(
        db,
        ap_item_id="ap-1",
        organization_id="org-x",
        mutator=lambda box: {**box, "state": "needs_approval"},
        actor_id="agent:ap",
    )
    write = db._update_calls[0]
    for forbidden in ("id", "organization_id", "updated_at", "created_at"):
        # ``_expected_updated_at`` is the gate, not a write — that's allowed.
        assert forbidden not in write or forbidden == "_expected_updated_at"
