"""Tests for dispute/exception workflow.

Covers:
- Dispute store CRUD (create, get, list, update)
- Dispute service lifecycle (open, contact, respond, resolve, escalate, close)
- Dispute summary stats
- API endpoints (list, create, resolve, escalate, summary)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData
from clearledgr.services.dispute_service import DisputeService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "disputes.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
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
        "organization_id": "default",
    })


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------

class TestDisputeStore:
    def test_create_and_get(self, db):
        _create_ap_item(db, "ap-1")
        dispute = db.create_dispute(
            ap_item_id="ap-1",
            organization_id="default",
            dispute_type="missing_po",
            vendor_name="Test Vendor",
            description="PO number missing from invoice",
        )
        assert dispute["id"].startswith("dsp_")
        assert dispute["status"] == "open"

        found = db.get_dispute(dispute["id"])
        assert found is not None
        assert found["dispute_type"] == "missing_po"

    def test_list_by_org(self, db):
        _create_ap_item(db, "ap-2")
        _create_ap_item(db, "ap-3")
        db.create_dispute("ap-2", "default", "wrong_amount")
        db.create_dispute("ap-3", "default", "duplicate")

        disputes = db.list_disputes("default")
        assert len(disputes) == 2

    def test_list_by_status(self, db):
        _create_ap_item(db, "ap-4")
        d = db.create_dispute("ap-4", "default", "missing_info")
        db.update_dispute(d["id"], status="resolved", resolved_at=datetime.now(timezone.utc).isoformat())

        open_disputes = db.list_disputes("default", status="open")
        assert len(open_disputes) == 0

        resolved = db.list_disputes("default", status="resolved")
        assert len(resolved) == 1

    def test_get_disputes_for_item(self, db):
        _create_ap_item(db, "ap-5")
        db.create_dispute("ap-5", "default", "missing_po")
        db.create_dispute("ap-5", "default", "wrong_amount")

        disputes = db.get_disputes_for_item("ap-5")
        assert len(disputes) == 2

    def test_update(self, db):
        _create_ap_item(db, "ap-6")
        d = db.create_dispute("ap-6", "default", "other")
        db.update_dispute(d["id"], status="vendor_contacted", vendor_contacted_at="2026-04-04T10:00:00Z")

        updated = db.get_dispute(d["id"])
        assert updated["status"] == "vendor_contacted"

    def test_get_nonexistent(self, db):
        assert db.get_dispute("dsp_nonexistent") is None


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------

class TestDisputeService:
    def test_full_lifecycle(self, db):
        _create_ap_item(db, "lc-1", "Acme Corp")
        svc = DisputeService("default")

        # Open
        dispute = svc.open_dispute("lc-1", "missing_po", description="No PO on invoice")
        assert dispute["status"] == "open"
        assert dispute["vendor_name"] == "Acme Corp"  # auto-filled

        # Contact vendor
        svc.mark_vendor_contacted(dispute["id"], followup_thread_id="thread-123")
        d = db.get_dispute(dispute["id"])
        assert d["status"] == "vendor_contacted"
        assert d["followup_count"] == 1

        # Response received
        svc.mark_response_received(dispute["id"])
        d = db.get_dispute(dispute["id"])
        assert d["status"] == "response_received"

        # Resolve
        svc.resolve_dispute(dispute["id"], "Vendor provided PO #12345")
        d = db.get_dispute(dispute["id"])
        assert d["status"] == "resolved"
        assert d["resolution"] == "Vendor provided PO #12345"

    def test_escalation(self, db):
        _create_ap_item(db, "esc-1")
        svc = DisputeService("default")
        dispute = svc.open_dispute("esc-1", "vendor_mismatch")
        svc.escalate_dispute(dispute["id"])

        d = db.get_dispute(dispute["id"])
        assert d["status"] == "escalated"
        assert d["escalated_at"] is not None

    def test_close_without_resolution(self, db):
        _create_ap_item(db, "cls-1")
        svc = DisputeService("default")
        dispute = svc.open_dispute("cls-1", "duplicate")
        svc.close_dispute(dispute["id"], "Duplicate dispute — merged with DSP-001")

        d = db.get_dispute(dispute["id"])
        assert d["status"] == "closed"

    def test_list_open(self, db):
        _create_ap_item(db, "lo-1")
        _create_ap_item(db, "lo-2")
        svc = DisputeService("default")
        svc.open_dispute("lo-1", "missing_po")
        d2 = svc.open_dispute("lo-2", "wrong_amount")
        svc.resolve_dispute(d2["id"], "Fixed")

        open_disputes = svc.list_open()
        assert len(open_disputes) == 1

    def test_summary(self, db):
        _create_ap_item(db, "sm-1")
        _create_ap_item(db, "sm-2")
        _create_ap_item(db, "sm-3")
        svc = DisputeService("default")
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
        from clearledgr.api import workspace_shell as ws_module

        def _fake_user():
            return TokenData(
                user_id="dsp-user",
                email="dsp@test.com",
                organization_id="default",
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
        db.create_dispute("api-2", "default", "wrong_amount")
        resp = client.get("/api/workspace/disputes")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

    def test_summary_endpoint(self, client, db):
        _create_ap_item(db, "api-3")
        db.create_dispute("api-3", "default", "duplicate")
        resp = client.get("/api/workspace/disputes/summary")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_resolve_endpoint(self, client, db):
        _create_ap_item(db, "api-4")
        d = db.create_dispute("api-4", "default", "missing_info")
        resp = client.post(
            f"/api/workspace/disputes/{d['id']}/resolve",
            json={"resolution": "Vendor provided info"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "resolved"

    def test_escalate_endpoint(self, client, db):
        _create_ap_item(db, "api-5")
        d = db.create_dispute("api-5", "default", "vendor_mismatch")
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
