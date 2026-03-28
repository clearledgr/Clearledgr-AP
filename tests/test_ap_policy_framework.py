from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from clearledgr.api import ap_policies as ap_policies_module
from clearledgr.api.ap_policies import router as ap_policies_router
from clearledgr.core.auth import TokenData
from clearledgr.core.database import ClearledgrDB
from clearledgr.services.invoice_workflow import InvoiceData, InvoiceWorkflowService
from clearledgr.services import policy_compliance as policy_compliance_module
from clearledgr.services import agent_background as agent_background_module


def _make_db(tmp_path: Path) -> ClearledgrDB:
    db = ClearledgrDB(str(tmp_path / "ap-policy-framework.db"))
    db.initialize()
    return db


def _fake_user(role: str = "admin", organization_id: str = "default") -> TokenData:
    return TokenData(
        user_id="ap-policy-user",
        email="policy-admin@example.com",
        organization_id=organization_id,
        role=role,
        exp=datetime.now(timezone.utc) + timedelta(hours=1),
    )


class _FakeWorkflowDB:
    def __init__(self) -> None:
        self._rows = {}

    def get_invoice_status(self, gmail_id: str):
        return self._rows.get(gmail_id)

    def save_invoice_status(self, **kwargs):
        gmail_id = kwargs.get("gmail_id")
        self._rows[gmail_id] = dict(kwargs)
        return gmail_id

    def update_invoice_status(self, gmail_id: str = "", **kwargs):
        key = gmail_id or kwargs.pop("gmail_id", "")
        self._rows.setdefault(key, {})
        self._rows[key].update(kwargs)
        return True

    def get_slack_thread(self, gmail_id: str):
        return None


def test_ap_policy_api_is_versioned_and_auditable(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr("clearledgr.api.ap_policies.get_db", lambda: db)
    monkeypatch.setattr("clearledgr.services.policy_compliance.get_db", lambda: db)

    app = FastAPI()
    app.include_router(ap_policies_router)
    app.dependency_overrides[ap_policies_module.get_current_user] = lambda: _fake_user()
    client = TestClient(app)

    put_payload = {
        "organization_id": "default",
        "updated_by": "finance-admin@example.com",
        "enabled": True,
        "config": {
            "inherit_defaults": False,
            "approval_automation": {
                "reminder_hours": 6,
                "escalation_hours": 18,
                "escalation_channel": "#finance-escalations",
            },
            "approval_thresholds": [
                {
                    "policy_id": "approval_cfo_1000",
                    "name": "CFO sign-off at 1k",
                    "threshold": 1000,
                    "operator": "gte",
                    "approvers": ["cfo"],
                }
            ],
            "vendor_rules": [
                {
                    "policy_id": "google_director_review",
                    "vendor_contains": "google",
                    "threshold": 500,
                    "operator": "gte",
                    "approvers": ["director", "cfo"],
                }
            ],
            "budget_rules": [
                {
                    "policy_id": "budget_exceeded_block",
                    "statuses": ["exceeded"],
                    "action": "block",
                }
            ],
        },
    }

    put_response = client.put("/api/ap/policies/ap_business_v1", json=put_payload)
    assert put_response.status_code == 200
    put_body = put_response.json()
    assert put_body["policy"]["version"] == 1
    assert put_body["policy"]["updated_by"] == "finance-admin@example.com"
    assert len(put_body["effective_policies"]) == 3
    assert put_body["approval_automation"] == {
        "reminder_hours": 6,
        "escalation_hours": 18,
        "escalation_channel": "#finance-escalations",
    }

    get_response = client.get(
        "/api/ap/policies",
        params={
            "organization_id": "default",
            "policy_name": "ap_business_v1",
            "include_versions": "true",
        },
    )
    assert get_response.status_code == 200
    get_body = get_response.json()
    assert get_body["policy"]["version"] == 1
    assert len(get_body["versions"]) == 1
    assert get_body["approval_automation"]["reminder_hours"] == 6
    assert get_body["approval_automation"]["escalation_hours"] == 18

    versions_response = client.get(
        "/api/ap/policies/ap_business_v1/versions",
        params={"organization_id": "default"},
    )
    assert versions_response.status_code == 200
    assert versions_response.json()["versions"][0]["version"] == 1

    audit_response = client.get(
        "/api/ap/policies/ap_business_v1/audit",
        params={"organization_id": "default"},
    )
    assert audit_response.status_code == 200
    events = audit_response.json()["events"]
    assert len(events) >= 1
    assert events[0]["action"] == "upsert"


def test_ap_policy_api_rejects_invalid_vendor_rule(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr("clearledgr.api.ap_policies.get_db", lambda: db)
    monkeypatch.setattr("clearledgr.services.policy_compliance.get_db", lambda: db)

    app = FastAPI()
    app.include_router(ap_policies_router)
    app.dependency_overrides[ap_policies_module.get_current_user] = lambda: _fake_user()
    client = TestClient(app)

    invalid_payload = {
        "organization_id": "default",
        "updated_by": "finance-admin@example.com",
        "enabled": True,
        "config": {
            "vendor_rules": [
                {
                    "vendor_contains": "google",
                }
            ]
        },
    }
    response = client.put("/api/ap/policies/ap_business_v1", json=invalid_payload)
    assert response.status_code == 422
    assert response.json()["detail"]["message"] == "invalid_policy_document"


def test_ap_policy_api_rejects_invalid_approval_automation(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr("clearledgr.api.ap_policies.get_db", lambda: db)
    monkeypatch.setattr("clearledgr.services.policy_compliance.get_db", lambda: db)

    app = FastAPI()
    app.include_router(ap_policies_router)
    app.dependency_overrides[ap_policies_module.get_current_user] = lambda: _fake_user()
    client = TestClient(app)

    invalid_payload = {
        "organization_id": "default",
        "updated_by": "finance-admin@example.com",
        "enabled": True,
        "config": {
            "approval_automation": {
                "reminder_hours": 12,
                "escalation_hours": 4,
            }
        },
    }
    response = client.put("/api/ap/policies/ap_business_v1", json=invalid_payload)
    assert response.status_code == 422
    assert response.json()["detail"]["message"] == "invalid_policy_document"
    assert "approval_automation.escalation_hours" in " ".join(response.json()["detail"]["errors"])


def test_runtime_policy_changes_drive_workflow_routing(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    db.upsert_ap_policy_version(
        organization_id="default",
        policy_name="ap_business_v1",
        updated_by="finance-admin@example.com",
        enabled=True,
        config={
            "inherit_defaults": False,
            "approval_thresholds": [
                {
                    "policy_id": "cfo_anything_over_100",
                    "threshold": 100,
                    "operator": "gte",
                    "approvers": ["cfo"],
                }
            ],
        },
    )

    monkeypatch.setattr(policy_compliance_module, "get_db", lambda: db)
    monkeypatch.setattr(
        "clearledgr.services.invoice_workflow.get_policy_compliance",
        lambda _org: policy_compliance_module.PolicyComplianceService("default"),
    )

    service = InvoiceWorkflowService(organization_id="default", auto_approve_threshold=0.95)
    service.db = _FakeWorkflowDB()

    calls = {"auto": 0, "send": 0}

    async def fake_auto(_invoice, reason="high_confidence"):
        calls["auto"] += 1
        return {"status": "auto_approved", "reason": reason}

    async def fake_send(_invoice, extra_context=None):
        calls["send"] += 1
        return {"status": "pending_approval", "validation_gate": extra_context.get("validation_gate")}

    monkeypatch.setattr(service, "_auto_approve_and_post", fake_auto)
    monkeypatch.setattr(service, "_send_for_approval", fake_send)

    invoice = InvoiceData(
        gmail_id="gmail-policy-1",
        subject="Invoice 2001",
        sender="billing@google.com",
        vendor_name="Google Workspace",
        amount=150.0,
        confidence=0.99,
    )

    result = asyncio.run(service.process_new_invoice(invoice))
    reason_codes = result.get("validation_gate", {}).get("reason_codes", [])

    assert result["status"] == "pending_approval"
    assert calls["send"] == 1
    assert calls["auto"] == 0
    assert any(code.startswith("policy_requirement_") for code in reason_codes)


def test_background_approval_timeouts_follow_policy_milestones(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr("clearledgr.core.database.get_db", lambda: db)
    monkeypatch.setattr("clearledgr.services.policy_compliance.get_db", lambda: db)

    db.upsert_ap_policy_version(
        organization_id="default",
        policy_name="ap_business_v1",
        updated_by="finance-admin@example.com",
        enabled=True,
        config={
            "approval_automation": {
                "reminder_hours": 2,
                "escalation_hours": 6,
                "escalation_channel": "#finance-escalations",
            }
        },
    )

    requested_at = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
    created = db.create_ap_item(
        {
            "id": "policy-timeout-1",
            "invoice_key": "inv-policy-timeout-1",
            "thread_id": "thread-policy-timeout-1",
            "message_id": "msg-policy-timeout-1",
            "subject": "Policy timeout invoice",
            "sender": "billing@example.com",
            "vendor_name": "Policy Vendor",
            "amount": 125.0,
            "currency": "USD",
            "invoice_number": "INV-POLICY-TIMEOUT-1",
            "state": "needs_approval",
            "organization_id": "default",
            "updated_at": requested_at,
            "metadata": {
                "approval_sent_to": ["approver-1"],
                "approval_requested_at": requested_at,
            },
        }
    )
    db.update_ap_item(
        created["id"],
        metadata={
            "approval_sent_to": ["approver-1"],
            "approval_requested_at": requested_at,
        },
    )
    with db.connect() as conn:
        conn.execute("UPDATE ap_items SET updated_at = ? WHERE id = ?", (requested_at, created["id"]))
        conn.commit()

    calls = []

    async def _fake_send_approval_reminder(*, ap_item, approver_ids, hours_pending, organization_id=None, stage="reminder", escalation_channel=None):
        calls.append(
            {
                "ap_item_id": ap_item.get("id"),
                "approver_ids": list(approver_ids or []),
                "hours_pending": hours_pending,
                "organization_id": organization_id,
                "stage": stage,
                "escalation_channel": escalation_channel,
            }
        )
        return True

    monkeypatch.setattr(
        "clearledgr.services.slack_notifications.send_approval_reminder",
        _fake_send_approval_reminder,
    )

    asyncio.run(agent_background_module._check_approval_timeouts("default"))

    assert calls == [
        {
            "ap_item_id": "policy-timeout-1",
            "approver_ids": ["approver-1"],
            "hours_pending": 2.0,
            "organization_id": "default",
            "stage": "reminder",
            "escalation_channel": "#finance-escalations",
        },
        {
            "ap_item_id": "policy-timeout-1",
            "approver_ids": ["approver-1"],
            "hours_pending": 6.0,
            "organization_id": "default",
            "stage": "escalation",
            "escalation_channel": "#finance-escalations",
        },
    ]

    refreshed = db.get_ap_item("policy-timeout-1")
    metadata = refreshed.get("metadata") or {}
    if isinstance(metadata, str):
        import json
        metadata = json.loads(metadata)
    assert metadata["approval_nudge_count"] == 1
    assert metadata["approval_escalation_count"] == 1
    assert metadata["approval_reminder_milestones"]["reminder_2h"]
    assert metadata["approval_reminder_milestones"]["escalation_6h"]

    events = db.list_ap_audit_events("policy-timeout-1")
    event_types = [event.get("event_type") for event in events]
    reasons = [event.get("decision_reason") for event in events]
    assert "approval_nudge_sent" in event_types
    assert "approval_escalation_sent" in event_types
    assert "approval_nudge_auto_2h" in reasons
    assert "approval_escalation_auto_6h" in reasons
