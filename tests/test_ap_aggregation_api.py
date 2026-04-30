from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from main import app
from clearledgr.api import ops as ops_module
from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    db = db_module.get_db()
    db.initialize()
    return db


@pytest.fixture()
def client(db):
    def _fake_user():
        return TokenData(
            user_id="ops-user-1",
            email="ops@example.com",
            organization_id="default",
            role="owner",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[ops_module.get_current_user] = _fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ops_module.get_current_user, None)


def test_ap_aggregation_ops_endpoint_requires_auth(db):
    app.dependency_overrides.pop(ops_module.get_current_user, None)
    client = TestClient(app)
    response = client.get("/api/ops/ap-aggregation?organization_id=default")
    assert response.status_code == 401


def _create_item(db, item_id: str, vendor: str, amount: float) -> dict:
    return db.create_ap_item(
        {
            "id": item_id,
            "invoice_key": f"inv-{item_id}",
            "thread_id": f"thread-{item_id}",
            "message_id": f"msg-{item_id}",
            "subject": f"Invoice for {vendor}",
            "sender": "billing@example.com",
            "vendor_name": vendor,
            "amount": amount,
            "currency": "USD",
            "invoice_number": f"INV-{item_id}",
            "state": "needs_approval",
            "organization_id": "default",
        }
    )


def test_ap_aggregation_endpoints_return_multi_system_metrics(client, db):
    item_one = _create_item(db, "AGG-API-1", "Google", 125.0)
    item_two = _create_item(db, "AGG-API-2", "Google", 300.0)
    db.link_ap_item_source(
        {
            "ap_item_id": item_one["id"],
            "source_type": "spreadsheet",
            "source_ref": "sheet-1",
            "subject": "Sheet 1",
            "sender": "sheets",
        }
    )
    db.link_ap_item_source(
        {
            "ap_item_id": item_two["id"],
            "source_type": "card_statement",
            "source_ref": "card-txn-1",
            "subject": "Card statement 1",
            "sender": "amex",
        }
    )

    ap_items_response = client.get("/api/ap/items/metrics/aggregation?organization_id=default")
    assert ap_items_response.status_code == 200
    ap_items_metrics = ap_items_response.json()["metrics"]
    assert ap_items_metrics["totals"]["items"] >= 2
    assert ap_items_metrics["sources"]["total_links"] >= 2
    assert any(row["vendor_name"] == "Google" for row in ap_items_metrics["spend_by_vendor"])

    ops_response = client.get("/api/ops/ap-aggregation?organization_id=default")
    assert ops_response.status_code == 200
    ops_metrics = ops_response.json()["metrics"]
    assert ops_metrics["totals"]["items"] >= 2
    assert "spreadsheet" in ops_metrics["sources"]["connected_systems"] or "card_statement" in ops_metrics["sources"]["connected_systems"]


def test_ap_kpis_surface_operator_metrics_and_pilot_scorecard(client, db):
    now = datetime.now(timezone.utc)
    overdue_requested_at = (now - timedelta(hours=9)).isoformat()
    overdue_created_at = (now - timedelta(hours=10)).isoformat()
    approved_created_at = (now - timedelta(hours=8)).isoformat()
    approved_at = (now - timedelta(hours=6)).isoformat()
    posted_at = (now - timedelta(hours=5)).isoformat()

    touchless_item = db.create_ap_item(
        {
            "id": "pilot-touchless-1",
            "invoice_key": "inv-pilot-touchless-1",
            "thread_id": "thread-pilot-touchless-1",
            "message_id": "msg-pilot-touchless-1",
            "subject": "Touchless invoice",
            "sender": "billing@example.com",
            "vendor_name": "Touchless Vendor",
            "amount": 125.0,
            "currency": "USD",
            "invoice_number": "INV-PILOT-TOUCHLESS-1",
            "state": "posted_to_erp",
            "organization_id": "default",
            "created_at": approved_created_at,
            "updated_at": posted_at,
            "erp_posted_at": posted_at,
            "metadata": {
                "ap_decision_recommendation": "approve",
            },
        }
    )

    handled_item = db.create_ap_item(
        {
            "id": "pilot-handled-1",
            "invoice_key": "inv-pilot-handled-1",
            "thread_id": "thread-pilot-handled-1",
            "message_id": "msg-pilot-handled-1",
            "subject": "Handled invoice",
            "sender": "billing@example.com",
            "vendor_name": "Handled Vendor",
            "amount": 240.0,
            "currency": "USD",
            "invoice_number": "INV-PILOT-HANDLED-1",
            "state": "posted_to_erp",
            "approval_required": True,
            "organization_id": "default",
            "created_at": approved_created_at,
            "updated_at": posted_at,
            "erp_posted_at": posted_at,
            "metadata": {
                "ap_decision_recommendation": "approve",
            },
        }
    )
    db.save_approval(
        {
            "ap_item_id": handled_item["id"],
            "channel_id": "slack-approvals",
            "message_ts": "1710000000.001",
            "source_channel": "slack",
            "status": "approved",
            "approved_by": "approver-1",
            "approved_at": approved_at,
            "organization_id": "default",
            "created_at": approved_created_at,
        }
    )
    db.append_audit_event(
        {
            "ap_item_id": touchless_item["id"],
            "event_type": "erp_post_attempted",
            "actor_type": "system",
            "actor_id": "erp-adapter",
            "organization_id": "default",
            "ts": approved_at,
        }
    )
    db.append_audit_event(
        {
            "ap_item_id": touchless_item["id"],
            "event_type": "erp_post_succeeded",
            "actor_type": "system",
            "actor_id": "erp-adapter",
            "organization_id": "default",
            "ts": posted_at,
        }
    )
    db.append_audit_event(
        {
            "ap_item_id": handled_item["id"],
            "event_type": "erp_post_attempted",
            "actor_type": "system",
            "actor_id": "erp-adapter",
            "organization_id": "default",
            "ts": approved_at,
        }
    )
    db.append_audit_event(
        {
            "ap_item_id": handled_item["id"],
            "event_type": "erp_post_failed",
            "actor_type": "system",
            "actor_id": "erp-adapter",
            "organization_id": "default",
            "ts": approved_at,
        }
    )
    db.append_audit_event(
        {
            "ap_item_id": handled_item["id"],
            "event_type": "erp_post_succeeded",
            "actor_type": "system",
            "actor_id": "erp-adapter",
            "organization_id": "default",
            "ts": posted_at,
        }
    )
    db.append_audit_event(
        {
            "ap_item_id": handled_item["id"],
            "event_type": "ap_decision_override",
            "actor_type": "user",
            "actor_id": "ops-user-1",
            "organization_id": "default",
            "ts": posted_at,
        }
    )

    approval_item = db.create_ap_item(
        {
            "id": "pilot-approval-1",
            "invoice_key": "inv-pilot-approval-1",
            "thread_id": "thread-pilot-approval-1",
            "message_id": "msg-pilot-approval-1",
            "subject": "Approval invoice",
            "sender": "billing@example.com",
            "vendor_name": "Approval Vendor",
            "amount": 310.0,
            "currency": "USD",
            "invoice_number": "INV-PILOT-APPROVAL-1",
            "state": "needs_approval",
            "approval_required": True,
            "approval_requested_at": overdue_requested_at,
            "organization_id": "default",
            "created_at": overdue_created_at,
            "updated_at": overdue_requested_at,
            "metadata": {
                "approval_escalation_count": 1,
                "approval_last_escalated_at": overdue_requested_at,
                "approval_reassignment_count": 1,
                "approval_last_reassigned_at": overdue_requested_at,
            },
        }
    )
    db.save_approval(
        {
            "ap_item_id": approval_item["id"],
            "channel_id": "slack-approvals",
            "message_ts": "1710000000.002",
            "source_channel": "slack",
            "status": "pending",
            "organization_id": "default",
            "created_at": overdue_requested_at,
        }
    )
    db.append_audit_event(
        {
            "ap_item_id": approval_item["id"],
            "event_type": "approval_escalation_sent",
            "actor_type": "system",
            "actor_id": "runtime",
            "organization_id": "default",
            "ts": now.isoformat(),
        }
    )
    db.append_audit_event(
        {
            "ap_item_id": approval_item["id"],
            "event_type": "approval_reassigned",
            "actor_type": "system",
            "actor_id": "runtime",
            "organization_id": "default",
            "ts": now.isoformat(),
        }
    )

    db.create_ap_item(
        {
            "id": "pilot-entity-1",
            "invoice_key": "inv-pilot-entity-1",
            "thread_id": "thread-pilot-entity-1",
            "message_id": "msg-pilot-entity-1",
            "subject": "Entity routing invoice",
            "sender": "billing@example.com",
            "vendor_name": "Routing Vendor",
            "amount": 180.0,
            "currency": "USD",
            "invoice_number": "INV-PILOT-ENTITY-1",
            "state": "validated",
            "organization_id": "default",
            "metadata": {
                "entity_routing": {
                    "status": "needs_review",
                    "reason": "Multiple entities matched.",
                    "candidates": [
                        {"entity_code": "US-01", "entity_name": "US Entity"},
                        {"entity_code": "GH-01", "entity_name": "Ghana Entity"},
                    ],
                }
            },
        }
    )

    resolved_entity_item = db.create_ap_item(
        {
            "id": "pilot-entity-2",
            "invoice_key": "inv-pilot-entity-2",
            "thread_id": "thread-pilot-entity-2",
            "message_id": "msg-pilot-entity-2",
            "subject": "Resolved entity invoice",
            "sender": "billing@example.com",
            "vendor_name": "Resolved Routing Vendor",
            "amount": 190.0,
            "currency": "USD",
            "invoice_number": "INV-PILOT-ENTITY-2",
            "state": "ready_to_post",
            "organization_id": "default",
            "metadata": {
                "entity_routing": {
                    "status": "resolved",
                    "selected": {"entity_code": "US-01", "entity_name": "US Entity"},
                    "candidates": [{"entity_code": "US-01", "entity_name": "US Entity"}],
                }
            },
        }
    )
    db.append_audit_event(
        {
            "ap_item_id": resolved_entity_item["id"],
            "event_type": "entity_route_resolved",
            "actor_type": "user",
            "actor_id": "ops-user-1",
            "organization_id": "default",
            "ts": now.isoformat(),
        }
    )

    response = client.get("/api/ops/ap-kpis?organization_id=default")
    assert response.status_code == 200
    payload = response.json()["kpis"]

    operator_metrics = payload["operator_metrics"]
    assert operator_metrics["live_queue"]["approval_queue_count"] == 1
    assert operator_metrics["live_queue"]["approval_sla_breached_open_count"] == 1
    assert operator_metrics["live_queue"]["approval_escalated_open_count"] == 1
    assert operator_metrics["live_queue"]["approval_reassigned_open_count"] == 1
    assert operator_metrics["live_queue"]["entity_route_needs_review_count"] == 1
    assert operator_metrics["activity"]["approval_escalation_event_count"] == 1
    assert operator_metrics["activity"]["approval_reassignment_event_count"] == 1
    assert operator_metrics["activity"]["entity_route_resolution_event_count"] == 1

    pilot_scorecard = payload["pilot_scorecard"]
    assert pilot_scorecard["summary"]["touchless_rate_pct"] == 50.0
    assert pilot_scorecard["summary"]["approval_sla_breached_open_count"] == 1
    assert pilot_scorecard["summary"]["entity_route_needs_review_count"] == 1
    assert pilot_scorecard["approval_workflow"]["escalated_open_count"] == 1
    assert pilot_scorecard["approval_workflow"]["reassigned_open_count"] == 1
    assert pilot_scorecard["entity_routing"]["single_candidate_resolved_count"] == 1
    assert any("approvals are currently beyond the" in line for line in pilot_scorecard["highlights"])

    proof_scorecard = payload["proof_scorecard"]
    assert proof_scorecard["summary"]["auto_approved_rate_pct"] == 50.0
    assert proof_scorecard["summary"]["human_override_rate_pct"] == 50.0
    assert proof_scorecard["summary"]["posting_success_rate_pct"] == 100.0
    assert proof_scorecard["summary"]["recovery_success_rate_pct"] == 100.0
    assert proof_scorecard["posting_reliability"]["attempted_count"] == 2
    assert proof_scorecard["recovery"]["attempted_count"] == 1
    assert proof_scorecard["recovery"]["recovered_count"] == 1
