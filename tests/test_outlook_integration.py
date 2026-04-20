"""Tests for Outlook / Microsoft 365 integration.

Covers:
- OutlookToken dataclass (expiry check)
- OutlookTokenStore (encrypt/decrypt, CRUD)
- OutlookAPIClient (auth, message listing, message parsing)
- OutlookAutopilot (start/stop, polling)
- OAuth routes (connect, callback, disconnect, status)
- Webhook validation handshake
- Outlook autopilot state DB methods
- Configuration validation
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "outlook.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    inst = db_module.get_db()
    inst.initialize()
    return inst


@pytest.fixture()
def outlook_env(monkeypatch):
    """Set Microsoft OAuth env vars for testing."""
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("MICROSOFT_REDIRECT_URI", "http://localhost:8010/outlook/callback")
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "common")


# ---------------------------------------------------------------------------
# Token tests
# ---------------------------------------------------------------------------

class TestOutlookToken:
    def test_not_expired(self):
        from clearledgr.services.outlook_api import OutlookToken
        token = OutlookToken(
            user_id="u1", access_token="at", refresh_token="rt",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            email="u@test.com",
        )
        assert token.is_expired() is False

    def test_expired(self):
        from clearledgr.services.outlook_api import OutlookToken
        token = OutlookToken(
            user_id="u1", access_token="at", refresh_token="rt",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            email="u@test.com",
        )
        assert token.is_expired() is True

    def test_near_expiry_considered_expired(self):
        from clearledgr.services.outlook_api import OutlookToken
        token = OutlookToken(
            user_id="u1", access_token="at", refresh_token="rt",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=3),
            email="u@test.com",
        )
        # Within 5-min buffer
        assert token.is_expired() is True


class TestOutlookTokenStore:
    def test_store_and_retrieve(self, db):
        from clearledgr.services.outlook_api import OutlookToken, OutlookTokenStore
        store = OutlookTokenStore()
        token = OutlookToken(
            user_id="store-test", access_token="secret-at", refresh_token="secret-rt",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            email="store@test.com",
        )
        store.store(token)
        retrieved = store.get("store-test")

        assert retrieved is not None
        assert retrieved.access_token == "secret-at"
        assert retrieved.refresh_token == "secret-rt"
        assert retrieved.email == "store@test.com"

    def test_delete(self, db):
        from clearledgr.services.outlook_api import OutlookToken, OutlookTokenStore
        store = OutlookTokenStore()
        token = OutlookToken(
            user_id="del-test", access_token="at", refresh_token="rt",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            email="del@test.com",
        )
        store.store(token)
        store.delete("del-test")
        assert store.get("del-test") is None

    def test_list_all(self, db):
        from clearledgr.services.outlook_api import OutlookToken, OutlookTokenStore
        store = OutlookTokenStore()
        for i in range(3):
            store.store(OutlookToken(
                user_id=f"list-{i}", access_token=f"at-{i}", refresh_token=f"rt-{i}",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                email=f"list{i}@test.com",
            ))
        tokens = store.list_all()
        assert len(tokens) == 3


# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------

class TestOutlookConfig:
    def test_is_configured_when_set(self, outlook_env):
        from clearledgr.services.outlook_api import is_outlook_configured
        assert is_outlook_configured() is True

    def test_is_not_configured_when_missing(self, monkeypatch):
        monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
        monkeypatch.delenv("MICROSOFT_CLIENT_SECRET", raising=False)
        from clearledgr.services.outlook_api import is_outlook_configured
        assert is_outlook_configured() is False

    def test_validate_raises_on_missing(self, monkeypatch):
        monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
        from clearledgr.services.outlook_api import validate_microsoft_oauth_config
        with pytest.raises(ValueError, match="MICROSOFT_CLIENT_ID"):
            validate_microsoft_oauth_config()

    def test_generate_auth_url(self, outlook_env):
        from clearledgr.services.outlook_api import generate_auth_url
        url = generate_auth_url(state="test-state")
        assert "login.microsoftonline.com" in url
        assert "test-client-id" in url
        assert "test-state" in url
        assert "Mail.Read" in url


# ---------------------------------------------------------------------------
# API client tests
# ---------------------------------------------------------------------------

class TestOutlookAPIClient:
    def test_ensure_authenticated_no_token(self, db):
        from clearledgr.services.outlook_api import OutlookAPIClient
        client = OutlookAPIClient("nonexistent-user")
        result = asyncio.run(client.ensure_authenticated())
        assert result is False

    def test_parse_message(self):
        from clearledgr.services.outlook_api import OutlookAPIClient
        data = {
            "id": "msg-123",
            "conversationId": "conv-456",
            "subject": "Invoice #1234",
            "from": {"emailAddress": {"address": "vendor@example.com"}},
            "toRecipients": [{"emailAddress": {"address": "ap@company.com"}}],
            "receivedDateTime": "2026-04-03T10:00:00Z",
            "bodyPreview": "Please find attached invoice...",
            "body": {"contentType": "html", "content": "<p>Invoice body</p>"},
            "hasAttachments": True,
            "categories": ["Clearledgr/Processing"],
            "attachments": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "id": "att-1",
                    "name": "invoice.pdf",
                    "contentType": "application/pdf",
                    "size": 12345,
                },
            ],
        }
        msg = OutlookAPIClient._parse_message(data)

        assert msg.id == "msg-123"
        assert msg.conversation_id == "conv-456"
        assert msg.subject == "Invoice #1234"
        assert msg.sender == "vendor@example.com"
        assert msg.recipient == "ap@company.com"
        assert msg.has_attachments is True
        assert len(msg.attachments) == 1
        assert msg.attachments[0]["name"] == "invoice.pdf"
        assert msg.body_html == "<p>Invoice body</p>"


# ---------------------------------------------------------------------------
# Autopilot state DB tests
# ---------------------------------------------------------------------------

class TestOutlookAutopilotState:
    def test_save_and_retrieve(self, db):
        db.save_outlook_autopilot_state(
            user_id="ap-user",
            email="ap@test.com",
            last_scan_at="2026-04-03T10:00:00+00:00",
        )
        state = db.get_outlook_autopilot_state("ap-user")
        assert state is not None
        assert state["email"] == "ap@test.com"
        assert state["last_scan_at"] == "2026-04-03T10:00:00+00:00"

    def test_upsert_updates_existing(self, db):
        db.save_outlook_autopilot_state(user_id="upd-user", email="v1@test.com")
        db.save_outlook_autopilot_state(user_id="upd-user", email="v2@test.com", last_error="auth_failed")
        state = db.get_outlook_autopilot_state("upd-user")
        assert state["email"] == "v2@test.com"
        assert state["last_error"] == "auth_failed"

    def test_list_all(self, db):
        db.save_outlook_autopilot_state(user_id="list-1", email="a@t.com")
        db.save_outlook_autopilot_state(user_id="list-2", email="b@t.com")
        states = db.list_outlook_autopilot_states()
        assert len(states) == 2

    def test_missing_returns_none(self, db):
        assert db.get_outlook_autopilot_state("nonexistent") is None


# ---------------------------------------------------------------------------
# Autopilot class tests
# ---------------------------------------------------------------------------

class TestOutlookAutopilot:
    def test_disabled_when_not_configured(self, db, monkeypatch):
        monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
        monkeypatch.delenv("MICROSOFT_CLIENT_SECRET", raising=False)
        from clearledgr.services.outlook_autopilot import OutlookAutopilot
        ap = OutlookAutopilot()
        assert ap.enabled is False

    def test_start_when_disabled(self, db, monkeypatch):
        monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
        from clearledgr.services.outlook_autopilot import OutlookAutopilot
        ap = OutlookAutopilot()
        asyncio.run(ap.start())
        assert ap.get_status()["state"] == "disabled"

    def test_tick_with_no_tokens(self, db, outlook_env):
        from clearledgr.services.outlook_autopilot import OutlookAutopilot
        ap = OutlookAutopilot()
        asyncio.run(ap._tick())
        assert ap.get_status()["detail"] == "no_tokens"


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

class TestOutlookRoutes:
    @pytest.fixture()
    def client(self, db, outlook_env, monkeypatch):
        # §12 #6 — Outlook is disabled in V1 by default. These route
        # tests exercise the post-V1 behaviour where the flag is on,
        # so enable it explicitly for the duration of this class.
        monkeypatch.setenv("FEATURE_OUTLOOK_ENABLED", "true")

        from main import app
        from clearledgr.core.auth import TokenData, get_current_user

        def _fake_user():
            return TokenData(
                user_id="route-user",
                email="route@test.com",
                organization_id="default",
                role="owner",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        app.dependency_overrides[get_current_user] = _fake_user
        from fastapi.testclient import TestClient
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_connect_start(self, client):
        resp = client.get("/outlook/connect/start")
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_url" in data
        assert "login.microsoftonline.com" in data["auth_url"]

    def test_status_not_connected(self, client, db):
        resp = client.get("/outlook/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False

    def test_disconnect(self, client, db):
        resp = client.post("/outlook/disconnect")
        assert resp.status_code == 200
        assert resp.json()["status"] == "disconnected"

    def test_webhook_validation_handshake(self, client):
        resp = client.post("/outlook/webhook?validationToken=test-token-123")
        assert resp.status_code == 200
        assert resp.text == "test-token-123"

    def test_webhook_notification(self, client, monkeypatch):
        # Outlook webhook is now fail-closed: without a configured
        # secret it returns 503, and notifications whose clientState
        # doesn't match the secret are silently dropped (still 202).
        # Configure a secret and send a matching clientState so the
        # happy-path assertion still exercises a legitimate callsite.
        monkeypatch.setenv("OUTLOOK_WEBHOOK_SECRET", "outlook-secret-abc")
        resp = client.post("/outlook/webhook", json={
            "value": [
                {
                    "changeType": "created",
                    "resource": "me/mailFolders('Inbox')/messages/msg-1",
                    "clientState": "outlook-secret-abc",
                }
            ]
        })
        assert resp.status_code == 202

    def test_webhook_notification_unset_secret_is_fail_closed(self, client, monkeypatch):
        monkeypatch.delenv("OUTLOOK_WEBHOOK_SECRET", raising=False)
        resp = client.post("/outlook/webhook", json={
            "value": [
                {
                    "changeType": "created",
                    "resource": "me/mailFolders('Inbox')/messages/msg-1",
                    "clientState": "",
                }
            ]
        })
        assert resp.status_code == 503
        assert resp.json()["error"] == "webhook_not_configured"
