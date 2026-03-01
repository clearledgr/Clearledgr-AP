"""
Clearledgr v1 - FastAPI Backend

Clearledgr v1: Agentic Finance Execution Layer (AP-first)

Run Instructions:
-----------------
1. Install dependencies:
   pip install -r requirements

2. Run the app locally with uvicorn:
   uvicorn main:app --host 0.0.0.0 --port 8000 --reload

3. Test /health endpoint:
   curl http://localhost:8000/health

4. Test runtime intent preview endpoint:
   curl -X POST http://localhost:8000/api/agent/intents/preview \
     -H "Content-Type: application/json" \
     -d '{"intent":"read_ap_workflow_health","input":{"limit":25},"organization_id":"default"}'
"""
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
import os
from typing import Optional, List, Dict, Any
from clearledgr.services.auth import get_api_key_optional
from clearledgr.services.rate_limit import RateLimitMiddleware
from clearledgr.services.errors import ClearledgrError, to_http_exception
from clearledgr.core.errors import safe_error
from clearledgr.services.logging import log_request, log_error, logger
from clearledgr.services.metrics import record_request, record_error, get_metrics
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import time
import uuid
from datetime import datetime, timezone
from clearledgr.api import (
    v1_router,
    erp_router,
    gmail_extension_router,
    slack_invoices_router,
    teams_invoices_router,
)

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
        {"url": "http://localhost:8000", "description": "Development server"},
        {"url": "https://api.clearledgr.com", "description": "Production server"},
    ],
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


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


STRICT_PROFILE_ALLOWED_EXACT_PATHS = {
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/health",
    "/metrics",
    "/console",
}

STRICT_PROFILE_ALLOWED_PREFIXES = (
    "/v1",
    "/static",
    "/api/v1",
    "/api/erp",
    "/api/agent",
    "/api/ap",
    "/api/ops",
    "/api/admin",
    "/extension",
    "/slack",
    "/teams",
    "/gmail",
    "/auth",
    "/config",
    "/erp",
)


def _is_strict_profile_allowed_path(path: str) -> bool:
    normalized = path if path.startswith("/") else f"/{path}"
    if normalized in STRICT_PROFILE_ALLOWED_EXACT_PATHS:
        return True
    for prefix in STRICT_PROFILE_ALLOWED_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
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
        if isinstance(route_path, str) and not _is_strict_profile_allowed_path(route_path):
            continue
        selected_routes.append(route)

    contract = _runtime_surface_contract()
    app.router.routes = list(selected_routes)
    app.state._runtime_surface_contract = contract
    if getattr(app.state, "_runtime_surface_mode", None) != "strict":
        app.openapi_schema = None
        app.state._openapi_cache = {}
        app.state._runtime_surface_mode = "strict"


app.include_router(v1_router)
app.include_router(erp_router)
app.include_router(gmail_extension_router)
app.include_router(slack_invoices_router)
app.include_router(teams_invoices_router)

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

# Add middleware in order (last added = outermost, executed first).
# CorrelationIdMiddleware must be outermost so correlation_id is available to
# all downstream middleware and handlers.
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(LegacySurfaceGuardMiddleware)
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
    from clearledgr.services.monitoring import get_monitor
    
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
    
    # Track in monitoring service
    monitor = get_monitor()
    status_code = status_map.get(exc.code.value, 500)
    severity = "error" if status_code >= 500 else "warning"
    monitor.capture_error(
        exc,
        context={
            "path": str(request.url.path),
            "method": request.method,
            "code": exc.code.value,
            **exc.context
        },
        severity=severity,
        alert=status_code >= 500  # Only alert on server errors
    )
    
    return JSONResponse(
        status_code=status_code,
        content=exc.to_dict()
    )


# Global exception handler for unhandled exceptions
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Handle unhandled exceptions with monitoring and structured response."""
    from fastapi.responses import JSONResponse
    from clearledgr.services.monitoring import get_monitor
    
    # Track in monitoring service
    monitor = get_monitor()
    error_id = monitor.capture_error(
        exc,
        context={
            "path": str(request.url.path),
            "method": request.method,
            "query_params": str(request.query_params),
        },
        severity="critical",
        alert=True
    )
    
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

    if normalized_origins:
        # Explicit origin list takes precedence; regex disabled to avoid
        # emitting ambiguous multi-value origin headers.
        return normalized_origins, None

    if wildcard_requested:
        # Credentials are enabled, so wildcard-origin mode is unsafe/invalid.
        # Fall back to safe canonical defaults instead of `*`.
        logger.warning("CORS_ALLOW_ORIGINS wildcard ignored; falling back to canonical origin allowlist")

    default_regex = configured_regex or r"^chrome-extension://[a-z]{32}$"
    return _default_cors_origins, default_regex


_default_cors_origins = [
    "https://mail.google.com",
    "https://gmail.google.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8010",
    "http://127.0.0.1:8010",
]

_cors_allow_origins, _cors_allow_origin_regex = _resolve_cors_policy(
    os.getenv("CORS_ALLOW_ORIGINS", ""),
    os.getenv("CORS_ALLOW_ORIGIN_REGEX", ""),
)

# HTTPS enforcement in production
if os.getenv("ENV", "dev").lower() in ("production", "prod"):
    try:
        from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
        app.add_middleware(HTTPSRedirectMiddleware)
    except ImportError:
        logger.warning("HTTPSRedirectMiddleware not available")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins,
    allow_origin_regex=_cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
)

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    """Initialize startup route profile and lazy DB initialization."""
    _apply_runtime_surface_profile()

# (Autonomous agent, chat, engine, and webhooks routers removed — archived to branch)

# Include Auth API
try:
    from clearledgr.api.auth import router as auth_router
    app.include_router(auth_router)
except ImportError:
    pass

# Include Organization Config API
try:
    from clearledgr.api.org_config import router as org_config_router
    app.include_router(org_config_router)
except ImportError:
    pass

# Include Gmail Webhooks API (for Pub/Sub push notifications)
try:
    from clearledgr.api.gmail_webhooks import router as gmail_webhooks_router
    app.include_router(gmail_webhooks_router)
except ImportError:
    pass

# ERP Connections API (OAuth flows)
try:
    from clearledgr.api.erp_connections import router as erp_connections_router
    app.include_router(erp_connections_router)
except ImportError:
    pass

# Browser-agent control plane APIs
try:
    from clearledgr.api.agent_sessions import router as agent_sessions_router
    app.include_router(agent_sessions_router)
except ImportError:
    pass

# Agent intent runtime contract (preview/execute)
try:
    from clearledgr.api.agent_intents import router as agent_intents_router
    app.include_router(agent_intents_router)
except ImportError:
    pass

# AP item routes (sources/context/audit/merge/split)
try:
    from clearledgr.api.ap_items import router as ap_items_router
    app.include_router(ap_items_router)
except ImportError:
    pass

# AP business policy management (versioned + auditable)
try:
    from clearledgr.api.ap_policies import router as ap_policies_router
    app.include_router(ap_policies_router)
except ImportError:
    pass

# Ops health/KPI endpoints (including browser-agent metrics)
try:
    from clearledgr.api.ops import router as ops_router
    app.include_router(ops_router)
except ImportError:
    pass

# Admin Center APIs (single contract for console + onboarding)
try:
    from clearledgr.api.admin_console import router as admin_console_router
    admin_console_enabled = str(os.getenv("ADMIN_CONSOLE_ENABLED", "true")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    if admin_console_enabled:
        app.include_router(admin_console_router)
except ImportError:
    pass

# Start Gmail autopilot (24/7 background inbox scanning)
@app.on_event("startup")
async def startup_gmail_autopilot():
    """Start Gmail autopilot for automatic invoice detection."""
    try:
        from clearledgr.services.gmail_autopilot import start_gmail_autopilot
        await start_gmail_autopilot(app)
        logger.info("Gmail autopilot started")
    except Exception as e:
        logger.warning(f"Gmail autopilot not started: {e}")


@app.on_event("startup")
async def startup_agent_background():
    """Start agent background intelligence loop."""
    try:
        from clearledgr.services.agent_background import start_agent_background
        await start_agent_background(app)
        logger.info("Agent background intelligence started")
    except Exception as e:
        logger.warning(f"Agent background not started: {e}")


@app.on_event("startup")
async def startup_agent_runtime():
    """Start the finance agent runtime and resume interrupted planner tasks."""
    try:
        from clearledgr.services.agent_orchestrator import get_orchestrator
        from clearledgr.services.finance_agent_runtime import get_platform_finance_runtime

        runtime = get_platform_finance_runtime("default")
        resumed = await runtime.resume_pending_agent_tasks()
        get_orchestrator("default").start_durable_workers()
        logger.info("Finance agent runtime started (%d planner tasks resumed)", resumed)
    except Exception as e:
        logger.warning(f"Agent runtime not started: {e}")


@app.on_event("shutdown")
async def shutdown_gmail_autopilot():
    """Stop Gmail autopilot background service."""
    try:
        from clearledgr.services.gmail_autopilot import stop_gmail_autopilot
        await stop_gmail_autopilot(app)
    except Exception as e:
        logger.warning(f"Gmail autopilot stop failed: {e}")


@app.on_event("shutdown")
async def shutdown_agent_background():
    """Stop agent background intelligence loop."""
    try:
        from clearledgr.services.agent_background import stop_agent_background
        from clearledgr.services.agent_orchestrator import get_orchestrator

        await stop_agent_background()
        await get_orchestrator("default").stop_durable_workers()
    except Exception as e:
        logger.warning(f"Agent background stop failed: {e}")

# Serve static files (admin page)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/console", tags=["Admin"], include_in_schema=False)
async def console_page():
    """Customer-facing Admin Center UI."""
    enabled = str(os.getenv("ADMIN_CONSOLE_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"}
    if not enabled:
        raise HTTPException(status_code=404, detail="Admin console disabled")
    console_file = os.path.join(os.path.dirname(__file__), "static", "console", "index.html")
    if os.path.exists(console_file):
        return FileResponse(console_file)
    raise HTTPException(status_code=404, detail="Console page not found")


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
    from clearledgr.services.monitoring import get_monitor
    
    monitor = get_monitor()
    health_status = await monitor.check_health()
    
    return {
        **health_status,
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
