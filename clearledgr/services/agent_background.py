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
from datetime import datetime, timezone, timedelta

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
        logger.error("Slack alert failed: %s", e)


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

            # Every tick: drain ERP-post retry queue (Gap #5 crash recovery)
            await _drain_erp_post_retry_queue()

            # Every 15 minutes: check overdue and stale tasks + approval timeouts
            if tick % 1 == 0:  # runs every iteration (15 min sleep)
                await _check_overdue_tasks()
                await _check_approval_timeouts(DEFAULT_ORG_ID)

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
            # Rich KPI dashboard (replaces the plain-text alert)
            try:
                from clearledgr.services.slack_notifications import send_overdue_summary
                await send_overdue_summary(
                    overdue_items=overdue,
                    stale_items=stale,
                    organization_id=DEFAULT_ORG_ID,
                )
            except Exception as _kpi_err:
                # Fall back to plain-text alert if KPI dashboard fails
                logger.error("KPI dashboard failed, falling back to plain alert: %s", _kpi_err)
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
        logger.error("Overdue task check failed: %s", e)


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
        logger.error("Anomaly detection failed: %s", e)


async def _send_daily_digest():
    """Generate and send daily spending digest to Slack."""
    try:
        from clearledgr.services.proactive_insights import get_proactive_insights

        insights_service = get_proactive_insights(DEFAULT_ORG_ID)
        digest = insights_service.generate_daily_digest()

        if digest and digest.insights:
            logger.info(f"Daily digest: {len(digest.insights)} insights generated — {digest.summary}")
            lines = [f":bar_chart: *Daily AP Digest* — {digest.summary}"]
            for insight in digest.insights[:8]:
                lines.append(f"  • {insight.title}")
            await _slack_alert("\n".join(lines))
    except Exception as e:
        logger.error("Daily digest generation failed: %s", e)


def _retry_backoff_seconds(attempt_number: int) -> int:
    """Backoff schedule for durable ERP retry jobs."""
    schedule = [300, 900, 1800, 3600]
    safe_attempt = max(1, int(attempt_number or 1))
    idx = min(len(schedule) - 1, safe_attempt - 1)
    return schedule[idx]


async def _drain_erp_post_retry_queue():
    """Sweep durable retry jobs via the canonical AP workflow runtime."""
    try:
        from clearledgr.core.database import get_db
        from clearledgr.services.invoice_workflow import get_invoice_workflow

        db = get_db()
        if not hasattr(db, "list_due_agent_retry_jobs"):
            return

        summary = {
            "claimed": 0,
            "completed": 0,
            "rescheduled": 0,
            "dead_letter": 0,
        }
        workflow = get_invoice_workflow(DEFAULT_ORG_ID)
        due_jobs = db.list_due_agent_retry_jobs(
            organization_id=DEFAULT_ORG_ID,
            limit=25,
        )

        for job in due_jobs:
            job_id = str(job.get("id") or "").strip()
            if not job_id:
                continue
            claimed = db.claim_agent_retry_job(
                job_id,
                worker_id=f"agent_background:{DEFAULT_ORG_ID}",
            )
            if not claimed:
                continue
            summary["claimed"] += 1

            job_type = str(claimed.get("job_type") or "").strip().lower()
            if job_type == "erp_post_retry":
                ap_item_id = str(claimed.get("ap_item_id") or "").strip()
                if not ap_item_id:
                    db.complete_agent_retry_job(
                        job_id,
                        status="dead_letter",
                        last_error="missing_ap_item_id",
                        result={"error": "missing_ap_item_id"},
                    )
                    summary["dead_letter"] += 1
                    continue

                outcome = await workflow.resume_workflow(ap_item_id)
                outcome_status = str(outcome.get("status") or "").strip().lower()

                if outcome_status == "recovered":
                    db.complete_agent_retry_job(
                        job_id,
                        status="completed",
                        result=outcome,
                        last_error=None,
                    )
                    summary["completed"] += 1
                    continue

                retry_count = max(1, int(claimed.get("retry_count") or 1))
                max_retries = max(1, int(claimed.get("max_retries") or 3))
                if outcome_status == "still_failing" and retry_count < max_retries:
                    next_retry_at = (
                        datetime.now(timezone.utc)
                        + timedelta(seconds=_retry_backoff_seconds(retry_count))
                    ).isoformat()
                    db.reschedule_agent_retry_job(
                        job_id,
                        next_retry_at=next_retry_at,
                        last_error=str(outcome.get("reason") or "still_failing"),
                        result=outcome,
                        status="pending",
                    )
                    summary["rescheduled"] += 1
                    continue

                db.complete_agent_retry_job(
                    job_id,
                    status="dead_letter",
                    last_error=str(
                        outcome.get("reason")
                        or outcome.get("error")
                        or outcome_status
                        or "retry_unrecoverable"
                    ),
                    result=outcome,
                )
                summary["dead_letter"] += 1
                continue

            # Legacy post-process jobs are no longer part of the canonical AP runtime.
            if job_type == "post_process":
                db.complete_agent_retry_job(
                    job_id,
                    status="dead_letter",
                    last_error="post_process_runtime_removed",
                    result={"error": "post_process_runtime_removed"},
                )
                summary["dead_letter"] += 1
                continue

            db.complete_agent_retry_job(
                job_id,
                status="dead_letter",
                last_error=f"unsupported_retry_job_type:{job_type or 'unknown'}",
                result={"error": "unsupported_retry_job_type", "job_type": job_type},
            )
            summary["dead_letter"] += 1

        if summary["claimed"] > 0:
            logger.info(
                "Durable queue drain: claimed=%s completed=%s rescheduled=%s dead_letter=%s",
                summary["claimed"],
                summary["completed"],
                summary["rescheduled"],
                summary["dead_letter"],
            )
    except Exception as exc:
        logger.error("Durable queue drain failed: %s", exc)


async def _check_approval_timeouts(org_id: str):
    """Send reminders / escalations for AP items stuck in needs_approval.

    Milestone  Hours  Action
    ---------  -----  ------
    reminder    4h    DM each pending approver once
    escalation 24h    DM + post to approval channel once

    Deduplication is DB-backed via the ap_item's metadata column
    (``approval_reminder_milestones`` dict). This survives process restarts,
    deploys, and scale-out — unlike the old module-level ``_reminded_set``.
    """
    try:
        import json as _json
        from clearledgr.core.database import get_db
        from clearledgr.services.slack_notifications import send_approval_reminder

        db = get_db()
        if not hasattr(db, "get_overdue_approvals"):
            return

        now_iso = datetime.now(timezone.utc).isoformat()

        # Check 4-hour milestone first, then 24-hour
        for min_hours, milestone in [(4.0, "4h"), (24.0, "24h")]:
            overdue = db.get_overdue_approvals(org_id, min_hours=min_hours)
            for item in overdue:
                ap_item_id = item.get("id")
                if not ap_item_id:
                    continue

                # --- DB-persisted deduplication (survives restarts) ---
                try:
                    meta = _json.loads(item.get("metadata") or "{}")
                except Exception:
                    meta = {}
                milestones_sent = meta.get("approval_reminder_milestones") or {}
                if milestone in milestones_sent:
                    continue  # already sent and recorded in DB

                approver_ids = db.get_pending_approver_ids(ap_item_id)
                reminder_sent = await send_approval_reminder(
                    ap_item=item,
                    approver_ids=approver_ids,
                    hours_pending=min_hours,
                    organization_id=org_id,
                )

                try:
                    db.append_ap_audit_event(
                        {
                            "ap_item_id": ap_item_id,
                            "event_type": "approval_nudge_sent" if reminder_sent else "approval_nudge_failed",
                            "actor_type": "system",
                            "actor_id": "agent_background",
                            "reason": f"approval_nudge_auto_{milestone}",
                            "metadata": {
                                "auto": True,
                                "milestone": milestone,
                                "hours_pending": min_hours,
                                "approver_count": len(approver_ids or []),
                            },
                            "organization_id": org_id,
                            "source": "agent_background",
                            "idempotency_key": f"approval_nudge_auto:{ap_item_id}:{milestone}",
                        }
                    )
                except Exception as audit_exc:
                    logger.error("Could not append auto-approval-nudge audit event: %s", audit_exc)

                # Build metadata patch — include escalation record for 24h
                patch: dict = {
                    "approval_reminder_milestones": {
                        **milestones_sent,
                        milestone: now_iso,
                    }
                }
                if milestone == "24h":
                    patch["escalated_at"] = now_iso
                    patch["escalation_reason"] = "approval_timeout_24h"
                    patch["escalation_vendor"] = item.get("vendor_name")
                    patch["escalation_amount"] = item.get("amount")

                if hasattr(db, "update_ap_item_metadata_merge"):
                    db.update_ap_item_metadata_merge(ap_item_id, patch)

                logger.info(
                    "Approval timeout %s milestone triggered for ap_item_id=%s",
                    milestone,
                    ap_item_id,
                )
    except Exception as exc:
        logger.error("Approval timeout check failed: %s", exc)


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
        logger.error("Period-end detection failed: %s", e)
