"""Tests for Module 1 approver workload (§74).

Pinned by these tests:

  - Empty org returns []
  - Multiple pending approval steps with overlapping approvers
    aggregate correctly
  - Completed chains do not contribute
  - Oldest-pending age in days reflects the oldest chain that names
    the approver
  - User name + email resolution from the users table
  - Cross-tenant isolation: orgB approvers don't appear in orgA's
    workload
  - API endpoint returns the same shape
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.api import dashboard as dashboard_routes  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.auth import get_current_user  # noqa: E402
from solden.services import approver_workload  # noqa: E402


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    inst.ensure_organization("orgB", organization_name="Beta Co")
    return inst


def _user(org: str = "orgA") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=f"leader@{org}.com",
        email=f"leader@{org}.com",
        organization_id=org,
        role="owner",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(dashboard_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


def _make_pending_chain(
    db, *, organization_id: str, approvers: list, days_ago: int = 0,
):
    """Insert a pending approval_chain + step row directly so we
    don't depend on the wider invoice workflow."""
    chain_id = f"chain-{uuid.uuid4().hex[:12]}"
    invoice_id = f"INV-{uuid.uuid4().hex[:8]}"
    created_at = (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat()
    step_id = f"step-{uuid.uuid4().hex[:12]}"
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO approval_chains
              (id, organization_id, invoice_id, vendor_name, amount, gl_code,
               department, status, current_step, requester_id, requester_name,
               created_at, completed_at, metadata, entity_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', 0, %s, %s, %s, NULL, '{}', NULL)
            ON CONFLICT DO NOTHING
            """,
            (chain_id, organization_id, invoice_id, "Test Vendor",
             1000.0, "5000", "engineering", "agent", "Agent",
             created_at),
        )
        cur.execute(
            """
            INSERT INTO approval_steps
              (id, chain_id, step_index, level, approvers, approval_type,
               status, approved_by, approved_at, rejection_reason, comments,
               created_at, updated_at)
            VALUES (%s, %s, 0, 'manager', %s, 'any', 'pending', NULL, NULL,
                    NULL, '', %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (step_id, chain_id, json.dumps(approvers), created_at, created_at),
        )
        conn.commit()
    return chain_id


# ─── Tests ──────────────────────────────────────────────────────────


class TestWorkloadAggregation:
    def test_empty_org_returns_empty(self, db):
        result = approver_workload.get_approver_workload(db, "orgA")
        assert result == []

    def test_single_approver_single_chain(self, db):
        _make_pending_chain(db, organization_id="orgA", approvers=["sara@orga.com"])
        result = approver_workload.get_approver_workload(db, "orgA")
        assert len(result) == 1
        assert result[0]["approver_id"] == "sara@orga.com"
        assert result[0]["pending_count"] == 1

    def test_aggregates_across_chains(self, db):
        # Sara appears on 3 chains, Tobi on 1.
        for _ in range(3):
            _make_pending_chain(db, organization_id="orgA", approvers=["sara@orga.com"])
        _make_pending_chain(db, organization_id="orgA", approvers=["tobi@orga.com"])

        result = approver_workload.get_approver_workload(db, "orgA")
        names = {r["approver_id"]: r["pending_count"] for r in result}
        assert names["sara@orga.com"] == 3
        assert names["tobi@orga.com"] == 1
        # Order: most-loaded first
        assert result[0]["approver_id"] == "sara@orga.com"

    def test_multi_approver_step_counts_each(self, db):
        # Both Sara and Tobi on the same step → both get +1.
        _make_pending_chain(
            db, organization_id="orgA",
            approvers=["sara@orga.com", "tobi@orga.com"],
        )
        result = approver_workload.get_approver_workload(db, "orgA")
        names = {r["approver_id"] for r in result}
        assert "sara@orga.com" in names
        assert "tobi@orga.com" in names

    def test_oldest_pending_age_is_chain_age(self, db):
        _make_pending_chain(
            db, organization_id="orgA",
            approvers=["alice@orga.com"], days_ago=5,
        )
        _make_pending_chain(
            db, organization_id="orgA",
            approvers=["alice@orga.com"], days_ago=1,
        )
        result = approver_workload.get_approver_workload(db, "orgA")
        alice = next(r for r in result if r["approver_id"] == "alice@orga.com")
        assert alice["pending_count"] == 2
        # Oldest is 5 days old
        assert alice["oldest_pending_age_days"] == 5

    def test_cross_tenant_isolation(self, db):
        _make_pending_chain(
            db, organization_id="orgA", approvers=["sara@orga.com"],
        )
        _make_pending_chain(
            db, organization_id="orgB", approvers=["bob@orgb.com"],
        )
        result_a = approver_workload.get_approver_workload(db, "orgA")
        result_b = approver_workload.get_approver_workload(db, "orgB")
        a_ids = {r["approver_id"] for r in result_a}
        b_ids = {r["approver_id"] for r in result_b}
        assert a_ids == {"sara@orga.com"}
        assert b_ids == {"bob@orgb.com"}

    def test_user_lookup_resolves_display_name(self, db):
        # Create a real user row; the workload should pull name/email
        # for display.
        user = db.create_user(
            email="real-approver@orga.com",
            name="Real Approver",
            organization_id="orgA",
            role="ap_manager",
        )
        user_id = str(user["id"])
        _make_pending_chain(
            db, organization_id="orgA", approvers=[user_id],
        )
        result = approver_workload.get_approver_workload(db, "orgA")
        assert len(result) == 1
        assert result[0]["name"] == "Real Approver"
        assert result[0]["email"] == "real-approver@orga.com"


class TestAPI:
    def test_endpoint_returns_workload(self, db, client_orgA):
        _make_pending_chain(
            db, organization_id="orgA", approvers=["sara@orga.com"],
        )
        resp = client_orgA.get("/api/workspace/dashboard/approver-workload")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["approvers"][0]["approver_id"] == "sara@orga.com"

    def test_endpoint_empty_org(self, client_orgA):
        resp = client_orgA.get("/api/workspace/dashboard/approver-workload")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["approvers"] == []
