"""Operational health endpoints for AP v1 tenants."""
from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from clearledgr.core.auth import TokenData, get_current_user
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


router = APIRouter(
    prefix="/api/ops",
    tags=["ops"],
    dependencies=[Depends(get_current_user)],
)


_OPS_ADMIN_ROLES = {"admin", "owner"}


def _assert_org_access(user: TokenData, organization_id: str) -> None:
    if user.role in _OPS_ADMIN_ROLES:
        return
    if str(organization_id or "default") != str(user.organization_id):
        raise HTTPException(status_code=403, detail="org_mismatch")


def _require_admin(user: TokenData) -> None:
    if user.role not in _OPS_ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="admin_required")


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
async def get_tenant_health(
    organization_id: str = Query("default"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    _assert_org_access(user, organization_id)
    db = get_db()
    metrics = db.get_operational_metrics(
        organization_id,
        approval_sla_minutes=_approval_sla_minutes(),
        workflow_stuck_minutes=_workflow_stuck_minutes(),
    )
    return {"health": metrics}


@router.get("/ap-kpis")
async def get_ap_kpis(
    organization_id: str = Query("default"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    _assert_org_access(user, organization_id)
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
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    _assert_org_access(user, organization_id)
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
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    _assert_org_access(user, organization_id)
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
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    _assert_org_access(user, organization_id)
    db = get_db()
    metrics = db.get_browser_agent_metrics(
        organization_id=organization_id,
        window_hours=window_hours,
    )
    return {"metrics": metrics}


@router.get("/erp-routing-strategy")
async def get_erp_routing_strategy(
    organization_id: str = Query("default"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    _assert_org_access(user, organization_id)
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
async def get_all_tenant_health(
    user: TokenData = Depends(get_current_user),
) -> Dict[str, List[Dict[str, Any]]]:
    _require_admin(user)
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
async def get_autopilot_status(
    request: Request,
    _user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
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
    # Surface agent orchestrator runtime truth-in-claims so the Gmail sidebar and
    # ops tools do not imply durable autonomy/retries when only in-memory retry
    # behavior is available.
    try:
        from clearledgr.services.agent_orchestrator import get_orchestrator

        orchestrator = get_orchestrator(getattr(_user, "organization_id", "default") or "default")
        payload["agent_runtime"] = orchestrator.runtime_status()
    except Exception as exc:  # pragma: no cover - best effort diagnostics only
        payload["agent_runtime"] = {
            "available": False,
            "error": str(exc),
        }
    if temporal_blocked and not payload.get("error"):
        payload["error"] = "temporal_unavailable"
        payload["detail"] = "temporal_required_unavailable"
    return {"autopilot": payload}


@router.get("/extraction-quality")
async def get_extraction_quality(
    organization_id: str = Query("default"),
    window_hours: int = Query(default=168, ge=1, le=8760, description="Look-back window in hours (default 7 days)"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return extraction correction rate for a time window.

    Queries ``audit_events`` for ``correction_applied`` events (written by
    ``correction_learning.py`` when an operator corrects an extracted field).
    Also counts the total AP items created in the same window to derive a
    meaningful correction rate.

    Required by PLAN.md §8.2 (extraction correction rate metric).
    """
    _assert_org_access(user, organization_id)
    db = get_db()

    from datetime import timedelta

    cutoff = (
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        - timedelta(hours=window_hours)
    ).isoformat()

    # Query correction events in window
    correction_event_types = {"correction_applied", "field_correction", "extraction_correction"}
    corrections: List[Dict[str, Any]] = []
    corrected_fields: Dict[str, int] = {}

    if hasattr(db, "list_audit_events_by_type"):
        for evt_type in correction_event_types:
            rows = db.list_audit_events_by_type(organization_id, evt_type, since=cutoff)
            corrections.extend(rows or [])
    elif hasattr(db, "connect"):
        # Fallback: direct query
        sql = db._prepare_sql(
            "SELECT * FROM audit_events WHERE organization_id = ? "
            "AND event_type IN ('correction_applied','field_correction','extraction_correction') "
            "AND ts >= ? ORDER BY ts DESC LIMIT 5000"
        )
        try:
            with db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, cutoff))
                corrections = [dict(row) for row in cur.fetchall()]
        except Exception:
            corrections = []

    # Extract which fields were corrected from payload_json
    import json as _json
    for evt in corrections:
        try:
            payload = evt.get("payload_json") or {}
            if isinstance(payload, str):
                payload = _json.loads(payload)
            field = str(payload.get("field") or payload.get("corrected_field") or "unknown")
            corrected_fields[field] = corrected_fields.get(field, 0) + 1
        except Exception:
            pass

    # Total AP items created in window for rate denominator
    total_items = 0
    if hasattr(db, "count_ap_items_since"):
        total_items = db.count_ap_items_since(organization_id, cutoff)
    else:
        try:
            sql2 = db._prepare_sql(
                "SELECT COUNT(*) as cnt FROM ap_items WHERE organization_id = ? AND created_at >= ?"
            )
            with db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql2, (organization_id, cutoff))
                row = cur.fetchone()
                total_items = int((dict(row) if row else {}).get("cnt") or 0)
        except Exception:
            total_items = 0

    correction_count = len(corrections)
    correction_rate_pct = round(
        (correction_count / total_items * 100) if total_items > 0 else 0.0, 2
    )

    return {
        "organization_id": organization_id,
        "window_hours": window_hours,
        "total_items_in_window": total_items,
        "correction_count": correction_count,
        "correction_rate_pct": correction_rate_pct,
        "corrected_fields": corrected_fields,
        "note": (
            "correction_rate_pct = corrections / total_items_in_window * 100. "
            "A rate above 10% warrants extraction model review."
        ),
    }
