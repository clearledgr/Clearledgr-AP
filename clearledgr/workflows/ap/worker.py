"""Temporal worker bootstrap for AP workflows."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

try:  # pragma: no cover - integration runtime
    from temporalio.client import Client
    from temporalio.worker import Worker
except Exception:  # pragma: no cover
    Client = None
    Worker = None

from .workflow import APTemporalWorkflow
from .activities import (
    append_audit_activity,
    request_approval_activity,
    approve_activity,
    reject_activity,
    retry_post_activity,
    dispatch_browser_commands_activity,
    wait_browser_results_activity,
)


class APTemporalWorkerRuntime:
    def __init__(self) -> None:
        self.enabled = str(os.getenv("AP_TEMPORAL_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"}
        self.start_worker = str(os.getenv("AP_TEMPORAL_START_WORKER", "false")).strip().lower() in {"1", "true", "yes", "on"}
        self.address = os.getenv("TEMPORAL_ADDRESS", "").strip()
        self.namespace = os.getenv("TEMPORAL_NAMESPACE", "default").strip() or "default"
        self.task_queue = os.getenv("TEMPORAL_AP_TASK_QUEUE", "clearledgr-ap")
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if not self.enabled or not self.start_worker:
            logger.info("AP Temporal worker disabled")
            return
        if not self.address:
            logger.warning("AP Temporal worker not started: TEMPORAL_ADDRESS missing")
            return
        if Client is None or Worker is None:
            logger.warning("AP Temporal worker not started: temporalio not installed")
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        client = await Client.connect(self.address, namespace=self.namespace)
        worker = Worker(
            client,
            task_queue=self.task_queue,
            workflows=[APTemporalWorkflow],
            activities=[
                append_audit_activity,
                request_approval_activity,
                approve_activity,
                reject_activity,
                retry_post_activity,
                dispatch_browser_commands_activity,
                wait_browser_results_activity,
            ],
        )
        logger.info("Starting AP Temporal worker on task queue %s", self.task_queue)
        await worker.run()


_RUNTIME: Optional[APTemporalWorkerRuntime] = None


def get_ap_temporal_worker_runtime() -> APTemporalWorkerRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = APTemporalWorkerRuntime()
    return _RUNTIME
