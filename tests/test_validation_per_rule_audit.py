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


@pytest.mark.asyncio
async def test_validation_gate_records_skip_when_check_raises(postgres_test_db):
    """Phase 1, Gap 2 — silent except-pass fix.

    Before this fix, a rule whose underlying check raised (and the
    exception was swallowed by the rule block's try/except) would be
    recorded as a ``pass`` because no reason rows were added — silently
    lying to the audit trail about whether the rule actually
    evaluated. The fix plumbs the caught exception through to
    ``_record_rule_verdict``, which now promotes the verdict to
    ``skip`` with the exception text as ``skip_reason``.

    Targets ``currency_consistency`` because its block has a single
    outer ``try/except`` around ``self.db.get_vendor_profile``: a DB
    outage propagates to the outer except, so the new skip-promotion
    machinery fires. (Some other rule blocks defensively wrap the
    DB call in an INNER try/except that returns empty data — those
    rules then run on empty data, which is a different failure mode
    that records a non-skip verdict by design.)
    """
    db = get_db()
    db.initialize()
    _seed_ap_item_for_validation(
        db, vendor_name="Boom Vendor", invoice_number="INV-EXC-1",
    )

    invoice = InvoiceData(
        gmail_id="thread-INV-EXC-1",
        subject="Bill from Boom Vendor",
        sender="boom@example.com",
        vendor_name="Boom Vendor",
        amount=1000.0,
        currency="USD",
        invoice_number="INV-EXC-1",
        confidence=0.99,
        organization_id="default",
        user_id="test-user",
    )

    workflow = _make_workflow()
    original = workflow.db.get_vendor_profile

    def _exploding_get_vendor_profile(*args, **kwargs):
        raise RuntimeError("simulated DB outage for gate audit fidelity test")

    workflow.db.get_vendor_profile = _exploding_get_vendor_profile  # type: ignore[assignment]
    try:
        gate = await workflow._evaluate_deterministic_validation(invoice)
    finally:
        workflow.db.get_vendor_profile = original  # type: ignore[assignment]

    rule_results = gate.get("rule_results") or []
    by_id = {r["rule_id"]: r for r in rule_results}

    # currency_consistency has a single outer try/except → skip-promotion fires.
    rule = by_id.get("currency_consistency")
    assert rule is not None, "missing currency_consistency rule_results entry"
    assert rule["verdict"] == "skip", (
        f"currency_consistency: expected 'skip' on caught exception, "
        f"got {rule['verdict']!r} (silent-pass regression)"
    )
    assert rule.get("message"), (
        "currency_consistency: skip verdict must carry skip_reason text"
    )
    # Evidence must capture exception_type so the audit row records
    # *what* failed, not just that something did.
    assert rule.get("evidence", {}).get("exception_type") == "RuntimeError"


@pytest.mark.asyncio
async def test_fuzzy_dedup_window_honours_per_tenant_setting(postgres_test_db):
    """The fuzzy duplicate gate (vendor + amount + window when invoice
    number is missing) reads ``settings_json["dedup"]`` for the
    window length and amount tolerance. Hardcoded 7-day / 2% defaults
    apply when the org hasn't customised. Tightened settings (e.g.
    1 day / 0.5%) must propagate into the gate.
    """
    db = get_db()
    db.initialize()
    org_id = "default"

    # Ensure the org row exists (the postgres_test_db fixture truncates
    # most tables between tests; organization rows aren't auto-seeded).
    if not db.get_organization(org_id):
        db.create_organization(
            organization_id=org_id, name="Default Org",
        )

    # Snapshot existing settings so we can restore at end of test.
    _existing_org = db.get_organization(org_id) or {}
    _existing_settings = _existing_org.get("settings_json")
    if isinstance(_existing_settings, str):
        _existing_settings = json.loads(_existing_settings)
    _existing_settings = _existing_settings or {}

    # Customise the dedup window to 1 day (tighter than the 7-day default).
    new_settings = dict(_existing_settings)
    new_settings["dedup"] = {"fuzzy_window_days": 1, "fuzzy_amount_tolerance": 0.005}
    db.update_organization(org_id, settings_json=new_settings)

    invoice = InvoiceData(
        gmail_id="thread-fuzzy-1",
        subject="Bill from Pulse Vendor",
        sender="pulse@example.com",
        vendor_name="Pulse Vendor",
        amount=750.0,
        currency="USD",
        invoice_number="",  # missing on purpose to trigger fuzzy path
        confidence=0.95,
        organization_id=org_id,
        user_id="test-user",
    )

    workflow = _make_workflow()
    captured_kwargs: list[dict] = []
    original = workflow.db.get_ap_items_by_vendor

    def _capturing_get_ap_items_by_vendor(*args, **kwargs):
        captured_kwargs.append(dict(kwargs))
        # Return empty so we don't trigger the actual reason; we only
        # care that the gate plumbed our 1-day window.
        return []

    workflow.db.get_ap_items_by_vendor = _capturing_get_ap_items_by_vendor  # type: ignore[assignment]
    try:
        await workflow._evaluate_deterministic_validation(invoice)
    finally:
        workflow.db.get_ap_items_by_vendor = original  # type: ignore[assignment]
        # Restore the org settings so this test doesn't leak into others.
        db.update_organization(org_id, settings_json=_existing_settings)

    assert captured_kwargs, "fuzzy dedup branch never invoked get_ap_items_by_vendor"
    # The validation gate also has a velocity check that calls the
    # same DB method with its own (unrelated) days=7 default. Match by
    # the fuzzy-branch's distinctive limit=20 to find the one we care
    # about.
    fuzzy_calls = [c for c in captured_kwargs if c.get("limit") == 20]
    assert fuzzy_calls, (
        f"fuzzy dedup call (limit=20) not in captured kwargs: {captured_kwargs}"
    )
    assert fuzzy_calls[0].get("days") == 1, (
        f"expected days=1 from settings_json[dedup].fuzzy_window_days, "
        f"got days={fuzzy_calls[0].get('days')}"
    )


@pytest.mark.asyncio
async def test_currency_consistency_passes_org_id_first_to_get_vendor_profile(postgres_test_db):
    """Regression for the arg-order swap bug.

    Before this fix, ``_evaluate_deterministic_validation`` called
    ``self.db.get_vendor_profile(invoice.vendor_name, self.organization_id)``
    — but the store signature is ``(organization_id, vendor_name)``.
    The SQL ``WHERE organization_id = %s AND vendor_name = %s`` never
    matched, so the currency_consistency rule silently recorded
    ``pass`` for every invoice regardless of currency mismatch.

    This test wires a vendor profile with EUR default_currency, sends
    a USD invoice, and asserts that ``currency_mismatch`` lands in
    ``reason_codes``. Pre-fix this assertion failed because the
    profile lookup returned None (wrong WHERE bindings).
    """
    db = get_db()
    db.initialize()
    org_id = "default"
    vendor = "EUR Vendor"
    if not db.get_organization(org_id):
        db.create_organization(organization_id=org_id, name="Default Org")
    db.upsert_vendor_profile(
        organization_id=org_id,
        vendor_name=vendor,
        invoice_count=5,
        last_invoice_amount=1000.0,
        avg_invoice_amount=1000.0,
    )
    # Stamp the EUR currency directly on the row (default_currency
    # isn't in the upsert allowlist; write through metadata is fine
    # for this test since the gate reads either default_currency or
    # currency keys off the dict).
    if hasattr(db, "update_ap_item_metadata_merge"):
        # Directly poke the column via a raw update so the gate sees it.
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE vendor_profiles SET metadata = %s "
                "WHERE organization_id = %s AND vendor_name = %s",
                ('{"default_currency": "EUR"}', org_id, vendor),
            )
            conn.commit()

    _seed_ap_item_for_validation(
        db, vendor_name=vendor, invoice_number="INV-CCY-1",
    )

    invoice = InvoiceData(
        gmail_id="thread-INV-CCY-1",
        subject=f"Bill from {vendor}",
        sender="eur@example.com",
        vendor_name=vendor,
        amount=2500.0,
        currency="USD",  # mismatch with the vendor's EUR profile
        invoice_number="INV-CCY-1",
        confidence=0.99,
        organization_id=org_id,
        user_id="test-user",
    )

    workflow = _make_workflow()

    # Capture get_vendor_profile invocations to assert the arg order.
    captured_args: list[tuple] = []
    original = workflow.db.get_vendor_profile

    def _capturing_get_vendor_profile(*args, **kwargs):
        captured_args.append((args, kwargs))
        return original(*args, **kwargs)

    workflow.db.get_vendor_profile = _capturing_get_vendor_profile  # type: ignore[assignment]
    try:
        await workflow._evaluate_deterministic_validation(invoice)
    finally:
        workflow.db.get_vendor_profile = original  # type: ignore[assignment]

    # Find the call from the currency_consistency rule body — it
    # passes positional args. Older buggy code passed (vendor_name,
    # org_id); fixed code passes (org_id, vendor_name).
    currency_calls = [
        a for a in captured_args
        if a[0] and len(a[0]) >= 2 and (a[0][0] == org_id or a[0][1] == vendor)
    ]
    assert currency_calls, (
        f"currency_consistency rule never invoked get_vendor_profile: {captured_args}"
    )
    # Pin the corrected order: organization_id MUST be the first
    # positional arg, vendor_name the second.
    found_correct_order = any(
        call_args[0][0] == org_id and call_args[0][1] == vendor
        for call_args in currency_calls
    )
    assert found_correct_order, (
        "currency_consistency must pass (organization_id, vendor_name) — pre-fix "
        f"the order was reversed and the lookup never matched. Got: {currency_calls}"
    )


@pytest.mark.asyncio
async def test_fraud_thresholds_gate_records_low_frequency_high_amount(postgres_test_db):
    """C6: Module 4 fraud thresholds now appear in the gate's
    ``rule_results``, not just the decision layer's ``risk_flags``.

    The shared ``evaluate_fraud_thresholds`` helper drives both the
    decision-layer ``compute_vendor_risk_score`` and the gate's new
    ``fraud_thresholds`` rule, so the gate's audit verdict cannot
    drift from the routing layer's flag.

    This test seeds a low-frequency vendor (1 prior invoice, avg
    $500) and submits a $5000 invoice — 10x the average. The
    Module-4 ``low_frequency_high_amount`` threshold should fire
    AND the gate's ``rule_results`` should carry a fail verdict for
    rule_id ``fraud_thresholds`` with the threshold details as
    evidence.
    """
    db = get_db()
    db.initialize()
    org_id = "default"
    vendor = "Low-Frequency Vendor"
    if not db.get_organization(org_id):
        db.create_organization(organization_id=org_id, name="Default Org")

    # Seed vendor with 1 prior invoice (below default threshold of 3),
    # average amount $500. Defaults: low_frequency_invoice_multiplier=3.0,
    # so $5000 (= 10× $500) > $500 × 3 = $1500 → threshold fires.
    db.upsert_vendor_profile(
        organization_id=org_id,
        vendor_name=vendor,
        invoice_count=1,
        avg_invoice_amount=500.0,
        last_invoice_amount=500.0,
    )

    _seed_ap_item_for_validation(
        db, vendor_name=vendor, invoice_number="INV-LFHA-1", amount=5000.0,
    )

    invoice = InvoiceData(
        gmail_id="thread-INV-LFHA-1",
        subject=f"Bill from {vendor}",
        sender="lf@example.com",
        vendor_name=vendor,
        amount=5000.0,
        currency="USD",
        invoice_number="INV-LFHA-1",
        confidence=0.99,
        organization_id=org_id,
        user_id="test-user",
    )

    workflow = _make_workflow()
    gate = await workflow._evaluate_deterministic_validation(invoice)

    # The fraud_thresholds rule should be in rule_results with verdict=fail.
    rule_results = gate.get("rule_results") or []
    fraud_thresholds_rule = next(
        (r for r in rule_results if r["rule_id"] == "fraud_thresholds"), None,
    )
    assert fraud_thresholds_rule is not None, (
        f"missing fraud_thresholds entry in rule_results: "
        f"{[r['rule_id'] for r in rule_results]}"
    )
    assert fraud_thresholds_rule["verdict"] == "fail", (
        f"expected fraud_thresholds verdict=fail, got {fraud_thresholds_rule['verdict']!r}"
    )

    # The fired flag should land in reason_codes.
    reason_codes = gate.get("reason_codes") or []
    assert "low_frequency_high_amount" in reason_codes, (
        f"low_frequency_high_amount not in reason_codes: {reason_codes}"
    )

    # Evidence on the rule_result must include the threshold details.
    evidence = fraud_thresholds_rule.get("evidence", {})
    new_reasons = evidence.get("reasons") or []
    assert any(
        r.get("code") == "low_frequency_high_amount"
        for r in new_reasons
    ), f"low_frequency_high_amount not in rule evidence: {new_reasons}"


@pytest.mark.asyncio
async def test_fraud_thresholds_gate_passes_when_no_threshold_fires(postgres_test_db):
    """A vendor with healthy history + a normal-sized invoice should
    produce a ``pass`` verdict for the fraud_thresholds rule and emit
    no Module-4 reason_codes. Pins the negative case so we know the
    rule isn't a false-positive machine."""
    db = get_db()
    db.initialize()
    org_id = "default"
    vendor = "Established Vendor"
    if not db.get_organization(org_id):
        db.create_organization(organization_id=org_id, name="Default Org")

    db.upsert_vendor_profile(
        organization_id=org_id,
        vendor_name=vendor,
        invoice_count=12,           # well above low-freq threshold of 3
        avg_invoice_amount=1000.0,
        last_invoice_amount=1000.0,
    )

    _seed_ap_item_for_validation(
        db, vendor_name=vendor, invoice_number="INV-OK-1", amount=900.0,
    )
    invoice = InvoiceData(
        gmail_id="thread-INV-OK-1",
        subject=f"Bill from {vendor}",
        sender="ok@example.com",
        vendor_name=vendor,
        amount=900.0,
        currency="USD",
        invoice_number="INV-OK-1",
        confidence=0.99,
        organization_id=org_id,
        user_id="test-user",
    )

    workflow = _make_workflow()
    gate = await workflow._evaluate_deterministic_validation(invoice)

    rule_results = gate.get("rule_results") or []
    fraud_thresholds_rule = next(
        (r for r in rule_results if r["rule_id"] == "fraud_thresholds"), None,
    )
    assert fraud_thresholds_rule is not None
    assert fraud_thresholds_rule["verdict"] == "pass", (
        f"expected pass on healthy vendor, got {fraud_thresholds_rule['verdict']!r}"
    )

    reason_codes = gate.get("reason_codes") or []
    for module4_code in (
        "new_vendor_high_amount",
        "low_frequency_high_amount",
        "recent_bank_change",
        "bank_change_warn",
    ):
        assert module4_code not in reason_codes, (
            f"{module4_code} fired unexpectedly on healthy vendor: {reason_codes}"
        )
