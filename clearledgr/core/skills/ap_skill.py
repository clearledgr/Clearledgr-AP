"""AP (Accounts Payable) skill — four tools that wrap the existing AP pipeline.

Claude calls these tools during the planning loop instead of following the
hardcoded 8-step sequence in AgentOrchestrator._process_invoice_legacy().

Tool catalogue (in typical execution order):
  1. enrich_with_context  — fetch vendor history, correction suggestions, priority
  2. run_validation_gate  — deterministic confidence/PO/budget checks
  3. get_ap_decision      — call APDecisionService (Claude Sonnet) with full context
  4. execute_routing      — route based on recommendation; auto-approve or HITL pause

Each handler:
  - Accepts **kwargs (extra args from Claude are silently ignored).
  - Accepts organization_id injected by the runtime.
  - NEVER raises — returns {"ok": False, "error": "..."} on failure.
  - Sets {"is_hitl_pause": True} when a human decision is required (runtime maps to "awaiting_human").
"""
from __future__ import annotations

import json
import logging
import inspect
from typing import Any, Dict, List, Optional

from clearledgr.core.skills.base import AgentTool, AgentTask, FinanceSkill

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_invoice(invoice_payload: Dict[str, Any]):
    """Reconstruct InvoiceData from a serialised dict (best-effort)."""
    from clearledgr.services.invoice_workflow import InvoiceData
    valid_fields = InvoiceData.__dataclass_fields__.keys()
    filtered = {k: v for k, v in invoice_payload.items() if k in valid_fields}
    return InvoiceData(**filtered)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def _handle_enrich_with_context(
    invoice_payload: Dict[str, Any],
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Fetch vendor history, correction learning, and priority for the invoice."""
    try:
        from clearledgr.core.database import get_db
        from clearledgr.services.correction_learning import get_correction_learning_service

        invoice = _build_invoice(invoice_payload)
        db = get_db()

        vendor_profile = db.get_vendor_profile(organization_id, invoice.vendor_name) or {}
        vendor_history = db.get_vendor_invoice_history(organization_id, invoice.vendor_name, limit=6) or []
        decision_feedback = db.get_vendor_decision_feedback(organization_id, invoice.vendor_name) or []

        correction_svc = get_correction_learning_service(organization_id)
        correction_suggestions = correction_svc.suggest(invoice.vendor_name, invoice.amount)

        return {
            "ok": True,
            "vendor_profile": vendor_profile,
            "vendor_history": vendor_history[:6],
            "decision_feedback": decision_feedback[:10],
            "correction_suggestions": correction_suggestions,
            "vendor_name": invoice.vendor_name,
            "amount": invoice.amount,
        }
    except Exception as exc:
        logger.warning("[APSkill] enrich_with_context failed: %s", exc)
        return {"ok": False, "error": str(exc)}


async def _handle_run_validation_gate(
    invoice_payload: Dict[str, Any],
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Run deterministic validation: confidence threshold, PO check, budget gate."""
    try:
        from clearledgr.services.invoice_workflow import get_invoice_workflow

        invoice = _build_invoice(invoice_payload)
        workflow = get_invoice_workflow(organization_id)
        gate = workflow._evaluate_deterministic_validation(invoice)
        passed = not gate.get("failed", False)
        return {
            "ok": True,
            "passed": passed,
            "gate_result": gate,
            "failures": gate.get("failures", []),
            "override_needed": not passed,
        }
    except Exception as exc:
        logger.warning("[APSkill] run_validation_gate failed: %s", exc)
        return {"ok": False, "error": str(exc)}


async def _handle_get_ap_decision(
    invoice_payload: Dict[str, Any],
    vendor_context: Optional[Dict[str, Any]] = None,
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Call APDecisionService (Claude Sonnet) with full vendor context."""
    try:
        from clearledgr.services.ap_decision import APDecisionService
        from clearledgr.core.database import get_db

        invoice = _build_invoice(invoice_payload)
        db = get_db()
        ctx = vendor_context or {}

        vendor_profile = ctx.get("vendor_profile") or db.get_vendor_profile(organization_id, invoice.vendor_name) or {}
        vendor_history = ctx.get("vendor_history") or db.get_vendor_invoice_history(organization_id, invoice.vendor_name, limit=6) or []
        decision_feedback = ctx.get("decision_feedback") or db.get_vendor_decision_feedback(organization_id, invoice.vendor_name) or []
        correction_suggestions = ctx.get("correction_suggestions") or []

        service = APDecisionService()
        decision_or_awaitable = service.decide(
            invoice=invoice,
            vendor_profile=vendor_profile,
            vendor_history=vendor_history,
            decision_feedback=decision_feedback,
            correction_suggestions=correction_suggestions,
            org_config={"organization_id": organization_id},
        )
        decision = (
            await decision_or_awaitable
            if inspect.isawaitable(decision_or_awaitable)
            else decision_or_awaitable
        )
        return {
            "ok": True,
            "recommendation": decision.recommendation,
            "reasoning": decision.reasoning,
            "confidence": decision.confidence,
            "risk_flags": decision.risk_flags,
            "info_needed": decision.info_needed,
        }
    except Exception as exc:
        logger.warning("[APSkill] get_ap_decision failed: %s", exc)
        return {"ok": False, "error": str(exc), "recommendation": "escalate"}


async def _handle_execute_routing(
    invoice_payload: Dict[str, Any],
    recommendation: str = "escalate",
    confidence: float = 0.0,
    reason: str = "",
    organization_id: str = "default",
    **_kwargs,
) -> Dict[str, Any]:
    """Route invoice: auto-approve or request human review (HITL pause).

    Sets invoice.confidence based on the recommendation before handing off
    to process_new_invoice, which determines the actual routing path.
    """
    try:
        from clearledgr.services.invoice_workflow import get_invoice_workflow

        invoice = _build_invoice(invoice_payload)
        workflow = get_invoice_workflow(organization_id)

        # Map recommendation → confidence that controls workflow routing
        APPROVAL_THRESHOLD = float(
            __import__("os").getenv("INVOICE_AUTO_APPROVE_THRESHOLD", "0.95")
        )
        if recommendation == "approve" and confidence >= APPROVAL_THRESHOLD:
            invoice.confidence = max(confidence, APPROVAL_THRESHOLD)
        else:
            # Anything below threshold → workflow sends for human review
            invoice.confidence = min(confidence, APPROVAL_THRESHOLD - 0.01)

        result = await workflow.process_new_invoice(invoice)
        needs_human = result.get("status") in (
            "pending_approval", "needs_info", "escalated", "failed"
        )
        return {
            "ok": True,
            "status": result.get("status"),
            "invoice_id": result.get("invoice_id"),
            "recommendation": recommendation,
            "is_hitl_pause": needs_human,
            "hitl_context": {
                "invoice_id": result.get("invoice_id"),
                "recommendation": recommendation,
                "reason": reason or result.get("reason", ""),
                "status": result.get("status"),
            } if needs_human else None,
        }
    except Exception as exc:
        logger.warning("[APSkill] execute_routing failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# APSkill
# ---------------------------------------------------------------------------

class APSkill(FinanceSkill):
    """AP domain skill — wraps InvoiceWorkflowService for use in the planning loop."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id

    @property
    def skill_name(self) -> str:
        return "ap_invoice_processing"

    def get_tools(self) -> List[AgentTool]:
        return [
            AgentTool(
                name="enrich_with_context",
                description=(
                    "Fetch vendor history, prior approval decisions, and GL correction "
                    "suggestions for this invoice. Call this first to understand the vendor."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "invoice_payload": {
                            "type": "object",
                            "description": "The full invoice data dict.",
                        }
                    },
                    "required": ["invoice_payload"],
                },
                handler=_handle_enrich_with_context,
            ),
            AgentTool(
                name="run_validation_gate",
                description=(
                    "Run deterministic validation: confidence threshold, PO number check, "
                    "and budget gate. If this fails, escalate to human — do not auto-approve."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "invoice_payload": {
                            "type": "object",
                            "description": "The full invoice data dict.",
                        }
                    },
                    "required": ["invoice_payload"],
                },
                handler=_handle_run_validation_gate,
            ),
            AgentTool(
                name="get_ap_decision",
                description=(
                    "Call the AP decision AI with full vendor context to get a recommendation: "
                    "approve, needs_info, escalate, or reject. Use vendor_context from "
                    "enrich_with_context if available."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "invoice_payload": {
                            "type": "object",
                            "description": "The full invoice data dict.",
                        },
                        "vendor_context": {
                            "type": "object",
                            "description": "Vendor enrichment from enrich_with_context (optional).",
                        },
                    },
                    "required": ["invoice_payload"],
                },
                handler=_handle_get_ap_decision,
            ),
            AgentTool(
                name="execute_routing",
                description=(
                    "Route the invoice based on the AP decision recommendation. "
                    "If recommendation is 'approve' with high confidence, auto-approves and posts to ERP. "
                    "Otherwise routes for human review (pauses for HITL). "
                    "Always call this as the final step."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "invoice_payload": {
                            "type": "object",
                            "description": "The full invoice data dict.",
                        },
                        "recommendation": {
                            "type": "string",
                            "enum": ["approve", "needs_info", "escalate", "reject"],
                            "description": "The AP decision recommendation.",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence score 0.0–1.0 from get_ap_decision.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for the routing decision.",
                        },
                    },
                    "required": ["invoice_payload", "recommendation"],
                },
                handler=_handle_execute_routing,
            ),
        ]

    def build_system_prompt(self, task: AgentTask) -> str:
        payload = task.payload
        invoice = payload.get("invoice", {})
        vendor = invoice.get("vendor_name", "Unknown")
        amount = invoice.get("amount", 0)
        currency = invoice.get("currency", "USD")
        confidence = invoice.get("confidence", 0)

        return f"""You are the Clearledgr AP agent processing an invoice for approval.

Invoice summary:
- Vendor: {vendor}
- Amount: {currency} {amount:,.2f}
- Extraction confidence: {confidence:.0%}
- Organization: {task.organization_id}

Your job: process this invoice through AP review using the available tools.

Recommended sequence:
1. Call enrich_with_context to understand the vendor relationship
2. Call run_validation_gate to check hard rules
3. Call get_ap_decision with the vendor context to get a recommendation
4. Call execute_routing with the recommendation to complete processing

Rules:
- NEVER skip run_validation_gate — it is a hard guardrail
- If validation gate fails, set recommendation to "escalate" in execute_routing
- NEVER auto-approve without calling get_ap_decision first
- NEVER reject without human sign-off — use "escalate" instead
- After calling execute_routing, you are done — do not call more tools

When you are finished, respond with a brief summary of what was decided and why."""
