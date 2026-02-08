"""Invoice extraction and categorization workflow."""
from typing import Dict

from clearledgr.agents import AgentContext, CategorizationAgent, ExceptionRoutingAgent, InvoiceExtractionAgent
from clearledgr.models.invoices import Invoice
from clearledgr.services.audit import AuditTrailService


class InvoiceWorkflow:
    def __init__(self, audit: AuditTrailService | None = None) -> None:
        self.audit = audit or AuditTrailService()
        self.extraction_agent = InvoiceExtractionAgent()
        self.categorization_agent = CategorizationAgent()
        self.exception_agent = ExceptionRoutingAgent()

    def run(self, input_state: Dict) -> Invoice:
        ctx = AgentContext(
            organization_id=input_state.get("organization_id"),
            requester=input_state.get("requester"),
            state=input_state,
            audit=self.audit,
        )
        self.extraction_agent.execute(ctx)
        self.categorization_agent.execute(ctx)

        extraction = ctx.state.get("invoice_extraction")
        categorization = ctx.state.get("invoice_categorization")
        invoice = Invoice(
            invoice_id=input_state.get("invoice_id") or "invoice_unknown",
            extraction=extraction,
            categorization=categorization,
            status="categorized",
        )

        ctx.state["invoice"] = invoice
        ctx.state["match_found"] = input_state.get("match_found", False)
        if not ctx.state["match_found"]:
            self.exception_agent.execute(ctx)
        return invoice
