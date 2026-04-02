"""Runtime orchestration alignment tests for InvoiceWorkflowService.

Unlike DB-direct state-machine tests, these tests exercise real service methods
(`process_new_invoice`, `approve_invoice`, auto-approve/post paths) and assert
the persisted canonical AP transitions and audit behavior.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.services.invoice_workflow import InvoiceData, InvoiceWorkflowService


class _LearningStub:
    def suggest_gl_code(self, **_kwargs):
        return None

    def record_approval(self, **_kwargs):
        return None


class _BudgetStub:
    def check_invoice(self, _payload):
        return []

    def record_spending(self, _budget_id, _amount):
        return None


class _PolicyServiceStub:
    class _Result:
        def to_dict(self):
            return {"compliant": True, "violations": []}

    def check(self, _payload):
        return self._Result()


class _POServiceStub:
    def match_invoice_to_po(self, **_kwargs):
        return {"status": "matched", "exceptions": []}

    def match_invoice_to_gr(self, **_kwargs):
        return {"status": "matched", "exceptions": []}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "workflow.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


@pytest.fixture()
def service(db, monkeypatch):
    svc = InvoiceWorkflowService(organization_id="default", auto_approve_threshold=0.95)
    svc.db = db

    monkeypatch.setattr("clearledgr.services.invoice_workflow.get_learning_service", lambda _org: _LearningStub())
    monkeypatch.setattr("clearledgr.services.invoice_workflow.get_budget_awareness", lambda _org: _BudgetStub())

    async def _noop_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(svc, "_send_posted_notification", _noop_async)
    monkeypatch.setattr(svc, "_update_slack_approved", _noop_async)
    monkeypatch.setattr(svc, "_send_teams_budget_card", lambda *_args, **_kwargs: {"status": "skipped", "reason": "test"})
    return svc


def _create_ap_item(
    db,
    *,
    gmail_id: str,
    state: str,
    amount: float = 125.0,
    confidence: float = 0.99,
    metadata: dict | None = None,
) -> Dict[str, str]:
    return db.create_ap_item(
        {
            "invoice_key": f"vendor|{gmail_id}|{amount:.2f}|",
            "thread_id": gmail_id,
            "message_id": f"msg-{gmail_id}",
            "subject": f"Invoice {gmail_id}",
            "sender": "billing@vendor.test",
            "vendor_name": "Vendor Test",
            "amount": amount,
            "currency": "USD",
            "invoice_number": f"INV-{gmail_id.upper()}",
            "due_date": "2026-03-01",
            "state": state,
            "confidence": confidence,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-test",
            "metadata": metadata or {},
        }
    )


def _transition_pairs(db, ap_item_id: str) -> List[Tuple[str, str]]:
    events = db.list_ap_audit_events(ap_item_id)
    pairs: List[Tuple[str, str]] = []
    for event in events:
        from_state = event.get("from_state")
        to_state = event.get("to_state")
        if from_state and to_state:
            pairs.append((str(from_state), str(to_state)))
    return pairs


def test_process_new_invoice_advances_to_validated_before_routing(service, db, monkeypatch):
    async def _fake_validation(_invoice):
        return {
            "passed": True,
            "checked_at": "2026-02-25T00:00:00+00:00",
            "reason_codes": [],
            "reasons": [],
            "policy_compliance": {},
            "po_match_result": None,
            "budget_impact": [],
            "budget": {"status": "healthy"},
        }

    monkeypatch.setattr(
        service,
        "_evaluate_deterministic_validation",
        _fake_validation,
    )

    async def _fake_send_for_approval(_invoice, extra_context=None):
        return {"status": "pending_approval", "extra_context": extra_context}

    monkeypatch.setattr(service, "_send_for_approval", _fake_send_for_approval)

    invoice = InvoiceData(
        gmail_id="gmail-proc-validated",
        subject="Invoice",
        sender="billing@vendor.test",
        vendor_name="Vendor Test",
        amount=100.0,
        confidence=0.50,  # force manual route
    )

    result = asyncio.run(service.process_new_invoice(invoice))
    assert result["status"] == "pending_approval"

    row = db.get_invoice_status(invoice.gmail_id)
    assert row is not None
    assert row["state"] == "validated"

    ap_item = db.get_ap_item_by_thread("default", invoice.gmail_id)
    assert ap_item is not None
    transitions = _transition_pairs(db, ap_item["id"])
    assert ("received", "validated") in transitions


def test_workflow_state_transition_audits_share_single_correlation_id_across_intake_and_approval(service, db, monkeypatch):
    async def _fake_validation(_invoice):
        return {
            "passed": True,
            "checked_at": "2026-02-25T00:00:00+00:00",
            "reason_codes": [],
            "reasons": [],
            "policy_compliance": {},
            "po_match_result": None,
            "budget_impact": [],
            "budget": {"status": "healthy"},
        }

    monkeypatch.setattr(
        service,
        "_evaluate_deterministic_validation",
        _fake_validation,
    )

    async def _fake_send_for_approval(_invoice, extra_context=None):
        return {"status": "pending_approval", "extra_context": extra_context}

    async def _fake_post(_invoice, **_kwargs):
        return {"status": "success", "bill_id": "BILL-CORR-1", "vendor_id": "VEN-1"}

    monkeypatch.setattr(service, "_send_for_approval", _fake_send_for_approval)
    monkeypatch.setattr(service, "_load_budget_context_from_invoice_row", lambda _row: [])
    monkeypatch.setattr(service, "_check_po_exception_block", lambda _row: {"blocked": False, "exceptions": []})
    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    invoice = InvoiceData(
        gmail_id="gmail-correlation-chain",
        subject="Invoice",
        sender="billing@vendor.test",
        vendor_name="Vendor Test",
        amount=100.0,
        confidence=0.50,
        invoice_number="INV-CORR-1",
        due_date="2026-03-10",
        field_confidences={
            "vendor": 0.99,
            "amount": 0.99,
            "invoice_number": 0.99,
            "due_date": 0.99,
        },
    )

    intake_result = asyncio.run(service.process_new_invoice(invoice))
    assert intake_result["status"] == "pending_approval"
    approve_result = asyncio.run(
        service.approve_invoice(
            gmail_id=invoice.gmail_id,
            approved_by="approver@example.com",
            allow_confidence_override=True,
            override_justification="test_correlation_chain",
        )
    )
    assert approve_result["status"] == "approved"

    ap_item = db.get_ap_item_by_thread("default", invoice.gmail_id)
    assert ap_item is not None
    metadata_raw = ap_item.get("metadata")
    metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else dict(metadata_raw or {})
    correlation_id = str(metadata.get("correlation_id") or "")
    assert correlation_id

    audit_events = db.list_ap_audit_events(ap_item["id"])
    transitions = [e for e in audit_events if e.get("event_type") == "state_transition"]
    assert transitions
    assert any(e.get("to_state") == "posted_to_erp" for e in transitions)
    assert all(e.get("correlation_id") == correlation_id for e in transitions)


def test_process_new_invoice_routes_to_review_on_low_confidence_critical_field(service, db, monkeypatch):
    monkeypatch.setattr(
        "clearledgr.services.invoice_workflow.get_policy_compliance",
        lambda _org: _PolicyServiceStub(),
    )
    monkeypatch.setattr(
        "clearledgr.services.invoice_workflow.get_purchase_order_service",
        lambda _org: _POServiceStub(),
    )

    captured_context = {}

    async def _fake_send_for_approval(_invoice, extra_context=None):
        captured_context.update(extra_context or {})
        return {"status": "pending_approval", "extra_context": extra_context}

    monkeypatch.setattr(service, "_send_for_approval", _fake_send_for_approval)

    invoice = InvoiceData(
        gmail_id="gmail-confidence-route",
        subject="Invoice",
        sender="billing@vendor.test",
        vendor_name="Vendor Test",
        amount=100.0,
        confidence=0.99,
        field_confidences={
            "vendor": 0.82,
            "amount": 0.99,
            "invoice_number": 0.99,
            "due_date": 0.99,
        },
        invoice_number="INV-ROUTE",
        due_date="2026-03-10",
    )

    result = asyncio.run(service.process_new_invoice(invoice))
    assert result["status"] == "pending_approval"

    validation_gate = result.get("validation_gate") or {}
    assert "confidence_field_review_required" in (validation_gate.get("reason_codes") or [])
    confidence_gate = validation_gate.get("confidence_gate") or {}
    assert confidence_gate.get("requires_field_review") is True
    assert any(b["field"] == "vendor" for b in (confidence_gate.get("confidence_blockers") or []))
    assert "validation_gate" in captured_context

    ap_item = db.get_ap_item_by_thread("default", invoice.gmail_id)
    assert ap_item is not None
    metadata_raw = ap_item.get("metadata")
    metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else dict(metadata_raw or {})
    assert metadata["requires_field_review"] is True
    assert any(b["field"] == "vendor" for b in metadata["confidence_blockers"])


def test_approve_invoice_success_transitions_through_ready_to_post(service, db, monkeypatch):
    item = _create_ap_item(db, gmail_id="gmail-approve-success", state="needs_approval")

    monkeypatch.setattr(service, "_load_budget_context_from_invoice_row", lambda _row: [])
    monkeypatch.setattr(service, "_check_po_exception_block", lambda _row: {"blocked": False, "exceptions": []})

    async def _fake_post(_invoice, **_kwargs):
        return {"status": "success", "bill_id": "BILL-123", "vendor_id": "VEN-1"}

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    result = asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-approve-success",
            approved_by="approver@example.com",
        )
    )

    assert result["status"] == "approved"

    row = db.get_invoice_status("gmail-approve-success")
    assert row["state"] == "closed"  # M1: posted_to_erp now transitions to closed
    assert row["erp_reference"] == "BILL-123"


def test_send_for_approval_promotes_received_items_before_needs_approval(service, db, monkeypatch):
    item = _create_ap_item(db, gmail_id="gmail-route-from-received", state="received")

    monkeypatch.setattr(
        db,
        "get_slack_thread",
        lambda _gmail_id: {
            "channel_id": "C-APPROVALS",
            "thread_ts": "1710000000.999",
            "thread_id": "1710000000.999",
        },
    )

    invoice = InvoiceData(
        gmail_id="gmail-route-from-received",
        subject="Invoice",
        sender="billing@vendor.test",
        vendor_name="Vendor Test",
        amount=125.0,
        confidence=0.91,
        invoice_number="INV-GMAIL-ROUTE-FROM-RECEIVED",
    )

    result = asyncio.run(service._send_for_approval(invoice))

    assert result["status"] == "pending_approval"
    assert result["existing"] is True

    row = db.get_invoice_status("gmail-route-from-received")
    assert row["state"] == "needs_approval"

    transitions = _transition_pairs(db, item["id"])
    assert ("received", "validated") in transitions
    assert ("validated", "needs_approval") in transitions


def test_reject_invoice_updates_slack_thread_with_gmail_id(service, db, monkeypatch):
    item = _create_ap_item(db, gmail_id="gmail-reject-slack", state="needs_approval")

    calls = []

    def _spy_update_slack_thread_status(*args, **kwargs):
        calls.append({"args": args, "kwargs": dict(kwargs)})
        return True

    monkeypatch.setattr(
        db,
        "get_slack_thread",
        lambda _gmail_id: {
            "channel_id": "C-APPROVALS",
            "thread_ts": "1710000000.123",
            "thread_id": "1710000000.123",
        },
    )
    monkeypatch.setattr(db, "update_slack_thread_status", _spy_update_slack_thread_status)

    async def _noop_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_update_slack_rejected", _noop_async)

    result = asyncio.run(
        service.reject_invoice(
            gmail_id="gmail-reject-slack",
            reason="duplicate invoice",
            rejected_by="approver@example.com",
            source_channel="slack",
            source_channel_id="C-APPROVALS",
            source_message_ref="1710000000.123",
        )
    )

    assert result["status"] == "rejected"
    assert calls, "Expected reject flow to update Slack thread metadata"
    assert calls[0]["kwargs"]["gmail_id"] == "gmail-reject-slack"
    assert calls[0]["kwargs"]["thread_id"] == "1710000000.123"

    row = db.get_invoice_status("gmail-reject-slack")
    assert row is not None
    assert row["state"] == "rejected"

    approvals = db.list_approvals_by_item(item["id"])
    assert approvals
    assert str(approvals[0].get("status")) == "rejected"


def test_reject_invoice_records_vendor_feedback_summary(service, db, monkeypatch):
    _create_ap_item(
        db,
        gmail_id="gmail-reject-feedback",
        state="needs_approval",
        metadata={"ap_decision_recommendation": "approve"},
    )

    async def _noop_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_update_slack_rejected", _noop_async)
    monkeypatch.setattr(db, "get_slack_thread", lambda _gmail_id: None)

    result = asyncio.run(
        service.reject_invoice(
            gmail_id="gmail-reject-feedback",
            reason="duplicate invoice",
            rejected_by="approver@example.com",
            source_channel="slack",
            source_channel_id="C-APPROVALS",
            source_message_ref="1710000000.555",
        )
    )
    assert result["status"] == "rejected"

    summary = db.get_vendor_decision_feedback_summary("default", "Vendor Test")
    assert summary["total_feedback"] >= 1
    assert summary["reject_count"] >= 1
    assert summary["override_count"] >= 1  # human rejected when agent rec was approve
    assert summary["reject_after_approve_count"] >= 1


def test_request_budget_adjustment_records_vendor_feedback_summary(service, db, monkeypatch):
    _create_ap_item(
        db,
        gmail_id="gmail-request-info-feedback",
        state="needs_approval",
        metadata={"ap_decision_recommendation": "approve"},
    )
    monkeypatch.setattr(db, "get_slack_thread", lambda _gmail_id: None)

    async def _noop_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_update_slack_budget_adjustment_requested", _noop_async)

    result = asyncio.run(
        service.request_budget_adjustment(
            gmail_id="gmail-request-info-feedback",
            requested_by="approver@example.com",
            reason="missing_po_number",
            source_channel="slack",
            source_channel_id="C-APPROVALS",
            source_message_ref="1710000000.777",
        )
    )
    assert result["status"] == "needs_info"
    row = db.get_invoice_status("gmail-request-info-feedback")
    assert row is not None
    assert row["state"] == "needs_info"

    summary = db.get_vendor_decision_feedback_summary("default", "Vendor Test")
    assert summary["total_feedback"] >= 1
    assert summary["request_info_count"] >= 1
    assert summary["request_info_after_approve_count"] >= 1


def test_request_budget_adjustment_creates_followup_metadata_and_draft(service, db, monkeypatch):
    item = _create_ap_item(
        db,
        gmail_id="gmail-request-info-followup",
        state="needs_approval",
        metadata={"needs_info_question": "Please provide PO number."},
    )
    monkeypatch.setattr(db, "get_slack_thread", lambda _gmail_id: None)

    async def _noop_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_update_slack_budget_adjustment_requested", _noop_async)

    class _FakeGmailClient:
        def __init__(self, user_id):
            self.user_id = user_id

        async def ensure_authenticated(self):
            return True

        async def create_draft(self, **_kwargs):
            return "draft-followup-test-1"

    monkeypatch.setattr("clearledgr.services.gmail_api.GmailAPIClient", _FakeGmailClient)

    result = asyncio.run(
        service.request_budget_adjustment(
            gmail_id="gmail-request-info-followup",
            requested_by="approver@example.com",
            reason="need_po_number",
            source_channel="slack",
            source_channel_id="C-APPROVALS",
            source_message_ref="1710000000.888",
        )
    )
    assert result["status"] == "needs_info"

    row = db.get_ap_item(item["id"])
    assert row is not None
    metadata = json.loads(row["metadata"]) if isinstance(row.get("metadata"), str) else dict(row.get("metadata") or {})
    assert metadata.get("needs_info_draft_id") == "draft-followup-test-1"
    assert metadata.get("followup_attempt_count") == 1
    assert metadata.get("followup_next_action") == "await_vendor_response"
    assert metadata.get("followup_last_sent_at")
    assert metadata.get("followup_sla_due_at")

    events = db.list_ap_audit_events(item["id"])
    assert any(ev.get("event_type") == "vendor_followup_draft_prepared" for ev in events)


def test_approve_invoice_records_vendor_outcome_and_feedback(service, db, monkeypatch):
    _create_ap_item(
        db,
        gmail_id="gmail-approve-feedback",
        state="needs_approval",
        metadata={"ap_decision_recommendation": "escalate"},
    )
    monkeypatch.setattr(service, "_load_budget_context_from_invoice_row", lambda _row: [])
    monkeypatch.setattr(service, "_check_po_exception_block", lambda _row: {"blocked": False, "exceptions": []})

    async def _fake_post(_invoice, **_kwargs):
        return {"status": "success", "bill_id": "BILL-FEEDBACK-1", "vendor_id": "VEN-1"}

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    result = asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-approve-feedback",
            approved_by="approver@example.com",
            source_channel="slack",
            source_channel_id="C-APPROVALS",
            source_message_ref="1710000000.888",
        )
    )
    assert result["status"] == "approved"

    summary = db.get_vendor_decision_feedback_summary("default", "Vendor Test")
    assert summary["total_feedback"] >= 1
    assert summary["approve_count"] >= 1
    assert summary["override_count"] >= 1  # human approved while recommendation was escalate

    profile = db.get_vendor_profile("default", "Vendor Test")
    assert profile is not None
    assert profile["invoice_count"] >= 1


def test_approve_invoice_failure_transitions_to_failed_post(service, db, monkeypatch):
    item = _create_ap_item(db, gmail_id="gmail-approve-fail", state="needs_approval")

    monkeypatch.setattr(service, "_load_budget_context_from_invoice_row", lambda _row: [])
    monkeypatch.setattr(service, "_check_po_exception_block", lambda _row: {"blocked": False, "exceptions": []})

    async def _fake_post(_invoice, **_kwargs):
        return {"status": "error", "reason": "api_timeout"}

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    result = asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-approve-fail",
            approved_by="approver@example.com",
        )
    )

    assert result["status"] == "error"

    row = db.get_invoice_status("gmail-approve-fail")
    assert row["state"] == "failed_post"
    assert row["last_error"] == "api_timeout"

    transitions = _transition_pairs(db, item["id"])
    assert ("needs_approval", "approved") in transitions
    assert ("approved", "ready_to_post") in transitions
    assert ("ready_to_post", "failed_post") in transitions
    assert ("ready_to_post", "posted_to_erp") not in transitions


def test_approve_invoice_duplicate_decision_idempotency_key_does_not_repost(service, db, monkeypatch):
    item = _create_ap_item(db, gmail_id="gmail-approve-idem", state="needs_approval")
    monkeypatch.setattr(service, "_load_budget_context_from_invoice_row", lambda _row: [])
    monkeypatch.setattr(service, "_check_po_exception_block", lambda _row: {"blocked": False, "exceptions": []})

    calls = {"post": 0}

    async def _fake_post(_invoice, **_kwargs):
        calls["post"] += 1
        return {"status": "success", "bill_id": "BILL-IDEM-1"}

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    first = asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-approve-idem",
            approved_by="approver@example.com",
            source_channel="slack",
            source_channel_id="C1",
            source_message_ref="1711111111.111",
            decision_idempotency_key="decision-key-1",
        )
    )
    assert first["status"] == "approved"
    assert first["decision_idempotency_key"] == "decision-key-1"

    second = asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-approve-idem",
            approved_by="approver@example.com",
            source_channel="slack",
            source_channel_id="C1",
            source_message_ref="1711111111.111",
            decision_idempotency_key="decision-key-1",
        )
    )
    assert second["status"] == "approved"
    assert second["duplicate_action"] is True
    assert second["decision_idempotency_key"] == "decision-key-1"
    assert calls["post"] == 1

    approval = db.get_approval_by_decision_key(item["id"], "decision-key-1")
    assert approval is not None
    assert approval["status"] == "approved"


def test_approve_invoice_blocks_low_confidence_critical_fields_without_override(service, db, monkeypatch):
    _create_ap_item(
        db,
        gmail_id="gmail-confidence-block",
        state="needs_approval",
        confidence=0.99,
        metadata={
            "field_confidences": {
                "vendor": 0.80,
                "amount": 0.99,
                "invoice_number": 0.99,
                "due_date": 0.99,
            }
        },
    )

    monkeypatch.setattr(service, "_load_budget_context_from_invoice_row", lambda _row: [])
    monkeypatch.setattr(service, "_check_po_exception_block", lambda _row: {"blocked": False, "exceptions": []})

    async def _should_not_post(_invoice):
        raise AssertionError("ERP post should not execute when confidence review is required")

    monkeypatch.setattr(service, "_post_to_erp", _should_not_post)

    result = asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-confidence-block",
            approved_by="approver@example.com",
        )
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "field_review_required"
    assert result["requires_field_review"] is True
    assert any(b["field"] == "vendor" for b in result["confidence_blockers"])

    row = db.get_invoice_status("gmail-confidence-block")
    assert row["state"] == "needs_approval"


def test_approve_invoice_blocks_confidence_override_even_with_justification(service, db, monkeypatch):
    item = _create_ap_item(
        db,
        gmail_id="gmail-confidence-override",
        state="needs_approval",
        confidence=0.99,
        metadata={
            "field_confidences": {
                "vendor": 0.80,
                "amount": 0.99,
                "invoice_number": 0.99,
                "due_date": 0.99,
            }
        },
    )

    monkeypatch.setattr(service, "_load_budget_context_from_invoice_row", lambda _row: [])
    monkeypatch.setattr(service, "_check_po_exception_block", lambda _row: {"blocked": False, "exceptions": []})

    async def _should_not_post(_invoice, **_kwargs):
        raise AssertionError("ERP post should not execute when confidence review is required")

    monkeypatch.setattr(service, "_post_to_erp", _should_not_post)

    result = asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-confidence-override",
            approved_by="approver@example.com",
            allow_confidence_override=True,
            override_justification="Reviewed invoice number and amount manually",
        )
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "field_review_required"

    row = db.get_invoice_status("gmail-confidence-override")
    assert row["state"] == "needs_approval"

    stored = db.get_ap_item(item["id"])
    metadata_raw = stored.get("metadata")
    metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else dict(metadata_raw or {})
    assert metadata["requires_field_review"] is True

    audit_events = db.list_ap_audit_events(item["id"])
    override_events = [e for e in audit_events if e.get("event_type") == "confidence_override_used"]
    assert override_events == []


def test_approve_invoice_blocks_blocking_source_conflicts(service, db, monkeypatch):
    _create_ap_item(
        db,
        gmail_id="gmail-source-conflict-block",
        state="needs_approval",
        confidence=0.99,
        metadata={
            "field_confidences": {
                "vendor": 0.99,
                "amount": 0.99,
                "invoice_number": 0.99,
                "due_date": 0.99,
            },
            "requires_field_review": True,
            "source_conflicts": [
                {
                    "field": "amount",
                    "blocking": True,
                    "reason": "source_value_mismatch",
                    "preferred_source": "attachment",
                    "values": {"email": 400.0, "attachment": 440.0},
                }
            ],
        },
    )

    monkeypatch.setattr(service, "_load_budget_context_from_invoice_row", lambda _row: [])
    monkeypatch.setattr(service, "_check_po_exception_block", lambda _row: {"blocked": False, "exceptions": []})

    async def _should_not_post(_invoice, **_kwargs):
        raise AssertionError("ERP post should not execute when blocking source conflicts are present")

    monkeypatch.setattr(service, "_post_to_erp", _should_not_post)

    result = asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-source-conflict-block",
            approved_by="approver@example.com",
            allow_confidence_override=True,
            override_justification="Attempted override should still fail closed",
        )
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "field_review_required"
    assert result["exception_code"] == "field_conflict"
    assert result["blocked_fields"] == ["amount"]
    assert result["blocking_source_conflicts"][0]["field"] == "amount"

    row = db.get_invoice_status("gmail-source-conflict-block")
    assert row["state"] == "needs_approval"


def test_auto_approve_success_transitions_through_ready_to_post(service, db, monkeypatch):
    item = _create_ap_item(db, gmail_id="gmail-auto-success", state="validated")

    async def _fake_post(_invoice, **_kwargs):
        return {"status": "success", "bill_id": "BILL-AUTO-1", "vendor_id": "VEN-1"}

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    invoice = InvoiceData(
        gmail_id="gmail-auto-success",
        subject="Invoice",
        sender="billing@vendor.test",
        vendor_name="Vendor Test",
        amount=125.0,
        confidence=0.99,
    )

    result = asyncio.run(service._auto_approve_and_post(invoice))
    assert result["status"] == "auto_approved"

    row = db.get_invoice_status("gmail-auto-success")
    assert row["state"] == "closed"  # M1: posted_to_erp now transitions to closed
    assert row["erp_reference"] == "BILL-AUTO-1"

    transitions = _transition_pairs(db, item["id"])
    assert ("validated", "needs_approval") in transitions
    assert ("needs_approval", "approved") in transitions
    assert ("approved", "ready_to_post") in transitions
    assert ("ready_to_post", "posted_to_erp") in transitions


def test_auto_approve_blocks_when_field_review_required(service, db, monkeypatch):
    _create_ap_item(
        db,
        gmail_id="gmail-auto-blocked",
        state="validated",
        confidence=0.99,
        metadata={
            "requires_field_review": True,
            "field_confidences": {
                "vendor": 0.80,
                "amount": 0.99,
                "invoice_number": 0.99,
                "due_date": 0.99,
            },
        },
    )

    async def _should_not_post(_invoice, **_kwargs):
        raise AssertionError("ERP post should not execute when auto-approve is blocked for field review")

    monkeypatch.setattr(service, "_post_to_erp", _should_not_post)

    invoice = InvoiceData(
        gmail_id="gmail-auto-blocked",
        subject="Invoice",
        sender="billing@vendor.test",
        vendor_name="Vendor Test",
        amount=125.0,
        confidence=0.99,
    )

    result = asyncio.run(service._auto_approve_and_post(invoice))
    assert result["status"] == "blocked"
    assert result["reason"] == "field_review_required"

    row = db.get_invoice_status("gmail-auto-blocked")
    assert row["state"] == "validated"


def test_auto_approve_failure_transitions_to_failed_post(service, db, monkeypatch):
    item = _create_ap_item(db, gmail_id="gmail-auto-fail", state="validated")

    async def _fake_post(_invoice, **_kwargs):
        return {"status": "error", "reason": "erp_unavailable"}

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    invoice = InvoiceData(
        gmail_id="gmail-auto-fail",
        subject="Invoice",
        sender="billing@vendor.test",
        vendor_name="Vendor Test",
        amount=140.0,
        confidence=0.99,
    )

    result = asyncio.run(service._auto_approve_and_post(invoice))
    assert result["status"] == "error"

    row = db.get_invoice_status("gmail-auto-fail")
    assert row["state"] == "failed_post"
    assert row["last_error"] == "erp_unavailable"

    transitions = _transition_pairs(db, item["id"])
    assert ("validated", "needs_approval") in transitions
    assert ("needs_approval", "approved") in transitions
    assert ("approved", "ready_to_post") in transitions
    assert ("ready_to_post", "failed_post") in transitions

# ---------------------------------------------------------------------------
# Gap #5 — resume_workflow crash-recovery tests
# ---------------------------------------------------------------------------

def test_resume_workflow_from_failed_post_succeeds(service, db, monkeypatch):
    """resume_workflow: failed_post → ready_to_post → posted_to_erp on success."""
    item = _create_ap_item(db, gmail_id="gmail-resume-ok", state="failed_post")

    async def _fake_post(_invoice, **_kwargs):
        return {"status": "success", "bill_id": "BILL-RESUME-1", "vendor_id": "VEN-R1"}

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    result = asyncio.run(service.resume_workflow(item["id"]))

    assert result["status"] == "recovered"
    assert result["erp_reference"] == "BILL-RESUME-1"

    row = db.get_invoice_status("gmail-resume-ok")
    assert row["state"] == "closed"  # M1: posted_to_erp now transitions to closed
    assert row["erp_reference"] == "BILL-RESUME-1"

    audit_events = db.list_ap_audit_events(item["id"])
    event_types = [e["event_type"] for e in audit_events]
    assert "erp_post_resumed" in event_types


def test_resume_workflow_from_ready_to_post_succeeds(service, db, monkeypatch):
    """resume_workflow: ready_to_post → posted_to_erp — no state regression."""
    item = _create_ap_item(db, gmail_id="gmail-resume-rtp", state="ready_to_post")

    async def _fake_post(_invoice, **_kwargs):
        return {"status": "success", "bill_id": "BILL-RTP-1"}

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    result = asyncio.run(service.resume_workflow(item["id"]))

    assert result["status"] == "recovered"
    row = db.get_invoice_status("gmail-resume-rtp")
    assert row["state"] == "closed"  # M1: posted_to_erp now transitions to closed


def test_resume_workflow_blocks_when_field_review_required(service, db, monkeypatch):
    item = _create_ap_item(
        db,
        gmail_id="gmail-resume-blocked",
        state="failed_post",
        metadata={
            "requires_field_review": True,
            "field_confidences": {
                "vendor": 0.80,
                "amount": 0.99,
                "invoice_number": 0.99,
                "due_date": 0.99,
            },
        },
    )

    async def _should_not_post(_invoice, **_kwargs):
        raise AssertionError("ERP post should not execute when resume is blocked for field review")

    monkeypatch.setattr(service, "_post_to_erp", _should_not_post)

    result = asyncio.run(service.resume_workflow(item["id"]))

    assert result["status"] == "blocked"
    assert result["reason"] == "field_review_required"

    row = db.get_invoice_status("gmail-resume-blocked")
    assert row["state"] == "failed_post"


def test_resume_workflow_still_failing_stays_in_failed_post(service, db, monkeypatch):
    """resume_workflow: ERP still down → stays failed_post, returns still_failing."""
    item = _create_ap_item(db, gmail_id="gmail-resume-fail", state="failed_post")

    async def _fake_post(_invoice, **_kwargs):
        return {"status": "error", "reason": "erp_timeout"}

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    result = asyncio.run(service.resume_workflow(item["id"]))

    assert result["status"] == "still_failing"
    assert result["reason"] == "erp_timeout"

    row = db.get_invoice_status("gmail-resume-fail")
    assert row["state"] == "failed_post"


def test_resume_workflow_still_failing_preserves_nonrecoverable_error_code(service, db, monkeypatch):
    item = _create_ap_item(db, gmail_id="gmail-resume-no-erp", state="failed_post")

    async def _fake_post(_invoice, **_kwargs):
        return {
            "status": "error",
            "reason": "ERP is not properly configured",
            "error_code": "erp_not_configured",
        }

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    result = asyncio.run(service.resume_workflow(item["id"]))

    assert result["status"] == "still_failing"
    assert result["error_code"] == "erp_not_configured"
    assert result["recoverability"]["recoverable"] is False

    row = db.get_invoice_status("gmail-resume-no-erp")
    assert row["state"] == "failed_post"
    assert row["exception_code"] == "erp_not_configured"


def test_resume_workflow_retry_storm_recovers_once_without_double_post(service, db, monkeypatch):
    """Repeated retries through outage/recovery should post exactly once."""
    item = _create_ap_item(db, gmail_id="gmail-resume-storm", state="failed_post")
    calls = {"count": 0}

    async def _fake_post(_invoice, **_kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            return {"status": "error", "reason": "erp_timeout"}
        return {"status": "success", "bill_id": "BILL-STORM-1"}

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    first = asyncio.run(service.resume_workflow(item["id"]))
    second = asyncio.run(service.resume_workflow(item["id"]))
    third = asyncio.run(service.resume_workflow(item["id"]))
    after_recovery = asyncio.run(service.resume_workflow(item["id"]))

    assert first["status"] == "still_failing"
    assert second["status"] == "still_failing"
    assert third["status"] == "recovered"
    assert after_recovery["status"] == "not_resumable"
    assert calls["count"] == 3

    row = db.get_invoice_status("gmail-resume-storm")
    assert row["state"] == "closed"  # M1: posted_to_erp now transitions to closed
    assert row["erp_reference"] == "BILL-STORM-1"


def test_resume_workflow_not_resumable_for_posted_state(service, db, monkeypatch):
    """resume_workflow: posted_to_erp is terminal — returns not_resumable."""
    item = _create_ap_item(db, gmail_id="gmail-resume-posted", state="posted_to_erp")

    result = asyncio.run(service.resume_workflow(item["id"]))

    assert result["status"] == "not_resumable"
    assert result["current_state"] == "posted_to_erp"


def test_resume_workflow_not_resumable_for_needs_approval(service, db, monkeypatch):
    """resume_workflow: needs_approval requires human decision — not resumable."""
    item = _create_ap_item(db, gmail_id="gmail-resume-needs-appr", state="needs_approval")

    result = asyncio.run(service.resume_workflow(item["id"]))

    assert result["status"] == "not_resumable"


def test_approve_invoice_failure_enqueues_retry_job(service, db, monkeypatch):
    """Failed ERP post should create an erp_post_retry job for background recovery."""
    item = _create_ap_item(db, gmail_id="gmail-retry-enqueue", state="needs_approval")

    monkeypatch.setattr(service, "_load_budget_context_from_invoice_row", lambda _row: [])
    monkeypatch.setattr(service, "_check_po_exception_block", lambda _row: {"blocked": False, "exceptions": []})

    async def _fake_post(_invoice, **_kwargs):
        return {"status": "error", "reason": "connector_timeout"}

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-retry-enqueue",
            approved_by="approver@example.com",
        )
    )

    row = db.get_invoice_status("gmail-retry-enqueue")
    assert row["state"] == "failed_post"

    # The retry job must be enqueued (connector_timeout is a recoverable token)
    jobs = db.list_agent_retry_jobs("default", ap_item_id=item["id"], status="pending")
    assert jobs, "Expected a pending erp_post_retry job after failed_post"
    assert jobs[0]["job_type"] == "erp_post_retry"
    assert jobs[0]["ap_item_id"] == item["id"]


def test_approve_invoice_connector_failure_preserves_exception_code_and_skips_retry_job(service, db, monkeypatch):
    item = _create_ap_item(db, gmail_id="gmail-no-erp", state="needs_approval")

    monkeypatch.setattr(service, "_load_budget_context_from_invoice_row", lambda _row: [])
    monkeypatch.setattr(service, "_check_po_exception_block", lambda _row: {"blocked": False, "exceptions": []})

    async def _fake_post(_invoice, **_kwargs):
        return {
            "status": "error",
            "reason": "No ERP connected for organization",
            "error_code": "erp_not_connected",
        }

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    result = asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-no-erp",
            approved_by="approver@example.com",
        )
    )

    assert result["status"] == "error"

    row = db.get_invoice_status("gmail-no-erp")
    assert row["state"] == "failed_post"
    assert row["exception_code"] == "erp_not_connected"
    assert row["last_error"] == "No ERP connected for organization"

    jobs = db.list_agent_retry_jobs("default", ap_item_id=item["id"], status="pending")
    assert jobs == []


def test_enqueue_erp_post_retry_is_idempotent(service, db, monkeypatch):
    """Second _enqueue_erp_post_retry call for same ap_item_id is a no-op."""
    item = _create_ap_item(db, gmail_id="gmail-retry-idem", state="failed_post")

    service._enqueue_erp_post_retry(
        ap_item_id=item["id"],
        gmail_id="gmail-retry-idem",
    )
    service._enqueue_erp_post_retry(
        ap_item_id=item["id"],
        gmail_id="gmail-retry-idem",
    )

    jobs = db.list_agent_retry_jobs("default", ap_item_id=item["id"])
    assert len(jobs) == 1, "Idempotency key must prevent duplicate retry jobs"


def test_batch_route_low_risk_precheck_allows_clean_validated_item(service):
    row = {
        "id": "ap-route-ok",
        "state": "validated",
        "document_type": "invoice",
        "metadata": {},
    }
    precheck = service.evaluate_batch_route_low_risk_for_approval(row)
    assert precheck["eligible"] is True
    assert precheck["reason_codes"] == []


def test_batch_route_low_risk_precheck_blocks_policy_risk_fields(service):
    row = {
        "id": "ap-route-blocked",
        "state": "validated",
        "document_type": "invoice",
        "exception_code": "policy_validation_failed",
        "requires_field_review": True,
        "confidence_blockers": [{"field": "vendor"}],
        "metadata": {
            "source_conflicts": [
                {
                    "field": "amount",
                    "blocking": True,
                    "reason": "source_value_mismatch",
                    "preferred_source": "attachment",
                    "values": {"email": 400.0, "attachment": 440.0},
                }
            ],
        },
    }
    precheck = service.evaluate_batch_route_low_risk_for_approval(row)
    assert precheck["eligible"] is False
    assert "exception_present" in precheck["reason_codes"]
    assert "field_review_required" in precheck["reason_codes"]
    assert "blocking_source_conflicts" in precheck["reason_codes"]
    assert precheck["blocked_fields"] == ["vendor", "amount"]


def test_batch_retry_recoverable_precheck_allows_transient_failed_post(service):
    row = {
        "id": "ap-retry-ok",
        "state": "failed_post",
        "last_error": "connector timeout",
        "metadata": {},
    }
    precheck = service.evaluate_batch_retry_recoverable_failure(row)
    assert precheck["eligible"] is True
    assert precheck["recoverability"]["recoverable"] is True


def test_batch_retry_recoverable_precheck_blocks_non_recoverable_failed_post(service):
    row = {
        "id": "ap-retry-blocked",
        "state": "failed_post",
        "last_error": "duplicate invoice already posted",
        "metadata": {},
    }
    precheck = service.evaluate_batch_retry_recoverable_failure(row)
    assert precheck["eligible"] is False
    assert precheck["recoverability"]["recoverable"] is False


def test_batch_retry_recoverable_precheck_blocks_connector_configuration_failure(service):
    row = {
        "id": "ap-retry-no-erp",
        "state": "failed_post",
        "last_error": "No ERP connected for organization",
        "exception_code": "erp_not_connected",
        "metadata": {},
    }
    precheck = service.evaluate_batch_retry_recoverable_failure(row)
    assert precheck["eligible"] is False
    assert precheck["recoverability"]["recoverable"] is False
    assert precheck["recoverability"]["reason"].startswith("non_recoverable_")


def test_batch_retry_recoverable_precheck_blocks_field_review_required(service):
    row = {
        "id": "ap-retry-field-review",
        "state": "failed_post",
        "last_error": "connector timeout",
        "metadata": {
            "requires_field_review": True,
            "source_conflicts": [
                {
                    "field": "amount",
                    "blocking": True,
                    "reason": "source_value_mismatch",
                    "preferred_source": "attachment",
                    "values": {"email": 400.0, "attachment": 440.0},
                }
            ],
        },
    }
    precheck = service.evaluate_batch_retry_recoverable_failure(row)
    assert precheck["eligible"] is False
    assert "field_review_required" in precheck["reason_codes"]
    assert "blocking_source_conflicts" in precheck["reason_codes"]
    assert precheck["exception_code"] == "field_conflict"
