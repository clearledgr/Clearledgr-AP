"""Tests for ``GET /api/workspace/audit/search`` + ``GET /api/workspace/audit/event/{id}``.

Module 7 v1 Pass 1 — the dashboard's audit search surface. Covers:

  * Admin gate — non-admin users 403.
  * Tenant scope — cross-tenant search 403s, cross-tenant event detail
    404s with the same token as truly-missing.
  * Newest-first ordering.
  * Filter semantics: from_ts/to_ts (inclusive), event_type (comma list),
    actor_id (exact), box_type/box_id (narrow to one Box).
  * Composite-cursor pagination — last page emits next_cursor=None;
    cursor decodes round-trip.
"""
from __future__ import annotations

import sys
import time
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


def _seed_event(db, *, box_id: str, event_type: str, organization_id: str = "default", actor_id: str = "admin@example.com", box_type: str = "ap_item", ts: str | None = None):
    """Insert a minimal audit event. Returns the inserted row dict."""
    payload = {
        "box_id": box_id,
        "box_type": box_type,
        "event_type": event_type,
        "actor_type": "user",
        "actor_id": actor_id,
        "organization_id": organization_id,
        "source": "test_seed",
        "payload_json": {"reason": "test"},
        # Idempotency key keyed on every dimension we vary so two seeds
        # in the same test never trip the UNIQUE constraint.
        "idempotency_key": f"audit_search_test:{organization_id}:{box_id}:{event_type}:{actor_id}:{ts or time.time_ns()}",
    }
    if ts:
        payload["ts"] = ts
    return db.append_audit_event(payload)


# ---------------------------------------------------------------------------
# Role + tenant gate
# ---------------------------------------------------------------------------


def test_search_requires_admin(client_factory):
    client = client_factory(_operator_user)
    resp = client.get("/api/workspace/audit/search?organization_id=default")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_role_required"


def test_search_blocks_cross_tenant(client_factory):
    client = client_factory(_admin_user)
    resp = client.get("/api/workspace/audit/search?organization_id=other-tenant")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "org_access_denied"


def test_event_detail_404_for_cross_tenant_event(db, client_factory):
    """An admin in org A querying an event from org B gets 404 with
    the same token as a truly-missing event — never a 403, never any
    signal that the event exists in another tenant."""
    seed = _seed_event(db, box_id="cross-tenant-box", event_type="state_transition", organization_id="other-tenant")
    client = client_factory(_admin_user)
    resp = client.get(f"/api/workspace/audit/event/{seed['id']}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "audit_event_not_found"


def test_event_detail_404_for_missing_event(client_factory):
    client = client_factory(_admin_user)
    resp = client.get("/api/workspace/audit/event/EVT-does-not-exist-99999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "audit_event_not_found"


# ---------------------------------------------------------------------------
# Happy path + ordering
# ---------------------------------------------------------------------------


def test_search_returns_newest_first(db, client_factory):
    e1 = _seed_event(db, box_id="ap-1", event_type="state_transition", ts="2026-04-26T10:00:00+00:00")
    e2 = _seed_event(db, box_id="ap-1", event_type="invoice_approved", ts="2026-04-27T10:00:00+00:00")
    e3 = _seed_event(db, box_id="ap-1", event_type="erp_post_completed", ts="2026-04-28T10:00:00+00:00")

    client = client_factory(_admin_user)
    resp = client.get("/api/workspace/audit/search?organization_id=default&limit=10")
    assert resp.status_code == 200
    body = resp.json()
    ids = [e["id"] for e in body["events"]]
    # Newest first: e3, e2, e1.
    assert ids[:3] == [e3["id"], e2["id"], e1["id"]]


def test_search_filters_by_event_type(db, client_factory):
    _seed_event(db, box_id="ap-2", event_type="state_transition")
    _seed_event(db, box_id="ap-2", event_type="invoice_approved")
    _seed_event(db, box_id="ap-2", event_type="erp_post_completed")

    client = client_factory(_admin_user)
    resp = client.get(
        "/api/workspace/audit/search?organization_id=default&event_type=invoice_approved,erp_post_completed&limit=10"
    )
    assert resp.status_code == 200
    types = {e["event_type"] for e in resp.json()["events"]}
    assert types == {"invoice_approved", "erp_post_completed"}


def test_search_filters_by_actor(db, client_factory):
    _seed_event(db, box_id="ap-3", event_type="invoice_approved", actor_id="alice@example.com")
    _seed_event(db, box_id="ap-3", event_type="invoice_approved", actor_id="bob@example.com")

    client = client_factory(_admin_user)
    resp = client.get(
        "/api/workspace/audit/search?organization_id=default&actor_id=alice@example.com&limit=10"
    )
    assert resp.status_code == 200
    actors = {e["actor_id"] for e in resp.json()["events"]}
    assert actors == {"alice@example.com"}


def test_search_filters_by_box(db, client_factory):
    _seed_event(db, box_id="ap-4a", event_type="state_transition")
    _seed_event(db, box_id="ap-4b", event_type="state_transition")

    client = client_factory(_admin_user)
    resp = client.get(
        "/api/workspace/audit/search?organization_id=default&box_type=ap_item&box_id=ap-4a&limit=10"
    )
    assert resp.status_code == 200
    box_ids = {e["box_id"] for e in resp.json()["events"]}
    assert box_ids == {"ap-4a"}


def test_search_filters_by_date_range(db, client_factory):
    _seed_event(db, box_id="ap-5", event_type="invoice_approved", ts="2026-04-25T10:00:00+00:00")
    in_range = _seed_event(db, box_id="ap-5", event_type="invoice_approved", ts="2026-04-27T10:00:00+00:00")
    _seed_event(db, box_id="ap-5", event_type="invoice_approved", ts="2026-04-30T10:00:00+00:00")

    client = client_factory(_admin_user)
    resp = client.get(
        "/api/workspace/audit/search?organization_id=default&from_ts=2026-04-26T00:00:00%2B00:00&to_ts=2026-04-29T00:00:00%2B00:00&box_id=ap-5&limit=10"
    )
    assert resp.status_code == 200
    ids = [e["id"] for e in resp.json()["events"]]
    assert ids == [in_range["id"]]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_search_paginates_with_cursor(db, client_factory):
    # 5 distinct events in one Box, walk pages of 2 and verify ordering
    # + that the last page emits next_cursor=None.
    seeded = []
    for i in range(5):
        seeded.append(_seed_event(
            db,
            box_id="ap-paginate",
            event_type="state_transition",
            ts=f"2026-04-2{i+1}T10:00:00+00:00",
        ))
    expected_order = [s["id"] for s in reversed(seeded)]  # newest-first

    client = client_factory(_admin_user)
    seen: list = []
    cursor = None
    pages = 0
    while True:
        url = "/api/workspace/audit/search?organization_id=default&box_id=ap-paginate&limit=2"
        if cursor:
            url += f"&cursor={cursor}"
        resp = client.get(url)
        assert resp.status_code == 200
        body = resp.json()
        seen.extend(e["id"] for e in body["events"])
        cursor = body.get("next_cursor")
        pages += 1
        if not cursor:
            break
        if pages > 5:
            pytest.fail("pagination loop did not terminate")

    assert seen == expected_order
    # 5 events / page-size 2 = 3 pages (2+2+1).
    assert pages == 3


def test_search_returns_no_cursor_when_results_fit_in_one_page(db, client_factory):
    _seed_event(db, box_id="ap-onepage", event_type="state_transition")
    client = client_factory(_admin_user)
    resp = client.get("/api/workspace/audit/search?organization_id=default&box_id=ap-onepage&limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["next_cursor"] is None
    assert len(body["events"]) >= 1
