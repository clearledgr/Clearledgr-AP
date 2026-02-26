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


def test_ap_kpis_exposes_agentic_telemetry_bundle(client, db):
    now = datetime.now(timezone.utc)

    posted_touchless = db.create_ap_item(
        {
            "invoice_key": "vendor|touchless|100.00|",
            "thread_id": "thread-touchless",
            "message_id": "msg-touchless",
            "subject": "Touchless invoice",
            "sender": "vendor1@example.com",
            "vendor_name": "Touchless Vendor",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": "INV-TL-1",
            "state": "posted_to_erp",
            "confidence": 0.99,
            "approval_required": False,
            "erp_reference": "ERP-TL-1",
            "erp_posted_at": (now - timedelta(hours=2)).isoformat(),
            "organization_id": "default",
            "user_id": "user-agent",
        }
    )

    posted_with_approval = db.create_ap_item(
        {
            "invoice_key": "vendor|approval|250.00|",
            "thread_id": "thread-approval",
            "message_id": "msg-approval",
            "subject": "Approval invoice",
            "sender": "vendor2@example.com",
            "vendor_name": "Approval Vendor",
            "amount": 250.0,
            "currency": "USD",
            "invoice_number": "INV-AP-1",
            "state": "posted_to_erp",
            "confidence": 0.97,
            "approval_required": True,
            "erp_reference": "ERP-AP-1",
            "erp_posted_at": (now - timedelta(hours=1)).isoformat(),
            "organization_id": "default",
            "user_id": "user-agent",
            "metadata": {
                "validation_gate": {"reason_codes": ["policy_po_missing"]},
            },
        }
    )
    db.save_approval(
        {
            "ap_item_id": posted_with_approval["id"],
            "channel_id": "C-ops-kpi",
            "message_ts": "1740566400.000100",
            "source_channel": "slack",
            "source_message_ref": "slack:ops-kpi",
            "organization_id": "default",
            "status": "approved",
            "approved_by": "manager@example.com",
            "created_at": (now - timedelta(hours=6)).isoformat(),
            "approved_at": (now - timedelta(hours=4)).isoformat(),
            "decision_payload": {
                "decision": "approve_override",
                "budget_override": True,
                "override_justification": "Quarter-close exception",
            },
        }
    )

    blocked_open_item = db.create_ap_item(
        {
            "invoice_key": "vendor|blocked|500.00|",
            "thread_id": "thread-blocked",
            "message_id": "msg-blocked",
            "subject": "Blocked invoice",
            "sender": "vendor3@example.com",
            "vendor_name": "Blocked Vendor",
            "amount": 500.0,
            "currency": "USD",
            "invoice_number": "INV-BLK-1",
            "state": "failed_post",
            "confidence": 0.82,
            "approval_required": True,
            "last_error": "ERP timeout while posting",
            "organization_id": "default",
            "user_id": "user-agent",
            "metadata": {
                "requires_field_review": True,
                "confidence_blockers": [{"field": "amount"}],
                "validation_gate": {"reason_codes": ["policy_po_missing"]},
                "budget_status": "critical",
                "exception_code": "erp_timeout",
            },
        }
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": blocked_open_item["id"],
            "organization_id": "default",
            "event_type": "erp_api_attempt",
            "actor_type": "system",
            "actor_id": "test",
        }
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": blocked_open_item["id"],
            "organization_id": "default",
            "event_type": "erp_api_fallback_requested",
            "actor_type": "system",
            "actor_id": "test",
        }
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": blocked_open_item["id"],
            "organization_id": "default",
            "event_type": "browser_command_confirmed",
            "actor_type": "user",
            "actor_id": "approver@example.com",
        }
    )

    session = db.create_agent_session(
        {
            "organization_id": "default",
            "ap_item_id": blocked_open_item["id"],
            "created_by": "test_agent",
            "metadata": {"workflow_id": "erp_posting_fallback"},
        }
    )
    db.upsert_browser_action_event(
        {
            "organization_id": "default",
            "ap_item_id": blocked_open_item["id"],
            "session_id": session["id"],
            "command_id": "cmd-ops-kpi-1",
            "tool_name": "click",
            "status": "completed",
            "requires_confirmation": True,
            "request_payload": {"tool_risk": "high_risk"},
            "result_payload": {"ok": True},
        }
    )

    response = client.get("/api/ops/ap-kpis?organization_id=default")
    assert response.status_code == 200
    payload = response.json()
    assert "kpis" in payload
    kpis = payload["kpis"]
    telemetry = kpis.get("agentic_telemetry") or {}

    assert telemetry
    assert telemetry["straight_through_rate"]["eligible_count"] >= 2
    assert telemetry["straight_through_rate"]["count"] >= 1
    assert telemetry["human_intervention_rate"]["count"] >= 1
    assert telemetry["awaiting_approval_time_hours"]["avg"] >= 0
    assert telemetry["erp_browser_fallback_rate"]["attempt_count"] >= 1
    assert telemetry["erp_browser_fallback_rate"]["fallback_requested_count"] >= 1
    assert telemetry["agent_suggestion_acceptance"]["accepted_count"] >= 1
    assert telemetry["agent_actions_requiring_manual_override"]["count"] >= 1
    assert telemetry["approval_override_rate"]["override_count"] >= 1

    top_reasons = telemetry["top_blocker_reasons"]["top_reasons"]
    assert isinstance(top_reasons, list)
    assert top_reasons
    assert any(
        str(entry.get("reason", "")).startswith(("confidence:", "policy:", "budget:", "erp:"))
        for entry in top_reasons
    )


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


def test_browser_fallback_full_e2e_api_fail_to_posted_to_erp(client, db):
    """Full E2E: API fail → fallback dispatch → macro preview → confirmation capture
    → live execution → posted_to_erp state transition → audit reconciliation.

    Covers Gap #16 (PLAN.md §8.4) browser fallback E2E test coverage requirement.

    Flow:
      1. Item moves through validated → failed_post (simulates API-first ERP failure)
      2. Browser agent session created with erp_posting_fallback workflow
      3. Macro preview (dry_run=True) confirms confirmation-required steps exist
      4. Macro dispatched (dry_run=False) → commands are queued or blocked
      5. Blocked commands resubmitted with confirm=True (confirmation capture)
      6. Results submitted for all queued commands (live execution)
      7. Session complete endpoint called → AP item transitions to posted_to_erp
      8. Audit trail verified: fallback event + browser events + correlation ID
    """
    # ── 1. API-first ERP posting failure → failed_post ─────────────────────
    item = _create_item(db)
    correlation_id = f"e2e-g16-{item['id'][:8]}"
    _move_item_to_failed_post(db, item["id"])
    assert db.get_ap_item(item["id"])["state"] == "failed_post"

    # ── 2. Browser agent fallback session ───────────────────────────────────
    session_id = _create_session(
        client,
        item["id"],
        metadata={
            "workflow_id": "erp_posting_fallback",
            "email_id": item["message_id"],
            "correlation_id": correlation_id,
        },
    )
    assert session_id

    # ── 3. Macro preview (dry_run=True) ─────────────────────────────────────
    preview = client.post(
        f"/api/agent/sessions/{session_id}/macros/post_invoice_to_erp",
        json={"actor_id": "test_agent", "dry_run": True},
    )
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["status"] == "preview"
    preview_commands = preview_payload["commands"]
    assert len(preview_commands) >= 1, "Macro must generate at least one command"
    # Each preview entry is a preview_command() result — tool_name lives inside
    # the nested "command" dict; requires_confirmation is under "decision".
    needs_confirm = [
        c for c in preview_commands
        if (
            c.get("decision", {}).get("requires_confirmation")
            or c.get("command", {}).get("tool_name") in {"click", "open_tab", "fill_form", "submit"}
        )
    ]
    assert needs_confirm, (
        f"Expected at least one confirmation-required step in macro preview; "
        f"got tools: {[c.get('command', {}).get('tool_name') for c in preview_commands]}"
    )

    # ── 4. Live macro dispatch → queued + blocked commands ─────────────────
    dispatch = client.post(
        f"/api/agent/sessions/{session_id}/macros/post_invoice_to_erp",
        json={"actor_id": "test_agent", "dry_run": False},
    )
    assert dispatch.status_code == 200
    dispatch_body = dispatch.json()
    assert dispatch_body["status"] == "dispatched"
    total = dispatch_body["queued"] + dispatch_body["blocked"] + dispatch_body["denied"]
    assert total >= 1, f"Expected at least one command to be dispatched: {dispatch_body}"

    # ── 5. Confirmation capture — resubmit blocked commands with confirm=True
    session_detail = client.get(f"/api/agent/sessions/{session_id}").json()
    blocked_events = [
        e for e in session_detail["events"]
        if e.get("status") == "blocked_for_approval"
    ]
    for blocked in blocked_events:
        req = blocked.get("request_payload") or {}
        confirmed = client.post(
            f"/api/agent/sessions/{session_id}/commands",
            json={
                "actor_id": "test_agent",
                "tool_name": blocked["tool_name"],
                "command_id": blocked["command_id"],
                "target": req.get("target") or {"url": "https://mail.google.com/mail/u/0/#inbox"},
                "params": req.get("params") or {},
                "confirm": True,
                "confirmed_by": "auth-user-1",
            },
        )
        assert confirmed.status_code == 200, (
            f"Confirmation failed for cmd {blocked['command_id']}: {confirmed.json()}"
        )
        assert confirmed.json()["event"]["status"] == "queued", (
            f"Expected queued after confirmation; got: {confirmed.json()['event']['status']}"
        )

    # ── 6. Submit results for queued commands (live execution) ──────────────
    session_detail = client.get(f"/api/agent/sessions/{session_id}").json()
    queued_events = [e for e in session_detail["events"] if e.get("status") == "queued"]
    for queued in queued_events:
        result_resp = client.post(
            f"/api/agent/sessions/{session_id}/results",
            json={
                "actor_id": "runner",
                "command_id": queued["command_id"],
                "status": "completed",
                "result_payload": {
                    "ok": True,
                    "url": "https://mail.google.com/mail/u/0/#inbox",
                    "correlation_id": correlation_id,
                },
            },
        )
        assert result_resp.status_code == 200, (
            f"Result submission failed for cmd {queued['command_id']}: {result_resp.json()}"
        )

    # ── 7. Complete session → posted_to_erp state transition ────────────────
    erp_ref = "ERP-E2E-G16-001"
    complete_resp = client.post(
        f"/api/agent/sessions/{session_id}/complete",
        json={
            "macro_name": "post_invoice_to_erp",
            "status": "completed",
            "erp_reference": erp_ref,
            "evidence": {"screenshot_hash": "sha256-e2e-placeholder"},
            "idempotency_key": f"e2e-g16-{item['id']}",
            "correlation_id": correlation_id,
        },
    )
    assert complete_resp.status_code == 200
    completion = complete_resp.json()["completion"]
    assert completion["status"] == "success"
    assert completion["ap_item_state"] == "posted_to_erp"
    assert completion["erp_reference"] == erp_ref
    assert completion["duplicate"] is False

    # ── 8. State transition: AP item is posted_to_erp ───────────────────────
    final_item = db.get_ap_item(item["id"])
    assert final_item["state"] == "posted_to_erp"
    assert final_item["erp_reference"] == erp_ref

    # ── 9. Audit reconciliation: full event chain + correlation ID ───────────
    audits = db.list_ap_audit_events(item["id"])
    event_types = {a.get("event_type") for a in audits}

    assert "erp_browser_fallback_completed" in event_types, (
        f"Missing erp_browser_fallback_completed in audit trail; "
        f"present event types: {event_types}"
    )
    # Browser command events must be present (from macro dispatch + results)
    assert event_types & {"browser_command_enqueued", "browser_command_result"}, (
        f"Expected browser command audit events; present: {event_types}"
    )

    # Correlation ID must be propagated to the fallback completion event
    fallback_events = [
        a for a in audits
        if a.get("event_type") == "erp_browser_fallback_completed"
    ]
    assert fallback_events
    fb_event = fallback_events[-1]
    fb_payload = fb_event.get("payload_json") or {}
    corr_in_event = fb_event.get("correlation_id") == correlation_id
    corr_in_payload = fb_payload.get("correlation_id") == correlation_id
    assert corr_in_event or corr_in_payload, (
        f"Correlation ID '{correlation_id}' not found in fallback completion event. "
        f"event correlation_id='{fb_event.get('correlation_id')}', "
        f"payload correlation_id='{fb_payload.get('correlation_id')}'"
    )
