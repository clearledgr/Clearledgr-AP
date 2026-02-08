"""
Vita - Autonomous Finance Agent

Vita is NOT a chatbot. Vita is a finance expert that:
1. EXECUTES workflows autonomously
2. PROACTIVELY surfaces issues
3. REASONS about finance context (timing, materiality, period-end)
4. TAKES ACTION with confidence

The difference:
- Chatbot: "Here are your 5 exceptions"
- Vita: "I found 5 exceptions. 2 are timing differences that will clear by Friday.
         1 is a bank fee I've already categorized. The remaining 2 need your attention:
         - Vendor ABC: €5,230 mismatch - likely a partial payment, want me to match it?
         - Unknown charge: €150 - flagged for review"
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VitaResponse:
    """Response from Vita agent."""
    text: str
    data: Optional[Dict[str, Any]] = None
    actions: List[Dict[str, Any]] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    executed: bool = False  # True if Vita took action


class VitaAgent:
    """
    Vita - Your autonomous finance agent.
    
    Vita operates at three levels:
    1. OBSERVE - Monitor data, detect patterns, surface issues
    2. DECIDE - Apply finance expertise to determine action
    3. ACT - Execute or escalate based on confidence
    """
    
    def __init__(self):
        self._engine = None
    
    @property
    def engine(self):
        """Lazy load the core engine."""
        if self._engine is None:
            from clearledgr.core.engine import get_engine
            self._engine = get_engine()
        return self._engine
    
    async def process(
        self,
        text: str,
        user_id: str,
        channel: str = "api",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> VitaResponse:
        """
        Process user input and TAKE ACTION.
        
        Vita doesn't just answer - Vita does.
        """
        metadata = metadata or {}
        org_id = metadata.get("organization_id", "default")
        
        # Normalize input
        text_lower = text.lower().strip()
        
        # Route to appropriate handler
        # Note: Use substrings for stems (reconcil matches reconcile, reconciliation)
        if any(w in text_lower for w in ["reconcil", "match transaction", "process transaction", "run recon"]):
            return await self._handle_reconciliation(text_lower, org_id, user_id)
        
        elif any(w in text_lower for w in ["status", "dashboard", "overview", "how are", "what needs", "attention"]):
            return await self._handle_status(org_id)
        
        elif any(w in text_lower for w in ["exception", "issue", "problem", "error", "unmatched"]):
            return await self._handle_exceptions(org_id, user_id)
        
        elif any(w in text_lower for w in ["approve", "accept", "confirm"]):
            return await self._handle_approval(text_lower, org_id, user_id)
        
        elif any(w in text_lower for w in ["reject", "decline", "deny"]):
            return await self._handle_rejection(text_lower, org_id, user_id)
        
        elif any(w in text_lower for w in ["email", "inbox", "finance email"]):
            return await self._handle_emails(org_id)
        
        elif any(w in text_lower for w in ["draft", "journal", "entry", "entries", "post to sap"]):
            return await self._handle_drafts(org_id, user_id)
        
        elif any(w in text_lower for w in ["help", "what can you"]):
            return self._handle_help()
        
        elif any(w in text_lower for w in ["hi", "hello", "hey", "good morning", "good afternoon", "good evening"]):
            return await self._handle_greeting(org_id)
        
        else:
            return await self._handle_default(text, org_id)
    
    async def _handle_status(self, org_id: str) -> VitaResponse:
        """Provide proactive status with actionable insights."""
        dashboard = self.engine.get_dashboard_data(org_id)
        
        emails = dashboard.get("email_count", 0)
        exceptions = dashboard.get("open_exceptions", 0)
        pending = dashboard.get("pending_drafts", 0)
        match_rate = dashboard.get("match_rate", 0)
        
        # Build proactive response
        insights = []
        actions = []
        urgent = False
        
        if exceptions > 0:
            urgent = True
            insights.append(f"**{exceptions} exceptions** need attention")
            actions.append({
                "label": "Review Exceptions",
                "action_id": "view_exceptions",
                "style": "primary"
            })
        
        if pending > 0:
            insights.append(f"**{pending} draft entries** awaiting approval")
            actions.append({
                "label": "Approve Drafts",
                "action_id": "approve_drafts",
            })
        
        if emails > 0:
            insights.append(f"**{emails} finance emails** detected")
        
        if match_rate >= 95:
            insights.append(f"Match rate is excellent at **{match_rate}%**")
        elif match_rate >= 80:
            insights.append(f"Match rate is good at **{match_rate}%**")
        elif match_rate > 0:
            insights.append(f"Match rate needs attention: **{match_rate}%**")
            actions.append({
                "label": "Run Reconciliation",
                "action_id": "run_reconciliation",
            })
        
        if not insights:
            text = "All clear. No pending items or exceptions.\n\nI'm monitoring for new finance emails and will alert you when action is needed."
        else:
            priority = "URGENT: " if urgent else ""
            text = f"{priority}Here's what needs your attention:\n\n" + "\n".join(f"- {i}" for i in insights)
        
        return VitaResponse(
            text=text,
            data=dashboard,
            actions=actions,
            suggestions=["Run reconciliation", "Show exceptions", "Approve all drafts"],
        )
    
    async def _handle_reconciliation(self, text: str, org_id: str, user_id: str) -> VitaResponse:
        """Actually run reconciliation - don't just describe it."""
        
        # Check if we have data to reconcile
        transactions = self.engine.get_transactions(org_id, status="pending", limit=100)
        
        if not transactions:
            return VitaResponse(
                text="No pending transactions to reconcile.\n\nI'm waiting for new data from:\n- Gmail (bank statements, invoices)\n- Connected payment gateways\n- SAP GL exports",
                suggestions=["Check finance emails", "What's my status?"],
            )
        
        # We have data - run reconciliation
        # In production, this would fetch actual gateway/bank data
        # For now, use what's in the database
        
        gateway_txs = [t for t in transactions if t.get("source") == "gateway"]
        bank_txs = [t for t in transactions if t.get("source") == "bank"]
        
        if not gateway_txs and not bank_txs:
            return VitaResponse(
                text=f"Found {len(transactions)} pending transactions but they're not classified by source yet.\n\nWould you like me to:\n1. Process them as bank transactions\n2. Wait for more data",
                actions=[
                    {"label": "Process as Bank", "action_id": "process_as_bank", "style": "primary"},
                    {"label": "Wait", "action_id": "cancel"},
                ],
            )
        
        # Actually run reconciliation - data already exists in DB
        try:
            result = self.engine.run_reconciliation(
                organization_id=org_id,
                gateway_transactions=[],  # Already in DB
                bank_transactions=[],     # Already in DB
            )
            
            matches = result.get("matches", 0)
            exceptions = result.get("exceptions", 0)
            rate = result.get("match_rate", 0)
            
            text = f"**Reconciliation Complete**\n\n"
            text += f"- {matches} transactions matched automatically\n"
            text += f"- {exceptions} exceptions flagged for review\n"
            text += f"- Match rate: {rate:.1f}%\n\n"
            
            if exceptions > 0:
                text += f"I've prioritized the {exceptions} exceptions by amount and urgency. Want me to walk through them?"
            else:
                text += "No exceptions! Ready to generate journal entries for posting."
            
            return VitaResponse(
                text=text,
                data=result,
                actions=[
                    {"label": "Review Exceptions", "action_id": "view_exceptions"} if exceptions > 0 else {},
                    {"label": "Generate Journal Entries", "action_id": "generate_entries", "style": "primary"},
                ],
                suggestions=["Show exceptions", "Generate journal entries", "What's my status?"],
                executed=True,
            )
        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")
            return VitaResponse(
                text=f"Reconciliation encountered an error: {str(e)}\n\nThis usually means:\n- Missing required data (gateway or bank transactions)\n- Data format issues\n\nWould you like me to diagnose the problem?",
                actions=[{"label": "Diagnose", "action_id": "diagnose_recon"}],
            )
    
    async def _handle_exceptions(self, org_id: str, user_id: str) -> VitaResponse:
        """Handle exceptions with finance expertise - not just list them."""
        exceptions = self.engine.get_exceptions(org_id, status="open", limit=20)
        
        if not exceptions:
            return VitaResponse(
                text="No open exceptions. Your reconciliation is clean.",
                suggestions=["What's my status?", "Show draft entries"],
            )
        
        # Analyze exceptions like a finance expert
        critical = [e for e in exceptions if e.get("priority") == "critical"]
        high = [e for e in exceptions if e.get("priority") == "high"]
        medium = [e for e in exceptions if e.get("priority") == "medium"]
        low = [e for e in exceptions if e.get("priority") == "low"]
        
        total_amount = sum(e.get("amount", 0) for e in exceptions)
        
        text = f"**{len(exceptions)} Exceptions** (Total: EUR {total_amount:,.2f})\n\n"
        
        # Provide expert analysis
        if critical:
            text += f"**CRITICAL ({len(critical)})** - Requires immediate attention:\n"
            for e in critical[:3]:
                text += f"  - {e.get('vendor', 'Unknown')}: EUR {e.get('amount', 0):,.2f}\n"
            text += "\n"
        
        if high:
            text += f"**HIGH ({len(high)})** - Review before close:\n"
            for e in high[:3]:
                text += f"  - {e.get('vendor', 'Unknown')}: EUR {e.get('amount', 0):,.2f}\n"
            text += "\n"
        
        if medium or low:
            text += f"**ROUTINE ({len(medium) + len(low)})** - Can be batched\n\n"
        
        # Proactive recommendation
        if len(exceptions) <= 5:
            text += "I can walk you through each one. Say 'next' to review them."
        else:
            text += f"Would you like me to:\n1. Auto-resolve routine items (bank fees, timing differences)\n2. Walk through critical items one by one"
        
        return VitaResponse(
            text=text,
            data={"exceptions": exceptions, "summary": {"critical": len(critical), "high": len(high), "medium": len(medium), "low": len(low)}},
            actions=[
                {"label": "Auto-resolve Routine", "action_id": "auto_resolve_routine", "style": "primary"},
                {"label": "Review One by One", "action_id": "review_exceptions"},
            ],
            suggestions=["Resolve all low priority", "Show critical only", "Export exceptions"],
        )
    
    async def _handle_approval(self, text: str, org_id: str, user_id: str) -> VitaResponse:
        """Approve drafts - actually do it."""
        drafts = self.engine.get_draft_entries(org_id, status="pending", limit=50)
        
        if not drafts:
            return VitaResponse(
                text="No pending drafts to approve.",
                suggestions=["What's my status?", "Run reconciliation"],
            )
        
        # Check if user wants to approve all or specific
        if "all" in text:
            # Approve all
            approved = 0
            for draft in drafts:
                try:
                    self.engine.approve_draft(
                        draft_id=draft["id"],
                        organization_id=org_id,
                        user_id=user_id,
                    )
                    approved += 1
                except Exception as e:
                    logger.error(f"Failed to approve draft {draft['id']}: {e}")
            
            total_amount = sum(d.get("amount", 0) for d in drafts)
            
            return VitaResponse(
                text=f"**Approved {approved} journal entries**\n\nTotal: EUR {total_amount:,.2f}\n\nReady to post to SAP?",
                data={"approved": approved, "total_amount": total_amount},
                actions=[
                    {"label": "Post to SAP", "action_id": "post_to_sap", "style": "primary"},
                    {"label": "Review First", "action_id": "review_approved"},
                ],
                executed=True,
            )
        else:
            # Show drafts for approval
            total = len(drafts)
            total_amount = sum(d.get("amount", 0) for d in drafts)
            high_confidence = [d for d in drafts if d.get("confidence", 0) >= 0.9]
            
            text = f"**{total} Draft Entries** awaiting approval\n\n"
            text += f"Total: EUR {total_amount:,.2f}\n"
            text += f"High confidence (>90%): {len(high_confidence)}\n\n"
            
            if len(high_confidence) == total:
                text += "All entries are high confidence. Safe to approve all."
            else:
                text += f"Recommend reviewing {total - len(high_confidence)} lower confidence entries."
            
            return VitaResponse(
                text=text,
                data={"drafts": drafts[:10]},
                actions=[
                    {"label": "Approve All", "action_id": "approve_all", "style": "primary"},
                    {"label": "Approve High Confidence Only", "action_id": "approve_high_confidence"},
                    {"label": "Review Each", "action_id": "review_drafts"},
                ],
                suggestions=["Approve all", "Show details", "Export drafts"],
            )
    
    async def _handle_rejection(self, text: str, org_id: str, user_id: str) -> VitaResponse:
        """Handle rejection requests."""
        return VitaResponse(
            text="Which item would you like to reject? Please specify by ID or description.",
            suggestions=["Show pending drafts", "Show exceptions"],
        )
    
    async def _handle_emails(self, org_id: str) -> VitaResponse:
        """Show finance emails with context."""
        emails = self.engine.get_finance_emails(org_id, limit=20)
        
        if not emails:
            return VitaResponse(
                text="No finance emails detected yet.\n\nI'm monitoring your inbox for:\n- Bank statements\n- Invoices\n- Payment confirmations\n- Settlement reports",
                suggestions=["What's my status?"],
            )
        
        unprocessed = [e for e in emails if e.get("status") == "detected"]
        processed = [e for e in emails if e.get("status") == "processed"]
        
        text = f"**{len(emails)} Finance Emails**\n\n"
        
        if unprocessed:
            text += f"**{len(unprocessed)} Ready to Process:**\n"
            for e in unprocessed[:5]:
                text += f"- {e.get('email_type', 'finance')}: {e.get('subject', '')[:40]}...\n"
            text += "\n"
        
        if processed:
            text += f"**{len(processed)} Already Processed**\n"
        
        return VitaResponse(
            text=text,
            data={"emails": emails},
            actions=[
                {"label": "Process All", "action_id": "process_all_emails", "style": "primary"} if unprocessed else {},
            ],
            suggestions=["Process emails", "Run reconciliation"],
        )
    
    async def _handle_drafts(self, org_id: str, user_id: str) -> VitaResponse:
        """Show draft journal entries."""
        return await self._handle_approval("show", org_id, user_id)
    
    async def _handle_greeting(self, org_id: str) -> VitaResponse:
        """Greet with proactive status."""
        # Get status and include it in greeting
        status = await self._handle_status(org_id)
        
        greeting = "Hi! I'm Vita, your finance agent.\n\n"
        return VitaResponse(
            text=greeting + status.text,
            data=status.data,
            actions=status.actions,
            suggestions=status.suggestions,
        )
    
    def _handle_help(self) -> VitaResponse:
        """Show what Vita can do."""
        text = """**I'm Vita, your autonomous finance agent.**

I don't just answer questions - I take action.

**What I do:**
- **Reconcile** - Match transactions across sources automatically
- **Categorize** - Assign GL accounts with AI
- **Detect** - Find exceptions, anomalies, and issues
- **Generate** - Create journal entries from matches
- **Approve** - Process approvals with audit trail
- **Post** - Send entries to SAP

**Try saying:**
- "Reconcile my transactions"
- "Approve all high confidence drafts"
- "What needs my attention?"
- "Resolve the bank fee exceptions"
- "Post approved entries to SAP"

I monitor your Gmail, Sheets, and Slack continuously. When I detect finance emails or anomalies, I'll alert you."""

        return VitaResponse(
            text=text,
            suggestions=["What's my status?", "Run reconciliation", "Show exceptions"],
        )
    
    async def _handle_default(self, text: str, org_id: str) -> VitaResponse:
        """Handle unrecognized input with helpful response."""
        # Try to be helpful even with unrecognized input
        dashboard = self.engine.get_dashboard_data(org_id)
        
        suggestions = []
        if dashboard.get("open_exceptions", 0) > 0:
            suggestions.append("Show exceptions")
        if dashboard.get("pending_drafts", 0) > 0:
            suggestions.append("Approve drafts")
        suggestions.append("What's my status?")
        
        return VitaResponse(
            text=f"I'm not sure how to help with that. I'm a finance agent - I can reconcile transactions, approve entries, and resolve exceptions.\n\nTry: \"{suggestions[0]}\"",
            suggestions=suggestions,
        )


# Global instance
_vita_agent: Optional[VitaAgent] = None


def get_vita_agent() -> VitaAgent:
    """Get the global Vita agent instance."""
    global _vita_agent
    if _vita_agent is None:
        _vita_agent = VitaAgent()
    return _vita_agent
