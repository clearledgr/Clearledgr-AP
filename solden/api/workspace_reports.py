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
from fastapi.responses import Response

from solden.core.auth import TokenData, get_current_user
from solden.services import workspace_reports

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


# ---------------------------------------------------------------------------
# CSV export endpoints — one per report.
#
# Each ``.csv`` route runs the same generator, then serialises the
# primary view (series for trend reports, breakdown for ranking
# reports) to CSV with a UTF-8 BOM. The Content-Disposition header
# carries a stable filename hint so right-click "save link as" lands
# the operator on a sensible default.
# ---------------------------------------------------------------------------

def _csv_response(payload: Dict[str, Any]) -> Response:
    csv_text = workspace_reports.report_to_csv(payload)
    filename = workspace_reports.csv_filename(
        payload.get("report_type", "report"),
        payload.get("params", {}),
    )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _pdf_response(payload: Dict[str, Any]) -> Response:
    pdf_bytes = workspace_reports.report_to_pdf(payload)
    filename = workspace_reports.pdf_filename(
        payload.get("report_type", "report"),
        payload.get("params", {}),
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/volume.csv")
def export_volume_csv(
    period: str = Query("weekly"),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    vendor_name: Optional[str] = Query(None),
    user: TokenData = Depends(get_current_user),
) -> Response:
    payload = workspace_reports.generate_volume_report(
        organization_id=user.organization_id,
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id, vendor_name=vendor_name,
    )
    return _csv_response(payload)


@router.get("/agent-performance.csv")
def export_agent_performance_csv(
    period: str = Query("weekly"),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    user: TokenData = Depends(get_current_user),
) -> Response:
    payload = workspace_reports.generate_agent_performance_report(
        organization_id=user.organization_id,
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id,
    )
    return _csv_response(payload)


@router.get("/cycle-time.csv")
def export_cycle_time_csv(
    period: str = Query("weekly"),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    user: TokenData = Depends(get_current_user),
) -> Response:
    payload = workspace_reports.generate_cycle_time_report(
        organization_id=user.organization_id,
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id,
    )
    return _csv_response(payload)


@router.get("/exception-breakdown.csv")
def export_exception_breakdown_csv(
    period: str = Query("weekly"),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    user: TokenData = Depends(get_current_user),
) -> Response:
    payload = workspace_reports.generate_exception_breakdown_report(
        organization_id=user.organization_id,
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id,
    )
    return _csv_response(payload)


@router.get("/vendor-quality.csv")
def export_vendor_quality_csv(
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    min_invoices: int = Query(3, ge=1, le=100),
    limit: int = Query(25, ge=1, le=100),
    user: TokenData = Depends(get_current_user),
) -> Response:
    payload = workspace_reports.generate_vendor_quality_report(
        organization_id=user.organization_id,
        from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id,
        min_invoices=min_invoices,
        limit=limit,
    )
    return _csv_response(payload)


# ─── PDF exports — Module 8 spec line 276 ──────────────────────────
#
# Mirror the .csv routes above. Reuse the same generator + the new
# report_to_pdf helper. Filename hint via Content-Disposition; the
# browser handles save-as.

@router.get("/volume.pdf")
def export_volume_pdf(
    period: str = Query("weekly"),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    vendor_name: Optional[str] = Query(None),
    user: TokenData = Depends(get_current_user),
) -> Response:
    payload = workspace_reports.generate_volume_report(
        organization_id=user.organization_id,
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id, vendor_name=vendor_name,
    )
    return _pdf_response(payload)


@router.get("/agent-performance.pdf")
def export_agent_performance_pdf(
    period: str = Query("weekly"),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    user: TokenData = Depends(get_current_user),
) -> Response:
    payload = workspace_reports.generate_agent_performance_report(
        organization_id=user.organization_id,
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id,
    )
    return _pdf_response(payload)


@router.get("/cycle-time.pdf")
def export_cycle_time_pdf(
    period: str = Query("weekly"),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    user: TokenData = Depends(get_current_user),
) -> Response:
    payload = workspace_reports.generate_cycle_time_report(
        organization_id=user.organization_id,
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id,
    )
    return _pdf_response(payload)


@router.get("/exception-breakdown.pdf")
def export_exception_breakdown_pdf(
    period: str = Query("weekly"),
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    user: TokenData = Depends(get_current_user),
) -> Response:
    payload = workspace_reports.generate_exception_breakdown_report(
        organization_id=user.organization_id,
        period=period, from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id,
    )
    return _pdf_response(payload)


@router.get("/vendor-quality.pdf")
def export_vendor_quality_pdf(
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
    entity_id: Optional[str] = Query(None),
    min_invoices: int = Query(3, ge=1, le=100),
    limit: int = Query(25, ge=1, le=100),
    user: TokenData = Depends(get_current_user),
) -> Response:
    payload = workspace_reports.generate_vendor_quality_report(
        organization_id=user.organization_id,
        from_ts=from_ts, to_ts=to_ts,
        entity_id=entity_id,
        min_invoices=min_invoices,
        limit=limit,
    )
    return _pdf_response(payload)
