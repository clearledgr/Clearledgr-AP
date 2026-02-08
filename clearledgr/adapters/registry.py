"""Adapter registry for ingestion."""
from __future__ import annotations

from typing import Dict

from clearledgr.adapters.base import BaseAdapter
from clearledgr.adapters.gmail import GmailAdapter
from clearledgr.adapters.sheets import SheetsAdapter
from clearledgr.adapters.slack import SlackAdapter


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: Dict[str, BaseAdapter] = {}
        self.register(GmailAdapter())
        self.register(SheetsAdapter())
        self.register(SlackAdapter())

    def register(self, adapter: BaseAdapter) -> None:
        self._adapters[adapter.source] = adapter

    def get(self, source: str) -> BaseAdapter:
        if source not in self._adapters:
            raise ValueError(f"Unknown adapter source: {source}")
        return self._adapters[source]

    def list_sources(self) -> list[str]:
        return list(self._adapters.keys())


registry = AdapterRegistry()
