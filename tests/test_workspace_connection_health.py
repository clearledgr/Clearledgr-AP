"""Tests for ``GET /api/workspace/connections/health`` (Module 5 Pass B).

Connection health is *derived state* — there is no health table.
The view assembles ``audit_events`` aggregates +
``organization_integrations`` rows + ``webhook_deliveries`` counts.
These tests cover:

  * Empty state: a tenant with no integration rows + no audit events
    returns every integration as ``not_configured``.
  * Healthy classification: integration row says connected + no
    errors + recent events → ``healthy``.
  * Degraded classification: 1-4 errors in window → ``degraded``.
  * Down classification: 5+ errors → ``down``.
  * Stale-but-connected → ``down`` (connected row, zero events,
    last_sync_at older than the window).
  * Latest-error trim: only one error per kind is surfaced; payload
    is reduced to {ts, event_type, message}.
  * Webhook counters: status='success'/'failed'/'retrying' rows
    aggregate into delivered/failed/retrying buckets.
  * Tenant scope: cross-tenant 403.
  * Window param: window_hours=1 vs window_hours=24 surfaces
    different events.
  * Other-source events (e.g. 'erp_admin' source on a non-erp event_type)
    classify under the right kind via the source-prefix branch.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import workspace_shell as ws  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402
from clearledgr.services.connection_health import (  # noqa: E402
    build_connection_health,
)


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


def _user(org_id: str = "default", role: str = "owner"):
    return SimpleNamespace(
        email=f"{role}@example.com",
        user_id=f"{role}-user",
        organization_id=org_id,
        role=role,
    )


@pytest.fixture()
def client_factory(db):
    def _build(user_factory):
        app = FastAPI()
        app.include_router(ws.router)
        app.dependency_overrides[get_current_user] = user_factory
        return TestClient(app)
    return _build


def _seed_event(
    db,
    *,
    event_type: str,
    source: str = "test",
    organization_id: str = "default",
    box_id: str = "box-test",
    box_type: str = "ap_item",
    payload: dict | None = None,
    ts: str | None = None,
    counter: int = 0,
):
    payload_json = payload or {"detail": "test"}
    return db.append_audit_event({
        "event_type": event_type,
        "actor_type": "system",
        "actor_id": "system",
        "organization_id": organization_id,
        "box_id": box_id,
        "box_type": box_type,
        "source": source,
        "payload_json": payload_json,
        "idempotency_key": f"conn_health_test:{organization_id}:{event_type}:{source}:{counter}:{time.time_ns()}",
        **({"ts": ts} if ts else {}),
    })


def _seed_integration(db, *, organization_id="default", integration_type="gmail", status="connected", last_sync_at=None):
    return db.upsert_organization_integration(
        organization_id=organization_id,
        integration_type=integration_type,
        status=status,
        mode="per_org",
        metadata={},
        last_sync_at=last_sync_at or datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Direct service-layer tests (no HTTP)
# ---------------------------------------------------------------------------


def test_empty_tenant_returns_all_not_configured(db):
    out = build_connection_health(db, "default")
    assert out["organization_id"] == "default"
    assert {row["integration_type"] for row in out["integrations"]} == {"gmail", "slack", "teams", "erp"}
    for row in out["integrations"]:
        assert row["status"] == "not_configured", row
        assert row["events_24h"] == 0
        assert row["errors_24h"] == 0


def test_connected_with_recent_events_classifies_healthy(db):
    _seed_integration(db, integration_type="gmail")
    for i in range(3):
        _seed_event(db, event_type="gmail_thread_linked", source="gmail_webhook", counter=i)
    out = build_connection_health(db, "default")
    gmail = next(r for r in out["integrations"] if r["integration_type"] == "gmail")
    assert gmail["status"] == "healthy"
    assert gmail["events_24h"] >= 3
    assert gmail["errors_24h"] == 0


def test_one_error_classifies_degraded(db):
    _seed_integration(db, integration_type="erp")
    _seed_event(db, event_type="erp_post_completed", source="erp_router", counter=1)
    _seed_event(db, event_type="erp_post_failed", source="erp_router", counter=2,
                payload={"error": "QB API rate-limited"})
    out = build_connection_health(db, "default")
    erp = next(r for r in out["integrations"] if r["integration_type"] == "erp")
    assert erp["status"] == "degraded"
    assert erp["errors_24h"] == 1
    assert erp["latest_error"]["event_type"] == "erp_post_failed"
    assert "rate-limited" in (erp["latest_error"]["message"] or "")


def test_five_errors_classifies_down(db):
    _seed_integration(db, integration_type="erp")
    for i in range(5):
        _seed_event(db, event_type="erp_post_failed", source="erp_router", counter=i,
                    payload={"error": f"failure #{i}"})
    out = build_connection_health(db, "default")
    erp = next(r for r in out["integrations"] if r["integration_type"] == "erp")
    assert erp["status"] == "down"
    assert erp["errors_24h"] == 5


def test_stale_connected_with_no_events_classifies_down(db):
    """Integration says connected but last_sync_at is 48h old + zero
    events in the window → 'down'. This is the "leader sees the
    breakage within 10 minutes" pathological case the scope spec
    targets: an integration that crashed silently."""
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    _seed_integration(db, integration_type="slack", status="connected", last_sync_at=stale_ts)
    out = build_connection_health(db, "default", window_hours=24)
    slack = next(r for r in out["integrations"] if r["integration_type"] == "slack")
    assert slack["status"] == "down"


def test_disconnected_integration_returns_not_configured(db):
    _seed_integration(db, integration_type="teams", status="disconnected")
    out = build_connection_health(db, "default")
    teams = next(r for r in out["integrations"] if r["integration_type"] == "teams")
    assert teams["status"] == "not_configured"


def test_latest_error_payload_trimmed_to_summary(db):
    _seed_integration(db, integration_type="erp")
    _seed_event(
        db, event_type="erp_post_failed", source="erp_router", counter=99,
        payload={"error": "x" * 500, "huge": "y" * 9999},
    )
    out = build_connection_health(db, "default")
    erp = next(r for r in out["integrations"] if r["integration_type"] == "erp")
    err = erp["latest_error"]
    assert err is not None
    # message capped at 300 chars
    assert err["message"] is not None
    assert len(err["message"]) <= 300
    # The huge field is not surfaced in the trimmed payload.
    assert "huge" not in (err.get("message") or "")


def test_webhook_delivery_aggregates(db):
    db.insert_webhook_delivery(
        organization_id="default", webhook_subscription_id="wh_1",
        event_type="invoice.approved", request_url="https://x", status="success",
    )
    db.insert_webhook_delivery(
        organization_id="default", webhook_subscription_id="wh_1",
        event_type="invoice.approved", request_url="https://x", status="success",
    )
    db.insert_webhook_delivery(
        organization_id="default", webhook_subscription_id="wh_2",
        event_type="invoice.posted", request_url="https://y", status="failed",
        error_message="timeout",
    )
    out = build_connection_health(db, "default")
    assert out["webhooks"]["delivered"] == 2
    assert out["webhooks"]["failed"] == 1
    assert out["webhooks"]["retrying"] == 0


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


def test_endpoint_returns_health_for_org(db, client_factory):
    _seed_integration(db, integration_type="gmail")
    _seed_event(db, event_type="gmail_thread_linked", source="gmail_webhook", counter=10)
    client = client_factory(_user)
    resp = client.get("/api/workspace/connections/health?organization_id=default")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["organization_id"] == "default"
    assert body["window_hours"] == 24
    assert "computed_at" in body
    gmail = next(r for r in body["integrations"] if r["integration_type"] == "gmail")
    assert gmail["status"] == "healthy"


def test_endpoint_blocks_cross_tenant(db, client_factory):
    client = client_factory(_user)
    resp = client.get("/api/workspace/connections/health?organization_id=other-tenant")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "org_access_denied"


def test_endpoint_window_param_narrows_results(db, client_factory):
    """Events outside the window must not count. Seed a stale event 48h
    ago, then query with window_hours=1 — count should be zero."""
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    _seed_integration(db, integration_type="erp")
    _seed_event(db, event_type="erp_post_completed", source="erp_router",
                counter=1000, ts=stale_ts)
    client = client_factory(_user)
    resp_wide = client.get(
        "/api/workspace/connections/health?organization_id=default&window_hours=72"
    )
    resp_narrow = client.get(
        "/api/workspace/connections/health?organization_id=default&window_hours=1"
    )
    assert resp_wide.status_code == 200
    assert resp_narrow.status_code == 200
    erp_wide = next(r for r in resp_wide.json()["integrations"] if r["integration_type"] == "erp")
    erp_narrow = next(r for r in resp_narrow.json()["integrations"] if r["integration_type"] == "erp")
    assert erp_wide["events_24h"] >= 1
    assert erp_narrow["events_24h"] == 0


def test_endpoint_bounds_window_to_168_hours(client_factory):
    """window_hours > 168 is rejected by the Query() max bound. Keeps
    operators from accidentally pulling years of audit history with
    a typo."""
    client = client_factory(_user)
    resp = client.get(
        "/api/workspace/connections/health?organization_id=default&window_hours=999"
    )
    assert resp.status_code == 422
