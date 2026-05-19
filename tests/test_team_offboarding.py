"""Tests for Module 6 — user offboarding (deactivate / reactivate).

Pinned by these tests:

  - Auth-layer enforcement: a deactivated user's next request fails
    with 403 user_deactivated. The reconciliation in
    auth._reconcile_token_data is what makes this work — without it,
    deactivation would only stop new logins.
  - API keys auto-revoke: deactivating a user revokes every active
    API key they own so the X-API-Key header path also fails. The
    spec's "30 seconds across all surfaces" is a closed loop only if
    both auth surfaces revoke.
  - Self-protection: an admin cannot deactivate themselves.
  - Last-Owner protection: cannot deactivate the only remaining
    active Owner — would brick the org.
  - Cross-tenant 404: orgB cannot deactivate / reactivate orgA users.
    No membership oracle.
  - Audit logging: a user_deactivated audit event is appended for
    Module 7 read endpoints.
  - Reactivate restores access; auth path stops 403'ing.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.api import api_keys as keys_routes  # noqa: E402
from solden.api import team_offboarding as offboarding_routes  # noqa: E402
from solden.core import auth as auth_module  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.auth import TokenData, get_current_user  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta Co")
    return inst


def _make_user(db, *, email: str, role: str = "ap_clerk", org: str = "orgA"):
    user = db.create_user(
        email=email,
        name=email.split("@")[0],
        organization_id=org,
        role=role,
    )
    return str(user["id"])


def _admin(user_id: str, org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        email=f"admin-{user_id}@{org}.com",
        organization_id=org,
        role="owner",
        exp=None,
    )


def _make_app(user_factory):
    app = FastAPI()
    app.include_router(offboarding_routes.router)
    app.include_router(keys_routes.router)
    app.dependency_overrides[get_current_user] = user_factory
    return app


# ─── Tests: Happy path ─────────────────────────────────────────────


class TestDeactivate:
    def test_deactivate_marks_user_inactive(self, db):
        admin_id = _make_user(db, email="admin@orga.com", role="owner")
        target_id = _make_user(db, email="target@orga.com", role="ap_clerk")
        client = TestClient(_make_app(lambda: _admin(admin_id)))

        resp = client.post(f"/api/workspace/team/users/{target_id}/deactivate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deactivated"] is True
        assert body["user_id"] == target_id

        fresh = db.get_user(target_id)
        assert bool(fresh.get("is_active")) is False

    def test_already_inactive_is_idempotent(self, db):
        admin_id = _make_user(db, email="admin@orga.com", role="owner")
        target_id = _make_user(db, email="target@orga.com", role="ap_clerk")
        db.update_user(target_id, is_active=False)
        client = TestClient(_make_app(lambda: _admin(admin_id)))

        resp = client.post(f"/api/workspace/team/users/{target_id}/deactivate")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("already_inactive") is True

    def test_audit_event_appended(self, db):
        admin_id = _make_user(db, email="admin@orga.com", role="owner")
        target_id = _make_user(db, email="target@orga.com", role="ap_clerk")
        client = TestClient(_make_app(lambda: _admin(admin_id)))

        client.post(f"/api/workspace/team/users/{target_id}/deactivate")
        # Read back via the box-keyed audit event listing if exposed.
        if hasattr(db, "list_box_audit_events"):
            events = db.list_box_audit_events(
                box_type="user", box_id=target_id,
            ) or []
            kinds = {e.get("event_type") for e in events}
            assert "user_deactivated" in kinds


class TestSelfProtection:
    def test_admin_cannot_deactivate_self(self, db):
        admin_id = _make_user(db, email="admin@orga.com", role="owner")
        client = TestClient(_make_app(lambda: _admin(admin_id)))

        resp = client.post(f"/api/workspace/team/users/{admin_id}/deactivate")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "cannot_deactivate_self"
        # Admin still active
        assert bool(db.get_user(admin_id).get("is_active")) is True


class TestLastOwnerProtection:
    def test_cannot_deactivate_last_active_owner(self, db):
        # Two owners; deactivate the second admin would leave one active
        # admin — that should succeed. So set up so the target IS the
        # only Owner and the actor is a different role.
        ctrl_id = _make_user(db, email="ctrl@orga.com", role="financial_controller")
        only_owner_id = _make_user(db, email="owner@orga.com", role="owner")
        client = TestClient(_make_app(lambda: _admin(ctrl_id)))
        # Override the actor's role since _admin always uses owner;
        # build a fresh dependency factory matching the actor role.

        def actor_as_ctrl():
            return SimpleNamespace(
                user_id=ctrl_id, email="ctrl@orga.com",
                organization_id="orgA", role="financial_controller", exp=None,
            )
        app = FastAPI()
        app.include_router(offboarding_routes.router)
        app.dependency_overrides[get_current_user] = actor_as_ctrl
        client = TestClient(app)

        resp = client.post(f"/api/workspace/team/users/{only_owner_id}/deactivate")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "last_owner_protected"

        # The Owner is still active.
        assert bool(db.get_user(only_owner_id).get("is_active")) is True

    def test_can_deactivate_owner_when_another_active_owner_exists(self, db):
        # Two owners — deactivating one leaves one. Allowed.
        owner1 = _make_user(db, email="o1@orga.com", role="owner")
        owner2 = _make_user(db, email="o2@orga.com", role="owner")
        client = TestClient(_make_app(lambda: _admin(owner1)))

        resp = client.post(f"/api/workspace/team/users/{owner2}/deactivate")
        assert resp.status_code == 200
        assert resp.json()["deactivated"] is True


class TestCrossTenant:
    def test_orgB_cannot_deactivate_orgA_user(self, db):
        target_id = _make_user(db, email="target@orga.com", org="orgA")
        b_admin_id = _make_user(db, email="admin@orgb.com", role="owner", org="orgB")

        def b_admin():
            return SimpleNamespace(
                user_id=b_admin_id, email="admin@orgb.com",
                organization_id="orgB", role="owner", exp=None,
            )
        app = FastAPI()
        app.include_router(offboarding_routes.router)
        app.dependency_overrides[get_current_user] = b_admin
        client = TestClient(app)

        resp = client.post(f"/api/workspace/team/users/{target_id}/deactivate")
        assert resp.status_code == 404
        # The orgA user is still active.
        assert bool(db.get_user(target_id).get("is_active")) is True

    def test_missing_user_returns_404(self, db):
        admin_id = _make_user(db, email="admin@orga.com", role="owner")
        client = TestClient(_make_app(lambda: _admin(admin_id)))
        resp = client.post("/api/workspace/team/users/does-not-exist/deactivate")
        assert resp.status_code == 404


class TestApiKeyCascade:
    def test_deactivation_revokes_all_user_api_keys(self, db):
        admin_id = _make_user(db, email="admin@orga.com", role="owner")
        target_id = _make_user(db, email="target@orga.com", role="ap_clerk")

        # Target creates an API key (via the api-keys endpoint as themselves).
        def as_target():
            return SimpleNamespace(
                user_id=target_id, email="target@orga.com",
                organization_id="orgA", role="ap_clerk", exp=None,
            )
        target_app = FastAPI()
        target_app.include_router(keys_routes.router)
        target_app.dependency_overrides[get_current_user] = as_target
        target_client = TestClient(target_app)
        created = target_client.post(
            "/api/workspace/api-keys", json={"label": "ci"},
        ).json()
        assert db.validate_api_key(created["raw_key"]) is not None

        # Admin deactivates the target.
        client = TestClient(_make_app(lambda: _admin(admin_id)))
        resp = client.post(f"/api/workspace/team/users/{target_id}/deactivate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["api_keys_revoked"] >= 1

        # Key now fails to authenticate via the X-API-Key path.
        assert db.validate_api_key(created["raw_key"]) is None


class TestAuthLayerEnforcement:
    def test_deactivated_user_cannot_authenticate(self, db):
        target_id = _make_user(db, email="target@orga.com", role="ap_clerk")
        # Resolve a token-data that references the target.
        from datetime import datetime, timedelta, timezone as _tz
        token = TokenData(
            user_id=target_id, email="target@orga.com",
            organization_id="orgA", role="ap_clerk",
            exp=datetime.now(_tz.utc) + timedelta(hours=1),
        )
        # While active — reconcile passes through.
        resolved = auth_module._reconcile_token_data(token)
        assert resolved.user_id == target_id

        # Deactivate the user, then re-reconcile — must raise 403.
        db.update_user(target_id, is_active=False)
        with pytest.raises(HTTPException) as exc:
            auth_module._reconcile_token_data(token)
        assert exc.value.status_code == 403
        assert exc.value.detail == "user_deactivated"


class TestReactivate:
    def test_reactivate_restores_access(self, db):
        admin_id = _make_user(db, email="admin@orga.com", role="owner")
        target_id = _make_user(db, email="target@orga.com", role="ap_clerk")
        # Deactivate then reactivate.
        client = TestClient(_make_app(lambda: _admin(admin_id)))
        client.post(f"/api/workspace/team/users/{target_id}/deactivate")

        resp = client.post(f"/api/workspace/team/users/{target_id}/reactivate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["reactivated"] is True
        assert bool(db.get_user(target_id).get("is_active")) is True

        # Auth path no longer 403's.
        from datetime import datetime, timedelta, timezone as _tz
        token = TokenData(
            user_id=target_id, email="target@orga.com",
            organization_id="orgA", role="ap_clerk",
            exp=datetime.now(_tz.utc) + timedelta(hours=1),
        )
        resolved = auth_module._reconcile_token_data(token)
        assert resolved.user_id == target_id

    def test_reactivate_idempotent_when_already_active(self, db):
        admin_id = _make_user(db, email="admin@orga.com", role="owner")
        target_id = _make_user(db, email="target@orga.com", role="ap_clerk")
        client = TestClient(_make_app(lambda: _admin(admin_id)))
        resp = client.post(f"/api/workspace/team/users/{target_id}/reactivate")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("already_active") is True


class TestAdminGate:
    def test_non_admin_cannot_deactivate(self, db):
        clerk_id = _make_user(db, email="clerk@orga.com", role="ap_clerk")
        target_id = _make_user(db, email="target@orga.com", role="ap_clerk")

        def as_clerk():
            return SimpleNamespace(
                user_id=clerk_id, email="clerk@orga.com",
                organization_id="orgA", role="ap_clerk", exp=None,
            )
        app = FastAPI()
        app.include_router(offboarding_routes.router)
        app.dependency_overrides[get_current_user] = as_clerk
        client = TestClient(app)

        resp = client.post(f"/api/workspace/team/users/{target_id}/deactivate")
        assert resp.status_code == 403
