"""Route exceptions to the right person via Slack/Teams.

Smart routing: exception type determines priority, handler, and channel.
Not all exceptions are equal — a fraud risk goes to the CFO, a PO mismatch
goes to the purchasing team, a GL code issue goes to the accountant.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from clearledgr.services import email_tasks
from clearledgr.services.task_notifications import send_task_notification

logger = logging.getLogger(__name__)

# Exception type → routing rules
# Each rule: priority, handler_role, suggested_channel, tags
EXCEPTION_ROUTING_RULES: Dict[str, Dict[str, Any]] = {
    "po_required_missing": {
        "priority": "medium",
        "handler_role": "procurement",
        "suggested_channel": "#procurement",
        "tags": ["exception", "po", "procurement"],
        "reason": "PO matching — purchasing team can locate or create the PO",
    },
    "amount_anomaly_high": {
        "priority": "high",
        "handler_role": "finance_manager",
        "suggested_channel": "#finance-exceptions",
        "tags": ["exception", "amount", "anomaly"],
        "reason": "Significant amount variance — needs manager review",
    },
    "amount_anomaly_moderate": {
        "priority": "medium",
        "handler_role": "ap_clerk",
        "suggested_channel": "#finance-approvals",
        "tags": ["exception", "amount"],
        "reason": "Moderate amount variance — AP clerk can verify",
    },
    "erp_vendor_not_found": {
        "priority": "medium",
        "handler_role": "ap_clerk",
        "suggested_channel": "#finance-approvals",
        "tags": ["exception", "vendor", "erp"],
        "reason": "Vendor not in ERP — AP clerk can create vendor record",
    },
    "duplicate_invoice": {
        "priority": "high",
        "handler_role": "ap_clerk",
        "suggested_channel": "#finance-exceptions",
        "tags": ["exception", "duplicate"],
        "reason": "Potential duplicate — verify before processing",
    },
    "bank_details_mismatch_from_invoice": {
        "priority": "critical",
        "handler_role": "cfo",
        "suggested_channel": "#finance-security",
        "tags": ["exception", "fraud", "bank_change", "security"],
        "reason": "⚠ FRAUD RISK: Bank details changed — CFO/security must verify",
    },
    "vendor_mismatch": {
        "priority": "medium",
        "handler_role": "ap_clerk",
        "suggested_channel": "#finance-approvals",
        "tags": ["exception", "vendor"],
        "reason": "Vendor name mismatch — verify correct entity",
    },
    "currency_mismatch": {
        "priority": "medium",
        "handler_role": "treasury",
        "suggested_channel": "#treasury",
        "tags": ["exception", "currency", "fx"],
        "reason": "Currency mismatch — treasury team handles FX",
    },
    "posting_exhausted": {
        "priority": "critical",
        "handler_role": "erp_admin",
        "suggested_channel": "#it-support",
        "tags": ["exception", "erp", "posting_failure"],
        "reason": "ERP posting failed after all retries — IT/ERP admin needed",
    },
    "erp_sync_mismatch": {
        "priority": "high",
        "handler_role": "erp_admin",
        "suggested_channel": "#it-support",
        "tags": ["exception", "erp", "sync"],
        "reason": "ERP sync discrepancy — IT/ERP admin should investigate",
    },
}

DEFAULT_ROUTING = {
    "priority": "high",
    "handler_role": "ap_clerk",
    "suggested_channel": "#finance-exceptions",
    "tags": ["exception", "invoice"],
    "reason": "Unclassified exception — AP team review",
}


class ExceptionRoutingService:
    def __init__(self) -> None:
        email_tasks.init_tasks_db()

    def route_invoice_exception(
        self,
        title: str,
        description: str,
        organization_id: Optional[str],
        requester: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        exception_code = (metadata or {}).get("exception_code", "")
        routing = EXCEPTION_ROUTING_RULES.get(exception_code, DEFAULT_ROUTING)

        # Enrich description with AI reasoning if available
        ai_suggestion = (metadata or {}).get("ai_suggestion", "")
        if ai_suggestion:
            description = f"{description}\n\nAI analysis: {ai_suggestion}"

        task = email_tasks.create_task_from_email(
            email_id=metadata.get("email_id") if metadata else "email_unknown",
            email_subject=metadata.get("email_subject") if metadata else "Invoice exception",
            email_sender=metadata.get("email_sender") if metadata else "unknown",
            thread_id=metadata.get("thread_id") if metadata else "thread_unknown",
            created_by=requester or "system",
            task_type=f"review_exception_{exception_code}" if exception_code else "review_exception",
            title=f"[{routing['priority'].upper()}] {title}",
            description=description,
            priority=routing["priority"],
            related_entity_type="invoice",
            related_entity_id=metadata.get("invoice_id") if metadata else None,
            related_amount=metadata.get("amount") if metadata else None,
            related_vendor=metadata.get("vendor") if metadata else None,
            tags=routing["tags"],
            organization_id=organization_id,
        )

        logger.info(
            "[ExceptionRouting] %s → %s (priority=%s, handler=%s)",
            exception_code or "unknown",
            routing.get("suggested_channel", "#finance"),
            routing["priority"],
            routing["handler_role"],
        )

        send_task_notification(
            "created", task,
            additional_context={
                **(metadata or {}),
                "routing_reason": routing["reason"],
                "handler_role": routing["handler_role"],
                "suggested_channel": routing.get("suggested_channel"),
            },
        )
        return task
