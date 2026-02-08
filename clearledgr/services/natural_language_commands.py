"""
Natural Language Command Processor

Understands free-form instructions in Slack and converts them to actions.

Examples:
- "Approve all AWS invoices under $500"
- "Show me pending invoices from Stripe"
- "What did we pay Acme last month?"
- "Flag anything over $10,000 for review"

Architecture: Part of the REASONING LAYER
See: docs/AGENT_ARCHITECTURE.md

Changelog:
- 2026-01-23: Initial implementation
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from clearledgr.services.llm_multimodal import MultiModalLLMService
from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


class CommandIntent(Enum):
    """Recognized command intents."""
    APPROVE = "approve"
    REJECT = "reject"
    SHOW = "show"
    SEARCH = "search"
    SUMMARIZE = "summarize"
    FLAG = "flag"
    SET_RULE = "set_rule"
    PREDICT = "predict"
    HELP = "help"
    UNKNOWN = "unknown"


@dataclass
class ParsedCommand:
    """A parsed natural language command."""
    intent: CommandIntent
    entities: Dict[str, Any] = field(default_factory=dict)
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    original_text: str = ""
    clarification_needed: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value,
            "entities": self.entities,
            "conditions": self.conditions,
            "confidence": self.confidence,
            "clarification_needed": self.clarification_needed,
        }


@dataclass
class CommandResult:
    """Result of executing a command."""
    success: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    affected_items: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_prompt: Optional[str] = None


class NaturalLanguageProcessor:
    """
    Processes natural language commands from Slack.
    
    Usage:
        processor = NaturalLanguageProcessor("org_123")
        
        # Parse a command
        parsed = processor.parse("Approve all AWS invoices under $500")
        
        # Execute if confident
        if parsed.confidence > 0.8 and not parsed.clarification_needed:
            result = await processor.execute(parsed)
        else:
            # Ask for clarification
            print(parsed.clarification_needed)
    """
    
    # Pattern matching for common commands
    PATTERNS = {
        "approve": [
            r"approve\s+(?:all\s+)?(.+?)(?:\s+under\s+\$?([\d,]+))?$",
            r"(?:auto[- ]?)?approve\s+(.+)",
            r"okay\s+(?:the\s+)?(.+?)(?:\s+invoice)?",
        ],
        "reject": [
            r"reject\s+(?:all\s+)?(.+)",
            r"decline\s+(.+)",
            r"don'?t\s+(?:pay|process)\s+(.+)",
        ],
        "show": [
            r"show\s+(?:me\s+)?(?:all\s+)?(.+?)(?:\s+invoices?)?$",
            r"list\s+(?:all\s+)?(.+?)(?:\s+invoices?)?$",
            r"what(?:'s|\s+is|\s+are)\s+(.+)",
        ],
        "search": [
            r"find\s+(.+)",
            r"search\s+(?:for\s+)?(.+)",
            r"look\s+(?:for|up)\s+(.+)",
        ],
        "summarize": [
            r"summar(?:y|ize)\s+(?:of\s+)?(.+)",
            r"how\s+much\s+(?:did\s+we\s+)?(?:pay|spend)\s+(?:on\s+)?(.+)",
            r"total\s+(?:for\s+)?(.+)",
        ],
        "flag": [
            r"flag\s+(.+?)(?:\s+for\s+review)?$",
            r"mark\s+(.+?)\s+(?:for\s+)?review",
            r"hold\s+(.+)",
        ],
        "predict": [
            r"predict\s+(.+)",
            r"forecast\s+(.+)",
            r"what(?:'s|\s+is)\s+coming\s+(?:up|due)",
        ],
    }
    
    # Entity extraction patterns
    ENTITY_PATTERNS = {
        "vendor": r"(?:from\s+)?([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)",
        "amount_max": r"under\s+\$?([\d,]+(?:\.\d{2})?)",
        "amount_min": r"over\s+\$?([\d,]+(?:\.\d{2})?)",
        "amount_exact": r"\$?([\d,]+(?:\.\d{2})?)",
        "time_period": r"(?:last|this|past)\s+(\w+)",
        "status": r"(pending|approved|rejected|new|posted)",
        "count": r"(\d+)\s+(?:invoices?|items?)",
    }
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()
        self.llm = MultiModalLLMService()
    
    def parse(self, text: str) -> ParsedCommand:
        """
        Parse a natural language command.
        
        First tries pattern matching for common commands,
        then falls back to LLM for complex commands.
        """
        text = text.strip().lower()
        
        # Try pattern matching first (faster, more reliable for simple commands)
        for intent_name, patterns in self.PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    intent = CommandIntent(intent_name)
                    entities = self._extract_entities(text)
                    conditions = self._extract_conditions(text)
                    
                    return ParsedCommand(
                        intent=intent,
                        entities=entities,
                        conditions=conditions,
                        confidence=0.85,
                        original_text=text,
                    )
        
        # Fall back to LLM for complex commands
        return self._llm_parse(text)
    
    def _extract_entities(self, text: str) -> Dict[str, Any]:
        """Extract entities from text."""
        entities = {}
        
        # Extract vendor
        vendor_match = re.search(r"(?:from|for|to)\s+([A-Z][a-zA-Z]+(?:\s*(?:Inc|LLC|Ltd|Co)\.?)?)", text, re.IGNORECASE)
        if vendor_match:
            entities["vendor"] = vendor_match.group(1).strip()
        
        # Extract amounts
        amount_under = re.search(r"under\s+\$?([\d,]+(?:\.\d{2})?)", text)
        if amount_under:
            entities["amount_max"] = float(amount_under.group(1).replace(",", ""))
        
        amount_over = re.search(r"over\s+\$?([\d,]+(?:\.\d{2})?)", text)
        if amount_over:
            entities["amount_min"] = float(amount_over.group(1).replace(",", ""))
        
        # Extract time period
        time_match = re.search(r"(?:last|this|past)\s+(week|month|year|day|\d+\s*days?)", text)
        if time_match:
            entities["time_period"] = time_match.group(1)
        
        # Extract status
        status_match = re.search(r"\b(pending|approved|rejected|new|posted|unpaid|paid)\b", text)
        if status_match:
            entities["status"] = status_match.group(1)
        
        return entities
    
    def _extract_conditions(self, text: str) -> List[Dict[str, Any]]:
        """Extract conditions/filters from text."""
        conditions = []
        
        # Amount conditions
        if "under" in text:
            match = re.search(r"under\s+\$?([\d,]+)", text)
            if match:
                conditions.append({
                    "field": "amount",
                    "operator": "lt",
                    "value": float(match.group(1).replace(",", "")),
                })
        
        if "over" in text:
            match = re.search(r"over\s+\$?([\d,]+)", text)
            if match:
                conditions.append({
                    "field": "amount",
                    "operator": "gt",
                    "value": float(match.group(1).replace(",", "")),
                })
        
        # Vendor conditions
        vendor_match = re.search(r"(?:from|for)\s+([A-Z]\w+)", text, re.IGNORECASE)
        if vendor_match:
            conditions.append({
                "field": "vendor",
                "operator": "eq",
                "value": vendor_match.group(1),
            })
        
        return conditions
    
    def _llm_parse(self, text: str) -> ParsedCommand:
        """Use LLM to parse complex commands."""
        try:
            prompt = f"""Parse this natural language command for an AP (Accounts Payable) system.

COMMAND: "{text}"

Extract:
1. Intent: What does the user want to do?
   - approve: Approve invoices
   - reject: Reject/decline invoices  
   - show: Display/list invoices
   - search: Find specific invoices
   - summarize: Get totals or summaries
   - flag: Mark for review
   - predict: Forecast or predict
   - help: User needs help
   - unknown: Can't determine

2. Entities: Key information
   - vendor: Company name if mentioned
   - amount_min: Minimum amount if specified
   - amount_max: Maximum amount if specified
   - time_period: Time range if mentioned
   - status: Invoice status if mentioned

3. Conditions: Filters to apply

Return JSON:
{{
  "intent": "approve|reject|show|search|summarize|flag|predict|help|unknown",
  "entities": {{"vendor": "...", "amount_max": 500, ...}},
  "conditions": [{{"field": "amount", "operator": "lt", "value": 500}}],
  "confidence": 0.0-1.0,
  "clarification_needed": "Question to ask if unclear" or null
}}"""

            result = self.llm.generate_json(prompt)
            
            intent_str = result.get("intent", "unknown")
            try:
                intent = CommandIntent(intent_str)
            except:
                intent = CommandIntent.UNKNOWN
            
            return ParsedCommand(
                intent=intent,
                entities=result.get("entities", {}),
                conditions=result.get("conditions", []),
                confidence=result.get("confidence", 0.5),
                original_text=text,
                clarification_needed=result.get("clarification_needed"),
            )
            
        except Exception as e:
            logger.warning(f"LLM parsing failed: {e}")
            return ParsedCommand(
                intent=CommandIntent.UNKNOWN,
                confidence=0.0,
                original_text=text,
                clarification_needed="I didn't understand that command. Try: 'show pending invoices' or 'approve AWS invoices under $500'",
            )
    
    async def execute(self, command: ParsedCommand) -> CommandResult:
        """
        Execute a parsed command.
        
        For destructive actions (approve, reject), requires confirmation.
        """
        if command.intent == CommandIntent.APPROVE:
            return await self._execute_approve(command)
        
        elif command.intent == CommandIntent.REJECT:
            return await self._execute_reject(command)
        
        elif command.intent == CommandIntent.SHOW:
            return await self._execute_show(command)
        
        elif command.intent == CommandIntent.SEARCH:
            return await self._execute_search(command)
        
        elif command.intent == CommandIntent.SUMMARIZE:
            return await self._execute_summarize(command)
        
        elif command.intent == CommandIntent.FLAG:
            return await self._execute_flag(command)
        
        elif command.intent == CommandIntent.PREDICT:
            return await self._execute_predict(command)
        
        elif command.intent == CommandIntent.HELP:
            return self._execute_help(command)
        
        else:
            return CommandResult(
                success=False,
                message="I didn't understand that command.",
                requires_confirmation=False,
            )
    
    async def _execute_approve(self, command: ParsedCommand) -> CommandResult:
        """Execute an approve command."""
        # Find matching invoices
        invoices = self._find_matching_invoices(command)
        
        if not invoices:
            return CommandResult(
                success=False,
                message="No matching invoices found.",
            )
        
        # Calculate total
        total = sum(inv.get("amount", 0) for inv in invoices)
        
        # Require confirmation for bulk approve
        return CommandResult(
            success=True,
            message=f"Found {len(invoices)} invoices totaling ${total:,.2f}",
            data={
                "invoices": [
                    {"id": inv.get("id"), "vendor": inv.get("vendor"), "amount": inv.get("amount")}
                    for inv in invoices[:10]  # Show first 10
                ],
                "total_count": len(invoices),
                "total_amount": total,
            },
            affected_items=[inv.get("id") for inv in invoices],
            requires_confirmation=True,
            confirmation_prompt=f"Approve {len(invoices)} invoices totaling ${total:,.2f}?",
        )
    
    async def _execute_reject(self, command: ParsedCommand) -> CommandResult:
        """Execute a reject command."""
        invoices = self._find_matching_invoices(command)
        
        if not invoices:
            return CommandResult(
                success=False,
                message="No matching invoices found.",
            )
        
        return CommandResult(
            success=True,
            message=f"Found {len(invoices)} invoices to reject",
            affected_items=[inv.get("id") for inv in invoices],
            requires_confirmation=True,
            confirmation_prompt=f"Reject {len(invoices)} invoices?",
        )
    
    async def _execute_show(self, command: ParsedCommand) -> CommandResult:
        """Execute a show/list command."""
        invoices = self._find_matching_invoices(command)
        
        if not invoices:
            return CommandResult(
                success=True,
                message="No invoices found matching your criteria.",
                data={"invoices": []},
            )
        
        # Format for display
        invoice_list = []
        for inv in invoices[:20]:  # Limit to 20
            invoice_list.append({
                "vendor": inv.get("vendor", "Unknown"),
                "amount": inv.get("amount", 0),
                "status": inv.get("status", "unknown"),
                "date": inv.get("created_at", ""),
            })
        
        total = sum(inv.get("amount", 0) for inv in invoices)
        
        return CommandResult(
            success=True,
            message=f"Found {len(invoices)} invoices totaling ${total:,.2f}",
            data={
                "invoices": invoice_list,
                "total_count": len(invoices),
                "total_amount": total,
            },
        )
    
    async def _execute_search(self, command: ParsedCommand) -> CommandResult:
        """Execute a search command."""
        # Similar to show but might include more search logic
        return await self._execute_show(command)
    
    async def _execute_summarize(self, command: ParsedCommand) -> CommandResult:
        """Execute a summarize command."""
        vendor = command.entities.get("vendor")
        time_period = command.entities.get("time_period", "month")
        
        # Calculate days from time period
        days = {"day": 1, "week": 7, "month": 30, "year": 365}.get(time_period, 30)
        
        # Get spending data
        if vendor:
            invoices = self._find_matching_invoices(command)
            total = sum(inv.get("amount", 0) for inv in invoices)
            count = len(invoices)
            
            return CommandResult(
                success=True,
                message=f"Paid {vendor} ${total:,.2f} ({count} invoices) in the last {time_period}",
                data={
                    "vendor": vendor,
                    "total": total,
                    "count": count,
                    "period": time_period,
                },
            )
        else:
            # General summary
            return CommandResult(
                success=True,
                message=f"Summary for last {time_period}: Use '/clearledgr report' for full details",
            )
    
    async def _execute_flag(self, command: ParsedCommand) -> CommandResult:
        """Execute a flag command."""
        invoices = self._find_matching_invoices(command)
        
        if not invoices:
            return CommandResult(
                success=False,
                message="No matching invoices found to flag.",
            )
        
        return CommandResult(
            success=True,
            message=f"Will flag {len(invoices)} invoices for review",
            affected_items=[inv.get("id") for inv in invoices],
            requires_confirmation=True,
            confirmation_prompt=f"Flag {len(invoices)} invoices for review?",
        )
    
    async def _execute_predict(self, command: ParsedCommand) -> CommandResult:
        """Execute a predict/forecast command."""
        # Integrate with cash flow prediction
        return CommandResult(
            success=True,
            message="Based on recurring invoices, approximately $X,XXX due in the next 7 days. Use '/clearledgr forecast' for details.",
        )
    
    def _execute_help(self, command: ParsedCommand) -> CommandResult:
        """Return help information."""
        examples = [
            "• 'Approve all AWS invoices under $500'",
            "• 'Show pending invoices from Stripe'",
            "• 'How much did we pay Acme last month?'",
            "• 'Flag anything over $10,000 for review'",
            "• 'What invoices are due this week?'",
        ]
        
        return CommandResult(
            success=True,
            message="I can help with:\n" + "\n".join(examples),
        )
    
    def _find_matching_invoices(self, command: ParsedCommand) -> List[Dict[str, Any]]:
        """Find invoices matching the command criteria."""
        try:
            # Build query from conditions
            filters = {}
            
            for condition in command.conditions:
                field = condition.get("field")
                operator = condition.get("operator")
                value = condition.get("value")
                
                if field == "vendor" and operator == "eq":
                    filters["vendor"] = value
                elif field == "amount":
                    if operator == "lt":
                        filters["amount_max"] = value
                    elif operator == "gt":
                        filters["amount_min"] = value
            
            # Add entities as filters
            if "vendor" in command.entities:
                filters["vendor"] = command.entities["vendor"]
            if "status" in command.entities:
                filters["status"] = command.entities["status"]
            if "amount_max" in command.entities:
                filters["amount_max"] = command.entities["amount_max"]
            if "amount_min" in command.entities:
                filters["amount_min"] = command.entities["amount_min"]
            
            # Query database
            if hasattr(self.db, 'search_invoices'):
                return self.db.search_invoices(
                    organization_id=self.organization_id,
                    **filters,
                ) or []
            
        except Exception as e:
            logger.warning(f"Invoice search failed: {e}")
        
        return []


# Convenience function
def get_nlp_processor(organization_id: str = "default") -> NaturalLanguageProcessor:
    """Get a natural language processor instance."""
    return NaturalLanguageProcessor(organization_id=organization_id)
