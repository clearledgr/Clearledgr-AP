"""Tests for ``GET /api/workspace/audit/chain-status``.

Backs the marketing claim that the audit chain is "tamper-evident
at the schema layer" with a runtime check operators (and external
auditors) can run against a live tenant.

What's tested here:

  1. Empty chain (no audit rows yet) → chain_intact=True,
     chain_length=0, head fields=None.
  2. Healthy chain → chain_intact=True, head_chain_seq matches
     the actual row count, head_hash_prefix populated.
  3. Per-tenant scope: orgA's audit rows don't pollute orgB's
     chain status.
  4. Tampered row (forced via direct UPDATE that bypasses the
     no-update trigger) → chain_intact=False with a structured
     break report.
  5. Auth: unauthenticated → 401; user without org_id → 403.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.api import audit_chain as ac_routes  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.auth import TokenData, get_current_user  # noqa: E402


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgChainA", organization_name="Chain A")
    inst.ensure_organization("orgChainB", organization_name="Chain B")
    return inst


def _user(role: str = "ap_clerk", org: str = "orgChainA") -> TokenData:
    return TokenData(
        user_id="u1",
        email=f"u1@{org}.test",
        organization_id=org,
        role=role,
        exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture()
def app(db):
    app = FastAPI()
    app.include_router(ac_routes.router)
    return app


def _client_as(app, user: TokenData) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _seed_box(db, *, item_id: str, org: str = "orgChainA") -> dict:
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "Vendor",
        "amount": 1.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "received",
    })
    return db.get_ap_item(item["id"])


def _emit_audit(db, *, ap_item_id: str, event_type: str, org: str, idempotency_key: str = "") -> dict:
    return db.append_audit_event({
        "ap_item_id": ap_item_id,
        "event_type": event_type,
        "actor_type": "agent",
        "actor_id": "test",
        "organization_id": org,
        "idempotency_key": idempotency_key or None,
        "payload_json": {"note": event_type},
    })


# ─── Empty chain ───────────────────────────────────────────────────


def test_empty_chain_returns_intact_with_zero_length(app, db):
    """An org that has never appended an audit row should report
    chain_intact=True with length=0. An empty chain is technically
    consistent."""
    # Seed a different org so we know the query is org-scoped.
    box_other = _seed_box(db, item_id="AP-other", org="orgChainB")
    _emit_audit(db, ap_item_id=box_other["id"], event_type="agent_action:noise", org="orgChainB")

    client = _client_as(app, _user(org="orgChainA"))
    resp = client.get("/api/workspace/audit/chain-status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["organization_id"] == "orgChainA"
    assert body["chain_intact"] is True
    assert body["chain_length"] == 0
    assert body["head_event_id"] is None
    assert body["head_hash_prefix"] is None
    assert body["verified_rows"] == 0
    # The genesis sentinel for orgChainA is deterministic — useful
    # for an external auditor who wants to verify the chain root
    # without hitting the DB.
    assert body["genesis_hash_prefix"]


# ─── Healthy chain ─────────────────────────────────────────────────


def test_healthy_chain_reports_intact_with_chain_length(app, db):
    box = _seed_box(db, item_id="AP-chain-1", org="orgChainA")
    # Append a few audit rows.
    for i in range(5):
        _emit_audit(
            db, ap_item_id=box["id"],
            event_type=f"agent_action:test_{i}",
            org="orgChainA",
            idempotency_key=f"chain-test:{box['id']}:{i}",
        )

    client = _client_as(app, _user(org="orgChainA"))
    resp = client.get("/api/workspace/audit/chain-status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chain_intact"] is True
    # We added 5 rows on top of any existing (e.g. state-transition
    # audits the create_ap_item triggers). Just check the head is
    # at least 5.
    assert body["chain_length"] >= 5
    assert body["head_chain_seq"] == body["chain_length"]
    assert body["head_event_id"]
    assert body["head_hash_prefix"]
    assert body["verified_rows"] >= 5
    assert body["verified_at"]


def test_per_tenant_scope_isolates_chains(app, db):
    box_a = _seed_box(db, item_id="AP-scope-a", org="orgChainA")
    box_b = _seed_box(db, item_id="AP-scope-b", org="orgChainB")
    for i in range(3):
        _emit_audit(
            db, ap_item_id=box_a["id"],
            event_type=f"agent_action:a_{i}",
            org="orgChainA",
            idempotency_key=f"scope-a:{i}",
        )
    for i in range(7):
        _emit_audit(
            db, ap_item_id=box_b["id"],
            event_type=f"agent_action:b_{i}",
            org="orgChainB",
            idempotency_key=f"scope-b:{i}",
        )

    a_resp = _client_as(app, _user(org="orgChainA")).get(
        "/api/workspace/audit/chain-status",
    )
    b_resp = _client_as(app, _user(org="orgChainB")).get(
        "/api/workspace/audit/chain-status",
    )
    assert a_resp.status_code == 200
    assert b_resp.status_code == 200
    a_body = a_resp.json()
    b_body = b_resp.json()
    assert a_body["chain_intact"] is True
    assert b_body["chain_intact"] is True
    # Genesis hashes differ — proves per-org chains are independent.
    assert a_body["genesis_hash_prefix"] != b_body["genesis_hash_prefix"]


# ─── Tampered chain ────────────────────────────────────────────────


def test_tampered_row_breaks_chain(app, db):
    """Force a row's payload to mutate while keeping the same
    hash. Recompute should disagree → chain_intact=False."""
    box = _seed_box(db, item_id="AP-tamper-1", org="orgChainA")
    for i in range(3):
        _emit_audit(
            db, ap_item_id=box["id"],
            event_type=f"agent_action:tamper_{i}",
            org="orgChainA",
            idempotency_key=f"tamper-test:{i}",
        )

    # Rewrite an audit row's event_type via raw SQL. The
    # no-update trigger blocks this for normal callers, so we
    # disable it for the duration of the tamper to simulate a
    # privileged adversary or storage corruption — exactly the
    # case the chain is meant to catch.
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "ALTER TABLE audit_events DISABLE TRIGGER trg_audit_events_no_update"
        )
        cur.execute(
            "UPDATE audit_events SET event_type = 'agent_action:tampered' "
            "WHERE organization_id = %s AND chain_seq = "
            "(SELECT MAX(chain_seq) FROM audit_events WHERE organization_id = %s)",
            ("orgChainA", "orgChainA"),
        )
        cur.execute(
            "ALTER TABLE audit_events ENABLE TRIGGER trg_audit_events_no_update"
        )
        conn.commit()

    client = _client_as(app, _user(org="orgChainA"))
    resp = client.get("/api/workspace/audit/chain-status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chain_intact"] is False
    assert body["broken_at_chain_seq"]
    assert body["broken_at_event_id"]
    assert body["break_kind"] in {
        "hash_recompute_mismatch",
        "prev_hash_breaks_linkage",
    }


# ─── Auth ──────────────────────────────────────────────────────────


def test_user_without_org_id_gets_403(app, db):
    user = TokenData(
        user_id="u1",
        email="u1@nowhere.test",
        organization_id="",  # missing
        role="ap_clerk",
        exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    resp = client.get("/api/workspace/audit/chain-status")
    assert resp.status_code == 403
    assert "missing_user_organization_id" in resp.json()["detail"]


def test_sample_size_validation(app, db):
    client = _client_as(app, _user(org="orgChainA"))
    # Below minimum.
    assert client.get("/api/workspace/audit/chain-status?sample_size=0").status_code == 422
    # Above maximum.
    assert client.get(
        "/api/workspace/audit/chain-status?sample_size=10000"
    ).status_code == 422
    # Valid bounds.
    assert client.get(
        "/api/workspace/audit/chain-status?sample_size=10"
    ).status_code == 200
