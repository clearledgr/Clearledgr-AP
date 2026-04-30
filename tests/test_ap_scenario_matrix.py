from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from clearledgr.core import database as db_module
from clearledgr.core.ap_states import IllegalTransitionError
from clearledgr.services.approval_delegation import DelegationService
from clearledgr.services.gmail_extension_support import build_needs_info_draft_payload
from clearledgr.services.policy_compliance import PolicyAction, PolicyComplianceService


SCENARIO_ORG = "scenario-org"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization(
        organization_id=SCENARIO_ORG,
        organization_name="Scenario Matrix Org",
        domain="scenario.test",
    )
    yield inst
    db_path = tmp_path / "ap-scenario-matrix.db"
    if db_path.exists():
        os.unlink(db_path)


def _create_item(db, item_id: str, **overrides):
    payload = {
        "id": item_id,
        "organization_id": SCENARIO_ORG,
        "invoice_key": f"inv-{item_id.lower()}",
        "thread_id": f"thread-{item_id.lower()}",
        "message_id": f"msg-{item_id.lower()}",
        "subject": f"Invoice {item_id}",
        "sender": "billing@example.com",
        "vendor_name": "Acme Supply",
        "amount": 1250.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "received",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {},
    }
    payload.update(overrides)
    return db.create_ap_item(payload)


def test_simple_invoice_happy_path_closes(db):
    item = _create_item(db, "SCENARIO-HAPPY-1", amount=240.0)
    ap_id = item["id"]

    for state, actor_type, actor_id in [
        ("validated", "system", "parser"),
        ("needs_approval", "system", "router"),
        ("approved", "user", "approver@scenario.test"),
        ("ready_to_post", "system", "workflow"),
        ("posted_to_erp", "system", "erp-adapter"),
        ("closed", "system", "auto-closer"),
    ]:
        kwargs = {"state": state, "_actor_type": actor_type, "_actor_id": actor_id}
        if state == "approved":
            kwargs["approved_by"] = actor_id
            kwargs["approved_at"] = datetime.now(timezone.utc).isoformat()
        if state == "posted_to_erp":
            kwargs["erp_reference"] = "NS-SCENARIO-1"
            kwargs["erp_posted_at"] = datetime.now(timezone.utc).isoformat()
        db.update_ap_item(ap_id, **kwargs)

    assert db.get_ap_item(ap_id)["state"] == "closed"


def test_missing_po_policy_routes_invoice_to_review(db):
    service = PolicyComplianceService(SCENARIO_ORG)
    result = service.check(
        {
            "vendor_name": "Acme Supply",
            "amount": 1800.0,
            "currency": "USD",
            "po_number": "",
            "vendor_intelligence": {"known_vendor": True},
        }
    )

    actions = {action.value for action in result.required_actions}
    assert PolicyAction.FLAG_FOR_REVIEW.value in actions
    assert any(violation.policy_id == "po_required" for violation in result.violations)


def test_new_vendor_requires_approval(db):
    service = PolicyComplianceService(SCENARIO_ORG)
    result = service.check(
        {
            "vendor_name": "Brand New Vendor Ltd",
            "amount": 640.0,
            "currency": "USD",
            "vendor_intelligence": {"known_vendor": False},
            "is_first_invoice": True,
        }
    )

    actions = {action.value for action in result.required_actions}
    assert PolicyAction.REQUIRE_APPROVAL.value in actions
    assert "manager" in result.required_approvers
    assert any(violation.policy_id == "new_vendor" for violation in result.violations)


def test_high_amount_requires_multi_approval(db):
    service = PolicyComplianceService(SCENARIO_ORG)
    result = service.check(
        {
            "vendor_name": "Advisory Partner LLP",
            "amount": 15000.0,
            "currency": "USD",
            "vendor_intelligence": {"known_vendor": True},
        }
    )

    actions = {action.value for action in result.required_actions}
    assert PolicyAction.REQUIRE_MULTI_APPROVAL.value in actions
    assert {"director", "cfo"}.issubset(set(result.required_approvers))
    assert any(violation.policy_id == "amt_10000" for violation in result.violations)


def test_request_info_loop_returns_item_to_validated(db):
    item = _create_item(db, "SCENARIO-INFO-1")
    ap_id = item["id"]

    db.update_ap_item(ap_id, state="validated", _actor_type="system", _actor_id="parser")
    db.update_ap_item(ap_id, state="needs_info", _actor_type="system", _actor_id="validator")
    assert db.get_ap_item(ap_id)["state"] == "needs_info"

    db.update_ap_item(ap_id, state="validated", _actor_type="user", _actor_id="submitter@scenario.test")
    assert db.get_ap_item(ap_id)["state"] == "validated"


def test_rejection_path_is_terminal(db):
    item = _create_item(db, "SCENARIO-REJECT-1")
    ap_id = item["id"]

    db.update_ap_item(ap_id, state="validated", _actor_type="system", _actor_id="parser")
    db.update_ap_item(ap_id, state="needs_approval", _actor_type="system", _actor_id="router")
    db.update_ap_item(
        ap_id,
        state="rejected",
        rejected_by="finance-lead@scenario.test",
        rejected_at=datetime.now(timezone.utc).isoformat(),
        rejection_reason="Duplicate invoice",
        _actor_type="user",
        _actor_id="finance-lead@scenario.test",
    )

    assert db.get_ap_item(ap_id)["state"] == "rejected"
    with pytest.raises(IllegalTransitionError):
        db.update_ap_item(ap_id, state="approved", _actor_type="user", _actor_id="override@scenario.test")


def test_erp_retry_failure_recovery_posts_successfully(db):
    item = _create_item(db, "SCENARIO-RECOVER-1", amount=4200.0)
    ap_id = item["id"]

    for state in ("validated", "needs_approval", "approved", "ready_to_post"):
        kwargs = {"state": state, "_actor_type": "system", "_actor_id": "runtime"}
        if state == "approved":
            kwargs["approved_by"] = "approver@scenario.test"
            kwargs["approved_at"] = datetime.now(timezone.utc).isoformat()
        db.update_ap_item(ap_id, **kwargs)

    db.update_ap_item(
        ap_id,
        state="failed_post",
        last_error="ERP timeout",
        _actor_type="system",
        _actor_id="erp-adapter",
    )
    db.update_ap_item(
        ap_id,
        state="ready_to_post",
        last_error=None,
        _actor_type="user",
        _actor_id="ops@scenario.test",
    )
    db.update_ap_item(
        ap_id,
        state="posted_to_erp",
        erp_reference="NS-RECOVER-1",
        erp_posted_at=datetime.now(timezone.utc).isoformat(),
        _actor_type="system",
        _actor_id="erp-adapter",
    )

    posted = db.get_ap_item(ap_id)
    assert posted["state"] == "posted_to_erp"
    assert posted["erp_reference"] == "NS-RECOVER-1"


def test_delegated_approver_swaps_to_delegate(db):
    service = DelegationService(SCENARIO_ORG)
    service.create_rule(
        delegator_id="mgr-1",
        delegator_email="manager@scenario.test",
        delegate_id="mgr-2",
        delegate_email="delegate@scenario.test",
        reason="OOO",
    )

    resolved = service.resolve_approvers(["manager@scenario.test", "cfo@scenario.test"])
    assert resolved == ["delegate@scenario.test", "cfo@scenario.test"]


def test_vendor_followup_loop_builds_email_from_needs_info_state(db):
    item = _create_item(
        db,
        "SCENARIO-FOLLOWUP-1",
        state="needs_info",
        sender="school-billing@example.org",
        subject="Invoice for tuition",
        invoice_number="SCH-1001",
        exception_code="missing_po",
    )

    payload = build_needs_info_draft_payload(
        ap_item_id=item["id"],
        ap_item=item,
    )

    assert payload["to"] == "school-billing@example.org"
    assert payload["subject"] == "Re: Invoice for tuition"
    assert "Please provide a valid Purchase Order (PO) number" in payload["body"]
