"""Reconciliation matching agent."""
from typing import Dict

from clearledgr.agents.base import AgentContext, BaseAgent
from clearledgr.models.reconciliation import ReconciliationConfig
from clearledgr.services.matching import match_bank_to_gl


class ReconciliationMatchingAgent(BaseAgent):
    name = "ReconciliationMatchingAgent"

    def validate(self, ctx: AgentContext) -> None:
        if "bank_transactions" not in ctx.state or "gl_transactions" not in ctx.state:
            raise ValueError("Missing transactions for reconciliation")

    def execute(self, ctx: AgentContext) -> Dict:
        self.validate(ctx)
        config = ctx.state.get("config") or ReconciliationConfig()
        result = match_bank_to_gl(
            ctx.state["bank_transactions"],
            ctx.state["gl_transactions"],
            config,
        )
        ctx.state["reconciliation_result"] = result
        self.log_event(
            ctx,
            action="reconciliation_completed",
            entity_type="reconciliation",
            metadata={"match_rate": result.match_rate},
        )
        return {"result": result}
