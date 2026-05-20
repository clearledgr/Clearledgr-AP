"""Pluggable box-seed strategies.

A *seed strategy* turns inbound unstructured intake (e.g. an invoice email
payload) into a persisted Box of a given type. Today only ``ap_item`` is
seeded this way (invoice email -> ap_item); this registry lets a future
box type plug in its own seeding so the runtime dispatches by ``box_type``
instead of hardcoding the AP path.

The AP strategy deliberately delegates to the runtime's existing
``_seed_ap_item_for_invoice_processing`` body rather than relocating it —
that body is deeply coupled to runtime internals (db, parsers, org
resolution), so wrapping it is the clean seam; the body is the AP
strategy's implementation, reached either directly by AP callers or
generically via ``runtime.seed_box('ap_item', ...)``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class BoxSeedStrategy(Protocol):
    """Turn an intake payload into a persisted Box of ``box_type``."""

    box_type: str

    def seed(
        self,
        runtime: Any,
        payload: Dict[str, Any],
        *,
        correlation_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        ...


_SEED_STRATEGIES: Dict[str, BoxSeedStrategy] = {}


def register_seed_strategy(strategy: BoxSeedStrategy) -> None:
    """Register a seed strategy under its ``box_type``."""
    _SEED_STRATEGIES[strategy.box_type] = strategy


def get_seed_strategy(box_type: str) -> Optional[BoxSeedStrategy]:
    return _SEED_STRATEGIES.get(box_type)


class APSeedStrategy:
    """Seeds an ``ap_item`` Box from an invoice email payload."""

    box_type = "ap_item"

    def seed(
        self,
        runtime: Any,
        payload: Dict[str, Any],
        *,
        correlation_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return runtime._seed_ap_item_for_invoice_processing(
            payload, correlation_id=correlation_id
        )


register_seed_strategy(APSeedStrategy())
