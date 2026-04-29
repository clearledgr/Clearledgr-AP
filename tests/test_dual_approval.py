"""Tests for Wave 6 / H1 — dual approval (two-person rule).

Covers:
  * State machine extension: NEEDS_SECOND_APPROVAL exists; valid
    transitions in/out wired (NEEDS_APPROVAL <-> NEEDS_SECOND_APPROVAL,
    NEEDS_SECOND_APPROVAL -> APPROVED / NEEDS_INFO / REJECTED).
  * Threshold lookup: default = infinity (disabled);
    set/get round-trip; settings_json[routing_thresholds] location.
  * Below-threshold first_approve advances directly to APPROVED.
  * Above-threshold first_approve -> NEEDS_SECOND_APPROVAL +
    first_approver/first_approved_at on metadata + audit event.
  * Second approver same as first -> 403 + blocked audit event,
    no state change.
  * Second approver = bill requester -> 403 + same.
  * Bill requester can't first_approve (SOX self-approval block).
  * Distinct second approver -> APPROVED + second_approver fields
    on metadata.
  * Revoke first sig -> NEEDS_APPROVAL; metadata cleared.
  * API: 5 endpoints (first / second / revoke / GET policy / PUT
    policy) with cross-org isolation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import dual_approval as da_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.ap_states import (  # noqa: E402
    APState,
    VALID_TRANSITIONS,
    validate_transition,
)
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.dual_approval import (  # noqa: E402
    DualApprovalNotPendingError,
    DualApprovalRequesterApprovalError,
    DualApprovalSelfApprovalError,
    first_approve,
    get_dual_approval_threshold,
    revoke_first_signature,
    second_approve,
    set_dual_approval_threshold,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta Co")
    return inst


def _user(uid: str = "user-1", org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uid, email=f"{uid}@orgA.com",
        organization_id=org, role="user",
    )


def _client(db, *, uid: str, org: str = "orgA") -> TestClient:
    app = FastAPI()
    app.include_router(da_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user(uid, org)
    return TestClient(app)


def _make_ap_item_at_needs_approval(
    db, *,
    item_id: str,
    amount: float,
    requester: str = "requester-1",
    org: str = "orgA",
) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Vendor X",
        "amount": amount,
        "currency": "USD",
        "state": "received",
        "user_id": requester,
    })
    for s in ("validated", "needs_approval"):
        db.update_ap_item(item["id"], state=s)
    return db.get_ap_item(item["id"])


def _meta(item: dict) -> dict:
    raw = item.get("metadata")
    if isinstance(raw, str):
        return json.loads(raw) if raw else {}
    return raw if isinstance(raw, dict) else {}


# ─── State machine extension ──────────────────────────────────────


def test_needs_second_approval_state_exists():
    assert APState.NEEDS_SECOND_APPROVAL.value == "needs_second_approval"
    assert APState.NEEDS_SECOND_APPROVAL in VALID_TRANSITIONS
    # Outbound: APPROVED, NEEDS_APPROVAL, REJECTED, NEEDS_INFO,
    # SNOOZED, CLOSED
    out = VALID_TRANSITIONS[APState.NEEDS_SECOND_APPROVAL]
    assert APState.APPROVED in out
    assert APState.NEEDS_APPROVAL in out
    assert APState.REJECTED in out


def test_needs_approval_can_advance_to_second_approval():
    assert validate_transition("needs_approval", "needs_second_approval") is True
    assert validate_transition("needs_second_approval", "approved") is True
    assert validate_transition("needs_second_approval", "needs_approval") is True
    # Forbidden: cannot leapfrog from validated to second_approval
    assert validate_transition("validated", "needs_second_approval") is False


# ─── Threshold lookup ─────────────────────────────────────────────


def test_threshold_default_infinity(db):
    assert get_dual_approval_threshold(db, "orgA") == float("inf")


def test_threshold_set_and_get(db):
    set_dual_approval_threshold(db, "orgA", 10000.0)
    assert get_dual_approval_threshold(db, "orgA") == 10000.0
    set_dual_approval_threshold(db, "orgA", None)
    assert get_dual_approval_threshold(db, "orgA") == float("inf")


# ─── First-signature paths ────────────────────────────────────────


def test_below_threshold_advances_to_approved(db):
    set_dual_approval_threshold(db, "orgA", 10000.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-below", amount=500.0,
    )
    result = first_approve(
        db, organization_id="orgA",
        ap_item_id=item["id"], approver_id="approver-1",
    )
    assert result.new_state == "approved"
    assert result.requires_second_signature is False
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "approved"
    assert fresh["approved_by"] == "approver-1"


def test_above_threshold_lands_at_needs_second_approval(db):
    set_dual_approval_threshold(db, "orgA", 1000.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-above", amount=5000.0,
    )
    result = first_approve(
        db, organization_id="orgA",
        ap_item_id=item["id"], approver_id="approver-1",
        approver_email="alice@orgA.com",
    )
    assert result.new_state == "needs_second_approval"
    assert result.requires_second_signature is True
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "needs_second_approval"
    meta = _meta(fresh)
    assert meta["first_approver"] == "approver-1"
    assert meta["first_approver_email"] == "alice@orgA.com"
    assert meta["first_approved_at"]


def test_first_signature_audit_event_emitted(db):
    set_dual_approval_threshold(db, "orgA", 100.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-audit", amount=500.0,
    )
    first_approve(
        db, organization_id="orgA",
        ap_item_id=item["id"], approver_id="approver-1",
    )
    expected_key = f"dual_approval_first:orgA:{item['id']}:approver-1"
    fetched = db.get_ap_audit_event_by_key(expected_key)
    assert fetched is not None
    assert fetched["event_type"] == "dual_approval_first_signature"


def test_requester_cannot_first_approve(db):
    set_dual_approval_threshold(db, "orgA", 100.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-self", amount=500.0,
        requester="approver-1",
    )
    with pytest.raises(DualApprovalRequesterApprovalError):
        first_approve(
            db, organization_id="orgA",
            ap_item_id=item["id"], approver_id="approver-1",
        )
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "needs_approval"


# ─── Second-signature paths ───────────────────────────────────────


def test_second_approver_distinct_advances_to_approved(db):
    set_dual_approval_threshold(db, "orgA", 100.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-second", amount=5000.0,
    )
    first_approve(
        db, organization_id="orgA",
        ap_item_id=item["id"], approver_id="approver-1",
    )
    result = second_approve(
        db, organization_id="orgA",
        ap_item_id=item["id"], approver_id="approver-2",
        approver_email="bob@orgA.com",
    )
    assert result.new_state == "approved"
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "approved"
    meta = _meta(fresh)
    assert meta["first_approver"] == "approver-1"
    assert meta["second_approver"] == "approver-2"
    assert meta["second_approver_email"] == "bob@orgA.com"


def test_second_approver_same_as_first_blocked(db):
    set_dual_approval_threshold(db, "orgA", 100.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-same", amount=5000.0,
    )
    first_approve(
        db, organization_id="orgA",
        ap_item_id=item["id"], approver_id="approver-1",
    )
    with pytest.raises(DualApprovalSelfApprovalError):
        second_approve(
            db, organization_id="orgA",
            ap_item_id=item["id"], approver_id="approver-1",
        )
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "needs_second_approval"  # unchanged


def test_second_approver_is_requester_blocked(db):
    set_dual_approval_threshold(db, "orgA", 100.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-req",
        amount=5000.0,
        requester="approver-2",
    )
    first_approve(
        db, organization_id="orgA",
        ap_item_id=item["id"], approver_id="approver-1",
    )
    with pytest.raises(DualApprovalRequesterApprovalError):
        second_approve(
            db, organization_id="orgA",
            ap_item_id=item["id"], approver_id="approver-2",
        )


def test_second_approve_when_not_pending_raises(db):
    set_dual_approval_threshold(db, "orgA", 1_000_000.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-not-pending", amount=500.0,
    )
    # Below threshold, first approve goes straight to approved.
    first_approve(
        db, organization_id="orgA",
        ap_item_id=item["id"], approver_id="approver-1",
    )
    with pytest.raises(DualApprovalNotPendingError):
        second_approve(
            db, organization_id="orgA",
            ap_item_id=item["id"], approver_id="approver-2",
        )


# ─── Revoke ───────────────────────────────────────────────────────


def test_revoke_returns_to_needs_approval(db):
    set_dual_approval_threshold(db, "orgA", 100.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-revoke", amount=5000.0,
    )
    first_approve(
        db, organization_id="orgA",
        ap_item_id=item["id"], approver_id="approver-1",
    )
    result = revoke_first_signature(
        db, organization_id="orgA",
        ap_item_id=item["id"], actor_id="approver-1",
        reason="needed more details from vendor",
    )
    assert result.new_state == "needs_approval"
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "needs_approval"
    meta = _meta(fresh)
    assert "first_approver" not in meta


def test_revoke_when_not_pending_raises(db):
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-rev-bad", amount=500.0,
    )
    with pytest.raises(DualApprovalNotPendingError):
        revoke_first_signature(
            db, organization_id="orgA",
            ap_item_id=item["id"], actor_id="approver-1",
        )


# ─── API ───────────────────────────────────────────────────────────


def test_api_first_approve_below_threshold(db):
    client = _client(db, uid="approver-1")
    set_dual_approval_threshold(db, "orgA", 10000.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-api-1", amount=500.0,
    )
    resp = client.post(
        f"/api/workspace/ap-items/{item['id']}/approve/first",
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["new_state"] == "approved"


def test_api_full_two_signature_flow(db):
    client_a = _client(db, uid="alice")
    client_b = _client(db, uid="bob")
    set_dual_approval_threshold(db, "orgA", 100.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-api-2", amount=5000.0,
    )
    first = client_a.post(
        f"/api/workspace/ap-items/{item['id']}/approve/first",
    )
    assert first.json()["new_state"] == "needs_second_approval"
    second = client_b.post(
        f"/api/workspace/ap-items/{item['id']}/approve/second",
    )
    assert second.status_code == 200
    assert second.json()["new_state"] == "approved"


def test_api_self_approval_403(db):
    client_a = _client(db, uid="alice")
    set_dual_approval_threshold(db, "orgA", 100.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-api-self", amount=5000.0,
    )
    client_a.post(f"/api/workspace/ap-items/{item['id']}/approve/first")
    resp = client_a.post(
        f"/api/workspace/ap-items/{item['id']}/approve/second",
    )
    assert resp.status_code == 403


def test_api_revoke_endpoint(db):
    client_a = _client(db, uid="alice")
    set_dual_approval_threshold(db, "orgA", 100.0)
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-api-rev", amount=5000.0,
    )
    client_a.post(f"/api/workspace/ap-items/{item['id']}/approve/first")
    resp = client_a.post(
        f"/api/workspace/ap-items/{item['id']}/approve/revoke",
        json={"reason": "wait for PO"},
    )
    assert resp.status_code == 200
    assert resp.json()["new_state"] == "needs_approval"


def test_api_get_put_policy(db):
    client = _client(db, uid="alice")
    resp = client.get("/api/workspace/policy/dual-approval")
    assert resp.status_code == 200
    assert resp.json()["dual_approval_threshold"] is None

    put_resp = client.put(
        "/api/workspace/policy/dual-approval",
        json={"dual_approval_threshold": 10000.0},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["dual_approval_threshold"] == 10000.0


def test_api_cross_org_404(db):
    client_a = _client(db, uid="alice", org="orgA")
    item = _make_ap_item_at_needs_approval(
        db, item_id="AP-da-cross", amount=500.0, org="orgB",
    )
    resp = client_a.post(
        f"/api/workspace/ap-items/{item['id']}/approve/first",
    )
    assert resp.status_code == 404
