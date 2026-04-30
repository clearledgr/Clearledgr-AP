"""Workspace dashboard read endpoints — Module 1 (Live Operations).

  GET /api/workspace/dashboard/approver-workload

The Live Operations page anchors on a few aggregations that don't
fit cleanly into either the AP-item routes (per-record) or the
reports surface (multi-day rollups). Per-approver pending counts
fall here.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services import approver_workload

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspace/dashboard", tags=["dashboard"])


@router.get("/approver-workload")
def get_approver_workload(
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Per-approver pending counts + oldest-stuck age for the
    Live Operations approver-workload strip."""
    db = get_db()
    rows = approver_workload.get_approver_workload(db, user.organization_id)
    return {
        "organization_id": user.organization_id,
        "approvers": rows,
        "count": len(rows),
    }
