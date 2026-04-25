"""AP aging report — open payables bucketed by days past due.

Computes aging buckets (current, 1-30, 31-60, 61-90, 90+ days) from
AP items that have a due_date and are not yet closed/rejected.
Includes per-bucket totals (grouped by currency), vendor breakdown,
and summary stats.

All queries filter by organization_id.  Never raises — returns empty on error.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from clearledgr.core.money import ZERO, money_sum, money_to_float, to_decimal

logger = logging.getLogger(__name__)

# Ordered bucket definitions: (label, max_days_past_due_inclusive)
# None = unbounded upper end.
AGING_BUCKETS: List[Tuple[str, Optional[int]]] = [
    ("current", 0),
    ("1_30", 30),
    ("31_60", 60),
    ("61_90", 90),
    ("90_plus", None),
]

# Labels only, for iteration
BUCKET_LABELS = [label for label, _ in AGING_BUCKETS]


def _bucket_label(days_past_due: int) -> str:
    """Return the aging bucket label for a given number of days past due."""
    for label, cap in AGING_BUCKETS:
        if cap is not None and days_past_due <= cap:
            return label
    return "90_plus"


# Default max items per bucket in the response
_DEFAULT_ITEMS_PER_BUCKET = 50


class APAgingReport:
    """Generates an AP aging report for a single tenant."""

    # States considered "open" — invoice exists but isn't fully resolved
    OPEN_STATES = (
        "received",
        "validated",
        "needs_approval",
        "approved",
        "ready_to_post",
        "posted_to_erp",
    )

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    def generate(
        self, items_per_bucket: int = _DEFAULT_ITEMS_PER_BUCKET
    ) -> Dict[str, Any]:
        """Return the full aging report.  Never raises."""
        try:
            items_per_bucket = max(0, int(items_per_bucket))
        except (TypeError, ValueError):
            items_per_bucket = _DEFAULT_ITEMS_PER_BUCKET

        try:
            items = self._fetch_open_items()
            no_due_date_count = self._count_no_due_date()
            today = date.today()
            buckets = self._build_buckets(items, today, items_per_bucket)
            vendor_breakdown = self._build_vendor_breakdown(items, today)
            summary = self._build_summary(buckets, items, no_due_date_count)
            return {
                "organization_id": self.organization_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "as_of_date": today.isoformat(),
                "summary": summary,
                "buckets": buckets,
                "vendor_breakdown": vendor_breakdown,
            }
        except Exception as exc:
            logger.warning("[APAgingReport] generate failed: %s", exc)
            return {
                "organization_id": self.organization_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "as_of_date": date.today().isoformat(),
                "summary": {},
                "buckets": {},
                "vendor_breakdown": [],
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_open_items(self) -> List[Dict[str, Any]]:
        """Fetch all open AP items with a due_date for this org."""
        placeholders = ", ".join("%s" for _ in self.OPEN_STATES)
        sql = (
            f"SELECT id, vendor_name, amount, due_date, state, currency, "
            f"invoice_number, created_at "
            f"FROM ap_items "
            f"WHERE organization_id = %s "
            f"  AND due_date IS NOT NULL "
            f"  AND state IN ({placeholders}) "
            f"ORDER BY due_date ASC"
        )
        params = [self.organization_id] + list(self.OPEN_STATES)
        try:
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("[APAgingReport] _fetch_open_items failed: %s", exc)
            return []

    def _count_no_due_date(self) -> int:
        """Count open AP items with no due_date (excluded from aging)."""
        placeholders = ", ".join("%s" for _ in self.OPEN_STATES)
        sql = (
            f"SELECT COUNT(*) FROM ap_items "
            f"WHERE organization_id = %s "
            f"  AND (due_date IS NULL OR due_date = '') "
            f"  AND state IN ({placeholders})"
        )
        params = [self.organization_id] + list(self.OPEN_STATES)
        try:
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Bucket building
    # ------------------------------------------------------------------

    def _build_buckets(
        self,
        items: List[Dict[str, Any]],
        today: date,
        items_per_bucket: int,
    ) -> Dict[str, Dict[str, Any]]:
        """Bucket items by days past due, with currency-grouped totals."""
        result: Dict[str, Dict[str, Any]] = {}
        for label in BUCKET_LABELS:
            result[label] = {
                "totals_by_currency": {},
                "count": 0,
                "items": [],
            }

        # Track per-currency totals in Decimal so a bucket with 1000
        # invoices doesn't drift by a cent. At render time we convert
        # to float for the JSON response.
        decimal_totals: Dict[str, Dict[str, Decimal]] = {
            label: defaultdict(lambda: ZERO) for label in result
        }

        for item in items:
            due = self._parse_date(item.get("due_date"))
            if due is None:
                continue
            days_past = (today - due).days
            label = _bucket_label(days_past)
            amount = to_decimal(item.get("amount"))
            currency = item.get("currency") or "USD"

            bucket = result[label]
            decimal_totals[label][currency] = decimal_totals[label][currency] + amount
            bucket["count"] += 1
            if len(bucket["items"]) < items_per_bucket:
                bucket["items"].append({
                    "id": item.get("id"),
                    "vendor_name": item.get("vendor_name"),
                    "invoice_number": item.get("invoice_number"),
                    "amount": money_to_float(amount),
                    "currency": currency,
                    "due_date": str(item.get("due_date") or ""),
                    "days_past_due": max(0, days_past),
                    "state": item.get("state"),
                })

        # Convert Decimal per-currency totals to floats at the JSON boundary.
        for label in result:
            result[label]["totals_by_currency"] = {
                cur: money_to_float(amt)
                for cur, amt in decimal_totals[label].items()
            }

        return result

    # ------------------------------------------------------------------
    # Vendor breakdown
    # ------------------------------------------------------------------

    def _build_vendor_breakdown(
        self, items: List[Dict[str, Any]], today: date
    ) -> List[Dict[str, Any]]:
        """Aging breakdown per vendor, grouped by currency."""
        # vendor -> currency -> bucket -> Decimal amount
        data: Dict[str, Dict[str, Dict[str, Decimal]]] = defaultdict(
            lambda: defaultdict(lambda: {label: ZERO for label in BUCKET_LABELS})
        )

        for item in items:
            vendor = item.get("vendor_name") or "Unknown"
            due = self._parse_date(item.get("due_date"))
            if due is None:
                continue
            days_past = (today - due).days
            label = _bucket_label(days_past)
            amount = to_decimal(item.get("amount"))
            currency = item.get("currency") or "USD"

            data[vendor][currency][label] = data[vendor][currency][label] + amount

        rows = []
        for vendor, currencies in data.items():
            for currency, bucket_amounts in currencies.items():
                row: Dict[str, Any] = {
                    "vendor_name": vendor,
                    "currency": currency,
                    "total": money_to_float(money_sum(bucket_amounts.values())),
                }
                for label in BUCKET_LABELS:
                    row[label] = money_to_float(bucket_amounts[label])
                rows.append(row)

        # Sort by total descending
        rows.sort(key=lambda r: r["total"], reverse=True)
        return rows

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _build_summary(
        self,
        buckets: Dict[str, Dict[str, Any]],
        items: List[Dict[str, Any]],
        no_due_date_count: int,
    ) -> Dict[str, Any]:
        """Summary stats from the bucketed data."""
        total_count = sum(b["count"] for b in buckets.values())
        overdue_count = sum(
            b["count"] for label, b in buckets.items() if label != "current"
        )
        vendor_count = len({
            item.get("vendor_name") for item in items if item.get("vendor_name")
        })

        # Aggregate totals by currency across all buckets
        totals_by_currency: Dict[str, float] = defaultdict(float)
        overdue_by_currency: Dict[str, float] = defaultdict(float)
        current_by_currency: Dict[str, float] = defaultdict(float)

        for label, bucket in buckets.items():
            for cur, amt in bucket.get("totals_by_currency", {}).items():
                totals_by_currency[cur] += amt
                if label != "current":
                    overdue_by_currency[cur] += amt
                else:
                    current_by_currency[cur] += amt

        def _round_currency_dict(d: Dict[str, float]) -> Dict[str, float]:
            return {cur: round(amt, 2) for cur, amt in d.items()}

        # Weighted average days past due
        weighted_avg = self._weighted_avg_days_past_due(items)

        return {
            "total_open_payables": _round_currency_dict(dict(totals_by_currency)),
            "total_open_count": total_count,
            "total_overdue": _round_currency_dict(dict(overdue_by_currency)),
            "overdue_count": overdue_count,
            "current_payables": _round_currency_dict(dict(current_by_currency)),
            "current_count": buckets.get("current", {}).get("count", 0),
            "vendor_count": vendor_count,
            "no_due_date_count": no_due_date_count,
            "weighted_avg_days_past_due": weighted_avg,
            "overdue_pct": (
                round((overdue_count / total_count) * 100, 1)
                if total_count > 0
                else 0.0
            ),
        }

    @staticmethod
    def _weighted_avg_days_past_due(items: List[Dict[str, Any]]) -> Optional[float]:
        """Dollar-weighted average days past due (overdue items only).

        Weight = item amount.  Only counts items where days_past_due > 0.
        Returns None if no overdue items exist.
        """
        today = date.today()
        total_weight = 0.0
        weighted_sum = 0.0
        for item in items:
            due = APAgingReport._parse_date(item.get("due_date"))
            if due is None:
                continue
            days_past = (today - due).days
            if days_past <= 0:
                continue
            amount = float(item.get("amount") or 0)
            if amount <= 0:
                continue
            weighted_sum += days_past * amount
            total_weight += amount
        if total_weight <= 0:
            return None
        return round(weighted_sum / total_weight, 1)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(val: Any) -> Optional[date]:
        """Parse a date string (YYYY-MM-DD or ISO datetime) to a date object."""
        if val is None:
            return None
        if isinstance(val, date) and not isinstance(val, datetime):
            return val
        if isinstance(val, datetime):
            return val.date()
        try:
            raw = str(val).strip()
            # Handle "YYYY-MM-DD" (possibly with time appended)
            return date.fromisoformat(raw[:10])
        except (TypeError, ValueError):
            return None


def get_ap_aging_report(organization_id: str = "default") -> APAgingReport:
    """Factory — returns a new APAgingReport for the given org."""
    return APAgingReport(organization_id=organization_id)
