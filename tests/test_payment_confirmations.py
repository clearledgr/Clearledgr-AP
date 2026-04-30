"""Tests for Wave 2 / C2 — payment_confirmations table + store +
service + audit emit.

Covers:
  * Round-trip insert + lookup (by primary id and by external composite
    key (org, source, payment_id)).
  * Listing scoped to org / status / source / time-window.
  * Per-AP-item history feed.
  * Idempotent webhook redelivery: a second
    ``record_payment_confirmation`` with the same external key returns
    duplicate=True, no second AP-state transition, no second audit event.
  * State machine walk for the four arrival cases:
      posted_to_erp     + confirmed → walks to payment_executed
      awaiting_payment  + confirmed → payment_executed (skip in-flight)
      payment_in_flight + confirmed → payment_executed
      awaiting_payment  + failed    → payment_failed
  * Disputed status records the row + audit event but does NOT
    transition state.
  * Tenant isolation: org A confirmations invisible to org B.
  * Audit event has the canonical idempotency key + box_id link.
  * AP item missing → confirmation row still recorded (the bank says
    the money moved; the AP item link is informational).
  * Decimal coercion + amount formats round-trip (str / float / Decimal).
"""
from __future__ import annotations

import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.stores.payment_confirmations_store import (  # noqa: E402
    PaymentConfirmationConflict,
)
from clearledgr.services.payment_tracking import (  # noqa: E402
    PaymentConfirmationResult,
    record_payment_confirmation,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    return inst


def _make_posted_ap_item(db, *, item_id: str, org: str = "default") -> dict:
    """AP item walked to posted_to_erp — the realistic starting state
    for a payment-tracking webhook."""
    db.ensure_organization(org, organization_name=org)
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Acme",
        "amount": 1000.0,
        "state": "received",
    })
    for s in (
        "validated", "needs_approval", "approved", "ready_to_post", "posted_to_erp",
    ):
        db.update_ap_item(item["id"], state=s)
    return db.get_ap_item(item["id"])


def _make_awaiting_ap_item(db, *, item_id: str, org: str = "default") -> dict:
    item = _make_posted_ap_item(db, item_id=item_id, org=org)
    db.update_ap_item(item["id"], state="awaiting_payment")
    return db.get_ap_item(item["id"])


# ─── Store: CRUD round-trip ─────────────────────────────────────────


def test_create_and_get_by_id(db):
    item = _make_posted_ap_item(db, item_id="AP-pc-roundtrip-1")
    row = db.create_payment_confirmation(
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-100",
        source="quickbooks",
        status="confirmed",
        settlement_at="2026-04-29T10:00:00+00:00",
        amount="1000.00",
        currency="EUR",
        method="ach",
        payment_reference="QB-BillPayment-100",
    )
    assert row["id"].startswith("PC-")
    assert row["payment_id"] == "PAY-100"
    assert row["status"] == "confirmed"
    assert row["amount"] == Decimal("1000.00")

    fetched = db.get_payment_confirmation(row["id"])
    assert fetched is not None
    assert fetched["id"] == row["id"]
    assert fetched["currency"] == "EUR"
    assert fetched["method"] == "ach"


def test_get_by_external_composite_key(db):
    item = _make_posted_ap_item(db, item_id="AP-pc-extkey-1")
    db.create_payment_confirmation(
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-EXT-1",
        source="xero",
    )
    fetched = db.get_payment_confirmation_by_external_id(
        "default", "xero", "PAY-EXT-1",
    )
    assert fetched is not None
    assert fetched["payment_id"] == "PAY-EXT-1"
    assert fetched["source"] == "xero"


def test_unique_composite_key_raises_conflict(db):
    item = _make_posted_ap_item(db, item_id="AP-pc-conflict-1")
    db.create_payment_confirmation(
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-DUP",
        source="quickbooks",
    )
    with pytest.raises(PaymentConfirmationConflict):
        db.create_payment_confirmation(
            organization_id="default",
            ap_item_id=item["id"],
            payment_id="PAY-DUP",
            source="quickbooks",
        )


def test_invalid_status_rejected(db):
    item = _make_posted_ap_item(db, item_id="AP-pc-bad-status")
    with pytest.raises(ValueError):
        db.create_payment_confirmation(
            organization_id="default",
            ap_item_id=item["id"],
            payment_id="PAY-BS",
            source="manual",
            status="paid",  # not in {confirmed, failed, disputed}
        )


def test_amount_coercion_accepts_str_float_decimal(db):
    item = _make_posted_ap_item(db, item_id="AP-pc-amt")
    for i, amt in enumerate(["123.45", 678.90, Decimal("999.99")]):
        row = db.create_payment_confirmation(
            organization_id="default",
            ap_item_id=item["id"],
            payment_id=f"PAY-AMT-{i}",
            source="manual",
            amount=amt,
        )
        assert isinstance(row["amount"], Decimal)


def test_list_for_ap_item_newest_first(db):
    item = _make_posted_ap_item(db, item_id="AP-pc-list-1")
    db.create_payment_confirmation(
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-A",
        source="manual",
        status="failed",
        settlement_at="2026-04-01T10:00:00+00:00",
        failure_reason="insufficient_funds",
    )
    db.create_payment_confirmation(
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-B",
        source="manual",
        status="confirmed",
        settlement_at="2026-04-15T10:00:00+00:00",
    )
    rows = db.list_payment_confirmations_for_ap_item("default", item["id"])
    assert len(rows) == 2
    # Newest first by settlement_at DESC.
    assert rows[0]["payment_id"] == "PAY-B"
    assert rows[1]["payment_id"] == "PAY-A"


# ─── Store: feed filters ────────────────────────────────────────────


def test_list_filter_by_status(db):
    item = _make_posted_ap_item(db, item_id="AP-pc-filter-1")
    db.create_payment_confirmation(
        organization_id="default", ap_item_id=item["id"],
        payment_id="PAY-OK", source="manual", status="confirmed",
    )
    db.create_payment_confirmation(
        organization_id="default", ap_item_id=item["id"],
        payment_id="PAY-X", source="manual", status="failed",
        failure_reason="bank_returned",
    )
    confirmed = db.list_payment_confirmations("default", status="confirmed")
    failed = db.list_payment_confirmations("default", status="failed")
    confirmed_ids = {r["payment_id"] for r in confirmed}
    failed_ids = {r["payment_id"] for r in failed}
    assert "PAY-OK" in confirmed_ids
    assert "PAY-X" in failed_ids
    assert "PAY-OK" not in failed_ids


def test_list_filter_invalid_status_rejects(db):
    with pytest.raises(ValueError):
        db.list_payment_confirmations("default", status="bogus")


def test_list_filter_by_source(db):
    item = _make_posted_ap_item(db, item_id="AP-pc-src-1")
    db.create_payment_confirmation(
        organization_id="default", ap_item_id=item["id"],
        payment_id="PAY-QB", source="quickbooks",
    )
    db.create_payment_confirmation(
        organization_id="default", ap_item_id=item["id"],
        payment_id="PAY-XR", source="xero",
    )
    qb = db.list_payment_confirmations("default", source="quickbooks")
    qb_ids = {r["payment_id"] for r in qb}
    assert "PAY-QB" in qb_ids
    assert "PAY-XR" not in qb_ids


def test_tenant_isolation_in_list(db):
    db.ensure_organization("orgA", organization_name="orgA")
    db.ensure_organization("orgB", organization_name="orgB")
    a = _make_posted_ap_item(db, item_id="AP-pc-iso-A", org="orgA")
    b = _make_posted_ap_item(db, item_id="AP-pc-iso-B", org="orgB")
    db.create_payment_confirmation(
        organization_id="orgA", ap_item_id=a["id"],
        payment_id="PAY-A1", source="manual",
    )
    db.create_payment_confirmation(
        organization_id="orgB", ap_item_id=b["id"],
        payment_id="PAY-B1", source="manual",
    )
    a_rows = db.list_payment_confirmations("orgA")
    b_rows = db.list_payment_confirmations("orgB")
    assert {r["payment_id"] for r in a_rows} == {"PAY-A1"}
    assert {r["payment_id"] for r in b_rows} == {"PAY-B1"}


# ─── Service: state walk ────────────────────────────────────────────


def test_service_posted_to_erp_walks_to_executed(db):
    item = _make_posted_ap_item(db, item_id="AP-pc-walk-1")
    result = record_payment_confirmation(
        db,
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-WALK-1",
        source="quickbooks",
        status="confirmed",
        amount="1000.00",
        currency="EUR",
    )
    assert isinstance(result, PaymentConfirmationResult)
    assert result.duplicate is False
    assert result.ap_state_before == "posted_to_erp"
    assert result.ap_state_after == "payment_executed"
    assert result.ap_state_unchanged_reason is None
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "payment_executed"


def test_service_awaiting_payment_skips_to_executed(db):
    item = _make_awaiting_ap_item(db, item_id="AP-pc-walk-2")
    result = record_payment_confirmation(
        db,
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-WALK-2",
        source="sap_b1",
        status="confirmed",
    )
    assert result.ap_state_before == "awaiting_payment"
    assert result.ap_state_after == "payment_executed"


def test_service_in_flight_to_executed(db):
    item = _make_awaiting_ap_item(db, item_id="AP-pc-walk-3")
    db.update_ap_item(item["id"], state="payment_in_flight")
    result = record_payment_confirmation(
        db,
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-WALK-3",
        source="xero",
        status="confirmed",
    )
    assert result.ap_state_before == "payment_in_flight"
    assert result.ap_state_after == "payment_executed"


def test_service_failure_walk(db):
    item = _make_awaiting_ap_item(db, item_id="AP-pc-walk-4")
    result = record_payment_confirmation(
        db,
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-WALK-4",
        source="quickbooks",
        status="failed",
        failure_reason="insufficient_funds",
    )
    assert result.ap_state_before == "awaiting_payment"
    assert result.ap_state_after == "payment_failed"
    fresh = db.get_ap_item(item["id"])
    assert fresh["state"] == "payment_failed"


def test_service_failed_then_retry_to_executed(db):
    """Doc Stage 9 retry path: payment_failed → awaiting_payment → executed."""
    item = _make_awaiting_ap_item(db, item_id="AP-pc-walk-5")
    db.update_ap_item(item["id"], state="payment_failed")
    result = record_payment_confirmation(
        db,
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-WALK-5",
        source="quickbooks",
        status="confirmed",
    )
    assert result.ap_state_before == "payment_failed"
    assert result.ap_state_after == "payment_executed"


def test_service_disputed_records_no_transition(db):
    item = _make_awaiting_ap_item(db, item_id="AP-pc-disputed")
    result = record_payment_confirmation(
        db,
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-DISPUTED",
        source="manual",
        status="disputed",
        notes="vendor claims double-charge",
    )
    assert result.ap_state_after == "awaiting_payment"
    assert result.ap_state_unchanged_reason is not None
    assert "disputed" in result.ap_state_unchanged_reason


def test_service_terminal_state_no_transition(db):
    """Closed AP item still records the confirmation but does not
    re-transition (closed is terminal)."""
    item = _make_posted_ap_item(db, item_id="AP-pc-terminal")
    db.update_ap_item(item["id"], state="closed")
    result = record_payment_confirmation(
        db,
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-TERM",
        source="manual",
        status="confirmed",
    )
    assert result.duplicate is False
    assert result.ap_state_unchanged_reason == "terminal:closed"
    assert result.ap_state_after == "closed"
    # Confirmation row still inserted.
    assert result.confirmation["payment_id"] == "PAY-TERM"


def test_service_already_at_target_no_transition(db):
    item = _make_awaiting_ap_item(db, item_id="AP-pc-already")
    db.update_ap_item(item["id"], state="payment_executed")
    result = record_payment_confirmation(
        db,
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-ALREADY",
        source="manual",
        status="confirmed",
    )
    assert result.ap_state_unchanged_reason == "already_at_target"
    assert result.ap_state_after == "payment_executed"


# ─── Service: idempotency ───────────────────────────────────────────


def test_service_idempotent_redelivery(db):
    """Webhook redelivery of the same external payment is a no-op."""
    item = _make_awaiting_ap_item(db, item_id="AP-pc-idem")
    first = record_payment_confirmation(
        db,
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-IDEM",
        source="quickbooks",
        status="confirmed",
    )
    assert first.duplicate is False

    # Same external key again → duplicate.
    second = record_payment_confirmation(
        db,
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-IDEM",
        source="quickbooks",
        status="confirmed",
    )
    assert second.duplicate is True
    assert second.confirmation["id"] == first.confirmation["id"]
    assert second.ap_state_unchanged_reason == "duplicate_redelivery"

    # Exactly one confirmation row.
    rows = db.list_payment_confirmations_for_ap_item("default", item["id"])
    assert len(rows) == 1


def test_service_audit_event_idempotency_key_present(db):
    """Audit event for the confirmation carries the canonical
    idempotency key so a webhook redelivery short-circuits at the
    audit layer too."""
    item = _make_awaiting_ap_item(db, item_id="AP-pc-audit-idem")
    record_payment_confirmation(
        db,
        organization_id="default",
        ap_item_id=item["id"],
        payment_id="PAY-AUDIT-1",
        source="xero",
        status="confirmed",
    )
    expected_key = (
        f"payment_confirmation:default:xero:PAY-AUDIT-1:{item['id']}"
    )
    fetched = db.get_ap_audit_event_by_key(expected_key)
    assert fetched is not None
    assert fetched["event_type"] == "payment_confirmation_recorded"
    assert fetched["box_id"] == item["id"]
    assert fetched["box_type"] == "ap_item"


def test_service_redelivery_does_not_double_emit_audit(db):
    item = _make_awaiting_ap_item(db, item_id="AP-pc-audit-no-double")
    record_payment_confirmation(
        db, organization_id="default", ap_item_id=item["id"],
        payment_id="PAY-NDD", source="manual", status="confirmed",
    )
    record_payment_confirmation(
        db, organization_id="default", ap_item_id=item["id"],
        payment_id="PAY-NDD", source="manual", status="confirmed",
    )
    matching = [
        e for e in db.list_box_audit_events("ap_item", item["id"])
        if e.get("event_type") == "payment_confirmation_recorded"
    ]
    assert len(matching) == 1


# ─── Service: tenant + missing item ─────────────────────────────────


def test_service_tenant_isolation(db):
    db.ensure_organization("orgX", organization_name="orgX")
    db.ensure_organization("orgY", organization_name="orgY")
    x = _make_awaiting_ap_item(db, item_id="AP-pc-tenant-X", org="orgX")
    y = _make_awaiting_ap_item(db, item_id="AP-pc-tenant-Y", org="orgY")
    record_payment_confirmation(
        db, organization_id="orgX", ap_item_id=x["id"],
        payment_id="PAY-X", source="manual", status="confirmed",
    )
    record_payment_confirmation(
        db, organization_id="orgY", ap_item_id=y["id"],
        payment_id="PAY-Y", source="manual", status="confirmed",
    )
    # Same external (source, payment_id) different org should NOT
    # collide — the unique index includes organization_id.
    record_payment_confirmation(
        db, organization_id="orgX", ap_item_id=x["id"],
        payment_id="PAY-COMMON", source="manual", status="confirmed",
    )
    record_payment_confirmation(
        db, organization_id="orgY", ap_item_id=y["id"],
        payment_id="PAY-COMMON", source="manual", status="confirmed",
    )
    x_rows = db.list_payment_confirmations("orgX")
    y_rows = db.list_payment_confirmations("orgY")
    assert {r["payment_id"] for r in x_rows} == {"PAY-X", "PAY-COMMON"}
    assert {r["payment_id"] for r in y_rows} == {"PAY-Y", "PAY-COMMON"}


def test_service_missing_ap_item_still_records(db):
    """If the AP item has been deleted/never existed, the confirmation
    row + audit event are still recorded — the bank's statement is the
    source of truth, and the orphan signals an investigation
    candidate."""
    bogus_id = f"AP-missing-{uuid.uuid4().hex[:8]}"
    result = record_payment_confirmation(
        db,
        organization_id="default",
        ap_item_id=bogus_id,
        payment_id="PAY-ORPHAN",
        source="manual",
        status="confirmed",
    )
    assert result.duplicate is False
    assert result.ap_state_unchanged_reason == "ap_item_not_found"
    assert result.confirmation["payment_id"] == "PAY-ORPHAN"
