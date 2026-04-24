"""Tests for approval delegation service.

Covers:
- Rule CRUD (create, list, deactivate, get)
- Delegate resolution (with date range)
- Approver list resolution (swap delegated approvers)
- API endpoints (list, create, deactivate)
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

from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData
from clearledgr.services.approval_delegation import DelegationService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


# ---------------------------------------------------------------------------
# Rule CRUD tests
# ---------------------------------------------------------------------------

class TestDelegationRules:
    def test_create_and_list(self, db):
        svc = DelegationService("default")
        rule = svc.create_rule(
            delegator_id="u1", delegator_email="alice@co.com",
            delegate_id="u2", delegate_email="bob@co.com",
            reason="Annual leave",
        )
        assert rule["id"].startswith("dlg_")
        assert rule["is_active"] is True

        rules = svc.list_rules()
        assert len(rules) == 1
        assert rules[0]["delegator_email"] == "alice@co.com"

    def test_deactivate(self, db):
        svc = DelegationService("default")
        rule = svc.create_rule(
            delegator_id="u1", delegator_email="alice@co.com",
            delegate_id="u2", delegate_email="bob@co.com",
        )
        assert svc.deactivate_rule(rule["id"]) is True

        rules = svc.list_rules(active_only=True)
        assert len(rules) == 0

        all_rules = svc.list_rules(active_only=False)
        assert len(all_rules) == 1
        assert all_rules[0]["is_active"] is False

    def test_get_rule(self, db):
        svc = DelegationService("default")
        rule = svc.create_rule(
            delegator_id="u1", delegator_email="a@co.com",
            delegate_id="u2", delegate_email="b@co.com",
        )
        found = svc.get_rule(rule["id"])
        assert found is not None
        assert found["delegate_email"] == "b@co.com"

    def test_get_nonexistent(self, db):
        svc = DelegationService("default")
        assert svc.get_rule("dlg_nonexistent") is None


# ---------------------------------------------------------------------------
# Delegate resolution tests
# ---------------------------------------------------------------------------

class TestDelegateResolution:
    def test_finds_delegate(self, db):
        svc = DelegationService("default")
        svc.create_rule(
            delegator_id="u1", delegator_email="alice@co.com",
            delegate_id="u2", delegate_email="bob@co.com",
        )
        assert svc.get_delegate_for("alice@co.com") == "bob@co.com"

    def test_no_delegate_returns_none(self, db):
        svc = DelegationService("default")
        assert svc.get_delegate_for("charlie@co.com") is None

    def test_date_range_active(self, db):
        svc = DelegationService("default")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        svc.create_rule(
            delegator_id="u1", delegator_email="alice@co.com",
            delegate_id="u2", delegate_email="bob@co.com",
            starts_at=yesterday, ends_at=tomorrow,
        )
        assert svc.get_delegate_for("alice@co.com") == "bob@co.com"

    def test_date_range_not_yet_started(self, db):
        svc = DelegationService("default")
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        next_week = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        svc.create_rule(
            delegator_id="u1", delegator_email="alice@co.com",
            delegate_id="u2", delegate_email="bob@co.com",
            starts_at=tomorrow, ends_at=next_week,
        )
        assert svc.get_delegate_for("alice@co.com") is None

    def test_date_range_expired(self, db):
        svc = DelegationService("default")
        last_week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        svc.create_rule(
            delegator_id="u1", delegator_email="alice@co.com",
            delegate_id="u2", delegate_email="bob@co.com",
            starts_at=last_week, ends_at=yesterday,
        )
        assert svc.get_delegate_for("alice@co.com") is None

    def test_resolve_approvers_swaps_delegated(self, db):
        svc = DelegationService("default")
        svc.create_rule(
            delegator_id="u1", delegator_email="alice@co.com",
            delegate_id="u2", delegate_email="bob@co.com",
        )
        resolved = svc.resolve_approvers(["alice@co.com", "charlie@co.com"])
        assert resolved == ["bob@co.com", "charlie@co.com"]

    def test_resolve_approvers_no_delegation(self, db):
        svc = DelegationService("default")
        resolved = svc.resolve_approvers(["alice@co.com", "bob@co.com"])
        assert resolved == ["alice@co.com", "bob@co.com"]


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestDelegationEndpoints:
    @pytest.fixture()
    def client(self, db):
        from main import app
        from clearledgr.api import workspace_shell as ws_module

        def _fake_user():
            return TokenData(
                user_id="dlg-user",
                email="dlg@test.com",
                organization_id="default",
                role="owner",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        app.dependency_overrides[ws_module.get_current_user] = _fake_user
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(ws_module.get_current_user, None)

    def test_create_rule(self, client, db):
        resp = client.post(
            "/api/workspace/delegation-rules",
            json={
                "delegator_email": "alice@co.com",
                "delegate_email": "bob@co.com",
                "reason": "OOO 10-15 April",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["delegator_email"] == "alice@co.com"
        assert data["delegate_email"] == "bob@co.com"

    def test_list_rules(self, client, db):
        svc = DelegationService("default")
        svc.create_rule("u1", "a@co.com", "u2", "b@co.com")
        resp = client.get("/api/workspace/delegation-rules")
        assert resp.status_code == 200
        assert len(resp.json()["rules"]) == 1

    def test_deactivate_rule(self, client, db):
        svc = DelegationService("default")
        rule = svc.create_rule("u1", "a@co.com", "u2", "b@co.com")
        resp = client.post(f"/api/workspace/delegation-rules/{rule['id']}/deactivate")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deactivated"

    def test_create_missing_fields(self, client, db):
        resp = client.post(
            "/api/workspace/delegation-rules",
            json={"delegator_email": "a@co.com"},
        )
        assert resp.status_code == 400

    def test_deactivate_nonexistent(self, client, db):
        resp = client.post("/api/workspace/delegation-rules/dlg_nonexistent/deactivate")
        assert resp.status_code == 404
