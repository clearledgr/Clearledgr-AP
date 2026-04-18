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


class _RuntimeStub:
    def __init__(self):
        self.calls = []

    async def execute_intent(self, intent, payload=None, *, idempotency_key=None):
        self.calls.append((intent, {"payload": dict(payload or {}), "idempotency_key": idempotency_key}))
        if intent == "approve_invoice":
            return {"status": "approved", "result": {"status": "approved", "erp_result": {"bill_id": "BILL-1"}}}
        if intent == "request_info":
            return {"status": "needs_info"}
        if intent == "reject_invoice":
            return {"status": "rejected"}
        return {"status": "error", "reason": "unsupported_intent"}


class _InvoiceStub:
    """Minimal invoice-like object for routing tests."""
    def __init__(self, amount=100.0, gl_code="", department="", vendor_name="", entity_code=""):
        self.amount = amount
        self.gl_code = gl_code
        self.department = department
        self.vendor_name = vendor_name
        self.entity_code = entity_code
        self.vendor_intelligence = {}


def test_approval_routing_passes_invoice_context_for_gl_filtering(db):
    from clearledgr.services.invoice_workflow import InvoiceWorkflowService

    svc = InvoiceWorkflowService(organization_id="default")
    svc.db = db
    svc._settings = {
        "approval_thresholds": [
            {
                "min_amount": 0,
                "max_amount": None,
                "approver_channel": "#engineering-approvals",
                "approvers": ["eng-lead@company.com"],
                "gl_codes": ["6100"],
                "approval_type": "any",
            },
            {
                "min_amount": 0,
                "max_amount": None,
                "approver_channel": "#finance-approvals",
                "approvers": [],
                "approval_type": "any",
            },
        ],
    }

    # Invoice with matching GL code → routes to engineering channel
    invoice = _InvoiceStub(amount=500.0, gl_code="6100")
    result = svc.get_approval_target_for_amount(500.0, invoice=invoice)
    assert result["channel"] == "#engineering-approvals"
    assert result["approvers"] == ["eng-lead@company.com"]
    assert result["matched_rule"]["gl_codes"] == ["6100"]

    # Invoice with different GL code → skips first rule, matches second
    invoice2 = _InvoiceStub(amount=500.0, gl_code="7200")
    result2 = svc.get_approval_target_for_amount(500.0, invoice=invoice2)
    assert result2["channel"] == "#finance-approvals"


def test_approval_routing_preserves_structured_approver_targets(db):
    from clearledgr.services.invoice_workflow import InvoiceWorkflowService

    svc = InvoiceWorkflowService(organization_id="default")
    svc.db = db
    svc._settings = {
        "approval_thresholds": [
            {
                "min_amount": 0,
                "max_amount": None,
                "approver_channel": "#finance-approvals",
                "approver_targets": [
                    {
                        "email": "jane@company.com",
                        "display_name": "Jane Approver",
                        "slack_user_id": "U123",
                        "slack_resolution": "resolved",
                    }
                ],
                "approval_type": "all",
            }
        ],
    }

    result = svc.get_approval_target_for_amount(750.0, invoice=_InvoiceStub(amount=750.0))

    assert result["channel"] == "#finance-approvals"
    assert result["approval_type"] == "all"
    assert result["approvers"] == ["jane@company.com"]
    assert result["approver_targets"][0]["display_name"] == "Jane Approver"
    assert result["approver_targets"][0]["slack_user_id"] == "U123"


def test_approval_routing_falls_back_to_amount_only_without_invoice(db):
    from clearledgr.services.invoice_workflow import InvoiceWorkflowService

    svc = InvoiceWorkflowService(organization_id="default")
    svc.db = db
    svc._settings = {
        "approval_thresholds": [
            {
                "min_amount": 0,
                "max_amount": 1000,
                "approver_channel": "#small-invoices",
                "gl_codes": ["6100"],
            },
            {
                "min_amount": 0,
                "max_amount": 1000,
                "approver_channel": "#default-channel",
            },
        ],
    }

    # No invoice passed → GL filter is permissive (no invoice GL to reject),
    # so first rule matches on amount alone
    result = svc.get_approval_target_for_amount(500.0)
    assert result["channel"] == "#small-invoices"

    # With invoice that has a non-matching GL → first rule skipped, second matches
    invoice = _InvoiceStub(amount=500.0, gl_code="9999")
    result2 = svc.get_approval_target_for_amount(500.0, invoice=invoice)
    assert result2["channel"] == "#default-channel"


def test_approval_routing_vendor_and_department_filters(db):
    from clearledgr.services.invoice_workflow import InvoiceWorkflowService

    svc = InvoiceWorkflowService(organization_id="default")
    svc.db = db
    svc._settings = {
        "approval_thresholds": [
            {
                "min_amount": 0,
                "max_amount": None,
                "approver_channel": "#aws-approvals",
                "vendors": ["Amazon Web Services"],
                "departments": ["engineering"],
            },
            {
                "min_amount": 0,
                "max_amount": None,
                "approver_channel": "#general",
            },
        ],
    }

    # Matching vendor + department
    invoice = _InvoiceStub(amount=1000.0, vendor_name="Amazon Web Services", department="Engineering")
    result = svc.get_approval_target_for_amount(1000.0, invoice=invoice)
    assert result["channel"] == "#aws-approvals"

    # Wrong department → falls through
    invoice2 = _InvoiceStub(amount=1000.0, vendor_name="Amazon Web Services", department="Marketing")
    result2 = svc.get_approval_target_for_amount(1000.0, invoice=invoice2)
    assert result2["channel"] == "#general"


def test_segregation_of_duties_blocks_submitter_from_approving():
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        check_segregation_of_duties,
        resolve_action_precedence,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-sod-1",
        run_id="run-sod-1",
        action="approve",
        actor_id="operator-1",
        actor_display="Operator One",
        reason=None,
        source_channel="slack",
        source_channel_id="C1",
        source_message_ref="msg-1",
        request_ts=str(int(time.time())),
        idempotency_key="idem-sod-1",
        gmail_id="thread-sod-1",
        organization_id="default",
    )

    # Submitter matches approver — should block
    ap_item = {"id": "ap-sod-1", "state": "needs_approval", "user_id": "operator-1"}
    assert check_segregation_of_duties(action, ap_item) == "segregation_of_duties_violation"

    result = resolve_action_precedence(action, ap_item)
    assert result.status == "blocked"
    assert result.reason == "segregation_of_duties_violation"
    assert not result.should_dispatch


def test_segregation_of_duties_allows_different_approver():
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        check_segregation_of_duties,
        resolve_action_precedence,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-sod-2",
        run_id="run-sod-2",
        action="approve",
        actor_id="manager-1",
        actor_display="Manager One",
        reason=None,
        source_channel="slack",
        source_channel_id="C1",
        source_message_ref="msg-2",
        request_ts=str(int(time.time())),
        idempotency_key="idem-sod-2",
        gmail_id="thread-sod-2",
        organization_id="default",
    )

    # Different submitter — should allow
    ap_item = {"id": "ap-sod-2", "state": "needs_approval", "user_id": "operator-1"}
    assert check_segregation_of_duties(action, ap_item) is None

    result = resolve_action_precedence(action, ap_item)
    assert result.status == "dispatch"
    assert result.should_dispatch


def test_segregation_of_duties_allows_rejection_by_submitter():
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        check_segregation_of_duties,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-sod-3",
        run_id="run-sod-3",
        action="reject",
        actor_id="operator-1",
        actor_display="Operator One",
        reason="rejected_in_slack",
        source_channel="slack",
        source_channel_id="C1",
        source_message_ref="msg-3",
        request_ts=str(int(time.time())),
        idempotency_key="idem-sod-3",
        gmail_id="thread-sod-3",
        organization_id="default",
    )

    # Reject by same person — allowed (doesn't release funds)
    ap_item = {"id": "ap-sod-3", "state": "needs_approval", "user_id": "operator-1"}
    assert check_segregation_of_duties(action, ap_item) is None


def test_segregation_of_duties_case_insensitive():
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        check_segregation_of_duties,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-sod-4",
        run_id="run-sod-4",
        action="approve",
        actor_id="Operator-1",
        actor_display="Operator One",
        reason=None,
        source_channel="teams",
        source_channel_id="C1",
        source_message_ref="msg-4",
        request_ts=str(int(time.time())),
        idempotency_key="idem-sod-4",
        gmail_id="thread-sod-4",
        organization_id="default",
    )

    ap_item = {"id": "ap-sod-4", "state": "needs_approval", "user_id": "operator-1"}
    assert check_segregation_of_duties(action, ap_item) == "segregation_of_duties_violation"


def test_approver_authorization_blocks_unauthorized_actor():
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        check_approver_authorization,
        resolve_action_precedence,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-auth-1",
        run_id="run-auth-1",
        action="approve",
        actor_id="U_RANDOM",
        actor_display="Random Person",
        reason=None,
        source_channel="slack",
        source_channel_id="C1",
        source_message_ref="msg-1",
        request_ts=str(int(time.time())),
        idempotency_key="idem-auth-1",
        gmail_id="thread-auth-1",
        organization_id="default",
        actor_email="random@company.com",
    )

    # Named approvers exist but actor is not in the list
    approvers = ["jane@company.com", "bob@company.com"]
    assert check_approver_authorization(action, approvers) == "not_authorized_approver"

    ap_item = {"id": "ap-auth-1", "state": "needs_approval", "user_id": "someone_else"}
    result = resolve_action_precedence(action, ap_item, pending_step_approvers=approvers)
    assert result.status == "blocked"
    assert result.reason == "not_authorized_approver"


def test_approver_authorization_allows_named_approver():
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        check_approver_authorization,
        resolve_action_precedence,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-auth-2",
        run_id="run-auth-2",
        action="approve",
        actor_id="U_JANE",
        actor_display="Jane",
        reason=None,
        source_channel="slack",
        source_channel_id="C1",
        source_message_ref="msg-2",
        request_ts=str(int(time.time())),
        idempotency_key="idem-auth-2",
        gmail_id="thread-auth-2",
        organization_id="default",
        actor_email="jane@company.com",
    )

    approvers = ["jane@company.com", "bob@company.com"]
    assert check_approver_authorization(action, approvers) is None

    ap_item = {"id": "ap-auth-2", "state": "needs_approval", "user_id": "someone_else"}
    result = resolve_action_precedence(action, ap_item, pending_step_approvers=approvers)
    assert result.status == "dispatch"


def test_approver_authorization_allows_anyone_when_no_named_approvers():
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        check_approver_authorization,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-auth-3",
        run_id="run-auth-3",
        action="approve",
        actor_id="U_ANYONE",
        actor_display="Anyone",
        reason=None,
        source_channel="slack",
        source_channel_id="C1",
        source_message_ref="msg-3",
        request_ts=str(int(time.time())),
        idempotency_key="idem-auth-3",
        gmail_id="thread-auth-3",
        organization_id="default",
        actor_email="anyone@company.com",
    )

    # Empty approvers list → open approval
    assert check_approver_authorization(action, []) is None
    assert check_approver_authorization(action, None) is None


def test_approver_authorization_case_insensitive():
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        check_approver_authorization,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-auth-4",
        run_id="run-auth-4",
        action="approve",
        actor_id="U_BOB",
        actor_display="Bob",
        reason=None,
        source_channel="teams",
        source_channel_id="C1",
        source_message_ref="msg-4",
        request_ts=str(int(time.time())),
        idempotency_key="idem-auth-4",
        gmail_id="thread-auth-4",
        organization_id="default",
        actor_email="Bob@Company.com",
    )

    approvers = ["bob@company.com"]
    assert check_approver_authorization(action, approvers) is None


def test_approver_authorization_allows_rejection_by_non_approver():
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        check_approver_authorization,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-auth-5",
        run_id="run-auth-5",
        action="reject",
        actor_id="U_RANDOM",
        actor_display="Random",
        reason="rejected_in_slack",
        source_channel="slack",
        source_channel_id="C1",
        source_message_ref="msg-5",
        request_ts=str(int(time.time())),
        idempotency_key="idem-auth-5",
        gmail_id="thread-auth-5",
        organization_id="default",
        actor_email="random@company.com",
    )

    # Rejection is always allowed even if not a named approver
    approvers = ["jane@company.com"]
    assert check_approver_authorization(action, approvers) is None


def test_approver_authorization_teams_actor_id_is_email():
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        check_approver_authorization,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-auth-6",
        run_id="run-auth-6",
        action="approve",
        actor_id="jane@company.com",
        actor_display="Jane",
        reason=None,
        source_channel="teams",
        source_channel_id="C1",
        source_message_ref="msg-6",
        request_ts=str(int(time.time())),
        idempotency_key="idem-auth-6",
        gmail_id="thread-auth-6",
        organization_id="default",
        actor_email=None,  # No resolved email — but actor_id IS the email
    )

    approvers = ["jane@company.com"]
    assert check_approver_authorization(action, approvers) is None


def test_sod_checked_before_authorization():
    """SoD blocks before authorization check — submitter can't approve even if named."""
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        resolve_action_precedence,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-auth-7",
        run_id="run-auth-7",
        action="approve",
        actor_id="operator-1",
        actor_display="Operator",
        reason=None,
        source_channel="slack",
        source_channel_id="C1",
        source_message_ref="msg-7",
        request_ts=str(int(time.time())),
        idempotency_key="idem-auth-7",
        gmail_id="thread-auth-7",
        organization_id="default",
        actor_email="operator@company.com",
    )

    # operator-1 is BOTH the submitter AND a named approver
    ap_item = {"id": "ap-auth-7", "state": "needs_approval", "user_id": "operator-1"}
    approvers = ["operator@company.com"]

    result = resolve_action_precedence(action, ap_item, pending_step_approvers=approvers)
    # SoD should block BEFORE authorization is checked
    assert result.status == "blocked"
    assert result.reason == "segregation_of_duties_violation"


def test_approval_action_precedence_prefers_duplicate_before_stale():
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        resolve_action_precedence,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-1",
        run_id="run-1",
        action="approve",
        actor_id="approver-1",
        actor_display="Approver",
        reason=None,
        source_channel="slack",
        source_channel_id="channel-1",
        source_message_ref="msg-1",
        request_ts=str(int(time.time()) - 90_000),
        idempotency_key="idem-1",
        gmail_id="thread-1",
        organization_id="default",
    )

    result = resolve_action_precedence(
        action,
        {"id": "ap-1", "state": "needs_approval"},
        already_processed=True,
    )

    assert result.status == "duplicate"
    assert result.reason == "duplicate_callback"


def test_approval_action_precedence_marks_superseded_states_as_stale():
    from clearledgr.core.approval_action_contract import (
        NormalizedApprovalAction,
        resolve_action_precedence,
    )

    action = NormalizedApprovalAction(
        ap_item_id="ap-2",
        run_id="run-2",
        action="approve",
        actor_id="approver-2",
        actor_display="Approver",
        reason=None,
        source_channel="teams",
        source_channel_id="channel-2",
        source_message_ref="msg-2",
        request_ts=str(int(time.time())),
        idempotency_key="idem-2",
        gmail_id="thread-2",
        organization_id="default",
    )

    result = resolve_action_precedence(
        action,
        {"id": "ap-2", "state": "approved"},
    )

    assert result.status == "stale"
    assert result.reason == "superseded_by_state_approved"


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
    assert "Raised by Clearledgr" in body_text
    assert "Open in Gmail" in body_text

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
    assert "Recommended decision" in standard_text
    assert "What happens next" in standard_text
    assert "Raised by Clearledgr" in standard_context_text
    assert "Open in Gmail" in standard_context_text

    mentioned_blocks = svc._build_approval_blocks(
        invoice,
        extra_context={
            "budget": {"status": "healthy", "requires_decision": False},
            "approval_mentions": ["<@U123>"],
            "approval_assignee_labels": ["approver@company.com"],
        },
    )
    mention_text = " ".join(
        str(block.get("text", {}).get("text") or "")
        for block in mentioned_blocks
        if isinstance(block, dict) and isinstance(block.get("text"), dict)
    )
    assert "Approvers for this request" in mention_text
    assert "<@U123>" in mention_text

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
    assert "Recommended decision" in budget_text


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
    assert "posts this invoice automatically" in next_lines[0]
    assert "missing policy or evidence details" in next_lines[1]
    assert "duplicate risk is confirmed" in next_lines[2]
    assert "request more information before posting" in recommended


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
    assert "records the justification and then posts this invoice to erp" in next_lines[0]
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
    original_append = db.append_audit_event

    def _spy_append(payload):
        captured.append(dict(payload))
        return original_append(payload)

    monkeypatch.setattr(db, "append_audit_event", _spy_append)

    async def _raise_invalid(_request):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    monkeypatch.setattr("clearledgr.api.slack_invoices._require_slack_signature", _raise_invalid)
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
    original_append = db.append_audit_event

    def _spy_append(payload):
        captured.append(dict(payload))
        return original_append(payload)

    monkeypatch.setattr(db, "append_audit_event", _spy_append)

    async def _return_body(request):
        return await request.body()

    monkeypatch.setattr("clearledgr.api.slack_invoices._require_slack_signature", _return_body)

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
    runtime = _RuntimeStub()

    async def _return_body(request):
        return await request.body()

    monkeypatch.setattr("clearledgr.api.slack_invoices._require_slack_signature", _return_body)
    async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
        return await runtime.execute_intent(intent, payload, idempotency_key=idempotency_key)

    monkeypatch.setattr("clearledgr.api.slack_invoices._dispatch_runtime_intent", _runtime_execute)

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

    assert [name for name, _kwargs in runtime.calls] == ["request_info"]
    call_kwargs = runtime.calls[0][1]
    assert call_kwargs["payload"]["ap_item_id"] == item["id"]
    assert call_kwargs["payload"]["reason"] == "budget_adjustment_requested_in_slack"
    assert call_kwargs["payload"]["source_channel"] == "slack"
    assert call_kwargs["idempotency_key"]
    assert call_kwargs["payload"]["correlation_id"] == "corr-slack-1"

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


def test_slack_interactive_response_url_path_acks_fast_and_completes_in_background(monkeypatch, client, db):
    item = _create_ap_item(db, gmail_id="thread-slack-response-url")
    db.update_ap_item(item["id"], metadata={"correlation_id": "corr-slack-response-url"})
    runtime = _RuntimeStub()
    posted = []

    async def _return_body(request):
        return await request.body()

    async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
        return await runtime.execute_intent(intent, payload, idempotency_key=idempotency_key)

    async def _fake_post(response_url, payload, *, organization_id="default", ap_item_id=None):
        posted.append({
            "response_url": response_url,
            "payload": dict(payload or {}),
            "organization_id": organization_id,
            "ap_item_id": ap_item_id,
        })
        return True

    monkeypatch.setattr("clearledgr.api.slack_invoices._require_slack_signature", _return_body)
    monkeypatch.setattr("clearledgr.api.slack_invoices._dispatch_runtime_intent", _runtime_execute)
    monkeypatch.setattr("clearledgr.api.slack_invoices._post_to_response_url", _fake_post)

    payload = {
        "callback_id": "run-slack-response-url-1",
        "response_url": "https://hooks.slack.com/actions/response-url",
        "user": {"id": "U1", "username": "approver"},
        "channel": {"id": "C1"},
        "message": {"ts": "1711111111.123"},
        "actions": [{"action_id": "request_info_thread-slack-response-url", "value": "thread-slack-response-url"}],
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
    assert "processing this action" in response.json()["text"].lower()
    assert [name for name, _kwargs in runtime.calls] == ["request_info"]
    assert posted
    assert posted[0]["response_url"] == "https://hooks.slack.com/actions/response-url"
    assert "Request for info recorded" in posted[0]["payload"]["text"]
    assert posted[0]["payload"]["replace_original"] is False

    events = db.list_ap_audit_events(item["id"])
    event_types = [e.get("event_type") for e in events]
    assert "channel_action_received" in event_types
    assert "channel_action_processed" in event_types


def test_slack_interactive_forwards_resolved_actor_identity(monkeypatch, client, db):
    item = _create_ap_item(db, gmail_id="thread-slack-actor-identity")
    db.update_ap_item(item["id"], metadata={"correlation_id": "corr-slack-actor-identity"})
    runtime = _RuntimeStub()

    async def _return_body(request):
        return await request.body()

    async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
        return await runtime.execute_intent(intent, payload, idempotency_key=idempotency_key)

    async def _fake_identity(_db, slack_user_id, organization_id):
        assert slack_user_id == "U_MO"
        assert organization_id == "default"
        return {
            "email": "mo@clearledgr.com",
            "display_name": "Mo Mbalam",
            "slack_user_id": "U_MO",
        }

    monkeypatch.setattr("clearledgr.api.slack_invoices._require_slack_signature", _return_body)
    monkeypatch.setattr("clearledgr.api.slack_invoices._dispatch_runtime_intent", _runtime_execute)
    monkeypatch.setattr("clearledgr.api.slack_invoices._resolve_slack_actor_identity", _fake_identity)

    payload = {
        "callback_id": "run-slack-actor-identity-1",
        "user": {"id": "U_MO", "username": "mo"},
        "channel": {"id": "C1"},
        "message": {"ts": "1711111111.777"},
        "actions": [{"action_id": "approve_invoice_thread-slack-actor-identity", "value": "thread-slack-actor-identity"}],
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
    assert [name for name, _kwargs in runtime.calls] == ["approve_invoice"]
    dispatched = runtime.calls[0][1]["payload"]
    assert dispatched["actor_id"] == "U_MO"
    assert dispatched["actor_display"] == "Mo Mbalam"
    assert dispatched["actor_email"] == "mo@clearledgr.com"
    assert dispatched["actor_identity"]["platform"] == "slack"
    assert dispatched["actor_identity"]["platform_user_id"] == "U_MO"
    assert dispatched["actor_identity"]["display_name"] == "Mo Mbalam"
    assert dispatched["actor_identity"]["email"] == "mo@clearledgr.com"

    events = db.list_ap_audit_events(item["id"])
    received = next(event for event in events if event.get("event_type") == "channel_action_received")
    payload_json = received.get("payload_json") or {}
    action = payload_json.get("action") or {}
    assert action["actor_email"] == "mo@clearledgr.com"
    assert action["actor_display"] == "Mo Mbalam"


def test_legacy_slack_interactions_alias_routes_to_same_handler(monkeypatch, client, db):
    item = _create_ap_item(db, gmail_id="thread-slack-legacy-alias")
    db.update_ap_item(item["id"], metadata={"correlation_id": "corr-slack-legacy-alias"})
    runtime = _RuntimeStub()

    async def _return_body(request):
        return await request.body()

    async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
        return await runtime.execute_intent(intent, payload, idempotency_key=idempotency_key)

    async def _fake_post(response_url, payload, *, organization_id="default", ap_item_id=None):
        return True

    monkeypatch.setattr("clearledgr.api.slack_invoices._require_slack_signature", _return_body)
    monkeypatch.setattr("clearledgr.api.slack_invoices._dispatch_runtime_intent", _runtime_execute)
    monkeypatch.setattr("clearledgr.api.slack_invoices._post_to_response_url", _fake_post)

    payload = {
        "callback_id": "run-slack-legacy-alias-1",
        "response_url": "https://hooks.slack.com/actions/response-url",
        "user": {"id": "U1", "username": "approver"},
        "channel": {"id": "C1"},
        "message": {"ts": "1711111111.123"},
        "actions": [{"action_id": "request_info_thread-slack-legacy-alias", "value": "thread-slack-legacy-alias"}],
    }
    response = client.post(
        "/slack/interactions",
        content=_slack_form_body(payload),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "x-slack-request-timestamp": str(int(time.time())),
        },
    )

    assert response.status_code == 200
    assert "processing this action" in response.json()["text"].lower()
    assert [name for name, _kwargs in runtime.calls] == ["request_info"]


def test_slack_interactive_duplicate_storm_is_idempotent(monkeypatch, client, db):
    """Burst duplicate callbacks should execute workflow once and audit duplicates."""
    item = _create_ap_item(db, gmail_id="thread-slack-storm")
    db.update_ap_item(item["id"], metadata={"correlation_id": "corr-slack-storm"})
    runtime = _RuntimeStub()

    async def _return_body(request):
        return await request.body()

    monkeypatch.setattr("clearledgr.api.slack_invoices._require_slack_signature", _return_body)
    async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
        return await runtime.execute_intent(intent, payload, idempotency_key=idempotency_key)

    monkeypatch.setattr("clearledgr.api.slack_invoices._dispatch_runtime_intent", _runtime_execute)

    payload = {
        "callback_id": "run-slack-storm-1",
        "user": {"id": "U1", "username": "approver"},
        "channel": {"id": "C1"},
        "message": {"ts": "1712222222.000"},
        "actions": [{"action_id": "request_info_thread-slack-storm", "value": "thread-slack-storm"}],
    }
    body = _slack_form_body(payload)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "x-slack-request-timestamp": str(int(time.time())),
    }

    responses = [client.post("/slack/invoices/interactive", content=body, headers=headers) for _ in range(25)]

    assert all(resp.status_code == 200 for resp in responses)
    duplicate_responses = [
        resp for resp in responses if "Duplicate action ignored" in str(resp.json().get("text") or "")
    ]
    assert len(duplicate_responses) >= 24
    assert [name for name, _kwargs in runtime.calls] == ["request_info"]

    events = db.list_ap_audit_events(item["id"])
    processed = [e for e in events if e.get("event_type") == "channel_action_processed"]
    duplicates = [e for e in events if e.get("event_type") == "channel_action_duplicate"]
    assert len(processed) == 1
    assert len(duplicates) >= 1


def test_slack_interactive_approve_surfaces_field_review_block(monkeypatch, client, db):
    item = _create_ap_item(db, gmail_id="thread-slack-field-review")
    db.update_ap_item(item["id"], metadata={"correlation_id": "corr-slack-field-review"})

    async def _return_body(request):
        return await request.body()

    async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
        assert intent == "approve_invoice"
        return {
            "status": "blocked",
            "reason": "field_review_required",
            "result": {"status": "blocked", "reason": "field_review_required"},
        }

    monkeypatch.setattr("clearledgr.api.slack_invoices._require_slack_signature", _return_body)
    monkeypatch.setattr("clearledgr.api.slack_invoices._dispatch_runtime_intent", _runtime_execute)

    payload = {
        "callback_id": "run-slack-field-review-1",
        "user": {"id": "U1", "username": "approver"},
        "channel": {"id": "C1"},
        "message": {"ts": "1713333333.000"},
        "actions": [{"action_id": "approve_invoice_thread-slack-field-review", "value": "thread-slack-field-review"}],
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
    assert "Field review required before posting" in response.json()["text"]


def test_teams_interactive_requires_authorization_and_audits(monkeypatch, client, db):
    monkeypatch.setenv("FEATURE_TEAMS_ENABLED", "true")
    captured = []
    original_append = db.append_audit_event

    def _spy_append(payload):
        captured.append(dict(payload))
        return original_append(payload)

    monkeypatch.setattr(db, "append_audit_event", _spy_append)

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
    monkeypatch.setenv("FEATURE_TEAMS_ENABLED", "true")
    captured = []
    original_append = db.append_audit_event

    def _spy_append(payload):
        captured.append(dict(payload))
        return original_append(payload)

    monkeypatch.setattr(db, "append_audit_event", _spy_append)
    monkeypatch.setattr(
        "clearledgr.api.teams_invoices._verify_teams_token",
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
    monkeypatch.setenv("FEATURE_TEAMS_ENABLED", "true")
    item = _create_ap_item(db, gmail_id="thread-teams-1")
    runtime = _RuntimeStub()
    monkeypatch.setattr(
        "clearledgr.api.teams_invoices._verify_teams_token",
        lambda _auth: {"appid": "bot-test", "iat": int(time.time())},
    )
    async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
        return await runtime.execute_intent(intent, payload, idempotency_key=idempotency_key)

    monkeypatch.setattr("clearledgr.api.teams_invoices._dispatch_runtime_intent", _runtime_execute)

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

    assert [name for name, _kwargs in runtime.calls] == ["request_info"]
    kwargs = runtime.calls[0][1]
    assert kwargs["payload"]["ap_item_id"] == item["id"]
    assert kwargs["payload"]["reason"] == "budget_adjustment_requested_in_teams"
    assert kwargs["payload"]["source_channel"] == "teams"
    assert kwargs["idempotency_key"]

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


def test_teams_interactive_marks_superseded_approval_cards_as_stale(monkeypatch, client, db):
    monkeypatch.setenv("FEATURE_TEAMS_ENABLED", "true")
    item = _create_ap_item(db, gmail_id="thread-teams-superseded")
    db.update_ap_item(item["id"], state="approved")

    monkeypatch.setattr(
        "clearledgr.api.teams_invoices._verify_teams_token",
        lambda _auth: {"appid": "bot-test", "iat": int(time.time())},
    )

    headers = {"Authorization": "Bearer test-token"}
    payload = {
        "action": "approve",
        "email_id": "thread-teams-superseded",
        "organization_id": "default",
        "actor": "approver@clearledgr.com",
        "conversation_id": "19:finance",
        "message_id": "msg-superseded",
        "request_ts": str(int(time.time())),
    }

    response = client.post("/teams/invoices/interactive", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "stale"
    assert response.json()["reason"] == "superseded_by_state_approved"

    events = db.list_ap_audit_events(item["id"])
    stale_events = [event for event in events if event.get("event_type") == "channel_action_stale"]
    assert stale_events
    stale_payload = stale_events[-1].get("payload_json") or {}
    assert stale_payload.get("reason") == "superseded_by_state_approved"


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
    runtime = _RuntimeStub()

    async def _return_body(request):
        return await request.body()

    monkeypatch.setattr("clearledgr.api.slack_invoices._require_slack_signature", _return_body)
    async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
        return await runtime.execute_intent(intent, payload, idempotency_key=idempotency_key)

    monkeypatch.setattr("clearledgr.api.slack_invoices._dispatch_runtime_intent", _runtime_execute)

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
    assert runtime.calls == []

    events = db.list_ap_audit_events(item["id"])
    event_types = [e.get("event_type") for e in events]
    assert "channel_action_blocked" in event_types


def test_teams_interactive_blocks_actions_when_rollout_control_disables_teams(monkeypatch, client, db):
    # §12 / §6.8 — feature-flag enables the interactive route so the
    # rollout-control path can be exercised (the rollout control is a
    # per-org toggle separate from the V1 surface gate).
    monkeypatch.setenv("FEATURE_TEAMS_ENABLED", "true")
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
    runtime = _RuntimeStub()
    monkeypatch.setattr(
        "clearledgr.api.teams_invoices._verify_teams_token",
        lambda _auth: {"appid": "bot-test", "iat": int(time.time())},
    )
    async def _runtime_execute(self, intent, payload=None, *, idempotency_key=None):
        return await runtime.execute_intent(intent, payload, idempotency_key=idempotency_key)

    monkeypatch.setattr("clearledgr.api.teams_invoices._dispatch_runtime_intent", _runtime_execute)

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
    assert runtime.calls == []

    events = db.list_ap_audit_events(item["id"])
    event_types = [e.get("event_type") for e in events]
    assert "channel_action_blocked" in event_types
