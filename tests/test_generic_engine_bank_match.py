"""Generic-runtime proof: drive a bank_match Box through the SAME engine
primitives as ap_item, with no AP-specific code on the critical path.

This is the empirical test of the "workflow runtime" thesis: a second
box type runs through box_registry CRUD dispatch + the CoordinationEngine
without bespoke handlers. ``test_bank_match_box.py`` covers the
store/registry in isolation; THIS file covers the ENGINE path.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import box_registry  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.coordination_engine import CoordinationEngine  # noqa: E402
from solden.core.plan import Action, Plan  # noqa: E402

ORG = "orgGenBM"


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization(ORG, organization_name=ORG)
    return inst


def _parent_ap(db, item_id="AP-gen-bm"):
    return db.create_ap_item({
        "id": item_id,
        "organization_id": ORG,
        "vendor_name": "Acme",
        "amount": 200.0,
        "state": "received",
    })


def _bank_match(db, parent_id, box_id="BM-gen-1"):
    return box_registry.create_box("bank_match", {
        "id": box_id,
        "organization_id": ORG,
        "parent_ap_item_id": parent_id,
        "payment_confirmation_id": "pc-1",
        "bank_statement_line_id": "bsl-1",
        "confidence": 0.92,
    }, db)


def _audit_rows(db, organization_id, box_type=None):
    if box_type:
        sql = ("SELECT * FROM audit_events WHERE organization_id = %s "
               "AND box_type = %s ORDER BY chain_seq ASC")
        params = (organization_id, box_type)
    else:
        sql = ("SELECT * FROM audit_events WHERE organization_id = %s "
               "ORDER BY chain_seq ASC")
        params = (organization_id,)
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def test_create_box_dispatches_to_bank_match(db):
    parent = _parent_ap(db)
    match = _bank_match(db, parent["id"])
    assert match["state"] == "proposed"
    # generic get_box round-trips without naming a table
    loaded = box_registry.get_box("bank_match", match["id"], db)
    assert loaded["id"] == match["id"]
    # the create audit row is attributed to bank_match, not ap_item
    rows = _audit_rows(db, ORG, box_type="bank_match")
    assert any(r["box_id"] == match["id"] for r in rows)


def test_update_box_advances_bank_match_state(db):
    parent = _parent_ap(db, item_id="AP-gen-bm-2")
    match = _bank_match(db, parent["id"], box_id="BM-gen-2")
    box_registry.update_box(
        "bank_match", match["id"], db, state="accepted", actor_id="u-controller",
    )
    loaded = box_registry.get_box("bank_match", match["id"], db)
    assert loaded["state"] == "accepted"


def test_box_lifecycle_primitives_generic_for_bank_match(db):
    parent = _parent_ap(db, item_id="AP-gen-bm-3")
    match = _bank_match(db, parent["id"], box_id="BM-gen-3")
    exc = db.raise_box_exception(
        box_id=match["id"], box_type="bank_match", organization_id=ORG,
        exception_type="manual_review", reason="spot check",
        raised_by="agent", severity="medium",
    )
    assert exc and exc.get("id")
    open_excs = db.list_box_exceptions(box_id=match["id"], box_type="bank_match")
    assert any(e["id"] == exc["id"] for e in open_excs)
    db.resolve_box_exception(exception_id=exc["id"], resolved_by="u1")
    outcome = db.record_box_outcome(
        box_id=match["id"], box_type="bank_match", organization_id=ORG,
        outcome_type="accepted", recorded_by="agent",
    )
    assert outcome


def test_bank_match_rides_the_shared_audit_hash_chain(db):
    # bank_match writes must flow through the SAME org-partitioned
    # SHA-256 hash-chain trigger as ap_item — no bespoke audit path.
    parent = _parent_ap(db, item_id="AP-gen-bm-4")
    match = _bank_match(db, parent["id"], box_id="BM-gen-4")
    box_registry.update_box("bank_match", match["id"], db, state="accepted", actor_id="u1")

    bm_rows = _audit_rows(db, ORG, box_type="bank_match")
    # proposed (create) + accepted (update) both landed, in chain order
    assert len(bm_rows) >= 2
    for r in bm_rows:
        assert r.get("hash"), "trigger did not stamp a hash"
        assert r.get("chain_seq") is not None, "trigger did not assign chain_seq"
    # the accepted row chains off the proposed row's hash
    proposed = next(r for r in bm_rows if r["event_type"] == "bank_match_proposed")
    accepted = next(r for r in bm_rows if r["event_type"] == "bank_match_accepted")
    assert accepted["prev_hash"] == proposed["hash"]
    assert accepted["chain_seq"] > proposed["chain_seq"]


def test_coordination_engine_drives_bank_match_move(db):
    parent = _parent_ap(db, item_id="AP-gen-bm-5")
    match = _bank_match(db, parent["id"], box_id="BM-gen-5")
    engine = CoordinationEngine(db=db, organization_id=ORG)
    plan = Plan(
        event_type="bank_match_decision",
        actions=[Action("move_box_stage", "DET", {"target": "accepted", "actor_id": "u1"})],
        box_id=match["id"],
        box_type="bank_match",
        organization_id=ORG,
    )
    result = asyncio.run(engine.execute(plan))
    assert result.status == "completed"
    loaded = box_registry.get_box("bank_match", match["id"], db)
    assert loaded["state"] == "accepted"  # advanced via the generic handler


def test_engine_exception_path_for_typeless_stall_state(db):
    # bank_match has no exception_state; a failed action must raise a
    # box_exception, NOT attempt an illegal move to AP's needs_info.
    parent = _parent_ap(db, item_id="AP-gen-bm-6")
    match = _bank_match(db, parent["id"], box_id="BM-gen-6")
    engine = CoordinationEngine(db=db, organization_id=ORG)
    plan = Plan(
        event_type="bank_match_decision",
        actions=[Action("move_box_stage", "DET", {"target": "needs_info", "actor_id": "u1"})],
        box_id=match["id"],
        box_type="bank_match",
        organization_id=ORG,
    )
    result = asyncio.run(engine.execute(plan))
    assert result.status == "failed"
    # the box stayed proposed — no illegal needs_info move landed
    loaded = box_registry.get_box("bank_match", match["id"], db)
    assert loaded["state"] == "proposed"
    # and an exception was raised against it instead
    excs = db.list_box_exceptions(box_id=match["id"], box_type="bank_match")
    assert any(e.get("exception_type") == "action_failed" for e in excs)
