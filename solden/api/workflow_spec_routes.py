"""Workflow-spec authoring API — tenants define Box types at runtime.

Level 2 control plane. An org admin drafts a declarative :class:`WorkflowSpec`,
validates it, and activates a version; the active spec then governs every Box
of that type created in the org (via the generic box routes in
``workflow_routes.py``). Versions are immutable once created; activating a new
one archives the old, and in-flight Boxes keep the version they were created
under.

    POST /api/workspace/workflow-specs                              (admin)
    GET  /api/workspace/workflow-specs                             (member)
    POST /api/workspace/workflow-specs/validate                     (admin)
    GET  /api/workspace/workflow-specs/{box_type}                  (member)
    POST /api/workspace/workflow-specs/{box_type}/versions/{v}/activate (admin)
    POST /api/workspace/workflow-specs/{box_type}/versions/{v}/archive  (admin)

Every path here is also added to the strict-profile allowlist (see
``workflow_routes.workflow_allowlist_patterns``).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from solden.core.auth import get_current_user, require_workspace_admin
from solden.core.database import get_db
from solden.core.workflow_spec import from_json, validate_spec

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspace", tags=["workflow-specs"])


def _session_org(user: Any) -> str:
    org = str(getattr(user, "organization_id", "") or "").strip()
    if not org:
        raise HTTPException(status_code=403, detail="user_missing_organization_id")
    return org


def _actor_id(user: Any) -> str:
    return str(getattr(user, "email", "") or getattr(user, "user_id", "") or "")


class SpecBody(BaseModel):
    """A serialized WorkflowSpec (see workflow_spec.to_json shape)."""
    box_type: str = Field(..., max_length=64)
    url_slug: str = Field(..., max_length=64)
    states: List[str]
    initial_state: str
    terminal_states: List[str] = Field(default_factory=list)
    transitions: Dict[str, List[str]] = Field(default_factory=dict)
    action_states: Dict[str, str] = Field(default_factory=dict)
    fields: List[str] = Field(default_factory=list)
    exception_state: str | None = None
    policy_version: str = "v1"
    hooks: Dict[str, Any] = Field(default_factory=dict)
    conditions: Dict[str, Any] = Field(default_factory=dict)


@router.post("/workflow-specs/validate")
def validate_workflow_spec(
    body: SpecBody,
    _user=Depends(require_workspace_admin),
) -> Dict[str, Any]:
    """Validate a spec without saving it. Returns {valid, errors}."""
    errors = validate_spec(from_json(body.model_dump()))
    return {"valid": not errors, "errors": errors}


@router.post("/workflow-specs")
def create_workflow_spec(
    body: SpecBody,
    _user=Depends(require_workspace_admin),
) -> Dict[str, Any]:
    """Create the next draft version of a Box type for this org."""
    organization_id = _session_org(_user)
    db = get_db()
    try:
        return db.create_workflow_spec_draft(
            organization_id, body.model_dump(), created_by=_actor_id(_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/workflow-specs")
def list_workflow_specs(_user=Depends(get_current_user)) -> Dict[str, Any]:
    organization_id = _session_org(_user)
    db = get_db()
    rows = db.list_workflow_specs(organization_id)
    return {"count": len(rows), "workflow_specs": rows}


@router.get("/workflow-specs/{box_type}")
def get_workflow_spec(box_type: str, _user=Depends(get_current_user)) -> Dict[str, Any]:
    organization_id = _session_org(_user)
    db = get_db()
    row = db.get_workflow_spec_row(organization_id, box_type)
    if not row:
        raise HTTPException(status_code=404, detail="workflow_spec_not_found")
    return row


@router.post("/workflow-specs/{box_type}/versions/{version}/activate")
def activate_workflow_spec(
    box_type: str,
    version: int,
    _user=Depends(require_workspace_admin),
) -> Dict[str, Any]:
    organization_id = _session_org(_user)
    db = get_db()
    try:
        return db.activate_workflow_spec(
            organization_id, box_type, version, actor=_actor_id(_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/workflow-specs/{box_type}/versions/{version}/archive")
def archive_workflow_spec(
    box_type: str,
    version: int,
    _user=Depends(require_workspace_admin),
) -> Dict[str, Any]:
    organization_id = _session_org(_user)
    db = get_db()
    return db.archive_workflow_spec(
        organization_id, box_type, version, actor=_actor_id(_user),
    )
