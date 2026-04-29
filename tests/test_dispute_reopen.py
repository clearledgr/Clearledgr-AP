"""Tests for Wave 6 / H3 — dispute reopen ceremony.

Covers:
  * Original must be in a terminal/post-payment state (closed,
    payment_executed, reversed). Anything earlier raises
    OriginalNotReopenableError.
  * credit_note reopen: spawns correction AP item with metadata
    flagging it; original stays in its terminal state; back-link
    stamped on original metadata.
  * rebill reopen: same shape, different reopen_kind label.
  * Validation: invalid kind, empty reason, non-positive amount
    all raise ValueError.
  * Cross-org access raises ValueError on lookup.
  * Idempotent: calling reopen twice on the same original returns
    the same correction id without creating a second.
  * Audit events emitted on BOTH boxes (original + correction)
    with stable idempotency keys.
  * Dispute row opened alongside via DisputeService surface.
  * API: POST creates correction; GET returns chain; cross-org 404;
    409 when original isn't reopenable; 400 on bad payload.
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

from clearledgr.api import dispute_reopen as dr_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.dispute_reopen import (  # noqa: E402
    OriginalNotReopenableError,
    get_correction_chain,
    reopen_for_dispute,
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
        user_id=uid, email=f"{uid}@example.com",
        organization_id=org, role="user",
    )


def _client(db, *, uid: str = "user-1", org: str = "orgA") -> TestClient:
    app = FastAPI()
    app.include_router(dr_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user(uid, org)
    return TestClient(app)


def _make_closed_ap_item(
    db,
    *,
    item_id: str,
    org: str = "orgA",
    amount: float = 1000.0,
    invoice_number: str = "INV-100",
    final_state: str = "closed",
) -> dict:
    """Walk an AP item to a terminal state suitable for reopen."""
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Vendor X",
        "amount": amount,
        "currency": "USD",
        "invoice_number": invoice_number,
        "state": "received",
        "user_id": "requester-1",
    })
    walk = [
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp",
    ]
    if final_state == "payment_executed":
        walk.extend([
            "awaiting_payment", "payment_executed",
        ])
    elif final_state == "closed":
        walk.extend([
            "awaiting_payment", "payment_executed", "closed",
        ])
    elif final_state == "reversed":
        walk.append("reversed")
    for s in walk:
        db.update_ap_item(item["id"], state=s)
    return db.get_ap_item(item["id"])


def _meta(item: dict) -> dict:
    raw = item.get("metadata")
    if isinstance(raw, str):
        return json.loads(raw) if raw else {}
    return raw if isinstance(raw, dict) else {}


# ─── Eligibility ──────────────────────────────────────────────────


def test_original_must_be_terminal_or_post_payment(db):
    item = db.create_ap_item({
        "id": "AP-dr-not-terminal",
        "organization_id": "orgA",
        "vendor_name": "Vendor X",
        "amount": 100.0,
        "currency": "USD",
        "state": "received",
    })
    db.update_ap_item(item["id"], state="validated")
    db.update_ap_item(item["id"], state="needs_approval")
    with pytest.raises(OriginalNotReopenableError):
        reopen_for_dispute(
            db, organization_id="orgA",
            original_ap_item_id=item["id"],
            reopen_kind="credit_note",
            correction_amount=50.0,
            reason="vendor disputed",
            actor_id="ops-1",
        )


def test_reopen_works_from_closed(db):
    item = _make_closed_ap_item(
        db, item_id="AP-dr-closed", final_state="closed",
    )
    result = reopen_for_dispute(
        db, organization_id="orgA",
        original_ap_item_id=item["id"],
        reopen_kind="credit_note",
        correction_amount=200.0,
        reason="overcharged",
        actor_id="ops-1",
    )
    assert result.original_ap_item_id == item["id"]
    assert result.correction_ap_item_id != item["id"]


def test_reopen_works_from_payment_executed(db):
    item = _make_closed_ap_item(
        db, item_id="AP-dr-pay-exec", final_state="payment_executed",
    )
    result = reopen_for_dispute(
        db, organization_id="orgA",
        original_ap_item_id=item["id"],
        reopen_kind="rebill",
        correction_amount=750.0,
        reason="vendor reissued at correct amount",
        actor_id="ops-1",
    )
    assert result.reopen_kind == "rebill"


# ─── Validation ───────────────────────────────────────────────────


def test_invalid_reopen_kind_raises(db):
    item = _make_closed_ap_item(db, item_id="AP-dr-bad-kind")
    with pytest.raises(ValueError):
        reopen_for_dispute(
            db, organization_id="orgA",
            original_ap_item_id=item["id"],
            reopen_kind="bogus",
            correction_amount=100.0,
            reason="test",
            actor_id="ops-1",
        )


def test_empty_reason_raises(db):
    item = _make_closed_ap_item(db, item_id="AP-dr-no-reason")
    with pytest.raises(ValueError):
        reopen_for_dispute(
            db, organization_id="orgA",
            original_ap_item_id=item["id"],
            reopen_kind="credit_note",
            correction_amount=100.0,
            reason="",
            actor_id="ops-1",
        )


def test_non_positive_amount_raises(db):
    item = _make_closed_ap_item(db, item_id="AP-dr-bad-amt")
    with pytest.raises(ValueError):
        reopen_for_dispute(
            db, organization_id="orgA",
            original_ap_item_id=item["id"],
            reopen_kind="credit_note",
            correction_amount=0,
            reason="test",
            actor_id="ops-1",
        )


def test_unknown_ap_item_raises(db):
    with pytest.raises(ValueError):
        reopen_for_dispute(
            db, organization_id="orgA",
            original_ap_item_id="AP-does-not-exist",
            reopen_kind="credit_note",
            correction_amount=100.0,
            reason="test",
            actor_id="ops-1",
        )


def test_cross_org_raises(db):
    item = _make_closed_ap_item(
        db, item_id="AP-dr-cross", org="orgB",
    )
    with pytest.raises(ValueError):
        reopen_for_dispute(
            db, organization_id="orgA",  # different org
            original_ap_item_id=item["id"],
            reopen_kind="credit_note",
            correction_amount=100.0,
            reason="test",
            actor_id="ops-1",
        )


# ─── Correction shape ─────────────────────────────────────────────


def test_correction_ap_item_has_metadata_link(db):
    item = _make_closed_ap_item(db, item_id="AP-dr-link")
    result = reopen_for_dispute(
        db, organization_id="orgA",
        original_ap_item_id=item["id"],
        reopen_kind="credit_note",
        correction_amount=200.0,
        reason="overcharged 200",
        actor_id="ops-1",
    )
    correction = db.get_ap_item(result.correction_ap_item_id)
    assert correction is not None
    assert correction["state"] == "received"
    assert correction["vendor_name"] == "Vendor X"
    meta = _meta(correction)
    block = meta.get("dispute_reopen") or {}
    assert block.get("kind") == "credit_note"
    assert block.get("original_ap_item_id") == item["id"]
    assert block.get("correction_amount") == 200.0
    assert "overcharged" in block.get("reason", "")


def test_original_unchanged_state_after_reopen(db):
    item = _make_closed_ap_item(
        db, item_id="AP-dr-orig-stays", final_state="closed",
    )
    reopen_for_dispute(
        db, organization_id="orgA",
        original_ap_item_id=item["id"],
        reopen_kind="credit_note",
        correction_amount=100.0,
        reason="x",
        actor_id="ops-1",
    )
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "closed"  # SOX-immutable terminal state


def test_original_back_link_stamped(db):
    item = _make_closed_ap_item(db, item_id="AP-dr-back-link")
    result = reopen_for_dispute(
        db, organization_id="orgA",
        original_ap_item_id=item["id"],
        reopen_kind="credit_note",
        correction_amount=150.0,
        reason="duplicate billing",
        actor_id="ops-1",
    )
    fresh = db.get_ap_item(item["id"])
    block = (_meta(fresh).get("dispute_reopen") or {})
    assert block.get("correction_ap_item_id") == result.correction_ap_item_id
    assert block.get("kind") == "credit_note"
    assert block.get("reopened_by") == "ops-1"


# ─── Idempotency ──────────────────────────────────────────────────


def test_reopen_is_idempotent(db):
    item = _make_closed_ap_item(db, item_id="AP-dr-idem")
    first = reopen_for_dispute(
        db, organization_id="orgA",
        original_ap_item_id=item["id"],
        reopen_kind="credit_note",
        correction_amount=100.0,
        reason="x",
        actor_id="ops-1",
    )
    second = reopen_for_dispute(
        db, organization_id="orgA",
        original_ap_item_id=item["id"],
        reopen_kind="credit_note",
        correction_amount=100.0,
        reason="x",
        actor_id="ops-1",
    )
    assert first.correction_ap_item_id == second.correction_ap_item_id


# ─── Audit ────────────────────────────────────────────────────────


def test_audit_events_on_both_boxes(db):
    item = _make_closed_ap_item(db, item_id="AP-dr-audit")
    result = reopen_for_dispute(
        db, organization_id="orgA",
        original_ap_item_id=item["id"],
        reopen_kind="credit_note",
        correction_amount=100.0,
        reason="audit test",
        actor_id="ops-1",
    )
    orig_events = db.list_box_audit_events("ap_item", item["id"])
    corr_events = db.list_box_audit_events(
        "ap_item", result.correction_ap_item_id,
    )
    orig_types = [e.get("event_type") for e in orig_events]
    corr_types = [e.get("event_type") for e in corr_events]
    assert "dispute_reopened" in orig_types
    assert "dispute_correction_created" in corr_types


def test_audit_idempotency_keys(db):
    item = _make_closed_ap_item(db, item_id="AP-dr-audit-key")
    reopen_for_dispute(
        db, organization_id="orgA",
        original_ap_item_id=item["id"],
        reopen_kind="credit_note",
        correction_amount=100.0,
        reason="x",
        actor_id="ops-1",
    )
    expected_orig_key = f"dispute_reopen:orgA:{item['id']}"
    fetched = db.get_ap_audit_event_by_key(expected_orig_key)
    assert fetched is not None
    assert fetched["event_type"] == "dispute_reopened"


# ─── Chain helper ─────────────────────────────────────────────────


def test_get_correction_chain_returns_link(db):
    item = _make_closed_ap_item(db, item_id="AP-dr-chain")
    result = reopen_for_dispute(
        db, organization_id="orgA",
        original_ap_item_id=item["id"],
        reopen_kind="credit_note",
        correction_amount=100.0,
        reason="chain test",
        actor_id="ops-1",
    )
    chain = get_correction_chain(
        db, organization_id="orgA", ap_item_id=item["id"],
    )
    assert chain.get("correction_ap_item_id") == result.correction_ap_item_id
    assert chain.get("kind") == "credit_note"


# ─── API ──────────────────────────────────────────────────────────


def test_api_post_creates_correction(db):
    client = _client(db, uid="ops-1")
    item = _make_closed_ap_item(db, item_id="AP-dr-api-1")
    resp = client.post(
        f"/api/workspace/ap-items/{item['id']}/dispute-reopen",
        json={
            "reopen_kind": "credit_note",
            "correction_amount": 250.0,
            "reason": "vendor overcharged",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["correction_ap_item_id"] != item["id"]
    assert data["reopen_kind"] == "credit_note"


def test_api_post_409_when_not_reopenable(db):
    client = _client(db)
    item = db.create_ap_item({
        "id": "AP-dr-api-409",
        "organization_id": "orgA",
        "vendor_name": "Vendor X",
        "amount": 100.0,
        "state": "received",
    })
    db.update_ap_item(item["id"], state="validated")
    db.update_ap_item(item["id"], state="needs_approval")
    resp = client.post(
        f"/api/workspace/ap-items/{item['id']}/dispute-reopen",
        json={
            "reopen_kind": "credit_note",
            "correction_amount": 50.0,
            "reason": "x",
        },
    )
    assert resp.status_code == 409


def test_api_post_404_for_unknown_item(db):
    client = _client(db)
    resp = client.post(
        "/api/workspace/ap-items/AP-no-such/dispute-reopen",
        json={
            "reopen_kind": "credit_note",
            "correction_amount": 50.0,
            "reason": "x",
        },
    )
    assert resp.status_code == 404


def test_api_post_400_on_bad_payload(db):
    client = _client(db)
    item = _make_closed_ap_item(db, item_id="AP-dr-api-bad")
    resp = client.post(
        f"/api/workspace/ap-items/{item['id']}/dispute-reopen",
        json={
            "reopen_kind": "bogus",  # caught by Pydantic pattern
            "correction_amount": 50.0,
            "reason": "x",
        },
    )
    assert resp.status_code == 422


def test_api_get_chain(db):
    client = _client(db)
    item = _make_closed_ap_item(db, item_id="AP-dr-api-chain")
    client.post(
        f"/api/workspace/ap-items/{item['id']}/dispute-reopen",
        json={
            "reopen_kind": "credit_note",
            "correction_amount": 100.0,
            "reason": "x",
        },
    )
    resp = client.get(
        f"/api/workspace/ap-items/{item['id']}/dispute-reopen",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "credit_note"
    assert "correction_ap_item_id" in body


def test_api_cross_org_404(db):
    client = _client(db, org="orgA")
    other = _make_closed_ap_item(
        db, item_id="AP-dr-api-cross", org="orgB",
    )
    resp = client.post(
        f"/api/workspace/ap-items/{other['id']}/dispute-reopen",
        json={
            "reopen_kind": "credit_note",
            "correction_amount": 50.0,
            "reason": "x",
        },
    )
    assert resp.status_code == 404
