"""Generic Box data-plane — one router for every declarative Box type.

Unlike the bespoke types (each with its own routes module), declarative types
share a single set of request-time-resolved endpoints: the ``{box_type}`` path
segment selects the type, the org's active spec is resolved per request, and
``{action}`` is mapped to a target state via the spec's ``action_states``.

    GET  /api/workspace/workflows/{box_type}                 list
    POST /api/workspace/workflows/{box_type}                 create
    GET  /api/workspace/workflows/{box_type}/{box_id}        read
    POST /api/workspace/workflows/{box_type}/{box_id}/{action}  transition

Because box_type / box_id / action are path *parameters*, four fixed route
templates cover every declared type — so the strict-profile allowlist needs
only the fixed patterns from :func:`workflow_allowlist_patterns`, regardless of
how many types a tenant defines.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from solden.core.auth import get_current_user
from solden.core.database import get_db
from solden.core.workflow_spec import (
    IllegalWorkflowTransitionError,
    resolve_spec,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspace", tags=["workflows"])


def _session_org(user: Any) -> str:
    org = str(getattr(user, "organization_id", "") or "").strip()
    if not org:
        raise HTTPException(status_code=403, detail="user_missing_organization_id")
    return org


def _actor_id(user: Any) -> str:
    return str(getattr(user, "email", "") or getattr(user, "user_id", "") or "")


def _require_spec(box_type: str, organization_id: str):
    spec = resolve_spec(box_type, organization_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="unknown_workflow_type")
    return spec


def _require_box(db: Any, box_type: str, box_id: str, organization_id: str) -> Dict[str, Any]:
    box = db.get_generic_box(box_type, box_id)
    if (
        not box
        or str(box.get("organization_id") or "") != organization_id
        or str(box.get("box_type") or "") != box_type
    ):
        raise HTTPException(status_code=404, detail="box_not_found")
    return box


class BoxCreate(BaseModel):
    box_id: str = Field("", max_length=128)
    data: Dict[str, Any] = Field(default_factory=dict)
    # Optional raw input. If the type declares llm_fields, the model extracts
    # those fields from this text and they seed the Box's data (explicit data
    # keys win over extracted ones).
    source_text: str = Field("", max_length=100_000)


class ActionBody(BaseModel):
    reason: str = Field("", max_length=2000)


@router.get("/workflows/{box_type}")
def list_boxes(box_type: str, _user=Depends(get_current_user)) -> Dict[str, Any]:
    organization_id = _session_org(_user)
    _require_spec(box_type, organization_id)
    db = get_db()
    rows = db.list_generic_boxes(box_type, organization_id)
    return {"count": len(rows), "boxes": rows}


@router.post("/workflows/{box_type}")
def create_box(box_type: str, body: BoxCreate, _user=Depends(get_current_user)) -> Dict[str, Any]:
    organization_id = _session_org(_user)
    spec = _require_spec(box_type, organization_id)

    data: Dict[str, Any] = dict(body.data or {})
    # Spec-driven extraction: if the type declares llm_fields and raw input was
    # supplied, the agent reads the declared fields from it. Explicit data keys
    # take precedence over extracted ones (caller intent wins).
    if body.source_text and getattr(spec, "llm_fields", None):
        from solden.services.box_extraction import extract_box_fields
        extracted = extract_box_fields(
            box_type, organization_id, text=body.source_text,
        )
        data = {**extracted, **data}

    db = get_db()
    payload: Dict[str, Any] = {
        "organization_id": organization_id,
        "created_by": _actor_id(_user),
        "data": data,
    }
    if body.box_id:
        payload["id"] = body.box_id
    return db.create_generic_box(box_type, payload)


@router.get("/workflows/{box_type}/{box_id}")
def get_box(box_type: str, box_id: str, _user=Depends(get_current_user)) -> Dict[str, Any]:
    organization_id = _session_org(_user)
    db = get_db()
    return _require_box(db, box_type, box_id, organization_id)


@router.post("/workflows/{box_type}/{box_id}/{action}")
def act_on_box(
    box_type: str,
    box_id: str,
    action: str,
    body: ActionBody,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    organization_id = _session_org(_user)
    db = get_db()
    box = _require_box(db, box_type, box_id, organization_id)
    # Resolve the box's PINNED spec version so an in-flight Box keeps its own
    # action vocabulary + transition graph even after a newer version is active.
    spec = resolve_spec(box_type, organization_id, box.get("spec_version"))
    if spec is None:
        raise HTTPException(status_code=404, detail="unknown_workflow_type")
    target = spec.action_states.get(action)
    if not target:
        raise HTTPException(status_code=404, detail="unknown_action")
    from solden.core.hooks.dispatcher import HookDenied
    try:
        return db.update_generic_box_state(
            box_type, box_id, target,
            actor_id=_actor_id(_user), reason=body.reason.strip(),
        )
    except IllegalWorkflowTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except HookDenied as exc:
        raise HTTPException(status_code=409, detail=f"hook_denied:{exc}")


def workflow_allowlist_patterns() -> tuple[str, ...]:
    """Strict-profile allowlist regexes for the whole workflow platform surface.

    Fixed templates only — they cover every declared type because box_type /
    box_id / action are path parameters. Folded into
    ``main.STRICT_PROFILE_ALLOWED_DYNAMIC_PATTERNS`` at construction so both the
    startup route prune and the per-request guard honor these routes.
    """
    return (
        # Authoring (control plane)
        r"^/api/workspace/workflow-specs$",
        r"^/api/workspace/workflow-specs/validate$",
        r"^/api/workspace/workflow-specs/[^/]+$",
        r"^/api/workspace/workflow-specs/[^/]+/versions/[^/]+/(activate|archive)$",
        # Boxes (data plane)
        r"^/api/workspace/workflows/[^/]+$",
        r"^/api/workspace/workflows/[^/]+/[^/]+$",
        r"^/api/workspace/workflows/[^/]+/[^/]+/[^/]+$",
    )


def mount_workflow_routers(app: Any) -> None:
    """Mount the workflow platform routers (control plane + data plane)."""
    from solden.api.workflow_spec_routes import router as workflow_spec_router
    app.include_router(workflow_spec_router)
    app.include_router(router)
