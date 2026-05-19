"""Tests for the workspace rule engine + API — Module 3.

Covers four layers:

  - Schema validation (validate_rule_body)
  - Evaluation (evaluate_rules with full trace)
  - Conflict detection (find_rule_conflicts at save time)
  - Store CRUD + version history + revert
  - API endpoints (create / update / delete / list / test / versions /
    revert / templates) including 422 on bad schema, 409 on
    conflicts, 404 on cross-tenant
  - End-to-end: rule wired into APDecisionService.decide so a matched
    rule overrides the 10-step cascade
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

from solden.api import workspace_rules as rule_routes  # noqa: E402
from solden.core import database as db_module  # noqa: E402
from solden.core.auth import get_current_user  # noqa: E402
from solden.services import rule_engine  # noqa: E402


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
        role="user",
    )


@pytest.fixture()
def client_orgA(db):
    app = FastAPI()
    app.include_router(rule_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgA")
    return TestClient(app)


@pytest.fixture()
def client_orgB(db):
    app = FastAPI()
    app.include_router(rule_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user("orgB")
    return TestClient(app)


def _create_rule_payload(**overrides):
    base = {
        "name": "Auto-approve <$1K USD",
        "priority": 100,
        "conditions": {
            "all_of": [
                {"field": "amount", "op": "lt", "value": 1000},
                {"field": "currency", "op": "eq", "value": "USD"},
            ],
        },
        "actions": [{"type": "auto_approve"}],
    }
    base.update(overrides)
    return base


# ─── Tests: Schema validation ────────────────────────────────────────


class TestValidation:
    def test_valid_body_returns_no_errors(self):
        errors = rule_engine.validate_rule_body(
            conditions={"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            actions=[{"type": "auto_approve"}],
        )
        assert errors == []

    def test_unknown_field_rejected(self):
        errors = rule_engine.validate_rule_body(
            conditions={"all_of": [{"field": "frobnicate", "op": "eq", "value": "x"}]},
            actions=[{"type": "auto_approve"}],
        )
        codes = {e.code for e in errors}
        assert "unknown_field" in codes

    def test_unknown_operator_rejected(self):
        errors = rule_engine.validate_rule_body(
            conditions={"all_of": [{"field": "amount", "op": "approximately", "value": 1000}]},
            actions=[{"type": "auto_approve"}],
        )
        codes = {e.code for e in errors}
        assert "unknown_op" in codes

    def test_in_op_requires_array_value(self):
        errors = rule_engine.validate_rule_body(
            conditions={"all_of": [{"field": "currency", "op": "in", "value": "USD"}]},
            actions=[{"type": "auto_approve"}],
        )
        codes = {e.code for e in errors}
        assert "value_must_be_array" in codes

    def test_route_to_role_requires_role(self):
        errors = rule_engine.validate_rule_body(
            conditions={"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            actions=[{"type": "route_to_role"}],
        )
        codes = {e.code for e in errors}
        assert "missing_role" in codes

    def test_empty_conditions_rejected(self):
        errors = rule_engine.validate_rule_body(
            conditions={},
            actions=[{"type": "auto_approve"}],
        )
        codes = {e.code for e in errors}
        assert "no_clauses" in codes

    def test_empty_actions_rejected(self):
        errors = rule_engine.validate_rule_body(
            conditions={"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            actions=[],
        )
        codes = {e.code for e in errors}
        assert "actions_empty" in codes


# ─── Tests: Evaluation + trace ──────────────────────────────────────


class TestEvaluation:
    def test_simple_match(self):
        rules = [{
            "id": "r1", "name": "low-amount", "priority": 100, "status": "active",
            "workflow": "ap", "entity_id": None, "created_at": "0",
            "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            "actions": [{"type": "auto_approve"}],
        }]
        result = rule_engine.evaluate_rules(
            {"amount": 500, "currency": "USD", "workflow": "ap"},
            rules,
        )
        assert result.matched_rule is not None
        assert result.matched_rule["id"] == "r1"
        assert result.matched_actions == [{"type": "auto_approve"}]

    def test_priority_order_wins(self):
        rules = [
            {
                "id": "r-second", "name": "second", "priority": 200, "status": "active",
                "workflow": "ap", "entity_id": None, "created_at": "0",
                "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 10000}]},
                "actions": [{"type": "route_to_role", "role": "ap_manager"}],
            },
            {
                "id": "r-first", "name": "first", "priority": 100, "status": "active",
                "workflow": "ap", "entity_id": None, "created_at": "0",
                "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 10000}]},
                "actions": [{"type": "auto_approve"}],
            },
        ]
        result = rule_engine.evaluate_rules(
            {"amount": 500, "workflow": "ap"}, rules,
        )
        # priority 100 wins over 200
        assert result.matched_rule["id"] == "r-first"

    def test_paused_rule_skipped(self):
        rules = [{
            "id": "r1", "name": "paused", "priority": 100, "status": "paused",
            "workflow": "ap", "entity_id": None, "created_at": "0",
            "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            "actions": [{"type": "auto_approve"}],
        }]
        result = rule_engine.evaluate_rules({"amount": 500, "workflow": "ap"}, rules)
        assert result.matched_rule is None
        assert result.rule_trace[0].skipped_reason == "status=paused"

    def test_entity_mismatch_skipped(self):
        rules = [{
            "id": "r1", "name": "eu-only", "priority": 100, "status": "active",
            "workflow": "ap", "entity_id": "eu-1", "created_at": "0",
            "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            "actions": [{"type": "auto_approve"}],
        }]
        result = rule_engine.evaluate_rules(
            {"amount": 500, "entity_id": "us-1", "workflow": "ap"}, rules,
        )
        assert result.matched_rule is None
        assert "entity_mismatch" in result.rule_trace[0].skipped_reason

    def test_full_trace_returned_for_test_mode(self):
        rules = [{
            "id": "r1", "name": "low", "priority": 100, "status": "active",
            "workflow": "ap", "entity_id": None, "created_at": "0",
            "conditions": {"all_of": [
                {"field": "amount", "op": "lt", "value": 1000},
                {"field": "currency", "op": "eq", "value": "USD"},
            ]},
            "actions": [{"type": "auto_approve"}],
        }]
        result = rule_engine.evaluate_rules(
            {"amount": 500, "currency": "EUR", "workflow": "ap"},  # currency mismatch
            rules,
        )
        assert result.matched_rule is None
        clauses = result.rule_trace[0].all_of_traces
        assert len(clauses) == 2
        assert clauses[0].matched is True   # amount <1000
        assert clauses[1].matched is False  # currency != USD


# ─── Tests: Conflict detection ──────────────────────────────────────


class TestConflictDetection:
    def test_same_priority_overlap_flagged(self):
        # Both rules at priority 100 catch the $500 probe — same-priority
        # overlap is unstable evaluation order, so the engine flags it.
        existing = [{
            "id": "old-r1", "name": "old-low", "priority": 100, "status": "active",
            "workflow": "ap", "entity_id": None, "created_at": "0",
            "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            "actions": [{"type": "auto_approve"}],
        }]
        candidate = {
            "id": "new-r1", "name": "new-low-usd", "priority": 100, "status": "active",
            "workflow": "ap", "entity_id": None, "created_at": "1",
            "conditions": {"all_of": [
                {"field": "amount", "op": "lt", "value": 1000},
                {"field": "currency", "op": "eq", "value": "USD"},
            ]},
            "actions": [{"type": "route_to_role", "role": "ap_manager"}],
        }
        conflicts = rule_engine.find_rule_conflicts(candidate, existing)
        kinds = {c.kind for c in conflicts}
        assert "same_priority_overlap" in kinds

    def test_redundant_rule_flagged(self):
        # Higher-priority rule (lower number) catches everything the
        # candidate would.
        existing = [{
            "id": "broad", "name": "broad-catch", "priority": 50, "status": "active",
            "workflow": "ap", "entity_id": None, "created_at": "0",
            "conditions": {"all_of": [{"field": "amount", "op": "lte", "value": 999999}]},
            "actions": [{"type": "auto_approve"}],
        }]
        candidate = {
            "id": "narrow", "name": "narrow-catch", "priority": 200, "status": "active",
            "workflow": "ap", "entity_id": None, "created_at": "1",
            "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            "actions": [{"type": "route_to_role", "role": "ap_manager"}],
        }
        conflicts = rule_engine.find_rule_conflicts(candidate, existing)
        kinds = {c.kind for c in conflicts}
        assert "redundant" in kinds

    def test_non_overlapping_rules_no_conflict(self):
        existing = [{
            "id": "high", "name": "high", "priority": 100, "status": "active",
            "workflow": "ap", "entity_id": None, "created_at": "0",
            "conditions": {"all_of": [{"field": "amount", "op": "gte", "value": 50000}]},
            "actions": [{"type": "require_dual_approval"}],
        }]
        candidate = {
            "id": "low", "name": "low", "priority": 200, "status": "active",
            "workflow": "ap", "entity_id": None, "created_at": "1",
            "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            "actions": [{"type": "auto_approve"}],
        }
        assert rule_engine.find_rule_conflicts(candidate, existing) == []


# ─── Tests: Store CRUD + version history ────────────────────────────


class TestStoreCRUD:
    def test_create_returns_normalised_rule(self, db):
        rule = db.create_rule({
            "organization_id": "orgA",
            "name": "low-amount",
            "priority": 100,
            "workflow": "ap",
            "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            "actions": [{"type": "auto_approve"}],
            "created_by": "user-1",
        })
        assert rule["id"].startswith("rule-")
        assert rule["status"] == "active"
        assert rule["version"] == 1
        assert rule["conditions"] == {"all_of": [{"field": "amount", "op": "lt", "value": 1000}]}
        assert rule["actions"] == [{"type": "auto_approve"}]

    def test_update_increments_version_and_snapshots(self, db):
        rule = db.create_rule({
            "organization_id": "orgA",
            "name": "low",
            "priority": 100,
            "workflow": "ap",
            "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            "actions": [{"type": "auto_approve"}],
            "created_by": "u1",
        })
        updated = db.update_rule(
            rule["id"], "orgA",
            actor="u2",
            change_note="raised threshold",
            conditions={"all_of": [{"field": "amount", "op": "lt", "value": 2000}]},
        )
        assert updated["version"] == 2
        versions = db.list_rule_versions(rule["id"], "orgA")
        assert len(versions) == 2

    def test_revert_writes_new_version_with_old_body(self, db):
        rule = db.create_rule({
            "organization_id": "orgA",
            "name": "v1",
            "priority": 100,
            "workflow": "ap",
            "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            "actions": [{"type": "auto_approve"}],
            "created_by": "u1",
        })
        db.update_rule(
            rule["id"], "orgA", actor="u1",
            change_note="bumped",
            conditions={"all_of": [{"field": "amount", "op": "lt", "value": 5000}]},
        )

        reverted = db.revert_rule_to_version(rule["id"], 1, "orgA", actor="u1")
        assert reverted["version"] == 3
        assert reverted["conditions"]["all_of"][0]["value"] == 1000

    def test_cross_tenant_update_blocked(self, db):
        rule = db.create_rule({
            "organization_id": "orgA",
            "name": "low",
            "priority": 100,
            "workflow": "ap",
            "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
            "actions": [{"type": "auto_approve"}],
            "created_by": "u1",
        })
        result = db.update_rule(rule["id"], "orgB", actor="orgB-user", priority=999)
        assert result is None
        unchanged = db.get_rule(rule["id"])
        assert unchanged["priority"] == 100


# ─── Tests: API endpoints ───────────────────────────────────────────


class TestAPI:
    def test_templates_returns_four(self, client_orgA):
        body = client_orgA.get("/api/workspace/rules/templates").json()
        assert len(body["templates"]) == 4
        names = {t["name"] for t in body["templates"]}
        assert any("Auto-approve" in n for n in names)
        assert any("dual approval" in n.lower() for n in names)

    def test_create_rule_returns_200(self, db, client_orgA):
        resp = client_orgA.post(
            "/api/workspace/rules",
            json=_create_rule_payload(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["rule"]["name"] == "Auto-approve <$1K USD"

    def test_create_with_invalid_body_returns_422(self, client_orgA):
        resp = client_orgA.post(
            "/api/workspace/rules",
            json=_create_rule_payload(
                conditions={"all_of": [{"field": "frobnicate", "op": "eq", "value": "x"}]},
            ),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "rule_validation_failed"

    def test_create_with_conflict_returns_409(self, db, client_orgA):
        client_orgA.post("/api/workspace/rules", json=_create_rule_payload())
        # Same rule again — same priority + same conditions = conflict
        resp = client_orgA.post(
            "/api/workspace/rules",
            json=_create_rule_payload(name="duplicate-low"),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["detail"]["code"] == "rule_conflict"
        assert len(body["detail"]["conflicts"]) >= 1

    def test_force_true_bypasses_conflict(self, db, client_orgA):
        client_orgA.post("/api/workspace/rules", json=_create_rule_payload())
        resp = client_orgA.post(
            "/api/workspace/rules",
            json=_create_rule_payload(name="duplicate-low", force=True),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "warnings" in body

    def test_list_isolated_by_org(self, db, client_orgA, client_orgB):
        client_orgA.post("/api/workspace/rules", json=_create_rule_payload())
        a_rules = client_orgA.get("/api/workspace/rules").json()["rules"]
        b_rules = client_orgB.get("/api/workspace/rules").json()["rules"]
        assert len(a_rules) >= 1
        assert b_rules == []

    def test_test_endpoint_returns_trace(self, db, client_orgA):
        client_orgA.post("/api/workspace/rules", json=_create_rule_payload())
        resp = client_orgA.post(
            "/api/workspace/rules/test",
            json={"invoice": {"amount": 500, "currency": "USD"}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"]["matched_rule_id"] is not None
        assert body["result"]["actions"] == [{"type": "auto_approve"}]
        assert isinstance(body["result"]["trace"], list)

    def test_test_endpoint_with_candidate_rule_preview(self, client_orgA):
        resp = client_orgA.post(
            "/api/workspace/rules/test",
            json={
                "invoice": {"amount": 500, "currency": "USD"},
                "candidate_rule": {
                    "name": "preview", "priority": 100,
                    "conditions": {"all_of": [{"field": "amount", "op": "lt", "value": 1000}]},
                    "actions": [{"type": "auto_approve"}],
                },
            },
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["matched_rule_id"] == "_candidate"

    def test_versions_endpoint(self, db, client_orgA):
        created = client_orgA.post(
            "/api/workspace/rules", json=_create_rule_payload(),
        ).json()["rule"]
        client_orgA.put(
            f"/api/workspace/rules/{created['id']}",
            json={"priority": 50, "force": True},
        )
        versions = client_orgA.get(
            f"/api/workspace/rules/{created['id']}/versions"
        ).json()["versions"]
        assert len(versions) == 2

    def test_revert_endpoint(self, db, client_orgA):
        created = client_orgA.post(
            "/api/workspace/rules", json=_create_rule_payload(),
        ).json()["rule"]
        client_orgA.put(
            f"/api/workspace/rules/{created['id']}",
            json={"priority": 999, "force": True},
        )
        reverted = client_orgA.post(
            f"/api/workspace/rules/{created['id']}/revert/1",
        ).json()["rule"]
        assert reverted["priority"] == 100

    def test_cross_tenant_get_returns_404(self, db, client_orgA, client_orgB):
        created = client_orgA.post(
            "/api/workspace/rules", json=_create_rule_payload(),
        ).json()["rule"]
        resp = client_orgB.get(f"/api/workspace/rules/{created['id']}/versions")
        assert resp.status_code == 404

    def test_delete_archives(self, db, client_orgA):
        created = client_orgA.post(
            "/api/workspace/rules", json=_create_rule_payload(),
        ).json()["rule"]
        client_orgA.delete(f"/api/workspace/rules/{created['id']}")
        active_list = client_orgA.get("/api/workspace/rules").json()["rules"]
        assert all(r["id"] != created["id"] for r in active_list)
        all_list = client_orgA.get(
            "/api/workspace/rules?include_inactive=true"
        ).json()["rules"]
        archived = next(r for r in all_list if r["id"] == created["id"])
        assert archived["status"] == "archived"


# ─── Tests: AP integration ──────────────────────────────────────────


class TestAPIntegration:
    """End-to-end: a rule landed in the rules table actually overrides
    the legacy 10-step cascade in APDecisionService."""

    def test_matched_rule_routes_via_decision_service(self, db):
        from solden.services.ap_decision import APDecisionService
        import asyncio

        # Create a rule that auto-approves under $1K USD.
        db.create_rule({
            "organization_id": "orgA",
            "name": "auto-low",
            "priority": 100,
            "workflow": "ap",
            "conditions": {
                "all_of": [
                    {"field": "amount", "op": "lt", "value": 1000},
                    {"field": "currency", "op": "eq", "value": "USD"},
                ],
            },
            "actions": [{"type": "auto_approve"}],
            "created_by": "u1",
        })

        invoice = SimpleNamespace(
            vendor_name="Test Vendor",
            amount=500.0,
            currency="USD",
            invoice_number="INV-1",
            confidence=0.6,  # below the legacy auto-approve threshold
            entity_id=None,
        )
        svc = APDecisionService()
        decision = asyncio.run(svc.decide(
            invoice,
            validation_gate={"passed": True, "reason_codes": []},
            org_config={"organization_id": "orgA"},
        ))
        assert decision.recommendation == "approve"
        assert decision.model == "rules:workspace"
        assert any("rule_matched:" in f for f in decision.risk_flags)

    def test_no_matching_rule_falls_through_to_cascade(self, db):
        from solden.services.ap_decision import APDecisionService
        import asyncio

        # Create a rule that only matches a different vendor.
        db.create_rule({
            "organization_id": "orgA",
            "name": "specific-vendor",
            "priority": 100,
            "workflow": "ap",
            "conditions": {"all_of": [{"field": "vendor_name", "op": "eq", "value": "Other"}]},
            "actions": [{"type": "auto_approve"}],
            "created_by": "u1",
        })

        invoice = SimpleNamespace(
            vendor_name="Test Vendor",
            amount=500.0,
            currency="USD",
            invoice_number="INV-1",
            confidence=0.97,  # high enough for legacy auto-approve
            entity_id=None,
        )
        svc = APDecisionService()
        decision = asyncio.run(svc.decide(
            invoice,
            validation_gate={"passed": True, "reason_codes": []},
            org_config={"organization_id": "orgA"},
        ))
        # No rule matched → legacy cascade kicked in (model="rules" not "rules:workspace")
        assert decision.model == "rules"
        assert decision.recommendation == "approve"
