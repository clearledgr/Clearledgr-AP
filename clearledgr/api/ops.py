"""Operational health endpoints for AP v1 tenants."""
from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import APIRouter, Query, Request

from clearledgr.core.database import get_db
from clearledgr.integrations.erp_router import get_erp_connection
from clearledgr.services.erp_connector_strategy import get_erp_connector_strategy
from clearledgr.services.gmail_api import token_store
from clearledgr.services.slack_api import SlackAPIClient
try:
    from clearledgr.services.teams_api import TeamsAPIClient
except ImportError:  # pragma: no cover - optional dependency in local/dev builds
    class TeamsAPIClient:  # type: ignore[override]
        @staticmethod
        def build_ap_kpi_digest_card(kpis: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
            return {
                "organization_id": organization_id,
                "kpis": kpis,
                "note": "teams_client_unavailable",
            }
try:
    from clearledgr.workflows.ap.client import get_ap_temporal_client
except ImportError:  # pragma: no cover - optional in reduced/local installs
    class _FallbackTemporalClient:
        enabled = False
        required = False
        temporal_available = False

    def get_ap_temporal_client() -> _FallbackTemporalClient:
        return _FallbackTemporalClient()


router = APIRouter(prefix="/api/ops", tags=["ops"])


def _build_slack_digest_text(kpis: Dict[str, Any], organization_id: str) -> str:
    builder = getattr(SlackAPIClient, "build_ap_kpi_digest_text", None)
    if callable(builder):
        return str(builder(kpis, organization_id))
    touchless = ((kpis or {}).get("touchless_rate_pct") or 0)
    exception_rate = ((kpis or {}).get("exception_rate_pct") or 0)
    return (
        f"AP KPI digest ({organization_id}): "
        f"touchless={touchless:.1f}% exception_rate={exception_rate:.1f}%"
    )


def _build_slack_digest_blocks(kpis: Dict[str, Any], organization_id: str) -> List[Dict[str, Any]]:
    builder = getattr(SlackAPIClient, "build_ap_kpi_digest_blocks", None)
    if callable(builder):
        blocks = builder(kpis, organization_id)
        if isinstance(blocks, list):
            return blocks
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _build_slack_digest_text(kpis, organization_id),
            },
        }
    ]


def _approval_sla_minutes() -> int:
    raw = os.getenv("AP_APPROVAL_SLA_MINUTES", "240")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 240


def _workflow_stuck_minutes() -> int:
    raw = os.getenv("AP_WORKFLOW_STUCK_MINUTES", "120")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 120


@router.get("/tenant-health")
async def get_tenant_health(organization_id: str = Query("default")) -> Dict[str, Any]:
    db = get_db()
    metrics = db.get_operational_metrics(
        organization_id,
        approval_sla_minutes=_approval_sla_minutes(),
        workflow_stuck_minutes=_workflow_stuck_minutes(),
    )
    return {"health": metrics}


@router.get("/ap-kpis")
async def get_ap_kpis(organization_id: str = Query("default")) -> Dict[str, Any]:
    db = get_db()
    kpis = db.get_ap_kpis(
        organization_id,
        approval_sla_minutes=_approval_sla_minutes(),
    )
    return {"kpis": kpis}


@router.get("/ap-kpis/digest")
async def get_ap_kpi_digest(
    organization_id: str = Query("default"),
    surface: str = Query("all"),
) -> Dict[str, Any]:
    db = get_db()
    kpis = db.get_ap_kpis(
        organization_id,
        approval_sla_minutes=_approval_sla_minutes(),
    )
    normalized_surface = str(surface or "all").strip().lower()
    payload: Dict[str, Any] = {"organization_id": organization_id, "kpis": kpis}
    if normalized_surface in {"all", "slack"}:
        payload["slack"] = {
            "text": _build_slack_digest_text(kpis, organization_id),
            "blocks": _build_slack_digest_blocks(kpis, organization_id),
        }
    if normalized_surface in {"all", "teams"}:
        payload["teams"] = TeamsAPIClient.build_ap_kpi_digest_card(kpis, organization_id)
    return payload


@router.get("/ap-aggregation")
async def get_ap_aggregation(
    organization_id: str = Query("default"),
    limit: int = Query(10000, ge=100, le=50000),
    vendor_limit: int = Query(10, ge=1, le=50),
) -> Dict[str, Any]:
    db = get_db()
    metrics = db.get_ap_aggregation_metrics(
        organization_id=organization_id,
        limit=limit,
        vendor_limit=vendor_limit,
    )
    return {"metrics": metrics}


@router.get("/browser-agent")
async def get_browser_agent_metrics(
    organization_id: str = Query("default"),
    window_hours: int = Query(24, ge=1, le=168),
) -> Dict[str, Any]:
    db = get_db()
    metrics = db.get_browser_agent_metrics(
        organization_id=organization_id,
        window_hours=window_hours,
    )
    return {"metrics": metrics}


@router.get("/erp-routing-strategy")
async def get_erp_routing_strategy(organization_id: str = Query("default")) -> Dict[str, Any]:
    strategy = get_erp_connector_strategy()
    connection = get_erp_connection(organization_id)
    erp_type = str((connection.type if connection else "unconfigured") or "unconfigured")
    route_plan = strategy.build_route_plan(
        erp_type=erp_type,
        connection_present=connection is not None,
    )
    return {
        "organization_id": organization_id,
        "selected_route": route_plan,
        "capability_matrix": strategy.list_capabilities(),
    }


@router.get("/tenant-health/all")
async def get_all_tenant_health() -> Dict[str, List[Dict[str, Any]]]:
    db = get_db()
    orgs = db.list_organizations_with_ap_items()
    if not orgs:
        orgs = ["default"]
    health = [
        db.get_operational_metrics(
            org_id,
            approval_sla_minutes=_approval_sla_minutes(),
            workflow_stuck_minutes=_workflow_stuck_minutes(),
        )
        for org_id in orgs
    ]
    return {"health": health}


@router.get("/autopilot-status")
async def get_autopilot_status(request: Request) -> Dict[str, Any]:
    """Return backend autopilot status for Gmail sidebar UX.

    The sidebar uses this endpoint to represent true backend autonomy and avoid
    misleading "active" states when no OAuth token exists.
    """
    autopilot = getattr(getattr(request.app, "state", None), "gmail_autopilot", None)
    status = {}
    if autopilot and hasattr(autopilot, "get_status"):
        try:
            status = autopilot.get_status() or {}
        except Exception:
            status = {}

    tokens = token_store.list_all()
    has_tokens = len(tokens) > 0
    enabled = str(os.getenv("GMAIL_AUTOPILOT_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"}
    mode = os.getenv("GMAIL_AUTOPILOT_MODE", "both").strip().lower() or "both"

    state = str(status.get("state") or "idle")
    if not enabled:
        state = "disabled"
    elif not has_tokens:
        state = "auth_required"

    temporal_client = get_ap_temporal_client()
    temporal_required = bool(temporal_client.enabled and temporal_client.required)
    temporal_available = bool(temporal_client.temporal_available)
    temporal_blocked = temporal_required and not temporal_available

    if temporal_blocked:
        state = "blocked"

    payload: Dict[str, Any] = {
        "enabled": enabled,
        "mode": mode,
        "state": state,
        "token_count": len(tokens),
        "has_tokens": has_tokens,
        "users": status.get("users", len(tokens)),
        "processed_count": status.get("processed_count", 0),
        "failed_count": status.get("failed_count", 0),
        "detail": status.get("detail"),
        "last_run": status.get("last_run"),
        "error": status.get("error"),
        "temporal_required": temporal_required,
        "temporal_available": temporal_available,
        "temporal_blocked": temporal_blocked,
    }
    if temporal_blocked and not payload.get("error"):
        payload["error"] = "temporal_unavailable"
        payload["detail"] = "temporal_required_unavailable"
    return {"autopilot": payload}
