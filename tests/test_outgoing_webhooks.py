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
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

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
        # Box-keyed payload — ap_item_id is gone.
        payload = call_args.kwargs["payload"]
        assert "ap_item_id" not in payload
        assert payload["box_id"] == "ap-3"
        assert payload["box_type"] == "ap_item"

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
        assert payload["box_id"] == item["id"]
        assert payload["box_type"] == "ap_item"
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


class TestWebhookCrossOrgIsolation:
    """Multi-tenant isolation for webhook subscriptions.

    Webhook subscriptions were the one store I could not find an
    explicit cross-org regression test for during the 2026-04-22
    audit. Every other major org-scoped resource (AP items, audit
    events, box exceptions, vendor KYC, vendor domain lock) had
    isolation tests; webhooks did not. This class closes that gap.

    The guarantees under test, all enforced by
    ``clearledgr.api.workspace_shell._resolve_org_id`` raising
    HTTPException(403) when the requested organization_id does not
    match the caller's token:

    1. Query-param spoofing on LIST returns 403 (not silently
       filtered empty results).
    2. Query-param spoofing on CREATE returns 403 (a user from org
       A cannot create a webhook in org B's namespace).
    3. DELETE by id on another org's webhook returns 403 (the
       handler fetches the sub first, then asserts the caller's
       token owns that sub's organization_id).
    4. LIST without a query param scopes strictly to the caller's
       org — a webhook owned by another org never appears.
    """

    def test_list_with_query_param_spoofing_returns_403(self, client, db):
        # Pre-seed a webhook owned by a DIFFERENT org.
        db.create_webhook_subscription(
            organization_id="other-org",
            url="https://other-org-internal.example/hook",
            event_types=["*"],
        )
        # Caller's token belongs to 'default' (per the client fixture).
        # Trying to list 'other-org' webhooks by query param must be rejected.
        resp = client.get("/api/workspace/webhooks?organization_id=other-org")
        assert resp.status_code == 403, (
            f"cross-org LIST must return 403, got {resp.status_code}: {resp.json()}"
        )

    def test_list_without_param_scopes_to_caller_org_only(self, client, db):
        # Seed one webhook per org.
        own = db.create_webhook_subscription(
            "default", "https://own.example/hook", ["*"]
        )
        db.create_webhook_subscription(
            "other-org", "https://other.example/hook", ["*"]
        )
        resp = client.get("/api/workspace/webhooks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1, (
            f"LIST must return only caller's org subs; got {data['count']}"
        )
        assert data["webhooks"][0]["id"] == own["id"]
        # Belt and braces: no webhook in the response belongs to another org.
        assert all(
            sub["organization_id"] == "default" for sub in data["webhooks"]
        )

    def test_create_with_query_param_spoofing_returns_403(self, client, db):
        # Caller is in 'default'; try to create a webhook targeted at 'other-org'.
        resp = client.post(
            "/api/workspace/webhooks?organization_id=other-org",
            json={
                "url": "https://attempted-cross-org.example/hook",
                "event_types": ["*"],
            },
        )
        assert resp.status_code == 403, (
            f"cross-org CREATE must return 403, got {resp.status_code}: {resp.json()}"
        )
        # And no row was written against either org.
        assert db.list_webhook_subscriptions("other-org") == []

    def test_delete_other_orgs_webhook_returns_403(self, client, db):
        # Seed a webhook owned by 'other-org'.
        other_sub = db.create_webhook_subscription(
            organization_id="other-org",
            url="https://other-org.example/hook",
            event_types=["*"],
        )
        # Caller (in 'default') tries to delete by ID — the handler
        # looks up the sub first, then applies _resolve_org_id which
        # must fail for a foreign org.
        resp = client.delete(f"/api/workspace/webhooks/{other_sub['id']}")
        assert resp.status_code == 403, (
            f"cross-org DELETE must return 403, got {resp.status_code}: {resp.json()}"
        )
        # The foreign webhook is still intact.
        still_there = db.get_webhook_subscription(other_sub["id"])
        assert still_there is not None
        assert still_there["organization_id"] == "other-org"
