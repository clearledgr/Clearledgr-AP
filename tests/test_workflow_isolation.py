"""Tenant isolation for declarative Box types.

Two orgs may each define a type with the SAME name but a different graph; the
specs and Boxes are fully isolated. Org B can neither read nor drive Org A's
spec or Boxes.
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

ORG_A = "orgWFiso_A"
ORG_B = "orgWFiso_B"


def _user(org):
    return TokenData(
        user_id=f"u_{org}", email=f"admin@{org}.test", organization_id=org,
        role="member", workspace_role="admin",
        exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


def _spec(states, transitions, action_states, terminal):
    return {
        "box_type": "purchase_request",
        "url_slug": "purchase-request",
        "states": states,
        "initial_state": "draft",
        "terminal_states": terminal,
        "transitions": transitions,
        "action_states": action_states,
    }


SPEC_A = _spec(
    ["draft", "submitted", "approved"],
    {"draft": ["submitted"], "submitted": ["approved"]},
    {"submit": "submitted", "approve": "approved"},
    ["approved"],
)
# Same box_type name, different graph: A's "approve" doesn't exist for B.
SPEC_B = _spec(
    ["draft", "in_review", "closed"],
    {"draft": ["in_review"], "in_review": ["closed"]},
    {"review": "in_review", "close": "closed"},
    ["closed"],
)


@pytest.fixture()
def client():
    db = db_module.get_db()
    db.initialize()
    db.ensure_organization(ORG_A, organization_name=ORG_A)
    db.ensure_organization(ORG_B, organization_name=ORG_B)
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def _as(org):
    app.dependency_overrides[get_current_user] = lambda: _user(org)


def _author_and_activate(client, spec):
    row = client.post("/api/workspace/workflow-specs", json=spec).json()
    client.post(
        f"/api/workspace/workflow-specs/{row['box_type']}/versions/{row['version']}/activate",
        json={},
    )


def test_specs_and_boxes_are_tenant_isolated(client):
    _as(ORG_A)
    _author_and_activate(client, SPEC_A)
    client.post("/api/workspace/workflows/purchase_request",
                json={"box_id": "PR-A1", "data": {"amount": 10}})

    _as(ORG_B)
    _author_and_activate(client, SPEC_B)
    client.post("/api/workspace/workflows/purchase_request",
                json={"box_id": "PR-B1", "data": {"amount": 20}})

    # B cannot see A's box.
    assert client.get("/api/workspace/workflows/purchase_request/PR-A1").status_code == 404
    # B's list contains only B's box.
    b_boxes = client.get("/api/workspace/workflows/purchase_request").json()["boxes"]
    ids = {b["id"] for b in b_boxes}
    assert "PR-B1" in ids and "PR-A1" not in ids
    # B cannot drive A's box.
    assert client.post("/api/workspace/workflows/purchase_request/PR-A1/close", json={}).status_code == 404

    # Each org's box follows ITS OWN graph.
    _as(ORG_A)
    a_spec = client.get("/api/workspace/workflow-specs/purchase_request").json()
    assert a_spec["spec_json"]["action_states"].get("approve") == "approved"
    # A's "approve" action drives A's box (after submit).
    client.post("/api/workspace/workflows/purchase_request/PR-A1/submit", json={})
    assert client.post("/api/workspace/workflows/purchase_request/PR-A1/approve", json={}).json()["state"] == "approved"
    # A has no "review" action (that's B's vocabulary).
    assert client.post("/api/workspace/workflows/purchase_request/PR-A1/review", json={}).status_code in (404, 409)

    _as(ORG_B)
    b_spec = client.get("/api/workspace/workflow-specs/purchase_request").json()
    assert b_spec["spec_json"]["action_states"].get("review") == "in_review"
    # B can't read A's spec content; it only ever sees its own.
    assert "approve" not in b_spec["spec_json"]["action_states"]
