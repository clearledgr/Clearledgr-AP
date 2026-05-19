"""Phase 2 — Teams adapter ↔ Phase 1 audit-trail integration.

The Teams interactive endpoint normalises the operator's Adaptive
Card click and dispatches it through the runtime via
``dispatch_runtime_intent(runtime, "approve_invoice", payload, ...)``
with ``payload["source_channel"] = "teams"``. That source_channel
flows down through the workflow methods (``approve_invoice`` /
``reject_invoice`` / ``request_info``) to ``update_ap_item`` as
``_source="teams"``. Phase 1's auto-built ``decision_context``
snapshot then sets ``ui_surface = "teams"`` on the resulting
``state_transition`` audit_event — without any handler / workflow
plumbing change.

These tests prove that compose end-to-end:

1. Dispatch through the runtime with ``source_channel="teams"``
   produces a ``state_transition`` audit row whose
   ``payload_json.decision_context.ui_surface`` is ``"teams"``.
2. The same path captures the metadata-derived snapshot fields
   (``agent_recommendation``, ``validation_gate_at_decision``,
   ``vendor_profile_snapshot``, ``risk_flags_shown``,
   ``confidence_at_decision``, ``policy_version``, ``actor_*``).
3. Reject and request_info paths likewise carry
   ``ui_surface="teams"``.

JWT verification, action normalisation, and idempotency precedence
are covered by ``test_teams_verify.py`` and the channel-action
contract tests; this module is strictly the audit-row contract.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

from solden.core.database import get_db
from solden.services.agent_command_dispatch import (
    build_channel_runtime,
    dispatch_runtime_intent,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _seed_ap_item_at_needs_approval(
    db,
    *,
    ap_item_id: str = "AP-TEAMS-1",
    organization_id: str = "org-test",
) -> Dict[str, Any]:
    """Create an AP item with the metadata Phase 1 reads for the
    auto-built decision_context snapshot, and walk it to
    ``needs_approval`` so the approval intents are valid transitions.
    """
    metadata = {
        "ap_decision_recommendation": "approve",
        "validation_gate": {
            "passed": True,
            "reason_codes": [],
            "rule_results": [
                {"rule_id": "field_presence", "verdict": "pass"},
                {"rule_id": "amount_cross_validation", "verdict": "pass"},
            ],
        },
        "vendor_intelligence": {
            "trusted": True,
            "invoice_count": 24,
            "default_currency": "USD",
        },
        "fraud_flags": ["low_risk"],
        "reasoning_risks": ["amount_within_normal_range"],
        "field_confidences": {"vendor_name": 0.99, "amount": 0.97},
    }
    payload = {
        "id": ap_item_id,
        "invoice_key": f"AcmeCo::INV-{ap_item_id}",
        "thread_id": f"thread-{ap_item_id}",
        "vendor_name": "Acme Co",
        "amount": 1000.0,
        "currency": "USD",
        "invoice_number": f"INV-{ap_item_id}",
        "subject": "Bill from Acme Co",
        "sender": "acme@example.com",
        "state": "received",
        "approval_policy_version": "v2",
        "confidence": 0.95,
        "field_confidences": {"vendor_name": 0.99, "amount": 0.97},
        "organization_id": organization_id,
        "metadata": metadata,
    }
    db.create_ap_item(payload)
    # Walk to needs_approval — the precondition for approve_invoice.
    db.update_ap_item(
        ap_item_id, state="validated",
        _actor_type="agent", _actor_id="invoice_validation",
        _decision_reason="validation_passed",
    )
    db.update_ap_item(
        ap_item_id, state="needs_approval",
        _actor_type="agent", _actor_id="invoice_validation",
        _decision_reason="route_for_approval",
    )
    return db.get_ap_item(ap_item_id)


def _state_transition_audits(db, ap_item_id: str) -> List[Dict[str, Any]]:
    rows = db.list_ap_audit_events(ap_item_id, limit=50, order="desc") or []
    return [r for r in rows if r.get("event_type") == "state_transition"]


def _payload_decision_context(audit_row: Dict[str, Any]) -> Dict[str, Any]:
    raw = audit_row.get("payload_json")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
    elif isinstance(raw, dict):
        parsed = raw
    else:
        return {}
    decision_context = parsed.get("decision_context")
    return decision_context if isinstance(decision_context, dict) else {}


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_teams_approve_dispatch_lands_ui_surface_teams(postgres_test_db):
    """Approving via Teams produces a state_transition audit row whose
    decision_context.ui_surface is ``"teams"`` and carries the
    auto-built metadata snapshot fields from the AP item.
    """
    db = get_db()
    db.initialize()
    db.ensure_organization("org-test", organization_name="Test Org")
    _seed_ap_item_at_needs_approval(db, ap_item_id="AP-TEAMS-APPROVE")

    runtime = build_channel_runtime(
        organization_id="org-test",
        actor_id="alice@example.com",
        actor_email="alice@example.com",
        db=db,
        fallback_actor="teams_user",
    )

    result = await dispatch_runtime_intent(
        runtime,
        "approve_invoice",
        {
            "ap_item_id": "AP-TEAMS-APPROVE",
            "email_id": "thread-AP-TEAMS-APPROVE",
            "reason": "approved_in_teams",
            "source_channel": "teams",
            "source_channel_id": "msteams-conv-1",
            "source_message_ref": "msteams-msg-1",
            "actor_id": "alice@example.com",
            "actor_display": "Alice (Teams)",
            "actor_email": "alice@example.com",
        },
        idempotency_key="teams-approve-AP-TEAMS-APPROVE-1",
    )

    # The intent dispatch may return blocked / approved / posted depending
    # on whether the runtime registered the AP skill in the test fixture.
    # We don't assert on the outcome — we assert on the audit row, which
    # is where the SoR claim lives.
    assert isinstance(result, dict), f"runtime returned non-dict: {result!r}"

    audits = _state_transition_audits(db, "AP-TEAMS-APPROVE")
    # The seed walk produced 2 transitions (received->validated->
    # needs_approval). The Teams approve dispatch should have produced
    # at least one more (needs_approval->approved or
    # needs_approval->ready_to_post or further). At minimum, one of
    # the transitions caused by the Teams flow must carry ui_surface
    # equal to "teams".
    teams_transitions = [
        a for a in audits
        if _payload_decision_context(a).get("ui_surface") == "teams"
    ]
    assert teams_transitions, (
        "No state_transition audit row carries ui_surface='teams'. "
        f"Audit rows: {[(a.get('prev_state'), a.get('new_state'), _payload_decision_context(a).get('ui_surface')) for a in audits]}"
    )

    # The most recent teams-driven transition should carry the auto-
    # built snapshot fields derived from the AP item's metadata.
    teams_audit = teams_transitions[0]
    decision_context = _payload_decision_context(teams_audit)
    assert decision_context.get("ui_surface") == "teams"
    # actor + decision_reason were passed through the Teams dispatch.
    assert decision_context.get("actor_type") == "user"
    assert decision_context.get("actor_id") == "alice@example.com"
    # Auto-built fields from the AP item's metadata.
    assert decision_context.get("agent_recommendation") == "approve"
    assert decision_context.get("validation_gate_at_decision", {}).get("passed") is True
    assert decision_context.get("vendor_profile_snapshot", {}).get("trusted") is True
    assert "low_risk" in decision_context.get("risk_flags_shown", [])
    assert decision_context.get("policy_version") == "v2"
    assert "snapshotted_at" in decision_context


@pytest.mark.asyncio
async def test_teams_reject_dispatch_lands_ui_surface_teams(postgres_test_db):
    """Reject via Teams: same audit-trail contract."""
    db = get_db()
    db.initialize()
    db.ensure_organization("org-test", organization_name="Test Org")
    _seed_ap_item_at_needs_approval(db, ap_item_id="AP-TEAMS-REJECT")

    runtime = build_channel_runtime(
        organization_id="org-test",
        actor_id="bob@example.com",
        actor_email="bob@example.com",
        db=db,
        fallback_actor="teams_user",
    )

    await dispatch_runtime_intent(
        runtime,
        "reject_invoice",
        {
            "ap_item_id": "AP-TEAMS-REJECT",
            "email_id": "thread-AP-TEAMS-REJECT",
            "reason": "duplicate_supplier_invoice",
            "source_channel": "teams",
            "source_channel_id": "msteams-conv-2",
            "source_message_ref": "msteams-msg-2",
            "actor_id": "bob@example.com",
            "actor_display": "Bob (Teams)",
            "actor_email": "bob@example.com",
        },
        idempotency_key="teams-reject-AP-TEAMS-REJECT-1",
    )

    audits = _state_transition_audits(db, "AP-TEAMS-REJECT")
    teams_transitions = [
        a for a in audits
        if _payload_decision_context(a).get("ui_surface") == "teams"
    ]
    assert teams_transitions, (
        "Reject via Teams did not produce a state_transition audit row "
        "with ui_surface='teams'."
    )
    decision_context = _payload_decision_context(teams_transitions[0])
    assert decision_context.get("ui_surface") == "teams"
    assert decision_context.get("actor_id") == "bob@example.com"


@pytest.mark.asyncio
async def test_teams_request_info_dispatch_lands_ui_surface_teams(postgres_test_db):
    """Request-info via Teams: same audit-trail contract."""
    db = get_db()
    db.initialize()
    db.ensure_organization("org-test", organization_name="Test Org")
    _seed_ap_item_at_needs_approval(db, ap_item_id="AP-TEAMS-RFI")

    runtime = build_channel_runtime(
        organization_id="org-test",
        actor_id="carol@example.com",
        actor_email="carol@example.com",
        db=db,
        fallback_actor="teams_user",
    )

    await dispatch_runtime_intent(
        runtime,
        "request_info",
        {
            "ap_item_id": "AP-TEAMS-RFI",
            "email_id": "thread-AP-TEAMS-RFI",
            "reason": "missing_po_number",
            "source_channel": "teams",
            "source_channel_id": "msteams-conv-3",
            "source_message_ref": "msteams-msg-3",
            "actor_id": "carol@example.com",
            "actor_display": "Carol (Teams)",
            "actor_email": "carol@example.com",
        },
        idempotency_key="teams-rfi-AP-TEAMS-RFI-1",
    )

    audits = _state_transition_audits(db, "AP-TEAMS-RFI")
    teams_transitions = [
        a for a in audits
        if _payload_decision_context(a).get("ui_surface") == "teams"
    ]
    assert teams_transitions, (
        "request_info via Teams did not produce a state_transition audit "
        "row with ui_surface='teams'."
    )
    decision_context = _payload_decision_context(teams_transitions[0])
    assert decision_context.get("ui_surface") == "teams"
    assert decision_context.get("actor_id") == "carol@example.com"
