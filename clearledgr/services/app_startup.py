from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from clearledgr.services.logging import logger

# Maximum reasonable in-flight age for a task_run before we treat it as
# orphaned by an api crash. The longest legitimate single task is bounded
# by Celery hard timeouts (low single digit minutes) plus a safety margin
# for retries; 30 min is well past any normal completion. Anything older
# is almost certainly an orphan from a redeploy or worker SIGKILL.
_TASK_RUN_ORPHAN_THRESHOLD = timedelta(minutes=30)

_DEFERRED_STARTUP_TASK_ATTR = "deferred_startup_task"
_DEFERRED_STARTUP_HANDLE_ATTR = "deferred_startup_handle"


async def run_deferred_startup(app: Any) -> None:
    """Run slow startup tasks after the server has already bound."""
    try:
        from clearledgr.services.gmail_autopilot import start_gmail_autopilot

        await asyncio.wait_for(start_gmail_autopilot(app), timeout=10.0)
        logger.info("Gmail autopilot started")
    except asyncio.TimeoutError:
        logger.warning("Gmail autopilot startup timed out (10s) — skipping")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gmail autopilot not started: %s", exc)

    try:
        # §12 #6 — Outlook is not shipped in V1. The autopilot loop
        # stays in the tree as post-launch scaffolding; flag gates
        # whether it actually starts. Without this gate any deployment
        # that sets MICROSOFT_CLIENT_ID would silently bring Outlook
        # live, which breaks the V1 positioning the thesis is explicit
        # about.
        from clearledgr.core.feature_flags import is_outlook_enabled

        if not is_outlook_enabled():
            logger.info("Outlook autopilot skipped — §12 #6 V1 boundary (FEATURE_OUTLOOK_ENABLED not set)")
        else:
            from clearledgr.services.outlook_autopilot import start_outlook_autopilot

            await asyncio.wait_for(start_outlook_autopilot(app), timeout=10.0)
            logger.info("Outlook autopilot started")
    except asyncio.TimeoutError:
        logger.warning("Outlook autopilot startup timed out (10s) — skipping")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Outlook autopilot not started: %s", exc)

    try:
        from clearledgr.services.agent_background import start_agent_background

        await asyncio.wait_for(start_agent_background(app), timeout=10.0)
        logger.info("Agent background intelligence started")
    except asyncio.TimeoutError:
        logger.warning("Agent background startup timed out (10s) — skipping")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent background not started: %s", exc)

    # Phase 1.4: One-shot reaper sweep at boot so windows that expired
    # while the process was down get finalized BEFORE the dedicated 60s
    # reaper loop starts ticking. Without this sweep, a process restart
    # can leave stale undo cards live for an extra 60s. With it, we
    # converge to clean state immediately on boot.
    try:
        from clearledgr.services.agent_background import (
            reap_expired_override_windows,
        )

        reaped = await asyncio.wait_for(reap_expired_override_windows(), timeout=10.0)
        logger.info(
            "Override window startup sweep complete (%d windows reaped)", reaped or 0
        )
    except asyncio.TimeoutError:
        logger.warning("Override window startup sweep timed out (10s) — skipping")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Override window startup sweep not started: %s", exc)

    try:
        from clearledgr.services.finance_agent_runtime import get_platform_finance_runtime

        runtime = get_platform_finance_runtime("default")
        recovery = await asyncio.wait_for(runtime.resume_pending_agent_tasks(), timeout=10.0)
        logger.info(
            "Finance agent runtime started (claimed=%d completed=%d rescheduled=%d dead_letter=%d)",
            int((recovery or {}).get("claimed") or 0),
            int((recovery or {}).get("completed") or 0),
            int((recovery or {}).get("rescheduled") or 0),
            int((recovery or {}).get("dead_letter") or 0),
        )
    except asyncio.TimeoutError:
        logger.warning("Finance agent runtime startup timed out (10s) — skipping")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Finance agent runtime not started: %s", exc)

    # P2 (audit 2026-04-28): resume_pending_agent_tasks() above drains
    # agent_retry_jobs, which is a different table from task_runs. A
    # process that crashed mid-CoordinationEngine run leaves a task_runs
    # row in 'pending' or 'running' forever — nothing reaps it. Sweep
    # those orphans now so they don't pile up across redeploys.
    try:
        await asyncio.wait_for(_reap_orphan_task_runs(), timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("Orphan task_runs sweep timed out (10s) — skipping")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Orphan task_runs sweep not started: %s", exc)

    # AgentPlanningEngine (Claude tool-use loop) retired. The deterministic
    # DeterministicPlanningEngine in clearledgr.core.planning_engine is the
    # only planning engine; it does not need skill registration at startup
    # because all actions are dispatched by CoordinationEngine._handlers
    # (populated at construction time, not via runtime registration).

    try:
        from clearledgr.services.erp_follow_on_reconciliation import (
            run_erp_follow_on_reconciliation_check,
        )

        checked = await asyncio.wait_for(run_erp_follow_on_reconciliation_check(), timeout=10.0)
        logger.info("ERP follow-on reconciliation check completed (%d items checked)", checked)
    except asyncio.TimeoutError:
        logger.warning("ERP follow-on reconciliation check timed out (10s) — skipping")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ERP follow-on reconciliation check not started: %s", exc)


async def _reap_orphan_task_runs() -> None:
    """Mark stale in-flight task_runs as failed with reason ``api_crash_orphan``.

    A task_run is considered orphaned when its ``status`` is still
    ``pending`` or ``running`` and its ``updated_at`` is older than
    :data:`_TASK_RUN_ORPHAN_THRESHOLD`. The threshold is chosen well
    past any legitimate single-task duration so we don't race
    in-flight rows from sibling workers.

    The sweep is fail-safe: if the DB is unavailable, parsing fails,
    or the threshold isn't met, the row is left alone for the next
    boot to retry.
    """

    def _do_sweep() -> Dict[str, int]:  # type: ignore[name-defined]
        from clearledgr.core.database import get_db

        db = get_db()
        rows = db.list_pending_task_runs(statuses=("pending", "running"))
        cutoff = datetime.now(timezone.utc) - _TASK_RUN_ORPHAN_THRESHOLD
        marked = 0
        skipped = 0
        for row in rows:
            updated_at_raw = row.get("updated_at") or row.get("created_at")
            if not updated_at_raw:
                skipped += 1
                continue
            try:
                # ISO-format strings persisted by _now() include tzinfo.
                updated_at = datetime.fromisoformat(str(updated_at_raw))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                skipped += 1
                continue
            if updated_at >= cutoff:
                # In-flight on another worker; leave alone.
                skipped += 1
                continue
            try:
                db.fail_task_run(
                    row["id"],
                    error="api_crash_orphan: task_run was in-flight when api stopped; no resume path. Re-trigger via the originating event if still relevant.",
                    retry_count=int(row.get("retry_count") or 0),
                )
                marked += 1
            except Exception as exc:
                logger.warning("[task_runs sweep] fail_task_run %s failed: %s", row.get("id"), exc)
        return {"marked": marked, "skipped": skipped, "total": len(rows)}

    result = await asyncio.to_thread(_do_sweep)
    logger.info(
        "Orphan task_runs sweep complete (marked_failed=%d in_flight_skipped=%d total_pending=%d)",
        result.get("marked", 0),
        result.get("skipped", 0),
        result.get("total", 0),
    )


def schedule_deferred_startup(app: Any) -> None:
    """Schedule deferred startup on the next event-loop turn.

    This avoids eager task execution during lifespan entry on runtimes that
    start tasks immediately, which can delay the server bind.
    """

    loop = asyncio.get_running_loop()

    def _launch() -> None:
        setattr(app.state, _DEFERRED_STARTUP_HANDLE_ATTR, None)
        task = asyncio.create_task(
            run_deferred_startup(app),
            name="clearledgr-deferred-startup",
        )
        setattr(app.state, _DEFERRED_STARTUP_TASK_ATTR, task)

    handle = loop.call_soon(_launch)
    setattr(app.state, _DEFERRED_STARTUP_HANDLE_ATTR, handle)
    setattr(app.state, _DEFERRED_STARTUP_TASK_ATTR, None)


async def cancel_deferred_startup(app: Any) -> None:
    handle = getattr(app.state, _DEFERRED_STARTUP_HANDLE_ATTR, None)
    if handle is not None:
        handle.cancel()
        setattr(app.state, _DEFERRED_STARTUP_HANDLE_ATTR, None)

    task = getattr(app.state, _DEFERRED_STARTUP_TASK_ATTR, None)
    if task is None:
        return
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task
    setattr(app.state, _DEFERRED_STARTUP_TASK_ATTR, None)
