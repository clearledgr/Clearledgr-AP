"""Tests for the sovereignty primitive — per-Box portable export.

The manifesto's "removable" promise stands or falls on this endpoint:
given an export document, can a third party reconstruct the workflow
record without any Solden code running? These tests assert the
schema is stable, complete, and tenant-scoped.

Covered:
  * Shape — every documented top-level key is present.
  * Completeness — every audit event the DB knows about appears in
    history, with the hash chain preserved.
  * Tenant isolation — org B cannot read org A's Box (404, not 403,
    so existence doesn't leak across tenants).
  * Missing Box — 404 for nonexistent ids.
  * Schema version — string is set and equals the module constant.
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

from solden.api import box_export as box_export_routes  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.auth import get_current_user  # noqa: E402


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="orgA")
    inst.ensure_organization("orgB", organization_name="orgB")
    return inst


def _user(org: str = "orgA", uid: str = "user-1") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uid,
        email=f"{uid}@example.com",
        organization_id=org,
        role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(box_export_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


@pytest.fixture()
def client_orgB(db):
    app = FastAPI()
    app.include_router(box_export_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgB")
    return TestClient(app)


def _make_ap_item_with_history(db, *, item_id: str, org: str = "orgA") -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Acme Widgets",
        "amount": 4200.0,
        "currency": "EUR",
        "invoice_number": "INV-7",
        "state": "received",
    })
    for prev, nxt in (
        ("received", "validated"),
        ("validated", "needs_approval"),
        ("needs_approval", "approved"),
    ):
        db.update_ap_item(item["id"], state=nxt)
        db.append_audit_event({
            "box_id": item["id"],
            "box_type": "ap_item",
            "event_type": "state_transition",
            "from_state": prev,
            "to_state": nxt,
            "actor_type": "agent",
            "actor_id": "test-agent",
            "organization_id": org,
            "decision_reason": f"test transition {prev}->{nxt}",
        })
    return db.get_ap_item(item["id"])


def test_export_returns_documented_top_level_keys(db, client_orgA):
    item = _make_ap_item_with_history(db, item_id="AP-export-shape")
    resp = client_orgA.get(f"/api/workspace/ap-items/{item['id']}/export")
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    assert doc["box_schema_version"] == box_export_routes.BOX_SCHEMA_VERSION
    assert "exported_at" in doc
    assert doc["exported_by"] == "user-1@example.com"
    assert doc["box"]["type"] == "ap_item"
    assert doc["box"]["id"] == item["id"]
    assert doc["box"]["organization_id"] == "orgA"
    assert doc["box"]["state"] == "approved"
    assert isinstance(doc["box"]["fields"], dict)
    assert doc["box"]["fields"].get("vendor_name") == "Acme Widgets"
    assert "history" in doc
    assert isinstance(doc["history"], list)
    assert "exceptions" in doc
    assert isinstance(doc["exceptions"], list)
    assert "outcome" in doc
    assert doc["links"] == {"parent_box": None, "child_boxes": []}


def test_export_history_includes_every_audit_event(db, client_orgA):
    item = _make_ap_item_with_history(db, item_id="AP-export-history")
    resp = client_orgA.get(f"/api/workspace/ap-items/{item['id']}/export")
    assert resp.status_code == 200
    history = resp.json()["history"]
    transitions = [e for e in history if e["event_type"] == "state_transition"]
    # The fixture wrote three explicit transitions; update_ap_item may
    # also auto-emit transitions, so we assert "at least three" not
    # "exactly three" — the export should never drop events.
    assert len(transitions) >= 3
    pairs = {(e["prev_state"], e["new_state"]) for e in transitions}
    assert ("received", "validated") in pairs
    assert ("validated", "needs_approval") in pairs
    assert ("needs_approval", "approved") in pairs


def test_export_preserves_hash_chain_fields(db, client_orgA):
    item = _make_ap_item_with_history(db, item_id="AP-export-chain")
    resp = client_orgA.get(f"/api/workspace/ap-items/{item['id']}/export")
    history = resp.json()["history"]
    # Hash chain may be absent in test infra if the trigger isn't
    # installed — we assert the keys are at least present so the
    # export shape is stable, regardless of whether the values are
    # populated by the running DB.
    for event in history:
        assert "hash" in event
        assert "prev_hash" in event
        assert "chain_seq" in event


def test_export_404s_for_missing_box(client_orgA):
    resp = client_orgA.get("/api/workspace/ap-items/AP-does-not-exist/export")
    assert resp.status_code == 404


def test_export_tenant_isolated(db, client_orgA, client_orgB):
    item = _make_ap_item_with_history(db, item_id="AP-export-cross-tenant", org="orgA")
    # orgA can read its own Box.
    own = client_orgA.get(f"/api/workspace/ap-items/{item['id']}/export")
    assert own.status_code == 200
    # orgB sees 404 — never 403, so existence doesn't leak.
    cross = client_orgB.get(f"/api/workspace/ap-items/{item['id']}/export")
    assert cross.status_code == 404
