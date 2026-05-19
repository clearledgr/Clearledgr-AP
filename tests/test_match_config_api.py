"""Tests for /api/workspace/settings/match-config (Phase 3).

Covers:
  * GET defaults: fresh org returns mode=two_way_fallback +
    historical tolerance defaults.
  * PUT mode only: writes new match_mode version, leaves tolerances.
  * PUT tolerances only: writes new match_tolerances version,
    preserves omitted fields.
  * PUT both: each call advances both version_numbers.
  * Validation: invalid mode → 422; out-of-range tolerance → 422.
  * Role gates: ap_clerk + ap_manager get 403 on PUT;
    financial_controller + cfo + owner can PUT.
  * Org isolation: orgA's PUT does not change orgB's config.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.api import match_config as mc_routes  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.auth import TokenData, get_current_user  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgC", organization_name="Config Co A")
    inst.ensure_organization("orgD", organization_name="Config Co B")
    return inst


def _user(role: str, org: str = "orgC", user_id: str = "u1") -> TokenData:
    return TokenData(
        user_id=user_id,
        email=f"{user_id}@{org}.test",
        organization_id=org,
        role=role,
        exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture()
def app(db):
    app = FastAPI()
    app.include_router(mc_routes.router)
    return app


def _client_as(app, user: TokenData) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


# ─── GET defaults ───────────────────────────────────────────────────


def test_get_returns_defaults_for_fresh_org(app, db):
    client = _client_as(app, _user("ap_clerk", org="orgC"))
    resp = client.get("/api/workspace/settings/match-config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_id"] == "orgC"
    assert body["mode"] == "two_way_fallback"
    assert body["tolerances"]["price_tolerance_percent"] == 2.0
    assert body["tolerances"]["quantity_tolerance_percent"] == 5.0
    assert body["tolerances"]["amount_tolerance"] == 10.0
    assert body["mode_version_number"] >= 1
    assert body["tolerances_version_number"] >= 1


# ─── PUT mode only ─────────────────────────────────────────────────


def test_put_mode_only_writes_new_mode_version(app, db):
    admin = _user("financial_controller", org="orgC", user_id="fc1")
    client = _client_as(app, admin)

    # Read baseline.
    base = client.get("/api/workspace/settings/match-config").json()
    base_mode_v = base["mode_version_number"]
    base_tol_v = base["tolerances_version_number"]

    resp = client.put(
        "/api/workspace/settings/match-config",
        json={"mode": "three_way_required"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "three_way_required"
    assert body["mode_version_number"] > base_mode_v
    # Tolerances untouched.
    assert body["tolerances_version_number"] == base_tol_v


# ─── PUT tolerances only ───────────────────────────────────────────


def test_put_partial_tolerances_preserves_omitted_fields(app, db):
    admin = _user("financial_controller", org="orgC", user_id="fc2")
    client = _client_as(app, admin)
    base = client.get("/api/workspace/settings/match-config").json()

    resp = client.put(
        "/api/workspace/settings/match-config",
        json={"tolerances": {"price_tolerance_percent": 0.5}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tolerances"]["price_tolerance_percent"] == 0.5
    # Omitted fields unchanged from baseline.
    assert (
        body["tolerances"]["quantity_tolerance_percent"]
        == base["tolerances"]["quantity_tolerance_percent"]
    )
    assert (
        body["tolerances"]["amount_tolerance"]
        == base["tolerances"]["amount_tolerance"]
    )
    assert body["tolerances_version_number"] > base["tolerances_version_number"]


# ─── PUT both ──────────────────────────────────────────────────────


def test_put_both_advances_both_versions(app, db):
    admin = _user("cfo", org="orgC", user_id="cfo1")
    client = _client_as(app, admin)
    base = client.get("/api/workspace/settings/match-config").json()

    resp = client.put(
        "/api/workspace/settings/match-config",
        json={
            "mode": "policy_only",
            "tolerances": {
                "price_tolerance_percent": 1.0,
                "quantity_tolerance_percent": 2.5,
                "amount_tolerance": 5.0,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "policy_only"
    assert body["tolerances"]["price_tolerance_percent"] == 1.0
    assert body["mode_version_number"] > base["mode_version_number"]
    assert body["tolerances_version_number"] > base["tolerances_version_number"]


# ─── Validation ────────────────────────────────────────────────────


def test_invalid_mode_rejected(app, db):
    admin = _user("financial_controller", org="orgC")
    client = _client_as(app, admin)
    resp = client.put(
        "/api/workspace/settings/match-config",
        json={"mode": "freestyle"},
    )
    assert resp.status_code == 422


def test_negative_tolerance_rejected(app, db):
    admin = _user("financial_controller", org="orgC")
    client = _client_as(app, admin)
    resp = client.put(
        "/api/workspace/settings/match-config",
        json={"tolerances": {"price_tolerance_percent": -1.0}},
    )
    assert resp.status_code == 422


# ─── Role gates ────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["read_only", "ap_clerk", "ap_manager"])
def test_non_admin_roles_cannot_put(app, db, role):
    client = _client_as(app, _user(role, org="orgC"))
    resp = client.put(
        "/api/workspace/settings/match-config",
        json={"mode": "policy_only"},
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("role", ["financial_controller", "cfo", "owner"])
def test_admin_roles_can_put(app, db, role):
    client = _client_as(app, _user(role, org="orgC", user_id=f"u_{role}"))
    resp = client.put(
        "/api/workspace/settings/match-config",
        json={"mode": "two_way_fallback"},
    )
    assert resp.status_code == 200, resp.text


def test_get_works_for_low_privilege_users(app, db):
    """Operational visibility — any org member can read the config."""
    client = _client_as(app, _user("ap_clerk", org="orgC"))
    resp = client.get("/api/workspace/settings/match-config")
    assert resp.status_code == 200


# ─── Org isolation ─────────────────────────────────────────────────


def test_orga_put_does_not_change_orgb(app, db):
    admin_a = _user("financial_controller", org="orgC", user_id="fcA")
    client_a = _client_as(app, admin_a)
    resp = client_a.put(
        "/api/workspace/settings/match-config",
        json={"mode": "three_way_required"},
    )
    assert resp.status_code == 200

    # Switch to orgD user; should see the default mode, not three_way_required.
    admin_b = _user("financial_controller", org="orgD", user_id="fcB")
    client_b = _client_as(app, admin_b)
    resp_b = client_b.get("/api/workspace/settings/match-config")
    assert resp_b.status_code == 200
    body_b = resp_b.json()
    assert body_b["organization_id"] == "orgD"
    assert body_b["mode"] == "two_way_fallback"
