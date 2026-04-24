from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from clearledgr.core.database import ClearledgrDB, get_db


def test_get_pending_approver_ids_falls_back_to_pending_chain_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key-for-approval-followup")
    db = get_db()
    db.initialize()

    db.create_ap_item(
        {
            "id": "ap-followup-1",
            "organization_id": "default",
            "thread_id": "gmail-thread-followup-1",
            "state": "needs_approval",
            "metadata": {
                "approval_chain_id": "chain-followup-1",
            },
        }
    )

    chain = SimpleNamespace(
        chain_id="chain-followup-1",
        organization_id="default",
        invoice_id="gmail-thread-followup-1",
        vendor_name="Approval Chain Co",
        amount=10.0,
        gl_code=None,
        department=None,
        status="pending",
        current_step=0,
        requester_id="ap_agent",
        requester_name="Clearledgr AP Agent",
        created_at=datetime.now(timezone.utc),
        completed_at=None,
        steps=[
            SimpleNamespace(
                step_id="step-followup-1",
                level="L1",
                approvers=["U123", "U456"],
                approval_type="any",
                status="pending",
                approved_by=None,
                approved_at=None,
                rejection_reason=None,
                comments="",
            )
        ],
    )
    db.db_create_approval_chain(chain)

    assert db.get_pending_approver_ids("ap-followup-1") == ["U123", "U456"]


def test_get_pending_approver_ids_prefers_delivery_targets_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key-for-approval-followup")
    db = get_db()
    db.initialize()

    db.create_ap_item(
        {
            "id": "ap-followup-2",
            "organization_id": "default",
            "thread_id": "gmail-thread-followup-2",
            "state": "needs_approval",
            "metadata": {
                "approval_sent_to": ["approver@company.com"],
                "approval_delivery_targets": ["U999"],
            },
        }
    )

    assert db.get_pending_approver_ids("ap-followup-2") == ["U999"]
