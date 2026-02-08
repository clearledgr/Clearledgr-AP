"""Route exceptions to Slack/Teams via task notifications."""
from __future__ import annotations

from typing import Any, Dict, Optional

from clearledgr.services import email_tasks
from clearledgr.services.task_notifications import send_task_notification


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
        task = email_tasks.create_task_from_email(
            email_id=metadata.get("email_id") if metadata else "email_unknown",
            email_subject=metadata.get("email_subject") if metadata else "Invoice exception",
            email_sender=metadata.get("email_sender") if metadata else "unknown",
            thread_id=metadata.get("thread_id") if metadata else "thread_unknown",
            created_by=requester or "system",
            task_type="review_exception",
            title=title,
            description=description,
            priority="high",
            related_entity_type="invoice",
            related_entity_id=metadata.get("invoice_id") if metadata else None,
            related_amount=metadata.get("amount") if metadata else None,
            related_vendor=metadata.get("vendor") if metadata else None,
            tags=["exception", "invoice"],
            organization_id=organization_id,
        )
        send_task_notification("created", task, additional_context=metadata or {})
        return task
