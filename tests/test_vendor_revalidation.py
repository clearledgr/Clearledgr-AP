"""Tests for Wave 1 / A11 — vendor mid-workflow re-validation.

Coverage:
  * Service: walk in-flight AP items for a vendor, set
    exception_code on each, audit-emit ``vendor_revalidation_triggered``.
  * Severity mapping: vendor_blocked → high, archived → medium,
    iban_change_pending → medium.
  * Idempotent: re-running with the same reason on items that
    already carry that exception_code is a no-op.
  * Terminal AP states are skipped (rejected, reversed, closed,
    posted_to_erp).
  * Non-existent vendor returns empty result, no errors.
  * HTTP integration: PATCH /vendors/{name}/status with
    new_status=blocked triggers eager revalidation; the response
    body carries the ``revalidation`` summary.
  * HTTP integration: same status no-op skips revalidation entirely.
  * HTTP integration: archived status surfaces as medium severity.
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

from clearledgr.api.vendor_status import router as vendor_status_router  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.vendor_revalidation import (  # noqa: E402
    HIGH_SEVERITY_REASONS,
    MEDIUM_SEVERITY_REASONS,
    revalidate_in_flight_ap_items,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    return inst


def _user(role: str = "owner", uid: str = "owner-user"):
    return SimpleNamespace(
        email=f"{role}@example.test",
        user_id=uid,
        organization_id="default",
        role=role,
    )


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(vendor_status_router)
    app.dependency_overrides[get_current_user] = lambda: _user()
    return TestClient(app)


def _seed_ap_item(db, *, item_id: str, vendor_name: str, state: str = "needs_approval"):
    return db.create_ap_item({
        "id": item_id,
        "organization_id": "default",
        "vendor_name": vendor_name,
        "amount": 250.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": state,
    })


# ─── Service: revalidate_in_flight_ap_items ─────────────────────────


def test_revalidation_flags_in_flight_items(db):
    db.upsert_vendor_profile(organization_id="default", vendor_name="AcmeRV")
    a = _seed_ap_item(db, item_id="ap-rv-1", vendor_name="AcmeRV", state="needs_approval")
    b = _seed_ap_item(db, item_id="ap-rv-2", vendor_name="AcmeRV", state="ready_to_post")

    out = revalidate_in_flight_ap_items(
        db,
        organization_id="default",
        vendor_name="AcmeRV",
        reason="vendor_blocked",
        actor="alice@example.test",
    )

    assert out.affected_ap_item_ids == ["ap-rv-2", "ap-rv-1"] or set(
        out.affected_ap_item_ids
    ) == {a["id"], b["id"]}
    assert len(out.affected_ap_item_ids) == 2

    # Each item now carries exception_code='vendor_blocked' with
    # high severity (vendor_blocked is in HIGH_SEVERITY_REASONS).
    a_fresh = db.get_ap_item(a["id"])
    b_fresh = db.get_ap_item(b["id"])
    assert a_fresh["exception_code"] == "vendor_blocked"
    assert b_fresh["exception_code"] == "vendor_blocked"
    assert a_fresh["exception_severity"] == "high"


def test_revalidation_severity_for_archived_status(db):
    _seed_ap_item(db, item_id="ap-rv-arch-1", vendor_name="AcmeRVArch", state="needs_approval")
    revalidate_in_flight_ap_items(
        db, organization_id="default", vendor_name="AcmeRVArch",
        reason="vendor_status_archived", actor="bob@example.test",
    )
    item = db.get_ap_item("ap-rv-arch-1")
    assert item["exception_code"] == "vendor_status_archived"
    assert item["exception_severity"] == "medium"


def test_revalidation_severity_for_iban_change(db):
    _seed_ap_item(db, item_id="ap-rv-iban-1", vendor_name="AcmeRVIban", state="needs_approval")
    revalidate_in_flight_ap_items(
        db, organization_id="default", vendor_name="AcmeRVIban",
        reason="vendor_iban_change_pending", actor="system:iban",
    )
    item = db.get_ap_item("ap-rv-iban-1")
    assert item["exception_code"] == "vendor_iban_change_pending"
    assert item["exception_severity"] == "medium"


def test_revalidation_skips_terminal_states(db):
    db.upsert_vendor_profile(organization_id="default", vendor_name="AcmeRVTerm")
    # Use create_ap_item with state directly. CLOSED/REJECTED/REVERSED
    # may need to be reached via valid transitions; for this test we
    # write the state on create which the create path accepts.
    _seed_ap_item(db, item_id="ap-rv-term-1", vendor_name="AcmeRVTerm", state="rejected")
    _seed_ap_item(db, item_id="ap-rv-term-2", vendor_name="AcmeRVTerm", state="closed")
    _seed_ap_item(db, item_id="ap-rv-term-3", vendor_name="AcmeRVTerm", state="reversed")
    _seed_ap_item(db, item_id="ap-rv-term-4", vendor_name="AcmeRVTerm", state="posted_to_erp")

    out = revalidate_in_flight_ap_items(
        db, organization_id="default", vendor_name="AcmeRVTerm",
        reason="vendor_blocked", actor="alice@example.test",
    )
    assert len(out.affected_ap_item_ids) == 0
    assert len(out.skipped_terminal) == 4


def test_revalidation_idempotent_on_same_reason(db):
    db.upsert_vendor_profile(organization_id="default", vendor_name="AcmeIdem")
    _seed_ap_item(db, item_id="ap-rv-idem-1", vendor_name="AcmeIdem")
    revalidate_in_flight_ap_items(
        db, organization_id="default", vendor_name="AcmeIdem",
        reason="vendor_blocked", actor="alice@example.test",
    )
    # Second call with same reason should skip the already-flagged
    # item and report it under skipped_already_flagged.
    out2 = revalidate_in_flight_ap_items(
        db, organization_id="default", vendor_name="AcmeIdem",
        reason="vendor_blocked", actor="alice@example.test",
    )
    assert len(out2.affected_ap_item_ids) == 0
    assert "ap-rv-idem-1" in out2.skipped_already_flagged


def test_revalidation_unknown_vendor_returns_empty_no_errors(db):
    out = revalidate_in_flight_ap_items(
        db, organization_id="default", vendor_name="DoesNotExist",
        reason="vendor_blocked", actor="alice@example.test",
    )
    assert out.affected_ap_item_ids == []
    assert out.errors == []


def test_revalidation_emits_audit_per_item(db):
    db.upsert_vendor_profile(organization_id="default", vendor_name="AcmeAudit")
    _seed_ap_item(db, item_id="ap-rv-audit-1", vendor_name="AcmeAudit")
    _seed_ap_item(db, item_id="ap-rv-audit-2", vendor_name="AcmeAudit")
    revalidate_in_flight_ap_items(
        db, organization_id="default", vendor_name="AcmeAudit",
        reason="vendor_blocked", actor="alice@example.test",
    )
    events = db.search_audit_events(
        organization_id="default",
        event_types=["vendor_revalidation_triggered"],
    )
    matching = [
        e for e in events.get("events", [])
        if e.get("box_id") in ("ap-rv-audit-1", "ap-rv-audit-2")
    ]
    assert len(matching) == 2
    payload = matching[0].get("payload_json")
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["vendor_name"] == "AcmeAudit"
    assert payload["reason"] == "vendor_blocked"
    assert payload["severity"] == "high"


# ─── Severity-set sanity ────────────────────────────────────────────


def test_severity_sets_are_disjoint():
    """Reasons are exclusively HIGH or MEDIUM, never both."""
    assert HIGH_SEVERITY_REASONS.isdisjoint(MEDIUM_SEVERITY_REASONS)


# ─── HTTP integration: vendor status flip ───────────────────────────


def test_status_flip_to_blocked_triggers_revalidation(db, client):
    db.upsert_vendor_profile(organization_id="default", vendor_name="AcmeBlock")
    _seed_ap_item(db, item_id="ap-block-1", vendor_name="AcmeBlock", state="needs_approval")
    _seed_ap_item(db, item_id="ap-block-2", vendor_name="AcmeBlock", state="ready_to_post")

    resp = client.patch(
        "/api/vendors/AcmeBlock/status?organization_id=default",
        json={"status": "blocked", "reason": "AML hit"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "blocked"
    # The PATCH response carries the revalidation summary
    assert "revalidation" in body
    assert body["revalidation"]["reason"] == "vendor_blocked"
    assert len(body["revalidation"]["affected_ap_item_ids"]) == 2

    # Items now carry the exception
    a = db.get_ap_item("ap-block-1")
    b = db.get_ap_item("ap-block-2")
    assert a["exception_code"] == "vendor_blocked"
    assert b["exception_code"] == "vendor_blocked"


def test_status_flip_to_archived_uses_medium_severity(db, client):
    db.upsert_vendor_profile(organization_id="default", vendor_name="AcmeArch")
    _seed_ap_item(db, item_id="ap-arch-1", vendor_name="AcmeArch")
    resp = client.patch(
        "/api/vendors/AcmeArch/status?organization_id=default",
        json={"status": "archived"},
    )
    assert resp.status_code == 200
    item = db.get_ap_item("ap-arch-1")
    assert item["exception_code"] == "vendor_status_archived"
    assert item["exception_severity"] == "medium"


def test_status_no_op_skips_revalidation(db, client):
    """Re-saving the same status should NOT trigger revalidation —
    the response carries no ``revalidation`` block."""
    db.upsert_vendor_profile(organization_id="default", vendor_name="AcmeNoOp")
    db.set_vendor_status(
        organization_id="default", vendor_name="AcmeNoOp", status="blocked",
    )
    _seed_ap_item(db, item_id="ap-noop-1", vendor_name="AcmeNoOp")

    resp = client.patch(
        "/api/vendors/AcmeNoOp/status?organization_id=default",
        json={"status": "blocked"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Same status → no revalidation summary in response
    assert "revalidation" not in body
    # And the item was NOT touched (no exception_code change)
    item = db.get_ap_item("ap-noop-1")
    assert item.get("exception_code") in (None, "")


def test_status_flip_back_to_active_does_not_clear_flags(db, client):
    """Going back to active doesn't auto-clear vendor_blocked flags
    — the original cause may still apply; operators clear via
    explicit AP-item resolution."""
    db.upsert_vendor_profile(organization_id="default", vendor_name="AcmeRevert")
    _seed_ap_item(db, item_id="ap-revert-1", vendor_name="AcmeRevert")
    db.set_vendor_status(
        organization_id="default", vendor_name="AcmeRevert", status="blocked",
    )
    db.update_ap_item(
        "ap-revert-1",
        exception_code="vendor_blocked",
        exception_severity="high",
    )
    # Now flip to active via the API
    resp = client.patch(
        "/api/vendors/AcmeRevert/status?organization_id=default",
        json={"status": "active"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Active is not in {blocked, archived} → no revalidation summary
    assert "revalidation" not in body
    # Item still carries the exception flag
    item = db.get_ap_item("ap-revert-1")
    assert item.get("exception_code") == "vendor_blocked"
