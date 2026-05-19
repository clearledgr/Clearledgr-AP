"""Timezone-aware datetime helpers.

Project rule: every datetime we persist, log, compare, or pass across
a process boundary is tz-aware UTC. No naive datetimes past this
module's boundary.

Why a helper module at all (vs. just using stdlib):

- ``datetime.utcnow()`` is deprecated in Python 3.12 (gives a naive
  datetime, which is a trap) and will be removed in 3.13+. Every new
  import of ``utcnow`` is a future regression.
- ``datetime.now()`` without a tz argument quietly gives whatever the
  server's local time happens to be. Railway gives us UTC today, but
  that's a deploy-environment coincidence, not a guarantee. Developers
  running the tests on macOS get local time instead — which has been
  the root cause of several intermittent test failures in the past.
- ``datetime.fromisoformat("2026-04-15")`` returns a naive datetime.
  Subsequent comparisons against ``now_utc()`` raise TypeError. Using
  ``parse_iso_utc`` below normalises to tz-aware UTC regardless of
  whether the input string carried a tz suffix.

If you find yourself writing ``datetime.now()`` in this repo, use
``now_utc()`` from this module instead.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union


def now_utc() -> datetime:
    """Return the current moment as a tz-aware UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """Return a tz-aware UTC copy of ``dt``.

    Assumes a naive datetime is ALREADY UTC and attaches the tz. This
    matches how we persist datetimes (``.isoformat()`` on a tz-aware
    UTC value) — if something bypassed the helpers and stored a naive
    value, the safest recovery is to read it back as UTC rather than
    re-interpret it as local time and shift by potentially 8 hours.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_iso_utc(value: Union[str, datetime, None]) -> Optional[datetime]:
    """Parse an ISO 8601 string (or pass through a datetime) to UTC.

    Returns None for None / empty strings so callers can use this in
    optional-field paths without a preceding null check. Trailing 'Z'
    is normalised to '+00:00' for stdlib compatibility.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def now_utc_iso() -> str:
    """Shortcut: current UTC moment as an ISO 8601 string with +00:00."""
    return now_utc().isoformat()
