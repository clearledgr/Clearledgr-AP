import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app
from clearledgr.core import database as db_module
from clearledgr.services import browser_agent as browser_agent_module


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    db_module._DB_INSTANCE = None
    browser_agent_module._SERVICE = None
    db = db_module.get_db()
    db.initialize()
    return db


@pytest.fixture()
def client(db):
    return TestClient(app)


def _create_item(db):
    return db.create_ap_item(
        {
            "invoice_key": "vendor|agent|100.00|",
            "thread_id": "thread-agent",
            "message_id": "msg-agent",
            "subject": "Invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": "INV-AGENT",
            "state": "validated",
            "confidence": 0.9,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-agent",
        }
    )


def _create_session(client, ap_item_id):
    response = client.post(
        "/api/agent/sessions",
        json={"org_id": "default", "ap_item_id": ap_item_id, "actor_id": "test_agent"},
    )
    assert response.status_code == 200
    return response.json()["session"]["id"]


def test_read_action_allowed_and_audited(client, db):
    item = _create_item(db)
    session_id = _create_session(client, item["id"])

    response = client.post(
        f"/api/agent/sessions/{session_id}/commands",
        json={
            "actor_id": "test_agent",
            "tool_name": "read_page",
            "command_id": "cmd-read-1",
            "target": {"url": "https://mail.google.com/mail/u/0/#inbox"},
            "params": {"include_tables": True},
        },
    )
    assert response.status_code == 200
    event = response.json()["event"]
    assert event["status"] == "queued"

    audits = db.list_ap_audit_events(item["id"])
    assert any(a.get("event_type") == "browser_command_enqueued" for a in audits)


def test_blocked_domain_denied_and_audited(client, db):
    item = _create_item(db)
    session_id = _create_session(client, item["id"])
    response = client.post(
        f"/api/agent/sessions/{session_id}/commands",
        json={
            "actor_id": "test_agent",
            "tool_name": "read_page",
            "command_id": "cmd-read-blocked",
            "target": {"url": "https://evil.example.com"},
            "params": {},
        },
    )
    assert response.status_code == 200
    event = response.json()["event"]
    assert event["status"] == "denied_policy"
    assert str(event.get("policy_reason", "")).startswith("blocked_domain")


def test_sensitive_action_requires_confirmation_then_can_queue(client, db):
    item = _create_item(db)
    session_id = _create_session(client, item["id"])
    blocked = client.post(
        f"/api/agent/sessions/{session_id}/commands",
        json={
            "actor_id": "test_agent",
            "tool_name": "click",
            "command_id": "cmd-click-1",
            "target": {"url": "https://mail.google.com/mail/u/0/#inbox"},
            "params": {"selector": "button[aria-label='Archive']"},
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["event"]["status"] == "blocked_for_approval"

    confirmed = client.post(
        f"/api/agent/sessions/{session_id}/commands",
        json={
            "actor_id": "test_agent",
            "tool_name": "click",
            "command_id": "cmd-click-1",
            "target": {"url": "https://mail.google.com/mail/u/0/#inbox"},
            "params": {"selector": "button[aria-label='Archive']"},
            "confirm": True,
            "confirmed_by": "human_approver",
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["event"]["status"] == "queued"


def test_duplicate_result_is_idempotent(client, db):
    item = _create_item(db)
    session_id = _create_session(client, item["id"])
    client.post(
        f"/api/agent/sessions/{session_id}/commands",
        json={
            "actor_id": "test_agent",
            "tool_name": "read_page",
            "command_id": "cmd-result-1",
            "target": {"url": "https://mail.google.com/mail/u/0/#inbox"},
        },
    )

    first = client.post(
        f"/api/agent/sessions/{session_id}/results",
        json={
            "actor_id": "runner",
            "command_id": "cmd-result-1",
            "status": "completed",
            "result_payload": {"ok": True, "url": "https://mail.google.com/mail/u/0/#inbox"},
        },
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/agent/sessions/{session_id}/results",
        json={
            "actor_id": "runner",
            "command_id": "cmd-result-1",
            "status": "completed",
            "result_payload": {"ok": True, "url": "https://mail.google.com/mail/u/0/#inbox"},
        },
    )
    assert second.status_code == 200

    session_payload = client.get(f"/api/agent/sessions/{session_id}").json()
    events = [event for event in session_payload["events"] if event["command_id"] == "cmd-result-1"]
    assert len(events) == 1
    assert events[0]["status"] == "completed"


def test_browser_evidence_is_queryable_via_audit_endpoint(client, db):
    item = _create_item(db)
    session_id = _create_session(client, item["id"])
    client.post(
        f"/api/agent/sessions/{session_id}/commands",
        json={
            "actor_id": "test_agent",
            "tool_name": "capture_evidence",
            "command_id": "cmd-evidence-1",
            "target": {"url": "https://mail.google.com/mail/u/0/#inbox"},
            "params": {"selector": "body"},
        },
    )
    client.post(
        f"/api/agent/sessions/{session_id}/results",
        json={
            "actor_id": "runner",
            "command_id": "cmd-evidence-1",
            "status": "completed",
            "result_payload": {
                "url": "https://mail.google.com/mail/u/0/#inbox",
                "tab_id": 21,
                "selector": "body",
                "action": "capture_evidence",
                "before": "a",
                "after": "b",
                "screenshot_hash": "abc123",
                "correlation_id": "corr-1",
            },
        },
    )
    response = client.get(f"/api/ap/items/{item['id']}/audit")
    assert response.status_code == 200
    events = response.json()["events"]
    browser_events = [event for event in events if event.get("event_type") == "browser_command_result"]
    assert browser_events
    payload = browser_events[-1].get("payload_json") or {}
    assert payload.get("result", {}).get("screenshot_hash") == "abc123"


def test_policy_misconfiguration_fails_safe(client, db):
    item = _create_item(db)
    session_id = _create_session(client, item["id"])
    policy_response = client.put(
        "/api/agent/policies/browser",
        json={
            "org_id": "default",
            "updated_by": "tester",
            "enabled": True,
            "config": {
                "allowed_domains": ["finance.internal"],
                "blocked_actions": [],
                "require_confirmation_for": ["click"],
            },
        },
    )
    assert policy_response.status_code == 200

    blocked = client.post(
        f"/api/agent/sessions/{session_id}/commands",
        json={
            "actor_id": "test_agent",
            "tool_name": "read_page",
            "command_id": "cmd-policy-1",
            "target": {"url": "https://mail.google.com/mail/u/0/#inbox"},
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["event"]["status"] == "denied_policy"


def test_preview_endpoint_returns_policy_summary(client, db):
    item = _create_item(db)
    session_id = _create_session(client, item["id"])

    response = client.post(
        f"/api/agent/sessions/{session_id}/commands/preview",
        json={
            "actor_id": "test_agent",
            "actor_role": "ap_operator",
            "workflow_id": "invoice_intake",
            "tool_name": "click",
            "command_id": "cmd-preview-1",
            "target": {"url": "https://mail.google.com/mail/u/0/#inbox"},
            "params": {"selector": "button[aria-label='Archive']"},
        },
    )
    assert response.status_code == 200
    payload = response.json()["preview"]
    assert payload["decision"]["allowed"] is True
    assert payload["decision"]["requires_confirmation"] is True
    assert payload["decision"]["tool_risk"] == "high_risk"
    assert "summary" in payload


def test_workflow_override_applies_to_policy_evaluation(client, db):
    item = _create_item(db)
    session_id = _create_session(client, item["id"])
    policy_response = client.put(
        "/api/agent/policies/browser",
        json={
            "org_id": "default",
            "updated_by": "tester",
            "enabled": True,
            "config": {
                "allowed_domains": ["mail.google.com"],
                "blocked_actions": [],
                "require_confirmation_for": ["click"],
                "workflow_overrides": {
                    "strict_posting": {
                        "blocked_actions": ["read_page"],
                    }
                },
            },
        },
    )
    assert policy_response.status_code == 200

    blocked = client.post(
        f"/api/agent/sessions/{session_id}/commands",
        json={
            "actor_id": "test_agent",
            "workflow_id": "strict_posting",
            "tool_name": "read_page",
            "command_id": "cmd-workflow-1",
            "target": {"url": "https://mail.google.com/mail/u/0/#inbox"},
        },
    )
    assert blocked.status_code == 200
    event = blocked.json()["event"]
    assert event["status"] == "denied_policy"
    assert event["policy_reason"].startswith("blocked_action")


def test_macro_preview_and_dispatch_flow(client, db):
    item = _create_item(db)
    session_id = _create_session(client, item["id"])

    preview = client.post(
        f"/api/agent/sessions/{session_id}/macros/ingest_invoice_match_po",
        json={
            "actor_id": "test_agent",
            "dry_run": True,
        },
    )
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["status"] == "preview"
    assert len(preview_payload["commands"]) >= 3

    dispatched = client.post(
        f"/api/agent/sessions/{session_id}/macros/ingest_invoice_match_po",
        json={
            "actor_id": "test_agent",
            "dry_run": False,
        },
    )
    assert dispatched.status_code == 200
    body = dispatched.json()
    assert body["status"] == "dispatched"
    assert body["queued"] + body["blocked"] + body["denied"] >= 1


def test_browser_agent_metrics_endpoint(client, db):
    item = _create_item(db)
    session_id = _create_session(client, item["id"])
    enqueue = client.post(
        f"/api/agent/sessions/{session_id}/commands",
        json={
            "actor_id": "test_agent",
            "tool_name": "read_page",
            "command_id": "cmd-metrics-1",
            "target": {"url": "https://mail.google.com/mail/u/0/#inbox"},
        },
    )
    assert enqueue.status_code == 200
    complete = client.post(
        f"/api/agent/sessions/{session_id}/results",
        json={
            "actor_id": "runner",
            "command_id": "cmd-metrics-1",
            "status": "completed",
            "result_payload": {"ok": True},
        },
    )
    assert complete.status_code == 200

    response = client.get("/api/ops/browser-agent?organization_id=default&window_hours=24")
    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert metrics["totals"]["events"] >= 1
    assert "execution" in metrics
    assert "api_first_routing" in metrics


def test_erp_routing_strategy_endpoint(client, db):
    _create_item(db)
    response = client.get("/api/ops/erp-routing-strategy?organization_id=default")
    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_id"] == "default"
    assert "selected_route" in payload
    assert "capability_matrix" in payload
    assert isinstance(payload["capability_matrix"], list)
