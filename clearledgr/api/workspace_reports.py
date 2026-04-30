"""Workspace reports API — Module 8.

Five fixed-scope reports the leader's dashboard pulls from. Every
endpoint is org-scoped via ``get_current_user``; the service layer
returns a stable empty payload on database failure so the frontend
never sees a 500.

  GET /api/workspace/reports/volume
  GET /api/workspace/reports/agent-performance
  GET /api/workspace/reports/cycle-time
  GET /api/workspace/reports/exception-breakdown
  GET /api/workspace/reports/vendor-quality

Common query params (where applicable):
  period      daily | weekly | monthly  (default: weekly)
  from        ISO timestamp (default: now - 90 days)
  to          ISO timestamp (default: now)
  entity_id   filter to a single entity
  vendor_name filter to a single vendor (volume only)

The endpoints intentionally do NOT support custom analytics — per
spec line 285, "no custom report builder; AI-generated insights;
customer-configurable dashboards; cross-customer benchmarking." The
five-report finite set is the contract; future reports go through a
new endpoint, not a polymorphic one.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.services import workspace_reports

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspace/reports", tags=["workspace-reports"])


@router.get("/volume")
def get_volume_report(
    period: str = Query("weekly"),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    vendor_name: Optional[str] = Query(None),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    return workspace_reports.generate_volume_report(
        organization_id=user.organization_id,
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id, vendor_name=vendor_name,
    )


@router.get("/agent-performance")
def get_agent_performance_report(
    period: str = Query("weekly"),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    return workspace_reports.generate_agent_performance_report(
        organization_id=user.organization_id,
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id,
    )


@router.get("/cycle-time")
def get_cycle_time_report(
    period: str = Query("weekly"),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    return workspace_reports.generate_cycle_time_report(
        organization_id=user.organization_id,
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id,
    )


@router.get("/exception-breakdown")
def get_exception_breakdown_report(
    period: str = Query("weekly"),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    return workspace_reports.generate_exception_breakdown_report(
        organization_id=user.organization_id,
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id,
    )


@router.get("/vendor-quality")
def get_vendor_quality_report(
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    min_invoices: int = Query(3, ge=1, le=100),
    limit: int = Query(25, ge=1, le=100),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    return workspace_reports.generate_vendor_quality_report(
        organization_id=user.organization_id,
        from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id,
        min_invoices=min_invoices,
        limit=limit,
    )
