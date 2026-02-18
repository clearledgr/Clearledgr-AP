"""AP business policy APIs (tenant-configurable, versioned, auditable)."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.database import get_db
from clearledgr.services.policy_compliance import AP_POLICY_NAME, get_policy_compliance


router = APIRouter(prefix="/api/ap/policies", tags=["ap-policies"])


class UpsertAPPolicyRequest(BaseModel):
    organization_id: str = Field(default="default", min_length=1)
    updated_by: str = Field(default="system", min_length=1)
    enabled: bool = True
    config: Dict[str, Any] = {}


def _get_effective_payload(organization_id: str, policy_name: str) -> Dict[str, Any]:
    service = get_policy_compliance(organization_id=organization_id, policy_name=policy_name)
    return {
        "policy": service.get_policy_document(),
        "effective_policies": service.describe_effective_policies(),
    }


@router.get("")
def get_ap_policy(
    organization_id: str = Query(default="default"),
    policy_name: str = Query(default=AP_POLICY_NAME),
    include_versions: bool = Query(default=False),
    versions_limit: int = Query(default=20, ge=1, le=200),
):
    db = get_db()
    payload = _get_effective_payload(organization_id=organization_id, policy_name=policy_name)
    response: Dict[str, Any] = {
        "organization_id": organization_id,
        "policy_name": policy_name,
        **payload,
    }
    if include_versions:
        response["versions"] = db.list_ap_policy_versions(
            organization_id=organization_id,
            policy_name=policy_name,
            limit=versions_limit,
        )
    return response


@router.get("/{policy_name}")
def get_named_ap_policy(
    policy_name: str,
    organization_id: str = Query(default="default"),
):
    payload = _get_effective_payload(organization_id=organization_id, policy_name=policy_name)
    return {
        "organization_id": organization_id,
        "policy_name": policy_name,
        **payload,
    }


@router.put("/{policy_name}")
def upsert_ap_policy(
    policy_name: str,
    request: UpsertAPPolicyRequest,
):
    db = get_db()
    service = get_policy_compliance(organization_id=request.organization_id, policy_name=policy_name)
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
        organization_id=request.organization_id,
        policy_name=policy_name,
        config=request.config or {},
        updated_by=request.updated_by,
        enabled=request.enabled,
    )
    effective = _get_effective_payload(
        organization_id=request.organization_id,
        policy_name=policy_name,
    )
    return {
        "organization_id": request.organization_id,
        "policy_name": policy_name,
        "policy": policy,
        "effective_policies": effective["effective_policies"],
    }


@router.get("/{policy_name}/versions")
def list_ap_policy_versions(
    policy_name: str,
    organization_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=500),
):
    db = get_db()
    return {
        "organization_id": organization_id,
        "policy_name": policy_name,
        "versions": db.list_ap_policy_versions(
            organization_id=organization_id,
            policy_name=policy_name,
            limit=limit,
        ),
    }


@router.get("/{policy_name}/audit")
def list_ap_policy_audit(
    policy_name: str,
    organization_id: str = Query(default="default"),
    limit: int = Query(default=100, ge=1, le=1000),
):
    db = get_db()
    return {
        "organization_id": organization_id,
        "policy_name": policy_name,
        "events": db.list_ap_policy_audit_events(
            organization_id=organization_id,
            policy_name=policy_name,
            limit=limit,
        ),
    }
