"""AP Items API for embedded queue, audit, and AP workflow commands."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.database import get_db
from clearledgr.services.invoice_workflow import get_invoice_workflow


router = APIRouter(prefix="/ap/items", tags=["ap-items"])


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


def _decorate_ap_item(db, item: Dict[str, Any]) -> Dict[str, Any]:
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


class ApproveCommandRequest(BaseModel):
    actor_id: str = Field(..., min_length=1)
    source_channel: Optional[str] = None
    source_message_ref: Optional[str] = None
    idempotency_key: Optional[str] = None


class RejectCommandRequest(BaseModel):
    actor_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    source_channel: Optional[str] = None
    source_message_ref: Optional[str] = None
    idempotency_key: Optional[str] = None


class ResubmitCommandRequest(BaseModel):
    actor_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    corrected_fields: Optional[Dict[str, Any]] = None


class LinkSourceRequest(BaseModel):
    source_type: str = Field(..., min_length=1)
    source_ref: str = Field(..., min_length=1)
    subject: Optional[str] = None
    sender: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class MergeItemsRequest(BaseModel):
    source_ap_item_id: str = Field(..., min_length=1)
    actor_id: str = Field(..., min_length=1)
    reason: Optional[str] = "manual_merge"


class SplitSourceRef(BaseModel):
    source_type: str = Field(..., min_length=1)
    source_ref: str = Field(..., min_length=1)


class SplitItemRequest(BaseModel):
    actor_id: str = Field(..., min_length=1)
    reason: Optional[str] = "manual_split"
    sources: Optional[list[SplitSourceRef]] = None


@router.get("")
async def list_ap_items(
    organization_id: str = "default",
    state: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    db = get_db()
    items = db.list_ap_items(organization_id, state=state, limit=limit, prioritized=True)
    return {"items": [_decorate_ap_item(db, item) for item in items]}


@router.get("/{ap_item_id}")
async def get_ap_item(ap_item_id: str):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")
    return _decorate_ap_item(db, item)


@router.get("/{ap_item_id}/audit")
async def get_ap_item_audit(ap_item_id: str):
    db = get_db()
    events = db.list_ap_audit_events(ap_item_id)
    return {"events": events}


@router.get("/by-thread/{thread_id}")
async def get_ap_item_by_thread(thread_id: str, organization_id: str = "default"):
    db = get_db()
    items = db.list_ap_items_by_thread(organization_id, thread_id)
    if not items:
        raise HTTPException(status_code=404, detail="AP item not found")
    decorated = [_decorate_ap_item(db, item) for item in items]
    return {"items": decorated, "latest": decorated[0]}


@router.get("/by-thread/{thread_id}/audit")
async def get_ap_item_audit_by_thread(thread_id: str, organization_id: str = "default"):
    db = get_db()
    events = db.list_ap_audit_events_by_thread(organization_id, thread_id)
    return {"events": events}


@router.post("/{ap_item_id}/retry-post")
async def retry_ap_item_post(ap_item_id: str):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")
    if item.get("state") != "failed_post":
        raise HTTPException(status_code=409, detail="retry_post allowed only from failed_post")

    organization_id = item.get("organization_id") or "default"
    workflow = get_invoice_workflow(organization_id)
    result = await workflow.retry_post(ap_item_id, actor_id="api_retry")
    if result.get("status") not in {"posted", "failed_post"}:
        raise HTTPException(status_code=500, detail="retry_post_failed")
    return result


@router.post("/{ap_item_id}/commands/approve")
async def approve_ap_item(ap_item_id: str, request: ApproveCommandRequest):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")

    organization_id = item.get("organization_id") or "default"
    workflow = get_invoice_workflow(organization_id)
    result = await workflow.approve_ap_item(
        ap_item_id=ap_item_id,
        approved_by=request.actor_id,
        source_channel=request.source_channel,
        source_message_ref=request.source_message_ref,
        idempotency_key=request.idempotency_key,
    )
    status = result.get("status")
    if status == "invalid_state":
        raise HTTPException(status_code=409, detail="Approval not allowed in current state")
    if status == "not_found":
        raise HTTPException(status_code=404, detail="AP item not found")
    if status == "rejected_terminal":
        raise HTTPException(status_code=409, detail="Rejected AP item cannot be approved")
    return result


@router.post("/{ap_item_id}/commands/reject")
async def reject_ap_item(ap_item_id: str, request: RejectCommandRequest):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")

    organization_id = item.get("organization_id") or "default"
    workflow = get_invoice_workflow(organization_id)
    result = await workflow.reject_ap_item(
        ap_item_id=ap_item_id,
        rejected_by=request.actor_id,
        reason=request.reason,
        source_channel=request.source_channel,
        source_message_ref=request.source_message_ref,
        idempotency_key=request.idempotency_key,
    )
    status = result.get("status")
    if status == "invalid_state":
        raise HTTPException(status_code=409, detail="Rejection not allowed in current state")
    if status == "conflict_post_started":
        raise HTTPException(status_code=409, detail="Rejection blocked because posting already started")
    if status == "not_found":
        raise HTTPException(status_code=404, detail="AP item not found")
    return result


@router.post("/{ap_item_id}/commands/resubmit")
async def resubmit_ap_item(ap_item_id: str, request: ResubmitCommandRequest):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")

    organization_id = item.get("organization_id") or "default"
    workflow = get_invoice_workflow(organization_id)
    result = await workflow.resubmit_ap_item(
        ap_item_id=ap_item_id,
        actor_id=request.actor_id,
        reason=request.reason,
        corrected_fields=request.corrected_fields,
    )
    if result.get("status") == "invalid_state":
        raise HTTPException(status_code=409, detail="Only rejected AP items can be resubmitted")
    return result


@router.post("/{ap_item_id}/merge")
async def merge_ap_items(ap_item_id: str, request: MergeItemsRequest):
    db = get_db()
    target = db.get_ap_item(ap_item_id)
    source = db.get_ap_item(request.source_ap_item_id)
    if not target or not source:
        raise HTTPException(status_code=404, detail="AP item not found")
    workflow = get_invoice_workflow(target.get("organization_id") or "default")
    result = workflow.merge_ap_items(
        target_ap_item_id=ap_item_id,
        source_ap_item_id=request.source_ap_item_id,
        actor_id=request.actor_id,
        reason=request.reason or "manual_merge",
    )
    status = result.get("status")
    if status == "invalid_request":
        raise HTTPException(status_code=400, detail=str(result.get("reason") or "invalid_merge_request"))
    if status == "not_found":
        raise HTTPException(status_code=404, detail="AP item not found")
    result["target_ap_item"] = _decorate_ap_item(db, result.get("target_ap_item") or target)
    result["source_ap_item"] = _decorate_ap_item(db, result.get("source_ap_item") or source)
    return result


@router.post("/{ap_item_id}/split")
async def split_ap_item(ap_item_id: str, request: SplitItemRequest):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")
    workflow = get_invoice_workflow(item.get("organization_id") or "default")
    result = workflow.split_ap_item(
        ap_item_id=ap_item_id,
        actor_id=request.actor_id,
        source_refs=[entry.model_dump() for entry in (request.sources or [])],
        reason=request.reason or "manual_split",
    )
    status = result.get("status")
    if status == "invalid_request":
        raise HTTPException(status_code=400, detail=str(result.get("reason") or "invalid_split_request"))
    if status == "not_found":
        raise HTTPException(status_code=404, detail="AP item not found")
    result["source_ap_item"] = _decorate_ap_item(db, result.get("source_ap_item") or item)
    new_item = result.get("new_ap_item")
    if new_item:
        result["new_ap_item"] = _decorate_ap_item(db, new_item)
    return result


@router.get("/{ap_item_id}/workflow-status")
async def get_ap_workflow_status(ap_item_id: str):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")
    workflow = get_invoice_workflow(item.get("organization_id") or "default")
    return await workflow.get_workflow_status(ap_item_id)


@router.get("/{ap_item_id}/agent-trace")
async def get_ap_agent_trace(ap_item_id: str):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")
    workflow = get_invoice_workflow(item.get("organization_id") or "default")
    return workflow.get_agent_trace(ap_item_id)


@router.get("/{ap_item_id}/sources")
async def get_ap_item_sources(ap_item_id: str):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")
    workflow = get_invoice_workflow(item.get("organization_id") or "default")
    sources = workflow.get_ap_item_sources(ap_item_id)
    return {"ap_item_id": ap_item_id, "sources": sources, "source_count": len(sources)}


@router.post("/{ap_item_id}/sources/link")
async def link_ap_item_source(ap_item_id: str, request: LinkSourceRequest):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")
    try:
        source = db.link_ap_item_source(
            {
                "ap_item_id": ap_item_id,
                "source_type": request.source_type,
                "source_ref": request.source_ref,
                "subject": request.subject,
                "sender": request.sender,
                "metadata": request.metadata or {},
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "linked", "source": source}


@router.get("/{ap_item_id}/context")
async def get_ap_item_context(ap_item_id: str, refresh: bool = False):
    db = get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")
    workflow = get_invoice_workflow(item.get("organization_id") or "default")
    try:
        context = await workflow.get_ap_item_context(ap_item_id, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return context
