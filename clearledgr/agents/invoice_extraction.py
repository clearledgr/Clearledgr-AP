"""Invoice extraction agent using multi-modal LLM and local parsing."""
from typing import Dict, Optional

from clearledgr.agents.base import AgentContext, BaseAgent
from clearledgr.models.invoices import InvoiceExtraction
from clearledgr.models.transactions import Money
from clearledgr.services.email_parser import EmailParser


class InvoiceExtractionAgent(BaseAgent):
    name = "InvoiceExtractionAgent"

    def __init__(self) -> None:
        self.parser = EmailParser()

    def validate(self, ctx: AgentContext) -> None:
        if not ctx.state.get("email_subject") and not ctx.state.get("email_body"):
            raise ValueError("Missing email content for extraction")

    def execute(self, ctx: AgentContext) -> Dict:
        self.validate(ctx)
        email_subject = ctx.state.get("email_subject") or ""
        email_body = ctx.state.get("email_body") or ""
        email_sender = ctx.state.get("email_sender") or ""
        attachments = ctx.state.get("attachments") or []

        parsed = self.parser.parse_email(
            subject=email_subject,
            body=email_body,
            sender=email_sender,
            attachments=attachments,
        )

        extraction = InvoiceExtraction(
            vendor=parsed.get("vendor"),
            invoice_number=parsed.get("primary_invoice"),
            invoice_date=_parse_date(parsed.get("primary_date")),
            total=_build_money(parsed.get("primary_amount"), parsed.get("currency")),
            currency=parsed.get("currency"),
            confidence=min(1.0, parsed.get("confidence", 0.0)),
            metadata={"source": "local_parser"},
        )

        llm = ctx.state.get("llm_service")
        if llm:
            try:
                llm_result = llm.extract_invoice(email_subject + "\n" + email_body, attachments)
                extraction = _merge_llm_extraction(extraction, llm_result)
                extraction.metadata["llm_provider"] = llm_result.get("provider")
            except Exception as exc:  # noqa: BLE001
                extraction.metadata["llm_error"] = str(exc)

        ctx.state["invoice_extraction"] = extraction
        self.log_event(
            ctx,
            action="invoice_extracted",
            entity_type="invoice",
            metadata={"confidence": extraction.confidence},
        )
        return {"extraction": extraction}


def _build_money(amount: Optional[float], currency: Optional[str]) -> Optional[Money]:
    if amount is None:
        return None
    return Money(amount=float(amount), currency=currency or "EUR")


def _parse_date(value: Optional[str]):
    if not value:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(value).date()
    except Exception:  # noqa: BLE001
        return None


def _merge_llm_extraction(base: InvoiceExtraction, llm_result: Dict) -> InvoiceExtraction:
    confidence = llm_result.get("confidence")
    total_amount = llm_result.get("total_amount")
    currency = llm_result.get("currency") or base.currency

    return InvoiceExtraction(
        vendor=llm_result.get("vendor") or base.vendor,
        invoice_number=llm_result.get("invoice_number") or base.invoice_number,
        invoice_date=_parse_date(llm_result.get("invoice_date")) or base.invoice_date,
        due_date=_parse_date(llm_result.get("due_date")) or base.due_date,
        total=_build_money(total_amount, currency) or base.total,
        currency=currency,
        confidence=min(1.0, float(confidence)) if confidence is not None else base.confidence,
        line_items=base.line_items,
        metadata={**base.metadata, "source": "llm"},
    )
