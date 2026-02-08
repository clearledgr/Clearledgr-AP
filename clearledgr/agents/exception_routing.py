"""Exception routing agent."""
from typing import Dict

from clearledgr.agents.base import AgentContext, BaseAgent
from clearledgr.services.exception_routing import ExceptionRoutingService


class ExceptionRoutingAgent(BaseAgent):
    name = "ExceptionRoutingAgent"

    def __init__(self, router: ExceptionRoutingService | None = None) -> None:
        self.router = router or ExceptionRoutingService()

    def validate(self, ctx: AgentContext) -> None:
        if "reconciliation_result" not in ctx.state and "invoice_extraction" not in ctx.state:
            raise ValueError("No exception source available")

    def execute(self, ctx: AgentContext) -> Dict:
        self.validate(ctx)

        reconciliation = ctx.state.get("reconciliation_result")
        if reconciliation and reconciliation.exceptions:
            task = self.router.route_invoice_exception(
                title="Reconciliation exception review",
                description="; ".join(reconciliation.exceptions),
                organization_id=ctx.organization_id,
                requester=ctx.requester,
                metadata={
                    "amount": None,
                    "vendor": None,
                    "email_subject": "Reconciliation exception",
                },
            )
            ctx.state["exception_task"] = task
            self.log_event(
                ctx,
                action="exception_routed",
                entity_type="reconciliation",
                metadata={"task_id": task.get("task_id")},
            )
            return {"task": task}

        extraction = ctx.state.get("invoice_extraction")
        if extraction and not ctx.state.get("match_found"):
            task = self.router.route_invoice_exception(
                title=f"Invoice exception: {extraction.vendor or 'Unknown vendor'}",
                description="Invoice requires review or matching.",
                organization_id=ctx.organization_id,
                requester=ctx.requester,
                metadata={
                    "vendor": extraction.vendor,
                    "amount": extraction.total.amount if extraction.total else None,
                    "email_subject": ctx.state.get("email_subject"),
                    "email_sender": ctx.state.get("email_sender"),
                },
            )
            ctx.state["exception_task"] = task
            self.log_event(
                ctx,
                action="exception_routed",
                entity_type="invoice",
                metadata={"task_id": task.get("task_id")},
            )
            return {"task": task}

        return {"task": None}
