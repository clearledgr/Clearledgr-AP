"""Gmail adapter for invoice ingestion."""
from __future__ import annotations

from clearledgr.adapters.base import BaseAdapter
from clearledgr.models.ingestion import IngestionEvent, NormalizedEvent


class GmailAdapter(BaseAdapter):
    source = "gmail"

    def normalize_event(self, event: IngestionEvent) -> NormalizedEvent:
        self.validate(event)
        payload = event.payload or {}
        attachments = []
        for attachment in payload.get("attachments", []) or []:
            if not isinstance(attachment, dict):
                continue
            attachments.append(
                {
                    "filename": attachment.get("filename") or attachment.get("name") or "attachment",
                    "content_type": attachment.get("content_type"),
                    "content_base64": attachment.get("content_base64"),
                    "content_text": attachment.get("content_text"),
                }
            )

        event_type = event.event_type
        if event_type in {"email_received", "invoice_received", "invoice_detected"}:
            normalized_type = "invoice_received"
        elif event_type in {"bank_statement", "bank_statement_received"}:
            normalized_type = "reconciliation_requested"
        else:
            normalized_type = event_type

        return NormalizedEvent(
            source=self.source,
            event_type=normalized_type,
            payload={
                "email_subject": payload.get("email_subject"),
                "email_body": payload.get("email_body"),
                "email_sender": payload.get("email_sender"),
                "attachments": attachments,
                "organization_id": event.organization_id,
                "requester": payload.get("requester"),
                "email_id": payload.get("email_id"),
                "thread_id": payload.get("thread_id"),
            },
            organization_id=event.organization_id,
        )
