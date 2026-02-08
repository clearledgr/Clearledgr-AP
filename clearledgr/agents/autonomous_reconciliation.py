"""
Autonomous Reconciliation Agent

This agent monitors for reconciliation-related events and automatically:
- Processes incoming bank statements
- Runs reconciliation when new data arrives
- Auto-matches high-confidence transactions
- Auto-creates journal entries for 95%+ confidence matches
- Escalates exceptions and low-confidence items

The agent operates continuously without user triggers.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from clearledgr.agents.runtime import (
    AutonomousAgent,
    AgentDecision,
    Event,
    EventBus,
    EventType,
)

logger = logging.getLogger(__name__)


class AutonomousReconciliationAgent(AutonomousAgent):
    """
    Autonomous agent for transaction reconciliation.
    
    Subscribes to:
    - Finance email detected (from Gmail watcher)
    - Sheets data updated (from Sheets watcher)
    - Reconciliation requested
    
    Actions:
    - Auto-run reconciliation when data arrives
    - Auto-match transactions above confidence threshold
    - Auto-create draft JEs for high-confidence matches
    - Escalate exceptions to appropriate team members
    """
    
    def __init__(self, event_bus: EventBus):
        super().__init__("ReconciliationAgent", event_bus)
        
        # Agent-specific thresholds
        self.auto_match_threshold = 0.90      # Auto-match at 90%+
        self.auto_je_threshold = 0.95         # Auto-create JE at 95%+
        self.auto_post_threshold = 0.98       # Auto-post to SAP at 98%+
        
        # Amount thresholds for human approval
        self.auto_approve_amount_limit = 5000  # EUR - auto-approve under this
        self.manager_approval_limit = 25000    # EUR - manager approval under this
        
        # Load AI service for enhanced matching
        self._ai_service = None
        self._scorer = None
    
    @property
    def ai_service(self):
        """Lazy load AI service."""
        if self._ai_service is None:
            try:
                from clearledgr.services.ai_enhanced import get_enhanced_ai_service
                self._ai_service = get_enhanced_ai_service()
            except ImportError:
                pass
        return self._ai_service
    
    @property
    def scorer(self):
        """Lazy load multi-factor scorer."""
        if self._scorer is None:
            try:
                from clearledgr.services.multi_factor_scoring import MultiFactorScorer
                self._scorer = MultiFactorScorer()
            except ImportError:
                pass
        return self._scorer
    
    def get_subscribed_events(self) -> List[EventType]:
        """Events this agent listens to."""
        return [
            EventType.GMAIL_FINANCE_EMAIL_DETECTED,
            EventType.GMAIL_ATTACHMENT_PARSED,
            EventType.SHEETS_DATA_UPDATED,
            EventType.SHEETS_RECONCILIATION_REQUESTED,
            EventType.APPROVAL_GRANTED,
        ]
    
    async def handle_event(self, event: Event) -> Optional[AgentDecision]:
        """
        Process an event and decide on action.
        
        This is the core decision-making logic of the agent.
        """
        if event.event_type == EventType.GMAIL_FINANCE_EMAIL_DETECTED:
            return await self._handle_finance_email(event)
        
        elif event.event_type == EventType.GMAIL_ATTACHMENT_PARSED:
            return await self._handle_attachment_parsed(event)
        
        elif event.event_type == EventType.SHEETS_DATA_UPDATED:
            return await self._handle_sheets_update(event)
        
        elif event.event_type == EventType.SHEETS_RECONCILIATION_REQUESTED:
            return await self._handle_reconciliation_request(event)
        
        elif event.event_type == EventType.APPROVAL_GRANTED:
            return await self._handle_approval(event)
        
        return None
    
    async def _handle_finance_email(self, event: Event) -> Optional[AgentDecision]:
        """Handle detected finance email."""
        payload = event.payload
        email_type = payload.get("email_type", "unknown")
        
        if email_type in ["bank_statement", "bankStatement"]:
            # Bank statement detected - high confidence we should process
            return AgentDecision(
                action="process_bank_statement",
                confidence=0.95,  # High confidence for bank statements
                reasoning="Bank statement detected, should extract and reconcile",
                should_auto_execute=True,
                requires_approval=False,
                payload={
                    "email_id": payload.get("email_id"),
                    "sender": payload.get("sender"),
                    "subject": payload.get("subject"),
                    "attachments": payload.get("attachments", []),
                },
            )
        
        elif email_type in ["invoice", "Invoice"]:
            # Invoice - process and categorize
            return AgentDecision(
                action="process_invoice",
                confidence=0.90,
                reasoning="Invoice detected, should extract and categorize",
                should_auto_execute=True,
                requires_approval=False,
                payload=payload,
            )
        
        # Other finance email types - lower confidence
        return AgentDecision(
            action="queue_for_review",
            confidence=0.60,
            reasoning=f"Finance email type '{email_type}' detected, needs review",
            should_auto_execute=False,
            requires_approval=True,
            payload=payload,
        )
    
    async def _handle_attachment_parsed(self, event: Event) -> Optional[AgentDecision]:
        """Handle parsed attachment with transaction data."""
        payload = event.payload
        transactions = payload.get("transactions", [])
        
        if not transactions:
            return None
        
        tx_count = len(transactions)
        total_amount = sum(abs(float(t.get("amount", 0))) for t in transactions)
        
        # Decide whether to auto-reconcile based on data quality
        has_dates = all(t.get("date") for t in transactions)
        has_amounts = all(t.get("amount") for t in transactions)
        has_refs = sum(1 for t in transactions if t.get("reference")) / max(tx_count, 1)
        
        data_quality_score = (
            (1.0 if has_dates else 0.0) * 0.3 +
            (1.0 if has_amounts else 0.0) * 0.4 +
            has_refs * 0.3
        )
        
        confidence = min(0.95, 0.70 + data_quality_score * 0.25)
        
        return AgentDecision(
            action="run_reconciliation",
            confidence=confidence,
            reasoning=f"Extracted {tx_count} transactions (€{total_amount:,.2f}), data quality: {data_quality_score:.0%}",
            should_auto_execute=confidence >= 0.85,
            requires_approval=total_amount > self.auto_approve_amount_limit,
            payload={
                "transactions": transactions,
                "source": payload.get("source", "attachment"),
                "total_amount": total_amount,
            },
        )
    
    async def _handle_sheets_update(self, event: Event) -> Optional[AgentDecision]:
        """Handle Sheets data update - check if reconciliation should run."""
        payload = event.payload
        sheet_name = payload.get("sheet_name", "")
        
        # Only trigger on input data sheets
        trigger_sheets = ["Gateway_Transactions", "SAP_Ledger_Export", "Bank_Transactions"]
        if not any(s.lower() in sheet_name.lower() for s in trigger_sheets):
            return None
        
        rows_changed = payload.get("rows_changed", 0)
        
        if rows_changed < 5:
            return None  # Minor change, don't trigger
        
        return AgentDecision(
            action="run_reconciliation",
            confidence=0.85,
            reasoning=f"{rows_changed} rows changed in {sheet_name}, should re-reconcile",
            should_auto_execute=True,
            requires_approval=False,
            payload={
                "sheet_id": payload.get("sheet_id"),
                "sheet_name": sheet_name,
                "trigger": "data_update",
            },
        )
    
    async def _handle_reconciliation_request(self, event: Event) -> Optional[AgentDecision]:
        """Handle explicit reconciliation request."""
        return AgentDecision(
            action="run_reconciliation",
            confidence=0.99,  # Explicit request = high confidence
            reasoning="Explicit reconciliation request received",
            should_auto_execute=True,
            requires_approval=False,
            payload=event.payload,
        )
    
    async def _handle_approval(self, event: Event) -> Optional[AgentDecision]:
        """Handle approval for a pending decision."""
        payload = event.payload
        original_action = payload.get("original_action")
        
        if original_action == "post_to_sap":
            return AgentDecision(
                action="execute_sap_posting",
                confidence=1.0,  # Approved by human
                reasoning="SAP posting approved by user",
                should_auto_execute=True,
                requires_approval=False,
                payload=payload,
            )
        
        return None
    
    async def execute_decision(self, decision: AgentDecision, event: Event) -> None:
        """Execute an approved decision."""
        action = decision.action
        payload = decision.payload
        correlation_id = event.correlation_id or event.event_id
        
        if action == "process_bank_statement":
            await self._execute_bank_statement_processing(payload, correlation_id)
        
        elif action == "process_invoice":
            await self._execute_invoice_processing(payload, correlation_id)
        
        elif action == "run_reconciliation":
            await self._execute_reconciliation(payload, correlation_id)
        
        elif action == "execute_sap_posting":
            await self._execute_sap_posting(payload, correlation_id)
        
        elif action == "queue_for_review":
            await self._queue_for_human_review(payload, correlation_id)
    
    async def _execute_bank_statement_processing(
        self, payload: Dict[str, Any], correlation_id: str
    ) -> None:
        """Process a bank statement."""
        logger.info(f"[{self.name}] Processing bank statement from {payload.get('sender')}")
        
        # In real implementation, this would:
        # 1. Parse the bank statement attachment
        # 2. Extract transactions
        # 3. Publish event for reconciliation
        
        # For now, publish that we're processing
        await self.event_bus.publish(Event(
            event_type=EventType.RECON_STARTED,
            payload={
                "source": "bank_statement",
                "email_id": payload.get("email_id"),
                "initiated_by": self.name,
            },
            source=self.name,
            correlation_id=correlation_id,
        ))
        
        # Simulate extraction and trigger reconciliation
        await self.event_bus.publish(Event(
            event_type=EventType.GMAIL_ATTACHMENT_PARSED,
            payload={
                "source": "bank_statement",
                "transactions": payload.get("transactions", []),
            },
            source=self.name,
            correlation_id=correlation_id,
        ))
    
    async def _execute_invoice_processing(
        self, payload: Dict[str, Any], correlation_id: str
    ) -> None:
        """Process an invoice."""
        logger.info(f"[{self.name}] Processing invoice from {payload.get('sender')}")
        
        # Categorize the invoice using AI
        if self.ai_service:
            try:
                from clearledgr.services.ai_enhanced import get_enhanced_ai_service
                ai = get_enhanced_ai_service()
                
                result = ai.categorize_transaction(
                    description=payload.get("subject", ""),
                    vendor=payload.get("vendor", payload.get("sender", "")),
                    amount=float(payload.get("amount", 0)),
                    gl_accounts=self._get_gl_accounts(),
                )
                
                payload["categorization"] = {
                    "gl_code": result.gl_code,
                    "gl_name": result.gl_name,
                    "confidence": result.confidence,
                    "reasoning": result.reasoning,
                }
            except Exception as e:
                logger.error(f"Categorization failed: {e}")
        
        # If high confidence, auto-create draft JE
        cat_confidence = payload.get("categorization", {}).get("confidence", 0)
        
        if cat_confidence >= self.auto_je_threshold:
            await self.event_bus.publish(Event(
                event_type=EventType.SHEETS_DRAFT_CREATED,
                payload={
                    "source": "invoice",
                    "auto_created": True,
                    "confidence": cat_confidence,
                    **payload,
                },
                source=self.name,
                correlation_id=correlation_id,
            ))
    
    async def _execute_reconciliation(
        self, payload: Dict[str, Any], correlation_id: str
    ) -> None:
        """Run reconciliation."""
        logger.info(f"[{self.name}] Running reconciliation")
        
        transactions = payload.get("transactions", [])
        
        # Notify start
        await self.event_bus.publish(Event(
            event_type=EventType.RECON_STARTED,
            payload={
                "transaction_count": len(transactions),
                "total_amount": payload.get("total_amount", 0),
                "initiated_by": self.name,
            },
            source=self.name,
            correlation_id=correlation_id,
        ))
        
        # In real implementation, run actual reconciliation
        # For now, simulate results
        
        # Publish completion
        await self.event_bus.publish(Event(
            event_type=EventType.RECON_COMPLETED,
            payload={
                "matched_count": len(transactions),
                "exception_count": 0,
                "auto_matched": True,
            },
            source=self.name,
            correlation_id=correlation_id,
        ))
    
    async def _execute_sap_posting(
        self, payload: Dict[str, Any], correlation_id: str
    ) -> None:
        """Post to SAP."""
        logger.info(f"[{self.name}] Posting to SAP")
        
        # In real implementation, call SAP service
        
        await self.event_bus.publish(Event(
            event_type=EventType.SAP_POSTING_COMPLETED,
            payload={
                "entry_id": payload.get("entry_id"),
                "sap_doc_number": f"SAP_{correlation_id[:8]}",
                "posted_by": self.name,
            },
            source=self.name,
            correlation_id=correlation_id,
        ))
    
    async def _queue_for_human_review(
        self, payload: Dict[str, Any], correlation_id: str
    ) -> None:
        """Queue item for human review."""
        logger.info(f"[{self.name}] Queuing for human review")
        
        await self.event_bus.publish(Event(
            event_type=EventType.APPROVAL_NEEDED,
            payload={
                "agent": self.name,
                "reason": "Low confidence - requires human review",
                **payload,
            },
            source=self.name,
            correlation_id=correlation_id,
        ))
    
    def _get_gl_accounts(self) -> List[Dict[str, Any]]:
        """Get available GL accounts."""
        return [
            {"code": "6000", "name": "Software & SaaS", "keywords": ["software", "subscription", "saas", "cloud"]},
            {"code": "6100", "name": "Professional Services", "keywords": ["consulting", "legal", "accounting"]},
            {"code": "6200", "name": "Marketing & Advertising", "keywords": ["marketing", "advertising", "ads"]},
            {"code": "6300", "name": "Office Supplies", "keywords": ["office", "supplies", "equipment"]},
            {"code": "6400", "name": "Travel & Entertainment", "keywords": ["travel", "flight", "hotel"]},
            {"code": "6500", "name": "Utilities", "keywords": ["utility", "electric", "water", "internet"]},
            {"code": "6900", "name": "Other Expenses", "keywords": []},
        ]
