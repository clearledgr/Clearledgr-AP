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
    assert list(assignment.delegation_chain) == ["deputy@example.com"]


def test_resolve_owner_walks_multi_hop_delegation_chain(db):
    """A→B→C should deliver work to C, not to B (the previous bug)."""
    _configure_routing(db, "orgOwn", {"needs_approval": "a@example.com"})
    item = _make_ap_item(db, item_id="AP-own-multi-hop", state="needs_approval")
    from clearledgr.services.approval_delegation import get_delegation_service
    delegation = get_delegation_service(organization_id="orgOwn")
    delegation.create_rule(
        delegator_id="u-a", delegator_email="a@example.com",
        delegate_id="u-b", delegate_email="b@example.com",
        reason="A on leave",
    )
    delegation.create_rule(
        delegator_id="u-b", delegator_email="b@example.com",
        delegate_id="u-c", delegate_email="c@example.com",
        reason="B also on leave",
    )
    assignment = resolve_owner(box=item, organization_id="orgOwn", db=db)
    assert assignment is not None
    assert assignment.owner_email == "c@example.com"
    assert assignment.original_owner_email == "a@example.com"
    assert list(assignment.delegation_chain) == ["b@example.com", "c@example.com"]
    # Reason of the last hop wins — auditors see why work landed at its
    # final destination.
    assert "B also" in assignment.delegation_reason


def test_resolve_owner_breaks_delegation_cycle(db):
    """A→B→A is a cycle. Walk must stop at B and not infinite-loop."""
    _configure_routing(db, "orgOwn", {"needs_approval": "a2@example.com"})
    item = _make_ap_item(db, item_id="AP-own-cycle", state="needs_approval")
    from clearledgr.services.approval_delegation import get_delegation_service
    delegation = get_delegation_service(organization_id="orgOwn")
    delegation.create_rule(
        delegator_id="u-a2", delegator_email="a2@example.com",
        delegate_id="u-b2", delegate_email="b2@example.com",
        reason="A2 OOO",
    )
    delegation.create_rule(
        delegator_id="u-b2", delegator_email="b2@example.com",
        delegate_id="u-a2", delegate_email="a2@example.com",
        reason="B2 returning the favor",
    )
    assignment = resolve_owner(box=item, organization_id="orgOwn", db=db)
    assert assignment is not None
    # Stop at B — never circle back to A.
    assert assignment.owner_email == "b2@example.com"
    assert assignment.original_owner_email == "a2@example.com"
    assert list(assignment.delegation_chain) == ["b2@example.com"]


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


def test_apply_resolved_owner_is_atomic_on_audit_failure(db, monkeypatch):
    """If the audit INSERT fails, the ap_items UPDATE must also roll back.

    The atomicity property: both writes commit together or neither
    does. The test targets the failure on the audit INSERT
    specifically — matching the SQL prefix ``INSERT INTO audit_events``
    — rather than counting execute() calls, because
    ``set_ap_item_owner_atomic`` runs an entity_id SELECT before the
    transaction begins. Counting executes would silently fire on the
    SELECT or the UPDATE, proving a weaker property than the one we
    care about (a failed audit INSERT specifically).
    """
    _configure_routing(db, "orgOwn", {"needs_approval": "controller@example.com"})
    item = _make_ap_item(db, item_id="AP-own-atomic", state="needs_approval")
    pre = db.get_ap_item(item["id"])
    assert pre.get("owner_email") is None, (
        "fixture seeded owner_email — atomicity post-condition would be ambiguous"
    )

    real_connect = db.connect
    triggered = {"audit_insert": False}

    class _AuditFailingCursor:
        def __init__(self, real_cursor):
            self._real = real_cursor

        def execute(self, sql, params=None):
            # Fail ONLY on the audit_events INSERT. Anything else
            # (the AP item UPDATE, the entity_id SELECT, etc.) runs
            # normally so we test atomicity of the actual write pair.
            sql_text = str(sql or "").lstrip()
            if sql_text.upper().startswith("INSERT INTO AUDIT_EVENTS"):
                triggered["audit_insert"] = True
                raise RuntimeError("simulated audit INSERT failure")
            return (
                self._real.execute(sql, params)
                if params is not None
                else self._real.execute(sql)
            )

        def __getattr__(self, name):
            return getattr(self._real, name)

    class _WrappedConn:
        def __init__(self, real_conn):
            self._real = real_conn

        def cursor(self):
            return _AuditFailingCursor(self._real.cursor())

        def __getattr__(self, name):
            return getattr(self._real, name)

    class _ConnCtx:
        def __init__(self, ctx):
            self._ctx = ctx

        def __enter__(self):
            conn = self._ctx.__enter__()
            return _WrappedConn(conn)

        def __exit__(self, *args):
            return self._ctx.__exit__(*args)

    def _patched_connect():
        return _ConnCtx(real_connect())

    # resolve_owner runs DB reads — let it run against the real
    # connect before we install the patch.
    assignment = resolve_owner(box=item, organization_id="orgOwn", db=db)
    assert assignment is not None

    monkeypatch.setattr(db, "connect", _patched_connect)
    with pytest.raises(RuntimeError, match="simulated audit INSERT failure"):
        apply_resolved_owner(
            db=db,
            ap_item_id=item["id"],
            organization_id="orgOwn",
            assignment=assignment,
            actor_id="test",
        )
    monkeypatch.setattr(db, "connect", real_connect)

    assert triggered["audit_insert"], (
        "audit_events INSERT never fired — the patch missed the call path; "
        "the test would have silently passed without exercising atomicity"
    )

    post = db.get_ap_item(item["id"])
    # Atomicity: owner columns unchanged after the audit INSERT
    # rolled back. With the old two-transaction implementation,
    # owner_* would have committed before the audit INSERT raised.
    assert post.get("owner_email") is None, (
        f"owner_email leaked through a failed-audit transaction: {post.get('owner_email')!r}"
    )
    assert post.get("owner_source") is None
    # The audit_events table must also have no owner_changed event
    # for this Box — the INSERT was rolled back as part of the same
    # transaction.
    events = db.list_ap_audit_events(item["id"])
    owner_events = [e for e in events if e.get("event_type") == "owner_changed"]
    assert not owner_events, (
        f"audit row leaked through a failed-INSERT transaction: {owner_events!r}"
    )


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


# ─── Sticky-manual doctrine (state-class semantics) ─────────────────


def test_state_class_groups_approval_states():
    """needs_approval and needs_second_approval share the approval class."""
    from clearledgr.services.box_owner import state_class
    assert state_class("needs_approval") == "approval"
    assert state_class("needs_second_approval") == "approval"
    assert state_class("needs_info") == "info"
    assert state_class("failed_post") == "post"
    # Auto-progressable states have no class — no manual owner persists there.
    assert state_class("validated") == ""
    assert state_class("approved") == ""
    assert state_class("") == ""


def test_state_class_partitions_human_action_states():
    """Different state classes must not collide; this is what makes
    cross-class transitions detectable."""
    from clearledgr.services.box_owner import state_class
    assert state_class("needs_info") != state_class("needs_approval")
    assert state_class("needs_info") != state_class("failed_post")
    assert state_class("needs_approval") != state_class("failed_post")
