"""Clearledgr v1 core API routes."""
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Body

from clearledgr.api.deps import get_audit_service, get_ingestion_service, get_llm_service
from clearledgr.models.exceptions import ApprovalDecision
from clearledgr.models.ingestion import IngestionEvent, IngestionResult, EmailIngestRequest
from clearledgr.models.requests import InvoiceExtractionRequest, ReconciliationRequest
from clearledgr.services.ingestion import IngestionService
from clearledgr.models.invoices import Invoice
from clearledgr.models.reconciliation import ReconciliationResult
from clearledgr.models.journal_entries import DraftJournalEntry
from clearledgr.services.journal_entries import JournalEntryService
from clearledgr.services.audit import AuditTrailService
from clearledgr.services.llm_multimodal import MultiModalLLMService
from clearledgr.workflows.invoice import InvoiceWorkflow
from clearledgr.workflows.reconciliation import ReconciliationWorkflow
from clearledgr.workflows.temporal_runtime import TemporalRuntime, temporal_enabled
from clearledgr.workflows.temporal_schedules import TemporalScheduleManager
from clearledgr.services.exceptions import ExceptionStore
from clearledgr.services.sap import SAPService
from fastapi import Body

router = APIRouter(prefix="/v1", tags=["v1"])


@router.get("/health")
def health_check():
    return {"status": "ok", "service": "clearledgr-core"}


@router.post("/invoices/extract", response_model=Invoice)
async def extract_invoice(
    payload: InvoiceExtractionRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    llm: MultiModalLLMService = Depends(get_llm_service),
):
    state = payload.model_dump()
    if temporal_enabled():
        runtime = TemporalRuntime()
        result = await runtime.start_invoice(state, wait=True)
        return Invoice.model_validate(result["result"])

    state["llm_service"] = llm
    workflow = InvoiceWorkflow(audit=audit)
    return workflow.run(state)


@router.post("/reconciliation/run", response_model=ReconciliationResult)
async def run_reconciliation(
    payload: ReconciliationRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    if temporal_enabled():
        runtime = TemporalRuntime()
        result = await runtime.start_reconciliation(payload.model_dump(), wait=True)
        return ReconciliationResult.model_validate(result["result"])

    state = {
        "bank_transactions": payload.bank_transactions,
        "gl_transactions": payload.gl_transactions,
        "config": payload.config,
        "organization_id": payload.organization_id,
        "requester": payload.requester,
    }
    workflow = ReconciliationWorkflow(audit=audit)
    return workflow.run(state)


@router.post("/approvals", response_model=ApprovalDecision)
def record_approval(
    payload: ApprovalDecision,
    audit: AuditTrailService = Depends(get_audit_service),
):
    if not payload.exception_id:
        raise HTTPException(status_code=400, detail="exception_id required")
    audit.record_event(
        user_email=payload.approved_by or "system",
        action="approval_recorded" if payload.approved else "approval_rejected",
        entity_type="exception",
        entity_id=payload.exception_id,
        metadata={"notes": payload.notes, "decision_id": payload.decision_id},
    )
    return payload


@router.post("/ingest", response_model=IngestionResult)
async def ingest_event(
    event: IngestionEvent,
    ingestion: IngestionService = Depends(get_ingestion_service),
):
    return await ingestion.handle(event)


@router.post("/ingest/email", response_model=IngestionResult)
async def ingest_email(
    payload: EmailIngestRequest,
    ingestion: IngestionService = Depends(get_ingestion_service),
):
    """
    Direct ingestion for bank statements/finance emails.
    If transactions are present and trigger_reconciliation is true,
    kicks off reconciliation immediately (Temporal if enabled).
    """
    # If transactions are already parsed, start reconciliation directly
    if payload.transactions and payload.trigger_reconciliation:
        recon_request = ReconciliationRequest(
            bank_transactions=payload.transactions,
            gl_transactions=[],
            organization_id=payload.organization_id,
            requester=payload.email_sender,
        )
        if temporal_enabled():
            runtime = TemporalRuntime()
            response = await runtime.start_reconciliation(recon_request.model_dump(), wait=False)
            return IngestionResult(status="queued", workflow_id=response.get("workflow_id"))
        workflow = ReconciliationWorkflow()
        workflow.run(recon_request.model_dump())
        return IngestionResult(status="processed", details={"transactions": len(payload.transactions)})

    # Otherwise fall back to generic ingestion path
    event = IngestionEvent(
        source=payload.source,
        event_type="reconciliation_requested",
        organization_id=payload.organization_id,
        payload=payload.model_dump(),
    )
    result = await ingestion.handle(event)
    if not payload.transactions:
        result.details.setdefault("note", "No transactions provided")
    return result


@router.post("/workflows/reconciliation/start")
async def start_reconciliation_workflow(payload: ReconciliationRequest):
    if not temporal_enabled():
        raise HTTPException(status_code=400, detail="Temporal is not enabled")
    runtime = TemporalRuntime()
    state = payload.model_dump()
    return await runtime.start_reconciliation(state, wait=False)


@router.post("/workflows/invoice/start")
async def start_invoice_workflow(payload: InvoiceExtractionRequest):
    if not temporal_enabled():
        raise HTTPException(status_code=400, detail="Temporal is not enabled")
    runtime = TemporalRuntime()
    state = payload.model_dump()
    return await runtime.start_invoice(state, wait=False)


@router.post("/workflows/reconciliation/schedule")
async def schedule_reconciliation(payload: Dict):
    """
    Create or update a reconciliation schedule (daily/weekly/monthly).
    """
    # Persist schedule locally for visibility even if Temporal is off
    from clearledgr.state.agent_memory import save_agent_schedule

    schedule_type = payload.get("schedule_type") or payload.get("frequency") or payload.get("schedule") or "daily"
    tool_id = payload.get("sheet_id") or payload.get("entity_id") or "default"
    schedule_config = payload.get("schedule_config") or payload
    save_agent_schedule(
        schedule_id=payload.get("schedule_id") or f"reconciliation-{tool_id}-{schedule_type}",
        tool_type="sheets",
        tool_id=tool_id,
        schedule_type=schedule_type,
        schedule_config=schedule_config,
        is_active=True,
    )

    if not temporal_enabled():
        return {"status": "persisted_local", "reason": "Temporal is not enabled"}
    manager = TemporalScheduleManager()
    schedule = manager.ensure_schedule(payload)
    return schedule


@router.get("/drafts", response_model=list[DraftJournalEntry])
def list_drafts_api(status: str | None = None):
    svc = JournalEntryService()
    return svc.list_drafts(status=status)


@router.get("/drafts/{entry_id}", response_model=DraftJournalEntry)
def get_draft_api(entry_id: str):
    svc = JournalEntryService()
    draft = svc.get_draft(entry_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.post("/drafts/{entry_id}/approve", response_model=DraftJournalEntry)
def approve_draft_api(entry_id: str):
    svc = JournalEntryService()
    updated = svc.update_status(entry_id, "APPROVED")
    if not updated:
        raise HTTPException(status_code=404, detail="Draft not found")
    return updated


@router.post("/drafts/{entry_id}/post", response_model=DraftJournalEntry)
def post_draft_api(entry_id: str):
    svc = JournalEntryService()
    updated = svc.update_status(entry_id, "POSTED")
    if not updated:
        raise HTTPException(status_code=404, detail="Draft not found")
    return updated


@router.post("/drafts/{entry_id}/reject", response_model=DraftJournalEntry)
def reject_draft_api(entry_id: str):
    svc = JournalEntryService()
    updated = svc.update_status(entry_id, "REJECTED")
    if not updated:
        raise HTTPException(status_code=404, detail="Draft not found")
    return updated


@router.get("/exceptions", response_model=list[dict])
def list_exceptions(limit: int = 20):
    """
    List persisted reconciliation exceptions (for Slack/Sheets).
    """
    store = ExceptionStore()
    return store.list_exceptions(limit=limit)


@router.post("/exceptions/{exception_id}/resolve", response_model=dict)
def resolve_exception(exception_id: str, status: str = Body(default="Resolved")):
    store = ExceptionStore()
    store.resolve_exception(exception_id, status=status)
    return {"exception_id": exception_id, "status": status}


@router.get("/sap/gl", response_model=list[dict])
def fetch_sap_gl(company_code: str | None = None):
    """
    Pull GL transactions from SAP (if configured).
    """
    sap = SAPService()
    return sap.pull_gl_transactions(company_code)


@router.post("/sap/journal_entries", response_model=dict)
def post_sap_journal_entries(entries: list[dict]):
    """
    Post journal entries to SAP (approved drafts).
    """
    sap = SAPService()
    return sap.post_journal_entries(entries)


@router.get("/workflows/{workflow_id}")
async def get_workflow_status(workflow_id: str):
    if not temporal_enabled():
        raise HTTPException(status_code=400, detail="Temporal is not enabled")
    runtime = TemporalRuntime()
    return await runtime.get_status(workflow_id)
