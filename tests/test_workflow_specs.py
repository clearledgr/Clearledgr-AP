"""Level 2 — tenant-authored, versioned declarative Box types via the API.

A workspace admin drafts a WorkflowSpec, validates it, activates a version,
then creates and drives Boxes of that type — all at runtime, no deploy. Also
proves version pinning: activating a new spec version never changes the legal
transitions of in-flight Boxes.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.auth import TokenData, get_current_user  # noqa: E402

ORG = "orgWFSpecs"


def _user(role="admin"):
    return TokenData(
        user_id="u_admin", email="admin@acme.test", organization_id=ORG,
        role="member", workspace_role=role,
        exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


SPEC_V1 = {
    "box_type": "vendor_coi",
    "url_slug": "vendor-coi",
    "states": ["draft", "submitted", "approved", "rejected"],
    "initial_state": "draft",
    "terminal_states": ["approved", "rejected"],
    "transitions": {"draft": ["submitted"], "submitted": ["approved", "rejected"]},
    "action_states": {"submit": "submitted", "approve": "approved", "reject": "rejected"},
    "fields": ["vendor", "policy_number"],
}

# v2 drops the "approved" path entirely.
SPEC_V2 = {
    "box_type": "vendor_coi",
    "url_slug": "vendor-coi",
    "states": ["draft", "submitted", "rejected"],
    "initial_state": "draft",
    "terminal_states": ["rejected"],
    "transitions": {"draft": ["submitted"], "submitted": ["rejected"]},
    "action_states": {"submit": "submitted", "reject": "rejected"},
    "fields": ["vendor"],
}


@pytest.fixture()
def client():
    db = db_module.get_db()
    db.initialize()
    db.ensure_organization(ORG, organization_name=ORG)
    app.dependency_overrides[get_current_user] = lambda: _user("admin")
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def _author_and_activate(client, spec):
    r = client.post("/api/workspace/workflow-specs", json=spec)
    assert r.status_code == 200, r.text
    row = r.json()
    box_type, version = row["box_type"], row["version"]
    a = client.post(f"/api/workspace/workflow-specs/{box_type}/versions/{version}/activate", json={})
    assert a.status_code == 200, a.text
    return version


def test_validate_endpoint_flags_bad_spec(client):
    bad = dict(SPEC_V1, action_states={"go": "nowhere"})
    r = client.post("/api/workspace/workflow-specs/validate", json=bad)
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert any("nowhere" in e for e in body["errors"])


def test_create_rejects_invalid_spec_422(client):
    bad = dict(SPEC_V1, initial_state="not_a_state")
    r = client.post("/api/workspace/workflow-specs", json=bad)
    assert r.status_code == 422


def test_author_activate_create_drive(client):
    _author_and_activate(client, SPEC_V1)
    # Create a box of the freshly-declared type.
    c = client.post("/api/workspace/workflows/vendor_coi",
                    json={"box_id": "COI-1", "data": {"vendor": "Globex", "policy_number": "P-9"}})
    assert c.status_code == 200, c.text
    box = c.json()
    assert box["state"] == "draft"
    assert box["vendor"] == "Globex"
    # Drive it through the declared lifecycle.
    s = client.post("/api/workspace/workflows/vendor_coi/COI-1/submit", json={})
    assert s.status_code == 200 and s.json()["state"] == "submitted"
    a = client.post("/api/workspace/workflows/vendor_coi/COI-1/approve", json={})
    assert a.status_code == 200 and a.json()["state"] == "approved"
    # List + get.
    lst = client.get("/api/workspace/workflows/vendor_coi").json()
    assert any(b["id"] == "COI-1" for b in lst["boxes"])
    got = client.get("/api/workspace/workflows/vendor_coi/COI-1").json()
    assert got["state"] == "approved"


def test_illegal_action_returns_409(client):
    _author_and_activate(client, SPEC_V1)
    client.post("/api/workspace/workflows/vendor_coi",
                json={"box_id": "COI-2", "data": {"vendor": "Acme"}})
    # approve a draft (must submit first) -> illegal edge
    r = client.post("/api/workspace/workflows/vendor_coi/COI-2/approve", json={})
    assert r.status_code == 409


def test_unknown_type_and_action_404(client):
    _author_and_activate(client, SPEC_V1)
    assert client.get("/api/workspace/workflows/nonexistent_type").status_code == 404
    client.post("/api/workspace/workflows/vendor_coi",
                json={"box_id": "COI-3", "data": {}})
    r = client.post("/api/workspace/workflows/vendor_coi/COI-3/teleport", json={})
    assert r.status_code == 404


def test_version_pinning(client):
    _author_and_activate(client, SPEC_V1)
    # Box B1 created under v1.
    client.post("/api/workspace/workflows/vendor_coi",
                json={"box_id": "COI-B1", "data": {"vendor": "PinnedCo"}})
    # Activate v2 (which has no 'approved' path at all).
    _author_and_activate(client, SPEC_V2)
    # B1 still follows v1: submit -> approve works even though v2 is active.
    assert client.post("/api/workspace/workflows/vendor_coi/COI-B1/submit", json={}).status_code == 200
    a = client.post("/api/workspace/workflows/vendor_coi/COI-B1/approve", json={})
    assert a.status_code == 200 and a.json()["state"] == "approved"
    # A new box B2 pins v2: 'approve' is not even a declared action.
    client.post("/api/workspace/workflows/vendor_coi",
                json={"box_id": "COI-B2", "data": {"vendor": "NewCo"}})
    client.post("/api/workspace/workflows/vendor_coi/COI-B2/submit", json={})
    assert client.post("/api/workspace/workflows/vendor_coi/COI-B2/approve", json={}).status_code == 404


def test_non_admin_cannot_author(client):
    app.dependency_overrides[get_current_user] = lambda: _user("member")
    try:
        r = client.post("/api/workspace/workflow-specs", json=SPEC_V1)
        assert r.status_code == 403
    finally:
        app.dependency_overrides[get_current_user] = lambda: _user("admin")
