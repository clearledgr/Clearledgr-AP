"""Temporal runtime helpers for API integration."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Type

from clearledgr.workflows.temporal_workflows import InvoiceWorkflowTemporal, ReconciliationWorkflowTemporal

try:
    from temporalio.client import Client, WorkflowHandle
    from temporalio.common import WorkflowIDReusePolicy

    TEMPORAL_AVAILABLE = True
except Exception:  # noqa: BLE001
    TEMPORAL_AVAILABLE = False
    Client = None
    WorkflowHandle = None
    WorkflowIDReusePolicy = None


def temporal_enabled() -> bool:
    return TEMPORAL_AVAILABLE and os.getenv("TEMPORAL_ENABLED", "false").lower() == "true"


# Workflow registry for dynamic lookup
WORKFLOW_REGISTRY: Dict[str, Type] = {}

def register_workflow(name: str, workflow_class: Type) -> None:
    """Register a workflow class by name."""
    WORKFLOW_REGISTRY[name] = workflow_class

# Register core workflows
register_workflow("ReconciliationWorkflow", ReconciliationWorkflowTemporal)
register_workflow("InvoiceWorkflow", InvoiceWorkflowTemporal)

# Import and register Gmail workflows
try:
    from clearledgr.workflows.gmail_workflows import (
        EmailTriageWorkflow,
        EmailProcessingWorkflow,
        BulkEmailScanWorkflow,
        ApproveAndPostWorkflow,
    )
    register_workflow("EmailTriageWorkflow", EmailTriageWorkflow)
    register_workflow("EmailProcessingWorkflow", EmailProcessingWorkflow)
    register_workflow("BulkEmailScanWorkflow", BulkEmailScanWorkflow)
    register_workflow("ApproveAndPostWorkflow", ApproveAndPostWorkflow)
except ImportError:
    pass

# Import and register SAP sync workflow
try:
    from clearledgr.workflows.sap_sync import DailySAPSyncWorkflow
    register_workflow("DailySAPSyncWorkflow", DailySAPSyncWorkflow)
except ImportError:
    pass


class TemporalRuntime:
    def __init__(self) -> None:
        self.address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
        self.namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
        self.task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "clearledgr-v1")
        self.gmail_task_queue = os.getenv("TEMPORAL_GMAIL_TASK_QUEUE", "clearledgr-gmail")

    async def _client(self) -> "Client":
        if not TEMPORAL_AVAILABLE:
            raise RuntimeError("Temporal SDK not available")
        return await Client.connect(self.address, namespace=self.namespace)

    async def start_workflow(
        self,
        workflow_name: str,
        payload: Dict[str, Any],
        workflow_id: Optional[str] = None,
        wait: bool = False,
        task_queue: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Start any registered workflow by name.
        
        This is the generic method that supports all workflow types including Gmail workflows.
        """
        workflow_class = WORKFLOW_REGISTRY.get(workflow_name)
        if not workflow_class:
            raise ValueError(f"Unknown workflow: {workflow_name}. Available: {list(WORKFLOW_REGISTRY.keys())}")
        
        client = await self._client()
        
        # Determine task queue - Gmail workflows use separate queue
        if task_queue is None:
            if workflow_name in ("EmailTriageWorkflow", "EmailProcessingWorkflow", "BulkEmailScanWorkflow", "ApproveAndPostWorkflow"):
                task_queue = self.gmail_task_queue
            else:
                task_queue = self.task_queue
        
        # Generate workflow ID if not provided
        if workflow_id is None:
            prefix = workflow_name.lower().replace("workflow", "")
            org_id = payload.get("organization_id", "default")
            workflow_id = f"{prefix}-{org_id}-{os.urandom(4).hex()}"
        
        handle = await client.start_workflow(
            workflow_class.run,
            payload,
            id=workflow_id,
            task_queue=task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
        
        if wait:
            result = await handle.result()
            return {"workflow_id": workflow_id, "result": result}
        return {"workflow_id": workflow_id, "status": "started"}

    async def start_reconciliation(
        self,
        payload: Dict[str, Any],
        workflow_id: Optional[str] = None,
        wait: bool = True,
    ) -> Dict[str, Any]:
        client = await self._client()
        workflow_id = workflow_id or f"recon-{payload.get('organization_id')}-{os.urandom(4).hex()}"
        handle = await client.start_workflow(
            ReconciliationWorkflowTemporal.run,
            payload,
            id=workflow_id,
            task_queue=self.task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
        if wait:
            result = await handle.result()
            return {"workflow_id": workflow_id, "result": result}
        return {"workflow_id": workflow_id}

    async def start_invoice(
        self,
        payload: Dict[str, Any],
        workflow_id: Optional[str] = None,
        wait: bool = True,
    ) -> Dict[str, Any]:
        client = await self._client()
        workflow_id = workflow_id or f"invoice-{payload.get('organization_id')}-{os.urandom(4).hex()}"
        handle = await client.start_workflow(
            InvoiceWorkflowTemporal.run,
            payload,
            id=workflow_id,
            task_queue=self.task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
        if wait:
            result = await handle.result()
            return {"workflow_id": workflow_id, "result": result}
        return {"workflow_id": workflow_id}

    async def get_status(self, workflow_id: str) -> Dict[str, Any]:
        client = await self._client()
        handle: WorkflowHandle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        return {
            "workflow_id": workflow_id,
            "status": str(desc.status),
            "start_time": desc.start_time,
            "close_time": desc.close_time,
        }
