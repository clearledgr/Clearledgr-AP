"""Tests for customer-side API keys — Module 11.

Pinned by these tests:

  - Show-once: the raw key is in the create response and **never
    again** in any subsequent response. The store only ever holds
    the SHA-256 hash.
  - Soft-delete revocation: revoked keys stay in the table (audit
    trail) but ``validate_api_key`` returns None for them.
  - Org-scoping: every endpoint requires the caller's organization_id
    to match the row. Cross-tenant get/rotate/delete return 404,
    never 403, so the membership oracle stays closed.
  - Rotation: revokes old + issues new under the same label, returns
    the new raw key, refuses to rotate an already-revoked key.
  - Generated keys are long enough and prefixed for grepability.
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

from solden.api import api_keys as keys_routes  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.auth import get_current_user  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta Co")
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=f"leader@{org}.com",
        email=f"leader@{org}.com",
        organization_id=org,
        role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(keys_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


@pytest.fixture()
def client_orgB(db):
    app = FastAPI()
    app.include_router(keys_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgB")
    return TestClient(app)


# ─── Tests: Create + show-once ──────────────────────────────────────


class TestCreate:
    def test_returns_raw_key_once(self, db, client_orgA):
        resp = client_orgA.post(
            "/api/workspace/api-keys",
            json={"label": "ci-script"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["label"] == "ci-script"
        assert body["raw_key"].startswith("ck_")
        # The raw key must be long enough that brute force is infeasible —
        # ck_ + base64(32 bytes) = ~46 chars.
        assert len(body["raw_key"]) >= 40
        assert body["key_prefix"].startswith("ck_")
        assert "..." in body["key_prefix"]
        assert body["is_active"] is True

    def test_subsequent_get_does_not_leak_raw_key(self, db, client_orgA):
        created = client_orgA.post(
            "/api/workspace/api-keys",
            json={"label": "secret"},
        ).json()
        fetched = client_orgA.get(
            f"/api/workspace/api-keys/{created['id']}",
        ).json()
        assert "raw_key" not in fetched
        # Prefix is still visible (recognising your own key).
        assert fetched["key_prefix"] == created["key_prefix"]

    def test_list_does_not_leak_raw_key(self, db, client_orgA):
        client_orgA.post("/api/workspace/api-keys", json={"label": "k1"})
        client_orgA.post("/api/workspace/api-keys", json={"label": "k2"})
        body = client_orgA.get("/api/workspace/api-keys").json()
        assert len(body["api_keys"]) >= 2
        for key in body["api_keys"]:
            assert "raw_key" not in key
            assert "key_hash" not in key

    def test_raw_key_validates_through_auth_path(self, db, client_orgA):
        # End-to-end: create a key, then verify the raw_key authenticates
        # via db.validate_api_key (the same path deps.py uses).
        created = client_orgA.post(
            "/api/workspace/api-keys",
            json={"label": "auth-test"},
        ).json()
        record = db.validate_api_key(created["raw_key"])
        assert record is not None
        assert record["organization_id"] == "orgA"
        assert record["id"] == created["id"]


# ─── Tests: Cross-tenant isolation ──────────────────────────────────


class TestTenantIsolation:
    def test_orgB_cannot_see_orgA_keys_in_list(self, db, client_orgA, client_orgB):
        client_orgA.post("/api/workspace/api-keys", json={"label": "orga-key"})
        b_list = client_orgB.get("/api/workspace/api-keys").json()
        assert b_list["api_keys"] == []

    def test_orgB_get_orgA_key_returns_404(self, db, client_orgA, client_orgB):
        a_key = client_orgA.post(
            "/api/workspace/api-keys", json={"label": "orga-key"},
        ).json()
        resp = client_orgB.get(f"/api/workspace/api-keys/{a_key['id']}")
        assert resp.status_code == 404

    def test_orgB_revoke_orgA_key_is_blocked(self, db, client_orgA, client_orgB):
        a_key = client_orgA.post(
            "/api/workspace/api-keys", json={"label": "orga-key"},
        ).json()
        resp = client_orgB.delete(f"/api/workspace/api-keys/{a_key['id']}")
        assert resp.status_code == 404
        # orgA's key is still active.
        record = db.get_api_key(a_key["id"], "orgA")
        assert record["is_active"] is True

    def test_orgB_rotate_orgA_key_is_blocked(self, db, client_orgA, client_orgB):
        a_key = client_orgA.post(
            "/api/workspace/api-keys", json={"label": "orga-key"},
        ).json()
        resp = client_orgB.post(f"/api/workspace/api-keys/{a_key['id']}/rotate")
        assert resp.status_code == 404


# ─── Tests: Revoke ──────────────────────────────────────────────────


class TestRevoke:
    def test_revoke_makes_key_invalid_for_auth(self, db, client_orgA):
        created = client_orgA.post(
            "/api/workspace/api-keys", json={"label": "revoke-test"},
        ).json()
        # Confirm valid before revoke
        assert db.validate_api_key(created["raw_key"]) is not None

        resp = client_orgA.delete(f"/api/workspace/api-keys/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True

        # Auth path now rejects the key
        assert db.validate_api_key(created["raw_key"]) is None

    def test_revoke_is_soft_delete_row_still_exists(self, db, client_orgA):
        created = client_orgA.post(
            "/api/workspace/api-keys", json={"label": "audit-trail"},
        ).json()
        client_orgA.delete(f"/api/workspace/api-keys/{created['id']}")

        # Default list excludes revoked keys.
        active = client_orgA.get("/api/workspace/api-keys").json()
        assert all(k["id"] != created["id"] for k in active["api_keys"])

        # include_revoked=true returns the audit row.
        all_keys = client_orgA.get(
            "/api/workspace/api-keys?include_revoked=true",
        ).json()
        revoked_row = next(k for k in all_keys["api_keys"] if k["id"] == created["id"])
        assert revoked_row["is_active"] is False

    def test_double_revoke_returns_already_revoked(self, db, client_orgA):
        created = client_orgA.post(
            "/api/workspace/api-keys", json={"label": "double-revoke"},
        ).json()
        first = client_orgA.delete(f"/api/workspace/api-keys/{created['id']}").json()
        assert first["revoked"] is True

        second = client_orgA.delete(f"/api/workspace/api-keys/{created['id']}").json()
        assert second.get("already_revoked") is True


# ─── Tests: Rotate ──────────────────────────────────────────────────


class TestRotate:
    def test_rotate_returns_new_raw_key_and_revokes_old(self, db, client_orgA):
        original = client_orgA.post(
            "/api/workspace/api-keys", json={"label": "ci-deploy"},
        ).json()

        resp = client_orgA.post(f"/api/workspace/api-keys/{original['id']}/rotate")
        assert resp.status_code == 200
        rotated = resp.json()
        assert rotated["raw_key"].startswith("ck_")
        assert rotated["raw_key"] != original["raw_key"]
        assert rotated["label"] == "ci-deploy"
        assert rotated["rotated_from"] == original["id"]
        assert rotated["id"] != original["id"]

        # Old key is revoked.
        assert db.validate_api_key(original["raw_key"]) is None
        # New key is valid.
        assert db.validate_api_key(rotated["raw_key"]) is not None

    def test_rotate_already_revoked_returns_400(self, db, client_orgA):
        created = client_orgA.post(
            "/api/workspace/api-keys", json={"label": "revoke-then-rotate"},
        ).json()
        client_orgA.delete(f"/api/workspace/api-keys/{created['id']}")
        resp = client_orgA.post(f"/api/workspace/api-keys/{created['id']}/rotate")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "key_already_revoked"


# ─── Tests: Generation ──────────────────────────────────────────────


class TestKeyGeneration:
    def test_generated_keys_are_unique(self, db, client_orgA):
        keys = set()
        for i in range(10):
            resp = client_orgA.post(
                "/api/workspace/api-keys", json={"label": f"k-{i}"},
            ).json()
            keys.add(resp["raw_key"])
        assert len(keys) == 10
