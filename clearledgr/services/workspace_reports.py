"""Workspace reports service — Module 8 (Reports).

The five fixed reports the GA scope mandates. Each function returns a
structured payload the frontend renders without further composition:
summary headline + time series + breakdown + metadata. CSV export
keys off the same data.

  1. volume              — invoices processed over time, by entity, by vendor
  2. agent_performance   — agent confidence trend, auto-resolution rate, exception rate
  3. cycle_time          — avg days from receipt to ERP post, by entity
  4. exception_breakdown — exception types ranked + trending
  5. vendor_quality      — vendors ranked by exception rate

All functions are organization-scoped and never raise; they return an
empty-but-valid payload on database failure so the frontend renders
"no data" calmly rather than blowing up.

Period bucketing uses Postgres ``date_trunc`` (daily/weekly/monthly).
The Postgres pool is the only engine path post-C.2/C.3, so the SQL
uses native PG features (date_trunc, FILTER, NULLIF, CASE).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Period + window helpers
# ---------------------------------------------------------------------------

VALID_PERIODS = frozenset({"daily", "weekly", "monthly"})

_PERIOD_TO_PG_TRUNC = {
    "daily": "day",
    "weekly": "week",
    "monthly": "month",
}

# How far back to scan when no explicit ``from``/``to`` are supplied.
# 90 days is the operator's default scan: long enough to spot a trend
# without dragging the query against a year of data on every page load.
_DEFAULT_LOOKBACK_DAYS = 90

# Hard cap on user-supplied lookback. Acceptance criterion is <5s for
# 1 year (365 days) of data; 400 days gives a small buffer without
# allowing operators to ad-hoc multi-year scans on the live DB.
_MAX_LOOKBACK_DAYS = 400


@dataclass
class ReportParams:
    """Resolved report query parameters."""
    period: str = "weekly"
    from_ts: str = ""
    to_ts: str = ""
    entity_id: Optional[str] = None
    vendor_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "period": self.period,
            "from": self.from_ts,
            "to": self.to_ts,
            "entity_id": self.entity_id,
            "vendor_name": self.vendor_name,
        }


def _resolve_window(
    *, period: Optional[str], from_ts: Optional[str], to_ts: Optional[str],
    entity_id: Optional[str] = None, vendor_name: Optional[str] = None,
) -> ReportParams:
    """Normalise user-supplied filters and clamp the window."""
    period_norm = (period or "weekly").strip().lower()
    if period_norm not in VALID_PERIODS:
        period_norm = "weekly"

    now = datetime.now(timezone.utc)
    parsed_to = _parse_iso_or_none(to_ts) or now
    parsed_from = _parse_iso_or_none(from_ts)
    if parsed_from is None:
        parsed_from = parsed_to - timedelta(days=_DEFAULT_LOOKBACK_DAYS)

    span_days = (parsed_to - parsed_from).days
    if span_days > _MAX_LOOKBACK_DAYS:
        parsed_from = parsed_to - timedelta(days=_MAX_LOOKBACK_DAYS)
    if parsed_from > parsed_to:
        # Caller flipped the order; flip back rather than throw.
        parsed_from, parsed_to = parsed_to, parsed_from

    return ReportParams(
        period=period_norm,
        from_ts=parsed_from.isoformat(),
        to_ts=parsed_to.isoformat(),
        entity_id=(entity_id or None),
        vendor_name=(vendor_name or None),
    )


def _parse_iso_or_none(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        # Accept "Z" and naive forms; coerce to UTC.
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Empty-payload helpers
# ---------------------------------------------------------------------------

def _empty_response(report_type: str, params: ReportParams) -> Dict[str, Any]:
    """Stable shape returned on DB failure or empty result."""
    return {
        "report_type": report_type,
        "params": params.to_dict(),
        "summary": {},
        "series": [],
        "breakdown": [],
        "generated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Report 1: Volume
# ---------------------------------------------------------------------------

def generate_volume_report(
    organization_id: str,
    *,
    period: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    entity_id: Optional[str] = None,
    vendor_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Invoice volume over time, by entity, by vendor.

    Multi-currency aware (Module 9): the SQL groups by
    ``(bucket, currency)`` and ``(vendor_name, currency)`` so the
    Python layer can convert each row to the org's functional
    currency before aggregating. ``summary.total_amount`` is in the
    functional currency; ``summary.currencies_seen`` lists the
    invoice currencies that contributed; ``summary.unconverted`` is
    the count of rows where no FX rate was available.

    Returns:
      summary:    {total_invoices, total_amount, currency,
                   distinct_vendors, currencies_seen, unconverted}
      series:     [{bucket, invoice_count, total_amount,
                    by_currency: [{currency, amount}, ...]}, ...]
      breakdown:  [{vendor_name, invoice_count, total_amount}, ...]
    """
    params = _resolve_window(
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id, vendor_name=vendor_name,
    )
    db = get_db()
    trunc = _PERIOD_TO_PG_TRUNC[params.period]
    where_extra, where_args = _common_where(params)

    # Per-(bucket, currency) — Python converts each row to functional
    # before aggregating into the bucket total.
    series_sql = (
        f"SELECT date_trunc('{trunc}', created_at::timestamptz) AS bucket, "
        "       currency, "
        "       COUNT(*)::bigint AS invoice_count, "
        "       COALESCE(SUM(amount), 0)::numeric AS total_amount "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND created_at >= %s AND created_at < %s "
        f"  {where_extra} "
        "GROUP BY bucket, currency ORDER BY bucket ASC"
    )
    breakdown_sql = (
        "SELECT vendor_name, currency, "
        "       COUNT(*)::bigint AS invoice_count, "
        "       COALESCE(SUM(amount), 0)::numeric AS total_amount "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND created_at >= %s AND created_at < %s "
        "  AND vendor_name IS NOT NULL AND vendor_name <> '' "
        f"  {where_extra} "
        "GROUP BY vendor_name, currency"
    )
    summary_sql = (
        "SELECT COUNT(*)::bigint AS total_invoices, "
        "       COUNT(DISTINCT vendor_name)::bigint AS distinct_vendors "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND created_at >= %s AND created_at < %s "
        f"  {where_extra}"
    )

    base_args: Tuple[Any, ...] = (organization_id, params.from_ts, params.to_ts)

    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(series_sql, base_args + where_args)
            series_rows = cur.fetchall()
            cur.execute(breakdown_sql, base_args + where_args)
            breakdown_rows = cur.fetchall()
            cur.execute(summary_sql, base_args + where_args)
            summary_row = cur.fetchone()
    except Exception as exc:
        logger.warning("[reports.volume] failed for org=%s: %s", organization_id, exc)
        return _empty_response("volume", params)

    # Convert each row to the org's functional currency before
    # aggregating. As-of date is the bucket's right edge, so a rate
    # entered for a given week applies to all invoices in that week.
    from clearledgr.services import workspace_fx

    functional_ccy = workspace_fx.get_functional_currency(db, organization_id)
    unconverted_count = 0
    currencies_seen: set = set()

    # Series rollup: bucket → {invoice_count, total, by_currency: {ccy: amount}}
    series_buckets: Dict[Any, Dict[str, Any]] = {}
    for row in series_rows:
        bucket_value = row[0]
        currency = (row[1] or functional_ccy).upper()
        invoice_count = int(row[2] or 0)
        amount = float(row[3] or 0)
        currencies_seen.add(currency)

        as_of = _bucket_as_of_iso(bucket_value)
        result = workspace_fx.convert(
            db, organization_id=organization_id,
            amount=amount, from_currency=currency, to_currency=functional_ccy,
            as_of_date=as_of,
        )
        if result is None:
            unconverted_count += invoice_count
            converted_amount = 0.0
            path = "none"
        else:
            converted_amount = result.converted_amount
            path = result.path

        bucket_key = bucket_value
        if bucket_key not in series_buckets:
            series_buckets[bucket_key] = {
                "bucket": _bucket_label(bucket_value, params.period),
                "invoice_count": 0,
                "total_amount": 0.0,
                "by_currency": [],
            }
        slot = series_buckets[bucket_key]
        slot["invoice_count"] += invoice_count
        slot["total_amount"] += converted_amount
        slot["by_currency"].append({
            "currency": currency,
            "amount": amount,
            "converted_amount": converted_amount,
            "conversion_path": path,
        })

    series = [series_buckets[k] for k in sorted(series_buckets.keys(), key=lambda v: str(v))]

    # Breakdown rollup: vendor → {invoice_count, total} after FX conversion
    vendor_rollup: Dict[str, Dict[str, Any]] = {}
    for row in breakdown_rows:
        vendor = row[0]
        currency = (row[1] or functional_ccy).upper()
        invoice_count = int(row[2] or 0)
        amount = float(row[3] or 0)
        currencies_seen.add(currency)

        # Use the window's end as the conversion as-of for the
        # vendor breakdown. The vendor breakdown is a snapshot view,
        # not a time series, so a single as-of is the right choice.
        result = workspace_fx.convert(
            db, organization_id=organization_id,
            amount=amount, from_currency=currency, to_currency=functional_ccy,
            as_of_date=params.to_ts,
        )
        if result is None:
            converted_amount = 0.0
        else:
            converted_amount = result.converted_amount

        if vendor not in vendor_rollup:
            vendor_rollup[vendor] = {
                "vendor_name": vendor,
                "invoice_count": 0,
                "total_amount": 0.0,
            }
        vendor_rollup[vendor]["invoice_count"] += invoice_count
        vendor_rollup[vendor]["total_amount"] += converted_amount

    breakdown = sorted(
        vendor_rollup.values(),
        key=lambda v: v["total_amount"],
        reverse=True,
    )[:10]

    summary = {
        "total_invoices": int((summary_row[0] if summary_row else 0) or 0),
        "total_amount": sum(s["total_amount"] for s in series),
        "distinct_vendors": int((summary_row[1] if summary_row else 0) or 0),
        "currency": functional_ccy,
        "currencies_seen": sorted(currencies_seen),
        "unconverted": unconverted_count,
    }
    return {
        "report_type": "volume",
        "params": params.to_dict(),
        "summary": summary,
        "series": series,
        "breakdown": breakdown,
        "generated_at": _now_iso(),
    }


def _bucket_as_of_iso(value: Any) -> str:
    """Pick a sensible as-of date for FX conversion of a bucket.

    Postgres ``date_trunc('week', t)`` returns the Monday of that
    week. For FX lookups we want the date the bucket *covers*, so
    we use the truncated value directly — rates pinned to that date
    or earlier apply.
    """
    if value is None:
        return datetime.now(timezone.utc).date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    try:
        return value.isoformat()
    except Exception:
        return str(value)[:10]


# ---------------------------------------------------------------------------
# Report 2: Agent Performance
# ---------------------------------------------------------------------------

# States that count as "auto-resolved" for the auto-resolution rate.
# The agent decided + the bill went through without human touch. Items
# parked in needs_info / needs_approval / failed_post are excluded
# even if they later closed — those required human attention.
_AUTO_RESOLVED_STATES = ("posted_to_erp", "payment_executed", "closed")
_HUMAN_ATTENTION_STATES = (
    "needs_info", "needs_approval", "needs_second_approval",
    "failed_post", "rejected", "snoozed",
)


def generate_agent_performance_report(
    organization_id: str,
    *,
    period: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Agent confidence trend, auto-resolution rate, exception rate over time.

    Returns:
      summary:   {auto_resolution_rate, exception_rate, avg_confidence, sample_size}
      series:    [{bucket, auto_resolution_rate, exception_rate, avg_confidence,
                   total_items}, ...]
      breakdown: []  (n/a — agent perf is a single dimension over time)
    """
    params = _resolve_window(
        period=period, from_ts=from_ts, to_ts=to_ts, entity_id=entity_id,
    )
    db = get_db()
    trunc = _PERIOD_TO_PG_TRUNC[params.period]
    where_extra, where_args = _common_where(params)

    auto_states_clause = "(" + ",".join(["%s"] * len(_AUTO_RESOLVED_STATES)) + ")"
    exception_states_clause = "(" + ",".join(["%s"] * len(_HUMAN_ATTENTION_STATES)) + ")"

    # Series: per-bucket, count of items resolved, exceptions, and avg
    # extraction confidence (the field we have on ap_items today; the
    # governance agent_confidence is on audit_events).
    series_sql = (
        f"SELECT date_trunc('{trunc}', created_at::timestamptz) AS bucket, "
        "       COUNT(*)::bigint AS total_items, "
        f"      COUNT(*) FILTER (WHERE state IN {auto_states_clause} "
        "                       AND (exception_code IS NULL OR exception_code = '')) AS auto_resolved, "
        f"      COUNT(*) FILTER (WHERE state IN {exception_states_clause} "
        "                       OR (exception_code IS NOT NULL AND exception_code <> '')) AS with_exception, "
        "       AVG(confidence) FILTER (WHERE confidence IS NOT NULL AND confidence > 0) AS avg_confidence "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND created_at >= %s AND created_at < %s "
        f"  {where_extra} "
        "GROUP BY bucket ORDER BY bucket ASC"
    )
    summary_sql = (
        "SELECT COUNT(*)::bigint AS total_items, "
        f"      COUNT(*) FILTER (WHERE state IN {auto_states_clause} "
        "                       AND (exception_code IS NULL OR exception_code = '')) AS auto_resolved, "
        f"      COUNT(*) FILTER (WHERE state IN {exception_states_clause} "
        "                       OR (exception_code IS NOT NULL AND exception_code <> '')) AS with_exception, "
        "       AVG(confidence) FILTER (WHERE confidence IS NOT NULL AND confidence > 0) AS avg_confidence "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND created_at >= %s AND created_at < %s "
        f"  {where_extra}"
    )

    series_args = (
        tuple(_AUTO_RESOLVED_STATES) + tuple(_HUMAN_ATTENTION_STATES)
        + (organization_id, params.from_ts, params.to_ts)
        + where_args
    )
    summary_args = series_args  # identical bind order

    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(series_sql, series_args)
            series_rows = cur.fetchall()
            cur.execute(summary_sql, summary_args)
            summary_row = cur.fetchone()
    except Exception as exc:
        logger.warning("[reports.agent_performance] failed for org=%s: %s", organization_id, exc)
        return _empty_response("agent_performance", params)

    def _rates(total: int, auto: int, exc: int) -> Tuple[float, float]:
        if total <= 0:
            return 0.0, 0.0
        return round(auto / total, 4), round(exc / total, 4)

    series = []
    for row in series_rows:
        total = int(row[1] or 0)
        auto = int(row[2] or 0)
        exc = int(row[3] or 0)
        auto_rate, exc_rate = _rates(total, auto, exc)
        series.append({
            "bucket": _bucket_label(row[0], params.period),
            "total_items": total,
            "auto_resolution_rate": auto_rate,
            "exception_rate": exc_rate,
            "avg_confidence": round(float(row[4]), 4) if row[4] is not None else None,
        })

    if summary_row:
        total = int(summary_row[0] or 0)
        auto = int(summary_row[1] or 0)
        exc = int(summary_row[2] or 0)
        auto_rate, exc_rate = _rates(total, auto, exc)
        summary = {
            "sample_size": total,
            "auto_resolution_rate": auto_rate,
            "exception_rate": exc_rate,
            "avg_confidence": round(float(summary_row[3]), 4) if summary_row[3] is not None else None,
        }
    else:
        summary = {
            "sample_size": 0, "auto_resolution_rate": 0.0,
            "exception_rate": 0.0, "avg_confidence": None,
        }

    return {
        "report_type": "agent_performance",
        "params": params.to_dict(),
        "summary": summary,
        "series": series,
        "breakdown": [],
        "generated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Report 3: Cycle Time
# ---------------------------------------------------------------------------

def generate_cycle_time_report(
    organization_id: str,
    *,
    period: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Average days from invoice receipt to ERP post, by period and entity.

    Only items that reached ``erp_posted_at`` count — the metric is
    closed cycle time, not pending. ``created_at`` is the canonical
    receipt timestamp; ``erp_posted_at`` is the canonical post time.

    Returns:
      summary:   {avg_cycle_days, p50_cycle_days, p90_cycle_days, posted_count}
      series:    [{bucket, avg_cycle_days, p50_cycle_days, p90_cycle_days,
                   posted_count}, ...]
      breakdown: [{entity_id, entity_name, avg_cycle_days, posted_count}, ...]
                  one row per entity (when multi-entity orgs use this).
    """
    params = _resolve_window(
        period=period, from_ts=from_ts, to_ts=to_ts, entity_id=entity_id,
    )
    db = get_db()
    trunc = _PERIOD_TO_PG_TRUNC[params.period]
    where_extra, where_args = _common_where(params)

    # erp_posted_at + created_at are TEXT (ISO strings) in the schema —
    # cast both to timestamptz for arithmetic. extract(epoch from delta)
    # gives seconds; divide by 86400 for days.
    delta_expr = (
        "EXTRACT(EPOCH FROM "
        " (erp_posted_at::timestamptz - created_at::timestamptz)) / 86400.0"
    )

    series_sql = (
        f"SELECT date_trunc('{trunc}', erp_posted_at::timestamptz) AS bucket, "
        f"       AVG({delta_expr}) AS avg_days, "
        f"       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {delta_expr}) AS p50_days, "
        f"       PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY {delta_expr}) AS p90_days, "
        "        COUNT(*)::bigint AS posted_count "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND erp_posted_at IS NOT NULL "
        "  AND erp_posted_at::timestamptz >= %s AND erp_posted_at::timestamptz < %s "
        "  AND created_at IS NOT NULL "
        f"  {where_extra} "
        "GROUP BY bucket ORDER BY bucket ASC"
    )
    summary_sql = (
        f"SELECT AVG({delta_expr}) AS avg_days, "
        f"       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {delta_expr}) AS p50_days, "
        f"       PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY {delta_expr}) AS p90_days, "
        "        COUNT(*)::bigint AS posted_count "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND erp_posted_at IS NOT NULL "
        "  AND erp_posted_at::timestamptz >= %s AND erp_posted_at::timestamptz < %s "
        "  AND created_at IS NOT NULL "
        f"  {where_extra}"
    )
    breakdown_sql = (
        "SELECT entity_id, "
        f"       AVG({delta_expr}) AS avg_days, "
        "        COUNT(*)::bigint AS posted_count "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND erp_posted_at IS NOT NULL "
        "  AND erp_posted_at::timestamptz >= %s AND erp_posted_at::timestamptz < %s "
        "  AND created_at IS NOT NULL "
        f"  {where_extra} "
        "GROUP BY entity_id ORDER BY posted_count DESC LIMIT 25"
    )

    base_args = (organization_id, params.from_ts, params.to_ts)

    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(series_sql, base_args + where_args)
            series_rows = cur.fetchall()
            cur.execute(summary_sql, base_args + where_args)
            summary_row = cur.fetchone()
            cur.execute(breakdown_sql, base_args + where_args)
            breakdown_rows = cur.fetchall()

            # Resolve entity names if the entities table has rows for
            # this org. Falls back to the raw entity_id when missing.
            entity_names: Dict[str, str] = {}
            try:
                if hasattr(db, "list_entities"):
                    for ent in db.list_entities(organization_id):
                        eid = ent.get("entity_id") or ent.get("id")
                        if eid:
                            entity_names[str(eid)] = ent.get("name") or ent.get("entity_code") or str(eid)
            except Exception:
                pass
    except Exception as exc:
        logger.warning("[reports.cycle_time] failed for org=%s: %s", organization_id, exc)
        return _empty_response("cycle_time", params)

    series = [
        {
            "bucket": _bucket_label(row[0], params.period),
            "avg_cycle_days": round(float(row[1]), 2) if row[1] is not None else None,
            "p50_cycle_days": round(float(row[2]), 2) if row[2] is not None else None,
            "p90_cycle_days": round(float(row[3]), 2) if row[3] is not None else None,
            "posted_count": int(row[4] or 0),
        }
        for row in series_rows
    ]
    breakdown = [
        {
            "entity_id": row[0],
            "entity_name": entity_names.get(str(row[0])) if row[0] else None,
            "avg_cycle_days": round(float(row[1]), 2) if row[1] is not None else None,
            "posted_count": int(row[2] or 0),
        }
        for row in breakdown_rows
    ]
    if summary_row:
        summary = {
            "avg_cycle_days": round(float(summary_row[0]), 2) if summary_row[0] is not None else None,
            "p50_cycle_days": round(float(summary_row[1]), 2) if summary_row[1] is not None else None,
            "p90_cycle_days": round(float(summary_row[2]), 2) if summary_row[2] is not None else None,
            "posted_count": int(summary_row[3] or 0),
        }
    else:
        summary = {
            "avg_cycle_days": None, "p50_cycle_days": None,
            "p90_cycle_days": None, "posted_count": 0,
        }

    return {
        "report_type": "cycle_time",
        "params": params.to_dict(),
        "summary": summary,
        "series": series,
        "breakdown": breakdown,
        "generated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Report 4: Exception Breakdown
# ---------------------------------------------------------------------------

def generate_exception_breakdown_report(
    organization_id: str,
    *,
    period: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Exception types ranked by count, plus per-period trend.

    Returns:
      summary:    {total_exceptions, distinct_codes, top_code, top_code_count}
      series:     [{bucket, total_exceptions}, ...]
      breakdown:  [{exception_code, count, share}, ...]  ranked desc by count
    """
    params = _resolve_window(
        period=period, from_ts=from_ts, to_ts=to_ts, entity_id=entity_id,
    )
    db = get_db()
    trunc = _PERIOD_TO_PG_TRUNC[params.period]
    where_extra, where_args = _common_where(params)

    breakdown_sql = (
        "SELECT exception_code, COUNT(*)::bigint AS count "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND created_at >= %s AND created_at < %s "
        "  AND exception_code IS NOT NULL AND exception_code <> '' "
        f"  {where_extra} "
        "GROUP BY exception_code ORDER BY count DESC LIMIT 20"
    )
    series_sql = (
        f"SELECT date_trunc('{trunc}', created_at::timestamptz) AS bucket, "
        "       COUNT(*)::bigint AS total_exceptions "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND created_at >= %s AND created_at < %s "
        "  AND exception_code IS NOT NULL AND exception_code <> '' "
        f"  {where_extra} "
        "GROUP BY bucket ORDER BY bucket ASC"
    )

    base_args = (organization_id, params.from_ts, params.to_ts)

    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(breakdown_sql, base_args + where_args)
            breakdown_rows = cur.fetchall()
            cur.execute(series_sql, base_args + where_args)
            series_rows = cur.fetchall()
    except Exception as exc:
        logger.warning("[reports.exception_breakdown] failed for org=%s: %s", organization_id, exc)
        return _empty_response("exception_breakdown", params)

    total_exceptions = sum(int(row[1] or 0) for row in breakdown_rows)
    breakdown = []
    for row in breakdown_rows:
        count = int(row[1] or 0)
        share = round(count / total_exceptions, 4) if total_exceptions else 0.0
        breakdown.append({
            "exception_code": row[0],
            "count": count,
            "share": share,
        })

    series = [
        {
            "bucket": _bucket_label(row[0], params.period),
            "total_exceptions": int(row[1] or 0),
        }
        for row in series_rows
    ]

    top_code = breakdown[0] if breakdown else None
    summary = {
        "total_exceptions": total_exceptions,
        "distinct_codes": len(breakdown),
        "top_code": top_code["exception_code"] if top_code else None,
        "top_code_count": top_code["count"] if top_code else 0,
    }

    return {
        "report_type": "exception_breakdown",
        "params": params.to_dict(),
        "summary": summary,
        "series": series,
        "breakdown": breakdown,
        "generated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Report 5: Vendor Quality
# ---------------------------------------------------------------------------

def generate_vendor_quality_report(
    organization_id: str,
    *,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    entity_id: Optional[str] = None,
    min_invoices: int = 3,
    limit: int = 25,
) -> Dict[str, Any]:
    """Vendors ranked by exception rate.

    Per spec line 282 ("no personally identifying ranking"), this
    ranks **vendor relationships**, not employees. The metric is the
    fraction of invoices from a given vendor that landed with a
    non-null exception_code.

    A minimum-invoice floor (``min_invoices``) keeps a 1-of-1 vendor
    from showing up at 100% exception rate next to a 200-invoice
    vendor at 12% — the floor enforces statistical relevance.

    Returns:
      summary:   {ranked_vendor_count, avg_exception_rate, worst_vendor}
      series:    []  (per-vendor not a time series)
      breakdown: [{vendor_name, total_invoices, exception_count,
                  exception_rate}, ...]  ranked desc by exception_rate
    """
    params = _resolve_window(
        period="weekly",  # not used; vendor quality is a snapshot
        from_ts=from_ts, to_ts=to_ts, entity_id=entity_id,
    )
    db = get_db()
    where_extra, where_args = _common_where(params)
    floor = max(1, int(min_invoices or 1))
    limit_n = max(1, min(int(limit or 25), 100))

    sql = (
        "SELECT vendor_name, "
        "       COUNT(*)::bigint AS total_invoices, "
        "       COUNT(*) FILTER (WHERE exception_code IS NOT NULL "
        "                       AND exception_code <> '')::bigint AS exception_count "
        "FROM ap_items "
        "WHERE organization_id = %s "
        "  AND created_at >= %s AND created_at < %s "
        "  AND vendor_name IS NOT NULL AND vendor_name <> '' "
        f"  {where_extra} "
        "GROUP BY vendor_name "
        "HAVING COUNT(*) >= %s "
        "ORDER BY (COUNT(*) FILTER (WHERE exception_code IS NOT NULL "
        "                          AND exception_code <> ''))::float / "
        "         NULLIF(COUNT(*), 0) DESC, "
        "         total_invoices DESC "
        "LIMIT %s"
    )
    base_args = (organization_id, params.from_ts, params.to_ts)
    args = base_args + where_args + (floor, limit_n)

    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, args)
            rows = cur.fetchall()
    except Exception as exc:
        logger.warning("[reports.vendor_quality] failed for org=%s: %s", organization_id, exc)
        return _empty_response("vendor_quality", params)

    breakdown = []
    for row in rows:
        total = int(row[1] or 0)
        excs = int(row[2] or 0)
        rate = round(excs / total, 4) if total else 0.0
        breakdown.append({
            "vendor_name": row[0],
            "total_invoices": total,
            "exception_count": excs,
            "exception_rate": rate,
        })

    if breakdown:
        avg_rate = round(
            sum(b["exception_rate"] for b in breakdown) / len(breakdown), 4,
        )
        worst = breakdown[0]
    else:
        avg_rate = 0.0
        worst = None

    summary = {
        "ranked_vendor_count": len(breakdown),
        "avg_exception_rate": avg_rate,
        "worst_vendor": worst["vendor_name"] if worst else None,
        "worst_exception_rate": worst["exception_rate"] if worst else None,
        "min_invoices_floor": floor,
    }

    return {
        "report_type": "vendor_quality",
        "params": params.to_dict(),
        "summary": summary,
        "series": [],
        "breakdown": breakdown,
        "generated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Common WHERE-clause builder for entity / vendor filters
# ---------------------------------------------------------------------------

def _common_where(params: ReportParams) -> Tuple[str, Tuple[Any, ...]]:
    """Builds the shared WHERE-clause fragment for entity/vendor filters.

    Returns the SQL fragment (starts with " AND " when non-empty) and
    the tuple of bind parameters in the order they appear. The caller
    splices the fragment into the query and concatenates the args
    after the base ``(organization_id, from_ts, to_ts)`` triple.
    """
    fragments: List[str] = []
    args: List[Any] = []
    if params.entity_id:
        fragments.append("AND entity_id = %s")
        args.append(params.entity_id)
    if params.vendor_name:
        fragments.append("AND vendor_name = %s")
        args.append(params.vendor_name)
    return (" ".join(fragments), tuple(args))


def _bucket_label(value: Any, period: str) -> str:
    """Render a date_trunc result as a stable string label."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        if period == "monthly":
            return value.strftime("%Y-%m")
        if period == "weekly":
            # ISO week year + week-of-year. Postgres date_trunc('week')
            # already pins to Monday; format as YYYY-W##.
            iso = value.isocalendar()
            return f"{iso[0]:04d}-W{iso[1]:02d}"
        return value.strftime("%Y-%m-%d")
    except Exception:
        return str(value)


# ---------------------------------------------------------------------------
# Report registry — used by the API endpoint dispatch + scheduled email.
# ---------------------------------------------------------------------------

REPORT_GENERATORS = {
    "volume": generate_volume_report,
    "agent_performance": generate_agent_performance_report,
    "cycle_time": generate_cycle_time_report,
    "exception_breakdown": generate_exception_breakdown_report,
    "vendor_quality": generate_vendor_quality_report,
}

VALID_REPORT_TYPES = frozenset(REPORT_GENERATORS.keys())


# ---------------------------------------------------------------------------
# CSV serialisation
# ---------------------------------------------------------------------------

# Each report's "primary" view + the column order operators see when
# they download a spreadsheet. The trend reports surface the time
# series (bucket-level rows); the ranking reports surface the
# breakdown (one row per ranked entity). The non-primary view is
# still available via the JSON endpoint when a user wants both.
_CSV_SHAPE = {
    "volume": (
        "series",
        ["bucket", "invoice_count", "total_amount"],
    ),
    "agent_performance": (
        "series",
        ["bucket", "total_items", "auto_resolution_rate", "exception_rate", "avg_confidence"],
    ),
    "cycle_time": (
        "series",
        ["bucket", "avg_cycle_days", "p50_cycle_days", "p90_cycle_days", "posted_count"],
    ),
    "exception_breakdown": (
        "breakdown",
        ["exception_code", "count", "share"],
    ),
    "vendor_quality": (
        "breakdown",
        ["vendor_name", "total_invoices", "exception_count", "exception_rate"],
    ),
}


def report_to_csv(report_payload: Dict[str, Any]) -> str:
    """Serialise a report payload to a CSV string.

    Same UTF-8 BOM convention as ``report_export.rows_to_csv`` —
    Excel on Windows otherwise mangles non-ASCII vendor names. The
    BOM is invisible to UTF-8-aware tools (Sheets, Numbers, modern
    Excel).
    """
    import csv as _csv
    import io as _io

    report_type = report_payload.get("report_type", "")
    shape = _CSV_SHAPE.get(report_type)
    if shape is None:
        return "﻿" + ""
    section_key, columns = shape
    rows = report_payload.get(section_key) or []

    output = _io.StringIO()
    writer = _csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: _csv_cell(row.get(col)) for col in columns})
    return "﻿" + output.getvalue()


def _csv_cell(value: Any) -> str:
    """Render a value for a CSV cell. None becomes empty; floats keep
    their precision but lose trailing-zero noise."""
    if value is None:
        return ""
    if isinstance(value, float):
        # Floor to a sensible precision so 0.34999999999999998 doesn't
        # land in the spreadsheet.
        return f"{value:g}"
    return str(value)


def csv_filename(report_type: str, params: Dict[str, Any]) -> str:
    """Stable filename hint for the Content-Disposition header."""
    safe = report_type.replace("_", "-")
    from_ts = (params.get("from") or "")[:10] or "all"
    to_ts = (params.get("to") or "")[:10] or "all"
    return f"clearledgr-{safe}-{from_ts}-to-{to_ts}.csv"
