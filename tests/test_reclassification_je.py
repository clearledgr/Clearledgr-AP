"""Tests for Wave 6 / H4 — reclassification JE generator.

Covers:
  * Bill must have posted to ERP before reclassification (state in
    {posted_to_erp, awaiting_payment, payment_in_flight,
    payment_executed, closed}). Earlier states raise NotPostedError.
  * Validation: missing reason / to_account, same from/to, non-positive
    amount, unknown AP item, cross-org access.
  * Proposal shape: 2 lines (Dr to_account, Cr from_account),
    balanced, posting_date inherits from erp_posted_at.
  * Amount source priority: explicit override > net_amount > amount.
  * record_reclassification: stamps history on metadata, audit
    event with stable idempotency key, idempotent on duplicate
    submission.
  * list_reclassifications: returns history newest first,
    cross-org returns empty.
  * Rendered text contains Dr/Cr, balanced flag, reason.
  * API: preview returns proposal + text, commit persists,
    list returns history; cross-org 404; 409 not posted; 400 bad payload.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import reclassification_je as rc_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.reclassification_je import (  # noqa: E402
    NotPostedError,
    build_reclassification_proposal,
    list_reclassifications,
    record_reclassification,
    render_reclassification_text,
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
    app.include_router(rc_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user(uid, org)
    return TestClient(app)


def _make_posted_ap_item(
    db, *,
    item_id: str,
    org: str = "orgA",
    amount: float = 1000.0,
    net: float = None,
    vat: float = 0.0,
    invoice_number: str = "INV-100",
    final_state: str = "posted_to_erp",
) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Vendor X",
        "amount": amount,
        "currency": "USD",
        "invoice_number": invoice_number,
        "state": "received",
    })
    walk = [
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp",
    ]
    if final_state == "payment_executed":
        walk.extend(["awaiting_payment", "payment_executed"])
    elif final_state == "closed":
        walk.extend([
            "awaiting_payment", "payment_executed", "closed",
        ])
    for s in walk:
        db.update_ap_item(item["id"], state=s)
    update_kwargs: dict = {
        "erp_posted_at": "2026-04-15T10:00:00+00:00",
        "erp_journal_entry_id": f"JE-{item_id}",
    }
    if net is not None:
        update_kwargs["net_amount"] = Decimal(str(net))
    if vat:
        update_kwargs["vat_amount"] = Decimal(str(vat))
    db.update_ap_item(item["id"], **update_kwargs)
    return db.get_ap_item(item["id"])


def _meta(item: dict) -> dict:
    raw = item.get("metadata")
    if isinstance(raw, str):
        return json.loads(raw) if raw else {}
    return raw if isinstance(raw, dict) else {}


# ─── Eligibility ──────────────────────────────────────────────────


def test_bill_must_be_posted(db):
    item = db.create_ap_item({
        "id": "AP-rc-not-posted",
        "organization_id": "orgA",
        "vendor_name": "V",
        "amount": 100.0,
        "currency": "USD",
        "state": "received",
    })
    db.update_ap_item(item["id"], state="validated")
    db.update_ap_item(item["id"], state="needs_approval")
    with pytest.raises(NotPostedError):
        build_reclassification_proposal(
            db, organization_id="orgA",
            ap_item_id=item["id"],
            to_account="6020",
            reason="x",
        )


def test_works_from_payment_executed(db):
    item = _make_posted_ap_item(
        db, item_id="AP-rc-pay-exec",
        final_state="payment_executed",
    )
    proposal = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"],
        to_account="6020",
        reason="vendor coded under Cloud, should be Office",
    )
    assert proposal.to_account == "6020"


def test_works_from_closed(db):
    item = _make_posted_ap_item(
        db, item_id="AP-rc-closed", final_state="closed",
    )
    proposal = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"],
        to_account="6020",
        reason="post-close correction",
    )
    assert proposal.balanced is True


# ─── Validation ───────────────────────────────────────────────────


def test_empty_reason_raises(db):
    item = _make_posted_ap_item(db, item_id="AP-rc-no-reason")
    with pytest.raises(ValueError):
        build_reclassification_proposal(
            db, organization_id="orgA",
            ap_item_id=item["id"], to_account="6020", reason="",
        )


def test_empty_to_account_raises(db):
    item = _make_posted_ap_item(db, item_id="AP-rc-no-to")
    with pytest.raises(ValueError):
        build_reclassification_proposal(
            db, organization_id="orgA",
            ap_item_id=item["id"], to_account="", reason="x",
        )


def test_same_from_and_to_account_raises(db):
    item = _make_posted_ap_item(db, item_id="AP-rc-same")
    # The default from_account is the org's expense GL — pass it
    # explicitly as to_account so they collide.
    with pytest.raises(ValueError):
        build_reclassification_proposal(
            db, organization_id="orgA",
            ap_item_id=item["id"],
            to_account="400",  # Xero default expense
            from_account="400",
            reason="x",
        )


def test_non_positive_amount_raises(db):
    item = _make_posted_ap_item(db, item_id="AP-rc-neg-amt")
    with pytest.raises(ValueError):
        build_reclassification_proposal(
            db, organization_id="orgA",
            ap_item_id=item["id"], to_account="6020",
            reason="x", amount=-100.0,
        )


def test_unknown_ap_item_raises(db):
    with pytest.raises(ValueError):
        build_reclassification_proposal(
            db, organization_id="orgA",
            ap_item_id="AP-no-such",
            to_account="6020", reason="x",
        )


def test_cross_org_raises(db):
    item = _make_posted_ap_item(
        db, item_id="AP-rc-cross", org="orgB",
    )
    with pytest.raises(ValueError):
        build_reclassification_proposal(
            db, organization_id="orgA",
            ap_item_id=item["id"], to_account="6020", reason="x",
        )


# ─── Proposal shape ───────────────────────────────────────────────


def test_proposal_two_balanced_lines(db):
    item = _make_posted_ap_item(
        db, item_id="AP-rc-shape",
        amount=1000.0, net=1000.0,
    )
    proposal = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"],
        to_account="6020", reason="x",
    )
    assert len(proposal.lines) == 2
    debit = next(ln for ln in proposal.lines if ln.direction == "debit")
    credit = next(ln for ln in proposal.lines if ln.direction == "credit")
    assert debit.account_code == "6020"
    assert credit.account_code == proposal.from_account
    assert debit.amount == credit.amount == Decimal("1000.00")
    assert proposal.balanced is True


def test_proposal_inherits_posting_date(db):
    item = _make_posted_ap_item(
        db, item_id="AP-rc-pd",
    )
    proposal = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"],
        to_account="6020", reason="x",
    )
    assert proposal.posting_date == "2026-04-15T10:00:00+00:00"


def test_amount_priority_explicit_override(db):
    """Operator override beats net_amount."""
    item = _make_posted_ap_item(
        db, item_id="AP-rc-amt-override",
        amount=1000.0, net=900.0,
    )
    proposal = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"],
        to_account="6020", reason="partial",
        amount=200.0,
    )
    assert proposal.amount == Decimal("200.00")


def test_amount_priority_uses_net_when_no_override(db):
    item = _make_posted_ap_item(
        db, item_id="AP-rc-amt-net",
        amount=1190.0, net=1000.0, vat=190.0,
    )
    proposal = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"],
        to_account="6020", reason="x",
    )
    assert proposal.amount == Decimal("1000.00")


def test_amount_priority_falls_back_to_gross(db):
    """Legacy bill without net_amount — fall through to gross."""
    item = _make_posted_ap_item(
        db, item_id="AP-rc-amt-gross",
        amount=1190.0,  # no net
    )
    proposal = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"],
        to_account="6020", reason="x",
    )
    assert proposal.amount == Decimal("1190.00")


# ─── record_reclassification ──────────────────────────────────────


def test_record_stamps_history_and_emits_audit(db):
    item = _make_posted_ap_item(db, item_id="AP-rc-record")
    proposal = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"],
        to_account="6020",
        reason="hosting was miscategorised",
    )
    record = record_reclassification(
        db, organization_id="orgA",
        proposal=proposal, actor_id="ops-1",
    )
    assert record["reclassification_id"] == proposal.reclassification_id

    fresh = db.get_ap_item(item["id"])
    history = (_meta(fresh).get("reclassifications") or [])
    assert any(
        r["reclassification_id"] == proposal.reclassification_id
        for r in history
    )
    expected_key = (
        f"reclassification:orgA:{proposal.reclassification_id}"
    )
    audit = db.get_ap_audit_event_by_key(expected_key)
    assert audit is not None
    assert audit["event_type"] == "reclassification_recorded"


def test_record_idempotent_on_duplicate_payload(db):
    item = _make_posted_ap_item(db, item_id="AP-rc-idem")
    p1 = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"], to_account="6020", reason="x",
    )
    record_reclassification(
        db, organization_id="orgA", proposal=p1, actor_id="ops-1",
    )
    p2 = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"], to_account="6020", reason="x",
    )
    record2 = record_reclassification(
        db, organization_id="orgA", proposal=p2, actor_id="ops-1",
    )
    # Same payload -> existing record returned, no second history entry.
    fresh = db.get_ap_item(item["id"])
    history = (_meta(fresh).get("reclassifications") or [])
    assert len(history) == 1
    assert record2["reclassification_id"] == p1.reclassification_id


# ─── list_reclassifications ───────────────────────────────────────


def test_list_returns_newest_first(db):
    item = _make_posted_ap_item(db, item_id="AP-rc-list")
    p1 = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"], to_account="6020", reason="first",
    )
    record_reclassification(
        db, organization_id="orgA", proposal=p1, actor_id="ops-1",
    )
    p2 = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"], to_account="6030", reason="second",
    )
    record_reclassification(
        db, organization_id="orgA", proposal=p2, actor_id="ops-1",
    )
    history = list_reclassifications(
        db, organization_id="orgA", ap_item_id=item["id"],
    )
    assert len(history) == 2
    assert history[0]["reason"] == "second"


def test_list_cross_org_returns_empty(db):
    item = _make_posted_ap_item(
        db, item_id="AP-rc-list-cross", org="orgB",
    )
    history = list_reclassifications(
        db, organization_id="orgA", ap_item_id=item["id"],
    )
    assert history == []


# ─── Renderer ─────────────────────────────────────────────────────


def test_render_includes_lines_and_reason(db):
    item = _make_posted_ap_item(db, item_id="AP-rc-render")
    proposal = build_reclassification_proposal(
        db, organization_id="orgA",
        ap_item_id=item["id"],
        to_account="6020", reason="rendered text test",
    )
    text = render_reclassification_text(proposal)
    assert "Dr" in text
    assert "Cr" in text
    assert "balanced" in text
    assert "rendered text test" in text


# ─── API ──────────────────────────────────────────────────────────


def test_api_preview_returns_proposal(db):
    client = _client(db)
    item = _make_posted_ap_item(db, item_id="AP-rc-api-preview")
    resp = client.post(
        f"/api/workspace/ap-items/{item['id']}/reclassify/preview",
        json={"to_account": "6020", "reason": "test"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["to_account"] == "6020"
    assert data["balanced"] is True
    assert "rendered_text" in data


def test_api_commit_persists(db):
    client = _client(db)
    item = _make_posted_ap_item(db, item_id="AP-rc-api-commit")
    resp = client.post(
        f"/api/workspace/ap-items/{item['id']}/reclassify",
        json={"to_account": "6020", "reason": "committed"},
    )
    assert resp.status_code == 200
    assert "recorded" in resp.json()
    fresh = db.get_ap_item(item["id"])
    assert (_meta(fresh).get("reclassifications") or [])


def test_api_list_returns_history(db):
    client = _client(db)
    item = _make_posted_ap_item(db, item_id="AP-rc-api-list")
    client.post(
        f"/api/workspace/ap-items/{item['id']}/reclassify",
        json={"to_account": "6020", "reason": "first"},
    )
    resp = client.get(
        f"/api/workspace/ap-items/{item['id']}/reclassifications",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["reason"] == "first"


def test_api_409_when_not_posted(db):
    client = _client(db)
    item = db.create_ap_item({
        "id": "AP-rc-api-409",
        "organization_id": "orgA",
        "vendor_name": "V",
        "amount": 100.0,
        "state": "received",
    })
    db.update_ap_item(item["id"], state="validated")
    resp = client.post(
        f"/api/workspace/ap-items/{item['id']}/reclassify",
        json={"to_account": "6020", "reason": "x"},
    )
    assert resp.status_code == 409


def test_api_404_unknown_item(db):
    client = _client(db)
    resp = client.post(
        "/api/workspace/ap-items/AP-no-such/reclassify/preview",
        json={"to_account": "6020", "reason": "x"},
    )
    assert resp.status_code == 404


def test_api_cross_org_404(db):
    client = _client(db, org="orgA")
    other = _make_posted_ap_item(
        db, item_id="AP-rc-api-cross", org="orgB",
    )
    resp = client.post(
        f"/api/workspace/ap-items/{other['id']}/reclassify/preview",
        json={"to_account": "6020", "reason": "x"},
    )
    assert resp.status_code == 404


def test_api_400_on_validation_failure(db):
    client = _client(db)
    item = _make_posted_ap_item(db, item_id="AP-rc-api-400")
    resp = client.post(
        f"/api/workspace/ap-items/{item['id']}/reclassify/preview",
        json={"to_account": "6020", "reason": "x", "amount": -50.0},
    )
    assert resp.status_code == 422  # Pydantic catches gt=0
