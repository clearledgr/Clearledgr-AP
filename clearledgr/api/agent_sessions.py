"""Browser-native agent session APIs (AP v1)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.database import get_db
from clearledgr.services.browser_agent import get_browser_agent_service


router = APIRouter(prefix="/api/agent", tags=["agent"])


class CreateSessionRequest(BaseModel):
    org_id: str = Field(default="default", min_length=1)
    ap_item_id: str = Field(..., min_length=1)
    actor_id: str = Field(default="agent_runtime", min_length=1)
    metadata: Optional[Dict[str, Any]] = None


class EnqueueCommandRequest(BaseModel):
    actor_id: str = Field(default="agent_runtime", min_length=1)
    actor_role: Optional[str] = None
    workflow_id: Optional[str] = None
    tool_name: str = Field(..., min_length=1)
    command_id: Optional[str] = None
    correlation_id: Optional[str] = None
    target: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    confirm: bool = False
    confirmed_by: Optional[str] = None


class SubmitResultRequest(BaseModel):
    actor_id: str = Field(default="extension_runner", min_length=1)
    command_id: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)
    result_payload: Dict[str, Any] = {}


class UpsertPolicyRequest(BaseModel):
    org_id: str = Field(default="default", min_length=1)
    updated_by: str = Field(default="system", min_length=1)
    enabled: bool = True
    config: Dict[str, Any] = {}


class PreviewCommandRequest(BaseModel):
    actor_id: str = Field(default="agent_runtime", min_length=1)
    actor_role: Optional[str] = None
    workflow_id: Optional[str] = None
    tool_name: str = Field(..., min_length=1)
    command_id: Optional[str] = None
    correlation_id: Optional[str] = None
    target: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None


class DispatchMacroRequest(BaseModel):
    actor_id: str = Field(default="agent_runtime", min_length=1)
    actor_role: Optional[str] = None
    workflow_id: Optional[str] = None
    correlation_id: Optional[str] = None
    dry_run: bool = False
    params: Optional[Dict[str, Any]] = None


@router.post("/sessions")
async def create_agent_session(request: CreateSessionRequest):
    db = get_db()
    ap_item = db.get_ap_item(request.ap_item_id)
    if not ap_item:
        raise HTTPException(status_code=404, detail="AP item not found")
    if str(ap_item.get("organization_id") or "default") != request.org_id:
        raise HTTPException(status_code=403, detail="org_mismatch")

    service = get_browser_agent_service()
    try:
        session = service.create_session(
            organization_id=request.org_id,
            ap_item_id=request.ap_item_id,
            created_by=request.actor_id,
            metadata=request.metadata,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "browser_agent_disabled":
            raise HTTPException(status_code=503, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    return {"session": session}


@router.get("/sessions/{session_id}")
async def get_agent_session(session_id: str):
    service = get_browser_agent_service()
    try:
        payload = service.get_session(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="session_not_found")
    return payload


@router.post("/sessions/{session_id}/commands")
async def enqueue_agent_command(session_id: str, request: EnqueueCommandRequest):
    service = get_browser_agent_service()
    try:
        event = service.enqueue_command(
            session_id=session_id,
            command={
                "tool_name": request.tool_name,
                "command_id": request.command_id,
                "correlation_id": request.correlation_id,
                "target": request.target or {},
                "params": request.params or {},
                "idempotency_key": request.idempotency_key,
            },
            actor_id=request.actor_id,
            confirm=request.confirm,
            confirmed_by=request.confirmed_by,
            actor_role=request.actor_role,
            workflow_id=request.workflow_id,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "browser_agent_disabled":
            raise HTTPException(status_code=503, detail=detail)
        if detail == "session_not_found":
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    return {"event": event}


@router.post("/sessions/{session_id}/commands/preview")
async def preview_agent_command(session_id: str, request: PreviewCommandRequest):
    service = get_browser_agent_service()
    try:
        payload = service.preview_command(
            session_id=session_id,
            command={
                "tool_name": request.tool_name,
                "command_id": request.command_id,
                "correlation_id": request.correlation_id,
                "target": request.target or {},
                "params": request.params or {},
                "actor_role": request.actor_role,
                "workflow_id": request.workflow_id,
            },
            actor_id=request.actor_id,
            actor_role=request.actor_role,
            workflow_id=request.workflow_id,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "browser_agent_disabled":
            raise HTTPException(status_code=503, detail=detail)
        if detail == "session_not_found":
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    return {"preview": payload}


@router.post("/sessions/{session_id}/macros/{macro_name}")
async def dispatch_agent_macro(session_id: str, macro_name: str, request: DispatchMacroRequest):
    service = get_browser_agent_service()
    try:
        payload = service.dispatch_macro(
            session_id=session_id,
            macro_name=macro_name,
            actor_id=request.actor_id,
            actor_role=request.actor_role,
            workflow_id=request.workflow_id,
            correlation_id=request.correlation_id,
            params=request.params or {},
            dry_run=bool(request.dry_run),
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "browser_agent_disabled":
            raise HTTPException(status_code=503, detail=detail)
        if detail == "session_not_found":
            raise HTTPException(status_code=404, detail=detail)
        if detail == "macro_not_supported":
            raise HTTPException(status_code=400, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    return payload


@router.post("/sessions/{session_id}/results")
async def submit_agent_result(session_id: str, request: SubmitResultRequest):
    service = get_browser_agent_service()
    try:
        event = service.submit_result(
            session_id=session_id,
            command_id=request.command_id,
            status=request.status,
            result_payload=request.result_payload,
            actor_id=request.actor_id,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "browser_agent_disabled":
            raise HTTPException(status_code=503, detail=detail)
        if detail in {"session_not_found", "command_not_found"}:
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    return {"event": event}


@router.get("/policies/browser")
async def get_browser_policy(org_id: str = "default"):
    service = get_browser_agent_service()
    return {"policy": service.get_policy(org_id)}


@router.put("/policies/browser")
async def upsert_browser_policy(request: UpsertPolicyRequest):
    db = get_db()
    policy = db.upsert_agent_policy(
        organization_id=request.org_id,
        policy_name="browser_agent_v1",
        config=request.config or {},
        updated_by=request.updated_by,
        enabled=request.enabled,
    )
    return {"policy": policy}
