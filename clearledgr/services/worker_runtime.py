from __future__ import annotations

import asyncio
import os
import signal
from types import SimpleNamespace

from dotenv import load_dotenv

from clearledgr.services.app_startup import run_deferred_startup
from clearledgr.services.logging import logger

load_dotenv()


def _process_role() -> str:
    raw = str(os.getenv("CLEARLEDGR_PROCESS_ROLE", "worker") or "").strip().lower()
    if raw in {"api"}:
        return "web"
    if raw in {"web", "worker", "all"}:
        return raw
    return "worker"


async def _shutdown(app) -> None:
    try:
        from clearledgr.services.gmail_autopilot import stop_gmail_autopilot

        await stop_gmail_autopilot(app)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Worker shutdown: Gmail autopilot stop failed: %s", exc)

    try:
        from clearledgr.services.outlook_autopilot import stop_outlook_autopilot

        await stop_outlook_autopilot(app)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Worker shutdown: Outlook autopilot stop failed: %s", exc)

    try:
        from clearledgr.services.agent_background import stop_agent_background

        await stop_agent_background()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Worker shutdown: Agent background stop failed: %s", exc)


async def run_worker() -> None:
    role = _process_role()
    if role == "web":
        raise RuntimeError("CLEARLEDGR_PROCESS_ROLE=web cannot run worker_runtime")

    app = SimpleNamespace(state=SimpleNamespace())
    await run_deferred_startup(app)
    logger.info("Clearledgr worker runtime started (role=%s)", role)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await stop_event.wait()
    await _shutdown(app)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
