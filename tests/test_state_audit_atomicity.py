"""Phase 10 drift fence — state + audit atomicity.

Spec invariant: when `update_ap_item` mutates state, the `ap_items`
UPDATE and the `audit_events` INSERT must commit together or not at
all. A torn write (state changed without an audit row, or audit row
without a state change) would violate the deck's promise that every
Box is a persistent, attributable record of its timeline.

The current implementation in `ap_store.py:update_ap_item` does this
via a single `conn.commit()` that covers both statements in the same
`with self.connect()` block. This test file locks that invariant so
future refactors can't quietly split the commits.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "atomicity.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    inst = db_module.get_db()
    inst.initialize()
    return inst


def _seed(db, box_id: str = "AP-ATOM") -> None:
    db.create_ap_item({
        "id": box_id,
        "invoice_key": f"inv-{box_id}",
        "thread_id": f"thr-{box_id}",
        "message_id": f"msg-{box_id}",
        "subject": "Invoice",
        "sender": "billing@vendor.com",
        "vendor_name": "Acme",
        "amount": 500.0,
        "currency": "USD",
        "invoice_number": f"INV-{box_id}",
        "state": "received",
        "organization_id": "default",
    })


def test_state_and_audit_commit_together_on_success(db):
    """Happy path: a state transition persists both the new state and
    exactly one state_transition audit row."""
    _seed(db, "AP-ATOM-1")
    db.update_ap_item(
        "AP-ATOM-1",
        state="validated",
        _actor_type="agent",
        _actor_id="invoice_workflow",
    )

    item = db.get_ap_item("AP-ATOM-1")
    assert item["state"] == "validated"

    events = db.list_ap_audit_events("AP-ATOM-1")
    transitions = [e for e in events if e.get("event_type") == "state_transition"]
    assert len(transitions) == 1
    assert transitions[0]["prev_state"] == "received"
    assert transitions[0]["new_state"] == "validated"


def test_no_torn_state_when_audit_insert_fails(db):
    """If the audit_events INSERT raises mid-commit, the ap_items
    UPDATE must roll back. A torn state (new state without audit row)
    is the specific invariant this fence locks.

    We force the failure by wrapping `_prepare_sql` on the db so the
    audit_events INSERT never reaches the cursor. Because both writes
    share one `conn.commit()` in the same `with self.connect()` block,
    the UPDATE must not persist.
    """
    _seed(db, "AP-ATOM-2")

    real_prepare = db._prepare_sql

    def _reject_audit_insert(sql):
        if "INSERT INTO audit_events" in str(sql):
            raise RuntimeError("simulated audit insert failure")
        return real_prepare(sql)

    with patch.object(db, "_prepare_sql", _reject_audit_insert):
        with pytest.raises(Exception):
            db.update_ap_item(
                "AP-ATOM-2",
                state="validated",
                _actor_type="agent",
                _actor_id="invoice_workflow",
            )

    # Critical: state must NOT have advanced.
    item = db.get_ap_item("AP-ATOM-2")
    assert item["state"] == "received", (
        "State advanced without a matching audit row. update_ap_item "
        "must roll back the ap_items UPDATE when the audit_events "
        "INSERT fails — the two writes share a single conn.commit()."
    )

    # And no state_transition audit row should exist.
    events = db.list_ap_audit_events("AP-ATOM-2")
    transitions = [e for e in events if e.get("event_type") == "state_transition"]
    assert len(transitions) == 0


def test_non_state_update_does_not_write_audit_row(db):
    """A field-only update (no state change) should NOT write a
    state_transition row. The audit is specifically for state moves.
    """
    _seed(db, "AP-ATOM-3")
    before = len(db.list_ap_audit_events("AP-ATOM-3"))

    db.update_ap_item(
        "AP-ATOM-3",
        invoice_number="INV-NEWNUM",
        _actor_type="agent",
        _actor_id="test",
    )

    after_events = db.list_ap_audit_events("AP-ATOM-3")
    # No new state_transition rows; other lifecycle emits may have
    # happened (e.g. if exception_code was touched) but we only touched
    # invoice_number so the transition-specific count must be zero.
    transitions = [e for e in after_events if e.get("event_type") == "state_transition"]
    assert len(transitions) == 0
