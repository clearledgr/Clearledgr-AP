from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app
from clearledgr.core import database as db_module


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "v1-core.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


@pytest.fixture()
def client(db):
    return TestClient(app)


def _create_ap_item(db, *, item_id: str, state: str, metadata: dict) -> dict:
    return db.create_ap_item(
        {
            "id": item_id,
            "invoice_key": f"inv-{item_id}",
            "thread_id": f"thread-{item_id}",
            "message_id": f"msg-{item_id}",
            "subject": "Invoice needs review",
            "sender": "billing@example.com",
            "vendor_name": "Google",
            "amount": 1200.0,
            "currency": "USD",
            "invoice_number": f"INV-{item_id}",
            "state": state,
            "organization_id": "default",
            "metadata": metadata,
        }
    )


def test_extension_pipeline_normalizes_exception_taxonomy(client, db):
    item = _create_ap_item(
        db,
        item_id="PIPE-EX-1",
        state="needs_approval",
        metadata={
            "validation_gate": {
                "reason_codes": ["po_required_missing"],
                "reasons": [
                    {
                        "code": "po_required_missing",
                        "message": "PO is required for this vendor",
                        "severity": "warning",
                    }
                ],
            }
        },
    )

    response = client.get("/extension/pipeline?organization_id=default")
    assert response.status_code == 200
    payload = response.json()
    rows = payload.get("pending_approval", [])
    row = next((entry for entry in rows if entry.get("id") == item["id"]), None)
    assert row is not None
    assert row["exception_code"] == "po_missing_reference"
    assert row["exception_severity"] == "medium"
    assert row.get("priority_score") is not None


def test_worklist_derives_budget_exception_and_teams_interactive(monkeypatch, client, db):
    item = _create_ap_item(
        db,
        item_id="TEAM-BUDGET-1",
        state="needs_approval",
        metadata={
            "budget_impact": [
                {
                    "budget_name": "Software",
                    "after_approval_status": "exceeded",
                    "after_approval_percent": 108.0,
                    "remaining": -500.0,
                    "invoice_amount": 1200.0,
                }
            ]
        },
    )

    worklist_response = client.get("/extension/worklist?organization_id=default")
    assert worklist_response.status_code == 200
    worklist_rows = worklist_response.json()["items"]
    row = next((entry for entry in worklist_rows if entry.get("id") == item["id"]), None)
    assert row is not None
    assert row["exception_code"] == "budget_overrun"
    assert row["exception_severity"] == "critical"
    assert row["budget_requires_decision"] is True

    class _FakeWorkflow:
        async def approve_invoice(self, **kwargs):
            return {"status": "approved", "kwargs": kwargs}

        async def request_budget_adjustment(self, **kwargs):
            return {"status": "needs_info", "kwargs": kwargs}

        async def reject_invoice(self, **kwargs):
            return {"status": "rejected", "kwargs": kwargs}

    monkeypatch.setattr("clearledgr.api.teams_invoices.get_invoice_workflow", lambda _org: _FakeWorkflow())

    interactive_response = client.post(
        "/teams/invoices/interactive",
        json={
            "action": "approve_budget_override",
            "email_id": item["thread_id"],
            "organization_id": "default",
            "actor": "approver@clearledgr.com",
            "conversation_id": "19:finance",
            "message_id": "msg-001",
            "justification": "Critical month-end payment",
        },
    )
    assert interactive_response.status_code == 200
    payload = interactive_response.json()
    assert payload["status"] == "approved"

    stored = db.get_ap_item(item["id"])
    metadata_raw = stored.get("metadata")
    if isinstance(metadata_raw, str):
        metadata = json.loads(metadata_raw or "{}")
    else:
        metadata = dict(metadata_raw or {})
    assert metadata["teams"]["state"] == "approved"
    assert metadata["teams"]["channel"] == "19:finance"
    assert metadata["teams"]["message_id"] == "msg-001"
