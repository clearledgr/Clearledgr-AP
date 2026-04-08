"""Tests for outgoing webhook system.

Covers:
- Webhook subscription CRUD (store layer)
- HMAC signature computation
- Webhook delivery (mocked HTTP)
- Event emission with subscription matching
- State transition webhook hook
- Retry via notification queue
- API endpoints (list, create, delete, test)
- Wildcard subscription matching
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData
from clearledgr.services.webhook_delivery import (
    compute_signature,
    deliver_webhook,
    emit_webhook_event,
    emit_state_change_webhook,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "webhooks.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    inst = db_module.get_db()
    inst.initialize()
    return inst


@pytest.fixture()
def client(db):
    from main import app
    from clearledgr.api import workspace_shell as ws_module

    def _fake_user():
        return TokenData(
            user_id="wh-user",
            email="wh@example.com",
            organization_id="default",
            role="owner",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[ws_module.get_current_user] = _fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ws_module.get_current_user, None)


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------

class TestWebhookStore:
    def test_create_and_list(self, db):
        sub = db.create_webhook_subscription(
            organization_id="default",
            url="https://example.com/hook",
            event_types=["invoice.approved", "invoice.posted_to_erp"],
            secret="s3cret",
            description="Test hook",
        )
        assert sub["id"].startswith("wh_")
        assert sub["url"] == "https://example.com/hook"
        assert sub["event_types"] == ["invoice.approved", "invoice.posted_to_erp"]

        subs = db.list_webhook_subscriptions("default")
        assert len(subs) == 1
        assert subs[0]["is_active"] is True

    def test_get_by_id(self, db):
        sub = db.create_webhook_subscription("default", "https://a.com/h", ["*"])
        found = db.get_webhook_subscription(sub["id"])
        assert found is not None
        assert found["url"] == "https://a.com/h"

    def test_delete(self, db):
        sub = db.create_webhook_subscription("default", "https://b.com/h", ["*"])
        assert db.delete_webhook_subscription(sub["id"]) is True
        assert db.get_webhook_subscription(sub["id"]) is None

    def test_update(self, db):
        sub = db.create_webhook_subscription("default", "https://c.com/h", ["invoice.approved"])
        db.update_webhook_subscription(sub["id"], is_active=False)
        updated = db.get_webhook_subscription(sub["id"])
        assert updated["is_active"] is False

    def test_get_active_for_event(self, db):
        db.create_webhook_subscription("default", "https://d.com/h1", ["invoice.approved"])
        db.create_webhook_subscription("default", "https://d.com/h2", ["invoice.rejected"])
        db.create_webhook_subscription("default", "https://d.com/h3", ["*"])

        matches = db.get_active_webhooks_for_event("default", "invoice.approved")
        urls = {m["url"] for m in matches}
        assert "https://d.com/h1" in urls  # exact match
        assert "https://d.com/h3" in urls  # wildcard
        assert "https://d.com/h2" not in urls

    def test_inactive_excluded(self, db):
        sub = db.create_webhook_subscription("default", "https://e.com/h", ["*"])
        db.update_webhook_subscription(sub["id"], is_active=False)
        assert db.get_active_webhooks_for_event("default", "invoice.approved") == []


# ---------------------------------------------------------------------------
# Signature tests
# ---------------------------------------------------------------------------

class TestHMACSignature:
    def test_signature_matches(self):
        payload = b'{"event":"test"}'
        secret = "my-secret"
        sig = compute_signature(payload, secret)
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert sig == expected

    def test_different_secrets_produce_different_sigs(self):
        payload = b"data"
        sig1 = compute_signature(payload, "secret1")
        sig2 = compute_signature(payload, "secret2")
        assert sig1 != sig2


# ---------------------------------------------------------------------------
# Delivery tests
# ---------------------------------------------------------------------------

class TestWebhookDelivery:
    def test_successful_delivery(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("clearledgr.services.webhook_delivery.httpx.AsyncClient", return_value=mock_client):
            ok = asyncio.run(deliver_webhook(
                url="https://example.com/hook",
                event_type="invoice.approved",
                payload={"ap_item_id": "ap-1"},
                secret="test-secret",
            ))

        assert ok is True
        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "X-Clearledgr-Signature" in headers
        assert headers["X-Clearledgr-Event"] == "invoice.approved"

    def test_failed_delivery(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("clearledgr.services.webhook_delivery.httpx.AsyncClient", return_value=mock_client):
            ok = asyncio.run(deliver_webhook(
                url="https://example.com/hook",
                event_type="test",
                payload={},
            ))

        assert ok is False


# ---------------------------------------------------------------------------
# Event emission tests
# ---------------------------------------------------------------------------

class TestEmitWebhookEvent:
    def test_emit_to_matching_subscriptions(self, db):
        db.create_webhook_subscription("default", "https://f.com/h", ["invoice.approved"], secret="sec")

        with patch("clearledgr.services.webhook_delivery.deliver_webhook", new_callable=AsyncMock, return_value=True) as mock_deliver:
            count = asyncio.run(emit_webhook_event(
                organization_id="default",
                event_type="invoice.approved",
                payload={"ap_item_id": "ap-1"},
            ))

        assert count == 1
        mock_deliver.assert_called_once()

    def test_no_subscriptions_returns_zero(self, db):
        count = asyncio.run(emit_webhook_event("default", "invoice.approved", {}))
        assert count == 0

    def test_failed_delivery_enqueues_retry(self, db):
        db.create_webhook_subscription("default", "https://g.com/h", ["invoice.posted_to_erp"])

        with patch("clearledgr.services.webhook_delivery.deliver_webhook", new_callable=AsyncMock, return_value=False):
            asyncio.run(emit_webhook_event(
                organization_id="default",
                event_type="invoice.posted_to_erp",
                payload={"ap_item_id": "ap-2"},
            ))

        # Should have enqueued a retry notification
        pending = db.get_pending_notifications(limit=10)
        webhook_notifs = [n for n in pending if n.get("channel") == "webhook"]
        assert len(webhook_notifs) == 1


class TestEmitStateChangeWebhook:
    def test_maps_state_to_event_type(self, db):
        db.create_webhook_subscription("default", "https://h.com/h", ["*"])

        with patch("clearledgr.services.webhook_delivery.deliver_webhook", new_callable=AsyncMock, return_value=True) as mock_deliver:
            count = asyncio.run(emit_state_change_webhook(
                organization_id="default",
                ap_item_id="ap-3",
                new_state="approved",
                prev_state="needs_approval",
                item_data={"vendor_name": "Acme", "amount": 1000},
            ))

        assert count == 1
        call_args = mock_deliver.call_args
        assert call_args.kwargs["event_type"] == "invoice.approved"

    def test_unknown_state_returns_zero(self, db):
        count = asyncio.run(emit_state_change_webhook("default", "ap-4", "unknown_state"))
        assert count == 0

    def test_sync_state_transition_enqueues_webhook_without_instantiating_coroutine(self, db):
        item = db.create_ap_item(
            {
                "invoice_key": "webhook|sync|100.00|",
                "thread_id": "thread-webhook-sync",
                "message_id": "msg-webhook-sync",
                "subject": "Invoice",
                "sender": "vendor@example.com",
                "vendor_name": "Webhook Vendor",
                "amount": 100.0,
                "currency": "USD",
                "invoice_number": "INV-WH-1",
                "state": "received",
                "organization_id": "default",
                "user_id": "webhook-test",
            }
        )

        with patch("asyncio.get_running_loop", side_effect=RuntimeError):
            with patch(
                "clearledgr.services.webhook_delivery.emit_state_change_webhook",
                new_callable=AsyncMock,
            ) as mock_emit:
                assert db.update_ap_item(
                    item["id"],
                    state="validated",
                    _actor_type="system",
                    _actor_id="tester",
                )

        mock_emit.assert_not_called()
        pending = db.get_pending_notifications(limit=10)
        webhook_notifs = [n for n in pending if n.get("channel") == "webhook"]
        assert len(webhook_notifs) == 1
        payload = webhook_notifs[0]["payload_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["event_type"] == "ap_item.state_changed"
        assert payload["ap_item_id"] == item["id"]
        assert payload["new_state"] == "validated"


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestWebhookEndpoints:
    def test_create_webhook(self, client, db):
        resp = client.post(
            "/api/workspace/webhooks",
            json={
                "url": "https://test.com/hook",
                "event_types": ["invoice.approved"],
                "secret": "my-secret",
                "description": "Test",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://test.com/hook"
        assert data["secret"] == "***"  # redacted

    def test_list_webhooks(self, client, db):
        db.create_webhook_subscription("default", "https://i.com/h", ["*"])
        resp = client.get("/api/workspace/webhooks")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_delete_webhook(self, client, db):
        sub = db.create_webhook_subscription("default", "https://j.com/h", ["*"])
        resp = client.delete(f"/api/workspace/webhooks/{sub['id']}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_nonexistent_returns_404(self, client, db):
        resp = client.delete("/api/workspace/webhooks/wh_nonexistent")
        assert resp.status_code == 404

    def test_test_webhook(self, client, db):
        sub = db.create_webhook_subscription("default", "https://k.com/h", ["*"])
        with patch("clearledgr.services.webhook_delivery.deliver_webhook", new_callable=AsyncMock, return_value=True):
            resp = client.post(f"/api/workspace/webhooks/{sub['id']}/test")
        assert resp.status_code == 200
        assert resp.json()["delivered"] is True

    def test_create_without_url_returns_400(self, client, db):
        resp = client.post("/api/workspace/webhooks", json={"event_types": ["*"]})
        assert resp.status_code == 400
