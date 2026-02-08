"""Reconciliation workflow orchestration."""
import os
from typing import Dict

from clearledgr.agents import AgentContext, ExceptionRoutingAgent, ReconciliationMatchingAgent
from clearledgr.agents.intelligent_reconciliation import IntelligentReconciliationAgent
from clearledgr.services.journal_entries import JournalEntryService
from clearledgr.services.notifications import NotificationService
from clearledgr.models.reconciliation import ReconciliationResult
from clearledgr.services.audit import AuditTrailService
from clearledgr.services.exceptions import ExceptionStore


class ReconciliationWorkflow:
    def __init__(self, audit: AuditTrailService | None = None) -> None:
        self.audit = audit or AuditTrailService()
        self.matching_agent = IntelligentReconciliationAgent()
        self.exception_agent = ExceptionRoutingAgent()
        self.journal_entries = JournalEntryService()
        self.notifications = NotificationService()
        self.exception_store = ExceptionStore()

    def run(self, input_state: Dict) -> ReconciliationResult:
        ctx = AgentContext(
            organization_id=input_state.get("organization_id"),
            requester=input_state.get("requester"),
            state=input_state,
            audit=self.audit,
        )
        self.matching_agent.execute(ctx)
        result: ReconciliationResult = ctx.state.get("reconciliation_result")

        # Auto-generate draft journal entries for matched items
        if result and result.matches:
            drafts = []
            for match in result.matches:
                je = self.journal_entries.generate_draft(match)
                drafts.append(je)
            ctx.state["draft_journal_entries"] = drafts
            result.draft_journal_entries = drafts

        if result and result.exceptions:
            self.exception_agent.execute(ctx)
            # Persist exceptions for Slack/Sheets surfaces
            to_store = []
            for idx, exc in enumerate(result.exceptions):
                to_store.append(
                    {
                        "exception_id": getattr(exc, "exception_id", None) or f"exc_{idx}",
                        "status": "Pending",
                        "priority": "High",
                        "amount": getattr(exc, "amount", None) if hasattr(exc, "amount") else None,
                        "reason": str(exc),
                        "description": str(exc),
                        "source": "reconciliation",
                    }
                )
            self.exception_store.upsert_exceptions(to_store)

        # Notify summary/approvals (Slack/email wiring)
        if result:
            drafts_url = os.getenv("CLEARLEDGR_DRAFTS_URL", "#")
            exceptions_url = os.getenv("CLEARLEDGR_EXCEPTIONS_URL", "#")
            self.notifications.send_daily_summary(
                input_state.get("organization_id") or "org_default",
                {
                    "processed": len((input_state.get("bank_transactions") or []))
                    + len((input_state.get("gl_transactions") or [])),
                    "matched": len(result.matches),
                    "match_rate": result.match_rate,
                    "exceptions": result.exceptions,
                    "draft_journal_entries": result.draft_journal_entries,
                    "drafts_url": drafts_url,
                    "exceptions_url": exceptions_url,
                    "entity_id": input_state.get("organization_id") or "org_default",
                },
            )
            if result.exceptions:
                self.notifications.send_exception_alert(
                    input_state.get("organization_id") or "org_default",
                    [{"description": ex, "exceptions_url": exceptions_url} for ex in result.exceptions],
                )
            if result.draft_journal_entries:
                self.notifications.send_approval_request(
                    input_state.get("organization_id") or "org_default",
                    len(result.draft_journal_entries),
                )
                # Also push draft cards to Slack when configured
                try:
                    draft_dicts = [
                        je if isinstance(je, dict) else je.dict()  # type: ignore[attr-defined]
                        for je in result.draft_journal_entries
                    ]
                    self.notifications.send_drafts(draft_dicts)
                except Exception:
                    pass
            if result.exceptions:
                try:
                    self.notifications.send_exception_cards(
                        [
                            ex if isinstance(ex, dict) else {"description": str(ex)}
                            for ex in result.exceptions
                        ]
                    )
                except Exception:
                    pass
        return result
