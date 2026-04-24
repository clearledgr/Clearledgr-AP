"""Endpoint-level idempotency contract tests.

Covers:
- The :mod:`clearledgr.core.idempotency` helper round-trip
  (resolve_idempotency_key / load / save).
- ``POST /api/ap/items/{id}/snooze`` end-to-end replay (the simplest
  endpoint that doesn't need a runtime mock — pure DB writes).
- ``POST /api/ap/items/bulk-approve`` end-to-end replay with a stub
  finance runtime, since bulk endpoints are the highest-risk site
  for "client retried, agent double-approved" bugs.

Storage piggybacks on ``audit_events.idempotency_key`` (UNIQUE).
We assert that:
  1. A first call with a key returns its real response and persists it.
  2. A second call with the same key returns the cached response and
     sets ``idempotency_replayed=True`` without re-executing the action.
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

from clearledgr.api import ap_items_action_routes as action_routes  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import require_ops_user  # noqa: E402
from clearledgr.core.idempotency import (  # noqa: E402
    load_idempotent_response,
    resolve_idempotency_key,
    save_idempotent_response,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


def _fake_user():
    return SimpleNamespace(
        email="ops@example.com",
        user_id="ops-user",
        organization_id="default",
        role="ops",
    )


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(action_routes.router, prefix="/api/ap/items")
    app.dependency_overrides[require_ops_user] = _fake_user
    return TestClient(app)


def _make_ap_item(db, item_id: str, state: str = "needs_approval") -> dict:
    return db.create_ap_item({
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"thread-{item_id}",
        "message_id": f"msg-{item_id}",
        "subject": "Invoice for review",
        "sender": "billing@vendor.com",
        "vendor_name": "Acme Corp",
        "amount": 500.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": state,
        "organization_id": "default",
    })


# ---------------------------------------------------------------------------
# Helper-level tests (no HTTP layer, fastest signal)
# ---------------------------------------------------------------------------


class TestResolveIdempotencyKey:
    def test_header_wins_over_body(self):
        assert resolve_idempotency_key("hdr-123", "body-456") == "hdr-123"

    def test_falls_back_to_body(self):
        assert resolve_idempotency_key(None, "body-456") == "body-456"

    def test_empty_header_falls_back(self):
        assert resolve_idempotency_key("   ", "body-456") == "body-456"

    def test_both_empty_returns_none(self):
        assert resolve_idempotency_key(None, None) is None
        assert resolve_idempotency_key("", "") is None
        assert resolve_idempotency_key("  ", "  ") is None


class TestLoadSaveRoundTrip:
    def test_save_then_load_returns_response_with_replay_flag(self, db):
        _make_ap_item(db, "ITEM-RT-1")

        save_idempotent_response(
            db,
            "key-roundtrip-1",
            {"status": "approved", "ap_item_id": "ITEM-RT-1"},
            box_id="ITEM-RT-1",
            box_type="ap_item",
            organization_id="default",
        )

        replay = load_idempotent_response(db, "key-roundtrip-1")
        assert replay is not None
        assert replay["status"] == "approved"
        assert replay["ap_item_id"] == "ITEM-RT-1"
        assert replay["idempotency_replayed"] is True
        assert "audit_event_id" in replay

    def test_load_missing_key_returns_none(self, db):
        assert load_idempotent_response(db, "nonexistent-key") is None

    def test_save_with_no_key_is_noop(self, db):
        save_idempotent_response(db, None, {"status": "ok"})
        save_idempotent_response(db, "", {"status": "ok"})
        assert load_idempotent_response(db, "") is None

    def test_save_falls_back_to_synthetic_box_for_bulk(self, db):
        save_idempotent_response(
            db,
            "key-bulk-1",
            {"total": 3, "succeeded": 3},
            organization_id="default",
        )
        replay = load_idempotent_response(db, "key-bulk-1")
        assert replay is not None
        assert replay["total"] == 3
        assert replay["idempotency_replayed"] is True


# ---------------------------------------------------------------------------
# End-to-end endpoint tests
# ---------------------------------------------------------------------------


class TestSnoozeEndpointIdempotency:
    """Snooze is the simplest action endpoint — pure DB writes, no
    runtime mock needed."""

    def test_first_call_runs_then_replay_returns_cached(self, client, db):
        item = _make_ap_item(db, "SNOOZE-IDEM-1", state="needs_approval")

        r1 = client.post(
            f"/api/ap/items/{item['id']}/snooze",
            json={
                "duration_minutes": 60,
                "note": "Wait for vendor reply",
                "idempotency_key": "snooze-key-1",
            },
        )
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        assert body1["status"] == "snoozed"
        assert body1.get("idempotency_replayed") is not True
        first_snoozed_until = body1["snoozed_until"]

        r2 = client.post(
            f"/api/ap/items/{item['id']}/snooze",
            json={
                "duration_minutes": 60,
                "note": "Wait for vendor reply",
                "idempotency_key": "snooze-key-1",
            },
        )
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["status"] == "snoozed"
        assert body2["idempotency_replayed"] is True
        # Same snoozed_until => the action did NOT re-run (would have
        # produced a fresh now+60min timestamp otherwise).
        assert body2["snoozed_until"] == first_snoozed_until


class TestBulkApproveEndpointIdempotency:
    """Bulk endpoints are the highest-risk replay surface: a retried
    POST without idempotency could re-approve N invoices."""

    @pytest.fixture(autouse=True)
    def stub_finance_runtime(self, monkeypatch):
        from clearledgr.services import ap_item_service as shared_mod

        class _StubRuntime:
            execute_count = 0

            def __init__(self, *a, **kw):
                pass

            async def execute_intent(self, intent, payload):
                _StubRuntime.execute_count += 1
                return {
                    "status": "approved",
                    "ap_item_id": payload.get("ap_item_id"),
                    "erp_reference": f"ERP-{payload.get('ap_item_id')}",
                }

        _StubRuntime.execute_count = 0
        monkeypatch.setattr(
            shared_mod, "_finance_agent_runtime_cls", lambda: _StubRuntime
        )
        self._stub = _StubRuntime
        yield

    def test_bulk_approve_replay_does_not_re_execute(self, client, db):
        ids = [f"BULK-APPR-{i}" for i in range(3)]
        for ap_id in ids:
            _make_ap_item(db, ap_id, state="needs_approval")

        body = {
            "ap_item_ids": ids,
            "idempotency_key": "bulk-approve-key-1",
        }
        r1 = client.post("/api/ap/items/bulk-approve", json=body)
        assert r1.status_code == 200, r1.text
        first_count = self._stub.execute_count
        assert first_count == 3

        r2 = client.post("/api/ap/items/bulk-approve", json=body)
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["idempotency_replayed"] is True
        assert body2["total"] == 3
        assert self._stub.execute_count == first_count, (
            "bulk-approve replay re-executed approve_invoice — "
            "this is the exact bug idempotency must prevent"
        )


class TestRetryPostHeaderIdempotency:
    """retry_erp_post has no body, so it takes Idempotency-Key as a
    header. This is the Stripe convention for endpoints with path-only
    signatures."""

    @pytest.fixture(autouse=True)
    def stub_finance_runtime(self, monkeypatch):
        from clearledgr.services import ap_item_service as shared_mod

        class _StubRuntime:
            execute_count = 0

            def __init__(self, *a, **kw):
                pass

            async def execute_intent(self, intent, payload):
                _StubRuntime.execute_count += 1
                return {
                    "status": "posted",
                    "ap_item_id": payload.get("ap_item_id"),
                    "erp_reference": f"ERP-{payload.get('ap_item_id')}",
                }

        _StubRuntime.execute_count = 0
        monkeypatch.setattr(
            shared_mod, "_finance_agent_runtime_cls", lambda: _StubRuntime
        )
        self._stub = _StubRuntime
        yield

    def test_header_idempotency_key_is_honored(self, client, db):
        item = _make_ap_item(db, "RETRY-HDR-1", state="failed_post")

        headers = {"Idempotency-Key": "retry-key-1"}
        r1 = client.post(
            f"/api/ap/items/{item['id']}/retry-post?organization_id=default",
            headers=headers,
        )
        assert r1.status_code == 200, r1.text
        first_count = self._stub.execute_count
        assert first_count == 1

        r2 = client.post(
            f"/api/ap/items/{item['id']}/retry-post?organization_id=default",
            headers=headers,
        )
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["idempotency_replayed"] is True
        assert self._stub.execute_count == first_count
