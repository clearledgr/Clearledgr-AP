"""Policy versioning + replay endpoints (Gap 2).

Backed by :mod:`clearledgr.services.policy_service`. Five policy
kinds are versioned:

* ``approval_thresholds``
* ``gl_account_map``
* ``confidence_gate``
* ``autonomy_policy``
* ``vendor_master_gate``

Endpoints:

* ``GET /api/policies/{kind}/active?organization_id=...`` — current version.
* ``GET /api/policies/{kind}/versions?organization_id=...&limit=...`` — version history.
* ``GET /api/policies/versions/{version_id}?organization_id=...`` — one version.
* ``POST /api/policies/{kind}/versions`` — create a new version (request
  body carries the content + description).
* ``POST /api/policies/versions/{version_id}/rollback`` — create a new
  version copying the historical content.
* ``POST /api/policies/replay`` — given a version_id + date window,
  return per-AP-item deltas. The novel piece: lets a finance team
  ask "what would have routed differently under the old policy?"
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from clearledgr.api.deps import verify_org_access
from clearledgr.core.auth import get_current_user
from clearledgr.services.policy_service import (
    PolicyKindError,
    PolicyService,
    PolicyVersionNotFound,
)

router = APIRouter(prefix="/api/policies", tags=["policies"])


def _service(organization_id: str, user) -> PolicyService:
    org = str(organization_id or "default").strip() or "default"
    verify_org_access(org, user)
    return PolicyService(organization_id=org)


def _version_to_dict(v) -> Dict[str, Any]:
    return {
        "id": v.id,
        "organization_id": v.organization_id,
        "policy_kind": v.policy_kind,
        "version_number": v.version_number,
        "content": v.content,
        "content_hash": v.content_hash,
        "created_at": v.created_at,
        "created_by": v.created_by,
        "description": v.description,
        "parent_version_id": v.parent_version_id,
        "is_rollback": v.is_rollback,
    }


# ─── Read endpoints ─────────────────────────────────────────────────


@router.get("/{kind}/active")
def get_active_policy(
    kind: str,
    organization_id: str = Query(default="default"),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    service = _service(organization_id, user)
    try:
        version = service.get_active(kind)
    except PolicyKindError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _version_to_dict(version)


@router.get("/{kind}/versions")
def list_policy_versions(
    kind: str,
    organization_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=500),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    service = _service(organization_id, user)
    try:
        versions = service.list_versions(kind, limit=limit)
    except PolicyKindError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "kind": kind,
        "organization_id": service.organization_id,
        "count": len(versions),
        "versions": [_version_to_dict(v) for v in versions],
    }


@router.get("/versions/{version_id}")
def get_policy_version(
    version_id: str,
    organization_id: str = Query(default="default"),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    service = _service(organization_id, user)
    try:
        version = service.get_version(version_id)
    except PolicyVersionNotFound:
        raise HTTPException(status_code=404, detail=f"version {version_id!r} not found")
    return _version_to_dict(version)


# ─── Write endpoints ────────────────────────────────────────────────


class PolicyVersionCreateRequest(BaseModel):
    content: Dict[str, Any]
    description: Optional[str] = ""


@router.post("/{kind}/versions")
def create_policy_version(
    kind: str,
    body: PolicyVersionCreateRequest,
    organization_id: str = Query(default="default"),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    service = _service(organization_id, user)
    actor = _actor_from_user(user)
    try:
        version = service.set_policy(
            kind=kind,
            content=body.content or {},
            actor=actor,
            description=body.description or "",
        )
    except PolicyKindError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _version_to_dict(version)


class PolicyRollbackRequest(BaseModel):
    description: Optional[str] = ""


@router.post("/versions/{version_id}/rollback")
def rollback_policy(
    version_id: str,
    body: PolicyRollbackRequest,
    organization_id: str = Query(default="default"),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    service = _service(organization_id, user)
    actor = _actor_from_user(user)
    try:
        new_version = service.rollback_to(
            version_id=version_id,
            actor=actor,
            description=body.description or "",
        )
    except PolicyVersionNotFound:
        raise HTTPException(status_code=404, detail=f"version {version_id!r} not found")
    return _version_to_dict(new_version)


# ─── Replay endpoint ────────────────────────────────────────────────


class PolicyReplayRequest(BaseModel):
    version_id: str
    since: Optional[str] = None
    until: Optional[str] = None
    limit: int = 500


@router.post("/replay")
def replay_policy(
    body: PolicyReplayRequest,
    organization_id: str = Query(default="default"),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    service = _service(organization_id, user)
    try:
        result = service.replay_against(
            version_id=body.version_id,
            since=body.since,
            until=body.until,
            limit=int(body.limit or 500),
        )
    except PolicyVersionNotFound:
        raise HTTPException(status_code=404, detail=f"version {body.version_id!r} not found")
    return {
        "target_version_id": result.target_version_id,
        "target_version_number": result.target_version_number,
        "target_kind": result.target_kind,
        "items_evaluated": result.items_evaluated,
        "summary": result.summary,
        "deltas": [
            {
                "ap_item_id": d.ap_item_id,
                "field": d.field,
                "current_value": d.current_value,
                "replayed_value": d.replayed_value,
            }
            for d in result.deltas
        ],
    }


# ─── Helpers ────────────────────────────────────────────────────────


def _actor_from_user(user) -> str:
    if user is None:
        return "anonymous"
    email = str(getattr(user, "email", "") or "").strip()
    if email:
        return email
    user_id = str(getattr(user, "user_id", "") or getattr(user, "id", "") or "").strip()
    return user_id or "system"
