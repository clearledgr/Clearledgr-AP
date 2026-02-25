import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app
from clearledgr.api import agent_sessions as agent_sessions_module
from clearledgr.api import ops as ops_module
from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData
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
    def _fake_user():
        return TokenData(
            user_id="auth-user-1",
            email="auth@example.com",
            organization_id="default",
            role="owner",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[agent_sessions_module.get_current_user] = _fake_user
    app.dependency_overrides[ops_module.get_current_user] = _fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(agent_sessions_module.get_current_user, None)
        app.dependency_overrides.pop(ops_module.get_current_user, None)


@pytest.fixture()
def unauth_client(db):
    app.dependency_overrides.pop(agent_sessions_module.get_current_user, None)
    app.dependency_overrides.pop(ops_module.get_current_user, None)
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


def _move_item_to_failed_post(db, ap_item_id: str) -> None:
    for state in ("needs_approval", "approved", "ready_to_post", "failed_post"):
        assert db.update_ap_item(
            ap_item_id,
            state=state,
            _actor_type="test",
            _actor_id="test-runner",
        )


def _create_session(client, ap_item_id, metadata=None):
    response = client.post(
        "/api/agent/sessions",
        json={
            "org_id": "default",
            "ap_item_id": ap_item_id,
            "actor_id": "test_agent",
            "metadata": metadata or {},
        },
    )
    assert response.status_code == 200
    return response.json()["session"]["id"]


def test_agent_sessions_endpoint_requires_auth(unauth_client, db):
    item = _create_item(db)
    response = unauth_client.post(
        "/api/agent/sessions",
        json={
            "org_id": "default",
            "ap_item_id": item["id"],
            "actor_id": "spoofed_actor",
        },
    )
    assert response.status_code == 401


def test_browser_fallback_complete_endpoint_requires_auth(unauth_client, db):
    item = _create_item(db)
    _move_item_to_failed_post(db, item["id"])
    session = db.create_agent_session(
        {
            "organization_id": "default",
            "ap_item_id": item["id"],
            "created_by": "runner",
            "metadata": {"workflow_id": "erp_posting_fallback"},
        }
    )
    response = unauth_client.post(
        f"/api/agent/sessions/{session['id']}/complete",
        json={
            "macro_name": "post_invoice_to_erp",
            "status": "success",
            "erp_reference": "ERP-UNAUTH-1",
        },
    )
    assert response.status_code == 401


def test_ops_endpoints_require_auth(unauth_client):
    response = unauth_client.get("/api/ops/browser-agent?organization_id=default")
    assert response.status_code == 401


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


def test_browser_fallback_complete_success_finalizes_ap_item_and_is_idempotent(client, db):
    item = _create_item(db)
    _move_item_to_failed_post(db, item["id"])
    session_id = _create_session(
        client,
        item["id"],
        metadata={"workflow_id": "erp_posting_fallback", "email_id": item["message_id"]},
    )

    first = client.post(
        f"/api/agent/sessions/{session_id}/complete",
        json={
            "macro_name": "post_invoice_to_erp",
            "status": "completed",
            "erp_reference": "ERP-FALLBACK-123",
            "evidence": {"receipt_id": "rcpt-1"},
            "idempotency_key": "fallback-complete-1",
            "correlation_id": "corr-fallback-1",
        },
    )
    assert first.status_code == 200
    completion = first.json()["completion"]
    assert completion["status"] == "success"
    assert completion["duplicate"] is False
    assert completion["ap_item_state"] == "posted_to_erp"
    assert completion["erp_reference"] == "ERP-FALLBACK-123"

    updated_item = db.get_ap_item(item["id"])
    assert updated_item["state"] == "posted_to_erp"
    assert updated_item["erp_reference"] == "ERP-FALLBACK-123"

    session_payload = client.get(f"/api/agent/sessions/{session_id}")
    assert session_payload.status_code == 200
    assert session_payload.json()["session"]["state"] == "completed"

    audits = db.list_ap_audit_events(item["id"])
    assert any(a.get("event_type") == "erp_browser_fallback_completed" for a in audits)

    second = client.post(
        f"/api/agent/sessions/{session_id}/complete",
        json={
            "macro_name": "post_invoice_to_erp",
            "status": "success",
            "erp_reference": "ERP-FALLBACK-123",
            "idempotency_key": "fallback-complete-1",
        },
    )
    assert second.status_code == 200
    second_completion = second.json()["completion"]
    assert second_completion["duplicate"] is True
    assert second_completion["ap_item_state"] == "posted_to_erp"


def test_browser_fallback_complete_failure_keeps_failed_post_and_audits(client, db):
    item = _create_item(db)
    _move_item_to_failed_post(db, item["id"])
    session_id = _create_session(
        client,
        item["id"],
        metadata={"workflow_id": "erp_posting_fallback"},
    )

    response = client.post(
        f"/api/agent/sessions/{session_id}/complete",
        json={
            "macro_name": "post_invoice_to_erp",
            "status": "failed",
            "error_code": "erp_ui_timeout",
            "error_message_redacted": "Timed out while posting bill",
            "idempotency_key": "fallback-complete-fail-1",
        },
    )
    assert response.status_code == 200
    completion = response.json()["completion"]
    assert completion["status"] == "failed"
    assert completion["ap_item_state"] == "failed_post"

    updated_item = db.get_ap_item(item["id"])
    assert updated_item["state"] == "failed_post"
    assert "Timed out" in str(updated_item.get("last_error") or "")

    audits = db.list_ap_audit_events(item["id"])
    assert any(a.get("event_type") == "erp_browser_fallback_failed" for a in audits)

def test_submit_result_uses_authenticated_actor_identity(client, db):
    item = _create_item(db)
    session_id = _create_session(client, item["id"])
    enqueue = client.post(
        f"/api/agent/sessions/{session_id}/commands",
        json={
            "actor_id": "spoofed_request_actor",
            "tool_name": "read_page",
            "command_id": "cmd-auth-bind-1",
            "target": {"url": "https://mail.google.com/mail/u/0/#inbox"},
        },
    )
    assert enqueue.status_code == 200

    result = client.post(
        f"/api/agent/sessions/{session_id}/results",
        json={
            "actor_id": "spoofed_runner",
            "command_id": "cmd-auth-bind-1",
            "status": "completed",
            "result_payload": {"ok": True},
        },
    )
    assert result.status_code == 200

    audits = db.list_ap_audit_events(item["id"])
    result_events = [a for a in audits if a.get("event_type") == "browser_command_result"]
    assert result_events
    assert result_events[-1]["actor_id"] == "auth-user-1"


def test_runner_trust_policy_denies_low_privileged_result_callback_and_audits(client, db, monkeypatch):
    monkeypatch.setenv("AP_BROWSER_RUNNER_TRUST_MODE", "api_or_admin")
    item = _create_item(db)
    session_id = _create_session(client, item["id"])
    enqueue = client.post(
        f"/api/agent/sessions/{session_id}/commands",
        json={
            "actor_id": "test_agent",
            "tool_name": "read_page",
            "command_id": "cmd-runner-policy-1",
            "target": {"url": "https://mail.google.com/mail/u/0/#inbox"},
        },
    )
    assert enqueue.status_code == 200

    def _low_priv_user():
        return TokenData(
            user_id="normal-user-1",
            email="user@example.com",
            organization_id="default",
            role="user",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[agent_sessions_module.get_current_user] = _low_priv_user
    try:
        low_client = TestClient(app)
        denied = low_client.post(
            f"/api/agent/sessions/{session_id}/results",
            json={
                "actor_id": "runner",
                "command_id": "cmd-runner-policy-1",
                "status": "completed",
                "result_payload": {"ok": True},
            },
        )
    finally:
        app.dependency_overrides[agent_sessions_module.get_current_user] = lambda: TokenData(
            user_id="auth-user-1",
            email="auth@example.com",
            organization_id="default",
            role="owner",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        app.dependency_overrides.pop(agent_sessions_module.get_current_user, None)

    assert denied.status_code == 403
    assert denied.json()["detail"] == "runner_trust_policy_denied"
    audits = db.list_ap_audit_events(item["id"])
    unauthorized = [a for a in audits if a.get("event_type") == "runner_callback_unauthorized"]
    assert unauthorized
    assert unauthorized[-1]["source"] == "agent_runner"
    payload = unauthorized[-1].get("payload_json") or {}
    assert (
        unauthorized[-1].get("decision_reason") == "runner_trust_policy_denied"
        or payload.get("reason") == "runner_trust_policy_denied"
    )


def test_runner_trust_policy_denies_low_privileged_fallback_complete_and_audits(client, db, monkeypatch):
    monkeypatch.setenv("AP_BROWSER_RUNNER_TRUST_MODE", "api_or_admin")
    item = _create_item(db)
    _move_item_to_failed_post(db, item["id"])
    session = db.create_agent_session(
        {
            "organization_id": "default",
            "ap_item_id": item["id"],
            "created_by": "runner",
            "metadata": {"workflow_id": "erp_posting_fallback"},
        }
    )
    session_id = session["id"]

    def _low_priv_user():
        return TokenData(
            user_id="normal-user-2",
            email="user2@example.com",
            organization_id="default",
            role="user",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[agent_sessions_module.get_current_user] = _low_priv_user
    try:
        low_client = TestClient(app)
        denied = low_client.post(
            f"/api/agent/sessions/{session_id}/complete",
            json={
                "macro_name": "post_invoice_to_erp",
                "status": "success",
                "erp_reference": "ERP-SHOULD-NOT-POST",
            },
        )
    finally:
        app.dependency_overrides.pop(agent_sessions_module.get_current_user, None)

    assert denied.status_code == 403
    assert denied.json()["detail"] == "runner_trust_policy_denied"
    audits = db.list_ap_audit_events(item["id"])
    unauthorized = [a for a in audits if a.get("event_type") == "runner_callback_unauthorized"]
    assert unauthorized
    assert any((a.get("payload_json") or {}).get("endpoint") == "complete" for a in unauthorized)


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


def test_preview_includes_session_context_snapshot(client, db):
    item = _create_item(db)
    session_id = _create_session(
        client,
        item["id"],
        metadata={
            "context_snapshot": {
                "source_count": 3,
                "budget_status": "critical",
                "has_context_conflict": True,
            }
        },
    )
    response = client.post(
        f"/api/agent/sessions/{session_id}/commands/preview",
        json={
            "actor_id": "test_agent",
            "tool_name": "click",
            "command_id": "cmd-preview-context-1",
            "target": {"url": "https://mail.google.com/mail/u/0/#inbox"},
            "params": {"selector": "button[aria-label='Archive']"},
        },
    )
    assert response.status_code == 200
    payload = response.json()["preview"]
    assert payload["context_snapshot"]["source_count"] == 3
    assert "linked sources" in payload["summary"]
    assert any("context conflict" in warning for warning in payload["warnings"])


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


def test_autopilot_status_includes_agent_runtime_truth_claims(client, monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("AP_AGENT_AUTONOMOUS_RETRY_ENABLED", "true")
    monkeypatch.setenv("AP_AGENT_NON_DURABLE_RETRY_ALLOWED", "true")
    monkeypatch.setenv("AP_AGENT_RETRY_BACKOFF_SECONDS", "0,5,10")
    monkeypatch.setenv("AP_AGENT_RETRY_POLL_SECONDS", "2")

    response = client.get("/api/ops/autopilot-status")
    assert response.status_code == 200
    payload = response.json()["autopilot"]

    assert "agent_runtime" in payload
    retry = payload["agent_runtime"]["autonomous_retry"]
    assert retry["mode"] == "durable_db_retry_queue"
    assert retry["durable"] is True
    assert retry["enabled"] is True
    assert retry["allow_non_durable"] is True
    assert retry["backoff_seconds"] == [0, 5, 10]
    assert retry["poll_interval_seconds"] == 2


def test_autopilot_status_keeps_durable_retry_enabled_in_production(client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AP_AGENT_AUTONOMOUS_RETRY_ENABLED", "true")
    monkeypatch.setenv("AP_AGENT_NON_DURABLE_RETRY_ALLOWED", "false")

    response = client.get("/api/ops/autopilot-status")
    assert response.status_code == 200
    retry = response.json()["autopilot"]["agent_runtime"]["autonomous_retry"]

    assert retry["enabled"] is True
    assert retry["durable"] is True
    assert retry["allow_non_durable"] is False
    assert retry["reason"] is None
