from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import main as _main
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
    # Re-read ``main.app`` on every test rather than binding it at
    # module import time. ``test_runtime_surface_scope`` reloads main
    # which swaps ``main.app`` for a fresh FastAPI instance; any test
    # file that did ``from main import app`` at import time would be
    # left pointing at a stale pre-reload app and all its auth
    # dependency-overrides would target the wrong instance.
    return TestClient(_main.app)


def _create_ap_item(
    db,
    *,
    item_id: str,
    state: str,
    metadata: dict,
    confidence: float = 0.99,
) -> dict:
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
            "confidence": confidence,
            "organization_id": "default",
            "metadata": metadata,
        }
    )


def test_extension_pipeline_normalizes_exception_taxonomy(client, db):
    from datetime import datetime, timezone

    from clearledgr.core.auth import TokenData, get_current_user
    import main as _m
    app = _m.app

    def _mock_user():
        return TokenData(
            user_id="test-user",
            email="test@default.com",
            organization_id="default",
            role="user",
            exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )

    app.dependency_overrides[get_current_user] = _mock_user
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

    try:
        response = client.get("/extension/pipeline?organization_id=default")
        assert response.status_code == 200
        payload = response.json()
        rows = payload.get("pending_approval", [])
        row = next((entry for entry in rows if entry.get("id") == item["id"]), None)
        assert row is not None
        assert row["exception_code"] == "po_missing_reference"
        assert row["exception_severity"] == "medium"
        assert row.get("priority_score") is not None
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_vendor_followup_endpoint_requires_auth(client):
    response = client.post(
        "/extension/vendor-followup",
        json={
            "email_id": "gmail-thread-unauth",
            "organization_id": "default",
        },
    )
    assert response.status_code == 401


def test_worklist_derives_budget_exception_and_teams_interactive(monkeypatch, client, db):
    # §12 / §6.8 — Teams is disabled in V1 by default. This test
    # exercises the Teams-interactive derivation path (post-V1
    # behaviour), so opt into the flag explicitly.
    monkeypatch.setenv("FEATURE_TEAMS_ENABLED", "true")

    from datetime import datetime, timezone

    from clearledgr.core.auth import TokenData, get_current_user
    import main as _m
    app = _m.app

    def _mock_user():
        return TokenData(
            user_id="test-user",
            email="test@default.com",
            organization_id="default",
            role="user",
            exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )

    app.dependency_overrides[get_current_user] = _mock_user

    try:
        _run_worklist_test(monkeypatch, client, db)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _run_worklist_test(monkeypatch, client, db):
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
    assert row["next_action"] == "budget_decision"
    assert row["requires_field_review"] is False
    assert isinstance(row["confidence_blockers"], list)

    confidence_item = _create_ap_item(
        db,
        item_id="CONF-REVIEW-1",
        state="needs_approval",
        metadata={
            "field_confidences": {
                "vendor": 0.82,
                "amount": 0.99,
                "invoice_number": 0.99,
                "due_date": 0.99,
            }
        },
    )
    db.update_ap_item(confidence_item["id"], confidence=0.99)

    worklist_response = client.get("/extension/worklist?organization_id=default")
    assert worklist_response.status_code == 200
    worklist_rows = worklist_response.json()["items"]
    confidence_row = next(
        (entry for entry in worklist_rows if entry.get("id") == confidence_item["id"]),
        None,
    )
    assert confidence_row is not None
    assert confidence_row["requires_field_review"] is True
    assert confidence_row["next_action"] == "review_fields"
    assert any(blocker["field"] == "vendor" for blocker in confidence_row["confidence_blockers"])

    class _FakeWorkflow:
        async def approve_invoice(self, **kwargs):
            return {"status": "approved", "kwargs": kwargs}

        async def request_budget_adjustment(self, **kwargs):
            return {"status": "needs_info", "kwargs": kwargs}

        async def reject_invoice(self, **kwargs):
            return {"status": "rejected", "kwargs": kwargs}

    async def _fake_dispatch(runtime, intent, payload, *, idempotency_key=None):
        if intent == "approve_invoice":
            return {"status": "approved", "result": {"status": "approved"}}
        return {"status": "error"}

    monkeypatch.setattr("clearledgr.api.teams_invoices._dispatch_runtime_intent", _fake_dispatch)
    monkeypatch.setattr(
        "clearledgr.api.teams_invoices._verify_teams_token",
        lambda _auth: {"appid": "test-bot", "iat": 1890000000},
    )

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
        headers={"Authorization": "Bearer test-token"},
    )
    assert interactive_response.status_code == 200
    payload = interactive_response.json()
    assert payload["status"] == "approved"

    # Teams state is now stored in channel_threads (Gap #11) instead of
    # the AP item metadata blob.
    threads = db.get_channel_threads(item["id"])
    teams_thread = next((t for t in threads if t.get("channel") == "teams"), None)
    assert teams_thread is not None, f"No teams channel_thread found; threads={threads}"
    assert teams_thread["state"] == "approved"
    assert teams_thread["conversation_id"] == "19:finance"
    assert teams_thread["message_id"] == "msg-001"
