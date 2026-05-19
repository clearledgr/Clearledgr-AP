"""Business-day arithmetic — DESIGN_THESIS.md §11.

The vendor-activation SLA is stated in *business days* ("a new vendor
went from invited to active in under five business days"). A calendar-
day count is wrong on both sides of the weekend: a vendor invited on
Friday and activated Wednesday is 5 calendar days but 3 business days
— reporting that as "over SLA" would train operators to distrust the
metric.

The helper is small, deterministic, and deliberately minimal:

- Mon–Fri are business days.
- Weekends are not.
- No public-holiday awareness. Holidays vary per jurisdiction and
  per customer; adding a holiday calendar would trade a small
  accuracy gain for a large coupling with per-customer config that
  this metric doesn't justify. An activation that spans a UK bank
  holiday but completes inside 5 business days will still read as
  within SLA on this counter, which is close enough for the success
  definition the thesis describes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def business_days_between(start: datetime, end: datetime) -> int:
    """Return the number of business days (Mon–Fri) between ``start``
    and ``end``, inclusive of ``start`` and exclusive of ``end``.

    Returns 0 when ``end <= start``. Timezone-naive inputs are treated
    as UTC so callers from both aware and naive code paths get the
    same answer — the metric is an elapsed count, not a wall-clock
    comparison, so timezone of origin doesn't affect the business-day
    tally.
    """
    if start is None or end is None:
        return 0
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end <= start:
        return 0

    # Operate on date boundaries, not datetimes — the count is
    # "how many weekdays are between these two dates", and hours /
    # minutes / timezones shouldn't shift the tally.
    start_date = start.date()
    end_date = end.date()

    if start_date == end_date:
        # Same calendar day — treat as 0 business days elapsed.
        return 0

    # Walk day-by-day. The total span is at most ~100 days in
    # practice (a vendor stuck for 3+ months has bigger problems
    # than SLA reporting), so the naive loop is cheap.
    count = 0
    cursor = start_date
    one_day = timedelta(days=1)
    while cursor < end_date:
        # weekday(): Mon=0 .. Sun=6. Weekdays are < 5.
        if cursor.weekday() < 5:
            count += 1
        cursor += one_day
    return count


def business_days_from_iso(
    start_iso: str, end_iso: str,
) -> int:
    """Convenience wrapper for ISO-8601 string inputs — the shape
    most DB rows already carry on the audit trail. Returns 0 on any
    parse failure so callers don't have to duplicate defensive code.
    """
    try:
        start_dt = datetime.fromisoformat(str(start_iso or "").replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(end_iso or "").replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0
    return business_days_between(start_dt, end_dt)
