"""Browser-native agent session APIs (AP v1)."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.browser_agent import get_browser_agent_service
from clearledgr.services.erp_api_first import reconcile_browser_fallback_completion


router = APIRouter(
    prefix="/api/agent",
    tags=["agent"],
    dependencies=[Depends(get_current_user)],
)


_ORG_ADMIN_ROLES = {"admin", "owner"}


def _assert_org_access(user: TokenData, org_id: str) -> None:
    if not org_id:
        return
    if user.role in _ORG_ADMIN_ROLES:
        return
    if str(org_id) != str(user.organization_id):
        raise HTTPException(status_code=403, detail="org_mismatch")


def _load_session_for_user(db, user: TokenData, session_id: str) -> Dict[str, Any]:
    session = db.get_agent_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session_not_found")
    _assert_org_access(user, str(session.get("organization_id") or "default"))
    return session


def _runner_trust_mode() -> str:
    mode = str(os.getenv("AP_BROWSER_RUNNER_TRUST_MODE", "api_or_admin")).strip().lower()
    if mode in {"authenticated", "api_only", "api_or_admin"}:
        return mode
    return "api_or_admin"


def _audit_runner_callback_unauthorized(
    *,
    db,
    session: Optional[Dict[str, Any]],
    user: TokenData,
    endpoint: str,
    reason: str,
) -> None:
    session_id = str((session or {}).get("id") or "unknown")
    ap_item_id = str((session or {}).get("ap_item_id") or "") or f"agent_runner_callback:{session_id}"
    organization_id = str((session or {}).get("organization_id") or user.organization_id or "default")
    try:
        db.append_ap_audit_event(
            {
                "ap_item_id": ap_item_id,
                "event_type": "runner_callback_unauthorized",
                "actor_type": "user",
                "actor_id": user.user_id,
                "source": "agent_runner",
                "reason": reason,
                "organization_id": organization_id,
                "metadata": {
                    "endpoint": endpoint,
                    "role": user.role,
                    "trust_mode": _runner_trust_mode(),
                    "session_id": session_id,
                },
                "idempotency_key": f"runner_callback_unauthorized:{endpoint}:{session_id}:{user.user_id}:{user.role}:{_runner_trust_mode()}",
            }
        )
    except Exception:
        # Best-effort audit for denied callbacks.
        return


def _assert_runner_callback_authorized(
    *,
    db,
    session: Optional[Dict[str, Any]],
    user: TokenData,
    endpoint: str,
) -> None:
    mode = _runner_trust_mode()
    if mode == "authenticated":
        return
    if mode == "api_or_admin" and user.role in {"api", "admin", "owner"}:
        return
    if mode == "api_only" and user.role == "api":
        return
    _audit_runner_callback_unauthorized(
        db=db,
        session=session,
        user=user,
        endpoint=endpoint,
        reason="runner_trust_policy_denied",
    )
    raise HTTPException(status_code=403, detail="runner_trust_policy_denied")


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


class CompleteFallbackRequest(BaseModel):
    macro_name: str = Field(default="post_invoice_to_erp", min_length=1)
    status: str = Field(..., min_length=1)
    erp_reference: Optional[str] = None
    evidence: Dict[str, Any] = {}
    error_code: Optional[str] = None
    error_message_redacted: Optional[str] = None
    idempotency_key: Optional[str] = None
    correlation_id: Optional[str] = None


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
async def create_agent_session(
    request: CreateSessionRequest,
    user: TokenData = Depends(get_current_user),
):
    _assert_org_access(user, request.org_id)
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
            created_by=user.user_id,
            metadata=request.metadata,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "browser_agent_disabled":
            raise HTTPException(status_code=503, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    return {"session": session}


@router.get("/sessions/{session_id}")
async def get_agent_session(
    session_id: str,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    _load_session_for_user(db, user, session_id)
    service = get_browser_agent_service()
    try:
        payload = service.get_session(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="session_not_found")
    return payload


@router.post("/sessions/{session_id}/commands")
async def enqueue_agent_command(
    session_id: str,
    request: EnqueueCommandRequest,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    _load_session_for_user(db, user, session_id)
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
            actor_id=user.user_id,
            confirm=request.confirm,
            confirmed_by=request.confirmed_by or (user.user_id if request.confirm else None),
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
async def preview_agent_command(
    session_id: str,
    request: PreviewCommandRequest,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    _load_session_for_user(db, user, session_id)
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
            actor_id=user.user_id,
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
async def dispatch_agent_macro(
    session_id: str,
    macro_name: str,
    request: DispatchMacroRequest,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    _load_session_for_user(db, user, session_id)
    service = get_browser_agent_service()
    try:
        payload = service.dispatch_macro(
            session_id=session_id,
            macro_name=macro_name,
            actor_id=user.user_id,
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
async def submit_agent_result(
    session_id: str,
    request: SubmitResultRequest,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    session = _load_session_for_user(db, user, session_id)
    _assert_runner_callback_authorized(
        db=db,
        session=session,
        user=user,
        endpoint="results",
    )
    service = get_browser_agent_service()
    try:
        event = service.submit_result(
            session_id=session_id,
            command_id=request.command_id,
            status=request.status,
            result_payload=request.result_payload,
            actor_id=user.user_id,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "browser_agent_disabled":
            raise HTTPException(status_code=503, detail=detail)
        if detail in {"session_not_found", "command_not_found"}:
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    return {"event": event}


@router.post("/sessions/{session_id}/complete")
async def complete_browser_fallback_session(
    session_id: str,
    request: CompleteFallbackRequest,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    session = _load_session_for_user(db, user, session_id)
    _assert_runner_callback_authorized(
        db=db,
        session=session,
        user=user,
        endpoint="complete",
    )
    try:
        completion = reconcile_browser_fallback_completion(
            session_id=session_id,
            macro_name=request.macro_name,
            status=request.status,
            actor_id=user.user_id,
            erp_reference=request.erp_reference,
            evidence=request.evidence or {},
            error_code=request.error_code,
            error_message_redacted=request.error_message_redacted,
            idempotency_key=request.idempotency_key,
            correlation_id=request.correlation_id,
            db=db,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail in {"session_not_found", "fallback_ap_item_not_found"}:
            raise HTTPException(status_code=404, detail=detail)
        if detail in {"not_fallback_session", "unsupported_fallback_macro", "invalid_completion_status"}:
            raise HTTPException(status_code=400, detail=detail)
        if detail == "fallback_failure_after_posted" or detail.startswith("invalid_state_for_fallback_"):
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    return {"completion": completion}


@router.get("/policies/browser")
async def get_browser_policy(
    org_id: str = "default",
    user: TokenData = Depends(get_current_user),
):
    _assert_org_access(user, org_id)
    service = get_browser_agent_service()
    return {"policy": service.get_policy(org_id)}


@router.put("/policies/browser")
async def upsert_browser_policy(
    request: UpsertPolicyRequest,
    user: TokenData = Depends(get_current_user),
):
    _assert_org_access(user, request.org_id)
    db = get_db()
    policy = db.upsert_agent_policy(
        organization_id=request.org_id,
        policy_name="browser_agent_v1",
        config=request.config or {},
        updated_by=user.user_id,
        enabled=request.enabled,
    )
    return {"policy": policy}
