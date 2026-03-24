"""Workspace shell support API for Gmail-routed pages and setup surfaces."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.core.launch_controls import (
    get_ga_readiness,
    get_rollback_controls,
    set_ga_readiness,
    set_rollback_controls,
    summarize_ga_readiness,
)
from clearledgr.services.erp_readiness import evaluate_erp_connector_readiness
from clearledgr.services.learning_calibration import get_learning_calibration_service
from clearledgr.services.policy_compliance import AP_POLICY_NAME, get_policy_compliance
from clearledgr.services.gmail_api import generate_auth_url, get_google_oauth_config
from clearledgr.services.slack_api import SlackAPIClient, resolve_slack_runtime
from clearledgr.services.teams_api import TeamsAPIClient
from clearledgr.services.subscription import PlanTier, get_subscription_service


router = APIRouter(prefix="/api/workspace", tags=["workspace"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _utcnow().isoformat()


def _secret_key() -> str:
    from clearledgr.core.secrets import require_secret
    return require_secret("CLEARLEDGR_SECRET_KEY")


def _sign_state(payload: Dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("utf-8")
    signature = hmac.new(_secret_key().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _unsign_state(state: str) -> Dict[str, Any]:
    if "." not in state:
        raise HTTPException(status_code=400, detail="invalid_state")
    body, signature = state.split(".", 1)
    expected = hmac.new(_secret_key().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=400, detail="invalid_state_signature")
    try:
        decoded = json.loads(base64.urlsafe_b64decode(body.encode("utf-8")).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid_state_payload") from exc
    if not isinstance(decoded, dict):
        raise HTTPException(status_code=400, detail="invalid_state_payload")
    issued_at = int(decoded.get("iat") or 0)
    if issued_at and _utcnow().timestamp() - issued_at > 600:
        raise HTTPException(status_code=400, detail="expired_state")
    return decoded


def _require_admin(user: TokenData) -> None:
    if user.role not in {"admin", "owner"}:
        raise HTTPException(status_code=403, detail="admin_role_required")


def _require_ops_access(user: TokenData) -> None:
    if str(user.role or "").strip().lower() not in {"owner", "admin", "operator"}:
        raise HTTPException(status_code=403, detail="ops_role_required")


def _workspace_capabilities(role: Optional[str]) -> Dict[str, bool]:
    normalized_role = str(role or "").strip().lower()
    has_workspace_role = bool(normalized_role)
    is_admin = normalized_role in {"owner", "admin", "api"}
    is_ops = normalized_role in {"owner", "admin", "operator", "api"}

    return {
        "view_home": True,
        "view_pipeline": True,
        "view_review": has_workspace_role,
        "view_upcoming": has_workspace_role,
        "view_activity": has_workspace_role,
        "view_vendors": has_workspace_role,
        "view_templates": has_workspace_role,
        "view_connections": has_workspace_role,
        "view_rules": has_workspace_role,
        "view_team": has_workspace_role,
        "view_company": has_workspace_role,
        "view_plan": has_workspace_role,
        "view_reconciliation": has_workspace_role,
        "view_system_status": has_workspace_role,
        "view_reports": has_workspace_role,
        "view_ops_workspace": is_ops,
        "operate_records": is_ops,
        "manage_connections": is_admin,
        "manage_rules": is_admin,
        "manage_team": is_admin,
        "manage_company": is_admin,
        "manage_plan": is_admin,
        "manage_admin_pages": is_admin,
    }


def _resolve_org_id(user: TokenData, organization_id: Optional[str]) -> str:
    resolved = (organization_id or user.organization_id or "default").strip()
    if not resolved:
        resolved = "default"
    if user.role != "owner" and resolved != user.organization_id:
        raise HTTPException(status_code=403, detail="org_access_denied")
    return resolved


def _load_org_settings(org: Dict[str, Any]) -> Dict[str, Any]:
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:  # noqa: BLE001
            settings = {}
    if not isinstance(settings, dict):
        settings = {}
    return settings


def _save_org_settings(organization_id: str, settings: Dict[str, Any]) -> None:
    get_db().update_organization(organization_id, settings=settings)


def _load_user_preferences(user_row: Dict[str, Any]) -> Dict[str, Any]:
    preferences = user_row.get("preferences_json") or user_row.get("preferences") or {}
    if isinstance(preferences, str):
        try:
            preferences = json.loads(preferences)
        except Exception:  # noqa: BLE001
            preferences = {}
    if not isinstance(preferences, dict):
        preferences = {}
    return preferences


def _save_user_preferences(user_id: str, preferences: Dict[str, Any]) -> None:
    get_db().update_user_preferences(user_id, preferences=preferences)


def _deep_merge_dict(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base or {})
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _gmail_status_for_org(organization_id: str, user: TokenData) -> Dict[str, Any]:
    db = get_db()
    token = db.get_oauth_token(user.user_id, "gmail")
    if not token:
        user_ids = {str(item.get("id")) for item in db.get_users(organization_id, include_inactive=False)}
        for candidate in db.list_oauth_tokens(provider="gmail"):
            if str(candidate.get("user_id")) in user_ids:
                token = candidate
                break
    connected = bool(token)
    has_refresh_token = bool(token and str(token.get("refresh_token") or "").strip())
    durable = connected and has_refresh_token
    ap_state = db.get_gmail_autopilot_state(user.user_id) or {}
    watch_exp = ap_state.get("watch_expiration")
    watch_active = False
    if watch_exp:
        try:
            exp_ts = int(watch_exp) if str(watch_exp).isdigit() else 0
            watch_active = exp_ts > int(_utcnow().timestamp() * 1000)
        except (ValueError, TypeError):
            pass
    if watch_active:
        watch_status = "active"
    elif durable:
        watch_status = "polling"
    elif connected:
        watch_status = "reconnect_required"
    else:
        watch_status = "disconnected"
    # Gap #17: surface expiry warning when watch expires within 24 hours
    watch_expires_soon = False
    if watch_active and watch_exp:
        try:
            exp_ts_ms = int(watch_exp) if str(watch_exp).isdigit() else 0
            cutoff_ms = int((_utcnow() + timedelta(hours=24)).timestamp() * 1000)
            watch_expires_soon = 0 < exp_ts_ms < cutoff_ms
        except (ValueError, TypeError):
            pass
    status = "connected" if durable else ("reconnect_required" if connected else "disconnected")
    return {
        "name": "gmail",
        "connected": connected,
        "status": status,
        "mode": "oauth",
        "email": token.get("email") if token else None,
        "durable": durable,
        "has_refresh_token": has_refresh_token,
        "requires_reconnect": connected and not durable,
        "last_sync_at": ap_state.get("last_scan_at"),
        "watch_expiration": watch_exp,
        "watch_status": watch_status,
        "watch_expires_soon": watch_expires_soon,
        "invoices_processed": int(ap_state.get("invoices_processed") or 0),
    }


def _slack_status_for_org(organization_id: str) -> Dict[str, Any]:
    db = get_db()
    org = db.get_organization(organization_id) or {}
    integration = db.get_organization_integration(organization_id, "slack") or {}
    install = db.get_slack_installation(organization_id) or {}
    mode = (
        integration.get("mode")
        or org.get("integration_mode")
        or os.getenv("SLACK_INTEGRATION_MODE", "shared")
    )
    settings = _load_org_settings(org)
    slack_channels = settings.get("slack_channels") if isinstance(settings.get("slack_channels"), dict) else {}
    return {
        "name": "slack",
        "connected": bool(integration.get("status") == "connected" or install),
        "status": integration.get("status") or ("connected" if install else "disconnected"),
        "mode": mode,
        "team_id": install.get("team_id"),
        "team_name": install.get("team_name"),
        "approval_channel": slack_channels.get("invoices") if isinstance(slack_channels, dict) else None,
        "last_sync_at": integration.get("last_sync_at"),
    }


def _erp_status_for_org(organization_id: str) -> Dict[str, Any]:
    db = get_db()
    conns = db.get_erp_connections(organization_id)
    latest = conns[0] if conns else {}
    return {
        "name": "erp",
        "connected": bool(conns),
        "status": "connected" if conns else "disconnected",
        "connections": [
            {
                "erp_type": item.get("erp_type"),
                "base_url": item.get("base_url"),
                "last_sync_at": item.get("last_sync_at"),
                "is_active": bool(item.get("is_active", 1)),
            }
            for item in conns
        ],
        "last_sync_at": latest.get("last_sync_at"),
    }


def _teams_status_for_org(organization_id: str) -> Dict[str, Any]:
    db = get_db()
    integration = db.get_organization_integration(organization_id, "teams") or {}
    metadata = integration.get("metadata") if isinstance(integration.get("metadata"), dict) else {}
    configured_webhook = str((metadata or {}).get("webhook_url") or "").strip()
    env_webhook = str(os.getenv("TEAMS_APPROVAL_WEBHOOK_URL", "")).strip()
    webhook_url = configured_webhook or env_webhook
    return {
        "name": "teams",
        "connected": bool(webhook_url),
        "status": integration.get("status") or ("connected" if webhook_url else "disconnected"),
        "mode": integration.get("mode") or "per_org",
        "webhook_configured": bool(webhook_url),
        "webhook_url": configured_webhook,
        "managed_by": "org" if configured_webhook else ("env" if env_webhook else "none"),
        "last_sync_at": integration.get("last_sync_at"),
    }


def _build_health(organization_id: str, user: TokenData) -> Dict[str, Any]:
    db = get_db()
    org = db.ensure_organization(organization_id, organization_name=organization_id)
    settings = _load_org_settings(org)
    integrations = {
        "gmail": _gmail_status_for_org(organization_id, user),
        "slack": _slack_status_for_org(organization_id),
        "teams": _teams_status_for_org(organization_id),
        "erp": _erp_status_for_org(organization_id),
    }
    required_actions: List[Dict[str, str]] = []

    if not integrations["gmail"]["connected"]:
        required_actions.append({"code": "connect_gmail", "message": "Connect Gmail account"})
    elif integrations["gmail"].get("requires_reconnect"):
        required_actions.append({
            "code": "reconnect_gmail",
            "message": "Reconnect Gmail to restore durable background monitoring.",
            "severity": "warning",
        })
    elif integrations["gmail"].get("watch_expires_soon"):
        required_actions.append({
            "code": "renew_gmail_watch",
            "message": "Gmail push-notification watch expires within 24 hours — renew via /api/gmail/watch/renew",
            "severity": "warning",
        })
    elif integrations["gmail"].get("watch_status") not in {"active", "polling"}:
        required_actions.append({
            "code": "reactivate_gmail_watch",
            "message": "Gmail push-notification watch is not active — re-authenticate or renew the watch",
            "severity": "warning",
        })
    if not integrations["slack"]["connected"]:
        required_actions.append({"code": "connect_slack", "message": "Connect Slack workspace"})
    if not integrations["teams"]["connected"]:
        required_actions.append({"code": "connect_teams", "message": "Connect Microsoft Teams webhook"})
    if not integrations["erp"]["connected"]:
        required_actions.append({"code": "connect_erp", "message": "Connect ERP system"})

    slack_channels = settings.get("slack_channels") if isinstance(settings.get("slack_channels"), dict) else {}
    if integrations["slack"]["connected"] and not (slack_channels or {}).get("invoices"):
        required_actions.append({"code": "set_slack_channel", "message": "Set Slack approval channel"})

    slack_oauth_ready = bool(
        os.getenv("SLACK_CLIENT_ID", "").strip() and os.getenv("SLACK_CLIENT_SECRET", "").strip()
    )
    if not slack_oauth_ready:
        required_actions.append(
            {"code": "configure_slack_oauth_env", "message": "Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET"}
        )

    slack_redirect_uri = os.getenv(
        "SLACK_REDIRECT_URI",
        f"{os.getenv('API_BASE_URL', 'http://127.0.0.1:8010').rstrip('/')}/api/workspace/integrations/slack/install/callback",
    ).strip()

    return {
        "organization_id": organization_id,
        "timestamp": _now_iso(),
        "integrations": integrations,
        "diagnostics": {
            "slack_oauth_ready": slack_oauth_ready,
            "slack_redirect_uri": slack_redirect_uri,
            "workspace_shell_enabled": str(os.getenv("WORKSPACE_SHELL_ENABLED", "true")).strip().lower()
            not in {"0", "false", "no", "off"},
        },
        "required_actions": required_actions,
    }


def _metric_percent(metric: Any) -> float:
    if isinstance(metric, dict):
        raw = metric.get("value", metric.get("rate"))
    else:
        raw = metric
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if 0 <= value <= 1:
        return value * 100.0
    return value


def _metric_hours(metric: Any) -> float:
    if isinstance(metric, dict):
        raw = metric.get("avg_hours", metric.get("avg"))
    else:
        raw = metric
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _build_agentic_snapshot(kpis: Dict[str, Any]) -> Dict[str, Any]:
    payload = kpis if isinstance(kpis, dict) else {}
    agentic = payload.get("agentic_telemetry") if isinstance(payload.get("agentic_telemetry"), dict) else {}
    top_blockers = []
    rows = agentic.get("top_blocker_reasons", {}).get("top_reasons") if isinstance(agentic.get("top_blocker_reasons"), dict) else []
    if isinstance(rows, list):
        for entry in rows[:3]:
            if not isinstance(entry, dict):
                continue
            reason = str(entry.get("reason") or "").replace("_", " ").strip()
            count = int(entry.get("count") or 0)
            if reason:
                top_blockers.append(f"{reason} ({count})")
    shadow = agentic.get("shadow_decision_scoring") if isinstance(agentic.get("shadow_decision_scoring"), dict) else {}
    shadow_summary = shadow.get("summary") if isinstance(shadow.get("summary"), dict) else {}
    post_verification = agentic.get("post_action_verification") if isinstance(agentic.get("post_action_verification"), dict) else {}
    post_verification_summary = post_verification.get("summary") if isinstance(post_verification.get("summary"), dict) else {}
    return {
        "window_hours": int(agentic.get("window_hours") or 0),
        "straight_through_rate_pct": round(_metric_percent(agentic.get("straight_through_rate")), 2),
        "human_intervention_rate_pct": round(_metric_percent(agentic.get("human_intervention_rate")), 2),
        "erp_browser_fallback_rate_pct": round(_metric_percent(agentic.get("erp_browser_fallback_rate")), 2),
        "agent_suggestion_acceptance_pct": round(_metric_percent(agentic.get("agent_suggestion_acceptance")), 2),
        "manual_override_required_pct": round(_metric_percent(agentic.get("agent_actions_requiring_manual_override")), 2),
        "awaiting_approval_avg_hours": round(_metric_hours(agentic.get("awaiting_approval_time_hours")), 2),
        "shadow_action_match_pct": round(_metric_percent(shadow_summary.get("action_match_rate")), 2),
        "shadow_critical_field_match_pct": round(_metric_percent(shadow_summary.get("critical_field_match_rate")), 2),
        "shadow_disagreement_count": int(shadow_summary.get("disagreement_count") or 0),
        "shadow_scored_items": int(shadow_summary.get("scored_item_count") or 0),
        "post_verification_rate_pct": round(_metric_percent(post_verification_summary.get("verification_rate")), 2),
        "post_verification_mismatch_count": int(post_verification_summary.get("mismatch_count") or 0),
        "post_verification_attempted_count": int(post_verification_summary.get("attempted_count") or 0),
        "top_blockers": top_blockers,
    }


class SlackInstallStartRequest(BaseModel):
    organization_id: Optional[str] = None
    mode: str = Field(default="per_org", pattern="^(shared|per_org)$")
    redirect_path: str = "/"


class SlackChannelRequest(BaseModel):
    organization_id: Optional[str] = None
    channel_id: str = Field(..., min_length=2)


class SlackTestRequest(BaseModel):
    organization_id: Optional[str] = None
    channel_id: Optional[str] = None
    message: str = "Clearledgr admin test: Slack approval channel is connected."


class TeamsWebhookRequest(BaseModel):
    organization_id: Optional[str] = None
    webhook_url: str = Field(..., min_length=8, max_length=1024)


class TeamsTestRequest(BaseModel):
    organization_id: Optional[str] = None
    message: str = "Clearledgr admin test: Teams approval channel is connected."


class OnboardingStepRequest(BaseModel):
    organization_id: Optional[str] = None
    step: int = Field(..., ge=1, le=5)


class APPolicyRequest(BaseModel):
    organization_id: Optional[str] = None
    updated_by: Optional[str] = None
    enabled: bool = True
    config: Dict[str, Any] = {}


class OrgSettingsPatchRequest(BaseModel):
    organization_id: Optional[str] = None
    patch: Dict[str, Any]


class UserPreferencesPatchRequest(BaseModel):
    organization_id: Optional[str] = None
    patch: Dict[str, Any]


class TeamInviteCreateRequest(BaseModel):
    organization_id: Optional[str] = None
    email: EmailStr
    role: str = Field(default="member", pattern="^(admin|member|viewer|user)$")
    expires_in_days: int = Field(default=7, ge=1, le=30)


class ERPConnectStartRequest(BaseModel):
    organization_id: Optional[str] = None
    erp_type: str = Field(..., pattern="^(quickbooks|xero|netsuite|sap)$")


class SAPConnectSubmitRequest(BaseModel):
    organization_id: Optional[str] = None
    base_url: str = Field(..., min_length=8, max_length=512)
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)


class NetSuiteConnectSubmitRequest(BaseModel):
    organization_id: Optional[str] = None
    account_id: str = Field(..., min_length=1, max_length=128)
    consumer_key: str = Field(..., min_length=1, max_length=256)
    consumer_secret: str = Field(..., min_length=1, max_length=256)
    token_id: str = Field(..., min_length=1, max_length=256)
    token_secret: str = Field(..., min_length=1, max_length=256)


class GmailConnectStartRequest(BaseModel):
    organization_id: Optional[str] = None
    redirect_path: str = Field(default="/gmail/connected", max_length=512)


class SubscriptionPlanPatchRequest(BaseModel):
    organization_id: Optional[str] = None
    plan: str = Field(..., pattern="^(free|trial|pro|enterprise)$")


class RollbackControlsRequest(BaseModel):
    organization_id: Optional[str] = None
    updated_by: Optional[str] = None
    controls: Dict[str, Any] = {}


class GAReadinessRequest(BaseModel):
    organization_id: Optional[str] = None
    updated_by: Optional[str] = None
    evidence: Dict[str, Any] = {}


class LearningCalibrationRecomputeRequest(BaseModel):
    organization_id: Optional[str] = None
    window_days: int = Field(default=180, ge=1, le=365)
    min_feedback: int = Field(default=20, ge=1, le=1000)
    limit: int = Field(default=5000, ge=10, le=100000)
    auto_apply: bool = Field(default=False)


@router.get("/bootstrap")
def get_admin_bootstrap(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    org = db.ensure_organization(org_id, organization_name=org_id)
    org_settings = _load_org_settings(org)
    subscription = get_subscription_service().get_subscription(org_id).to_dict()
    health = _build_health(org_id, user)

    current_user = db.get_user(user.user_id) or {}
    integrations = [
        _gmail_status_for_org(org_id, user),
        _slack_status_for_org(org_id),
        _teams_status_for_org(org_id),
        _erp_status_for_org(org_id),
    ]

    onboarding = {
        "completed": bool(subscription.get("onboarding_completed")),
        "step": int(subscription.get("onboarding_step") or 0),
        "steps": [
            {"id": 1, "name": "Connect Gmail"},
            {"id": 2, "name": "Connect Slack and Teams"},
            {"id": 3, "name": "Connect ERP"},
            {"id": 4, "name": "Set approval channel"},
            {"id": 5, "name": "Review AP policy defaults"},
        ],
    }
    current_role = current_user.get("role") or user.role
    capabilities = _workspace_capabilities(current_role)

    return {
        "organization": {
            "id": org.get("id"),
            "name": org.get("name"),
            "domain": org.get("domain"),
            "integration_mode": org.get("integration_mode") or "shared",
            "settings": org_settings,
        },
        "current_user": {
            "id": current_user.get("id") or user.user_id,
            "email": current_user.get("email") or user.email,
            "name": current_user.get("name") or user.email.split("@")[0],
            "role": current_role,
            "organization_id": org_id,
            "preferences": _load_user_preferences(current_user),
            "capabilities": capabilities,
        },
        "capabilities": capabilities,
        "integrations": integrations,
        "onboarding": onboarding,
        "subscription": subscription,
        "health": health,
        "required_actions": health.get("required_actions", []),
        "dashboard": _safe_dashboard_stats(org_id),
    }


def _safe_dashboard_stats(org_id: str) -> Dict[str, Any]:
    """Load dashboard stats for bootstrap. Never fails — returns empty on error."""
    try:
        db = get_db()
        pipeline = db.get_invoice_pipeline(org_id) if hasattr(db, "get_invoice_pipeline") else {}
        from datetime import date as _date
        today = _date.today().isoformat()
        total = sum(len(v) for v in pipeline.values()) if pipeline else 0
        pending = len(pipeline.get("needs_approval", []) + pipeline.get("pending_approval", []))  if pipeline else 0
        posted = sum(1 for inv in pipeline.get("posted_to_erp", []) + pipeline.get("closed", []) if isinstance(inv, dict) and str(inv.get("created_at", "")).startswith(today)) if pipeline else 0
        rejected = sum(1 for inv in pipeline.get("rejected", []) if isinstance(inv, dict) and str(inv.get("created_at", "")).startswith(today)) if pipeline else 0
        kpis = db.get_ap_kpis(org_id, approval_sla_minutes=240) if hasattr(db, "get_ap_kpis") else {}
        agentic_snapshot = _build_agentic_snapshot(kpis)
        return {
            "total_invoices": total,
            "pending_approval": pending,
            "posted_today": posted,
            "rejected_today": rejected,
            "auto_approved_rate": round(_metric_percent((kpis or {}).get("touchless_rate")), 2),
            "avg_processing_time_hours": round(_metric_hours((kpis or {}).get("cycle_time_hours")), 2),
            "total_amount_pending": sum(float(inv.get("amount") or 0) for inv in pipeline.get("needs_approval", []) + pipeline.get("pending_approval", []) if isinstance(inv, dict)) if pipeline else 0,
            "total_amount_posted_today": 0,
            "agentic_telemetry": (kpis or {}).get("agentic_telemetry") or {},
            "agentic_snapshot": agentic_snapshot,
        }
    except Exception:
        return {}


@router.get("/integrations")
def get_admin_integrations(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    return {
        "organization_id": org_id,
        "integrations": [
            _gmail_status_for_org(org_id, user),
            _slack_status_for_org(org_id),
            _teams_status_for_org(org_id),
            _erp_status_for_org(org_id),
        ],
    }


@router.post("/integrations/gmail/connect/start")
def start_gmail_connect(
    request: GmailConnectStartRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    redirect_path = str(request.redirect_path or "/gmail/connected").strip()
    if not redirect_path.startswith("/"):
        raise HTTPException(status_code=400, detail="invalid_redirect_path")

    oauth_redirect_uri = get_google_oauth_config().get("redirect_uri")
    state = _sign_state(
        {
            "organization_id": org_id,
            "user_id": user.user_id,
            "redirect_url": redirect_path,
            "oauth_redirect_uri": oauth_redirect_uri,
            "iat": int(_utcnow().timestamp()),
            "nonce": secrets.token_urlsafe(8),
        }
    )
    try:
        auth_url = generate_auth_url(state=state)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "auth_url": auth_url,
        "state": state,
        "organization_id": org_id,
        "redirect_path": redirect_path,
    }


@router.post("/integrations/slack/install/start")
def start_slack_install(
    request: SlackInstallStartRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    client_id = os.getenv("SLACK_CLIENT_ID", "").strip()
    client_secret = os.getenv("SLACK_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="slack_oauth_not_configured")

    redirect_uri = os.getenv(
        "SLACK_REDIRECT_URI",
        f"{os.getenv('API_BASE_URL', 'http://127.0.0.1:8010').rstrip('/')}/api/workspace/integrations/slack/install/callback",
    ).strip()
    scopes = os.getenv(
        "SLACK_OAUTH_SCOPES",
        "chat:write,commands,channels:read,groups:read,users:read",
    ).strip()
    state = _sign_state(
        {
            "organization_id": org_id,
            "user_id": user.user_id,
            "mode": request.mode,
            "redirect_path": request.redirect_path,
            "nonce": secrets.token_urlsafe(8),
            "iat": int(_utcnow().timestamp()),
        }
    )
    params = {
        "client_id": client_id,
        "scope": scopes,
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
    }
    auth_url = f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"
    return {"auth_url": auth_url, "state": state, "mode": request.mode}


@router.get("/integrations/slack/install/callback")
async def slack_install_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
):
    if error:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(f"<html><body><h2>Slack connection failed</h2><p>{error}</p><p>Close this tab and try again.</p></body></html>", status_code=400)
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing_code_or_state")

    state_payload = _unsign_state(state)
    org_id = str(state_payload.get("organization_id") or "default")
    mode = str(state_payload.get("mode") or "per_org")

    client_id = os.getenv("SLACK_CLIENT_ID", "").strip()
    client_secret = os.getenv("SLACK_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv(
        "SLACK_REDIRECT_URI",
        f"{os.getenv('API_BASE_URL', 'http://127.0.0.1:8010').rstrip('/')}/api/workspace/integrations/slack/install/callback",
    ).strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="slack_oauth_not_configured")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    payload = response.json() if response.content else {}
    if response.status_code >= 400 or not payload.get("ok"):
        raise HTTPException(status_code=400, detail={"message": "slack_install_failed", "payload": payload})

    team = payload.get("team") or {}
    authed_user = payload.get("authed_user") or {}
    access_token = payload.get("access_token")
    scope_csv = payload.get("scope") or ""
    team_id = str(team.get("id") or "")
    if not team_id or not access_token:
        raise HTTPException(status_code=400, detail="invalid_slack_install_payload")

    db = get_db()
    db.ensure_organization(org_id, organization_name=org_id)
    db.upsert_slack_installation(
        organization_id=org_id,
        team_id=team_id,
        team_name=team.get("name"),
        bot_user_id=authed_user.get("id"),
        bot_token=access_token,
        scope_csv=scope_csv,
        mode=mode,
        is_active=True,
        metadata={"install_payload": payload},
    )
    db.update_organization(org_id, integration_mode=mode)
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""<!DOCTYPE html><html><head><title>Connected</title>
<style>body{font-family:system-ui;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#f8f9fa}
.card{text-align:center;padding:2rem;border-radius:8px;background:#fff;box-shadow:0 2px 8px rgba(0,0,0,.1)}
h1{color:#22c55e;margin:0 0 .5rem}</style></head>
<body><div class="card"><h1>Slack Connected</h1>
<p>You can close this tab. Use <code>/clearledgr setup</code> in Slack to continue.</p></div></body></html>""")


@router.post("/integrations/slack/channel")
def set_slack_channel(
    request: SlackChannelRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    org = db.ensure_organization(org_id, organization_name=org_id)
    settings = _load_org_settings(org)
    channels = settings.get("slack_channels") if isinstance(settings.get("slack_channels"), dict) else {}
    channels["invoices"] = request.channel_id.strip()
    settings["slack_channels"] = channels
    _save_org_settings(org_id, settings)
    db.upsert_organization_integration(
        organization_id=org_id,
        integration_type="slack",
        status="connected",
        mode=(db.get_organization(org_id) or {}).get("integration_mode") or "shared",
        metadata={"approval_channel": request.channel_id.strip()},
        last_sync_at=_now_iso(),
    )
    return {"success": True, "organization_id": org_id, "channel_id": request.channel_id.strip()}


@router.post("/integrations/slack/test")
async def test_slack_channel(
    request: SlackTestRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    runtime = resolve_slack_runtime(org_id)
    token = runtime.get("bot_token")
    if not token:
        raise HTTPException(status_code=400, detail="slack_not_connected")
    channel = request.channel_id or runtime.get("approval_channel") or "#finance-approvals"
    client = SlackAPIClient(bot_token=token)
    sent = await client.send_message(channel=channel, text=request.message)
    return {
        "success": True,
        "organization_id": org_id,
        "channel": channel,
        "mode": runtime.get("mode"),
        "message_ts": sent.ts,
    }


@router.post("/integrations/teams/webhook")
def set_teams_webhook(
    request: TeamsWebhookRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    webhook_url = str(request.webhook_url or "").strip()
    if not webhook_url.startswith("https://"):
        raise HTTPException(status_code=422, detail="invalid_teams_webhook_url")
    db = get_db()
    db.upsert_organization_integration(
        organization_id=org_id,
        integration_type="teams",
        status="connected",
        mode="per_org",
        metadata={"webhook_url": webhook_url},
        last_sync_at=_now_iso(),
    )
    return {"success": True, "organization_id": org_id}


@router.post("/integrations/teams/test")
def test_teams_webhook(
    request: TeamsTestRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    client = TeamsAPIClient.from_env(org_id)
    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "weight": "Bolder", "text": "Clearledgr Teams connectivity test"},
                        {"type": "TextBlock", "wrap": True, "text": request.message},
                    ],
                },
            }
        ],
    }
    result = client._post_json(payload)
    if result.get("status") != "sent":
        raise HTTPException(status_code=400, detail=f"teams_test_failed:{result.get('reason') or result.get('status')}")
    return {"success": True, "organization_id": org_id}


@router.get("/integrations/slack/manifest")
def slack_manifest_template(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    redirect_uri = os.getenv(
        "SLACK_REDIRECT_URI",
        f"{os.getenv('API_BASE_URL', 'http://127.0.0.1:8010').rstrip('/')}/api/workspace/integrations/slack/install/callback",
    ).strip()
    app_base = os.getenv("API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
    return {
        "organization_id": org_id,
        "manifest": {
            "display_information": {"name": "Clearledgr AP"},
            "features": {"bot_user": {"display_name": "Clearledgr AP"}},
            "oauth_config": {
                "redirect_urls": [redirect_uri],
                "scopes": {
                    "bot": os.getenv(
                        "SLACK_OAUTH_SCOPES",
                        "chat:write,commands,channels:read,groups:read,users:read",
                    ).split(",")
                },
            },
            "settings": {
                "event_subscriptions": {"request_url": f"{app_base}/slack/events"},
                "interactivity": {"is_enabled": True, "request_url": f"{app_base}/slack/interactions"},
                "slash_commands": [
                    {"command": "/clearledgr", "url": f"{app_base}/slack/commands", "description": "Clearledgr AP"}
                ],
            },
        },
    }


@router.post("/integrations/erp/connect/start")
def erp_connect_start(
    request: ERPConnectStartRequest,
    user: TokenData = Depends(get_current_user),
):
    """Start ERP connection flow. Returns auth_url for OAuth ERPs or form spec for credential-based ERPs."""
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    erp_type = request.erp_type

    if erp_type == "netsuite":
        return {
            "erp_type": "netsuite",
            "method": "form",
            "fields": [
                {"name": "account_id", "label": "Account ID", "type": "text", "placeholder": "1234567 or 1234567_SB1", "required": True},
                {"name": "consumer_key", "label": "Consumer Key", "type": "text", "required": True},
                {"name": "consumer_secret", "label": "Consumer Secret", "type": "password", "required": True},
                {"name": "token_id", "label": "Token ID", "type": "text", "required": True},
                {"name": "token_secret", "label": "Token Secret", "type": "password", "required": True},
            ],
            "submit_url": "/api/workspace/integrations/erp/connect/netsuite",
            "help_text": "In NetSuite: Setup > Company > Enable Features > SuiteCloud > Token-Based Authentication. Then create an Integration record and generate a Token.",
        }

    if erp_type == "sap":
        return {
            "erp_type": "sap",
            "method": "form",
            "fields": [
                {
                    "name": "base_url",
                    "label": "Base URL",
                    "type": "text",
                    "placeholder": "https://<tenant>.sapbydesign.com/sap/byd/odata/v1/financials",
                    "required": True,
                },
                {"name": "username", "label": "Username", "type": "text", "required": True},
                {"name": "password", "label": "Password", "type": "password", "required": True},
            ],
            "submit_url": "/api/workspace/integrations/erp/connect/sap",
            "help_text": "Use a least-privilege integration account with API access to the SAP OData base URL.",
        }

    # OAuth-based ERPs (QuickBooks, Xero)
    from clearledgr.api.erp_connections import (
        _oauth_states,
        QUICKBOOKS_CLIENT_ID, QUICKBOOKS_REDIRECT_URI, QUICKBOOKS_AUTH_URL,
        XERO_CLIENT_ID, XERO_REDIRECT_URI, XERO_AUTH_URL,
    )
    from urllib.parse import urlencode as _urlencode

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "organization_id": org_id,
        "return_url": "success_page",
        "created_at": _now_iso(),
    }

    if erp_type == "quickbooks":
        if not QUICKBOOKS_CLIENT_ID:
            raise HTTPException(status_code=500, detail="QuickBooks not configured on this server")
        params = {
            "client_id": QUICKBOOKS_CLIENT_ID,
            "redirect_uri": QUICKBOOKS_REDIRECT_URI,
            "response_type": "code",
            "scope": "com.intuit.quickbooks.accounting",
            "state": state,
        }
        return {"erp_type": "quickbooks", "method": "oauth", "auth_url": f"{QUICKBOOKS_AUTH_URL}?{_urlencode(params)}"}

    if erp_type == "xero":
        if not XERO_CLIENT_ID:
            raise HTTPException(status_code=500, detail="Xero not configured on this server")
        params = {
            "client_id": XERO_CLIENT_ID,
            "redirect_uri": XERO_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid profile email accounting.transactions accounting.contacts offline_access",
            "state": state,
        }
        return {"erp_type": "xero", "method": "oauth", "auth_url": f"{XERO_AUTH_URL}?{_urlencode(params)}"}


@router.post("/integrations/erp/connect/sap")
async def connect_sap(
    request: SAPConnectSubmitRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    base_url = str(request.base_url or "").strip().rstrip("/")
    if not base_url.startswith("https://"):
        raise HTTPException(status_code=422, detail="invalid_sap_base_url")

    credentials = base64.b64encode(f"{request.username}:{request.password}".encode("utf-8")).decode("utf-8")
    metadata_url = f"{base_url}/$metadata"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                metadata_url,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Accept": "application/xml,application/json,*/*",
                },
            )
        if response.status_code >= 400:
            raise HTTPException(status_code=400, detail=f"sap_connection_test_failed:{response.status_code}")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"sap_connection_test_failed:{exc}") from exc

    from clearledgr.integrations.erp_router import ERPConnection, set_erp_connection

    set_erp_connection(
        org_id,
        ERPConnection(
            type="sap",
            access_token=credentials,
            refresh_token="",
            base_url=base_url,
        ),
    )

    return {
        "success": True,
        "organization_id": org_id,
        "erp_type": "sap",
        "base_url": base_url,
    }


@router.post("/integrations/erp/connect/netsuite")
async def connect_netsuite(
    request: NetSuiteConnectSubmitRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)

    from clearledgr.integrations.erp_router import (
        ERPConnection,
        get_netsuite_accounts,
        set_erp_connection,
    )

    connection = ERPConnection(
        type="netsuite",
        account_id=request.account_id,
        consumer_key=request.consumer_key,
        consumer_secret=request.consumer_secret,
        token_id=request.token_id,
        token_secret=request.token_secret,
    )

    try:
        accounts = await get_netsuite_accounts(connection)
        if accounts is None:
            raise HTTPException(status_code=400, detail="netsuite_connection_test_failed")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"netsuite_connection_test_failed:{exc}") from exc

    set_erp_connection(org_id, connection)
    return {
        "success": True,
        "organization_id": org_id,
        "erp_type": "netsuite",
        "account_id": request.account_id,
        "accounts_found": len(accounts) if isinstance(accounts, list) else 0,
    }


@router.get("/onboarding/status")
def get_onboarding_status(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    sub = get_subscription_service().get_subscription(org_id)
    return {
        "organization_id": org_id,
        "onboarding_completed": sub.onboarding_completed,
        "onboarding_step": sub.onboarding_step,
    }


@router.post("/onboarding/step")
def complete_onboarding_step(
    request: OnboardingStepRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    sub = get_subscription_service().complete_onboarding_step(org_id, request.step)
    return {
        "success": True,
        "organization_id": org_id,
        "onboarding_completed": sub.onboarding_completed,
        "onboarding_step": sub.onboarding_step,
    }


@router.get("/policies/ap")
def get_ap_policy(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    policy_service = get_policy_compliance(organization_id=org_id, policy_name=AP_POLICY_NAME)
    db = get_db()
    policy = db.get_ap_policy(org_id, AP_POLICY_NAME)
    return {
        "organization_id": org_id,
        "policy_name": AP_POLICY_NAME,
        "policy": policy,
        "effective_policies": policy_service.describe_effective_policies(),
    }


@router.put("/policies/ap")
def put_ap_policy(
    request: APPolicyRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    policy_service = get_policy_compliance(organization_id=org_id, policy_name=AP_POLICY_NAME)
    errors = policy_service.validate_policy_config(request.config or {})
    if errors:
        raise HTTPException(status_code=422, detail={"message": "invalid_policy_document", "errors": errors})
    updated = db.upsert_ap_policy_version(
        organization_id=org_id,
        policy_name=AP_POLICY_NAME,
        config=request.config or {},
        updated_by=request.updated_by or user.user_id,
        enabled=bool(request.enabled),
    )
    return {"organization_id": org_id, "policy_name": AP_POLICY_NAME, "policy": updated}


@router.get("/org/settings")
def get_org_settings(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    org = db.ensure_organization(org_id, organization_name=org_id)
    return {"organization_id": org_id, "settings": _load_org_settings(org)}


@router.patch("/org/settings")
def patch_org_settings(
    request: OrgSettingsPatchRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    org = db.ensure_organization(org_id, organization_name=org_id)
    settings = _load_org_settings(org)
    patch = request.patch or {}

    org_updates: Dict[str, Any] = {}
    if "organization_name" in patch:
        org_updates["name"] = patch.get("organization_name")
    if "name" in patch:
        org_updates["name"] = patch.get("name")
    if "domain" in patch:
        org_updates["domain"] = patch.get("domain")
    if "integration_mode" in patch:
        mode = str(patch.get("integration_mode") or "").strip().lower()
        if mode not in {"shared", "per_org"}:
            raise HTTPException(status_code=422, detail="invalid_integration_mode")
        org_updates["integration_mode"] = mode

    if org_updates:
        db.update_organization(org_id, **org_updates)

    for key, value in patch.items():
        if key in {"organization_name", "name", "domain", "integration_mode"}:
            continue
        settings[key] = value
    _save_org_settings(org_id, settings)
    updated_org = db.get_organization(org_id) or {}
    return {
        "success": True,
        "organization_id": org_id,
        "organization": {
            "id": updated_org.get("id"),
            "name": updated_org.get("name"),
            "domain": updated_org.get("domain"),
            "integration_mode": updated_org.get("integration_mode") or "shared",
        },
        "settings": settings,
    }


@router.get("/user/preferences")
def get_user_preferences(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    _require_ops(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    current_user = db.get_user(user.user_id)
    if not current_user:
        raise HTTPException(status_code=404, detail="user_not_found")
    if str(current_user.get("organization_id") or org_id) != org_id:
        raise HTTPException(status_code=403, detail="org_access_denied")
    return {
        "organization_id": org_id,
        "user_id": current_user.get("id") or user.user_id,
        "preferences": _load_user_preferences(current_user),
    }


@router.patch("/user/preferences")
def patch_user_preferences(
    request: UserPreferencesPatchRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_ops(user)
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    current_user = db.get_user(user.user_id)
    if not current_user:
        raise HTTPException(status_code=404, detail="user_not_found")
    if str(current_user.get("organization_id") or org_id) != org_id:
        raise HTTPException(status_code=403, detail="org_access_denied")
    preferences = _deep_merge_dict(_load_user_preferences(current_user), request.patch or {})
    _save_user_preferences(str(current_user.get("id") or user.user_id), preferences)
    return {
        "success": True,
        "organization_id": org_id,
        "user_id": current_user.get("id") or user.user_id,
        "preferences": preferences,
    }


@router.get("/rollback-controls")
def get_admin_rollback_controls(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    controls = get_rollback_controls(org_id)
    return {"organization_id": org_id, "rollback_controls": controls}


@router.put("/rollback-controls")
def put_admin_rollback_controls(
    request: RollbackControlsRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    controls = set_rollback_controls(
        org_id,
        request.controls or {},
        updated_by=request.updated_by or user.user_id,
    )
    return {"success": True, "organization_id": org_id, "rollback_controls": controls}


@router.get("/ga-readiness")
def get_admin_ga_readiness(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    evidence = get_ga_readiness(org_id)
    rollback_controls = get_rollback_controls(org_id)
    return {
        "organization_id": org_id,
        "ga_readiness": evidence,
        "rollback_controls": rollback_controls,
        "summary": summarize_ga_readiness(evidence, rollback_controls=rollback_controls),
    }


@router.put("/ga-readiness")
def put_admin_ga_readiness(
    request: GAReadinessRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    evidence = set_ga_readiness(
        org_id,
        request.evidence or {},
        updated_by=request.updated_by or user.user_id,
    )
    rollback_controls = get_rollback_controls(org_id)
    return {
        "success": True,
        "organization_id": org_id,
        "ga_readiness": evidence,
        "rollback_controls": rollback_controls,
        "summary": summarize_ga_readiness(evidence, rollback_controls=rollback_controls),
    }


@router.get("/ops/connector-readiness")
def get_ops_connector_readiness(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    _require_ops_access(user)
    org_id = _resolve_org_id(user, organization_id)
    report = evaluate_erp_connector_readiness(org_id, db=get_db(), require_full_ga_scope=False)
    return {
        "organization_id": org_id,
        "generated_at": _now_iso(),
        "connector_readiness": report,
    }


@router.get("/ops/learning-calibration")
def get_ops_learning_calibration(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    _require_ops_access(user)
    org_id = _resolve_org_id(user, organization_id)
    service = get_learning_calibration_service(org_id, db=get_db())
    snapshot = service.get_latest_snapshot()
    return {
        "organization_id": org_id,
        "snapshot": snapshot,
    }


@router.post("/ops/learning-calibration/recompute")
def recompute_ops_learning_calibration(
    request: LearningCalibrationRecomputeRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_ops_access(user)
    org_id = _resolve_org_id(user, request.organization_id)
    service = get_learning_calibration_service(org_id, db=get_db())
    snapshot = service.recompute_snapshot(
        window_days=request.window_days,
        min_feedback=request.min_feedback,
        limit=request.limit,
        auto_apply=request.auto_apply,
    )
    return {
        "success": True,
        "organization_id": org_id,
        "snapshot": snapshot,
    }


@router.post("/vendor-intelligence/bootstrap")
def bootstrap_vendor_intelligence(
    organization_id: Optional[str] = Query(default=None),
    dry_run: bool = Query(default=False),
    limit: int = Query(default=5000, ge=1, le=50000),
    user: TokenData = Depends(get_current_user),
):
    """Populate vendor_profiles and vendor_invoice_history from existing ap_items.

    Idempotent — safe to run multiple times. Use dry_run=true to preview counts
    without writing any data.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.vendor_bootstrap import bootstrap_vendor_intelligence as _bootstrap
    result = _bootstrap(get_db(), org_id, limit=limit, dry_run=dry_run)
    return {"organization_id": org_id, **result}


@router.get("/vendor-intelligence/profiles")
def list_vendor_profiles(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """List vendor profiles for an org (intelligence accumulated by the reasoning layer)."""
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    sql = db._prepare_sql(
        "SELECT * FROM vendor_profiles WHERE organization_id = ? ORDER BY invoice_count DESC LIMIT 200"
    )
    try:
        with db.connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            cur = conn.cursor()
            cur.execute(sql, (org_id,))
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        rows = []
    return {"organization_id": org_id, "profiles": rows, "count": len(rows)}


class VendorProfilePatchRequest(BaseModel):
    organization_id: Optional[str] = None
    requires_po: Optional[bool] = None
    always_approved: Optional[bool] = None
    bank_details_changed_at: Optional[str] = None  # ISO date e.g. "2026-02-20T14:00:00Z"
    typical_gl_code: Optional[str] = None
    payment_terms: Optional[str] = None
    contract_amount: Optional[float] = None


@router.get("/vendor-intelligence/profiles/{vendor_name}")
def get_vendor_profile(
    vendor_name: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Get a single vendor profile including history summary."""
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    profile = db.get_vendor_profile(org_id, vendor_name) if hasattr(db, "get_vendor_profile") else None
    if not profile:
        raise HTTPException(status_code=404, detail="vendor_profile_not_found")
    history = db.get_vendor_invoice_history(org_id, vendor_name, limit=10) if hasattr(db, "get_vendor_invoice_history") else []
    return {
        "organization_id": org_id,
        "vendor_name": vendor_name,
        "profile": profile,
        "recent_history": history,
    }


@router.patch("/vendor-intelligence/profiles/{vendor_name}")
def patch_vendor_profile(
    vendor_name: str,
    request: VendorProfilePatchRequest,
    user: TokenData = Depends(get_current_user),
):
    """Update operator-controlled vendor profile fields.

    Lets operators manually set policy overrides (requires_po, always_approved),
    flag bank detail changes, assign a GL code, or record payment terms — without
    waiting for the reasoning layer to accumulate enough history.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    if not hasattr(db, "upsert_vendor_profile"):
        raise HTTPException(status_code=503, detail="vendor_intelligence_not_available")

    updates: Dict[str, Any] = {}
    if request.requires_po is not None:
        updates["requires_po"] = 1 if request.requires_po else 0
    if request.always_approved is not None:
        updates["always_approved"] = 1 if request.always_approved else 0
    if request.bank_details_changed_at is not None:
        updates["bank_details_changed_at"] = request.bank_details_changed_at.strip() or None
    if request.typical_gl_code is not None:
        updates["typical_gl_code"] = request.typical_gl_code.strip() or None
    if request.payment_terms is not None:
        updates["payment_terms"] = request.payment_terms.strip() or None
    if request.contract_amount is not None:
        updates["contract_amount"] = request.contract_amount

    if not updates:
        raise HTTPException(status_code=422, detail="no_fields_to_update")

    profile = db.upsert_vendor_profile(org_id, vendor_name, **updates)
    return {
        "success": True,
        "organization_id": org_id,
        "vendor_name": vendor_name,
        "profile": profile,
    }


@router.get("/team/invites")
def list_team_invites(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    invites = get_db().list_team_invites(org_id)
    base = os.getenv("APP_BASE_URL", os.getenv("API_BASE_URL", "http://127.0.0.1:8010")).rstrip("/")
    for invite in invites:
        invite["invite_link"] = f"{base}/api/auth/google/start?invite_token={invite.get('token')}"
    return {"organization_id": org_id, "invites": invites}


@router.post("/team/invites")
def create_team_invite(
    request: TeamInviteCreateRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    expires_at = (_utcnow() + timedelta(days=request.expires_in_days)).isoformat()
    db = get_db()
    invite = db.create_team_invite(
        organization_id=org_id,
        email=request.email,
        role=request.role,
        created_by=user.user_id,
        expires_at=expires_at,
    )
    base = os.getenv("APP_BASE_URL", os.getenv("API_BASE_URL", "http://127.0.0.1:8010")).rstrip("/")
    invite_link = f"{base}/api/auth/google/start?invite_token={invite.get('token')}"
    return {"success": True, "organization_id": org_id, "invite": invite, "invite_link": invite_link}


@router.post("/team/invites/{invite_id}/revoke")
def revoke_team_invite(
    invite_id: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    invite = db.get_team_invite(invite_id)
    if not invite or str(invite.get("organization_id")) != org_id:
        raise HTTPException(status_code=404, detail="invite_not_found")
    ok = db.revoke_team_invite(invite_id)
    if not ok:
        raise HTTPException(status_code=400, detail="invite_not_revoked")
    return {"success": True, "invite_id": invite_id}


@router.get("/subscription")
def get_admin_subscription(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    return {"organization_id": org_id, "subscription": get_subscription_service().get_subscription(org_id).to_dict()}


@router.patch("/subscription/plan")
def patch_subscription_plan(
    request: SubscriptionPlanPatchRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    service = get_subscription_service()
    plan = request.plan.lower().strip()
    if plan == PlanTier.FREE.value:
        sub = service.downgrade_to_free(org_id)
    elif plan == PlanTier.TRIAL.value:
        sub = service.start_trial(org_id)
    elif plan == PlanTier.PRO.value:
        sub = service.upgrade_to_pro(org_id)
    elif plan == PlanTier.ENTERPRISE.value:
        # Enterprise plan controls now; billing integration remains deferred.
        record = get_db().upsert_subscription_record(
            org_id,
            {
                "plan": PlanTier.ENTERPRISE.value,
                "status": "active",
            },
        )
        sub = service._subscription_from_row(record, org_id)
    else:
        raise HTTPException(status_code=400, detail="invalid_plan")
    return {"success": True, "organization_id": org_id, "subscription": sub.to_dict()}


@router.get("/health")
def get_admin_health(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    health = _build_health(org_id, user)
    evidence = get_ga_readiness(org_id)
    rollback_controls = get_rollback_controls(org_id)
    health["launch_controls"] = {
        "rollback_controls": rollback_controls,
        "ga_readiness_summary": summarize_ga_readiness(evidence, rollback_controls=rollback_controls),
    }
    return health
