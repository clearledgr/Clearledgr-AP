"""Cycle-time + touchless-rate API (Wave 5 / G6).

  GET /api/workspace/metrics/cycle-time?period_start=2026-04-01&period_end=2026-04-30
      Per-period cycle-time + touchless-rate report. Used by the
      operator dashboard's headline numbers ("median 3.2 days /
      touchless 62% this month").
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.cycle_time_metrics import (
    compute_cycle_time_report,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace",
    tags=["cycle-time-metrics"],
)


@router.get("/metrics/cycle-time")
def get_cycle_time(
    period_start: str = Query(..., min_length=10),
    period_end: str = Query(..., min_length=10),
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    report = compute_cycle_time_report(
        db,
        organization_id=user.organization_id,
        period_start=period_start,
        period_end=period_end,
    )
    return report.to_dict()
