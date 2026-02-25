"""
Clearledgr v1 - FastAPI Backend

Clearledgr v1: Transaction Reconciliation & Categorization

Run Instructions:
-----------------
1. Install dependencies:
   pip install -r requirements

2. Run the app locally with uvicorn:
   uvicorn main:app --host 0.0.0.0 --port 8000 --reload

3. Test /health endpoint:
   curl http://localhost:8000/health

4. Test reconciliation endpoint:
   curl -X POST http://localhost:8000/run-reconciliation \
     -F "config={...}" -F "period_start=2025-11-01" ...
"""
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
import json
import os
from typing import Optional, List, Dict
from clearledgr.services.auth import verify_api_key, get_api_key_optional
from clearledgr.services.rate_limit import RateLimitMiddleware
from clearledgr.services.errors import ClearledgrError, ReconciliationError, to_http_exception
from clearledgr.core.errors import safe_error
from clearledgr.services.validation import SheetsRunRequest as SheetsRunRequestModel
from clearledgr.services.logging import log_request, log_reconciliation_run, log_error, logger
from clearledgr.services.metrics import record_request, record_error, record_reconciliation_run, get_metrics
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
    ai_enhanced_router,
    ap_workflow_router,
    ap_advanced_router,
)

app = FastAPI(
    title="Clearledgr API",
    description="""
    Clearledgr API v1 - Transaction Reconciliation & Categorization
    
    **Clearledgr is a unifying intelligent layer for finance teams, embedding AI agents into tools finance teams already use.**
    
    This API powers embedded intelligence within Google Sheets, Gmail, and Slack.
    
    ## Transaction Reconciliation
    - Multi-source reconciliation (CSV, Google Sheets)
    - Intelligent 3-way/2-way transaction matching with tolerance and date windows
    - LLM-powered exception explanations
    - Real-time notifications (Slack app)
    
    ## Transaction Categorization
    - Auto-classify transactions to GL accounts
    - Keyword, pattern, and historical matching
    - Learns from user corrections
    
    ## Email Integration
    - Autonomous processing of invoices and settlements
    - Auto-categorization and matching
    
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

app.include_router(v1_router)
app.include_router(erp_router)
app.include_router(gmail_extension_router)
app.include_router(slack_invoices_router)
app.include_router(teams_invoices_router)

for optional_router in (ai_enhanced_router, ap_workflow_router, ap_advanced_router):
    if optional_router:
        app.include_router(optional_router)

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

# Add middleware in order (last added = outermost, executed first).
# CorrelationIdMiddleware must be outermost so correlation_id is available to
# all downstream middleware and handlers.
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(CorrelationIdMiddleware)


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


_default_cors_origins = [
    "https://mail.google.com",
    "https://gmail.google.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8010",
    "http://127.0.0.1:8010",
]

_configured_cors_origins = _parse_cors_origins(os.getenv("CORS_ALLOW_ORIGINS", ""))
_cors_allow_origins = _configured_cors_origins or _default_cors_origins
_cors_allow_origin_regex = os.getenv("CORS_ALLOW_ORIGIN_REGEX", r"^chrome-extension://[a-z]{32}$")

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
    """Initialize database on startup."""
    pass  # ClearledgrDB initializes lazily via get_db()

# Include Slack app
try:
    from ui.slack.app import router as slack_router
    app.include_router(slack_router)
except ImportError:
    pass

# Include Teams app
try:
    from ui.teams.app import router as teams_router
    app.include_router(teams_router)
except ImportError:
    pass

# Include LLM Proxy
try:
    from clearledgr.api.llm_proxy import router as llm_router
    app.include_router(llm_router)
except ImportError:
    pass

# Include Enhanced AI API
try:
    from clearledgr.api.ai_enhanced import router as ai_router
    app.include_router(ai_router)
except ImportError:
    pass

# (Autonomous agent, chat, engine, and webhooks routers removed — archived to branch)

# Include Onboarding API
try:
    from clearledgr.api.onboarding import router as onboarding_router
    app.include_router(onboarding_router)
except ImportError:
    pass

# Include Auth API
try:
    from clearledgr.api.auth import router as auth_router
    app.include_router(auth_router)
except ImportError:
    pass

# Include ERP OAuth API
try:
    from clearledgr.api.erp_oauth import router as erp_oauth_router
    app.include_router(erp_oauth_router)
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

# Settings API
try:
    from clearledgr.api.settings import router as settings_router
    app.include_router(settings_router)
except ImportError:
    pass

# Analytics/Dashboard API
try:
    from clearledgr.api.analytics import router as analytics_router
    app.include_router(analytics_router)
except ImportError:
    pass

# Payments API
try:
    from clearledgr.api.payments import router as payments_router
    app.include_router(payments_router)
except ImportError:
    pass

# Bank feeds (Okra for Africa, TrueLayer/Nordigen for Europe)
try:
    from clearledgr.api.bank_feeds import router as bank_feeds_router
    app.include_router(bank_feeds_router)
except ImportError:
    pass

# Learning / Feedback loop (vendor→GL mappings)
try:
    from clearledgr.api.learning import router as learning_router
    app.include_router(learning_router)
except ImportError:
    pass

# AP Workflow routes (payments, GL corrections, recurring)
try:
    from clearledgr.api.ap_workflow import router as ap_workflow_router
    app.include_router(ap_workflow_router)
except ImportError:
    pass

# AP Advanced routes (document retention, multi-currency, tax, accruals)
try:
    from clearledgr.api.ap_advanced import router as ap_advanced_router
    app.include_router(ap_advanced_router)
except ImportError:
    pass

# Payment Requests API (email/Slack/UI payment requests)
try:
    from clearledgr.api.payment_requests import router as payment_requests_router
    app.include_router(payment_requests_router)
except ImportError:
    pass

# Subscription & billing
try:
    from clearledgr.api.subscription import router as subscription_router
    app.include_router(subscription_router)
except ImportError:
    pass

# Browser-agent control plane APIs
try:
    from clearledgr.api.agent_sessions import router as agent_sessions_router
    app.include_router(agent_sessions_router)
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
        await stop_agent_background()
    except Exception as e:
        logger.warning(f"Agent background stop failed: {e}")

# Serve static files (admin page)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/admin", tags=["Admin"], include_in_schema=False)
async def admin_page():
    """Internal admin page for QA and testing. Dev-only."""
    if os.getenv("ENV", "dev").lower() not in ("dev", "development", "test"):
        raise HTTPException(status_code=404)
    admin_file = os.path.join(os.path.dirname(__file__), "static", "admin.html")
    if os.path.exists(admin_file):
        return FileResponse(admin_file)
    else:
        raise HTTPException(status_code=404, detail="Admin page not found")


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


# ============================================================================
# EMAIL INTEGRATION ENDPOINTS
# ============================================================================

from clearledgr.services.email_parser import parse_email, parse_invoice_text, parse_payment_confirmation
from clearledgr.services.email_matcher import (
    match_invoice_to_transactions, match_payment_to_invoice,
    get_exceptions_for_vendor
)
from clearledgr.services.email_tasks import (
    create_task_from_email, update_task_status, assign_task, add_comment,
    get_task, get_tasks, get_tasks_for_email, get_overdue_tasks, TaskTypes, TaskStatus
)
from clearledgr.services.audit_trail import (
    record_audit_event, get_audit_trail, get_entity_history, get_user_activity,
    AuditActions, EntityTypes, SourceTypes
)
from clearledgr.services.task_notifications import (
    send_task_created_notification, send_task_assigned_notification,
    send_task_completed_notification, send_task_comment_notification,
    send_overdue_summary
)
from clearledgr.services.task_scheduler import run_all_checks, run_overdue_check


class ParseEmailRequest(BaseModel):
    """Request to parse an email."""
    subject: str
    body: str
    sender: str
    attachments: Optional[List[Dict]] = None


class MatchInvoiceRequest(BaseModel):
    """Request to match an invoice to transactions."""
    invoice: Dict
    bank_transactions: List[Dict]
    internal_transactions: Optional[List[Dict]] = None
    config: Optional[Dict] = None


class MatchPaymentRequest(BaseModel):
    """Request to match a payment to invoices."""
    payment: Dict
    open_invoices: List[Dict]
    config: Optional[Dict] = None


class VendorExceptionsRequest(BaseModel):
    """Request to get vendor exceptions."""
    vendor: str
    all_invoices: List[Dict]
    all_transactions: List[Dict]
    config: Optional[Dict] = None


class CreateTaskRequest(BaseModel):
    """Request to create a task from email."""
    email_id: str
    email_subject: str
    email_sender: str
    thread_id: str
    created_by: str
    task_type: str
    title: Optional[str] = None
    description: Optional[str] = None
    assignee_email: Optional[str] = None
    due_date: Optional[str] = None
    priority: str = "medium"
    related_entity_type: Optional[str] = None
    related_entity_id: Optional[str] = None
    related_amount: Optional[float] = None
    related_vendor: Optional[str] = None
    tags: Optional[List[str]] = None
    organization_id: Optional[str] = None


class UpdateTaskStatusRequest(BaseModel):
    """Request to update task status."""
    task_id: str
    new_status: str
    changed_by: str
    notes: Optional[str] = None


class AssignTaskRequest(BaseModel):
    """Request to assign a task."""
    task_id: str
    assignee_email: str
    assigned_by: str


class AddCommentRequest(BaseModel):
    """Request to add comment to task."""
    task_id: str
    user_email: str
    comment: str


class RecordAuditRequest(BaseModel):
    """Request to record audit event."""
    user_email: str
    action: str
    entity_type: str
    entity_id: Optional[str] = None
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    source_name: Optional[str] = None
    before_state: Optional[Dict] = None
    after_state: Optional[Dict] = None
    metadata: Optional[Dict] = None
    organization_id: Optional[str] = None


class EmailProcessRequest(BaseModel):
    """Full email processing request."""
    subject: str
    body: str
    sender: str
    attachments: Optional[List[Dict]] = None
    bank_transactions: List[Dict]
    internal_transactions: Optional[List[Dict]] = None
    open_invoices: Optional[List[Dict]] = None
    user_email: str
    organization_id: Optional[str] = None
    config: Optional[Dict] = None


@app.post(
    "/email/parse",
    tags=["Email Integration"],
    summary="Parse Email",
    description="Parse an email and extract financial data"
)
async def parse_email_endpoint(
    request: ParseEmailRequest,
    api_key: str = Depends(get_api_key_optional),
):
    """
    Parse an email and extract financial data.
    
    Extracts:
    - Email type (invoice, payment, statement, etc.)
    - Amounts and currencies
    - Invoice numbers
    - Dates
    - Vendor information
    """
    try:
        result = parse_email(
            subject=request.subject,
            body=request.body,
            sender=request.sender,
            attachments=request.attachments
        )
        return result
    except Exception as e:
        log_error("email_parse_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "email parse")
        )


@app.post(
    "/email/match-invoice",
    tags=["Email Integration"],
    summary="Match Invoice to Transactions",
    description="Match a parsed invoice to bank and internal transactions"
)
async def match_invoice_endpoint(
    request: MatchInvoiceRequest,
    api_key: str = Depends(get_api_key_optional),
):
    """
    Match invoice to transactions.
    
    Performs 3-way or 2-way matching and returns match confidence.
    Auto-approves high-confidence matches (>= 90%).
    """
    try:
        result = match_invoice_to_transactions(
            invoice=request.invoice,
            bank_transactions=request.bank_transactions,
            internal_transactions=request.internal_transactions,
            config=request.config
        )
        return result
    except Exception as e:
        log_error("invoice_match_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "invoice match")
        )


@app.post(
    "/email/match-payment",
    tags=["Email Integration"],
    summary="Match Payment to Invoices",
    description="Match a payment confirmation to open invoices"
)
async def match_payment_endpoint(
    request: MatchPaymentRequest,
    api_key: str = Depends(get_api_key_optional),
):
    """
    Match payment to open invoices.
    
    Finds matching open invoices based on amount, date, and payer.
    """
    try:
        result = match_payment_to_invoice(
            payment=request.payment,
            open_invoices=request.open_invoices,
            config=request.config
        )
        return result
    except Exception as e:
        log_error("payment_match_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "payment match")
        )


@app.post(
    "/email/vendor-exceptions",
    tags=["Email Integration"],
    summary="Get Vendor Exceptions",
    description="Get all unmatched items for a vendor"
)
async def vendor_exceptions_endpoint(
    request: VendorExceptionsRequest,
    api_key: str = Depends(get_api_key_optional),
):
    """
    Get exception summary for a vendor.
    
    Returns unmatched invoices and transactions for the vendor.
    """
    try:
        result = get_exceptions_for_vendor(
            vendor=request.vendor,
            all_invoices=request.all_invoices,
            all_transactions=request.all_transactions,
            config=request.config
        )
        return result
    except Exception as e:
        log_error("vendor_exceptions_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "vendor exceptions")
        )


@app.post(
    "/email/process",
    tags=["Email Integration"],
    summary="Process Email End-to-End",
    description="Parse email, match to transactions, and record audit trail"
)
async def process_email_endpoint(
    request: EmailProcessRequest,
    api_key: str = Depends(get_api_key_optional),
):
    """
    Full end-to-end email processing.
    
    1. Parses the email
    2. Matches to transactions based on email type
    3. Records audit trail
    4. Returns match results and exceptions
    """
    try:
        # Step 1: Parse email
        parsed = parse_email(
            subject=request.subject,
            body=request.body,
            sender=request.sender,
            attachments=request.attachments
        )
        
        # Step 2: Match based on email type
        match_result = None
        if parsed['email_type'] == 'invoice':
            match_result = match_invoice_to_transactions(
                invoice=parsed,
                bank_transactions=request.bank_transactions,
                internal_transactions=request.internal_transactions,
                config=request.config
            )
        elif parsed['email_type'] == 'payment_confirmation' and request.open_invoices:
            match_result = match_payment_to_invoice(
                payment=parsed,
                open_invoices=request.open_invoices,
                config=request.config
            )
        
        # Step 3: Record audit trail
        audit_event_id = record_audit_event(
            user_email=request.user_email,
            action=AuditActions.EMAIL_PROCESSED,
            entity_type=EntityTypes.EMAIL,
            source_type=SourceTypes.EMAIL,
            source_name=request.subject,
            after_state={
                "parsed": parsed,
                "match_result": match_result
            },
            organization_id=request.organization_id
        )
        
        # Step 4: Get vendor exceptions if we have vendor info
        vendor_exceptions = None
        vendor = parsed.get('vendor')
        if vendor:
            vendor_exceptions = get_exceptions_for_vendor(
                vendor=vendor,
                all_invoices=request.open_invoices or [],
                all_transactions=request.bank_transactions,
                config=request.config
            )
        
        # Step 5: AUTO-CREATE TASK if exception found (autonomous behavior)
        auto_created_task = None
        if match_result and not match_result.get('matched'):
            # Clearledgr automatically creates follow-up task for unmatched items
            task_type = "reconcile_item"
            if parsed.get('email_type') == 'invoice':
                task_type = "reconcile_item"
            elif parsed.get('email_type') == 'payment_confirmation':
                task_type = "verify_payment"
            
            amount_str = ""
            if parsed.get('primary_amount'):
                amt = parsed['primary_amount']
                amount_str = f" ({amt.get('currency', 'EUR')} {amt.get('value', 0):,.2f})"
            
            auto_created_task = create_task_from_email(
                email_id=f"email_{datetime.now(timezone.utc).timestamp()}",
                email_subject=parsed.get('subject', 'Finance Email'),
                email_sender=parsed.get('sender', ''),
                thread_id=f"thread_{datetime.now(timezone.utc).timestamp()}",
                created_by="clearledgr-agent",
                task_type=task_type,
                title=f"Unmatched {parsed.get('email_type', 'item')} from {vendor}{amount_str}",
                description=f"Clearledgr could not auto-match this item. Reason: {match_result.get('message', 'No matching transaction found')}",
                priority="medium" if not parsed.get('primary_amount') or parsed['primary_amount'].get('value', 0) < 10000 else "high",
                related_vendor=vendor,
                related_amount=parsed.get('primary_amount', {}).get('value') if parsed.get('primary_amount') else None,
                organization_id=request.organization_id
            )
            
            # Notify via Slack/Teams
            try:
                send_task_created_notification(auto_created_task)
            except Exception:
                pass
        
        return {
            "parsed_email": parsed,
            "match_result": match_result,
            "vendor_exceptions": vendor_exceptions,
            "audit_event_id": audit_event_id,
            "auto_matched": match_result.get('auto_approve', False) if match_result else False,
            "auto_created_task": auto_created_task
        }
    except Exception as e:
        log_error("email_process_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "email processing")
        )


# Task Management Endpoints

@app.post(
    "/email/tasks",
    tags=["Email Tasks"],
    summary="Create Task from Email",
    description="Create a close task from an email thread"
)
async def create_task_endpoint(
    request: CreateTaskRequest,
    api_key: str = Depends(get_api_key_optional),
):
    """Create a task from an email."""
    try:
        result = create_task_from_email(
            email_id=request.email_id,
            email_subject=request.email_subject,
            email_sender=request.email_sender,
            thread_id=request.thread_id,
            created_by=request.created_by,
            task_type=request.task_type,
            title=request.title,
            description=request.description,
            assignee_email=request.assignee_email,
            due_date=request.due_date,
            priority=request.priority,
            related_entity_type=request.related_entity_type,
            related_entity_id=request.related_entity_id,
            related_amount=request.related_amount,
            related_vendor=request.related_vendor,
            tags=request.tags,
            organization_id=request.organization_id
        )
        
        # Record audit
        record_audit_event(
            user_email=request.created_by,
            action=AuditActions.TASK_CREATED,
            entity_type=EntityTypes.TASK,
            entity_id=result['task_id'],
            source_type=SourceTypes.EMAIL,
            source_id=request.email_id,
            after_state=result,
            organization_id=request.organization_id
        )
        
        # Send Slack/Teams notification (skip reconciliation exceptions to avoid spam)
        should_notify = request.task_type != "reconciliation_exception"
        if request.tags and "silent" in request.tags:
            should_notify = False
        if should_notify:
            try:
                send_task_created_notification(result)
            except Exception:
                pass  # Non-fatal
        
        return result
    except Exception as e:
        log_error("create_task_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "create task")
        )


@app.patch(
    "/email/tasks/status",
    tags=["Email Tasks"],
    summary="Update Task Status",
    description="Update the status of a task"
)
async def update_task_status_endpoint(
    request: UpdateTaskStatusRequest,
    api_key: str = Depends(get_api_key_optional),
):
    """Update task status."""
    try:
        result = update_task_status(
            task_id=request.task_id,
            new_status=request.new_status,
            changed_by=request.changed_by,
            notes=request.notes
        )
        
        # Send Slack/Teams notification for completion
        if request.new_status == "completed":
            try:
                send_task_completed_notification(result)
            except Exception:
                pass  # Non-fatal
        
        return result
    except Exception as e:
        log_error("update_task_status_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "update task status")
        )


@app.patch(
    "/email/tasks/assign",
    tags=["Email Tasks"],
    summary="Assign Task",
    description="Assign a task to a user"
)
async def assign_task_endpoint(
    request: AssignTaskRequest,
    api_key: str = Depends(get_api_key_optional),
):
    """Assign task to user."""
    try:
        result = assign_task(
            task_id=request.task_id,
            assignee_email=request.assignee_email,
            assigned_by=request.assigned_by
        )
        
        # Record audit
        record_audit_event(
            user_email=request.assigned_by,
            action=AuditActions.TASK_ASSIGNED,
            entity_type=EntityTypes.TASK,
            entity_id=request.task_id,
            after_state={"assignee": request.assignee_email}
        )
        
        # Send Slack/Teams notification
        try:
            send_task_assigned_notification(result, request.assigned_by)
        except Exception:
            pass  # Non-fatal
        
        return result
    except Exception as e:
        log_error("assign_task_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "assign task")
        )


@app.post(
    "/email/tasks/comments",
    tags=["Email Tasks"],
    summary="Add Task Comment",
    description="Add a comment to a task"
)
async def add_comment_endpoint(
    request: AddCommentRequest,
    api_key: str = Depends(get_api_key_optional),
):
    """Add comment to task."""
    try:
        result = add_comment(
            task_id=request.task_id,
            user_email=request.user_email,
            comment=request.comment
        )
        
        # Send Slack/Teams notification
        try:
            task = get_task(request.task_id)
            if task:
                send_task_comment_notification(task, request.comment, request.user_email)
        except Exception:
            pass  # Non-fatal
        
        return result
    except Exception as e:
        log_error("add_comment_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "add comment")
        )


@app.get(
    "/email/tasks/{task_id}",
    tags=["Email Tasks"],
    summary="Get Task",
    description="Get a task by ID"
)
async def get_task_endpoint(
    task_id: str,
    api_key: str = Depends(get_api_key_optional),
):
    """Get task by ID."""
    try:
        result = get_task(task_id)
        if not result:
            raise HTTPException(status_code=404, detail="Task not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        log_error("get_task_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "get task")
        )


@app.get(
    "/email/tasks",
    tags=["Email Tasks"],
    summary="List Tasks",
    description="List tasks with optional filters"
)
async def list_tasks_endpoint(
    status: Optional[str] = None,
    assignee_email: Optional[str] = None,
    task_type: Optional[str] = None,
    organization_id: Optional[str] = None,
    include_completed: bool = False,
    limit: int = 100,
    api_key: str = Depends(get_api_key_optional),
):
    """List tasks with filters."""
    try:
        result = get_tasks(
            status=status,
            assignee_email=assignee_email,
            task_type=task_type,
            organization_id=organization_id,
            include_completed=include_completed,
            limit=limit
        )
        return {"tasks": result, "count": len(result)}
    except Exception as e:
        log_error("list_tasks_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "list tasks")
        )


@app.get(
    "/email/tasks/by-email/{email_id}",
    tags=["Email Tasks"],
    summary="Get Tasks for Email",
    description="Get all tasks created from a specific email"
)
async def get_tasks_for_email_endpoint(
    email_id: str,
    api_key: str = Depends(get_api_key_optional),
):
    """Get tasks created from an email."""
    try:
        result = get_tasks_for_email(email_id)
        return {"tasks": result, "count": len(result)}
    except Exception as e:
        log_error("get_tasks_for_email_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "tasks for email")
        )


@app.get(
    "/email/tasks/overdue",
    tags=["Email Tasks"],
    summary="Get Overdue Tasks",
    description="Get all overdue tasks"
)
async def get_overdue_tasks_endpoint(
    organization_id: Optional[str] = None,
    api_key: str = Depends(get_api_key_optional),
):
    """Get overdue tasks."""
    try:
        result = get_overdue_tasks(organization_id)
        return {"tasks": result, "count": len(result)}
    except Exception as e:
        log_error("get_overdue_tasks_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "overdue tasks")
        )


@app.post(
    "/email/tasks/notify-overdue",
    tags=["Email Tasks"],
    summary="Send Overdue Tasks Notification",
    description="Send Slack/Teams notification for all overdue tasks"
)
async def notify_overdue_tasks_endpoint(
    organization_id: Optional[str] = None,
    api_key: str = Depends(get_api_key_optional),
):
    """Send notification for overdue tasks to Slack/Teams."""
    try:
        tasks = get_overdue_tasks(organization_id)
        if tasks:
            send_overdue_summary(tasks)
        return {
            "notified": True,
            "overdue_count": len(tasks),
            "message": f"Sent notification for {len(tasks)} overdue task(s)"
        }
    except Exception as e:
        log_error("notify_overdue_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "overdue notification")
        )


@app.post(
    "/email/tasks/run-scheduler",
    tags=["Email Tasks"],
    summary="Run Task Scheduler",
    description="Run all scheduled task checks (overdue, approaching deadline, stale tasks)"
)
async def run_task_scheduler_endpoint(
    api_key: str = Depends(get_api_key_optional),
):
    """
    Run all task scheduler checks.
    
    Clearledgr autonomously:
    - Checks for overdue tasks and sends reminders
    - Checks for approaching deadlines (due tomorrow/day after)
    - Checks for stale tasks with no activity
    - Escalates severely overdue tasks (7+ days)
    
    This endpoint should be called periodically (e.g., daily via cron).
    """
    try:
        result = run_all_checks()
        return result
    except Exception as e:
        log_error("task_scheduler_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "task scheduler")
        )


# Audit Trail Endpoints

@app.post(
    "/audit/record",
    tags=["Audit Trail"],
    summary="Record Audit Event",
    description="Record an audit event"
)
async def record_audit_endpoint(
    request: RecordAuditRequest,
    api_key: str = Depends(get_api_key_optional),
):
    """Record an audit event."""
    try:
        event_id = record_audit_event(
            user_email=request.user_email,
            action=request.action,
            entity_type=request.entity_type,
            entity_id=request.entity_id,
            source_type=request.source_type,
            source_id=request.source_id,
            source_name=request.source_name,
            before_state=request.before_state,
            after_state=request.after_state,
            metadata=request.metadata,
            organization_id=request.organization_id
        )
        return {"event_id": event_id, "status": "recorded"}
    except Exception as e:
        log_error("record_audit_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "record audit")
        )


@app.get(
    "/audit/trail",
    tags=["Audit Trail"],
    summary="Get Audit Trail",
    description="Query audit trail with filters"
)
async def get_audit_trail_endpoint(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    user_email: Optional[str] = None,
    organization_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
    api_key: str = Depends(get_api_key_optional),
):
    """Query audit trail."""
    try:
        result = get_audit_trail(
            entity_type=entity_type,
            entity_id=entity_id,
            user_email=user_email,
            organization_id=organization_id,
            start_date=start_date,
            end_date=end_date,
            action=action,
            limit=limit
        )
        return {"events": result, "count": len(result)}
    except Exception as e:
        log_error("get_audit_trail_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "audit trail")
        )


@app.get(
    "/audit/entity/{entity_type}/{entity_id}",
    tags=["Audit Trail"],
    summary="Get Entity History",
    description="Get complete audit history for an entity"
)
async def get_entity_history_endpoint(
    entity_type: str,
    entity_id: str,
    api_key: str = Depends(get_api_key_optional),
):
    """Get entity history."""
    try:
        result = get_entity_history(entity_type, entity_id)
        return {"events": result, "count": len(result)}
    except Exception as e:
        log_error("get_entity_history_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "entity history")
        )


@app.get(
    "/audit/user/{user_email}",
    tags=["Audit Trail"],
    summary="Get User Activity",
    description="Get recent activity for a user"
)
async def get_user_activity_endpoint(
    user_email: str,
    limit: int = 50,
    api_key: str = Depends(get_api_key_optional),
):
    """Get user activity."""
    try:
        result = get_user_activity(user_email, limit)
        return {"events": result, "count": len(result)}
    except Exception as e:
        log_error("get_user_activity_error", str(e))
        raise HTTPException(
            status_code=500,
            detail=safe_error(e, "user activity")
        )
