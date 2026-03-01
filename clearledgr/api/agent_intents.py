"""Finance agent intent API contract (preview/execute)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import get_current_user
from clearledgr.core.database import get_db
from clearledgr.core.finance_contracts import ActionExecution, SkillRequest
from clearledgr.services.finance_agent_runtime import (
    FinanceAgentRuntime,
    IntentNotSupportedError,
)


router = APIRouter(prefix="/api/agent/intents", tags=["agent-intents"])
_ORG_ADMIN_ROLES = {"admin", "owner", "api"}


class AgentIntentPreviewRequest(BaseModel):
    intent: str = Field(..., min_length=1)
    input: Dict[str, Any] = Field(default_factory=dict)
    organization_id: Optional[str] = None


class AgentIntentExecuteRequest(BaseModel):
    intent: str = Field(..., min_length=1)
    input: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None
    organization_id: Optional[str] = None


class SkillRequestPayload(BaseModel):
    org_id: Optional[str] = None
    skill_id: str = Field(..., min_length=1)
    task_type: str = Field(..., min_length=1)
    entity_id: str = ""
    correlation_id: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)


class ActionExecutionPayload(BaseModel):
    entity_id: str = ""
    action: str = Field(..., min_length=1)
    preview: bool = False
    reason: Optional[str] = None
    idempotency_key: str = ""


class AgentSkillPreviewRequest(BaseModel):
    request: SkillRequestPayload
    organization_id: Optional[str] = None


class AgentSkillExecuteRequest(BaseModel):
    request: SkillRequestPayload
    action: Optional[ActionExecutionPayload] = None
    organization_id: Optional[str] = None


def _translate_runtime_error(exc: Exception) -> HTTPException:
    if isinstance(exc, IntentNotSupportedError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="agent_intent_runtime_error")


def _resolve_org_id_for_user(user: Any, requested_org_id: Optional[str]) -> str:
    org_id = str(requested_org_id or getattr(user, "organization_id", None) or "default")
    role = str(getattr(user, "role", "") or "").strip().lower()
    user_org = str(getattr(user, "organization_id", None) or "default")
    if role not in _ORG_ADMIN_ROLES and org_id != user_org:
        raise HTTPException(status_code=403, detail="org_mismatch")
    return org_id


def _runtime_for_request(user: Any, requested_org_id: Optional[str]) -> FinanceAgentRuntime:
    org_id = _resolve_org_id_for_user(user, requested_org_id)
    return FinanceAgentRuntime(
        organization_id=org_id,
        actor_id=getattr(user, "user_id", None) or getattr(user, "email", None) or "user",
        actor_email=getattr(user, "email", None),
        db=get_db(),
    )


@router.get("/skills")
async def list_skills(user=Depends(get_current_user)):
    runtime = _runtime_for_request(user, None)
    rows = runtime.list_skills()
    return {
        "organization_id": runtime.organization_id,
        "skills": rows,
        "supported_intents": sorted(list(runtime.supported_intents)),
    }


@router.get("/skills/{skill_id}/readiness")
async def get_skill_readiness(
    skill_id: str,
    window_hours: int = Query(default=168, ge=1, le=720),
    organization_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    runtime = _runtime_for_request(user, organization_id)
    try:
        return runtime.skill_readiness(skill_id, window_hours=window_hours)
    except Exception as exc:
        raise _translate_runtime_error(exc)


@router.post("/preview")
async def preview_intent(
    request: AgentIntentPreviewRequest,
    user=Depends(get_current_user),
):
    runtime = _runtime_for_request(user, request.organization_id)
    try:
        return runtime.preview_intent(request.intent, request.input)
    except Exception as exc:
        raise _translate_runtime_error(exc)


@router.post("/execute")
async def execute_intent(
    request: AgentIntentExecuteRequest,
    user=Depends(get_current_user),
):
    runtime = _runtime_for_request(user, request.organization_id)
    try:
        return await runtime.execute_intent(
            request.intent,
            request.input,
            idempotency_key=request.idempotency_key,
        )
    except Exception as exc:
        raise _translate_runtime_error(exc)


@router.post("/preview-request")
async def preview_skill_request(
    body: AgentSkillPreviewRequest,
    user=Depends(get_current_user),
):
    runtime = _runtime_for_request(user, body.organization_id or body.request.org_id)
    req = SkillRequest(
        org_id=runtime.organization_id,
        skill_id=body.request.skill_id,
        task_type=body.request.task_type.strip().lower(),
        entity_id=body.request.entity_id,
        correlation_id=body.request.correlation_id,
        payload=dict(body.request.payload or {}),
    )
    try:
        return runtime.preview_skill_request(req)
    except Exception as exc:
        raise _translate_runtime_error(exc)


@router.post("/execute-request")
async def execute_skill_request(
    body: AgentSkillExecuteRequest,
    user=Depends(get_current_user),
):
    runtime = _runtime_for_request(user, body.organization_id or body.request.org_id)
    req = SkillRequest(
        org_id=runtime.organization_id,
        skill_id=body.request.skill_id,
        task_type=body.request.task_type.strip().lower(),
        entity_id=body.request.entity_id,
        correlation_id=body.request.correlation_id,
        payload=dict(body.request.payload or {}),
    )
    action_payload = body.action
    action = ActionExecution(
        entity_id=(action_payload.entity_id if action_payload else "") or req.entity_id,
        action=(action_payload.action if action_payload else req.task_type),
        preview=bool(action_payload.preview) if action_payload else False,
        reason=action_payload.reason if action_payload else None,
        idempotency_key=(action_payload.idempotency_key if action_payload else "") or "",
    )
    try:
        return await runtime.execute_skill_request(req, action=action)
    except Exception as exc:
        raise _translate_runtime_error(exc)
