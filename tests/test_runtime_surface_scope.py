from __future__ import annotations

import importlib
import os
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from solden.services.finance_agent_runtime import FinanceAgentRuntime

os.environ.setdefault("CLEARLEDGR_SKIP_DEFERRED_STARTUP", "true")

ROOT = Path(__file__).resolve().parent.parent


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
        # 2026-04-29: Module 6 (Users, Roles, and Permissions). Cap
        # raised 275 → 285 to cover:
        #   * permissions catalog + custom roles CRUD (5 endpoints)
        #   * per-entity role assignments (3 endpoints)
        #   * SAML admin + IdP-facing flows (6 endpoints — config
        #     CRUD + sp-metadata + login + acs)
        # 2026-05-03: Path B audit-trail compose pass (Phases 2-4)
        # added per-render-target action endpoints + the /api/workspace
        # + /api/ops branches grew with the rebrand-era settings panel,
        # report subscriptions, and audit exports. Cap raised 285 → 340:
        #   * 3 NetSuite SuiteApp panel actions (/extension/ap-items/
        #     by-netsuite-bill/{id}/{approve,reject,request-info})
        #   * 3 SAP Fiori panel actions (/extension/ap-items/by-sap-
        #     invoice/{approve,reject,request-info})
        #   * accrual-journal-entry API surface
        #   * report subscriptions CRUD
        #   * additional bank-match + bank-statement read paths
        #   * field-review batch + audit-export hooks
        # 2026-05-19: v89 two-axis auth + Outlook+Teams full ship +
        # coordination-layer workspace audit. Cap raised 340 → 380:
        #   * Outlook intake: /api/workspace/integrations/outlook/
        #     {connect/start,disconnect} (2)
        #   * Teams interactive bot: /api/workspace/integrations/teams/
        #     {manifest,webhook} (2)
        #   * v89 audit + RBAC: api-keys + scopes catalog + rotate (4),
        #     audit chain-status + exports + retention (5), delegation
        #     rules + escalation policies (5)
        #   * Records read API + intervention surface
        # 2026-05-21: declarative workflow platform (Level 1 + 2). Cap raised
        # 380 → 395 to cover the 8 generic surface templates that serve EVERY
        # tenant-declared Box type (box_type/box_id/action are path params, so
        # the surface does NOT grow per declared type):
        #   * spec authoring: /api/workspace/workflow-specs (+/validate,
        #     /{box_type}, /{box_type}/versions/{version}/{activate,archive})
        #   * generic boxes: /api/workspace/workflows/{box_type}
        #     (+/{box_id}, +/{box_id}/{action})
        # 2026-05-23: manifesto audit found 19 AP feature routers (~68
        # endpoints: dual-approval, gdpr, vat, sanctions, three-way-match,
        # peppol, bank-statements, accrual-je, payment-confirmations,
        # reclassification-je, africa-einvoice, vendor-match/inquiry,
        # journal-entry-preview, dispute-reopen, cycle-time, pdf-split,
        # threshold-policy, erp-connection-ops) were mounted + tested but
        # never allowlisted, so the prune silently stripped them all and they
        # 404'd in prod. Several cap-raise notes above ALREADY assumed these
        # were in the surface (e.g. "accrual-journal-entry API surface"); the
        # allowlist just never caught up. Now correctly allowlisted. Cap
        # raised 395 → 455 to reflect the real intended surface.
        # 2026-05-25: +5 routes that were mounted+tested but never allowlisted,
        # so the prune silently 404'd them in prod (manifesto per-file review):
        # ap items audit/export, ops/box-health, and the 3 NetSuite-panel
        # action POSTs (approve/reject/request-info). Cap 455 -> 460.
        assert len(paths) <= 460
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
            # 2026-04-29 Module 6 Pass C: SAML SSO IdP-facing flows.
            # /saml/{org_id}/sp-metadata, /saml/{org_id}/login,
            # /saml/{org_id}/acs are reachable without auth (the SAML
            # signature is the auth on ACS); per-tenant scoping is
            # enforced inside the handlers. NO trailing slash — see the
            # matcher (startswith(f"{prefix}/")); "/saml/" silently 404'd
            # every SAML sub-path. Locked by the sub-path asserts below.
            "/saml",
            # Workspace SPA module surfaces (each surface is org-
            # scoped + admin-role gated in the handlers). The strict
            # profile allows the prefix; the handlers enforce auth.
            # Modules 1, 3, 4, 6, 8, 9, 10, 11.
            "/api/workspace/dashboard",
            "/api/workspace/rules",
            "/api/workspace/reports",
            "/api/workspace/fx-rates",
            "/api/workspace/onboarding/sample-data",
            "/api/workspace/api-keys",
            "/api/workspace/escalation-policies",
            "/api/workspace/notification-preferences",
            "/api/workspace/account",
            "/api/workspace/saml",
            "/api/workspace/fraud-thresholds",
            "/api/workspace/billing",
            "/api/webhooks/paddle",
            # 2026-05-23 manifesto audit: AP feature routers that were
            # mounted + tested but never allowlisted (silently 404'd in prod).
            "/api/workspace/accrual-je",
            "/api/workspace/bank-statements",
            "/api/workspace/gdpr",
            "/api/workspace/peppol",
            "/api/workspace/payment-confirmations",
            "/api/workspace/sanctions-checks",
            "/api/workspace/vat-returns",
            "/api/workspace/vat",
            "/api/workspace/vendor-inquiries",
            "/api/workspace/policy/dual-approval",
            "/api/workspace/policy/thresholds",
            "/api/workspace/pdf",
            "/api/workspace/metrics/cycle-time",
            "/api/workspace/africa-einvoice",
        }
        # Phase 2.1.b IBAN-verification + Phase 3.1.b vendor-onboarding
        # endpoints have been deprioritized (memory: 2026-04-30 — VO is
        # AP-subordinate; the standalone VO scaffolding is parked
        # dormant). The assertions previously here exercised endpoints
        # that the rebrand-era cleanup unmounted. The corresponding
        # ``vendor_inquiry.lookup`` read-only status block is the only
        # surviving vendor-facing surface.


def test_strict_profile_allows_saml_sso_subpaths(monkeypatch):
    """SAML SSO IdP-facing sub-paths must resolve ALLOWED by the real matcher.

    Regression for the trailing-slash bug: the prefix was "/saml/" while the
    matcher tests startswith(f"{prefix}/") == startswith("/saml//"), so every
    /saml/{org}/* path silently 404'd in strict-profile prod. The prefix-set
    assertion alone never caught this (it checked the string, not the match).
    """
    matcher = _main_module()._is_strict_profile_allowed_path
    for sub in ("sp-metadata", "login", "acs", "logout", "slo"):
        path = f"/saml/acme/{sub}"
        assert matcher(path) is True, f"{path} should be allowlisted but is dropped"


def test_strict_profile_allows_mounted_ap_feature_routers(monkeypatch):
    """Every endpoint of the mounted AP feature routers must resolve ALLOWED.

    Regression for the 2026-05-23 manifesto audit: 19 routers (~68 endpoints)
    were include_router'd + tested but never added to the strict-profile
    allowlist, so the boot stripped them all and every call 404'd in prod
    (the documented match-config failure mode, at scale). This walks each
    router's real routes through the live matcher so a future router that
    forgets its allowlist entry trips here.
    """
    import importlib

    matcher = _main_module()._is_strict_profile_allowed_path
    routers = [
        "accrual_journal_entry", "africa_einvoice", "bank_statements",
        "cycle_time_metrics", "dispute_reopen", "dual_approval",
        "erp_connection_ops", "gdpr", "journal_entry_preview",
        "multi_invoice_split", "payment_confirmations", "peppol",
        "reclassification_je", "three_way_match", "threshold_policy",
        "vat", "vendor_inquiry", "vendor_match", "sanctions",
    ]
    dropped = []
    for name in routers:
        mod = importlib.import_module(f"solden.api.{name}")
        for route in getattr(mod, "router").routes:
            path = getattr(route, "path", "")
            if path and not matcher(path):
                dropped.append(path)
    assert not dropped, f"strict-profile drops mounted AP endpoints: {dropped}"


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
        response = client.get("/api/ap/items/AP-SURFACE-PROBE?organization_id=org-test")
        assert response.status_code in {401, 404}
        assert response.json().get("detail") != "endpoint_disabled_in_ap_v1_profile"


def test_strict_profile_allows_explicit_gmail_thread_recovery(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AP_V1_STRICT_SURFACES", raising=False)
    monkeypatch.delenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", raising=False)
    monkeypatch.delenv("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", raising=False)

    with TestClient(_app()) as client:
        response = client.post("/extension/by-thread/thread-surface-probe/recover?organization_id=org-test")
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
            json={"organization_id": "org-test", "patch": {"gmail_extension": {"probe": True}}},
        )
        assert response.status_code in {401, 403, 404}
        assert response.json().get("detail") != "endpoint_disabled_in_ap_v1_profile"


def test_ap_runtime_registers_sidebar_core_intents():
    runtime = FinanceAgentRuntime(
        organization_id="org-test",
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
    source = (ROOT / "solden/api/gmail_extension.py").read_text(encoding="utf-8")

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
