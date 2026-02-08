"""Google Sheets adapter for reconciliation ingestion."""
from __future__ import annotations

from clearledgr.adapters.base import BaseAdapter
from clearledgr.models.ingestion import IngestionEvent, NormalizedEvent


class SheetsAdapter(BaseAdapter):
    source = "sheets"

    def normalize_event(self, event: IngestionEvent) -> NormalizedEvent:
        self.validate(event)
        payload = event.payload or {}

        event_type = event.event_type
        if event_type in {"reconciliation_requested", "reconciliation_run"}:
            normalized_type = "reconciliation_requested"
        else:
            normalized_type = event_type

        return NormalizedEvent(
            source=self.source,
            event_type=normalized_type,
            payload={
                "bank_transactions": payload.get("bank_transactions", []),
                "gl_transactions": payload.get("gl_transactions", []),
                "config": payload.get("config"),
                "organization_id": event.organization_id,
                "requester": payload.get("requester"),
                "sheet_id": payload.get("sheet_id"),
            },
            organization_id=event.organization_id,
        )
