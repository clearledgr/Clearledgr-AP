"""Adapter interfaces for embedded sources."""
from __future__ import annotations

from abc import ABC, abstractmethod
from clearledgr.models.ingestion import IngestionEvent, NormalizedEvent


class BaseAdapter(ABC):
    source: str = "unknown"

    @abstractmethod
    def normalize_event(self, event: IngestionEvent) -> NormalizedEvent:
        """Normalize raw ingestion event into a canonical format."""

    def validate(self, event: IngestionEvent) -> None:
        if event.source != self.source:
            raise ValueError(f"Adapter source mismatch: expected {self.source}")
