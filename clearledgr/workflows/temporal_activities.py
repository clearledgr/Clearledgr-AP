"""Temporal activities for Clearledgr workflows."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from temporalio import activity

from clearledgr.agents.base import AgentContext
from clearledgr.agents.categorization import CategorizationAgent
from clearledgr.agents.invoice_extraction import InvoiceExtractionAgent
from clearledgr.models.invoices import InvoiceCategorization, InvoiceExtraction
from clearledgr.models.reconciliation import ReconciliationConfig
from clearledgr.models.requests import InvoiceExtractionRequest, ReconciliationRequest
from clearledgr.services.audit import AuditTrailService
from clearledgr.services.exception_routing import ExceptionRoutingService
from clearledgr.services.matching import match_bank_to_gl
from clearledgr.services.intelligent_matching import IntelligentMatchingService
from clearledgr.services.journal_entries import JournalEntryService
from clearledgr.services.llm_multimodal import MultiModalLLMService
from clearledgr.services.reconciliation_inputs import (
    DEFAULT_BANK_TAB,
    DEFAULT_GL_TAB,
    load_reconciliation_inputs_from_sheets,
)
from clearledgr.services.sap import SAPService
from ui.slack.app import send_daily_summary


@activity.defn
async def reconciliation_match_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    request = ReconciliationRequest.model_validate(payload)
    matcher = IntelligentMatchingService(config=request.config)
    result = matcher.match(request.bank_transactions, request.gl_transactions)

    # Auto-generate draft journal entries for matched items
    je_service = JournalEntryService()
    drafts = []
    for match in result.matches:
        je = je_service.generate_draft(match)
        drafts.append(je.model_dump())

    audit = AuditTrailService()
    audit.record_event(
        user_email=request.requester or "system",
        action="reconciliation_matched",
        entity_type="reconciliation",
        metadata={"match_rate": result.match_rate},
        organization_id=request.organization_id,
    )
    return {
        **result.model_dump(),
        "draft_journal_entries": drafts,
    }


@activity.defn
async def fetch_reconciliation_inputs_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    schedule_config = payload.get("schedule_config") or {}
    tool_type = payload.get("tool_type")
    tool_id = payload.get("tool_id")
    sheet_id = (
        schedule_config.get("sheet_id")
        or payload.get("sheet_id")
        or (tool_id if tool_type == "sheets" else None)
    )

    if not sheet_id:
        return {
            "bank_transactions": [],
            "gl_transactions": [],
            "config": ReconciliationConfig().model_dump(),
            "reason": "missing_sheet_id",
        }

    bank_tab = schedule_config.get("bank_tab") or os.getenv("DEFAULT_BANK_TAB") or DEFAULT_BANK_TAB
    gl_tab = (
        schedule_config.get("gl_tab")
        or schedule_config.get("internal_tab")
        or os.getenv("DEFAULT_INTERNAL_TAB")
        or DEFAULT_GL_TAB
    )

    sap_gl_rows = []
    try:
        sap = SAPService()
        sap_gl_rows = sap.pull_gl_transactions(schedule_config.get("company_code"))
    except Exception:
        sap_gl_rows = []

    bank_transactions, gl_transactions, config = load_reconciliation_inputs_from_sheets(
        sheet_id=sheet_id,
        bank_tab=bank_tab,
        gl_tab=gl_tab,
        schedule_config=schedule_config,
        sap_gl=sap_gl_rows,
    )

    return {
        "bank_transactions": [txn.model_dump() for txn in bank_transactions],
        "gl_transactions": [txn.model_dump() for txn in gl_transactions],
        "config": config.model_dump(),
        "sheet_id": sheet_id,
        "bank_tab": bank_tab,
        "gl_tab": gl_tab,
    }


@activity.defn
async def daily_slack_summary_activity(_: Dict[str, Any]) -> Dict[str, Any]:
    """Activity to push the daily summary into Slack (uses SLACK_DEFAULT_CHANNEL)."""
    try:
        await send_daily_summary()
        return {"status": "sent"}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


@activity.defn
async def invoice_extraction_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    request = InvoiceExtractionRequest.model_validate(payload)
    audit = AuditTrailService()
    llm = MultiModalLLMService()
    agent = InvoiceExtractionAgent()

    ctx = AgentContext(
        organization_id=request.organization_id,
        requester=request.requester,
        state={
            "email_subject": request.email_subject,
            "email_body": request.email_body,
            "email_sender": request.email_sender,
            "attachments": [att.model_dump() for att in request.attachments],
            "llm_service": llm,
        },
        audit=audit,
    )
    agent.execute(ctx)
    extraction: InvoiceExtraction = ctx.state["invoice_extraction"]
    return extraction.model_dump()


@activity.defn
async def invoice_categorization_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    extraction = InvoiceExtraction.model_validate(payload)
    audit = AuditTrailService()
    agent = CategorizationAgent()
    ctx = AgentContext(
        organization_id=None,
        requester=None,
        state={"invoice_extraction": extraction},
        audit=audit,
    )
    agent.execute(ctx)
    categorization: InvoiceCategorization = ctx.state["invoice_categorization"]
    return categorization.model_dump()


@activity.defn
async def route_exception_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    router = ExceptionRoutingService()
    task = router.route_invoice_exception(
        title=payload.get("title", "Exception review"),
        description=payload.get("description", "Exception requires review."),
        organization_id=payload.get("organization_id"),
        requester=payload.get("requester"),
        metadata=payload.get("metadata") or {},
    )
    return task


@activity.defn
async def audit_event_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    audit = AuditTrailService()
    audit.record_event(
        user_email=payload.get("user_email", "system"),
        action=payload.get("action", "event"),
        entity_type=payload.get("entity_type", "workflow"),
        entity_id=payload.get("entity_id"),
        organization_id=payload.get("organization_id"),
        metadata=payload.get("metadata"),
    )
    return {"status": "recorded"}
