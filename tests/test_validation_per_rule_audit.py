"""Phase 1, Gap 2 — per-rule validation audit trail.

The deterministic validation gate now records *every rule* it
evaluates — passes included, not just failures — and emits a single
``validation_gate_evaluated`` audit_event with the full per-rule
breakdown. Without this, an auditor opening the audit chain can only
prove which rules failed, never which rules ran-and-passed. That's
the difference between a system-of-record audit trail and a
coordinator's failure log.

These tests confirm the contract end-to-end against the real
Postgres test fixture so the audit_event actually lands.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from clearledgr.core.database import get_db
from clearledgr.services.invoice_models import InvoiceData
from clearledgr.services.invoice_workflow import InvoiceWorkflowService


def _make_workflow(organization_id: str = "default") -> InvoiceWorkflowService:
    return InvoiceWorkflowService(organization_id=organization_id)


def _seed_ap_item_for_validation(
    db, *, vendor_name: str = "Acme Co", amount: float = 1000.0,
    invoice_number: str = "INV-100", state: str = "received",
) -> str:
    payload = {
        "invoice_key": f"{vendor_name}::{invoice_number}",
        "thread_id": f"thread-{invoice_number}",
        "vendor_name": vendor_name,
        "amount": amount,
        "currency": "USD",
        "invoice_number": invoice_number,
        "subject": f"Bill from {vendor_name}",
        "sender": f"{vendor_name.lower().replace(' ', '')}@example.com",
        "state": state,
        "organization_id": "default",
    }
    result = db.create_ap_item(payload)
    return result["id"]


def _latest_validation_audit(db, ap_item_id: str):
    rows = db.list_ap_audit_events(ap_item_id, limit=20, order="desc")
    for row in rows or []:
        if row.get("event_type") == "validation_gate_evaluated":
            return row
    return None


@pytest.mark.asyncio
async def test_validation_gate_evaluated_audit_records_every_rule(postgres_test_db):
    """A clean invoice through the gate should yield ``rule_results``
    with at least one entry per rule section, and the audit_event
    payload mirrors the gate's rule_results."""
    db = get_db()
    db.initialize()
    ap_item_id = _seed_ap_item_for_validation(db)

    invoice = InvoiceData(
        gmail_id=f"thread-INV-100",
        subject="Bill from Acme Co",
        sender="acme@example.com",
        vendor_name="Acme Co",
        amount=1000.0,
        currency="USD",
        invoice_number="INV-100",
        confidence=0.95,
        organization_id="default",
        user_id="test-user",
    )

    workflow = _make_workflow()
    gate = await workflow._evaluate_deterministic_validation(invoice)

    rule_results = gate.get("rule_results")
    assert isinstance(rule_results, list) and rule_results, (
        "validation gate did not produce rule_results"
    )

    # Every rule_results entry must have the canonical RuleResult shape.
    for entry in rule_results:
        assert "rule_id" in entry
        assert entry.get("verdict") in {"pass", "fail", "skip"}
        assert "evaluated_at" in entry

    rule_ids = {entry["rule_id"] for entry in rule_results}
    # Spot-check a representative subset of the 22 rule sections —
    # if any of these go missing we've lost coverage.
    expected_rules = {
        "field_presence",
        "amount_cross_validation",
        "currency_consistency",
        "duplicate_invoice",
        "confidence_gate",
        "fraud_controls",
    }
    missing = expected_rules - rule_ids
    assert not missing, f"validation gate dropped rule audit for: {missing}"

    audit_row = _latest_validation_audit(db, ap_item_id)
    assert audit_row is not None, (
        "validation_gate_evaluated audit_event not emitted"
    )
    payload = audit_row.get("payload_json") or audit_row.get("metadata") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    audit_rules = payload.get("rules") or payload.get("metadata", {}).get("rules")
    if audit_rules is None and isinstance(payload, dict):
        # Some audit-store impls nest under metadata.
        audit_rules = (payload.get("metadata") or {}).get("rules")
    assert isinstance(audit_rules, list) and audit_rules, (
        "audit_event did not carry per-rule breakdown"
    )
    assert len(audit_rules) == len(rule_results)


@pytest.mark.asyncio
async def test_validation_gate_records_failed_rule_with_evidence(postgres_test_db):
    """An invoice missing a required field should produce a fail
    rule_result with the new reason rows attached as evidence."""
    db = get_db()
    db.initialize()
    ap_item_id = _seed_ap_item_for_validation(db, vendor_name="", invoice_number="INV-200")

    invoice = InvoiceData(
        gmail_id="thread-INV-200",
        subject="Bill missing vendor",
        sender="unknown@example.com",
        vendor_name="",  # missing required field — triggers field_presence fail
        amount=500.0,
        currency="USD",
        invoice_number="INV-200",
        confidence=0.5,
        organization_id="default",
        user_id="test-user",
    )

    workflow = _make_workflow()
    gate = await workflow._evaluate_deterministic_validation(invoice)

    rule_results = gate.get("rule_results") or []
    field_presence = next(
        (r for r in rule_results if r["rule_id"] == "field_presence"),
        None,
    )
    assert field_presence is not None
    assert field_presence["verdict"] == "fail"
    assert field_presence.get("evidence", {}).get("reasons"), (
        "failed rule must attach the failing reason rows as evidence"
    )


@pytest.mark.asyncio
async def test_validation_gate_blocks_sanctions_blocked_vendor(postgres_test_db):
    """A vendor whose rolled-up ``sanctions_status`` is 'blocked' must
    fail the new sanctions_status gate with severity=error and
    reason_code 'vendor_sanctions_blocked'. Defence-in-depth: the
    pre-payment gate is the last line; the validation gate is the
    first."""
    db = get_db()
    db.initialize()
    vendor_name = "Sanctioned Co"
    ap_item_id = _seed_ap_item_for_validation(
        db, vendor_name=vendor_name, invoice_number="INV-S1",
    )

    # Seed the vendor profile with a 'blocked' disposition. The same
    # path the screening service uses to roll up the rolled-up
    # disposition column.
    db.upsert_vendor_profile(
        organization_id="default",
        vendor_name=vendor_name,
        sanctions_status="blocked",
        last_sanctions_check_at=datetime.now(timezone.utc).isoformat(),
    )

    invoice = InvoiceData(
        gmail_id="thread-INV-S1",
        subject=f"Bill from {vendor_name}",
        sender="sanctioned@example.com",
        vendor_name=vendor_name,
        amount=1000.0,
        currency="USD",
        invoice_number="INV-S1",
        confidence=0.95,
        organization_id="default",
        user_id="test-user",
    )

    workflow = _make_workflow()
    gate = await workflow._evaluate_deterministic_validation(invoice)

    reason_codes = gate.get("reason_codes") or []
    assert "vendor_sanctions_blocked" in reason_codes, (
        f"expected vendor_sanctions_blocked in reason_codes, got {reason_codes}"
    )

    sanctions_rule = next(
        (r for r in gate.get("rule_results") or []
         if r["rule_id"] == "sanctions_status"),
        None,
    )
    assert sanctions_rule is not None
    assert sanctions_rule["verdict"] == "fail"
