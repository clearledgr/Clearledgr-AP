"""Regression tests for the LLM cost hard-cap (B.2 MVP, 2026-04-22).

Covers the runaway-spend guard wired into ``LLMGateway.call()``:
- Below-cap calls proceed normally (no side effect).
- At-or-above-cap calls pause the org, fire alerts/webhook/audit,
  and raise ``LLMBudgetExceededError``.
- Paused org fast-fails without re-querying cost (the tombstone IS
  the answer).
- A new billing month clears the tombstone automatically.
- Override endpoints (customer CFO + ops CS) clear the pause, are
  role-gated, and write an audit event.

Everything is tested against a temp-file SQLite DB via the standard
monkeypatch fixture pattern used across the rest of the suite.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData
from clearledgr.core.llm_gateway import (
    LLMAction,
    LLMBudgetExceededError,
    LLMGateway,
    reset_llm_gateway,
)
from clearledgr.services.subscription import (
    PlanLimits,
    PlanTier,
    get_subscription_service,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    # SubscriptionService caches the DB reference at construction —
    # reset its singleton too so the per-test DB wins.
    import clearledgr.services.subscription as _sub_mod
    _sub_mod._subscription_service = None
    inst = db_module.get_db()
    inst.initialize()
    # Every test operates on an org row with a known subscription tier.
    inst.ensure_organization("budget-test-org", organization_name="Budget Test")
    reset_llm_gateway()
    yield inst
    reset_llm_gateway()
    _sub_mod._subscription_service = None


@pytest.fixture()
def gateway(db):
    """Fresh gateway bound to the test DB."""
    gw = LLMGateway(api_key="test-key-not-called", db=db)
    return gw


# ---------------------------------------------------------------------------
# Tier defaults
# ---------------------------------------------------------------------------


class TestPlanLimitsTierDefaults:
    """Tier defaults match the plan: FREE $10, STARTER $50, PRO $250, ENT $5000."""

    def test_free_tier_cap_is_ten(self):
        assert PlanLimits.for_tier(PlanTier.FREE).monthly_llm_cost_usd_hard_cap == 10.0

    def test_starter_tier_cap_is_fifty(self):
        assert PlanLimits.for_tier(PlanTier.STARTER).monthly_llm_cost_usd_hard_cap == 50.0

    def test_professional_tier_cap_is_two_fifty(self):
        assert PlanLimits.for_tier(PlanTier.PROFESSIONAL).monthly_llm_cost_usd_hard_cap == 250.0

    def test_enterprise_tier_cap_is_five_thousand(self):
        """Enterprise is bounded even though other limits are -1 unlimited.
        This is intentional — a runaway guard without a ceiling is useless."""
        cap = PlanLimits.for_tier(PlanTier.ENTERPRISE).monthly_llm_cost_usd_hard_cap
        assert cap == 5000.0
        assert cap != -1


class TestGetEffectiveLlmCostCap:
    """Override precedence: per-org settings > tier default > safe fallback."""

    def test_returns_tier_default_for_known_org(self, db):
        svc = get_subscription_service()
        # ensure_organization creates a row with default (FREE) tier.
        cap = svc.get_effective_llm_cost_cap("budget-test-org")
        assert cap > 0
        # FREE tier default.
        assert cap == 10.0

    def test_respects_per_org_override_in_settings_json(self, db):
        db.update_organization(
            "budget-test-org",
            settings={"llm_cost_hard_cap_usd_override": 123.45},
        )
        svc = get_subscription_service()
        assert svc.get_effective_llm_cost_cap("budget-test-org") == 123.45

    def test_falls_back_to_safe_floor_for_unknown_org(self, db):
        svc = get_subscription_service()
        # No row → get_subscription will auto-create at FREE tier.
        cap = svc.get_effective_llm_cost_cap("nonexistent-org")
        assert cap >= 10.0  # At minimum the FREE tier floor


# ---------------------------------------------------------------------------
# Gateway pre-flight check
# ---------------------------------------------------------------------------


class TestBudgetCapEnforcement:

    def test_below_cap_call_proceeds_normally(self, gateway, db):
        """Cost < cap → _enforce_budget_cap returns silently."""
        with patch.object(
            get_subscription_service(),
            "_get_llm_cost_this_month",
            return_value={"total_cost_usd": 3.00},
        ):
            # Does not raise.
            gateway._enforce_budget_cap("budget-test-org")

        # Tombstone must not be set on a healthy call.
        org = db.get_organization("budget-test-org")
        assert not org.get("llm_cost_paused_at")

    def test_over_cap_pauses_org_and_raises(self, gateway, db):
        """Cost >= cap → stamp tombstone, raise LLMBudgetExceededError."""
        with patch.object(
            get_subscription_service(),
            "_get_llm_cost_this_month",
            return_value={"total_cost_usd": 99.99},  # >> FREE $10 cap
        ):
            with pytest.raises(LLMBudgetExceededError) as exc_info:
                gateway._enforce_budget_cap("budget-test-org")
            # The raise message must not contain "llm" (avoid tripping
            # the coordination engine's LLM-failure classifier into
            # template fallback — we want this classified as persistent).
            msg = str(exc_info.value).lower()
            assert "budget" in msg
            # Defence-in-depth: assert the exact substring isn't present.
            # (Class name may contain "llm" but the rendered message
            #  should not.)
            assert "llm " not in msg and "anthropic" not in msg

        # Tombstone must now be stamped.
        org = db.get_organization("budget-test-org")
        assert org.get("llm_cost_paused_at"), "expected llm_cost_paused_at to be set"

    def test_paused_org_fast_fails_without_cost_query(self, gateway, db):
        """When the tombstone is set THIS month, we must NOT re-query cost.
        The tombstone is the answer; a cost query on every fast-fail is
        exactly the waste we're trying to avoid for a paused workspace."""
        # Stamp the tombstone directly — as if a prior call tripped it.
        db.update_organization(
            "budget-test-org",
            llm_cost_paused_at=datetime.now(timezone.utc).isoformat(),
        )

        with patch.object(
            get_subscription_service(),
            "_get_llm_cost_this_month",
        ) as cost_mock:
            with pytest.raises(LLMBudgetExceededError):
                gateway._enforce_budget_cap("budget-test-org")
            assert cost_mock.call_count == 0, (
                f"paused workspace must NOT trigger a cost query; "
                f"got {cost_mock.call_count} calls"
            )

    def test_new_billing_month_auto_clears_pause(self, gateway, db):
        """Tombstone from a prior calendar month → clear and proceed."""
        # Stamp with a timestamp from a month ago.
        last_month = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
        db.update_organization("budget-test-org", llm_cost_paused_at=last_month)

        with patch.object(
            get_subscription_service(),
            "_get_llm_cost_this_month",
            return_value={"total_cost_usd": 1.00},  # Well under cap
        ):
            # Does not raise — the stale tombstone is cleared.
            gateway._enforce_budget_cap("budget-test-org")

        # Tombstone is gone after the roll-over.
        org = db.get_organization("budget-test-org")
        assert not org.get("llm_cost_paused_at")


class TestBudgetCapSideEffects:
    """When pause fires, three side effects must land: CS alert, webhook, audit."""

    def test_pause_calls_alert_cs_team(self, gateway, db):
        # Patch at the defining module — llm_gateway imports alert_cs_team
        # inside _trip_budget_cap via `from clearledgr.services.monitoring
        # import alert_cs_team`, so the patch target is the defining module.
        with patch("clearledgr.services.webhook_delivery.emit_webhook_event", AsyncMock()), \
             patch("clearledgr.services.monitoring.alert_cs_team") as alert_mock, \
             patch.object(
                get_subscription_service(),
                "_get_llm_cost_this_month",
                return_value={"total_cost_usd": 50.00},  # over FREE $10 cap
        ):
            with pytest.raises(LLMBudgetExceededError):
                gateway._enforce_budget_cap("budget-test-org")

        assert alert_mock.called, "expected alert_cs_team to be called"
        call_kwargs = alert_mock.call_args.kwargs
        assert call_kwargs.get("severity") == "error"
        assert call_kwargs.get("organization_id") == "budget-test-org"
        assert "LLM budget exceeded" in call_kwargs.get("title", "")

    def test_pause_emits_webhook_event(self, gateway, db):
        mock_emit = AsyncMock(return_value=0)
        with patch("clearledgr.services.webhook_delivery.emit_webhook_event", mock_emit), \
             patch("clearledgr.services.monitoring.alert_cs_team"), \
             patch.object(
                get_subscription_service(),
                "_get_llm_cost_this_month",
                return_value={"total_cost_usd": 50.00},
        ):
            with pytest.raises(LLMBudgetExceededError):
                gateway._enforce_budget_cap("budget-test-org")

        assert mock_emit.called, "expected billing.llm_budget_exceeded webhook emit"
        kwargs = mock_emit.call_args.kwargs
        assert kwargs.get("event_type") == "billing.llm_budget_exceeded"
        payload = kwargs.get("payload") or {}
        assert payload.get("organization_id") == "budget-test-org"
        assert payload.get("cost_usd") == 50.00
        assert payload.get("cap_usd") == 10.0

    def test_pause_appends_audit_event(self, gateway, db):
        with patch("clearledgr.services.webhook_delivery.emit_webhook_event", AsyncMock()), \
             patch("clearledgr.services.monitoring.alert_cs_team"), \
             patch.object(
                get_subscription_service(),
                "_get_llm_cost_this_month",
                return_value={"total_cost_usd": 50.00},
        ):
            with pytest.raises(LLMBudgetExceededError):
                gateway._enforce_budget_cap("budget-test-org")

        # Read the audit_events row we just wrote. Schema uses `ts`
        # (timestamp) column, not `created_at`. Org-level events are
        # keyed with box_type='organization' + box_id=org_id.
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT event_type, actor_type, actor_id, organization_id, "
                    "       box_id, box_type "
                    "FROM audit_events "
                    "WHERE organization_id = %s AND event_type = %s "
                    "ORDER BY ts DESC LIMIT 1"
                ),
                ("budget-test-org", "llm_budget_paused"),
            )
            row = cur.fetchone()

        assert row is not None, "expected llm_budget_paused audit event"
        row = dict(row)
        assert row["event_type"] == "llm_budget_paused"
        assert row["actor_type"] == "system"
        assert row["actor_id"] == "llm_gateway"
        assert row["box_type"] == "organization"
        assert row["box_id"] == "budget-test-org"


# ---------------------------------------------------------------------------
# Override endpoints
# ---------------------------------------------------------------------------


def _make_test_client(db, *, role: str = "cfo", org_id: str = "budget-test-org"):
    from main import app
    from clearledgr.api import workspace_shell as ws_module
    from clearledgr.api import ops as ops_module

    def _fake_user():
        return TokenData(
            user_id=f"user-{role}",
            email=f"{role}@example.com",
            organization_id=org_id,
            role=role,
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    app.dependency_overrides[ws_module.get_current_user] = _fake_user
    app.dependency_overrides[ops_module.get_current_user] = _fake_user
    client = TestClient(app)
    return client, ws_module, ops_module


class TestCustomerOverrideEndpoint:
    """POST /api/workspace/llm-budget/override — CFO role required."""

    def test_cfo_override_clears_pause(self, db):
        db.update_organization(
            "budget-test-org",
            llm_cost_paused_at=datetime.now(timezone.utc).isoformat(),
        )
        client, ws_module, ops_module = _make_test_client(db, role="cfo")
        try:
            resp = client.post(
                "/api/workspace/llm-budget/override",
                json={"reason": "end-of-quarter legitimate spike; Q3 close"},
            )
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "cleared"
        assert body["organization_id"] == "budget-test-org"

        # Tombstone is gone.
        org = db.get_organization("budget-test-org")
        assert not org.get("llm_cost_paused_at")

    def test_owner_can_override(self, db):
        """OWNER outranks CFO — should also succeed."""
        db.update_organization(
            "budget-test-org",
            llm_cost_paused_at=datetime.now(timezone.utc).isoformat(),
        )
        client, ws_module, ops_module = _make_test_client(db, role="owner")
        try:
            resp = client.post(
                "/api/workspace/llm-budget/override",
                json={"reason": "owner override"},
            )
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)
        assert resp.status_code == 200

    def test_ap_manager_blocked_by_role_gate(self, db):
        """Lower roles (AP Manager, Financial Controller) must 403."""
        client, ws_module, ops_module = _make_test_client(db, role="ap_manager")
        try:
            resp = client.post(
                "/api/workspace/llm-budget/override",
                json={"reason": "trying to sneak past the gate"},
            )
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)
        assert resp.status_code == 403
        assert "cfo" in resp.json()["detail"].lower()

    def test_override_writes_audit_event(self, db):
        db.update_organization(
            "budget-test-org",
            llm_cost_paused_at=datetime.now(timezone.utc).isoformat(),
        )
        client, ws_module, ops_module = _make_test_client(db, role="cfo")
        try:
            client.post(
                "/api/workspace/llm-budget/override",
                json={"reason": "legitimate operational need"},
            )
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT event_type, actor_type, decision_reason "
                    "FROM audit_events "
                    "WHERE organization_id = %s AND event_type = %s "
                    "ORDER BY ts DESC LIMIT 1"
                ),
                ("budget-test-org", "llm_budget_override_applied"),
            )
            row = dict(cur.fetchone() or {})

        assert row.get("event_type") == "llm_budget_override_applied"
        assert row.get("actor_type") == "user"
        assert row.get("decision_reason") == "legitimate operational need"


class TestLlmBudgetStatusEndpoint:
    """GET /api/workspace/llm-budget/status — drives the in-product banner.

    The banner needs four things to render correctly: (a) is this
    workspace paused right now, (b) what's the cost vs cap, (c) when
    does the cycle reset, (d) can this specific user lift the pause
    without leaving Gmail. All four come from this endpoint.
    """

    def test_unpaused_org_returns_paused_false(self, db):
        client, ws_module, ops_module = _make_test_client(db, role="ap_clerk")
        try:
            resp = client.get("/api/workspace/llm-budget/status")
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["paused"] is False
        assert body["paused_at"] is None
        assert body["cap_usd"] > 0  # Tier default kicks in
        assert "period_start" in body and "period_end" in body

    def test_paused_org_returns_paused_true_with_timestamp(self, db):
        paused_at = datetime.now(timezone.utc).isoformat()
        db.update_organization("budget-test-org", llm_cost_paused_at=paused_at)
        client, ws_module, ops_module = _make_test_client(db, role="ap_clerk")
        try:
            resp = client.get("/api/workspace/llm-budget/status")
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["paused"] is True
        assert body["paused_at"] == paused_at

    def test_can_override_is_true_for_cfo(self, db):
        client, ws_module, ops_module = _make_test_client(db, role="cfo")
        try:
            resp = client.get("/api/workspace/llm-budget/status")
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)
        assert resp.status_code == 200
        assert resp.json()["can_override"] is True

    def test_can_override_is_true_for_owner(self, db):
        client, ws_module, ops_module = _make_test_client(db, role="owner")
        try:
            resp = client.get("/api/workspace/llm-budget/status")
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)
        assert resp.status_code == 200
        assert resp.json()["can_override"] is True

    def test_can_override_is_false_for_ap_clerk(self, db):
        client, ws_module, ops_module = _make_test_client(db, role="ap_clerk")
        try:
            resp = client.get("/api/workspace/llm-budget/status")
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)
        assert resp.status_code == 200
        assert resp.json()["can_override"] is False

    def test_status_respects_per_org_override(self, db):
        db.update_organization(
            "budget-test-org",
            settings={"llm_cost_hard_cap_usd_override": 777.77},
        )
        client, ws_module, ops_module = _make_test_client(db, role="ap_clerk")
        try:
            resp = client.get("/api/workspace/llm-budget/status")
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)
        assert resp.status_code == 200
        assert resp.json()["cap_usd"] == 777.77


class TestOpsResetEndpoint:
    """POST /api/ops/llm-budget/reset — CS-accessible for incidents."""

    def test_admin_can_reset(self, db):
        db.update_organization(
            "budget-test-org",
            llm_cost_paused_at=datetime.now(timezone.utc).isoformat(),
        )
        client, ws_module, ops_module = _make_test_client(db, role="owner")
        try:
            resp = client.post(
                "/api/ops/llm-budget/reset",
                params={
                    "organization_id": "budget-test-org",
                    "reason": "cs incident ticket #123",
                },
            )
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cleared"

        org = db.get_organization("budget-test-org")
        assert not org.get("llm_cost_paused_at")

    def test_non_admin_blocked(self, db):
        client, ws_module, ops_module = _make_test_client(db, role="ap_clerk")
        try:
            resp = client.post(
                "/api/ops/llm-budget/reset",
                params={
                    "organization_id": "budget-test-org",
                    "reason": "trying without admin",
                },
            )
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)
        assert resp.status_code == 403

    def test_ops_reset_audit_distinguishes_actor(self, db):
        """The ops reset uses actor_type='cs_team' so the audit trail
        distinguishes CS-initiated resets from customer CFO overrides."""
        db.update_organization(
            "budget-test-org",
            llm_cost_paused_at=datetime.now(timezone.utc).isoformat(),
        )
        client, ws_module, ops_module = _make_test_client(db, role="owner")
        try:
            client.post(
                "/api/ops/llm-budget/reset",
                params={
                    "organization_id": "budget-test-org",
                    "reason": "cs ticket",
                },
            )
        finally:
            from main import app
            app.dependency_overrides.pop(ws_module.get_current_user, None)
            app.dependency_overrides.pop(ops_module.get_current_user, None)

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT actor_type FROM audit_events "
                    "WHERE organization_id = %s AND event_type = %s "
                    "ORDER BY ts DESC LIMIT 1"
                ),
                ("budget-test-org", "llm_budget_override_applied"),
            )
            row = dict(cur.fetchone() or {})
        assert row.get("actor_type") == "cs_team"
