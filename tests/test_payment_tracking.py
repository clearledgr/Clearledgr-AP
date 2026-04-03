"""Tests for payment scheduling and tracking.

Validates:
- PaymentRecord model and serialization
- PaymentStore CRUD (create, read, update, list, summary)
- check_payment_readiness APSkill tool
- Payment status update validation
- Worklist item enrichment with payment fields
"""

import asyncio
import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path=None):
    """Return a fresh ClearledgrDB backed by a temp file."""
    if tmp_path is None:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
    else:
        path = str(tmp_path / "test_payments.db")
    os.environ["CLEARLEDGR_DB_PATH"] = path

    import clearledgr.core.database as db_mod
    db_mod._DB_INSTANCE = None
    db_mod._CLEARLEDGR_DB_IMPL = None
    db = db_mod.get_db()
    db.initialize()
    return db


# ---------------------------------------------------------------------------
# PaymentRecord model
# ---------------------------------------------------------------------------


def test_payment_record_to_dict():
    from clearledgr.services.payment_models import PaymentRecord

    rec = PaymentRecord(
        id="PAY-abc123",
        ap_item_id="AP-xyz",
        organization_id="org-1",
        vendor_name="Acme Corp",
        amount=1500.0,
        currency="USD",
        status="ready_for_payment",
        due_date="2026-05-01",
        erp_reference="ERP-9999",
        created_at="2026-04-01T00:00:00Z",
        updated_at="2026-04-01T00:00:00Z",
    )
    d = rec.to_dict()
    assert d["id"] == "PAY-abc123"
    assert d["ap_item_id"] == "AP-xyz"
    assert d["vendor_name"] == "Acme Corp"
    assert d["amount"] == 1500.0
    assert d["status"] == "ready_for_payment"
    assert d["due_date"] == "2026-05-01"
    assert d["erp_reference"] == "ERP-9999"
    assert d["payment_method"] is None


def test_payment_statuses_and_methods_are_frozen():
    from clearledgr.services.payment_models import PAYMENT_STATUSES, PAYMENT_METHODS

    assert "ready_for_payment" in PAYMENT_STATUSES
    assert "scheduled" in PAYMENT_STATUSES
    assert "completed" in PAYMENT_STATUSES
    assert "ach" in PAYMENT_METHODS
    assert "wire" in PAYMENT_METHODS


# ---------------------------------------------------------------------------
# PaymentStore CRUD
# ---------------------------------------------------------------------------


def test_create_and_get_payment(tmp_path):
    db = _fresh_db(tmp_path)
    payment = db.create_payment({
        "ap_item_id": "AP-001",
        "organization_id": "org-1",
        "vendor_name": "Acme",
        "amount": 500.0,
        "currency": "USD",
        "status": "ready_for_payment",
        "due_date": "2026-05-01",
        "erp_reference": "ERP-100",
    })
    assert payment["id"].startswith("PAY-")
    assert payment["vendor_name"] == "Acme"
    assert payment["amount"] == 500.0

    fetched = db.get_payment(payment["id"])
    assert fetched is not None
    assert fetched["ap_item_id"] == "AP-001"
    assert fetched["status"] == "ready_for_payment"


def test_get_payment_by_ap_item(tmp_path):
    db = _fresh_db(tmp_path)
    db.create_payment({
        "ap_item_id": "AP-002",
        "organization_id": "org-1",
        "vendor_name": "Widget Co",
        "amount": 250.0,
    })
    result = db.get_payment_by_ap_item("AP-002")
    assert result is not None
    assert result["vendor_name"] == "Widget Co"


def test_get_payment_nonexistent(tmp_path):
    db = _fresh_db(tmp_path)
    assert db.get_payment("PAY-nonexistent") is None


def test_update_payment(tmp_path):
    db = _fresh_db(tmp_path)
    payment = db.create_payment({
        "ap_item_id": "AP-003",
        "organization_id": "org-1",
        "vendor_name": "TestCo",
        "amount": 1000.0,
    })
    updated = db.update_payment(
        payment["id"],
        status="scheduled",
        payment_method="ach",
        scheduled_date="2026-04-15",
        notes="Scheduled by CFO",
    )
    assert updated["status"] == "scheduled"
    assert updated["payment_method"] == "ach"
    assert updated["scheduled_date"] == "2026-04-15"
    assert updated["notes"] == "Scheduled by CFO"


def test_update_payment_rejects_disallowed_columns(tmp_path):
    db = _fresh_db(tmp_path)
    payment = db.create_payment({
        "ap_item_id": "AP-004",
        "organization_id": "org-1",
        "vendor_name": "Safe Co",
        "amount": 200.0,
    })
    # Attempt to update a non-whitelisted column (ap_item_id)
    result = db.update_payment(payment["id"], ap_item_id="EVIL", status="scheduled")
    assert result["status"] == "scheduled"
    assert result["ap_item_id"] == "AP-004"  # not overwritten


def test_list_payments_by_org(tmp_path):
    db = _fresh_db(tmp_path)
    for i in range(5):
        db.create_payment({
            "ap_item_id": f"AP-{i}",
            "organization_id": "org-1",
            "vendor_name": f"Vendor {i}",
            "amount": 100.0 * (i + 1),
            "status": "ready_for_payment" if i < 3 else "completed",
        })
    all_payments = db.list_payments_by_org("org-1")
    assert len(all_payments) == 5

    ready = db.list_payments_by_org("org-1", status="ready_for_payment")
    assert len(ready) == 3

    completed = db.list_payments_by_org("org-1", status="completed")
    assert len(completed) == 2


def test_list_payments_by_status(tmp_path):
    db = _fresh_db(tmp_path)
    db.create_payment({
        "ap_item_id": "AP-s1",
        "organization_id": "org-2",
        "vendor_name": "StatusVendor",
        "amount": 50.0,
        "status": "scheduled",
    })
    results = db.list_payments_by_status("org-2", "scheduled")
    assert len(results) == 1
    assert results[0]["status"] == "scheduled"


def test_list_payments_by_vendor(tmp_path):
    db = _fresh_db(tmp_path)
    db.create_payment({
        "ap_item_id": "AP-v1",
        "organization_id": "org-1",
        "vendor_name": "TargetVendor",
        "amount": 300.0,
    })
    db.create_payment({
        "ap_item_id": "AP-v2",
        "organization_id": "org-1",
        "vendor_name": "OtherVendor",
        "amount": 400.0,
    })
    results = db.list_payments_by_org("org-1", vendor="TargetVendor")
    assert len(results) == 1
    assert results[0]["vendor_name"] == "TargetVendor"


def test_payment_summary(tmp_path):
    db = _fresh_db(tmp_path)
    db.create_payment({"ap_item_id": "AP-a", "organization_id": "org-1", "vendor_name": "A", "amount": 100, "status": "ready_for_payment"})
    db.create_payment({"ap_item_id": "AP-b", "organization_id": "org-1", "vendor_name": "B", "amount": 200, "status": "ready_for_payment"})
    db.create_payment({"ap_item_id": "AP-c", "organization_id": "org-1", "vendor_name": "C", "amount": 300, "status": "scheduled"})
    db.create_payment({"ap_item_id": "AP-d", "organization_id": "org-1", "vendor_name": "D", "amount": 400, "status": "completed"})

    summary = db.get_payment_summary("org-1")
    assert summary["ready_for_payment"] == 2
    assert summary["scheduled"] == 1
    assert summary["completed"] == 1


# ---------------------------------------------------------------------------
# APSkill: check_payment_readiness
# ---------------------------------------------------------------------------


def test_check_payment_readiness_not_posted(tmp_path):
    db = _fresh_db(tmp_path)
    # Create an AP item in received state
    db.create_ap_item({
        "id": "AP-notposted",
        "thread_id": "thread-1",
        "state": "received",
        "vendor_name": "TestVendor",
        "amount": 100.0,
        "organization_id": "org-1",
    })

    from clearledgr.core.skills.ap_skill import _handle_check_payment_readiness

    result = asyncio.run(
        _handle_check_payment_readiness(
            {"id": "AP-notposted"},
            organization_id="org-1",
        )
    )
    assert result["ok"] is True
    assert result["payment_ready"] is False
    assert result["reason"] == "not_posted"


def test_check_payment_readiness_posted_with_record(tmp_path):
    db = _fresh_db(tmp_path)
    db.create_ap_item({
        "id": "AP-posted",
        "thread_id": "thread-2",
        "state": "posted_to_erp",
        "vendor_name": "PostedVendor",
        "amount": 2500.0,
        "erp_reference": "ERP-555",
        "organization_id": "org-1",
    })
    db.create_payment({
        "ap_item_id": "AP-posted",
        "organization_id": "org-1",
        "vendor_name": "PostedVendor",
        "amount": 2500.0,
        "due_date": "2026-05-15",
        "erp_reference": "ERP-555",
        "status": "ready_for_payment",
    })

    from clearledgr.core.skills.ap_skill import _handle_check_payment_readiness

    result = asyncio.run(
        _handle_check_payment_readiness(
            {"id": "AP-posted"},
            organization_id="org-1",
        )
    )
    assert result["ok"] is True
    assert result["payment_ready"] is True
    assert result["payment_status"] == "ready_for_payment"
    assert result["vendor_name"] == "PostedVendor"
    assert result["amount"] == 2500.0


def test_check_payment_readiness_item_not_found(tmp_path):
    _fresh_db(tmp_path)
    from clearledgr.core.skills.ap_skill import _handle_check_payment_readiness

    result = asyncio.run(
        _handle_check_payment_readiness(
            {"id": "AP-ghost"},
            organization_id="org-1",
        )
    )
    assert result["ok"] is True
    assert result["payment_ready"] is False
    assert result["reason"] == "ap_item_not_found"


# ---------------------------------------------------------------------------
# Worklist item payment fields
# ---------------------------------------------------------------------------


def test_build_worklist_item_includes_payment_fields_for_posted(tmp_path):
    db = _fresh_db(tmp_path)
    db.create_ap_item({
        "id": "AP-wl",
        "thread_id": "thread-wl",
        "state": "posted_to_erp",
        "vendor_name": "WorklistVendor",
        "amount": 750.0,
        "due_date": "2026-06-01",
        "organization_id": "org-1",
        "metadata": {
            "payment_id": "PAY-wl123",
            "payment_status": "scheduled",
            "due_date": "2026-06-01",
        },
    })

    from clearledgr.services.ap_item_service import build_worklist_item

    item = db.get_ap_item("AP-wl")
    result = build_worklist_item(db, item)
    assert result["payment_status"] == "scheduled"
    assert result["payment_id"] == "PAY-wl123"
    assert result["payment_due_date"] == "2026-06-01"


def test_build_worklist_item_no_payment_fields_for_received(tmp_path):
    db = _fresh_db(tmp_path)
    db.create_ap_item({
        "id": "AP-recv",
        "thread_id": "thread-recv",
        "state": "received",
        "vendor_name": "RecvVendor",
        "amount": 100.0,
        "organization_id": "org-1",
    })

    from clearledgr.services.ap_item_service import build_worklist_item

    item = db.get_ap_item("AP-recv")
    result = build_worklist_item(db, item)
    assert result["payment_status"] is None
    assert result["payment_id"] is None
    assert result["payment_due_date"] is None
