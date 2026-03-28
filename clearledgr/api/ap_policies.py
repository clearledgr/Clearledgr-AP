"""AP business policy APIs (tenant-configurable, versioned, auditable)."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.policy_compliance import (
    AP_POLICY_NAME,
    get_approval_automation_policy,
    get_policy_compliance,
)


router = APIRouter(prefix="/api/ap/policies", tags=["ap-policies"])
_ADMIN_ROLES = {"admin", "owner"}


class UpsertAPPolicyRequest(BaseModel):
    organization_id: str = Field(default="default", min_length=1)
    updated_by: str = Field(default="system", min_length=1)
    enabled: bool = True
    config: Dict[str, Any] = {}


def _resolve_org_id(user: TokenData, requested_org: str) -> str:
    org = str(requested_org or user.organization_id or "default").strip() or "default"
    if str(user.role or "").strip().lower() in _ADMIN_ROLES:
        return org
    if org != str(user.organization_id or "").strip():
        raise HTTPException(status_code=403, detail="org_mismatch")
    return org


def _get_effective_payload(organization_id: str, policy_name: str) -> Dict[str, Any]:
    service = get_policy_compliance(organization_id=organization_id, policy_name=policy_name)
    return {
        "policy": service.get_policy_document(),
        "effective_policies": service.describe_effective_policies(),
        "approval_automation": get_approval_automation_policy(
            organization_id=organization_id,
            policy_name=policy_name,
        ),
    }


@router.get("")
def get_ap_policy(
    organization_id: str = Query(default="default"),
    policy_name: str = Query(default=AP_POLICY_NAME),
    include_versions: bool = Query(default=False),
    versions_limit: int = Query(default=20, ge=1, le=200),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    payload = _get_effective_payload(organization_id=org_id, policy_name=policy_name)
    response: Dict[str, Any] = {
        "organization_id": org_id,
        "policy_name": policy_name,
        **payload,
    }
    if include_versions:
        response["versions"] = db.list_ap_policy_versions(
            organization_id=org_id,
            policy_name=policy_name,
            limit=versions_limit,
        )
    return response


@router.get("/{policy_name}")
def get_named_ap_policy(
    policy_name: str,
    organization_id: str = Query(default="default"),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    payload = _get_effective_payload(organization_id=org_id, policy_name=policy_name)
    return {
        "organization_id": org_id,
        "policy_name": policy_name,
        **payload,
    }


@router.put("/{policy_name}")
def upsert_ap_policy(
    policy_name: str,
    request: UpsertAPPolicyRequest,
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    service = get_policy_compliance(organization_id=org_id, policy_name=policy_name)
    parse_errors = service.validate_policy_config(request.config or {})
    if parse_errors:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "invalid_policy_document",
                "errors": parse_errors,
            },
        )

    policy = db.upsert_ap_policy_version(
        organization_id=org_id,
        policy_name=policy_name,
        config=request.config or {},
        updated_by=request.updated_by or user.user_id,
        enabled=request.enabled,
    )
    effective = _get_effective_payload(
        organization_id=org_id,
        policy_name=policy_name,
    )
    return {
        "organization_id": org_id,
        "policy_name": policy_name,
        "policy": policy,
        "effective_policies": effective["effective_policies"],
        "approval_automation": effective["approval_automation"],
    }


@router.get("/{policy_name}/versions")
def list_ap_policy_versions(
    policy_name: str,
    organization_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=500),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    return {
        "organization_id": org_id,
        "policy_name": policy_name,
        "versions": db.list_ap_policy_versions(
            organization_id=org_id,
            policy_name=policy_name,
            limit=limit,
        ),
    }


@router.get("/{policy_name}/audit")
def list_ap_policy_audit(
    policy_name: str,
    organization_id: str = Query(default="default"),
    limit: int = Query(default=100, ge=1, le=1000),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    return {
        "organization_id": org_id,
        "policy_name": policy_name,
        "events": db.list_ap_policy_audit_events(
            organization_id=org_id,
            policy_name=policy_name,
            limit=limit,
        ),
    }
