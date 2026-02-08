"""Temporal worker entrypoint for Clearledgr workflows."""
from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from clearledgr.workflows.temporal_workflows import (
    ReconciliationWorkflowTemporal,
    InvoiceWorkflowTemporal,
    ScheduledReconciliationWorkflowTemporal,
    DailySlackSummaryWorkflowTemporal,
)
from clearledgr.workflows.temporal_activities import (
    audit_event_activity,
    fetch_reconciliation_inputs_activity,
    invoice_categorization_activity,
    invoice_extraction_activity,
    daily_slack_summary_activity,
    reconciliation_match_activity,
    route_exception_activity,
)

# Gmail extension workflows
from clearledgr.workflows.gmail_workflows import (
    EmailTriageWorkflow,
    EmailProcessingWorkflow,
    BulkEmailScanWorkflow,
    ApproveAndPostWorkflow,
)
from clearledgr.workflows.gmail_activities import (
    classify_email_activity,
    extract_email_data_activity,
    match_bank_feed_activity,
    match_erp_activity,
    verify_match_confidence_activity,
    apply_gmail_label_activity,
    post_to_erp_activity,
    update_slack_thread_activity,
    send_slack_notification_activity,
    create_mismatch_review_task_activity,
)

# SAP sync workflow
from clearledgr.workflows.sap_sync import (
    DailySAPSyncWorkflow,
    pull_sap_gl_activity,
    update_sheets_activity,
    notify_sync_complete_activity,
)


async def main() -> None:
    address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "clearledgr-v1")

    client = await Client.connect(address, namespace=namespace)

    # Main worker for core workflows
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[
            ReconciliationWorkflowTemporal,
            InvoiceWorkflowTemporal,
            ScheduledReconciliationWorkflowTemporal,
            DailySlackSummaryWorkflowTemporal,
            DailySAPSyncWorkflow,  # Added SAP sync workflow
        ],
        activities=[
            reconciliation_match_activity,
            fetch_reconciliation_inputs_activity,
            invoice_extraction_activity,
            invoice_categorization_activity,
            daily_slack_summary_activity,
            route_exception_activity,
            audit_event_activity,
            # SAP sync activities
            pull_sap_gl_activity,
            update_sheets_activity,
            notify_sync_complete_activity,
        ],
    )

    # Gmail extension worker (separate task queue for isolation)
    gmail_worker = Worker(
        client,
        task_queue="clearledgr-gmail",
        workflows=[
            EmailTriageWorkflow,
            EmailProcessingWorkflow,
            BulkEmailScanWorkflow,
            ApproveAndPostWorkflow,
        ],
        activities=[
            # Classification & Extraction
            classify_email_activity,
            extract_email_data_activity,
            # Matching
            match_bank_feed_activity,
            match_erp_activity,
            # HITL - Confidence verification
            verify_match_confidence_activity,
            # Actions
            apply_gmail_label_activity,
            post_to_erp_activity,
            # Multi-System Routing
            update_slack_thread_activity,
            send_slack_notification_activity,
            create_mismatch_review_task_activity,
            # Shared activities
            audit_event_activity,
            route_exception_activity,
        ],
    )

    # Run both workers concurrently
    await asyncio.gather(
        worker.run(),
        gmail_worker.run(),
    )


if __name__ == "__main__":
    asyncio.run(main())
