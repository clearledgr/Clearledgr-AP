"""Tests for Module 7 v1 Pass 3 — audit-event webhook fan-out + delivery log.

Covers:

  * append_audit_event enqueues the dispatch task (best-effort; never
    blocks the audit write).
  * dispatch_audit_webhooks enqueues one deliver_audit_webhook task
    per matching active subscription; subscriptions for non-matching
    event_types are skipped; inactive subscriptions are skipped.
  * deliver_audit_webhook records a webhook_deliveries row with the
    correct status (success | failed | retrying) and attempt number;
    retries on failure schedule a follow-up with exponential backoff.
  * GET /api/workspace/webhooks/{id}/deliveries returns the log,
    admin-gated, tenant-scoped (404 on cross-tenant).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import workspace_shell as ws  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    inst.ensure_organization("other-tenant", organization_name="other-tenant")
    return inst


def _admin_user(org_id: str = "default"):
    return SimpleNamespace(
        email="admin@example.com",
        user_id="admin-user",
        organization_id=org_id,
        role="owner",
    )


def _operator_user():
    return SimpleNamespace(
        email="ops@example.com",
        user_id="ops-user",
        organization_id="default",
        role="ap_clerk",
    )


@pytest.fixture()
def client_factory(db):
    def _build(user_factory):
        app = FastAPI()
        app.include_router(ws.router)
        app.dependency_overrides[get_current_user] = user_factory
        return TestClient(app)
    return _build


def _seed_event(db, *, box_id="ap-fanout-1", event_type="state_transition", organization_id="default"):
    """Seed an event WITHOUT triggering fan-out — patches the dispatch
    task to no-op so tests can control timing."""
    with patch("clearledgr.services.celery_tasks.dispatch_audit_webhooks") as mock_dispatch:
        mock_dispatch.delay.return_value = None
        return db.append_audit_event({
            "box_id": box_id,
            "box_type": "ap_item",
            "event_type": event_type,
            "actor_type": "user",
            "actor_id": "admin@example.com",
            "organization_id": organization_id,
            "source": "test_seed",
            "payload_json": {"reason": "test"},
            "idempotency_key": f"fanout_test:{organization_id}:{box_id}:{event_type}:{time.time_ns()}",
        })


# ---------------------------------------------------------------------------
# append_audit_event fan-out enqueue
# ---------------------------------------------------------------------------


def test_append_audit_event_dispatches_fanout(db):
    """Successful audit insert enqueues the dispatch task. Best-effort:
    a Celery dispatch failure must not raise out of append_audit_event
    (the canonical audit write succeeds regardless)."""
    with patch("clearledgr.services.celery_tasks.dispatch_audit_webhooks") as mock_dispatch:
        mock_dispatch.delay.return_value = None
        event = db.append_audit_event({
            "box_id": "ap-dispatch-1",
            "box_type": "ap_item",
            "event_type": "state_transition",
            "actor_type": "user",
            "actor_id": "admin@example.com",
            "organization_id": "default",
            "source": "test_seed",
            "payload_json": {"reason": "test"},
            "idempotency_key": f"dispatch_test:{time.time_ns()}",
        })
        assert event is not None
        mock_dispatch.delay.assert_called_once_with(event["id"])


def test_append_audit_event_swallows_dispatch_errors(db):
    """A broker outage during fan-out enqueue MUST NOT raise — the
    audit write itself is the source of truth. Webhook delivery is
    downstream observability that can fail without taking the canonical
    write down with it."""
    with patch("clearledgr.services.celery_tasks.dispatch_audit_webhooks") as mock_dispatch:
        mock_dispatch.delay.side_effect = RuntimeError("broker unreachable")
        event = db.append_audit_event({
            "box_id": "ap-swallow-1",
            "box_type": "ap_item",
            "event_type": "state_transition",
            "actor_type": "user",
            "actor_id": "admin@example.com",
            "organization_id": "default",
            "source": "test_seed",
            "payload_json": {"reason": "test"},
            "idempotency_key": f"swallow_test:{time.time_ns()}",
        })
        assert event is not None  # write committed despite broker error


# ---------------------------------------------------------------------------
# dispatch_audit_webhooks: subscription matching
# ---------------------------------------------------------------------------


def test_dispatch_skips_when_no_subscriptions(db):
    """Audit event for an org with zero matching subscriptions:
    dispatch returns 'noop' without scheduling deliveries."""
    event = _seed_event(db, event_type="state_transition")
    from clearledgr.services.celery_tasks import dispatch_audit_webhooks

    with patch("clearledgr.services.celery_tasks.deliver_audit_webhook") as mock_deliver:
        result = dispatch_audit_webhooks.run(event["id"])
    assert result["status"] == "noop"
    assert result["subscribers"] == 0
    mock_deliver.delay.assert_not_called()


def test_dispatch_enqueues_one_task_per_matching_subscription(db):
    """Two subs for the same event_type → two deliver tasks. A third
    sub for a different event_type is skipped."""
    sub_a = db.create_webhook_subscription(
        organization_id="default",
        url="https://siem.example.com/audit",
        event_types=["state_transition", "invoice_approved"],
        secret="topsecret",
    )
    sub_b = db.create_webhook_subscription(
        organization_id="default",
        url="https://siem-b.example.com/audit",
        event_types=["state_transition"],
        secret="topsecret",
    )
    db.create_webhook_subscription(
        organization_id="default",
        url="https://noisy.example.com",
        event_types=["invoice_approved"],
        secret="",
    )

    event = _seed_event(db, event_type="state_transition")
    from clearledgr.services.celery_tasks import dispatch_audit_webhooks

    with patch("clearledgr.services.celery_tasks.deliver_audit_webhook") as mock_deliver:
        result = dispatch_audit_webhooks.run(event["id"])

    assert result["status"] == "dispatched"
    assert result["subscribers"] == 2
    delivered_sub_ids = {call.args[1] for call in mock_deliver.delay.call_args_list}
    assert delivered_sub_ids == {sub_a["id"], sub_b["id"]}


# ---------------------------------------------------------------------------
# deliver_audit_webhook: success / failure / retry chain
# ---------------------------------------------------------------------------


def test_deliver_records_success_row(db):
    sub = db.create_webhook_subscription(
        organization_id="default",
        url="https://siem.example.com/audit",
        event_types=["state_transition"],
        secret="topsecret",
    )
    event = _seed_event(db, event_type="state_transition")

    from clearledgr.services.celery_tasks import deliver_audit_webhook

    async def _ok(*args, **kwargs):
        return True

    with patch("clearledgr.services.webhook_delivery.deliver_webhook", side_effect=_ok):
        result = deliver_audit_webhook.run(event["id"], sub["id"], 1)

    assert result["status"] == "success"
    rows = db.list_webhook_deliveries(
        organization_id="default", webhook_subscription_id=sub["id"],
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "success"
    assert rows[0]["attempt_number"] == 1
    assert rows[0]["audit_event_id"] == event["id"]


def test_deliver_records_failure_and_schedules_retry(db):
    sub = db.create_webhook_subscription(
        organization_id="default",
        url="https://flaky.example.com/audit",
        event_types=["state_transition"],
        secret="s",
    )
    event = _seed_event(db, event_type="state_transition")

    from clearledgr.services.celery_tasks import deliver_audit_webhook

    async def _fail(*args, **kwargs):
        return False

    with patch("clearledgr.services.webhook_delivery.deliver_webhook", side_effect=_fail):
        with patch.object(deliver_audit_webhook, "apply_async") as mock_retry:
            result = deliver_audit_webhook.run(event["id"], sub["id"], 1)

    # First failure: record as 'retrying' (we know there's a retry coming).
    assert result["status"] == "retrying"
    rows = db.list_webhook_deliveries(
        organization_id="default", webhook_subscription_id=sub["id"],
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "retrying"
    assert rows[0]["next_retry_at"] is not None
    # Retry was scheduled with countdown matching the first backoff slot (30s).
    mock_retry.assert_called_once()
    call_kwargs = mock_retry.call_args.kwargs
    assert call_kwargs.get("countdown") == 30
    # Args carry incremented attempt counter for the retry.
    assert call_kwargs.get("args") == [event["id"], sub["id"], 2]


def test_deliver_terminal_failure_at_max_attempts(db):
    """At attempt == _AUDIT_WEBHOOK_MAX_ATTEMPTS, no more retries are
    scheduled and the row is recorded as 'failed' (terminal)."""
    sub = db.create_webhook_subscription(
        organization_id="default",
        url="https://dead.example.com/audit",
        event_types=["state_transition"],
        secret="s",
    )
    event = _seed_event(db, event_type="state_transition")

    from clearledgr.services.celery_tasks import (
        _AUDIT_WEBHOOK_MAX_ATTEMPTS,
        deliver_audit_webhook,
    )

    async def _fail(*args, **kwargs):
        return False

    with patch("clearledgr.services.webhook_delivery.deliver_webhook", side_effect=_fail):
        with patch.object(deliver_audit_webhook, "apply_async") as mock_retry:
            result = deliver_audit_webhook.run(
                event["id"], sub["id"], _AUDIT_WEBHOOK_MAX_ATTEMPTS,
            )
    assert result["status"] == "failed"
    rows = db.list_webhook_deliveries(
        organization_id="default", webhook_subscription_id=sub["id"],
    )
    assert rows[0]["status"] == "failed"
    assert rows[0]["next_retry_at"] is None
    mock_retry.assert_not_called()


def test_deliver_skips_inactive_subscription(db):
    sub = db.create_webhook_subscription(
        organization_id="default",
        url="https://siem.example.com/audit",
        event_types=["state_transition"],
        secret="s",
    )
    db.delete_webhook_subscription(sub["id"])  # marks is_active=False per existing infra
    event = _seed_event(db, event_type="state_transition")

    from clearledgr.services.celery_tasks import deliver_audit_webhook

    with patch("clearledgr.services.webhook_delivery.deliver_webhook") as mock_deliver:
        result = deliver_audit_webhook.run(event["id"], sub["id"], 1)
    assert result["status"] == "skipped"
    mock_deliver.assert_not_called()
    # No delivery row written either.
    rows = db.list_webhook_deliveries(
        organization_id="default", webhook_subscription_id=sub["id"],
    )
    assert rows == []


# ---------------------------------------------------------------------------
# GET /api/workspace/webhooks/{id}/deliveries
# ---------------------------------------------------------------------------


def test_deliveries_endpoint_requires_admin(client_factory, db):
    sub = db.create_webhook_subscription(
        organization_id="default",
        url="https://siem.example.com/audit",
        event_types=["state_transition"],
        secret="s",
    )
    client = client_factory(_operator_user)
    resp = client.get(f"/api/workspace/webhooks/{sub['id']}/deliveries")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_role_required"


def test_deliveries_endpoint_404s_cross_tenant(db, client_factory):
    other_sub = db.create_webhook_subscription(
        organization_id="other-tenant",
        url="https://siem.other.com/audit",
        event_types=["state_transition"],
        secret="s",
    )
    client = client_factory(_admin_user)
    resp = client.get(f"/api/workspace/webhooks/{other_sub['id']}/deliveries")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "webhook_not_found"


def test_deliveries_endpoint_returns_log_newest_first(db, client_factory):
    sub = db.create_webhook_subscription(
        organization_id="default",
        url="https://siem.example.com/audit",
        event_types=["state_transition"],
        secret="s",
    )
    # Three delivery attempts spaced apart to make ordering observable.
    for i in range(3):
        db.insert_webhook_delivery(
            organization_id="default",
            webhook_subscription_id=sub["id"],
            event_type="state_transition",
            request_url=sub["url"],
            status="success",
            attempt_number=1,
            http_status_code=200,
            duration_ms=50 + i,
        )
        time.sleep(0.01)

    client = client_factory(_admin_user)
    resp = client.get(f"/api/workspace/webhooks/{sub['id']}/deliveries?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["webhook_id"] == sub["id"]
    assert body["count"] == 3
    timestamps = [r["attempted_at"] for r in body["deliveries"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_deliveries_endpoint_filters_by_status(db, client_factory):
    sub = db.create_webhook_subscription(
        organization_id="default",
        url="https://siem.example.com/audit",
        event_types=["state_transition"],
        secret="s",
    )
    db.insert_webhook_delivery(
        organization_id="default",
        webhook_subscription_id=sub["id"],
        event_type="state_transition",
        request_url=sub["url"],
        status="success",
        http_status_code=200,
    )
    db.insert_webhook_delivery(
        organization_id="default",
        webhook_subscription_id=sub["id"],
        event_type="state_transition",
        request_url=sub["url"],
        status="failed",
        error_message="connection refused",
    )
    client = client_factory(_admin_user)
    resp = client.get(
        f"/api/workspace/webhooks/{sub['id']}/deliveries?status=failed&limit=10"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["deliveries"][0]["status"] == "failed"
    assert "connection refused" in (body["deliveries"][0].get("error_message") or "")
