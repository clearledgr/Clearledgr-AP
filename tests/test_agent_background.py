from __future__ import annotations

import asyncio

from solden.services import agent_background as agent_background_module


def test_check_approval_timeouts_self_isolates_org_failure(monkeypatch):
    """_check_approval_timeouts wraps its whole body in try/except, so a failure
    inside one org's run (here: a broken get_db) is swallowed and never raised.

    This is the real per-org isolation the background loop relies on at the
    `for org_id in org_ids: await _check_approval_timeouts(org_id)` call site:
    each org self-contains its errors so the next org still runs.
    """
    from solden.core import database as database_module

    def _broken_get_db():
        raise RuntimeError("db down for this org")

    monkeypatch.setattr(database_module, "get_db", _broken_get_db)

    # Must return normally (None), not raise, despite the internal failure.
    result = asyncio.run(agent_background_module._check_approval_timeouts("org-a"))
    assert result is None


def test_check_overdue_tasks_continues_when_one_org_fails(monkeypatch):
    """Per-org isolation: if _collect raises for the first org, _check_overdue
    _tasks logs it and STILL delivers the summary for the remaining orgs."""
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

    import solden.services.task_scheduler as task_scheduler_module
    import solden.services.slack_notifications as slack_notifications_module

    monkeypatch.setattr(task_scheduler_module, "should_send_reminder", lambda *args, **kwargs: True)
    monkeypatch.setattr(task_scheduler_module, "log_reminder", lambda *args, **kwargs: None)
    monkeypatch.setattr(slack_notifications_module, "send_overdue_summary", _send_summary)

    asyncio.run(agent_background_module._check_overdue_tasks())

    # org-a fails but is isolated; org-b's summary is still delivered.
    assert delivered == [("org-b", 1, 0)]
