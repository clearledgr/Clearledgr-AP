from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime

os.environ.setdefault("CLEARLEDGR_SKIP_DEFERRED_STARTUP", "true")


def _main_module():
    return importlib.import_module("main")


def _reload_main_module():
    module = _main_module()
    return importlib.reload(module)


def _runtime_surface_contract():
    return _main_module()._runtime_surface_contract()


def _should_skip_deferred_startup():
    return _main_module()._should_skip_deferred_startup()


def _app():
    return _main_module().app


def _strict_profile_allowed_prefixes():
    return _main_module().STRICT_PROFILE_ALLOWED_PREFIXES


def _mounted_paths() -> set[str]:
    paths: set[str] = set()
    for route in _app().router.routes:
        route_path = getattr(route, "path", None)
        if isinstance(route_path, str):
            paths.add(route_path)
    return paths


def test_strict_profile_blocks_legacy_surfaces(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    monkeypatch.delenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", raising=False)

    with TestClient(_app()) as client:
        blocked = client.get("/email/tasks")
        assert blocked.status_code == 404
        body = blocked.json()
        assert body["detail"] == "endpoint_disabled_in_ap_v1_profile"
        assert "/email/tasks" not in _mounted_paths()

        outlook_blocked = client.get("/outlook/status/user-1")
        assert outlook_blocked.status_code == 404
        outlook_body = outlook_blocked.json()
        assert outlook_body["detail"] == "endpoint_disabled_in_ap_v1_profile"
        assert outlook_body["reason"] == "non_canonical_surface_disabled"

        config_blocked = client.get("/config/organizations/default")
        assert config_blocked.status_code == 404
        assert config_blocked.json()["detail"] == "endpoint_disabled_in_ap_v1_profile"

        erp_legacy_blocked = client.get("/erp/status/default")
        assert erp_legacy_blocked.status_code == 404
        assert erp_legacy_blocked.json()["detail"] == "endpoint_disabled_in_ap_v1_profile"

        canonical = client.get("/health")
        assert canonical.status_code == 200


def test_strict_profile_contract_ignores_legacy_runtime_flags(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AP_V1_STRICT_SURFACES", "false")
    monkeypatch.setenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", "true")
    monkeypatch.setenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", "true")

    contract = _runtime_surface_contract()
    assert contract["production_like"] is True
    assert contract["strict_requested"] is True
    assert contract["strict_forced_on_in_production"] is False
    assert contract["strict_effective"] is True
    assert contract["legacy_override_requested"] is True
    assert contract["legacy_override_effective"] is False
    warnings = set(contract.get("warnings") or [])
    assert "legacy_override_ignored_strict_ap_v1" in warnings
    assert "strict_disable_request_ignored_strict_ap_v1" in warnings
    assert "allow_legacy_in_production_ignored_strict_ap_v1" in warnings

    with TestClient(_app()) as client:
        response = client.get("/email/tasks")
        assert response.status_code == 404
        body = response.json()
        assert body["detail"] == "endpoint_disabled_in_ap_v1_profile"
        assert "/email/tasks" not in _mounted_paths()

        mounted = _mounted_paths()
        assert "/outlook/status/{user_id}" not in mounted


def test_production_https_redirect_respects_proxy_headers_and_exempts_health(monkeypatch):
    """In production ENV, plain-HTTP requests must redirect to HTTPS
    unless the load balancer sets ``x-forwarded-proto: https`` (proxied
    TLS termination) OR the path is health-exempt. We can't use
    ``/openapi.json`` as the probe path because production disables
    OpenAPI entirely (``openapi_url=None``). Use an allow-listed ops
    endpoint instead — the HTTPS middleware fires before auth, so a
    401 response means the middleware let the request through.
    """
    monkeypatch.setenv("ENV", "production")

    app = _reload_main_module().app

    probe_path = "/api/ops/monitoring-health"

    with TestClient(app) as client:
        health = client.get("/health", follow_redirects=False)
        assert health.status_code == 200

        # Proxied HTTPS request — middleware should NOT redirect.
        # The request reaches auth and returns 401 (no creds). Any
        # non-3xx status proves the HTTPS middleware let it through.
        proxied = client.get(
            probe_path,
            headers={"x-forwarded-proto": "https"},
            follow_redirects=False,
        )
        assert proxied.status_code != 307, (
            f"Proxied HTTPS request was redirected (status={proxied.status_code})"
        )

        # Non-HTTPS request → should redirect to HTTPS.
        redirected = client.get(probe_path, follow_redirects=False)
        assert redirected.status_code == 307
        assert redirected.headers["location"].startswith("https://")


def test_web_process_role_skips_deferred_startup(monkeypatch):
    monkeypatch.delenv("CLEARLEDGR_SKIP_DEFERRED_STARTUP", raising=False)
    monkeypatch.setenv("CLEARLEDGR_PROCESS_ROLE", "web")

    assert _should_skip_deferred_startup() is True
    assert _runtime_surface_contract()["process_role"] == "web"


def test_worker_process_role_keeps_deferred_startup_enabled(monkeypatch):
    monkeypatch.delenv("CLEARLEDGR_SKIP_DEFERRED_STARTUP", raising=False)
    monkeypatch.setenv("CLEARLEDGR_PROCESS_ROLE", "worker")

    assert _should_skip_deferred_startup() is False
    assert _runtime_surface_contract()["process_role"] == "worker"


def test_legacy_surface_override_does_not_restore_deleted_legacy_routes(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AP_V1_STRICT_SURFACES", "false")
    monkeypatch.setenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", "true")
    monkeypatch.setenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", "true")

    with TestClient(_app()) as client:
        response = client.get("/email/tasks")
        assert response.status_code == 404
        assert "/email/tasks" not in _mounted_paths()


def test_strict_profile_filters_legacy_paths_from_openapi(monkeypatch):
    """Legacy paths must not be exposed in strict AP-v1 production.

    Production disables ``openapi.json`` entirely (``openapi_url=None``),
    so this test checks the filtering via the mounted route list —
    equivalent coverage without depending on an endpoint that doesn't
    exist in production.
    """
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    monkeypatch.delenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", raising=False)

    with TestClient(_app()) as _client:
        paths = _mounted_paths()

    assert "/email/tasks" not in paths
    assert "/audit/trail" not in paths
    assert "/outlook/status/{user_id}" not in paths
    assert "/config/organizations/{organization_id}" not in paths
    assert "/erp/status/{organization_id}" not in paths
    # The canonical agent intent surface must stay exposed.
    assert "/api/agent/intents/preview" in paths


def test_strict_profile_route_surface_is_minimized(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    monkeypatch.delenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", raising=False)

    with TestClient(_app()) as _client:
        paths = _mounted_paths()
        # Phase 2.1.b added 6 IBAN verification endpoints. Phase 3.1.b
        # added 4 vendor onboarding control endpoints + 4 public portal
        # endpoints. Phase 3.5 (2026-04-13) added §5.1 object-model
        # routes: /api/pipelines/* (5), /api/saved-views (2), /api/box-links,
        # /api/ops/vendor-onboarding/sessions, /api/user/preferences (split
        # from /api/workspace/user/preferences). Cap raised from 215 to 230.
        # 2026-04-20 hardening pass: 4 inbound ERP webhook endpoints
        # (/erp/webhooks/{qbo|xero|netsuite|sap}/{org_id}) — each does
        # HMAC signature verification before any processing; cap raised
        # 230 → 240 to accommodate them.
        # 2026-04-28: cap raised 240 → 275 to cover ERP-native panel
        # surfaces (NetSuite Suitelet panel + SAP Fiori extension —
        # ~10 endpoints across /extension/*), Box-exception admin
        # console (3 endpoints), agent-intents API surface (3 endpoints),
        # and assorted recent additions including /api/leads, plan-
        # observability hooks, and field-review batch routes.
        assert len(paths) <= 275
        assert not any(path.startswith("/config/") for path in paths)
        assert "/erp/status/{organization_id}" not in paths
        assert "/erp/quickbooks/connect" not in paths
        assert "/erp/xero/connect" not in paths
        assert "/api/workspace/vendor-intelligence/bootstrap" not in paths
        # Slack manifest is a valid integration endpoint
        assert "/api/workspace/integrations/slack/manifest" in paths
        assert "/marketplace/apps" not in paths
        # OAuth callbacks remain available for admin ERP install flows.
        assert "/erp/quickbooks/callback" in paths
        assert "/erp/xero/callback" in paths
        assert set(_strict_profile_allowed_prefixes()) == {
            "/v1",
            "/static",
            "/fraud-controls",
            # Phase 3.5: §5.1 object-model + organization settings prefixes
            "/api/pipelines",
            "/api/saved-views",
            "/api/box-links",
            "/settings",
            # DESIGN_THESIS.md §4.07 — frontend perf telemetry
            "/api/ui",
            # 2026-04-20 hardening pass: inbound ERP webhooks
            # (signature-verified, per-tenant URL-scoped).
            "/erp/webhooks",
            # Phase 9 customer-admin surface inside Gmail (Streak
            # pattern) — box exception queue + resolve. Org-scoped +
            # admin-role gated in the handlers.
            "/api/admin/box",
            # 2026-04-20+ hardening: append-only ops surfaces.
            # /api/policies — versioned policy snapshots (org-scoped).
            # /api/ops/projections — read-side projection refresh.
            # /api/ops/outbox — outbox queue inspection.
            "/api/policies",
            "/api/ops/projections",
            "/api/ops/outbox",
        }
        # Phase 2.1.b IBAN verification endpoints are mounted and pass
        # the strict-profile route filter.
        assert "/api/vendors/{vendor_name}/iban-verification" in paths
        assert "/api/vendors/{vendor_name}/iban-verification/complete" in paths
        assert "/api/vendors/{vendor_name}/iban-verification/reject" in paths
        # Phase 3.1.b vendor onboarding endpoints are mounted (customer
        # control + public portal magic-link surface).
        assert "/api/vendors/{vendor_name}/onboarding/invite" in paths
        assert "/api/vendors/{vendor_name}/onboarding/session" in paths
        assert "/api/vendors/{vendor_name}/onboarding/escalate" in paths
        assert "/api/vendors/{vendor_name}/onboarding/reject" in paths
        assert "/portal/onboard/{token}" in paths
        assert "/portal/onboard/{token}/kyc" in paths
        assert "/portal/onboard/{token}/bank-details" in paths


def test_strict_profile_blocks_unknown_prefixed_routes(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    monkeypatch.delenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", raising=False)

    with TestClient(_app()) as client:
        for path in (
            "/auth/noncanonical-probe",
            "/gmail/noncanonical-probe",
            "/api/agent/noncanonical-probe",
            "/api/ap/noncanonical-probe",
        ):
            blocked = client.get(path)
            assert blocked.status_code == 404
            assert blocked.json().get("detail") == "endpoint_disabled_in_ap_v1_profile"


def test_strict_profile_allows_canonical_ap_item_detail_route(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    monkeypatch.delenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", raising=False)

    with TestClient(_app()) as client:
        response = client.get("/api/ap/items/AP-SURFACE-PROBE?organization_id=default")
        assert response.status_code in {401, 404}
        assert response.json().get("detail") != "endpoint_disabled_in_ap_v1_profile"


def test_strict_profile_allows_explicit_gmail_thread_recovery(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    monkeypatch.delenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", raising=False)

    with TestClient(_app()) as client:
        response = client.post("/extension/by-thread/thread-surface-probe/recover?organization_id=default")
        assert response.status_code in {401, 404}
        assert response.json().get("detail") != "endpoint_disabled_in_ap_v1_profile"


def test_strict_profile_allows_workspace_user_preferences_route(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    monkeypatch.delenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", raising=False)

    with TestClient(_app()) as client:
        # /api/user/preferences (split from /api/workspace/user/preferences)
        response = client.patch(
            "/api/user/preferences",
            json={"organization_id": "default", "patch": {"gmail_extension": {"probe": True}}},
        )
        assert response.status_code in {401, 403, 404}
        assert response.json().get("detail") != "endpoint_disabled_in_ap_v1_profile"


def test_ap_runtime_registers_sidebar_core_intents():
    runtime = FinanceAgentRuntime(
        organization_id="default",
        actor_id="operator-1",
        actor_email="operator@example.com",
        db=MagicMock(),
    )

    supported = runtime.supported_intents
    assert "request_approval" in supported
    assert "approve_invoice" in supported
    assert "request_info" in supported
    assert "nudge_approval" in supported
    assert "reject_invoice" in supported
    assert "post_to_erp" in supported


def test_gmail_extension_mutations_delegate_to_runtime_owned_ap_contract():
    source = (ROOT / "clearledgr/api/gmail_extension.py").read_text(encoding="utf-8")

    assert 'async def post_to_erp(' in source
    assert 'result = await runtime.execute_intent(' in source
    assert '"post_to_erp",' in source
    assert 'async def submit_for_approval(' in source
    assert 'async def escalate_to_manager(' in source
    assert 'runtime.escalate_invoice_review(' in source
    assert 'async def finance_summary_share(' in source
    assert 'runtime.share_finance_summary(' in source
    assert 'async def record_field_correction(' in source
    assert 'return runtime.record_field_correction(' in source
