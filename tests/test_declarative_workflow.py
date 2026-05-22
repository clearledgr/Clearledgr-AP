"""Generality proof #3 — and the platform proof.

bank_match and purchase_order proved the runtime is Box-type-agnostic, but
each was still hand-coded across ~10 files. This test proves the *declarative*
layer: a brand-new Box type (`contract_review`) is spun up from a single
:class:`WorkflowSpec` declaration — no per-type table, no ``*_states.py``, no
store mixin, no routes, no migration — and driven end-to-end through the SAME
``box_registry`` CRUD dispatch + CoordinationEngine + audit hash-chain +
exception queue as every built-in type.

If this passes, "declare a workflow, get a runtime" is true.
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
from solden.core import workflow_spec  # noqa: E402
from solden.core.coordination_engine import CoordinationEngine  # noqa: E402
from solden.core.plan import Action, Plan  # noqa: E402
from solden.core.workflow_spec import (  # noqa: E402
    IllegalWorkflowTransitionError,
    WorkflowSpec,
)

ORG = "orgGenWF"

# The ENTIRE per-type surface for a new Box type. No other code is written.
CONTRACT_REVIEW_SPEC = WorkflowSpec(
    box_type="contract_review",
    url_slug="contract-reviews",
    states=("draft", "in_review", "approved", "rejected"),
    initial_state="draft",
    terminal_states=("approved", "rejected"),
    transitions={
        "draft": {"in_review"},
        "in_review": {"approved", "rejected"},
    },
    action_states={"submit": "in_review", "approve": "approved", "reject": "rejected"},
    fields=("title", "counterparty", "value"),
    exception_state=None,  # no stall state -> illegal move raises a box_exception
)


@pytest.fixture(autouse=True)
def _register_spec():
    """Register the throwaway type, and pop it from BOTH registries after.

    Module-global registries persist across tests in a worker; the teardown
    prevents the throwaway type from leaking into unrelated tests.
    """
    workflow_spec.register_spec(CONTRACT_REVIEW_SPEC)
    try:
        yield
    finally:
        workflow_spec.unregister_spec("contract_review")


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization(ORG, organization_name=ORG)
    return inst


def _box(db, box_id="CR-1"):
    return box_registry.create_box("contract_review", {
        "id": box_id,
        "organization_id": ORG,
        "title": "MSA renewal",
        "counterparty": "Globex",
        "value": 25000,
        "created_by": "legal@acme.test",
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


def test_spec_validation_rejects_bad_graphs():
    # An action targeting an undeclared state is caught at validation time.
    bad = WorkflowSpec(
        box_type="bad_type",
        url_slug="bad-type",
        states=("a", "b"),
        initial_state="a",
        terminal_states=("b",),
        transitions={"a": {"b"}},
        action_states={"go": "nonexistent"},
    )
    errors = workflow_spec.validate_spec(bad)
    assert any("nonexistent" in e for e in errors)
    # Reserved box_type is rejected.
    assert workflow_spec.validate_spec(
        WorkflowSpec(box_type="ap_item", url_slug="x", states=("a",),
                     initial_state="a", terminal_states=("a",))
    )


def test_register_spec_derives_a_box_type():
    bt = box_registry.get("contract_review")
    assert bt.source_table == "boxes"
    assert bt.state_field == "state"
    assert bt.initial_state == "draft"
    assert bt.exception_state is None
    assert "draft" in bt.open_states
    assert bt.terminal_states == frozenset({"approved", "rejected"})


def test_create_box_dispatches_to_generic_store(db):
    box = _box(db)
    assert box["state"] == "draft"
    assert box["id"] == "CR-1"
    assert box["box_type"] == "contract_review"
    assert box["title"] == "MSA renewal"      # declared field round-trips via data JSONB
    assert box["counterparty"] == "Globex"
    loaded = box_registry.get_box("contract_review", "CR-1", db)
    assert loaded["id"] == "CR-1"
    rows = _audit_rows(db, ORG, box_type="contract_review")
    assert any(r["box_id"] == "CR-1"
               and r["event_type"] == "contract_review_created" for r in rows)


def test_data_payload_cannot_clobber_reserved_columns(db):
    # A malicious/clumsy 'state' in the data payload must not override the column.
    box_registry.create_box("contract_review", {
        "id": "CR-evil",
        "organization_id": ORG,
        "state": "approved",          # attempt to skip the lifecycle
        "title": "sneaky",
    }, db)
    loaded = box_registry.get_box("contract_review", "CR-evil", db)
    assert loaded["state"] == "draft"  # native column wins; entry is always initial


def test_update_box_advances_state_round_trip(db):
    _box(db, box_id="CR-2")
    box_registry.update_box("contract_review", "CR-2", db, state="in_review", actor_id="legal")
    box_registry.update_box("contract_review", "CR-2", db, state="approved", actor_id="gc")
    loaded = box_registry.get_box("contract_review", "CR-2", db)
    assert loaded["state"] == "approved"


def test_illegal_transition_raises_at_store(db):
    _box(db, box_id="CR-3")
    with pytest.raises(IllegalWorkflowTransitionError):
        box_registry.update_box("contract_review", "CR-3", db, state="approved", actor_id="gc")


def test_box_lifecycle_primitives_generic_for_declared_type(db):
    _box(db, box_id="CR-4")
    exc = db.raise_box_exception(
        box_id="CR-4", box_type="contract_review", organization_id=ORG,
        exception_type="legal_review", reason="needs counsel sign-off",
        raised_by="agent", severity="medium",
    )
    assert exc and exc.get("id")
    open_excs = db.list_box_exceptions(box_id="CR-4", box_type="contract_review")
    assert any(e["id"] == exc["id"] for e in open_excs)
    db.resolve_box_exception(exception_id=exc["id"], resolved_by="gc")
    outcome = db.record_box_outcome(
        box_id="CR-4", box_type="contract_review", organization_id=ORG,
        outcome_type="approved", recorded_by="agent",
    )
    assert outcome


def test_declared_box_rides_the_shared_audit_hash_chain(db):
    _box(db, box_id="CR-5")
    box_registry.update_box("contract_review", "CR-5", db, state="in_review", actor_id="legal")
    rows = _audit_rows(db, ORG, box_type="contract_review")
    assert len(rows) >= 2
    for r in rows:
        assert r.get("hash"), "trigger did not stamp a hash"
        assert r.get("chain_seq") is not None
    created = next(r for r in rows if r["event_type"] == "contract_review_created")
    submitted = next(r for r in rows if r["event_type"] == "contract_review_in_review")
    assert submitted["prev_hash"] == created["hash"]
    assert submitted["chain_seq"] > created["chain_seq"]


def test_coordination_engine_drives_declared_type_end_to_end(db):
    _box(db, box_id="CR-6")
    engine = CoordinationEngine(db=db, organization_id=ORG)
    plan = Plan(
        event_type="contract_decision",
        actions=[
            Action("move_box_stage", "DET", {"target": "in_review", "actor_id": "legal"}),
            Action("move_box_stage", "DET", {"target": "approved", "actor_id": "gc"}),
        ],
        box_id="CR-6",
        box_type="contract_review",
        organization_id=ORG,
    )
    result = asyncio.run(engine.execute(plan))
    assert result.status == "completed"
    loaded = box_registry.get_box("contract_review", "CR-6", db)
    assert loaded["state"] == "approved"


def test_box_summary_surfaces_declared_fields(db):
    from solden.core.box_summary import build_box_summary
    _box(db, box_id="CR-sum")
    summary = build_box_summary("CR-sum", db=db, box_type="contract_review")
    assert summary.current_stage == "draft"
    assert summary.key_fields.get("title") == "MSA renewal"
    assert summary.key_fields.get("counterparty") == "Globex"


def test_entering_declared_exception_state_raises_box_exception(db):
    # A spec that DOES declare an exception_state: transitioning a Box into it
    # auto-raises a first-class box_exception (parity with how AP raises one
    # when an ap_item lands in needs_info), no bespoke Python.
    spec = WorkflowSpec(
        box_type="vendor_kyc",
        url_slug="vendor-kyc",
        states=("submitted", "in_checks", "approved", "blocked"),
        initial_state="submitted",
        terminal_states=("approved",),
        transitions={
            "submitted": {"in_checks"},
            "in_checks": {"approved", "blocked"},
            "blocked": {"in_checks"},  # recoverable, like AP's needs_info
        },
        action_states={
            "start": "in_checks", "approve": "approved",
            "block": "blocked", "recheck": "in_checks",
        },
        fields=("vendor_name",),
        exception_state="blocked",
    )
    workflow_spec.register_spec(spec)
    try:
        box_registry.create_box("vendor_kyc", {
            "id": "VK-1", "organization_id": ORG, "vendor_name": "Sketchy LLC",
        }, db)
        box_registry.update_box("vendor_kyc", "VK-1", db, state="in_checks", actor_id="ops")
        box_registry.update_box(
            "vendor_kyc", "VK-1", db, state="blocked",
            actor_id="ops", reason="sanctions hit",
        )
        excs = db.list_box_exceptions(box_id="VK-1", box_type="vendor_kyc")
        assert any(e.get("exception_type") == "vendor_kyc_exception" for e in excs)
        # exception_state is a real state the Box moves into (not a veto).
        loaded = box_registry.get_box("vendor_kyc", "VK-1", db)
        assert loaded["state"] == "blocked"
    finally:
        workflow_spec.unregister_spec("vendor_kyc")


def test_engine_exception_path_for_typeless_stall_state(db):
    # contract_review has exception_state=None; an illegal move must raise a
    # box_exception, NOT attempt the illegal state move.
    _box(db, box_id="CR-7")
    engine = CoordinationEngine(db=db, organization_id=ORG)
    plan = Plan(
        event_type="contract_decision",
        actions=[Action("move_box_stage", "DET", {"target": "approved", "actor_id": "gc"})],
        box_id="CR-7",
        box_type="contract_review",
        organization_id=ORG,
    )
    result = asyncio.run(engine.execute(plan))
    assert result.status == "failed"   # draft->approved is illegal (must pass in_review)
    loaded = box_registry.get_box("contract_review", "CR-7", db)
    assert loaded["state"] == "draft"  # unchanged
    excs = db.list_box_exceptions(box_id="CR-7", box_type="contract_review")
    assert any(e.get("exception_type") == "action_failed" for e in excs)
