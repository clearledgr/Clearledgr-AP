"""Scheduled report delivery — push reports to Slack/email on a schedule.

Finance controllers need AP aging on Monday 8am, spend analysis on the 1st,
and posting status before month-end close. This service generates reports
and delivers them on a configurable schedule.

Runs from the background loop (agent_background.py).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Default schedules (can be overridden per org via settings_json)
DEFAULT_SCHEDULES = [
    {
        "id": "weekly_aging",
        "report_type": "ap_aging",
        "frequency": "weekly",
        "day_of_week": 0,  # Monday
        "hour_utc": 7,     # 8am CET / 8am WAT
        "channel": "slack",
        "enabled": True,
        "description": "Weekly AP aging report",
    },
    {
        "id": "monthly_spend",
        "report_type": "vendor_spend",
        "frequency": "monthly",
        "day_of_month": 1,
        "hour_utc": 7,
        "channel": "slack",
        "enabled": True,
        "description": "Monthly spend analysis",
    },
]


class ScheduledReportService:
    """Generate and deliver scheduled reports."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    def get_schedules(self) -> List[Dict[str, Any]]:
        """Get configured report schedules for this org."""
        try:
            org = self.db.get_organization(self.organization_id) or {}
            settings = org.get("settings_json") or org.get("settings") or {}
            if isinstance(settings, str):
                settings = json.loads(settings)
            return settings.get("report_schedules") or DEFAULT_SCHEDULES
        except Exception:
            return DEFAULT_SCHEDULES

    def save_schedules(self, schedules: List[Dict[str, Any]]) -> None:
        """Save report schedules to org settings."""
        try:
            org = self.db.get_organization(self.organization_id) or {}
            settings = org.get("settings_json") or org.get("settings") or {}
            if isinstance(settings, str):
                settings = json.loads(settings)
            if not isinstance(settings, dict):
                settings = {}
            settings["report_schedules"] = schedules
            self.db.update_organization(self.organization_id, settings_json=settings)
        except Exception as exc:
            logger.warning("[ScheduledReports] Failed to save schedules: %s", exc)

    async def run_due_reports(self) -> int:
        """Check all schedules and deliver reports that are due. Returns count delivered."""
        now = datetime.now(timezone.utc)
        schedules = self.get_schedules()
        delivered = 0

        for schedule in schedules:
            if not schedule.get("enabled", True):
                continue

            if not self._is_due(schedule, now):
                continue

            try:
                await self._deliver_report(schedule)
                delivered += 1
                self._mark_delivered(schedule["id"], now)
            except Exception as exc:
                logger.warning(
                    "[ScheduledReports] Failed to deliver %s: %s",
                    schedule.get("id"), exc,
                )

        return delivered

    def _is_due(self, schedule: Dict[str, Any], now: datetime) -> bool:
        """Check if a scheduled report is due to run."""
        hour = schedule.get("hour_utc", 7)
        if now.hour != hour:
            return False

        # Check if already delivered this period
        last_delivered = self._get_last_delivered(schedule["id"])
        if last_delivered:
            try:
                last_dt = datetime.fromisoformat(last_delivered.replace("Z", "+00:00"))
                # Don't deliver twice in the same hour
                if (now - last_dt).total_seconds() < 3600:
                    return False
            except (ValueError, TypeError):
                pass

        freq = schedule.get("frequency", "weekly")
        if freq == "weekly":
            return now.weekday() == schedule.get("day_of_week", 0)
        elif freq == "monthly":
            return now.day == schedule.get("day_of_month", 1)
        elif freq == "daily":
            return True

        return False

    async def _deliver_report(self, schedule: Dict[str, Any]) -> None:
        """Generate and deliver a single report."""
        from clearledgr.services.report_export import generate_report, rows_to_csv

        report_type = schedule.get("report_type", "ap_aging")
        rows, columns = generate_report(
            report_type=report_type,
            organization_id=self.organization_id,
            period_days=schedule.get("period_days", 30),
        )

        if not rows:
            logger.debug("[ScheduledReports] No data for %s, skipping", report_type)
            return

        channel = schedule.get("channel", "slack")
        description = schedule.get("description", report_type)

        if channel == "slack":
            await self._deliver_to_slack(report_type, description, rows, columns)
        elif channel == "sheets":
            await self._deliver_to_sheets(report_type, schedule, rows, columns)
        else:
            logger.warning("[ScheduledReports] Unknown channel: %s", channel)

    async def _deliver_to_slack(
        self,
        report_type: str,
        description: str,
        rows: List[Dict[str, Any]],
        columns: List[str],
    ) -> None:
        """Send report summary to Slack."""
        # Build a concise summary for Slack
        summary_lines = [f"*{description}*", f"_{len(rows)} items_", ""]

        if report_type == "ap_aging":
            # Summarize by bucket
            buckets: Dict[str, float] = {}
            for row in rows:
                for col in ["current", "1_30", "31_60", "61_90", "90_plus"]:
                    val = float(row.get(col) or 0)
                    if val > 0:
                        buckets[col] = buckets.get(col, 0) + val
            for bucket, total in buckets.items():
                label = bucket.replace("_", "-") if bucket != "current" else "Current"
                summary_lines.append(f"  {label}: ${total:,.2f}")

        elif report_type == "vendor_spend":
            vendor_rows = [r for r in rows if r.get("section") == "vendor"]
            for v in vendor_rows[:5]:
                summary_lines.append(f"  {v.get('vendor_name', '?')}: ${float(v.get('total_spend', 0)):,.2f}")

        elif report_type == "posting_status":
            states: Dict[str, int] = {}
            for row in rows:
                state = row.get("state", "?")
                states[state] = states.get(state, 0) + 1
            for state, count in sorted(states.items(), key=lambda x: -x[1]):
                summary_lines.append(f"  {state}: {count}")

        text = "\n".join(summary_lines)

        try:
            from clearledgr.services.agent_background import _slack_alert
            await _slack_alert(text, organization_id=self.organization_id)
            logger.info("[ScheduledReports] Delivered %s to Slack for org=%s", report_type, self.organization_id)
        except Exception as exc:
            logger.warning("[ScheduledReports] Slack delivery failed: %s", exc)

    async def _deliver_to_sheets(
        self,
        report_type: str,
        schedule: Dict[str, Any],
        rows: List[Dict[str, Any]],
        columns: List[str],
    ) -> None:
        """Push report to Google Sheets."""
        spreadsheet_url = schedule.get("spreadsheet_url", "")
        if not spreadsheet_url:
            logger.warning("[ScheduledReports] No spreadsheet_url for Sheets delivery")
            return

        try:
            from clearledgr.services.sheets_export import export_report_to_sheets
            from clearledgr.services.gmail_api import token_store

            tokens = token_store.list_all()
            if not tokens:
                logger.warning("[ScheduledReports] No Gmail tokens for Sheets delivery")
                return

            from clearledgr.services.sheets_api import SheetsAPIClient
            spreadsheet_id = SheetsAPIClient.extract_spreadsheet_id(spreadsheet_url)
            if not spreadsheet_id:
                return

            await export_report_to_sheets(
                user_id=tokens[0].user_id,
                spreadsheet_id=spreadsheet_id,
                report_type=report_type,
                organization_id=self.organization_id,
            )
            logger.info("[ScheduledReports] Delivered %s to Sheets for org=%s", report_type, self.organization_id)
        except Exception as exc:
            logger.warning("[ScheduledReports] Sheets delivery failed: %s", exc)

    def _get_last_delivered(self, schedule_id: str) -> Optional[str]:
        """Get the last delivery timestamp for a schedule."""
        try:
            org = self.db.get_organization(self.organization_id) or {}
            settings = org.get("settings_json") or org.get("settings") or {}
            if isinstance(settings, str):
                settings = json.loads(settings)
            deliveries = settings.get("report_deliveries") or {}
            return deliveries.get(schedule_id)
        except Exception as exc:
            logger.debug("[ScheduledReports] _get_last_delivered failed: %s", exc)
            return None

    def _mark_delivered(self, schedule_id: str, now: datetime) -> None:
        """Mark a schedule as delivered. Logs on failure to prevent infinite re-delivery."""
        try:
            org = self.db.get_organization(self.organization_id) or {}
            settings = org.get("settings_json") or org.get("settings") or {}
            if isinstance(settings, str):
                settings = json.loads(settings)
            if not isinstance(settings, dict):
                settings = {}
            deliveries = settings.get("report_deliveries") or {}
            deliveries[schedule_id] = now.isoformat()
            settings["report_deliveries"] = deliveries
            self.db.update_organization(self.organization_id, settings_json=settings)
        except Exception as exc:
            logger.error(
                "[ScheduledReports] Failed to mark %s as delivered — report may re-send next hour: %s",
                schedule_id, exc,
            )


def get_scheduled_report_service(organization_id: str = "default") -> ScheduledReportService:
    return ScheduledReportService(organization_id=organization_id)
