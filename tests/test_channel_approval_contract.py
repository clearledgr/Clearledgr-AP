"""Slack/Teams handler-path tests for canonical approval action contract.

These tests exercise transport callback handlers (verification, normalization,
stale/duplicate behavior, and workflow dispatch kwargs) to complement the
DB-direct and service-level tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
import urllib.parse
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app
from clearledgr.core import database as db_module


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "channel-approval.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


@pytest.fixture()
def client(db):
    return TestClient(app)


def _create_ap_item(db, *, gmail_id: str) -> dict:
    return db.create_ap_item(
        {
            "invoice_key": f"inv-{gmail_id}",
            "thread_id": gmail_id,
            "message_id": f"msg-{gmail_id}",
            "subject": f"Invoice {gmail_id}",
            "sender": "billing@example.com",
            "vendor_name": "Vendor Test",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": f"INV-{gmail_id}",
            "state": "needs_approval",
            "confidence": 0.99,
            "organization_id": "default",
            "metadata": {},
        }
    )


def _slack_form_body(payload: dict) -> bytes:
    encoded = urllib.parse.urlencode({"payload": json.dumps(payload)})
    return encoded.encode("utf-8")


class _WorkflowStub:
    def __init__(self):
        self.calls = []

    async def approve_invoice(self, **kwargs):
        self.calls.append(("approve_invoice", kwargs))
        return {"status": "approved", "erp_result": {"bill_id": "BILL-1"}}

    async def request_budget_adjustment(self, **kwargs):
        self.calls.append(("request_budget_adjustment", kwargs))
        return {"status": "needs_info"}

    async def reject_invoice(self, **kwargs):
        self.calls.append(("reject_invoice", kwargs))
        return {"status": "rejected"}


def test_slack_and_teams_card_builders_include_request_info_action():
    from clearledgr.services.slack_api import SlackAPIClient
    from clearledgr.services.teams_api import TeamsAPIClient

    slack_blocks = SlackAPIClient.build_approval_blocks(
        title="Invoice Approval",
        details={"Vendor": "Acme", "Amount": "USD 100.00"},
        approve_action_id="approve_invoice",
        reject_action_id="reject_invoice",
        item_id="thread-123",
    )
    slack_actions = next(block for block in slack_blocks if block.get("type") == "actions")
    slack_action_ids = [el.get("action_id") for el in (slack_actions.get("elements") or [])]
    assert any(str(action_id).startswith("request_info_") for action_id in slack_action_ids)

    teams_card = TeamsAPIClient.build_invoice_budget_card(
        email_id="thread-123",
        organization_id="default",
        vendor="Acme",
        amount=100.0,
        currency="USD",
        invoice_number="INV-123",
        budget={"status": "healthy", "requires_decision": False, "checks": []},
        decision_reason_summary="Approval is required before posting to ERP.",
        next_step_lines=[
            "Approve / Post to ERP: the AP workflow attempts ERP posting automatically.",
            "Request info: returns the invoice to needs-info.",
            "Reject: records the rejection.",
        ],
    )
    teams_content = teams_card["attachments"][0]["content"]
    actions = teams_card["attachments"][0]["content"]["actions"]
    action_names = [a.get("data", {}).get("action") for a in actions]
    assert "request_info" in action_names
    assert any(
        a.get("type") == "Action.OpenUrl" and "mail.google.com" in str(a.get("url", "")).lower()
        for a in actions
    )
    body_text = " ".join(str(block.get("text") or "") for block in (teams_content.get("body") or []) if isinstance(block, dict))
    assert "Why this needs your decision" in body_text
    assert "What happens next" in body_text
    assert "Requested by Clearledgr AP Agent" in body_text
    assert "Source of truth" in body_text

    teams_budget_card = TeamsAPIClient.build_invoice_budget_card(
        email_id="thread-123",
        organization_id="default",
        vendor="Acme",
        amount=100.0,
        currency="USD",
        invoice_number="INV-123",
        budget={"status": "critical", "requires_decision": True, "checks": []},
    )
    budget_actions = teams_budget_card["attachments"][0]["content"]["actions"]
    budget_action_names = [a.get("data", {}).get("action") for a in budget_actions]
    assert "request_info" in budget_action_names


def test_invoice_workflow_slack_blocks_include_request_info_for_standard_and_budget_paths(monkeypatch, db):
    from clearledgr.services.invoice_workflow import InvoiceData, InvoiceWorkflowService

    svc = InvoiceWorkflowService(organization_id="default")
    svc.db = db

    invoice = InvoiceData(
        gmail_id="thread-card-1",
        subject="Invoice",
        sender="billing@example.com",
        vendor_name="Acme",
        amount=100.0,
        currency="USD",
        invoice_number="INV-123",
        due_date="2026-03-01",
        confidence=0.98,
    )

    standard_blocks = svc._build_approval_blocks(invoice, extra_context={"budget": {"status": "healthy", "requires_decision": False}})
    standard_actions = next(block for block in standard_blocks if block.get("type") == "actions")
    standard_ids = [el.get("action_id") for el in (standard_actions.get("elements") or []) if isinstance(el, dict)]
    assert any(str(action_id).startswith("request_info_") for action_id in standard_ids)
    standard_text = " ".join(
        str(block.get("text", {}).get("text") or "")
        for block in standard_blocks
        if isinstance(block, dict) and isinstance(block.get("text"), dict)
    )
    standard_context_text = " ".join(
        str(el.get("text") or "")
        for block in standard_blocks
        if isinstance(block, dict) and block.get("type") == "context"
        for el in (block.get("elements") or [])
        if isinstance(el, dict)
    )
    assert "Why this needs your decision" in standard_text
    assert "Recommended now" in standard_text
    assert "What happens next" in standard_text
    assert "Requested by Clearledgr AP Agent" in standard_context_text
    assert "Source of truth" in standard_context_text

    budget_blocks = svc._build_approval_blocks(
        invoice,
        extra_context={
            "budget": {"status": "critical", "requires_decision": True},
            "budget_impact": [
                {
                    "name": "Marketing",
                    "after_approval_status": "critical",
                    "after_approval_percent": 93,
                }
            ],
        },
    )
    budget_actions = next(block for block in budget_blocks if block.get("type") == "actions")
    budget_ids = [el.get("action_id") for el in (budget_actions.get("elements") or []) if isinstance(el, dict)]
    assert any(str(action_id).startswith("request_info_") for action_id in budget_ids)
    budget_text = " ".join(
        str(block.get("text", {}).get("text") or "")
        for block in budget_blocks
        if isinstance(block, dict) and isinstance(block.get("text"), dict)
    )
    assert "Budget check is critical" in budget_text or "Budget check requires" in budget_text
    assert "Recommended now" in budget_text


def test_approval_surface_copy_tunes_what_happens_next_for_confidence_validation_and_duplicate(db):
    from clearledgr.services.invoice_workflow import InvoiceData, InvoiceWorkflowService

    svc = InvoiceWorkflowService(organization_id="default")
    svc.db = db
    invoice = InvoiceData(
        gmail_id="thread-copy-1",
        subject="Invoice review needed",
        sender="billing@example.com",
        vendor_name="Acme",
        amount=420.0,
        currency="USD",
        invoice_number="INV-COPY-1",
        due_date="2026-03-05",
        confidence=0.81,
        potential_duplicates=2,
    )

    copy_payload = svc._build_approval_surface_copy(
        invoice=invoice,
        extra_context={
            "confidence_gate": {
                "requires_field_review": True,
                "blockers": [{"field": "amount"}],
            },
            "validation_gate": {
                "reason_codes": ["policy_po_missing"],
                "reasons": [{"code": "policy_po_missing", "message": "PO reference missing for this invoice."}],
            },
        },
        budget_summary={"status": "healthy", "requires_decision": False},
    )

    next_lines = [str(line).lower() for line in (copy_payload.get("what_happens_next") or [])]
    recommended = str(copy_payload.get("recommended_action_text") or "").lower()
    assert next_lines
    assert "confidence override" in next_lines[0]
    assert "missing policy/evidence" in next_lines[1]
    assert "duplicate risk is confirmed" in next_lines[2]
    assert "request info first" in recommended


def test_approval_surface_copy_tunes_budget_hard_block_next_steps(db):
    from clearledgr.services.invoice_workflow import InvoiceData, InvoiceWorkflowService

    svc = InvoiceWorkflowService(organization_id="default")
    svc.db = db
    invoice = InvoiceData(
        gmail_id="thread-copy-2",
        subject="Budget blocked invoice",
        sender="billing@example.com",
        vendor_name="BudgetCo",
        amount=2000.0,
        currency="USD",
        invoice_number="INV-COPY-2",
        due_date="2026-03-12",
        confidence=0.96,
    )

    copy_payload = svc._build_approval_surface_copy(
        invoice=invoice,
        extra_context={
            "validation_gate": {
                "reason_codes": ["policy_budget_limit"],
                "reasons": [{"code": "policy_budget_limit", "message": "Budget threshold exceeded."}],
            }
        },
        budget_summary={"status": "exceeded", "requires_decision": True, "hard_block": True},
    )
    next_lines = [str(line).lower() for line in (copy_payload.get("what_happens_next") or [])]
    recommended = str(copy_payload.get("recommended_action_text") or "").lower()
    assert next_lines
    assert "hard-budget-block justification" in next_lines[0]
    assert "budget or policy clarification" in next_lines[1]
    assert "request budget adjustment" in recommended


def test_approval_surface_copy_uses_po_and_vendor_queue_context_in_why_summary(db):
    from clearledgr.services.invoice_workflow import InvoiceData, InvoiceWorkflowService

    svc = InvoiceWorkflowService(organization_id="default")
    svc.db = db
    invoice = InvoiceData(
        gmail_id="thread-copy-3",
        subject="PO exception invoice",
        sender="billing@example.com",
        vendor_name="QueueVendor",
        amount=800.0,
        currency="USD",
        invoice_number="INV-COPY-3",
        due_date="2026-03-15",
        confidence=0.97,
    )

    copy_payload = svc._build_approval_surface_copy(
        invoice=invoice,
        extra_context={
            "po_match_result": {
                "exceptions": [{"type": "price_mismatch", "severity": "high"}],
            },
            "approval_context": {
                "vendor_open_invoices": 4,
            },
        },
        budget_summary={"status": "healthy", "requires_decision": False},
    )
    why = str(copy_payload.get("why_summary") or "").lower()
    assert "po/receipt exception detected" in why


def test_slack_interactive_rejects_invalid_signature_and_audits(monkeypatch, client, db):
    captured = []
    original_append = db.append_ap_audit_event

    def _spy_append(payload):
        captured.append(dict(payload))
        return original_append(payload)

    monkeypatch.setattr(db, "append_ap_audit_event", _spy_append)

    async def _raise_invalid(_request):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    monkeypatch.setattr("clearledgr.api.slack_invoices.require_slack_signature", _raise_invalid)
    payload = {
        "user": {"id": "U1", "username": "approver"},
        "channel": {"id": "C1"},
        "message": {"ts": "1700000000.123"},
        "actions": [{"action_id": "approve_invoice_thread-slack-unauth", "value": "thread-slack-unauth"}],
    }
    body = _slack_form_body(payload)

    response = client.post(
        "/slack/invoices/interactive",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 401
    assert any(evt.get("event_type") == "channel_callback_unauthorized" for evt in captured)
    assert any(str(evt.get("idempotency_key") or "").startswith("slack:unauthorized:") for evt in captured)


def test_slack_interactive_invalid_payload_audits(monkeypatch, client, db):
    captured = []
    original_append = db.append_ap_audit_event

    def _spy_append(payload):
        captured.append(dict(payload))
        return original_append(payload)

    monkeypatch.setattr(db, "append_ap_audit_event", _spy_append)

    async def _return_body(request):
        return await request.body()

    monkeypatch.setattr("clearledgr.api.slack_invoices.require_slack_signature", _return_body)

    malformed_body = b"payload=%7Bnot-json"
    response = client.post(
        "/slack/invoices/interactive",
        content=malformed_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_payload"
    assert any(evt.get("event_type") == "channel_action_invalid" for evt in captured)
    invalid_keys = [
        str(evt.get("idempotency_key") or "")
        for evt in captured
        if evt.get("event_type") == "channel_action_invalid"
    ]
    assert any(key.startswith("slack:invalid:") for key in invalid_keys)
    persisted = next((db.get_ap_audit_event_by_key(key) for key in invalid_keys if key.startswith("slack:invalid:")), None)
    assert persisted is not None
    assert persisted.get("event_type") == "channel_action_invalid"


def test_slack_interactive_request_info_duplicate_and_stale(monkeypatch, client, db):
    item = _create_ap_item(db, gmail_id="thread-slack-1")
    db.update_ap_item(item["id"], metadata={"correlation_id": "corr-slack-1"})
    workflow = _WorkflowStub()

    async def _return_body(request):
        return await request.body()

    monkeypatch.setattr("clearledgr.api.slack_invoices.require_slack_signature", _return_body)
    monkeypatch.setattr("clearledgr.api.slack_invoices.get_invoice_workflow", lambda _org: workflow)

    payload = {
        "callback_id": "run-slack-1",
        "user": {"id": "U1", "username": "approver"},
        "channel": {"id": "C1"},
        "message": {"ts": "1711111111.000"},
        "actions": [{"action_id": "request_info_thread-slack-1", "value": "thread-slack-1"}],
    }
    body = _slack_form_body(payload)
    now_ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "x-slack-request-timestamp": now_ts,
    }

    first = client.post("/slack/invoices/interactive", content=body, headers=headers)
    assert first.status_code == 200
    assert "Request for info recorded" in first.json()["text"]

    second = client.post("/slack/invoices/interactive", content=body, headers=headers)
    assert second.status_code == 200
    assert "Duplicate action ignored" in second.json()["text"]

    assert [name for name, _kwargs in workflow.calls] == ["request_budget_adjustment"]
    call_kwargs = workflow.calls[0][1]
    assert call_kwargs["reason"] == "budget_adjustment_requested_in_slack"
    assert call_kwargs["source_channel"] == "slack"
    assert call_kwargs["decision_idempotency_key"]
    assert call_kwargs["correlation_id"] == "corr-slack-1"

    events = db.list_ap_audit_events(item["id"])
    event_types = [e.get("event_type") for e in events]
    assert "channel_action_received" in event_types
    assert "channel_action_processed" in event_types
    assert "channel_action_duplicate" in event_types
    correlated_events = [
        e for e in events
        if e.get("event_type") in {"channel_action_received", "channel_action_processed", "channel_action_duplicate"}
    ]
    assert correlated_events
    assert all(e.get("correlation_id") == "corr-slack-1" for e in correlated_events)

    # Stale callback (same action, older request timestamp) returns explicit stale response.
    stale_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "x-slack-request-timestamp": str(int(time.time()) - 90000),
    }
    stale = client.post("/slack/invoices/interactive", content=body, headers=stale_headers)
    assert stale.status_code == 200
    assert "stale/expired" in stale.json()["text"]


def test_teams_interactive_requires_authorization_and_audits(monkeypatch, client, db):
    captured = []
    original_append = db.append_ap_audit_event

    def _spy_append(payload):
        captured.append(dict(payload))
        return original_append(payload)

    monkeypatch.setattr(db, "append_ap_audit_event", _spy_append)

    payload = {
        "action": "approve_invoice",
        "email_id": "thread-teams-unauth",
        "organization_id": "default",
    }
    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/teams/invoices/interactive",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert any(evt.get("event_type") == "channel_callback_unauthorized" for evt in captured)
    assert any(str(evt.get("idempotency_key") or "").startswith("teams:unauthorized:") for evt in captured)


def test_teams_interactive_invalid_payload_audits(monkeypatch, client, db):
    captured = []
    original_append = db.append_ap_audit_event

    def _spy_append(payload):
        captured.append(dict(payload))
        return original_append(payload)

    monkeypatch.setattr(db, "append_ap_audit_event", _spy_append)
    monkeypatch.setattr(
        "clearledgr.api.teams_invoices.verify_teams_token",
        lambda _auth: {"appid": "bot-test", "iat": int(time.time())},
    )

    response = client.post(
        "/teams/invoices/interactive",
        content=b"{",
        headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_payload"
    assert any(evt.get("event_type") == "channel_action_invalid" for evt in captured)
    invalid_keys = [
        str(evt.get("idempotency_key") or "")
        for evt in captured
        if evt.get("event_type") == "channel_action_invalid"
    ]
    assert any(key.startswith("teams:invalid:") for key in invalid_keys)
    persisted = next((db.get_ap_audit_event_by_key(key) for key in invalid_keys if key.startswith("teams:invalid:")), None)
    assert persisted is not None
    assert persisted.get("event_type") == "channel_action_invalid"


def test_teams_interactive_common_contract_request_info_duplicate_invalid_and_stale(monkeypatch, client, db):
    item = _create_ap_item(db, gmail_id="thread-teams-1")
    workflow = _WorkflowStub()
    monkeypatch.setattr(
        "clearledgr.api.teams_invoices.verify_teams_token",
        lambda _auth: {"appid": "bot-test", "iat": int(time.time())},
    )
    monkeypatch.setattr("clearledgr.api.teams_invoices.get_invoice_workflow", lambda _org: workflow)

    headers = {"Authorization": "Bearer test-token"}
    payload = {
        "action": "request_info",
        "email_id": "thread-teams-1",
        "organization_id": "default",
        "actor": "approver@clearledgr.com",
        "conversation_id": "19:finance",
        "message_id": "msg-001",
        "request_ts": str(int(time.time())),
    }

    first = client.post("/teams/invoices/interactive", json=payload, headers=headers)
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["status"] == "needs_info"
    assert first_body["action"] == "request_info"

    second = client.post("/teams/invoices/interactive", json=payload, headers=headers)
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    assert [name for name, _kwargs in workflow.calls] == ["request_budget_adjustment"]
    kwargs = workflow.calls[0][1]
    assert kwargs["reason"] == "budget_adjustment_requested_in_teams"
    assert kwargs["source_channel"] == "teams"
    assert kwargs["decision_idempotency_key"]

    invalid_payload = {
        **payload,
        "action": "flag_invoice",
        "message_id": "msg-002",
    }
    invalid = client.post("/teams/invoices/interactive", json=invalid_payload, headers=headers)
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "unsupported_action"

    stale_payload = {
        **payload,
        "message_id": "msg-003",
        "request_ts": str(int(time.time()) - 90000),
    }
    stale = client.post("/teams/invoices/interactive", json=stale_payload, headers=headers)
    assert stale.status_code == 200
    assert stale.json()["status"] == "stale"

    events = db.list_ap_audit_events(item["id"])
    event_types = [e.get("event_type") for e in events]
    assert "channel_action_received" in event_types
    assert "channel_action_processed" in event_types
    assert "channel_action_duplicate" in event_types
    assert "channel_action_invalid" in event_types
    assert "channel_action_stale" in event_types


def test_slack_interactive_blocks_actions_when_rollout_control_disables_slack(monkeypatch, client, db):
    item = _create_ap_item(db, gmail_id="thread-slack-blocked")
    db.ensure_organization("default", organization_name="default")
    db.update_organization(
        "default",
        settings={
            "rollback_controls": {
                "channel_actions_disabled": {"slack": True},
                "reason": "slack_rollback_control_enabled",
            }
        },
    )
    workflow = _WorkflowStub()

    async def _return_body(request):
        return await request.body()

    monkeypatch.setattr("clearledgr.api.slack_invoices.require_slack_signature", _return_body)
    monkeypatch.setattr("clearledgr.api.slack_invoices.get_invoice_workflow", lambda _org: workflow)

    payload = {
        "callback_id": "run-slack-blocked-1",
        "user": {"id": "U1", "username": "approver"},
        "channel": {"id": "C1"},
        "message": {"ts": "1711111111.100"},
        "actions": [{"action_id": "approve_invoice_thread-slack-blocked", "value": "thread-slack-blocked"}],
    }
    response = client.post(
        "/slack/invoices/interactive",
        content=_slack_form_body(payload),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "x-slack-request-timestamp": str(int(time.time())),
        },
    )
    assert response.status_code == 200
    assert "temporarily disabled" in response.json()["text"]
    assert workflow.calls == []

    events = db.list_ap_audit_events(item["id"])
    event_types = [e.get("event_type") for e in events]
    assert "channel_action_blocked" in event_types


def test_teams_interactive_blocks_actions_when_rollout_control_disables_teams(monkeypatch, client, db):
    item = _create_ap_item(db, gmail_id="thread-teams-blocked")
    db.ensure_organization("default", organization_name="default")
    db.update_organization(
        "default",
        settings={
            "rollback_controls": {
                "channel_actions_disabled": {"teams": True},
                "reason": "teams_rollback_control_enabled",
            }
        },
    )
    workflow = _WorkflowStub()
    monkeypatch.setattr(
        "clearledgr.api.teams_invoices.verify_teams_token",
        lambda _auth: {"appid": "bot-test", "iat": int(time.time())},
    )
    monkeypatch.setattr("clearledgr.api.teams_invoices.get_invoice_workflow", lambda _org: workflow)

    response = client.post(
        "/teams/invoices/interactive",
        json={
            "action": "approve_invoice",
            "email_id": "thread-teams-blocked",
            "organization_id": "default",
            "actor": "approver@clearledgr.com",
            "conversation_id": "19:finance",
            "message_id": "msg-blocked",
            "request_ts": str(int(time.time())),
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["reason"] == "teams_rollback_control_enabled"
    assert workflow.calls == []

    events = db.list_ap_audit_events(item["id"])
    event_types = [e.get("event_type") for e in events]
    assert "channel_action_blocked" in event_types
