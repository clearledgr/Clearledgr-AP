"""Tests for dispute/exception workflow.

Covers:
- Dispute store CRUD (create, get, list, update)
- Dispute service lifecycle (open, contact, respond, resolve, escalate, close)
- Dispute summary stats
- API endpoints (list, create, resolve, escalate, summary)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from solden.core import database as db_module
from solden.core.auth import TokenData
from solden.services.dispute_service import DisputeService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


def _create_ap_item(db, item_id, vendor="Test Vendor"):
    db.create_ap_item({
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"t-{item_id}",
        "message_id": f"m-{item_id}",
        "subject": f"Invoice from {vendor}",
        "sender": "v@test.com",
        "vendor_name": vendor,
        "amount": 1000.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "needs_info",
        "organization_id": "org-test",
    })


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------

class TestDisputeStore:
    def test_create_and_get(self, db):
        _create_ap_item(db, "ap-1")
        dispute = db.create_dispute(
            ap_item_id="ap-1",
            organization_id="org-test",
            dispute_type="missing_po",
            vendor_name="Test Vendor",
            description="PO number missing from invoice",
        )
        assert dispute["id"].startswith("dsp_")
        assert dispute["status"] == "open"

        found = db.get_dispute(dispute["id"], "org-test")
        # Cross-tenant fail-closed: the same id from a different org → None.
        assert db.get_dispute(dispute["id"], "other-tenant") is None
        assert found is not None
        assert found["dispute_type"] == "missing_po"

    def test_list_by_org(self, db):
        _create_ap_item(db, "ap-2")
        _create_ap_item(db, "ap-3")
        db.create_dispute("ap-2", "org-test", "wrong_amount")
        db.create_dispute("ap-3", "org-test", "duplicate")

        disputes = db.list_disputes("org-test")
        assert len(disputes) == 2

    def test_list_by_status(self, db):
        _create_ap_item(db, "ap-4")
        d = db.create_dispute("ap-4", "org-test", "missing_info")
        db.update_dispute(d["id"], "org-test", status="resolved", resolved_at=datetime.now(timezone.utc).isoformat())

        open_disputes = db.list_disputes("org-test", status="open")
        assert len(open_disputes) == 0

        resolved = db.list_disputes("org-test", status="resolved")
        assert len(resolved) == 1

    def test_get_disputes_for_item(self, db):
        _create_ap_item(db, "ap-5")
        db.create_dispute("ap-5", "org-test", "missing_po")
        db.create_dispute("ap-5", "org-test", "wrong_amount")

        disputes = db.get_disputes_for_item("ap-5", "org-test")
        assert len(disputes) == 2

    def test_update(self, db):
        _create_ap_item(db, "ap-6")
        d = db.create_dispute("ap-6", "org-test", "other")
        db.update_dispute(d["id"], "org-test", status="vendor_contacted", vendor_contacted_at="2026-04-04T10:00:00Z")

        updated = db.get_dispute(d["id"], "org-test")
        assert updated["status"] == "vendor_contacted"

    def test_get_nonexistent(self, db):
        assert db.get_dispute("dsp_nonexistent", "org-test") is None


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------

class TestDisputeService:
    def test_full_lifecycle(self, db):
        _create_ap_item(db, "lc-1", "Acme Corp")
        svc = DisputeService("org-test")

        # Open
        dispute = svc.open_dispute("lc-1", "missing_po", description="No PO on invoice")
        assert dispute["status"] == "open"
        assert dispute["vendor_name"] == "Acme Corp"  # auto-filled

        # Contact vendor
        svc.mark_vendor_contacted(dispute["id"], followup_thread_id="thread-123")
        d = db.get_dispute(dispute["id"], "org-test")
        assert d["status"] == "vendor_contacted"
        assert d["followup_count"] == 1

        # Response received
        svc.mark_response_received(dispute["id"])
        d = db.get_dispute(dispute["id"], "org-test")
        assert d["status"] == "response_received"

        # Resolve
        svc.resolve_dispute(dispute["id"], "Vendor provided PO #12345")
        d = db.get_dispute(dispute["id"], "org-test")
        assert d["status"] == "resolved"
        assert d["resolution"] == "Vendor provided PO #12345"

    def test_escalation(self, db):
        _create_ap_item(db, "esc-1")
        svc = DisputeService("org-test")
        dispute = svc.open_dispute("esc-1", "vendor_mismatch")
        svc.escalate_dispute(dispute["id"])

        d = db.get_dispute(dispute["id"], "org-test")
        assert d["status"] == "escalated"
        assert d["escalated_at"] is not None

    def test_close_without_resolution(self, db):
        _create_ap_item(db, "cls-1")
        svc = DisputeService("org-test")
        dispute = svc.open_dispute("cls-1", "duplicate")
        svc.close_dispute(dispute["id"], "Duplicate dispute — merged with DSP-001")

        d = db.get_dispute(dispute["id"], "org-test")
        assert d["status"] == "closed"

    def test_list_open(self, db):
        _create_ap_item(db, "lo-1")
        _create_ap_item(db, "lo-2")
        svc = DisputeService("org-test")
        svc.open_dispute("lo-1", "missing_po")
        d2 = svc.open_dispute("lo-2", "wrong_amount")
        svc.resolve_dispute(d2["id"], "Fixed")

        open_disputes = svc.list_open()
        assert len(open_disputes) == 1

    def test_summary(self, db):
        _create_ap_item(db, "sm-1")
        _create_ap_item(db, "sm-2")
        _create_ap_item(db, "sm-3")
        svc = DisputeService("org-test")
        svc.open_dispute("sm-1", "missing_po")
        svc.open_dispute("sm-2", "missing_po")
        d3 = svc.open_dispute("sm-3", "wrong_amount")
        svc.resolve_dispute(d3["id"], "Corrected")

        summary = svc.get_dispute_summary()
        assert summary["total"] == 3
        assert summary["open_count"] == 2
        assert summary["by_type"]["missing_po"] == 2
        assert summary["by_status"]["resolved"] == 1


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestDisputeEndpoints:
    @pytest.fixture()
    def client(self, db):
        from main import app
        from solden.api import workspace_shell as ws_module

        def _fake_user():
            return TokenData(
                user_id="dsp-user",
                email="dsp@test.com",
                organization_id="org-test",
                role="owner",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        app.dependency_overrides[ws_module.get_current_user] = _fake_user
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(ws_module.get_current_user, None)

    def test_create_dispute(self, client, db):
        _create_ap_item(db, "api-1")
        resp = client.post(
            "/api/workspace/disputes",
            json={"ap_item_id": "api-1", "dispute_type": "missing_po", "description": "No PO"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "open"

    def test_list_disputes(self, client, db):
        _create_ap_item(db, "api-2")
        db.create_dispute("api-2", "org-test", "wrong_amount")
        resp = client.get("/api/workspace/disputes")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

    def test_summary_endpoint(self, client, db):
        _create_ap_item(db, "api-3")
        db.create_dispute("api-3", "org-test", "duplicate")
        resp = client.get("/api/workspace/disputes/summary")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_resolve_endpoint(self, client, db):
        _create_ap_item(db, "api-4")
        d = db.create_dispute("api-4", "org-test", "missing_info")
        resp = client.post(
            f"/api/workspace/disputes/{d['id']}/resolve",
            json={"resolution": "Vendor provided info"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "resolved"

    def test_escalate_endpoint(self, client, db):
        _create_ap_item(db, "api-5")
        d = db.create_dispute("api-5", "org-test", "vendor_mismatch")
        resp = client.post(f"/api/workspace/disputes/{d['id']}/escalate")
        assert resp.status_code == 200
        assert resp.json()["status"] == "escalated"

    def test_resolve_nonexistent_returns_404(self, client, db):
        resp = client.post(
            "/api/workspace/disputes/dsp_nonexistent/resolve",
            json={"resolution": "test"},
        )
        assert resp.status_code == 404

    def test_create_missing_fields_returns_400(self, client, db):
        resp = client.post("/api/workspace/disputes", json={"ap_item_id": "x"})
        assert resp.status_code == 400


def test_open_dispute_does_not_leak_cross_tenant_vendor_name(db):
    """open_dispute auto-fills vendor_name from the AP item, but must NOT read
    another tenant's item: a cross-tenant ap_item_id with empty vendor_name
    must not copy that tenant's vendor_name into the caller's dispute."""
    db.ensure_organization("disp-org-a", organization_name="A")
    db.ensure_organization("disp-org-b", organization_name="B")
    db.create_ap_item({
        "id": "DISP-XT-ITEM", "invoice_key": "k", "thread_id": "t", "message_id": "m",
        "subject": "s", "sender": "x@b.com", "vendor_name": "SecretVendorB",
        "amount": 10.0, "currency": "USD", "invoice_number": "INV-XT",
        "state": "needs_info", "organization_id": "disp-org-b",
    })
    # Org A opens a dispute referencing org B's item, no vendor_name supplied.
    svc = DisputeService("disp-org-a")
    dispute = svc.open_dispute(ap_item_id="DISP-XT-ITEM", dispute_type="other")
    assert dispute["vendor_name"] != "SecretVendorB"
    assert (dispute.get("vendor_name") or "") == ""


def test_open_dispute_autofills_same_org_vendor_name(db):
    """Control: same-org item DOES auto-fill vendor_name (the feature works)."""
    db.ensure_organization("disp-org-a", organization_name="A")
    db.create_ap_item({
        "id": "DISP-OWN-ITEM", "invoice_key": "k2", "thread_id": "t2", "message_id": "m2",
        "subject": "s", "sender": "x@a.com", "vendor_name": "OwnVendorA",
        "amount": 10.0, "currency": "USD", "invoice_number": "INV-OWN",
        "state": "needs_info", "organization_id": "disp-org-a",
    })
    svc = DisputeService("disp-org-a")
    dispute = svc.open_dispute(ap_item_id="DISP-OWN-ITEM", dispute_type="other")
    assert dispute["vendor_name"] == "OwnVendorA"
