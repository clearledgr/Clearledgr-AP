"""Task scheduler / notifications tenant isolation.

The scheduler used to scan every tenant's email_tasks and send reminders to
one global #finance channel via the legacy task_notifications path. These
tests pin the per-org behaviour:

  - send_task_notification routes to the task's own org Slack (per-org token
    + channel) and skips orgs with no connected Slack (never a global channel);
  - the overdue scan is scoped to the org it's run for.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402
from solden.services import task_notifications  # noqa: E402

# task_scheduler / email_tasks run a module-level get_db() at import, so they
# are imported lazily inside the test that needs a live DB (after the test DB
# env is configured).


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme")
    inst.ensure_organization("orgB", organization_name="Beta")
    return inst


# ─── Per-org Slack routing ──────────────────────────────────────────


def test_task_notification_routes_to_org_slack(monkeypatch):
    sent = {}

    def fake_resolve(org_id):
        return {
            "connected": True,
            "bot_token": f"tok-{org_id}",
            "approval_channel": f"#ap-{org_id}",
        }

    async def fake_send(channel, blocks, token=None):
        sent["channel"] = channel
        sent["token"] = token
        return {"ok": True}

    monkeypatch.setattr("solden.services.slack_api.resolve_slack_runtime", fake_resolve)
    # send_slack_message may be unbound when ui.slack.app can't import in the
    # test env; raising=False creates it for the test.
    monkeypatch.setattr(task_notifications, "send_slack_message", fake_send, raising=False)
    monkeypatch.setattr(task_notifications, "SLACK_AVAILABLE", True)

    ok = task_notifications.send_task_notification(
        "overdue", {"title": "T", "organization_id": "orgA", "task_id": "t1"}
    )
    assert ok is True
    # Routed to orgA's own token + channel — never a shared global channel.
    assert sent["token"] == "tok-orgA"
    assert sent["channel"] == "#ap-orgA"


def test_task_notification_skips_org_without_slack(monkeypatch):
    def fake_resolve(org_id):
        return {"connected": False, "bot_token": None, "approval_channel": "#x"}

    monkeypatch.setattr("solden.services.slack_api.resolve_slack_runtime", fake_resolve)
    monkeypatch.setattr(task_notifications, "SLACK_AVAILABLE", True)

    ok = task_notifications.send_task_notification(
        "overdue", {"title": "T", "organization_id": "orgB", "task_id": "t2"}
    )
    assert ok is False


# ─── Org-scoped scan ────────────────────────────────────────────────


def test_overdue_check_is_org_scoped(db, monkeypatch):
    from solden.services import task_scheduler
    from solden.services.email_tasks import create_task_from_email

    # No Slack for any org → reminder sends are no-ops; we only assert the scan scope.
    monkeypatch.setattr(
        "solden.services.slack_api.resolve_slack_runtime",
        lambda org_id: {"connected": False, "bot_token": None},
    )
    past = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    create_task_from_email(
        email_id="eA", email_subject="s", email_sender="x", thread_id="tA",
        created_by="u", task_type="review", due_date=past, organization_id="orgA",
    )
    create_task_from_email(
        email_id="eB", email_subject="s", email_sender="x", thread_id="tB",
        created_by="u", task_type="review", due_date=past, organization_id="orgB",
    )

    res = task_scheduler.run_overdue_check("orgA")
    assert res["overdue_tasks"] == 1
