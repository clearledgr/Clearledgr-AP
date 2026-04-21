"""Tests for deterministic gate enforcement over routing recommendations.

Post-Phase 4, the architectural commitment from DESIGN_THESIS.md §7.6
is enforced by construction: the 10-step rule cascade in
`APDecisionService._compute_routing_decision` cannot emit `approve`
when the validation gate has failed (step 1 returns `needs_info`, step
2 returns `escalate`).

`enforce_gate_constraint` remains as a defensive backstop for any
future upstream path that constructs an APDecision directly and
bypasses the rule cascade. Two remaining concerns:

  - The pure-function matrix: `enforce_gate_constraint` behaves
    correctly across the gate × recommendation combinations.
  - The workflow narrow waist: `process_new_invoice` must re-apply the
    constraint when callers pass a pre-computed `ap_decision=` that did
    not go through the rule cascade.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_invoice(**kwargs) -> Any:
    from clearledgr.services.invoice_workflow import InvoiceData
    defaults = dict(
        gmail_id="gmail_gate_001",
        subject="Invoice INV-GATE-001",
        sender="billing@gatevendor.com",
        vendor_name="Gate Test Vendor",
        amount=1500.00,
        currency="USD",
        invoice_number="INV-GATE-001",
        due_date="2026-04-30",
        confidence=0.97,
        organization_id="org_gate_test",
        field_confidences={
            "vendor": 0.99,
            "amount": 0.97,
            "invoice_number": 0.95,
            "due_date": 0.92,
        },
    )
    defaults.update(kwargs)
    return InvoiceData(**defaults)


def _make_decision(recommendation: str, **overrides) -> Any:
    from clearledgr.services.ap_decision import APDecision
    base: Dict[str, Any] = dict(
        recommendation=recommendation,
        reasoning="Original reasoning.",
        confidence=0.9,
        info_needed=None,
        risk_flags=["vendor_new"],
        vendor_context_used={"k": "v"},
        model="rules",
        fallback=False,
    )
    base.update(overrides)
    return APDecision(**base)


# ===========================================================================
# enforce_gate_constraint pure-function matrix
# ===========================================================================

class TestEnforceGateConstraintMatrix:
    """Exhaustive matrix for the defensive enforcement helper."""

    def test_none_gate_returns_decision_unchanged(self):
        from clearledgr.services.ap_decision import enforce_gate_constraint
        decision = _make_decision("approve")
        result = enforce_gate_constraint(decision, None)
        assert result is decision
        assert result.recommendation == "approve"
        assert result.gate_override is False
        assert result.original_recommendation is None

    def test_passed_gate_returns_decision_unchanged(self):
        from clearledgr.services.ap_decision import enforce_gate_constraint
        decision = _make_decision("approve")
        result = enforce_gate_constraint(
            decision, {"passed": True, "reason_codes": []}
        )
        assert result is decision
        assert result.recommendation == "approve"
        assert result.gate_override is False

    def test_failed_gate_plus_approve_forces_escalate(self):
        from clearledgr.services.ap_decision import enforce_gate_constraint
        decision = _make_decision(
            "approve",
            reasoning="Vendor is always safe and the amount looks right.",
            risk_flags=["low_history"],
        )
        gate = {
            "passed": False,
            "reason_codes": ["po_required_missing", "amount_anomaly_high"],
        }
        result = enforce_gate_constraint(decision, gate)

        assert result is not decision
        assert result.recommendation == "escalate"
        assert result.gate_override is True
        assert result.original_recommendation == "approve"
        assert "po_required_missing" in result.reasoning
        assert "amount_anomaly_high" in result.reasoning
        assert "gate_override_applied" in result.risk_flags
        assert "low_history" in result.risk_flags
        assert result.confidence == decision.confidence
        assert result.vendor_context_used == decision.vendor_context_used
        assert result.fallback is decision.fallback
        assert result.info_needed is None

    @pytest.mark.parametrize("rec", ["escalate", "needs_info", "reject"])
    def test_failed_gate_plus_non_approve_passes_through(self, rec):
        from clearledgr.services.ap_decision import enforce_gate_constraint
        decision = _make_decision(rec)
        result = enforce_gate_constraint(
            decision, {"passed": False, "reason_codes": ["po_required_missing"]}
        )
        assert result is decision
        assert result.recommendation == rec
        assert result.gate_override is False
        assert result.original_recommendation is None

    def test_failed_gate_with_missing_reason_codes_still_overrides(self):
        from clearledgr.services.ap_decision import enforce_gate_constraint
        decision = _make_decision("approve")
        result = enforce_gate_constraint(decision, {"passed": False})
        assert result.recommendation == "escalate"
        assert result.gate_override is True
        assert "unknown" in result.reasoning.lower()

    def test_input_decision_is_not_mutated(self):
        from clearledgr.services.ap_decision import enforce_gate_constraint
        decision = _make_decision("approve", risk_flags=["low_history"])
        _ = enforce_gate_constraint(
            decision, {"passed": False, "reason_codes": ["duplicate_invoice"]}
        )
        assert decision.recommendation == "approve"
        assert decision.risk_flags == ["low_history"]
        assert decision.gate_override is False
        assert decision.original_recommendation is None


# ===========================================================================
# decide() routes through enforce_gate_constraint even if the cascade is broken
# ===========================================================================

class TestDecideServiceEnforcesGate:

    def test_broken_cascade_returning_approve_on_failed_gate_is_overridden(self, monkeypatch):
        """Defensive: if `_compute_routing_decision` ever returned
        'approve' with a failed gate (a bug), `enforce_gate_constraint`
        must catch it at the `decide()` exit.
        """
        from clearledgr.services.ap_decision import APDecision, APDecisionService

        def _broken_cascade(self, invoice, validation_gate, vendor_context_used=None, **kwargs):
            return APDecision(
                recommendation="approve",  # the bug being simulated
                reasoning="Broken cascade ignored the gate.",
                confidence=0.97,
                info_needed=None,
                risk_flags=[],
                vendor_context_used=vendor_context_used or {},
                model="rules",
            )

        monkeypatch.setattr(APDecisionService, "_compute_routing_decision", _broken_cascade)

        invoice = _make_invoice(confidence=0.97)
        svc = APDecisionService()
        decision = asyncio.run(
            svc.decide(
                invoice,
                validation_gate={
                    "passed": False,
                    "reason_codes": ["po_required_missing"],
                },
            )
        )
        assert decision.recommendation == "escalate"
        assert decision.gate_override is True
        assert decision.original_recommendation == "approve"

    def test_normal_cascade_gate_failed_returns_escalate_natively(self):
        """Control: the rule cascade handles failed gates natively (step 2).
        `enforce_gate_constraint` is a no-op — gate_override stays False.
        """
        from clearledgr.services.ap_decision import APDecisionService

        invoice = _make_invoice(confidence=0.97)
        svc = APDecisionService()
        decision = asyncio.run(
            svc.decide(
                invoice,
                validation_gate={
                    "passed": False,
                    "reason_codes": ["erp_preflight_vendor_not_found"],
                },
            )
        )
        assert decision.recommendation == "escalate"
        assert decision.gate_override is False


# ===========================================================================
# process_new_invoice narrow-waist re-enforcement
# ===========================================================================

class TestProcessNewInvoiceNarrowWaistEnforcement:
    """If a caller passes `ap_decision=` that didn't go through the rule
    cascade (e.g. a pre-computed external decision), the workflow must
    re-enforce the gate before routing."""

    def test_pre_computed_approve_plus_failed_gate_forces_escalate(self, tmp_path, monkeypatch):
        from clearledgr.core.database import ClearledgrDB
        from clearledgr.core import database as db_module
        from clearledgr.services.ap_decision import APDecision
        from clearledgr.services.invoice_workflow import (
            InvoiceData, InvoiceWorkflowService
        )

        db = ClearledgrDB(db_path=str(tmp_path / "gate_waist.db"))
        db.initialize()
        monkeypatch.setattr(db_module, "_DB_INSTANCE", db)

        workflow = InvoiceWorkflowService(organization_id="org_gate_test")

        async def _fake_eval_gate(inv):
            return {
                "passed": False,
                "reason_codes": ["po_required_missing"],
                "reasons": [
                    {
                        "code": "po_required_missing",
                        "message": "PO required",
                        "severity": "error",
                    }
                ],
                "policy_compliance": {"compliant": False, "violations": []},
                "po_match_result": None,
                "budget_impact": [],
                "budget": {"status": "ok"},
                "confidence_gate": {},
                "erp_preflight": {},
            }

        monkeypatch.setattr(
            workflow, "_evaluate_deterministic_validation", _fake_eval_gate
        )

        async def _fake_send_for_approval(invoice, **kwargs):
            return {
                "status": "pending_approval",
                "invoice_id": invoice.gmail_id,
                "reason": kwargs.get("decision_reason", "escalated_by_gate"),
            }

        monkeypatch.setattr(
            workflow, "_send_for_approval", _fake_send_for_approval
        )

        monkeypatch.setattr(
            workflow, "_record_validation_gate_failure",
            lambda *a, **kw: None,
        )

        captured_audit = []
        original_append = db.append_audit_event

        def _spy_append(payload):
            captured_audit.append(payload)
            try:
                return original_append(payload)
            except Exception:
                return None

        monkeypatch.setattr(db, "append_audit_event", _spy_append)

        invoice = InvoiceData(
            gmail_id="gmail_waist_1",
            subject="Invoice INV-WAIST-1",
            sender="billing@waist.test",
            vendor_name="Waist Test Vendor",
            amount=1500.0,
            currency="USD",
            invoice_number="INV-WAIST-1",
            due_date="2026-05-01",
            confidence=0.98,
            organization_id="org_gate_test",
            field_confidences={"vendor": 0.99, "amount": 0.98, "invoice_number": 0.97, "due_date": 0.95},
        )

        pre_computed = APDecision(
            recommendation="approve",  # raw external approval, unchecked
            reasoning="Vendor has clean history and amount looks right.",
            confidence=0.98,
            info_needed=None,
            risk_flags=[],
            vendor_context_used={},
            model="external_caller",
            fallback=False,
        )

        result = asyncio.run(
            workflow.process_new_invoice(invoice, ap_decision=pre_computed)
        )

        assert result.get("status") in {"pending_approval", "escalated"}
        assert result.get("status") != "posted_to_erp"

        override_events = [
            e for e in captured_audit
            if (e.get("event_type") == "llm_gate_override_applied")
        ]
        assert len(override_events) == 1
        evt = override_events[0]
        assert evt["metadata"]["pre_override_recommendation"] == "approve"
        assert evt["metadata"]["enforced_recommendation"] == "escalate"
        assert "po_required_missing" in evt["metadata"]["gate_reason_codes"]
