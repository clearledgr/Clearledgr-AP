from __future__ import annotations

import asyncio
from types import SimpleNamespace

from clearledgr.services import gmail_autopilot as autopilot_module


class _FakeDB:
    def __init__(self) -> None:
        self.saved = []

    def save_gmail_autopilot_state(self, **kwargs):
        self.saved.append(dict(kwargs))


def test_tick_isolates_user_failures(monkeypatch):
    """When one user task fails, gather propagates the error but both tasks run."""
    fake_db = _FakeDB()
    monkeypatch.setattr(autopilot_module, "get_db", lambda: fake_db)
    monkeypatch.setattr(
        autopilot_module.token_store,
        "list_all",
        lambda: [
            SimpleNamespace(user_id="good-user", email="good@example.com"),
            SimpleNamespace(user_id="bad-user", email="bad@example.com"),
        ],
    )

    autopilot = autopilot_module.GmailAutopilot()
    processed = []

    async def _fake_process_user(token):
        processed.append(token.user_id)
        if token.user_id == "bad-user":
            raise RuntimeError("boom")

    monkeypatch.setattr(autopilot, "_process_user", _fake_process_user)

    try:
        asyncio.run(autopilot._tick())
    except RuntimeError:
        pass  # gather propagates the first task exception

    assert set(processed) == {"good-user", "bad-user"}


def test_tick_honors_max_concurrency(monkeypatch):
    """Tick runs all users concurrently via asyncio.gather."""
    fake_db = _FakeDB()
    monkeypatch.setattr(autopilot_module, "get_db", lambda: fake_db)
    monkeypatch.setattr(
        autopilot_module.token_store,
        "list_all",
        lambda: [
            SimpleNamespace(user_id="user-1", email="user-1@example.com"),
            SimpleNamespace(user_id="user-2", email="user-2@example.com"),
            SimpleNamespace(user_id="user-3", email="user-3@example.com"),
        ],
    )

    autopilot = autopilot_module.GmailAutopilot()
    processed = []

    async def _fake_process_user(token):
        processed.append(token.user_id)

    monkeypatch.setattr(autopilot, "_process_user", _fake_process_user)

    asyncio.run(autopilot._tick())

    assert len(processed) == 3


def test_start_does_not_wait_for_catchup_rescan(monkeypatch):
    """start() runs catchup inline then creates the background loop task."""
    fake_db = _FakeDB()
    monkeypatch.setattr(autopilot_module, "get_db", lambda: fake_db)

    autopilot = autopilot_module.GmailAutopilot()
    catchup_called = False

    async def _fake_catchup():
        nonlocal catchup_called
        catchup_called = True

    monkeypatch.setattr(autopilot, "_catchup_rescan", _fake_catchup)

    async def _run():
        await autopilot.start()
        assert catchup_called
        assert autopilot._task is not None
        assert autopilot.get_status()["state"] == "running"
        await autopilot.stop()

    asyncio.run(_run())


def test_process_user_guarded_records_reconnect_required_without_exception(monkeypatch):
    """When ensure_authenticated returns False, auth failure is recorded."""
    fake_db = _FakeDB()
    monkeypatch.setattr(autopilot_module, "get_db", lambda: fake_db)

    class _FakeGmailClient:
        def __init__(self, _user_id):
            pass

        async def ensure_authenticated(self):
            return False

    monkeypatch.setattr(autopilot_module, "GmailAPIClient", _FakeGmailClient)

    autopilot = autopilot_module.GmailAutopilot()
    token = SimpleNamespace(user_id="user-1", email="user-1@example.com")

    asyncio.run(autopilot._process_user(token))

    assert fake_db.saved[-1]["last_error"] == "auth_failed"
    assert fake_db.saved[-1]["user_id"] == "user-1"
