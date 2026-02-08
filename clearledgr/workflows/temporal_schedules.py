"""
Temporal Schedule Manager for Clearledgr v1 (Autonomous Edition)

Implements autonomous scheduling from product_spec_updated.md:
- Daily SAP GL sync at 8:00am
- Daily reconciliation at 9:00am
- Daily Slack summary notifications

Supports daily, weekly, and monthly schedules.
"""
from __future__ import annotations

import os
import asyncio
from datetime import timedelta
from typing import Any, Dict, Optional


class TemporalScheduleManager:
    def __init__(self) -> None:
        self.address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
        self.namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
        self.task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "clearledgr-v1")

    def ensure_schedule(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create or update a workflow schedule from a simple payload.
        
        Per product_spec_updated.md, supports:
        - sap_sync: Daily SAP GL sync at 8:00am
        - reconciliation: Daily reconciliation at 9:00am
        - daily_summary: Daily Slack summary after reconciliation
        
        Payload may include:
        - schedule_type: daily/weekly/monthly/sap_sync
        - workflow: reconciliation/sap_sync/daily_summary
        - cron: override cron string
        - entity_id: org identifier
        - sheet_id/period_start/period_end/etc passed through to workflow
        """
        schedule_type = payload.get("schedule_type") or payload.get("frequency") or payload.get("schedule") or "daily"
        cron = payload.get("cron") or cron_from_schedule_type(schedule_type, payload.get("time_of_day"))
        if not cron:
            return {"status": "skipped", "reason": "missing cron expression"}

        schedule_id = payload.get("schedule_id") or f"reconciliation-{payload.get('entity_id','default')}-{schedule_type}"
        workflow_to_start = payload.get("workflow") or "reconciliation"
        workflow_run = None
        
        # Map workflow name to workflow class
        if workflow_to_start == "reconciliation":
            workflow_run = ScheduledReconciliationWorkflowTemporal.run
        elif workflow_to_start == "sap_sync":
            try:
                from clearledgr.workflows.sap_sync import DailySAPSyncWorkflow
                workflow_run = DailySAPSyncWorkflow.run
                if not schedule_id.startswith("sap-sync"):
                    schedule_id = f"sap-sync-{payload.get('entity_id', 'default')}"
            except ImportError:
                return {"status": "skipped", "reason": "SAP sync workflow not available"}
        elif workflow_to_start == "daily_summary":
            try:
                from clearledgr.workflows.temporal_workflows import DailySlackSummaryWorkflowTemporal
                workflow_run = DailySlackSummaryWorkflowTemporal.run
                if not schedule_id.startswith("daily-summary"):
                    schedule_id = f"daily-summary-{schedule_type}"
            except ImportError:
                return {"status": "skipped", "reason": "Daily summary workflow not available"}
        
        if workflow_run is None:
            return {"status": "skipped", "reason": "unknown workflow"}

        try:
            return asyncio.run(self.upsert_reconciliation_schedule(schedule_id, payload, cron, workflow_run))
        except RuntimeError:
            # If already inside an event loop (e.g., FastAPI), create a new loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self.upsert_reconciliation_schedule(schedule_id, payload, cron, workflow_run))
            finally:
                loop.close()

    async def upsert_reconciliation_schedule(
        self,
        schedule_id: str,
        payload: Dict[str, Any],
        cron: str,
        workflow_run,
    ) -> Dict[str, Any]:
        client, schedule_module = await _load_schedule_client(self.address, self.namespace)
        if not client or not schedule_module:
            return {"status": "skipped", "reason": "Temporal schedule API unavailable"}

        Schedule = schedule_module["Schedule"]
        ScheduleSpec = schedule_module["ScheduleSpec"]
        ScheduleActionStartWorkflow = schedule_module["ScheduleActionStartWorkflow"]
        ScheduleState = schedule_module.get("ScheduleState")

        schedule_kwargs = {
            "action": ScheduleActionStartWorkflow(
                workflow_run,
                payload,
                id=schedule_id,
                task_queue=self.task_queue,
            ),
            "spec": ScheduleSpec(cron_expressions=[cron]),
        }
        if ScheduleState:
            schedule_kwargs["state"] = ScheduleState(note="Clearledgr reconciliation schedule")

        schedule = Schedule(**schedule_kwargs)

        try:
            handle = await client.create_schedule(schedule_id, schedule, trigger_immediately=False)
            return {"status": "created", "schedule_id": schedule_id, "handle": str(handle)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "exists", "schedule_id": schedule_id, "error": str(exc)}


def cron_from_schedule_type(schedule_type: str, time_of_day: str | None = None) -> Optional[str]:
    """
    Convert schedule type to cron expression.
    
    Per product_spec_updated.md:
    - sap_sync: Daily at 8:00am (before reconciliation)
    - daily: Reconciliation at 9:00am
    - daily_summary: After reconciliation (~9:07am)
    """
    minute, hour = parse_time_of_day(time_of_day)
    
    schedule_map = {
        # SAP sync runs at 8:00am (1 hour before reconciliation)
        "sap_sync": f"0 8 * * *",
        # Daily reconciliation at configured time (default 9:00am)
        "daily": f"{minute} {hour} * * *",
        # Weekly on Mondays
        "weekly": f"{minute} {hour} * * 1",
        # Monthly on 1st
        "monthly": f"{minute} {hour} 1 * *",
        # Daily summary 7 minutes after reconciliation
        "daily_summary": f"{minute + 7 if minute + 7 < 60 else 7} {hour if minute + 7 < 60 else hour + 1} * * *",
    }
    return schedule_map.get(schedule_type)


def parse_time_of_day(time_str: str | None) -> tuple[int, int]:
    """Return (minute, hour) with configurable default (env: CLEARLEDGR_DEFAULT_SCHEDULE_TIME, default 09:00)."""
    if not time_str:
        time_str = os.getenv("CLEARLEDGR_DEFAULT_SCHEDULE_TIME", "09:00")
    try:
        parts = time_str.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        hour = max(0, min(23, hour))
        minute = max(0, min(59, minute))
        return minute, hour
    except Exception:  # noqa: BLE001
        return 0, 2


async def _load_schedule_client(address: str, namespace: str):
    try:
        from temporalio.client import Client  # type: ignore
    except Exception:  # noqa: BLE001
        return None, None

    schedule_module = {}
    try:
        from temporalio.client import Schedule, ScheduleSpec, ScheduleActionStartWorkflow, ScheduleState  # type: ignore

        schedule_module = {
            "Schedule": Schedule,
            "ScheduleSpec": ScheduleSpec,
            "ScheduleActionStartWorkflow": ScheduleActionStartWorkflow,
            "ScheduleState": ScheduleState,
        }
    except Exception:  # noqa: BLE001
        return None, None

    client = await Client.connect(address, namespace=namespace)
    return client, schedule_module
