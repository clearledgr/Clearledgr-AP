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


class _RecurringDetector:
    def __init__(self, *, is_recurring: bool = False, auto_approve: bool = False):
        self.is_recurring = is_recurring
        self.auto_approve = auto_approve

    def analyze_invoice(self, **_kwargs):
        return {
            "is_recurring": self.is_recurring,
            "auto_approve": self.auto_approve,
            "alerts": [],
            "pattern": {"invoice_count": 1},
        }


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
    monkeypatch.setattr(
        "clearledgr.services.recurring_detection.get_recurring_detector",
        lambda _org: _RecurringDetector(is_recurring=False, auto_approve=False),
    )

    monkeypatch.setattr(
        service,
        "_evaluate_deterministic_validation",
        lambda _invoice: {
            "passed": True,
            "checked_at": "2026-02-25T00:00:00+00:00",
            "reason_codes": [],
            "reasons": [],
            "policy_compliance": {},
            "po_match_result": None,
            "budget_impact": [],
            "budget": {"status": "healthy"},
        },
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
    monkeypatch.setattr(
        "clearledgr.services.recurring_detection.get_recurring_detector",
        lambda _org: _RecurringDetector(is_recurring=False, auto_approve=False),
    )
    monkeypatch.setattr(
        service,
        "_evaluate_deterministic_validation",
        lambda _invoice: {
            "passed": True,
            "checked_at": "2026-02-25T00:00:00+00:00",
            "reason_codes": [],
            "reasons": [],
            "policy_compliance": {},
            "po_match_result": None,
            "budget_impact": [],
            "budget": {"status": "healthy"},
        },
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
        "clearledgr.services.recurring_detection.get_recurring_detector",
        lambda _org: _RecurringDetector(is_recurring=False, auto_approve=False),
    )
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
    assert row["state"] == "posted_to_erp"
    assert row["erp_reference"] == "BILL-123"


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

    assert result["status"] == "needs_field_review"
    assert result["requires_field_review"] is True
    assert any(b["field"] == "vendor" for b in result["confidence_blockers"])

    row = db.get_invoice_status("gmail-confidence-block")
    assert row["state"] == "needs_approval"


def test_approve_invoice_allows_confidence_override_with_justification_and_audits(service, db, monkeypatch):
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

    async def _fake_post(_invoice, **_kwargs):
        return {"status": "success", "bill_id": "BILL-CONF-1"}

    monkeypatch.setattr(service, "_post_to_erp", _fake_post)

    result = asyncio.run(
        service.approve_invoice(
            gmail_id="gmail-confidence-override",
            approved_by="approver@example.com",
            allow_confidence_override=True,
            override_justification="Reviewed invoice number and amount manually",
        )
    )

    assert result["status"] == "approved"
    assert result["confidence_override"] is True

    row = db.get_invoice_status("gmail-confidence-override")
    assert row["state"] == "posted_to_erp"

    stored = db.get_ap_item(item["id"])
    metadata_raw = stored.get("metadata")
    metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else dict(metadata_raw or {})
    assert metadata["confidence_override"]["used"] is True
    assert metadata["confidence_override"]["justification"]

    audit_events = db.list_ap_audit_events(item["id"])
    override_events = [e for e in audit_events if e.get("event_type") == "confidence_override_used"]
    assert override_events


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
    assert row["state"] == "posted_to_erp"
    assert row["erp_reference"] == "BILL-AUTO-1"

    transitions = _transition_pairs(db, item["id"])
    assert ("validated", "needs_approval") in transitions
    assert ("needs_approval", "approved") in transitions
    assert ("approved", "ready_to_post") in transitions
    assert ("ready_to_post", "posted_to_erp") in transitions


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
