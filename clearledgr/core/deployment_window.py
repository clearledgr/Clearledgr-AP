"""
Deployment Freeze Window — DESIGN_THESIS.md §7.7

"No model deployment happens during a period when the agent is actively
processing a high invoice volume. Deployment windows: Tuesday through
Thursday, between 10am and 2pm UK time."

This module enforces deployment timing constraints. Call
``is_deployment_allowed()`` before any model-update or configuration-change
endpoint that could affect agent behavior.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # Python <3.9

_UK_TZ = ZoneInfo("Europe/London")

# Deployment allowed: Tuesday (1) through Thursday (3), 10:00-14:00 UK time.
_ALLOWED_WEEKDAYS = {1, 2, 3}  # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4
_ALLOWED_HOUR_START = 10
_ALLOWED_HOUR_END = 14


def is_deployment_allowed(now: datetime | None = None) -> Dict[str, Any]:
    """Check whether the current time falls within the deployment window.

    Returns a dict with:
    - ``allowed``: bool
    - ``reason``: human-readable explanation
    - ``uk_time``: the UK-local time used for the check
    """
    if now is None:
        now = datetime.now(_UK_TZ)
    else:
        now = now.astimezone(_UK_TZ)

    weekday = now.weekday()
    hour = now.hour
    uk_time_str = now.strftime("%A %H:%M %Z")

    if weekday not in _ALLOWED_WEEKDAYS:
        return {
            "allowed": False,
            "reason": f"Deployments allowed Tuesday-Thursday only. Current: {uk_time_str}.",
            "uk_time": uk_time_str,
        }

    if hour < _ALLOWED_HOUR_START or hour >= _ALLOWED_HOUR_END:
        return {
            "allowed": False,
            "reason": f"Deployments allowed 10:00-14:00 UK only. Current: {uk_time_str}.",
            "uk_time": uk_time_str,
        }

    return {
        "allowed": True,
        "reason": f"Deployment window open. Current: {uk_time_str}.",
        "uk_time": uk_time_str,
    }
