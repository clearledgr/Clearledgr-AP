"""DB-level AP pipeline smoke test (state-machine/audit contract).

Validates the canonical PLAN.md state path using a real temp-file SQLite DB and
direct AP item transitions:
    received -> validated -> needs_approval -> approved
    -> ready_to_post -> posted_to_erp -> closed

This file is intentionally a storage-contract smoke test (transition legality,
audit completeness, retry semantics). It does not prove runtime service/callback
orchestration behavior; that coverage lives in:
- ``tests/test_invoice_workflow_runtime_state_transitions.py``
- ``tests/test_channel_approval_contract.py``
"""

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core.ap_states import APState, IllegalTransitionError
from clearledgr.core.database import ClearledgrDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    """Fresh isolated ClearledgrDB with a test organization."""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp.close()
    _db = ClearledgrDB(db_path=tmp.name)
    _db.initialize()
    _db.ensure_organization(
        organization_id="e2e-org",
        organization_name="E2E Test Org",
        domain="e2e.test",
    )
    yield _db
    os.unlink(tmp.name)


@pytest.fixture()
def invoice_payload():
    """Realistic invoice payload as extracted from an email."""
    return {
        "organization_id": "e2e-org",
        "vendor_name": "Acme Industrial Supply",
        "amount": 4_250.00,
        "currency": "USD",
        "invoice_number": f"INV-{uuid.uuid4().hex[:8].upper()}",
        "state": "received",
        "thread_id": f"gmail-{uuid.uuid4().hex[:12]}",
        "due_date": "2026-03-15",
        "description": "Monthly widget supply order #47",
        "po_number": "PO-2026-0042",
    }


# ---------------------------------------------------------------------------
# Happy path: received -> ... -> closed
# ---------------------------------------------------------------------------

class TestFullPipelineE2E:
    """End-to-end: single invoice from intake through closure."""

    def test_happy_path_with_audit_trail(self, db, invoice_payload):
        """Invoice traverses every state on the primary path and each
        transition is recorded in the audit trail."""

        # 1. Intake — create AP item in received state
        item = db.create_ap_item(invoice_payload)
        ap_id = item["id"] if isinstance(item, dict) else item
        fetched = db.get_ap_item(ap_id)
        assert fetched["state"] == "received"
        assert fetched["vendor_name"] == "Acme Industrial Supply"
        assert float(fetched["amount"]) == 4_250.00

        # 2. Validation — received -> validated
        db.update_ap_item(
            ap_id,
            state="validated",
            confidence=0.95,
            _actor_type="system",
            _actor_id="email_parser",
        )
        assert db.get_ap_item(ap_id)["state"] == "validated"

        # 3. Route to approval — validated -> needs_approval
        db.update_ap_item(
            ap_id,
            state="needs_approval",
            _actor_type="system",
            _actor_id="approval_router",
        )
        assert db.get_ap_item(ap_id)["state"] == "needs_approval"

        # 4. Approve — needs_approval -> approved
        db.update_ap_item(
            ap_id,
            state="approved",
            approved_by="jane.cfo@e2e.test",
            approved_at=datetime.now(timezone.utc).isoformat(),
            _actor_type="user",
            _actor_id="jane.cfo@e2e.test",
        )
        fetched = db.get_ap_item(ap_id)
        assert fetched["state"] == "approved"
        assert fetched["approved_by"] == "jane.cfo@e2e.test"

        # 5. Queue for posting — approved -> ready_to_post
        db.update_ap_item(
            ap_id,
            state="ready_to_post",
            _actor_type="system",
            _actor_id="workflow_engine",
        )
        assert db.get_ap_item(ap_id)["state"] == "ready_to_post"

        # 6. ERP post — ready_to_post -> posted_to_erp
        erp_ref = f"NS-BILL-{uuid.uuid4().hex[:6].upper()}"
        db.update_ap_item(
            ap_id,
            state="posted_to_erp",
            erp_reference=erp_ref,
            erp_posted_at=datetime.now(timezone.utc).isoformat(),
            _actor_type="system",
            _actor_id="erp_adapter",
        )
        fetched = db.get_ap_item(ap_id)
        assert fetched["state"] == "posted_to_erp"
        assert fetched["erp_reference"] == erp_ref
        assert fetched["erp_posted_at"] is not None

        # 7. Close — posted_to_erp -> closed
        db.update_ap_item(
            ap_id,
            state="closed",
            _actor_type="system",
            _actor_id="auto_closer",
        )
        assert db.get_ap_item(ap_id)["state"] == "closed"

        # 8. Terminal guard — cannot transition from closed
        with pytest.raises(IllegalTransitionError):
            db.update_ap_item(
                ap_id,
                state="received",
                _actor_type="system",
                _actor_id="should_fail",
            )

        # 9. Verify audit trail completeness
        audit = db.list_audit_events(organization_id="e2e-org")
        ap_events = [e for e in audit if e.get("box_id") == ap_id]

        # Should have exactly 6 state_transition events
        transitions = [e for e in ap_events if e.get("event_type") == "state_transition"]
        assert len(transitions) >= 6, (
            f"Expected >= 6 audit transitions, got {len(transitions)}"
        )

        # Verify the transition chain
        expected_chain = [
            ("received", "validated"),
            ("validated", "needs_approval"),
            ("needs_approval", "approved"),
            ("approved", "ready_to_post"),
            ("ready_to_post", "posted_to_erp"),
            ("posted_to_erp", "closed"),
        ]
        for prev_state, new_state in expected_chain:
            match = [
                e for e in transitions
                if e.get("prev_state") == prev_state
                and e.get("new_state") == new_state
            ]
            assert len(match) >= 1, (
                f"Missing audit entry for {prev_state} -> {new_state}"
            )

        # Every audit event has actor info
        for event in transitions:
            assert event.get("actor_type"), f"Missing actor_type in {event}"
            assert event.get("actor_id"), f"Missing actor_id in {event}"


    def test_exception_path_fail_retry_succeed(self, db, invoice_payload):
        """Exception path: ERP post fails, retries, then succeeds."""

        item = db.create_ap_item(invoice_payload)
        ap_id = item["id"] if isinstance(item, dict) else item

        # Fast-forward to ready_to_post
        for from_state, to_state, actor in [
            ("received", "validated", "parser"),
            ("validated", "needs_approval", "router"),
            ("needs_approval", "approved", "approver"),
            ("approved", "ready_to_post", "workflow"),
        ]:
            db.update_ap_item(
                ap_id, state=to_state,
                _actor_type="system", _actor_id=actor,
            )

        assert db.get_ap_item(ap_id)["state"] == "ready_to_post"

        # ERP fails
        db.update_ap_item(
            ap_id,
            state="failed_post",
            last_error="NetSuite API: 503 Service Unavailable",
            _actor_type="system",
            _actor_id="erp_adapter",
        )
        fetched = db.get_ap_item(ap_id)
        assert fetched["state"] == "failed_post"
        assert "503" in fetched["last_error"]

        # Operator retries
        db.update_ap_item(
            ap_id,
            state="ready_to_post",
            last_error=None,
            _actor_type="user",
            _actor_id="ops@e2e.test",
        )
        assert db.get_ap_item(ap_id)["state"] == "ready_to_post"

        # ERP succeeds on retry
        erp_ref = "NS-BILL-RETRY-001"
        db.update_ap_item(
            ap_id,
            state="posted_to_erp",
            erp_reference=erp_ref,
            erp_posted_at=datetime.now(timezone.utc).isoformat(),
            _actor_type="system",
            _actor_id="erp_adapter",
        )
        fetched = db.get_ap_item(ap_id)
        assert fetched["state"] == "posted_to_erp"
        assert fetched["erp_reference"] == erp_ref

        # Verify audit captured the exception path
        audit = db.list_audit_events(organization_id="e2e-org")
        ap_events = [
            e for e in audit
            if e.get("box_id") == ap_id and e.get("event_type") == "state_transition"
        ]
        fail_events = [e for e in ap_events if e.get("new_state") == "failed_post"]
        assert len(fail_events) >= 1, "Missing audit entry for failed_post transition"
        retry_events = [
            e for e in ap_events
            if e.get("prev_state") == "failed_post" and e.get("new_state") == "ready_to_post"
        ]
        assert len(retry_events) >= 1, "Missing audit entry for retry transition"


    def test_rejection_path(self, db, invoice_payload):
        """Rejection path: needs_approval -> rejected (terminal)."""

        item = db.create_ap_item(invoice_payload)
        ap_id = item["id"] if isinstance(item, dict) else item

        # Fast-forward to needs_approval
        db.update_ap_item(ap_id, state="validated", _actor_type="system", _actor_id="parser")
        db.update_ap_item(ap_id, state="needs_approval", _actor_type="system", _actor_id="router")

        # Reject
        db.update_ap_item(
            ap_id,
            state="rejected",
            rejected_by="finance-lead",
            rejection_reason="Duplicate of INV-0039",
            rejected_at=datetime.now(timezone.utc).isoformat(),
            _actor_type="user",
            _actor_id="finance-lead",
        )
        fetched = db.get_ap_item(ap_id)
        assert fetched["state"] == "rejected"
        assert fetched["rejected_by"] == "finance-lead"
        assert fetched["rejection_reason"] == "Duplicate of INV-0039"

        # Terminal — cannot proceed
        with pytest.raises(IllegalTransitionError):
            db.update_ap_item(
                ap_id, state="approved",
                _actor_type="user", _actor_id="override",
            )


    def test_needs_info_resubmit_path(self, db, invoice_payload):
        """Needs-info loop: validated -> needs_info -> validated -> continues."""

        item = db.create_ap_item(invoice_payload)
        ap_id = item["id"] if isinstance(item, dict) else item

        # received -> validated
        db.update_ap_item(ap_id, state="validated", _actor_type="system", _actor_id="parser")

        # validated -> needs_info (missing PO number)
        db.update_ap_item(
            ap_id, state="needs_info",
            _actor_type="system", _actor_id="validator",
        )
        assert db.get_ap_item(ap_id)["state"] == "needs_info"

        # needs_info -> validated (info provided)
        db.update_ap_item(
            ap_id, state="validated",
            _actor_type="user", _actor_id="submitter",
        )
        fetched = db.get_ap_item(ap_id)
        assert fetched["state"] == "validated"

        # Continue to approval
        db.update_ap_item(ap_id, state="needs_approval", _actor_type="system", _actor_id="router")
        assert db.get_ap_item(ap_id)["state"] == "needs_approval"


    def test_cross_tenant_isolation_in_pipeline(self, db, invoice_payload):
        """Items from different orgs don't leak across tenant boundaries."""

        db.ensure_organization("org-alpha", "Alpha Corp")
        db.ensure_organization("org-beta", "Beta Inc")

        payload_a = {**invoice_payload, "organization_id": "org-alpha"}
        payload_b = {**invoice_payload, "organization_id": "org-beta",
                     "invoice_number": f"INV-B-{uuid.uuid4().hex[:6]}"}

        item_a = db.create_ap_item(payload_a)
        item_b = db.create_ap_item(payload_b)
        id_a = item_a["id"] if isinstance(item_a, dict) else item_a
        id_b = item_b["id"] if isinstance(item_b, dict) else item_b

        items_alpha = db.list_ap_items_all(organization_id="org-alpha")
        items_beta = db.list_ap_items_all(organization_id="org-beta")

        alpha_ids = {i["id"] for i in items_alpha}
        beta_ids = {i["id"] for i in items_beta}

        assert id_a in alpha_ids
        assert id_a not in beta_ids
        assert id_b in beta_ids
        assert id_b not in alpha_ids


    def test_idempotent_erp_guard(self, db, invoice_payload):
        """Double-posting the same AP item to ERP is blocked at the DB level."""

        item = db.create_ap_item(invoice_payload)
        ap_id = item["id"] if isinstance(item, dict) else item

        # Fast-forward to posted_to_erp
        for from_st, to_st in [
            ("received", "validated"),
            ("validated", "needs_approval"),
            ("needs_approval", "approved"),
            ("approved", "ready_to_post"),
        ]:
            db.update_ap_item(ap_id, state=to_st, _actor_type="system", _actor_id="test")

        db.update_ap_item(
            ap_id,
            state="posted_to_erp",
            erp_reference="NS-IDEM-001",
            _actor_type="system",
            _actor_id="erp",
        )

        # Trying to transition again from posted_to_erp to posted_to_erp
        # should fail (not a valid transition)
        with pytest.raises(IllegalTransitionError):
            db.update_ap_item(
                ap_id,
                state="posted_to_erp",
                erp_reference="NS-IDEM-002",
                _actor_type="system",
                _actor_id="erp",
            )

        # Original reference preserved
        assert db.get_ap_item(ap_id)["erp_reference"] == "NS-IDEM-001"
