"""Declarative + executable AP workflow definition.

This module provides a readable, top-level map of the AP invoice pipeline.
Each step names an ``APState``, the executor method that handles it, and the
success/failure target states.

The workflow is declarative *and executable*: ``APWorkflowExecutor``,
``dispatch_step()``, and ``run_invoice_entry_workflow()`` bind the declared
``AP_WORKFLOW_STEPS`` map to ``InvoiceWorkflowService`` runtime methods. The
service remains the implementation substrate, while this module is the
canonical workflow contract and dispatch layer used by the local durable
runtime/orchestration entrypoints.

Primary path:
    received -> validated -> needs_approval -> approved -> ready_to_post
             -> posted_to_erp -> closed

Exception paths:
    validated -> needs_info       (missing data)
    needs_approval -> rejected    (human rejects)
    ready_to_post -> failed_post  (ERP error)
    failed_post -> ready_to_post  (retry)
    needs_info -> validated       (resubmit)
"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from clearledgr.core.ap_states import APState, TERMINAL_STATES

if TYPE_CHECKING:  # pragma: no cover
    from clearledgr.services.invoice_workflow import InvoiceData, InvoiceWorkflowService


@dataclass(frozen=True)
class WorkflowStep:
    """A single step in the AP workflow.

    Attributes:
        state:          The APState this step handles.
        execute:        Method name on ``InvoiceWorkflowService`` that runs
                        when an item is in this state.
        on_success:     Target state when execution succeeds.
        on_failure:     Target state when execution fails.
        auto_threshold: Optional confidence threshold (0–1). If the invoice's
                        confidence meets or exceeds this value the step may
                        auto-advance without human intervention.
        description:    Human-readable explanation of the step.
    """

    state: APState
    execute: str
    on_success: APState
    on_failure: APState
    auto_threshold: Optional[float] = None
    description: str = ""


# ── The canonical AP workflow ────────────────────────────────────────────

AP_WORKFLOW_STEPS: List[WorkflowStep] = [
    WorkflowStep(
        state=APState.RECEIVED,
        execute="process_new_invoice",
        on_success=APState.VALIDATED,
        on_failure=APState.NEEDS_INFO,
        description="Extract data, classify email, validate required fields.",
    ),
    WorkflowStep(
        state=APState.VALIDATED,
        execute="_route_for_decision",
        on_success=APState.NEEDS_APPROVAL,
        on_failure=APState.NEEDS_INFO,
        auto_threshold=0.95,
        description="Route to auto-approve (>=95% confidence) or Slack approval queue.",
    ),
    WorkflowStep(
        state=APState.NEEDS_APPROVAL,
        execute="_await_human_decision",
        on_success=APState.APPROVED,
        on_failure=APState.REJECTED,
        description="Wait for human approve/reject via Slack or admin console.",
    ),
    WorkflowStep(
        state=APState.APPROVED,
        execute="_prepare_for_posting",
        on_success=APState.READY_TO_POST,
        on_failure=APState.APPROVED,
        description="Prepare ERP payload, resolve GL codes, validate posting readiness.",
    ),
    WorkflowStep(
        state=APState.READY_TO_POST,
        execute="_post_to_erp",
        on_success=APState.POSTED_TO_ERP,
        on_failure=APState.FAILED_POST,
        description="Post bill to ERP (API-first with browser-agent fallback).",
    ),
    WorkflowStep(
        state=APState.POSTED_TO_ERP,
        execute="_finalize",
        on_success=APState.CLOSED,
        on_failure=APState.POSTED_TO_ERP,
        description="Send confirmation, update audit trail, mark closed.",
    ),
]


# ── Lookup helpers ───────────────────────────────────────────────────────

_STEP_BY_STATE = {step.state: step for step in AP_WORKFLOW_STEPS}


def step_for_state(state: APState) -> Optional[WorkflowStep]:
    """Return the workflow step that handles ``state``, or ``None``."""
    return _STEP_BY_STATE.get(state)


def next_step(current: APState, success: bool = True) -> Optional[WorkflowStep]:
    """Return the workflow step that follows ``current``.

    If ``success`` is True, returns the step for ``on_success``.
    Otherwise returns the step for ``on_failure``.
    """
    step = _STEP_BY_STATE.get(current)
    if step is None:
        return None
    target = step.on_success if success else step.on_failure
    if target in TERMINAL_STATES:
        return None
    return _STEP_BY_STATE.get(target)


def workflow_summary() -> str:
    """Return a human-readable summary of the AP workflow."""
    lines = ["AP Workflow Steps", "=" * 50]
    for i, step in enumerate(AP_WORKFLOW_STEPS, 1):
        threshold = f" (auto >= {step.auto_threshold})" if step.auto_threshold else ""
        lines.append(
            f"{i}. [{step.state.value}] -> {step.execute}()"
            f" -> OK:{step.on_success.value} / FAIL:{step.on_failure.value}"
            f"{threshold}"
        )
        if step.description:
            lines.append(f"   {step.description}")
    return "\n".join(lines)


# ── Executable orchestration adapter ─────────────────────────────────────


@dataclass(frozen=True)
class WorkflowDispatchResult:
    """Structured result for an executed workflow step."""

    step: WorkflowStep
    status: str
    result: Dict[str, Any]
    current_state: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["step"] = {
            "state": self.step.state.value,
            "execute": self.step.execute,
            "on_success": self.step.on_success.value,
            "on_failure": self.step.on_failure.value,
            "auto_threshold": self.step.auto_threshold,
            "description": self.step.description,
        }
        return payload


class APWorkflowExecutor:
    """Executable adapter that binds declarative workflow steps to runtime code.

    This class wraps ``InvoiceWorkflowService`` so ``AP_WORKFLOW_STEPS`` is not
    merely documentation: callers can dispatch a step by AP state and this
    executor invokes the mapped implementation.
    """

    def __init__(self, service: "InvoiceWorkflowService"):
        self.service = service

    async def process_new_invoice(self, *, invoice: "InvoiceData", **_kwargs: Any) -> Dict[str, Any]:
        return await self.service.process_new_invoice(invoice)

    async def _route_for_decision(self, *, invoice: "InvoiceData", **_kwargs: Any) -> Dict[str, Any]:
        threshold = float(getattr(self.service, "auto_approve_threshold", 0.95))
        if float(getattr(invoice, "confidence", 0.0) or 0.0) >= threshold:
            return await self.service._auto_approve_and_post(invoice)
        return await self.service._send_for_approval(invoice)

    async def _await_human_decision(self, *, gmail_id: str, **_kwargs: Any) -> Dict[str, Any]:
        row = self.service.db.get_invoice_status(gmail_id)
        current_state = self.service._canonical_invoice_state(row)
        return {
            "status": "awaiting_human_decision",
            "gmail_id": gmail_id,
            "current_state": current_state,
            "terminal": current_state in {APState.REJECTED.value, APState.CLOSED.value},
        }

    async def _prepare_for_posting(self, *, gmail_id: str, **_kwargs: Any) -> Dict[str, Any]:
        ok = self.service._transition_invoice_state(gmail_id, APState.READY_TO_POST.value)
        row = self.service.db.get_invoice_status(gmail_id)
        return {
            "status": "ready_to_post" if ok else "error",
            "gmail_id": gmail_id,
            "current_state": self.service._canonical_invoice_state(row),
        }

    async def _post_to_erp(self, *, invoice: "InvoiceData", **_kwargs: Any) -> Dict[str, Any]:
        return await self.service._post_to_erp(invoice)

    async def _finalize(self, *, gmail_id: str, **_kwargs: Any) -> Dict[str, Any]:
        ok = self.service._transition_invoice_state(gmail_id, APState.CLOSED.value)
        row = self.service.db.get_invoice_status(gmail_id)
        return {
            "status": "closed" if ok else "error",
            "gmail_id": gmail_id,
            "current_state": self.service._canonical_invoice_state(row),
        }


def validate_workflow_bindings() -> Dict[str, Any]:
    """Return binding validation for the declarative workflow adapter."""
    missing: List[Dict[str, str]] = []
    for step in AP_WORKFLOW_STEPS:
        if not hasattr(APWorkflowExecutor, step.execute):
            missing.append({"state": step.state.value, "execute": step.execute})
    return {
        "valid": not missing,
        "step_count": len(AP_WORKFLOW_STEPS),
        "missing": missing,
    }


async def dispatch_step(
    service: "InvoiceWorkflowService",
    *,
    state: APState | str,
    **kwargs: Any,
) -> WorkflowDispatchResult:
    """Dispatch the declared AP workflow step for *state* via ``APWorkflowExecutor``."""
    normalized_state = state if isinstance(state, APState) else APState(str(state))
    step = step_for_state(normalized_state)
    if step is None:
        raise ValueError(f"No workflow step for state {normalized_state}")
    executor = APWorkflowExecutor(service)
    handler = getattr(executor, step.execute, None)
    if handler is None:
        raise RuntimeError(f"Workflow step handler not implemented: {step.execute}")
    result = await handler(**kwargs)
    current_state = None
    gmail_id = kwargs.get("gmail_id")
    invoice = kwargs.get("invoice")
    if not gmail_id and invoice is not None:
        gmail_id = getattr(invoice, "gmail_id", None)
    if gmail_id:
        row = service.db.get_invoice_status(str(gmail_id))
        current_state = service._canonical_invoice_state(row)
    result_dict = result if isinstance(result, dict) else {"result": result}
    status = str(result_dict.get("status") or "ok")
    return WorkflowDispatchResult(
        step=step,
        status=status,
        result=result_dict,
        current_state=current_state,
    )


async def run_invoice_entry_workflow(
    service: "InvoiceWorkflowService",
    invoice: "InvoiceData",
    *,
    workflow_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute the declared `received` step for an AP invoice entry workflow.

    This uses the declarative step map (`AP_WORKFLOW_STEPS`) as the dispatch
    source, making the workflow definition an executable orchestration layer.
    """
    dispatch = await dispatch_step(service, state=APState.RECEIVED, invoice=invoice)
    return {
        "workflow_id": workflow_id,
        "workflow_type": "ap_invoice_entry",
        "state": APState.RECEIVED.value,
        "dispatch": dispatch.to_dict(),
        "metadata": metadata or {},
    }
