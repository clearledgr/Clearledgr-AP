"""Temporal workflow definitions for Clearledgr."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict

from temporalio import workflow
from temporalio.common import RetryPolicy

from clearledgr.workflows.temporal_activities import (
    audit_event_activity,
    fetch_reconciliation_inputs_activity,
    invoice_categorization_activity,
    invoice_extraction_activity,
    daily_slack_summary_activity,
    reconciliation_match_activity,
    route_exception_activity,
)


DEFAULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)


@workflow.defn
class ReconciliationWorkflowTemporal:
    @workflow.run
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await workflow.execute_activity(
            reconciliation_match_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=DEFAULT_RETRY,
        )

        if result.get("exceptions"):
            await workflow.execute_activity(
                route_exception_activity,
                {
                    "title": "Reconciliation exception review",
                    "description": "; ".join(result.get("exceptions", [])),
                    "organization_id": payload.get("organization_id"),
                    "requester": payload.get("requester"),
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )

        await workflow.execute_activity(
            audit_event_activity,
            {
                "user_email": payload.get("requester") or "system",
                "action": "workflow_completed",
                "entity_type": "reconciliation",
                "organization_id": payload.get("organization_id"),
                "metadata": {"match_rate": result.get("match_rate")},
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=DEFAULT_RETRY,
        )
        return result


@workflow.defn
class InvoiceWorkflowTemporal:
    @workflow.run
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        extraction = await workflow.execute_activity(
            invoice_extraction_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=DEFAULT_RETRY,
        )

        categorization = await workflow.execute_activity(
            invoice_categorization_activity,
            extraction,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )

        if not payload.get("match_found"):
            await workflow.execute_activity(
                route_exception_activity,
                {
                    "title": f"Invoice exception: {extraction.get('vendor') or 'Unknown vendor'}",
                    "description": "Invoice requires review or matching.",
                    "organization_id": payload.get("organization_id"),
                    "requester": payload.get("requester"),
                    "metadata": {
                        "vendor": extraction.get("vendor"),
                        "amount": (extraction.get("total") or {}).get("amount"),
                        "email_subject": payload.get("email_subject"),
                        "email_sender": payload.get("email_sender"),
                    },
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )

        return {
            "invoice_id": payload.get("invoice_id") or "invoice_unknown",
            "extraction": extraction,
            "categorization": categorization,
            "status": "categorized",
        }


@workflow.defn
class ScheduledReconciliationWorkflowTemporal:
    @workflow.run
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload_state = dict(payload)
        if not payload_state.get("bank_transactions") or not payload_state.get("gl_transactions"):
            fetched = await workflow.execute_activity(
                fetch_reconciliation_inputs_activity,
                payload_state,
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=DEFAULT_RETRY,
            )
            payload_state.update(fetched)

        if not payload_state.get("bank_transactions") or not payload_state.get("gl_transactions"):
            await workflow.execute_activity(
                audit_event_activity,
                {
                    "user_email": payload_state.get("requester") or "system",
                    "action": "reconciliation_skipped",
                    "entity_type": "reconciliation",
                    "organization_id": payload_state.get("organization_id"),
                    "metadata": {
                        "reason": "missing transactions",
                        "tool_type": payload_state.get("tool_type"),
                        "tool_id": payload_state.get("tool_id"),
                    },
                },
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=DEFAULT_RETRY,
            )
            return {"status": "skipped", "reason": "missing transactions"}

        return await ReconciliationWorkflowTemporal().run(payload_state)


@workflow.defn
class DailySlackSummaryWorkflowTemporal:
    @workflow.run
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        await workflow.execute_activity(
            daily_slack_summary_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        return {"status": "sent"}
