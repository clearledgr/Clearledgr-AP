"""Spend analysis service — portfolio-level spend intelligence.

Aggregates AP item data to produce:
- Top vendors by spend
- Spend by GL category (from vendor profiles)
- Monthly trends with month-over-month change %
- Budget utilization (delegates to BudgetAwarenessService)
- Portfolio anomalies (vendor spikes, new vendors, unusual GL)
- Summary metrics (total spend, invoice count, avg days to post)

All queries filter by organization_id.  Never raises — returns empty on error.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SpendAnalysisService:
    """Spend analysis across the AP portfolio for a single tenant."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def analyze(self, period_days: int = 30) -> Dict[str, Any]:
        """Return a full spend analysis dict.  Never raises."""
        try:
            period_days = max(1, int(period_days))
        except (TypeError, ValueError):
            period_days = 30

        try:
            return {
                "organization_id": self.organization_id,
                "period_days": period_days,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "summary": self._build_summary(period_days),
                "top_vendors": self._top_vendors_by_spend(period_days),
                "spend_by_gl_category": self._spend_by_gl_category(period_days),
                "monthly_trends": self._monthly_trends(months=6),
                "budget_utilization": self._budget_utilization(),
                "anomalies": self._detect_portfolio_anomalies(period_days),
            }
        except Exception as exc:
            logger.warning("[SpendAnalysis] analyze failed: %s", exc)
            return {
                "organization_id": self.organization_id,
                "period_days": period_days,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "summary": {},
                "top_vendors": [],
                "spend_by_gl_category": [],
                "monthly_trends": [],
                "budget_utilization": {},
                "anomalies": [],
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Private query helpers
    # ------------------------------------------------------------------

    def _top_vendors_by_spend(
        self, period_days: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Top vendors by total spend in the period.

        Only considers AP items in terminal-success states
        (posted_to_erp, closed).
        """
        cutoff = self._cutoff_iso(period_days)
        sql = (
            "SELECT vendor_name, SUM(amount) AS total, COUNT(*) AS invoice_count "
            "FROM ap_items "
            "WHERE organization_id = %s "
            "  AND state IN ('posted_to_erp', 'closed') "
            "  AND created_at >= %s "
            "  AND amount IS NOT NULL "
            "GROUP BY vendor_name "
            "ORDER BY total DESC "
            "LIMIT %s"
        )
        try:
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id, cutoff, limit))
                rows = cur.fetchall()
            return [
                {
                    "vendor_name": row[0],
                    "total_spend": float(row[1] or 0),
                    "invoice_count": int(row[2] or 0),
                }
                for row in rows
                if row[0]
            ]
        except Exception as exc:
            logger.warning("[SpendAnalysis] _top_vendors_by_spend failed: %s", exc)
            return []

    def _spend_by_gl_category(self, period_days: int) -> List[Dict[str, Any]]:
        """Aggregate spend by GL code.

        Uses the vendor profile's ``typical_gl_code`` to map each vendor's
        spend to a GL category.  Vendors with no GL mapping are bucketed
        under ``"unclassified"``.
        """
        cutoff = self._cutoff_iso(period_days)
        # Step 1: get per-vendor spend
        sql = (
            "SELECT vendor_name, SUM(amount) AS total "
            "FROM ap_items "
            "WHERE organization_id = %s "
            "  AND state IN ('posted_to_erp', 'closed') "
            "  AND created_at >= %s "
            "  AND amount IS NOT NULL "
            "GROUP BY vendor_name"
        )
        try:
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id, cutoff))
                vendor_spend_rows = cur.fetchall()

            vendor_spend: Dict[str, float] = {}
            vendor_names: List[str] = []
            for row in vendor_spend_rows:
                name = row[0]
                if name:
                    vendor_spend[name] = float(row[1] or 0)
                    vendor_names.append(name)

            # Step 2: look up GL codes from vendor profiles
            profiles = self.db.get_vendor_profiles_bulk(
                self.organization_id, vendor_names
            )

            gl_totals: Dict[str, float] = {}
            for vname, amount in vendor_spend.items():
                profile = profiles.get(vname) or {}
                gl = profile.get("typical_gl_code") or "unclassified"
                gl_totals[gl] = gl_totals.get(gl, 0.0) + amount

            return sorted(
                [
                    {"gl_code": gl, "total_spend": total}
                    for gl, total in gl_totals.items()
                ],
                key=lambda x: x["total_spend"],
                reverse=True,
            )
        except Exception as exc:
            logger.warning("[SpendAnalysis] _spend_by_gl_category failed: %s", exc)
            return []

    def _monthly_trends(self, months: int = 6) -> List[Dict[str, Any]]:
        """Monthly spend totals with month-over-month change %.

        Returns newest month first.
        """
        now = datetime.now(timezone.utc)
        trends: List[Dict[str, Any]] = []

        for i in range(months):
            # Compute start/end of month (i=0 is current month)
            ref = now.replace(day=1) - timedelta(days=30 * i)
            month_start = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if month_start.month == 12:
                month_end = month_start.replace(
                    year=month_start.year + 1, month=1
                )
            else:
                month_end = month_start.replace(month=month_start.month + 1)

            sql = (
                "SELECT SUM(amount) AS total, COUNT(*) AS cnt "
                "FROM ap_items "
                "WHERE organization_id = %s "
                "  AND state IN ('posted_to_erp', 'closed') "
                "  AND created_at >= %s "
                "  AND created_at < %s "
                "  AND amount IS NOT NULL"
            )
            try:
                self.db.initialize()
                with self.db.connect() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        sql,
                        (
                            self.organization_id,
                            month_start.isoformat(),
                            month_end.isoformat(),
                        ),
                    )
                    row = cur.fetchone()
                total = float(row[0] or 0) if row else 0.0
                count = int(row[1] or 0) if row else 0
            except Exception:
                total = 0.0
                count = 0

            trends.append({
                "month": month_start.strftime("%Y-%m"),
                "total_spend": total,
                "invoice_count": count,
                "mom_change_pct": None,  # filled below
            })

        # Compute month-over-month change % (compare to previous month)
        for idx in range(len(trends) - 1):
            current = trends[idx]["total_spend"]
            previous = trends[idx + 1]["total_spend"]
            if previous > 0:
                trends[idx]["mom_change_pct"] = round(
                    ((current - previous) / previous) * 100, 1
                )
            elif current > 0:
                trends[idx]["mom_change_pct"] = 100.0
            else:
                trends[idx]["mom_change_pct"] = 0.0

        return trends

    def _budget_utilization(self) -> Dict[str, Any]:
        """Budget utilization from BudgetAwarenessService."""
        try:
            from clearledgr.services.budget_awareness import get_budget_awareness
            service = get_budget_awareness(self.organization_id)
            report = service.get_report()
            return report.to_dict()
        except Exception as exc:
            logger.debug("[SpendAnalysis] _budget_utilization skipped: %s", exc)
            return {}

    def _detect_portfolio_anomalies(
        self, period_days: int
    ) -> List[Dict[str, Any]]:
        """Detect vendor spend spikes >50%, new vendors, and unusual GL patterns."""
        anomalies: List[Dict[str, Any]] = []

        try:
            # Current period spend by vendor
            current_spend = self.db.get_spending_by_vendor(
                self.organization_id, days=period_days
            )
            # Previous period spend by vendor (same duration, shifted back)
            previous_spend = self.db.get_spending_for_period(
                self.organization_id,
                days_ago_start=period_days * 2,
                days_ago_end=period_days,
            )

            # Vendor spend spikes >50%
            for vendor, current_total in current_spend.items():
                prev_total = previous_spend.get(vendor, 0.0)
                if prev_total > 0:
                    change_pct = ((current_total - prev_total) / prev_total) * 100
                    if change_pct > 50:
                        anomalies.append({
                            "type": "spend_spike",
                            "vendor": vendor,
                            "current_spend": current_total,
                            "previous_spend": prev_total,
                            "change_pct": round(change_pct, 1),
                            "message": (
                                f"{vendor} spend increased {change_pct:.0f}% "
                                f"(${prev_total:,.2f} -> ${current_total:,.2f})"
                            ),
                        })

            # New vendors (in current period but not in previous)
            for vendor, total in current_spend.items():
                if vendor not in previous_spend:
                    anomalies.append({
                        "type": "new_vendor",
                        "vendor": vendor,
                        "current_spend": total,
                        "message": f"New vendor: {vendor} (${total:,.2f} in period)",
                    })

            # Unusual GL: vendors with no GL code mapping
            try:
                vendor_names = list(current_spend.keys())
                if vendor_names:
                    profiles = self.db.get_vendor_profiles_bulk(
                        self.organization_id, vendor_names
                    )
                    for vendor in vendor_names:
                        profile = profiles.get(vendor) or {}
                        if not profile.get("typical_gl_code"):
                            spend_amount = current_spend[vendor]
                            anomalies.append({
                                "type": "missing_gl_mapping",
                                "vendor": vendor,
                                "current_spend": spend_amount,
                                "message": (
                                    f"{vendor} has no GL code mapping "
                                    f"(${spend_amount:,.2f} unclassified)"
                                ),
                            })
            except Exception:
                pass  # GL anomaly detection is best-effort

        except Exception as exc:
            logger.warning(
                "[SpendAnalysis] _detect_portfolio_anomalies failed: %s", exc
            )

        return anomalies

    def _build_summary(self, period_days: int) -> Dict[str, Any]:
        """Total spend, invoice count, and average days to post."""
        cutoff = self._cutoff_iso(period_days)
        sql = (
            "SELECT SUM(amount) AS total, COUNT(*) AS cnt "
            "FROM ap_items "
            "WHERE organization_id = %s "
            "  AND state IN ('posted_to_erp', 'closed') "
            "  AND created_at >= %s "
            "  AND amount IS NOT NULL"
        )
        try:
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id, cutoff))
                row = cur.fetchone()
            total = float(row[0] or 0) if row else 0.0
            count = int(row[1] or 0) if row else 0
        except Exception:
            total = 0.0
            count = 0

        # Average days from created_at to erp_posted_at
        avg_days = self._avg_days_to_post(period_days)

        return {
            "total_spend": total,
            "invoice_count": count,
            "avg_days_to_post": avg_days,
            "period_days": period_days,
        }

    def _avg_days_to_post(self, period_days: int) -> Optional[float]:
        """Average days between created_at and erp_posted_at for posted items."""
        cutoff = self._cutoff_iso(period_days)
        sql = (
            "SELECT created_at, erp_posted_at "
            "FROM ap_items "
            "WHERE organization_id = %s "
            "  AND state IN ('posted_to_erp', 'closed') "
            "  AND created_at >= %s "
            "  AND erp_posted_at IS NOT NULL"
        )
        try:
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id, cutoff))
                rows = cur.fetchall()

            if not rows:
                return None

            total_days = 0.0
            valid = 0
            for row in rows:
                try:
                    created = self._parse_datetime(row[0])
                    posted = self._parse_datetime(row[1])
                    if created and posted:
                        delta = (posted - created).total_seconds() / 86400.0
                        if delta >= 0:
                            total_days += delta
                            valid += 1
                except Exception:
                    continue

            return round(total_days / valid, 1) if valid > 0 else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _cutoff_iso(period_days: int) -> str:
        return (
            datetime.now(timezone.utc) - timedelta(days=max(1, period_days))
        ).isoformat()

    @staticmethod
    def _parse_datetime(val: Any) -> Optional[datetime]:
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        try:
            raw = str(val)
            # Handle ISO format with or without timezone
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None


def get_spend_analysis_service(
    organization_id: str = "default",
) -> SpendAnalysisService:
    """Factory — returns a new SpendAnalysisService for the given org."""
    return SpendAnalysisService(organization_id=organization_id)
