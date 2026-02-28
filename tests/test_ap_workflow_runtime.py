from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app
from clearledgr.core import database as db_module
from clearledgr.core.ap_states import APState
from clearledgr.services.invoice_workflow import InvoiceData, InvoiceWorkflowService
from clearledgr.workflows import ap_workflow as ap_workflow_module
from clearledgr.workflows.temporal_runtime import TemporalRuntime


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "ap-workflow-runtime.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


def test_ap_workflow_bindings_are_executable(monkeypatch, db):
    bindings = ap_workflow_module.validate_workflow_bindings()
    assert bindings["valid"] is True
    assert bindings["missing"] == []

    service = InvoiceWorkflowService(organization_id="default")
    service.db = db

    async def _fake_process_new_invoice(self, invoice):
        self.db.save_invoice_status(
            gmail_id=invoice.gmail_id,
            status="received",
            email_subject=invoice.subject,
            vendor=invoice.vendor_name,
            amount=invoice.amount,
            currency=invoice.currency,
            invoice_number=invoice.invoice_number,
            due_date=invoice.due_date,
            confidence=invoice.confidence,
            organization_id=self.organization_id,
        )
        self.db.update_invoice_status(gmail_id=invoice.gmail_id, status="validated")
        return {"status": "pending_approval", "invoice_id": invoice.gmail_id}

    monkeypatch.setattr(InvoiceWorkflowService, "process_new_invoice", _fake_process_new_invoice)

    invoice = InvoiceData(
        gmail_id="gmail-ap-workflow-dispatch",
        subject="Invoice",
        sender="billing@example.com",
        vendor_name="Acme",
        amount=100.0,
        confidence=0.91,
        invoice_number="INV-DISPATCH-1",
        due_date="2026-03-10",
        organization_id="default",
    )

    dispatch = asyncio.run(
        ap_workflow_module.dispatch_step(
            service,
            state=APState.RECEIVED,
            invoice=invoice,
        )
    )

    payload = dispatch.to_dict()
    assert payload["step"]["state"] == "received"
    assert payload["step"]["execute"] == "process_new_invoice"
    assert payload["status"] == "pending_approval"
    assert payload["current_state"] == "validated"


def test_local_temporal_runtime_persists_ap_workflow_run_and_status(monkeypatch, db):
    async def _fake_process_new_invoice(self, invoice):
        self.db.save_invoice_status(
            gmail_id=invoice.gmail_id,
            status="received",
            email_subject=invoice.subject,
            vendor=invoice.vendor_name,
            amount=invoice.amount,
            currency=invoice.currency,
            invoice_number=invoice.invoice_number,
            due_date=invoice.due_date,
            confidence=invoice.confidence,
            organization_id=self.organization_id,
        )
        self.db.update_invoice_status(gmail_id=invoice.gmail_id, status="validated")
        return {"status": "pending_approval", "invoice_id": invoice.gmail_id}

    monkeypatch.setattr(InvoiceWorkflowService, "process_new_invoice", _fake_process_new_invoice)

    runtime = TemporalRuntime(db=db)
    result = asyncio.run(
        runtime.start_invoice(
            {
                "gmail_id": "gmail-runtime-1",
                "subject": "Invoice Runtime",
                "sender": "billing@example.com",
                "vendor_name": "Acme",
                "amount": 150.0,
                "currency": "USD",
                "invoice_number": "INV-RUNTIME-1",
                "due_date": "2026-03-11",
                "confidence": 0.88,
                "organization_id": "default",
            },
            organization_id="default",
            wait=True,
        )
    )

    assert result["status"] == "completed"
    workflow_id = result["workflow_id"]
    assert workflow_id
    assert result["workflow_type"] == "ap_invoice_entry"
    assert result["runtime_backend"] == "local_db"
    assert result["result"]["entry_result"]["dispatch"]["step"]["state"] == "received"

    persisted = db.get_workflow_run(workflow_id)
    assert persisted is not None
    assert persisted["status"] == "completed"
    assert persisted["workflow_type"] == "ap_invoice_entry"
    assert persisted["ap_item_id"]

    ap_item = db.get_ap_item(str(persisted["ap_item_id"]))
    assert ap_item is not None
    assert ap_item["workflow_id"] == workflow_id
    assert ap_item["run_id"] == workflow_id


def test_extension_workflow_status_endpoint_uses_local_runtime(db):
    from datetime import datetime, timezone

    from clearledgr.core.auth import TokenData, get_current_user

    def _mock_user():
        return TokenData(
            user_id="workflow-user",
            email="workflow@default.com",
            organization_id="default",
            role="user",
            exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )

    app.dependency_overrides[get_current_user] = _mock_user
    runtime = TemporalRuntime(db=db)
    run = db.create_workflow_run(
        {
            "workflow_name": "APInvoiceEntryWorkflow",
            "workflow_type": "ap_invoice_entry",
            "organization_id": "default",
            "status": "running",
            "runtime_backend": "local_db",
            "input_json": {"gmail_id": "gmail-endpoint-1"},
            "metadata_json": {"source": "test"},
        }
    )

    try:
        client = TestClient(app)
        response = client.get(f"/extension/workflow/{run['id']}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["workflow_id"] == run["id"]
        assert payload["status"] == "running"
        assert payload["runtime_backend"] == "local_db"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
