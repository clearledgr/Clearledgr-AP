"""
Clearledgr v1 - FastAPI Backend

Clearledgr v1: Agentic Finance Execution Layer (AP-first)

Run Instructions:
-----------------
1. Install dependencies:
   pip install -r requirements

2. Run the app locally with uvicorn:
   uvicorn main:app --host 0.0.0.0 --port 8010 --reload

3. Test /health endpoint:
   curl http://localhost:8010/health

4. Test runtime intent preview endpoint:
   curl -X POST http://localhost:8010/api/agent/intents/preview \
     -H "Content-Type: application/json" \
     -d '{"intent":"read_ap_workflow_health","input":{"limit":25},"organization_id":"default"}'
"""
from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
import os
import re
import secrets
from typing import Optional, List, Dict, Any
from clearledgr.services.auth import get_api_key_optional
from clearledgr.services.rate_limit import RateLimitMiddleware
from clearledgr.services.errors import ClearledgrError, to_http_exception
from clearledgr.core.errors import safe_error
from clearledgr.services.logging import log_request, log_error, logger
from clearledgr.services.metrics import record_request, record_error, get_metrics

# Surface the persistent-metrics mode on /health without calling
# get_metrics() (which would run 9 SELECTs per call, see L1247).
_PERSISTENT_METRICS = str(os.getenv("ENV", "dev")).strip().lower() in {
    "prod", "production", "staging", "stage",
}
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import time
import uuid
from datetime import datetime, timezone

from clearledgr.services.app_startup import cancel_deferred_startup, schedule_deferred_startup


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """Canonical app lifecycle — fires slow startup in background so server binds fast."""
    _apply_runtime_surface_profile()

    # Eager background init: kick off ClearledgrDB.initialize() in a
    # thread without blocking lifespan. The port binds immediately
    # (so /health and Railway's 30s healthcheck window are never
    # impacted) but each worker warms its schema state in parallel.
    # By the time the first user-facing request arrives, init has
    # finished and the request runs against a hot worker — no more
    # "first request after deploy takes 30s" cold-start cliff that
    # makes sign-in feel like a 2-minute hang.
    #
    # Race-safety: if a request arrives while the background task is
    # still running, the request handler's lazy-init call goes through
    # the same pg_advisory_xact_lock — at most one DDL pass runs, the
    # rest wait on the lock and exit cleanly with _initialized=True.
    async def _warm_db_in_background():
        import asyncio
        try:
            from clearledgr.core.database import get_db
            await asyncio.to_thread(get_db().initialize)
            logger.info("Database schema initialized (background)")
        except Exception as exc:
            logger.error("Background db init failed: %s", exc)

    import asyncio
    asyncio.create_task(_warm_db_in_background())

    if _should_skip_deferred_startup():
        yield
        return
    # Defer launch to the next loop turn so eager task execution cannot block bind.
    schedule_deferred_startup(app)
    try:
        yield
    finally:
        await cancel_deferred_startup(app)
        try:
            from clearledgr.services.gmail_autopilot import stop_gmail_autopilot
            await stop_gmail_autopilot(app)
        except Exception as e:
            logger.warning(f"Gmail autopilot stop failed: {e}")

        try:
            from clearledgr.services.agent_background import stop_agent_background

            await stop_agent_background()
        except Exception as e:
            logger.warning(f"Agent background stop failed: {e}")

        try:
            # Drain the shared httpx pool cleanly so in-flight requests
            # finish and keep-alive sockets close gracefully on exit.
            from clearledgr.core.http_client import close_http_client

            await close_http_client()
        except Exception as e:
            logger.warning(f"Shared HTTP client close failed: {e}")

app = FastAPI(
    title="Clearledgr API",
    description="""
    Clearledgr API v1 - Agentic Finance Execution Layer (AP-first)
    
    **Clearledgr is a finance execution agent platform: one runtime, AP-first skills, embedded where operators already work.**
    
    This API powers an embedded AP operating model across Gmail, Slack/Teams approvals, and ERP write-back.
    
    ## Agent Runtime
    - Canonical intent contract: `/api/agent/intents/preview` and `/api/agent/intents/execute`
    - Skill-packaged execution (AP skills first; expandable to adjacent finance workflows)
    - Deterministic policy prechecks before execution
    - Idempotency-aware execution and auditable outcomes
    
    ## AP Workflow (v1)
    - Invoice/AP intake, extraction, and routing
    - Needs-info follow-up loop (draft-first)
    - Low-risk approval routing and recoverable retry handling
    - ERP posting with API-first + controlled fallback patterns
    
    ## Embedded Surfaces
    - Gmail-first operator workflow
    - Slack/Teams approval decisions
    - Ops and audit visibility for finance operators
    
    ## Authentication
    API key authentication is optional. Set `API_KEY` environment variable to enable.
    When enabled, include `X-API-Key` header in requests.
    
    ## Rate Limiting
    Default: 100 requests per 60 seconds per client (IP or API key).
    Configure via `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW` environment variables.
    """,
    version="1.0.0",
    contact={
        "name": "Clearledgr Support",
        "email": "support@clearledgr.com",
    },
    license_info={
        "name": "Proprietary",
    },
    servers=[
        {"url": "http://localhost:8010", "description": "Development server"},
        {"url": "https://api.clearledgr.com", "description": "Production server"},
    ],
    # Gate the interactive schema browser in production. /docs, /redoc
    # and /openapi.json render the entire API surface (route paths,
    # parameter shapes, example payloads). Fine in dev where we want
    # to eyeball the shape, but in prod it's free reconnaissance for
    # anyone who finds the domain. Dev/staging keep the default
    # browsable schema; prod gets 404s.
    docs_url=None if str(os.getenv("ENV", "dev")).strip().lower() in {"prod", "production"} else "/docs",
    redoc_url=None if str(os.getenv("ENV", "dev")).strip().lower() in {"prod", "production"} else "/redoc",
    openapi_url=None if str(os.getenv("ENV", "dev")).strip().lower() in {"prod", "production"} else "/openapi.json",
    lifespan=app_lifespan,
)


# ---------------------------------------------------------------------------
# Sentry error tracking (opt-in via SENTRY_DSN env var)
# ---------------------------------------------------------------------------
_sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
if _sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.httpx import HttpxIntegration
        from clearledgr.core.sentry_config import build_sentry_before_send

        sentry_sdk.init(
            dsn=_sentry_dsn,
            environment=os.getenv("ENV", "development"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            send_default_pii=False,
            # before_send scrubs local-variable capture in exception
            # frames — Sentry's default only hides "well-known" PII
            # fields, not the invoice/vendor/bank_details objects that
            # land in our exception scopes.
            before_send=build_sentry_before_send(),
            integrations=[FastApiIntegration(), HttpxIntegration()],
        )
        logger.info("Sentry error tracking initialized")
    except ImportError:
        logger.warning("SENTRY_DSN set but sentry-sdk not installed — pip install sentry-sdk[fastapi]")
    except Exception as exc:
        logger.warning("Sentry initialization failed: %s", exc)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _process_role() -> str:
    raw = str(os.getenv("CLEARLEDGR_PROCESS_ROLE", "all") or "").strip().lower()
    if raw in {"api"}:
        return "web"
    if raw in {"web", "worker", "all"}:
        return raw
    return "all"


def _should_skip_deferred_startup() -> bool:
    if _env_flag("CLEARLEDGR_SKIP_DEFERRED_STARTUP", default=False):
        return True
    return _process_role() == "web"


def _runtime_surface_contract() -> Dict[str, Any]:
    env_name = str(os.getenv("ENV", "dev")).strip().lower()
    prod_like = env_name in {"production", "prod", "staging", "stage"}
    legacy_override_requested = _env_flag("CLEARLEDGR_ENABLE_LEGACY_SURFACES", default=False)
    allow_legacy_in_production = _env_flag("AP_V1_ALLOW_LEGACY_SURFACES_IN_PRODUCTION", default=False)
    strict_requested = _env_flag("AP_V1_STRICT_SURFACES", default=True)

    # AP-v1 now runs strict-only; legacy/full runtime surface toggles are
    # intentionally ignored to prevent configuration drift.
    warnings: List[str] = []
    if legacy_override_requested:
        warnings.append("legacy_override_ignored_strict_ap_v1")
    if not strict_requested:
        warnings.append("strict_disable_request_ignored_strict_ap_v1")
    if allow_legacy_in_production:
        warnings.append("allow_legacy_in_production_ignored_strict_ap_v1")

    return {
        "environment": env_name,
        "process_role": _process_role(),
        "production_like": prod_like,
        "strict_requested": True,
        "strict_forced_on_in_production": False,
        "strict_effective": True,
        "legacy_override_requested": legacy_override_requested,
        "legacy_override_effective": False,
        "allow_legacy_in_production": allow_legacy_in_production,
        "warnings": warnings,
        "profile": "strict",
    }


def _request_transport_scheme(request: Request) -> str:
    forwarded_proto = str(request.headers.get("x-forwarded-proto", "") or "").strip().lower()
    if forwarded_proto:
        return forwarded_proto.split(",")[0].strip().lower()
    forwarded_scheme = str(request.headers.get("x-forwarded-scheme", "") or "").strip().lower()
    if forwarded_scheme:
        return forwarded_scheme.split(",")[0].strip().lower()
    return str(request.url.scheme or "http").strip().lower() or "http"


class ProxyAwareHTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """Honor edge TLS headers and keep internal health checks unredirected."""

    _NO_REDIRECT_PATHS = frozenset({"/health"})

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._NO_REDIRECT_PATHS:
            return await call_next(request)
        if _request_transport_scheme(request) == "https":
            return await call_next(request)
        return RedirectResponse(str(request.url.replace(scheme="https")), status_code=307)


STRICT_PROFILE_ALLOWED_EXACT_PATHS = {
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/health",
    "/metrics",
    "/workspace",
    # Inbound demo-request leads from the marketing site (clearledgr.com).
    "/leads",
    # OAuth callbacks required for ERP admin connect flows.
    "/erp/quickbooks/callback",
    "/erp/xero/callback",
    # Outlook OAuth + webhooks
    "/outlook/connect/start",
    "/outlook/callback",
    "/outlook/disconnect",
    "/outlook/status",
    "/outlook/webhook",
}

STRICT_PROFILE_ALLOWED_PREFIXES = (
    "/v1",
    "/static",
    "/fraud-controls",  # DESIGN_THESIS.md §8 — architectural fraud-control admin
    # §5.1 Object Model — Pipeline / SavedView / BoxLink endpoints backing
    # the Gmail extension pipeline view and sidebar linked-records section.
    "/api/pipelines",
    "/api/saved-views",
    "/api/box-links",
    # Gap 2: versioned policy storage + replay
    "/api/policies",
    # Gap 4: transactional outbox ops
    "/api/ops/outbox",
    # Gap 6: read-side projections (ops rebuild + projector introspection).
    # Vendor summary endpoints live under /api/vendors which has its own
    # per-route allowlist further down — do not broaden /api/vendors here.
    "/api/ops/projections",
    # Organization settings — GL mappings, approval thresholds, migration
    # state, autonomy rules. All routes are scoped to /settings/{org_id}/*
    # and enforce org access in each handler.
    "/settings",
    # DESIGN_THESIS.md §4.07 — frontend performance telemetry. Beacons
    # are unauthenticated by design (navigator.sendBeacon cannot set
    # headers) and contain only surface name + latency, no PII.
    "/api/ui",
    # Inbound ERP webhook endpoints. Each route verifies a per-tenant
    # HMAC signature before doing anything; unconfigured org/ERP pairs
    # fail-closed with 503. Kept outside auth middleware because the
    # caller is the ERP itself, not a user — signature is the auth.
    "/erp/webhooks",
    # §8 customer-admin surface inside Gmail (Streak pattern) — box
    # exception queue, stats, resolve. Org-scoped + admin-role gated
    # in the handlers themselves.
    "/api/admin/box",
)

STRICT_PROFILE_ALLOWED_OPS_PATHS = {
    "/api/ops/tenant-health",
    "/api/ops/ap-kpis",
    "/api/ops/ap-kpis/digest",
    "/api/ops/ap-aggregation",
    "/api/ops/browser-agent",
    "/api/ops/erp-routing-strategy",
    "/api/ops/tenant-health/all",
    "/api/ops/autopilot-status",
    "/api/ops/extraction-quality",
    "/api/ops/ap-decision-health",
    "/api/ops/monitoring-thresholds",
    "/api/ops/monitoring-thresholds/check",
    "/api/ops/monitoring-health",
    "/api/ops/retry-queue",
    "/api/ops/vendor-onboarding/sessions",
    "/api/ops/llm-cost-summary",
    "/api/ops/llm-budget/reset",
}

STRICT_PROFILE_ALLOWED_EXTENSION_PATHS = {
    "/extension/triage",
    "/extension/process",
    "/extension/scan",
    "/extension/pipeline",
    "/extension/worklist",
    "/extension/gmail/register-token",
    "/extension/gmail/exchange-code",
    "/extension/post-to-erp",
    "/extension/verify-confidence",
    "/extension/match-bank",
    "/extension/match-erp",
    "/extension/escalate",
    "/extension/submit-for-approval",
    "/extension/reject-invoice",
    "/extension/budget-decision",
    "/extension/approval-nudge",
    "/extension/vendor-followup",
    "/extension/route-low-risk-approval",
    "/extension/retry-recoverable-failure",
    "/extension/repair-historical-invoices",
    "/extension/cleanup-gmail-labels",
    "/extension/finance-summary-share",
    "/extension/record-field-correction",
    "/extension/health",
    "/extension/suggestions/gl-code",
    "/extension/suggestions/vendor",
    "/extension/suggestions/amount-validation",
    "/extension/sidebar/query",
    "/extension/sidebar/query/stream",
    "/extension/sidebar/query/suggestions",
    "/extension/feedback",
    "/extension/draft-reply",
    "/extension/sap/exchange",
    "/extension/ap-items/by-sap-invoice",
}

STRICT_PROFILE_ALLOWED_WORKSPACE_PATHS = {
    "/api/workspace/audit/export",
    "/api/workspace/audit/search",
    "/api/workspace/bootstrap",
    "/api/workspace/dashboard",
    "/api/workspace/entities",
    "/api/workspace/ga-readiness",
    "/api/workspace/health",
    "/api/workspace/integrations",
    "/api/workspace/org",
    "/api/workspace/org/settings",
    "/api/workspace/subscription",
    "/api/workspace/subscription/plan",
    "/api/workspace/integrations/erp/connect/netsuite",
    "/api/workspace/integrations/erp/connect/sap",
    "/api/workspace/integrations/erp/connect/start",
    "/api/workspace/integrations/gmail/connect/start",
    "/api/workspace/integrations/slack/channel",
    "/api/workspace/integrations/slack/manifest",
    "/api/workspace/integrations/slack/install/callback",
    "/api/workspace/integrations/slack/install/start",
    "/api/workspace/integrations/slack/test",
    "/api/workspace/integrations/teams/test",
    "/api/workspace/integrations/teams/webhook",
    "/api/workspace/onboarding/status",
    "/api/workspace/onboarding/step",
    "/api/workspace/chart-of-accounts",
    "/api/workspace/ops/connector-readiness",
    "/api/workspace/ops/learning-calibration",
    "/api/workspace/ops/learning-calibration/recompute",
    "/api/workspace/org/settings",
    "/api/workspace/policies/ap",
    "/api/workspace/rollback-controls",
    "/api/workspace/subscription",
    "/api/workspace/subscription/plan",
    "/api/workspace/subscription/billing-summary",
    "/api/workspace/implementation/status",
    "/api/workspace/team/invites",
    "/api/workspace/team/approvers",
    "/api/workspace/spend-analysis",
    "/api/workspace/erp-vendors",
    "/api/workspace/reports/export",
    "/api/workspace/webhooks",
    "/api/workspace/llm-budget/override",
    "/api/workspace/llm-budget/status",
    "/api/workspace/vendor-intelligence/duplicates",
    "/api/workspace/vendor-intelligence/merge",
    "/api/workspace/disputes",
    "/api/workspace/disputes/summary",
    "/api/workspace/delegation-rules",
    "/api/workspace/period-close/current",
    "/api/workspace/vendor-intelligence/reconcile-statement",
    "/api/workspace/tax-compliance/summary",
    "/api/workspace/tax-compliance/validate-tax-id",
    "/api/workspace/reports/export-to-sheets",
}

STRICT_PROFILE_ALLOWED_AUTH_PATHS = {
    "/auth/google-identity",
    "/auth/google/callback",
    "/auth/google/exchange",
    "/auth/google/start",
    "/auth/microsoft/start",
    "/auth/microsoft/callback",
    "/auth/invites/accept",
    "/auth/login",
    "/auth/logout",
    "/auth/me",
    "/auth/popup-complete",
    "/auth/refresh",
    "/auth/register",
    "/auth/users",
    "/auth/users/invite",
}

STRICT_PROFILE_ALLOWED_GMAIL_PATHS = {
    "/gmail/callback",
    "/gmail/connected",
    "/gmail/disconnect",
    "/gmail/push",
}

# Per-user data. These endpoints only touch the authenticated user's
# own row (resolved via the JWT, never via a path/query param). They
# are NOT org-scoped admin surfaces — any authenticated workspace
# member can reach them for their own record. Keep this bucket small
# and distinct from /api/workspace/* so the prefix-by-concern split
# stays visible at review time.
STRICT_PROFILE_ALLOWED_USER_PATHS = {
    "/api/user/preferences",
}

STRICT_PROFILE_ALLOWED_AGENT_PATHS = {
    "/api/agent/intents/execute",
    "/api/agent/intents/execute-request",
    "/api/agent/intents/preview",
    "/api/agent/intents/preview-request",
    "/api/agent/intents/skills",
    "/api/agent/policies/browser",
    "/api/agent/sessions",
}

STRICT_PROFILE_ALLOWED_AP_PATHS = {
    "/api/ap/audit/recent",
    "/api/ap/items/compose/create",
    "/api/ap/items/compose/lookup",
    "/api/ap/items/field-review/bulk-resolve",
    "/api/ap/items/metrics/aggregation",
    "/api/ap/items/search",
    "/api/ap/items/upcoming",
    "/api/ap/items/vendors",
    "/api/ap/policies",
}

STRICT_PROFILE_ALLOWED_INTERACTIVE_CALLBACK_PATHS = {
    "/slack/interactions",
    "/slack/invoices/interactive",
    "/teams/invoices/interactive",
}

STRICT_PROFILE_ALLOWED_DYNAMIC_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^/api/workspace/team/invites/[^/]+/revoke$",
        r"^/api/agent/intents/skills/[^/]+/readiness$",
        r"^/api/agent/sessions/[^/]+$",
        r"^/api/agent/sessions/[^/]+/commands$",
        r"^/api/agent/sessions/[^/]+/commands/preview$",
        r"^/api/agent/sessions/[^/]+/complete$",
        r"^/api/agent/sessions/[^/]+/macros/[^/]+$",
        r"^/api/agent/sessions/[^/]+/results$",
        r"^/api/ap/items/[^/]+$",
        r"^/api/ap/items/[^/]+/audit$",
        # Gap 6: time-travel snapshots for a Box
        r"^/api/ap/items/[^/]+/history$",
        r"^/api/ap/items/[^/]+/context$",
        r"^/api/ap/items/[^/]+/entity-route/resolve$",
        r"^/api/ap/items/[^/]+/merge$",
        r"^/api/ap/items/[^/]+/non-invoice/resolve$",
        r"^/api/ap/items/[^/]+/resubmit$",
        r"^/api/ap/items/[^/]+/retry-post$",
        # Phase 1.4: override-window reversal endpoint
        r"^/api/ap/items/[^/]+/reverse$",
        # Phase 2.1.b: IBAN change verification workflow endpoints
        r"^/api/vendors/[^/]+/iban-verification$",
        r"^/api/vendors/[^/]+/iban-verification/factors/(phone|sign-off|email-domain)$",
        r"^/api/vendors/[^/]+/iban-verification/complete$",
        r"^/api/vendors/[^/]+/iban-verification/reject$",
        # Phase 2.2: vendor trusted-domains allowlist endpoints
        r"^/api/vendors/[^/]+/trusted-domains$",
        r"^/api/vendors/[^/]+/trusted-domains/[^/]+$",
        # Phase 2.4: vendor KYC + risk score endpoints
        r"^/api/vendors/[^/]+/kyc$",
        # Gap 6: vendor rollup (read-side projection)
        r"^/api/vendors/summary$",
        r"^/api/vendors/[^/]+/summary$",
        # Phase 3.1.b: vendor onboarding control endpoints (customer-side)
        r"^/api/vendors/[^/]+/onboarding/invite$",
        r"^/api/vendors/[^/]+/onboarding/session$",
        r"^/api/vendors/[^/]+/onboarding/status$",
        r"^/api/vendors/[^/]+/onboarding/escalate$",
        r"^/api/vendors/[^/]+/onboarding/reject$",
        # Phase 3.1.b: vendor portal magic-link surface (public, unauthenticated)
        r"^/portal/onboard/[^/]+$",
        r"^/portal/onboard/[^/]+/kyc$",
        r"^/portal/onboard/[^/]+/bank-details$",
        # Short-form redirect so magic links can embed `/onboard/<token>`
        # directly — the 302 resolves to /portal/onboard/<token> above.
        r"^/onboard/[^/]+$",
        r"^/api/ap/items/[^/]+/field-review/resolve$",
        r"^/api/ap/items/[^/]+/fields$",
        r"^/api/ap/items/[^/]+/gmail-link$",
        r"^/api/ap/items/[^/]+/compose-link$",
        r"^/api/ap/items/[^/]+/notes$",
        r"^/api/ap/items/[^/]+/comments$",
        r"^/api/ap/items/[^/]+/files$",
        r"^/api/ap/items/[^/]+/sources$",
        r"^/api/ap/items/[^/]+/sources/link$",
        r"^/api/ap/items/[^/]+/split$",
        r"^/api/ap/items/[^/]+/tasks$",
        r"^/api/ap/items/tasks/[^/]+/(status|assign|comments)$",
        r"^/api/ap/items/vendors/[^/]+$",
        r"^/api/ap/policies/[^/]+$",
        r"^/api/ap/policies/[^/]+/audit$",
        r"^/api/ap/policies/[^/]+/versions$",
        r"^/api/ops/retry-queue/[^/]+/(retry|skip)$",
        r"^/auth/users/[^/]+$",
        r"^/auth/users/[^/]+/role$",
        r"^/extension/ap/[^/]+/explain$",
        r"^/extension/ap-items/by-netsuite-bill/[^/]+$",
        r"^/extension/by-thread/[^/]+/recover$",
        r"^/extension/invoice-pipeline/[^/]+$",
        r"^/extension/invoice-status/[^/]+$",
        r"^/extension/needs-info-draft/[^/]+$",
        r"^/extension/suggestions/form-prefill/[^/]+$",
        r"^/extension/workflow/[^/]+$",
        r"^/extension/by-thread/[^/]+$",
        r"^/gmail/status/[^/]+$",
        r"^/api/workspace/audit/event/[^/]+$",
        r"^/api/workspace/audit/exports/[^/]+$",
        r"^/api/workspace/entities/[^/]+$",
        r"^/api/workspace/webhooks/[^/]+$",
        r"^/api/workspace/webhooks/[^/]+/deliveries$",
        r"^/api/workspace/webhooks/[^/]+/test$",
        r"^/api/workspace/vendor-intelligence/profiles/[^/]+/aliases$",
        r"^/api/workspace/vendor-intelligence/profiles/[^/]+/aliases/[^/]+$",
        r"^/api/workspace/disputes/[^/]+/resolve$",
        r"^/api/workspace/disputes/[^/]+/escalate$",
        r"^/api/workspace/delegation-rules/[^/]+/deactivate$",
        r"^/api/workspace/period-close/accruals/[^/]+$",
        r"^/api/workspace/period-close/backdated/[^/]+$",
        r"^/api/workspace/period-close/lock/[^/]+$",
        r"^/api/workspace/period-close/unlock/[^/]+$",
    )
)


# §12 #6 / §6.8 — paths that only make sense when the corresponding
# V1 boundary flag is on. Kept in the allowlist sets so the strict-
# profile snapshot tests still pass without a second exception list,
# but stripped out at request time when the flag is off. Belt-and-
# braces: the route handlers themselves also return 404 via the
# feature_flags dependencies, so a flag misconfiguration at either
# layer still produces the same answer.
_OUTLOOK_GATED_PATHS = frozenset({
    "/outlook/connect/start",
    "/outlook/callback",
    "/outlook/disconnect",
    "/outlook/status",
    "/outlook/webhook",
})

_TEAMS_GATED_PATHS = frozenset({
    "/api/workspace/integrations/teams/test",
    "/api/workspace/integrations/teams/webhook",
    "/teams/invoices/interactive",
})

_TEAMS_GATED_PREFIXES = ("/teams/invoices",)


def _is_strict_profile_allowed_path(path: str) -> bool:
    from clearledgr.core.feature_flags import is_outlook_enabled, is_teams_enabled

    normalized = path if path.startswith("/") else f"/{path}"

    # Gate Outlook paths off the allowlist when the V1 flag is off.
    # The middleware then 404s the request before it reaches the
    # router-level dependency — symmetric with workspace shell's env
    # gating and keeps the surface invisible to procurement scanning.
    if normalized in _OUTLOOK_GATED_PATHS and not is_outlook_enabled():
        return False
    if normalized in _TEAMS_GATED_PATHS and not is_teams_enabled():
        return False
    if not is_teams_enabled():
        for prefix in _TEAMS_GATED_PREFIXES:
            if normalized == prefix or normalized.startswith(f"{prefix}/"):
                return False

    if normalized in STRICT_PROFILE_ALLOWED_EXACT_PATHS:
        return True
    if normalized in STRICT_PROFILE_ALLOWED_OPS_PATHS:
        return True
    if normalized in STRICT_PROFILE_ALLOWED_EXTENSION_PATHS:
        return True
    if normalized in STRICT_PROFILE_ALLOWED_WORKSPACE_PATHS:
        return True
    if normalized in STRICT_PROFILE_ALLOWED_AUTH_PATHS:
        return True
    if normalized in STRICT_PROFILE_ALLOWED_GMAIL_PATHS:
        return True
    if normalized in STRICT_PROFILE_ALLOWED_USER_PATHS:
        return True
    if normalized in STRICT_PROFILE_ALLOWED_AGENT_PATHS:
        return True
    if normalized in STRICT_PROFILE_ALLOWED_AP_PATHS:
        return True
    if normalized in STRICT_PROFILE_ALLOWED_INTERACTIVE_CALLBACK_PATHS:
        return True
    for pattern in STRICT_PROFILE_ALLOWED_DYNAMIC_PATTERNS:
        if pattern.match(normalized):
            return True
    for prefix in STRICT_PROFILE_ALLOWED_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            return True
    return False


def _is_flag_gated_path(path: str) -> bool:
    """Paths whose allowlist membership toggles at runtime via
    feature flags (Outlook, Teams). We keep them registered on the
    router unconditionally and let the LegacySurfaceGuardMiddleware
    do the gating per-request — otherwise flipping the flag would
    require restarting the process for routes to come back.
    """
    if path in _OUTLOOK_GATED_PATHS:
        return True
    if path in _TEAMS_GATED_PATHS:
        return True
    for prefix in _TEAMS_GATED_PREFIXES:
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    return False


def _apply_runtime_surface_profile() -> None:
    """Apply strict AP-v1 route profile by mutating mounted routes."""
    full_routes = getattr(app.state, "_full_route_table", None)
    if full_routes is None:
        full_routes = tuple(app.router.routes)
        app.state._full_route_table = full_routes

    selected_routes = []
    for route in full_routes:
        route_path = getattr(route, "path", None)
        if isinstance(route_path, str):
            # Flag-gated paths stay mounted — the middleware evaluates
            # them per-request so FEATURE_OUTLOOK_ENABLED / FEATURE_
            # TEAMS_ENABLED can be flipped without a process restart.
            if _is_flag_gated_path(route_path):
                selected_routes.append(route)
                continue
            if not _is_strict_profile_allowed_path(route_path):
                continue
        selected_routes.append(route)

    contract = _runtime_surface_contract()
    app.router.routes = list(selected_routes)
    app.state._runtime_surface_contract = contract
    if getattr(app.state, "_runtime_surface_mode", None) != "strict":
        app.openapi_schema = None
        app.state._openapi_cache = {}
        app.state._runtime_surface_mode = "strict"


STRICT_PROFILE_ACTIVE = bool(_runtime_surface_contract().get("strict_effective"))

from clearledgr.api.v1 import router as v1_router
from clearledgr.api.gmail_extension import router as gmail_extension_router
from clearledgr.api.netsuite_panel import router as netsuite_panel_router
from clearledgr.api.sap_extension import router as sap_extension_router
from clearledgr.api.slack_invoices import (
    legacy_router as slack_legacy_router,
    router as slack_invoices_router,
)
from clearledgr.api.teams_invoices import router as teams_invoices_router

app.include_router(v1_router)
app.include_router(gmail_extension_router)
app.include_router(netsuite_panel_router)
app.include_router(sap_extension_router)
app.include_router(slack_invoices_router)
app.include_router(slack_legacy_router)
app.include_router(teams_invoices_router)

# Eager-import the IntakeAdapter implementations so the registry is
# populated before any webhook fires. Each module calls
# `register_adapter(...)` at import time.
import clearledgr.integrations.erp_netsuite_intake_adapter  # noqa: F401,E402
import clearledgr.integrations.erp_sap_s4hana_intake_adapter  # noqa: F401,E402

# Eager-import the MatchEngine implementations (Gap 3) so the
# registry is populated before any matching call.
import clearledgr.services.match_engines  # noqa: F401,E402

# Eager-import state_observers (Gap 4) so the outbox observer-prefix
# handler is registered before the OutboxWorker starts polling. The
# worker process needs this even though it doesn't construct an
# InvoiceWorkflowService.
import clearledgr.services.state_observers  # noqa: F401,E402

# Eager-import annotation targets (Gap 5) so the annotation-prefix
# handler is registered + every target instance is in the registry
# before any annotation outbox row is processed.
import clearledgr.services.annotation_targets  # noqa: F401,E402

# Eager-import box projection (Gap 6) so the projection-prefix
# outbox handler is registered + BoxSummaryProjector +
# VendorSummaryProjector are in the projector registry before the
# OutboxWorker drains its first projection row.
import clearledgr.services.box_projection  # noqa: F401,E402

# Policies router (Gap 2 — versioned policy + replay)
from clearledgr.api.policies import router as policies_router  # noqa: E402
app.include_router(policies_router)

# Outbox ops router (Gap 4 — transactional outbox inspection / retry / replay)
from clearledgr.api.outbox_ops import router as outbox_ops_router  # noqa: E402
app.include_router(outbox_ops_router)

# Projection routers (Gap 6 — vendor summary + ops rebuild + introspection)
from clearledgr.api.projections_ops import (  # noqa: E402
    ops_router as projections_ops_router,
    vendors_router as projections_vendors_router,
)
app.include_router(projections_ops_router)
app.include_router(projections_vendors_router)

from clearledgr.api.leads import router as leads_router  # noqa: E402
app.include_router(leads_router)

class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Inject a correlation ID on every request and echo it back in the response.

    Reads ``X-Correlation-ID`` from the incoming request headers.  If absent,
    generates a new UUID4.  Stores the value in ``request.state.correlation_id``
    so downstream handlers and audit events can reference it, and adds it to
    the response headers so clients can correlate logs.
    """

    async def dispatch(self, request: Request, call_next):
        correlation_id = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )
        # Expose on request state for handlers/dependencies
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response


# Add request logging middleware
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log requests and record metrics."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        client_id = request.headers.get("X-API-Key", request.client.host if request.client else "unknown")

        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000

            # Log request
            log_request(
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
                client_id=client_id
            )

            # Record metrics
            record_request(request.method, request.url.path, response.status_code, duration_ms)

            if response.status_code >= 400:
                record_error(f"http_{response.status_code}", request.url.path)

            return response
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            record_error("exception", request.url.path)
            log_error("request_exception", str(e), {"path": request.url.path, "method": request.method})
            raise


class LegacySurfaceGuardMiddleware(BaseHTTPMiddleware):
    """Block non-canonical surfaces when strict AP-v1 mode is active."""

    async def dispatch(self, request: Request, call_next):
        if not _is_strict_profile_allowed_path(request.url.path):
            return JSONResponse(
                status_code=404,
                content={
                    "detail": "endpoint_disabled_in_ap_v1_profile",
                    "reason": "non_canonical_surface_disabled",
                    "path": request.url.path,
                },
            )
        return await call_next(request)


class WorkspaceSessionCSRFMiddleware(BaseHTTPMiddleware):
    """Enforce CSRF header validation for cookie-authenticated mutating requests."""

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    EXEMPT_PATHS = {
        "/auth/login",
        "/auth/register",
        "/auth/google-identity",
        "/auth/google/start",
        "/auth/google/callback",
        "/auth/google/exchange",
        "/auth/microsoft/start",
        "/auth/microsoft/callback",
        "/auth/invites/accept",
    }

    async def dispatch(self, request: Request, call_next):
        if request.method.upper() in self.SAFE_METHODS:
            return await call_next(request)
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        # CSRF only applies to browser-cookie authenticated workspace sessions.
        if request.headers.get("authorization"):
            return await call_next(request)

        access_cookie = request.cookies.get("clearledgr_workspace_access")
        if not access_cookie:
            return await call_next(request)

        csrf_cookie = str(request.cookies.get("clearledgr_workspace_csrf") or "").strip()
        csrf_header = str(request.headers.get("X-CSRF-Token") or "").strip()
        if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
            return JSONResponse(
                status_code=403,
                content={"detail": "csrf_validation_failed"},
            )
        return await call_next(request)

class RequestBodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject POST/PUT/PATCH requests whose body exceeds MAX_REQUEST_BODY_BYTES.

    FastAPI/Starlette/uvicorn have no built-in cap on JSON body size
    out of the box. Without this middleware a hostile (or buggy)
    client can POST a 1GB JSON blob and force the worker to allocate
    that much memory trying to decode it before any application code
    runs — trivial DoS, OOM-kill on the worker.

    The cap is generous (30MB default, env-overridable) because the
    invoice extraction path needs to accept PDF-as-base64 up to ~25MB.
    We check the Content-Length header: if absent on a body method,
    reject (modern HTTP clients always send it; chunked encoding is
    rarely used from legit callers and we don't want to stream-count
    hostile uploads). Safe methods (GET/HEAD/DELETE/OPTIONS) bypass
    because they have no body to cap.
    """

    _MAX_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(30 * 1024 * 1024)))
    _BODY_METHODS = {"POST", "PUT", "PATCH"}

    async def dispatch(self, request: Request, call_next):
        if request.method in self._BODY_METHODS:
            content_length = request.headers.get("content-length")
            if content_length is None:
                # Chunked / streamed body without Content-Length. We
                # don't stream-count because the allocation would
                # already have happened by the time we noticed. Reject
                # the shape outright.
                return JSONResponse(
                    status_code=411,
                    content={"detail": "content_length_required"},
                )
            try:
                claimed = int(content_length)
            except (TypeError, ValueError):
                return JSONResponse(
                    status_code=400,
                    content={"detail": "invalid_content_length"},
                )
            if claimed > self._MAX_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": "request_body_too_large",
                        "max_bytes": self._MAX_BYTES,
                    },
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject standard security headers into every response."""

    # Import maps are inline JSON blocks that require script-src allowance.
    # The legacy workspace shell uses an import map for Preact bare-specifier resolution.
    _CONSOLE_CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' https: data:; connect-src 'self' https:; "
        "frame-ancestors 'none'; form-action 'self'; base-uri 'self'; object-src 'none'"
    )
    _API_CSP = (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' https: data:; connect-src 'self' https:; "
        "frame-ancestors 'none'; form-action 'self'; base-uri 'self'; object-src 'none'"
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
        # Console pages need unsafe-inline for import maps; API routes stay strict
        is_console = request.url.path.startswith("/workspace") or request.url.path.startswith("/static/workspace")
        response.headers.setdefault(
            "Content-Security-Policy",
            self._CONSOLE_CSP if is_console else self._API_CSP,
        )
        # Tenant-scoped API responses contain invoices, vendor details,
        # bank-detail masks, and audit events. "Cache-Control: private,
        # no-store" tells every CDN, corporate proxy, and browser
        # between us and the client: don't persist this, don't share
        # across users. Missing this header means a misconfigured
        # upstream cache could serve org A's invoice to org B on a
        # URL collision. Applied to /api/* + /extension/* which are
        # the authenticated data paths. Static assets, health, and
        # docs keep their default behaviour.
        path = request.url.path
        if path.startswith("/api/") or path.startswith("/extension/") or path.startswith("/erp/") or path.startswith("/gmail/") or path.startswith("/slack/") or path.startswith("/portal/") or path == "/me":
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response

# Add middleware in order (last added = outermost, executed first).
# CorrelationIdMiddleware must be outermost so correlation_id is available to
# all downstream middleware and handlers.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)
# Body-size limit runs BEFORE the rate limiter so an oversized request
# is rejected cheaply without counting against the rate-limit budget.
# Add order: last added == outermost, so this stacks outside RateLimit.
app.add_middleware(RequestBodySizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(LegacySurfaceGuardMiddleware)
app.add_middleware(WorkspaceSessionCSRFMiddleware)
app.add_middleware(CorrelationIdMiddleware)


def custom_openapi():
    _apply_runtime_surface_profile()
    cache_key = "strict"
    cached = getattr(app.state, "_openapi_cache", {})
    if cache_key in cached:
        return cached[cache_key]

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    cached[cache_key] = schema
    app.state._openapi_cache = cached
    return schema


app.openapi = custom_openapi


# Global exception handler for ClearledgrErrors
@app.exception_handler(ClearledgrError)
async def clearledgr_exception_handler(request: Request, exc: ClearledgrError):
    """Handle all ClearledgrErrors with structured responses."""
    from fastapi.responses import JSONResponse
    
    status_map = {
        "INVALID_CSV": 400,
        "INVALID_CONFIG": 400,
        "INVALID_DATE": 400,
        "MISSING_FIELD": 400,
        "EMPTY_DATA": 400,
        "INVALID_API_KEY": 401,
        "RATE_LIMITED": 429,
        "RECONCILIATION_FAILED": 500,
        "CATEGORIZATION_FAILED": 500,
        "LLM_UNAVAILABLE": 503,
        "DATABASE_ERROR": 500,
        "NOTIFICATION_FAILED": 500,
        "SHEETS_ERROR": 502,
        "EXCEL_ERROR": 502,
        "SLACK_ERROR": 502,
        "TEAMS_ERROR": 502,
    }
    
    log_error(exc.code.value, str(exc), exc.context)
    status_code = status_map.get(exc.code.value, 500)
    record_error(exc.code.value, str(request.url.path))
    
    return JSONResponse(
        status_code=status_code,
        content=exc.to_dict()
    )


# Global exception handler for unhandled exceptions
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Handle unhandled exceptions with monitoring and structured response."""
    from fastapi.responses import JSONResponse
    error_id = str(uuid.uuid4())
    record_error("unhandled_exception", str(request.url.path))
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "error_id": error_id,
            "message": "An unexpected error occurred. Please try again or contact support.",
        }
    )

# Enable CORS for all origins
def _parse_cors_origins(raw: str) -> List[str]:
    values = [item.strip() for item in (raw or "").split(",")]
    return [item for item in values if item]


def _resolve_cors_policy(configured_origins_raw: str, configured_regex_raw: str) -> tuple[List[str], Optional[str]]:
    configured_origins = _parse_cors_origins(configured_origins_raw)
    configured_regex = str(configured_regex_raw or "").strip()

    normalized_origins: List[str] = []
    seen = set()
    wildcard_requested = False
    for origin in configured_origins:
        token = str(origin or "").strip()
        if not token:
            continue
        if token == "*":
            wildcard_requested = True
            continue
        if token in seen:
            continue
        seen.add(token)
        normalized_origins.append(token)

    _UNSAFE_CORS_PATTERNS = {".*", ".+", "^.*$", "^.+$", "", ".*\\..*"}
    if configured_regex and configured_regex in _UNSAFE_CORS_PATTERNS:
        logger.error(
            "CORS_ALLOW_ORIGIN_REGEX=%r is too permissive; falling back to default",
            configured_regex,
        )
        configured_regex = ""
    # Default origin pattern matches:
    #   - Gmail extension (chrome-extension://<32-char-id>) — original Streak-style integration
    #   - NetSuite-hosted Suitelets (https://<account>.app.netsuite.com) — embedded panel iframe
    #     served by the Clearledgr SuiteApp under integrations/netsuite-suiteapp/
    #   - SAP BTP-hosted Approuter (https://<approuter>-<account>.<region>.hana.ondemand.com)
    #     — the SAP Fiori extension under integrations/sap-fiori-extension/
    #   - SAP S/4HANA Fiori Launchpad (https://<host>.s4hana.cloud.sap or *.fiori.cloud.sap)
    #     when the Fiori app is consumed via the customer's launchpad rather than standalone
    default_regex = configured_regex or (
        r"^("
        r"chrome-extension://[a-z]{32}"
        r"|https://[a-z0-9_-]+\.app\.netsuite\.com"
        r"|https://[a-z0-9_.-]+\.hana\.ondemand\.com"
        r"|https://[a-z0-9_.-]+\.s4hana\.cloud\.sap"
        r"|https://[a-z0-9_.-]+\.fiori\.cloud\.sap"
        r")$"
    )

    if normalized_origins:
        # Explicit origin list ADDS to the dynamic regex coverage rather
        # than replacing it. Two consumers depend on this: the Gmail
        # extension's per-install chrome-extension://<32-char-id> origin
        # and the per-tenant ERP host patterns. Setting CORS_ALLOW_ORIGINS
        # to add workspace.clearledgr.com or any other static origin must NOT
        # break those dynamic origins. Starlette's CORSMiddleware accepts
        # the request when EITHER the origin matches the explicit list
        # OR the regex — they coexist cleanly.
        return normalized_origins, default_regex

    if wildcard_requested:
        # Credentials are enabled, so wildcard-origin mode is unsafe/invalid.
        # Fall back to safe canonical defaults instead of `*`.
        logger.warning("CORS_ALLOW_ORIGINS wildcard ignored; falling back to canonical origin allowlist")

    return _default_cors_origins, default_regex


_default_cors_origins = [
    "https://mail.google.com",
    "https://gmail.google.com",
    "https://clearledgr.com",
    "https://www.clearledgr.com",
    "https://workspace.clearledgr.com",
    "http://localhost:8010",
    "http://127.0.0.1:8010",
]

_cors_allow_origins, _cors_allow_origin_regex = _resolve_cors_policy(
    os.getenv("CORS_ALLOW_ORIGINS", ""),
    os.getenv("CORS_ALLOW_ORIGIN_REGEX", ""),
)

# HTTPS enforcement in production
if os.getenv("ENV", "dev").lower() in ("production", "prod"):
    app.add_middleware(ProxyAwareHTTPSRedirectMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins,
    allow_origin_regex=_cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID", "X-CSRF-Token"],
)

# (Autonomous agent, chat, engine, and webhooks routers removed — archived to branch)

# ── Router registration ────────────────────────────────────────────────
#
# These imports are REQUIRED for the app to function. A missing module
# means a bug, not a configuration variant — the app should refuse to
# start rather than silently drop an entire API surface. The historical
# "try: import; except ImportError: pass" pattern hid real breakage in
# production (user_preferences disappearing, pipelines routes missing,
# etc. all left no log signal at startup).
#
# If a router is legitimately optional (e.g. only in some deployments),
# gate it on an env var, NOT on import failure.

# Core + auth
from clearledgr.api.auth import router as auth_router
app.include_router(auth_router)

# Organization Config API — not mounted in strict AP-v1 profile
if not STRICT_PROFILE_ACTIVE:
    from clearledgr.api.org_config import router as org_config_router
    app.include_router(org_config_router)

# Fraud Controls API — CFO-gated writes of architectural fraud params
# (payment ceiling, velocity limits, first-payment dormancy). See
# DESIGN_THESIS.md §8.
from clearledgr.api.fraud_controls import router as fraud_controls_router
app.include_router(fraud_controls_router)

# IBAN Change Verification API — Phase 2.1.b. Three-factor CFO-gated
# workflow that lifts the IBAN change freeze. See DESIGN_THESIS.md §8.
from clearledgr.api.iban_verification import router as iban_verification_router
app.include_router(iban_verification_router)

# Vendor Trusted-Domains API — Phase 2.2. CFO-gated allowlist of sender
# domains the validation gate trusts for each vendor. See DESIGN_THESIS.md §8.
from clearledgr.api.vendor_domains import router as vendor_domains_router
app.include_router(vendor_domains_router)

# Vendor KYC API — Phase 2.4. First-class KYC fields + computed signals.
# Reads = any org member; writes = Financial Controller+. See DESIGN_THESIS.md §3.
from clearledgr.api.vendor_kyc import router as vendor_kyc_router
app.include_router(vendor_kyc_router)

# Vendor Onboarding control API — Phase 3.1.b. Customer-side session
# lifecycle + ops list endpoint. See DESIGN_THESIS.md §9.
from clearledgr.api.vendor_onboarding import (
    router as vendor_onboarding_router,
    ops_router as vendor_onboarding_ops_router,
)
app.include_router(vendor_onboarding_router)
app.include_router(vendor_onboarding_ops_router)

# Vendor Portal — Phase 3.1.b. The ONLY unauthenticated surface
# (magic-link tokens, 14-day TTL). See DESIGN_THESIS.md §9.
from clearledgr.api.vendor_portal import (
    router as vendor_portal_router,
    shortcut_router as vendor_portal_shortcut_router,
)
app.include_router(vendor_portal_router)
app.include_router(vendor_portal_shortcut_router)

# Gmail integration
from clearledgr.api.gmail_webhooks import router as gmail_webhooks_router
app.include_router(gmail_webhooks_router)

from clearledgr.api.gmail_schedule import router as gmail_schedule_router
app.include_router(gmail_schedule_router)

# §5.1 Object Model — Pipeline / SavedView / BoxLink endpoints
from clearledgr.api.pipelines import (
    router as pipelines_router,
    saved_views_router,
    box_links_router,
)
app.include_router(pipelines_router)
app.include_router(saved_views_router)
app.include_router(box_links_router)

# Organization settings (thresholds, GL mappings, migration)
from clearledgr.api.settings import router as settings_router
app.include_router(settings_router)

# Outlook / Microsoft 365 routes (OAuth + webhooks) — optional surface,
# currently not in strict AP-v1 scope but shipped so admins who toggle
# Outlook intake get real endpoints. Keep required so it fails loudly
# if the module breaks.
from clearledgr.api.outlook_routes import router as outlook_router
app.include_router(outlook_router)

# ERP Connections API (OAuth flows). Strict profile exposes only the
# OAuth-callback completion routes; full profile exposes the whole router.
if STRICT_PROFILE_ACTIVE:
    from clearledgr.api.erp_connections import quickbooks_callback, xero_callback
    app.add_api_route(
        "/erp/quickbooks/callback",
        quickbooks_callback,
        methods=["GET"],
        tags=["ERP Connections"],
    )
    app.add_api_route(
        "/erp/xero/callback",
        xero_callback,
        methods=["GET"],
        tags=["ERP Connections"],
    )
else:
    from clearledgr.api.erp_connections import router as erp_connections_router
    app.include_router(erp_connections_router)

# Inbound ERP webhook endpoints (HMAC-signed; QBO/Xero/NetSuite/SAP).
# Each route verifies signature before processing — unconfigured
# secrets fail-closed with 503 "webhook_not_configured".
from clearledgr.api.erp_webhooks import router as erp_webhooks_router
app.include_router(erp_webhooks_router)

# Agent intent runtime contract (preview/execute)
from clearledgr.api.agent_intents import router as agent_intents_router
app.include_router(agent_intents_router)

# AP item routes (sources/context/audit/merge/split)
from clearledgr.api.ap_items import router as ap_items_router
app.include_router(ap_items_router)

# AP audit feeds for admin/activity surfaces
from clearledgr.api.ap_audit import router as ap_audit_router
app.include_router(ap_audit_router)

# Frontend performance telemetry (DESIGN_THESIS.md §4.07)
from clearledgr.api.ui_perf import router as ui_perf_router
app.include_router(ui_perf_router)

# AP business policy management (versioned + auditable)
from clearledgr.api.ap_policies import router as ap_policies_router
app.include_router(ap_policies_router)

# Ops health/KPI endpoints
from clearledgr.api.ops import router as ops_router
app.include_router(ops_router)

# Workspace shell support APIs — always mounted. The standalone HTML
# page at /workspace is gated separately (§4 Principle 01).
from clearledgr.api.workspace_shell import router as workspace_shell_router
app.include_router(workspace_shell_router)

# Phase 9 Backoffice surface — Box exceptions admin UI endpoints.
from clearledgr.api.box_exceptions_admin import router as box_exceptions_admin_router
app.include_router(box_exceptions_admin_router)

# Per-user preferences — /api/user/* prefix, not /api/workspace/*, because
# preferences are per-user data (UI state, saved views, template choices)
# not org-level admin data. No ops-role gate applies.
from clearledgr.api.user_preferences import router as user_preferences_router
app.include_router(user_preferences_router)

# Serve static files (standalone workspace shell)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/workspace", tags=["Workspace"], include_in_schema=False)
async def workspace_page():
    """Standalone workspace shell UI — Clearledgr internal ops only.

    DESIGN_THESIS.md §4 Principle 01: "There is no separate web application
    in V1. There is no new tab. There is no dashboard the AP team checks."

    The workspace shell is disabled by default. It is available only when
    explicitly enabled via WORKSPACE_SHELL_ENABLED=true for Clearledgr
    internal operations (§14 Backoffice). Customer-facing AP work happens
    entirely inside Gmail via the extension.
    """
    enabled = str(os.getenv("WORKSPACE_SHELL_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        raise HTTPException(status_code=404, detail="Workspace shell disabled")
    workspace_file = os.path.join(os.path.dirname(__file__), "static", "workspace", "index.html")
    if os.path.exists(workspace_file):
        return FileResponse(workspace_file)
    raise HTTPException(status_code=404, detail="Workspace page not found")


@app.get(
    "/health",
    tags=["System"],
    summary="Health Check",
    description="Check API health and version",
    response_description="API health status"
)
async def health():
    """
    Health check endpoint.

    Returns API status, version, and detailed health checks.
    No authentication required.
    """
    from clearledgr.core.database import get_db

    checks: Dict[str, Dict[str, Any]] = {}
    status = "healthy"
    try:
        db = get_db()
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            _ = cur.fetchone()
        checks["database"] = {"status": "healthy"}
    except Exception as exc:  # noqa: BLE001
        checks["database"] = {"status": "unhealthy", "error": str(exc)}
        status = "unhealthy"

    # Metrics-backend mode reflects whether the persistent store is
    # mounted; we report that without calling `get_metrics()`, which
    # runs 9 separate SELECTs to build a full report. Health checks
    # fire on every Railway healthcheck poll + every browser session
    # bootstrap; making them serialise on those queries dominated p50
    # latency for /health (~8.6s observed in prod).
    checks["metrics_backend"] = {
        "status": "healthy",
        "mode": "durable_db" if _PERSISTENT_METRICS else "memory",
    }

    # §11.2.1: Event queue depth for autoscaler. In prod this is
    # Redis-backed; if Redis is unreachable, get_event_queue() silently
    # falls back to an in-process queue that does NOT persist across
    # workers — which means losing events on the next deploy. Make the
    # health endpoint flip to unhealthy in that case so Railway stops
    # routing traffic to a backend that can't persist work. In dev
    # (ENV=dev) we tolerate the in-memory backend so local `uvicorn`
    # doesn't require Redis just to pass /health.
    is_prod_like = str(os.getenv("ENV", "dev")).strip().lower() in {
        "prod", "production", "staging", "stage",
    }
    try:
        from clearledgr.core.event_queue import get_event_queue
        queue = get_event_queue()
        pinging = False
        try:
            pinging = bool(queue.ping())
        except Exception as ping_exc:  # noqa: BLE001
            pinging = False
            checks["event_queue_ping_error"] = str(ping_exc)
        pending = queue.pending_count()
        if pinging:
            queue_status = "healthy"
        else:
            # In production, a non-pinging queue is a real outage —
            # we can't durably enqueue events. Flip the overall
            # health so Railway pulls the worker out of rotation.
            queue_status = "unhealthy" if is_prod_like else "degraded"
        checks["event_queue"] = {
            "status": queue_status,
            "pending": pending,
        }
        if queue_status == "unhealthy":
            status = "unhealthy"
    except Exception as exc:
        checks["event_queue"] = {"status": "unknown", "error": str(exc)}
        if is_prod_like:
            status = "unhealthy"

    return {
        "status": status,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "v1.0.0",
        "runtime_surface_contract": _runtime_surface_contract(),
    }


@app.get(
    "/metrics",
    tags=["System"],
    summary="Get Metrics",
    description="Get API performance and usage metrics",
    response_description="Metrics including uptime, requests, errors, and performance stats"
)
async def metrics_endpoint(
    api_key: str = Depends(get_api_key_optional),
):
    """
    Get API metrics.
    
    Returns:
    - Uptime information
    - Request statistics by endpoint and status
    - Error statistics
    - Reconciliation run statistics
    - Performance metrics (response times, requests per second)
    """
    try:
        return get_metrics()
    except Exception as e:
        log_error("metrics_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "metrics")
        )


# Apply route profile once after all routes are registered.
_apply_runtime_surface_profile()
