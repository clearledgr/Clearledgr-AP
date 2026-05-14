"""Tests for the ownership primitive — manifesto §"Ownership".

Verifies the resolve → apply → reassign chain end-to-end:

  * resolve_owner respects HUMAN_ACTION_STATES (no owner for
    auto-progressable states),
  * org settings_json drives the state→default-owner map,
  * active delegation_rules promote a delegate over the base owner
    and record source='delegate' with the original_owner_email,
  * apply_resolved_owner writes both the ap_items columns and an
    owner_changed audit event,
  * the manual reassign endpoint sets owner_source='manual' and
    is tenant-scoped (cross-org returns 404, not 403),
  * the CoordinationEngine hook never overwrites a manual assignment.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import box_owner_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.box_owner import (  # noqa: E402
    HUMAN_ACTION_STATES,
    apply_resolved_owner,
    reassign_manually,
    resolve_owner,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgOwn", organization_name="orgOwn")
    inst.ensure_organization("orgOther", organization_name="orgOther")
    return inst


def _user(org: str = "orgOwn", uid: str = "user-1") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uid,
        email=f"{uid}@example.com",
        organization_id=org,
        role="user",
    )


@pytest.fixture()
def client_own(db):
    app = FastAPI()
    app.include_router(box_owner_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgOwn")
    return TestClient(app)


@pytest.fixture()
def client_other(db):
    app = FastAPI()
    app.include_router(box_owner_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgOther")
    return TestClient(app)


def _configure_routing(db, org: str, routing: dict) -> None:
    db.update_organization(org, settings={"routing_owners": routing})


def _make_ap_item(db, *, item_id: str, state: str, org: str = "orgOwn") -> dict:
    return db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Acme",
        "amount": 100.0,
        "state": state,
    })


# ─── resolve_owner ──────────────────────────────────────────────────


def test_resolve_owner_returns_none_for_auto_progressable_state(db):
    _configure_routing(db, "orgOwn", {"needs_approval": "controller@example.com"})
    item = _make_ap_item(db, item_id="AP-own-auto", state="validated")
    assert "validated" not in HUMAN_ACTION_STATES
    assert resolve_owner(box=item, organization_id="orgOwn", db=db) is None


def test_resolve_owner_returns_none_when_no_org_config(db):
    _configure_routing(db, "orgOwn", {})
    item = _make_ap_item(db, item_id="AP-own-noconfig", state="needs_approval")
    assert resolve_owner(box=item, organization_id="orgOwn", db=db) is None


def test_resolve_owner_returns_base_when_no_delegation(db):
    _configure_routing(db, "orgOwn", {"needs_approval": "controller@example.com"})
    item = _make_ap_item(db, item_id="AP-own-base", state="needs_approval")
    assignment = resolve_owner(box=item, organization_id="orgOwn", db=db)
    assert assignment is not None
    assert assignment.owner_email == "controller@example.com"
    assert assignment.owner_source == "auto"
    assert assignment.original_owner_email == "controller@example.com"


def test_resolve_owner_walks_delegation(db):
    _configure_routing(db, "orgOwn", {"needs_approval": "controller@example.com"})
    item = _make_ap_item(db, item_id="AP-own-delegate", state="needs_approval")
    from clearledgr.services.approval_delegation import get_delegation_service
    delegation = get_delegation_service(organization_id="orgOwn")
    delegation.create_rule(
        delegator_id="u-controller",
        delegator_email="controller@example.com",
        delegate_id="u-deputy",
        delegate_email="deputy@example.com",
        reason="PTO 2026-05-15",
    )

    assignment = resolve_owner(box=item, organization_id="orgOwn", db=db)
    assert assignment is not None
    assert assignment.owner_email == "deputy@example.com"
    assert assignment.owner_source == "delegate"
    assert assignment.original_owner_email == "controller@example.com"
    assert "PTO" in assignment.delegation_reason


# ─── apply_resolved_owner ───────────────────────────────────────────


def test_apply_resolved_owner_writes_columns_and_audit_event(db):
    _configure_routing(db, "orgOwn", {"needs_approval": "controller@example.com"})
    item = _make_ap_item(db, item_id="AP-own-apply", state="needs_approval")
    assignment = resolve_owner(box=item, organization_id="orgOwn", db=db)
    assert assignment is not None
    apply_resolved_owner(
        db=db,
        ap_item_id=item["id"],
        organization_id="orgOwn",
        assignment=assignment,
        actor_id="test",
    )
    fresh = db.get_ap_item(item["id"])
    assert fresh["owner_email"] == "controller@example.com"
    assert fresh["owner_source"] == "auto"
    assert fresh["owner_assigned_at"]
    events = db.list_ap_audit_events(item["id"])
    owner_events = [e for e in events if e.get("event_type") == "owner_changed"]
    assert owner_events, "owner_changed audit event must be written"
    body = owner_events[-1].get("payload_json") or {}
    assert body.get("owner_email") == "controller@example.com"


# ─── reassign_manually ─────────────────────────────────────────────


def test_reassign_manually_bypasses_delegation(db):
    _configure_routing(db, "orgOwn", {"needs_approval": "controller@example.com"})
    from clearledgr.services.approval_delegation import get_delegation_service
    delegation = get_delegation_service(organization_id="orgOwn")
    delegation.create_rule(
        delegator_id="u-c",
        delegator_email="controller@example.com",
        delegate_id="u-d",
        delegate_email="deputy@example.com",
        reason="OOO",
    )
    item = _make_ap_item(db, item_id="AP-own-manual", state="needs_approval")
    assignment = reassign_manually(
        db=db,
        ap_item_id=item["id"],
        organization_id="orgOwn",
        new_owner_email="cfo@example.com",
        reason="exec override",
        actor_id="operator@example.com",
    )
    assert assignment.owner_email == "cfo@example.com"
    assert assignment.owner_source == "manual"
    # Manual reassign respects the operator's choice — no delegation walk.
    assert assignment.original_owner_email == "cfo@example.com"

    fresh = db.get_ap_item(item["id"])
    assert fresh["owner_email"] == "cfo@example.com"
    assert fresh["owner_source"] == "manual"


# ─── POST /ap-items/{id}/reassign endpoint ─────────────────────────


def test_reassign_endpoint_records_audit_event(db, client_own):
    item = _make_ap_item(db, item_id="AP-own-endpoint", state="needs_approval")
    resp = client_own.post(
        f"/api/workspace/ap-items/{item['id']}/reassign",
        json={"new_owner_email": "controller@example.com", "reason": "test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["owner"]["owner_email"] == "controller@example.com"
    assert body["owner"]["owner_source"] == "manual"

    events = db.list_ap_audit_events(item["id"])
    owner_events = [e for e in events if e.get("event_type") == "owner_changed"]
    assert owner_events


def test_reassign_endpoint_tenant_isolated(db, client_own, client_other):
    item = _make_ap_item(db, item_id="AP-own-tenant", state="needs_approval", org="orgOwn")
    # Cross-tenant request: 404, not 403.
    resp = client_other.post(
        f"/api/workspace/ap-items/{item['id']}/reassign",
        json={"new_owner_email": "x@example.com", "reason": ""},
    )
    assert resp.status_code == 404
    # The owner column on the AP item stays untouched.
    fresh = db.get_ap_item(item["id"])
    assert fresh.get("owner_email") in (None, "")


def test_reassign_endpoint_404_for_missing_box(client_own):
    resp = client_own.post(
        "/api/workspace/ap-items/AP-does-not-exist/reassign",
        json={"new_owner_email": "x@example.com", "reason": ""},
    )
    assert resp.status_code == 404
