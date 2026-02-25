"""
Agent Background Intelligence Loop

Runs periodic tasks that make Clearledgr proactive:
- Overdue/stale AP item nudges (every 15 min) → Slack alert
- Volume/pattern anomaly detection (every hour) → Slack alert
- Period-end alerts (daily) → Slack alert
- Spending digest (daily) → Slack digest

Started on FastAPI app startup alongside GmailAutopilot.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_ORG_ID = os.getenv("DEFAULT_ORGANIZATION_ID", "default")
SLACK_CHANNEL = (
    os.getenv("SLACK_APPROVAL_CHANNEL")
    or os.getenv("SLACK_DEFAULT_CHANNEL")
    or "#finance"
)

# Background task handle for cleanup
_background_task = None


async def _slack_alert(text: str, blocks=None):
    """Send an alert to the configured Slack channel."""
    try:
        from ui.slack.app import send_message
        await send_message(SLACK_CHANNEL, text, blocks=blocks, organization_id=DEFAULT_ORG_ID)
    except Exception as e:
        logger.debug(f"Slack alert failed: {e}")


async def start_agent_background(app=None):
    """Start the background intelligence loop."""
    global _background_task
    if _background_task is not None:
        logger.warning("Agent background already running")
        return

    _background_task = asyncio.create_task(_run_loop())
    logger.info("Agent background intelligence loop started")


async def stop_agent_background():
    """Stop the background loop."""
    global _background_task
    if _background_task:
        _background_task.cancel()
        _background_task = None
        logger.info("Agent background intelligence loop stopped")


async def _run_loop():
    """Main background loop."""
    # Stagger startup to avoid thundering herd
    await asyncio.sleep(30)

    tick = 0
    while True:
        try:
            tick += 1

            # Every 15 minutes: check overdue and stale tasks
            if tick % 1 == 0:  # runs every iteration (15 min sleep)
                await _check_overdue_tasks()

            # Every hour (4 ticks)
            if tick % 4 == 0:
                await _check_anomalies()

            # Daily (96 ticks at 15-min intervals, but we check by hour)
            now = datetime.now(timezone.utc)
            if tick % 4 == 0 and now.hour == 8:
                await _send_daily_digest()
            if tick % 4 == 0 and now.hour == 7:
                await _check_period_end()

        except asyncio.CancelledError:
            logger.info("Agent background loop cancelled")
            return
        except Exception as e:
            logger.error(f"Agent background loop error: {e}")

        # Sleep 15 minutes
        await asyncio.sleep(900)


async def _check_overdue_tasks():
    """Check for overdue and stale AP items, send nudges to Slack."""
    try:
        from clearledgr.services.task_scheduler import TaskScheduler

        scheduler = TaskScheduler(organization_id=DEFAULT_ORG_ID)
        results = scheduler.run_all_checks()

        overdue = results.get("overdue", [])
        stale = results.get("stale", [])

        if overdue or stale:
            logger.info(
                f"Background check: {len(overdue)} overdue, {len(stale)} stale tasks"
            )
            lines = [":clock3: *AP Status Check*"]
            if overdue:
                lines.append(f"\n*{len(overdue)} overdue item(s):*")
                for item in overdue[:5]:
                    vendor = item.get("vendor_name", "Unknown")
                    amount = item.get("amount", 0)
                    due = item.get("due_date", "?")
                    lines.append(f"  • {vendor} — ${amount:,.2f} (due {due})")
            if stale:
                lines.append(f"\n*{len(stale)} stale item(s) needing attention:*")
                for item in stale[:5]:
                    vendor = item.get("vendor_name", "Unknown")
                    state = item.get("state", "?")
                    lines.append(f"  • {vendor} — stuck in `{state}`")
            await _slack_alert("\n".join(lines))
    except Exception as e:
        logger.debug(f"Overdue task check failed: {e}")


async def _check_anomalies():
    """Detect volume and pattern anomalies, alert Slack."""
    try:
        from clearledgr.services.agent_anomaly_detection import AnomalyDetectionService

        service = AnomalyDetectionService(organization_id=DEFAULT_ORG_ID)
        anomalies = service.detect_all()

        if anomalies:
            logger.info(f"Detected {len(anomalies)} anomalies")
            lines = [":warning: *Anomaly Detection*"]
            for a in anomalies[:5]:
                atype = a.get("type", "?")
                desc = a.get("description", "")
                lines.append(f"  • *{atype}:* {desc}")
            await _slack_alert("\n".join(lines))
    except Exception as e:
        logger.debug(f"Anomaly detection failed: {e}")


async def _send_daily_digest():
    """Generate and send daily spending digest to Slack."""
    try:
        from clearledgr.services.proactive_insights import get_proactive_insights

        insights_service = get_proactive_insights(DEFAULT_ORG_ID)
        digest = insights_service.generate_daily_digest()

        if digest:
            logger.info(f"Daily digest: {len(digest)} insights generated")
            lines = [":bar_chart: *Daily AP Digest*"]
            for insight in digest[:8]:
                title = insight.get("title", "") if isinstance(insight, dict) else str(insight)
                lines.append(f"  • {title}")
            await _slack_alert("\n".join(lines))
    except Exception as e:
        logger.debug(f"Daily digest generation failed: {e}")


async def _check_period_end():
    """Detect period-end and alert about closing deadlines in Slack."""
    try:
        from clearledgr.services.agent_monitoring import AgentMonitoringService

        monitoring = AgentMonitoringService(organization_id=DEFAULT_ORG_ID)
        period_info = monitoring.detect_period_end()

        if period_info and period_info.get("is_period_end"):
            period_type = period_info.get("period_type", "month")
            days_left = period_info.get("days_remaining", "?")
            logger.info(f"Period-end detected: {period_type}")
            await _slack_alert(
                f":calendar: *Period-End Alert*\n"
                f"{period_type.title()}-end closing in *{days_left} day(s)*. "
                f"Review pending AP items before the cutoff."
            )
    except Exception as e:
        logger.debug(f"Period-end detection failed: {e}")
