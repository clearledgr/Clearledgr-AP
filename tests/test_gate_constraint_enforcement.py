"""Tests for Phase 1.1 — deterministic gate enforcement over LLM recommendations.

These tests verify the architectural commitment from DESIGN_THESIS.md §7.6:

    "The LLM reasons within boundaries set by rules. It never acts beyond
    what the rules permit, regardless of its reasoning."

The enforcement is layered:
  - Layer 1 (prompt): _build_reasoning_prompt restricts valid recommendations
  - Layer 2 (service): enforce_gate_constraint clamps decide() output
  - Layer 3 (agent skill): _handle_get_ap_decision + _handle_execute_routing
  - Layer 4 (workflow waist): process_new_invoice re-enforces before routing

This test file covers all four layers.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirrors tests/test_ap_decision.py)
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
        reasoning="Original LLM reasoning.",
        confidence=0.9,
        info_needed=None,
        risk_flags=["vendor_new"],
        vendor_context_used={"k": "v"},
        model="claude-sonnet-4-test",
        fallback=False,
    )
    base.update(overrides)
    return APDecision(**base)


def _fake_claude_response(recommendation: str, reasoning: str, **kw) -> Dict:
    """Return a fake Anthropic tool_use response (Layer 1 upgrade shape)."""
    payload = {
        "recommendation": recommendation,
        "reasoning": reasoning,
        "confidence": kw.get("confidence", 0.91),
        "info_needed": kw.get("info_needed"),
        "risk_flags": kw.get("risk_flags", []),
    }
    return {
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_fake_gate",
                "name": "record_ap_decision",
                "input": payload,
            }
        ],
        "model": "claude-sonnet-4-test",
        "stop_reason": "tool_use",
    }


# ===========================================================================
# Layer 2: enforce_gate_constraint pure-function matrix
# ===========================================================================

class TestEnforceGateConstraintMatrix:
    """Exhaustive matrix for the pure enforcement helper."""

    def test_none_gate_returns_decision_unchanged(self):
        from clearledgr.services.ap_decision import enforce_gate_constraint
        decision = _make_decision("approve")
        result = enforce_gate_constraint(decision, None)
        assert result is decision  # unchanged identity — no copy
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

        assert result is not decision  # returns a fresh APDecision
        assert result.recommendation == "escalate"
        assert result.gate_override is True
        assert result.original_recommendation == "approve"
        assert "po_required_missing" in result.reasoning
        assert "amount_anomaly_high" in result.reasoning
        assert "gate_override_applied" in result.risk_flags
        # Original risk flags preserved alongside the override marker
        assert "low_history" in result.risk_flags
        # Confidence, vendor context, and fallback flag preserved
        assert result.confidence == decision.confidence
        assert result.vendor_context_used == decision.vendor_context_used
        assert result.fallback is decision.fallback
        # info_needed is cleared because 'escalate' doesn't need a question
        assert result.info_needed is None

    @pytest.mark.parametrize("rec", ["escalate", "needs_info", "reject"])
    def test_failed_gate_plus_non_approve_passes_through(self, rec):
        from clearledgr.services.ap_decision import enforce_gate_constraint
        decision = _make_decision(rec)
        result = enforce_gate_constraint(
            decision, {"passed": False, "reason_codes": ["po_required_missing"]}
        )
        assert result is decision  # non-approve → untouched
        assert result.recommendation == rec
        assert result.gate_override is False
        assert result.original_recommendation is None

    def test_failed_gate_with_missing_reason_codes_still_overrides(self):
        from clearledgr.services.ap_decision import enforce_gate_constraint
        decision = _make_decision("approve")
        # Gate is failed but reason_codes list is missing — still override.
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
        # Verify input was not touched
        assert decision.recommendation == "approve"
        assert decision.risk_flags == ["low_history"]
        assert decision.gate_override is False
        assert decision.original_recommendation is None


# ===========================================================================
# Layer 1a: tool schema structurally constrains the recommendation enum
# ===========================================================================

class TestToolSchemaDynamicEnum:
    """Layer 1 of §7.6 enforcement: Anthropic's tool-use API receives a
    ``record_ap_decision`` tool whose ``recommendation`` enum is narrowed
    based on the gate outcome. Combined with a forced ``tool_choice``,
    this structurally prevents Claude from emitting 'approve' when rules
    have not been satisfied."""

    def test_schema_name_is_stable(self):
        from clearledgr.services.ap_decision import (
            _build_decision_tool_schema, _DECISION_TOOL_NAME,
        )
        schema = _build_decision_tool_schema(True, [])
        assert schema["name"] == _DECISION_TOOL_NAME == "record_ap_decision"

    def test_passed_gate_enum_contains_all_four_actions(self):
        from clearledgr.services.ap_decision import _build_decision_tool_schema
        schema = _build_decision_tool_schema(gate_passed=True, reason_codes=[])
        enum = schema["input_schema"]["properties"]["recommendation"]["enum"]
        assert set(enum) == {"approve", "needs_info", "escalate", "reject"}

    def test_failed_gate_enum_structurally_excludes_approve(self):
        from clearledgr.services.ap_decision import _build_decision_tool_schema
        schema = _build_decision_tool_schema(
            gate_passed=False,
            reason_codes=["po_required_missing", "duplicate_invoice"],
        )
        enum = schema["input_schema"]["properties"]["recommendation"]["enum"]
        assert set(enum) == {"needs_info", "escalate", "reject"}
        # approve is STRUCTURALLY unavailable, not merely discouraged
        assert "approve" not in enum
        # The tool description must cite the §7.6 constraint + reason codes
        assert "§7.6" in schema["description"]
        assert "po_required_missing" in schema["description"]
        assert "duplicate_invoice" in schema["description"]
        assert "structurally excluded" in schema["description"]

    def test_schema_required_fields_cover_decision_core(self):
        from clearledgr.services.ap_decision import _build_decision_tool_schema
        schema = _build_decision_tool_schema(True, [])
        required = set(schema["input_schema"]["required"])
        assert {"recommendation", "reasoning", "confidence"}.issubset(required)


# ===========================================================================
# Layer 1b: the prompt still explains the architectural constraint in text
# ===========================================================================

class TestPromptConstrainsActionsOnFailedGate:

    def test_prompt_describes_architectural_constraint_on_failed_gate(self):
        from clearledgr.services.ap_decision import _build_reasoning_prompt
        invoice = _make_invoice()
        prompt = _build_reasoning_prompt(
            invoice=invoice,
            vendor_profile={},
            vendor_history=[],
            decision_feedback={},
            correction_suggestions={},
            org_config={"organization_id": "org_gate_test"},
            validation_gate={
                "passed": False,
                "reason_codes": ["po_required_missing", "duplicate_invoice"],
            },
        )
        # Architectural constraint language must appear so Claude reasons
        # about WHY 'approve' is missing from the tool schema enum.
        assert "ARCHITECTURAL CONSTRAINT" in prompt
        assert "structurally" in prompt and "UNAVAILABLE" in prompt
        assert "po_required_missing" in prompt
        assert "duplicate_invoice" in prompt
        # The prompt no longer carries a raw JSON enum hint — the tool
        # schema owns structure. But it must still reference the tool by name.
        assert "record_ap_decision" in prompt

    def test_prompt_on_passed_gate_omits_architectural_constraint(self):
        from clearledgr.services.ap_decision import _build_reasoning_prompt
        invoice = _make_invoice()
        prompt = _build_reasoning_prompt(
            invoice=invoice,
            vendor_profile={},
            vendor_history=[],
            decision_feedback={},
            correction_suggestions={},
            org_config={"organization_id": "org_gate_test"},
            validation_gate={"passed": True, "reason_codes": []},
        )
        assert "ARCHITECTURAL CONSTRAINT" not in prompt
        assert "record_ap_decision" in prompt


# ===========================================================================
# Layer 1c: _call_claude sends tools + forced tool_choice in the HTTP payload
# ===========================================================================

class TestCallClaudeSendsForcedToolChoice:
    """Regression guard: catches any future change that drops the forced
    ``tool_choice`` and silently degrades enforcement to text completion."""

    def test_call_claude_payload_includes_forced_tool_choice(self, monkeypatch):
        import httpx
        from clearledgr.services.ap_decision import (
            APDecisionService, _build_decision_tool_schema, _DECISION_TOOL_NAME,
        )

        captured: Dict[str, Any] = {}

        _fake_body = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_capture",
                    "name": _DECISION_TOOL_NAME,
                    "input": {
                        "recommendation": "escalate",
                        "reasoning": "Captured.",
                        "confidence": 0.9,
                        "risk_flags": [],
                    },
                }
            ],
            "model": "claude-sonnet-4-test",
        }

        class _FakeResponse:
            status_code = 200
            # gateway size-checks resp.content before json parsing.
            content = json.dumps(_fake_body).encode("utf-8")
            def raise_for_status(self):
                return None
            def json(self):
                return _fake_body

        class _FakeAsyncClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, url, headers=None, json=None, **kwargs):
                # Accept/ignore timeout and any other kwargs the real
                # httpx.AsyncClient exposes — the real gateway now passes
                # timeout= per call, which this stub must tolerate.
                captured["url"] = url
                captured["headers"] = headers
                captured["json"] = json
                return _FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        # get_http_client() caches a shared httpx.AsyncClient instance
        # on first call. Reset the cache so the next get_http_client()
        # (inside the LLM gateway) builds a fresh instance through the
        # patched httpx.AsyncClient class. Restored automatically by
        # monkeypatch at test teardown.
        import clearledgr.core.http_client as http_mod
        monkeypatch.setattr(http_mod, "_shared_client", None)

        svc = APDecisionService(api_key="test-key")
        schema = _build_decision_tool_schema(
            gate_passed=False, reason_codes=["po_required_missing"]
        )
        asyncio.run(svc._call_claude("test prompt", tool_schema=schema))

        payload = captured.get("json") or {}
        # Must send exactly one tool — the record_ap_decision schema
        assert "tools" in payload and len(payload["tools"]) == 1
        assert payload["tools"][0]["name"] == _DECISION_TOOL_NAME
        # Must force Claude to call that specific tool (no text completion)
        assert "tool_choice" in payload
        assert payload["tool_choice"] == {"type": "tool", "name": _DECISION_TOOL_NAME}
        # The enum in the HTTP-wire payload must match Layer 1 gate state —
        # this is the actual byte-level constraint that flows to Anthropic.
        wire_enum = payload["tools"][0]["input_schema"]["properties"]["recommendation"]["enum"]
        assert set(wire_enum) == {"needs_info", "escalate", "reject"}
        assert "approve" not in wire_enum


# ===========================================================================
# Layer 2: APDecisionService.decide() routes through enforce_gate_constraint
# ===========================================================================

class TestDecideServiceEnforcesGate:

    def test_claude_approve_plus_failed_gate_overridden_to_escalate(self, monkeypatch):
        from clearledgr.services.ap_decision import APDecisionService
        from clearledgr.core.llm_gateway import reset_llm_gateway
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        reset_llm_gateway()  # rebuild singleton so it reads the new env var
        invoice = _make_invoice(confidence=0.97)

        # Even if a hypothetically broken Claude response said "approve",
        # the service must override it.
        with patch.object(
            APDecisionService,
            "_call_claude",
            new_callable=AsyncMock,
            return_value=_fake_claude_response(
                "approve",
                "Vendor history looks fine — proceeding to approve.",
            ),
        ):
            svc = APDecisionService(api_key="test-key")
            decision = asyncio.run(
                svc.decide(
                    invoice,
                    validation_gate={
                        "passed": False,
                        "reason_codes": ["confidence_field_review_required"],
                    },
                )
            )
        assert decision.recommendation == "escalate"
        assert decision.gate_override is True
        assert decision.original_recommendation == "approve"

    def test_fallback_path_is_also_wrapped_by_enforcement(self, monkeypatch):
        """Defensive: if _fallback_decision ever returned 'approve' with a
        failed gate (a bug), enforce_gate_constraint must catch it.

        This simulates the bug by monkey-patching _fallback_decision to
        return a broken 'approve' decision and then asserting the pipeline
        overrode it."""
        from clearledgr.services.ap_decision import APDecision, APDecisionService
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        def _broken_fallback(self, invoice, validation_gate, vendor_context_used=None, **kwargs):
            return APDecision(
                recommendation="approve",  # <-- the bug being simulated
                reasoning="Broken fallback ignored the gate.",
                confidence=0.97,
                info_needed=None,
                risk_flags=[],
                vendor_context_used=vendor_context_used or {},
                model="fallback",
                fallback=True,
            )

        monkeypatch.setattr(APDecisionService, "_fallback_decision", _broken_fallback)

        invoice = _make_invoice(confidence=0.97)
        svc = APDecisionService(api_key=None)
        decision = asyncio.run(
            svc.decide(
                invoice,
                validation_gate={
                    "passed": False,
                    "reason_codes": ["po_required_missing"],
                },
            )
        )
        assert decision.fallback is True
        assert decision.recommendation == "escalate"
        assert decision.gate_override is True
        assert decision.original_recommendation == "approve"

    def test_fallback_gate_failed_returns_escalate_natively(self, monkeypatch):
        """Control: the real fallback already handles failed gates by
        returning 'escalate' (step 2 in _fallback_decision), and the
        enforcement wrapper is a no-op on that path — gate_override=False."""
        from clearledgr.services.ap_decision import APDecisionService
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        invoice = _make_invoice(confidence=0.97)
        svc = APDecisionService(api_key=None)
        # Use a generic error code that the fallback does NOT special-case
        # (unlike po_required_missing → needs_info).
        decision = asyncio.run(
            svc.decide(
                invoice,
                validation_gate={
                    "passed": False,
                    "reason_codes": ["erp_preflight_vendor_not_found"],
                },
            )
        )
        assert decision.fallback is True
        assert decision.recommendation == "escalate"
        # enforcement was not needed — the fallback already escalated
        assert decision.gate_override is False


# ===========================================================================
# Layer 3a: _handle_get_ap_decision enforces even when LLM returns approve
# ===========================================================================

class TestGetAPDecisionHandlerEnforces:

    def test_failed_gate_threaded_explicitly_overrides_approve(self, tmp_path, monkeypatch):
        """When the planning loop threads a failed gate, approve → escalate."""
        from clearledgr.core.database import ClearledgrDB
        from clearledgr.core import database as db_module
        from clearledgr.core.skills.ap_skill import _handle_get_ap_decision
        from clearledgr.services.ap_decision import APDecisionService

        db = ClearledgrDB(db_path=str(tmp_path / "gate_h.db"))
        db.initialize()
        monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
        from clearledgr.core.llm_gateway import reset_llm_gateway
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        reset_llm_gateway()

        invoice = _make_invoice()
        invoice_dict = {
            "gmail_id": invoice.gmail_id,
            "subject": invoice.subject,
            "sender": invoice.sender,
            "vendor_name": invoice.vendor_name,
            "amount": invoice.amount,
            "currency": invoice.currency,
            "invoice_number": invoice.invoice_number,
            "due_date": invoice.due_date,
            "confidence": invoice.confidence,
            "organization_id": invoice.organization_id,
            "field_confidences": invoice.field_confidences,
        }

        # Provide decision_feedback via vendor_context so the handler doesn't
        # attempt a DB lookup whose method name is irrelevant to this test.
        vendor_context = {"decision_feedback": {}}

        with patch.object(
            APDecisionService,
            "_call_claude",
            new_callable=AsyncMock,
            return_value=_fake_claude_response(
                "approve",
                "Looks fine.",
            ),
        ):
            result = asyncio.run(
                _handle_get_ap_decision(
                    invoice_payload=invoice_dict,
                    vendor_context=vendor_context,
                    validation_gate={
                        "passed": False,
                        "reason_codes": ["po_required_missing"],
                    },
                    organization_id="org_gate_test",
                )
            )

        assert result["ok"] is True
        assert result["recommendation"] == "escalate"
        assert result["gate_override"] is True
        assert result["original_recommendation"] == "approve"
        # The handler returns the resolved gate so execute_routing can see it
        assert result["validation_gate"]["passed"] is False


# ===========================================================================
# Layer 3b: _handle_execute_routing enforces gate before building decision
# ===========================================================================

class TestExecuteRoutingHandlerEnforces:

    def test_failed_gate_threaded_forces_escalate_before_workflow_called(self, monkeypatch):
        """If LLM says 'approve' with a failed gate, execute_routing must not
        call process_new_invoice with an 'approve' decision — it must enforce
        first so the workflow sees 'escalate'."""
        from clearledgr.core.skills import ap_skill

        captured = {"decision": None, "invoice": None}

        class _FakeWorkflow:
            async def process_new_invoice(self, invoice, ap_decision=None):
                captured["decision"] = ap_decision
                captured["invoice"] = invoice
                return {
                    "status": "escalated",
                    "invoice_id": invoice.gmail_id,
                    "reason": "gate_override",
                }

        def _fake_get_workflow(org_id):
            return _FakeWorkflow()

        import clearledgr.services.invoice_workflow as iwm
        monkeypatch.setattr(iwm, "get_invoice_workflow", _fake_get_workflow)

        invoice_dict = {
            "gmail_id": "gmail_exec_routing_1",
            "subject": "Invoice INV-EXEC-1",
            "sender": "billing@gateexec.test",
            "vendor_name": "Gate Exec Vendor",
            "amount": 2500.0,
            "invoice_number": "INV-EXEC-1",
            "currency": "USD",
            "confidence": 0.98,
            "organization_id": "org_gate_test",
        }

        result = asyncio.run(
            ap_skill._handle_execute_routing(
                invoice_payload=invoice_dict,
                recommendation="approve",
                confidence=0.98,
                reason="Vendor is safe",
                risk_flags=[],
                validation_gate={
                    "passed": False,
                    "reason_codes": ["duplicate_invoice"],
                },
                organization_id="org_gate_test",
            )
        )

        assert result["ok"] is True
        assert result["recommendation"] == "escalate"
        assert result["original_recommendation"] == "approve"
        assert result["gate_override"] is True

        # Critical: the workflow must NOT have been called with an 'approve' decision.
        pre_computed = captured["decision"]
        assert pre_computed is not None
        assert pre_computed.recommendation == "escalate"
        assert pre_computed.gate_override is True
        assert pre_computed.original_recommendation == "approve"

    def test_passed_gate_allows_approve_through(self, monkeypatch):
        """Control: with a passing gate, 'approve' should reach the workflow."""
        from clearledgr.core.skills import ap_skill

        captured = {"decision": None}

        class _FakeWorkflow:
            async def process_new_invoice(self, invoice, ap_decision=None):
                captured["decision"] = ap_decision
                return {"status": "posted_to_erp", "invoice_id": invoice.gmail_id}

        import clearledgr.services.invoice_workflow as iwm
        monkeypatch.setattr(iwm, "get_invoice_workflow", lambda org_id: _FakeWorkflow())

        invoice_dict = {
            "gmail_id": "gmail_exec_routing_2",
            "subject": "Invoice INV-EXEC-2",
            "sender": "billing@clean.test",
            "vendor_name": "Clean Vendor",
            "amount": 1000.0,
            "invoice_number": "INV-EXEC-2",
            "currency": "USD",
            "confidence": 0.98,
            "organization_id": "org_gate_test",
        }

        result = asyncio.run(
            ap_skill._handle_execute_routing(
                invoice_payload=invoice_dict,
                recommendation="approve",
                confidence=0.98,
                reason="All signals green",
                risk_flags=[],
                validation_gate={"passed": True, "reason_codes": []},
                organization_id="org_gate_test",
            )
        )

        assert result["ok"] is True
        assert result["recommendation"] == "approve"
        assert result["gate_override"] is False
        assert captured["decision"].recommendation == "approve"
        assert captured["decision"].gate_override is False


# ===========================================================================
# Layer 3c: _handle_run_validation_gate returns correct passed field
# ===========================================================================

class TestRunValidationGateReturnsCorrectPassedField:
    """Regression test for the bug where the handler read gate.get('failed') —
    a key that doesn't exist in the gate dict — so passed was always True."""

    def test_handler_reports_failed_when_reason_codes_present(self, tmp_path, monkeypatch):
        from clearledgr.core.database import ClearledgrDB
        from clearledgr.core import database as db_module
        from clearledgr.core.skills.ap_skill import _handle_run_validation_gate

        db = ClearledgrDB(db_path=str(tmp_path / "gate_run.db"))
        db.initialize()
        monkeypatch.setattr(db_module, "_DB_INSTANCE", db)

        # Build an invoice that the gate will FAIL (missing invoice_number).
        invoice_dict = {
            "gmail_id": "gmail_run_gate_1",
            "subject": "Invoice missing number",
            "sender": "billing@missingnumber.test",
            "vendor_name": "Missing Number Vendor",
            "amount": 500.0,
            "invoice_number": "",  # empty → gate fails
            "currency": "USD",
            "confidence": 0.97,
            "organization_id": "org_gate_test",
        }

        result = asyncio.run(
            _handle_run_validation_gate(
                invoice_payload=invoice_dict,
                organization_id="org_gate_test",
            )
        )

        assert result["ok"] is True
        # The bug had this returning True. It must now be False.
        assert result["passed"] is False
        assert result["override_needed"] is True
        assert isinstance(result["reason_codes"], list) and len(result["reason_codes"]) > 0
        # The canonical gate object must be returned and usable downstream.
        vg = result["validation_gate"]
        assert vg["passed"] is False
        assert vg["reason_codes"] == result["reason_codes"]


# ===========================================================================
# Layer 4: process_new_invoice narrow-waist re-enforcement
# ===========================================================================

class TestProcessNewInvoiceNarrowWaistEnforcement:
    """Even if the planning-loop skill layer is bypassed, the workflow must
    re-enforce the gate on the pre-computed decision before routing."""

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

        # Force the deterministic gate to return FAILED regardless of inputs.
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

        # Stub out approval/HITL routing side-effects so we only exercise
        # the enforcement decision point.
        async def _fake_send_for_approval(invoice, **kwargs):
            return {
                "status": "pending_approval",
                "invoice_id": invoice.gmail_id,
                "reason": kwargs.get("decision_reason", "escalated_by_gate"),
            }

        monkeypatch.setattr(
            workflow, "_send_for_approval", _fake_send_for_approval
        )

        # Also stub the validation-gate-failure recording path which may
        # touch infrastructure not available in unit tests.
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

        # Build the invoice + a pre-computed 'approve' decision (mimicking
        # what the agent planning loop would hand over WITHOUT its own
        # enforcement running).
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
            recommendation="approve",  # <-- raw LLM approval, unchecked
            reasoning="Vendor has clean history and amount looks right.",
            confidence=0.98,
            info_needed=None,
            risk_flags=[],
            vendor_context_used={},
            model="agent_planning_loop",
            fallback=False,
        )

        result = asyncio.run(
            workflow.process_new_invoice(invoice, ap_decision=pre_computed)
        )

        # Expected: the narrow-waist enforcement flipped it to escalate-
        # pending_approval, NOT posted_to_erp.
        assert result.get("status") in {"pending_approval", "escalated"}
        assert result.get("status") != "posted_to_erp"

        # Audit event must have been emitted with the override details
        override_events = [
            e for e in captured_audit
            if (e.get("event_type") == "llm_gate_override_applied")
        ]
        assert len(override_events) == 1
        evt = override_events[0]
        assert evt["metadata"]["pre_override_recommendation"] == "approve"
        assert evt["metadata"]["enforced_recommendation"] == "escalate"
        assert "po_required_missing" in evt["metadata"]["gate_reason_codes"]
