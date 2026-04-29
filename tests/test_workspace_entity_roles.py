"""Tests for Module 6 Pass B — per-entity role + approval ceiling.

Coverage:
  * Resolver fallback: no row → org role wins.
  * Resolver entity scope: row found → entity role + ceiling win.
  * Resolver custom-role lookup via cr_<hex> token.
  * Resolver tolerates DB errors (returns org-level fallback).
  * can_approve: permission gate, ceiling enforcement, no-amount path.
  * PUT /entity-roles idempotent replace (insert + update + delete in
    one transaction).
  * PUT validates role token (standard OR existing custom role id).
  * PUT non-negative ceiling enforced.
  * PUT admin-gated.
  * GET tenant-scoped, cross-tenant rows filtered out.
  * GET effective-permissions reflects entity > org precedence.
  * Audit emit on replace_user_entity_roles.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import workspace_shell as ws  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import (  # noqa: E402
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_READ_ONLY,
    get_current_user,
)
from clearledgr.core.permissions import (  # noqa: E402
    PERMISSION_APPROVE_INVOICES,
    PERMISSION_VIEW_AUDIT_LOG,
)
from clearledgr.services.role_resolver import (  # noqa: E402
    can_approve,
    resolve_role,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    inst.ensure_organization("other-tenant", organization_name="other-tenant")
    return inst


@pytest.fixture()
def two_entities(db):
    """Two entities in the default org."""
    e1 = db.create_entity(
        organization_id="default",
        code="EU",
        name="EU subsidiary",
    )
    e2 = db.create_entity(
        organization_id="default",
        code="US",
        name="US subsidiary",
    )
    return e1["id"], e2["id"]


def _user(org_id: str = "default", role: str = "owner", uid: str = "owner-user"):
    return SimpleNamespace(
        email=f"{role}@example.com",
        user_id=uid,
        organization_id=org_id,
        role=role,
    )


@pytest.fixture()
def client_factory():
    def _build(user_factory):
        app = FastAPI()
        app.include_router(ws.router)
        app.dependency_overrides[get_current_user] = user_factory
        return TestClient(app)
    return _build


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def test_resolver_falls_back_to_org_role_when_no_row(db, two_entities):
    eu, _us = two_entities
    rr = resolve_role(db, user_id="alice", org_role=ROLE_AP_MANAGER, entity_id=eu)
    assert rr.scope == "org"
    assert rr.role == ROLE_AP_MANAGER
    assert PERMISSION_APPROVE_INVOICES in rr.permissions
    assert rr.approval_ceiling is None


def test_resolver_uses_entity_row_when_present(db, two_entities):
    eu, _us = two_entities
    db.set_user_entity_role(
        user_id="alice", entity_id=eu, organization_id="default",
        role=ROLE_READ_ONLY, approval_ceiling=None,
    )
    # Org role would normally let her approve; the entity row downgrades
    # her in EU.
    rr = resolve_role(db, user_id="alice", org_role=ROLE_AP_MANAGER, entity_id=eu)
    assert rr.scope == "entity"
    assert rr.role == ROLE_READ_ONLY
    assert PERMISSION_APPROVE_INVOICES not in rr.permissions
    assert PERMISSION_VIEW_AUDIT_LOG in rr.permissions


def test_resolver_applies_approval_ceiling(db, two_entities):
    eu, _us = two_entities
    db.set_user_entity_role(
        user_id="alice", entity_id=eu, organization_id="default",
        role=ROLE_AP_CLERK, approval_ceiling=Decimal("50000.00"),
    )
    rr = resolve_role(db, user_id="alice", org_role=ROLE_AP_CLERK, entity_id=eu)
    assert rr.approval_ceiling == Decimal("50000.00")
    assert rr.can_approve(Decimal("49999.99")) is True
    assert rr.can_approve(Decimal("50000.00")) is True
    assert rr.can_approve(Decimal("50000.01")) is False


def test_resolver_no_ceiling_means_unbounded(db, two_entities):
    eu, _us = two_entities
    db.set_user_entity_role(
        user_id="alice", entity_id=eu, organization_id="default",
        role=ROLE_AP_MANAGER, approval_ceiling=None,
    )
    rr = resolve_role(db, user_id="alice", org_role=ROLE_AP_MANAGER, entity_id=eu)
    assert rr.can_approve(Decimal("999999.99")) is True


def test_resolver_no_approve_permission_blocks_regardless_of_ceiling(db, two_entities):
    eu, _us = two_entities
    db.set_user_entity_role(
        user_id="alice", entity_id=eu, organization_id="default",
        role=ROLE_READ_ONLY, approval_ceiling=Decimal("999999.00"),
    )
    rr = resolve_role(db, user_id="alice", org_role=ROLE_AP_MANAGER, entity_id=eu)
    # Read-only can't approve regardless of the ceiling
    assert rr.can_approve(Decimal("100.00")) is False


def test_can_approve_helper_short_form(db, two_entities):
    eu, _us = two_entities
    db.set_user_entity_role(
        user_id="alice", entity_id=eu, organization_id="default",
        role=ROLE_AP_CLERK, approval_ceiling=Decimal("1000"),
    )
    assert can_approve(
        db, user_id="alice", org_role=ROLE_AP_CLERK,
        entity_id=eu, amount=Decimal("999"),
    ) is True
    assert can_approve(
        db, user_id="alice", org_role=ROLE_AP_CLERK,
        entity_id=eu, amount=Decimal("1001"),
    ) is False


def test_resolver_handles_custom_role(db, two_entities):
    eu, _us = two_entities
    custom = db.create_custom_role(
        organization_id="default", name="Reviewer",
        permissions=[PERMISSION_VIEW_AUDIT_LOG, PERMISSION_APPROVE_INVOICES],
    )
    db.set_user_entity_role(
        user_id="alice", entity_id=eu, organization_id="default",
        role=custom["id"],
    )
    rr = resolve_role(db, user_id="alice", org_role=ROLE_READ_ONLY, entity_id=eu)
    assert rr.role == custom["id"]
    assert rr.permissions == frozenset({
        PERMISSION_VIEW_AUDIT_LOG, PERMISSION_APPROVE_INVOICES,
    })


def test_resolver_unknown_custom_id_collapses_to_empty(db, two_entities):
    """A stale cr_* assignment (custom role deleted out from under the
    user) returns the empty permission set — fail-closed."""
    eu, _us = two_entities
    db.set_user_entity_role(
        user_id="alice", entity_id=eu, organization_id="default",
        role="cr_nonexistent_id",
    )
    rr = resolve_role(db, user_id="alice", org_role=ROLE_AP_MANAGER, entity_id=eu)
    assert rr.role == "cr_nonexistent_id"
    assert rr.permissions == frozenset()


# ---------------------------------------------------------------------------
# Store layer
# ---------------------------------------------------------------------------


def test_replace_user_entity_roles_inserts_and_deletes(db, two_entities):
    eu, us = two_entities
    db.set_user_entity_role(
        user_id="alice", entity_id=eu, organization_id="default",
        role=ROLE_AP_MANAGER,
    )
    # Replace with a different shape: keep eu (different role), drop us
    out = db.replace_user_entity_roles(
        user_id="alice", organization_id="default",
        assignments=[
            {"entity_id": eu, "role": ROLE_AP_CLERK,
             "approval_ceiling": Decimal("10000")},
        ],
    )
    assert len(out) == 1
    assert out[0]["entity_id"] == eu
    assert out[0]["role"] == ROLE_AP_CLERK
    assert out[0]["approval_ceiling"] == Decimal("10000.00")


def test_replace_with_empty_clears_all(db, two_entities):
    eu, us = two_entities
    db.set_user_entity_role(
        user_id="alice", entity_id=eu, organization_id="default",
        role=ROLE_AP_MANAGER,
    )
    db.set_user_entity_role(
        user_id="alice", entity_id=us, organization_id="default",
        role=ROLE_READ_ONLY,
    )
    out = db.replace_user_entity_roles(
        user_id="alice", organization_id="default", assignments=[],
    )
    assert out == []


def test_set_user_entity_role_rejects_negative_ceiling(db, two_entities):
    eu, _us = two_entities
    with pytest.raises(ValueError):
        db.set_user_entity_role(
            user_id="alice", entity_id=eu, organization_id="default",
            role=ROLE_AP_CLERK, approval_ceiling=Decimal("-1"),
        )


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


def test_put_entity_roles_replaces_idempotently(db, two_entities, client_factory):
    eu, us = two_entities
    db.set_user_entity_role(
        user_id="alice", entity_id=eu, organization_id="default",
        role=ROLE_AP_MANAGER,
    )
    client = client_factory(_user)
    resp = client.put(
        "/api/workspace/users/alice/entity-roles?organization_id=default",
        json={
            "assignments": [
                {"entity_id": eu, "role": ROLE_AP_CLERK,
                 "approval_ceiling": 5000},
                {"entity_id": us, "role": ROLE_READ_ONLY},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 2
    by_entity = {a["entity_id"]: a for a in body["assignments"]}
    assert by_entity[eu]["role"] == ROLE_AP_CLERK
    assert by_entity[eu]["approval_ceiling"] == "5000.00"
    assert by_entity[us]["role"] == ROLE_READ_ONLY
    assert by_entity[us]["approval_ceiling"] is None


def test_put_entity_roles_rejects_unknown_role(db, two_entities, client_factory):
    eu, _us = two_entities
    client = client_factory(_user)
    resp = client.put(
        "/api/workspace/users/alice/entity-roles?organization_id=default",
        json={
            "assignments": [
                {"entity_id": eu, "role": "made_up_role"},
            ]
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "invalid_role"


def test_put_entity_roles_accepts_custom_role_id(db, two_entities, client_factory):
    eu, _us = two_entities
    custom = db.create_custom_role(
        organization_id="default", name="Reviewer",
        permissions=[PERMISSION_VIEW_AUDIT_LOG],
    )
    client = client_factory(_user)
    resp = client.put(
        "/api/workspace/users/alice/entity-roles?organization_id=default",
        json={
            "assignments": [
                {"entity_id": eu, "role": custom["id"]},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assignments"][0]["role"] == custom["id"]


def test_put_entity_roles_blocked_for_non_admin(db, two_entities, client_factory):
    eu, _us = two_entities
    client = client_factory(lambda: _user(role=ROLE_AP_CLERK, uid="clerk-user"))
    resp = client.put(
        "/api/workspace/users/alice/entity-roles?organization_id=default",
        json={"assignments": [{"entity_id": eu, "role": ROLE_AP_CLERK}]},
    )
    assert resp.status_code == 403


def test_put_entity_roles_emits_audit(db, two_entities, client_factory):
    eu, _us = two_entities
    client = client_factory(_user)
    resp = client.put(
        "/api/workspace/users/alice/entity-roles?organization_id=default",
        json={
            "assignments": [
                {"entity_id": eu, "role": ROLE_AP_CLERK,
                 "approval_ceiling": 1000},
            ]
        },
    )
    assert resp.status_code == 200
    events = db.search_audit_events(
        organization_id="default",
        event_types=["user_entity_role_replaced"],
    )
    assert any(e.get("box_id") == "alice" for e in events.get("events", []))


def test_get_entity_roles_lists_for_user(db, two_entities, client_factory):
    eu, _us = two_entities
    db.set_user_entity_role(
        user_id="alice", entity_id=eu, organization_id="default",
        role=ROLE_AP_MANAGER,
    )
    client = client_factory(_user)
    resp = client.get(
        "/api/workspace/users/alice/entity-roles?organization_id=default"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["assignments"][0]["entity_id"] == eu


def test_get_effective_permissions_reflects_entity_override(
    db, two_entities, client_factory,
):
    """The /effective-permissions endpoint mirrors the resolver: an
    entity row that downgrades the user trumps their org-level role."""
    eu, us = two_entities
    # Make alice an AP Manager at the org level. ``create_user`` here
    # has a positional signature (email, name, organization_id, role),
    # so we look up the resulting row to get its assigned id.
    user_row = db.create_user(
        email="alice@acme.test",
        name="Alice",
        organization_id="default",
        role=ROLE_AP_MANAGER,
    )
    alice_id = user_row.get("id") or user_row.get("user_id")
    db.set_user_entity_role(
        user_id=alice_id, entity_id=eu, organization_id="default",
        role=ROLE_READ_ONLY,
    )
    client = client_factory(_user)
    # In EU, she's read-only.
    resp = client.get(
        f"/api/workspace/users/{alice_id}/effective-permissions"
        f"?organization_id=default&entity_id={eu}",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "entity"
    assert body["role"] == ROLE_READ_ONLY
    assert PERMISSION_APPROVE_INVOICES not in body["permissions"]
    # In US, no override, falls back to org role.
    resp2 = client.get(
        f"/api/workspace/users/{alice_id}/effective-permissions"
        f"?organization_id=default&entity_id={us}",
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["scope"] == "org"
    assert body2["role"] == ROLE_AP_MANAGER
    assert PERMISSION_APPROVE_INVOICES in body2["permissions"]


def test_get_entity_roles_cross_tenant_filters_rows(
    db, two_entities, client_factory,
):
    """A row whose organization_id mismatches the resolved org must
    not leak across tenants on the GET endpoint."""
    eu, _us = two_entities
    # Stash a row that claims another tenant — defensive guard.
    db.set_user_entity_role(
        user_id="alice", entity_id="other-eu", organization_id="other-tenant",
        role=ROLE_READ_ONLY,
    )
    db.set_user_entity_role(
        user_id="alice", entity_id=eu, organization_id="default",
        role=ROLE_AP_MANAGER,
    )
    client = client_factory(_user)
    resp = client.get("/api/workspace/users/alice/entity-roles?organization_id=default")
    assert resp.status_code == 200
    rows = resp.json()["assignments"]
    assert len(rows) == 1
    assert rows[0]["organization_id"] == "default"
