from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.services.agent_memory import AgentMemoryService
from clearledgr.services import browser_agent as browser_agent_module


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "browser_agent_dispatch.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    db_module._DB_INSTANCE = None
    browser_agent_module._SERVICE = None
    db = db_module.get_db()
    db.initialize()
    return db


def _create_item(db):
    return db.create_ap_item(
        {
            "invoice_key": "vendor|dispatch|100.00|",
            "thread_id": "thread-dispatch",
            "message_id": "msg-dispatch",
            "subject": "Invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": "INV-DISPATCH",
            "state": "validated",
            "confidence": 0.9,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "dispatch-test",
        }
    )


def test_dispatch_macro_records_dispatched_at_for_follow_on_sessions(db):
    item = _create_item(db)
    service = browser_agent_module.get_browser_agent_service()
    session = service.create_session(
        organization_id="default",
        ap_item_id=str(item["id"]),
        created_by="tester",
        metadata={"workflow_id": "erp_credit_application_fallback"},
    )

    payload = service.dispatch_macro(
        session_id=str(session["id"]),
        macro_name="apply_credit_note_in_erp",
        actor_id="tester",
        actor_role="ap_operator",
        workflow_id="erp_credit_application_fallback",
        correlation_id="corr-follow-on-dispatch",
        params={
            "target_erp_reference": "ERP-BILL-1",
            "credit_note_number": "CN-001",
            "amount": 25.0,
            "currency": "USD",
            "erp_url": "https://mail.google.com/mail/u/0/#inbox",
        },
        dry_run=False,
    )

    assert payload["status"] == "dispatched"
    stored = db.get_agent_session(str(session["id"]))
    metadata = stored["metadata"]
    assert str(metadata.get("dispatched_at") or "").strip()
    assert metadata["last_macro_name"] == "apply_credit_note_in_erp"
    assert metadata["workflow_id"] == "erp_credit_application_fallback"
    assert metadata["correlation_id"] == "corr-follow-on-dispatch"


def test_browser_agent_session_payload_includes_canonical_agent_memory(db):
    item = _create_item(db)
    db.upsert_vendor_profile(
        "default",
        "Vendor",
        payment_terms="Net 30",
        invoice_count=4,
    )
    db.create_workflow_run(
        {
            "organization_id": "default",
            "workflow_name": "erp_posting_fallback",
            "workflow_type": "browser_fallback",
            "ap_item_id": str(item["id"]),
            "status": "running",
            "metadata": {"source": "test"},
        }
    )
    db.create_agent_retry_job(
        {
            "organization_id": "default",
            "ap_item_id": str(item["id"]),
            "job_type": "erp_post_retry",
            "status": "pending",
        }
    )

    AgentMemoryService("default", db=db).capture_runtime_state(
        skill_id="ap_v1",
        ap_item=item,
        ap_item_id=str(item["id"]),
        event_type="approval_request_routed",
        reason="awaiting_approver",
        response={"status": "pending_approval"},
        actor_id="tester",
        correlation_id="corr-browser-memory-1",
    )

    service = browser_agent_module.get_browser_agent_service()
    session = service.create_session(
        organization_id="default",
        ap_item_id=str(item["id"]),
        created_by="tester",
        metadata={"workflow_id": "erp_posting_fallback"},
    )
    payload = service.get_session(str(session["id"]))

    assert payload["session"]["agent_next_action"]["type"] == "await_approval"
    assert payload["agent_memory"]["identity_memory"]["name"] == "Clearledgr AP Agent"
    assert payload["agent_memory"]["semantic_memory"]["vendor_profile"]["payment_terms"] == "Net 30"
    assert payload["agent_memory"]["episodic_memory"]["workflow_runs"]
    assert payload["agent_memory"]["episodic_memory"]["retry_jobs"]
