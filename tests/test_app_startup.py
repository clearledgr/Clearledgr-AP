from __future__ import annotations

import asyncio
from types import SimpleNamespace

from clearledgr.services import app_startup


def test_schedule_deferred_startup_launches_on_next_loop_turn(monkeypatch):
    async def _run() -> None:
        app = SimpleNamespace(state=SimpleNamespace())
        observed: list[str] = []

        async def fake_run(target):
            observed.append("started")
            await asyncio.sleep(0)
            observed.append("completed")

        monkeypatch.setattr(app_startup, "run_deferred_startup", fake_run)

        app_startup.schedule_deferred_startup(app)
        assert getattr(app.state, "deferred_startup_task", None) is None

        await asyncio.sleep(0)
        task = getattr(app.state, "deferred_startup_task", None)
        assert task is not None

        await asyncio.sleep(0)
        assert observed == ["started"]

        await app_startup.cancel_deferred_startup(app)
        assert getattr(app.state, "deferred_startup_task", None) is None

    asyncio.run(_run())


def test_cancel_deferred_startup_before_launch_clears_pending_handle(monkeypatch):
    async def _run() -> None:
        app = SimpleNamespace(state=SimpleNamespace())
        observed: list[str] = []

        async def fake_run(target):
            observed.append("started")

        monkeypatch.setattr(app_startup, "run_deferred_startup", fake_run)

        app_startup.schedule_deferred_startup(app)
        await app_startup.cancel_deferred_startup(app)
        await asyncio.sleep(0)

        assert observed == []
        assert getattr(app.state, "deferred_startup_task", None) is None

    asyncio.run(_run())
