"""Shared retry-job recovery helpers for agent startup and background loops."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from clearledgr.core.database import get_db
from clearledgr.services.invoice_workflow import get_invoice_workflow


logger = logging.getLogger(__name__)


def _try_ops_alert(ap_item_id: str, retry_count: int, last_error: str) -> None:
    """Best-effort Slack ops alert for dead-lettered AP items."""
    try:
        from clearledgr.services.slack_notifications import send_ops_alert
        send_ops_alert(
            f"AP item {ap_item_id} entered dead letter queue after {retry_count} retries. "
            f"Last error: {last_error}. Manual intervention required."
        )
    except Exception:
        pass  # Alert is best-effort; CRITICAL log above is the guaranteed signal


def retry_backoff_seconds(attempt_number: int) -> int:
    """Backoff schedule for durable ERP retry jobs."""
    schedule = [300, 900, 1800, 3600]
    safe_attempt = max(1, int(attempt_number or 1))
    idx = min(len(schedule) - 1, safe_attempt - 1)
    return schedule[idx]


async def drain_agent_retry_jobs(
    *,
    organization_id: Optional[str] = None,
    limit: int = 25,
    worker_id_prefix: str = "agent_recovery",
) -> Dict[str, int]:
    """Drain due retry jobs through the canonical workflow runtime."""
    summary = {
        "claimed": 0,
        "completed": 0,
        "rescheduled": 0,
        "dead_letter": 0,
    }
    db = get_db()
    if not hasattr(db, "list_due_agent_retry_jobs"):
        return summary

    # Release stale locks (jobs claimed > 15 minutes ago that are still running)
    if hasattr(db, "release_stale_retry_locks"):
        try:
            stale_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
            released = db.release_stale_retry_locks(stale_cutoff)
            if released:
                logger.warning("Released %d stale retry job locks (older than 15 min)", released)
        except Exception as stale_exc:
            logger.warning("Stale lock release failed: %s", stale_exc)

    due_jobs = db.list_due_agent_retry_jobs(
        organization_id=organization_id,
        limit=limit,
    )
    for job in due_jobs:
        job_id = str(job.get("id") or "").strip()
        if not job_id:
            continue
        job_org_id = str(job.get("organization_id") or organization_id or "default").strip() or "default"
        claimed = db.claim_agent_retry_job(
            job_id,
            worker_id=f"{worker_id_prefix}:{job_org_id}",
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
                logger.critical(
                    "AP item %s entered dead letter queue after %d retries. Last error: %s. "
                    "Manual intervention required.",
                    ap_item_id or "unknown", 0, "missing_ap_item_id",
                )
                _try_ops_alert(ap_item_id or "unknown", 0, "missing_ap_item_id")
                summary["dead_letter"] += 1
                continue

            workflow = get_invoice_workflow(job_org_id)
            try:
                outcome = await asyncio.wait_for(workflow.resume_workflow(ap_item_id), timeout=120)
            except asyncio.TimeoutError:
                logger.error("Retry job timed out after 120s for ap_item=%s", ap_item_id)
                outcome = {"status": "timeout"}
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
                    + timedelta(seconds=retry_backoff_seconds(retry_count))
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

            last_error = str(
                outcome.get("reason")
                or outcome.get("error")
                or outcome_status
                or "retry_unrecoverable"
            )
            db.complete_agent_retry_job(
                job_id,
                status="dead_letter",
                last_error=last_error,
                result=outcome,
            )
            logger.critical(
                "AP item %s entered dead letter queue after %d retries. Last error: %s. "
                "Manual intervention required.",
                ap_item_id, retry_count, last_error,
            )
            _try_ops_alert(ap_item_id, retry_count, last_error)
            summary["dead_letter"] += 1
            continue

        if job_type == "post_process":
            db.complete_agent_retry_job(
                job_id,
                status="dead_letter",
                last_error="post_process_runtime_removed",
                result={"error": "post_process_runtime_removed"},
            )
            logger.critical(
                "Retry job %s entered dead letter queue (post_process_runtime_removed). "
                "Manual intervention required.",
                job_id,
            )
            summary["dead_letter"] += 1
            continue

        unsupported_error = f"unsupported_retry_job_type:{job_type or 'unknown'}"
        db.complete_agent_retry_job(
            job_id,
            status="dead_letter",
            last_error=unsupported_error,
            result={"error": "unsupported_retry_job_type", "job_type": job_type},
        )
        logger.critical(
            "Retry job %s entered dead letter queue (unsupported type: %s). "
            "Manual intervention required.",
            job_id, job_type or "unknown",
        )
        summary["dead_letter"] += 1

    if summary["claimed"] > 0:
        logger.info(
            "Agent retry drain[%s]: claimed=%s completed=%s rescheduled=%s dead_letter=%s",
            organization_id or "all",
            summary["claimed"],
            summary["completed"],
            summary["rescheduled"],
            summary["dead_letter"],
        )
    return summary
