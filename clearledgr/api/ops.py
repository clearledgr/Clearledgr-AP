"""Operational health endpoints for AP v1 tenants."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db


router = APIRouter(
    prefix="/api/ops",
    tags=["ops"],
    dependencies=[Depends(get_current_user)],
)


_OPS_ADMIN_ROLES = {"admin", "owner"}


def get_erp_connection(*args, **kwargs):
    from clearledgr.integrations.erp_router import get_erp_connection as _get_erp_connection

    return _get_erp_connection(*args, **kwargs)


def get_erp_connector_strategy():
    from clearledgr.services.erp_connector_strategy import (
        get_erp_connector_strategy as _get_erp_connector_strategy,
    )

    return _get_erp_connector_strategy()


def get_approval_automation_policy(*args, **kwargs):
    from clearledgr.services.policy_compliance import (
        get_approval_automation_policy as _get_approval_automation_policy,
    )

    return _get_approval_automation_policy(*args, **kwargs)


def _get_token_store():
    from clearledgr.services.gmail_api import token_store

    return token_store


def _slack_api_client_class():
    from clearledgr.services.slack_api import SlackAPIClient

    return SlackAPIClient


def _teams_api_client_class():
    try:
        from clearledgr.services.teams_api import TeamsAPIClient

        return TeamsAPIClient
    except ImportError:  # pragma: no cover - optional dependency in local/dev builds
        class TeamsAPIClientFallback:  # type: ignore[override]
            @staticmethod
            def build_ap_kpi_digest_card(kpis: Dict[str, Any], organization_id: str) -> Dict[str, Any]:
                return {
                    "organization_id": organization_id,
                    "kpis": kpis,
                    "note": "teams_client_unavailable",
                }

        return TeamsAPIClientFallback


def _runtime_health_snapshot() -> Dict[str, Any]:
    """Live health check for the agent runtime's actual dependencies.

    Replaces the old get_ap_temporal_client() placeholder. Checks:
      - Redis reachable (ping)
      - Celery Beat heartbeat (via Redis `celery-beat-last-tick` key)
      - Event queue streams have consumer groups

    `blocked` is True only if Redis is unreachable — without Redis, the
    event queue cannot accept events and the system cannot function.
    """
    snapshot: Dict[str, Any] = {
        "redis_reachable": False,
        "event_queue_ready": False,
        "beat_heartbeat_age_sec": None,
        "blocked": False,
        "detail": None,
    }
    try:
        import os
        import redis as _redis_lib
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        client = _redis_lib.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        snapshot["redis_reachable"] = True

        # Event queue: both streams should have the consumer group registered.
        from clearledgr.core.event_queue import STREAM_HIGH, STREAM_STANDARD, GROUP_NAME
        ok = True
        for stream in (STREAM_HIGH, STREAM_STANDARD):
            try:
                groups = client.xinfo_groups(stream) or []
                if not any(g.get("name") == GROUP_NAME for g in groups):
                    ok = False
                    break
            except Exception:
                ok = False
                break
        snapshot["event_queue_ready"] = ok

        # Beat heartbeat: Celery Beat writes a key each tick (via the
        # drain-event-stream scheduled task's monotonic timestamp).
        # If nothing has been written in 5+ minutes, Beat is likely dead.
        heartbeat_key = "clearledgr:beat:last-tick"
        try:
            raw = client.get(heartbeat_key)
            if raw is not None:
                from datetime import datetime, timezone
                last = datetime.fromisoformat(str(raw))
                now = datetime.now(timezone.utc)
                age = (now - last).total_seconds()
                snapshot["beat_heartbeat_age_sec"] = round(age, 1)
        except Exception:
            pass
    except Exception as exc:
        snapshot["blocked"] = True
        snapshot["detail"] = f"redis_unreachable: {exc}"
    return snapshot


def _assert_org_access(user: TokenData, organization_id: str) -> None:
    if user.role in _OPS_ADMIN_ROLES:
        return
    if str(organization_id or "default") != str(user.organization_id):
        raise HTTPException(status_code=403, detail="org_mismatch")


def _require_admin(user: TokenData) -> None:
    if user.role not in _OPS_ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="admin_required")


def _build_slack_digest_text(kpis: Dict[str, Any], organization_id: str) -> str:
    SlackAPIClient = _slack_api_client_class()
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
    SlackAPIClient = _slack_api_client_class()
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


def _approval_sla_minutes(organization_id: str = "default") -> int:
    try:
        reminder_hours = int(
            get_approval_automation_policy(organization_id=organization_id).get("reminder_hours") or 4
        )
    except (TypeError, ValueError):
        reminder_hours = 4
    return max(60, min(reminder_hours * 60, 10080))


def _workflow_stuck_minutes() -> int:
    raw = os.getenv("AP_WORKFLOW_STUCK_MINUTES", "120")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 120


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "true" if default else "false")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return int(default)


def _is_production_env() -> bool:
    return str(os.getenv("ENV", "dev")).strip().lower() in {"prod", "production"}


def _schedule_from_env(name: str, default_csv: str) -> List[int]:
    raw = str(os.getenv(name, default_csv)).strip()
    schedule: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            schedule.append(max(0, int(part)))
        except ValueError:
            continue
    return schedule or [int(v) for v in default_csv.split(",")]


def _execution_contract_status() -> Dict[str, Any]:
    """Runtime contract status for ops monitoring.

    The agent runtime is now a single path: DeterministicPlanningEngine →
    CoordinationEngine, per AGENT_DESIGN_SPECIFICATION.md §4 + §5. The
    old Claude tool-use loop (AgentPlanningEngine) was retired and its
    env flags (``AGENT_PLANNING_LOOP``, ``AGENT_LEGACY_FALLBACK_ON_ERROR``)
    are no longer consulted. The contract is always "deterministic
    planning with Claude called only at spec \u00a77.1 bounded points."
    """
    production_env = _is_production_env()
    return {
        "mode": "deterministic_planning",
        "production_env": production_env,
        "production_contract_enforced": production_env,
        "planning_engine": "DeterministicPlanningEngine",
        "coordination_engine": "CoordinationEngine",
        "llm_surface": "spec_7_1_bounded_actions_only",
        "warnings": [],
    }


def _resolve_runtime_surface_contract(request: Request) -> Dict[str, Any] | None:
    # Prefer the app's strict AP-v1 surface contract so diagnostics reflect
    # the effective runtime constraints reported by `main.py`.
    try:
        from main import _runtime_surface_contract  # local import avoids import-time cycle

        contract = _runtime_surface_contract()
        if isinstance(contract, dict):
            state = getattr(request.app, "state", None)
            if state is not None:
                setattr(state, "_runtime_surface_contract", contract)
            return contract
    except Exception:
        pass
    cached = getattr(getattr(request.app, "state", None), "_runtime_surface_contract", None)
    if isinstance(cached, dict):
        return cached
    return None


@router.get("/tenant-health")
async def get_tenant_health(
    organization_id: str = Query("default"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    _assert_org_access(user, organization_id)
    db = get_db()
    metrics = db.get_operational_metrics(
        organization_id,
        approval_sla_minutes=_approval_sla_minutes(organization_id),
        workflow_stuck_minutes=_workflow_stuck_minutes(),
    )
    return {"health": metrics}


@router.get("/box-health")
async def get_box_health(
    organization_id: str = Query("default"),
    limit: int = Query(default=500, ge=1, le=2000),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Drill-down: which specific Boxes are stuck, in what stage, for how long.

    Complements /tenant-health (aggregates) by listing the individual
    AP Boxes plus time-in-stage buckets and exception clusters.
    """
    _assert_org_access(user, organization_id)
    db = get_db()
    health = db.get_box_health(
        organization_id,
        stuck_threshold_minutes=_workflow_stuck_minutes(),
        approval_sla_minutes=_approval_sla_minutes(organization_id),
        limit=limit,
    )
    return {"health": health}


@router.get("/llm-cost-summary")
async def get_llm_cost_summary(
    organization_id: str = Query("default"),
    window_days: int = Query(default=30, ge=1, le=365),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """LLM token usage + cost attribution for one tenant.

    Aggregates ``llm_call_log`` rows within ``window_days``. Returns
    the total dollar spend, the action-by-action breakdown, and a
    day-by-day trend so CS can spot cost spikes and capacity plan
    against the Anthropic bill. Without this endpoint a runaway
    tenant is invisible until the monthly bill arrives.
    """
    _assert_org_access(user, organization_id)
    db = get_db()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=window_days)).isoformat()

    db.initialize()
    summary = {
        "organization_id": organization_id,
        "window_days": int(window_days),
        "window_start": cutoff,
        "generated_at": now.isoformat(),
        "total_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": 0.0,
        "error_calls": 0,
        "by_action": [],
        "by_day": [],
    }

    try:
        with db.connect() as conn:
            cur = conn.cursor()
            # Aggregates
            cur.execute(
                db._prepare_sql(
                    "SELECT COUNT(*) AS n, "
                    "       COALESCE(SUM(input_tokens), 0) AS input_tok, "
                    "       COALESCE(SUM(output_tokens), 0) AS output_tok, "
                    "       COALESCE(SUM(cost_estimate_usd), 0) AS cost, "
                    "       COALESCE(SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END), 0) AS errs "
                    "FROM llm_call_log "
                    "WHERE organization_id = ? AND created_at >= ?"
                ),
                (organization_id, cutoff),
            )
            row = cur.fetchone()
            if row is not None:
                if hasattr(row, "keys"):
                    r = dict(row)
                    summary["total_calls"] = int(r.get("n") or 0)
                    summary["total_input_tokens"] = int(r.get("input_tok") or 0)
                    summary["total_output_tokens"] = int(r.get("output_tok") or 0)
                    summary["total_cost_usd"] = round(float(r.get("cost") or 0.0), 4)
                    summary["error_calls"] = int(r.get("errs") or 0)
                else:
                    summary["total_calls"] = int(row[0] or 0)
                    summary["total_input_tokens"] = int(row[1] or 0)
                    summary["total_output_tokens"] = int(row[2] or 0)
                    summary["total_cost_usd"] = round(float(row[3] or 0.0), 4)
                    summary["error_calls"] = int(row[4] or 0)

            # Per-action breakdown
            cur.execute(
                db._prepare_sql(
                    "SELECT action, "
                    "       COUNT(*) AS n, "
                    "       COALESCE(SUM(input_tokens), 0) AS input_tok, "
                    "       COALESCE(SUM(output_tokens), 0) AS output_tok, "
                    "       COALESCE(SUM(cost_estimate_usd), 0) AS cost "
                    "FROM llm_call_log "
                    "WHERE organization_id = ? AND created_at >= ? "
                    "GROUP BY action "
                    "ORDER BY cost DESC"
                ),
                (organization_id, cutoff),
            )
            for row in cur.fetchall():
                if hasattr(row, "keys"):
                    r = dict(row)
                    summary["by_action"].append({
                        "action": r.get("action"),
                        "calls": int(r.get("n") or 0),
                        "input_tokens": int(r.get("input_tok") or 0),
                        "output_tokens": int(r.get("output_tok") or 0),
                        "cost_usd": round(float(r.get("cost") or 0.0), 4),
                    })
                else:
                    summary["by_action"].append({
                        "action": row[0],
                        "calls": int(row[1] or 0),
                        "input_tokens": int(row[2] or 0),
                        "output_tokens": int(row[3] or 0),
                        "cost_usd": round(float(row[4] or 0.0), 4),
                    })

            # Per-day trend (substr(created_at, 1, 10) = 'YYYY-MM-DD')
            cur.execute(
                db._prepare_sql(
                    "SELECT substr(created_at, 1, 10) AS day, "
                    "       COUNT(*) AS n, "
                    "       COALESCE(SUM(cost_estimate_usd), 0) AS cost "
                    "FROM llm_call_log "
                    "WHERE organization_id = ? AND created_at >= ? "
                    "GROUP BY substr(created_at, 1, 10) "
                    "ORDER BY day ASC"
                ),
                (organization_id, cutoff),
            )
            for row in cur.fetchall():
                if hasattr(row, "keys"):
                    r = dict(row)
                    summary["by_day"].append({
                        "day": r.get("day"),
                        "calls": int(r.get("n") or 0),
                        "cost_usd": round(float(r.get("cost") or 0.0), 4),
                    })
                else:
                    summary["by_day"].append({
                        "day": row[0],
                        "calls": int(row[1] or 0),
                        "cost_usd": round(float(row[2] or 0.0), 4),
                    })
    except Exception as exc:
        logger.warning("llm-cost-summary query failed: %s", exc)

    return {"summary": summary}


@router.get("/ap-kpis")
async def get_ap_kpis(
    organization_id: str = Query("default"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    _assert_org_access(user, organization_id)
    db = get_db()
    kpis = db.get_ap_kpis(
        organization_id,
        approval_sla_minutes=_approval_sla_minutes(organization_id),
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
        approval_sla_minutes=_approval_sla_minutes(organization_id),
    )
    normalized_surface = str(surface or "all").strip().lower()
    payload: Dict[str, Any] = {"organization_id": organization_id, "kpis": kpis}
    if normalized_surface in {"all", "slack"}:
        payload["slack"] = {
            "text": _build_slack_digest_text(kpis, organization_id),
            "blocks": _build_slack_digest_blocks(kpis, organization_id),
        }
    if normalized_surface in {"all", "teams"}:
        payload["teams"] = _teams_api_client_class().build_ap_kpi_digest_card(kpis, organization_id)
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
            approval_sla_minutes=_approval_sla_minutes(org_id),
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

    tokens = _get_token_store().list_all()
    has_tokens = len(tokens) > 0
    enabled = str(os.getenv("GMAIL_AUTOPILOT_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"}
    mode = os.getenv("GMAIL_AUTOPILOT_MODE", "both").strip().lower() or "both"

    state = str(status.get("state") or "idle")
    if not enabled:
        state = "disabled"
    elif not has_tokens:
        state = "auth_required"

    # Real runtime health: Redis (event queue + rate limiting + beat heartbeat)
    # and Celery workers. Replaces the legacy Temporal health fields.
    runtime_health = _runtime_health_snapshot()
    runtime_blocked = bool(runtime_health.get("blocked"))
    if runtime_blocked:
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
        "runtime_health": runtime_health,
    }
    runtime_surface_contract = _resolve_runtime_surface_contract(request)
    if isinstance(runtime_surface_contract, dict):
        payload["runtime_surface"] = runtime_surface_contract
    # Surface canonical runtime readiness and durable queue state.
    try:
        from clearledgr.services.finance_agent_runtime import get_platform_finance_runtime

        org_id = str(getattr(_user, "organization_id", "default") or "default")
        runtime = get_platform_finance_runtime(org_id)
        execution_contract = _execution_contract_status()
        enabled_by_config = _env_flag("AP_AGENT_AUTONOMOUS_RETRY_ENABLED", True)
        post_process_enabled = _env_flag("AP_AGENT_POST_PROCESS_DURABLE_ENABLED", True)
        allow_non_durable_default = not _is_production_env()
        allow_non_durable = _env_flag(
            "AP_AGENT_NON_DURABLE_RETRY_ALLOWED",
            allow_non_durable_default,
        )
        payload["agent_runtime"] = {
            "available": True,
            "mode": "finance_agent_runtime",
            "organization_id": org_id,
            "pending_retry_jobs": await runtime.resume_pending_agent_tasks(),
            "ap_skill_readiness": runtime.skill_readiness("ap_v1", window_hours=168),
            "ap_autonomy_gate": runtime.ap_autonomy_summary(window_hours=168),
            "autonomous_retry": {
                "enabled": bool(enabled_by_config),
                "durable": True,
                "mode": "durable_db_retry_queue",
                "post_process_mode": "durable_db_post_process_queue",
                "allow_non_durable": bool(allow_non_durable),
                "backoff_seconds": _schedule_from_env("AP_AGENT_RETRY_BACKOFF_SECONDS", "5,15,45"),
                "poll_interval_seconds": max(1, _env_int("AP_AGENT_RETRY_POLL_SECONDS", 5)),
                "worker_running": False,
                "reason": None if enabled_by_config else "autonomous_retry_disabled_by_config",
            },
            "post_process": {
                "enabled": bool(post_process_enabled),
                "durable": True,
                "mode": "durable_db_post_process_queue",
                "backoff_seconds": _schedule_from_env("AP_AGENT_POST_PROCESS_BACKOFF_SECONDS", "5,15,45"),
                "max_attempts": max(1, _env_int("AP_AGENT_POST_PROCESS_MAX_ATTEMPTS", 3)),
                "allow_non_durable": False,
                "worker_running": False,
                "reason": None if post_process_enabled else "post_process_disabled_by_config",
            },
            "legacy_fallback_on_planner_error": execution_contract["legacy_fallback_on_error"],
            "execution_contract": execution_contract,
        }
    except Exception as exc:  # pragma: no cover - best effort diagnostics only
        payload["agent_runtime"] = {
            "available": False,
            "mode": "finance_agent_runtime",
            "error": str(exc),
        }
    if runtime_blocked and not payload.get("error"):
        payload["error"] = "runtime_unavailable"
        payload["detail"] = runtime_health.get("detail") or "runtime_components_unavailable"
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
        except Exception as exc:
            logger.debug("Payload parse failed: %s", exc)

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

    # --- Per-field confidence breakdown from ap_items.field_confidences column ---
    # Read field_confidences from items created in the window and compute per-field
    # average confidence + high/low distribution.
    CONFIDENCE_HIGH_THRESHOLD = 0.95

    field_confidence_buckets: Dict[str, List[float]] = {}
    try:
        sql_fc = db._prepare_sql(
            "SELECT field_confidences FROM ap_items "
            "WHERE organization_id = ? AND created_at >= ? "
            "AND field_confidences IS NOT NULL LIMIT 2000"
        )
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql_fc, (organization_id, cutoff))
            for row in cur.fetchall():
                raw = (dict(row) if hasattr(row, "keys") else {"field_confidences": row[0]}).get("field_confidences")
                if not raw:
                    continue
                try:
                    fc_map = _json.loads(raw) if isinstance(raw, str) else raw
                    if not isinstance(fc_map, dict):
                        continue
                    for field_name, conf_val in fc_map.items():
                        try:
                            conf_float = float(conf_val)
                        except (TypeError, ValueError):
                            continue
                        if field_name not in field_confidence_buckets:
                            field_confidence_buckets[field_name] = []
                        field_confidence_buckets[field_name].append(conf_float)
                except Exception:
                    pass
    except Exception:
        field_confidence_buckets = {}

    by_field: Dict[str, Any] = {}
    for field_name, scores in field_confidence_buckets.items():
        if not scores:
            continue
        avg_conf = sum(scores) / len(scores)
        high_count = sum(1 for s in scores if s >= CONFIDENCE_HIGH_THRESHOLD)
        low_count = len(scores) - high_count
        corrections_for_field = corrected_fields.get(field_name, 0)
        by_field[field_name] = {
            "sample_count": len(scores),
            "avg_confidence": round(avg_conf, 4),
            "avg_confidence_pct": round(avg_conf * 100, 1),
            "high_confidence_count": high_count,
            "low_confidence_count": low_count,
            "high_confidence_pct": round(high_count / len(scores) * 100, 1),
            "correction_count": corrections_for_field,
        }

    return {
        "organization_id": organization_id,
        "window_hours": window_hours,
        "total_items_in_window": total_items,
        "correction_count": correction_count,
        "correction_rate_pct": correction_rate_pct,
        "corrected_fields": corrected_fields,
        "by_field": by_field,
        "confidence_threshold": CONFIDENCE_HIGH_THRESHOLD,
        "note": (
            "correction_rate_pct = corrections / total_items_in_window * 100. "
            "A rate above 10% warrants extraction model review. "
            "by_field requires field_confidences column populated on ap_items."
        ),
    }


# ---------------------------------------------------------------------------
# Gap #9 — Post-GA monitoring thresholds (PLAN.md §8.5)
# ---------------------------------------------------------------------------

def _threshold_pct(env_var: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(env_var, str(default))))
    except (TypeError, ValueError):
        return default


def _threshold_int(env_var: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(env_var, str(default))))
    except (TypeError, ValueError):
        return default


def _evaluate_monitoring_thresholds(
    organization_id: str,
    window_hours: int,
    db,
) -> Dict[str, Any]:
    """Core threshold evaluation logic.

    Computes four rate metrics for the given window and compares them against
    env-var-configurable thresholds.  Returns a structured dict with ``alerts``
    (list of threshold breaches) and raw ``metrics``.

    Thresholds (PLAN.md §8.5):
    - ``AP_ALERT_POST_FAILURE_RATE_PCT``  (default 20 %) — per-tenant ERP disable trigger
    - ``AP_ALERT_EXCEPTION_RATE_PCT``     (default 15 %) — connector-specific degradation
    - ``AP_ALERT_CORRECTION_RATE_PCT``    (default 10 %) — extraction model review trigger
    - ``AP_ALERT_DUPLICATE_POST_COUNT``   (default 1)    — duplicate-posting circuit breaker
    """
    from datetime import datetime, timedelta, timezone
    import json as _json

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=window_hours)).isoformat()

    # ── 1. ERP post failure rate ────────────────────────────────────────────
    attempted = 0
    failed = 0
    _post_sql = db._prepare_sql(
        "SELECT event_type FROM audit_events "
        "WHERE organization_id = ? "
        "AND event_type IN ('erp_post_attempted', 'erp_post_failed') "
        "AND ts >= ?"
    )
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(_post_sql, (organization_id, cutoff))
            for row in cur.fetchall():
                et = (dict(row) if not isinstance(row, dict) else row).get("event_type", "")
                if et == "erp_post_attempted":
                    attempted += 1
                elif et == "erp_post_failed":
                    failed += 1
    except Exception as exc:
        logger.debug("Ops query failed: %s", exc)
    post_failure_rate_pct = round((failed / attempted * 100) if attempted else 0.0, 2)

    # ── 2. Exception rate (items in exception/failed states / total active) ──
    exception_count = 0
    total_active = 0
    _state_sql = db._prepare_sql(
        "SELECT state FROM ap_items WHERE organization_id = ? AND created_at >= ?"
    )
    _exception_states = {"exception", "failed_post", "needs_info"}
    _active_states = {
        "received", "validated", "needs_approval", "approved",
        "ready_to_post", "failed_post", "needs_info", "exception",
    }
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(_state_sql, (organization_id, cutoff))
            for row in cur.fetchall():
                state = (dict(row) if not isinstance(row, dict) else row).get("state", "")
                if state in _active_states:
                    total_active += 1
                if state in _exception_states:
                    exception_count += 1
    except Exception as exc:
        logger.debug("Ops query failed: %s", exc)
    exception_rate_pct = round((exception_count / total_active * 100) if total_active else 0.0, 2)

    # ── 3. Extraction correction rate ───────────────────────────────────────
    correction_count = 0
    total_items = 0
    _corr_sql = db._prepare_sql(
        "SELECT COUNT(*) AS cnt FROM audit_events "
        "WHERE organization_id = ? "
        "AND event_type IN ('correction_applied','field_correction','extraction_correction') "
        "AND ts >= ?"
    )
    _total_sql = db._prepare_sql(
        "SELECT COUNT(*) AS cnt FROM ap_items WHERE organization_id = ? AND created_at >= ?"
    )
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(_corr_sql, (organization_id, cutoff))
            row = cur.fetchone()
            correction_count = int((dict(row) if row else {}).get("cnt") or 0)
            cur.execute(_total_sql, (organization_id, cutoff))
            row = cur.fetchone()
            total_items = int((dict(row) if row else {}).get("cnt") or 0)
    except Exception as exc:
        logger.debug("Ops query failed: %s", exc)
    correction_rate_pct = round((correction_count / total_items * 100) if total_items else 0.0, 2)

    # ── 4. Duplicate posting count ───────────────────────────────────────────
    duplicate_post_count = 0
    _dup_sql = db._prepare_sql(
        "SELECT COUNT(*) AS cnt FROM audit_events "
        "WHERE organization_id = ? "
        "AND event_type IN ('duplicate_post_detected', 'idempotency_key_collision') "
        "AND ts >= ?"
    )
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(_dup_sql, (organization_id, cutoff))
            row = cur.fetchone()
            duplicate_post_count = int((dict(row) if row else {}).get("cnt") or 0)
    except Exception as exc:
        logger.debug("Ops query failed: %s", exc)

    # ── Thresholds ──────────────────────────────────────────────────────────
    thresh_post_failure_pct = _threshold_pct("AP_ALERT_POST_FAILURE_RATE_PCT", 20.0)
    thresh_exception_pct = _threshold_pct("AP_ALERT_EXCEPTION_RATE_PCT", 15.0)
    thresh_correction_pct = _threshold_pct("AP_ALERT_CORRECTION_RATE_PCT", 10.0)
    thresh_dup_count = _threshold_int("AP_ALERT_DUPLICATE_POST_COUNT", 1)

    # ── Build alerts ─────────────────────────────────────────────────────────
    alerts: List[Dict[str, Any]] = []

    if post_failure_rate_pct >= thresh_post_failure_pct and attempted > 0:
        alerts.append({
            "type": "post_failure_rate",
            "severity": "critical",
            "current_value": post_failure_rate_pct,
            "threshold": thresh_post_failure_pct,
            "message": (
                f"ERP post failure rate {post_failure_rate_pct}% exceeds "
                f"{thresh_post_failure_pct}% threshold — consider disabling "
                f"erp_posting for {organization_id} until root cause is resolved."
            ),
            "action": "disable_erp_posting",
        })

    if exception_rate_pct >= thresh_exception_pct and total_active > 0:
        alerts.append({
            "type": "exception_rate",
            "severity": "warning",
            "current_value": exception_rate_pct,
            "threshold": thresh_exception_pct,
            "message": (
                f"Exception/failed rate {exception_rate_pct}% exceeds "
                f"{thresh_exception_pct}% threshold — investigate connector "
                f"stability for {organization_id}."
            ),
            "action": "investigate_connector",
        })

    if correction_rate_pct >= thresh_correction_pct and total_items > 0:
        alerts.append({
            "type": "correction_rate",
            "severity": "warning",
            "current_value": correction_rate_pct,
            "threshold": thresh_correction_pct,
            "message": (
                f"Extraction correction rate {correction_rate_pct}% exceeds "
                f"{thresh_correction_pct}% threshold — extraction model review "
                f"recommended for {organization_id}."
            ),
            "action": "review_extraction_model",
        })

    if duplicate_post_count >= thresh_dup_count:
        alerts.append({
            "type": "duplicate_post",
            "severity": "critical",
            "current_value": duplicate_post_count,
            "threshold": thresh_dup_count,
            "message": (
                f"{duplicate_post_count} duplicate-posting incident(s) detected "
                f"for {organization_id} — circuit breaker should be triggered."
            ),
            "action": "circuit_break_erp_posting",
        })

    return {
        "organization_id": organization_id,
        "evaluated_at": now.isoformat(),
        "window_hours": window_hours,
        "alerts": alerts,
        "alert_count": len(alerts),
        "metrics": {
            "post_failure_rate_pct": post_failure_rate_pct,
            "erp_post_attempted": attempted,
            "erp_post_failed": failed,
            "exception_rate_pct": exception_rate_pct,
            "exception_count": exception_count,
            "total_active_in_window": total_active,
            "correction_rate_pct": correction_rate_pct,
            "correction_count": correction_count,
            "total_items_in_window": total_items,
            "duplicate_post_count": duplicate_post_count,
        },
        "thresholds": {
            "post_failure_rate_pct": thresh_post_failure_pct,
            "exception_rate_pct": thresh_exception_pct,
            "correction_rate_pct": thresh_correction_pct,
            "duplicate_post_count": thresh_dup_count,
        },
    }


@router.get("/ap-decision-health")
async def get_ap_decision_health(
    organization_id: str = Query("default"),
    window_hours: int = Query(default=168, ge=1, le=8760),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """AP reasoning layer health: fallback rate, recommendation breakdown, override rate.

    A fallback_rate_pct > 0 means Claude was unavailable for some invoices and
    rule-based routing was used instead. An override_rate_pct > 0 means humans
    disagreed with Claude's recommendation.
    """
    _assert_org_access(user, organization_id)
    db = get_db()
    import json as _json
    from datetime import timedelta

    cutoff = (
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        - timedelta(hours=window_hours)
    ).isoformat()

    # Read ap_items created in window that have an ap_decision in metadata
    total_items = 0
    decisions: List[Dict[str, Any]] = []
    try:
        sql = db._prepare_sql(
            "SELECT metadata, state FROM ap_items "
            "WHERE organization_id = ? AND created_at >= ? LIMIT 10000"
        )
        with db.connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            cur = conn.cursor()
            cur.execute(sql, (organization_id, cutoff))
            for row in cur.fetchall():
                total_items += 1
                meta_raw = (dict(row) or {}).get("metadata") or {}
                try:
                    meta = meta_raw if isinstance(meta_raw, dict) else _json.loads(meta_raw)
                except Exception:
                    meta = {}
                if meta.get("ap_decision_recommendation"):
                    decisions.append({
                        "recommendation": meta.get("ap_decision_recommendation"),
                        "model": meta.get("ap_decision_model", "unknown"),
                        "state": (dict(row) or {}).get("state"),
                    })
    except Exception as exc:
        logger.debug("Ops query failed: %s", exc)

    decision_count = len(decisions)
    fallback_count = sum(1 for d in decisions if d.get("model") == "fallback")
    rec_counts: Dict[str, int] = {}
    for d in decisions:
        r = str(d.get("recommendation") or "unknown")
        rec_counts[r] = rec_counts.get(r, 0) + 1

    fallback_rate_pct = round(fallback_count / decision_count * 100, 2) if decision_count else 0.0

    # Override rate: Claude said approve but item ended up rejected (or vice versa)
    overrides = 0
    try:
        sql2 = db._prepare_sql(
            "SELECT COUNT(*) as cnt FROM audit_events "
            "WHERE organization_id = ? AND event_type = 'ap_decision_override' AND ts >= ?"
        )
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql2, (organization_id, cutoff))
            row = cur.fetchone()
            overrides = int((dict(row) if row else {}).get("cnt") or 0)
    except Exception as exc:
        logger.debug("Ops query failed: %s", exc)

    override_rate_pct = round(overrides / decision_count * 100, 2) if decision_count else 0.0

    # Alerts
    alerts: List[Dict[str, str]] = []
    if fallback_rate_pct == 100.0 and decision_count > 0:
        alerts.append({"code": "claude_fully_offline", "message": "All AP decisions used rule-based fallback — check ANTHROPIC_API_KEY"})
    elif fallback_rate_pct > 20.0:
        alerts.append({"code": "high_fallback_rate", "message": f"Claude fallback rate is {fallback_rate_pct:.0f}% — check API key and connectivity"})
    if override_rate_pct > 15.0:
        alerts.append({"code": "high_override_rate", "message": f"Human override rate is {override_rate_pct:.0f}% — Claude decisions may need prompt tuning"})

    return {
        "organization_id": organization_id,
        "window_hours": window_hours,
        "total_ap_items": total_items,
        "decisions_with_claude": decision_count,
        "fallback_count": fallback_count,
        "fallback_rate_pct": fallback_rate_pct,
        "override_count": overrides,
        "override_rate_pct": override_rate_pct,
        "recommendation_breakdown": rec_counts,
        "alerts": alerts,
    }


@router.get("/monitoring-thresholds")
async def get_monitoring_thresholds(
    organization_id: str = Query("default"),
    window_hours: int = Query(default=24, ge=1, le=168),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Evaluate post-GA monitoring thresholds and return structured alerts.

    Implements PLAN.md §8.5 observability requirements:
    - Post failure rate threshold → per-tenant ERP disable signal
    - Exception/failed rate → connector-specific degradation alert
    - Correction rate → extraction model review trigger
    - Duplicate posting count → circuit-breaker trigger

    Configure thresholds via environment variables:
    - ``AP_ALERT_POST_FAILURE_RATE_PCT``  (default 20)
    - ``AP_ALERT_EXCEPTION_RATE_PCT``     (default 15)
    - ``AP_ALERT_CORRECTION_RATE_PCT``    (default 10)
    - ``AP_ALERT_DUPLICATE_POST_COUNT``   (default 1)
    """
    _assert_org_access(user, organization_id)
    db = get_db()
    return _evaluate_monitoring_thresholds(organization_id, window_hours, db)


@router.post("/monitoring-thresholds/check")
async def check_and_alert_thresholds(
    organization_id: str = Query("default"),
    window_hours: int = Query(default=24, ge=1, le=168),
    push_slack: bool = Query(default=False, description="Push alert summary to Slack digest channel if alerts are found"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Evaluate thresholds and optionally push an alert to the Slack digest channel.

    When ``push_slack=true`` and alerts are present, posts a structured Slack
    message to the ``AP_OPS_SLACK_CHANNEL`` channel (env var, default
    ``#ap-ops-alerts``).  Designed to be called from a cron job or the durable
    retry worker loop.
    """
    _assert_org_access(user, organization_id)
    db = get_db()
    result = _evaluate_monitoring_thresholds(organization_id, window_hours, db)

    if push_slack and result["alert_count"] > 0:
        channel = os.getenv("AP_OPS_SLACK_CHANNEL", "#ap-ops-alerts")
        alerts = result["alerts"]
        lines = [f"*AP Monitoring Alert* — `{organization_id}` (last {window_hours}h)"]
        for alert in alerts:
            sev = str(alert.get("severity") or "warning").upper()
            lines.append(f"• [{sev}] {alert['message']}")
        text = "\n".join(lines)
        try:
            slack_token = os.getenv("SLACK_BOT_TOKEN", "")
            if slack_token:
                import httpx as _httpx
                _httpx.post(
                    "https://slack.com/api/chat.postMessage",
                    json={"channel": channel, "text": text, "mrkdwn": True},
                    headers={"Authorization": f"Bearer {slack_token}"},
                    timeout=10,
                )
                result["slack_notified"] = True
                result["slack_channel"] = channel
            else:
                result["slack_notified"] = False
                result["slack_note"] = "SLACK_BOT_TOKEN not configured"
        except Exception as exc:
            result["slack_notified"] = False
            result["slack_error"] = str(exc)

    return result


# ---------------------------------------------------------------------------
# Monitoring health checks — broad system health (beyond AP thresholds)
# ---------------------------------------------------------------------------

@router.get("/monitoring-health")
async def get_monitoring_health(
    organization_id: str = Query("default"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Run all monitoring health checks (dead letters, auth, autopilot, overdue, posting).

    Returns per-check status with alert flag and severity.  Broader than
    ``/monitoring-thresholds`` which focuses on AP-specific metrics only.
    """
    _assert_org_access(user, organization_id)
    from clearledgr.services.monitoring import run_monitoring_checks
    return await run_monitoring_checks(organization_id=organization_id)


# ---------------------------------------------------------------------------
# Gap #18 — Dead-letter queue ops surface (PLAN.md §8.4)
# ---------------------------------------------------------------------------

def _serialize_retry_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """Add computed backoff_state and overdue flag to a retry job row."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    retry_count = int(job.get("retry_count") or 0)
    max_retries = int(job.get("max_retries") or 3)
    next_retry_at_raw = job.get("next_retry_at")
    overdue = False
    if next_retry_at_raw:
        try:
            next_dt = datetime.fromisoformat(str(next_retry_at_raw).replace("Z", "+00:00"))
            if next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=timezone.utc)
            overdue = now > next_dt
        except (TypeError, ValueError):
            pass

    job["backoff_state"] = {
        "retry_count": retry_count,
        "max_retries": max_retries,
        "next_retry_at": next_retry_at_raw,
        "overdue": overdue,
        "exhausted": retry_count >= max_retries,
    }
    return job


@router.get("/retry-queue")
async def get_retry_queue(
    organization_id: str = Query("default"),
    status: str = Query(
        default="dead_letter",
        description="Job status filter: 'dead_letter', 'pending', 'all'",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """List stuck or dead-lettered durable retry jobs with backoff state.

    Implements PLAN.md §8.4 — dead-letter queue visibility.

    Status values:
    - ``dead_letter`` (default): permanently failed after exhausting retries.
    - ``pending``: jobs still in retry backlog (includes overdue ones).
    - ``all``: all jobs regardless of status.

    Each job includes a ``backoff_state`` object with ``retry_count``,
    ``max_retries``, ``next_retry_at``, ``overdue``, and ``exhausted`` flags.
    Use ``POST /api/ops/retry-queue/{job_id}/retry`` or ``.../skip`` for
    manual intervention.
    """
    _assert_org_access(user, organization_id)
    db = get_db()
    safe_status: Any = str(status or "dead_letter").strip().lower()
    query_status = None if safe_status == "all" else safe_status
    if not hasattr(db, "list_agent_retry_jobs"):
        return {
            "organization_id": organization_id,
            "status_filter": safe_status,
            "jobs": [],
            "total": 0,
            "note": "retry_queue_not_supported",
        }
    jobs = db.list_agent_retry_jobs(
        organization_id,
        status=query_status,
        limit=limit,
    )
    serialized = [_serialize_retry_job(j) for j in jobs]
    return {
        "organization_id": organization_id,
        "status_filter": safe_status,
        "jobs": serialized,
        "total": len(serialized),
    }


@router.post("/retry-queue/{job_id}/retry")
async def manual_retry_job(
    job_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Re-queue a dead-lettered or stuck retry job for immediate execution.

    Resets the job to ``pending`` with ``next_retry_at = now`` so the durable
    retry worker picks it up on its next cycle.  Admin or owner role required.
    """
    _require_admin(user)
    from datetime import datetime, timezone

    db = get_db()
    if not hasattr(db, "get_agent_retry_job"):
        raise HTTPException(status_code=501, detail="retry_queue_not_supported")

    job = db.get_agent_retry_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="retry_job_not_found")

    now_iso = datetime.now(timezone.utc).isoformat()
    updated = db.reschedule_agent_retry_job(
        job_id,
        next_retry_at=now_iso,
        last_error=job.get("last_error"),
        status="pending",
    )
    if not updated:
        raise HTTPException(status_code=500, detail="retry_job_update_failed")

    return {
        "job_id": job_id,
        "action": "retry",
        "status": "pending",
        "next_retry_at": now_iso,
        "previous_status": str(job.get("status") or "unknown"),
    }


@router.post("/retry-queue/{job_id}/skip")
async def skip_retry_job(
    job_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Mark a retry job as skipped so it no longer blocks the queue.

    Transitions the job to ``skipped`` (terminal state, never re-processed).
    Admin or owner role required.
    """
    _require_admin(user)
    from datetime import datetime, timezone

    db = get_db()
    if not hasattr(db, "get_agent_retry_job"):
        raise HTTPException(status_code=501, detail="retry_queue_not_supported")

    job = db.get_agent_retry_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="retry_job_not_found")

    updated = db.complete_agent_retry_job(
        job_id,
        status="skipped",
        last_error=job.get("last_error"),
        result={
            "skipped_by": str(user.user_id),
            "skipped_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    if not updated:
        raise HTTPException(status_code=500, detail="skip_job_update_failed")

    return {
        "job_id": job_id,
        "action": "skip",
        "status": "skipped",
        "previous_status": str(job.get("status") or "unknown"),
    }
