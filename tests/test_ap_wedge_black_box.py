from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import create_access_token  # noqa: E402
from clearledgr.services.invoice_workflow import InvoiceWorkflowService  # noqa: E402


def _jwt_for(org_id: str, user_id: str = "ops-user", role: str = "owner") -> str:
    return create_access_token(
        user_id=user_id,
        email=f"{user_id}@{org_id}.example",
        organization_id=org_id,
        role=role,
        expires_delta=timedelta(hours=1),
    )


def _auth_headers(org_id: str) -> dict:
    return {"Authorization": f"Bearer {_jwt_for(org_id)}"}


def _create_validated_item(db, item_id: str = "ap-black-box-1") -> dict:
    return db.create_ap_item(
        {
            "id": item_id,
            "invoice_key": f"inv-{item_id}",
            "thread_id": f"thread-{item_id}",
            "message_id": f"msg-{item_id}",
            "subject": "Invoice for black-box route",
            "sender": "billing@example.com",
            "vendor_name": "Black Box Vendor",
            "amount": 245.0,
            "currency": "USD",
            "invoice_number": f"INV-{item_id}",
            "state": "validated",
            "organization_id": "default",
            "metadata": {"correlation_id": f"corr-{item_id}"},
        }
    )


def test_request_approval_black_box_route_drives_runtime_workflow_and_audit(tmp_path, monkeypatch):
    db = db_module.get_db()
    db.initialize()

    item = _create_validated_item(db)

    async def _fake_send_for_approval(self, invoice, extra_context=None):
        db.update_ap_item(
            item["id"],
            state="needs_approval",
            _actor_type="system",
            _actor_id="workflow_test_stub",
        )
        db.update_ap_item_metadata_merge(
            item["id"],
            {
                "approval_requested_at": "2026-03-27T00:00:00+00:00",
                "approval_sent_to": ["approver@clearledgr.com"],
                "approval_channel": "cl-finance-ap",
                "extra_context": extra_context or {},
            },
        )
        return {
            "status": "pending_approval",
            "channel": "slack",
            "email_id": getattr(invoice, "gmail_id", None),
        }

    monkeypatch.setattr(InvoiceWorkflowService, "_send_for_approval", _fake_send_for_approval)

    client = TestClient(app)
    response = client.post(
        "/api/agent/intents/execute",
        headers=_auth_headers("default"),
        json={
            "intent": "request_approval",
            "input": {
                "ap_item_id": item["id"],
                "email_id": item["thread_id"],
                "reason": "black_box_wedge_test",
            },
            "organization_id": "default",
            "idempotency_key": "idem-black-box-request-approval",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending_approval"
    assert payload["ap_item_id"] == item["id"]
    assert payload["email_id"] == item["thread_id"]

    stored = db.get_ap_item(item["id"])
    assert stored is not None
    assert stored["state"] == "needs_approval"
    metadata_raw = stored.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else json.loads(metadata_raw or "{}")
    assert metadata["approval_requested_at"] == "2026-03-27T00:00:00+00:00"
    assert metadata["approval_sent_to"] == ["approver@clearledgr.com"]

    audit_rows = db.list_ap_audit_events(item["id"])
    event_types = [row.get("event_type") for row in audit_rows]
    assert "state_transition" in event_types
    assert "approval_request_routed" in event_types
