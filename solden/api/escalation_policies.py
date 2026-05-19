"""Escalation policy API — Module 11.

CRUD over ``escalation_policies`` plus a read endpoint for the
``escalation_events`` audit log.

  POST   /api/workspace/escalation-policies
  GET    /api/workspace/escalation-policies
  GET    /api/workspace/escalation-policies/{id}
  PATCH  /api/workspace/escalation-policies/{id}
  DELETE /api/workspace/escalation-policies/{id}
  GET    /api/workspace/escalation-policies/events
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from solden.core.auth import TokenData, get_current_user
from solden.core.database import get_db
from solden.core.stores.escalation_policy_store import VALID_ACTIONS

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace/escalation-policies",
    tags=["escalation-policies"],
)


class PolicyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    threshold_hours: int = Field(..., ge=1, le=720)
    exception_types: Optional[List[str]] = None
    severity_filter: Optional[List[str]] = None
    action: str = Field("notify_email")
    recipients: List[EmailStr] = Field(default_factory=list)


class PolicyPatchRequest(BaseModel):
    name: Optional[str] = None
    threshold_hours: Optional[int] = Field(None, ge=1, le=720)
    exception_types: Optional[List[str]] = None
    severity_filter: Optional[List[str]] = None
    action: Optional[str] = None
    recipients: Optional[List[EmailStr]] = None
    is_active: Optional[bool] = None


@router.post("")
def create_policy(
    body: PolicyCreateRequest,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    if body.action not in VALID_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_action",
                "message": f"action must be one of {sorted(VALID_ACTIONS)}",
            },
        )
    if body.action == "notify_email" and not body.recipients:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "missing_recipients",
                "message": "notify_email requires at least one recipient",
            },
        )

    db = get_db()
    return db.create_escalation_policy({
        "organization_id": user.organization_id,
        "name": body.name,
        "threshold_hours": body.threshold_hours,
        "exception_types": body.exception_types or [],
        "severity_filter": body.severity_filter or [],
        "action": body.action,
        "recipients": [str(r) for r in body.recipients],
        "created_by": getattr(user, "user_id", "") or getattr(user, "email", ""),
    })


@router.get("")
def list_policies(
    include_inactive: bool = False,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    rows = db.list_escalation_policies(
        user.organization_id, include_inactive=include_inactive,
    )
    return {"policies": rows}


@router.get("/events")
def list_events(
    limit: int = 100,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    rows = db.list_escalation_events(user.organization_id, limit=limit)
    return {"events": rows}


@router.get("/{policy_id}")
def get_policy(
    policy_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    policy = db.get_escalation_policy(policy_id)
    if not policy or policy.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="policy_not_found")
    return policy


@router.patch("/{policy_id}")
def patch_policy(
    policy_id: str,
    body: PolicyPatchRequest,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    existing = db.get_escalation_policy(policy_id)
    if not existing or existing.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="policy_not_found")

    fields: Dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.threshold_hours is not None:
        fields["threshold_hours"] = body.threshold_hours
    if body.exception_types is not None:
        fields["exception_types"] = body.exception_types
    if body.severity_filter is not None:
        fields["severity_filter"] = body.severity_filter
    if body.action is not None:
        if body.action not in VALID_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_action",
                        "message": f"action must be one of {sorted(VALID_ACTIONS)}"},
            )
        fields["action"] = body.action
    if body.recipients is not None:
        fields["recipients"] = [str(r) for r in body.recipients]
    if body.is_active is not None:
        fields["is_active"] = body.is_active

    updated = db.update_escalation_policy(
        policy_id, user.organization_id, **fields,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="policy_not_found")
    return updated


@router.delete("/{policy_id}")
def delete_policy(
    policy_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    deleted = db.delete_escalation_policy(policy_id, user.organization_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="policy_not_found")
    return {"deleted": True, "policy_id": policy_id}
