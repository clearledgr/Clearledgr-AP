"""Tests for the approval-revert reversibility primitive.

Covers:
  * Happy path: an approved AP item within window reverts to
    needs_approval, clears approved_by/approved_at, records an
    approval_reverted audit event.
  * Expired window: outcome status='expired', state unchanged.
  * Invalid state: revert refused from states other than
    APPROVED / READY_TO_POST.
  * Tenant isolation: cross-org revert returns 404, not 403.
  * State machine: the new APPROVED → NEEDS_APPROVAL and
    READY_TO_POST → NEEDS_APPROVAL edges are valid.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.api import box_revert_routes  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.ap_states import APState, validate_transition  # noqa: E402
from solden.core.auth import get_current_user  # noqa: E402
from solden.services.approval_revert import (  # noqa: E402
    attempt_approval_revert,
)


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgRev", organization_name="orgRev")
    inst.ensure_organization("orgRev2", organization_name="orgRev2")
    return inst


def _user(org: str = "orgRev", uid: str = "user-1") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uid,
        email=f"{uid}@example.com",
        organization_id=org,
        role="user",
    )


@pytest.fixture()
def client_a(db):
    app = FastAPI()
    app.include_router(box_revert_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgRev")
    return TestClient(app)


@pytest.fixture()
def client_b(db):
    app = FastAPI()
    app.include_router(box_revert_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgRev2")
    return TestClient(app)


def _make_approved_item(db, *, item_id: str, approved_at: str, org: str = "orgRev") -> dict:
    db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Acme",
        "amount": 500.0,
        "state": "received",
    })
    for nxt in ("validated", "needs_approval", "approved"):
        db.update_ap_item(item_id, state=nxt)
    db.update_ap_item(item_id, approved_at=approved_at, approved_by="alice@example.com")
    return db.get_ap_item(item_id)


# ─── State machine edges ────────────────────────────────────────────


def test_state_machine_permits_approved_to_needs_approval():
    assert validate_transition(APState.APPROVED.value, APState.NEEDS_APPROVAL.value)


def test_state_machine_permits_ready_to_post_to_needs_approval():
    assert validate_transition(APState.READY_TO_POST.value, APState.NEEDS_APPROVAL.value)


# ─── attempt_approval_revert ────────────────────────────────────────


def test_revert_within_window_returns_to_needs_approval(db):
    now = datetime.now(timezone.utc)
    item = _make_approved_item(
        db, item_id="AP-rev-window", approved_at=now.isoformat(),
    )
    outcome = attempt_approval_revert(
        db=db,
        ap_item_id=item["id"],
        organization_id="orgRev",
        actor_id="operator@example.com",
        reason="caught wrong vendor before posting",
    )
    assert outcome.status == "reverted", outcome.to_dict()
    assert outcome.new_state == "needs_approval"
    assert outcome.window_seconds_remaining > 0

    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "needs_approval"
    assert not fresh.get("approved_at")
    assert not fresh.get("approved_by")

    events = db.list_ap_audit_events(item["id"])
    revert_events = [e for e in events if e.get("event_type") == "approval_reverted"]
    assert revert_events
    body = revert_events[-1].get("payload_json") or {}
    assert body.get("reason") == "caught wrong vendor before posting"


def test_revert_clears_stale_payment_scheduled_metadata(db):
    """Defensive: if metadata.payment_scheduled is set (e.g. by a
    future state-machine refactor that moves the marker earlier),
    revert from READY_TO_POST must clear it so the flag doesn't
    survive into needs_approval and mislead downstream observers."""
    now = datetime.now(timezone.utc)
    item = _make_approved_item(
        db, item_id="AP-rev-meta-cleanup", approved_at=now.isoformat(),
    )
    # Advance approved -> ready_to_post and seed the stale flag.
    db.update_ap_item(item["id"], state="ready_to_post")
    db.update_ap_item(item["id"], metadata={
        "payment_scheduled": True,
        "payment_scheduled_at": now.isoformat(),
        "unrelated_key": "preserve me",
    })

    outcome = attempt_approval_revert(
        db=db,
        ap_item_id=item["id"],
        organization_id="orgRev",
        actor_id="operator@example.com",
        reason="catch before ERP",
    )
    assert outcome.status == "reverted"

    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "needs_approval"
    meta = fresh.get("metadata") or {}
    assert "payment_scheduled" not in meta
    assert "payment_scheduled_at" not in meta
    # Other metadata keys must survive.
    assert meta.get("unrelated_key") == "preserve me"


def test_revert_after_window_expires_is_refused(db):
    expired = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    item = _make_approved_item(
        db, item_id="AP-rev-expired", approved_at=expired,
    )
    outcome = attempt_approval_revert(
        db=db,
        ap_item_id=item["id"],
        organization_id="orgRev",
        actor_id="operator@example.com",
        reason="too late",
    )
    assert outcome.status == "expired"
    assert outcome.window_seconds_remaining == 0
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "approved"


def test_revert_from_invalid_state_is_refused(db):
    db.create_ap_item({
        "id": "AP-rev-bad-state",
        "organization_id": "orgRev",
        "vendor_name": "Acme",
        "amount": 100.0,
        "state": "received",
    })
    outcome = attempt_approval_revert(
        db=db,
        ap_item_id="AP-rev-bad-state",
        organization_id="orgRev",
        actor_id="operator@example.com",
        reason="oops",
    )
    assert outcome.status == "invalid_state"
    fresh = db.get_ap_item("AP-rev-bad-state")
    assert fresh["state"] == "received"


def test_revert_cross_tenant_returns_not_found(db):
    now = datetime.now(timezone.utc)
    item = _make_approved_item(
        db, item_id="AP-rev-tenant", approved_at=now.isoformat(), org="orgRev",
    )
    outcome = attempt_approval_revert(
        db=db,
        ap_item_id=item["id"],
        organization_id="orgRev2",
        actor_id="operator@example.com",
        reason="cross-tenant probe",
    )
    assert outcome.status == "not_found"
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "approved"


# ─── POST endpoint ──────────────────────────────────────────────────


def test_revert_endpoint_200_within_window(db, client_a):
    now = datetime.now(timezone.utc)
    item = _make_approved_item(
        db, item_id="AP-rev-endpoint", approved_at=now.isoformat(),
    )
    resp = client_a.post(
        f"/api/workspace/ap-items/{item['id']}/revert-approval",
        json={"reason": "operator override"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "reverted"


def test_revert_endpoint_409_expired(db, client_a):
    expired = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    item = _make_approved_item(
        db, item_id="AP-rev-end-exp", approved_at=expired,
    )
    resp = client_a.post(
        f"/api/workspace/ap-items/{item['id']}/revert-approval",
        json={"reason": ""},
    )
    assert resp.status_code == 409


def test_revert_endpoint_404_cross_tenant(db, client_a, client_b):
    now = datetime.now(timezone.utc)
    item = _make_approved_item(
        db, item_id="AP-rev-end-xtenant", approved_at=now.isoformat(), org="orgRev",
    )
    resp = client_b.post(
        f"/api/workspace/ap-items/{item['id']}/revert-approval",
        json={"reason": ""},
    )
    assert resp.status_code == 404
