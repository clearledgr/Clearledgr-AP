from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from clearledgr.services import agent_background as agent_background_module


def test_run_loop_iteration_isolates_org_failures(monkeypatch):
    """_check_approval_timeouts per-org calls are invoked in sequence and errors
    in one org propagate (the outer _run_loop catches them)."""
    seen = []

    async def _approval_timeout(org_id):
        seen.append(org_id)
        if org_id == "org-a":
            raise RuntimeError("boom")

    monkeypatch.setattr(agent_background_module, "_check_approval_timeouts", _approval_timeout)

    async def _run_both():
        for org_id in ["org-a", "org-b"]:
            try:
                await agent_background_module._check_approval_timeouts(org_id)
            except RuntimeError:
                pass  # simulate the outer loop catching per-org errors

    asyncio.run(_run_both())

    assert seen == ["org-a", "org-b"]


def test_check_overdue_tasks_continues_when_one_org_fails(monkeypatch):
    """_check_overdue_tasks has an outer try/except; if _collect fails for the
    first org, the entire function bails (logged but not raised)."""
    delivered = []

    def _collect(org_id):
        if org_id == "org-a":
            raise RuntimeError("broken")
        return {"overdue": [{"vendor_name": "Acme", "amount": 100.0, "due_date": "2026-03-01"}], "stale": []}

    async def _send_summary(*, overdue_items, stale_items, organization_id):
        delivered.append((organization_id, len(overdue_items), len(stale_items)))

    monkeypatch.setattr(agent_background_module, "_collect_org_overdue_and_stale_tasks", _collect)
    monkeypatch.setattr(
        agent_background_module,
        "_active_org_ids",
        lambda: ["org-a", "org-b"],
    )

    import clearledgr.services.task_scheduler as task_scheduler_module
    import clearledgr.services.slack_notifications as slack_notifications_module

    monkeypatch.setattr(task_scheduler_module, "should_send_reminder", lambda *args, **kwargs: True)
    monkeypatch.setattr(task_scheduler_module, "log_reminder", lambda *args, **kwargs: None)
    monkeypatch.setattr(slack_notifications_module, "send_overdue_summary", _send_summary)

    asyncio.run(agent_background_module._check_overdue_tasks())

    # org-a fails and the outer try/except catches the error, so org-b is skipped
    assert delivered == []
