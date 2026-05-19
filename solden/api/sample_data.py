"""Sample data API — Module 10 (onboarding self-serve).

  POST /api/workspace/onboarding/sample-data/load    — load curated set
  POST /api/workspace/onboarding/sample-data/clear   — delete all samples
  GET  /api/workspace/onboarding/sample-data/status  — count + helper text
  GET  /api/workspace/onboarding/sample-data/preview — list rows the
                                                       dashboard renders

Admin-gated (admin / cfo / financial_controller). Cross-tenant writes
are blocked at the SQL layer — every helper takes organization_id and
filters with it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query

from solden.core.auth import TokenData, get_current_user
from solden.core.database import get_db
from solden.services import sample_data

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace/onboarding/sample-data",
    tags=["sample-data"],
)


@router.get("/status")
def get_status(
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    count = sample_data.count_sample_data(db, user.organization_id)
    return {
        "organization_id": user.organization_id,
        "sample_count": count,
        "loaded": count > 0,
    }


@router.post("/load")
def load(
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    summary = sample_data.load_sample_data(db, user.organization_id)
    summary["organization_id"] = user.organization_id
    return summary


@router.post("/clear")
def clear(
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    summary = sample_data.clear_sample_data(db, user.organization_id)
    summary["organization_id"] = user.organization_id
    return summary


@router.get("/preview")
def preview(
    limit: int = Query(50, ge=1, le=200),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    items = sample_data.list_sample_items(db, user.organization_id, limit=limit)
    return {
        "organization_id": user.organization_id,
        "items": items,
        "count": len(items),
    }
