"""Shared utilities used across the Clearledgr codebase."""
from __future__ import annotations

from typing import Any, Optional


def safe_int(value: Any, default: int = 0) -> int:
    """Convert value to int, returning default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """Convert value to float, returning default on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_float_or_none(value: Any) -> Optional[float]:
    """Convert value to float, returning None on failure or None input."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
