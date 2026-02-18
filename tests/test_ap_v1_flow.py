import json
import time
import hmac
import hashlib
import urllib.parse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app
from clearledgr.core import database as db_module
from clearledgr.services import invoice_workflow as workflow_module
from clearledgr.services import slack_api as slack_api_module
from clearledgr.services import teams_api as teams_api_module
from clearledgr.services import gmail_autopilot as autopilot_module
from clearledgr.api import gmail_extension as gmail_extension_module
from clearledgr.services.gmail_api import GmailToken
from clearledgr.services.invoice_workflow import get_invoice_workflow
from clearledgr.services.email_parser import parse_email
from clearledgr.workflows.ap import client as temporal_client_module


class DummyMessage:
    def __init__(self, labels):
        self.labels = labels


class DummyGmailClient:
    added_labels = []
    labels_by_message = {}
    notes = []

    def __init__(self, user_id):
        self.user_id = user_id

    async def ensure_authenticated(self):
        return True

    async def list_labels(self):
        return []

    async def create_label(self, name):
        return {"id": name, "name": name}

    async def add_label(self, message_id, label_ids):
        DummyGmailClient.added_labels.append((message_id, label_ids))
        existing = DummyGmailClient.labels_by_message.get(message_id, [])
        for label_id in label_ids:
            if label_id not in existing:
                existing.append(label_id)
        DummyGmailClient.labels_by_message[message_id] = existing

    async def get_message(self, message_id, format="metadata"):
        return DummyMessage(DummyGmailClient.labels_by_message.get(message_id, []))

    async def send_thread_note(self, thread_id, to_email, subject, body):
        DummyGmailClient.notes.append((thread_id, to_email, subject, body))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ERP_MODE", "mock")
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    monkeypatch.setenv("AP_TEMPORAL_REQUIRED", "false")
    db_module._DB_INSTANCE = None
    temporal_client_module._CLIENT = None
    db = db_module.get_db()
    db.initialize()
    return db


@pytest.fixture()
def client(db):
    return TestClient(app)


@pytest.fixture()
def mock_gmail(monkeypatch):
    DummyGmailClient.added_labels = []
    DummyGmailClient.labels_by_message = {}
    DummyGmailClient.notes = []
    monkeypatch.setattr(workflow_module, "GmailAPIClient", DummyGmailClient)
    return DummyGmailClient


def _sign_slack(body: bytes, timestamp: str, secret: str) -> str:
    sig_base = f"v0:{timestamp}:{body.decode()}"
    digest = hmac.new(secret.encode(), sig_base.encode(), hashlib.sha256).hexdigest()
    return f"v0={digest}"


def _sign_teams(body: bytes, timestamp: str, secret: str) -> str:
    sig_base = f"v1:{timestamp}:{body.decode()}"
    digest = hmac.new(secret.encode(), sig_base.encode(), hashlib.sha256).hexdigest()
    return f"v1={digest}"


def test_reject_transition_writes_audit_and_updates_email(db, mock_gmail):
    ap_item = db.create_ap_item({
        "invoice_key": "vendor|inv|100.00|",
        "thread_id": "thread-1",
        "message_id": "msg-1",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 100.0,
        "currency": "USD",
        "invoice_number": "INV-1",
        "state": "needs_approval",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-1"
    })

    workflow = get_invoice_workflow("default")
    result = asyncio_run(workflow.reject_ap_item(
        ap_item_id=ap_item["id"],
        rejected_by="user-1",
        reason="duplicate"
    ))

    assert result["status"] == "rejected"
    updated = db.get_ap_item(ap_item["id"])
    assert updated["state"] == "rejected"
    assert updated["rejected_by"] == "user-1"
    assert updated["rejection_reason"] == "duplicate"

    events = db.list_ap_audit_events(ap_item["id"])
    assert any(e.get("new_state") == "rejected" for e in events)
    assert DummyGmailClient.added_labels
    assert DummyGmailClient.notes


def test_slack_signature_verification(client):
    slack_api_module.SLACK_SIGNING_SECRET = "test-secret"

    payload = {"type": "block_actions", "actions": [{"action_id": "approve_ap_fake", "value": "fake"}]}
    body = f"payload={urllib.parse.quote(json.dumps(payload))}".encode()

    ts = str(int(time.time()))
    sig = _sign_slack(body, ts, "test-secret")

    # Invalid signature
    response = client.post(
        "/slack/invoices/interactive",
        data=body,
        headers={
            "x-slack-request-timestamp": ts,
            "x-slack-signature": "v0=invalid"
        }
    )
    assert response.status_code == 401

    # Valid signature
    response = client.post(
        "/slack/invoices/interactive",
        data=body,
        headers={
            "x-slack-request-timestamp": ts,
            "x-slack-signature": sig
        }
    )
    assert response.status_code in (200, 404)


def test_slack_invalid_signature_is_audited_when_item_resolved(db, client):
    slack_api_module.SLACK_SIGNING_SECRET = "test-secret"
    ap_item = db.create_ap_item({
        "invoice_key": "vendor|sig-audit|50.00|",
        "thread_id": "thread-sig",
        "message_id": "msg-sig",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 50.0,
        "currency": "USD",
        "invoice_number": "INV-SIG",
        "state": "needs_approval",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-sig",
    })
    action_value = json.dumps({"ap_item_id": ap_item["id"], "run_id": "run-1"})
    payload = {
        "type": "block_actions",
        "user": {"id": "U1", "username": "approver"},
        "channel": {"id": "C1"},
        "message": {"ts": "111.222"},
        "actions": [{"action_id": "approve_ap", "value": action_value}],
    }
    body = f"payload={urllib.parse.quote(json.dumps(payload))}".encode()
    ts = str(int(time.time()))
    response = client.post(
        "/api/slack/actions",
        data=body,
        headers={
            "x-slack-request-timestamp": ts,
            "x-slack-signature": "v0=invalid",
        },
    )
    assert response.status_code == 401
    events = db.list_ap_audit_events(ap_item["id"])
    assert any(event.get("event_type") == "approval_callback_rejected" for event in events)


def test_teams_signature_verification(client):
    teams_api_module.TEAMS_SIGNING_SECRET = "teams-secret"
    teams_api_module.TEAMS_LEGACY_HMAC_ALLOWED = False
    payload = {
        "action": "approve",
        "ap_item_id": "AP-MISSING",
        "run_id": "RUN-1",
        "actor_id": "U-1",
        "message_ref": "msg-1",
        "channel": "finance",
    }
    body = json.dumps(payload).encode("utf-8")
    ts = str(int(time.time()))
    sig = _sign_teams(body, ts, "teams-secret")

    response = client.post(
        "/api/teams/actions",
        content=body,
        headers={
            "x-teams-request-timestamp": ts,
            "x-teams-signature": "v1=invalid",
            "content-type": "application/json",
        },
    )
    assert response.status_code == 401

    response = client.post(
        "/api/teams/actions",
        content=body,
        headers={
            "x-teams-request-timestamp": ts,
            "x-teams-signature": sig,
            "content-type": "application/json",
        },
    )
    # JWT-first mode rejects legacy HMAC when fallback is disabled.
    assert response.status_code == 401

    teams_api_module.TEAMS_LEGACY_HMAC_ALLOWED = True
    response = client.post(
        "/api/teams/actions",
        content=body,
        headers={
            "x-teams-request-timestamp": ts,
            "x-teams-signature": sig,
            "content-type": "application/json",
        },
    )
    assert response.status_code == 404
    teams_api_module.TEAMS_LEGACY_HMAC_ALLOWED = False


def test_slack_approval_idempotency(db, client, mock_gmail):
    slack_api_module.SLACK_SIGNING_SECRET = "test-secret"

    ap_item = db.create_ap_item({
        "invoice_key": "vendor|inv|100.00|",
        "thread_id": "thread-2",
        "message_id": "msg-2",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 100.0,
        "currency": "USD",
        "invoice_number": "INV-2",
        "state": "needs_approval",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-2"
    })

    payload = {
        "type": "block_actions",
        "user": {"id": "U1", "username": "approver"},
        "channel": {"id": "C1"},
        "message": {"ts": "123.456"},
        "actions": [{"action_id": f"approve_ap_{ap_item['id']}", "value": ap_item["id"]}]
    }
    body = f"payload={urllib.parse.quote(json.dumps(payload))}".encode()
    ts = str(int(time.time()))
    sig = _sign_slack(body, ts, "test-secret")

    response = client.post(
        "/slack/invoices/interactive",
        data=body,
        headers={
            "x-slack-request-timestamp": ts,
            "x-slack-signature": sig
        }
    )
    assert response.status_code == 200

    response = client.post(
        "/slack/invoices/interactive",
        data=body,
        headers={
            "x-slack-request-timestamp": ts,
            "x-slack-signature": sig
        }
    )
    assert response.status_code == 200

    events = db.list_ap_audit_events(ap_item["id"])
    posted_events = [
        e for e in events
        if e.get("event_type") == "state_transition" and e.get("new_state") == "posted_to_erp"
    ]
    assert len(posted_events) == 1


def test_erp_posting_persists_reference(db, mock_gmail):
    ap_item = db.create_ap_item({
        "invoice_key": "vendor|inv|200.00|",
        "thread_id": "thread-3",
        "message_id": "msg-3",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 200.0,
        "currency": "USD",
        "invoice_number": "INV-3",
        "state": "needs_approval",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-3"
    })

    workflow = get_invoice_workflow("default")
    result = asyncio_run(workflow.approve_ap_item(
        ap_item_id=ap_item["id"],
        approved_by="user-3",
        idempotency_key="approve:test"
    ))

    assert result["status"] == "posted"
    updated = db.get_ap_item(ap_item["id"])
    assert updated["erp_reference"]
    assert updated["state"] == "closed"
    assert any("Posted to ERP" in note[3] for note in DummyGmailClient.notes)


def test_retry_post_endpoint(db, client, mock_gmail):
    ap_item = db.create_ap_item({
        "invoice_key": "vendor|inv-retry|200.00|",
        "thread_id": "thread-retry",
        "message_id": "msg-retry",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 200.0,
        "currency": "USD",
        "invoice_number": "INV-RETRY",
        "state": "failed_post",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-retry",
    })

    response = client.post(f"/api/ap/items/{ap_item['id']}/retry-post")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"posted", "failed_post"}


def test_reconciliation_routes_removed(client):
    response = client.get("/analytics/dashboard/default")
    assert response.status_code == 404


def test_direct_gmail_approve_endpoint_is_disabled(client):
    response = client.post(
        "/extension/approve-and-post",
        json={"email_id": "thread-test", "extraction": {}, "organization_id": "default"},
    )
    assert response.status_code == 409


def test_direct_gmail_reject_endpoint_is_disabled(client):
    response = client.post(
        "/extension/reject-invoice",
        json={"email_id": "thread-test", "reason": "no"},
    )
    assert response.status_code == 409


def test_rejected_resubmission_creates_new_item(db, mock_gmail, monkeypatch):
    class DummySlackClient:
        async def send_message(self, channel, text, blocks=None, **kwargs):
            return type("Msg", (), {"channel": channel, "ts": "111.222"})()

    monkeypatch.setattr(workflow_module, "get_slack_client", lambda *args, **kwargs: DummySlackClient())

    rejected = db.create_ap_item({
        "invoice_key": "vendor|inv-4|100.00|",
        "thread_id": "thread-4",
        "message_id": "msg-4",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 100.0,
        "currency": "USD",
        "invoice_number": "INV-4",
        "state": "rejected",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-4",
        "metadata": {"attachment_hashes": ["abc123"]}
    })

    invoice = workflow_module.InvoiceData(
        gmail_id="msg-4b",
        thread_id="thread-4b",
        message_id="msg-4b",
        subject="Invoice resubmission",
        sender="vendor@example.com",
        vendor_name="Vendor",
        amount=100.0,
        currency="USD",
        invoice_number="INV-4",
        invoice_date=None,
        due_date=None,
        confidence=0.8,
        organization_id="default",
        user_id="user-4",
        metadata={"attachment_hashes": ["abc123"]},
    )

    workflow = get_invoice_workflow("default")
    result = asyncio_run(workflow.process_new_invoice(invoice))
    new_item = result.get("ap_item")
    assert new_item
    assert new_item["id"] != rejected["id"]
    raw_meta = new_item.get("metadata") or {}
    metadata = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
    assert metadata.get("supersedes_ap_item_id") == rejected["id"]
    assert new_item["state"] == "needs_approval"


def test_by_thread_endpoint_returns_all_items_and_latest(db, client):
    first = db.create_ap_item({
        "invoice_key": "vendor|thread-many-1|11.00|",
        "thread_id": "thread-many",
        "message_id": "msg-many-1",
        "subject": "Invoice 1",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 11.0,
        "currency": "USD",
        "invoice_number": "INV-M1",
        "state": "received",
        "confidence": 0.8,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-many",
    })
    second = db.create_ap_item({
        "invoice_key": "vendor|thread-many-2|12.00|",
        "thread_id": "thread-many",
        "message_id": "msg-many-2",
        "subject": "Invoice 2",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 12.0,
        "currency": "USD",
        "invoice_number": "INV-M2",
        "state": "validated",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-many",
    })

    response = client.get("/api/ap/items/by-thread/thread-many?organization_id=default")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("items"), list)
    assert len(payload["items"]) == 2
    assert payload["latest"]["id"] in {first["id"], second["id"]}


def test_illegal_reject_transition_is_blocked(db):
    ap_item = db.create_ap_item({
        "invoice_key": "vendor|inv-6|42.00|",
        "thread_id": "thread-6",
        "message_id": "msg-6",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 42.0,
        "currency": "USD",
        "invoice_number": "INV-6",
        "state": "validated",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-6",
    })
    workflow = get_invoice_workflow("default")
    result = asyncio_run(workflow.reject_ap_item(ap_item["id"], rejected_by="user-6", reason="invalid"))
    assert result["status"] == "invalid_state"
    assert db.get_ap_item(ap_item["id"])["state"] == "validated"


def test_reject_from_approved_before_post_attempt_is_allowed(db):
    ap_item = db.create_ap_item({
        "invoice_key": "vendor|inv-8|42.00|",
        "thread_id": "thread-8",
        "message_id": "msg-8",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 42.0,
        "currency": "USD",
        "invoice_number": "INV-8",
        "state": "approved",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-8",
    })
    workflow = get_invoice_workflow("default")
    result = asyncio_run(workflow.reject_ap_item(ap_item["id"], rejected_by="user-8", reason="invalid"))
    assert result["status"] == "rejected"
    assert db.get_ap_item(ap_item["id"])["state"] == "rejected"


def test_reject_from_approved_after_post_attempt_is_blocked(db):
    ap_item = db.create_ap_item({
        "invoice_key": "vendor|inv-9|42.00|",
        "thread_id": "thread-9",
        "message_id": "msg-9",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 42.0,
        "currency": "USD",
        "invoice_number": "INV-9",
        "state": "approved",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-9",
        "post_attempted_at": datetime.now(timezone.utc).isoformat(),
    })
    workflow = get_invoice_workflow("default")
    result = asyncio_run(workflow.reject_ap_item(ap_item["id"], rejected_by="user-9", reason="invalid"))
    assert result["status"] == "conflict_post_started"
    assert db.get_ap_item(ap_item["id"])["state"] == "approved"


def test_post_audit_event_endpoint(client, db):
    ap_item = db.create_ap_item({
        "invoice_key": "vendor|inv-7|10.00|",
        "thread_id": "thread-7",
        "message_id": "msg-7",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 10.0,
        "currency": "USD",
        "invoice_number": "INV-7",
        "state": "received",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-7",
    })
    response = client.post(
        "/api/audit/events",
        json={
            "org_id": "default",
            "ap_item_id": ap_item["id"],
            "actor_type": "system",
            "actor_id": "tester",
            "event_type": "thread_updated",
            "prev_state": "received",
            "new_state": "received",
            "thread_id": "thread-7",
            "message_id": "msg-7",
            "payload_json": {"note": "ok"},
            "idempotency_key": f"test:event:{ap_item['id']}",
        },
    )
    assert response.status_code == 200
    event = response.json()["event"]
    assert event["event_type"] == "thread_updated"
    assert event["external_refs"]["gmail_thread_id"] == "thread-7"


def test_audit_persistence_external_refs(db, mock_gmail):
    ap_item = db.create_ap_item({
        "invoice_key": "vendor|inv-5|50.00|",
        "thread_id": "thread-5",
        "message_id": "msg-5",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 50.0,
        "currency": "USD",
        "invoice_number": "INV-5",
        "state": "needs_approval",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-5"
    })

    workflow = get_invoice_workflow("default")
    result = asyncio_run(workflow.reject_ap_item(
        ap_item_id=ap_item["id"],
        rejected_by="user-5",
        reason="invalid"
    ))
    assert result["status"] == "rejected"
    events = db.list_ap_audit_events(ap_item["id"])
    assert any(e.get("external_refs", {}).get("gmail_thread_id") == "thread-5" for e in events)


def test_parser_extracts_google_invoice_amount_not_invoice_number():
    parsed = parse_email(
        subject="Google Workspace invoice",
        body=(
            "Invoice number: 5449235811\n"
            "Invoice date 31 Dec 2025\n"
            "Total in EUR €40.23\n"
        ),
        sender="payments-noreply@google.com",
        attachments=[],
    )
    assert parsed.get("primary_invoice") == "5449235811"
    assert parsed.get("primary_amount") == 40.23
    assert parsed.get("currency") == "EUR"


def test_parser_keeps_legitimate_zero_amount_invoice():
    parsed = parse_email(
        subject="Google Cloud invoice",
        body=(
            "Invoice number: 5463719421\n"
            "Invoice date 31 Dec 2025\n"
            "Total in USD US$0.00\n"
        ),
        sender="payments-noreply@google.com",
        attachments=[],
    )
    assert parsed.get("primary_invoice") == "5463719421"
    assert parsed.get("primary_amount") == 0.0
    assert parsed.get("currency") == "USD"


def test_policy_engine_routes_to_needs_info(db, monkeypatch):
    monkeypatch.setenv(
        "AP_VENDOR_RULES_JSON",
        json.dumps({"vendor": {"max_amount": 25, "require_invoice_number": True}}),
    )
    monkeypatch.setenv("AP_REQUIRE_ATTACHMENT", "true")
    invoice = workflow_module.InvoiceData(
        gmail_id="msg-policy",
        thread_id="thread-policy",
        message_id="msg-policy",
        subject="Invoice",
        sender="vendor@example.com",
        vendor_name="Vendor",
        amount=100.0,
        currency="USD",
        invoice_number="INV-POLICY",
        invoice_date=None,
        due_date=None,
        confidence=0.91,
        organization_id="default",
        user_id="user-policy",
        metadata={"attachment_hashes": ["hash-1"]},
    )
    workflow = get_invoice_workflow("default")
    result = asyncio_run(workflow.process_new_invoice(invoice))
    assert result["status"] == "needs_info"
    missing = set(result.get("missing_fields") or [])
    assert "vendor_amount_limit_exceeded" in missing


def test_sla_escalation_is_idempotent(db, monkeypatch):
    monkeypatch.setenv("AP_APPROVAL_SLA_MINUTES", "1")
    stale_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    ap_item = db.create_ap_item({
        "invoice_key": "vendor|sla|10.00|",
        "thread_id": "thread-sla",
        "message_id": "msg-sla",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 10.0,
        "currency": "USD",
        "invoice_number": "INV-SLA",
        "state": "needs_approval",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-sla",
    })
    db.update_ap_item(ap_item["id"], created_at=stale_time)

    class DummySlack:
        bot_token = "x"

        async def send_message(self, channel, text):
            return type("Msg", (), {"ts": "999.111"})()

    class DummyTeams:
        webhook_url = ""

        async def send_approval_message(self, **kwargs):
            return type("Msg", (), {"message_id": "teams-1"})()

    monkeypatch.setattr(autopilot_module, "get_slack_client", lambda: DummySlack())
    monkeypatch.setattr(autopilot_module, "get_teams_client", lambda: DummyTeams())

    autopilot = autopilot_module.GmailAutopilot()
    asyncio_run(autopilot._process_sla_escalations())
    asyncio_run(autopilot._process_sla_escalations())

    events = db.list_ap_audit_events(ap_item["id"])
    escalations = [e for e in events if e.get("event_type") == "approval_escalated"]
    assert len(escalations) == 1


def test_ops_tenant_health_endpoint(client, db, monkeypatch):
    monkeypatch.setenv("AP_APPROVAL_SLA_MINUTES", "1")
    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(minutes=20)).isoformat()
    approved_at = (now - timedelta(minutes=10)).isoformat()
    attempted_at = (now - timedelta(minutes=9)).isoformat()
    failed_at = (now - timedelta(minutes=8)).isoformat()
    ap_item = db.create_ap_item({
        "invoice_key": "vendor|ops|20.00|",
        "thread_id": "thread-ops",
        "message_id": "msg-ops",
        "subject": "Invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 20.0,
        "currency": "USD",
        "invoice_number": "INV-OPS",
        "state": "needs_approval",
        "confidence": 0.95,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-ops",
    })
    db.update_ap_item(ap_item["id"], created_at=created_at)
    db.save_approval({
        "ap_item_id": ap_item["id"],
        "channel_id": "slack:C-OPS",
        "message_ts": "123.456",
        "status": "approved",
        "approved_by": "approver",
        "approved_at": approved_at,
        "organization_id": "default",
        "created_at": created_at,
    })
    db.append_ap_audit_event({
        "ap_item_id": ap_item["id"],
        "event_type": "erp_post_attempted",
        "from_state": "ready_to_post",
        "to_state": "ready_to_post",
        "actor_type": "system",
        "actor_id": "erp",
        "organization_id": "default",
        "ts": attempted_at,
    })
    db.append_ap_audit_event({
        "ap_item_id": ap_item["id"],
        "event_type": "erp_post_failed",
        "from_state": "ready_to_post",
        "to_state": "failed_post",
        "actor_type": "system",
        "actor_id": "erp",
        "organization_id": "default",
        "ts": failed_at,
    })
    db.append_ap_audit_event({
        "ap_item_id": ap_item["id"],
        "event_type": "approval_callback_rejected",
        "from_state": "needs_approval",
        "to_state": "needs_approval",
        "actor_type": "system",
        "actor_id": "slack_callback",
        "organization_id": "default",
        "ts": failed_at,
    })

    response = client.get("/api/ops/tenant-health?organization_id=default")
    assert response.status_code == 200
    health = response.json()["health"]
    assert health["organization_id"] == "default"
    assert "queue_lag" in health
    assert "approval_latency" in health
    assert "posting" in health
    assert "post_failure_rate" in health
    assert "callback_verification_failures" in health
    assert "workflow_stuck_count" in health
    assert health["callback_verification_failures"]["count"] >= 1


def test_autopilot_status_blocks_when_temporal_required_unavailable(client, monkeypatch):
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "true")
    monkeypatch.setenv("AP_TEMPORAL_REQUIRED", "true")
    monkeypatch.delenv("TEMPORAL_ADDRESS", raising=False)
    temporal_client_module._CLIENT = None

    response = client.get("/api/ops/autopilot-status")
    assert response.status_code == 200
    autopilot = response.json()["autopilot"]
    assert autopilot["state"] == "blocked"
    assert autopilot["temporal_required"] is True
    assert autopilot["temporal_available"] is False
    assert autopilot["error"] == "temporal_unavailable"


def test_register_gmail_token_enables_backend_autopilot(client, db, monkeypatch):
    class DummyResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "google-user-1", "email": "ap-user@example.com"}

    class DummyAsyncClient:
        def __init__(self, timeout=10.0):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            return DummyResponse()

    monkeypatch.setattr(gmail_extension_module.httpx, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(gmail_extension_module.token_store, "_db", db)

    response = client.post(
        "/extension/gmail/register-token",
        json={"access_token": "token-123", "expires_in": 3600},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["user_id"] == "google-user-1"
    assert payload["email"] == "ap-user@example.com"

    tokens = db.list_oauth_tokens("gmail")
    assert len(tokens) == 1
    assert tokens[0]["email"] == "ap-user@example.com"

    autopilot_response = client.get("/api/ops/autopilot-status")
    assert autopilot_response.status_code == 200
    autopilot = autopilot_response.json()["autopilot"]
    assert autopilot["has_tokens"] is True
    assert autopilot["state"] != "auth_required"


def test_register_gmail_token_falls_back_to_gmail_profile_when_userinfo_denied(client, db, monkeypatch):
    class DummyResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, timeout=10.0):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            if "oauth2/v2/userinfo" in url:
                return DummyResponse(403, {"error": {"message": "insufficient_scope"}})
            if "gmail/v1/users/me/profile" in url:
                return DummyResponse(200, {"emailAddress": "gmail-scope-only@example.com"})
            return DummyResponse(404, {})

    monkeypatch.setattr(gmail_extension_module.httpx, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(gmail_extension_module.token_store, "_db", db)

    response = client.post(
        "/extension/gmail/register-token",
        json={"access_token": "token-123", "expires_in": 3600},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["user_id"] == "gmail-scope-only@example.com"
    assert payload["email"] == "gmail-scope-only@example.com"

    tokens = db.list_oauth_tokens("gmail")
    assert len(tokens) == 1
    assert tokens[0]["email"] == "gmail-scope-only@example.com"

    autopilot_response = client.get("/api/ops/autopilot-status")
    assert autopilot_response.status_code == 200
    autopilot = autopilot_response.json()["autopilot"]
    assert autopilot["has_tokens"] is True
    assert autopilot["state"] != "auth_required"


def test_gmail_token_is_expired_handles_timezone_aware_and_naive_datetimes():
    future_aware = GmailToken(
        user_id="u1",
        access_token="a",
        refresh_token="r",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        email="u1@example.com",
    )
    assert future_aware.is_expired() is False

    future_naive = GmailToken(
        user_id="u2",
        access_token="a",
        refresh_token="r",
        expires_at=datetime.now() + timedelta(minutes=30),
        email="u2@example.com",
    )
    assert future_naive.is_expired() is False

    past_aware = GmailToken(
        user_id="u3",
        access_token="a",
        refresh_token="r",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        email="u3@example.com",
    )
    assert past_aware.is_expired() is True


def test_invoice_number_duplicate_links_sources_and_single_item(db, client, monkeypatch):
    monkeypatch.setenv("AP_APPROVAL_SURFACE", "gmail")
    monkeypatch.setenv("AP_APPROVAL_THRESHOLD", "1000000")
    workflow = get_invoice_workflow("default")
    async def _stub_send_for_approval(_ap_item):
        return 1
    monkeypatch.setattr(workflow, "_send_for_approval", _stub_send_for_approval)

    invoice_one = workflow_module.InvoiceData(
        gmail_id="msg-dup-1",
        thread_id="thread-dup-1",
        message_id="msg-dup-1",
        subject="Invoice INV-DUP-1",
        sender="vendor@example.com",
        vendor_name="Vendor",
        amount=125.0,
        currency="USD",
        invoice_number="INV-DUP-1",
        due_date="2026-02-28",
        organization_id="default",
        user_id="user-dup",
        metadata={"attachment_hashes": ["hash-dup-1"]},
    )
    first = asyncio_run(workflow.process_new_invoice(invoice_one))
    first_item = first["ap_item"]

    invoice_two = workflow_module.InvoiceData(
        gmail_id="msg-dup-2",
        thread_id="thread-dup-2",
        message_id="msg-dup-2",
        subject="Invoice INV-DUP-1 Follow-up",
        sender="vendor@example.com",
        vendor_name="Vendor",
        amount=125.0,
        currency="USD",
        invoice_number="INV-DUP-1",
        due_date="2026-02-28",
        organization_id="default",
        user_id="user-dup",
        metadata={"attachment_hashes": ["hash-dup-1"]},
    )
    second = asyncio_run(workflow.process_new_invoice(invoice_two))
    assert second["status"] == "duplicate"
    assert second["ap_item"]["id"] == first_item["id"]

    all_items = db.list_ap_items("default")
    assert len(all_items) == 1
    sources = db.list_ap_item_sources(first_item["id"])
    refs = {(source.get("source_type"), source.get("source_ref")) for source in sources}
    assert ("gmail_thread", "thread-dup-1") in refs
    assert ("gmail_thread", "thread-dup-2") in refs
    assert ("gmail_message", "msg-dup-1") in refs
    assert ("gmail_message", "msg-dup-2") in refs

    response = client.get(f"/api/ap/items/{first_item['id']}/sources")
    assert response.status_code == 200
    assert response.json()["source_count"] >= 4


def test_attachment_hash_duplicate_merge_when_invoice_number_missing(db, monkeypatch):
    monkeypatch.setenv("AP_APPROVAL_SURFACE", "gmail")
    workflow = get_invoice_workflow("default")

    first = asyncio_run(
        workflow.process_new_invoice(
            workflow_module.InvoiceData(
                gmail_id="msg-hash-1",
                thread_id="thread-hash-1",
                message_id="msg-hash-1",
                subject="Invoice without number",
                sender="vendor@example.com",
                vendor_name="Vendor",
                amount=99.0,
                currency="USD",
                invoice_number=None,
                due_date="2026-02-01",
                organization_id="default",
                user_id="user-hash",
                metadata={"attachment_hashes": ["same-hash"]},
            )
        )
    )
    second = asyncio_run(
        workflow.process_new_invoice(
            workflow_module.InvoiceData(
                gmail_id="msg-hash-2",
                thread_id="thread-hash-2",
                message_id="msg-hash-2",
                subject="Invoice resend without number",
                sender="vendor@example.com",
                vendor_name="Vendor",
                amount=99.0,
                currency="USD",
                invoice_number=None,
                due_date="2026-02-10",
                organization_id="default",
                user_id="user-hash",
                metadata={"attachment_hashes": ["same-hash"]},
            )
        )
    )
    assert first["ap_item"]["id"] == second["ap_item"]["id"]
    assert second["status"] == "duplicate"
    assert second.get("merge_reason") == "attachment_hash"


def test_invoice_number_amount_conflict_creates_new_item_with_conflict_flag(db, client, monkeypatch):
    monkeypatch.setenv("AP_APPROVAL_SURFACE", "gmail")
    monkeypatch.setenv("AP_APPROVAL_THRESHOLD", "1000000")
    workflow = get_invoice_workflow("default")
    async def _stub_send_for_approval(_ap_item):
        return 1
    monkeypatch.setattr(workflow, "_send_for_approval", _stub_send_for_approval)

    base = asyncio_run(
        workflow.process_new_invoice(
            workflow_module.InvoiceData(
                gmail_id="msg-conflict-1",
                thread_id="thread-conflict-1",
                message_id="msg-conflict-1",
                subject="Invoice conflict seed",
                sender="vendor@example.com",
                vendor_name="Vendor",
                amount=100.0,
                currency="USD",
                invoice_number="INV-CONFLICT",
                due_date="2026-03-01",
                organization_id="default",
                user_id="user-conflict",
                metadata={"attachment_hashes": ["hash-c-1"]},
            )
        )
    )
    assert base["status"] in {"needs_approval", "needs_info"}

    conflict = asyncio_run(
        workflow.process_new_invoice(
            workflow_module.InvoiceData(
                gmail_id="msg-conflict-2",
                thread_id="thread-conflict-2",
                message_id="msg-conflict-2",
                subject="Invoice conflict candidate",
                sender="vendor@example.com",
                vendor_name="Vendor",
                amount=400.0,
                currency="USD",
                invoice_number="INV-CONFLICT",
                due_date="2026-03-01",
                organization_id="default",
                user_id="user-conflict",
                metadata={"attachment_hashes": ["hash-c-2"]},
            )
        )
    )
    assert conflict["status"] != "duplicate"
    all_items = db.list_ap_items("default")
    assert len(all_items) == 2

    response = client.get(f"/api/ap/items/{conflict['ap_item']['id']}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["has_context_conflict"] is True
    assert payload["merge_reason"] == "invoice_number"


def test_worklist_sources_context_endpoints_and_pipeline_compat(db, client, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "")
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "")

    item = db.create_ap_item({
        "invoice_key": "vendor|worklist|15.00|",
        "thread_id": "thread-worklist",
        "message_id": "msg-worklist",
        "subject": "Worklist invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor",
        "amount": 15.0,
        "currency": "USD",
        "invoice_number": "INV-WORKLIST",
        "state": "needs_info",
        "confidence": 0.9,
        "approval_required": True,
        "organization_id": "default",
        "user_id": "user-worklist",
        "metadata": {"merge_reason": "invoice_number"},
    })
    db.link_ap_item_source({
        "ap_item_id": item["id"],
        "source_type": "gmail_thread",
        "source_ref": "thread-worklist-alt",
        "subject": "Worklist invoice alt thread",
        "sender": "vendor@example.com",
        "metadata": {"test": True},
    })

    worklist_response = client.get("/extension/worklist?organization_id=default")
    assert worklist_response.status_code == 200
    worklist_items = worklist_response.json()["items"]
    assert any(entry.get("id") == item["id"] and entry.get("source_count", 0) >= 1 for entry in worklist_items)

    pipeline_response = client.get("/extension/pipeline?organization_id=default")
    assert pipeline_response.status_code == 200
    pipeline = pipeline_response.json()
    assert isinstance(pipeline, dict)
    assert "needs_info" in pipeline

    sources_response = client.get(f"/api/ap/items/{item['id']}/sources")
    assert sources_response.status_code == 200
    assert sources_response.json()["source_count"] >= 1

    context_response = client.get(f"/api/ap/items/{item['id']}/context?refresh=true")
    assert context_response.status_code == 200
    context = context_response.json()
    assert "email" in context
    assert "web" in context
    assert "approvals" in context
    assert "erp" in context
    assert context["approvals"]["slack"]["available"] is False


def test_ap_policy_api_versioning_roundtrip(client):
    get_before = client.get("/api/ap/policies?organization_id=default&policy_name=ap_business_v1")
    assert get_before.status_code == 200
    before_policy = get_before.json()["policy"]
    assert before_policy["organization_id"] == "default"

    create_response = client.put(
        "/api/ap/policies/ap_business_v1",
        json={
            "org_id": "default",
            "updated_by": "tester",
            "enabled": True,
            "config": {
                "validation": {
                    "require_budget_context": True,
                    "budget_check_required_over": 0,
                    "block_on_budget_overrun": False,
                }
            },
        },
    )
    assert create_response.status_code == 200
    first = create_response.json()["policy"]
    assert first["version"] == 1
    assert first["config"]["validation"]["require_budget_context"] is True

    update_response = client.put(
        "/api/ap/policies/ap_business_v1",
        json={
            "org_id": "default",
            "updated_by": "tester-2",
            "enabled": True,
            "config": {
                "validation": {
                    "require_budget_context": True,
                    "budget_check_required_over": 0,
                    "block_on_budget_overrun": True,
                }
            },
        },
    )
    assert update_response.status_code == 200
    second = update_response.json()["policy"]
    assert second["version"] == 2
    assert second["updated_by"] == "tester-2"
    assert second["config"]["validation"]["block_on_budget_overrun"] is True

    get_after = client.get(
        "/api/ap/policies?organization_id=default&policy_name=ap_business_v1&include_versions=true"
    )
    assert get_after.status_code == 200
    payload = get_after.json()
    assert payload["policy"]["version"] == 2
    assert isinstance(payload.get("versions"), list)
    assert len(payload["versions"]) >= 2
    assert payload["versions"][0]["version"] == 2


def test_budget_overrun_routing_respects_policy_block_flag(db, monkeypatch):
    monkeypatch.setenv("AP_APPROVAL_SURFACE", "gmail")
    monkeypatch.setenv("AP_REQUIRE_ATTACHMENT", "true")
    monkeypatch.setenv("AP_CHAT_ONLY_THRESHOLD", "1000000")
    workflow = get_invoice_workflow("default")
    async def _stub_send_for_approval(_ap_item):
        return 1
    monkeypatch.setattr(workflow, "_send_for_approval", _stub_send_for_approval)

    db.upsert_ap_policy_version(
        organization_id="default",
        policy_name="ap_business_v1",
        updated_by="tester",
        enabled=True,
        config={
            "validation": {
                "require_budget_context": True,
                "budget_check_required_over": 0,
                "block_on_budget_overrun": False,
            }
        },
    )

    non_blocking = asyncio_run(
        workflow.process_new_invoice(
            workflow_module.InvoiceData(
                gmail_id="msg-budget-1",
                thread_id="thread-budget-1",
                message_id="msg-budget-1",
                subject="Invoice non-blocking budget",
                sender="vendor@example.com",
                vendor_name="Vendor",
                amount=120.0,
                currency="USD",
                invoice_number="INV-BUDGET-1",
                due_date="2026-02-25",
                organization_id="default",
                user_id="user-budget",
                metadata={
                    "attachment_hashes": ["budget-hash-1"],
                    "budget": {"remaining": 50.0, "currency": "USD"},
                },
            )
        )
    )
    assert non_blocking["status"] == "needs_approval"
    non_blocking_meta = non_blocking["ap_item"]["metadata"]
    if isinstance(non_blocking_meta, str):
        non_blocking_meta = json.loads(non_blocking_meta)
    assert non_blocking_meta["budget_check_result"]["status"] == "over_budget"
    assert non_blocking_meta["exception_code"] == "budget_overrun"

    db.upsert_ap_policy_version(
        organization_id="default",
        policy_name="ap_business_v1",
        updated_by="tester",
        enabled=True,
        config={
            "validation": {
                "require_budget_context": True,
                "budget_check_required_over": 0,
                "block_on_budget_overrun": True,
            }
        },
    )

    blocking = asyncio_run(
        workflow.process_new_invoice(
            workflow_module.InvoiceData(
                gmail_id="msg-budget-2",
                thread_id="thread-budget-2",
                message_id="msg-budget-2",
                subject="Invoice blocking budget",
                sender="vendor@example.com",
                vendor_name="Vendor",
                amount=130.0,
                currency="USD",
                invoice_number="INV-BUDGET-2",
                due_date="2026-02-26",
                organization_id="default",
                user_id="user-budget",
                metadata={
                    "attachment_hashes": ["budget-hash-2"],
                    "budget": {"remaining": 40.0, "currency": "USD"},
                },
            )
        )
    )
    assert blocking["status"] == "needs_info"
    assert "budget_overrun" in set(blocking.get("missing_fields") or [])


def test_worklist_prioritizes_exception_items(client, db):
    low = db.create_ap_item(
        {
            "invoice_key": "vendor|prio-low|9.00|",
            "thread_id": "thread-prio-low",
            "message_id": "msg-prio-low",
            "subject": "Low priority invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 9.0,
            "currency": "USD",
            "invoice_number": "INV-PRIO-LOW",
            "state": "needs_approval",
            "confidence": 0.8,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-prio",
            "metadata": {"priority_score": 10},
        }
    )
    high = db.create_ap_item(
        {
            "invoice_key": "vendor|prio-high|50.00|",
            "thread_id": "thread-prio-high",
            "message_id": "msg-prio-high",
            "subject": "High priority invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 50.0,
            "currency": "USD",
            "invoice_number": "INV-PRIO-HIGH",
            "state": "needs_info",
            "confidence": 0.8,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-prio",
            "metadata": {
                "exception_code": "budget_overrun",
                "exception_severity": "high",
                "priority_score": 350,
            },
        }
    )
    db.update_ap_item(
        high["id"],
        created_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    )

    response = client.get("/extension/worklist?organization_id=default")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 2
    assert items[0]["id"] == high["id"]
    assert items[0]["exception_severity"] == "high"
    assert float(items[0]["priority_score"]) >= float(items[1]["priority_score"])
    assert any(entry["id"] == low["id"] for entry in items)


def test_worklist_handles_naive_due_date_without_server_error(client, db):
    db.create_ap_item(
        {
            "invoice_key": "vendor|naive-due|22.00|",
            "thread_id": "thread-naive-due",
            "message_id": "msg-naive-due",
            "subject": "Naive due date invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 22.0,
            "currency": "USD",
            "invoice_number": "INV-NAIVE-DUE",
            "state": "needs_approval",
            "confidence": 0.8,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-naive-due",
            "due_date": "2026-02-20T12:00:00",
            "metadata": {},
        }
    )

    response = client.get("/extension/worklist?organization_id=default")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("items"), list)


def test_ap_kpis_endpoint_contract(client, db):
    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(hours=6)).isoformat()
    posted_at = (now - timedelta(hours=2)).isoformat()

    completed = db.create_ap_item(
        {
            "invoice_key": "vendor|kpi-complete|120.00|",
            "thread_id": "thread-kpi-1",
            "message_id": "msg-kpi-1",
            "subject": "Completed invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 120.0,
            "currency": "USD",
            "invoice_number": "INV-KPI-1",
            "state": "closed",
            "confidence": 0.9,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-kpi",
            "metadata": {
                "discount": {"available": True, "amount": 15.0, "deadline": (now - timedelta(days=1)).isoformat(), "taken": False}
            },
        }
    )
    db.update_ap_item(completed["id"], created_at=created_at, erp_posted_at=posted_at)
    db.save_approval(
        {
            "ap_item_id": completed["id"],
            "channel_id": "slack:C-KPI",
            "message_ts": "123.789",
            "status": "approved",
            "approved_by": "approver",
            "approved_at": (now - timedelta(hours=4)).isoformat(),
            "organization_id": "default",
            "created_at": (now - timedelta(hours=5)).isoformat(),
        }
    )
    db.create_ap_item(
        {
            "invoice_key": "vendor|kpi-open|45.00|",
            "thread_id": "thread-kpi-2",
            "message_id": "msg-kpi-2",
            "subject": "Open exception invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 45.0,
            "currency": "USD",
            "invoice_number": "INV-KPI-2",
            "state": "needs_info",
            "confidence": 0.8,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-kpi",
            "metadata": {"exception_code": "missing_budget_context"},
        }
    )

    response = client.get("/api/ops/ap-kpis?organization_id=default")
    assert response.status_code == 200
    kpis = response.json()["kpis"]
    assert kpis["organization_id"] == "default"
    assert "touchless_rate" in kpis
    assert "cycle_time_hours" in kpis
    assert "exception_rate" in kpis
    assert "on_time_approvals" in kpis
    assert "missed_discounts_baseline" in kpis
    assert kpis["totals"]["items"] >= 2
    assert kpis["missed_discounts_baseline"]["candidate_count"] >= 1


def test_context_endpoint_returns_partial_warnings_for_unavailable_connectors(client, db, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "")
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "")
    item = db.create_ap_item(
        {
            "invoice_key": "vendor|context-warn|21.00|",
            "thread_id": "thread-context-warn",
            "message_id": "msg-context-warn",
            "subject": "Context warning invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 21.0,
            "currency": "USD",
            "invoice_number": "INV-CONTEXT-WARN",
            "state": "needs_approval",
            "confidence": 0.9,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-context",
            "metadata": {
                "po_match_result": {"required": True, "status": "matched"},
                "budget_check_result": {"required": True, "status": "within_budget", "remaining": 79.0, "currency": "USD"},
                "risk_signals": {"requires_human_review": False},
            },
        }
    )

    response = client.get(f"/api/ap/items/{item['id']}/context?refresh=true")
    assert response.status_code == 200
    payload = response.json()
    assert payload["partial"] is True
    assert "slack_unavailable" in payload["warnings"]
    assert "teams_unavailable" in payload["warnings"]
    assert "erp_unavailable" in payload["warnings"]
    assert payload["po_match"]["status"] == "matched"
    assert payload["budget"]["status"] == "within_budget"
    assert "freshness" in payload


def test_manual_merge_endpoint_moves_sources_and_hides_source_item(client, db):
    target = db.create_ap_item(
        {
            "invoice_key": "vendor|merge-target|100.00|",
            "thread_id": "thread-merge-target",
            "message_id": "msg-merge-target",
            "subject": "Target invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": "INV-MERGE",
            "state": "needs_info",
            "confidence": 0.8,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-merge",
        }
    )
    source = db.create_ap_item(
        {
            "invoice_key": "vendor|merge-source|100.00|",
            "thread_id": "thread-merge-source",
            "message_id": "msg-merge-source",
            "subject": "Source invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": "INV-MERGE",
            "state": "needs_info",
            "confidence": 0.8,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-merge",
        }
    )
    db.link_ap_item_source(
        {
            "ap_item_id": target["id"],
            "source_type": "gmail_thread",
            "source_ref": "thread-merge-target-a",
            "subject": "target source",
            "sender": "vendor@example.com",
        }
    )
    db.link_ap_item_source(
        {
            "ap_item_id": source["id"],
            "source_type": "gmail_thread",
            "source_ref": "thread-merge-source-b",
            "subject": "source source",
            "sender": "vendor@example.com",
        }
    )

    response = client.post(
        f"/api/ap/items/{target['id']}/merge",
        json={
            "source_ap_item_id": source["id"],
            "actor_id": "tester",
            "reason": "manual_merge_test",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "merged"

    merged_sources = db.list_ap_item_sources(target["id"])
    merged_refs = {(entry["source_type"], entry["source_ref"]) for entry in merged_sources}
    assert ("gmail_thread", "thread-merge-target-a") in merged_refs
    assert ("gmail_thread", "thread-merge-source-b") in merged_refs

    source_row = db.get_ap_item(source["id"])
    source_metadata = source_row.get("metadata")
    if isinstance(source_metadata, str):
        source_metadata = json.loads(source_metadata)
    assert source_row["state"] == "closed"
    assert source_metadata["hidden_from_worklist"] is True
    assert source_metadata["merged_into_ap_item_id"] == target["id"]

    worklist = client.get("/extension/worklist?organization_id=default")
    assert worklist.status_code == 200
    listed_ids = [entry["id"] for entry in worklist.json()["items"]]
    assert source["id"] not in listed_ids


def test_manual_split_endpoint_moves_selected_source_to_new_item(client, db):
    item = db.create_ap_item(
        {
            "invoice_key": "vendor|split-source|210.00|",
            "thread_id": "thread-split-main",
            "message_id": "msg-split-main",
            "subject": "Split invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 210.0,
            "currency": "USD",
            "invoice_number": "INV-SPLIT",
            "state": "needs_info",
            "confidence": 0.8,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-split",
        }
    )
    source_a = db.link_ap_item_source(
        {
            "ap_item_id": item["id"],
            "source_type": "gmail_thread",
            "source_ref": "thread-split-a",
            "subject": "split-a",
            "sender": "vendor@example.com",
        }
    )
    db.link_ap_item_source(
        {
            "ap_item_id": item["id"],
            "source_type": "gmail_thread",
            "source_ref": "thread-split-b",
            "subject": "split-b",
            "sender": "vendor@example.com",
        }
    )

    response = client.post(
        f"/api/ap/items/{item['id']}/split",
        json={
            "actor_id": "tester",
            "reason": "manual_split_test",
            "sources": [{"source_type": source_a["source_type"], "source_ref": source_a["source_ref"]}],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "split"
    assert payload["new_ap_item"]["state"] == "needs_info"

    new_item_id = payload["new_ap_item"]["id"]
    original_sources = db.list_ap_item_sources(item["id"])
    new_sources = db.list_ap_item_sources(new_item_id)
    original_refs = {(entry["source_type"], entry["source_ref"]) for entry in original_sources}
    new_refs = {(entry["source_type"], entry["source_ref"]) for entry in new_sources}

    assert (source_a["source_type"], source_a["source_ref"]) not in original_refs
    assert (source_a["source_type"], source_a["source_ref"]) in new_refs


def test_context_endpoint_includes_connector_coverage_and_source_quality(client, db, monkeypatch):
    monkeypatch.setenv("AP_CONTEXT_STALE_AFTER_SECONDS", "300")
    stale_detected = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    item = db.create_ap_item(
        {
            "invoice_key": "vendor|ctx-connectors|87.00|",
            "thread_id": "thread-ctx-main",
            "message_id": "msg-ctx-main",
            "subject": "Connector rich invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 87.0,
            "currency": "USD",
            "invoice_number": "INV-CTX-CONNECT",
            "state": "needs_approval",
            "confidence": 0.9,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-ctx",
            "metadata": {
                "web_context": [
                    {"source_type": "payment_portal", "source_ref": "https://pay.vendor.example/invoice/1"},
                    {"source_type": "procurement", "source_ref": "PO-4432"},
                    {"source_type": "dms", "source_ref": "doc://invoice/4432"},
                ]
            },
        }
    )
    db.update_ap_item(item["id"], created_at=stale_detected)
    db.link_ap_item_source(
        {
            "ap_item_id": item["id"],
            "source_type": "gmail_thread",
            "source_ref": "thread-ctx-main",
            "subject": "ctx-source",
            "sender": "vendor@example.com",
            "detected_at": stale_detected,
        }
    )
    db.link_ap_item_source(
        {
            "ap_item_id": item["id"],
            "source_type": "payment_portal",
            "source_ref": "https://pay.vendor.example/invoice/1",
            "subject": "portal",
            "sender": "vendor@example.com",
            "detected_at": stale_detected,
        }
    )
    db.link_ap_item_source(
        {
            "ap_item_id": item["id"],
            "source_type": "procurement",
            "source_ref": "PO-4432",
            "subject": "proc",
            "sender": "vendor@example.com",
            "detected_at": stale_detected,
        }
    )
    db.link_ap_item_source(
        {
            "ap_item_id": item["id"],
            "source_type": "dms",
            "source_ref": "doc://invoice/4432",
            "subject": "dms",
            "sender": "vendor@example.com",
            "detected_at": stale_detected,
        }
    )

    response = client.get(f"/api/ap/items/{item['id']}/context?refresh=true")
    assert response.status_code == 200
    payload = response.json()
    assert payload["web"]["connector_coverage"]["payment_portal"] is True
    assert payload["web"]["connector_coverage"]["procurement"] is True
    assert payload["web"]["connector_coverage"]["dms"] is True
    assert len(payload["web"]["payment_portals"]) >= 1
    assert len(payload["web"]["procurement"]) >= 1
    assert len(payload["web"]["dms_documents"]) >= 1
    assert "source_quality" in payload
    assert payload["freshness"]["is_stale"] is True
    assert "context_stale" in payload["warnings"]


def test_ap_kpi_digest_endpoint_returns_slack_and_teams_payloads(client, db):
    db.create_ap_item(
        {
            "invoice_key": "vendor|kpi-digest|44.00|",
            "thread_id": "thread-kpi-digest",
            "message_id": "msg-kpi-digest",
            "subject": "Digest invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 44.0,
            "currency": "USD",
            "invoice_number": "INV-KPI-DIGEST",
            "state": "needs_approval",
            "confidence": 0.9,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-kpi",
        }
    )
    response = client.get("/api/ops/ap-kpis/digest?organization_id=default&surface=all")
    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_id"] == "default"
    assert "kpis" in payload
    assert "slack" in payload
    assert isinstance(payload["slack"]["text"], str)
    assert isinstance(payload["slack"]["blocks"], list)
    assert "teams" in payload
    assert payload["teams"]["@type"] == "MessageCard"


def asyncio_run(coro):
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        return asyncio.run(coro)
    return loop.run_until_complete(coro)
