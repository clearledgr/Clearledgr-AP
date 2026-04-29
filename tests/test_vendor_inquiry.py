"""Tests for Wave 6 / H2 — vendor inquiry surface.

Covers:
  * Sanitized status mapping: each AP state maps to the right
    vendor-facing bucket (received / under_review / awaiting_approval
    / approved / scheduled_for_payment / paid / on_hold / rejected).
  * Lookup paths:
      - missing email / invoice → no_match_reason set, found=False
      - sender domain not on any vendor profile → not recognised
      - invoice number doesn't match anything for that vendor →
        invoice_not_found_for_vendor
      - happy match → found=True with status + narrative
  * Sub-domain matching: ap@billing.vendor-x.com matches a
    vendor profile with sender_domains=['vendor-x.com'].
  * Paid status: payment_reference + settlement_at populated from
    the payment_confirmations row.
  * No vendor data leaked: vendor_name not in reply body.
  * Reply renderer: subject + narrative for found cases; "more
    information needed" for not-found.
  * API: lookup + reply endpoints; org-scoped.
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

from clearledgr.api import vendor_inquiry as vi_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.vendor_inquiry import (  # noqa: E402
    _AP_STATE_TO_VENDOR_STATUS,
    lookup_vendor_inquiry,
    render_inquiry_reply,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta Co")
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="user-1", email=f"op@{org}.com",
        organization_id=org, role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(vi_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


def _make_ap_item_with_invoice(
    db, *,
    item_id: str,
    state: str = "needs_approval",
    invoice_number: str = "INV-9001",
    org: str = "orgA",
    vendor: str = "Vendor X",
) -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": vendor,
        "amount": 1000.0,
        "currency": "USD",
        "invoice_number": invoice_number,
        "state": "received",
    })
    if state == "received":
        return db.get_ap_item(item["id"])
    walk = ["validated"]
    if state in ("validated", "needs_info"):
        if state == "needs_info":
            walk.append("needs_info")
    elif state == "needs_approval":
        walk.append("needs_approval")
    elif state == "approved":
        walk.extend(["needs_approval", "approved"])
    elif state == "ready_to_post":
        walk.extend(["needs_approval", "approved", "ready_to_post"])
    elif state == "posted_to_erp":
        walk.extend([
            "needs_approval", "approved", "ready_to_post", "posted_to_erp",
        ])
    elif state == "awaiting_payment":
        walk.extend([
            "needs_approval", "approved", "ready_to_post",
            "posted_to_erp", "awaiting_payment",
        ])
    elif state == "payment_executed":
        walk.extend([
            "needs_approval", "approved", "ready_to_post",
            "posted_to_erp", "awaiting_payment", "payment_executed",
        ])
    elif state == "rejected":
        walk.extend(["needs_approval", "rejected"])
    for s in walk:
        db.update_ap_item(item["id"], state=s)
    return db.get_ap_item(item["id"])


# ─── Status mapping ────────────────────────────────────────────────


def test_status_mapping_covers_canonical_states():
    for state in (
        "received", "validated", "needs_approval",
        "needs_second_approval", "approved", "ready_to_post",
        "posted_to_erp", "awaiting_payment", "payment_in_flight",
        "payment_executed", "payment_failed", "rejected", "closed",
    ):
        assert state in _AP_STATE_TO_VENDOR_STATUS, (
            f"missing vendor mapping for state {state!r}"
        )


def test_status_mapping_groups():
    """Internal states roll up to the right vendor bucket."""
    assert _AP_STATE_TO_VENDOR_STATUS["needs_approval"] == "awaiting_approval"
    assert _AP_STATE_TO_VENDOR_STATUS["needs_second_approval"] == "awaiting_approval"
    assert _AP_STATE_TO_VENDOR_STATUS["awaiting_payment"] == "scheduled_for_payment"
    assert _AP_STATE_TO_VENDOR_STATUS["payment_executed"] == "paid"


# ─── Lookup: edge cases ────────────────────────────────────────────


def test_lookup_missing_email(db):
    result = lookup_vendor_inquiry(
        db, organization_id="orgA",
        sender_email="", invoice_number="INV-1",
    )
    assert result.found is False
    assert result.no_match_reason == "missing_sender_domain"


def test_lookup_missing_invoice(db):
    result = lookup_vendor_inquiry(
        db, organization_id="orgA",
        sender_email="ap@vendor-x.com", invoice_number="",
    )
    assert result.found is False
    assert result.no_match_reason == "missing_invoice_number"


def test_lookup_unknown_domain(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        sender_domains=["vendor-x.com"],
    )
    result = lookup_vendor_inquiry(
        db, organization_id="orgA",
        sender_email="someone@unrelated.com",
        invoice_number="INV-1",
    )
    assert result.found is False
    assert result.no_match_reason == "sender_domain_not_recognised"


def test_lookup_subdomain_matches(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        sender_domains=["vendor-x.com"],
    )
    _make_ap_item_with_invoice(
        db, item_id="AP-vi-sub", state="needs_approval",
        invoice_number="INV-9001",
    )
    result = lookup_vendor_inquiry(
        db, organization_id="orgA",
        sender_email="ap-clerk@billing.vendor-x.com",
        invoice_number="INV-9001",
    )
    assert result.found is True
    assert result.status == "awaiting_approval"


def test_lookup_invoice_not_found_for_vendor(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        sender_domains=["vendor-x.com"],
    )
    result = lookup_vendor_inquiry(
        db, organization_id="orgA",
        sender_email="ap@vendor-x.com",
        invoice_number="INV-DOES-NOT-EXIST",
    )
    assert result.found is False
    assert result.no_match_reason == "invoice_not_found_for_vendor"


# ─── Happy paths ───────────────────────────────────────────────────


def test_lookup_under_review(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        sender_domains=["vendor-x.com"],
    )
    _make_ap_item_with_invoice(
        db, item_id="AP-vi-rev", state="validated",
        invoice_number="INV-V1",
    )
    result = lookup_vendor_inquiry(
        db, organization_id="orgA",
        sender_email="ap@vendor-x.com",
        invoice_number="INV-V1",
    )
    assert result.found is True
    assert result.status == "under_review"
    assert result.narrative


def test_lookup_paid_includes_payment_reference(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        sender_domains=["vendor-x.com"],
    )
    item = _make_ap_item_with_invoice(
        db, item_id="AP-vi-paid", state="awaiting_payment",
        invoice_number="INV-P1",
    )
    db.create_payment_confirmation(
        organization_id="orgA",
        ap_item_id=item["id"],
        payment_id="PAY-CONF-1",
        source="manual",
        status="confirmed",
        settlement_at="2026-04-29T10:00:00+00:00",
        amount="1000.00",
        currency="USD",
        payment_reference="WIRE-77",
    )
    db.update_ap_item(item["id"], state="payment_executed")

    result = lookup_vendor_inquiry(
        db, organization_id="orgA",
        sender_email="ap@vendor-x.com",
        invoice_number="INV-P1",
    )
    assert result.found is True
    assert result.status == "paid"
    assert result.payment_reference == "WIRE-77"
    assert result.settlement_at == "2026-04-29T10:00:00+00:00"


def test_lookup_invoice_normalised(db):
    """Whitespace + casing in the inquiry should still match a stored
    invoice_number."""
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        sender_domains=["vendor-x.com"],
    )
    _make_ap_item_with_invoice(
        db, item_id="AP-vi-norm", state="approved",
        invoice_number="INV-N-7",
    )
    result = lookup_vendor_inquiry(
        db, organization_id="orgA",
        sender_email="ap@vendor-x.com",
        invoice_number="  inv-n-7  ",
    )
    assert result.found is True


def test_lookup_tenant_isolation(db):
    db.upsert_vendor_profile(
        "orgB", "Vendor X",
        sender_domains=["vendor-x.com"],
    )
    _make_ap_item_with_invoice(
        db, item_id="AP-vi-iso-B", org="orgB",
        state="approved", invoice_number="INV-ISO",
    )
    # orgA looks up the same domain + invoice — must NOT find orgB's item.
    result = lookup_vendor_inquiry(
        db, organization_id="orgA",
        sender_email="ap@vendor-x.com",
        invoice_number="INV-ISO",
    )
    assert result.found is False


# ─── Reply renderer ────────────────────────────────────────────────


def test_reply_for_found_includes_status(db):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        sender_domains=["vendor-x.com"],
    )
    _make_ap_item_with_invoice(
        db, item_id="AP-vi-render", state="approved",
        invoice_number="INV-R1",
    )
    result = lookup_vendor_inquiry(
        db, organization_id="orgA",
        sender_email="ap@vendor-x.com",
        invoice_number="INV-R1",
    )
    rendered = render_inquiry_reply(
        organization_name="Acme UK Ltd",
        vendor_name=None,
        invoice_number="INV-R1",
        result=result,
    )
    assert "INV-R1" in rendered["body"]
    assert "approved" in rendered["body"].lower()
    assert "Vendor X" not in rendered["body"]


def test_reply_for_not_found_says_more_info_needed(db):
    result = lookup_vendor_inquiry(
        db, organization_id="orgA",
        sender_email="ap@unknown.com", invoice_number="INV-X",
    )
    rendered = render_inquiry_reply(
        organization_name="Acme UK Ltd",
        vendor_name=None,
        invoice_number="INV-X",
        result=result,
    )
    assert "more information needed" in rendered["subject"].lower()
    assert "could not locate" in rendered["body"].lower()


# ─── API ───────────────────────────────────────────────────────────


def test_api_lookup_returns_status(db, client_orgA):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        sender_domains=["vendor-x.com"],
    )
    _make_ap_item_with_invoice(
        db, item_id="AP-vi-api-1", state="needs_approval",
        invoice_number="INV-API-1",
    )
    resp = client_orgA.post(
        "/api/workspace/vendor-inquiries/lookup",
        json={
            "sender_email": "ap@vendor-x.com",
            "invoice_number": "INV-API-1",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["found"] is True
    assert data["status"] == "awaiting_approval"


def test_api_reply_returns_subject_and_body(db, client_orgA):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        sender_domains=["vendor-x.com"],
    )
    _make_ap_item_with_invoice(
        db, item_id="AP-vi-api-2", state="approved",
        invoice_number="INV-API-2",
    )
    resp = client_orgA.post(
        "/api/workspace/vendor-inquiries/reply",
        json={
            "sender_email": "ap@vendor-x.com",
            "invoice_number": "INV-API-2",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["found"] is True
    assert "INV-API-2" in data["reply_subject"]
    assert "INV-API-2" in data["reply_body"]
    assert "Acme UK Ltd" in data["reply_body"]


def test_api_lookup_unknown_returns_no_match_reason(client_orgA):
    resp = client_orgA.post(
        "/api/workspace/vendor-inquiries/lookup",
        json={
            "sender_email": "ap@unknown-vendor.com",
            "invoice_number": "INV-X",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["no_match_reason"] == "sender_domain_not_recognised"
