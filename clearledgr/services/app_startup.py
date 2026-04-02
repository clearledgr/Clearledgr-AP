from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from clearledgr.services.logging import logger

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
        from clearledgr.services.agent_background import start_agent_background

        await asyncio.wait_for(start_agent_background(app), timeout=10.0)
        logger.info("Agent background intelligence started")
    except asyncio.TimeoutError:
        logger.warning("Agent background startup timed out (10s) — skipping")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent background not started: %s", exc)

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

    try:
        from clearledgr.core.agent_runtime import get_planning_engine
        from clearledgr.core.skills.ap_skill import APSkill
        from clearledgr.core.skills.compound_skill import CompoundSkill

        planner = get_planning_engine()
        planner.register_skill(APSkill("default"))
        planner.register_skill(CompoundSkill("default"))
        logger.info("Planning engine skills registered (APSkill, CompoundSkill)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Planning engine skill registration failed: %s", exc)

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
