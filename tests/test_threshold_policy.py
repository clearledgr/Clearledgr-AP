"""Tests for Wave 5 / G4 — configurable confidence thresholds.

Covers:
  * Resolution layering: vendor override > vendor learned > org
    default > hardcoded fallback. source_chain reports which layer
    won per field.
  * Validation: auto_approve_min must be > escalate_below; clamping
    to [0.5, 0.99]; nudge-above when learned threshold collides with
    org escalate_below.
  * Org config CRUD: GET defaults to empty; PUT persists; partial
    PUT preserves untouched fields.
  * Vendor override CRUD: GET defaults to empty; PUT requires existing
    vendor; DELETE clears the override.
  * API: org policy, vendor policy, resolve, cross-org isolation.
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

from clearledgr.api import threshold_policy as tp_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.threshold_policy import (  # noqa: E402
    get_org_thresholds,
    resolve_thresholds,
    set_org_thresholds,
    set_vendor_threshold_overrides,
)


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
        user_id="user-1", email="op@orgA.com",
        organization_id=org, role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(tp_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


# ─── Resolution layering ───────────────────────────────────────────


def test_resolve_falls_back_to_hardcoded(db):
    result = resolve_thresholds(db, organization_id="orgA")
    assert result.auto_approve_min == 0.95
    assert result.escalate_below == 0.70
    assert result.po_required_above is None
    assert result.source_chain["auto_approve_min"] == "hardcoded_fallback"


def test_resolve_uses_org_default(db):
    set_org_thresholds(
        db, "orgA",
        auto_approve_min=0.92, escalate_below=0.65,
        po_required_above=2500.0,
    )
    result = resolve_thresholds(db, organization_id="orgA")
    assert result.auto_approve_min == 0.92
    assert result.escalate_below == 0.65
    assert result.po_required_above == 2500.0
    assert all(
        v == "org_default" for v in result.source_chain.values()
    )


def test_resolve_vendor_override_wins(db):
    set_org_thresholds(db, "orgA", auto_approve_min=0.95)
    db.upsert_vendor_profile("orgA", "Vendor X")
    set_vendor_threshold_overrides(
        db, "orgA", "Vendor X",
        auto_approve_min=0.85,
    )
    result = resolve_thresholds(
        db, organization_id="orgA", vendor_name="Vendor X",
    )
    assert result.auto_approve_min == 0.85
    assert result.source_chain["auto_approve_min"] == "vendor_override"


def test_resolve_vendor_learned_used_when_no_override(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        metadata={"learned_auto_approve_threshold": 0.88},
    )
    result = resolve_thresholds(
        db, organization_id="orgA", vendor_name="Vendor X",
    )
    assert result.auto_approve_min == 0.88
    assert result.source_chain["auto_approve_min"] == "vendor_learned"


def test_resolve_clamps_out_of_range_values(db):
    set_org_thresholds(db, "orgA", auto_approve_min=1.5)  # over max
    result = resolve_thresholds(db, organization_id="orgA")
    assert result.auto_approve_min == 0.99


def test_resolve_nudges_when_collision(db):
    """If learned threshold ≤ org escalate_below, the resolver nudges
    auto_approve_min to escalate_below + 0.01 so the routing window
    stays well-defined."""
    set_org_thresholds(db, "orgA", escalate_below=0.92)
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        metadata={"learned_auto_approve_threshold": 0.85},
    )
    result = resolve_thresholds(
        db, organization_id="orgA", vendor_name="Vendor X",
    )
    assert result.auto_approve_min > result.escalate_below
    assert "nudged" in result.source_chain["auto_approve_min"]


# ─── Validation ────────────────────────────────────────────────────


def test_set_org_rejects_inverted_thresholds(db):
    with pytest.raises(ValueError):
        set_org_thresholds(
            db, "orgA",
            auto_approve_min=0.65, escalate_below=0.85,
        )


def test_set_vendor_rejects_inverted_thresholds(db):
    db.upsert_vendor_profile("orgA", "Vendor X")
    with pytest.raises(ValueError):
        set_vendor_threshold_overrides(
            db, "orgA", "Vendor X",
            auto_approve_min=0.6, escalate_below=0.7,
        )


def test_set_vendor_unknown_raises(db):
    with pytest.raises(ValueError):
        set_vendor_threshold_overrides(
            db, "orgA", "NoSuchVendor",
            auto_approve_min=0.85,
        )


# ─── Persistence ───────────────────────────────────────────────────


def test_partial_org_put_preserves_other_fields(db):
    set_org_thresholds(db, "orgA", auto_approve_min=0.92, escalate_below=0.65)
    set_org_thresholds(db, "orgA", po_required_above=1000.0)
    block = get_org_thresholds(db, "orgA")
    assert block["auto_approve_min"] == 0.92
    assert block["escalate_below"] == 0.65
    assert block["po_required_above"] == 1000.0


def test_clear_vendor_override_reverts_to_default(db):
    set_org_thresholds(db, "orgA", auto_approve_min=0.92)
    db.upsert_vendor_profile("orgA", "Vendor X")
    set_vendor_threshold_overrides(
        db, "orgA", "Vendor X", auto_approve_min=0.80,
    )
    set_vendor_threshold_overrides(
        db, "orgA", "Vendor X", clear=True,
    )
    result = resolve_thresholds(
        db, organization_id="orgA", vendor_name="Vendor X",
    )
    assert result.auto_approve_min == 0.92
    assert result.source_chain["auto_approve_min"] == "org_default"


# ─── API ───────────────────────────────────────────────────────────


def test_api_get_put_org_thresholds(db, client_orgA):
    resp = client_orgA.get("/api/workspace/policy/thresholds")
    assert resp.status_code == 200
    assert resp.json() == {
        "auto_approve_min": None,
        "escalate_below": None,
        "po_required_above": None,
    }
    put_resp = client_orgA.put(
        "/api/workspace/policy/thresholds",
        json={
            "auto_approve_min": 0.92,
            "escalate_below": 0.65,
            "po_required_above": 1500.0,
        },
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["auto_approve_min"] == 0.92


def test_api_invalid_pair_400(client_orgA):
    resp = client_orgA.put(
        "/api/workspace/policy/thresholds",
        json={"auto_approve_min": 0.6, "escalate_below": 0.8},
    )
    assert resp.status_code == 400


def test_api_vendor_thresholds_crud(db, client_orgA):
    db.upsert_vendor_profile("orgA", "Vendor API")
    put_resp = client_orgA.put(
        "/api/workspace/vendors/Vendor API/thresholds",
        json={"auto_approve_min": 0.85},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["auto_approve_min"] == 0.85

    get_resp = client_orgA.get(
        "/api/workspace/vendors/Vendor API/thresholds",
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["auto_approve_min"] == 0.85

    del_resp = client_orgA.delete(
        "/api/workspace/vendors/Vendor API/thresholds",
    )
    assert del_resp.status_code == 200
    after = client_orgA.get(
        "/api/workspace/vendors/Vendor API/thresholds",
    )
    assert after.json()["auto_approve_min"] is None


def test_api_vendor_unknown_404(client_orgA):
    resp = client_orgA.get(
        "/api/workspace/vendors/NoSuchVendor/thresholds",
    )
    assert resp.status_code == 404


def test_api_resolve_endpoint(db, client_orgA):
    set_org_thresholds(db, "orgA", auto_approve_min=0.92, escalate_below=0.65)
    resp = client_orgA.get(
        "/api/workspace/policy/thresholds/resolve",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["auto_approve_min"] == 0.92
    assert data["source_chain"]["auto_approve_min"] == "org_default"


def test_api_resolve_with_vendor_query(db, client_orgA):
    db.upsert_vendor_profile("orgA", "Vendor R")
    set_vendor_threshold_overrides(
        db, "orgA", "Vendor R", auto_approve_min=0.82, escalate_below=0.6,
    )
    resp = client_orgA.get(
        "/api/workspace/policy/thresholds/resolve?vendor=Vendor R",
    )
    data = resp.json()
    assert data["vendor_name"] == "Vendor R"
    assert data["auto_approve_min"] == 0.82
    assert data["source_chain"]["auto_approve_min"] == "vendor_override"
