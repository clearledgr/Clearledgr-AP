"""Tests for Wave 2 / C5 — outbound remittance advice + per-vendor opt-out.

Covers:
  * render_remittance_advice() — subject + body shape, fallbacks,
    bank-account formatting.
  * send_remittance_advice() outcomes:
      sent, opted_out, no_email, no_gmail, not_confirmed,
      duplicate (idempotent), send_failed.
  * Hook from record_payment_confirmation: a confirmed payment
    triggers the remittance hook; a failed payment does not.
  * API: GET/PUT /vendors/{vendor_name}/remittance-config —
    org-scoped, vendor-not-found 404, partial update, opt-out flag.
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
from clearledgr.services.payment_tracking import (  # noqa: E402
    record_payment_confirmation,
)
from clearledgr.services.remittance_advice import (  # noqa: E402
    RemittanceAdviceResult,
    render_remittance_advice,
    send_remittance_advice,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme Holdings GmbH")
    inst.ensure_organization("orgB", organization_name="Beta Co")
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="user-1", email="op@acme.com",
        organization_id=org, role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(pc_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


def _make_awaiting_ap_item(db, *, item_id: str, vendor: str = "Vendor X", org: str = "orgA") -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": vendor,
        "amount": 1500.0,
        "currency": "EUR",
        "invoice_number": f"INV-{item_id}",
        "state": "received",
    })
    for s in (
        "validated", "needs_approval", "approved",
        "ready_to_post", "posted_to_erp", "awaiting_payment",
    ):
        db.update_ap_item(item["id"], state=s)
    return db.get_ap_item(item["id"])


# ─── render_remittance_advice() ─────────────────────────────────────


def test_render_includes_invoice_amount_and_method():
    out = render_remittance_advice(
        organization_name="Acme Holdings GmbH",
        ap_item={
            "id": "AP-1", "vendor_name": "Vendor X",
            "invoice_number": "INV-9001", "amount": 1500.00,
            "currency": "EUR",
        },
        confirmation={
            "amount": 1500.00, "currency": "EUR",
            "settlement_at": "2026-04-29T10:00:00+00:00",
            "method": "wire", "payment_reference": "WIRE-77",
            "bank_account_last4": "4242",
        },
        vendor_profile={"vendor_name": "Vendor X"},
    )
    assert "INV-9001" in out["subject"]
    assert "EUR 1,500.00" in out["subject"]
    assert "Acme Holdings GmbH" in out["body"]
    assert "WIRE-77" in out["body"]
    assert "4242" in out["body"]
    assert "Vendor X" in out["body"]


def test_render_omits_bank_line_when_no_last4():
    out = render_remittance_advice(
        organization_name="Acme",
        ap_item={"vendor_name": "V", "invoice_number": "I1", "amount": 100, "currency": "USD"},
        confirmation={"amount": 100, "currency": "USD"},
        vendor_profile=None,
    )
    assert "ending in" not in out["body"]


# ─── send_remittance_advice() — outcomes ────────────────────────────


def test_send_status_not_confirmed_short_circuits(db):
    item = _make_awaiting_ap_item(db, item_id="AP-rem-failed")
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    result = send_remittance_advice(
        db,
        organization_id="orgA",
        ap_item_id=item["id"],
        payment_id="P-FAIL-1",
        confirmation={
            "id": "PC-1", "status": "failed", "payment_id": "P-FAIL-1",
            "amount": 1500.0, "currency": "EUR",
            "failure_reason": "insufficient_funds",
        },
    )
    assert result.status == "not_confirmed"
    assert result.audit_event_id is not None


def test_send_opted_out_skips(db):
    item = _make_awaiting_ap_item(db, item_id="AP-rem-opt-out")
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
        remittance_opt_out=1,
    )
    sender_calls = []

    def fake_sender(*, to, subject, body):
        sender_calls.append((to, subject))
        return {"id": "sent-1"}

    result = send_remittance_advice(
        db,
        organization_id="orgA",
        ap_item_id=item["id"],
        payment_id="P-OPT-1",
        confirmation={
            "status": "confirmed", "payment_id": "P-OPT-1",
            "amount": 1500.0, "currency": "EUR",
        },
        sender=fake_sender,
    )
    assert result.status == "opted_out"
    assert sender_calls == []


def test_send_no_email_records_audit(db):
    item = _make_awaiting_ap_item(db, item_id="AP-rem-no-email")
    # Vendor profile with no email contact at all.
    db.upsert_vendor_profile("orgA", "Vendor X", typical_gl_code="6010")
    result = send_remittance_advice(
        db,
        organization_id="orgA",
        ap_item_id=item["id"],
        payment_id="P-NE-1",
        confirmation={
            "status": "confirmed", "payment_id": "P-NE-1",
            "amount": 1500.0, "currency": "EUR",
        },
    )
    assert result.status == "no_email"


def test_send_no_gmail_when_no_sender_provided(db):
    item = _make_awaiting_ap_item(db, item_id="AP-rem-no-gmail")
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    result = send_remittance_advice(
        db,
        organization_id="orgA",
        ap_item_id=item["id"],
        payment_id="P-NG-1",
        confirmation={
            "status": "confirmed", "payment_id": "P-NG-1",
            "amount": 1500.0, "currency": "EUR",
        },
    )
    assert result.status == "no_gmail"
    assert result.sent_to == "ap@vendor-x.com"


def test_send_happy_path_with_sender(db):
    item = _make_awaiting_ap_item(db, item_id="AP-rem-sent")
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    sent = []

    def fake_sender(*, to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})
        return {"id": "msg-1"}

    result = send_remittance_advice(
        db,
        organization_id="orgA",
        ap_item_id=item["id"],
        payment_id="P-OK-1",
        confirmation={
            "status": "confirmed", "payment_id": "P-OK-1",
            "amount": 1500.0, "currency": "EUR",
            "method": "wire",
        },
        sender=fake_sender,
    )
    assert result.status == "sent"
    assert len(sent) == 1
    assert sent[0]["to"] == "ap@vendor-x.com"
    assert "remittance" in sent[0]["subject"].lower()


def test_send_idempotent_on_redelivery(db):
    """A second call for the same payment is a no-op duplicate."""
    item = _make_awaiting_ap_item(db, item_id="AP-rem-idem")
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    sent = []

    def fake_sender(*, to, subject, body):
        sent.append(to)
        return {"id": "ok"}

    confirmation = {
        "status": "confirmed", "payment_id": "P-IDEM",
        "amount": 1500.0, "currency": "EUR",
    }
    first = send_remittance_advice(
        db, organization_id="orgA", ap_item_id=item["id"],
        payment_id="P-IDEM", confirmation=confirmation, sender=fake_sender,
    )
    second = send_remittance_advice(
        db, organization_id="orgA", ap_item_id=item["id"],
        payment_id="P-IDEM", confirmation=confirmation, sender=fake_sender,
    )
    assert first.status == "sent"
    assert second.status == "duplicate"
    assert len(sent) == 1  # only first call sent


def test_send_remittance_email_overrides_primary(db):
    item = _make_awaiting_ap_item(db, item_id="AP-rem-override")
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ae@vendor-x.com",
        remittance_email="ap@vendor-x.com",
    )
    sent_to = []

    def fake_sender(*, to, subject, body):
        sent_to.append(to)
        return {"id": "ok"}

    send_remittance_advice(
        db, organization_id="orgA", ap_item_id=item["id"],
        payment_id="P-OV", confirmation={
            "status": "confirmed", "payment_id": "P-OV",
            "amount": 1500.0,
        },
        sender=fake_sender,
    )
    assert sent_to == ["ap@vendor-x.com"]


def test_send_failed_records_send_failed(db):
    item = _make_awaiting_ap_item(db, item_id="AP-rem-sendfail")
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )

    def boom(*, to, subject, body):
        raise RuntimeError("smtp_down")

    result = send_remittance_advice(
        db, organization_id="orgA", ap_item_id=item["id"],
        payment_id="P-BOOM", confirmation={
            "status": "confirmed", "payment_id": "P-BOOM",
            "amount": 1500.0,
        },
        sender=boom,
    )
    assert result.status == "send_failed"
    assert result.error and "smtp_down" in result.error


# ─── Hook from record_payment_confirmation ──────────────────────────


def test_record_payment_confirmation_triggers_remittance_audit(db):
    """A confirmed payment lands a remittance_advice_sent audit event,
    even when no Gmail sender is configured (status=no_gmail)."""
    item = _make_awaiting_ap_item(db, item_id="AP-rem-hook-1")
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    record_payment_confirmation(
        db,
        organization_id="orgA",
        ap_item_id=item["id"],
        payment_id="P-HOOK-1",
        source="manual",
        status="confirmed",
        amount=1500.0,
        currency="EUR",
    )
    expected_key = f"remittance_advice:orgA:{item['id']}:P-HOOK-1"
    fetched = db.get_ap_audit_event_by_key(expected_key)
    assert fetched is not None
    assert fetched["event_type"] == "remittance_advice_sent"


def test_record_payment_confirmation_no_remittance_for_failed(db):
    item = _make_awaiting_ap_item(db, item_id="AP-rem-hook-fail")
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    record_payment_confirmation(
        db,
        organization_id="orgA",
        ap_item_id=item["id"],
        payment_id="P-HOOK-FAIL",
        source="manual",
        status="failed",
        failure_reason="insufficient_funds",
    )
    expected_key = f"remittance_advice:orgA:{item['id']}:P-HOOK-FAIL"
    # No remittance audit is created for failed status (the hook
    # short-circuits before send_remittance_advice runs).
    assert db.get_ap_audit_event_by_key(expected_key) is None


# ─── API: vendor remittance config ──────────────────────────────────


def test_api_get_remittance_config(db, client_orgA):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ae@vendor-x.com",
        remittance_email="ap@vendor-x.com",
    )
    resp = client_orgA.get("/api/workspace/vendors/Vendor X/remittance-config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["remittance_email"] == "ap@vendor-x.com"
    assert data["primary_contact_email"] == "ae@vendor-x.com"
    assert data["remittance_opt_out"] is False


def test_api_get_unknown_vendor_404(client_orgA):
    resp = client_orgA.get(
        "/api/workspace/vendors/Unknown/remittance-config",
    )
    assert resp.status_code == 404


def test_api_put_sets_opt_out(db, client_orgA):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ap@vendor-x.com",
    )
    resp = client_orgA.put(
        "/api/workspace/vendors/Vendor X/remittance-config",
        json={"remittance_opt_out": True},
    )
    assert resp.status_code == 200
    assert resp.json()["remittance_opt_out"] is True
    fresh = db.get_vendor_profile("orgA", "Vendor X")
    assert int(fresh["remittance_opt_out"] or 0) == 1


def test_api_put_partial_keeps_other_fields(db, client_orgA):
    db.upsert_vendor_profile(
        "orgA", "Vendor X",
        primary_contact_email="ae@vendor-x.com",
        remittance_email="ap@vendor-x.com",
    )
    # Just toggle opt_out — should not blank out remittance_email.
    resp = client_orgA.put(
        "/api/workspace/vendors/Vendor X/remittance-config",
        json={"remittance_opt_out": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["remittance_email"] == "ap@vendor-x.com"
    assert data["remittance_opt_out"] is True
