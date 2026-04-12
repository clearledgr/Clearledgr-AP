"""BatchOps bulk endpoints — AGENT_DESIGN_SPECIFICATION §6.7.

Every bulk endpoint:
  - runs the action per item through the normal runtime/store path so
    Rule 1 pre-write and audit still fire,
  - captures per-item results instead of all-or-nothing,
  - caps batch size.

These tests lock in that contract.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import ap_items_action_routes as action_routes
from clearledgr.api.ap_item_contracts import (
    BulkApproveRequest,
    BulkRejectRequest,
    BulkRetryPostRequest,
    BulkSnoozeRequest,
)
from clearledgr.core import database as db_module
from clearledgr.core.auth import require_ops_user


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "bulk-batch-ops.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    instance = db_module.get_db()
    instance.initialize()
    return instance


def _fake_user():
    return SimpleNamespace(
        email="ops@example.com",
        user_id="ops-user",
        organization_id="default",
        role="ops",
    )


def _app_with_router() -> TestClient:
    app = FastAPI()
    app.include_router(action_routes.router, prefix="/api/ap/items")
    app.dependency_overrides[require_ops_user] = _fake_user
    return TestClient(app)


def _create_item(db, *, item_id: str, state: str = "needs_approval", thread_id: str = None) -> dict:
    return db.create_ap_item(
        {
            "id": item_id,
            "invoice_key": f"inv-{item_id}",
            "thread_id": thread_id or f"thread-{item_id}",
            "message_id": f"msg-{item_id}",
            "subject": f"Invoice {item_id}",
            "sender": "billing@example.com",
            "vendor_name": "Acme",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": f"INV-{item_id}",
            "state": state,
            "confidence": 0.99,
            "organization_id": "default",
            "metadata": {},
        }
    )


# ---------------------------------------------------------------------------
# Contract / validation tests
# ---------------------------------------------------------------------------


def test_bulk_contracts_require_nonempty_id_list():
    with pytest.raises(ValidationError):
        BulkApproveRequest(ap_item_ids=[])
    with pytest.raises(ValidationError):
        BulkRejectRequest(ap_item_ids=[], reason="why")


def test_bulk_contracts_cap_batch_at_100():
    too_many = [f"AP-{i}" for i in range(101)]
    with pytest.raises(ValidationError):
        BulkApproveRequest(ap_item_ids=too_many)
    with pytest.raises(ValidationError):
        BulkSnoozeRequest(ap_item_ids=too_many, duration_minutes=60)
    with pytest.raises(ValidationError):
        BulkRejectRequest(ap_item_ids=too_many, reason="why")


def test_bulk_reject_requires_reason():
    with pytest.raises(ValidationError):
        BulkRejectRequest(ap_item_ids=["AP-1"], reason="")


def test_bulk_snooze_caps_duration():
    # Max 30 days = 43200 minutes
    with pytest.raises(ValidationError):
        BulkSnoozeRequest(ap_item_ids=["AP-1"], duration_minutes=43201)
    with pytest.raises(ValidationError):
        BulkSnoozeRequest(ap_item_ids=["AP-1"], duration_minutes=0)


# ---------------------------------------------------------------------------
# Bulk snooze — runs end-to-end without mocking (no runtime needed)
# ---------------------------------------------------------------------------


def test_bulk_snooze_transitions_all_items_and_returns_per_item_results(db):
    _create_item(db, item_id="AP-S1", state="needs_approval")
    _create_item(db, item_id="AP-S2", state="needs_approval")
    _create_item(db, item_id="AP-S3", state="needs_approval")

    client = _app_with_router()
    response = client.post(
        "/api/ap/items/bulk-snooze?organization_id=default",
        json={
            "ap_item_ids": ["AP-S1", "AP-S2", "AP-S3"],
            "duration_minutes": 240,
            "note": "morning batch",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["succeeded"] == 3
    assert payload["failed"] == 0
    assert len(payload["results"]) == 3
    for result in payload["results"]:
        assert result["status"] == "snoozed"
        assert result["ok"] is True
        assert result["snoozed_until"]

    for item_id in ("AP-S1", "AP-S2", "AP-S3"):
        refreshed = db.get_ap_item(item_id)
        assert refreshed["state"] == "snoozed"


def test_bulk_snooze_reports_per_item_failures_without_aborting(db):
    _create_item(db, item_id="AP-OK", state="needs_approval")
    # Terminal state — invalid snooze transition
    _create_item(db, item_id="AP-BAD", state="closed")

    client = _app_with_router()
    response = client.post(
        "/api/ap/items/bulk-snooze?organization_id=default",
        json={
            "ap_item_ids": ["AP-OK", "AP-BAD", "AP-MISSING"],
            "duration_minutes": 60,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["succeeded"] == 1
    assert payload["failed"] == 2

    results_by_id = {r["ap_item_id"]: r for r in payload["results"]}
    assert results_by_id["AP-OK"]["status"] == "snoozed"
    assert results_by_id["AP-BAD"]["status"] == "error"
    assert "invalid_state_transition" in results_by_id["AP-BAD"]["reason"]
    assert results_by_id["AP-MISSING"]["status"] == "error"
    assert results_by_id["AP-MISSING"]["reason"] == "ap_item_not_found_or_wrong_org"


def test_bulk_snooze_rejects_wrong_org_items(db):
    _create_item(db, item_id="AP-WRONGORG", state="needs_approval")
    # Change org post-hoc
    db.update_ap_item("AP-WRONGORG", organization_id="other-org")

    client = _app_with_router()
    response = client.post(
        "/api/ap/items/bulk-snooze?organization_id=default",
        json={"ap_item_ids": ["AP-WRONGORG"], "duration_minutes": 60},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["succeeded"] == 0
    assert payload["results"][0]["reason"] == "ap_item_not_found_or_wrong_org"


# ---------------------------------------------------------------------------
# Bulk approve / reject — mock the runtime
# ---------------------------------------------------------------------------


def test_bulk_approve_calls_runtime_once_per_item_and_aggregates(db):
    _create_item(db, item_id="AP-A1")
    _create_item(db, item_id="AP-A2")
    _create_item(db, item_id="AP-A3")

    responses = [
        {"status": "posted_to_erp", "erp_reference": "ERP-1"},
        {"status": "approved"},
        {"status": "error", "reason": "validation_gate_failed"},
    ]

    mock_runtime = MagicMock()
    mock_runtime.execute_intent = AsyncMock(side_effect=responses)
    runtime_factory = MagicMock(return_value=mock_runtime)

    client = _app_with_router()
    with patch.object(action_routes.shared, "_finance_agent_runtime_cls", return_value=runtime_factory):
        response = client.post(
            "/api/ap/items/bulk-approve?organization_id=default",
            json={"ap_item_ids": ["AP-A1", "AP-A2", "AP-A3"]},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["succeeded"] == 2
    assert payload["failed"] == 1
    assert mock_runtime.execute_intent.await_count == 3
    results_by_id = {r["ap_item_id"]: r for r in payload["results"]}
    assert results_by_id["AP-A1"]["ok"] is True
    assert results_by_id["AP-A1"]["erp_reference"] == "ERP-1"
    assert results_by_id["AP-A2"]["ok"] is True
    assert results_by_id["AP-A3"]["ok"] is False


def test_bulk_approve_single_runtime_failure_does_not_abort_batch(db):
    _create_item(db, item_id="AP-EX1")
    _create_item(db, item_id="AP-EX2")

    async def _throw_then_succeed(intent, payload):
        if payload["ap_item_id"] == "AP-EX1":
            raise RuntimeError("network boom")
        return {"status": "posted_to_erp"}

    mock_runtime = MagicMock()
    mock_runtime.execute_intent = AsyncMock(side_effect=_throw_then_succeed)
    runtime_factory = MagicMock(return_value=mock_runtime)

    client = _app_with_router()
    with patch.object(action_routes.shared, "_finance_agent_runtime_cls", return_value=runtime_factory):
        response = client.post(
            "/api/ap/items/bulk-approve?organization_id=default",
            json={"ap_item_ids": ["AP-EX1", "AP-EX2"]},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["succeeded"] == 1
    assert payload["failed"] == 1
    results_by_id = {r["ap_item_id"]: r for r in payload["results"]}
    assert results_by_id["AP-EX1"]["status"] == "error"
    assert results_by_id["AP-EX2"]["status"] == "posted_to_erp"


def test_bulk_approve_override_forwards_justification(db):
    _create_item(db, item_id="AP-OV1")

    mock_runtime = MagicMock()
    mock_runtime.execute_intent = AsyncMock(return_value={"status": "approved"})
    runtime_factory = MagicMock(return_value=mock_runtime)

    client = _app_with_router()
    with patch.object(action_routes.shared, "_finance_agent_runtime_cls", return_value=runtime_factory):
        response = client.post(
            "/api/ap/items/bulk-approve?organization_id=default",
            json={
                "ap_item_ids": ["AP-OV1"],
                "override": True,
                "override_justification": "budget exception approved by CFO",
            },
        )
    assert response.status_code == 200

    call_args = mock_runtime.execute_intent.await_args
    intent, intent_payload = call_args.args
    assert intent == "approve_invoice"
    assert intent_payload["approve_override"] is True
    assert intent_payload["action_variant"] == "bulk_override"
    assert intent_payload["override_justification"] == "budget exception approved by CFO"


def test_bulk_reject_routes_to_reject_intent_and_tags_bulk_channel(db):
    _create_item(db, item_id="AP-R1")

    mock_runtime = MagicMock()
    mock_runtime.execute_intent = AsyncMock(return_value={"status": "rejected"})
    runtime_factory = MagicMock(return_value=mock_runtime)

    client = _app_with_router()
    with patch.object(action_routes.shared, "_finance_agent_runtime_cls", return_value=runtime_factory):
        response = client.post(
            "/api/ap/items/bulk-reject?organization_id=default",
            json={"ap_item_ids": ["AP-R1"], "reason": "duplicate"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["succeeded"] == 1

    call_args = mock_runtime.execute_intent.await_args
    intent, intent_payload = call_args.args
    assert intent == "reject_invoice"
    assert intent_payload["reason"] == "duplicate"
    assert intent_payload["source_channel"] == "gmail_extension_bulk"


# ---------------------------------------------------------------------------
# Bulk retry-post — state gate
# ---------------------------------------------------------------------------


def test_bulk_retry_post_rejects_items_not_in_failed_post_state(db):
    _create_item(db, item_id="AP-FAIL", state="failed_post")
    _create_item(db, item_id="AP-APPROVED", state="needs_approval")

    mock_runtime = MagicMock()
    mock_runtime.execute_intent = AsyncMock(return_value={"status": "posted"})
    runtime_factory = MagicMock(return_value=mock_runtime)

    client = _app_with_router()
    with patch.object(action_routes.shared, "_finance_agent_runtime_cls", return_value=runtime_factory):
        response = client.post(
            "/api/ap/items/bulk-retry-post?organization_id=default",
            json={"ap_item_ids": ["AP-FAIL", "AP-APPROVED"]},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["succeeded"] == 1
    assert payload["failed"] == 1
    results_by_id = {r["ap_item_id"]: r for r in payload["results"]}
    assert results_by_id["AP-FAIL"]["ok"] is True
    assert results_by_id["AP-APPROVED"]["status"] == "error"
    assert "invalid_state" in results_by_id["AP-APPROVED"]["reason"]
    # Runtime called only once — for the valid item
    assert mock_runtime.execute_intent.await_count == 1
