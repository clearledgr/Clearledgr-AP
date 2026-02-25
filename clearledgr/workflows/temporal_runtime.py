"""Durable workflow runtime adapter (local DB-backed fallback).

Temporal is optional in this codebase. When Temporal is not configured, this
module provides a durable local runtime backed by ``workflow_runs`` in the main
database so callers can still start workflows and poll status without hitting a
stub/placeholder implementation.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from clearledgr.core.database import ClearledgrDB, get_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def temporal_enabled() -> bool:
    """Compatibility guard used by legacy callers.

    Keeps the historical `AP_TEMPORAL_ENABLED` contract (default: disabled)
    to avoid changing endpoint behavior unexpectedly. The runtime itself is
    always available as a local durable fallback through :class:`TemporalRuntime`.
    """
    return _env_flag("AP_TEMPORAL_ENABLED", False)


class TemporalRuntime:
    """Durable workflow runtime adapter.

    Attributes mirror the lightweight interface consumed by ops/extension code:
    - `enabled`: runtime is available (local DB-backed fallback)
    - `required`: whether Temporal is required by environment policy
    - `temporal_available`: whether actual Temporal integration is active
    """

    def __init__(self, db: Optional[ClearledgrDB] = None):
        self.db = db or get_db()
        self.enabled = True
        self.required = _env_flag("AP_TEMPORAL_REQUIRED", False)
        self.temporal_available = False
        self.runtime_backend = "local_db"

    def _create_run(
        self,
        *,
        workflow_name: str,
        workflow_type: str,
        organization_id: str,
        payload: Dict[str, Any],
        task_queue: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        status: str = "queued",
    ) -> Dict[str, Any]:
        return self.db.create_workflow_run(
            {
                "workflow_name": workflow_name,
                "workflow_type": workflow_type,
                "organization_id": organization_id,
                "status": status,
                "runtime_backend": self.runtime_backend,
                "task_queue": task_queue,
                "input_json": payload or {},
                "metadata_json": metadata or {},
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        )

    def _update_run(
        self,
        workflow_id: str,
        *,
        status: Optional[str] = None,
        ap_item_id: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        if status is not None:
            updates["status"] = status
        if ap_item_id is not None:
            updates["ap_item_id"] = ap_item_id
        if result is not None:
            updates["result_json"] = result
        if error is not None:
            updates["error_json"] = error
        if metadata is not None:
            updates["metadata_json"] = metadata
        if started_at is not None:
            updates["started_at"] = started_at
        if completed_at is not None:
            updates["completed_at"] = completed_at
        self.db.update_workflow_run(workflow_id, **updates)
        row = self.db.get_workflow_run(workflow_id)
        if not row:
            raise RuntimeError(f"Workflow run not found after update: {workflow_id}")
        return row

    async def start_workflow(
        self,
        workflow_name: str,
        payload: Dict[str, Any],
        *,
        task_queue: Optional[str] = None,
        wait: bool = True,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Start a workflow in the local durable runtime.

        Supports AP invoice entry workflows directly. Other workflow names are
        persisted and returned as `unsupported`/`queued` without raising.
        """
        normalized_name = str(workflow_name or "").strip() or "unknown"
        org_id = str((payload or {}).get("organization_id") or "default")

        if normalized_name in {"APInvoiceWorkflow", "APInvoiceEntryWorkflow"}:
            return await self.start_invoice(
                payload,
                organization_id=org_id,
                workflow_name=normalized_name,
                task_queue=task_queue,
                wait=wait,
                timeout_seconds=timeout_seconds,
            )

        run = self._create_run(
            workflow_name=normalized_name,
            workflow_type="generic",
            organization_id=org_id,
            payload=payload or {},
            task_queue=task_queue,
            metadata={"wait": bool(wait), "timeout_seconds": timeout_seconds, "supported": False},
            status="queued",
        )
        workflow_id = str(run["id"])
        if wait:
            run = self._update_run(
                workflow_id,
                status="unsupported",
                completed_at=_now_iso(),
                error={
                    "code": "unsupported_local_workflow",
                    "message": f"Local runtime does not implement {normalized_name}",
                },
            )
        return {
            "workflow_id": workflow_id,
            "status": run.get("status"),
            "workflow_name": normalized_name,
            "runtime_backend": self.runtime_backend,
            "supported": False,
        }

    async def start_invoice(
        self,
        payload: Dict[str, Any],
        *,
        organization_id: Optional[str] = None,
        workflow_name: str = "APInvoiceEntryWorkflow",
        task_queue: Optional[str] = "clearledgr-ap",
        wait: bool = True,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute an AP invoice entry workflow via the declarative AP workflow map."""
        invoice_payload = dict((payload or {}).get("invoice") or payload or {})
        org_id = str(organization_id or invoice_payload.get("organization_id") or "default")

        run = self._create_run(
            workflow_name=workflow_name,
            workflow_type="ap_invoice_entry",
            organization_id=org_id,
            payload=invoice_payload,
            task_queue=task_queue,
            metadata={"wait": bool(wait), "timeout_seconds": timeout_seconds},
            status="queued",
        )
        workflow_id = str(run["id"])

        started_at = _now_iso()
        self._update_run(workflow_id, status="running", started_at=started_at)

        try:
            from clearledgr.services.invoice_workflow import InvoiceData, InvoiceWorkflowService
            from clearledgr.workflows.ap_workflow import run_invoice_entry_workflow, validate_workflow_bindings

            bindings = validate_workflow_bindings()
            if not bindings.get("valid"):
                raise RuntimeError(f"ap_workflow_bindings_invalid:{bindings.get('missing')}")

            invoice = InvoiceData(
                gmail_id=str(invoice_payload.get("gmail_id") or invoice_payload.get("email_id") or ""),
                subject=str(invoice_payload.get("subject") or ""),
                sender=str(invoice_payload.get("sender") or ""),
                vendor_name=str(invoice_payload.get("vendor_name") or invoice_payload.get("vendor") or "Unknown"),
                amount=float(invoice_payload.get("amount") or 0.0),
                currency=str(invoice_payload.get("currency") or "USD"),
                invoice_number=invoice_payload.get("invoice_number"),
                due_date=invoice_payload.get("due_date"),
                po_number=invoice_payload.get("po_number"),
                confidence=float(invoice_payload.get("confidence") or 0.0),
                attachment_url=invoice_payload.get("attachment_url"),
                organization_id=org_id,
                user_id=invoice_payload.get("user_id"),
                invoice_text=invoice_payload.get("invoice_text"),
                reasoning_summary=invoice_payload.get("reasoning_summary"),
                reasoning_factors=invoice_payload.get("reasoning_factors"),
                reasoning_risks=invoice_payload.get("reasoning_risks"),
                vendor_intelligence=invoice_payload.get("vendor_intelligence"),
                policy_compliance=invoice_payload.get("policy_compliance"),
                priority=invoice_payload.get("priority"),
                budget_impact=invoice_payload.get("budget_impact"),
                po_match_result=invoice_payload.get("po_match_result"),
                budget_check_result=invoice_payload.get("budget_check_result"),
                potential_duplicates=int(invoice_payload.get("potential_duplicates") or 0),
                insights=invoice_payload.get("insights"),
                field_confidences=invoice_payload.get("field_confidences"),
            )
            if not invoice.gmail_id:
                raise ValueError("missing_gmail_id")

            service = InvoiceWorkflowService(organization_id=org_id)
            workflow_result = await run_invoice_entry_workflow(
                service,
                invoice,
                workflow_id=workflow_id,
                metadata={"runtime_backend": self.runtime_backend},
            )

            ap_item = None
            try:
                if hasattr(self.db, "get_ap_item_by_thread"):
                    ap_item = self.db.get_ap_item_by_thread(org_id, invoice.gmail_id)
            except Exception:
                ap_item = None
            ap_item_id = str(ap_item.get("id")) if isinstance(ap_item, dict) and ap_item.get("id") else None
            if ap_item_id:
                try:
                    self.db.update_ap_item(ap_item_id, workflow_id=workflow_id, run_id=workflow_id)
                except Exception:
                    pass

            completed_at = _now_iso()
            self._update_run(
                workflow_id,
                status="completed",
                ap_item_id=ap_item_id,
                result={
                    "workflow_id": workflow_id,
                    "status": "completed",
                    "workflow_name": workflow_name,
                    "organization_id": org_id,
                    "runtime_backend": self.runtime_backend,
                    "ap_item_id": ap_item_id,
                    "entry_result": workflow_result,
                },
                metadata={
                    "wait": bool(wait),
                    "timeout_seconds": timeout_seconds,
                    "bindings_valid": True,
                },
                completed_at=completed_at,
            )
            if wait:
                status = await self.get_status(workflow_id)
                return status
            return {
                "workflow_id": workflow_id,
                "status": "completed",
                "runtime_backend": self.runtime_backend,
                "ap_item_id": ap_item_id,
            }
        except Exception as exc:
            completed_at = _now_iso()
            self._update_run(
                workflow_id,
                status="failed",
                error={
                    "code": "workflow_execution_failed",
                    "message": str(exc),
                    "type": exc.__class__.__name__,
                },
                completed_at=completed_at,
            )
            if wait:
                return await self.get_status(workflow_id)
            return {
                "workflow_id": workflow_id,
                "status": "failed",
                "runtime_backend": self.runtime_backend,
                "error": {"message": str(exc), "type": exc.__class__.__name__},
            }

    async def start_reconciliation(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return await self.start_workflow("ReconciliationWorkflow", kwargs or {"args": list(args)}, wait=False)

    async def get_status(self, workflow_id: str) -> Dict[str, Any]:
        row = self.db.get_workflow_run(workflow_id)
        if not row:
            raise KeyError(workflow_id)
        return {
            "workflow_id": row.get("id"),
            "workflow_name": row.get("workflow_name"),
            "workflow_type": row.get("workflow_type"),
            "status": row.get("status"),
            "organization_id": row.get("organization_id"),
            "ap_item_id": row.get("ap_item_id"),
            "runtime_backend": row.get("runtime_backend") or self.runtime_backend,
            "task_queue": row.get("task_queue"),
            "input": row.get("input_json") or {},
            "result": row.get("result_json") or {},
            "error": row.get("error_json") or {},
            "metadata": row.get("metadata_json") or {},
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "updated_at": row.get("updated_at"),
        }

