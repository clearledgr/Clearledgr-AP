"""
Conversational Agent Service

Enables the agent to ask clarifying questions via Slack when uncertain,
and use responses to make better decisions.

Architecture: Part of the REASONING LAYER
See: docs/AGENT_ARCHITECTURE.md

Flow:
1. Agent detects uncertainty (low confidence, missing info, ambiguity)
2. Agent generates clarifying question with response options
3. Question sent to Slack with interactive buttons
4. User responds
5. Agent uses response to update decision

Changelog:
- 2026-01-23: Initial implementation
"""

import logging
import uuid
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from clearledgr.core.database import get_db
from clearledgr.services.slack_api import SlackAPIClient, get_slack_client

logger = logging.getLogger(__name__)


class QuestionType(Enum):
    """Types of clarifying questions the agent can ask."""
    CONFIRM_VENDOR = "confirm_vendor"
    CONFIRM_AMOUNT = "confirm_amount"
    CONFIRM_GL_CODE = "confirm_gl_code"
    CONFIRM_DUPLICATE = "confirm_duplicate"
    CLASSIFY_DOCUMENT = "classify_document"
    MISSING_INFO = "missing_info"
    CUSTOM = "custom"


@dataclass
class ClarifyingQuestion:
    """A question the agent needs answered."""
    question_id: str
    question_type: QuestionType
    question_text: str
    context: str  # Why is the agent asking?
    options: List[Dict[str, Any]]  # Response options
    invoice_id: str
    urgency: str = "normal"  # "urgent", "normal", "low"
    default_action: Optional[str] = None  # What to do if no response
    timeout_hours: int = 24
    
    def to_slack_blocks(self) -> List[Dict[str, Any]]:
        """Convert to Slack Block Kit blocks."""
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Clearledgr needs your input"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{self.question_text}*"
                }
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"_{self.context}_"}
                ]
            },
            {"type": "divider"},
        ]
        
        # Add response buttons
        button_elements = []
        for i, option in enumerate(self.options[:5]):  # Max 5 buttons
            style = "primary" if option.get("recommended") else None
            button = {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": option.get("label", f"Option {i+1}")
                },
                "action_id": f"clarify_{self.question_id}_{option.get('value', i)}",
                "value": f"{self.question_id}:{option.get('value', i)}",
            }
            if style:
                button["style"] = style
            button_elements.append(button)
        
        if button_elements:
            blocks.append({
                "type": "actions",
                "elements": button_elements
            })
        
        # Add timeout info
        if self.default_action:
            blocks.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": f":clock1: If no response in {self.timeout_hours}h, will {self.default_action}"
                }]
            })
        
        return blocks


@dataclass
class ConversationState:
    """Tracks the state of a conversation with the user."""
    conversation_id: str
    invoice_id: str
    organization_id: str
    questions_asked: List[ClarifyingQuestion] = field(default_factory=list)
    responses_received: Dict[str, Any] = field(default_factory=dict)
    status: str = "active"  # "active", "resolved", "timed_out"
    created_at: str = ""
    updated_at: str = ""


class ConversationalAgent:
    """
    Manages conversational interactions with users via Slack.
    
    Usage:
        agent = ConversationalAgent("org_123")
        
        # When uncertain, ask a question
        question = agent.create_question(
            question_type=QuestionType.CONFIRM_VENDOR,
            invoice_id="gmail_123",
            extracted_vendor="Stripe Inc",
            alternatives=["Stripe", "Stripe Payments"]
        )
        await agent.ask_question(question, channel="#finance-approvals")
        
        # When user responds
        agent.handle_response(question_id, response_value)
    """
    
    def __init__(self, organization_id: str = "default"):
        self.organization_id = organization_id
        self.db = get_db()
        self._slack_client: Optional[SlackAPIClient] = None
        self._conversations: Dict[str, ConversationState] = {}
    
    @property
    def slack_client(self) -> SlackAPIClient:
        """Lazy-load Slack client."""
        if self._slack_client is None:
            self._slack_client = get_slack_client()
        return self._slack_client
    
    def should_ask_question(
        self,
        confidence: float,
        risks: List[str],
        missing_fields: List[str],
    ) -> bool:
        """Determine if the agent should ask a clarifying question."""
        # Ask if confidence is in the "uncertain" range
        if 0.5 <= confidence < 0.75:
            return True
        
        # Ask if there are high-risk issues
        high_risk_keywords = ["duplicate", "Could not", "significantly differs"]
        if any(any(kw in risk for kw in high_risk_keywords) for risk in risks):
            return True
        
        # Ask if critical fields are missing
        critical_fields = ["vendor", "amount"]
        if any(f in missing_fields for f in critical_fields):
            return True
        
        return False
    
    def generate_questions(
        self,
        invoice_id: str,
        extraction: Dict[str, Any],
        reasoning_factors: List[Dict[str, Any]],
        risks: List[str],
    ) -> List[ClarifyingQuestion]:
        """Generate relevant clarifying questions based on analysis."""
        questions = []
        
        vendor = extraction.get("vendor", "Unknown")
        amount = extraction.get("total_amount")
        
        # Check for vendor uncertainty
        vendor_factor = next(
            (f for f in reasoning_factors if f.get("factor") == "vendor_familiarity"),
            None
        )
        if vendor_factor and vendor_factor.get("score", 1) < 0.5:
            questions.append(self._create_vendor_question(invoice_id, vendor, extraction))
        
        # Check for potential duplicate
        if any("duplicate" in risk.lower() for risk in risks):
            questions.append(self._create_duplicate_question(invoice_id, vendor, amount, risks))
        
        # Check for amount uncertainty
        amount_factor = next(
            (f for f in reasoning_factors if f.get("factor") == "amount_reasonableness"),
            None
        )
        if amount_factor and amount_factor.get("score", 1) < 0.5:
            questions.append(self._create_amount_question(invoice_id, vendor, amount))
        
        # Check for missing vendor in extraction
        if vendor == "Unknown" or not vendor:
            questions.append(self._create_missing_vendor_question(invoice_id, extraction))
        
        return questions
    
    def _create_vendor_question(
        self,
        invoice_id: str,
        vendor: str,
        extraction: Dict[str, Any],
    ) -> ClarifyingQuestion:
        """Create a question to confirm vendor identity."""
        return ClarifyingQuestion(
            question_id=f"vendor_{uuid.uuid4().hex[:8]}",
            question_type=QuestionType.CONFIRM_VENDOR,
            question_text=f"Is this invoice from *{vendor}*?",
            context=f"This is a new vendor we haven't processed before. Please confirm.",
            options=[
                {"label": f"✓ Yes, it's {vendor}", "value": "confirm", "recommended": True},
                {"label": "✗ No, wrong vendor", "value": "reject"},
                {"label": "Let me check", "value": "defer"},
            ],
            invoice_id=invoice_id,
            urgency="normal",
            default_action="proceed with detected vendor",
            timeout_hours=24,
        )
    
    def _create_duplicate_question(
        self,
        invoice_id: str,
        vendor: str,
        amount: float,
        risks: List[str],
    ) -> ClarifyingQuestion:
        """Create a question about potential duplicate."""
        # Find the duplicate risk message
        dup_msg = next((r for r in risks if "duplicate" in r.lower()), "Potential duplicate detected")
        
        return ClarifyingQuestion(
            question_id=f"dup_{uuid.uuid4().hex[:8]}",
            question_type=QuestionType.CONFIRM_DUPLICATE,
            question_text=f"Is this a duplicate invoice?",
            context=f"{dup_msg}. {vendor} - ${amount:,.2f}" if amount else dup_msg,
            options=[
                {"label": "✓ Not a duplicate, process it", "value": "not_duplicate", "recommended": True},
                {"label": "✗ Yes, it's a duplicate", "value": "is_duplicate"},
                {"label": "Let me check", "value": "defer"},
            ],
            invoice_id=invoice_id,
            urgency="urgent",
            default_action="hold for review",
            timeout_hours=12,
        )
    
    def _create_amount_question(
        self,
        invoice_id: str,
        vendor: str,
        amount: float,
    ) -> ClarifyingQuestion:
        """Create a question about unusual amount."""
        return ClarifyingQuestion(
            question_id=f"amt_{uuid.uuid4().hex[:8]}",
            question_type=QuestionType.CONFIRM_AMOUNT,
            question_text=f"Is ${amount:,.2f} the correct amount for {vendor}?",
            context="This amount is different from what we typically see from this vendor.",
            options=[
                {"label": "✓ Yes, amount is correct", "value": "confirm", "recommended": True},
                {"label": "✗ No, amount is wrong", "value": "reject"},
                {"label": "Let me verify", "value": "defer"},
            ],
            invoice_id=invoice_id,
            urgency="normal",
            default_action="hold for review",
            timeout_hours=24,
        )
    
    def _create_missing_vendor_question(
        self,
        invoice_id: str,
        extraction: Dict[str, Any],
    ) -> ClarifyingQuestion:
        """Create a question when vendor couldn't be identified."""
        sender = extraction.get("sender", "unknown sender")
        
        return ClarifyingQuestion(
            question_id=f"vendor_{uuid.uuid4().hex[:8]}",
            question_type=QuestionType.MISSING_INFO,
            question_text="Who is this invoice from?",
            context=f"Could not identify vendor. Email from: {sender}",
            options=[
                {"label": "I'll provide the vendor name", "value": "provide"},
                {"label": "Skip this invoice", "value": "skip"},
                {"label": "View email and decide", "value": "defer"},
            ],
            invoice_id=invoice_id,
            urgency="normal",
            default_action="skip invoice",
            timeout_hours=48,
        )
    
    def create_gl_question(
        self,
        invoice_id: str,
        vendor: str,
        suggested_gl: Optional[str],
        gl_options: List[Dict[str, str]],
    ) -> ClarifyingQuestion:
        """Create a question to confirm GL code assignment."""
        options = []
        
        if suggested_gl:
            options.append({
                "label": f"✓ {suggested_gl}",
                "value": suggested_gl,
                "recommended": True
            })
        
        for gl in gl_options[:3]:  # Max 3 alternatives
            if gl.get("code") != suggested_gl:
                options.append({
                    "label": f"{gl.get('code')} - {gl.get('description', '')}",
                    "value": gl.get("code"),
                })
        
        options.append({"label": "Other...", "value": "other"})
        
        return ClarifyingQuestion(
            question_id=f"gl_{uuid.uuid4().hex[:8]}",
            question_type=QuestionType.CONFIRM_GL_CODE,
            question_text=f"Which GL code for {vendor}?",
            context="Please confirm the expense category for this invoice.",
            options=options,
            invoice_id=invoice_id,
            urgency="low",
            default_action=f"use {suggested_gl}" if suggested_gl else "hold for review",
            timeout_hours=48,
        )
    
    async def ask_question(
        self,
        question: ClarifyingQuestion,
        channel: str = "#finance-approvals",
    ) -> Dict[str, Any]:
        """Send a clarifying question to Slack."""
        blocks = question.to_slack_blocks()
        
        try:
            message = await self.slack_client.send_message(
                channel=channel,
                text=f"Clearledgr needs input: {question.question_text}",
                blocks=blocks,
            )
            
            # Store question state
            self._store_question(question, message.ts, channel)
            
            logger.info(f"Asked clarifying question: {question.question_id} - {question.question_type.value}")
            
            return {
                "status": "asked",
                "question_id": question.question_id,
                "channel": channel,
                "ts": message.ts,
            }
            
        except Exception as e:
            logger.error(f"Failed to ask question: {e}")
            return {"status": "error", "error": str(e)}
    
    def _store_question(
        self,
        question: ClarifyingQuestion,
        slack_ts: str,
        channel: str,
    ) -> None:
        """Store question for later response handling."""
        # Store in memory (production would use database)
        self._conversations[question.question_id] = ConversationState(
            conversation_id=question.question_id,
            invoice_id=question.invoice_id,
            organization_id=self.organization_id,
            questions_asked=[question],
            status="active",
        )
        
        # Also store in database for persistence
        try:
            if hasattr(self.db, 'store_clarifying_question'):
                self.db.store_clarifying_question(
                    question_id=question.question_id,
                    invoice_id=question.invoice_id,
                    question_type=question.question_type.value,
                    question_text=question.question_text,
                    options=[o.get("value") for o in question.options],
                    slack_ts=slack_ts,
                    slack_channel=channel,
                    organization_id=self.organization_id,
                )
        except Exception as e:
            logger.warning(f"Failed to persist question to database: {e}")
    
    def handle_response(
        self,
        question_id: str,
        response_value: str,
        responder: str,
    ) -> Dict[str, Any]:
        """
        Handle a response to a clarifying question.
        
        Returns action to take based on response.
        """
        # Get question state
        conversation = self._conversations.get(question_id)
        if not conversation:
            # Try to load from database
            conversation = self._load_conversation(question_id)
        
        if not conversation:
            logger.warning(f"Unknown question: {question_id}")
            return {"status": "error", "reason": "Question not found"}
        
        question = conversation.questions_asked[0] if conversation.questions_asked else None
        if not question:
            return {"status": "error", "reason": "No question found"}
        
        # Record response
        conversation.responses_received[question_id] = {
            "value": response_value,
            "responder": responder,
        }
        conversation.status = "resolved"
        
        # Determine action based on question type and response
        action = self._determine_action(question, response_value)
        
        logger.info(
            f"Received response for {question_id}: {response_value} -> action: {action.get('action')}"
        )
        
        return {
            "status": "resolved",
            "question_id": question_id,
            "invoice_id": question.invoice_id,
            "response": response_value,
            "responder": responder,
            **action,
        }
    
    def _determine_action(
        self,
        question: ClarifyingQuestion,
        response_value: str,
    ) -> Dict[str, Any]:
        """Determine what action to take based on response."""
        
        if question.question_type == QuestionType.CONFIRM_VENDOR:
            if response_value == "confirm":
                return {"action": "proceed", "update": None}
            elif response_value == "reject":
                return {"action": "flag_for_review", "reason": "Vendor incorrect"}
            else:
                return {"action": "hold", "reason": "User deferred"}
        
        elif question.question_type == QuestionType.CONFIRM_DUPLICATE:
            if response_value == "not_duplicate":
                return {"action": "proceed", "update": {"duplicate_cleared": True}}
            elif response_value == "is_duplicate":
                return {"action": "reject", "reason": "Confirmed duplicate"}
            else:
                return {"action": "hold", "reason": "User deferred"}
        
        elif question.question_type == QuestionType.CONFIRM_AMOUNT:
            if response_value == "confirm":
                return {"action": "proceed", "update": {"amount_confirmed": True}}
            elif response_value == "reject":
                return {"action": "flag_for_review", "reason": "Amount incorrect"}
            else:
                return {"action": "hold", "reason": "User deferred"}
        
        elif question.question_type == QuestionType.CONFIRM_GL_CODE:
            if response_value == "other":
                return {"action": "request_gl", "reason": "User wants different GL"}
            else:
                return {"action": "proceed", "update": {"gl_code": response_value}}
        
        elif question.question_type == QuestionType.MISSING_INFO:
            if response_value == "provide":
                return {"action": "request_info", "info_needed": "vendor_name"}
            elif response_value == "skip":
                return {"action": "skip", "reason": "User skipped"}
            else:
                return {"action": "hold", "reason": "User deferred"}
        
        return {"action": "unknown", "reason": "Unhandled response"}
    
    def _load_conversation(self, question_id: str) -> Optional[ConversationState]:
        """Load conversation from database."""
        try:
            if hasattr(self.db, 'get_clarifying_question'):
                data = self.db.get_clarifying_question(question_id)
                if data:
                    return ConversationState(
                        conversation_id=question_id,
                        invoice_id=data.get("invoice_id", ""),
                        organization_id=data.get("organization_id", ""),
                        status=data.get("status", "active"),
                    )
        except Exception as e:
            logger.warning(f"Failed to load conversation: {e}")
        return None


# Convenience function
def get_conversational_agent(organization_id: str = "default") -> ConversationalAgent:
    """Get a conversational agent instance."""
    return ConversationalAgent(organization_id=organization_id)
