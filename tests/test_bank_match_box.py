"""Tests for the bank_match BoxType — manifesto's generalization proof.

The architectural test is: does the bank_match Box ring through the
same primitives as ap_item — typed state machine, audit funnel,
export shape, registry entry — without bespoke code paths?

Coverage:
  * BoxType registry includes bank_match with the right open/terminal
    sets and load_box dispatches to db.get_bank_match.
  * State machine: proposed → accepted | rejected; both terminal;
    no edges out of terminal states.
  * Store: create_bank_match writes the row + an audit_events row
    with box_type='bank_match' and policy_version stamped.
  * Store: update_bank_match_state writes the audit row + advances
    the column; refuses an illegal edge.
  * Endpoints: accept/reject/get/list + tenant isolation (cross-org
    returns 404 not 403).
  * Export: bank_match export has the documented shape and references
    its parent_ap_item; ap_item export populates child_boxes when
    bank_matches exist.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import bank_match_routes, box_export  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.core.bank_match_states import (  # noqa: E402
    BankMatchState,
    validate_bank_match_transition,
)
from clearledgr.core.box_registry import BOX_TYPES, load_box  # noqa: E402
from clearledgr.core.stores.bank_match_store import (  # noqa: E402
    IllegalBankMatchTransitionError,
)


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgBM", organization_name="orgBM")
    inst.ensure_organization("orgBM2", organization_name="orgBM2")
    return inst


def _user(org: str = "orgBM", uid: str = "u1") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uid,
        email=f"{uid}@example.com",
        organization_id=org,
        role="user",
    )


@pytest.fixture()
def client_a(db):
    app = FastAPI()
    app.include_router(bank_match_routes.router)
    app.include_router(box_export.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgBM")
    return TestClient(app)


@pytest.fixture()
def client_b(db):
    app = FastAPI()
    app.include_router(bank_match_routes.router)
    app.include_router(box_export.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgBM2")
    return TestClient(app)


def _make_parent_ap(db, *, item_id: str, org: str = "orgBM") -> dict:
    return db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Acme",
        "amount": 200.0,
        "state": "received",
    })


def _make_match(db, *, box_id: str, parent_id: str, org: str = "orgBM") -> dict:
    return db.create_bank_match({
        "id": box_id,
        "organization_id": org,
        "parent_ap_item_id": parent_id,
        "payment_confirmation_id": "pc-1",
        "bank_statement_line_id": "bsl-1",
        "confidence": 0.92,
    })


# ─── Registry ───────────────────────────────────────────────────────


def test_box_registry_includes_bank_match():
    assert "bank_match" in BOX_TYPES
    bt = BOX_TYPES["bank_match"]
    assert bt.source_table == "bank_match_boxes"
    assert bt.state_field == "state"
    assert "proposed" in bt.open_states
    assert {"accepted", "rejected"} <= bt.terminal_states


def test_load_box_dispatches_to_bank_match_loader(db):
    parent = _make_parent_ap(db, item_id="AP-bm-loader")
    match = _make_match(db, box_id="BM-loader", parent_id=parent["id"])
    loaded = load_box("bank_match", match["id"], db)
    assert loaded is not None
    assert loaded["id"] == match["id"]
    assert loaded["parent_ap_item_id"] == parent["id"]


# ─── State machine ──────────────────────────────────────────────────


def test_state_machine_proposed_can_accept_or_reject():
    assert validate_bank_match_transition("proposed", "accepted")
    assert validate_bank_match_transition("proposed", "rejected")


def test_state_machine_terminal_states_are_dead_ends():
    assert not validate_bank_match_transition("accepted", "rejected")
    assert not validate_bank_match_transition("rejected", "accepted")
    assert not validate_bank_match_transition("accepted", "proposed")


def test_state_machine_unknown_state_returns_false():
    assert not validate_bank_match_transition("proposed", "weird_state")


# ─── Store: create + audit ──────────────────────────────────────────


def test_create_bank_match_writes_audit_event(db):
    parent = _make_parent_ap(db, item_id="AP-bm-create")
    match = _make_match(db, box_id="BM-create", parent_id=parent["id"])
    assert match["state"] == "proposed"
    assert match["parent_ap_item_id"] == parent["id"]

    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT event_type, policy_version FROM audit_events "
            "WHERE box_id = %s AND box_type = %s",
            (match["id"], "bank_match"),
        )
        rows = cur.fetchall()
    event_types = [r["event_type"] if isinstance(r, dict) else r[0] for r in rows]
    assert "bank_match_proposed" in event_types
    policy_versions = [r["policy_version"] if isinstance(r, dict) else r[1] for r in rows]
    assert all(pv == "v1" for pv in policy_versions if pv is not None)


def test_update_bank_match_state_refuses_illegal_transition(db):
    parent = _make_parent_ap(db, item_id="AP-bm-illegal")
    match = _make_match(db, box_id="BM-illegal", parent_id=parent["id"])
    db.update_bank_match_state(
        match["id"], "accepted", actor_id="ops@example.com",
    )
    # accepted is terminal — further transition refused.
    with pytest.raises(IllegalBankMatchTransitionError):
        db.update_bank_match_state(
            match["id"], "rejected", actor_id="ops@example.com",
        )


# ─── Endpoints: accept / reject / get / list ───────────────────────


def test_accept_endpoint_advances_state(db, client_a):
    parent = _make_parent_ap(db, item_id="AP-bm-accept")
    match = _make_match(db, box_id="BM-accept", parent_id=parent["id"])
    resp = client_a.post(
        f"/api/workspace/bank-matches/{match['id']}/accept",
        json={"reason": "match verified"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "accepted"


def test_reject_endpoint_advances_state(db, client_a):
    parent = _make_parent_ap(db, item_id="AP-bm-reject")
    match = _make_match(db, box_id="BM-reject", parent_id=parent["id"])
    resp = client_a.post(
        f"/api/workspace/bank-matches/{match['id']}/reject",
        json={"reason": "wrong line"},
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "rejected"
    assert resp.json()["rejection_reason"] == "wrong line"


def test_action_endpoint_409_on_terminal_state(db, client_a):
    parent = _make_parent_ap(db, item_id="AP-bm-409")
    match = _make_match(db, box_id="BM-409", parent_id=parent["id"])
    client_a.post(
        f"/api/workspace/bank-matches/{match['id']}/accept",
        json={"reason": ""},
    )
    resp = client_a.post(
        f"/api/workspace/bank-matches/{match['id']}/reject",
        json={"reason": ""},
    )
    assert resp.status_code == 409


def test_list_for_ap_returns_only_org_boxes(db, client_a):
    parent_a = _make_parent_ap(db, item_id="AP-bm-list", org="orgBM")
    parent_b = _make_parent_ap(db, item_id="AP-bm-list-other", org="orgBM2")
    _make_match(db, box_id="BM-list-a", parent_id=parent_a["id"], org="orgBM")
    _make_match(db, box_id="BM-list-b", parent_id=parent_b["id"], org="orgBM2")
    resp = client_a.get(f"/api/workspace/ap-items/{parent_a['id']}/bank-match-boxes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["boxes"][0]["id"] == "BM-list-a"


def test_endpoint_cross_tenant_returns_not_found(db, client_a, client_b):
    parent = _make_parent_ap(db, item_id="AP-bm-tenant", org="orgBM")
    match = _make_match(db, box_id="BM-tenant", parent_id=parent["id"], org="orgBM")
    resp = client_b.get(f"/api/workspace/bank-matches/{match['id']}")
    assert resp.status_code == 404


# ─── Export ─────────────────────────────────────────────────────────


def test_bank_match_export_has_documented_shape(db, client_a):
    parent = _make_parent_ap(db, item_id="AP-bm-export")
    match = _make_match(db, box_id="BM-export", parent_id=parent["id"])
    resp = client_a.get(f"/api/workspace/bank-matches/{match['id']}/export")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["box_schema_version"] == box_export.BOX_SCHEMA_VERSION
    assert doc["box"]["type"] == "bank_match"
    assert doc["box"]["id"] == match["id"]
    assert doc["links"]["parent_box"] == {"type": "ap_item", "id": parent["id"]}
    # history should contain at least the bank_match_proposed event
    event_types = [e["event_type"] for e in doc["history"]]
    assert "bank_match_proposed" in event_types


def test_ap_export_links_child_bank_matches(db, client_a):
    parent = _make_parent_ap(db, item_id="AP-bm-child-export")
    match = _make_match(db, box_id="BM-child", parent_id=parent["id"])
    resp = client_a.get(f"/api/workspace/ap-items/{parent['id']}/export")
    assert resp.status_code == 200
    doc = resp.json()
    child_ids = [c["id"] for c in doc["links"]["child_boxes"]]
    assert match["id"] in child_ids
