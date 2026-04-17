"""Tests for DESIGN_THESIS §12 V1-boundary feature flags.

Covers the three behaviours the boundary relies on:

  1. Flag resolution — env var true/false parsing, defaults.
  2. Surface gating — routes return 404, autopilot early-returns,
     service-layer card senders skip cleanly when disabled.
  3. Strict-profile allowlist — Outlook/Teams paths fall off the
     allowed set when their flag is off, so the middleware layer
     404s before the handler dependency even runs.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from clearledgr.core import feature_flags


class TestFlagResolution:
    def test_defaults_are_both_off(self, monkeypatch):
        monkeypatch.delenv("FEATURE_OUTLOOK_ENABLED", raising=False)
        monkeypatch.delenv("FEATURE_TEAMS_ENABLED", raising=False)
        assert feature_flags.is_outlook_enabled() is False
        assert feature_flags.is_teams_enabled() is False

    def test_truthy_values_enable_flags(self, monkeypatch):
        for truthy in ("true", "1", "yes", "on", "enabled", "TRUE", " True "):
            monkeypatch.setenv("FEATURE_OUTLOOK_ENABLED", truthy)
            assert feature_flags.is_outlook_enabled() is True, f"failed for {truthy!r}"

    def test_falsy_values_disable_flags(self, monkeypatch):
        for falsy in ("", "false", "0", "no", "off", "disabled", "maybe"):
            monkeypatch.setenv("FEATURE_OUTLOOK_ENABLED", falsy)
            assert feature_flags.is_outlook_enabled() is False, f"failed for {falsy!r}"

    def test_outlook_and_teams_flags_are_independent(self, monkeypatch):
        monkeypatch.setenv("FEATURE_OUTLOOK_ENABLED", "true")
        monkeypatch.setenv("FEATURE_TEAMS_ENABLED", "false")
        assert feature_flags.is_outlook_enabled() is True
        assert feature_flags.is_teams_enabled() is False


class TestOutlookRouteGating:
    def test_outlook_routes_404_when_disabled(self, monkeypatch):
        monkeypatch.delenv("FEATURE_OUTLOOK_ENABLED", raising=False)
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        # All 5 Outlook endpoints should reject with the V1 reason.
        for path, method in [
            ("/outlook/connect/start", "get"),
            ("/outlook/callback?code=x&state=y", "get"),
            ("/outlook/disconnect", "post"),
            ("/outlook/status", "get"),
            ("/outlook/webhook", "post"),
        ]:
            resp = getattr(client, method)(path)
            assert resp.status_code == 404, f"{path} should 404, got {resp.status_code}"
            body = resp.json()
            # The middleware layer strips Outlook paths off the
            # allowlist when the flag is off, so the 404 surface is
            # the legacy-surface-guard rather than the router
            # dependency — both carry DESIGN_THESIS-linked reasons.
            detail = body.get("detail")
            if isinstance(detail, dict):
                assert "outlook" in str(detail).lower() or "ap_v1_profile" in str(detail).lower()
            else:
                assert "ap_v1_profile" in str(detail).lower() or "outlook" in str(detail).lower()


class TestTeamsRouteGating:
    def test_teams_invoices_interactive_404_when_disabled(self, monkeypatch):
        monkeypatch.delenv("FEATURE_TEAMS_ENABLED", raising=False)
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        resp = client.post("/teams/invoices/interactive", json={})
        assert resp.status_code == 404

    def test_workspace_teams_integration_routes_404_when_disabled(self, monkeypatch):
        monkeypatch.delenv("FEATURE_TEAMS_ENABLED", raising=False)
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        for path in [
            "/api/workspace/integrations/teams/test",
            "/api/workspace/integrations/teams/webhook",
        ]:
            resp = client.post(path, json={"organization_id": "default"})
            assert resp.status_code == 404, f"{path} should 404, got {resp.status_code}"


class TestStrictProfileAllowlist:
    def test_outlook_paths_stripped_when_disabled(self, monkeypatch):
        monkeypatch.delenv("FEATURE_OUTLOOK_ENABLED", raising=False)
        from main import _is_strict_profile_allowed_path
        for path in [
            "/outlook/connect/start",
            "/outlook/callback",
            "/outlook/disconnect",
            "/outlook/status",
            "/outlook/webhook",
        ]:
            assert _is_strict_profile_allowed_path(path) is False, (
                f"{path} should not be allowed when Outlook flag is off"
            )

    def test_outlook_paths_allowed_when_enabled(self, monkeypatch):
        monkeypatch.setenv("FEATURE_OUTLOOK_ENABLED", "true")
        from main import _is_strict_profile_allowed_path
        for path in [
            "/outlook/connect/start",
            "/outlook/callback",
            "/outlook/webhook",
        ]:
            assert _is_strict_profile_allowed_path(path) is True, (
                f"{path} should be allowed when Outlook flag is on"
            )

    def test_teams_paths_stripped_when_disabled(self, monkeypatch):
        monkeypatch.delenv("FEATURE_TEAMS_ENABLED", raising=False)
        from main import _is_strict_profile_allowed_path
        for path in [
            "/teams/invoices/interactive",
            "/teams/invoices/some/other",
            "/api/workspace/integrations/teams/test",
            "/api/workspace/integrations/teams/webhook",
        ]:
            assert _is_strict_profile_allowed_path(path) is False, (
                f"{path} should not be allowed when Teams flag is off"
            )

    def test_teams_paths_allowed_when_enabled(self, monkeypatch):
        monkeypatch.setenv("FEATURE_TEAMS_ENABLED", "true")
        from main import _is_strict_profile_allowed_path
        for path in [
            "/teams/invoices/interactive",
            "/api/workspace/integrations/teams/test",
            "/api/workspace/integrations/teams/webhook",
        ]:
            assert _is_strict_profile_allowed_path(path) is True, (
                f"{path} should be allowed when Teams flag is on"
            )

    def test_non_gated_paths_unaffected_by_flag_state(self, monkeypatch):
        # Gmail + Slack paths are V1 surfaces and must stay allowed
        # regardless of Outlook/Teams flag state.
        monkeypatch.delenv("FEATURE_OUTLOOK_ENABLED", raising=False)
        monkeypatch.delenv("FEATURE_TEAMS_ENABLED", raising=False)
        from main import _is_strict_profile_allowed_path
        for path in [
            "/slack/interactions",
            "/slack/invoices/interactive",
            "/api/workspace/integrations/slack/test",
            "/api/ap/audit/recent",
        ]:
            assert _is_strict_profile_allowed_path(path) is True, (
                f"{path} should always be allowed (V1 surface)"
            )


class TestBootstrapTeamsStatus:
    def test_teams_status_reports_disabled_in_v1(self, monkeypatch):
        monkeypatch.delenv("FEATURE_TEAMS_ENABLED", raising=False)
        from clearledgr.api.workspace_shell import _teams_status_for_org

        result = _teams_status_for_org("default")
        assert result["status"] == "disabled_in_v1"
        assert result["connected"] is False
        # Reason string points the extension UI at the thesis so the
        # disabled state has a rationale, not just an empty card.
        assert "Teams" in result["reason"]

    def test_teams_status_reflects_real_state_when_enabled(self, monkeypatch):
        monkeypatch.setenv("FEATURE_TEAMS_ENABLED", "true")
        from clearledgr.api.workspace_shell import _teams_status_for_org

        # With the flag on, the function falls through to the real DB
        # lookup. No webhook configured → connected=False but the
        # disabled_in_v1 terminal status is gone.
        result = _teams_status_for_org("default")
        assert result["status"] != "disabled_in_v1"


class TestTeamsCardSkipping:
    def test_teams_budget_card_returns_skipped_when_disabled(self, monkeypatch):
        monkeypatch.delenv("FEATURE_TEAMS_ENABLED", raising=False)
        # Minimal stub — we only need _send_teams_budget_card to
        # check the flag and short-circuit.
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService
        svc = InvoiceWorkflowService.__new__(InvoiceWorkflowService)
        svc.organization_id = "default"
        # Attribute accesses after the flag check won't happen, so
        # we don't need to populate teams_client, invoice, etc.
        result = svc._send_teams_budget_card(
            invoice=None,  # type: ignore[arg-type]
            budget_summary={},
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "teams_disabled_in_v1"


class TestOutlookAutopilotGating:
    @pytest.mark.asyncio
    async def test_autopilot_skips_when_disabled(self, monkeypatch):
        monkeypatch.delenv("FEATURE_OUTLOOK_ENABLED", raising=False)
        # Make start_outlook_autopilot a spy — if the flag skip fires,
        # the import (and therefore the spy) is never reached.
        import clearledgr.services.app_startup as app_startup_mod
        from unittest.mock import AsyncMock, MagicMock, patch

        spy = AsyncMock()
        fake_module = MagicMock()
        fake_module.start_outlook_autopilot = spy

        # Other imports inside run_deferred_startup can fail freely;
        # the test only cares that start_outlook_autopilot is not
        # called.
        with patch.dict(
            "sys.modules",
            {"clearledgr.services.outlook_autopilot": fake_module},
        ):
            # Swallow any exceptions from unrelated sibling tasks in
            # run_deferred_startup — each is wrapped in its own
            # try/except already, so none should propagate, but be
            # defensive for future refactors that add new siblings.
            try:
                await app_startup_mod.run_deferred_startup(app=MagicMock())
            except Exception:
                pass

        spy.assert_not_called()
