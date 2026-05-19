"""Tests for per-user notification preferences — Module 11.

Pinned by these tests:

  - Schema discovery: GET /schema returns the canonical channels +
    defaults so the frontend can render every available toggle.
  - Read returns defaults for new users (no DB-side prefs yet).
  - Write merges into the existing prefs blob — toggling slack does
    not reset email.
  - Unknown channels and unknown event types in the patch payload
    are silently dropped (defense-in-depth scrub).
  - should_notify() respects user's explicit opt-out, defaults to
    True for known events, defaults to True for unknown events
    (over-notify is safer than silent drop), False for unknown
    channels.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.api import notification_preferences as routes  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.auth import get_current_user  # noqa: E402
from solden.services import notification_preferences as svc  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    return inst


@pytest.fixture()
def user_id(db):
    user = db.create_user(
        email="leader@orga.com",
        name="Leader",
        organization_id="orgA",
        role="user",
    )
    return str(user["id"])


@pytest.fixture()
def client(db, user_id):
    app = FastAPI()
    app.include_router(routes.router)
    fake = SimpleNamespace(
        user_id=user_id,
        email="leader@orga.com",
        organization_id="orgA",
        role="user",
    )
    app.dependency_overrides[get_current_user] = lambda: fake
    return TestClient(app)


# ─── Tests: schema endpoint ─────────────────────────────────────────


class TestSchemaEndpoint:
    def test_schema_returns_canonical_channels_and_defaults(self, client):
        resp = client.get("/api/workspace/notification-preferences/schema")
        assert resp.status_code == 200
        body = resp.json()
        channels = set(body["channels"])
        assert channels == {"email", "slack", "in_app"}
        assert "exception_raised" in body["defaults"]["email"]
        assert "approval_requested" in body["defaults"]["slack"]


# ─── Tests: GET prefs ───────────────────────────────────────────────


class TestGetPrefs:
    def test_new_user_gets_defaults(self, client):
        resp = client.get("/api/workspace/notification-preferences")
        assert resp.status_code == 200
        body = resp.json()
        # Default for email.exception_raised is True per the canonical schema.
        assert body["preferences"]["email"]["exception_raised"] is True
        # Default for slack.approval_decided is True (vs email which is False).
        assert body["preferences"]["slack"]["approval_decided"] is True


# ─── Tests: PATCH prefs ─────────────────────────────────────────────


class TestPatchPrefs:
    def test_patch_email_toggles_does_not_reset_slack(self, client):
        resp = client.patch(
            "/api/workspace/notification-preferences",
            json={"email": {"exception_raised": False}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["preferences"]["email"]["exception_raised"] is False
        # Slack defaults still intact
        assert body["preferences"]["slack"]["approval_requested"] is True

    def test_unknown_channel_silently_dropped(self, client):
        # Pydantic ignores extra keys by default; the result is the
        # canonical-3-channel response with no `sms` smuggled in.
        resp = client.patch(
            "/api/workspace/notification-preferences",
            json={"sms": {"exception_raised": True}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "sms" not in body["preferences"]
        assert set(body["preferences"].keys()) == {"email", "slack", "in_app"}

    def test_unknown_event_silently_dropped(self, client):
        resp = client.patch(
            "/api/workspace/notification-preferences",
            json={"email": {"frobnicate": True}},
        )
        assert resp.status_code == 200
        body = resp.json()
        # `frobnicate` not present in the saved prefs.
        assert "frobnicate" not in body["preferences"]["email"]

    def test_persisted_prefs_survive_re_read(self, client):
        client.patch(
            "/api/workspace/notification-preferences",
            json={"slack": {"approval_decided": False}},
        )
        resp = client.get("/api/workspace/notification-preferences")
        assert resp.json()["preferences"]["slack"]["approval_decided"] is False


# ─── Tests: should_notify gate ──────────────────────────────────────


class TestShouldNotify:
    def test_default_true_when_user_has_no_prefs(self, db, user_id):
        assert svc.should_notify(
            db, user_id, channel="email", event="exception_raised",
        ) is True

    def test_returns_user_explicit_opt_out(self, db, user_id):
        svc.save_notification_prefs(db, user_id, {
            "email": {"exception_raised": False},
        })
        assert svc.should_notify(
            db, user_id, channel="email", event="exception_raised",
        ) is False

    def test_unknown_channel_returns_false(self, db, user_id):
        assert svc.should_notify(
            db, user_id, channel="sms", event="exception_raised",
        ) is False

    def test_unknown_event_returns_true_default_open(self, db, user_id):
        # Over-notify is safer than silent drop for new code paths
        # that haven't been added to the schema yet.
        assert svc.should_notify(
            db, user_id, channel="email", event="some_new_event",
        ) is True

    def test_no_user_id_returns_default_true(self, db):
        assert svc.should_notify(
            db, "", channel="email", event="exception_raised",
        ) is True


# ─── Tests: schema stability ────────────────────────────────────────


class TestSchemaStability:
    def test_schema_lock(self):
        # Lock the canonical channels — adding a new one is a deliberate
        # contract change, not a silent expansion.
        assert svc.VALID_CHANNELS == frozenset({"email", "slack", "in_app"})
        # Each channel has at least one toggle.
        for channel in svc.VALID_CHANNELS:
            assert len(svc.DEFAULT_NOTIFICATION_PREFS[channel]) >= 1
