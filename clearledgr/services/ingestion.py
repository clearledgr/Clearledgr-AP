"""Ingestion service to dispatch normalized events into workflows."""
from __future__ import annotations

from typing import Any, Dict

from clearledgr.adapters.registry import registry
from clearledgr.models.ingestion import IngestionEvent, IngestionResult, NormalizedEvent
from clearledgr.models.requests import InvoiceExtractionRequest, ReconciliationRequest
from clearledgr.workflows.invoice import InvoiceWorkflow
from clearledgr.workflows.reconciliation import ReconciliationWorkflow
from clearledgr.workflows.temporal_runtime import TemporalRuntime, temporal_enabled
from clearledgr.services.audit import AuditTrailService
from clearledgr.services.llm_multimodal import MultiModalLLMService


class IngestionService:
    def __init__(
        self,
        audit: AuditTrailService | None = None,
        llm: MultiModalLLMService | None = None,
    ) -> None:
        self.audit = audit or AuditTrailService()
        self.llm = llm

    async def handle(self, event: IngestionEvent) -> IngestionResult:
        adapter = registry.get(event.source)
        normalized = adapter.normalize_event(event)
        self.audit.record_event(
            user_email=event.payload.get("requester") if isinstance(event.payload, dict) else "system",
            action="ingestion_event",
            entity_type="ingestion",
            entity_id=f"{event.source}:{event.event_type}",
            organization_id=event.organization_id,
            metadata={"normalized_type": normalized.event_type},
        )
        if normalized.event_type == "invoice_received":
            return await self._handle_invoice(normalized)
        if normalized.event_type == "reconciliation_requested":
            return await self._handle_reconciliation(normalized)
        if normalized.event_type == "exception_approval":
            return IngestionResult(status="accepted", details={"action": "approval_event"})
        return IngestionResult(status="ignored", details={"reason": "unsupported event"})

    async def _handle_invoice(self, event: NormalizedEvent) -> IngestionResult:
        payload = InvoiceExtractionRequest.model_validate(event.payload)
        if temporal_enabled():
            runtime = TemporalRuntime()
            response = await runtime.start_invoice(payload.model_dump(), wait=False)
            return IngestionResult(status="queued", workflow_id=response["workflow_id"])

        workflow = InvoiceWorkflow(audit=self.audit)
        state = payload.model_dump()
        if self.llm:
            state["llm_service"] = self.llm
        workflow.run(state)
        return IngestionResult(status="processed")

    async def _handle_reconciliation(self, event: NormalizedEvent) -> IngestionResult:
        payload = ReconciliationRequest.model_validate(event.payload)
        if temporal_enabled():
            runtime = TemporalRuntime()
            response = await runtime.start_reconciliation(payload.model_dump(), wait=False)
            return IngestionResult(status="queued", workflow_id=response["workflow_id"])

        workflow = ReconciliationWorkflow(audit=self.audit)
        workflow.run(payload.model_dump())
        return IngestionResult(status="processed")
