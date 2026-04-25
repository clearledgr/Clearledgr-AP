"""Period close and accrual cutoff service.

Manages month-end / quarter-end close processes:
- Configurable cutoff dates per org (e.g. "March closes on April 5")
- Backdate detection: invoices received after cutoff that belong to prior period
- Accrual candidates: approved/posted items with no payment (uninvoiced liabilities)
- Period lock: prevents posting to closed periods
- Accrual report: estimated liabilities by vendor and GL

Period config is stored in org settings_json under "period_close".
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default: period closes on the 5th of the following month
DEFAULT_CLOSE_DAY_OFFSET = 5


class PeriodCloseService:
    """Manage period close and accrual cutoff for a single tenant."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def get_config(self) -> Dict[str, Any]:
        """Return period close config from org settings."""
        try:
            org = self.db.get_organization(self.organization_id)
            if not org:
                return self._default_config()
            settings = org.get("settings_json") or org.get("settings") or {}
            if isinstance(settings, str):
                settings = json.loads(settings)
            return settings.get("period_close") or self._default_config()
        except Exception:
            return self._default_config()

    def save_config(self, config: Dict[str, Any]) -> None:
        """Save period close config to org settings."""
        try:
            org = self.db.get_organization(self.organization_id)
            if not org:
                return
            settings = org.get("settings_json") or org.get("settings") or {}
            if isinstance(settings, str):
                settings = json.loads(settings)
            if not isinstance(settings, dict):
                settings = {}
            settings["period_close"] = config
            self.db.update_organization(self.organization_id, settings_json=settings)
        except Exception as exc:
            logger.warning("[PeriodClose] Failed to save config: %s", exc)

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return {
            "close_day_offset": DEFAULT_CLOSE_DAY_OFFSET,
            "locked_periods": [],
            "auto_lock": False,
        }

    # ------------------------------------------------------------------
    # Period detection
    # ------------------------------------------------------------------

    def get_current_period(self) -> Dict[str, Any]:
        """Return the current accounting period and its close status."""
        today = date.today()
        config = self.get_config()
        close_day = config.get("close_day_offset", DEFAULT_CLOSE_DAY_OFFSET)
        locked = config.get("locked_periods") or []

        # Current period = this month (or last month if before close day)
        if today.day <= close_day:
            # Still in the close window for the previous month
            prev = today.replace(day=1) - timedelta(days=1)
            period_month = prev.strftime("%Y-%m")
            period_closes_on = today.replace(day=close_day).isoformat()
            closing_window = True
        else:
            period_month = today.strftime("%Y-%m")
            if today.month == 12:
                next_month = today.replace(year=today.year + 1, month=1, day=close_day)
            else:
                next_month = today.replace(month=today.month + 1, day=close_day)
            period_closes_on = next_month.isoformat()
            closing_window = False

        is_locked = period_month in locked

        return {
            "period": period_month,
            "closes_on": period_closes_on,
            "is_locked": is_locked,
            "in_closing_window": closing_window,
            "close_day_offset": close_day,
            "days_until_close": (date.fromisoformat(period_closes_on) - today).days,
        }

    def is_period_locked(self, period: str) -> bool:
        """Check if a period (YYYY-MM) is locked."""
        config = self.get_config()
        locked = config.get("locked_periods") or []
        return period in locked

    def lock_period(self, period: str) -> bool:
        """Lock a period — prevents posting to that month."""
        config = self.get_config()
        locked = config.get("locked_periods") or []
        if period not in locked:
            locked.append(period)
            locked.sort()
            config["locked_periods"] = locked
            self.save_config(config)
            logger.info("[PeriodClose] Locked period %s for org=%s", period, self.organization_id)
            return True
        return False

    def unlock_period(self, period: str) -> bool:
        """Unlock a period."""
        config = self.get_config()
        locked = config.get("locked_periods") or []
        if period in locked:
            locked.remove(period)
            config["locked_periods"] = locked
            self.save_config(config)
            logger.info("[PeriodClose] Unlocked period %s for org=%s", period, self.organization_id)
            return True
        return False

    # ------------------------------------------------------------------
    # Backdate detection
    # ------------------------------------------------------------------

    def detect_backdated_invoices(self, period: str) -> List[Dict[str, Any]]:
        """Find invoices received after the period cutoff that belong to the prior period.

        An invoice is backdated if:
        - invoice_date falls within the period (YYYY-MM)
        - created_at (when Clearledgr received it) is after the cutoff
        """
        config = self.get_config()
        close_day = config.get("close_day_offset", DEFAULT_CLOSE_DAY_OFFSET)

        # Period = "2026-03" → cutoff = 2026-04-05
        try:
            year, month = int(period[:4]), int(period[5:7])
            if month == 12:
                cutoff = date(year + 1, 1, close_day)
            else:
                cutoff = date(year, month + 1, close_day)
            period_start = f"{period}-01"
            period_end_month = month + 1 if month < 12 else 1
            period_end_year = year if month < 12 else year + 1
            period_end = f"{period_end_year:04d}-{period_end_month:02d}-01"
        except (ValueError, IndexError):
            return []

        sql = (
            "SELECT id, vendor_name, amount, currency, invoice_number, invoice_date, created_at, state "
            "FROM ap_items WHERE organization_id = %s "
            "AND invoice_date >= %s AND invoice_date < %s "
            "AND created_at >= %s "
            "ORDER BY created_at DESC"
        )

        try:
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id, period_start, period_end, cutoff.isoformat()))
                return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("[PeriodClose] detect_backdated_invoices failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Accrual report
    # ------------------------------------------------------------------

    def generate_accrual_report(self, period: str) -> Dict[str, Any]:
        """Generate an accrual report for a period.

        Accrual candidates are AP items that are:
        - Created in the period (or have invoice_date in the period)
        - In states: approved, ready_to_post, posted_to_erp (not yet paid/closed)
        - Represent uninvoiced liabilities
        """
        try:
            year, month = int(period[:4]), int(period[5:7])
            period_start = f"{period}-01"
            period_end_month = month + 1 if month < 12 else 1
            period_end_year = year if month < 12 else year + 1
            period_end = f"{period_end_year:04d}-{period_end_month:02d}-01"
        except (ValueError, IndexError):
            return {"period": period, "accruals": [], "total_by_currency": {}}

        accrual_states = ("approved", "ready_to_post", "posted_to_erp")
        placeholders = ", ".join("%s" for _ in accrual_states)
        sql = (
            f"SELECT id, vendor_name, amount, currency, invoice_number, "
            f"invoice_date, state "
            f"FROM ap_items WHERE organization_id = %s "
            f"AND created_at >= %s AND created_at < %s "
            f"AND state IN ({placeholders}) "
            f"ORDER BY vendor_name, amount DESC"
        )
        params = [self.organization_id, period_start, period_end] + list(accrual_states)

        try:
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                items = [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("[PeriodClose] generate_accrual_report failed: %s", exc)
            items = []

        # Aggregate by currency
        total_by_currency: Dict[str, float] = {}
        by_vendor: Dict[str, Dict[str, float]] = {}

        for item in items:
            amount = float(item.get("amount") or 0)
            currency = item.get("currency") or "USD"
            vendor = item.get("vendor_name") or "Unknown"

            total_by_currency[currency] = total_by_currency.get(currency, 0) + amount
            if vendor not in by_vendor:
                by_vendor[vendor] = {}
            by_vendor[vendor][currency] = by_vendor[vendor].get(currency, 0) + amount

        # Round
        total_by_currency = {k: round(v, 2) for k, v in total_by_currency.items()}
        vendor_breakdown = [
            {"vendor_name": v, "totals_by_currency": {k: round(amt, 2) for k, amt in currencies.items()}}
            for v, currencies in sorted(by_vendor.items())
        ]

        return {
            "organization_id": self.organization_id,
            "period": period,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "accrual_count": len(items),
            "total_by_currency": total_by_currency,
            "vendor_breakdown": vendor_breakdown,
            "items": items[:200],  # Cap response
        }

    # ------------------------------------------------------------------
    # Period lock enforcement
    # ------------------------------------------------------------------

    def check_posting_allowed(self, invoice_date: Optional[str]) -> Dict[str, Any]:
        """Check if posting is allowed for an invoice with this date.

        Returns {"allowed": True/False, "reason": ...}
        """
        if not invoice_date:
            return {"allowed": True, "reason": "no_invoice_date"}

        try:
            inv_date = date.fromisoformat(str(invoice_date)[:10])
            period = inv_date.strftime("%Y-%m")
        except (ValueError, TypeError):
            return {"allowed": True, "reason": "unparseable_date"}

        if self.is_period_locked(period):
            return {
                "allowed": False,
                "reason": "period_locked",
                "period": period,
                "message": f"Period {period} is locked. Cannot post invoices dated in this period.",
            }

        return {"allowed": True, "period": period}


def get_period_close_service(organization_id: str = "default") -> PeriodCloseService:
    return PeriodCloseService(organization_id=organization_id)
