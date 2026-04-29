"""Tests for Wave 2 / C4 — manual payment confirmation API.

Covers the four endpoints under ``/api/workspace``:

  * POST /payment-confirmations
  * GET /payment-confirmations
  * GET /payment-confirmations/{id}
  * GET /ap-items/{id}/payment-confirmations

Concerns:
  * Auth: every endpoint requires a workspace user; tests use a stub
    via dependency_overrides.
  * Tenant isolation: an org A user cannot see / create / fetch an
    org B confirmation; cross-org access surfaces as 404, not 403,
    so we don't leak existence.
  * State walk: POST drives the AP item through the same canonical
    state path as the ERP webhooks (posted_to_erp ->
    awaiting_payment -> payment_executed).
  * Idempotency: the route is a thin shim over
    record_payment_confirmation, so the duplicate flag bubbles up.
  * Validation: invalid status, missing AP item, etc., return 4xx.
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

from clearledgr.api import payment_confirmations as pc_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="orgA")
    inst.ensure_organization("orgB", organization_name="orgB")
    return inst


def _user(org: str = "orgA", uid: str = "user-1") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uid,
        email=f"{uid}@example.com",
        organization_id=org,
        role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(pc_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


@pytest.fixture()
def client_orgB(db):
    app = FastAPI()
    app.include_router(pc_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgB")
    return TestClient(app)


def _make_awaiting_ap_item(
    db, *, item_id: str, org: str = "orgA",
) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Acme",
        "amount": 1500.0,
        "state": "received",
    })
    for s in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp", "awaiting_payment",
    ):
        db.update_ap_item(item["id"], state=s)
    return db.get_ap_item(item["id"])


# ─── POST /payment-confirmations ────────────────────────────────────


def test_post_records_confirmation_and_walks_state(db, client_orgA):
    item = _make_awaiting_ap_item(db, item_id="AP-c4-post-1")
    body = {
        "ap_item_id": item["id"],
        "payment_id": "wire-100",
        "source": "manual",
        "status": "confirmed",
        "settlement_at": "2026-04-29T10:00:00+00:00",
        "amount": 1500.00,
        "currency": "EUR",
        "method": "wire",
        "payment_reference": "WIRE-REF-100",
        "bank_account_last4": "4242",
        "notes": "Treasury portal wire executed by R. Patel",
    }
    resp = client_orgA.post("/api/workspace/payment-confirmations", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["duplicate"] is False
    assert data["ap_state_before"] == "awaiting_payment"
    assert data["ap_state_after"] == "payment_executed"
    assert data["confirmation"]["payment_id"] == "wire-100"
    assert data["confirmation"]["bank_account_last4"] == "4242"

    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "payment_executed"


def test_post_idempotent_redelivery(db, client_orgA):
    item = _make_awaiting_ap_item(db, item_id="AP-c4-idem")
    body = {
        "ap_item_id": item["id"],
        "payment_id": "wire-IDEM",
        "source": "manual",
        "status": "confirmed",
    }
    first = client_orgA.post("/api/workspace/payment-confirmations", json=body)
    second = client_orgA.post("/api/workspace/payment-confirmations", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert (
        first.json()["confirmation"]["id"]
        == second.json()["confirmation"]["id"]
    )


def test_post_invalid_status_rejected(db, client_orgA):
    item = _make_awaiting_ap_item(db, item_id="AP-c4-bad-status")
    body = {
        "ap_item_id": item["id"],
        "payment_id": "wire-BAD",
        "source": "manual",
        "status": "paid",  # not in valid set
    }
    resp = client_orgA.post("/api/workspace/payment-confirmations", json=body)
    assert resp.status_code == 422


def test_post_missing_ap_item_returns_404(db, client_orgA):
    body = {
        "ap_item_id": "AP-does-not-exist",
        "payment_id": "wire-X",
        "source": "manual",
        "status": "confirmed",
    }
    resp = client_orgA.post("/api/workspace/payment-confirmations", json=body)
    assert resp.status_code == 404


def test_post_cross_org_ap_item_returns_404(db, client_orgA):
    """orgA user cannot record a payment against an orgB AP item;
    we return 404 not 403 to avoid leaking existence."""
    other = _make_awaiting_ap_item(db, item_id="AP-c4-other-org", org="orgB")
    body = {
        "ap_item_id": other["id"],
        "payment_id": "wire-LEAK",
        "source": "manual",
        "status": "confirmed",
    }
    resp = client_orgA.post("/api/workspace/payment-confirmations", json=body)
    assert resp.status_code == 404


def test_post_failed_status_walks_to_payment_failed(db, client_orgA):
    item = _make_awaiting_ap_item(db, item_id="AP-c4-failed")
    body = {
        "ap_item_id": item["id"],
        "payment_id": "wire-FAIL",
        "source": "manual",
        "status": "failed",
        "failure_reason": "insufficient_funds",
    }
    resp = client_orgA.post("/api/workspace/payment-confirmations", json=body)
    assert resp.status_code == 200
    assert resp.json()["ap_state_after"] == "payment_failed"


# ─── GET /payment-confirmations ─────────────────────────────────────


def test_list_org_scoped(db, client_orgA, client_orgB):
    a = _make_awaiting_ap_item(db, item_id="AP-c4-list-A", org="orgA")
    b = _make_awaiting_ap_item(db, item_id="AP-c4-list-B", org="orgB")
    client_orgA.post("/api/workspace/payment-confirmations", json={
        "ap_item_id": a["id"], "payment_id": "PA",
        "source": "manual", "status": "confirmed",
    })
    client_orgB.post("/api/workspace/payment-confirmations", json={
        "ap_item_id": b["id"], "payment_id": "PB",
        "source": "manual", "status": "confirmed",
    })

    resp_a = client_orgA.get("/api/workspace/payment-confirmations")
    resp_b = client_orgB.get("/api/workspace/payment-confirmations")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    a_ids = {r["payment_id"] for r in resp_a.json()}
    b_ids = {r["payment_id"] for r in resp_b.json()}
    assert "PA" in a_ids and "PB" not in a_ids
    assert "PB" in b_ids and "PA" not in b_ids


def test_list_filter_by_status(db, client_orgA):
    """Two AP items, one with a confirmed payment and one that
    failed. Filtering by status must scope correctly."""
    a = _make_awaiting_ap_item(db, item_id="AP-c4-filter-OK")
    b = _make_awaiting_ap_item(db, item_id="AP-c4-filter-FAIL")
    client_orgA.post("/api/workspace/payment-confirmations", json={
        "ap_item_id": a["id"], "payment_id": "OK",
        "source": "manual", "status": "confirmed",
    })
    client_orgA.post("/api/workspace/payment-confirmations", json={
        "ap_item_id": b["id"], "payment_id": "X",
        "source": "manual", "status": "failed",
        "failure_reason": "bank_returned",
    })
    resp = client_orgA.get(
        "/api/workspace/payment-confirmations?status=failed",
    )
    assert resp.status_code == 200
    pids = {r["payment_id"] for r in resp.json()}
    assert "X" in pids
    assert "OK" not in pids


def test_list_invalid_status_filter_returns_400(client_orgA):
    resp = client_orgA.get(
        "/api/workspace/payment-confirmations?status=not-a-status",
    )
    assert resp.status_code == 400


# ─── GET /payment-confirmations/{id} ────────────────────────────────


def test_get_by_id(db, client_orgA):
    item = _make_awaiting_ap_item(db, item_id="AP-c4-byid")
    posted = client_orgA.post(
        "/api/workspace/payment-confirmations",
        json={
            "ap_item_id": item["id"], "payment_id": "P-BYID",
            "source": "manual", "status": "confirmed",
        },
    )
    pc_id = posted.json()["confirmation"]["id"]
    resp = client_orgA.get(f"/api/workspace/payment-confirmations/{pc_id}")
    assert resp.status_code == 200
    assert resp.json()["payment_id"] == "P-BYID"


def test_get_by_id_cross_org_404(db, client_orgA, client_orgB):
    item_b = _make_awaiting_ap_item(db, item_id="AP-c4-byid-other", org="orgB")
    posted = client_orgB.post(
        "/api/workspace/payment-confirmations",
        json={
            "ap_item_id": item_b["id"], "payment_id": "P-OTHER",
            "source": "manual", "status": "confirmed",
        },
    )
    pc_id = posted.json()["confirmation"]["id"]
    resp = client_orgA.get(f"/api/workspace/payment-confirmations/{pc_id}")
    assert resp.status_code == 404


# ─── GET /ap-items/{id}/payment-confirmations ───────────────────────


def test_list_for_ap_item(db, client_orgA):
    item = _make_awaiting_ap_item(db, item_id="AP-c4-feed")
    # First attempt: failure
    client_orgA.post("/api/workspace/payment-confirmations", json={
        "ap_item_id": item["id"], "payment_id": "FAIL-1",
        "source": "manual", "status": "failed",
        "failure_reason": "wrong_account",
    })
    # Retry: success
    client_orgA.post("/api/workspace/payment-confirmations", json={
        "ap_item_id": item["id"], "payment_id": "OK-2",
        "source": "manual", "status": "confirmed",
    })
    resp = client_orgA.get(
        f"/api/workspace/ap-items/{item['id']}/payment-confirmations",
    )
    assert resp.status_code == 200
    assert {r["payment_id"] for r in resp.json()} == {"FAIL-1", "OK-2"}


def test_list_for_ap_item_cross_org_404(db, client_orgA, client_orgB):
    other = _make_awaiting_ap_item(db, item_id="AP-c4-feed-other", org="orgB")
    resp = client_orgA.get(
        f"/api/workspace/ap-items/{other['id']}/payment-confirmations",
    )
    assert resp.status_code == 404
