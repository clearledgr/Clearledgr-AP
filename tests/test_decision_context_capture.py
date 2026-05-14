"""Phase 1, Gap 4 — decision_context capture on every state transition.

Every ``ap_items.state`` transition writes an ``audit_events`` row
with ``payload_json.decision_context`` containing the operator's
view at decision time:

* ``agent_recommendation`` — what the agent suggested
* ``validation_gate_at_decision`` — the gate verdict shown
* ``vendor_profile_snapshot`` — vendor history shown
* ``risk_flags_shown`` — fraud / risk flags
* ``confidence_at_decision`` + ``field_confidences_at_decision``
* ``ui_surface`` — slack / teams / gmail / outlook / web / api / agent_*
* ``policy_version``
* ``actor_type`` / ``actor_id`` / ``decision_reason``
* ``snapshotted_at``

Without this snapshot, an auditor reconstructing "why did this
invoice get approved" would need to join 4+ tables — and even then,
post-decision overwrites of metadata could mask what the operator
actually saw at the moment of decision.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from clearledgr.core.ap_states import APState
from clearledgr.core.database import get_db


def _seed_ap_item_with_metadata(db, *, ap_item_id: str = "AP-DC-1") -> str:
    metadata = {
        "ap_decision_recommendation": "approve",
        "validation_gate": {
            "passed": True,
            "reason_codes": [],
            "rule_results": [{"rule_id": "field_presence", "verdict": "pass"}],
        },
        "vendor_intelligence": {
            "trusted": True,
            "invoice_count": 12,
            "default_currency": "USD",
        },
        "fraud_flags": ["low_risk"],
        "reasoning_risks": ["amount_within_normal_range"],
        "field_confidences": {"vendor_name": 0.99, "amount": 0.97},
    }
    payload = {
        "id": ap_item_id,
        "invoice_key": f"AcmeCo::INV-{ap_item_id}",
        "vendor_name": "Acme Co",
        "amount": 1000.0,
        "currency": "USD",
        "invoice_number": f"INV-{ap_item_id}",
        "subject": "Bill from Acme Co",
        "sender": "acme@example.com",
        "state": "needs_approval",
        "approval_policy_version": "v2",
        "confidence": 0.95,
        "field_confidences": {"vendor_name": 0.99, "amount": 0.97},
        "organization_id": "org-test",
        "metadata": metadata,
    }
    db.create_ap_item(payload)
    return ap_item_id


def _latest_state_transition_audit(db, ap_item_id: str):
    rows = db.list_ap_audit_events(ap_item_id, limit=20, order="desc")
    for row in rows or []:
        if row.get("event_type") == "state_transition":
            return row
    return None


def _audit_payload(row) -> dict:
    raw = row.get("payload_json")
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    return {}


def test_state_transition_attaches_decision_context_snapshot(postgres_test_db):
    """A typical operator approval should auto-build a complete
    decision_context snapshot from the AP item's metadata + the
    actor kwargs the workflow passed."""
    db = get_db()
    db.initialize()
    ap_item_id = _seed_ap_item_with_metadata(db)

    db.update_ap_item(
        ap_item_id,
        state=APState.APPROVED.value,
        approved_by="alice@example.com",
        approved_at=datetime.now(timezone.utc).isoformat(),
        _actor_type="user",
        _actor_id="alice@example.com",
        _source="slack",
        _decision_reason="approve_invoice",
        _intent="approve_invoice",
        _ui_surface="slack",
    )

    audit = _latest_state_transition_audit(db, ap_item_id)
    assert audit is not None
    payload = _audit_payload(audit)
    decision_context = payload.get("decision_context")
    assert isinstance(decision_context, dict) and decision_context, (
        "state_transition audit must carry decision_context"
    )

    # Auto-built fields from the AP item's metadata
    assert decision_context.get("agent_recommendation") == "approve"
    assert decision_context.get("validation_gate_at_decision", {}).get("passed") is True
    assert decision_context.get("vendor_profile_snapshot", {}).get("trusted") is True
    assert "low_risk" in decision_context.get("risk_flags_shown", [])
    assert decision_context.get("confidence_at_decision") == 0.95
    assert decision_context.get("field_confidences_at_decision", {}).get("vendor_name") == 0.99
    assert decision_context.get("ui_surface") == "slack"
    assert decision_context.get("policy_version") == "v2"
    assert decision_context.get("intent") == "approve_invoice"
    assert decision_context.get("actor_type") == "user"
    assert decision_context.get("actor_id") == "alice@example.com"
    assert decision_context.get("decision_reason") == "approve_invoice"
    assert "snapshotted_at" in decision_context


def test_caller_decision_context_overrides_auto_built_fields(postgres_test_db):
    """Explicitly passing ``_decision_context`` should override the
    auto-built snapshot on key collisions."""
    db = get_db()
    db.initialize()
    ap_item_id = _seed_ap_item_with_metadata(db, ap_item_id="AP-DC-2")

    override = {
        "ui_surface": "teams",
        "intent": "approve_invoice_with_override",
        "intent_input": {"override_reason": "vendor_pre_approved"},
        "agent_recommendation": "needs_info",  # override metadata-derived value
    }
    db.update_ap_item(
        ap_item_id,
        state=APState.APPROVED.value,
        _actor_type="user",
        _actor_id="bob@example.com",
        _source="teams",
        _decision_reason="approve_invoice",
        _decision_context=override,
    )

    audit = _latest_state_transition_audit(db, ap_item_id)
    assert audit is not None
    payload = _audit_payload(audit)
    dc = payload["decision_context"]

    # Caller overrides win
    assert dc.get("ui_surface") == "teams"
    assert dc.get("intent") == "approve_invoice_with_override"
    assert dc.get("intent_input", {}).get("override_reason") == "vendor_pre_approved"
    assert dc.get("agent_recommendation") == "needs_info"

    # Auto-built fields the caller didn't override are still present
    assert dc.get("policy_version") == "v2"
    assert dc.get("confidence_at_decision") == 0.95


def test_audit_payload_includes_column_updates(postgres_test_db):
    """The ``column_updates`` block under payload_json captures every
    non-state column written, so an auditor can reconstruct what
    changed alongside the state transition."""
    db = get_db()
    db.initialize()
    ap_item_id = _seed_ap_item_with_metadata(db, ap_item_id="AP-DC-3")

    db.update_ap_item(
        ap_item_id,
        state=APState.APPROVED.value,
        approved_by="carol@example.com",
        last_error=None,
        _actor_type="user",
        _actor_id="carol@example.com",
        _source="web",
        _decision_reason="approve_invoice",
    )
    audit = _latest_state_transition_audit(db, ap_item_id)
    payload = _audit_payload(audit)
    column_updates = payload.get("column_updates") or {}
    assert "approved_by" in column_updates
    assert column_updates["approved_by"] == "carol@example.com"
