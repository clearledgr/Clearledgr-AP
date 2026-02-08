"""Slack adapter for approvals and exceptions."""
from __future__ import annotations

from clearledgr.adapters.base import BaseAdapter
from clearledgr.models.ingestion import IngestionEvent, NormalizedEvent


class SlackAdapter(BaseAdapter):
    source = "slack"

    def normalize_event(self, event: IngestionEvent) -> NormalizedEvent:
        self.validate(event)
        payload = event.payload or {}

        event_type = event.event_type
        if event_type in {"approval_received", "exception_approved", "exception_rejected"}:
            normalized_type = "exception_approval"
        else:
            normalized_type = event_type

        return NormalizedEvent(
            source=self.source,
            event_type=normalized_type,
            payload={
                "exception_id": payload.get("exception_id"),
                "approved": payload.get("approved"),
                "approved_by": payload.get("approved_by"),
                "notes": payload.get("notes"),
                "organization_id": event.organization_id,
            },
            organization_id=event.organization_id,
        )
