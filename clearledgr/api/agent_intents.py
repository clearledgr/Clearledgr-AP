"""Finance agent intent API contract (preview/execute)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import get_current_user
from clearledgr.core.database import get_db
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


@router.post("/preview")
async def preview_intent(
    request: AgentIntentPreviewRequest,
    user=Depends(get_current_user),
):
    org_id = _resolve_org_id_for_user(user, request.organization_id)
    runtime = FinanceAgentRuntime(
        organization_id=org_id,
        actor_id=getattr(user, "user_id", None) or getattr(user, "email", None) or "user",
        actor_email=getattr(user, "email", None),
        db=get_db(),
    )
    try:
        return runtime.preview_intent(request.intent, request.input)
    except Exception as exc:
        raise _translate_runtime_error(exc)


@router.post("/execute")
async def execute_intent(
    request: AgentIntentExecuteRequest,
    user=Depends(get_current_user),
):
    org_id = _resolve_org_id_for_user(user, request.organization_id)
    runtime = FinanceAgentRuntime(
        organization_id=org_id,
        actor_id=getattr(user, "user_id", None) or getattr(user, "email", None) or "user",
        actor_email=getattr(user, "email", None),
        db=get_db(),
    )
    try:
        return await runtime.execute_intent(
            request.intent,
            request.input,
            idempotency_key=request.idempotency_key,
        )
    except Exception as exc:
        raise _translate_runtime_error(exc)
