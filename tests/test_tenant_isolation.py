"""Cross-tenant data-isolation regression fence.

Every multi-tenant API must satisfy two contracts:

1. **Query-param spoofing is blocked.** If a user authenticated for
   org-A passes ``?organization_id=org-B`` on any endpoint, the
   request is rejected with 403 — not transparently filtered or
   silently accepted. Source: :func:`clearledgr.api.deps.soft_org_guard`.

2. **Resource-level org mismatch is blocked.** If a user authenticated
   for org-A passes a path param (``/api/ap/items/{id}``) where the
   resource belongs to org-B, the request is rejected with 403 or 404
   — NEVER 200 with other-tenant data in the body.

These tests are the regression fence: a future engineer who adds a
new list/get endpoint and forgets to filter by ``organization_id``
trips one of these tests instead of shipping a cross-tenant leak.

We walk representative endpoints across three router groups:
- ``/extension/*`` (Gmail extension surfaces)
- ``/api/ap/items/*`` (AP core read + write)
- ``/api/ops/*`` (admin / ops console)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import main as _main  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import TokenData, get_current_user  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


ORG_A = "tenant-a"
ORG_B = "tenant-b"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    # Ensure org rows exist so any FK-ish checks pass.
    inst.create_organization(ORG_A, name="Tenant A")
    inst.create_organization(ORG_B, name="Tenant B")
    return inst


def _user_for(org_id: str) -> TokenData:
    return TokenData(
        user_id=f"user-{org_id}",
        email=f"ops@{org_id}.test",
        organization_id=org_id,
        role="user",
        exp=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.fixture()
def client_as_org_a(db):
    _main.app.dependency_overrides[get_current_user] = lambda: _user_for(ORG_A)
    try:
        yield TestClient(_main.app)
    finally:
        _main.app.dependency_overrides.pop(get_current_user, None)


def _seed_ap_item(db, item_id: str, org_id: str, state: str = "needs_approval") -> dict:
    return db.create_ap_item({
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"thread-{item_id}",
        "message_id": f"msg-{item_id}",
        "subject": f"Invoice for {org_id}",
        "sender": f"billing@{org_id}.example",
        "vendor_name": "Acme",
        "amount": 1000.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": state,
        "organization_id": org_id,
    })


# ---------------------------------------------------------------------------
# Contract 1: query-param spoofing blocked by soft_org_guard
# ---------------------------------------------------------------------------


class TestQueryParamOrgSpoofing:
    """An org-A user MUST NOT be able to ask for org-B data by passing
    ``?organization_id=org-B`` on any JWT-authenticated endpoint."""

    @pytest.mark.parametrize("path", [
        # Extension surfaces (AP-first data surfaces for Gmail sidebar)
        "/extension/pipeline",
        "/extension/worklist",
        # Ops / admin surfaces — these were the places the earlier audit
        # flagged as "org filter present but no dedicated test".
        "/api/ops/ap-kpis",
        "/api/ops/retry-queue",
    ])
    def test_cross_tenant_query_param_rejected(self, client_as_org_a, path):
        response = client_as_org_a.get(f"{path}?organization_id={ORG_B}")
        # soft_org_guard raises 403 with detail "org_mismatch: ...".
        # A 2xx with actual data would be a tenant-leak bug.
        assert response.status_code == 403, (
            f"{path} accepted cross-tenant ?organization_id= "
            f"(status={response.status_code}, body={response.text[:200]})"
        )
        detail = response.json().get("detail") or response.json().get("error", "")
        assert "org_mismatch" in str(detail).lower(), (
            f"{path} returned 403 but not an org_mismatch — "
            f"detail={detail!r}"
        )

    def test_own_org_query_param_accepted(self, client_as_org_a):
        # Control: the SAME endpoint with the token's own org passes
        # the soft_org_guard. Any non-403 status is fine — the test
        # is about the guard, not the handler.
        response = client_as_org_a.get(
            f"/extension/worklist?organization_id={ORG_A}"
        )
        assert response.status_code != 403, (
            "Own-org query should pass soft_org_guard"
        )


# ---------------------------------------------------------------------------
# Contract 2: resource-level org mismatch blocked
# ---------------------------------------------------------------------------


class TestResourceLevelOrgMismatch:
    """An org-A user MUST NOT be able to read a resource whose row
    stores ``organization_id = org-B`` via a path-param endpoint."""

    def test_get_ap_item_cross_tenant_rejected(self, client_as_org_a, db):
        # Seed an item in ORG_B; org-A token requests it. Either 403
        # (explicit org check) or 404 (item not found for this org)
        # are acceptable — both deny. 200 with the item's data would
        # be a leak.
        _seed_ap_item(db, "OTHER-ORG-ITEM", ORG_B)
        r = client_as_org_a.get(
            f"/api/ap/items/OTHER-ORG-ITEM?organization_id={ORG_A}"
        )
        assert r.status_code in (403, 404), (
            f"cross-tenant GET returned {r.status_code} with body "
            f"{r.text[:200]} — must be 403 or 404"
        )
        # If 200 slipped through, sensitive fields must not appear.
        if r.status_code == 200:
            pytest.fail(
                "cross-tenant GET returned 200 — data leak: "
                f"{r.text[:400]}"
            )

    def test_post_snooze_cross_tenant_rejected(self, client_as_org_a, db):
        _seed_ap_item(db, "OTHER-ORG-SNOOZE", ORG_B, state="needs_approval")
        r = client_as_org_a.post(
            f"/api/ap/items/OTHER-ORG-SNOOZE/snooze?organization_id={ORG_A}",
            json={"duration_minutes": 60},
        )
        # Snooze must reject cross-tenant writes. 403/404 both OK.
        assert r.status_code in (403, 404), (
            f"cross-tenant snooze returned {r.status_code} — "
            f"must be 403/404. body={r.text[:200]}"
        )

    def test_bulk_approve_silently_skips_cross_tenant_ids(
        self, client_as_org_a, db,
    ):
        # Bulk endpoints use per-item result rows rather than fail
        # the whole batch. An ID belonging to a different org must
        # appear in the ``results`` list with a NON-ok status — never
        # as ``status=approved``.
        _seed_ap_item(db, "OTHER-ORG-BULK", ORG_B, state="needs_approval")
        r = client_as_org_a.post(
            f"/api/ap/items/bulk-approve?organization_id={ORG_A}",
            json={"ap_item_ids": ["OTHER-ORG-BULK"]},
        )
        if r.status_code == 403:
            return  # soft_org_guard rejected outright — good.
        assert r.status_code == 200, r.text
        body = r.json()
        # Every result entry that references the other-org ID must
        # report an error status, not ``approved``/``ok``.
        results = body.get("results", [])
        assert results, "bulk-approve returned empty results for non-empty input"
        for entry in results:
            if entry.get("ap_item_id") == "OTHER-ORG-BULK":
                assert entry.get("ok") is not True, (
                    "bulk-approve accepted a cross-tenant ap_item_id — "
                    "this is a tenant-leak bug"
                )
                assert entry.get("status") != "approved"


# ---------------------------------------------------------------------------
# Contract 3: list endpoints with own-org token return own-org data only
# ---------------------------------------------------------------------------


class TestListEndpointOrgFiltering:
    """Without any ``organization_id`` query param, a list endpoint
    must return only data owned by the token's org — never merge data
    across tenants into one response."""

    def test_worklist_returns_only_own_org_items(self, client_as_org_a, db):
        own_id = "WORKLIST-OWN"
        other_id = "WORKLIST-OTHER"
        _seed_ap_item(db, own_id, ORG_A, state="needs_approval")
        _seed_ap_item(db, other_id, ORG_B, state="needs_approval")

        r = client_as_org_a.get(
            f"/extension/worklist?organization_id={ORG_A}"
        )
        assert r.status_code == 200, r.text
        items = r.json().get("items", [])
        ids = {i.get("id") for i in items}
        assert own_id in ids, (
            "own-org worklist entry missing from response"
        )
        assert other_id not in ids, (
            f"worklist leaked cross-tenant item {other_id} into "
            f"{ORG_A}'s response — tenant isolation broken"
        )

    def test_pipeline_returns_only_own_org_items(self, client_as_org_a, db):
        own_id = "PIPELINE-OWN"
        other_id = "PIPELINE-OTHER"
        _seed_ap_item(db, own_id, ORG_A, state="needs_approval")
        _seed_ap_item(db, other_id, ORG_B, state="needs_approval")

        r = client_as_org_a.get(
            f"/extension/pipeline?organization_id={ORG_A}"
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        # Flatten every bucket's items.
        all_ids = set()
        for bucket in payload.values():
            if isinstance(bucket, list):
                all_ids.update(e.get("id") for e in bucket if isinstance(e, dict))
        assert own_id in all_ids
        assert other_id not in all_ids, (
            f"pipeline leaked cross-tenant item {other_id}"
        )
