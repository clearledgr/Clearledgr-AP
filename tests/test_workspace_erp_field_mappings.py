"""Tests for ``GET/PUT /api/workspace/erp/field-mappings`` (Module 5 Pass A).

The custom-field-mapping surface is admin-gated, tenant-scoped, and
audit-emits on diff. These tests cover:

  * The bounded catalog: GET surfaces the per-ERP catalog with
    pattern + default for client-side validation.
  * Round-trip: PUT then GET returns the persisted mapping.
  * Validation: unknown keys → 422; pattern violations → 422; both
    paths leave persisted state untouched.
  * Empty values revert to defaults (key dropped from persistence).
  * Audit emission only on actual diff (no-op PUT does not flood
    the audit log).
  * Tenant scope: cross-tenant 403 via ``_resolve_org_id``.
  * Admin gate: non-admin users 403.
  * Unknown ERP types: 400.
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    inst.ensure_organization("other-tenant", organization_name="other-tenant")
    # Reset settings_json on each test so the audit-on-diff assertions
    # observe a clean before-state.
    inst.update_organization("default", settings_json={})
    inst.update_organization("other-tenant", settings_json={})
    return inst


def _admin_user(org_id: str = "default"):
    return SimpleNamespace(
        email="admin@example.com",
        user_id="admin-user",
        organization_id=org_id,
        role="owner",
    )


def _operator_user(org_id: str = "default"):
    return SimpleNamespace(
        email="ops@example.com",
        user_id="ops-user",
        organization_id=org_id,
        role="ap_clerk",
    )


@pytest.fixture()
def client_factory(db):
    def _build(user_factory):
        app = FastAPI()
        app.include_router(ws.router)
        app.dependency_overrides[get_current_user] = user_factory
        return TestClient(app)
    return _build


def _count_audit_rows(db, org_id: str, event_type_prefix: str = "erp_admin_action:field_mapping_updated"):
    """Count audit rows for the given org + event-type prefix."""
    rows = db.search_audit_events(
        organization_id=org_id,
        from_ts=None,
        to_ts=None,
        event_types=[event_type_prefix],
        actor_id=None,
        box_type=None,
        box_id=None,
        limit=200,
        cursor=None,
    )
    events = rows.get("events") if isinstance(rows, dict) else rows
    return len(events or [])


# ---------------------------------------------------------------------------
# Catalog discovery
# ---------------------------------------------------------------------------


def test_get_returns_catalog_for_netsuite(client_factory):
    client = client_factory(_admin_user)
    resp = client.get("/api/workspace/erp/field-mappings?erp_type=netsuite&organization_id=default")
    assert resp.status_code == 200
    body = resp.json()
    assert body["erp_type"] == "netsuite"
    assert body["organization_id"] == "default"
    assert "netsuite" in body["supported_erps"]
    catalog = body["catalog"]
    keys = {entry["key"] for entry in catalog}
    # Spot-check a representative subset; full surface lives in the
    # catalog module and is exercised by its own unit tests.
    assert {"state_field", "box_id_field", "approver_field"} <= keys
    # Default is informational and the regex pattern is exposed for
    # client-side validation.
    state_entry = next(e for e in catalog if e["key"] == "state_field")
    assert state_entry["default"].startswith("custbody_")
    assert state_entry["pattern"].startswith("^")
    assert body["mappings"] == {}


def test_get_returns_catalog_for_each_supported_erp(client_factory):
    client = client_factory(_admin_user)
    for erp_type in ("netsuite", "sap", "quickbooks", "xero"):
        resp = client.get(f"/api/workspace/erp/field-mappings?erp_type={erp_type}&organization_id=default")
        assert resp.status_code == 200, erp_type
        assert len(resp.json()["catalog"]) >= 2


def test_get_unsupported_erp_returns_400(client_factory):
    client = client_factory(_admin_user)
    resp = client.get("/api/workspace/erp/field-mappings?erp_type=oracle_ebs&organization_id=default")
    assert resp.status_code == 400
    assert "unsupported_erp_type" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Round-trip + persistence
# ---------------------------------------------------------------------------


def test_put_persists_mapping_and_get_reads_back(db, client_factory):
    client = client_factory(_admin_user)
    payload = {
        "erp_type": "netsuite",
        "mappings": {
            "state_field": "custbody_acme_state",
            "box_id_field": "custbody_acme_box",
        },
    }
    resp = client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json=payload,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mappings"] == payload["mappings"]

    # Round-trip
    resp = client.get("/api/workspace/erp/field-mappings?erp_type=netsuite&organization_id=default")
    assert resp.status_code == 200
    assert resp.json()["mappings"] == payload["mappings"]

    # Persisted directly under settings_json so the read path can
    # find it without a join.
    org = db.get_organization("default")
    settings = org.get("settings_json") or {}
    assert settings["erp_field_mappings"]["netsuite"] == payload["mappings"]


def test_put_overwrites_only_target_erp(db, client_factory):
    """A PUT for SAP must not erase an existing NetSuite mapping."""
    client = client_factory(_admin_user)
    client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "netsuite", "mappings": {"state_field": "custbody_x"}},
    )
    client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "sap", "mappings": {"state_field": "ZZ_X"}},
    )
    netsuite = client.get("/api/workspace/erp/field-mappings?erp_type=netsuite&organization_id=default").json()
    sap = client.get("/api/workspace/erp/field-mappings?erp_type=sap&organization_id=default").json()
    assert netsuite["mappings"] == {"state_field": "custbody_x"}
    assert sap["mappings"] == {"state_field": "ZZ_X"}


def test_put_empty_value_drops_key(client_factory):
    """Empty string means 'revert to default' — the key vanishes from
    persistence."""
    client = client_factory(_admin_user)
    client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "netsuite", "mappings": {"state_field": "custbody_x", "box_id_field": "custbody_y"}},
    )
    resp = client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "netsuite", "mappings": {"state_field": "custbody_x", "box_id_field": ""}},
    )
    assert resp.status_code == 200
    assert resp.json()["mappings"] == {"state_field": "custbody_x"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_put_unknown_field_returns_422(db, client_factory):
    client = client_factory(_admin_user)
    resp = client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "netsuite", "mappings": {"made_up_field": "custbody_x"}},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["reason"] == "validation_failed"
    assert any("unknown_field:made_up_field" in err for err in detail["errors"])
    # Persisted state is unchanged.
    org = db.get_organization("default")
    settings = org.get("settings_json") or {}
    assert "erp_field_mappings" not in settings


def test_put_invalid_pattern_returns_422(db, client_factory):
    """NetSuite custom fields must be lowercase/digits/underscore — a
    space-containing value violates the pattern."""
    client = client_factory(_admin_user)
    resp = client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "netsuite", "mappings": {"state_field": "Custom Body Field"}},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("invalid_field_id:state_field" in err for err in detail["errors"])
    org = db.get_organization("default")
    assert "erp_field_mappings" not in (org.get("settings_json") or {})


def test_put_unsupported_erp_returns_400(client_factory):
    client = client_factory(_admin_user)
    resp = client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "oracle_ebs", "mappings": {}},
    )
    assert resp.status_code == 400
    assert "unsupported_erp_type" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Audit emission
# ---------------------------------------------------------------------------


def test_put_emits_audit_on_change(db, client_factory):
    client = client_factory(_admin_user)
    before = _count_audit_rows(db, "default")
    client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "netsuite", "mappings": {"state_field": "custbody_acme_state"}},
    )
    after = _count_audit_rows(db, "default")
    assert after == before + 1


def test_put_no_op_does_not_emit_audit(db, client_factory):
    """Re-saving the same mapping is a no-op — auditing it would flood
    the trail with noise on every refresh."""
    client = client_factory(_admin_user)
    client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "netsuite", "mappings": {"state_field": "custbody_x"}},
    )
    after_first = _count_audit_rows(db, "default")
    client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "netsuite", "mappings": {"state_field": "custbody_x"}},
    )
    after_second = _count_audit_rows(db, "default")
    assert after_second == after_first


def test_audit_payload_has_diff_with_before_after(db, client_factory):
    client = client_factory(_admin_user)
    client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "netsuite", "mappings": {"state_field": "custbody_old"}},
    )
    client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "netsuite", "mappings": {"state_field": "custbody_new"}},
    )
    rows = db.search_audit_events(
        organization_id="default",
        from_ts=None, to_ts=None,
        event_types=["erp_admin_action:field_mapping_updated"],
        actor_id=None, box_type=None, box_id=None,
        limit=10, cursor=None,
    )
    events = rows.get("events", []) if isinstance(rows, dict) else rows
    assert len(events) >= 2
    latest = events[0]  # newest first
    payload = latest.get("payload_json") or {}
    diff = payload.get("diff") or {}
    assert "state_field" in diff
    assert diff["state_field"]["before"] == "custbody_old"
    assert diff["state_field"]["after"] == "custbody_new"


# ---------------------------------------------------------------------------
# Role + tenant gates
# ---------------------------------------------------------------------------


def test_put_non_admin_403(client_factory):
    client = client_factory(_operator_user)
    resp = client.put(
        "/api/workspace/erp/field-mappings?organization_id=default",
        json={"erp_type": "netsuite", "mappings": {"state_field": "custbody_x"}},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_role_required"


def test_get_blocks_cross_tenant(client_factory):
    client = client_factory(_admin_user)
    resp = client.get(
        "/api/workspace/erp/field-mappings?erp_type=netsuite&organization_id=other-tenant"
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "org_access_denied"


def test_put_blocks_cross_tenant(client_factory):
    client = client_factory(_admin_user)
    resp = client.put(
        "/api/workspace/erp/field-mappings?organization_id=other-tenant",
        json={"erp_type": "netsuite", "mappings": {"state_field": "custbody_x"}},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "org_access_denied"
