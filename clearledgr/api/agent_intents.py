"""Finance agent intent API contract (preview/execute)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import get_current_user, require_ops_user
from clearledgr.core.database import get_db
from clearledgr.core.finance_contracts import ActionExecution, SkillRequest
from clearledgr.services.agent_command_dispatch import build_runtime_for_user


router = APIRouter(prefix="/api/agent/intents", tags=["agent-intents"])
logger = logging.getLogger(__name__)


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


def _intent_not_supported_error_type():
    from clearledgr.services.finance_agent_runtime import IntentNotSupportedError

    return IntentNotSupportedError


def _translate_runtime_error(exc: Exception) -> HTTPException:
    error_code = "agent_intent_runtime_error"
    IntentNotSupportedError = _intent_not_supported_error_type()
    if isinstance(exc, IntentNotSupportedError):
        error_code = "intent_not_supported"
        return HTTPException(status_code=400, detail={"code": error_code, "message": str(exc)})
    if isinstance(exc, LookupError):
        error_code = "lookup_error"
        return HTTPException(status_code=404, detail={"code": error_code, "message": str(exc)})
    if isinstance(exc, PermissionError):
        error_code = "permission_error"
        return HTTPException(status_code=403, detail={"code": error_code, "message": str(exc)})
    if isinstance(exc, ValueError):
        error_code = "validation_error"
        return HTTPException(status_code=400, detail={"code": error_code, "message": str(exc)})
    logger.exception("Unhandled agent intent runtime error: %s", exc)
    return HTTPException(
        status_code=500,
        detail={
            "code": error_code,
            "message": "Unexpected agent runtime failure",
        },
    )


def _runtime_for_request(user: Any, requested_org_id: Optional[str]) -> Any:
    return build_runtime_for_user(
        user,
        requested_org_id,
        db=get_db(),
        fallback_actor="user",
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
    user=Depends(require_ops_user),
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
    user=Depends(require_ops_user),
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
