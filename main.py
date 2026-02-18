"""
Clearledgr AP v1 - Embedded AP Execution API

Scope (PRD):
- Gmail intake for AP items
- Slack approval callbacks
- ERP posting
- Immutable audit trail
No dashboards, reconciliation, or non-AP workflows.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.middleware.base import BaseHTTPMiddleware

from clearledgr.core.database import get_db
from clearledgr.services.errors import ClearledgrError
from clearledgr.services.logging import log_error, log_request, logger
from clearledgr.services.rate_limit import RateLimitMiddleware

from clearledgr.api import (
    gmail_extension_router,
    slack_invoices_router,
    teams_invoices_router,
)
from clearledgr.api.auth import router as auth_router
from clearledgr.api.gmail_webhooks import router as gmail_webhooks_router
from clearledgr.api.ap_items import router as ap_items_router
from clearledgr.api.ap_policies import router as ap_policies_router
from clearledgr.api.audit_events import router as audit_events_router
from clearledgr.api.agent_sessions import router as agent_sessions_router
from clearledgr.api.ops import router as ops_router


app = FastAPI(
    title="Clearledgr AP v1",
    description="Embedded AP execution for Gmail and Slack.",
    version="1.0.0",
)

# Core routers (AP-only)
app.include_router(gmail_extension_router)
app.include_router(slack_invoices_router)
app.include_router(teams_invoices_router)
app.include_router(auth_router)
app.include_router(gmail_webhooks_router)
app.include_router(ap_items_router, prefix="/api")
app.include_router(ap_policies_router)
app.include_router(audit_events_router)
app.include_router(agent_sessions_router)
app.include_router(ops_router)


DEFAULT_CORS_ORIGINS = [
    "https://mail.google.com",
    "https://gmail.google.com",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]


def _get_cors_origins() -> list[str]:
    configured = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    origins = list(DEFAULT_CORS_ORIGINS)
    if configured:
        for origin in [value.strip() for value in configured.split(",") if value.strip()]:
            if origin not in origins:
                origins.append(origin)
    return origins


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Minimal request logging and metrics."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        try:
            log_request(
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=0,
                client_id=request.client.host if request.client else "unknown",
            )
        except Exception:
            pass
        return response


class PrivateNetworkCORSMiddleware(BaseHTTPMiddleware):
    """Allow secure origins (Gmail) to call localhost APIs in Chromium."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if (
            request.method == "OPTIONS"
            and request.headers.get("access-control-request-private-network", "").lower() == "true"
        ):
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_origin_regex=r"^(chrome-extension://.*|https://mail\.google\.com(:\d+)?|https://gmail\.google\.com(:\d+)?)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(PrivateNetworkCORSMiddleware)


@app.exception_handler(ClearledgrError)
async def clearledgr_exception_handler(request: Request, exc: ClearledgrError):
    from fastapi.responses import JSONResponse
    log_error(exc.code.value, str(exc), exc.context)
    return JSONResponse(status_code=500, content=exc.to_dict())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    from fastapi.responses import JSONResponse
    log_error("unhandled_exception", str(exc), {"path": str(request.url.path)})
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


@app.on_event("startup")
async def startup_event():
    """Initialize DB and optional Gmail autopilot."""
    db = get_db()
    db.initialize()
    try:
        from clearledgr.workflows.ap.worker import get_ap_temporal_worker_runtime

        runtime = get_ap_temporal_worker_runtime()
        await runtime.start()
    except Exception as exc:
        logger.warning("AP Temporal worker not started: %s", exc)
    try:
        from clearledgr.services.gmail_autopilot import start_gmail_autopilot
        await start_gmail_autopilot(app)
        logger.info("Gmail autopilot started")
    except Exception as exc:
        logger.warning("Gmail autopilot not started: %s", exc)


@app.on_event("shutdown")
async def shutdown_event():
    """Stop background services."""
    try:
        from clearledgr.workflows.ap.worker import get_ap_temporal_worker_runtime

        runtime = get_ap_temporal_worker_runtime()
        await runtime.stop()
    except Exception as exc:
        logger.warning("AP Temporal worker stop failed: %s", exc)
    try:
        from clearledgr.services.gmail_autopilot import stop_gmail_autopilot
        await stop_gmail_autopilot(app)
    except Exception as exc:
        logger.warning("Gmail autopilot stop failed: %s", exc)


@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "product": "clearledgr-ap-v1"}
