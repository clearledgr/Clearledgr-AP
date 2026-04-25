"""Tests for the per-tenant LLM cost summary endpoint.

Ops visibility: ``/api/ops/llm-cost-summary`` aggregates
``llm_call_log`` by action + day for one organization so CS can
spot runaway spend before the Anthropic bill arrives.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import app  # noqa: E402
from clearledgr.api import ops as ops_module  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import TokenData  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    d = db_module.get_db()
    d.initialize()
    return d


@pytest.fixture()
def client(db):
    def _fake_user():
        return TokenData(
            user_id="ops-user",
            email="ops@example.com",
            organization_id="default",
            role="owner",
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[ops_module.get_current_user] = _fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ops_module.get_current_user, None)


def _insert_call(db, *, org_id, action, input_tok, output_tok, cost, created_at,
                 error=None):
    sql = (
        "INSERT INTO llm_call_log "
        "(id, organization_id, action, model, input_tokens, output_tokens, "
        " latency_ms, cost_estimate_usd, truncated, error, correlation_id, "
        " created_at, box_id, box_type) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    import uuid
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (
            f"LLM-{uuid.uuid4().hex[:10]}", org_id, action, "claude-haiku-4-5",
            input_tok, output_tok, 100, cost, 0, error, None,
            created_at, None, None,
        ))
        conn.commit()


class TestLLMCostSummary:

    def test_aggregates_totals_and_by_action(self, client, db):
        now = datetime.now(timezone.utc)
        today = now.isoformat()
        _insert_call(db, org_id="default", action="extract_invoice_fields",
                     input_tok=1000, output_tok=200, cost=0.01, created_at=today)
        _insert_call(db, org_id="default", action="extract_invoice_fields",
                     input_tok=2000, output_tok=300, cost=0.02, created_at=today)
        _insert_call(db, org_id="default", action="classify_email",
                     input_tok=500, output_tok=50, cost=0.001, created_at=today)

        resp = client.get("/api/ops/llm-cost-summary?organization_id=default&window_days=30")
        assert resp.status_code == 200
        payload = resp.json()["summary"]

        assert payload["total_calls"] == 3
        assert payload["total_input_tokens"] == 3500
        assert payload["total_output_tokens"] == 550
        assert round(payload["total_cost_usd"], 4) == 0.031
        assert payload["error_calls"] == 0

        # Breakdown ordered by cost desc.
        actions = {row["action"]: row for row in payload["by_action"]}
        assert actions["extract_invoice_fields"]["calls"] == 2
        assert actions["extract_invoice_fields"]["cost_usd"] == 0.03
        assert actions["classify_email"]["calls"] == 1

    def test_window_cutoff_excludes_older_calls(self, client, db):
        now = datetime.now(timezone.utc)
        today = now.isoformat()
        ancient = (now - timedelta(days=60)).isoformat()
        _insert_call(db, org_id="default", action="extract_invoice_fields",
                     input_tok=100, output_tok=10, cost=0.002, created_at=today)
        _insert_call(db, org_id="default", action="extract_invoice_fields",
                     input_tok=9999, output_tok=9999, cost=1.0, created_at=ancient)

        resp = client.get("/api/ops/llm-cost-summary?organization_id=default&window_days=7")
        payload = resp.json()["summary"]
        assert payload["total_calls"] == 1
        assert payload["total_cost_usd"] == 0.002

    def test_error_calls_counted(self, client, db):
        today = datetime.now(timezone.utc).isoformat()
        _insert_call(db, org_id="default", action="extract_invoice_fields",
                     input_tok=100, output_tok=0, cost=0.0,
                     created_at=today, error="rate_limited")
        _insert_call(db, org_id="default", action="extract_invoice_fields",
                     input_tok=100, output_tok=10, cost=0.001, created_at=today)

        resp = client.get("/api/ops/llm-cost-summary?organization_id=default")
        payload = resp.json()["summary"]
        assert payload["total_calls"] == 2
        assert payload["error_calls"] == 1

    def test_organization_isolation(self, client, db):
        today = datetime.now(timezone.utc).isoformat()
        _insert_call(db, org_id="default", action="extract_invoice_fields",
                     input_tok=100, output_tok=10, cost=0.005, created_at=today)
        _insert_call(db, org_id="other-org", action="extract_invoice_fields",
                     input_tok=99999, output_tok=99999, cost=99.0, created_at=today)

        resp = client.get("/api/ops/llm-cost-summary?organization_id=default")
        payload = resp.json()["summary"]
        assert payload["total_calls"] == 1
        assert payload["total_cost_usd"] == 0.005

    def test_empty_window_returns_zero_totals(self, client, db):
        resp = client.get("/api/ops/llm-cost-summary?organization_id=default")
        payload = resp.json()["summary"]
        assert payload["total_calls"] == 0
        assert payload["total_cost_usd"] == 0.0
        assert payload["by_action"] == []
        assert payload["by_day"] == []
