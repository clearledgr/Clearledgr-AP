import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.workflows.ap.client import APTemporalClient
from clearledgr.workflows.ap.types import build_workflow_id
from clearledgr.workflows.ap.workflow import APTemporalWorkflow


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "temporal.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


def test_workflow_signals_update_state_and_close():
    wf = APTemporalWorkflow()
    wf.state = "needs_approval"
    APTemporalWorkflow.approval_decision(wf, {"action": "approve"})
    assert wf.state == "approved"
    assert wf.last_approval_decision == {"action": "approve"}

    APTemporalWorkflow.approval_decision(wf, {"action": "reject", "reason": "invalid"})
    assert wf.state == "rejected"
    assert wf.closed is True


def test_workflow_retry_post_signal_moves_failed_post_to_ready():
    wf = APTemporalWorkflow()
    wf.state = "failed_post"
    APTemporalWorkflow.retry_post(wf, {"actor_id": "system"})
    assert wf.retry_requested is True
    assert wf.state == "ready_to_post"


def test_temporal_client_start_or_attach_persists_runtime_ids(db):
    item = db.create_ap_item(
        {
            "invoice_key": "vendor|temporal|1.00|",
            "thread_id": "thread-temporal",
            "message_id": "msg-temporal",
            "subject": "Invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 1.0,
            "currency": "USD",
            "invoice_number": "INV-TEMP",
            "state": "received",
            "organization_id": "default",
            "approval_required": True,
        }
    )

    client = APTemporalClient()
    envelope = asyncio.run(
        client.start_or_attach(
            organization_id="default",
            ap_item_id=item["id"],
            command_name="intake",
            payload={"initial_state": "received"},
            actor_type="agent",
            actor_id="intake_workflow",
        )
    )

    updated = db.get_ap_item(item["id"])
    assert updated is not None
    assert updated.get("workflow_id") == build_workflow_id("default", item["id"])
    assert updated.get("run_id")
    assert envelope.workflow_id == updated.get("workflow_id")
    assert envelope.run_id == updated.get("run_id")
    assert envelope.detail == "local_fallback"
