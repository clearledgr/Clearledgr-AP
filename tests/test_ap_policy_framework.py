from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from clearledgr.api.ap_policies import router as ap_policies_router
from clearledgr.core.database import ClearledgrDB
from clearledgr.services.invoice_workflow import InvoiceData, InvoiceWorkflowService
from clearledgr.services import policy_compliance as policy_compliance_module


def _make_db(tmp_path: Path) -> ClearledgrDB:
    db = ClearledgrDB(str(tmp_path / "ap-policy-framework.db"))
    db.initialize()
    return db


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


class _RecurringDetector:
    def analyze_invoice(self, **_kwargs):
        return {
            "is_recurring": False,
            "auto_approve": False,
            "alerts": [],
            "pattern": {},
        }


def test_ap_policy_api_is_versioned_and_auditable(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr("clearledgr.api.ap_policies.get_db", lambda: db)
    monkeypatch.setattr("clearledgr.services.policy_compliance.get_db", lambda: db)

    app = FastAPI()
    app.include_router(ap_policies_router)
    client = TestClient(app)

    put_payload = {
        "organization_id": "default",
        "updated_by": "finance-admin@example.com",
        "enabled": True,
        "config": {
            "inherit_defaults": False,
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
    monkeypatch.setattr(
        "clearledgr.services.recurring_detection.get_recurring_detector",
        lambda _org: _RecurringDetector(),
    )

    service = InvoiceWorkflowService(organization_id="default", auto_approve_threshold=0.95)
    service.db = _FakeWorkflowDB()

    calls = {"auto": 0, "send": 0}

    async def fake_auto(_invoice, reason="high_confidence", recurring_info=None):
        calls["auto"] += 1
        return {"status": "auto_approved", "reason": reason, "recurring": recurring_info}

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
