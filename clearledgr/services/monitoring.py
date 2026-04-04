"""Monitoring and alerting service — threshold-based health checks.

Runs periodic checks and raises alerts when thresholds are breached.
Alerts are delivered via:
1. Slack (existing _slack_alert infrastructure)
2. Outgoing webhooks (event: monitor.alert)
3. Sentry (breadcrumbs on critical events)

Alert channels are configurable via MONITOR_ALERT_CHANNELS env var
(comma-separated: "slack,webhook,log").  Default: "slack,log".
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default thresholds (overridable via env vars)
_DEFAULTS = {
    "dead_letter_max": 5,           # max dead-letter items before alert
    "posting_failure_rate_pct": 20, # % of posted items that failed
    "auth_failure_max": 3,          # max auth failures across all users
    "stale_poll_hours": 2,          # hours since last autopilot poll
    "overdue_invoices_max": 20,     # max overdue invoices before alert
    "erp_error_rate_pct": 30,       # % of ERP calls that failed recently
}


def _threshold(name: str) -> float:
    env_key = f"MONITOR_THRESHOLD_{name.upper()}"
    raw = os.getenv(env_key, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULTS.get(name, 0)


def _alert_channels() -> List[str]:
    raw = os.getenv("MONITOR_ALERT_CHANNELS", "slack,log").strip()
    return [c.strip() for c in raw.split(",") if c.strip()]


class MonitoringService:
    """Runs health checks and emits alerts on threshold breaches."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    def run_all_checks(self) -> Dict[str, Any]:
        """Run all monitoring checks.  Returns a summary dict."""
        checks = [
            self._check_dead_letters(),
            self._check_auth_failures(),
            self._check_stale_autopilot(),
            self._check_overdue_invoices(),
            self._check_posting_failures(),
        ]
        alerts = [c for c in checks if c.get("alert")]
        return {
            "organization_id": self.organization_id,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "check_count": len(checks),
            "alert_count": len(alerts),
            "checks": checks,
            "alerts": alerts,
            "healthy": len(alerts) == 0,
        }

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_dead_letters(self) -> Dict[str, Any]:
        """Check for dead-lettered notifications (exhausted retries)."""
        try:
            sql = self.db._prepare_sql(
                "SELECT COUNT(*) FROM pending_notifications "
                "WHERE organization_id = ? AND status = 'dead_letter'"
            )
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id,))
                row = cur.fetchone()
            count = int(row[0]) if row else 0
        except Exception:
            count = 0

        threshold = int(_threshold("dead_letter_max"))
        return {
            "check": "dead_letters",
            "value": count,
            "threshold": threshold,
            "alert": count > threshold,
            "severity": "critical" if count > threshold * 2 else "warning",
            "message": f"{count} dead-lettered notifications (threshold: {threshold})",
        }

    def _check_auth_failures(self) -> Dict[str, Any]:
        """Check for recent OAuth auth failures in autopilot state."""
        try:
            gmail_states = self.db.list_gmail_autopilot_states()
            outlook_states = self.db.list_outlook_autopilot_states()
            all_states = gmail_states + outlook_states
            failures = [
                s for s in all_states
                if s.get("last_error") and "auth" in str(s.get("last_error", "")).lower()
            ]
            count = len(failures)
        except Exception:
            count = 0

        threshold = int(_threshold("auth_failure_max"))
        return {
            "check": "auth_failures",
            "value": count,
            "threshold": threshold,
            "alert": count > threshold,
            "severity": "critical",
            "message": f"{count} auth failures across email providers (threshold: {threshold})",
        }

    def _check_stale_autopilot(self) -> Dict[str, Any]:
        """Check if autopilot hasn't polled recently."""
        try:
            gmail_states = self.db.list_gmail_autopilot_states()
            outlook_states = self.db.list_outlook_autopilot_states()
            all_states = gmail_states + outlook_states

            if not all_states:
                return {
                    "check": "stale_autopilot",
                    "value": 0,
                    "threshold": 0,
                    "alert": False,
                    "severity": "info",
                    "message": "No autopilot users configured",
                }

            now = datetime.now(timezone.utc)
            stale_hours = _threshold("stale_poll_hours")
            stale_cutoff = now - timedelta(hours=stale_hours)
            stale_count = 0

            for s in all_states:
                last_scan = s.get("last_scan_at")
                if not last_scan:
                    stale_count += 1
                    continue
                try:
                    scan_dt = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))
                    if scan_dt < stale_cutoff:
                        stale_count += 1
                except (ValueError, TypeError):
                    stale_count += 1
        except Exception:
            stale_count = 0

        return {
            "check": "stale_autopilot",
            "value": stale_count,
            "threshold": 1,
            "alert": stale_count > 0,
            "severity": "warning",
            "message": f"{stale_count} user(s) with stale autopilot (>{stale_hours}h since last poll)",
        }

    def _check_overdue_invoices(self) -> Dict[str, Any]:
        """Check for excessive overdue AP items."""
        try:
            from clearledgr.services.ap_aging_report import APAgingReport
            report = APAgingReport(self.organization_id)
            data = report.generate()
            overdue_count = data.get("summary", {}).get("overdue_count", 0)
        except Exception:
            overdue_count = 0

        threshold = int(_threshold("overdue_invoices_max"))
        return {
            "check": "overdue_invoices",
            "value": overdue_count,
            "threshold": threshold,
            "alert": overdue_count > threshold,
            "severity": "warning",
            "message": f"{overdue_count} overdue invoices (threshold: {threshold})",
        }

    def _check_posting_failures(self) -> Dict[str, Any]:
        """Check for recent ERP posting failures."""
        try:
            sql = self.db._prepare_sql(
                "SELECT state, COUNT(*) as cnt FROM ap_items "
                "WHERE organization_id = ? "
                "AND updated_at >= ? "
                "AND state IN ('posted_to_erp', 'failed_post') "
                "GROUP BY state"
            )
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id, cutoff))
                rows = cur.fetchall()

            counts = {row[0]: int(row[1]) for row in rows}
            posted = counts.get("posted_to_erp", 0)
            failed = counts.get("failed_post", 0)
            total = posted + failed
            failure_rate = (failed / total * 100) if total > 0 else 0.0
        except Exception:
            failure_rate = 0.0
            failed = 0

        threshold = _threshold("posting_failure_rate_pct")
        return {
            "check": "posting_failures",
            "value": round(failure_rate, 1),
            "threshold": threshold,
            "alert": failure_rate > threshold and failed > 0,
            "severity": "critical" if failure_rate > 50 else "warning",
            "message": f"{failure_rate:.0f}% posting failure rate in last 24h ({failed} failed, threshold: {threshold}%)",
        }


async def run_monitoring_checks(organization_id: str = "default") -> Dict[str, Any]:
    """Run all monitoring checks and emit alerts for breaches."""
    service = MonitoringService(organization_id)
    result = service.run_all_checks()

    if not result["alerts"]:
        return result

    channels = _alert_channels()

    for alert in result["alerts"]:
        msg = f":rotating_light: *Monitor Alert* [{alert['severity'].upper()}]\n{alert['message']}"

        if "log" in channels:
            log_fn = logger.critical if alert["severity"] == "critical" else logger.warning
            log_fn("[Monitor] %s: %s", alert["check"], alert["message"])

        if "slack" in channels:
            try:
                from clearledgr.services.agent_background import _slack_alert
                await _slack_alert(msg, organization_id=organization_id)
            except Exception:
                pass

        if "webhook" in channels:
            try:
                from clearledgr.services.webhook_delivery import emit_webhook_event
                await emit_webhook_event(
                    organization_id=organization_id,
                    event_type="monitor.alert",
                    payload=alert,
                )
            except Exception:
                pass

        # Sentry breadcrumb
        try:
            import sentry_sdk
            sentry_sdk.add_breadcrumb(
                category="monitor",
                message=alert["message"],
                level=alert["severity"],
            )
            if alert["severity"] == "critical":
                sentry_sdk.capture_message(
                    f"[Monitor] {alert['check']}: {alert['message']}",
                    level="error",
                )
        except Exception:
            pass

    return result
