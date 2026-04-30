"""Pipeline object model API — DESIGN_THESIS.md §5.1.

First-class Pipeline, Stage, Column, SavedView, and BoxLink endpoints.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import get_current_user, require_ops_user
from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])
saved_views_router = APIRouter(prefix="/api/saved-views", tags=["saved-views"])
box_links_router = APIRouter(prefix="/api/box-links", tags=["box-links"])


# ==================== PIPELINE ENDPOINTS ====================


@router.get("")
def list_pipelines(
    organization_id: str = Query(default="default"),
    _user=Depends(get_current_user),
):
    """List all pipelines for an organization."""
    db = get_db()
    return {"pipelines": db.list_pipelines(organization_id)}


@router.get("/{slug}")
def get_pipeline(
    slug: str,
    organization_id: str = Query(default="default"),
    _user=Depends(get_current_user),
):
    """Get a pipeline with its stages and columns."""
    db = get_db()
    pipeline = db.get_pipeline(organization_id, slug)
    if not pipeline:
        raise HTTPException(status_code=404, detail="pipeline_not_found")
    return pipeline


@router.get("/{slug}/stages")
def get_pipeline_stages(
    slug: str,
    organization_id: str = Query(default="default"),
    _user=Depends(get_current_user),
):
    """Get stage definitions for a pipeline."""
    db = get_db()
    pipeline = db.get_pipeline(organization_id, slug)
    if not pipeline:
        raise HTTPException(status_code=404, detail="pipeline_not_found")
    return {"stages": pipeline.get("stages", [])}


@router.get("/{slug}/stages/{stage_slug}/boxes")
def list_boxes_in_stage(
    slug: str,
    stage_slug: str,
    organization_id: str = Query(default="default"),
    entity_id: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=1000),
    _user=Depends(get_current_user),
):
    """List Boxes (items) in a specific pipeline stage."""
    db = get_db()
    pipeline = db.get_pipeline(organization_id, slug)
    if not pipeline:
        raise HTTPException(status_code=404, detail="pipeline_not_found")
    boxes = db.list_boxes_in_stage(
        pipeline["id"], stage_slug, organization_id, limit=limit, entity_id=entity_id,
    )
    return {"stage": stage_slug, "boxes": boxes, "count": len(boxes)}


@router.get("/{slug}/columns")
def get_pipeline_columns(
    slug: str,
    organization_id: str = Query(default="default"),
    _user=Depends(get_current_user),
):
    """Get column definitions for a pipeline."""
    db = get_db()
    pipeline = db.get_pipeline(organization_id, slug)
    if not pipeline:
        raise HTTPException(status_code=404, detail="pipeline_not_found")
    return {"columns": pipeline.get("columns", [])}


# ==================== SAVED VIEW ENDPOINTS ====================


class CreateSavedViewRequest(BaseModel):
    organization_id: str = "default"
    pipeline_slug: str
    name: str = Field(..., min_length=1, max_length=100)
    filter_json: Dict[str, Any] = Field(default_factory=dict)
    sort_json: Dict[str, Any] = Field(default_factory=dict)
    show_in_inbox: bool = False


@saved_views_router.get("")
def list_saved_views(
    organization_id: str = Query(default="default"),
    pipeline: Optional[str] = None,
    _user=Depends(get_current_user),
):
    """List saved views for an org, optionally filtered by pipeline."""
    db = get_db()
    pipeline_id = None
    if pipeline:
        pl = db.get_pipeline(organization_id, pipeline)
        if pl:
            pipeline_id = pl["id"]
    views = db.list_saved_views(organization_id, pipeline_id=pipeline_id)
    return {"saved_views": views}


@saved_views_router.post("")
def create_saved_view(
    request: CreateSavedViewRequest,
    _user=Depends(require_ops_user),
):
    """Create a new saved view.

    §13 tier comparison — "Saved Views — Show in Inbox: Starter 3
    per pipeline, Pro+ Unlimited". Enforced here at creation time:
    before inserting the row, count existing views for the pipeline
    and compare against the workspace's plan tier cap. -1 is the
    unlimited sentinel (Pro/Enterprise).
    """
    db = get_db()
    pipeline = db.get_pipeline(request.organization_id, request.pipeline_slug)
    if not pipeline:
        raise HTTPException(status_code=404, detail="pipeline_not_found")

    # Quota enforcement — block when the Starter cap would be
    # exceeded. Pro+ returns unlimited and passes through.
    # Default (seeded) saved views — Exceptions, Awaiting Approval,
    # Due This Week — are part of the product experience on every
    # tier and don't count against the user-quota. Only user-created
    # (is_default=0) views are subject to the Starter cap.
    try:
        from clearledgr.services.subscription import get_subscription_service
        sub_svc = get_subscription_service()
        existing = db.list_saved_views(request.organization_id, pipeline_id=pipeline["id"])
        user_created_count = sum(
            1 for v in (existing or []) if not v.get("is_default")
        )
        check = sub_svc.check_limit(
            request.organization_id,
            "saved_views_per_pipeline",
            current_value=user_created_count,
        )
        if not check.get("unlimited") and not check.get("allowed"):
            raise HTTPException(
                status_code=402,  # Payment Required — signals tier limit, not auth failure
                detail={
                    "error": "saved_view_limit_reached",
                    "limit": check.get("limit"),
                    "current": check.get("current"),
                    "reason": (
                        f"Your plan allows {check.get('limit')} saved views per "
                        f"pipeline. Upgrade to Professional for unlimited saved views."
                    ),
                },
            )
    except HTTPException:
        raise
    except Exception as exc:
        # Fail-open on subscription-service errors so a billing outage
        # doesn't block operator workflow. The limit will re-enforce
        # on the next create attempt.
        logger.debug("[saved_views] tier-limit check skipped (non-fatal): %s", exc)

    actor = getattr(_user, "email", None) or getattr(_user, "user_id", "system")
    view = db.create_saved_view(
        organization_id=request.organization_id,
        pipeline_id=pipeline["id"],
        name=request.name,
        filter_json=request.filter_json,
        sort_json=request.sort_json,
        show_in_inbox=request.show_in_inbox,
        created_by=actor,
    )
    return view


@saved_views_router.delete("/{view_id}")
def delete_saved_view(
    view_id: str,
    _user=Depends(require_ops_user),
):
    """Delete a saved view (default views cannot be deleted)."""
    db = get_db()
    deleted = db.delete_saved_view(view_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="view_not_found_or_is_default")
    return {"status": "deleted"}


# ==================== BOX LINK ENDPOINTS ====================


class CreateBoxLinkRequest(BaseModel):
    source_box_id: str
    source_box_type: str
    target_box_id: str
    target_box_type: str
    link_type: str = "related"


@box_links_router.post("")
def create_box_link(
    request: CreateBoxLinkRequest,
    _user=Depends(require_ops_user),
):
    """Link two Boxes together (e.g. invoice ↔ vendor onboarding)."""
    db = get_db()
    link = db.link_boxes(
        request.source_box_id, request.source_box_type,
        request.target_box_id, request.target_box_type,
        request.link_type,
    )
    return link


@box_links_router.get("")
def get_box_links(
    box_id: str = Query(...),
    box_type: str = Query(default="invoice"),
    _user=Depends(get_current_user),
):
    """Get all links for a Box."""
    db = get_db()
    links = db.get_box_links(box_id, box_type)
    return {"links": links}
