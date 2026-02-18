"""Temporal client facade for AP workflow orchestration."""
from __future__ import annotations

import os
import uuid
from dataclasses import asdict
from typing import Any, Dict, Optional

from clearledgr.core.database import get_db

from .types import APWorkflowCommand, TransitionEnvelope, build_workflow_id
from .workflow import APTemporalWorkflow

try:  # pragma: no cover - exercised in integration
    from temporalio.client import Client, WorkflowIDReusePolicy, WorkflowAlreadyStartedError
except Exception:  # pragma: no cover
    Client = None
    WorkflowIDReusePolicy = None
    WorkflowAlreadyStartedError = Exception


class APTemporalClient:
    def __init__(self) -> None:
        self.enabled = str(os.getenv("AP_TEMPORAL_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"}
        self.required = str(os.getenv("AP_TEMPORAL_REQUIRED", "true")).strip().lower() not in {"0", "false", "no", "off"}
        self.address = os.getenv("TEMPORAL_ADDRESS", "").strip()
        self.namespace = os.getenv("TEMPORAL_NAMESPACE", "default").strip() or "default"
        self.task_queue = os.getenv("TEMPORAL_AP_TASK_QUEUE", "clearledgr-ap")
        self._client: Optional[Client] = None

    @property
    def temporal_available(self) -> bool:
        return bool(self.enabled and self.address and Client is not None)

    async def _get_client(self) -> Optional[Client]:
        if not self.temporal_available:
            return None
        if self._client is None:
            self._client = await Client.connect(self.address, namespace=self.namespace)
        return self._client

    async def start_or_attach(
        self,
        organization_id: str,
        ap_item_id: str,
        command_name: str,
        payload: Optional[Dict[str, Any]] = None,
        actor_type: str = "system",
        actor_id: str = "workflow",
        run_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> TransitionEnvelope:
        if self.enabled and self.required and not self.temporal_available:
            raise RuntimeError(
                "Temporal is required but unavailable. Set TEMPORAL_ADDRESS and start the AP worker, "
                "or set AP_TEMPORAL_ENABLED=false for local fallback."
            )

        workflow_id = build_workflow_id(organization_id, ap_item_id)
        run_id = run_id or f"run_{uuid.uuid4().hex}"
        correlation_id = correlation_id or f"corr_{uuid.uuid4().hex}"
        command = APWorkflowCommand(
            command=command_name,
            organization_id=organization_id,
            ap_item_id=ap_item_id,
            run_id=run_id,
            correlation_id=correlation_id,
            actor_type=actor_type,
            actor_id=actor_id,
            payload=payload or {},
        )

        if self.temporal_available:
            client = await self._get_client()
            assert client is not None
            try:
                await client.start_workflow(
                    APTemporalWorkflow.run,
                    asdict(command),
                    id=workflow_id,
                    task_queue=self.task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
                )
            except WorkflowAlreadyStartedError:
                pass

        # Persist runtime IDs regardless of transport mode for deterministic APIs.
        db = get_db()
        ap_item = db.get_ap_item(ap_item_id)
        if ap_item:
            metadata = ap_item.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    import json
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            metadata = {**metadata, "run_id": run_id, "workflow_id": workflow_id, "correlation_id": correlation_id}
            db.update_ap_item(
                ap_item_id,
                workflow_id=workflow_id,
                run_id=run_id,
                metadata=metadata,
            )

        return TransitionEnvelope(
            ap_item_id=ap_item_id,
            workflow_id=workflow_id,
            run_id=run_id,
            state=(ap_item or {}).get("state", "received"),
            status="accepted",
            correlation_id=correlation_id,
            detail="temporal" if self.temporal_available else "local_fallback",
        )

    async def signal(self, workflow_id: str, signal_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if not self.temporal_available:
            return
        client = await self._get_client()
        assert client is not None
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(signal_name, payload or {})

    async def query_status(self, organization_id: str, ap_item_id: str) -> Dict[str, Any]:
        db = get_db()
        item = db.get_ap_item(ap_item_id)
        if not item:
            return {"status": "not_found"}

        workflow_id = item.get("workflow_id") or build_workflow_id(organization_id, ap_item_id)
        run_id = item.get("run_id")

        if not self.temporal_available:
            return {
                "status": "local",
                "workflow_id": workflow_id,
                "run_id": run_id,
                "state": item.get("state"),
            }

        client = await self._get_client()
        assert client is not None
        handle = client.get_workflow_handle(workflow_id)
        try:
            response = await handle.query(APTemporalWorkflow.get_status)
            if isinstance(response, dict):
                response.setdefault("status", "temporal")
                response.setdefault("workflow_id", workflow_id)
                response.setdefault("run_id", run_id)
                return response
        except Exception:
            pass

        return {
            "status": "temporal_unavailable",
            "workflow_id": workflow_id,
            "run_id": run_id,
            "state": item.get("state"),
        }


_CLIENT: Optional[APTemporalClient] = None


def get_ap_temporal_client() -> APTemporalClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = APTemporalClient()
    return _CLIENT
