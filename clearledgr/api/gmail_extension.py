"""API endpoints for the Clearledgr Gmail Extension.

These endpoints are called by the Chrome extension to trigger
Temporal workflows for reliable email processing.

KEY DIFFERENTIATORS:
1. Audit-Link Generation - Every post generates a Clearledgr_Audit_ID
2. Human-in-the-Loop (HITL) - <95% confidence blocks "Post", shows "Review Mismatch"
3. Multi-System Routing - Approval triggers both ERP post AND Slack thread update
4. Intelligent Agent - Vendor intelligence, policy compliance, priority detection
"""
import json
import os
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from pydantic import BaseModel

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
from clearledgr.core.ap_confidence import evaluate_critical_field_confidence, extract_field_confidences
from clearledgr.core.auth import get_current_user
from clearledgr.core.database import get_db
from clearledgr.api.ap_items import build_worklist_item


router = APIRouter(prefix="/extension", tags=["gmail-extension"])


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


class ApproveAndPostRequest(BaseModel):
    """Request to approve and post an invoice with HITL gate."""
    email_id: str
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


# ==================== ENDPOINTS ====================

@router.post("/triage", dependencies=[Depends(get_current_user)])
async def triage_email(
    request: EmailTriageRequest,
    audit: AuditTrailService = Depends(get_audit_service),
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
    org_id = request.organization_id or "default"
    
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
    
    # Inline execution (no Temporal)
    from clearledgr.workflows.gmail_activities import (
        classify_email_activity,
        extract_email_data_activity,
    )
    
    # Initialize audit trail for this invoice
    trail = get_audit_trail(org_id)
    trail.log(
        invoice_id=request.email_id,
        event_type=AuditEventType.RECEIVED,
        summary=f"Email received from {request.sender or 'unknown'}",
        details={"subject": request.subject, "sender": request.sender},
    )
    
    classification = await classify_email_activity(payload)
    
    trail.log(
        invoice_id=request.email_id,
        event_type=AuditEventType.CLASSIFIED,
        summary=f"Classified as {classification.get('type', 'UNKNOWN')}",
        confidence=classification.get("confidence", 0),
        reasoning=classification.get("reason", "AI classification"),
    )
    
    if classification.get("type") == "NOISE":
        return {
            "email_id": request.email_id,
            "classification": classification,
            "action": "skipped",
        }
    
    extraction = await extract_email_data_activity({**payload, "classification": classification})
    extracted_amount = extraction.get("amount")
    amount_display = (
        f"{float(extracted_amount):,.2f}"
        if isinstance(extracted_amount, (int, float))
        else "Unknown"
    )

    trail.log(
        invoice_id=request.email_id,
        event_type=AuditEventType.EXTRACTED,
        summary=f"Extracted: {extraction.get('vendor', 'Unknown')} ${amount_display}",
        confidence=extraction.get("confidence", 0),
        vendor=extraction.get("vendor"),
        amount=extraction.get("amount"),
    )
    
    # ========== APPLY ALL INTELLIGENCE ==========
    
    # 1. Self-reflection: Agent checks its own work
    reflection = get_agent_reflection()
    original_text = f"{request.subject or ''} {request.snippet or ''} {request.body or ''}"
    reflection_result = reflection.reflect_on_extraction(extraction, original_text)
    
    if reflection_result.corrections_made:
        extraction = reflection_result.final_extraction
        trail.log(
            invoice_id=request.email_id,
            event_type=AuditEventType.VALIDATED,
            summary=f"Self-corrected {len(reflection_result.corrections_made)} field(s)",
            reasoning="; ".join(reflection_result.reflection_notes),
        )
    
    # 2. Vendor Intelligence: Know vendors before told
    vendor_intel = get_vendor_intelligence()
    vendor_info = vendor_intel.get_suggestion(extraction.get("vendor", ""))
    if vendor_info:
        extraction["vendor_intelligence"] = vendor_info
        # Apply suggested GL if not already set
        if not extraction.get("gl_code") and vendor_info.get("suggested_gl"):
            extraction["gl_code"] = vendor_info["suggested_gl"]
            extraction["gl_source"] = "vendor_intelligence"
    
    # 3. Policy Compliance: Check against company policies
    policy_service = get_policy_compliance(org_id)
    invoice_for_policy = {
        "vendor": extraction.get("vendor") or "",
        "amount": extraction.get("amount", 0),
        "category": extraction.get("category") or "",
        "vendor_intelligence": extraction.get("vendor_intelligence", {}),
    }
    policy_result = policy_service.check(invoice_for_policy)
    extraction["policy_compliance"] = policy_result.to_dict()
    
    if not policy_result.compliant:
        trail.log(
            invoice_id=request.email_id,
            event_type=AuditEventType.POLICY_CHECK,
            summary=f"Policy: {len(policy_result.violations)} requirement(s)",
            details={"violations": [v.message for v in policy_result.violations]},
        )
    
    # 4. Priority Detection: Smart urgency scoring
    priority_service = get_priority_detection(org_id)
    invoice_for_priority = {
        "id": request.email_id,
        "vendor": extraction.get("vendor"),
        "amount": extraction.get("amount", 0),
        "due_date": extraction.get("due_date"),
        "created_at": extraction.get("created_at"),
        "vendor_intelligence": extraction.get("vendor_intelligence", {}),
    }
    priority = priority_service.assess(invoice_for_priority)
    extraction["priority"] = priority.to_dict()
    
    # 5. Cross-Invoice Analysis: Duplicates and anomalies
    analyzer = get_cross_invoice_analyzer(org_id)
    analysis = analyzer.analyze(
        vendor=extraction.get("vendor", ""),
        amount=extraction.get("amount", 0),
        invoice_number=extraction.get("invoice_number"),
        invoice_date=extraction.get("invoice_date"),
        gmail_id=request.email_id,
    )
    extraction["cross_invoice_analysis"] = analysis.to_dict()
    duplicate_alerts = getattr(analysis, "duplicates", []) or []
    
    if duplicate_alerts:
        trail.log(
            invoice_id=request.email_id,
            event_type=AuditEventType.DUPLICATE_CHECK,
            summary=f"Potential duplicate detected",
            details={"duplicates": [getattr(d, "invoice_id", None) for d in duplicate_alerts]},
        )
    
    # 6. Budget Awareness: Check budget impact
    budget_service = get_budget_awareness(org_id)
    budget_checks = budget_service.check_invoice(invoice_for_policy)
    if budget_checks:
        extraction["budget_impact"] = [b.to_dict() for b in budget_checks]
        
        # Alert if budget critical
        for check in budget_checks:
            if check.after_approval_status.value in ["critical", "exceeded"]:
                trail.log(
                    invoice_id=request.email_id,
                    event_type=AuditEventType.ANALYZED,
                    summary=f"Budget alert: {check.budget.name} at {check.after_approval_percent:.0f}%",
                )
    
    # 7. Proactive Insights: Check for alerts
    insights_service = get_proactive_insights(org_id)
    insights = insights_service.analyze_after_invoice(invoice_for_priority)
    if insights:
        extraction["insights"] = [
            {"title": i.title, "description": i.description, "severity": i.severity}
            for i in insights
        ]
    
    # Record decision in audit trail
    trail.log(
        invoice_id=request.email_id,
        event_type=AuditEventType.DECISION_MADE,
        summary=f"Ready for processing - Priority: {priority.priority.label}",
        confidence=extraction.get("confidence", 0),
        reasoning=f"Vendor: {'known' if vendor_info else 'new'}, Policy: {'compliant' if policy_result.compliant else 'requirements'}, Duplicates: {len(duplicate_alerts)}",
    )
    
    # Legacy audit
    audit.record_event(
        user_email=request.user_email or "extension",
        action="email_triaged",
        entity_type="email",
        entity_id=request.email_id,
        organization_id=request.organization_id,
        metadata={
            "classification": classification.get("type"),
            "vendor": extraction.get("vendor"),
            "amount": extraction.get("amount"),
            "priority": priority.priority.value,
            "policy_compliant": policy_result.compliant,
            "potential_duplicates": len(duplicate_alerts),
        },
    )
    
    result = {
        "email_id": request.email_id,
        "classification": classification,
        "extraction": extraction,
        "action": "triaged",
        "ai_powered": True,
        "intelligence": {
            "vendor_known": vendor_info is not None,
            "vendor_info": vendor_info,
            "policy_compliant": policy_result.compliant,
            "policy_requirements": [v.message for v in policy_result.violations],
            "required_approvers": policy_result.required_approvers,
            "priority": priority.priority.value,
            "priority_label": priority.priority.label,
            "days_until_due": priority.days_until_due,
            "alerts": priority.alerts,
            "potential_duplicates": len(duplicate_alerts),
            "anomalies": [getattr(a, "anomaly_type", None) for a in (getattr(analysis, "anomalies", []) or [])],
            "budget_warnings": [
                b.warning_message for b in budget_checks if b.warning_message
            ] if budget_checks else [],
            "insights": [i.title for i in insights] if insights else [],
            "self_verified": reflection_result.self_verified,
        },
    }

    # Agent reasoning layer (deep autonomy)
    result = _apply_agent_reasoning(
        result=result,
        org_id=org_id,
        combined_text=combined_text,
        attachments=request.attachments or [],
    )

    return result


async def _apply_intelligence(result: Dict[str, Any], org_id: str, email_id: str) -> Dict[str, Any]:
    """Apply intelligence services to a triage result."""
    extraction = result.get("extraction", {})
    
    # Vendor Intelligence
    vendor_intel = get_vendor_intelligence()
    vendor_info = vendor_intel.get_suggestion(extraction.get("vendor", ""))
    if vendor_info:
        extraction["vendor_intelligence"] = vendor_info
    
    # Policy Compliance
    policy_service = get_policy_compliance(org_id)
    policy_result = policy_service.check({
        "vendor": extraction.get("vendor"),
        "amount": extraction.get("amount", 0),
        "vendor_intelligence": vendor_info or {},
    })
    extraction["policy_compliance"] = policy_result.to_dict()
    
    # Priority Detection
    priority_service = get_priority_detection(org_id)
    priority = priority_service.assess({
        "id": email_id,
        "vendor": extraction.get("vendor"),
        "amount": extraction.get("amount", 0),
        "due_date": extraction.get("due_date"),
    })
    extraction["priority"] = priority.to_dict()
    
    result["extraction"] = extraction
    result["intelligence"] = {
        "vendor_known": vendor_info is not None,
        "policy_compliant": policy_result.compliant,
        "priority": priority.priority.value,
        "priority_label": priority.priority.label,
    }
    
    return result


def _merge_agent_extraction(
    extraction: Dict[str, Any],
    agent_extraction: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill missing extraction fields from agent reasoning output."""
    if not agent_extraction:
        return extraction

    merged = dict(extraction or {})

    def _set_if_missing(key: str, value: Any):
        if value is None or value == "":
            return
        if merged.get(key) in (None, "", 0):
            merged[key] = value

    _set_if_missing("vendor", agent_extraction.get("vendor"))
    _set_if_missing("amount", agent_extraction.get("total_amount"))
    _set_if_missing("currency", agent_extraction.get("currency"))
    _set_if_missing("invoice_number", agent_extraction.get("invoice_number"))
    _set_if_missing("invoice_date", agent_extraction.get("invoice_date"))
    _set_if_missing("due_date", agent_extraction.get("due_date"))

    # Prefer agent line items if none exist
    if not merged.get("line_items") and agent_extraction.get("line_items"):
        merged["line_items"] = agent_extraction.get("line_items")

    return merged


def _apply_agent_reasoning(
    result: Dict[str, Any],
    org_id: str,
    combined_text: str,
    attachments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Run agent reasoning and merge decision + extraction."""
    if not combined_text and not attachments:
        return result

    try:
        agent = get_reasoning_agent(org_id)
        decision = agent.reason_about_invoice(combined_text, attachments)
    except Exception as exc:  # noqa: BLE001
        result.setdefault("agent_decision_error", str(exc))
        return result

    extraction = result.get("extraction") or {}
    extraction = _merge_agent_extraction(extraction, decision.extraction or {})

    # Boost confidence if agent produced one
    try:
        extraction_confidence = float(extraction.get("confidence") or 0.0)
        extraction["confidence"] = max(extraction_confidence, float(decision.confidence))
    except Exception:
        pass

    result["extraction"] = extraction
    result["agent_decision"] = decision.to_dict()
    return result


@router.post("/process", dependencies=[Depends(get_current_user)])
async def process_email(
    request: EmailProcessRequest,
    audit: AuditTrailService = Depends(get_audit_service),
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
):
    """
    Scan multiple emails in bulk.
    
    This triggers the BulkEmailScanWorkflow which processes
    each email through the triage workflow.
    
    Use this for inbox scanning.
    """
    payload = request.model_dump()
    
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
                    user_email=request.user_email,
                ),
                audit=audit,
            )
            results["processed"] += 1
            if triage.get("action") != "skipped":
                results["labeled"] += 1
        except Exception:
            pass
    
    return results


def _pipeline_bucket_for_state(state: Any) -> str:
    normalized = str(state or "").strip().lower()
    if normalized in {"new", "received", "validated"}:
        return "new"
    if normalized in {"needs_info", "needs_approval", "pending_approval"}:
        return "pending_approval"
    if normalized in {"approved", "ready_to_post"}:
        return "approved"
    if normalized in {"posted", "posted_to_erp", "closed"}:
        return "posted"
    if normalized in {"rejected"}:
        return "rejected"
    return "pending_approval"


def _build_extension_pipeline(db, organization_id: str, limit: int = 1000) -> Dict[str, List[Dict[str, Any]]]:
    items = db.list_ap_items(organization_id, limit=limit, prioritized=True)
    groups: Dict[str, List[Dict[str, Any]]] = {
        "new": [],
        "pending_approval": [],
        "approved": [],
        "posted": [],
        "rejected": [],
    }
    for item in items:
        normalized = build_worklist_item(db, item)
        bucket = _pipeline_bucket_for_state(normalized.get("state"))
        groups.setdefault(bucket, []).append(normalized)
    return groups


@router.get("/pipeline")
def get_invoice_pipeline(organization_id: Optional[str] = None):
    """Return invoice pipeline grouped by status for Gmail extension.

    This legacy endpoint is kept for compatibility and now mirrors the
    normalized exception taxonomy used by `/extension/worklist`.
    """
    org_id = organization_id or "default"
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

    _ADMIN_ROLES = {"admin", "owner"}
    if organization_id and organization_id != "default":
        if user.role not in _ADMIN_ROLES and str(user.organization_id) != str(organization_id):
            raise HTTPException(status_code=403, detail="org_mismatch")
        org_id = organization_id
    else:
        org_id = str(user.organization_id or "default")

    db = get_db()
    items = db.list_ap_items(org_id, limit=limit, prioritized=True)
    normalized = [build_worklist_item(db, item) for item in items]
    return {
        "organization_id": org_id,
        "items": normalized,
        "total": len(normalized),
    }


@router.post("/approve-and-post", dependencies=[Depends(get_current_user)])
async def approve_and_post(
    request: ApproveAndPostRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    """
    Approve and post an invoice to ERP — inline from Gmail extension.

    Uses the same ``InvoiceWorkflowService.approve_invoice()`` path as
    Slack approval buttons so behaviour is identical regardless of surface.
    """
    from clearledgr.core.ap_states import OverrideContext, OVERRIDE_TYPE_MULTI
    from clearledgr.services.agent_orchestrator import get_orchestrator

    org_id = request.organization_id or "default"
    orchestrator = get_orchestrator(org_id)

    # Resolve the AP item's gmail_id (thread_id) from request
    gmail_id = request.email_id

    actor = request.user_email or "gmail_extension"
    justification = (request.extraction.get("override_justification", "") if request.override else None)
    override_ctx = (
        OverrideContext(
            override_type=OVERRIDE_TYPE_MULTI,
            justification=str(justification or "override_requested_in_gmail"),
            actor_id=actor,
        )
        if request.override else None
    )

    result = await orchestrator.on_approval(
        gmail_id=gmail_id,
        approved_by=actor,
        source_channel="gmail_extension",
        allow_budget_override=request.override,
        allow_confidence_override=request.override,
        override_justification=justification,
        field_confidences=extract_field_confidences(request.extraction or {}),
        override_context=override_ctx,
    )

    return {
        "email_id": request.email_id,
        **result,
    }


@router.post("/verify-confidence")
async def verify_confidence(
    request: VerifyConfidenceRequest,
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
    org_id = request.organization_id or "default"

    # Look up the AP item to get its stored confidence
    ap_item = db.get_ap_item_by_thread(org_id, request.email_id)
    if not ap_item:
        # Try by message_id
        ap_item = db.get_ap_item_by_message_id(org_id, request.email_id)

    confidence_pct = 0
    mismatches = []
    confidence_gate: Dict[str, Any] = {
        "threshold": 0.95,
        "threshold_pct": 95,
        "confidence_blockers": [],
        "requires_field_review": True,
    }

    if ap_item:
        confidence_pct = round((ap_item.get("confidence") or 0) * 100)
        metadata = db._decode_json(ap_item.get("metadata"))

        # Surface mismatches from extraction vs stored data
        extraction = request.extraction or {}
        request_field_confidences = extract_field_confidences(extraction)
        stored_vendor = ap_item.get("vendor_name") or ""
        extracted_vendor = extraction.get("vendor") or ""
        if extracted_vendor and stored_vendor and extracted_vendor.lower() != stored_vendor.lower():
            mismatches.append({
                "field": "vendor",
                "extracted": extracted_vendor,
                "expected": stored_vendor,
                "severity": "medium",
            })

        stored_amount = ap_item.get("amount")
        extracted_amount = extraction.get("amount")
        if extracted_amount is not None and stored_amount is not None:
            try:
                if abs(float(extracted_amount) - float(stored_amount)) > 0.01:
                    mismatches.append({
                        "field": "amount",
                        "extracted": str(extracted_amount),
                        "expected": str(stored_amount),
                        "severity": "high",
                    })
            except (TypeError, ValueError):
                pass

        # Check exception codes from metadata
        exception_code = ap_item.get("exception_code") or metadata.get("exception_code")
        if exception_code:
            mismatches.append({
                "field": "exception",
                "extracted": exception_code,
                "expected": "none",
                "severity": metadata.get("exception_severity", "medium"),
            })

        confidence_gate = evaluate_critical_field_confidence(
            overall_confidence=ap_item.get("confidence"),
            field_values={
                "vendor": extraction.get("vendor") or ap_item.get("vendor_name"),
                "amount": extraction.get("amount")
                if extraction.get("amount") is not None else ap_item.get("amount"),
                "invoice_number": extraction.get("invoice_number") or ap_item.get("invoice_number"),
                "due_date": extraction.get("due_date") or ap_item.get("due_date"),
            },
            field_confidences=request_field_confidences or metadata.get("field_confidences"),
        )
    else:
        # No AP item found — report as low confidence
        confidence_pct = 0
        confidence_gate = evaluate_critical_field_confidence(
            overall_confidence=0,
            field_values=request.extraction or {},
            field_confidences=extract_field_confidences(request.extraction or {}),
        )

    return {
        "email_id": request.email_id,
        "confidence_pct": confidence_pct,
        "can_post": confidence_pct >= 95 and len(mismatches) == 0 and not confidence_gate.get("requires_field_review"),
        "mismatches": mismatches,
        "threshold": confidence_gate.get("threshold_pct", 95),
        "requires_field_review": bool(confidence_gate.get("requires_field_review")),
        "confidence_blockers": confidence_gate.get("confidence_blockers") or [],
        "confidence_gate": confidence_gate,
    }


@router.post("/match-bank")
async def match_bank_feed(
    request: MatchBankRequest,
):
    """
    Match extracted data against bank feed.
    
    Returns bank transaction match if found.
    """
    from clearledgr.workflows.gmail_activities import match_bank_feed_activity
    
    return await match_bank_feed_activity({
        "extraction": request.extraction,
        "organization_id": request.organization_id,
    })


@router.post("/match-erp")
async def match_erp(
    request: MatchERPRequest,
):
    """
    Match extracted data against ERP records (PO, vendor).
    
    Returns PO match, vendor match, and GL code suggestion.
    """
    from clearledgr.workflows.gmail_activities import match_erp_activity
    
    return await match_erp_activity({
        "extraction": request.extraction,
        "organization_id": request.organization_id,
    })


@router.post("/escalate", dependencies=[Depends(get_current_user)])
async def escalate_to_manager(
    request: EscalateRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    """
    DIFFERENTIATOR: Multi-System Routing - Escalate to manager via Slack.
    
    Sends mismatch details to Slack for manager review.
    """
    from clearledgr.workflows.gmail_activities import send_slack_notification_activity
    
    # Build escalation message
    mismatch_text = "\n".join([f"• {m.get('message', str(m))}" for m in request.mismatches[:5]])
    
    amount_text = f"{request.currency} {request.amount:,.2f}" if isinstance(request.amount, (int, float)) else "Unknown"
    message = request.message or (
        f"*Invoice Review Required*\n\n"
        f"*Vendor:* {request.vendor or 'Unknown'}\n"
        f"*Amount:* {amount_text}\n"
        f"*Confidence:* {request.confidence or 0}%\n\n"
        f"*Issues:*\n{mismatch_text}"
    )
    
    result = await send_slack_notification_activity({
        "type": "escalation",
        "email_id": request.email_id,
        "classification": {"type": "INVOICE"},
        "extraction": {
            "vendor": request.vendor,
            "amount": request.amount,
            "currency": request.currency,
        },
        "confidence_result": {
            "confidence_pct": request.confidence,
            "mismatches": request.mismatches,
            "requires_review": True,
        },
        "organization_id": request.organization_id,
    })
    
    # Record escalation in audit trail
    audit.record_event(
        user_email=request.user_email or "extension",
        action="invoice_escalated",
        entity_type="invoice",
        entity_id=request.email_id,
        organization_id=request.organization_id,
        metadata={
            "vendor": request.vendor,
            "amount": request.amount,
            "confidence": request.confidence,
            "mismatches": request.mismatches,
            "channel": request.channel,
        },
    )
    
    return {
        "email_id": request.email_id,
        "status": "escalated",
        "channel": request.channel,
        "message": message,
    }


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


class RejectInvoiceRequest(BaseModel):
    """Request to reject an invoice from Gmail sidebar."""
    email_id: str
    reason: str
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class BudgetDecisionRequest(BaseModel):
    """Budget decision from Gmail/embedded approval surfaces."""
    email_id: str
    decision: str  # approve_override | request_budget_adjustment | reject
    justification: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class ApprovalNudgeRequest(BaseModel):
    """Request to nudge pending approvers for an invoice."""
    email_id: str
    message: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class FinanceSummaryShareRequest(BaseModel):
    """Request to prepare/share a finance-lead exception summary."""
    email_id: str
    target: str = "email_draft"  # email_draft | slack_thread | teams_reply
    preview_only: bool = False
    recipient_email: Optional[str] = None
    note: Optional[str] = None
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


def _resolve_ap_item_for_extension_action(db: Any, organization_id: str, email_id: str) -> Optional[Dict[str, Any]]:
    item = None
    getter = getattr(db, "get_ap_item", None)
    if callable(getter):
        item = getter(email_id)
        if item and str(item.get("organization_id") or organization_id) != organization_id:
            item = None
    if not item and hasattr(db, "get_ap_item_by_thread"):
        item = db.get_ap_item_by_thread(organization_id, email_id)
    if not item and hasattr(db, "get_ap_item_by_message_id"):
        item = db.get_ap_item_by_message_id(organization_id, email_id)
    return item


def _append_extension_ap_audit(
    db: Any,
    *,
    ap_item_id: str,
    organization_id: str,
    event_type: str,
    actor_id: str,
    reason: str,
    metadata: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not hasattr(db, "append_ap_audit_event"):
        return None
    return db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": event_type,
            "actor_type": "user",
            "actor_id": actor_id,
            "reason": reason,
            "metadata": metadata or {},
            "organization_id": organization_id,
            "source": "gmail_extension",
            "correlation_id": correlation_id,
        }
    )


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
):
    """
    Submit an invoice for Slack approval with full intelligence.
    
    This is the main entry point for the Gmail → Slack → ERP flow.
    
    Behavior:
    - If confidence >= 95%, auto-approves and posts to ERP
    - If confidence < 95%, sends to Slack for manager approval
    - Shows vendor intelligence, policy requirements, budget impact in Slack
    
    Use this when an invoice is detected and ready for processing.
    """
    from clearledgr.services.invoice_workflow import InvoiceData, get_invoice_workflow
    
    org_id = request.organization_id or "default"
    
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
    
    # Log to audit trail
    trail = get_audit_trail(org_id)
    trail.log(
        invoice_id=request.email_id,
        event_type=AuditEventType.ROUTED,
        summary=f"Submitting for approval - Priority: {priority_data.get('priority_label', 'N/A')}",
        details={
            "policy_compliant": policy_result.get("compliant", True) if policy_result else True,
            "required_approvers": policy_result.get("required_approvers", []) if policy_result else [],
        },
        vendor=request.vendor,
        amount=request.amount,
    )
    
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
        organization_id=request.organization_id,
        user_id=request.user_email,
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
    
    workflow = get_invoice_workflow(
        organization_id=org_id,
        slack_channel=request.slack_channel,
    )
    
    # Respect agent decision when present
    decision = agent_decision.get("decision")
    if agent_confidence is not None:
        try:
            invoice.confidence = max(float(invoice.confidence), float(agent_confidence))
        except Exception:
            pass

    if decision and decision != "auto_approve":
        # Force human review path (even if confidence is high)
        invoice.confidence = min(invoice.confidence, workflow.auto_approve_threshold - 0.01)
    elif decision == "auto_approve":
        # Ensure auto-approve threshold is met
        invoice.confidence = max(invoice.confidence, workflow.auto_approve_threshold)

    result = await workflow.process_new_invoice(invoice)
    
    # Log result
    trail.log(
        invoice_id=request.email_id,
        event_type=AuditEventType.APPROVAL_REQUESTED if result.get("status") == "pending_approval" else AuditEventType.AUTO_APPROVED,
        summary=f"Status: {result.get('status')}",
    )
    
    # Legacy audit
    audit.record_event(
        user_email=request.user_email or "extension",
        action="invoice_submitted",
        entity_type="invoice",
        entity_id=request.email_id,
        organization_id=request.organization_id,
        metadata={
            "vendor": request.vendor,
            "amount": request.amount,
            "confidence": request.confidence,
            "result_status": result.get("status"),
            "policy_compliant": policy_result.get("compliant", True) if policy_result else True,
            "priority": priority_data.get("priority") if priority_data else None,
        },
    )
    
    return result


@router.post("/reject-invoice", dependencies=[Depends(get_current_user)])
async def reject_invoice(
    request: RejectInvoiceRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    """Reject an invoice and keep pipeline state in sync."""
    from clearledgr.services.invoice_workflow import get_invoice_workflow

    org_id = request.organization_id or "default"
    rejected_by = request.user_email or "extension"
    workflow = get_invoice_workflow(org_id)
    result = await workflow.reject_invoice(
        gmail_id=request.email_id,
        reason=request.reason,
        rejected_by=rejected_by,
    )

    audit.record_event(
        user_email=rejected_by,
        action="invoice_rejected",
        entity_type="invoice",
        entity_id=request.email_id,
        organization_id=org_id,
        metadata={"reason": request.reason, "result": result},
    )

    if result.get("status") != "rejected":
        raise HTTPException(status_code=400, detail=result.get("reason", "Reject failed"))
    return result


@router.post("/budget-decision", dependencies=[Depends(get_current_user)])
async def budget_decision(
    request: BudgetDecisionRequest,
    audit: AuditTrailService = Depends(get_audit_service),
):
    """Handle explicit budget decisions from Gmail sidebar surfaces."""
    from clearledgr.core.ap_states import OverrideContext, OVERRIDE_TYPE_BUDGET
    from clearledgr.services.invoice_workflow import get_invoice_workflow

    org_id = request.organization_id or "default"
    actor = request.user_email or "extension"
    workflow = get_invoice_workflow(org_id)
    decision = str(request.decision or "").strip().lower()

    if decision == "approve_override":
        if not str(request.justification or "").strip():
            raise HTTPException(status_code=400, detail="justification_required")
        ctx = OverrideContext(
            override_type=OVERRIDE_TYPE_BUDGET,
            justification=str(request.justification),
            actor_id=actor,
        )
        result = await workflow.approve_invoice(
            gmail_id=request.email_id,
            approved_by=actor,
            allow_budget_override=True,
            override_justification=request.justification,
            override_context=ctx,
        )
        if result.get("status") not in {"approved", "error"}:
            raise HTTPException(status_code=400, detail=result.get("reason", "budget_override_failed"))
    elif decision == "request_budget_adjustment":
        result = await workflow.request_budget_adjustment(
            gmail_id=request.email_id,
            requested_by=actor,
            reason=request.justification or "budget_adjustment_requested_in_gmail",
        )
    elif decision == "reject":
        reason = request.justification or "rejected_over_budget_in_gmail"
        result = await workflow.reject_invoice(
            gmail_id=request.email_id,
            reason=reason,
            rejected_by=actor,
        )
    else:
        raise HTTPException(status_code=400, detail="invalid_budget_decision")

    audit.record_event(
        user_email=actor,
        action="budget_decision",
        entity_type="invoice",
        entity_id=request.email_id,
        organization_id=org_id,
        metadata={
            "decision": decision,
            "justification": request.justification,
            "result": result,
        },
    )
    return result


@router.post("/approval-nudge", dependencies=[Depends(get_current_user)])
async def approval_nudge(
    request: ApprovalNudgeRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user = Depends(get_current_user),
):
    """Send a dedicated approver nudge for pending approvals (Slack/Teams best effort)."""
    from clearledgr.services.invoice_workflow import get_invoice_workflow

    org_id = request.organization_id or getattr(user, "organization_id", None) or "default"
    actor_email = request.user_email or getattr(user, "email", None) or "extension"
    db = get_db()
    ap_item = _resolve_ap_item_for_extension_action(db, org_id, request.email_id)
    if not ap_item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    state = str(ap_item.get("state") or "").strip().lower()
    if state not in {"needs_approval", "pending_approval"}:
        raise HTTPException(status_code=400, detail="item_not_waiting_for_approval")

    gmail_id = str(ap_item.get("thread_id") or request.email_id or "").strip()
    if not gmail_id:
        raise HTTPException(status_code=400, detail="missing_gmail_reference")

    workflow = get_invoice_workflow(org_id)
    try:
        amount_num = float(ap_item.get("amount") or 0.0)
    except (TypeError, ValueError):
        amount_num = 0.0

    nudge_text = (
        str(request.message).strip()
        if request.message and str(request.message).strip()
        else (
            f"Reminder: approval is still pending for "
            f"{ap_item.get('vendor_name') or ap_item.get('vendor') or 'invoice'} "
            f"({ap_item.get('currency') or 'USD'} {amount_num:,.2f}). "
            "Please review when available."
        )
    )

    slack_result: Dict[str, Any] = {"status": "skipped", "reason": "no_slack_thread"}
    teams_result: Dict[str, Any] = {"status": "skipped", "reason": "teams_unavailable"}

    slack_thread = db.get_slack_thread(gmail_id) if hasattr(db, "get_slack_thread") else None
    if slack_thread and getattr(workflow, "slack_client", None):
        try:
            sent = await workflow.slack_client.send_message(
                channel=str(slack_thread.get("channel_id") or ""),
                thread_ts=str(slack_thread.get("thread_ts") or slack_thread.get("thread_id") or ""),
                text=nudge_text,
            )
            slack_result = {
                "status": "sent",
                "channel_id": sent.channel,
                "thread_ts": sent.thread_ts or sent.ts,
                "message_ts": sent.ts,
            }
        except Exception as exc:
            slack_result = {"status": "error", "reason": str(exc)}

    teams_meta = _parse_json_dict(ap_item.get("metadata")).get("teams")
    if isinstance(teams_meta, dict) and getattr(workflow, "teams_client", None):
        try:
            budget_payload = {
                "status": ap_item.get("budget_status") or "unknown",
                "requires_decision": bool(ap_item.get("budget_requires_decision")),
            }
            result = workflow.teams_client.send_invoice_budget_card(
                email_id=gmail_id,
                organization_id=org_id,
                vendor=str(ap_item.get("vendor_name") or ap_item.get("vendor") or "Unknown"),
                amount=amount_num,
                currency=str(ap_item.get("currency") or "USD"),
                invoice_number=ap_item.get("invoice_number"),
                budget=budget_payload,
            )
            teams_result = result if isinstance(result, dict) else {"status": "sent"}
        except Exception as exc:
            teams_result = {"status": "error", "reason": str(exc)}

    correlation_id = str(
        ap_item.get("correlation_id")
        or _parse_json_dict(ap_item.get("metadata")).get("correlation_id")
        or ""
    ).strip() or None

    audit_row = _append_extension_ap_audit(
        db,
        ap_item_id=str(ap_item.get("id") or request.email_id),
        organization_id=org_id,
        event_type="approval_nudge_sent" if slack_result.get("status") == "sent" or teams_result.get("status") == "sent" else "approval_nudge_failed",
        actor_id=actor_email,
        reason="approval_nudge",
        metadata={
            "slack": slack_result,
            "teams": teams_result,
            "message": nudge_text[:400],
        },
        correlation_id=correlation_id,
    )

    audit.record_event(
        user_email=actor_email,
        action="approval_nudge",
        entity_type="invoice",
        entity_id=str(ap_item.get("id") or request.email_id),
        organization_id=org_id,
        metadata={
            "email_id": request.email_id,
            "slack": slack_result,
            "teams": teams_result,
            "audit_event_id": (audit_row or {}).get("id"),
        },
    )

    return {
        "status": "nudged" if slack_result.get("status") == "sent" or teams_result.get("status") == "sent" else "error",
        "email_id": request.email_id,
        "ap_item_id": str(ap_item.get("id") or ""),
        "slack": slack_result,
        "teams": teams_result,
        "audit_event_id": (audit_row or {}).get("id"),
    }


@router.post("/finance-summary-share", dependencies=[Depends(get_current_user)])
async def finance_summary_share(
    request: FinanceSummaryShareRequest,
    audit: AuditTrailService = Depends(get_audit_service),
    user = Depends(get_current_user),
):
    """Prepare or deliver a finance-lead exception summary share action."""
    from clearledgr.services.invoice_workflow import get_invoice_workflow
    from clearledgr.services.teams_notifications import (
        build_finance_summary_reply_activity,
        send_finance_summary_reply,
    )

    org_id = request.organization_id or getattr(user, "organization_id", None) or "default"
    actor_email = request.user_email or getattr(user, "email", None) or "extension"
    db = get_db()
    ap_item = _resolve_ap_item_for_extension_action(db, org_id, request.email_id)
    if not ap_item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    target = str(request.target or "email_draft").strip().lower()
    if target not in {"email_draft", "slack_thread", "teams_reply"}:
        raise HTTPException(status_code=400, detail="unsupported_share_target")

    audit_events = db.list_ap_audit_events(str(ap_item.get("id"))) if hasattr(db, "list_ap_audit_events") else []
    summary = _build_finance_lead_summary_payload(ap_item, audit_events=audit_events)

    recipient_email = (
        str(request.recipient_email or "").strip()
        or os.getenv("CLEARLEDGR_FINANCE_LEAD_EMAIL", "").strip()
        or os.getenv("FINANCE_LEAD_EMAIL", "").strip()
        or ""
    )
    note = str(request.note or "").strip()
    vendor = str(ap_item.get("vendor_name") or ap_item.get("vendor") or "Unknown vendor").strip()
    invoice_number = str(ap_item.get("invoice_number") or "N/A").strip()
    subject = f"[Clearledgr] Exception summary: {vendor} · Invoice {invoice_number}"
    body_lines = [
        "Hi,",
        "",
        "Clearledgr prepared the following AP exception summary for review:",
        "",
        *[f"- {line}" for line in (summary.get("lines") or [])],
    ]
    if note:
        body_lines.extend(["", "Operator note:", note])
    body_lines.extend(["", "Sent from Clearledgr Gmail Agent Actions."])
    draft = {
        "to": recipient_email,
        "subject": subject,
        "body": "\n".join(body_lines),
    }

    correlation_id = str(
        ap_item.get("correlation_id")
        or _parse_json_dict(ap_item.get("metadata")).get("correlation_id")
        or ""
    ).strip() or None
    ap_item_id = str(ap_item.get("id") or request.email_id)

    if request.preview_only:
        preview_payload: Dict[str, Any]
        if target == "email_draft":
            preview_payload = {
                "kind": "email_draft",
                "draft": draft,
                "recipient_email": recipient_email,
            }
        elif target == "slack_thread":
            gmail_id = str(ap_item.get("thread_id") or request.email_id or "").strip()
            slack_thread = db.get_slack_thread(gmail_id) if hasattr(db, "get_slack_thread") else None
            if not slack_thread:
                raise HTTPException(status_code=400, detail="slack_thread_not_found")
            text_lines = [f"*{summary.get('title') or 'Finance exception summary'}*"]
            text_lines.extend([f"• {line}" for line in (summary.get("lines") or [])[:8]])
            if note:
                text_lines.extend(["", f"_Operator note:_ {note}"])
            preview_payload = {
                "kind": "slack_thread",
                "channel_id": str(slack_thread.get("channel_id") or ""),
                "thread_ts": str(slack_thread.get("thread_ts") or slack_thread.get("thread_id") or ""),
                "text": "\n".join(text_lines),
            }
        else:  # teams_reply
            metadata = _parse_json_dict(ap_item.get("metadata"))
            teams_meta = metadata.get("teams") if isinstance(metadata.get("teams"), dict) else {}
            channel_id = str((teams_meta or {}).get("channel") or "").strip()
            reply_to_id = str((teams_meta or {}).get("message_id") or "").strip()
            if not channel_id:
                raise HTTPException(status_code=400, detail="teams_channel_not_found")
            item_payload = {
                "id": ap_item_id,
                "vendor": vendor,
                "amount": ap_item.get("amount") or 0,
                "currency": ap_item.get("currency") or "USD",
                "invoice_number": invoice_number,
            }
            preview_payload = {
                "kind": "teams_reply",
                "channel_id": channel_id,
                "reply_to_id": reply_to_id or None,
                "activity": build_finance_summary_reply_activity(
                    item_payload,
                    list(summary.get("lines") or []),
                    summary_title=str(summary.get("title") or "Finance exception summary"),
                    reply_to_id=reply_to_id or None,
                ),
            }

        audit_row = _append_extension_ap_audit(
            db,
            ap_item_id=ap_item_id,
            organization_id=org_id,
            event_type="finance_summary_share_previewed",
            actor_id=actor_email,
            reason=f"finance_summary_preview_{target}",
            metadata={
                "target": target,
                "summary_title": summary.get("title"),
                "summary_lines": summary.get("lines"),
                "preview_kind": preview_payload.get("kind"),
                "recipient_email": recipient_email if target == "email_draft" else None,
                "slack_channel_id": preview_payload.get("channel_id") if target == "slack_thread" else None,
                "teams_channel_id": preview_payload.get("channel_id") if target == "teams_reply" else None,
            },
            correlation_id=correlation_id,
        )
        audit.record_event(
            user_email=actor_email,
            action="finance_summary_share_previewed",
            entity_type="invoice",
            entity_id=ap_item_id,
            organization_id=org_id,
            metadata={
                "email_id": request.email_id,
                "target": target,
                "audit_event_id": (audit_row or {}).get("id"),
            },
        )
        return {
            "status": "preview",
            "target": target,
            "email_id": request.email_id,
            "ap_item_id": ap_item_id,
            "summary": summary,
            "preview": preview_payload,
            "audit_event_id": (audit_row or {}).get("id"),
        }

    if target == "email_draft":
        audit_row = _append_extension_ap_audit(
            db,
            ap_item_id=ap_item_id,
            organization_id=org_id,
            event_type="finance_summary_share_prepared",
            actor_id=actor_email,
            reason="finance_summary_email_draft",
            metadata={
                "target": target,
                "recipient_email": recipient_email,
                "summary_title": summary.get("title"),
                "summary_lines": summary.get("lines"),
            },
            correlation_id=correlation_id,
        )
        audit.record_event(
            user_email=actor_email,
            action="finance_summary_share_prepared",
            entity_type="invoice",
            entity_id=ap_item_id,
            organization_id=org_id,
            metadata={
                "email_id": request.email_id,
                "target": target,
                "recipient_email": recipient_email,
                "audit_event_id": (audit_row or {}).get("id"),
            },
        )
        return {
            "status": "prepared",
            "target": target,
            "email_id": request.email_id,
            "ap_item_id": ap_item_id,
            "summary": summary,
            "draft": draft,
            "audit_event_id": (audit_row or {}).get("id"),
        }

    workflow = get_invoice_workflow(org_id)
    delivery: Dict[str, Any]
    delivered = False

    if target == "slack_thread":
        gmail_id = str(ap_item.get("thread_id") or request.email_id or "").strip()
        slack_thread = db.get_slack_thread(gmail_id) if hasattr(db, "get_slack_thread") else None
        if not slack_thread:
            raise HTTPException(status_code=400, detail="slack_thread_not_found")
        if not getattr(workflow, "slack_client", None):
            raise HTTPException(status_code=400, detail="slack_client_unavailable")
        text_lines = [f"*{summary.get('title') or 'Finance exception summary'}*"]
        text_lines.extend([f"• {line}" for line in (summary.get("lines") or [])[:8]])
        if note:
            text_lines.extend(["", f"_Operator note:_ {note}"])
        try:
            sent = await workflow.slack_client.send_message(
                channel=str(slack_thread.get("channel_id") or ""),
                thread_ts=str(slack_thread.get("thread_ts") or slack_thread.get("thread_id") or ""),
                text="\n".join(text_lines),
            )
            delivery = {
                "channel_id": sent.channel,
                "thread_ts": sent.thread_ts or sent.ts,
                "message_ts": sent.ts,
                "status": "sent",
            }
            delivered = True
        except Exception as exc:
            delivery = {"status": "error", "reason": str(exc)}
    else:  # teams_reply
        metadata = _parse_json_dict(ap_item.get("metadata"))
        teams_meta = metadata.get("teams") if isinstance(metadata.get("teams"), dict) else {}
        channel_id = str((teams_meta or {}).get("channel") or "").strip()
        reply_to_id = str((teams_meta or {}).get("message_id") or "").strip()
        if not channel_id:
            raise HTTPException(status_code=400, detail="teams_channel_not_found")
        item_payload = {
            "id": ap_item_id,
            "vendor": vendor,
            "amount": ap_item.get("amount") or 0,
            "currency": ap_item.get("currency") or "USD",
            "invoice_number": invoice_number,
        }
        ok = await send_finance_summary_reply(
            item_payload,
            channel_id,
            list(summary.get("lines") or []),
            summary_title=str(summary.get("title") or "Finance exception summary"),
            reply_to_id=reply_to_id or None,
        )
        delivery = {
            "channel_id": channel_id,
            "reply_to_id": reply_to_id or None,
            "status": "sent" if ok else "error",
        }
        delivered = bool(ok)

    audit_row = _append_extension_ap_audit(
        db,
        ap_item_id=ap_item_id,
        organization_id=org_id,
        event_type="finance_summary_shared" if delivered else "finance_summary_share_failed",
        actor_id=actor_email,
        reason=f"finance_summary_{target}",
        metadata={
            "target": target,
            "summary_title": summary.get("title"),
            "summary_lines": summary.get("lines"),
            "delivery": delivery,
        },
        correlation_id=correlation_id,
    )

    audit.record_event(
        user_email=actor_email,
        action="finance_summary_shared" if delivered else "finance_summary_share_failed",
        entity_type="invoice",
        entity_id=ap_item_id,
        organization_id=org_id,
        metadata={
            "email_id": request.email_id,
            "target": target,
            "delivery": delivery,
            "audit_event_id": (audit_row or {}).get("id"),
        },
    )

    return {
        "status": "shared" if delivered else "error",
        "target": target,
        "email_id": request.email_id,
        "ap_item_id": ap_item_id,
        "summary": summary,
        "delivery": delivery,
        "audit_event_id": (audit_row or {}).get("id"),
    }


@router.get("/invoice-status/{gmail_id}")
async def get_invoice_status(gmail_id: str):
    """
    Get the current status of an invoice.
    
    Returns: new, pending_approval, approved, posted, rejected
    """
    from clearledgr.core.database import get_db
    
    db = get_db()
    status = db.get_invoice_status(gmail_id)
    
    if not status:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    return status


@router.get("/invoice-pipeline/{organization_id}")
async def get_invoice_pipeline_status(organization_id: str):
    """
    Get all invoices grouped by status (pipeline view).
    
    Returns invoices grouped into: new, pending_approval, approved, posted, rejected
    """
    from clearledgr.core.database import get_db
    
    db = get_db()
    pipeline = _build_extension_pipeline(db, organization_id)
    
    return {
        "organization_id": organization_id,
        "pipeline": pipeline,
        "counts": {status: len(invoices) for status, invoices in pipeline.items()},
    }


@router.get("/workflow/{workflow_id}")
async def get_workflow_status(workflow_id: str):
    """
    Get the status of a running workflow.
    
    Use this to poll for completion of async workflows.
    """
    runtime = TemporalRuntime()
    try:
        return await runtime.get_status(workflow_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow_not_found")


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
async def suggest_gl_code(request: GLSuggestionRequest):
    """
    Get AI-suggested GL code for a vendor.
    
    Returns primary suggestion + alternatives with confidence scores.
    Human reviews and confirms/changes.
    """
    from clearledgr.services.learning import get_learning_service
    from clearledgr.services.vendor_intelligence import get_vendor_intelligence
    
    learning = get_learning_service(request.organization_id)
    vendor_intel = get_vendor_intelligence()
    
    # Get suggestion from learning service (based on historical patterns)
    learned = learning.suggest_gl_code(request.vendor_name)
    
    # Get suggestion from vendor intelligence (known vendor profiles)
    vendor_profile = vendor_intel.get_suggestion(request.vendor_name)
    
    # Combine suggestions
    suggestions = []
    
    # Primary suggestion from learning (historical data)
    if learned and learned.get("gl_code"):
        suggestions.append({
            "gl_code": learned["gl_code"],
            "gl_name": learned.get("gl_description", ""),
            "confidence": learned.get("confidence", 0.5),
            "source": "learning",
            "reason": f"Used {learned.get('occurrence_count', 0)} times for this vendor",
        })
    
    # Suggestion from vendor intelligence (known profiles)
    if vendor_profile and vendor_profile.get("suggested_gl"):
        # Don't duplicate if same as learned
        if not suggestions or suggestions[0]["gl_code"] != vendor_profile["suggested_gl"]:
            suggestions.append({
                "gl_code": vendor_profile["suggested_gl"],
                "gl_name": vendor_profile.get("gl_description", ""),
                "confidence": 0.7 if vendor_profile.get("known_vendor") else 0.4,
                "source": "vendor_profile",
                "reason": f"Typical for {vendor_profile.get('category', 'this vendor type')}",
            })
    
    # Add alternatives from learning service
    if learned and learned.get("alternatives"):
        for alt in learned["alternatives"][:2]:  # Max 2 alternatives
            if not any(s["gl_code"] == alt["gl_code"] for s in suggestions):
                suggestions.append({
                    "gl_code": alt["gl_code"],
                    "gl_name": alt.get("gl_description", ""),
                    "confidence": alt.get("confidence", 0.3),
                    "source": "alternative",
                    "reason": "Also used for similar vendors",
                })
    
    # Sort by confidence
    suggestions.sort(key=lambda x: x["confidence"], reverse=True)
    
    return {
        "vendor_name": request.vendor_name,
        "primary": suggestions[0] if suggestions else None,
        "alternatives": suggestions[1:3] if len(suggestions) > 1 else [],
        "has_suggestion": len(suggestions) > 0,
    }


@router.post("/suggestions/vendor")
async def suggest_vendor(request: VendorSuggestionRequest):
    """
    Get AI-suggested vendor match from email context.
    
    Returns matched vendor + confidence for human confirmation.
    """
    from clearledgr.services.fuzzy_matching import get_fuzzy_matcher
    from clearledgr.services.vendor_management import get_vendor_management_service
    
    matcher = get_fuzzy_matcher()
    vendor_service = get_vendor_management_service(request.organization_id)
    
    # Try to match from extracted vendor name first
    candidates = []
    
    if request.extracted_vendor:
        # Direct match from extraction
        match = matcher.find_best_vendor_match(
            request.extracted_vendor,
            vendor_service.get_all_vendors()
        )
        if match and match.get("score", 0) > 0.6:
            candidates.append({
                "vendor_id": match.get("vendor_id"),
                "vendor_name": match.get("vendor_name"),
                "confidence": match.get("score", 0.7),
                "source": "extraction",
                "matched_from": request.extracted_vendor,
            })
    
    if request.sender_email:
        # Match from sender email domain
        domain = request.sender_email.split("@")[-1] if "@" in request.sender_email else None
        if domain:
            domain_match = matcher.find_vendor_by_domain(
                domain,
                vendor_service.get_all_vendors()
            )
            if domain_match and not any(c["vendor_id"] == domain_match.get("vendor_id") for c in candidates):
                candidates.append({
                    "vendor_id": domain_match.get("vendor_id"),
                    "vendor_name": domain_match.get("vendor_name"),
                    "confidence": domain_match.get("score", 0.6),
                    "source": "email_domain",
                    "matched_from": domain,
                })
    
    # Sort by confidence
    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    
    return {
        "extracted_vendor": request.extracted_vendor,
        "primary": candidates[0] if candidates else None,
        "alternatives": candidates[1:3] if len(candidates) > 1 else [],
        "has_suggestion": len(candidates) > 0,
        "is_new_vendor": len(candidates) == 0,
    }


@router.post("/suggestions/amount-validation")
async def validate_amount(
    vendor_name: str = Body(...),
    amount: float = Body(...),
    organization_id: str = Body("default"),
):
    """
    Validate invoice amount against vendor history.
    
    Returns whether amount seems reasonable + expected range.
    """
    vendor_intel = get_vendor_intelligence()
    
    validation = vendor_intel.validate_amount(vendor_name, amount)
    
    return {
        "vendor_name": vendor_name,
        "amount": amount,
        "is_reasonable": validation.get("seems_reasonable", True),
        "expected_range": validation.get("expected_range"),
        "concern": validation.get("concern"),
        "message": validation.get("message"),
    }


@router.get("/suggestions/form-prefill/{email_id}")
async def get_form_prefill(
    email_id: str,
    organization_id: str = "default",
):
    """
    Get all AI suggestions to pre-fill a form for an invoice.
    
    Combines vendor match, GL suggestion, and amount validation.
    Returns everything needed to pre-fill invoice forms.
    """
    from clearledgr.core.database import get_db
    from clearledgr.services.learning import get_learning_service
    from clearledgr.services.vendor_intelligence import get_vendor_intelligence
    
    db = get_db()
    learning = get_learning_service(organization_id)
    vendor_intel = get_vendor_intelligence()
    
    # Get stored extraction data for this email
    invoice = db.get_invoice_by_email_id(email_id)
    
    if not invoice:
        return {
            "email_id": email_id,
            "has_data": False,
            "message": "No extraction data found for this email",
        }
    
    vendor_name = invoice.get("vendor") or invoice.get("vendor_name", "")
    amount = invoice.get("amount", 0)
    
    # Get GL suggestion
    gl_suggestion = learning.suggest_gl_code(vendor_name) if vendor_name else None
    vendor_profile = vendor_intel.get_suggestion(vendor_name) if vendor_name else None
    
    # Get amount validation
    amount_validation = vendor_intel.validate_amount(vendor_name, amount) if vendor_name and amount else None
    
    return {
        "email_id": email_id,
        "has_data": True,
        "prefill": {
            "vendor": {
                "name": vendor_name,
                "confidence": invoice.get("confidence", 0.5),
            },
            "amount": {
                "value": amount,
                "is_reasonable": amount_validation.get("seems_reasonable", True) if amount_validation else True,
                "expected_range": amount_validation.get("expected_range") if amount_validation else None,
                "concern": amount_validation.get("concern") if amount_validation else None,
            },
            "gl_code": {
                "suggested": gl_suggestion.get("gl_code") if gl_suggestion else (vendor_profile.get("suggested_gl") if vendor_profile else None),
                "name": gl_suggestion.get("gl_description") if gl_suggestion else (vendor_profile.get("gl_description") if vendor_profile else None),
                "confidence": gl_suggestion.get("confidence", 0.5) if gl_suggestion else 0.4,
                "source": "learning" if gl_suggestion else ("vendor_profile" if vendor_profile else None),
            },
            "invoice_number": invoice.get("invoice_number"),
            "invoice_date": invoice.get("invoice_date"),
            "due_date": invoice.get("due_date"),
        },
    }


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
):
    """Generate a pre-filled vendor reply template for a needs_info AP item.
    Returns {to, subject, body} ready for Gmail compose URL construction.
    No auth required — callable from content-script.js without token."""
    db = get_db()
    ap_item = db.get_ap_item(ap_item_id)
    if not ap_item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    if ap_item.get("state") != "needs_info":
        raise HTTPException(status_code=400, detail="item_not_in_needs_info_state")

    vendor = ap_item.get("vendor_name") or "Vendor"
    invoice_number = ap_item.get("invoice_number") or "your recent invoice"
    sender_email = ap_item.get("sender") or ""
    original_subject = ap_item.get("subject") or f"Invoice {invoice_number}"

    # Map exception_code to a human-readable request for the vendor.
    _EXCEPTION_REASON_MAP = {
        "po_reference_required": "Please provide a valid Purchase Order (PO) number for this invoice. Our system requires a PO reference before we can process payment.",
        "missing_po": "Please provide a valid Purchase Order (PO) number for this invoice.",
        "missing_invoice_number": "Please provide a valid invoice number. The invoice number was missing or could not be read from your submission.",
        "invalid_invoice_number": "The invoice number on your submission appears to be invalid. Please re-send with a clearly formatted invoice number.",
        "amount_mismatch": "The invoice amount does not match our purchase order or approval records. Please confirm the correct total and any line-item breakdown.",
        "duplicate_invoice": "This invoice appears to be a duplicate of a previous submission. Please confirm the invoice number and date, or advise if this is a revised invoice.",
        "vendor_not_recognized": "We were unable to match your company to our vendor records. Please confirm your registered company name, VAT/tax ID, and remittance address.",
        "currency_mismatch": "The invoice currency does not match the currency on our purchase order. Please re-issue in the agreed contract currency.",
        "missing_line_items": "Please re-send the invoice with itemised line items (description, quantity, unit price) so we can match it against our purchase order.",
        "policy_attribute_failure": "Additional details are required to process this invoice under our accounting policy. Please confirm the PO number, cost centre, and project code associated with this charge.",
        "approval_limit_exceeded": "This invoice exceeds the approval limit for automatic processing. We are escalating internally — no action is needed from you at this time.",
        "tax_id_required": "Please include your VAT/tax identification number on the invoice. This is required for our accounts payable records.",
    }
    exception_code = str(ap_item.get("exception_code") or "").strip()
    reason_text = (
        str(reason).strip()
        if reason and str(reason).strip()
        else _EXCEPTION_REASON_MAP.get(exception_code)
        or str(ap_item.get("last_error") or "").strip()
        or "additional information is required before we can process this invoice"
    )

    body = (
        f"Dear {vendor},\n\n"
        f"Thank you for submitting invoice {invoice_number}.\n\n"
        f"We need the following before we can complete processing:\n\n"
        f"    {reason_text}\n\n"
        f"Please reply to this email with the requested information and we will "
        f"process your invoice promptly.\n\n"
        f"Best regards"
    )

    return {
        "ap_item_id": ap_item_id,
        "to": sender_email,
        "subject": f"Re: {original_subject}",
        "body": body,
    }


@router.post("/record-field-correction")
async def record_field_correction(
    request: FieldCorrectionRequest,
):
    """Record a field-level correction made by an operator in the Gmail sidebar.

    Persists to ``agent_corrections`` for accuracy trend analysis and fires a
    ``field_correction`` audit event so the correction appears in the audit trail.
    This is the missing link that allows org-specific extraction accuracy to
    compound over time.

    Note: no auth dependency to match the pattern of other extension endpoints
    (content-script.js callers don't carry Bearer tokens). The actor_id in the
    request body identifies the operator.
    """
    import json as _json
    from clearledgr.services.correction_learning import CorrectionLearningService
    from clearledgr.services.audit_trail import get_audit_trail, AuditEventType

    db = get_db()
    ap_item = db.get_ap_item(request.ap_item_id)
    if not ap_item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    organization_id = ap_item.get("organization_id") or "default"
    actor_id = request.actor_id or "operator"

    # 1) Persist to correction learning service (updates agent_corrections table)
    learning_svc = CorrectionLearningService(organization_id)
    try:
        learning_result = learning_svc.record_correction(
            correction_type=request.field,
            original_value=request.original_value,
            corrected_value=request.corrected_value,
            context={
                "ap_item_id": request.ap_item_id,
                "field": request.field,
                "vendor": ap_item.get("vendor_name"),
            },
            user_id=actor_id,
            invoice_id=ap_item.get("thread_id"),
            feedback=request.feedback,
        )
    except Exception as _learn_err:
        logger.warning("correction_learning.record_correction failed: %s", _learn_err)
        learning_result = {}

    # 2) Write audit event so the correction appears in the audit trail and
    #    is counted by GET /api/ops/extraction-quality.
    audit_meta = {
        "field": request.field,
        "original_value": str(request.original_value) if request.original_value is not None else None,
        "corrected_value": str(request.corrected_value),
        "actor_id": actor_id,
        "feedback": request.feedback,
        "learning_result": learning_result,
    }
    try:
        audit_svc = get_audit_trail(organization_id)
        audit_svc.record_event(
            event_type="field_correction",
            invoice_id=ap_item.get("thread_id") or request.ap_item_id,
            actor_type="operator",
            actor_id=actor_id,
            metadata=audit_meta,
        )
    except Exception as _audit_err:
        logger.warning("audit field_correction event failed: %s", _audit_err)

    return {
        "status": "recorded",
        "ap_item_id": request.ap_item_id,
        "field": request.field,
        "learning_result": learning_result,
    }
