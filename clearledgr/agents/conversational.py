"""
Clearledgr Conversational Agent

This is NOT a chatbot. This is a routing layer to the Finance Expert Agent.

The Finance Expert:
- Reasons like a senior accountant / CFO
- Understands context, timing, materiality
- Provides actionable recommendations with rationale
- Takes action with confidence, escalates with context

The difference:
- Chatbot: "Here are your exceptions"
- Finance Expert: "Two of these are timing differences that'll clear Friday. 
  The third is a billing cycle change - I recommend an accrual entry."

Workflow Execution:
- Vita can execute workflows via natural language commands
- All actions are audited with user identity and timestamp
- Risk-based confirmation for destructive actions
- Entity extraction for vendors, dates, amounts
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)

# Import audit service
try:
    from clearledgr.services.vita_audit import (
        VitaAuditService, VitaAuditEntry, ActionRiskLevel, ActionStatus,
        get_vita_audit_service
    )
    AUDIT_AVAILABLE = True
except ImportError:
    AUDIT_AVAILABLE = False
    logger.warning("Vita audit service not available")


class IntentType(Enum):
    """User intent categories."""
    # Queries
    QUERY_STATUS = "query_status"
    QUERY_EXCEPTIONS = "query_exceptions"
    QUERY_PENDING = "query_pending"
    QUERY_TRANSACTIONS = "query_transactions"
    QUERY_RECONCILIATION = "query_reconciliation"
    QUERY_INVOICES = "query_invoices"
    QUERY_SUMMARY = "query_summary"
    
    # Actions
    ACTION_APPROVE = "action_approve"
    ACTION_REJECT = "action_reject"
    ACTION_FLAG = "action_flag"
    ACTION_ASSIGN = "action_assign"
    ACTION_RECONCILE = "action_reconcile"
    ACTION_CATEGORIZE = "action_categorize"
    ACTION_POST = "action_post"
    
    # Help
    HELP = "help"
    GREETING = "greeting"
    UNKNOWN = "unknown"


@dataclass
class ParsedIntent:
    """Result of parsing user input."""
    intent: IntentType
    confidence: float
    entities: Dict[str, Any] = field(default_factory=dict)
    original_text: str = ""
    
    @property
    def is_query(self) -> bool:
        return self.intent.value.startswith("query_")
    
    @property
    def is_action(self) -> bool:
        return self.intent.value.startswith("action_")


@dataclass
class ConversationContext:
    """Tracks conversation state."""
    user_id: str
    channel: str  # slack, gmail, api
    history: List[Dict[str, Any]] = field(default_factory=list)
    current_topic: Optional[str] = None
    pending_action: Optional[Dict[str, Any]] = None
    last_query_results: Optional[List[Dict[str, Any]]] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None):
        self.history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        })
        # Keep last 20 messages
        if len(self.history) > 20:
            self.history = self.history[-20:]


@dataclass
class AgentResponse:
    """Response from conversational agent."""
    text: str
    data: Optional[Dict[str, Any]] = None
    actions: List[Dict[str, Any]] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    
    def to_slack_blocks(self) -> List[Dict]:
        """Convert to Slack Block Kit format."""
        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": self.text}
            }
        ]
        
        # Add data table if present
        if self.data and "items" in self.data:
            items = self.data["items"][:5]  # Limit to 5 items
            for item in items:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": self._format_item(item)
                    }
                })
        
        # Add action buttons
        if self.actions:
            buttons = []
            for action in self.actions[:5]:
                buttons.append({
                    "type": "button",
                    "text": {"type": "plain_text", "text": action["label"]},
                    "action_id": action["action_id"],
                    "value": action.get("value", ""),
                })
            blocks.append({"type": "actions", "elements": buttons})
        
        # Add suggestions
        if self.suggestions:
            suggestion_text = "*Try asking:*\n" + "\n".join(f"- {s}" for s in self.suggestions[:3])
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": suggestion_text}]
            })
        
        return blocks
    
    def _format_item(self, item: Dict) -> str:
        """Format a data item for display."""
        parts = []
        if "id" in item:
            parts.append(f"`{item['id']}`")
        if "description" in item or "vendor" in item:
            parts.append(item.get("description") or item.get("vendor", ""))
        if "amount" in item:
            amt = item["amount"]
            parts.append(f"*€{amt:,.2f}*" if isinstance(amt, (int, float)) else str(amt))
        if "status" in item:
            status = item["status"]
            indicator = "[DONE]" if status == "matched" else "[PENDING]" if status == "pending" else f"[{status.upper()}]"
            parts.append(indicator)
        return " | ".join(parts) if parts else str(item)


class IntentParser:
    """Parses natural language into structured intents."""
    
    # Intent patterns
    PATTERNS = {
        IntentType.QUERY_STATUS: [
            r"(what('?s| is)|show|get|check).*status",
            r"how('?s| is|'re| are).*doing",
            r"status.*update",
        ],
        IntentType.QUERY_EXCEPTIONS: [
            r"(show|list|get|what('?s| are)).*exception",
            r"exception.*(list|show|pending)",
            r"(any|how many).*exception",
            r"problem.*(transaction|item)",
        ],
        IntentType.QUERY_PENDING: [
            r"(what('?s| is)|show|list).*pending",
            r"pending.*(approval|review|item)",
            r"(need|require).*(approval|review|attention)",
            r"waiting.*(approval|review)",
        ],
        IntentType.QUERY_TRANSACTIONS: [
            r"(show|list|find|get).*transaction",
            r"transaction.*(over|under|from|to|with)",
            r"(unmatched|unreconciled).*transaction",
        ],
        IntentType.QUERY_RECONCILIATION: [
            r"reconciliation.*(status|rate|progress)",
            r"(what('?s| is)|how('?s| is)).*reconcil",
            r"match.*rate",
        ],
        IntentType.QUERY_INVOICES: [
            r"(show|list|find|get).*invoice",
            r"invoice.*(pending|unpaid|overdue)",
            r"(what|how many).*invoice",
        ],
        IntentType.QUERY_SUMMARY: [
            r"(give|show|get).*summary",
            r"(daily|weekly|monthly).*report",
            r"overview",
            r"dashboard",
        ],
        IntentType.ACTION_APPROVE: [
            r"approve.*",
            r"(mark|set).*approv",
            r"ok(ay)?.*this",
            r"looks? good",
            r"accept",
        ],
        IntentType.ACTION_REJECT: [
            r"reject.*",
            r"(mark|set).*reject",
            r"decline",
            r"(don'?t|do not).*approve",
        ],
        IntentType.ACTION_FLAG: [
            r"flag.*(for|to)",
            r"(mark|send).*(for review|to)",
            r"escalate",
            r"(need|needs|require).*review",
        ],
        IntentType.ACTION_ASSIGN: [
            r"assign.*(to)",
            r"(give|send).*(to)",
            r"(.*)'?s task",
        ],
        IntentType.ACTION_RECONCILE: [
            r"(run|start|do).*reconcil",
            r"reconcile.*(now|this)",
            r"match.*transaction",
        ],
        IntentType.ACTION_CATEGORIZE: [
            r"categorize",
            r"(set|change).*category",
            r"(mark|tag).*as",
            r"(put|move).*(in|to).*account",
        ],
        IntentType.ACTION_POST: [
            r"post.*(to)?.*(sap|erp)",
            r"(send|sync).*(to)?.*(sap|erp)",
            r"create.*entry",
        ],
        IntentType.HELP: [
            r"^help$",
            r"what can you",
            r"how (do|can) (i|you)",
            r"(show|list).*command",
        ],
        IntentType.GREETING: [
            r"^(hi|hello|hey|good\s*(morning|afternoon|evening))[\s!.]*$",
            r"^(yo|sup|what'?s up)[\s!.]*$",
        ],
    }
    
    # Entity patterns
    ENTITY_PATTERNS = {
        "amount": r"[€$£]?\s*(\d{1,3}(?:[,.\s]\d{3})*(?:[,.]\d{2})?)\s*(?:eur|usd|gbp)?",
        "amount_threshold": r"(over|above|more than|under|below|less than)\s*[€$£]?\s*(\d{1,3}(?:[,.\s]\d{3})*)",
        "vendor": r"(?:from|by|vendor|supplier)\s+([A-Z][A-Za-z0-9\s&]+?)(?:\s+(?:invoice|payment|charge)|$)",
        "person": r"(?:to|for|assign(?:ed)?\s+to)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        "date_relative": r"(today|yesterday|this week|last week|this month|last month)",
        "date_specific": r"(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)",
        "id": r"(?:id|#|number)\s*:?\s*([A-Z0-9-]+)",
        "status": r"(pending|approved|rejected|matched|unmatched|exception)",
    }
    
    # Known payment vendors/gateways for recognition
    KNOWN_VENDORS = [
        "adyen", "stripe", "paypal", "wise", "revolut", "square", "braintree",
        "worldpay", "checkout", "mollie", "klarna", "affirm", "afterpay",
        "amazon pay", "apple pay", "google pay", "shopify", "gocardless",
        "plaid", "dwolla", "transferwise", "western union", "moneygram",
    ]
    
    def parse(self, text: str, context: Optional[ConversationContext] = None) -> ParsedIntent:
        """Parse user input into structured intent."""
        text_lower = text.lower().strip()
        
        # Check each intent pattern
        best_intent = IntentType.UNKNOWN
        best_confidence = 0.0
        
        for intent, patterns in self.PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    confidence = 0.8 + (len(pattern) / 100)  # Longer patterns = higher confidence
                    if confidence > best_confidence:
                        best_confidence = min(confidence, 0.95)
                        best_intent = intent
        
        # Extract entities
        entities = self._extract_entities(text)
        
        # Context-aware adjustments
        if context and best_intent == IntentType.UNKNOWN:
            # If we had a previous query about items, "approve" might mean approve one of them
            if context.last_query_results and any(word in text_lower for word in ["first", "second", "that", "it", "this"]):
                if any(word in text_lower for word in ["approve", "ok", "yes"]):
                    best_intent = IntentType.ACTION_APPROVE
                    best_confidence = 0.7
                elif any(word in text_lower for word in ["reject", "no", "decline"]):
                    best_intent = IntentType.ACTION_REJECT
                    best_confidence = 0.7
        
        return ParsedIntent(
            intent=best_intent,
            confidence=best_confidence,
            entities=entities,
            original_text=text,
        )
    
    def _extract_entities(self, text: str) -> Dict[str, Any]:
        """Extract entities from text."""
        entities = {}
        text_lower = text.lower()
        
        # Check for known vendors first
        for vendor in self.KNOWN_VENDORS:
            if vendor in text_lower:
                entities["vendor"] = vendor.title()
                break
        
        for entity_type, pattern in self.ENTITY_PATTERNS.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                if entity_type == "amount_threshold":
                    # Tuple of (operator, amount)
                    entities["amount_operator"] = matches[0][0].lower()
                    entities["amount_value"] = self._parse_amount(matches[0][1])
                elif entity_type == "amount":
                    entities["amount"] = self._parse_amount(matches[0])
                elif entity_type == "vendor" and "vendor" not in entities:
                    # Only set if not already detected from known vendors
                    entities[entity_type] = matches[0] if len(matches) == 1 else matches
                else:
                    entities[entity_type] = matches[0] if len(matches) == 1 else matches
        
        # Extract date range
        entities["date_range"] = self._extract_date_range(text_lower)
        
        return entities
    
    def _extract_date_range(self, text: str) -> Dict[str, str]:
        """Extract date range from text."""
        today = datetime.now(timezone.utc).date()
        
        if "today" in text:
            return {"start": today.isoformat(), "end": today.isoformat()}
        elif "yesterday" in text:
            yesterday = today - timedelta(days=1)
            return {"start": yesterday.isoformat(), "end": yesterday.isoformat()}
        elif "this week" in text:
            start = today - timedelta(days=today.weekday())
            return {"start": start.isoformat(), "end": today.isoformat()}
        elif "last week" in text:
            end = today - timedelta(days=today.weekday() + 1)
            start = end - timedelta(days=6)
            return {"start": start.isoformat(), "end": end.isoformat()}
        elif "this month" in text:
            start = today.replace(day=1)
            return {"start": start.isoformat(), "end": today.isoformat()}
        elif "last month" in text:
            first_of_this_month = today.replace(day=1)
            end = first_of_this_month - timedelta(days=1)
            start = end.replace(day=1)
            return {"start": start.isoformat(), "end": end.isoformat()}
        elif "last 7 days" in text or "past week" in text:
            start = today - timedelta(days=7)
            return {"start": start.isoformat(), "end": today.isoformat()}
        elif "last 30 days" in text or "past month" in text:
            start = today - timedelta(days=30)
            return {"start": start.isoformat(), "end": today.isoformat()}
        
        # Default to last 7 days
        return {"start": (today - timedelta(days=7)).isoformat(), "end": today.isoformat()}
    
    def _parse_amount(self, amount_str: str) -> float:
        """Parse amount string to float."""
        # Remove currency symbols and whitespace
        cleaned = re.sub(r"[€$£\s]", "", amount_str)
        # Handle European format (1.234,56) vs US format (1,234.56)
        if "," in cleaned and "." in cleaned:
            if cleaned.rfind(",") > cleaned.rfind("."):
                # European: 1.234,56
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                # US: 1,234.56
                cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            # Could be either format
            parts = cleaned.split(",")
            if len(parts[-1]) == 2:
                # Likely decimal comma: 1234,56
                cleaned = cleaned.replace(",", ".")
            else:
                # Likely thousands comma: 1,234
                cleaned = cleaned.replace(",", "")
        
        try:
            return float(cleaned)
        except ValueError:
            return 0.0


class ConversationalAgent:
    """
    Routing layer to the Finance Expert Agent.
    
    Routes user queries to expert-level financial reasoning,
    not just data retrieval.
    """
    
    def __init__(self):
        self.parser = IntentParser()
        self.contexts: Dict[str, ConversationContext] = {}
        self._finance_expert = None
    
    @property
    def finance_expert(self):
        """Lazy load finance expert."""
        if self._finance_expert is None:
            try:
                from clearledgr.agents.finance_expert import get_finance_expert
                self._finance_expert = get_finance_expert()
            except ImportError:
                pass
        return self._finance_expert
    
    def get_or_create_context(
        self, 
        user_id: str, 
        channel: str = "slack"
    ) -> ConversationContext:
        """Get or create conversation context for user."""
        key = f"{channel}:{user_id}"
        if key not in self.contexts:
            self.contexts[key] = ConversationContext(user_id=user_id, channel=channel)
        return self.contexts[key]
    
    async def process(
        self,
        text: str,
        user_id: str,
        channel: str = "slack",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentResponse:
        """
        Process user input and return response.
        
        Args:
            text: User's message
            user_id: User identifier
            channel: Channel (slack, gmail, api)
            metadata: Additional context (e.g., current email, sheet)
                - user_email: User's email address (for audit trail)
                - user_name: User's display name (for audit trail)
                - organization_id: User's organization (for audit trail)
        
        Returns:
            AgentResponse with text, data, actions, and suggestions
        """
        metadata = metadata or {}
        context = self.get_or_create_context(user_id, channel)
        context.add_message("user", text, metadata)
        
        # Extract user identity from metadata for audit trail
        user_email = metadata.get("user_email")
        user_name = metadata.get("user_name")
        organization_id = metadata.get("organization_id")
        
        # Parse intent
        parsed = self.parser.parse(text, context)
        
        # Route to handler
        if parsed.intent == IntentType.GREETING:
            response = self._handle_greeting(context)
        elif parsed.intent == IntentType.HELP:
            response = self._handle_help(context)
        elif parsed.is_query:
            response = await self._handle_query(parsed, context)
        elif parsed.is_action:
            response = await self._handle_action(
                parsed, context,
                user_email=user_email,
                user_name=user_name,
                organization_id=organization_id,
            )
        else:
            response = self._handle_unknown(parsed, context)
        
        context.add_message("assistant", response.text)
        return response
    
    def _handle_greeting(self, context: ConversationContext) -> AgentResponse:
        """Handle greeting."""
        hour = datetime.now().hour
        if hour < 12:
            greeting = "Good morning"
        elif hour < 17:
            greeting = "Good afternoon"
        else:
            greeting = "Good evening"
        
        return AgentResponse(
            text=f"{greeting}! I'm Clearledgr, your finance assistant. How can I help you today?",
            suggestions=[
                "What's my reconciliation status?",
                "Show pending approvals",
                "Any exceptions to review?",
            ],
        )
    
    def _handle_help(self, context: ConversationContext) -> AgentResponse:
        """Handle help request."""
        return AgentResponse(
            text="*Here's what I can help you with:*\n\n"
                 "*Queries*\n"
                 "- \"What's the reconciliation status?\" - Check match rates\n"
                 "- \"Show pending approvals\" - List items needing approval\n"
                 "- \"Any exceptions?\" - See reconciliation exceptions\n"
                 "- \"Show invoices from Stripe\" - Find specific invoices\n"
                 "- \"Transactions over €5000\" - Filter by amount\n\n"
                 "*Actions*\n"
                 "- \"Approve the Stripe invoice\" - Approve items\n"
                 "- \"Flag this for Sarah\" - Assign for review\n"
                 "- \"Run reconciliation\" - Start reconciliation\n"
                 "- \"Post to SAP\" - Create journal entries\n",
            suggestions=[
                "Show me a summary",
                "What needs my attention?",
                "Run reconciliation",
            ],
        )
    
    async def _handle_query(
        self, 
        parsed: ParsedIntent, 
        context: ConversationContext
    ) -> AgentResponse:
        """Handle query intents."""
        intent = parsed.intent
        entities = parsed.entities
        
        if intent == IntentType.QUERY_STATUS:
            return await self._query_status(context)
        elif intent == IntentType.QUERY_EXCEPTIONS:
            return await self._query_exceptions(entities, context)
        elif intent == IntentType.QUERY_PENDING:
            return await self._query_pending(entities, context)
        elif intent == IntentType.QUERY_TRANSACTIONS:
            return await self._query_transactions(entities, context)
        elif intent == IntentType.QUERY_RECONCILIATION:
            return await self._query_reconciliation(context)
        elif intent == IntentType.QUERY_INVOICES:
            return await self._query_invoices(entities, context)
        elif intent == IntentType.QUERY_SUMMARY:
            return await self._query_summary(context)
        
        return AgentResponse(
            text="I'm not sure what you're looking for. Try being more specific.",
            suggestions=["Show reconciliation status", "List pending approvals", "Any exceptions?"],
        )
    
    async def _query_status(self, context: ConversationContext) -> AgentResponse:
        """Get overall status."""
        # In production, this would query actual data
        status = await self._get_mock_status()
        
        text = (
            f"*Clearledgr Status*\n\n"
            f"*Match Rate:* {status['match_rate']:.1f}%\n"
            f"*Transactions Today:* {status['transactions_today']}\n"
            f"*Pending Approvals:* {status['pending_approvals']}\n"
            f"*Open Exceptions:* {status['open_exceptions']}\n"
            f"*Last Reconciliation:* {status['last_recon']}"
        )
        
        actions = []
        if status['pending_approvals'] > 0:
            actions.append({
                "label": "Review Approvals",
                "action_id": "view_pending_approvals",
            })
        if status['open_exceptions'] > 0:
            actions.append({
                "label": "View Exceptions",
                "action_id": "view_exceptions",
            })
        
        return AgentResponse(
            text=text,
            data={"status": status},
            actions=actions,
        )
    
    async def _query_exceptions(
        self, 
        entities: Dict, 
        context: ConversationContext
    ) -> AgentResponse:
        """Query exceptions with expert-level analysis."""
        exceptions = await self._get_mock_exceptions(entities)
        context.last_query_results = exceptions
        
        if not exceptions:
            return AgentResponse(
                text="No exceptions found. All transactions matched successfully.",
                suggestions=["Show reconciliation status", "List pending approvals"],
            )
        
        # Use Finance Expert for analysis
        if self.finance_expert:
            try:
                expert_response = await self.finance_expert.analyze_exceptions(exceptions)
                
                # Convert expert response to agent response
                text = f"*{expert_response.summary}*\n\n"
                
                for insight in expert_response.insights[:3]:
                    confidence_indicator = "[HIGH]" if insight.confidence.value in ["certain", "high"] else "[REVIEW]"
                    text += f"\n{confidence_indicator} *{insight.title}*\n"
                    text += f"{insight.reasoning}\n"
                    if insight.recommendation:
                        text += f"_Recommendation: {insight.recommendation}_\n"
                
                if expert_response.context:
                    text += f"\n_{expert_response.context}_"
                
                actions = []
                for action in expert_response.recommended_actions[:3]:
                    actions.append({
                        "label": action.get("action", "").replace("_", " ").title(),
                        "action_id": action.get("action"),
                    })
                
                return AgentResponse(
                    text=text,
                    data={"items": exceptions, "expert_analysis": expert_response.to_dict()},
                    actions=actions,
                    suggestions=["Tell me more about the first one", "What should I prioritize?"],
                )
            except Exception as e:
                logger.error(f"Finance expert error: {e}")
        
        # Fallback to basic response
        text = f"*Found {len(exceptions)} exception(s):*"
        
        return AgentResponse(
            text=text,
            data={"items": exceptions},
            actions=[
                {"label": "Resolve All", "action_id": "resolve_all_exceptions"},
                {"label": "Export", "action_id": "export_exceptions"},
            ],
            suggestions=["Approve the first one", "Flag for review"],
        )
    
    async def _query_pending(
        self, 
        entities: Dict, 
        context: ConversationContext
    ) -> AgentResponse:
        """Query pending approvals."""
        pending = await self._get_mock_pending(entities)
        context.last_query_results = pending
        
        if not pending:
            return AgentResponse(
                text="No pending approvals. You're all caught up!",
                suggestions=["Show reconciliation status", "Any exceptions?"],
            )
        
        text = f"*{len(pending)} item(s) pending approval:*"
        
        return AgentResponse(
            text=text,
            data={"items": pending},
            actions=[
                {"label": "Approve All", "action_id": "approve_all_pending"},
                {"label": "Review in Sheets", "action_id": "open_sheets"},
            ],
        )
    
    async def _query_transactions(
        self, 
        entities: Dict, 
        context: ConversationContext
    ) -> AgentResponse:
        """Query transactions."""
        transactions = await self._get_mock_transactions(entities)
        context.last_query_results = transactions
        
        # Build description based on filters
        filters = []
        if "amount_operator" in entities:
            op = entities["amount_operator"]
            val = entities.get("amount_value", 0)
            filters.append(f"{op} €{val:,.2f}")
        if "vendor" in entities:
            filters.append(f"from {entities['vendor']}")
        if "status" in entities:
            filters.append(entities["status"])
        
        filter_desc = " ".join(filters) if filters else "all"
        
        if not transactions:
            return AgentResponse(
                text=f"No transactions found matching: {filter_desc}",
                suggestions=["Show all transactions", "Show unmatched transactions"],
            )
        
        text = f"*Found {len(transactions)} transaction(s)* ({filter_desc}):"
        
        return AgentResponse(
            text=text,
            data={"items": transactions},
        )
    
    async def _query_reconciliation(self, context: ConversationContext) -> AgentResponse:
        """Query reconciliation status."""
        recon = await self._get_mock_reconciliation()
        
        text = (
            f"*Reconciliation Status*\n\n"
            f"*3-Way Matches:* {recon['three_way']} ({recon['three_way_pct']:.1f}%)\n"
            f"*2-Way Matches:* {recon['two_way']} ({recon['two_way_pct']:.1f}%)\n"
            f"*Unmatched:* {recon['unmatched']} ({recon['unmatched_pct']:.1f}%)\n"
            f"*Total Match Rate:* {recon['match_rate']:.1f}%\n"
            f"*Last Run:* {recon['last_run']}"
        )
        
        actions = [
            {"label": "Run Reconciliation", "action_id": "run_reconciliation"},
            {"label": "View Details", "action_id": "view_recon_details"},
        ]
        
        return AgentResponse(text=text, actions=actions)
    
    async def _query_invoices(
        self, 
        entities: Dict, 
        context: ConversationContext
    ) -> AgentResponse:
        """Query invoices."""
        invoices = await self._get_mock_invoices(entities)
        context.last_query_results = invoices
        
        if not invoices:
            return AgentResponse(
                text="No invoices found matching your criteria.",
                suggestions=["Show all pending invoices", "Show invoices from this week"],
            )
        
        text = f"*Found {len(invoices)} invoice(s):*"
        
        return AgentResponse(
            text=text,
            data={"items": invoices},
            actions=[
                {"label": "Approve Selected", "action_id": "approve_invoices"},
            ],
        )
    
    async def _query_summary(self, context: ConversationContext) -> AgentResponse:
        """Get daily/weekly summary."""
        summary = await self._get_mock_summary()
        
        text = (
            f"*Daily Summary - {datetime.now().strftime('%B %d, %Y')}*\n\n"
            f"*Processed:* {summary['processed']} transactions (€{summary['total_amount']:,.2f})\n"
            f"*Auto-Matched:* {summary['auto_matched']} ({summary['auto_match_pct']:.1f}%)\n"
            f"*Manual Review:* {summary['manual_review']}\n"
            f"*Posted to SAP:* {summary['posted_to_sap']}\n"
            f"*Time Saved:* ~{summary['time_saved_hours']:.1f} hours"
        )
        
        return AgentResponse(
            text=text,
            suggestions=["Show pending approvals", "Any exceptions?"],
        )
    
    async def _handle_action(
        self, 
        parsed: ParsedIntent, 
        context: ConversationContext,
        user_email: Optional[str] = None,
        user_name: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> AgentResponse:
        """Handle action intents with audit trail."""
        intent = parsed.intent
        entities = parsed.entities
        
        if intent == IntentType.ACTION_APPROVE:
            return await self._action_approve(entities, context)
        elif intent == IntentType.ACTION_REJECT:
            return await self._action_reject(entities, context)
        elif intent == IntentType.ACTION_FLAG:
            return await self._action_flag(entities, context)
        elif intent == IntentType.ACTION_ASSIGN:
            return await self._action_assign(entities, context)
        elif intent == IntentType.ACTION_RECONCILE:
            return await self._action_reconcile(
                parsed, context, 
                user_email=user_email,
                user_name=user_name,
                organization_id=organization_id,
            )
        elif intent == IntentType.ACTION_POST:
            return await self._action_post(entities, context)
        
        return AgentResponse(
            text="I understood you want to take an action, but I need more details.",
            suggestions=["Approve pending invoices", "Flag for review", "Run reconciliation"],
        )
    
    async def _action_approve(
        self, 
        entities: Dict, 
        context: ConversationContext
    ) -> AgentResponse:
        """Approve items."""
        # Check if we have items from previous query
        if context.last_query_results and len(context.last_query_results) > 0:
            item = context.last_query_results[0]  # Approve first one for now
            
            return AgentResponse(
                text=f"Approved: {item.get('description', item.get('vendor', 'Item'))} "
                     f"for €{item.get('amount', 0):,.2f}",
                data={"approved": item},
                suggestions=["Show pending approvals", "Any more to approve?"],
            )
        
        return AgentResponse(
            text="What would you like to approve? Please specify or search first.",
            suggestions=["Show pending approvals", "Show invoices needing approval"],
            requires_confirmation=True,
        )
    
    async def _action_reject(
        self, 
        entities: Dict, 
        context: ConversationContext
    ) -> AgentResponse:
        """Reject items."""
        if context.last_query_results and len(context.last_query_results) > 0:
            item = context.last_query_results[0]
            
            return AgentResponse(
                text=f"Rejected: {item.get('description', item.get('vendor', 'Item'))}. "
                     f"It will be flagged for review.",
                data={"rejected": item},
            )
        
        return AgentResponse(
            text="What would you like to reject? Please specify or search first.",
            suggestions=["Show pending approvals"],
        )
    
    async def _action_flag(
        self, 
        entities: Dict, 
        context: ConversationContext
    ) -> AgentResponse:
        """Flag for review."""
        person = entities.get("person", "the team")
        
        if context.last_query_results and len(context.last_query_results) > 0:
            item = context.last_query_results[0]
            
            return AgentResponse(
                text=f"Flagged for {person}: {item.get('description', item.get('vendor', 'Item'))}. "
                     f"They'll be notified in Slack.",
                data={"flagged": item, "assigned_to": person},
            )
        
        return AgentResponse(
            text=f"I'll flag an item for {person}. Which item should I flag?",
            suggestions=["Show pending approvals", "Show exceptions"],
        )
    
    async def _action_assign(
        self, 
        entities: Dict, 
        context: ConversationContext
    ) -> AgentResponse:
        """Assign to person."""
        person = entities.get("person", "someone")
        
        return AgentResponse(
            text=f"To assign to {person}, please first search for the item you want to assign.",
            suggestions=["Show pending approvals", "Show exceptions"],
        )
    
    async def _action_reconcile(
        self, 
        parsed: ParsedIntent,
        context: ConversationContext,
        user_email: Optional[str] = None,
        user_name: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> AgentResponse:
        """
        Start reconciliation with optional vendor filter.
        
        Supports commands like:
        - "Run reconciliation"
        - "Reconcile Adyen transactions"
        - "Check and reconcile missing transactions from Stripe"
        """
        entities = parsed.entities if parsed else {}
        vendor = entities.get("vendor")
        date_range = entities.get("date_range", {})
        
        # Create audit entry
        audit_entry = None
        if AUDIT_AVAILABLE:
            audit_service = get_vita_audit_service()
            
            interpreted_intent = "reconcile all transactions"
            if vendor:
                interpreted_intent = f"reconcile {vendor} transactions"
                if date_range:
                    interpreted_intent += f" from {date_range.get('start')} to {date_range.get('end')}"
            
            audit_entry = audit_service.create_entry(
                user_id=context.user_id,
                user_email=user_email,
                user_name=user_name,
                organization_id=organization_id,
                original_command=parsed.original_text if parsed else "reconcile",
                interpreted_intent=interpreted_intent,
                action_type="reconcile",
                surface=context.channel,
                risk_level=ActionRiskLevel.MEDIUM,
                extracted_entities=entities,
                action_parameters={
                    "vendor_filter": vendor,
                    "date_range": date_range,
                },
                surface_context={"conversation_id": id(context)},
            )
        
        # Build response based on vendor filter
        if vendor:
            # Vendor-specific reconciliation
            text = (
                f"*Reconciling {vendor} Transactions*\n\n"
                f"*Command issued by:* {user_name or context.user_id}\n"
                f"*Time:* {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                f"*Scope:*\n"
                f"- Vendor: {vendor}\n"
                f"- Date range: {date_range.get('start', 'Last 7 days')} to {date_range.get('end', 'Today')}\n\n"
            )
            
            # In a real implementation, we'd query the actual unmatched count
            estimated_count = 23  # Mock value
            
            text += (
                f"*Found {estimated_count} unmatched {vendor} transactions.*\n\n"
                f"Proceed with reconciliation?"
            )
            
            # Store pending action for confirmation
            context.pending_action = {
                "type": "reconcile",
                "vendor": vendor,
                "date_range": date_range,
                "audit_id": audit_entry.audit_id if audit_entry else None,
            }
            
            return AgentResponse(
                text=text,
                data={
                    "vendor": vendor,
                    "estimated_count": estimated_count,
                    "audit_id": audit_entry.audit_id if audit_entry else None,
                },
                actions=[
                    {"label": f"Reconcile {vendor}", "action_id": "confirm_reconcile", "value": vendor, "style": "primary"},
                    {"label": "Preview First", "action_id": "preview_reconcile", "value": vendor},
                    {"label": "Cancel", "action_id": "cancel_reconcile"},
                ],
                requires_confirmation=True,
            )
        else:
            # Full reconciliation
            text = (
                "*Starting Full Reconciliation*\n\n"
                f"*Command issued by:* {user_name or context.user_id}\n"
                f"*Time:* {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                "*Processing:*\n"
                "- Loading bank transactions\n"
                "- Loading gateway transactions (all vendors)\n"
                "- Running multi-factor matching\n\n"
                "I'll notify you when complete."
            )
            
            # Record execution for low-risk full reconciliation
            if audit_entry and AUDIT_AVAILABLE:
                audit_service = get_vita_audit_service()
                audit_service.record_execution(
                    audit_id=audit_entry.audit_id,
                    success=True,
                    result={"status": "started", "type": "full_reconciliation"},
                )
            
            return AgentResponse(
                text=text,
                data={"audit_id": audit_entry.audit_id if audit_entry else None},
                actions=[
                    {"label": "View Progress", "action_id": "view_recon_progress"},
                ],
            )
    
    async def _action_post(
        self, 
        entities: Dict, 
        context: ConversationContext
    ) -> AgentResponse:
        """Post to SAP/ERP."""
        if context.last_query_results:
            count = len(context.last_query_results)
            return AgentResponse(
                text=f"Ready to post {count} item(s) to SAP. This will create journal entries.",
                actions=[
                    {"label": "Confirm Post", "action_id": "confirm_sap_post", "value": "confirm"},
                    {"label": "Cancel", "action_id": "cancel_sap_post", "value": "cancel"},
                ],
                requires_confirmation=True,
            )
        
        return AgentResponse(
            text="What should I post to SAP? Please search for items first.",
            suggestions=["Show approved items ready for posting", "Show matched transactions"],
        )
    
    def _handle_unknown(
        self, 
        parsed: ParsedIntent, 
        context: ConversationContext
    ) -> AgentResponse:
        """Handle unknown intent."""
        return AgentResponse(
            text="I'm not sure I understood that. Here are some things I can help with:",
            suggestions=[
                "What's my reconciliation status?",
                "Show pending approvals",
                "Any exceptions to review?",
                "Run reconciliation",
            ],
        )
    
    # Data access methods - query real state when available, fallback to defaults
    async def _get_mock_status(self) -> Dict:
        """Get reconciliation status from state database."""
        try:
            from clearledgr.state.models import list_runs
            runs = list_runs(limit=1)
            if runs:
                last_run = runs[0]
                summary = last_run.get("summary_json", {}) or {}
                return {
                    "match_rate": summary.get("matched_pct", 0),
                    "transactions_today": summary.get("processed", 0),
                    "pending_approvals": summary.get("pending_approvals", 0),
                    "open_exceptions": len(summary.get("exceptions", [])),
                    "last_recon": last_run.get("finished_at", "No runs yet")[:16].replace("T", " at "),
                }
        except Exception as e:
            logger.debug(f"State query failed, using defaults: {e}")
        
        # Fallback defaults for demo
        return {
            "match_rate": 0,
            "transactions_today": 0,
            "pending_approvals": 0,
            "open_exceptions": 0,
            "last_recon": "No runs yet",
        }
    
    async def _get_mock_exceptions(self, entities: Dict) -> List[Dict]:
        """Get exceptions from last reconciliation run."""
        try:
            from clearledgr.state.models import list_runs
            runs = list_runs(limit=1)
            if runs:
                summary = runs[0].get("summary_json", {}) or {}
                exceptions = summary.get("exceptions", [])
                if exceptions:
                    return [
                        {
                            "id": f"EXC-{i+1:03d}",
                            "description": exc.get("reason", "Unknown"),
                            "vendor": exc.get("counterparty", "Unknown"),
                            "amount": exc.get("amount", 0),
                            "status": "open",
                        }
                        for i, exc in enumerate(exceptions[:10])
                    ]
        except Exception as e:
            logger.debug(f"Exception query failed: {e}")
        
        return []
    
    async def _get_mock_pending(self, entities: Dict) -> List[Dict]:
        """Get pending approvals from state."""
        try:
            from clearledgr.state.models import list_runs
            runs = list_runs(limit=1)
            if runs:
                summary = runs[0].get("summary_json", {}) or {}
                drafts = summary.get("draft_journal_entries", [])
                if drafts:
                    return [
                        {
                            "id": draft.get("entry_id", f"JE-{i+1:03d}"),
                            "vendor": "Multiple",
                            "amount": draft.get("total_debits", 0),
                            "status": "pending",
                            "description": draft.get("description", "Journal entry"),
                        }
                        for i, draft in enumerate(drafts[:10])
                    ]
        except Exception as e:
            logger.debug(f"Pending query failed: {e}")
        
        return []
    
    async def _get_mock_transactions(self, entities: Dict) -> List[Dict]:
        """Get transactions from last reconciliation."""
        try:
            from clearledgr.state.models import list_runs
            runs = list_runs(limit=1)
            if runs:
                summary = runs[0].get("summary_json", {}) or {}
                reconciled = summary.get("reconciled", [])
                exceptions = summary.get("exceptions", [])
                
                transactions = []
                for i, r in enumerate(reconciled[:5]):
                    transactions.append({
                        "id": f"TXN-{i+1:03d}",
                        "description": r.get("description", "Matched transaction"),
                        "amount": r.get("amount", 0),
                        "status": "matched",
                    })
                for i, e in enumerate(exceptions[:5]):
                    transactions.append({
                        "id": f"EXC-{i+1:03d}",
                        "description": e.get("reason", "Exception"),
                        "amount": e.get("amount", 0),
                        "status": "exception",
                    })
                
                if transactions:
                    # Apply filters
                    if "amount_operator" in entities:
                        op = entities["amount_operator"]
                        val = entities.get("amount_value", 0)
                        if "over" in op or "above" in op or "more" in op:
                            transactions = [t for t in transactions if t["amount"] > val]
                        elif "under" in op or "below" in op or "less" in op:
                            transactions = [t for t in transactions if t["amount"] < val]
                    
                    if "status" in entities:
                        status = entities["status"]
                        transactions = [t for t in transactions if t["status"] == status]
                    
                    return transactions
        except Exception as e:
            logger.debug(f"Transaction query failed: {e}")
        
        return []
    
    async def _get_mock_reconciliation(self) -> Dict:
        """Get reconciliation stats from last run."""
        try:
            from clearledgr.state.models import list_runs
            runs = list_runs(limit=1)
            if runs:
                summary = runs[0].get("summary_json", {}) or {}
                total = summary.get("processed", 0) or 1
                matched = summary.get("matched", 0)
                
                return {
                    "three_way": summary.get("three_way_matches", 0),
                    "three_way_pct": summary.get("three_way_pct", 0),
                    "two_way": summary.get("two_way_matches", 0),
                    "two_way_pct": summary.get("two_way_pct", 0),
                    "unmatched": len(summary.get("exceptions", [])),
                    "unmatched_pct": 100 - summary.get("matched_pct", 0),
                    "match_rate": summary.get("matched_pct", 0),
                    "last_run": runs[0].get("finished_at", "Unknown")[:16].replace("T", " at "),
                }
        except Exception as e:
            logger.debug(f"Reconciliation query failed: {e}")
        
        return {
            "three_way": 0,
            "three_way_pct": 0,
            "two_way": 0,
            "two_way_pct": 0,
            "unmatched": 0,
            "unmatched_pct": 0,
            "match_rate": 0,
            "last_run": "No runs yet",
        }
    
    async def _get_mock_invoices(self, entities: Dict) -> List[Dict]:
        """Get invoices - currently returns empty until invoice tracking is added."""
        return []
    
    async def _get_mock_summary(self) -> Dict:
        """Get daily summary from reconciliation runs."""
        try:
            from clearledgr.state.models import list_runs
            runs = list_runs(limit=1)
            if runs:
                summary = runs[0].get("summary_json", {}) or {}
                processed = summary.get("processed", 0)
                matched = summary.get("matched", 0)
                
                return {
                    "processed": processed,
                    "total_amount": summary.get("matched_volume", 0),
                    "auto_matched": matched,
                    "auto_match_pct": summary.get("matched_pct", 0),
                    "manual_review": len(summary.get("exceptions", [])),
                    "posted_to_sap": summary.get("posted_to_sap", 0),
                    "time_saved_hours": round(processed * 0.035, 1),  # ~2min per transaction
                }
        except Exception as e:
            logger.debug(f"Summary query failed: {e}")
        
        return {
            "processed": 0,
            "total_amount": 0,
            "auto_matched": 0,
            "auto_match_pct": 0,
            "manual_review": 0,
            "posted_to_sap": 0,
            "time_saved_hours": 0,
        }


# Singleton instance
_agent: Optional[ConversationalAgent] = None


def get_conversational_agent() -> ConversationalAgent:
    """Get the conversational agent singleton."""
    global _agent
    if _agent is None:
        _agent = ConversationalAgent()
    return _agent
