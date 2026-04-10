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
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_ORG_ID = os.getenv("DEFAULT_ORGANIZATION_ID", "default")
SLACK_CHANNEL = (
    os.getenv("SLACK_APPROVAL_CHANNEL")
    or os.getenv("SLACK_DEFAULT_CHANNEL")
    or "#finance"
)

# Background task handles for cleanup
_background_task = None
_override_window_reaper_task = None


def _active_org_ids() -> List[str]:
    """Return the orgs that should receive background automation."""
    try:
        from clearledgr.core.database import get_db

        db = get_db()
    except Exception:
        return [DEFAULT_ORG_ID]

    org_ids: List[str] = []
    if hasattr(db, "list_organizations_with_ap_items"):
        try:
            org_ids.extend(db.list_organizations_with_ap_items() or [])
        except Exception as exc:
            logger.debug("Org discovery method 1 failed: %s", exc)
    if hasattr(db, "list_organizations"):
        try:
            for row in db.list_organizations(limit=500) or []:
                if not isinstance(row, dict):
                    continue
                org_ids.append(row.get("id") or row.get("organization_id"))
        except Exception as exc:
            logger.debug("Org discovery method 2 failed: %s", exc)
    try:
        from clearledgr.services.email_tasks import get_tasks

        for task in get_tasks(include_completed=True, limit=1000) or []:
            if not isinstance(task, dict):
                continue
            org_ids.append(task.get("organization_id"))
    except Exception as exc:
        logger.debug("Org discovery method 3 failed: %s", exc)

    normalized: List[str] = []
    seen = set()
    for org_id in org_ids:
        token = str(org_id or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized or [DEFAULT_ORG_ID]


def _parse_task_datetime(raw: Any) -> Optional[datetime]:
    token = str(raw or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(token, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                parsed = datetime.strptime(token, "%Y-%m-%d")
            except ValueError:
                return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_task_for_ap_summary(task: Dict[str, Any]) -> Dict[str, Any]:
    due_at = _parse_task_datetime(task.get("due_date"))
    updated_at = _parse_task_datetime(task.get("updated_at") or task.get("created_at"))
    amount = task.get("related_amount")
    try:
        amount_value = float(amount or 0)
    except (TypeError, ValueError):
        amount_value = 0.0
    return {
        "task_id": task.get("task_id"),
        "organization_id": task.get("organization_id") or DEFAULT_ORG_ID,
        "vendor_name": task.get("related_vendor") or task.get("source_email_sender") or task.get("title") or "Unknown task",
        "amount": amount_value,
        "due_date": due_at.date().isoformat() if due_at else (task.get("due_date") or "?"),
        "state": task.get("status") or "open",
        "title": task.get("title"),
        "task_type": task.get("task_type"),
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def _collect_org_overdue_and_stale_tasks(
    organization_id: str,
    *,
    stale_days: int = 5,
) -> Dict[str, List[Dict[str, Any]]]:
    from clearledgr.services.email_tasks import get_overdue_tasks, get_tasks

    overdue_items = [
        _normalize_task_for_ap_summary(task)
        for task in (get_overdue_tasks(organization_id=organization_id) or [])
        if isinstance(task, dict)
    ]

    stale_items: List[Dict[str, Any]] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    for task in get_tasks(include_completed=False, organization_id=organization_id, limit=1000) or []:
        if not isinstance(task, dict):
            continue
        updated_at = _parse_task_datetime(task.get("updated_at") or task.get("created_at"))
        if updated_at and updated_at < cutoff:
            stale_items.append(_normalize_task_for_ap_summary(task))

    return {
        "overdue": overdue_items,
        "stale": stale_items,
    }


async def _slack_alert(text: str, blocks=None, organization_id: Optional[str] = None):
    """Send an alert to the configured Slack channel."""
    try:
        from ui.slack.app import send_message
        await send_message(
            SLACK_CHANNEL,
            text,
            blocks=blocks,
            organization_id=str(organization_id or DEFAULT_ORG_ID).strip() or DEFAULT_ORG_ID,
        )
    except Exception as e:
        logger.error("Slack alert failed: %s", e)


async def reap_expired_override_windows() -> int:
    """Process expired override windows: mark them expired, update Slack cards.

    Phase 1.4: This is the canonical reaper for the override-window
    mechanism (DESIGN_THESIS.md §8). It runs every 60 seconds via
    ``_override_window_reaper_loop`` so that windows are finalized
    promptly after their deadline — well before the user notices a
    stale undo card.

    Returns the number of windows that were reaped on this call. The
    function is idempotent — windows already in a terminal state are
    skipped, and a partial failure (e.g., Slack API hiccup) leaves
    the window in ``expired`` state with no card update; the caller
    can retry safely.
    """
    try:
        from clearledgr.core.database import get_db
        from clearledgr.services import slack_cards
        from clearledgr.services.override_window import (
            get_override_window_service,
        )
    except Exception as exc:
        logger.warning("[OverrideWindowReaper] Imports failed: %s", exc)
        return 0

    try:
        db = get_db()
    except Exception as exc:
        logger.warning("[OverrideWindowReaper] DB unavailable: %s", exc)
        return 0

    try:
        expired = db.list_expired_override_windows()
    except Exception as exc:
        logger.warning("[OverrideWindowReaper] list_expired query failed: %s", exc)
        return 0

    reaped = 0
    for window in expired or []:
        window_id = window.get("id")
        organization_id = window.get("organization_id")
        if not window_id or not organization_id:
            continue
        try:
            service = get_override_window_service(organization_id, db=db)
            success = service.expire_window(window_id)
        except Exception as exc:
            logger.warning(
                "[OverrideWindowReaper] expire_window failed for %s: %s",
                window_id, exc,
            )
            continue
        if not success:
            continue
        reaped += 1

        # Best-effort Slack card update — if this fails the window is
        # still marked expired in the DB, the user just sees a stale card.
        try:
            ap_item_id = window.get("ap_item_id")
            ap_item = db.get_ap_item(ap_item_id) if ap_item_id else {}
            await slack_cards.update_card_to_finalized(
                organization_id=organization_id,
                ap_item=ap_item or {},
                window=window,
            )
        except Exception as exc:
            logger.debug(
                "[OverrideWindowReaper] Slack card finalize failed for %s: %s",
                window_id, exc,
            )
    if reaped:
        logger.info("[OverrideWindowReaper] Reaped %d expired override windows", reaped)
    return reaped


_OVERRIDE_WINDOW_REAPER_INTERVAL_SECONDS = int(
    os.getenv("OVERRIDE_WINDOW_REAPER_INTERVAL_SECONDS", "60")
)


async def _override_window_reaper_loop() -> None:
    """Dedicated 60-second loop that finalizes expired override windows.

    Runs in parallel with the main 15-minute background loop so the
    reaper cadence can stay tight (override windows are short-lived).
    """
    # Stagger startup so we don't compete with the main loop's first tick
    await asyncio.sleep(5)
    while True:
        try:
            await reap_expired_override_windows()
        except asyncio.CancelledError:
            logger.info("Override window reaper loop cancelled")
            return
        except Exception as exc:
            logger.error("[OverrideWindowReaper] loop iteration failed: %s", exc)
        await asyncio.sleep(_OVERRIDE_WINDOW_REAPER_INTERVAL_SECONDS)


async def start_agent_background(app=None):
    """Start the background intelligence loop + override-window reaper."""
    global _background_task, _override_window_reaper_task
    if _background_task is not None:
        logger.warning("Agent background already running")
        return

    async def _run_loop_with_restart():
        while True:
            try:
                await _run_loop()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.critical("Background loop crashed, restarting in 30s: %s", exc)
                await asyncio.sleep(30)

    async def _reaper_loop_with_restart():
        while True:
            try:
                await _override_window_reaper_loop()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.critical(
                    "Override window reaper crashed, restarting in 30s: %s", exc
                )
                await asyncio.sleep(30)

    _background_task = asyncio.create_task(_run_loop_with_restart())
    _override_window_reaper_task = asyncio.create_task(_reaper_loop_with_restart())
    logger.info(
        "Agent background intelligence loop started "
        "(override window reaper: every %ds)",
        _OVERRIDE_WINDOW_REAPER_INTERVAL_SECONDS,
    )


async def stop_agent_background():
    """Stop the background loop + override-window reaper."""
    global _background_task, _override_window_reaper_task
    if _background_task:
        _background_task.cancel()
        _background_task = None
        logger.info("Agent background intelligence loop stopped")
    if _override_window_reaper_task:
        _override_window_reaper_task.cancel()
        _override_window_reaper_task = None
        logger.info("Override window reaper loop stopped")


async def _run_loop():
    """Main background loop."""
    # Stagger startup to avoid thundering herd
    await asyncio.sleep(30)

    tick = 0
    while True:
        try:
            tick += 1
            org_ids = _active_org_ids()

            # Every tick: drain ERP-post retry queue (Gap #5 crash recovery)
            await _drain_erp_post_retry_queue()

            # Drain notification retry queue (F1: ensures failed Slack/Teams
            # notifications are retried instead of silently dropped)
            try:
                from clearledgr.services.slack_notifications import process_retry_queue
                drained = await process_retry_queue()
                if drained:
                    logger.info("Drained %d notification retries", drained)
            except Exception as exc:
                logger.warning("Notification retry drain failed: %s", exc)

            # Every 2nd tick (~30 min): scan for vendor follow-up responses
            if tick % 2 == 0:
                for org_id in org_ids:
                    await _check_vendor_followup_responses(org_id)

            # Every 15 minutes: check overdue and stale tasks + approval timeouts
            if tick % 1 == 0:  # runs every iteration (15 min sleep)
                await _check_overdue_tasks()
                for org_id in org_ids:
                    await _check_approval_timeouts(org_id)

            # Every 3rd tick (~45 min): sweep exception auto-resolution
            if tick % 3 == 0:
                for org_id in org_ids:
                    await _sweep_exception_resolutions(org_id)

            # Every 6th tick (~90 min): run task scheduler checks
            if tick % 6 == 0:
                await _run_task_scheduler_checks()

            # Every 12th tick (~3 hours): verify recent ERP postings
            if tick % 12 == 0:
                for org_id in org_ids:
                    await _verify_recent_erp_postings(org_id)

            # Every hour (4 ticks): chase stale vendor onboarding sessions
            # Phase 3.1.e — scans all orgs in a single pass, dispatches
            # 24h/48h chase emails, escalates after 72h, abandons after 30d.
            if tick % 4 == 0:
                try:
                    from clearledgr.services.vendor_onboarding_lifecycle import (
                        chase_stale_sessions,
                    )
                    chase_result = await chase_stale_sessions()
                    if chase_result.chases_sent or chase_result.escalations or chase_result.abandonments:
                        logger.info(
                            "[background] vendor onboarding chase: scanned=%d chases=%d escalations=%d abandonments=%d",
                            chase_result.sessions_scanned,
                            chase_result.chases_sent,
                            chase_result.escalations,
                            chase_result.abandonments,
                        )
                except Exception as chase_exc:
                    logger.warning(
                        "[background] vendor onboarding chase failed: %s", chase_exc
                    )

            # Every hour (4 ticks)
            if tick % 4 == 0:
                for org_id in org_ids:
                    await _check_anomalies(org_id)
                # E5: Run ERP follow-on reconciliation check every 4th tick (~60 min)
                for org_id in org_ids:
                    await _run_erp_reconciliation(org_id)
                # Poll ERP for payment status changes every ~1 hour
                for org_id in org_ids:
                    await _poll_payment_statuses(org_id)
                # Run monitoring health checks every ~1 hour
                for org_id in org_ids:
                    await _run_monitoring_checks(org_id)

            # Daily (96 ticks at 15-min intervals, but we check by hour)
            now = datetime.now(timezone.utc)
            if tick % 4 == 0 and now.hour == 8:
                for org_id in org_ids:
                    await _send_daily_digest(org_id)
            if tick % 4 == 0 and now.hour == 7:
                for org_id in org_ids:
                    await _check_period_end(org_id)
            # Daily vendor master sync at 2am UTC (3am CET / 3am WAT)
            if tick % 4 == 0 and now.hour == 2:
                for org_id in org_ids:
                    await _sync_vendor_master_data(org_id)

            # Scheduled report delivery — check every hour
            if tick % 4 == 0:
                for org_id in org_ids:
                    await _deliver_scheduled_reports(org_id)

        except asyncio.CancelledError:
            logger.info("Agent background loop cancelled")
            return
        except Exception as e:
            logger.error(f"Agent background loop error: {e}")

        # Sleep 15 minutes
        await asyncio.sleep(900)


async def _check_overdue_tasks():
    """Check overdue and stale task queues per org, then post summaries to Slack."""
    try:
        try:
            from clearledgr.services.task_scheduler import log_reminder, should_send_reminder
        except Exception:
            def should_send_reminder(_task_id: str, _reminder_type: str, min_hours: int = 24) -> bool:
                return True

            def log_reminder(_task_id: str, _reminder_type: str, next_reminder: Optional[str] = None) -> None:
                return None

        total_overdue = 0
        total_stale = 0
        org_ids = _active_org_ids()
        loop = asyncio.get_event_loop()
        for org_id in org_ids:
            # E7: Run sync DB call in executor to avoid blocking the event loop
            task_status = await loop.run_in_executor(None, _collect_org_overdue_and_stale_tasks, org_id)
            org_overdue = task_status.get("overdue", [])
            org_stale = task_status.get("stale", [])
            total_overdue += len(org_overdue)
            total_stale += len(org_stale)
            if org_overdue or org_stale:
                summary_task_id = f"{org_id}:daily_summary"
                if not should_send_reminder(summary_task_id, "overdue_summary", 20):
                    continue
                try:
                    from clearledgr.services.slack_notifications import send_overdue_summary
                    await send_overdue_summary(
                        overdue_items=org_overdue,
                        stale_items=org_stale,
                        organization_id=org_id,
                    )
                except Exception as _kpi_err:
                    logger.error("KPI dashboard failed, falling back to plain alert: %s", _kpi_err)
                    lines = [":clock3: *AP Status Check*"]
                    if org_overdue:
                        lines.append(f"\n*{len(org_overdue)} overdue item(s):*")
                        for item in org_overdue[:5]:
                            vendor = item.get("vendor_name", "Unknown")
                            amount = item.get("amount", 0)
                            due = item.get("due_date", "?")
                            lines.append(f"  • {vendor} — ${amount:,.2f} (due {due})")
                    if org_stale:
                        lines.append(f"\n*{len(org_stale)} stale item(s) needing attention:*")
                        for item in org_stale[:5]:
                            vendor = item.get("vendor_name", "Unknown")
                            state = item.get("state", "?")
                            lines.append(f"  • {vendor} — stuck in `{state}`")
                    await _slack_alert("\n".join(lines), organization_id=org_id)
                log_reminder(summary_task_id, "overdue_summary")
        if total_overdue or total_stale:
            logger.info(
                "Background check: %d overdue, %d stale tasks across %d org(s)",
                total_overdue,
                total_stale,
                len(org_ids),
            )
    except Exception as e:
        logger.error("Overdue task check failed: %s", e)


async def _check_anomalies(org_id: str):
    """Detect volume and pattern anomalies, alert Slack."""
    try:
        from clearledgr.services.agent_anomaly_detection import AnomalyDetectionService

        service = AnomalyDetectionService(organization_id=org_id)
        anomalies = service.detect_all()

        if anomalies:
            logger.info("Detected %d anomalies for org=%s", len(anomalies), org_id)
            lines = [":warning: *Anomaly Detection*"]
            for a in anomalies[:5]:
                atype = a.get("type", "?")
                desc = a.get("description", "")
                lines.append(f"  • *{atype}:* {desc}")
            await _slack_alert("\n".join(lines), organization_id=org_id)
    except Exception as e:
        logger.error("Anomaly detection failed: %s", e)


async def _send_daily_digest(org_id: str):
    """Generate and send daily spending digest to Slack."""
    try:
        from clearledgr.services.proactive_insights import get_proactive_insights

        insights_service = get_proactive_insights(org_id)
        digest = insights_service.generate_daily_digest()

        if digest and digest.insights:
            logger.info(
                "Daily digest for org=%s: %d insights generated — %s",
                org_id,
                len(digest.insights),
                digest.summary,
            )
            lines = [f":bar_chart: *Daily AP Digest* — {digest.summary}"]
            for insight in digest.insights[:8]:
                lines.append(f"  • {insight.title}")
            await _slack_alert("\n".join(lines), organization_id=org_id)
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
        due_jobs = db.list_due_agent_retry_jobs(
            organization_id=None,
            limit=25,
        )

        for job in due_jobs:
            job_id = str(job.get("id") or "").strip()
            if not job_id:
                continue
            job_org_id = str(job.get("organization_id") or DEFAULT_ORG_ID).strip()
            claimed = db.claim_agent_retry_job(
                job_id,
                worker_id=f"agent_background:{job_org_id}",
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

                workflow = get_invoice_workflow(job_org_id)
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

    Deduplication is DB-backed via the ap_item's metadata column
    (``approval_reminder_milestones`` dict). This survives process restarts,
    deploys, and scale-out — unlike the old module-level ``_reminded_set``.
    """
    try:
        import json as _json
        from clearledgr.core.database import get_db
        from clearledgr.services.policy_compliance import get_approval_automation_policy
        from clearledgr.services.slack_notifications import send_approval_reminder

        db = get_db()
        if not hasattr(db, "get_overdue_approvals"):
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        policy = get_approval_automation_policy(organization_id=org_id)
        reminder_hours = max(1.0, float(policy.get("reminder_hours") or 4.0))
        escalation_hours = max(reminder_hours, float(policy.get("escalation_hours") or 24.0))
        escalation_channel = str(policy.get("escalation_channel") or "").strip() or None

        def _milestone_key(stage: str, hours_value: float) -> str:
            hours_token = str(int(hours_value) if float(hours_value).is_integer() else hours_value).replace(".", "_")
            return f"{stage}_{hours_token}h"

        milestones = [
            ("reminder", reminder_hours, _milestone_key("reminder", reminder_hours)),
            ("escalation", escalation_hours, _milestone_key("escalation", escalation_hours)),
        ]

        for stage, min_hours, milestone in milestones:
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
                legacy_milestone = (
                    f"{int(min_hours)}h"
                    if float(min_hours).is_integer() and stage in {"reminder", "escalation"}
                    else None
                )
                if milestone in milestones_sent or (legacy_milestone and legacy_milestone in milestones_sent):
                    continue  # already sent and recorded in DB

                approver_ids = db.get_pending_approver_ids(ap_item_id)
                # Resolve delegation — swap OOO approvers with their delegates
                try:
                    from clearledgr.services.approval_delegation import get_delegation_service
                    approver_ids = get_delegation_service(org_id).resolve_approvers(approver_ids)
                except Exception as exc:
                    logger.debug("Delegation resolution failed: %s", exc)
                reminder_sent = await send_approval_reminder(
                    ap_item=item,
                    approver_ids=approver_ids,
                    hours_pending=min_hours,
                    organization_id=org_id,
                    stage=stage,
                    escalation_channel=escalation_channel,
                )

                try:
                    event_type = "approval_nudge_sent" if reminder_sent else "approval_nudge_failed"
                    reason = (
                        f"approval_nudge_auto_{int(min_hours) if float(min_hours).is_integer() else min_hours}h"
                    )
                    if stage == "escalation":
                        event_type = "approval_escalation_sent" if reminder_sent else "approval_escalation_failed"
                        reason = (
                            f"approval_escalation_auto_{int(min_hours) if float(min_hours).is_integer() else min_hours}h"
                        )
                    db.append_ap_audit_event(
                        {
                            "ap_item_id": ap_item_id,
                            "event_type": event_type,
                            "actor_type": "system",
                            "actor_id": "agent_background",
                            "reason": reason,
                            "metadata": {
                                "auto": True,
                                "stage": stage,
                                "milestone": milestone,
                                "hours_pending": min_hours,
                                "approver_count": len(approver_ids or []),
                            },
                            "organization_id": org_id,
                            "source": "agent_background",
                            "idempotency_key": f"approval_{stage}_auto:{ap_item_id}:{milestone}",
                        }
                    )
                except Exception as audit_exc:
                    logger.error("Could not append auto-approval-nudge audit event: %s", audit_exc)

                patch: dict = {}
                if reminder_sent:
                    patch["approval_reminder_milestones"] = {
                        **milestones_sent,
                        milestone: now_iso,
                    }
                    if stage == "reminder":
                        patch["approval_nudge_count"] = max(0, int(meta.get("approval_nudge_count") or 0)) + 1
                        patch["approval_last_nudged_at"] = now_iso
                        patch["approval_next_action"] = "wait_for_approval"
                    if stage == "escalation":
                        patch["escalated_at"] = now_iso
                        patch["escalation_reason"] = f"approval_timeout_{milestone}"
                        patch["escalation_vendor"] = item.get("vendor_name")
                        patch["escalation_amount"] = item.get("amount")
                        patch["approval_escalation_count"] = max(0, int(meta.get("approval_escalation_count") or 0)) + 1
                        patch["approval_last_escalated_at"] = now_iso
                        patch["approval_next_action"] = "wait_for_escalated_review"

                if patch and hasattr(db, "update_ap_item_metadata_merge"):
                    db.update_ap_item_metadata_merge(ap_item_id, patch)

                logger.info(
                    "Approval timeout %s milestone triggered for ap_item_id=%s",
                    milestone,
                    ap_item_id,
                )
        # Auto-reassign pending approvals to delegates (OOO)
        try:
            from clearledgr.services.approval_delegation import get_delegation_service
            delegation_svc = get_delegation_service(org_id)
            reassigned = delegation_svc.auto_reassign_pending_approvals()
            if reassigned:
                logger.info("Delegation: reassigned %d pending approval(s) for org=%s", reassigned, org_id)
        except Exception as deleg_exc:
            logger.warning("Delegation auto-reassign failed: %s", deleg_exc)

    except Exception as exc:
        logger.error("Approval timeout check failed: %s", exc)


async def _run_task_scheduler_checks():
    """Run the task scheduler's overdue/approaching/stale checks."""
    try:
        from clearledgr.services.task_scheduler import run_all_checks

        results = run_all_checks()
        total = results.get("total_reminders", 0)
        if total:
            logger.info(
                "Task scheduler checks completed: %d reminder(s) sent", total
            )
    except Exception as exc:
        logger.error("Task scheduler checks failed: %s", exc)


async def _run_erp_reconciliation(org_id: str):
    """Run ERP follow-on reconciliation check for stale posted items."""
    try:
        from clearledgr.services.erp_follow_on_reconciliation import (
            run_erp_follow_on_reconciliation_check,
        )

        checked = await run_erp_follow_on_reconciliation_check(organization_id=org_id)
        if checked:
            logger.info(
                "ERP follow-on reconciliation checked %d item(s) for org=%s",
                checked,
                org_id,
            )
    except Exception as e:
        logger.error("ERP follow-on reconciliation failed for org=%s: %s", org_id, e)


async def _verify_recent_erp_postings(org_id: str):
    """Verify that recently posted AP items actually exist in the ERP.

    Queries items with state ``posted_to_erp`` from the last 24 hours and
    calls ``verify_bill_posted`` for each.  If the bill is not found, sets
    ``exception_code = 'erp_sync_mismatch'`` on the AP item so it surfaces
    in the worklist.
    """
    try:
        from clearledgr.core.database import get_db
        from clearledgr.integrations.erp_router import verify_bill_posted

        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        # Fetch recently-posted items
        sql = db._prepare_sql(
            "SELECT * FROM ap_items "
            "WHERE organization_id = ? AND state = 'posted_to_erp' "
            "AND updated_at >= ? "
            "ORDER BY updated_at DESC LIMIT 50"
        )
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (org_id, cutoff))
            rows = [dict(r) for r in cur.fetchall()]

        if not rows:
            return

        mismatches = 0
        for item in rows:
            invoice_number = item.get("invoice_number") or item.get("erp_reference")
            if not invoice_number:
                continue

            # Skip items already flagged
            if item.get("exception_code") == "erp_sync_mismatch":
                continue

            try:
                result = await verify_bill_posted(
                    organization_id=org_id,
                    invoice_number=str(invoice_number),
                    expected_amount=float(item["amount"]) if item.get("amount") else None,
                )
            except Exception as ver_exc:
                logger.debug("ERP verification lookup failed for %s: %s", item.get("id"), ver_exc)
                continue

            if not result.get("verified", True):
                mismatches += 1
                ap_item_id = item.get("id")
                logger.warning(
                    "ERP sync mismatch: AP item %s (invoice %s) not found in ERP — reason=%s",
                    ap_item_id,
                    invoice_number,
                    result.get("reason"),
                )
                try:
                    db.update_ap_item(
                        ap_item_id,
                        exception_code="erp_sync_mismatch",
                        exception_severity="high",
                    )
                    db.append_ap_audit_event({
                        "ap_item_id": ap_item_id,
                        "event_type": "erp_sync_mismatch",
                        "actor_type": "system",
                        "actor_id": "agent_background",
                        "reason": result.get("reason", "bill_not_found_in_erp"),
                        "organization_id": org_id,
                        "source": "agent_background",
                        "idempotency_key": f"erp_sync_mismatch:{ap_item_id}",
                    })
                except Exception as update_exc:
                    logger.error("Failed to flag ERP sync mismatch for %s: %s", ap_item_id, update_exc)

        if mismatches:
            logger.info(
                "ERP sync verification: %d mismatch(es) out of %d item(s) for org=%s",
                mismatches, len(rows), org_id,
            )
    except Exception as exc:
        logger.error("ERP sync verification failed for org=%s: %s", org_id, exc)


def _is_within_days(date_str: Any, days: int) -> bool:
    """Check if an ISO date string is within the last N days."""
    parsed = _parse_task_datetime(date_str)
    if not parsed:
        return False
    return (datetime.now(timezone.utc) - parsed).days <= days


async def _poll_payment_statuses(org_id: str) -> Dict[str, int]:
    """Poll ERP for payment status updates on ready/scheduled payments.

    Reads payment status from the ERP via GET requests — NEVER executes payments.
    Caps per org per tick:
    - 50 ready/scheduled payments
    - 20 recently completed payments (reversal detection)
    - 20 overdue checks
    """
    try:
        from clearledgr.core.database import get_db
        from clearledgr.integrations.erp_router import get_bill_payment_status

        db = get_db()
        pending_payments = (
            db.list_payments_by_status(org_id, "ready_for_payment")
            + db.list_payments_by_status(org_id, "scheduled")
        )

        checked = 0
        updated = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        now = datetime.now(timezone.utc)

        for payment in pending_payments[:50]:  # Cap per tick
            erp_ref = payment.get("erp_reference")
            if not erp_ref:
                continue
            checked += 1

            try:
                status = await get_bill_payment_status(
                    organization_id=org_id,
                    erp_reference=erp_ref,
                    invoice_number=payment.get("invoice_number"),
                )

                ap_item_id = payment.get("ap_item_id")

                # Gap 3: Payment failed in ERP
                if status.get("payment_failed"):
                    fail_reason = status.get("reason", "unknown")
                    db.update_payment(
                        payment["id"],
                        status="failed",
                        notes=f"Payment failed in ERP: {fail_reason}",
                    )
                    if ap_item_id and hasattr(db, "update_ap_item_metadata_merge"):
                        db.update_ap_item_metadata_merge(ap_item_id, {
                            "payment_status": "failed",
                            "payment_failed_reason": fail_reason,
                        })
                    # Gap 4: Log payment event
                    if hasattr(db, "append_payment_event"):
                        db.append_payment_event(
                            payment_id=payment["id"],
                            org_id=org_id,
                            event_type="payment_failed",
                            amount=status.get("payment_amount"),
                            reference=status.get("payment_reference"),
                            method=status.get("payment_method"),
                            erp_data=status,
                        )
                    try:
                        from clearledgr.services.slack_notifications import (
                            send_payment_failed_notification,
                        )
                        await send_payment_failed_notification(
                            organization_id=org_id,
                            vendor_name=payment.get("vendor_name", "Unknown"),
                            amount=float(payment.get("amount") or 0),
                            currency=payment.get("currency", "USD"),
                            reason=fail_reason,
                            ap_item_id=ap_item_id,
                        )
                    except Exception as notify_exc:
                        logger.warning("Payment failed notification failed: %s", notify_exc)
                    updated += 1
                    continue

                # Gap 5: Credit/write-off closure
                closure_method = status.get("closure_method")
                if status.get("paid") and closure_method and closure_method != "payment":
                    db.update_payment(
                        payment["id"],
                        status="closed_by_credit",
                        notes=f"Closed by {closure_method}: {status.get('payment_reference', '')}",
                        completed_date=now_iso,
                    )
                    if ap_item_id and hasattr(db, "update_ap_item_metadata_merge"):
                        db.update_ap_item_metadata_merge(ap_item_id, {
                            "payment_status": "closed_by_credit",
                            "payment_closure_method": closure_method,
                            "payment_completed_at": now_iso,
                        })
                    # Auto-close AP item after credit closure (#34)
                    if ap_item_id:
                        try:
                            ap_item = db.get_ap_item(ap_item_id)
                            if ap_item and str(ap_item.get("state") or "").lower() == "posted_to_erp":
                                db.update_ap_item(ap_item_id, state="closed")
                                if hasattr(db, "append_ap_audit_event"):
                                    db.append_ap_audit_event(
                                        ap_item_id=ap_item_id,
                                        event_type="closed_by_credit",
                                        prev_state="posted_to_erp",
                                        new_state="closed",
                                        actor_type="system",
                                        actor_id="payment_poll",
                                        organization_id=org_id,
                                        payload={"closure_method": closure_method},
                                    )
                                logger.info("Auto-closed ap_item=%s after credit closure", ap_item_id)
                        except Exception as close_exc:
                            logger.warning("Could not auto-close ap_item=%s: %s", ap_item_id, close_exc)

                    # Log payment event
                    if hasattr(db, "append_payment_event"):
                        db.append_payment_event(
                            payment_id=payment["id"],
                            org_id=org_id,
                            event_type="credit_applied",
                            amount=status.get("payment_amount"),
                            reference=status.get("payment_reference"),
                            method=closure_method,
                            erp_data=status,
                        )
                    try:
                        from clearledgr.services.slack_notifications import (
                            send_payment_credit_applied_notification,
                        )
                        await send_payment_credit_applied_notification(
                            organization_id=org_id,
                            vendor_name=payment.get("vendor_name", "Unknown"),
                            amount=float(payment.get("amount") or 0),
                            currency=payment.get("currency", "USD"),
                            closure_method=closure_method,
                            reference=status.get("payment_reference"),
                            ap_item_id=ap_item_id,
                        )
                    except Exception as notify_exc:
                        logger.warning("Credit applied notification failed: %s", notify_exc)
                    updated += 1
                    continue

                if status.get("paid"):
                    db.update_payment(
                        payment["id"],
                        status="completed",
                        payment_reference=status.get("payment_reference", ""),
                        payment_method=status.get("payment_method", ""),
                        completed_date=now_iso,
                        paid_amount=status.get("payment_amount"),
                        notes=f"Payment detected in ERP: {status.get('payment_reference', '')}",
                    )
                    # Update AP item metadata
                    if ap_item_id and hasattr(db, "update_ap_item_metadata_merge"):
                        db.update_ap_item_metadata_merge(ap_item_id, {
                            "payment_status": "completed",
                            "payment_completed_at": now_iso,
                            "payment_method": status.get("payment_method", ""),
                            "payment_reference": status.get("payment_reference", ""),
                        })
                    # Auto-close AP item after full payment (#34)
                    if ap_item_id:
                        try:
                            ap_item = db.get_ap_item(ap_item_id)
                            if ap_item and str(ap_item.get("state") or "").lower() == "posted_to_erp":
                                db.update_ap_item(ap_item_id, state="closed")
                                if hasattr(db, "append_ap_audit_event"):
                                    db.append_ap_audit_event(
                                        ap_item_id=ap_item_id,
                                        event_type="closed_by_payment",
                                        prev_state="posted_to_erp",
                                        new_state="closed",
                                        actor_type="system",
                                        actor_id="payment_poll",
                                        organization_id=org_id,
                                        payload={"payment_reference": status.get("payment_reference", "")},
                                    )
                                logger.info("Auto-closed ap_item=%s after payment completed", ap_item_id)
                        except Exception as close_exc:
                            logger.warning("Could not auto-close ap_item=%s: %s", ap_item_id, close_exc)

                    # Log payment event
                    if hasattr(db, "append_payment_event"):
                        db.append_payment_event(
                            payment_id=payment["id"],
                            org_id=org_id,
                            event_type="payment_detected",
                            amount=status.get("payment_amount"),
                            reference=status.get("payment_reference"),
                            method=status.get("payment_method"),
                            erp_data=status,
                        )
                    # Notify via Slack
                    try:
                        from clearledgr.services.slack_notifications import (
                            send_payment_completed_notification,
                        )
                        await send_payment_completed_notification(
                            organization_id=org_id,
                            vendor_name=payment.get("vendor_name", "Unknown"),
                            amount=float(payment.get("amount") or 0),
                            currency=payment.get("currency", "USD"),
                            payment_reference=status.get("payment_reference"),
                            payment_method=status.get("payment_method"),
                            ap_item_id=ap_item_id,
                        )
                    except Exception as notify_exc:
                        logger.warning("Payment completion notification failed: %s", notify_exc)
                    updated += 1

                elif status.get("partial"):
                    db.update_payment(
                        payment["id"],
                        status="partial",
                        paid_amount=status.get("payment_amount"),
                        notes=(
                            f"Partial payment: {status.get('payment_amount')} of "
                            f"{payment.get('amount')}. Remaining: {status.get('remaining_balance')}"
                        ),
                    )
                    # Update AP item metadata
                    if ap_item_id and hasattr(db, "update_ap_item_metadata_merge"):
                        db.update_ap_item_metadata_merge(ap_item_id, {
                            "payment_status": "partial",
                            "payment_paid_amount": status.get("payment_amount"),
                            "payment_remaining": status.get("remaining_balance"),
                        })
                    # Gap 4: Log payment event
                    if hasattr(db, "append_payment_event"):
                        db.append_payment_event(
                            payment_id=payment["id"],
                            org_id=org_id,
                            event_type="partial_payment",
                            amount=status.get("payment_amount"),
                            reference=status.get("payment_reference"),
                            method=status.get("payment_method"),
                            erp_data=status,
                        )
                    # Notify via Slack
                    try:
                        from clearledgr.services.slack_notifications import (
                            send_payment_partial_notification,
                        )
                        await send_payment_partial_notification(
                            organization_id=org_id,
                            vendor_name=payment.get("vendor_name", "Unknown"),
                            amount=float(payment.get("amount") or 0),
                            paid_amount=float(status.get("payment_amount") or 0),
                            remaining=float(status.get("remaining_balance") or 0),
                            currency=payment.get("currency", "USD"),
                            ap_item_id=ap_item_id,
                        )
                    except Exception as notify_exc:
                        logger.warning("Partial payment notification failed: %s", notify_exc)
                    updated += 1

            except Exception as exc:
                logger.warning(
                    "Payment status poll failed for payment %s: %s",
                    payment.get("id"), exc,
                )

        # -------------------------------------------------------------------
        # Gap 1: Re-check recently completed payments for reversals
        # -------------------------------------------------------------------
        try:
            recent_completed = db.list_payments_by_status(org_id, "completed")
            recent_completed = [
                p for p in recent_completed
                if _is_within_days(p.get("completed_date"), 7)
            ]

            for payment in recent_completed[:20]:  # Cap at 20
                erp_ref = payment.get("erp_reference")
                if not erp_ref:
                    continue
                checked += 1

                try:
                    status = await get_bill_payment_status(
                        organization_id=org_id,
                        erp_reference=erp_ref,
                        invoice_number=payment.get("invoice_number"),
                    )

                    if not status.get("paid"):
                        # Payment was reversed/voided in ERP
                        db.update_payment(
                            payment["id"],
                            status="reversed",
                            notes=f"Payment reversal detected in ERP on {now_iso}",
                        )
                        ap_item_id = payment.get("ap_item_id")
                        if ap_item_id and hasattr(db, "update_ap_item_metadata_merge"):
                            db.update_ap_item_metadata_merge(ap_item_id, {
                                "payment_status": "reversed",
                            })
                        # Gap 4: Log reversal event
                        if hasattr(db, "append_payment_event"):
                            db.append_payment_event(
                                payment_id=payment["id"],
                                org_id=org_id,
                                event_type="reversal",
                                amount=payment.get("amount"),
                                reference=erp_ref,
                                erp_data=status,
                            )
                        try:
                            from clearledgr.services.slack_notifications import (
                                send_payment_reversed_notification,
                            )
                            await send_payment_reversed_notification(
                                organization_id=org_id,
                                vendor_name=payment.get("vendor_name", "Unknown"),
                                amount=float(payment.get("amount") or 0),
                                currency=payment.get("currency", "USD"),
                                reference=erp_ref,
                                ap_item_id=ap_item_id,
                            )
                        except Exception as notify_exc:
                            logger.warning("Payment reversed notification failed: %s", notify_exc)
                        updated += 1

                except Exception as exc:
                    logger.warning(
                        "Reversal check failed for payment %s: %s",
                        payment.get("id"), exc,
                    )
        except Exception as exc:
            logger.warning("Reversal detection sweep failed for org=%s: %s", org_id, exc)

        # -------------------------------------------------------------------
        # Gap 2: Check for overdue payments
        # -------------------------------------------------------------------
        try:
            all_ready = db.list_payments_by_status(org_id, "ready_for_payment")
            overdue_checked = 0
            for payment in all_ready:
                if overdue_checked >= 20:
                    break
                due_date = payment.get("due_date")
                if not due_date:
                    continue
                # Skip payments already alerted as overdue
                if payment.get("overdue_alerted"):
                    continue
                try:
                    due_dt = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                    if due_dt.tzinfo is None:
                        due_dt = due_dt.replace(tzinfo=timezone.utc)
                    if now > due_dt:
                        days_overdue = (now - due_dt).days
                        overdue_checked += 1
                        db.update_payment(
                            payment["id"],
                            status="overdue",
                            notes=f"Overdue by {days_overdue} days",
                            overdue_alerted=now_iso,
                        )
                        ap_item_id = payment.get("ap_item_id")
                        try:
                            from clearledgr.services.slack_notifications import (
                                send_payment_overdue_notification,
                            )
                            await send_payment_overdue_notification(
                                organization_id=org_id,
                                vendor_name=payment.get("vendor_name", "Unknown"),
                                amount=float(payment.get("amount") or 0),
                                currency=payment.get("currency", "USD"),
                                due_date=due_date,
                                days_overdue=days_overdue,
                                ap_item_id=ap_item_id,
                            )
                        except Exception as notify_exc:
                            logger.warning("Overdue payment notification failed: %s", notify_exc)
                        updated += 1
                except Exception as exc:
                    logger.warning("Overdue check failed for payment %s: %s", payment.get("id", "?"), exc)
        except Exception as exc:
            logger.warning("Overdue payment check failed for org=%s: %s", org_id, exc)

        if checked:
            logger.info(
                "Payment status poll for org=%s: checked=%d updated=%d",
                org_id, checked, updated,
            )
        return {"checked": checked, "updated": updated}
    except Exception as exc:
        logger.error("Payment status polling failed for org=%s: %s", org_id, exc)
        return {"checked": 0, "updated": 0}


async def _check_vendor_followup_responses(org_id: str):
    """Scan AP items in ``needs_info`` for vendor replies to follow-ups.

    For each item with ``pending_followup`` or ``followup_sent_at`` metadata:
    1. Check Gmail thread for vendor reply.
    2. If reply found: update metadata, transition needs_info -> validated,
       notify Slack.
    3. If no reply: check escalation (resend or escalate).
    """
    try:
        import json as _json
        from clearledgr.core.database import get_db
        from clearledgr.services.auto_followup import get_auto_followup_service
        from clearledgr.services.gmail_api import GmailAPIClient, token_store

        db = get_db()
        items = db.list_ap_items(org_id, state="needs_info", limit=100)
        if not items:
            return

        followup_svc = get_auto_followup_service(org_id)

        for item in items:
            ap_item_id = item.get("id")
            if not ap_item_id:
                continue

            try:
                metadata = _json.loads(item.get("metadata") or "{}") if isinstance(item.get("metadata"), str) else (item.get("metadata") or {})
            except Exception:
                metadata = {}

            followup_sent_at = metadata.get("followup_sent_at")
            if not followup_sent_at:
                continue

            thread_id = item.get("thread_id") or metadata.get("followup_thread_id")
            vendor_email = metadata.get("followup_to") or ""
            if not thread_id or not vendor_email:
                continue

            # --- Get a Gmail client for this org ---
            gmail_client = None
            try:
                tokens = token_store.list_all()
                if tokens:
                    gmail_client = GmailAPIClient(tokens[0].user_id)
                    if not await gmail_client.ensure_authenticated():
                        gmail_client = None
            except Exception:
                gmail_client = None

            if not gmail_client:
                continue

            # --- Check for vendor response ---
            response = await followup_svc.check_vendor_response(
                gmail_client=gmail_client,
                ap_item_id=ap_item_id,
                thread_id=thread_id,
                followup_sent_at=followup_sent_at,
                vendor_email=vendor_email,
            )

            if response:
                now_iso = datetime.now(timezone.utc).isoformat()
                metadata["vendor_response_received"] = True
                metadata["vendor_response_at"] = now_iso
                metadata["vendor_response_message_id"] = response.get("message_id")
                metadata.pop("pending_followup", None)

                try:
                    db.update_ap_item(
                        ap_item_id,
                        metadata=_json.dumps(metadata),
                        state="validated",
                    )
                except Exception as trans_exc:
                    logger.warning(
                        "Could not transition ap_item=%s back to validated: %s",
                        ap_item_id, trans_exc,
                    )

                vendor_name = item.get("vendor_name") or "Unknown"
                invoice_num = item.get("invoice_number") or "N/A"

                # Create/update dispute record for tracking
                try:
                    from clearledgr.services.dispute_service import get_dispute_service
                    dsp_svc = get_dispute_service(org_id)
                    existing_disputes = db.get_disputes_for_item(ap_item_id)
                    open_disputes = [d for d in existing_disputes if d.get("status") not in ("resolved", "closed")]
                    if open_disputes:
                        dsp_svc.mark_response_received(open_disputes[0]["id"])
                    else:
                        d = dsp_svc.open_dispute(ap_item_id, "missing_info", vendor_name=vendor_name)
                        dsp_svc.mark_vendor_contacted(d["id"], followup_thread_id=thread_id)
                        dsp_svc.mark_response_received(d["id"])
                except Exception as exc:
                    logger.warning("Dispute update failed for ap_item %s: %s", ap_item_id, exc)

                try:
                    from clearledgr.services.slack_notifications import send_vendor_response_notification
                    await send_vendor_response_notification(
                        organization_id=org_id,
                        vendor=vendor_name,
                        invoice_number=invoice_num,
                        ap_item_id=ap_item_id,
                    )
                except Exception as notify_exc:
                    logger.warning("Vendor response notification failed: %s", notify_exc)

                logger.info(
                    "Vendor response detected for ap_item=%s, transitioned back to validated",
                    ap_item_id,
                )
                continue

            # --- No response yet — check escalation ---
            attempt_count = int(metadata.get("followup_attempt_count") or 1)
            escalation = followup_svc.check_followup_escalation(
                ap_item_id=ap_item_id,
                followup_sent_at=followup_sent_at,
                followup_attempt_count=attempt_count,
            )

            if not escalation:
                continue

            action = escalation.get("action")
            vendor_name = item.get("vendor_name") or "Unknown"
            invoice_num = item.get("invoice_number") or "N/A"

            if action == "resend":
                # Re-send follow-up
                try:
                    original_question = metadata.get("followup_question") or metadata.get("pending_followup", {}).get("question") or ""
                    original_subject = item.get("subject") or "Invoice follow-up"

                    from clearledgr.services.vendor_communication_templates import render_template
                    rendered = render_template("followup_reminder", {
                        "original_subject": original_subject,
                        "original_question": original_question or "the information previously requested",
                        "invoice_number": invoice_num,
                        "amount": f"{item.get('amount', 0):,.2f}",
                        "currency": item.get("currency") or "USD",
                        "company_name": "Clearledgr",
                    })

                    await gmail_client.send_message(
                        to=vendor_email,
                        subject=rendered["subject"],
                        body=rendered["body"],
                        thread_id=thread_id,
                    )

                    now_iso = datetime.now(timezone.utc).isoformat()
                    metadata["followup_sent_at"] = now_iso
                    metadata["followup_attempt_count"] = escalation["attempt"]
                    db.update_ap_item(ap_item_id, metadata=_json.dumps(metadata))

                    logger.info(
                        "Re-sent follow-up #%d for ap_item=%s",
                        escalation["attempt"], ap_item_id,
                    )
                except Exception as resend_exc:
                    logger.warning("Follow-up resend failed for ap_item=%s: %s", ap_item_id, resend_exc)

            elif action == "escalate":
                # Mark as vendor_unresponsive
                try:
                    metadata["exception_code"] = "vendor_unresponsive"
                    metadata["escalated_at"] = datetime.now(timezone.utc).isoformat()
                    db.update_ap_item(ap_item_id, metadata=_json.dumps(metadata))
                except Exception as exc:
                    logger.warning("Escalation metadata update failed for ap_item %s: %s", ap_item_id, exc)

                try:
                    from clearledgr.services.slack_notifications import send_vendor_escalation_notification
                    await send_vendor_escalation_notification(
                        organization_id=org_id,
                        vendor=vendor_name,
                        invoice_number=invoice_num,
                        days_waiting=escalation.get("days_waiting", 0),
                        attempts=escalation.get("attempts", 0),
                        ap_item_id=ap_item_id,
                    )
                except Exception as notify_exc:
                    logger.warning("Vendor escalation notification failed: %s", notify_exc)

                logger.info(
                    "Vendor unresponsive — escalated ap_item=%s after %d attempts",
                    ap_item_id, escalation.get("attempts", 0),
                )
    except Exception as exc:
        logger.error("Vendor followup response check failed for org=%s: %s", org_id, exc)


async def _sweep_exception_resolutions(org_id: str):
    """Attempt auto-resolution for AP items with active exceptions.

    Runs every 3rd tick (~45 min).  Caps at 25 items per org per sweep to
    avoid monopolising the event loop.

    Auto-resolved items have their ``exception_code`` cleared and an audit
    event logged.  Items that cannot be resolved are left unchanged.
    """
    try:
        from clearledgr.core.database import get_db
        from clearledgr.services.exception_resolver import get_exception_resolver

        db = get_db()
        resolver = get_exception_resolver(org_id)

        # Active states: items still in the pipeline, not rejected/closed
        active_states = (
            "new", "enriched", "validated", "needs_approval", "approved",
            "needs_info", "failed_post",
        )

        items_resolved = 0
        items_checked = 0

        for state in active_states:
            if items_checked >= 25:
                break
            try:
                ap_items = db.list_ap_items(org_id, state=state, limit=50)
            except Exception:
                continue
            for item in ap_items:
                if items_checked >= 25:
                    break
                exc_code = item.get("exception_code") or ""
                if not exc_code:
                    continue

                items_checked += 1
                try:
                    result = await resolver.resolve(item, exc_code)
                except Exception:
                    continue

                if result.get("resolved"):
                    items_resolved += 1
                    try:
                        db.append_ap_audit_event({
                            "ap_item_id": item.get("id"),
                            "event_type": "exception_auto_resolved",
                            "actor_type": "system",
                            "actor_id": "agent_background",
                            "reason": result.get("action") or "auto_resolved",
                            "metadata": {
                                "exception_code": exc_code,
                                "resolution": result,
                            },
                            "organization_id": org_id,
                            "source": "agent_background",
                            "idempotency_key": f"exc_resolve:{item.get('id')}:{exc_code}",
                        })
                    except Exception as audit_exc:
                        logger.debug(
                            "Could not log exception resolution audit event: %s",
                            audit_exc,
                        )

        if items_checked > 0:
            logger.info(
                "Exception sweep for org=%s: checked=%d resolved=%d",
                org_id, items_checked, items_resolved,
            )
    except Exception as exc:
        logger.error("Exception resolution sweep failed for org=%s: %s", org_id, exc)


async def _check_period_end(org_id: str):
    """Detect period-end and alert about closing deadlines in Slack."""
    try:
        from clearledgr.services.agent_monitoring import detect_period_end

        period_info = detect_period_end()

        if period_info and period_info.get("is_period_end"):
            period_type = period_info.get("period_type", "month")
            days_left = period_info.get("days_remaining", "?")
            logger.info(f"Period-end detected: {period_type}")
            await _slack_alert(
                f":calendar: *Period-End Alert*\n"
                f"{period_type.title()}-end closing in *{days_left} day(s)*. "
                f"Review pending AP items before the cutoff.",
                organization_id=org_id,
            )
    except Exception as e:
        logger.error("Period-end detection failed: %s", e)


async def _sync_vendor_master_data(org_id: str):
    """Sync vendor master data from ERP to Clearledgr vendor profiles (daily)."""
    try:
        from clearledgr.services.vendor_erp_sync import sync_vendors_from_erp

        summary = await sync_vendors_from_erp(organization_id=org_id)
        synced = summary.get("synced_count", 0)
        new_count = summary.get("new_vendor_count", 0)
        deactivated = summary.get("deactivated_count", 0)
        terms_changed = summary.get("terms_changed_count", 0)

        if synced:
            logger.info(
                "Vendor master sync completed for org=%s: %d synced, %d new, %d deactivated",
                org_id, synced, new_count, deactivated,
            )
        # Alert on significant changes
        alerts = []
        if new_count:
            alerts.append(f"{new_count} new vendor(s) added")
        if deactivated:
            vendors = ", ".join(summary.get("deactivated_vendors", [])[:5])
            alerts.append(f"{deactivated} vendor(s) deactivated ({vendors})")
        if terms_changed:
            alerts.append(f"{terms_changed} vendor(s) changed payment terms")
        if alerts:
            await _slack_alert(
                f":arrows_counterclockwise: *Vendor Master Sync*\n"
                + "\n".join(f"• {a}" for a in alerts),
                organization_id=org_id,
            )
    except Exception as e:
        logger.error("Vendor master sync failed for org=%s: %s", org_id, e)


async def _deliver_scheduled_reports(org_id: str):
    """Check and deliver any scheduled reports that are due."""
    try:
        from clearledgr.services.scheduled_reports import get_scheduled_report_service
        service = get_scheduled_report_service(org_id)
        delivered = await service.run_due_reports()
        if delivered:
            logger.info("Scheduled reports: delivered %d for org=%s", delivered, org_id)
    except Exception as e:
        logger.error("Scheduled report delivery failed for org=%s: %s", org_id, e)


async def _run_monitoring_checks(org_id: str):
    """Run monitoring health checks and emit alerts on threshold breaches."""
    try:
        from clearledgr.services.monitoring import run_monitoring_checks

        result = await run_monitoring_checks(organization_id=org_id)
        if result.get("alert_count", 0) > 0:
            logger.warning(
                "Monitoring: %d alert(s) for org=%s",
                result["alert_count"], org_id,
            )
    except Exception as e:
        logger.error("Monitoring checks failed for org=%s: %s", org_id, e)
