"""Gmail extension endpoints (AP v1)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import base64
import hashlib
import os
from datetime import datetime, timedelta, timezone

import httpx

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from clearledgr.core.database import get_db
from clearledgr.services.email_parser import parse_email
from clearledgr.services.invoice_workflow import InvoiceData, get_invoice_workflow, compute_invoice_key
from clearledgr.services.gmail_api import GmailToken, token_store


router = APIRouter(prefix="/extension", tags=["gmail-extension"])


def _parse_metadata(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            import json as _json

            return _json.loads(raw)
        except Exception:
            return {}
    return {}


def _build_navigator_hint(item: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    due_raw = item.get("due_date")
    created_raw = item.get("created_at") or item.get("updated_at")
    due_at = None
    created_at = None
    try:
        if due_raw:
            due_at = datetime.fromisoformat(str(due_raw).replace("Z", "+00:00"))
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
    except Exception:
        due_at = None
    try:
        if created_raw:
            created_at = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
    except Exception:
        created_at = None

    urgency = "normal"
    if due_at:
        hours_to_due = (due_at - now).total_seconds() / 3600.0
        if hours_to_due <= 24:
            urgency = "urgent"
        elif hours_to_due <= 72:
            urgency = "elevated"

    risk_level = str(metadata.get("exception_severity") or "").strip().lower() or "low"
    if risk_level not in {"critical", "high", "medium", "low"}:
        risk_level = "low"

    sla_minutes = max(1, int(os.getenv("AP_APPROVAL_SLA_MINUTES", "240") or 240))
    sla_breached = False
    if str(item.get("state") or "") == "needs_approval" and created_at:
        lag_minutes = max(0.0, (now - created_at).total_seconds() / 60.0)
        sla_breached = lag_minutes > sla_minutes

    return {
        "urgency": urgency,
        "risk_level": risk_level,
        "sla_breached": sla_breached,
        "sla_minutes": sla_minutes,
    }


def _decorate_worklist_item(db, item: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(item)
    metadata = _parse_metadata(data.get("metadata"))
    sources = db.list_ap_item_sources(data.get("id"))
    primary_source = {
        "thread_id": data.get("thread_id"),
        "message_id": data.get("message_id"),
    }
    if not primary_source["thread_id"] or not primary_source["message_id"]:
        for source in sources:
            if source.get("source_type") == "gmail_thread" and not primary_source["thread_id"]:
                primary_source["thread_id"] = source.get("source_ref")
            if source.get("source_type") == "gmail_message" and not primary_source["message_id"]:
                primary_source["message_id"] = source.get("source_ref")

    source_keys = {
        (str(source.get("source_type") or ""), str(source.get("source_ref") or ""))
        for source in sources
        if source.get("source_type") and source.get("source_ref")
    }
    if primary_source.get("thread_id"):
        source_keys.add(("gmail_thread", str(primary_source["thread_id"])))
    if primary_source.get("message_id"):
        source_keys.add(("gmail_message", str(primary_source["message_id"])))

    data["source_count"] = len(source_keys)
    data["primary_source"] = primary_source
    data["merge_reason"] = metadata.get("merge_reason")
    data["has_context_conflict"] = bool(metadata.get("has_context_conflict"))
    data["exception_code"] = metadata.get("exception_code")
    data["exception_severity"] = metadata.get("exception_severity")
    data["budget_status"] = metadata.get("budget_status")
    data["priority_score"] = float(metadata.get("priority_score") or 0.0)
    data["po_match_result"] = metadata.get("po_match_result") or {}
    data["budget_check_result"] = metadata.get("budget_check_result") or {}
    data["risk_signals"] = metadata.get("risk_signals") or {}
    data["source_ranking"] = metadata.get("source_ranking") or {}
    data["navigator"] = _build_navigator_hint(data, metadata)
    data["conflict_actions"] = ["merge", "split"] if data["has_context_conflict"] else []
    return data


class EmailTriageRequest(BaseModel):
    email_id: str
    subject: Optional[str] = None
    sender: Optional[str] = None
    snippet: Optional[str] = None
    body: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    thread_id: Optional[str] = None
    message_id: Optional[str] = None


class SubmitForApprovalRequest(BaseModel):
    email_id: str
    run_id: Optional[str] = None
    invoice_key: Optional[str] = None
    idempotency_key: Optional[str] = None
    subject: Optional[str] = None
    sender: Optional[str] = None
    vendor: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = "USD"
    invoice_number: Optional[str] = None
    due_date: Optional[str] = None
    confidence: Optional[float] = 0.0
    organization_id: Optional[str] = None
    user_email: Optional[str] = None
    slack_channel: Optional[str] = None
    thread_id: Optional[str] = None
    message_id: Optional[str] = None


class ApproveAndPostRequest(BaseModel):
    email_id: str
    extraction: Dict[str, Any]
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class RejectInvoiceRequest(BaseModel):
    email_id: str
    reason: Optional[str] = None
    organization_id: Optional[str] = None
    user_email: Optional[str] = None


class RegisterGmailTokenRequest(BaseModel):
    access_token: str
    expires_in: Optional[int] = 3600
    email: Optional[str] = None


@router.post("/triage")
async def triage_email(request: EmailTriageRequest):
    """Classify and extract invoice metadata; create AP item."""
    org_id = request.organization_id or "default"
    sender = request.sender or ""
    subject = request.subject or ""
    body = request.body or request.snippet or ""
    attachments = request.attachments or []

    attachment_hashes: List[str] = []
    for att in attachments:
        payload = att.get("content_base64")
        if not payload:
            continue
        try:
            raw = base64.b64decode(payload)
        except Exception:
            continue
        attachment_hashes.append(hashlib.sha256(raw).hexdigest())

    extraction = parse_email(subject=subject, body=body, sender=sender, attachments=attachments)
    classification = {
        "type": "INVOICE" if extraction.get("email_type") == "invoice" else extraction.get("email_type", "unknown").upper(),
        "confidence": extraction.get("confidence", 0),
        "method": "rules",
    }

    primary_amount = extraction.get("primary_amount")
    invoice_number = extraction.get("primary_invoice")
    invoice_date = extraction.get("primary_date")
    due_date = extraction.get("primary_date")
    vendor = extraction.get("vendor") or sender
    currency = extraction.get("currency") or "USD"

    invoice = InvoiceData(
        gmail_id=request.email_id,
        thread_id=request.thread_id or request.email_id,
        message_id=request.message_id or request.email_id,
        subject=subject,
        sender=sender,
        vendor_name=vendor,
        amount=primary_amount,
        currency=currency,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        due_date=due_date,
        confidence=extraction.get("confidence", 0),
        organization_id=org_id,
        user_id=request.user_email,
        metadata={"raw": extraction, "attachment_hashes": attachment_hashes},
    )

    workflow = get_invoice_workflow(org_id)
    try:
        result = await workflow.process_new_invoice(invoice)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    ap_item = result.get("ap_item") or {}
    metadata = ap_item.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            import json as _json

            metadata = _json.loads(metadata)
        except Exception:
            metadata = {}
    workflow_id = ap_item.get("workflow_id") or metadata.get("workflow_id")
    run_id = ap_item.get("run_id") or metadata.get("run_id")

    return {
        "email_id": request.email_id,
        "classification": classification,
        "extraction": {
            "vendor": vendor,
            "amount": primary_amount,
            "currency": currency,
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "due_date": due_date,
            "confidence": extraction.get("confidence", 0),
        },
        "ap_item": ap_item,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "status": result.get("status"),
        "success": True,
    }


@router.post("/submit-for-approval")
async def submit_for_approval(request: SubmitForApprovalRequest):
    """Route approval request to Slack."""
    org_id = request.organization_id or "default"
    db = get_db()
    invoice_key = request.invoice_key or compute_invoice_key(
        request.vendor or "", request.amount, request.invoice_number, request.due_date
    )
    ap_item = db.get_ap_item_by_invoice_key(org_id, invoice_key)
    workflow = get_invoice_workflow(org_id, slack_channel=request.slack_channel)

    if ap_item and ap_item.get("state") == "rejected":
        invoice = InvoiceData(
            gmail_id=request.email_id,
            thread_id=request.thread_id or request.email_id,
            message_id=request.message_id or request.email_id,
            subject=request.subject or "",
            sender=request.sender or "",
            vendor_name=request.vendor or "",
            amount=request.amount,
            currency=request.currency or "USD",
            invoice_number=request.invoice_number,
            invoice_date=None,
            due_date=request.due_date,
            confidence=request.confidence or 0,
            organization_id=org_id,
            user_id=request.user_email,
            metadata={"manual_submit": True, "run_id": request.run_id},
        )
        try:
            result = await workflow.process_new_invoice(invoice)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"status": result.get("status"), "ap_item": result.get("ap_item")}

    if not ap_item:
        ap_item = db.create_ap_item({
            "invoice_key": invoice_key,
            "thread_id": request.thread_id or request.email_id,
            "message_id": request.message_id or request.email_id,
            "subject": request.subject,
            "sender": request.sender,
            "vendor_name": request.vendor,
            "amount": request.amount,
            "currency": request.currency or "USD",
            "invoice_number": request.invoice_number,
            "due_date": request.due_date,
            "state": "validated",
            "confidence": request.confidence or 0,
            "approval_required": True,
            "organization_id": org_id,
            "user_id": request.user_email,
            "metadata": {"run_id": request.run_id},
        })

    if ap_item.get("state") == "needs_approval":
        return {"status": "needs_approval", "ap_item": ap_item, "idempotent": True}
    try:
        ap_item = await workflow.request_approval(ap_item, reason="manual_submit_for_approval")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "needs_approval", "ap_item": ap_item}


@router.post("/approve-and-post")
async def approve_and_post(request: ApproveAndPostRequest):
    """Direct posting from Gmail is disabled in AP v1. Approval happens in Slack."""
    raise HTTPException(
        status_code=409,
        detail="Direct approve-and-post is disabled. Use Slack approval actions.",
    )


@router.post("/reject-invoice")
async def reject_invoice(request: RejectInvoiceRequest):
    """Direct rejection from Gmail is disabled in AP v1. Decision happens in Slack."""
    raise HTTPException(
        status_code=409,
        detail="Direct rejection is disabled. Use Slack approval actions.",
    )


@router.get("/pipeline")
async def get_pipeline(organization_id: str = "default"):
    """Return AP items grouped by state for the embedded queue view."""
    db = get_db()
    items = db.list_ap_items(organization_id)
    pipeline: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        metadata = _parse_metadata(item.get("metadata"))
        if metadata.get("hidden_from_worklist"):
            continue
        decorated = _decorate_worklist_item(db, item)
        pipeline.setdefault(item.get("state", "received"), []).append(decorated)
    return pipeline


@router.get("/worklist")
async def get_worklist(
    organization_id: str = "default",
    state: Optional[str] = None,
    limit: int = 200,
):
    """Return invoice-centric worklist with one row per AP item and source aggregation metadata."""
    db = get_db()
    items = db.list_ap_items(
        organization_id,
        state=state,
        limit=max(1, min(int(limit or 200), 1000)),
        prioritized=True,
    )
    worklist = []
    for item in items:
        metadata = _parse_metadata(item.get("metadata"))
        if metadata.get("hidden_from_worklist"):
            continue
        worklist.append(_decorate_worklist_item(db, item))
    return {
        "items": worklist,
        "count": len(worklist),
        "organization_id": organization_id,
    }


@router.post("/gmail/register-token")
async def register_gmail_token(request: RegisterGmailTokenRequest):
    """Persist Gmail OAuth token for backend autopilot usage."""
    access_token = (request.access_token or "").strip()
    if not access_token:
        raise HTTPException(status_code=400, detail="access_token_required")

    user_info: Dict[str, Any] = {}
    gmail_profile: Dict[str, Any] = {}
    userinfo_status: Optional[int] = None
    profile_status: Optional[int] = None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            userinfo_response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_status = userinfo_response.status_code
            if userinfo_status < 400:
                user_info = userinfo_response.json()

            # Gmail-scoped OAuth tokens may not be accepted by oauth2 userinfo.
            # Fall back to Gmail profile validation so local AP auth can proceed.
            if not user_info:
                gmail_profile_response = await client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                profile_status = gmail_profile_response.status_code
                if profile_status < 400:
                    gmail_profile = gmail_profile_response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"google_userinfo_unreachable:{exc}") from exc

    if userinfo_status and userinfo_status >= 400 and (not profile_status or profile_status >= 400):
        raise HTTPException(status_code=401, detail="invalid_google_access_token")

    user_id = str(user_info.get("id") or "").strip()
    email = str(user_info.get("email") or gmail_profile.get("emailAddress") or request.email or "").strip()
    if not user_id and email:
        user_id = email
    if not user_id:
        raise HTTPException(status_code=502, detail="google_profile_missing_id")
    if not email:
        raise HTTPException(status_code=502, detail="google_userinfo_missing_email")

    expires_in = int(request.expires_in or 3600)
    expires_in = max(60, min(expires_in, 86400))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    token_store.store(
        GmailToken(
            user_id=user_id,
            access_token=access_token,
            refresh_token="",
            expires_at=expires_at,
            email=email,
        )
    )

    db = get_db()
    db.save_gmail_autopilot_state(
        user_id=user_id,
        email=email,
        last_error=None,
    )

    return {
        "success": True,
        "user_id": user_id,
        "email": email,
        "expires_at": expires_at.isoformat(),
    }
