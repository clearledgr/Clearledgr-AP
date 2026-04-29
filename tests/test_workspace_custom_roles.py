"""Tests for Module 6 Pass A — permission catalog + custom roles.

Coverage:
  * permission catalog HTTP shape (list + standard role mappings)
  * custom-role round-trip: create → list → update → delete
  * 10-per-org limit (CustomRoleLimitExceeded → 409 custom_role_limit)
  * (org, lower(name)) uniqueness (CustomRoleNameTaken → 409 name_taken)
  * unknown permission tokens silently dropped on create+update
  * cross-tenant access is 404 (no existence leak)
  * non-admin user blocked from mutations
  * audit_event emitted on create / update / delete
  * has_permission resolver: standard role + custom role override
  * permissions list cannot be empty after normalization
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

from clearledgr.api import workspace_shell as ws  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.core.permissions import (  # noqa: E402
    ALL_PERMISSIONS,
    CUSTOM_ROLES_PER_ORG_LIMIT,
    PERMISSION_APPROVE_INVOICES,
    PERMISSION_CONFIGURE_RULES,
    PERMISSION_VIEW_AUDIT_LOG,
    has_permission,
)
from clearledgr.core.stores.custom_roles_store import (  # noqa: E402
    CustomRoleLimitExceeded,
    CustomRoleNameTaken,
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


def _user(org_id: str = "default", role: str = "owner"):
    return SimpleNamespace(
        email=f"{role}@example.com",
        user_id=f"{role}-user",
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
# Permission resolver
# ---------------------------------------------------------------------------


def test_has_permission_owner_grants_everything():
    for perm in ALL_PERMISSIONS:
        assert has_permission("owner", perm) is True


def test_has_permission_read_only_blocks_mutation():
    # ROLE_READ_ONLY only carries view_audit_log + see_reports
    assert has_permission("read_only", PERMISSION_VIEW_AUDIT_LOG) is True
    assert has_permission("read_only", PERMISSION_CONFIGURE_RULES) is False
    assert has_permission("read_only", PERMISSION_APPROVE_INVOICES) is False


def test_has_permission_unknown_role_grants_nothing():
    assert has_permission("nonexistent_role", PERMISSION_CONFIGURE_RULES) is False


def test_has_permission_custom_role_overrides_standard():
    """When custom_role_permissions is supplied, the standard role
    taxonomy is bypassed — even an owner with an empty custom set
    has zero permissions on this call."""
    assert has_permission(
        "owner",
        PERMISSION_CONFIGURE_RULES,
        custom_role_permissions=frozenset(),
    ) is False
    assert has_permission(
        "read_only",
        PERMISSION_APPROVE_INVOICES,
        custom_role_permissions={PERMISSION_APPROVE_INVOICES},
    ) is True


def test_has_permission_unknown_token_returns_false():
    assert has_permission("owner", "made_up_permission") is False


# ---------------------------------------------------------------------------
# Store layer
# ---------------------------------------------------------------------------


def test_create_and_list_custom_role_round_trip(db):
    row = db.create_custom_role(
        organization_id="default",
        name="AP Reviewer",
        permissions=[PERMISSION_APPROVE_INVOICES, PERMISSION_VIEW_AUDIT_LOG],
        description="Reviews approvals only",
        created_by="owner-user",
    )
    assert row["id"].startswith("cr_")
    assert sorted(row["permissions"]) == sorted([PERMISSION_APPROVE_INVOICES, PERMISSION_VIEW_AUDIT_LOG])

    listed = db.list_custom_roles("default")
    assert any(r["id"] == row["id"] for r in listed)


def test_create_custom_role_drops_unknown_permissions(db):
    row = db.create_custom_role(
        organization_id="default",
        name="Mixed",
        permissions=["approve_invoices", "made_up", "", "another_invalid"],
    )
    # Only the one valid perm survives
    assert row["permissions"] == [PERMISSION_APPROVE_INVOICES]


def test_create_rejects_empty_permissions(db):
    with pytest.raises(ValueError):
        db.create_custom_role(
            organization_id="default",
            name="Empty",
            permissions=["all_garbage", "more_garbage"],
        )


def test_create_enforces_10_role_limit(db):
    for i in range(CUSTOM_ROLES_PER_ORG_LIMIT):
        db.create_custom_role(
            organization_id="default",
            name=f"Role {i}",
            permissions=[PERMISSION_VIEW_AUDIT_LOG],
        )
    with pytest.raises(CustomRoleLimitExceeded):
        db.create_custom_role(
            organization_id="default",
            name="One Too Many",
            permissions=[PERMISSION_VIEW_AUDIT_LOG],
        )


def test_create_enforces_org_name_uniqueness(db):
    db.create_custom_role(
        organization_id="default",
        name="Auditor",
        permissions=[PERMISSION_VIEW_AUDIT_LOG],
    )
    # Same name, different case → still rejected
    with pytest.raises(CustomRoleNameTaken):
        db.create_custom_role(
            organization_id="default",
            name="AUDITOR",
            permissions=[PERMISSION_VIEW_AUDIT_LOG],
        )


def test_same_name_allowed_across_tenants(db):
    """Each tenant has its own (org, name) uniqueness — Acme and Globex
    can both have an 'Auditor' role."""
    db.create_custom_role(
        organization_id="default",
        name="Auditor",
        permissions=[PERMISSION_VIEW_AUDIT_LOG],
    )
    other = db.create_custom_role(
        organization_id="other-tenant",
        name="Auditor",
        permissions=[PERMISSION_VIEW_AUDIT_LOG],
    )
    assert other["organization_id"] == "other-tenant"


def test_update_custom_role_preserves_id(db):
    row = db.create_custom_role(
        organization_id="default", name="X",
        permissions=[PERMISSION_VIEW_AUDIT_LOG],
    )
    updated = db.update_custom_role(
        row["id"],
        permissions=[PERMISSION_APPROVE_INVOICES, PERMISSION_VIEW_AUDIT_LOG],
        description="updated",
    )
    assert updated["id"] == row["id"]
    assert sorted(updated["permissions"]) == sorted([PERMISSION_APPROVE_INVOICES, PERMISSION_VIEW_AUDIT_LOG])
    assert updated["description"] == "updated"


def test_update_with_empty_permissions_raises(db):
    row = db.create_custom_role(
        organization_id="default", name="X",
        permissions=[PERMISSION_VIEW_AUDIT_LOG],
    )
    with pytest.raises(ValueError):
        db.update_custom_role(row["id"], permissions=[])


def test_delete_custom_role(db):
    row = db.create_custom_role(
        organization_id="default", name="X",
        permissions=[PERMISSION_VIEW_AUDIT_LOG],
    )
    assert db.delete_custom_role(row["id"]) is True
    assert db.get_custom_role(row["id"]) is None
    assert db.delete_custom_role(row["id"]) is False  # already gone


def test_resolve_custom_role_permissions(db):
    row = db.create_custom_role(
        organization_id="default", name="X",
        permissions=[PERMISSION_APPROVE_INVOICES, PERMISSION_VIEW_AUDIT_LOG],
    )
    perms = db.resolve_custom_role_permissions(row["id"])
    assert PERMISSION_APPROVE_INVOICES in perms
    # Unknown ids return frozenset() — never raise
    assert db.resolve_custom_role_permissions("cr_nonexistent") == frozenset()
    assert db.resolve_custom_role_permissions(None) == frozenset()
    # Standard role tokens (not "cr_") collapse to frozenset() so the
    # resolver can be called uniformly.
    assert db.resolve_custom_role_permissions("owner") == frozenset()


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


def test_permission_catalog_endpoint(db, client_factory):
    client = client_factory(_user)
    resp = client.get("/api/workspace/permissions/catalog")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["custom_role_limit"] == CUSTOM_ROLES_PER_ORG_LIMIT
    keys = {p["key"] for p in body["permissions"]}
    assert keys == ALL_PERMISSIONS
    assert "owner" in body["standard_roles"]
    assert PERMISSION_VIEW_AUDIT_LOG in body["standard_roles"]["read_only"]


def test_create_custom_role_endpoint_returns_row_and_emits_audit(db, client_factory):
    client = client_factory(_user)
    resp = client.post(
        "/api/workspace/roles/custom?organization_id=default",
        json={
            "name": "AP Lead",
            "description": "Approves + sees audit",
            "permissions": [PERMISSION_APPROVE_INVOICES, PERMISSION_VIEW_AUDIT_LOG],
        },
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()
    assert row["name"] == "AP Lead"
    assert sorted(row["permissions"]) == sorted([PERMISSION_APPROVE_INVOICES, PERMISSION_VIEW_AUDIT_LOG])

    # Audit event recorded
    events = db.search_audit_events(organization_id="default", event_types=["custom_role_created"])
    assert any(e.get("box_id") == row["id"] for e in events.get("events", []))


def test_create_custom_role_blocked_for_non_admin(db, client_factory):
    """ap_clerk role does NOT pass _require_admin — 403."""
    client = client_factory(lambda: _user(role="ap_clerk"))
    resp = client.post(
        "/api/workspace/roles/custom?organization_id=default",
        json={"name": "X", "permissions": [PERMISSION_VIEW_AUDIT_LOG]},
    )
    assert resp.status_code == 403


def test_create_custom_role_409_on_limit(db, client_factory):
    for i in range(CUSTOM_ROLES_PER_ORG_LIMIT):
        db.create_custom_role(
            organization_id="default", name=f"R{i}",
            permissions=[PERMISSION_VIEW_AUDIT_LOG],
        )
    client = client_factory(_user)
    resp = client.post(
        "/api/workspace/roles/custom?organization_id=default",
        json={"name": "Overflow", "permissions": [PERMISSION_VIEW_AUDIT_LOG]},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "custom_role_limit"


def test_create_custom_role_409_on_name_collision(db, client_factory):
    db.create_custom_role(
        organization_id="default", name="Dupe",
        permissions=[PERMISSION_VIEW_AUDIT_LOG],
    )
    client = client_factory(_user)
    resp = client.post(
        "/api/workspace/roles/custom?organization_id=default",
        json={"name": "DUPE", "permissions": [PERMISSION_VIEW_AUDIT_LOG]},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "name_taken"


def test_create_custom_role_422_on_empty_permissions(db, client_factory):
    client = client_factory(_user)
    resp = client.post(
        "/api/workspace/roles/custom?organization_id=default",
        json={"name": "Empty", "permissions": ["nonsense", "garbage"]},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "validation_failed"


def test_update_custom_role_emits_diff(db, client_factory):
    row = db.create_custom_role(
        organization_id="default", name="Reviewer",
        permissions=[PERMISSION_VIEW_AUDIT_LOG],
    )
    client = client_factory(_user)
    resp = client.put(
        f"/api/workspace/roles/custom/{row['id']}?organization_id=default",
        json={"permissions": [PERMISSION_APPROVE_INVOICES]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["permissions"] == [PERMISSION_APPROVE_INVOICES]

    events = db.search_audit_events(
        organization_id="default", event_types=["custom_role_updated"],
    )
    matching = [e for e in events.get("events", []) if e.get("box_id") == row["id"]]
    assert matching, "expected audit event for update"
    payload = matching[0].get("payload_json") or {}
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    assert PERMISSION_APPROVE_INVOICES in (payload.get("permissions_added") or [])
    assert PERMISSION_VIEW_AUDIT_LOG in (payload.get("permissions_removed") or [])


def test_update_cross_tenant_returns_404(db, client_factory):
    """A role owned by another tenant must look indistinguishable from
    a missing role — 404, never 403, to avoid existence leaks."""
    other_row = db.create_custom_role(
        organization_id="other-tenant", name="Theirs",
        permissions=[PERMISSION_VIEW_AUDIT_LOG],
    )
    client = client_factory(_user)
    resp = client.put(
        f"/api/workspace/roles/custom/{other_row['id']}?organization_id=default",
        json={"name": "Mine"},
    )
    assert resp.status_code == 404


def test_delete_custom_role_endpoint(db, client_factory):
    row = db.create_custom_role(
        organization_id="default", name="Temp",
        permissions=[PERMISSION_VIEW_AUDIT_LOG],
    )
    client = client_factory(_user)
    resp = client.delete(
        f"/api/workspace/roles/custom/{row['id']}?organization_id=default"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    assert db.get_custom_role(row["id"]) is None

    events = db.search_audit_events(
        organization_id="default", event_types=["custom_role_deleted"],
    )
    assert any(e.get("box_id") == row["id"] for e in events.get("events", []))


def test_list_custom_roles_blocked_cross_tenant(db, client_factory):
    """list_custom_roles uses _resolve_org_id, so passing
    organization_id=other-tenant from a default-tenant user is 403."""
    client = client_factory(_user)
    resp = client.get("/api/workspace/roles/custom?organization_id=other-tenant")
    assert resp.status_code == 403
