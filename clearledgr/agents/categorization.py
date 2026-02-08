"""Categorization agent for GL codes with LLM enhancement."""
from typing import Dict, List, Optional

from clearledgr.agents.base import AgentContext, BaseAgent
from clearledgr.models.invoices import InvoiceCategorization, InvoiceExtraction


class CategorizationAgent(BaseAgent):
    name = "CategorizationAgent"

    def __init__(self, use_llm: bool = True) -> None:
        """Initialize categorization agent.
        
        Args:
            use_llm: Whether to use LLM-enhanced categorization (default: True)
        """
        self.use_llm = use_llm
        self._ai_service: Optional["EnhancedAIService"] = None

    @property
    def ai_service(self):
        """Lazy load AI service."""
        if self._ai_service is None and self.use_llm:
            try:
                from clearledgr.services.ai_enhanced import get_enhanced_ai_service
                self._ai_service = get_enhanced_ai_service()
            except ImportError:
                self._ai_service = None
        return self._ai_service

    def validate(self, ctx: AgentContext) -> None:
        if "invoice_extraction" not in ctx.state:
            raise ValueError("Missing invoice extraction for categorization")

    def execute(self, ctx: AgentContext) -> Dict:
        self.validate(ctx)
        extraction: InvoiceExtraction = ctx.state["invoice_extraction"]
        accounts = ctx.state.get("gl_accounts") or _default_accounts()
        historical_examples = ctx.state.get("historical_categorizations") or []

        # Try LLM-enhanced categorization first
        if self.ai_service:
            try:
                result = self.ai_service.categorize_transaction(
                    description=extraction.invoice_number or "",
                    vendor=extraction.vendor or "",
                    amount=float(extraction.total.amount) if extraction.total else 0,
                    gl_accounts=accounts,
                    historical_examples=historical_examples,
                )
                categorization = InvoiceCategorization(
                    gl_code=result.gl_code,
                    gl_name=result.gl_name,
                    confidence=result.confidence,
                )
                ctx.state["categorization_reasoning"] = result.reasoning
                ctx.state["categorization_alternatives"] = result.alternative_codes
            except Exception:
                # Fall back to rule-based
                suggestion = _suggest_account(extraction, accounts)
                categorization = InvoiceCategorization(
                    gl_code=suggestion.get("code"),
                    gl_name=suggestion.get("name"),
                    confidence=suggestion.get("confidence", 0.5),
                )
        else:
            # Rule-based categorization
            suggestion = _suggest_account(extraction, accounts)
            categorization = InvoiceCategorization(
                gl_code=suggestion.get("code"),
                gl_name=suggestion.get("name"),
                confidence=suggestion.get("confidence", 0.5),
            )

        ctx.state["invoice_categorization"] = categorization

        self.log_event(
            ctx,
            action="invoice_categorized",
            entity_type="invoice",
            metadata={
                "gl_code": categorization.gl_code, 
                "confidence": categorization.confidence,
                "used_llm": self.ai_service is not None
            },
        )
        return {"categorization": categorization}


def _suggest_account(extraction: InvoiceExtraction, accounts: List[Dict]) -> Dict:
    tokens = " ".join(
        [
            extraction.vendor or "",
            extraction.invoice_number or "",
        ]
    ).lower()
    best = {"code": "6900", "name": "Other Expenses", "confidence": 0.5}
    score_best = 0
    for account in accounts:
        score = 0
        for keyword in account.get("keywords", []):
            if keyword.lower() in tokens:
                score += 1
        if score > score_best:
            score_best = score
            best = {
                "code": account.get("code"),
                "name": account.get("name"),
                "confidence": min(0.5 + score * 0.1, 0.95),
            }
    return best


def _default_accounts() -> List[Dict]:
    return [
        {"code": "6000", "name": "Software & SaaS", "keywords": ["software", "subscription", "saas", "cloud"]},
        {"code": "6100", "name": "Professional Services", "keywords": ["consulting", "legal", "accounting"]},
        {"code": "6200", "name": "Marketing & Advertising", "keywords": ["marketing", "advertising", "ads"]},
        {"code": "6300", "name": "Office Supplies", "keywords": ["office", "supplies", "equipment"]},
        {"code": "6400", "name": "Travel & Entertainment", "keywords": ["travel", "flight", "hotel"]},
        {"code": "6500", "name": "Utilities", "keywords": ["utility", "electric", "water", "internet"]},
    ]
