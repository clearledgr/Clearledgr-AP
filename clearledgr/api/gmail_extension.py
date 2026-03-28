"""API endpoints for the Clearledgr Gmail Extension.

The Gmail surface is an operator entrypoint. User-triggered AP actions should
enter through ``FinanceAgentRuntime`` so policy gates, idempotency, and audit
semantics are owned by one contract boundary. Lower-level workflows remain
implementation machinery behind that runtime seam.
"""
import json
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from pydantic import BaseModel, Field

from clearledgr.api.deps import get_audit_service
from clearledgr.services.audit import AuditTrailService
from clearledgr.workflows.temporal_runtime import TemporalRuntime, temporal_enabled

# Import all intelligence services
from clearledgr.services.vendor_intelligence import get_vendor_intelligence
from clearledgr.services.policy_compliance import get_policy_compliance
from clearledgr.services.priority_detection import get_priority_detection
from clearledgr.services.audit_trail import get_audit_trail, AuditEventType
from clearledgr.services.budget_awareness import get_budget_awareness
from clearledgr.services.agent_reflection import get_agent_reflection
from clearledgr.services.proactive_insights import get_proactive_insights
from clearledgr.services.cross_invoice_analysis import get_cross_invoice_analyzer
from clearledgr.services.agent_reasoning import get_agent as get_reasoning_agent
from clearledgr.services.gmail_extension_support import (
    _explain_fallback as support_explain_fallback,
    _explain_with_claude as support_explain_with_claude,
    apply_agent_reasoning as support_apply_agent_reasoning,
    apply_intelligence as support_apply_intelligence,
    build_amount_validation_payload,
    build_extension_pipeline as support_build_extension_pipeline,
    build_form_prefill_payload,
    build_gl_suggestion_payload,
    build_needs_info_draft_payload,
    build_vendor_suggestion_payload,
    build_verify_confidence_payload,
    merge_agent_extraction as support_merge_agent_extraction,
    pipeline_bucket_for_state as support_pipeline_bucket_for_state,
    render_ap_item_explanation,
)
from clearledgr.core.ap_confidence import evaluate_critical_field_confidence, extract_field_confidences
from clearledgr.core.ap_item_resolution import resolve_ap_item_reference
from clearledgr.core.auth import get_current_user, require_ops_user, create_access_token, get_user_by_email
from clearledgr.core.database import get_db
from clearledgr.services.ap_item_service import build_worklist_item
from clearledgr.services.ap_projection import build_worklist_items
from clearledgr.services.gmail_api import (
    GmailAPIClient,
    GmailToken,
    token_store,
    GMAIL_PROFILE_URL,
    GOOGLE_USERINFO_URL,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/extension", tags=["gmail-extension"])

_ADMIN_ROLES = {"admin", "owner"}
EXTENSION_BACKEND_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60


def _is_admin_user(user: Any) -> bool:
    return str(getattr(user, "role", "") or "").strip().lower() in _ADMIN_ROLES


def _assert_user_org_access(user: Any, organization_id: str) -> None:
    org_id = str(organization_id or "default")
    user_org = str(getattr(user, "organization_id", "") or "")
    if _is_admin_user(user):
        return
    if user_org != org_id:
        raise HTTPException(status_code=403, detail="org_mismatch")


def _resolve_org_id_for_user(user: Any, requested_org: Optional[str]) -> str:
    requested = str(requested_org or "").strip()
    if requested and requested != "default":
        _assert_user_org_access(user, requested)
        return requested
    return str(getattr(user, "organization_id", None) or "default")


def _authenticated_actor(user: Any, fallback: str = "extension") -> str:
    return str(
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or fallback
    ).strip() or fallback


def _build_finance_runtime(user: Any, organization_id: str, *, db: Any = None):
    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime

    actor = _authenticated_actor(user, fallback="gmail_extension")
    return FinanceAgentRuntime(
        organization_id=organization_id,
        actor_id=getattr(user, "user_id", None) or actor,
        actor_email=actor,
        db=db or get_db(),
    )


async def _recover_ap_item_for_thread(
    db: Any,
    *,
    organization_id: str,
    thread_id: str,
    user: Any,
) -> Optional[Dict[str, Any]]:
    """Self-heal detected Gmail invoices that never materialized into ap_items."""
    user_id = str(getattr(user, "user_id", None) or "").strip()
    if not user_id or not thread_id:
        return None

    try:
        gmail_client = GmailAPIClient(user_id)
        if not await gmail_client.ensure_authenticated():
            return None
        messages = await gmail_client.get_thread(thread_id)
    except Exception:
        return None

    if not messages:
        return None

    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime

    actor = _authenticated_actor(user, fallback="gmail_extension")
    runtime = FinanceAgentRuntime(
        organization_id=organization_id,
        actor_id=user_id or actor,
        actor_email=actor,
        db=db,
    )

    for message in messages:
        try:
            existing = db.get_ap_item_by_message_id(organization_id, message.id)
        except Exception:
            existing = None
        if existing:
            return existing

        finance_email = db.get_finance_email_by_gmail_id(message.id)
        if not finance_email:
            continue

        seeded = runtime.seed_ap_item_for_invoice_processing(
            {
                "gmail_id": thread_id,
                "thread_id": thread_id,
                "message_id": message.id,
                "gmail_thread_id": thread_id,
                "gmail_message_id": message.id,
                "subject": getattr(finance_email, "subject", "") or getattr(message, "subject", "") or "Invoice",
                "sender": getattr(finance_email, "sender", "") or getattr(message, "sender", "") or "",
                "vendor_name": getattr(finance_email, "vendor", None) or "",
                "amount": getattr(finance_email, "amount", None) or 0.0,
                "currency": getattr(finance_email, "currency", None) or "USD",
                "invoice_number": getattr(finance_email, "invoice_number", None),
                "confidence": getattr(finance_email, "confidence", 0.0) or 0.0,
                "organization_id": organization_id,
                "user_id": getattr(finance_email, "user_id", None) or user_id,
                "email_type": getattr(finance_email, "email_type", None) or "invoice",
                "intake_source": "gmail_thread_recovery",
            },
            correlation_id=f"gmail-thread-recovery:{thread_id}",
        )
        if seeded:
            return seeded
    return None


def _resolve_invoice_repair_user(
    user: Any,
    *,
    requested_user_email: Optional[str],
    organization_id: str,
) -> Dict[str, str]:
    requested_email = str(requested_user_email or "").strip().lower()
    actor_email = str(getattr(user, "email", None) or "").strip().lower()
    actor_user_id = str(getattr(user, "user_id", None) or "").strip()

    if not requested_email or requested_email == actor_email:
        if not actor_user_id:
            raise HTTPException(status_code=400, detail="missing_authenticated_user_id")
        return {
            "user_id": actor_user_id,
            "email": actor_email or _authenticated_actor(user, fallback="gmail_extension"),
        }

    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="target_user_requires_admin")

    target_user = get_user_by_email(requested_email)
    if not target_user:
        raise HTTPException(status_code=404, detail="target_user_not_found")
    if str(getattr(target_user, "organization_id", "") or "").strip() != organization_id:
        raise HTTPException(status_code=403, detail="org_mismatch")

    target_user_id = str(getattr(target_user, "user_id", None) or "").strip()
    if not target_user_id:
        raise HTTPException(status_code=400, detail="target_user_missing_id")

    return {
        "user_id": target_user_id,
        "email": str(getattr(target_user, "email", None) or requested_email).strip() or requested_email,
    }


def _linked_ap_item_for_finance_email(
    db: Any,
    *,
    organization_id: str,
    finance_email: Any,
) -> Optional[Dict[str, Any]]:
    gmail_id = str(getattr(finance_email, "gmail_id", None) or "").strip()
    if gmail_id and hasattr(db, "get_ap_item_by_message_id"):
        try:
            item = db.get_ap_item_by_message_id(organization_id, gmail_id)
            if item:
                return item
        except Exception:
            pass

    metadata = _parse_json_dict(getattr(finance_email, "metadata", {}))
    gmail_thread_id = str(metadata.get("gmail_thread_id") or "").strip()
    if gmail_thread_id and hasattr(db, "get_ap_item_by_thread"):
        try:
            item = db.get_ap_item_by_thread(organization_id, gmail_thread_id)
            if item:
                return item
        except Exception:
            pass
    return None


def _finance_email_needs_historical_repair(
    db: Any,
    *,
    organization_id: str,
    finance_email: Any,
) -> bool:
    metadata = _parse_json_dict(getattr(finance_email, "metadata", {}))
    linked_ap_item = _linked_ap_item_for_finance_email(
        db,
        organization_id=organization_id,
        finance_email=finance_email,
    )
    if not linked_ap_item:
        return True

    ap_metadata = _parse_json_dict(linked_ap_item.get("metadata"))
    finance_has_trace = bool(metadata.get("field_provenance")) and bool(metadata.get("field_evidence"))
    ap_has_trace = bool(ap_metadata.get("field_provenance")) and bool(ap_metadata.get("field_evidence"))
    if not finance_has_trace or not ap_has_trace:
        return True

    finance_conflicts = metadata.get("source_conflicts") if isinstance(metadata.get("source_conflicts"), list) else []
    ap_conflicts = ap_metadata.get("source_conflicts") if isinstance(ap_metadata.get("source_conflicts"), list) else []
    finance_has_blocking_conflicts = any(
        isinstance(entry, dict) and bool(entry.get("blocking"))
        for entry in finance_conflicts
    )
    if finance_conflicts and not ap_conflicts:
        return True
    if finance_has_blocking_conflicts and not bool(ap_metadata.get("requires_field_review")):
        return True
    return False


def _load_historical_invoice_repair_candidates(
    db: Any,
    *,
    organization_id: str,
    target_user_id: str,
    gmail_ids: List[str],
    limit: int,
    before_created_at: Optional[str],
    only_unrepaired: bool,
) -> List[Any]:
    requested_gmail_ids = [
        str(value).strip()
        for value in (gmail_ids or [])
        if str(value or "").strip()
    ]

    if requested_gmail_ids:
        rows: List[Any] = []
        for gmail_id in requested_gmail_ids:
            record = db.get_finance_email_by_gmail_id(gmail_id) if hasattr(db, "get_finance_email_by_gmail_id") else None
            if record:
                rows.append(record)
    elif hasattr(db, "list_finance_emails_for_repair"):
        rows = db.list_finance_emails_for_repair(
            organization_id,
            email_type="invoice",
            user_id=target_user_id,
            before_created_at=before_created_at,
            limit=max(limit * 3, limit),
        )
    else:
        rows = db.get_finance_emails(organization_id, limit=max(limit * 3, limit))

    candidates: List[Any] = []
    normalized_before = _parse_iso_utc(before_created_at)
    for row in rows:
        email_type = str(getattr(row, "email_type", "") or "").strip().lower()
        row_user_id = str(getattr(row, "user_id", None) or "").strip()
        created_at = _parse_iso_utc(getattr(row, "created_at", None) or getattr(row, "received_at", None))

        if email_type != "invoice":
            continue
        if row_user_id and row_user_id != target_user_id:
            continue
        if normalized_before and created_at and created_at >= normalized_before:
            continue
        if only_unrepaired and not _finance_email_needs_historical_repair(
            db,
            organization_id=organization_id,
            finance_email=row,
        ):
            continue
        candidates.append(row)
        if len(candidates) >= limit:
            break
    return candidates


# ==================== REQUEST MODELS ====================

class EmailTriageRequest(BaseModel):
    """Request to triage a single email."""
    email_id: str
    subject: Optional[str] = None
    sender: Optional[str] = None
    snippet: Optional[str] = None
    body: Optional[str] = None  # Full email body for better extraction
    attachments: Optional[List[Dict[str, Any]]] = None  # With content_base64 for Claude Vision
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class EmailProcessRequest(BaseModel):
    """Request to fully process an email (triage + match + action)."""
    email_id: str
    subject: Optional[str] = None
    sender: Optional[str] = None
    snippet: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    auto_approve: bool = False
    approval_threshold: float = 1000.0


class BulkScanRequest(BaseModel):
    """Request to scan multiple emails."""
    email_ids: List[str]
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class HistoricalInvoiceRepairRequest(BaseModel):
    """Replay historical invoice emails into the live AP record store."""
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    gmail_ids: List[str] = Field(default_factory=list)
    limit: int = 100
    before_created_at: Optional[str] = None
    only_unrepaired: bool = True


class GmailLabelCleanupRequest(BaseModel):
    """Migrate and delete obsolete Gmail labels from a Clearledgr mailbox."""
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    dry_run: bool = False
    max_messages_per_label: int = Field(default=1000, ge=1, le=5000)


class ApproveAndPostRequest(BaseModel):
    """Request to approve and post an invoice with HITL gate."""
    email_id: str
    ap_item_id: Optional[str] = None
    extraction: Dict[str, Any]
    bank_match: Optional[Dict[str, Any]] = None
    erp_match: Optional[Dict[str, Any]] = None
    override: bool = False  # Force post despite low confidence
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class VerifyConfidenceRequest(BaseModel):
    """Request to verify match confidence (HITL check)."""
    email_id: str
    extraction: Dict[str, Any]
    bank_match: Optional[Dict[str, Any]] = None
    erp_match: Optional[Dict[str, Any]] = None
    organization_id: Optional[str] = None


class EscalateRequest(BaseModel):
    """Request to escalate to manager via Slack."""
    email_id: str
    vendor: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "USD"
    confidence: Optional[float] = None
    mismatches: List[Dict[str, Any]] = []
    message: Optional[str] = None
    channel: str = "#finance-escalations"
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class MatchBankRequest(BaseModel):
    """Request to match against bank feed."""
    extraction: Dict[str, Any]
    organization_id: Optional[str] = None


class MatchERPRequest(BaseModel):
    """Request to match against ERP."""
    extraction: Dict[str, Any]
    organization_id: Optional[str] = None


class RegisterGmailTokenRequest(BaseModel):
    """Register OAuth token acquired by the Gmail extension."""
    access_token: str
    expires_in: Optional[int] = 3600
    email: Optional[str] = None
    organization_id: Optional[str] = None


# ==================== ENDPOINTS ====================

@router.post("/triage", dependencies=[Depends(get_current_user)])
async def triage_email(
    request: EmailTriageRequest,
    user=Depends(get_current_user),
):
    """
    Triage a single email - classify, extract, and apply intelligence.
    
    This triggers the EmailTriageWorkflow which:
    1. Classifies the email (INVOICE, REMITTANCE, STATEMENT, etc.)
    2. Extracts financial data (vendor, amount, due date)
    3. Applies Gmail labels
    4. Enriches with vendor intelligence
    5. Checks policy compliance
    6. Calculates priority
    7. Detects duplicates/anomalies
    8. Self-validates extraction
    
    Returns immediately with workflow_id if Temporal is enabled,
    or waits for result if running inline.
    """
    payload = request.model_dump()
    org_id = _resolve_org_id_for_user(user, request.organization_id)
    payload["organization_id"] = org_id
    
    # Build combined text for agent reasoning (used for both Temporal + inline)
    combined_text = "\n".join(
        [v for v in [request.subject, request.snippet, request.body] if v]
    ).strip()

    if temporal_enabled():
        runtime = TemporalRuntime()
        result = await runtime.start_workflow(
            "EmailTriageWorkflow",
            payload,
            task_queue="clearledgr-gmail",
            wait=True,
            timeout_seconds=30,
        )
        # Still apply intelligence to Temporal results
        if result.get("extraction"):
            result = await _apply_intelligence(result, org_id, request.email_id)
        # Apply agent reasoning (deep autonomy) even when Temporal is used
        result = _apply_agent_reasoning(
            result=result,
            org_id=org_id,
            combined_text=combined_text,
            attachments=request.attachments or [],
        )
        return result
    
    from clearledgr.services.gmail_triage_service import (
        run_inline_gmail_triage,
    )
    return await run_inline_gmail_triage(
        payload=payload,
        org_id=org_id,
        combined_text=combined_text,
        attachments=request.attachments or [],
        agent_reasoning_fn=_apply_agent_reasoning,
    )


async def _apply_intelligence(result: Dict[str, Any], org_id: str, email_id: str) -> Dict[str, Any]:
    """Apply intelligence services to a triage result."""
    return support_apply_intelligence(result, org_id, email_id)


def _merge_agent_extraction(
    extraction: Dict[str, Any],
    agent_extraction: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill missing extraction fields from agent reasoning output."""
    return support_merge_agent_extraction(extraction, agent_extraction)


def _apply_agent_reasoning(
    result: Dict[str, Any],
    org_id: str,
    combined_text: str,
    attachments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Run agent reasoning and merge decision + extraction."""
    return support_apply_agent_reasoning(
        result,
        org_id,
        combined_text,
        attachments,
        reasoning_agent_factory=get_reasoning_agent,
    )


@router.post("/process", dependencies=[Depends(get_current_user)])
async def process_email(
    request: EmailProcessRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user=Depends(require_ops_user),
):
    """
    Fully process an email - triage, match, and suggest/execute action.
    
    This triggers the EmailProcessingWorkflow which:
    1. Triages the email
    2. Matches against bank feed
    3. Matches against ERP (PO, vendor)
    4. Determines suggested action
    5. Auto-posts if approved and under threshold
    6. Routes exceptions if needed
    
    Use this for the "Process" button in the extension.
    """
    payload = request.model_dump()
    payload["organization_id"] = _resolve_org_id_for_user(user, request.organization_id)
    
    if temporal_enabled():
        runtime = TemporalRuntime()
        # Don't wait - this can take longer
        result = await runtime.start_workflow(
            "EmailProcessingWorkflow",
            payload,
            task_queue="clearledgr-gmail",
            wait=False,
        )
        return {
            "status": "processing",
            "workflow_id": result.get("workflow_id"),
            "email_id": request.email_id,
        }
    
    # Inline execution - simplified
    triage_result = await triage_email(
        EmailTriageRequest(**{k: v for k, v in payload.items() if k in EmailTriageRequest.model_fields}),
        audit=audit,
        user=user,
    )
    
    return {
        "email_id": request.email_id,
        "status": "processed_inline",
        "triage": triage_result,
    }


@router.post("/scan", dependencies=[Depends(get_current_user)])
async def bulk_scan_emails(
    request: BulkScanRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user=Depends(require_ops_user),
):
    """
    Scan multiple emails in bulk.
    
    This triggers the BulkEmailScanWorkflow which processes
    each email through the triage workflow.
    
    Use this for inbox scanning.
    """
    payload = request.model_dump()
    payload["organization_id"] = _resolve_org_id_for_user(user, request.organization_id)
    
    if temporal_enabled():
        runtime = TemporalRuntime()
        result = await runtime.start_workflow(
            "BulkEmailScanWorkflow",
            payload,
            task_queue="clearledgr-gmail",
            wait=False,  # Don't wait for bulk operations
        )
        return {
            "status": "scanning",
            "workflow_id": result.get("workflow_id"),
            "email_count": len(request.email_ids),
        }
    
    # Inline execution
    results = {
        "total": len(request.email_ids),
        "processed": 0,
        "labeled": 0,
        "by_type": {},
    }
    
    for email_id in request.email_ids[:50]:  # Limit inline processing
        try:
            triage = await triage_email(
                EmailTriageRequest(
                    email_id=email_id,
                    organization_id=request.organization_id,
                ),
                audit=audit,
                user=user,
            )
            results["processed"] += 1
            if triage.get("action") != "skipped":
                results["labeled"] += 1
        except Exception:
            pass
    
    return results


def _pipeline_bucket_for_state(state: Any) -> str:
    return support_pipeline_bucket_for_state(state)


def _build_extension_pipeline(db, organization_id: str, limit: int = 1000) -> Dict[str, List[Dict[str, Any]]]:
    return support_build_extension_pipeline(
        db,
        organization_id,
        limit=limit,
        build_item_fn=build_worklist_item,
    )


@router.get("/pipeline")
def get_invoice_pipeline(
    organization_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """Return invoice pipeline grouped by status for Gmail extension.

    This legacy endpoint is kept for compatibility and now mirrors the
    normalized exception taxonomy used by `/extension/worklist`.
    """
    org_id = _resolve_org_id_for_user(user, organization_id)
    db = get_db()
    return _build_extension_pipeline(db, org_id)


@router.get("/worklist")
def get_extension_worklist(
    organization_id: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=1000),
    user=Depends(get_current_user),
):
    """Return invoice-centric worklist for the focused Gmail sidebar.

    Requires authentication.  Non-admin users are restricted to their own
    organisation; admin/owner roles may request any org.
    """
    from fastapi import HTTPException

    org_id = _resolve_org_id_for_user(user, organization_id)

    db = get_db()
    items = db.list_ap_items(org_id, limit=limit, prioritized=True)
    normalized = build_worklist_items(
        db,
        items,
        build_item=build_worklist_item,
    )
    return {
        "organization_id": org_id,
        "items": normalized,
        "total": len(normalized),
    }


@router.post("/repair-historical-invoices", dependencies=[Depends(get_current_user)])
async def repair_historical_invoices(
    request: HistoricalInvoiceRepairRequest,
    user=Depends(require_ops_user),
):
    """Replay stored invoice emails to repair provenance/conflict fields on live AP rows."""
    org_id = _resolve_org_id_for_user(user, request.organization_id)
    target_user = _resolve_invoice_repair_user(
        user,
        requested_user_email=request.user_email,
        organization_id=org_id,
    )
    db = get_db()
    limit = max(1, min(_safe_int(request.limit, 100), 500))
    candidates = _load_historical_invoice_repair_candidates(
        db,
        organization_id=org_id,
        target_user_id=target_user["user_id"],
        gmail_ids=request.gmail_ids,
        limit=limit,
        before_created_at=request.before_created_at,
        only_unrepaired=bool(request.only_unrepaired),
    )

    if not candidates:
        return {
            "status": "completed",
            "organization_id": org_id,
            "mailbox_user_email": target_user["email"],
            "processed": 0,
            "repaired": 0,
            "review_required": 0,
            "errors": 0,
            "next_cursor": None,
            "results": [],
        }

    gmail_client = GmailAPIClient(target_user["user_id"])
    if not await gmail_client.ensure_authenticated():
        raise HTTPException(status_code=409, detail="gmail_not_connected")

    from clearledgr.api.gmail_webhooks import process_invoice_email

    repaired = 0
    review_required = 0
    errors = 0
    results: List[Dict[str, Any]] = []

    for record in candidates:
        gmail_id = str(getattr(record, "gmail_id", None) or "").strip()
        if not gmail_id:
            errors += 1
            results.append({"gmail_id": None, "status": "error", "reason": "missing_gmail_id"})
            continue

        try:
            message = await gmail_client.get_message(gmail_id)
            replay_result = await process_invoice_email(
                client=gmail_client,
                message=message,
                user_id=target_user["user_id"],
                organization_id=org_id,
                confidence=float(getattr(record, "confidence", 0.0) or 0.0),
                run_runtime=False,
                create_draft=False,
                refresh_reason="historical_repair_pass",
            )
        except Exception as exc:
            errors += 1
            results.append(
                {
                    "gmail_id": gmail_id,
                    "status": "error",
                    "reason": str(exc),
                }
            )
            continue

        updated_record = db.get_finance_email_by_gmail_id(gmail_id) if hasattr(db, "get_finance_email_by_gmail_id") else None
        linked_item = _linked_ap_item_for_finance_email(
            db,
            organization_id=org_id,
            finance_email=updated_record or record,
        )
        normalized_item = build_worklist_item(db, linked_item) if linked_item else None
        item_requires_review = bool(normalized_item and normalized_item.get("requires_field_review"))

        if item_requires_review or str(getattr(updated_record, "status", "") or "").strip().lower() == "review_required":
            review_required += 1
        else:
            repaired += 1

        results.append(
            {
                "gmail_id": gmail_id,
                "thread_id": str(getattr(message, "thread_id", None) or "").strip() or None,
                "status": str(replay_result.get("status") or "refreshed"),
                "finance_email_status": str(getattr(updated_record, "status", None) or "").strip() or None,
                "ap_item_id": str((normalized_item or {}).get("id") or "").strip() or None,
                "requires_field_review": item_requires_review,
                "blocked_fields": (normalized_item or {}).get("blocked_fields") or [],
                "workflow_paused_reason": (normalized_item or {}).get("workflow_paused_reason"),
            }
        )

    next_cursor = None
    if len(candidates) >= limit:
        last_row = candidates[-1]
        next_cursor = str(getattr(last_row, "created_at", None) or "").strip() or None

    return {
        "status": "completed",
        "organization_id": org_id,
        "mailbox_user_email": target_user["email"],
        "processed": len(results),
        "repaired": repaired,
        "review_required": review_required,
        "errors": errors,
        "next_cursor": next_cursor,
        "results": results,
    }


@router.post("/cleanup-gmail-labels", dependencies=[Depends(get_current_user)])
async def cleanup_gmail_labels(
    request: GmailLabelCleanupRequest,
    user=Depends(require_ops_user),
):
    """Migrate legacy Clearledgr Gmail labels and delete obsolete label objects."""
    org_id = _resolve_org_id_for_user(user, request.organization_id)
    target_user = _resolve_invoice_repair_user(
        user,
        requested_user_email=request.user_email,
        organization_id=org_id,
    )

    gmail_client = GmailAPIClient(target_user["user_id"])
    if not await gmail_client.ensure_authenticated():
        raise HTTPException(status_code=409, detail="gmail_not_connected")

    from clearledgr.services.gmail_labels import cleanup_legacy_labels

    result = await cleanup_legacy_labels(
        gmail_client,
        user_email=target_user["email"],
        dry_run=bool(request.dry_run),
        max_messages_per_label=int(request.max_messages_per_label),
    )
    result["organization_id"] = org_id
    result["mailbox_user_email"] = target_user["email"]
    return result


@router.get("/by-thread/{thread_id}")
async def get_ap_item_by_thread(
    thread_id: str,
    organization_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """Read-only lookup of an AP item by Gmail thread_id for contextual sidebar."""
    org_id = _resolve_org_id_for_user(user, organization_id)
    db = get_db()
    item = db.get_ap_item_by_thread(org_id, thread_id)
    if not item:
        return {"found": False, "thread_id": thread_id, "item": None}
    return {"found": True, "thread_id": thread_id, "item": build_worklist_item(db, item)}


@router.post("/by-thread/{thread_id}/recover")
async def recover_ap_item_by_thread(
    thread_id: str,
    organization_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """Explicitly repair a missing AP item for a Gmail thread when lookup misses."""
    org_id = _resolve_org_id_for_user(user, organization_id)
    db = get_db()
    item = db.get_ap_item_by_thread(org_id, thread_id)
    recovered = False
    if not item:
        item = await _recover_ap_item_for_thread(
            db,
            organization_id=org_id,
            thread_id=thread_id,
            user=user,
        )
        recovered = bool(item)
    if not item:
        return {"found": False, "recovered": False, "thread_id": thread_id, "item": None}
    return {
        "found": True,
        "recovered": recovered,
        "thread_id": thread_id,
        "item": build_worklist_item(db, item),
    }


@router.post("/gmail/register-token")
async def register_gmail_token(request: RegisterGmailTokenRequest):
    """Register Gmail OAuth access token obtained by the browser extension.

    This endpoint is intentionally callable without API auth because it is the
    bootstrap path used immediately after extension OAuth.

    Security contract:
    - Caller-provided organization_id is advisory only.
    - Backend org/role are resolved from the provisioned user identity.
    - Cross-org bootstrap attempts are denied.
    """
    access_token = str(request.access_token or "").strip()
    if not access_token:
        raise HTTPException(status_code=400, detail="missing_google_access_token")

    profile_email: Optional[str] = None
    validation_error: Optional[str] = None

    async with httpx.AsyncClient(timeout=15.0) as client:
        headers = {"Authorization": f"Bearer {access_token}"}
        profile_response = await client.get(GMAIL_PROFILE_URL, headers=headers)
        if profile_response.status_code < 400:
            profile = profile_response.json()
            profile_email = str(profile.get("emailAddress") or "").strip() or None
        else:
            userinfo_response = await client.get(GOOGLE_USERINFO_URL, headers=headers)
            if userinfo_response.status_code < 400:
                payload = userinfo_response.json()
                profile_email = str(payload.get("email") or "").strip() or None
            else:
                validation_error = (
                    f"profile_status={profile_response.status_code},"
                    f"userinfo_status={userinfo_response.status_code}"
                )

    if not profile_email:
        detail = "invalid_google_access_token"
        if validation_error:
            detail = f"{detail}:{validation_error}"
        raise HTTPException(status_code=400, detail=detail)

    hinted_email = str(request.email or "").strip().lower()
    if hinted_email and hinted_email != profile_email.lower():
        logger.warning(
            "Gmail extension email mismatch: hinted=%s profile=%s",
            hinted_email,
            profile_email,
        )

    user = get_user_by_email(profile_email.lower())
    if user is None:
        # Auto-provision: create user from Google identity on first extension login
        from clearledgr.core.auth import create_user_from_google
        org_id = str(request.organization_id or "default").strip() or "default"
        user = create_user_from_google(
            email=profile_email.lower(),
            google_id=profile_email.lower(),
            organization_id=org_id,
        )
        logger.info("Auto-provisioned extension user: %s org=%s", profile_email, org_id)

    resolved_org_id = str(getattr(user, "organization_id", None) or "default").strip() or "default"
    requested_org = str(request.organization_id or "").strip()
    if requested_org and requested_org != resolved_org_id:
        raise HTTPException(status_code=403, detail="org_mismatch")

    expires_in = int(request.expires_in or 3600)
    expires_in = max(60, min(expires_in, 86400))
    user_id = str(getattr(user, "id", "") or "").strip() or profile_email
    existing_token = token_store.get(user_id)
    preserved_refresh_token = existing_token.refresh_token if existing_token else ""
    token_store.store(
        GmailToken(
            user_id=user_id,
            access_token=access_token,
            refresh_token=preserved_refresh_token,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
            email=profile_email,
        )
    )

    db = get_db()
    db.save_gmail_autopilot_state(
        user_id=user_id,
        email=profile_email,
        last_error=None,
    )

    backend_token_ttl_seconds = EXTENSION_BACKEND_TOKEN_TTL_SECONDS
    backend_access_token = create_access_token(
        user_id=user_id,
        email=str(getattr(user, "email", profile_email) or profile_email),
        organization_id=resolved_org_id,
        role=str(getattr(user, "role", None) or "user"),
        expires_delta=timedelta(seconds=backend_token_ttl_seconds),
    )

    return {
        "success": True,
        "email": profile_email,
        "user_id": user_id,
        "expires_in": expires_in,
        "source": "extension_access_token",
        "organization_id": resolved_org_id,
        "backend_access_token": backend_access_token,
        "backend_token_type": "bearer",
        "backend_expires_in": backend_token_ttl_seconds,
    }


class ExchangeCodeRequest(BaseModel):
    code: str
    redirect_uri: str
    organization_id: Optional[str] = "default"


@router.post("/gmail/exchange-code")
async def exchange_gmail_code(request: ExchangeCodeRequest):
    """Exchange an OAuth authorization code for access + refresh tokens.

    The extension uses authorization code flow (response_type=code, access_type=offline)
    so the backend gets a refresh token for 24/7 server-side scanning.
    The extension only needs the access token; the backend stores the refresh token.
    """
    from clearledgr.services.gmail_api import exchange_code_for_tokens
    from clearledgr.core.auth import create_user_from_google

    code = str(request.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="missing_authorization_code")

    # Exchange code for tokens via Google OAuth — returns GmailToken with refresh token
    try:
        gmail_token = await exchange_code_for_tokens(
            code=code,
            redirect_uri=str(request.redirect_uri or "").strip(),
        )
    except Exception as exc:
        logger.warning("Gmail code exchange failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"code_exchange_failed: {exc}")

    access_token = gmail_token.access_token
    refresh_token = gmail_token.refresh_token or ""
    profile_email = gmail_token.email
    expires_in = max(60, int((gmail_token.expires_at - datetime.now(timezone.utc)).total_seconds())) if gmail_token.expires_at else 3600

    if not access_token:
        raise HTTPException(status_code=400, detail="no_access_token_from_google")
    if not profile_email:
        raise HTTPException(status_code=400, detail="could_not_determine_email")

    # Provision user if needed
    user = get_user_by_email(profile_email.lower())
    if user is None:
        org_id = str(request.organization_id or "default").strip() or "default"
        user = create_user_from_google(
            email=profile_email.lower(),
            google_id=profile_email.lower(),
            organization_id=org_id,
        )
        logger.info("Auto-provisioned user via code exchange: %s", profile_email)

    user_id = str(getattr(user, "id", "") or "").strip() or profile_email
    resolved_org_id = str(getattr(user, "organization_id", None) or "default").strip()
    existing_token = token_store.get(user_id)
    if not refresh_token and existing_token and existing_token.refresh_token:
        refresh_token = existing_token.refresh_token

    # Store token WITH refresh token — this is what enables 24/7 server-side scanning
    token_store.store(
        GmailToken(
            user_id=user_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
            email=profile_email,
        )
    )
    logger.info(
        "Gmail code exchange: email=%s refresh=%s org=%s",
        profile_email, "yes" if refresh_token else "NO", resolved_org_id,
    )

    # Save autopilot state so server-side scanning picks up this user
    db = get_db()
    db.save_gmail_autopilot_state(
        user_id=user_id,
        email=profile_email,
        last_error=None,
    )

    # Create backend session token for the extension
    backend_token_ttl = EXTENSION_BACKEND_TOKEN_TTL_SECONDS
    backend_access_token = create_access_token(
        user_id=user_id,
        email=str(getattr(user, "email", profile_email) or profile_email),
        organization_id=resolved_org_id,
        role=str(getattr(user, "role", None) or "user"),
        expires_delta=timedelta(seconds=backend_token_ttl),
    )

    return {
        "success": True,
        "access_token": access_token,
        "expires_in": expires_in,
        "email": profile_email,
        "user_id": user_id,
        "organization_id": resolved_org_id,
        "has_refresh_token": bool(refresh_token),
        "backend_access_token": backend_access_token,
        "backend_token_type": "bearer",
        "backend_expires_in": backend_token_ttl,
    }


@router.post("/approve-and-post", dependencies=[Depends(get_current_user)])
async def approve_and_post(
    request: ApproveAndPostRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user=Depends(require_ops_user),
):
    """
    Approve and post an invoice to ERP — inline from Gmail extension.

    Canonical runtime fallback for older route/page surfaces.
    The canonical AP-first path is ``post_to_erp`` through the finance runtime,
    which preserves legal state transitions and policy guards.
    """
    org_id = _resolve_org_id_for_user(user, request.organization_id)
    db = get_db()
    ap_item = _resolve_ap_item_for_extension_action(db, org_id, request.ap_item_id or request.email_id)
    ap_item_id = str((ap_item or {}).get("id") or request.ap_item_id or "").strip() or None
    gmail_ref = str((ap_item or {}).get("thread_id") or request.email_id or "").strip()
    runtime = _build_finance_runtime(user, org_id, db=db)
    result = await runtime.execute_intent(
        "post_to_erp",
        {
            "ap_item_id": ap_item_id,
            "email_id": gmail_ref,
            "override": bool(request.override),
            "override_justification": (
                request.extraction.get("override_justification", "") if isinstance(request.extraction, dict) else ""
            ) or None,
            "field_confidences": extract_field_confidences(request.extraction or {}),
            "source_channel": "gmail_extension",
            "source_channel_id": "gmail_extension",
            "source_message_ref": gmail_ref,
        },
    )

    return {
        "email_id": request.email_id,
        **result,
    }


@router.post("/verify-confidence")
async def verify_confidence(
    request: VerifyConfidenceRequest,
    _user=Depends(get_current_user),
):
    """
    Verify extraction confidence and surface mismatches for HITL review.

    Returns:
    - confidence_pct: 0-100
    - can_post: True if >= 95%
    - mismatches: list of {field, extracted, expected, severity}
    """
    from clearledgr.core.database import get_db

    db = get_db()
    org_id = _resolve_org_id_for_user(_user, request.organization_id)

    # Look up the AP item to get its stored confidence
    ap_item = db.get_ap_item_by_thread(org_id, request.email_id)
    if not ap_item:
        # Try by message_id
        ap_item = db.get_ap_item_by_message_id(org_id, request.email_id)

    metadata = db._decode_json(ap_item.get("metadata")) if ap_item else {}
    return build_verify_confidence_payload(
        email_id=request.email_id,
        ap_item=ap_item,
        extraction=request.extraction or {},
        metadata=metadata,
    )


@router.post("/match-bank")
async def match_bank_feed(
    request: MatchBankRequest,
    _user=Depends(get_current_user),
):
    """
    Match extracted data against bank feed.
    
    Returns bank transaction match if found.
    """
    org_id = _resolve_org_id_for_user(_user, request.organization_id)
    from clearledgr.workflows.gmail_activities import match_bank_feed_activity
    
    return await match_bank_feed_activity({
        "extraction": request.extraction,
        "organization_id": org_id,
    })


@router.post("/match-erp")
async def match_erp(
    request: MatchERPRequest,
    _user=Depends(get_current_user),
):
    """
    Match extracted data against ERP records (PO, vendor).
    
    Returns PO match, vendor match, and GL code suggestion.
    """
    org_id = _resolve_org_id_for_user(_user, request.organization_id)
    from clearledgr.workflows.gmail_activities import match_erp_activity
    
    return await match_erp_activity({
        "extraction": request.extraction,
        "organization_id": org_id,
    })


@router.post("/escalate", dependencies=[Depends(get_current_user)])
async def escalate_to_manager(
    request: EscalateRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user=Depends(require_ops_user),
):
    """Runtime-owned escalation action for invoice review exceptions."""
    org_id = _resolve_org_id_for_user(user, request.organization_id)
    runtime = _build_finance_runtime(user, org_id)
    result = await runtime.escalate_invoice_review(
        email_id=request.email_id,
        vendor=request.vendor,
        amount=request.amount,
        currency=request.currency,
        confidence=request.confidence,
        mismatches=request.mismatches,
        message=request.message,
        channel=request.channel,
    )

    return result


class SubmitForApprovalRequest(BaseModel):
    """Request to submit invoice for Slack approval with intelligence."""
    email_id: str
    subject: str
    sender: str
    vendor: str
    amount: float
    currency: str = "USD"
    invoice_number: Optional[str] = None
    due_date: Optional[str] = None
    po_number: Optional[str] = None
    confidence: float = 0.0
    field_confidences: Optional[Dict[str, Any]] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    slack_channel: Optional[str] = None
    email_body: Optional[str] = None  # For discount detection
    # Intelligence data (from triage)
    vendor_intelligence: Optional[Dict[str, Any]] = None
    policy_compliance: Optional[Dict[str, Any]] = None
    priority: Optional[Dict[str, Any]] = None
    budget_impact: Optional[List[Dict[str, Any]]] = None
    potential_duplicates: int = 0
    insights: Optional[List[Dict[str, Any]]] = None
    # Agent reasoning + decision payload
    agent_decision: Optional[Dict[str, Any]] = None
    agent_confidence: Optional[float] = None
    reasoning_summary: Optional[str] = None
    reasoning_factors: Optional[List[Dict[str, Any]]] = None
    reasoning_risks: Optional[List[str]] = None
    idempotency_key: Optional[str] = None


class RejectInvoiceRequest(BaseModel):
    """Request to reject an invoice from Gmail sidebar."""
    email_id: str
    ap_item_id: Optional[str] = None
    reason: str
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class BudgetDecisionRequest(BaseModel):
    """Budget decision from Gmail/embedded approval surfaces."""
    email_id: str
    ap_item_id: Optional[str] = None
    decision: str  # approve_override | request_budget_adjustment | reject
    justification: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class ApprovalNudgeRequest(BaseModel):
    """Request to nudge pending approvers for an invoice."""
    email_id: str
    ap_item_id: Optional[str] = None
    message: Optional[str] = None
    idempotency_key: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class VendorFollowupRequest(BaseModel):
    """Request to prepare/refresh a vendor follow-up draft for needs_info items."""
    email_id: str
    reason: Optional[str] = None
    force: bool = False
    idempotency_key: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class FinanceSummaryShareRequest(BaseModel):
    """Request to prepare/share a finance-lead exception summary."""
    email_id: str
    ap_item_id: Optional[str] = None
    target: str = "email_draft"  # email_draft | slack_thread | teams_reply
    preview_only: bool = False
    recipient_email: Optional[str] = None
    note: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class RouteLowRiskApprovalRequest(BaseModel):
    """Batch route low-risk validated item into approval surfaces."""
    email_id: str
    ap_item_id: Optional[str] = None
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class RetryRecoverableFailureRequest(BaseModel):
    """Batch retry for recoverable failed_post AP items."""
    email_id: str
    ap_item_id: Optional[str] = None
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


def _parse_json_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_iso_utc(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _vendor_followup_sla_hours() -> int:
    try:
        hours = int(os.getenv("CLEARLEDGR_VENDOR_FOLLOWUP_SLA_HOURS", "24"))
    except (TypeError, ValueError):
        hours = 24
    return max(1, min(hours, 168))


def _vendor_followup_max_attempts() -> int:
    try:
        attempts = int(os.getenv("CLEARLEDGR_VENDOR_FOLLOWUP_MAX_ATTEMPTS", "3"))
    except (TypeError, ValueError):
        attempts = 3
    return max(1, min(attempts, 10))


def _merge_ap_item_metadata(db: Any, ap_item: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _parse_json_dict(ap_item.get("metadata"))
    metadata.update(updates or {})
    if hasattr(db, "update_ap_item"):
        db.update_ap_item(str(ap_item.get("id")), metadata=metadata)
    ap_item["metadata"] = metadata
    return metadata


def _resolve_ap_item_for_extension_action(db: Any, organization_id: str, reference_id: str) -> Optional[Dict[str, Any]]:
    return resolve_ap_item_reference(db, organization_id, reference_id)


def _load_idempotent_extension_response(db: Any, idempotency_key: Optional[str]) -> Optional[Dict[str, Any]]:
    key = str(idempotency_key or "").strip()
    if not key:
        return None
    existing = db.get_ap_audit_event_by_key(key)
    if not existing:
        return None
    payload = existing.get("payload_json") if isinstance(existing, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    response = payload.get("response")
    if isinstance(response, dict):
        replay = dict(response)
        replay.setdefault("audit_event_id", existing.get("id"))
        replay["idempotency_replayed"] = True
        return replay
    return {
        "status": "idempotent_replay",
        "audit_event_id": existing.get("id"),
        "idempotency_replayed": True,
    }


def _build_finance_lead_summary_payload(
    ap_item: Dict[str, Any],
    *,
    audit_events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    state = str(ap_item.get("state") or "received").strip().lower()
    next_action = str(ap_item.get("next_action") or "").strip().replace("_", " ")
    vendor = str(ap_item.get("vendor_name") or ap_item.get("vendor") or "Unknown vendor").strip()
    invoice_number = str(ap_item.get("invoice_number") or "N/A").strip()
    amount = ap_item.get("amount")
    currency = str(ap_item.get("currency") or "USD").strip().upper()
    due_date = str(ap_item.get("due_date") or "").strip()
    exception_code = str(ap_item.get("exception_code") or "").strip()
    exception_severity = str(ap_item.get("exception_severity") or "").strip()
    requires_field_review = bool(ap_item.get("requires_field_review"))
    confidence_blockers = ap_item.get("confidence_blockers") if isinstance(ap_item.get("confidence_blockers"), list) else []
    context_summary = ""
    metadata = _parse_json_dict(ap_item.get("metadata"))
    if isinstance(metadata.get("context_summary"), str):
        context_summary = metadata.get("context_summary", "").strip()

    amount_text = f"{currency} {float(amount):,.2f}" if isinstance(amount, (int, float)) else f"{currency} amount unavailable"
    lines: List[str] = [
        f"{vendor} · Invoice {invoice_number} · {amount_text}",
        f"Current state: {state.replace('_', ' ')}" + (f" · Next action: {next_action}" if next_action else ""),
    ]

    if exception_code:
        ex_line = f"Exception: {exception_code.replace('_', ' ')}"
        if exception_severity:
            ex_line += f" ({exception_severity})"
        lines.append(ex_line)
    if due_date:
        lines.append(f"Due date: {due_date}")
    if requires_field_review:
        fields = []
        for entry in confidence_blockers[:4]:
            if isinstance(entry, str):
                fields.append(entry)
            elif isinstance(entry, dict):
                fields.append(str(entry.get('field') or entry.get('code') or '').strip())
        fields = [f for f in fields if f]
        lines.append(
            f"Field review blockers: {', '.join(fields)}" if fields else "Field review blockers require review before posting."
        )
    if bool(ap_item.get("budget_requires_decision")):
        budget_status = str(ap_item.get("budget_status") or "review").replace("_", " ")
        lines.append(f"Budget decision required ({budget_status}).")
    if context_summary:
        lines.append(f"Context: {context_summary[:180]}")

    recent = []
    for event in (audit_events or [])[:4]:
        event_type = str(event.get("event_type") or event.get("eventType") or "").strip()
        if event_type:
            recent.append(event_type.replace("_", " "))
    if recent:
        lines.append(f"Recent activity: {' -> '.join(recent)}")

    # de-duplicate while preserving order
    deduped: List[str] = []
    seen: set[str] = set()
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)

    return {
        "title": "Finance lead exception summary",
        "lines": deduped[:8],
        "state": state,
        "next_action": str(ap_item.get("next_action") or ""),
    }


@router.post("/submit-for-approval", dependencies=[Depends(get_current_user)])
async def submit_for_approval(
    request: SubmitForApprovalRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user=Depends(require_ops_user),
):
    """
    Submit an invoice through the runtime-owned AP processing contract.
    
    Behavior:
    - If confidence >= 95%, auto-approves and posts to ERP
    - If confidence < 95%, sends to Slack for manager approval
    - Shows vendor intelligence, policy requirements, budget impact in Slack
    
    Use this when an invoice is detected and ready for processing.
    """
    from clearledgr.services.invoice_models import InvoiceData

    org_id = _resolve_org_id_for_user(user, request.organization_id)
    actor_email = _authenticated_actor(user)
    db = get_db()
    replay = _load_idempotent_extension_response(db, request.idempotency_key)
    if replay:
        return replay
    runtime = _build_finance_runtime(user, org_id, db=db)
    
    # If intelligence not provided, generate it now
    vendor_intel = request.vendor_intelligence
    policy_result = request.policy_compliance
    priority_data = request.priority
    budget_checks = request.budget_impact
    
    if not vendor_intel:
        vi = get_vendor_intelligence()
        vendor_intel = vi.get_suggestion(request.vendor)
    
    if not policy_result:
        ps = get_policy_compliance(org_id)
        policy_check = ps.check({
            "vendor": request.vendor,
            "amount": request.amount,
            "vendor_intelligence": vendor_intel or {},
        })
        policy_result = policy_check.to_dict()
    
    if not priority_data:
        pd = get_priority_detection(org_id)
        priority = pd.assess({
            "id": request.email_id,
            "vendor": request.vendor,
            "amount": request.amount,
            "due_date": request.due_date,
        })
        priority_data = priority.to_dict()
    
    if not budget_checks:
        bs = get_budget_awareness(org_id)
        checks = bs.check_invoice({
            "vendor": request.vendor,
            "amount": request.amount,
            "vendor_intelligence": vendor_intel or {},
        })
        budget_checks = [c.to_dict() for c in checks] if checks else None
    
    agent_decision = request.agent_decision or {}
    agent_confidence = request.agent_confidence
    if agent_confidence is None:
        agent_confidence = agent_decision.get("confidence")

    reasoning_block = agent_decision.get("reasoning") or {}
    reasoning_summary = request.reasoning_summary or reasoning_block.get("summary")
    reasoning_factors = request.reasoning_factors or reasoning_block.get("factors")
    reasoning_risks = request.reasoning_risks or reasoning_block.get("risks")

    invoice = InvoiceData(
        gmail_id=request.email_id,
        subject=request.subject,
        sender=request.sender,
        vendor_name=request.vendor,
        amount=request.amount,
        currency=request.currency,
        invoice_number=request.invoice_number,
        due_date=request.due_date,
        po_number=request.po_number,
        confidence=request.confidence,
        field_confidences=request.field_confidences,
        organization_id=org_id,
        user_id=getattr(user, "user_id", None) or actor_email,
        invoice_text=request.email_body or f"{request.subject}\n{request.vendor}",  # For discount detection
        # Pass intelligence to workflow
        vendor_intelligence=vendor_intel,
        policy_compliance=policy_result,
        priority=priority_data,
        budget_impact=budget_checks,
        potential_duplicates=request.potential_duplicates,
        insights=request.insights,
        reasoning_summary=reasoning_summary,
        reasoning_factors=reasoning_factors,
        reasoning_risks=reasoning_risks,
    )

    # Respect agent decision when present
    decision = agent_decision.get("decision")
    if agent_confidence is not None:
        try:
            invoice.confidence = max(float(invoice.confidence), float(agent_confidence))
        except Exception:
            pass

    approval_threshold = runtime.ap_auto_approve_threshold()
    if decision and decision != "auto_approve":
        # Force human review path (even if confidence is high)
        invoice.confidence = min(invoice.confidence, max(0.0, approval_threshold - 0.01))
    elif decision == "auto_approve":
        # Ensure auto-approve threshold is met
        invoice.confidence = max(invoice.confidence, approval_threshold)

    result = await runtime.execute_ap_invoice_processing(
        invoice_payload=invoice.__dict__,
        idempotency_key=request.idempotency_key,
    )
    
    response_payload = {
        **(result if isinstance(result, dict) else {"status": "unknown"}),
        "email_id": request.email_id,
        "ap_item_id": str((result or {}).get("ap_item_id") or request.email_id),
    }
    return response_payload


@router.post("/reject-invoice", dependencies=[Depends(get_current_user)])
async def reject_invoice(
    request: RejectInvoiceRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user=Depends(require_ops_user),
):
    """Reject an invoice and keep pipeline state in sync."""
    org_id = _resolve_org_id_for_user(user, request.organization_id)
    rejected_by = _authenticated_actor(user)
    db = get_db()
    ap_item = _resolve_ap_item_for_extension_action(db, org_id, request.ap_item_id or request.email_id)
    ap_item_id = str((ap_item or {}).get("id") or request.ap_item_id or "").strip() or None
    gmail_ref = str((ap_item or {}).get("thread_id") or request.email_id or "").strip()
    runtime = _build_finance_runtime(user, org_id, db=db)
    result = await runtime.execute_intent(
        "reject_invoice",
        {
            "ap_item_id": ap_item_id,
            "email_id": gmail_ref,
            "reason": request.reason,
            "source_channel": "gmail_extension",
            "source_channel_id": "gmail_extension",
            "source_message_ref": gmail_ref,
            "actor_id": rejected_by,
            "actor_display": rejected_by,
        },
    )

    if result.get("status") != "rejected":
        raise HTTPException(status_code=400, detail=result.get("reason", "Reject failed"))
    return result


@router.post("/budget-decision", dependencies=[Depends(get_current_user)])
async def budget_decision(
    request: BudgetDecisionRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user=Depends(require_ops_user),
):
    """Handle explicit budget decisions from Gmail sidebar surfaces."""
    org_id = _resolve_org_id_for_user(user, request.organization_id)
    actor = _authenticated_actor(user)
    decision = str(request.decision or "").strip().lower()
    db = get_db()
    ap_item = _resolve_ap_item_for_extension_action(db, org_id, request.ap_item_id or request.email_id)
    if not ap_item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    ap_item_id = str(ap_item.get("id") or request.ap_item_id or "").strip()
    gmail_ref = str(ap_item.get("thread_id") or request.email_id or "").strip()
    runtime = _build_finance_runtime(user, org_id, db=db)

    if decision == "approve_override":
        if not str(request.justification or "").strip():
            raise HTTPException(status_code=400, detail="justification_required")
        result = await runtime.execute_intent(
            "approve_invoice",
            {
                "ap_item_id": ap_item_id,
                "email_id": gmail_ref,
                "approve_override": True,
                "action_variant": "budget_override",
                "reason": request.justification,
                "override_justification": request.justification,
                "source_channel": "gmail_extension",
                "source_channel_id": "gmail_extension",
                "source_message_ref": gmail_ref,
                "actor_id": actor,
                "actor_display": actor,
            },
        )
        if result.get("status") not in {"approved", "posted", "posted_to_erp", "error"}:
            raise HTTPException(status_code=400, detail=result.get("reason", "budget_override_failed"))
    elif decision == "request_budget_adjustment":
        result = await runtime.execute_intent(
            "request_info",
            {
                "ap_item_id": ap_item_id,
                "email_id": gmail_ref,
                "reason": request.justification or "budget_adjustment_requested_in_gmail",
                "source_channel": "gmail_extension",
                "source_channel_id": "gmail_extension",
                "source_message_ref": gmail_ref,
                "actor_id": actor,
                "actor_display": actor,
            },
        )
    elif decision == "reject":
        reason = request.justification or "rejected_over_budget_in_gmail"
        result = await runtime.execute_intent(
            "reject_invoice",
            {
                "ap_item_id": ap_item_id,
                "email_id": gmail_ref,
                "reason": reason,
                "source_channel": "gmail_extension",
                "source_channel_id": "gmail_extension",
                "source_message_ref": gmail_ref,
                "actor_id": actor,
                "actor_display": actor,
            },
        )
    else:
        raise HTTPException(status_code=400, detail="invalid_budget_decision")

    return result


@router.post("/approval-nudge", dependencies=[Depends(get_current_user)])
async def approval_nudge(
    request: ApprovalNudgeRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user = Depends(require_ops_user),
):
    """Send a dedicated approver nudge for pending approvals (Slack/Teams best effort)."""
    org_id = _resolve_org_id_for_user(user, request.organization_id)
    actor_email = _authenticated_actor(user)
    db = get_db()
    replay = _load_idempotent_extension_response(db, request.idempotency_key)
    if replay:
        return replay
    ap_item = _resolve_ap_item_for_extension_action(db, org_id, request.ap_item_id or request.email_id)
    if not ap_item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    state = str(ap_item.get("state") or "").strip().lower()
    if state not in {"needs_approval", "pending_approval"}:
        raise HTTPException(status_code=400, detail="item_not_waiting_for_approval")

    gmail_id = str(ap_item.get("thread_id") or request.email_id or "").strip()
    if not gmail_id:
        raise HTTPException(status_code=400, detail="missing_gmail_reference")

    runtime = _build_finance_runtime(user, org_id, db=db)
    response = await runtime.execute_intent(
        "nudge_approval",
        {
            "ap_item_id": str(ap_item.get("id") or request.ap_item_id or "").strip() or None,
            "email_id": gmail_id,
            "message": str(request.message or "").strip() or None,
            "source_channel": "gmail_extension",
            "source_channel_id": "gmail_extension",
            "source_message_ref": gmail_id,
            "actor_id": actor_email,
            "actor_display": actor_email,
        },
        idempotency_key=request.idempotency_key,
    )

    return response


@router.post("/vendor-followup", dependencies=[Depends(get_current_user)])
async def vendor_followup(
    request: VendorFollowupRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user=Depends(require_ops_user),
):
    """Prepare a vendor follow-up draft through the canonical finance runtime."""
    from clearledgr.services.finance_agent_runtime import IntentNotSupportedError

    org_id = _resolve_org_id_for_user(user, request.organization_id)
    runtime = _build_finance_runtime(user, org_id)
    try:
        response = await runtime.execute_intent(
            "prepare_vendor_followups",
            {
                "email_id": request.email_id,
                "reason": request.reason,
                "force": request.force,
            },
            idempotency_key=request.idempotency_key,
        )
    except IntentNotSupportedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return response


@router.post("/route-low-risk-approval", dependencies=[Depends(get_current_user)])
async def route_low_risk_approval(
    request: RouteLowRiskApprovalRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user=Depends(require_ops_user),
):
    """Route a validated low-risk item into approval surfaces with policy prechecks."""
    from clearledgr.services.finance_agent_runtime import IntentNotSupportedError

    org_id = _resolve_org_id_for_user(user, request.organization_id)
    db = get_db()
    runtime = _build_finance_runtime(user, org_id, db=db)
    try:
        response = await runtime.execute_intent(
            "route_low_risk_for_approval",
            {
                "ap_item_id": request.ap_item_id,
                "email_id": request.email_id,
                "reason": request.reason,
            },
            idempotency_key=request.idempotency_key,
        )
    except IntentNotSupportedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return response


@router.post("/retry-recoverable-failure", dependencies=[Depends(get_current_user)])
async def retry_recoverable_failure(
    request: RetryRecoverableFailureRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user=Depends(require_ops_user),
):
    """Retry a recoverable failed-post item through the canonical finance runtime."""
    from clearledgr.services.finance_agent_runtime import IntentNotSupportedError

    org_id = _resolve_org_id_for_user(user, request.organization_id)
    runtime = _build_finance_runtime(user, org_id)
    try:
        response = await runtime.execute_intent(
            "retry_recoverable_failures",
            {
                "ap_item_id": request.ap_item_id,
                "email_id": request.email_id,
                "reason": request.reason,
            },
            idempotency_key=request.idempotency_key,
        )
    except IntentNotSupportedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return response


@router.post("/finance-summary-share", dependencies=[Depends(get_current_user)])
async def finance_summary_share(
    request: FinanceSummaryShareRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user = Depends(require_ops_user),
):
    """Prepare or deliver a finance-lead exception summary share action."""
    org_id = _resolve_org_id_for_user(user, request.organization_id)
    db = get_db()
    runtime = _build_finance_runtime(user, org_id, db=db)
    try:
        result = await runtime.share_finance_summary(
            reference_id=request.ap_item_id or request.email_id,
            target=request.target,
            preview_only=bool(request.preview_only),
            recipient_email=request.recipient_email,
            note=request.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return result


@router.get("/invoice-status/{gmail_id}")
async def get_invoice_status(
    gmail_id: str,
    user=Depends(get_current_user),
):
    """
    Get the current status of an invoice.
    
    Returns: new, pending_approval, approved, posted, rejected
    """
    from clearledgr.core.database import get_db
    
    db = get_db()
    status = db.get_invoice_status(gmail_id)
    
    if not status:
        raise HTTPException(status_code=404, detail="Invoice not found")
    _assert_user_org_access(user, str(status.get("organization_id") or "default"))
    return status


@router.get("/invoice-pipeline/{organization_id}")
async def get_invoice_pipeline_status(
    organization_id: str,
    user=Depends(get_current_user),
):
    """
    Get all invoices grouped by status (pipeline view).
    
    Returns invoices grouped into: new, pending_approval, approved, posted, rejected
    """
    from clearledgr.core.database import get_db
    
    _assert_user_org_access(user, organization_id)
    db = get_db()
    pipeline = _build_extension_pipeline(db, organization_id)
    
    return {
        "organization_id": organization_id,
        "pipeline": pipeline,
        "counts": {status: len(invoices) for status, invoices in pipeline.items()},
    }


@router.get("/workflow/{workflow_id}")
async def get_workflow_status(
    workflow_id: str,
    user=Depends(get_current_user),
):
    """
    Get the status of a running workflow.
    
    Use this to poll for completion of async workflows.
    """
    runtime = TemporalRuntime()
    try:
        payload = await runtime.get_status(workflow_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow_not_found")
    _assert_user_org_access(user, str(payload.get("organization_id") or "default"))
    return payload


@router.get("/ap/{ap_item_id}/explain")
def explain_ap_item(
    ap_item_id: str,
    organization_id: Optional[str] = Query(default=None),
    user=Depends(get_current_user),
):
    """Natural-language explanation of why an AP item is in its current state.

    Claude reads the audit trail, vendor history, and current item state and
    answers as the AP agent: "Here's what happened and why."

    Works without ANTHROPIC_API_KEY — falls back to a structured plain-text
    summary derived from audit events and the item's metadata.
    """
    db = get_db()
    org_id = _resolve_org_id_for_user(user, organization_id)

    item = db.get_ap_item(ap_item_id) if hasattr(db, "get_ap_item") else None
    if not item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    _assert_user_org_access(user, str(item.get("organization_id") or "default"))
    if item.get("organization_id") and org_id and item["organization_id"] != org_id:
        raise HTTPException(status_code=403, detail="org_mismatch")

    vendor = str(item.get("vendor_name") or "Unknown vendor")
    amount = item.get("amount")
    state = str(item.get("state") or "unknown")
    exception_code = item.get("exception_code")
    confidence = item.get("confidence")
    subject = item.get("subject") or ""

    # Audit events (last 10, oldest → newest)
    audit_events = []
    try:
        events = db.list_ap_audit_events(ap_item_id) if hasattr(db, "list_ap_audit_events") else []
        audit_events = events[-10:] if events else []
    except Exception:
        pass

    # Vendor profile + history
    vendor_profile = db.get_vendor_profile(org_id, vendor) if hasattr(db, "get_vendor_profile") else None
    vendor_history = db.get_vendor_invoice_history(org_id, vendor, limit=5) if hasattr(db, "get_vendor_invoice_history") else []

    # Metadata (may contain ap_decision_reasoning from the reasoning layer)
    import json as _json
    meta: dict = {}
    try:
        raw_meta = item.get("metadata") or "{}"
        meta = _json.loads(raw_meta) if isinstance(raw_meta, str) else (raw_meta or {})
    except Exception:
        pass

    prior_reasoning = str(meta.get("ap_decision_reasoning") or "").strip()
    needs_info_q = str(meta.get("needs_info_question") or "").strip()

    explanation = render_ap_item_explanation(
        vendor=vendor,
        amount=amount,
        state=state,
        exception_code=exception_code,
        confidence=confidence,
        subject=subject,
        audit_events=audit_events,
        vendor_profile=vendor_profile,
        vendor_history=vendor_history,
        prior_reasoning=prior_reasoning,
        needs_info_question=needs_info_q,
    )

    return {
        "ap_item_id": ap_item_id,
        "vendor": vendor,
        "state": state,
        "explanation": explanation["text"],
        "suggested_action": explanation.get("suggested_action"),
        "vendor_context_summary": explanation.get("vendor_context"),
        "audit_events_used": len(audit_events),
        "method": explanation.get("method", "llm"),
    }


def _explain_with_claude(
    *,
    api_key: str,
    vendor: str,
    amount: Any,
    state: str,
    exception_code: Optional[str],
    confidence: Any,
    subject: str,
    audit_events: list,
    vendor_profile: Optional[dict],
    vendor_history: list,
    prior_reasoning: str,
    needs_info_question: str,
) -> dict:
    """Ask Claude to explain an AP item's current state in plain English."""
    return support_explain_with_claude(
        api_key=api_key,
        vendor=vendor,
        amount=amount,
        state=state,
        exception_code=exception_code,
        confidence=confidence,
        subject=subject,
        audit_events=audit_events,
        vendor_profile=vendor_profile,
        vendor_history=vendor_history,
        prior_reasoning=prior_reasoning,
        needs_info_question=needs_info_question,
    )


def _explain_fallback(
    *,
    vendor: str,
    amount: Any,
    state: str,
    exception_code: Optional[str],
    confidence: Any,
    audit_events: list,
    prior_reasoning: str,
    needs_info_question: str,
) -> dict:
    """Plain-text fallback explanation built from structured fields (no LLM)."""
    return support_explain_fallback(
        vendor=vendor,
        amount=amount,
        state=state,
        exception_code=exception_code,
        confidence=confidence,
        audit_events=audit_events,
        prior_reasoning=prior_reasoning,
        needs_info_question=needs_info_question,
    )


@router.get("/health")
def extension_health():
    """Health check for extension API."""
    return {
        "status": "ok",
        "temporal_enabled": temporal_enabled(),
        "service": "clearledgr-gmail-extension",
        "differentiators": [
            "audit_link_generation",
            "human_in_the_loop",
            "multi_system_routing",
        ],
    }


# ==================== AI SUGGESTIONS FOR FORMS ====================
# These endpoints expose AI suggestions to pre-fill forms (human confirms)

class GLSuggestionRequest(BaseModel):
    """Request for GL code suggestion."""
    vendor_name: str
    amount: Optional[float] = None
    description: Optional[str] = None
    organization_id: Optional[str] = "default"


class VendorSuggestionRequest(BaseModel):
    """Request for vendor suggestion from email context."""
    sender_email: Optional[str] = None
    sender_name: Optional[str] = None
    subject: Optional[str] = None
    extracted_vendor: Optional[str] = None
    organization_id: Optional[str] = "default"


@router.post("/suggestions/gl-code")
async def suggest_gl_code(
    request: GLSuggestionRequest,
    _user=Depends(get_current_user),
):
    """
    Get AI-suggested GL code for a vendor.
    
    Returns primary suggestion + alternatives with confidence scores.
    Human reviews and confirms/changes.
    """
    org_id = _resolve_org_id_for_user(_user, request.organization_id)
    return build_gl_suggestion_payload(
        organization_id=org_id,
        vendor_name=request.vendor_name,
    )


@router.post("/suggestions/vendor")
async def suggest_vendor(
    request: VendorSuggestionRequest,
    _user=Depends(get_current_user),
):
    """
    Get AI-suggested vendor match from email context.
    
    Returns matched vendor + confidence for human confirmation.
    """
    org_id = _resolve_org_id_for_user(_user, request.organization_id)
    return build_vendor_suggestion_payload(
        organization_id=org_id,
        sender_email=request.sender_email,
        extracted_vendor=request.extracted_vendor,
    )


@router.post("/suggestions/amount-validation")
async def validate_amount(
    vendor_name: str = Body(...),
    amount: float = Body(...),
    organization_id: str = Body("default"),
    _user=Depends(get_current_user),
):
    """
    Validate invoice amount against vendor history.
    
    Returns whether amount seems reasonable + expected range.
    """
    _resolve_org_id_for_user(_user, organization_id)
    return build_amount_validation_payload(vendor_name, amount)


@router.get("/suggestions/form-prefill/{email_id}")
async def get_form_prefill(
    email_id: str,
    organization_id: str = "default",
    _user=Depends(get_current_user),
):
    """
    Get all AI suggestions to pre-fill a form for an invoice.
    
    Combines vendor match, GL suggestion, and amount validation.
    Returns everything needed to pre-fill invoice forms.
    """
    org_id = _resolve_org_id_for_user(_user, organization_id)
    db = get_db()
    invoice = db.get_invoice_by_email_id(email_id)
    try:
        return build_form_prefill_payload(
            email_id=email_id,
            organization_id=org_id,
            invoice=invoice,
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="org_mismatch")


# ==================== CORRECTION LEARNING ====================

class FieldCorrectionRequest(BaseModel):
    """Payload sent by the Gmail extension when an operator edits an extracted field."""
    ap_item_id: str
    field: str  # e.g. "vendor", "amount", "invoice_number", "due_date"
    original_value: Optional[Any] = None
    corrected_value: Any
    actor_id: Optional[str] = None  # email of the operator; falls back to token identity
    feedback: Optional[str] = None  # optional free-text reason


@router.get("/needs-info-draft/{ap_item_id}")
async def get_needs_info_draft(
    ap_item_id: str,
    reason: Optional[str] = Query(None, description="What information is needed — pre-fills the email body"),
    _user=Depends(get_current_user),
):
    """Generate a pre-filled vendor reply template for a needs_info AP item.
    Returns {to, subject, body} ready for Gmail compose URL construction.
    No auth required — callable from content-script.js without token."""
    db = get_db()
    ap_item = db.get_ap_item(ap_item_id)
    try:
        return build_needs_info_draft_payload(
            ap_item_id=ap_item_id,
            ap_item=ap_item,
            reason=reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/record-field-correction")
async def record_field_correction(
    request: FieldCorrectionRequest,
    user=Depends(require_ops_user),
):
    """Record a field-level correction through the runtime-owned AP contract."""
    actor_id = (
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or "operator"
    )
    runtime = _build_finance_runtime(
        user,
        str(getattr(user, "organization_id", None) or "default"),
    )
    try:
        return runtime.record_field_correction(
            ap_item_id=request.ap_item_id,
            field=request.field,
            original_value=request.original_value,
            corrected_value=request.corrected_value,
            feedback=request.feedback,
            actor_id=actor_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
