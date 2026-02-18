"""Tenant AP policy APIs (versioned)."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from clearledgr.core.database import get_db
from clearledgr.services.policy_engine import normalize_ap_policy


router = APIRouter(prefix="/api/ap/policies", tags=["ap-policies"])


class UpsertAPPolicyRequest(BaseModel):
    org_id: str = Field(default="default", min_length=1)
    updated_by: str = Field(default="system", min_length=1)
    enabled: bool = True
    config: Dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def get_ap_policy(
    organization_id: str = Query("default"),
    policy_name: str = Query("ap_business_v1"),
    include_versions: bool = Query(False),
    versions_limit: int = Query(20, ge=1, le=200),
):
    db = get_db()
    policy = db.get_ap_policy(organization_id, policy_name)
    if policy:
        normalized = normalize_ap_policy(policy.get("config_json") if isinstance(policy.get("config_json"), dict) else {})
        payload = {
            "id": policy.get("id"),
            "organization_id": policy.get("organization_id"),
            "policy_name": policy.get("policy_name") or policy_name,
            "version": policy.get("version"),
            "enabled": bool(policy.get("enabled")),
            "updated_by": policy.get("updated_by"),
            "created_at": policy.get("created_at"),
            "config": normalized,
        }
    else:
        payload = {
            "id": None,
            "organization_id": organization_id,
            "policy_name": policy_name,
            "version": "env-default",
            "enabled": False,
            "updated_by": "system",
            "created_at": None,
            "config": normalize_ap_policy({}),
        }

    response: Dict[str, Any] = {"policy": payload}
    if include_versions:
        response["versions"] = db.list_ap_policy_versions(
            organization_id,
            policy_name=policy_name,
            limit=versions_limit,
        )
    return response


@router.put("/{policy_name}")
async def upsert_ap_policy(policy_name: str, request: UpsertAPPolicyRequest):
    db = get_db()
    normalized = normalize_ap_policy(request.config or {})
    policy = db.upsert_ap_policy_version(
        organization_id=request.org_id,
        policy_name=policy_name,
        config=normalized,
        updated_by=request.updated_by,
        enabled=request.enabled,
    )
    return {
        "policy": {
            "id": policy.get("id"),
            "organization_id": policy.get("organization_id"),
            "policy_name": policy.get("policy_name") or policy_name,
            "version": policy.get("version"),
            "enabled": bool(policy.get("enabled")),
            "updated_by": policy.get("updated_by"),
            "created_at": policy.get("created_at"),
            "config": normalize_ap_policy(policy.get("config_json") if isinstance(policy.get("config_json"), dict) else {}),
        }
    }
