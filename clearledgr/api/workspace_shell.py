"""Workspace shell API — admin console endpoints.

Sections:
- Lines 555-660: Pydantic request models for all workspace endpoints
- Lines 662-754: Bootstrap and integration listing
- Lines 755-1259: Integration management (Gmail, Slack, Teams, ERP connect)
- Lines 1261-1442: Organization settings, policies, onboarding, and user preferences
- Lines 1445-1556: GA readiness, rollback controls, and ops monitoring
- Lines 1559-1672: Vendor intelligence management
- Lines 1675-1764: Team management, invites, and subscription
- Lines 1767-1780: Health endpoint

TODO: Split into workspace_integrations.py, workspace_config.py, workspace_health.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

import httpx
from clearledgr.core.http_client import get_http_client
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db


router = APIRouter(prefix="/api/workspace", tags=["workspace"])

SLACK_REQUIRED_BOT_SCOPES = (
    "chat:write",
    "commands",
    "channels:read",
    "groups:read",
    "im:write",
    "users:read",
    "users:read.email",
)

SLACK_REQUIRED_USER_SCOPES: tuple[str, ...] = ()


def _public_app_base_url() -> str:
    base = str(
        os.getenv("APP_BASE_URL", os.getenv("API_BASE_URL", "http://127.0.0.1:8010")) or ""
    ).strip().rstrip("/")
    return base or "http://127.0.0.1:8010"


def _slack_redirect_uri() -> str:
    return str(
        os.getenv(
            "SLACK_REDIRECT_URI",
            f"{_public_app_base_url()}/api/workspace/integrations/slack/install/callback",
        )
        or ""
    ).strip()


def _parse_slack_scope_csv(scope_csv: Optional[str]) -> List[str]:
    return [
        str(scope or "").strip()
        for scope in str(scope_csv or "").split(",")
        if str(scope or "").strip()
    ]


def _configured_slack_oauth_scopes() -> str:
    configured = _parse_slack_scope_csv(
        os.getenv("SLACK_OAUTH_SCOPES", ",".join(SLACK_REQUIRED_BOT_SCOPES))
    )
    merged: List[str] = []
    seen = set()
    for scope in [*configured, *SLACK_REQUIRED_BOT_SCOPES]:
        token = str(scope or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        merged.append(token)
    return ",".join(merged)


def _configured_slack_user_oauth_scopes() -> str:
    configured = _parse_slack_scope_csv(
        os.getenv("SLACK_USER_OAUTH_SCOPES", ",".join(SLACK_REQUIRED_USER_SCOPES))
    )
    merged: List[str] = []
    seen = set()
    for scope in [*configured, *SLACK_REQUIRED_USER_SCOPES]:
        token = str(scope or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        merged.append(token)
    return ",".join(merged)


def _missing_required_slack_scopes(scope_csv: Optional[str], user_scope_csv: Optional[str] = None) -> List[str]:
    granted = set(_parse_slack_scope_csv(scope_csv)) | set(_parse_slack_scope_csv(user_scope_csv))
    required = [*SLACK_REQUIRED_BOT_SCOPES, *SLACK_REQUIRED_USER_SCOPES]
    return [scope for scope in required if scope not in granted]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _utcnow().isoformat()


def _secret_key() -> str:
    from clearledgr.core.secrets import require_secret
    return require_secret("CLEARLEDGR_SECRET_KEY")


def _get_ga_readiness(*args, **kwargs):
    from clearledgr.core.launch_controls import get_ga_readiness

    return get_ga_readiness(*args, **kwargs)


def _get_rollback_controls(*args, **kwargs):
    from clearledgr.core.launch_controls import get_rollback_controls

    return get_rollback_controls(*args, **kwargs)


def _set_ga_readiness(*args, **kwargs):
    from clearledgr.core.launch_controls import set_ga_readiness

    return set_ga_readiness(*args, **kwargs)


def _set_rollback_controls(*args, **kwargs):
    from clearledgr.core.launch_controls import set_rollback_controls

    return set_rollback_controls(*args, **kwargs)


def _summarize_ga_readiness(*args, **kwargs):
    from clearledgr.core.launch_controls import summarize_ga_readiness

    return summarize_ga_readiness(*args, **kwargs)


def _evaluate_erp_connector_readiness(*args, **kwargs):
    from clearledgr.services.erp_readiness import evaluate_erp_connector_readiness

    return evaluate_erp_connector_readiness(*args, **kwargs)


def _get_learning_calibration_service(*args, **kwargs):
    from clearledgr.services.learning_calibration import get_learning_calibration_service

    return get_learning_calibration_service(*args, **kwargs)


def _ap_policy_name() -> str:
    from clearledgr.services.policy_compliance import AP_POLICY_NAME

    return AP_POLICY_NAME


def _get_approval_automation_policy(*args, **kwargs):
    from clearledgr.services.policy_compliance import get_approval_automation_policy

    return get_approval_automation_policy(*args, **kwargs)


def _get_policy_compliance(*args, **kwargs):
    from clearledgr.services.policy_compliance import get_policy_compliance

    return get_policy_compliance(*args, **kwargs)


def _generate_auth_url(*args, **kwargs):
    from clearledgr.services.gmail_api import generate_auth_url

    return generate_auth_url(*args, **kwargs)


def _get_google_oauth_config() -> Dict[str, Any]:
    from clearledgr.services.gmail_api import get_google_oauth_config

    return get_google_oauth_config()


def _slack_api_client_class():
    from clearledgr.services.slack_api import SlackAPIClient

    return SlackAPIClient


def _slack_api_error_type():
    from clearledgr.services.slack_api import SlackAPIError

    return SlackAPIError


def _resolve_slack_runtime(*args, **kwargs):
    from clearledgr.services.slack_api import resolve_slack_runtime

    return resolve_slack_runtime(*args, **kwargs)


def _get_slack_client(*args, **kwargs):
    from clearledgr.services.slack_api import get_slack_client

    return get_slack_client(*args, **kwargs)


def _teams_api_client_class():
    from clearledgr.services.teams_api import TeamsAPIClient

    return TeamsAPIClient


def _get_subscription_service():
    from clearledgr.services.subscription import get_subscription_service

    return get_subscription_service()


def _plan_tier():
    from clearledgr.services.subscription import PlanTier

    return PlanTier


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
    # Phase 2.3: admin access == Financial Controller or higher under
    # the thesis taxonomy (legacy "admin" → "financial_controller").
    from clearledgr.core.auth import has_admin_access
    if not has_admin_access(user.role):
        raise HTTPException(status_code=403, detail="admin_role_required")


def _require_ops_access(user: TokenData) -> None:
    # Phase 2.3: ops access == AP Manager or higher.
    from clearledgr.core.auth import has_ops_access
    if not has_ops_access(user.role):
        raise HTTPException(status_code=403, detail="ops_role_required")


def _workspace_capabilities(role: Optional[str]) -> Dict[str, bool]:
    # Phase 2.3: the capability matrix reads role via the rank
    # predicates so legacy values get normalized transparently.
    from clearledgr.core.auth import (
        has_admin_access,
        has_ops_access,
        normalize_user_role,
    )
    normalized_role = normalize_user_role(role)
    has_workspace_role = bool(normalized_role)
    is_admin = has_admin_access(role)
    is_ops = has_ops_access(role)

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
    """Resolve + enforce tenant scope.

    Previously allowed owner role to access any org — cross-tenant
    vulnerability. Owner controls authority within the org, not across
    orgs. No platform-level super-admin concept exists on tenant APIs.
    """
    resolved = (organization_id or user.organization_id or "default").strip()
    if not resolved:
        resolved = "default"
    if resolved != str(user.organization_id or "").strip():
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
    # Resolve the Gmail token in order of specificity:
    #   1. Exact user_id match (most common: token was stored against the
    #      same canonical user id present on this JWT).
    #   2. Exact email match (extension bootstrap can store against a
    #      profile_email fallback when the user row id isn't populated
    #      yet; or a legacy token predates a user-id migration).
    #   3. Any token belonging to an active user in this org (covers
    #      service-level reconnect flows where the calling user isn't
    #      the same as the one who originally connected Gmail).
    token = db.get_oauth_token(user.user_id, "gmail")
    if not token:
        user_email = str(getattr(user, "email", "") or "").strip().lower()
        if user_email:
            token = db.get_oauth_token_by_email(user_email, "gmail")
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
    runtime = _resolve_slack_runtime(organization_id)
    mode = (
        integration.get("mode")
        or org.get("integration_mode")
        or os.getenv("SLACK_INTEGRATION_MODE", "shared")
    )
    settings = _load_org_settings(org)
    slack_channels = settings.get("slack_channels") if isinstance(settings.get("slack_channels"), dict) else {}
    connected = bool(runtime.get("connected"))
    approval_channel = slack_channels.get("invoices") if isinstance(slack_channels, dict) else None
    scope_csv = str(install.get("scope_csv") or "").strip()
    install_metadata = install.get("metadata") if isinstance(install.get("metadata"), dict) else {}
    user_scope_csv = str((install_metadata or {}).get("user_scope_csv") or "").strip()
    scope_audit_known = bool(scope_csv or user_scope_csv)
    missing_scopes = _missing_required_slack_scopes(scope_csv, user_scope_csv) if scope_audit_known else []
    requires_reauthorization = bool(connected and scope_audit_known and missing_scopes)
    return {
        "name": "slack",
        "connected": connected,
        "status": "connected" if connected and not requires_reauthorization else ("reauthorization_required" if connected else "disconnected"),
        "mode": mode,
        "team_id": install.get("team_id"),
        "team_name": install.get("team_name"),
        "approval_channel": approval_channel,
        "approval_channel_configured": bool(approval_channel),
        "install_recorded": bool(install),
        "source": runtime.get("source"),
        "last_sync_at": integration.get("last_sync_at"),
        "scope_csv": scope_csv,
        "user_scope_csv": user_scope_csv,
        "scope_audit_known": scope_audit_known,
        "missing_scopes": missing_scopes,
        "email_lookup_ready": bool(scope_audit_known and "users:read.email" not in missing_scopes),
        "requires_reauthorization": requires_reauthorization,
    }


def _is_ap_policy_configured(organization_id: str) -> bool:
    """§15 Step 3 — has the admin set the three thesis-required AP
    policy values (auto-approve threshold, match tolerance,
    approval routing)?

    Returns True when the stored policy row carries a config_json
    with all three keys populated. Fail-closed: any read error
    treats the policy as unconfigured so the checklist nags the
    admin to finish setup rather than silently marking onboarding
    done on a database blip.
    """
    try:
        db = get_db()
        policy = db.get_ap_policy(organization_id, _ap_policy_name())
        if not policy:
            return False
        config = policy.get("config_json") or policy.get("config") or {}
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except (ValueError, TypeError):
                config = {}
        if not isinstance(config, dict):
            return False
        # Three §15-required values. Missing or empty-string entries
        # count as unconfigured; zero is a valid threshold and
        # shouldn't fail the check.
        required_keys = ("auto_approve_threshold", "match_tolerance", "approval_routing")
        for key in required_keys:
            value = config.get(key)
            if value is None:
                return False
            if isinstance(value, str) and not value.strip():
                return False
            if isinstance(value, (list, dict)) and not value:
                return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[workspace_shell] _is_ap_policy_configured check failed for %s: %s",
            organization_id, exc,
        )
        return False


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
                # Non-secret identifier the Gmail-extension ERP button
                # needs to build deep-links (thesis §6.5). Kept as a
                # single `deep_link_id` key so the extension doesn't
                # have to know each ERP's convention.
                "deep_link_id": _resolve_erp_deep_link_id(item),
            }
            for item in conns
        ],
        "last_sync_at": latest.get("last_sync_at"),
    }


def _resolve_erp_deep_link_id(conn_row: Dict[str, Any]) -> Optional[str]:
    """Return the non-secret ERP-side identifier needed to build a deep
    link into the vendor's ERP UI. Returns ``None`` when the connection
    doesn't carry enough information to build a link (we fall back to
    showing the ERP reference as plain text in that case).

    Mapping (DESIGN_THESIS.md §6.5 "NetSuite ↗"):
      QuickBooks → realm_id (company id, visible in app.qbo.intuit.com URL)
      Xero       → tenant_id (organisation short code)
      NetSuite   → account_id from credentials blob (subdomain of app.netsuite.com)
      SAP        → base_url (the host is the deep-link target; customer-specific)
    """
    erp_type = str(conn_row.get("erp_type") or "").strip().lower()
    if erp_type == "quickbooks":
        return str(conn_row.get("realm_id") or "").strip() or None
    if erp_type == "xero":
        return str(conn_row.get("tenant_id") or "").strip() or None
    if erp_type == "netsuite":
        creds = conn_row.get("credentials") or {}
        if isinstance(creds, str):
            try:
                import json as _json
                creds = _json.loads(creds)
            except Exception:
                creds = {}
        if isinstance(creds, dict):
            acct = str(creds.get("account_id") or "").strip()
            return acct or None
        return None
    if erp_type == "sap":
        # SAP deep-links require the customer's own server URL, which
        # we already store as base_url. Strip trailing slashes so the
        # extension can concatenate a path cleanly.
        base = str(conn_row.get("base_url") or "").strip().rstrip("/")
        return base or None
    return None


def _teams_status_for_org(organization_id: str) -> Dict[str, Any]:
    # §12 / §6.8 — when Teams is disabled in V1, the bootstrap
    # response reports a terminal "disabled_in_v1" status so the
    # extension's integrations UI shows a clear "post-launch" label
    # instead of a "Connect Teams" CTA that would 404 when clicked.
    from clearledgr.core.feature_flags import is_teams_enabled
    if not is_teams_enabled():
        return {
            "name": "teams",
            "connected": False,
            "status": "disabled_in_v1",
            "mode": "per_org",
            "webhook_configured": False,
            "webhook_url": "",
            "managed_by": "none",
            "last_sync_at": None,
            "reason": "DESIGN_THESIS §12 — Teams ships post-launch; Slack is the V1 approval surface.",
        }

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
    # §12 / §6.8 — don't nag admins to connect Teams when it's scoped
    # out of V1. Slack is the V1 approval surface and suffices on its
    # own; Teams onboarding reappears when the post-launch flag flips.
    from clearledgr.core.feature_flags import is_teams_enabled
    if is_teams_enabled() and not integrations["teams"]["connected"]:
        required_actions.append({"code": "connect_teams", "message": "Connect Microsoft Teams webhook"})
    if not integrations["erp"]["connected"]:
        required_actions.append({"code": "connect_erp", "message": "Connect ERP system"})

    slack_channels = settings.get("slack_channels") if isinstance(settings.get("slack_channels"), dict) else {}
    if integrations["slack"]["connected"] and not (slack_channels or {}).get("invoices"):
        required_actions.append({"code": "set_slack_channel", "message": "Set Slack approval channel"})

    # §15 Step 3 — Configure AP Policy. The three values the thesis
    # names (auto-approve threshold, match tolerance, approval
    # routing) must all be set before onboarding counts as complete.
    # Without this check an admin who connects every integration but
    # never opens Settings > Policy would see a "done" state while
    # the agent was running with no autonomy thresholds configured.
    if not _is_ap_policy_configured(organization_id):
        required_actions.append({
            "code": "configure_ap_policy",
            "message": (
                "Set your AP policy — auto-approve threshold, match "
                "tolerance, and approval routing. Settings > Policy."
            ),
        })
    if integrations["slack"].get("requires_reauthorization"):
        missing = ", ".join(integrations["slack"].get("missing_scopes") or [])
        required_actions.append(
            {
                "code": "reauthorize_slack_scopes",
                "message": f"Reconnect Slack to grant required scopes: {missing}",
                "severity": "warning",
            }
        )

    slack_oauth_ready = bool(
        os.getenv("SLACK_CLIENT_ID", "").strip() and os.getenv("SLACK_CLIENT_SECRET", "").strip()
    )
    if not slack_oauth_ready:
        required_actions.append(
            {"code": "configure_slack_oauth_env", "message": "Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET"}
        )

    slack_redirect_uri = _slack_redirect_uri()

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


def _build_pilot_snapshot(kpis: Dict[str, Any]) -> Dict[str, Any]:
    payload = kpis if isinstance(kpis, dict) else {}
    pilot = payload.get("pilot_scorecard") if isinstance(payload.get("pilot_scorecard"), dict) else {}
    summary = pilot.get("summary") if isinstance(pilot.get("summary"), dict) else {}
    approval = pilot.get("approval_workflow") if isinstance(pilot.get("approval_workflow"), dict) else {}
    routing = pilot.get("entity_routing") if isinstance(pilot.get("entity_routing"), dict) else {}
    highlights = pilot.get("highlights") if isinstance(pilot.get("highlights"), list) else []
    return {
        "window_days": int(pilot.get("window_days") or 0),
        "touchless_rate_pct": round(float(summary.get("touchless_rate_pct") or 0.0), 2),
        "avg_cycle_time_hours": round(float(summary.get("avg_cycle_time_hours") or 0.0), 2),
        "on_time_approvals_pct": round(float(summary.get("on_time_approvals_pct") or 0.0), 2),
        "avg_approval_wait_hours": round(float(summary.get("avg_approval_wait_hours") or 0.0), 2),
        "approval_sla_breached_open_count": int(summary.get("approval_sla_breached_open_count") or 0),
        "approval_escalated_open_count": int(approval.get("escalated_open_count") or 0),
        "approval_reassigned_open_count": int(approval.get("reassigned_open_count") or 0),
        "entity_route_needs_review_count": int(summary.get("entity_route_needs_review_count") or 0),
        "entity_route_manual_resolution_count_30d": int(routing.get("manual_resolution_event_count_30d") or 0),
        "highlights": [str(entry) for entry in highlights if str(entry or "").strip()][:4],
    }


def _build_proof_snapshot(kpis: Dict[str, Any]) -> Dict[str, Any]:
    payload = kpis if isinstance(kpis, dict) else {}
    proof = payload.get("proof_scorecard") if isinstance(payload.get("proof_scorecard"), dict) else {}
    summary = proof.get("summary") if isinstance(proof.get("summary"), dict) else {}
    decisions = proof.get("decisions") if isinstance(proof.get("decisions"), dict) else {}
    followup = proof.get("approval_followup") if isinstance(proof.get("approval_followup"), dict) else {}
    posting = proof.get("posting_reliability") if isinstance(proof.get("posting_reliability"), dict) else {}
    recovery = proof.get("recovery") if isinstance(proof.get("recovery"), dict) else {}
    highlights = proof.get("highlights") if isinstance(proof.get("highlights"), list) else []
    return {
        "window_days": int(proof.get("window_days") or 0),
        "auto_approved_rate_pct": round(float(summary.get("auto_approved_rate_pct") or 0.0), 2),
        "human_override_rate_pct": round(float(summary.get("human_override_rate_pct") or 0.0), 2),
        "avg_approval_wait_hours": round(float(summary.get("avg_approval_wait_hours") or 0.0), 2),
        "escalation_rate_pct": round(float(summary.get("escalation_rate_pct") or 0.0), 2),
        "posting_success_rate_pct": round(float(summary.get("posting_success_rate_pct") or 0.0), 2),
        "recovery_success_rate_pct": round(float(summary.get("recovery_success_rate_pct") or 0.0), 2),
        "human_override_count": int(decisions.get("human_override_count") or 0),
        "decision_count": int(decisions.get("decision_count") or 0),
        "escalation_event_count_30d": int(followup.get("escalation_event_count_30d") or 0),
        "posting_attempt_count": int(posting.get("attempted_count") or 0),
        "posting_mismatch_count": int(posting.get("mismatch_count") or 0),
        "recovery_attempt_count": int(recovery.get("attempted_count") or 0),
        "recovered_count": int(recovery.get("recovered_count") or 0),
        "highlights": [str(entry) for entry in highlights if str(entry or "").strip()][:4],
    }


def _approval_sla_minutes_for_org(organization_id: str) -> int:
    policy_name = _ap_policy_name()
    policy = _get_approval_automation_policy(organization_id=organization_id, policy_name=policy_name)
    try:
        hours = int(policy.get("reminder_hours") or 4)
    except (TypeError, ValueError):
        hours = 4
    return max(60, min(hours * 60, 10080))


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
    step: int = Field(..., ge=1, le=4)  # §15: exactly 4 onboarding steps


class APPolicyRequest(BaseModel):
    organization_id: Optional[str] = None
    updated_by: Optional[str] = None
    enabled: bool = True
    config: Dict[str, Any] = {}


class OrgSettingsPatchRequest(BaseModel):
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
async def get_admin_bootstrap(
    request: Request,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    try:
        from clearledgr.services.gmail_autopilot import ensure_gmail_autopilot_progress

        await ensure_gmail_autopilot_progress(request.app, user_id=str(getattr(user, "user_id", "") or "").strip())
    except Exception:
        pass
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    org = db.ensure_organization(org_id, organization_name=org_id)
    org_settings = _load_org_settings(org)
    subscription = _get_subscription_service().get_subscription(org_id).to_dict()
    health = _build_health(org_id, user)

    current_user = db.get_user(user.user_id) or {}
    integrations = [
        _gmail_status_for_org(org_id, user),
        _slack_status_for_org(org_id),
        _teams_status_for_org(org_id),
        _erp_status_for_org(org_id),
    ]

    # §15: The Four Onboarding Steps — thesis-defined order.
    # We derive completion from observable integration state rather than
    # trusting the DB flag alone. Without this, the OnboardingFlow modal
    # pops on every Gmail refresh forever — nothing in the product ever
    # called complete_onboarding_step(), so the flag stayed False even
    # after the user connected Gmail + ERP.
    integrations_by_name = {
        str(i.get("name") or "").lower(): i
        for i in integrations
        if isinstance(i, dict)
    }

    def _is_connected(name: str) -> bool:
        info = integrations_by_name.get(name) or {}
        status = str(info.get("status") or "").lower()
        return bool(info.get("connected")) or status in {"connected", "active", "ready"}

    gmail_connected = _is_connected("gmail")
    erp_connected = _is_connected("erp") or any(
        _is_connected(n) for n in ("quickbooks", "xero", "netsuite", "sap")
    )
    slack_or_teams_connected = _is_connected("slack") or _is_connected("teams")
    has_ap_policy = bool((org_settings or {}).get("ap_policy") or (org_settings or {}).get("workflow_controls"))

    # Hub-and-spoke onboarding (industry-standard ERP-first):
    #   1. Connect ERP            (anchor — without this, no AP coordination)
    #   2. Set AP policy          (auto-approve, match tolerance, GL map)
    #   3. Connect Slack/Teams    (approval surface)
    #   4. Install Gmail extension (optional intake channel — companion only;
    #                               ERP-native customers can skip)
    # Mirrors BILL.com / Ramp / Stampli onboarding sequence: integration
    # first, then policy, then collaboration surface, then optional clients.
    # The Gmail extension was step 1 in the previous Streak-aligned model;
    # demoting to step 4 reflects the new hub-and-spoke positioning where
    # Gmail is one of several intake channels, not the home.
    derived_step = 0
    if erp_connected:
        derived_step = 1
    if derived_step >= 1 and has_ap_policy:
        derived_step = 2
    if derived_step >= 2 and slack_or_teams_connected:
        derived_step = 3
    if derived_step >= 3 and gmail_connected:
        derived_step = 4

    persisted_step = int(subscription.get("onboarding_step") or 0)
    persisted_completed = bool(subscription.get("onboarding_completed"))
    effective_step = max(persisted_step, derived_step)
    # Onboarding can complete at step 3 — Gmail extension is optional. The
    # explicit completed flag from the user dismissing the wizard also wins.
    effective_completed = persisted_completed or derived_step >= 3

    onboarding = {
        "completed": effective_completed,
        "step": effective_step,
        "steps": [
            {
                "id": 1,
                "name": "Connect ERP",
                "description": "OAuth connection to NetSuite, SAP S/4HANA, Xero, or QuickBooks. Read access to PO, GRN, and vendor master.",
                "time_estimate": "5 minutes",
                "required": True,
            },
            {
                "id": 2,
                "name": "Set AP policy",
                "description": "Auto-approve threshold, match tolerance, and approval routing — built to your actual finance rules.",
                "time_estimate": "10 minutes",
                "required": True,
            },
            {
                "id": 3,
                "name": "Connect Slack or Teams",
                "description": "Choose your approval surface. Agent begins processing immediately after this step.",
                "time_estimate": "5 minutes",
                "required": True,
            },
            {
                "id": 4,
                "name": "Install Gmail extension",
                "description": "Optional. Adds a contextual sidebar in Gmail when an invoice email is open. ERP-native customers (SAP S/4HANA, NetSuite-only intake) can skip.",
                "time_estimate": "2 minutes",
                "required": False,
            },
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
        "trust_arc": _safe_trust_arc_state(org_id),
    }


def _safe_trust_arc_state(org_id: str) -> Dict[str, Any]:
    """§7.5: Load trust arc state for the Home page banner."""
    try:
        db = get_db()
        from clearledgr.services.trust_arc import get_trust_arc_status
        return get_trust_arc_status(db, org_id)
    except Exception:
        return {"phase": "not_started"}


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
        approval_sla_minutes = _approval_sla_minutes_for_org(org_id)
        kpis = db.get_ap_kpis(org_id, approval_sla_minutes=approval_sla_minutes) if hasattr(db, "get_ap_kpis") else {}
        agentic_snapshot = _build_agentic_snapshot(kpis)
        pilot_snapshot = _build_pilot_snapshot(kpis)
        proof_snapshot = _build_proof_snapshot(kpis)
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
            "pilot_snapshot": pilot_snapshot,
            "proof_snapshot": proof_snapshot,
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

    oauth_redirect_uri = _get_google_oauth_config().get("redirect_uri")
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
        auth_url = _generate_auth_url(state=state)
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

    redirect_uri = _slack_redirect_uri()
    scopes = _configured_slack_oauth_scopes()
    user_scopes = _configured_slack_user_oauth_scopes()
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
    if user_scopes:
        params["user_scope"] = user_scopes
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
    redirect_uri = _slack_redirect_uri()
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="slack_oauth_not_configured")

    client = get_http_client()
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
    user_scope_csv = authed_user.get("scope") or ""
    authed_user_token = authed_user.get("access_token")
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
        user_scope_csv=user_scope_csv,
        user_token=authed_user_token,
        mode=mode,
        is_active=True,
        metadata={
            "install_payload": payload,
            "user_scope_csv": user_scope_csv,
            "authed_user_id": authed_user.get("id"),
        },
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
    runtime = _resolve_slack_runtime(org_id)
    existing = db.get_organization_integration(org_id, "slack") or {}
    existing_metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
    db.upsert_organization_integration(
        organization_id=org_id,
        integration_type="slack",
        status="connected" if runtime.get("connected") else "disconnected",
        mode=existing.get("mode") or (db.get_organization(org_id) or {}).get("integration_mode") or "shared",
        metadata={**existing_metadata, "approval_channel": request.channel_id.strip()},
        last_sync_at=_now_iso(),
    )
    return {
        "success": True,
        "organization_id": org_id,
        "channel_id": request.channel_id.strip(),
        "slack_connected": bool(runtime.get("connected")),
        "slack_source": runtime.get("source"),
    }


@router.post("/integrations/slack/test")
async def test_slack_channel(
    request: SlackTestRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    runtime = _resolve_slack_runtime(org_id)
    token = runtime.get("bot_token")
    if not token:
        raise HTTPException(status_code=400, detail="slack_not_connected")
    channel = str(request.channel_id or runtime.get("approval_channel") or "").strip()
    SlackAPIClient = _slack_api_client_class()
    SlackAPIError = _slack_api_error_type()
    client = SlackAPIClient(bot_token=token)
    try:
        auth_context = await client.auth_test()
        resolved_channel = await client.resolve_channel(channel) if channel else None
    except SlackAPIError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "slack_verification_failed",
                "error": exc.error,
            },
        ) from exc

    if channel and not resolved_channel:
        raise HTTPException(status_code=400, detail="slack_channel_not_accessible")

    return {
        "success": True,
        "organization_id": org_id,
        "channel": f"#{resolved_channel.get('name')}" if resolved_channel and resolved_channel.get("name") else (channel or None),
        "channel_id": resolved_channel.get("id") if resolved_channel else None,
        "channel_verified": bool(resolved_channel) if channel else True,
        "mode": runtime.get("mode"),
        "message_posted": False,
        "verification": "silent",
        "team": auth_context.get("team"),
        "bot_user_id": auth_context.get("user_id"),
    }


@router.post("/integrations/teams/webhook")
def set_teams_webhook(
    request: TeamsWebhookRequest,
    user: TokenData = Depends(get_current_user),
):
    # §12 / §6.8 — Teams is scoped post-V1. Admins cannot wire a Teams
    # webhook in a V1 deployment regardless of role, so the admin UI
    # can't accidentally connect a surface we don't ship yet.
    from clearledgr.core.feature_flags import is_teams_enabled, teams_disabled_payload
    if not is_teams_enabled():
        raise HTTPException(status_code=404, detail=teams_disabled_payload())

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
    from clearledgr.core.feature_flags import is_teams_enabled, teams_disabled_payload
    if not is_teams_enabled():
        raise HTTPException(status_code=404, detail=teams_disabled_payload())

    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    client = _teams_api_client_class().from_env(org_id)
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
    redirect_uri = _slack_redirect_uri()
    app_base = _public_app_base_url()
    bot_scopes = [scope for scope in _configured_slack_oauth_scopes().split(",") if scope]
    user_scopes = [scope for scope in _configured_slack_user_oauth_scopes().split(",") if scope]
    return {
        "organization_id": org_id,
        "manifest": {
            "display_information": {"name": "Clearledgr AP"},
            "features": {"bot_user": {"display_name": "Clearledgr AP"}},
            "oauth_config": {
                "redirect_urls": [redirect_uri],
                "scopes": {
                    "bot": bot_scopes,
                    "user": user_scopes,
                },
            },
            "settings": {
                "event_subscriptions": {"request_url": f"{app_base}/slack/events"},
                "interactivity": {"is_enabled": True, "request_url": f"{app_base}/slack/invoices/interactive"},
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

    # OAuth-based ERPs (QuickBooks, Xero). State is stored in the
    # erp_oauth_states DB table (migration v10) via _save_oauth_state,
    # not the old in-memory _oauth_states dict (removed when it moved
    # to the DB for multi-worker safety).
    from clearledgr.api.erp_connections import (
        _save_oauth_state,
        QUICKBOOKS_CLIENT_ID, QUICKBOOKS_REDIRECT_URI, QUICKBOOKS_AUTH_URL,
        XERO_CLIENT_ID, XERO_REDIRECT_URI, XERO_AUTH_URL,
    )
    from urllib.parse import urlencode as _urlencode

    state = secrets.token_urlsafe(32)
    _save_oauth_state(state, {
        "organization_id": org_id,
        "return_url": "success_page",
        "erp_type": erp_type,
        "created_at": _now_iso(),
    })

    if erp_type == "quickbooks":
        if not QUICKBOOKS_CLIENT_ID:
            # Not configured on this deploy — don't 500, let the caller
            # (onboarding modal, Connections page) advance and surface a
            # "connect later" state.
            return {
                "erp_type": "quickbooks",
                "method": "not_configured",
                "reason": "quickbooks_client_id_missing",
                "message": "QuickBooks isn't set up on this deployment yet. You can connect it later from Connections.",
            }
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
            return {
                "erp_type": "xero",
                "method": "not_configured",
                "reason": "xero_client_id_missing",
                "message": "Xero isn't set up on this deployment yet. You can connect it later from Connections.",
            }
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
        client = get_http_client()
        response = await client.get(
            metadata_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Accept": "application/xml,application/json,*/*",
            },
        )
        if response.status_code >= 400:
            from clearledgr.api.erp_connections import _classify_erp_connect_error
            classified = _classify_erp_connect_error(
                "sap", Exception(f"HTTP {response.status_code}: {response.text[:200]}"),
            )
            raise HTTPException(status_code=400, detail=classified)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # §15 structured ERP error — name the permission + link to
        # remediation instead of leaking the raw exception.
        from clearledgr.api.erp_connections import _classify_erp_connect_error
        classified = _classify_erp_connect_error("sap", exc)
        raise HTTPException(status_code=400, detail=classified) from exc

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
    sub = _get_subscription_service().get_subscription(org_id)
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
    sub = _get_subscription_service().complete_onboarding_step(org_id, request.step)
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
    policy_name = _ap_policy_name()
    policy_service = _get_policy_compliance(organization_id=org_id, policy_name=policy_name)
    db = get_db()
    policy = db.get_ap_policy(org_id, policy_name)
    return {
        "organization_id": org_id,
        "policy_name": policy_name,
        "policy": policy,
        "effective_policies": policy_service.describe_effective_policies(),
        "approval_automation": _get_approval_automation_policy(
            organization_id=org_id,
            policy_name=policy_name,
        ),
    }


@router.put("/policies/ap")
def put_ap_policy(
    request: APPolicyRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    policy_name = _ap_policy_name()
    policy_service = _get_policy_compliance(organization_id=org_id, policy_name=policy_name)
    errors = policy_service.validate_policy_config(request.config or {})
    if errors:
        raise HTTPException(status_code=422, detail={"message": "invalid_policy_document", "errors": errors})
    updated = db.upsert_ap_policy_version(
        organization_id=org_id,
        policy_name=policy_name,
        config=request.config or {},
        updated_by=request.updated_by or user.user_id,
        enabled=bool(request.enabled),
    )
    return {
        "organization_id": org_id,
        "policy_name": policy_name,
        "policy": updated,
        "approval_automation": _get_approval_automation_policy(
            organization_id=org_id,
            policy_name=policy_name,
        ),
    }


@router.get("/org/settings")
def get_org_settings(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    org = db.ensure_organization(org_id, organization_name=org_id)
    return {"organization_id": org_id, "settings": _load_org_settings(org)}


_ORG_NAME_MIN_LEN = 1
_ORG_NAME_MAX_LEN = 128
_ORG_DOMAIN_MAX_LEN = 253  # RFC 1035 hostname cap


def _normalise_org_name(raw: Any) -> str:
    """Trim + sanity-check an organization display name.

    Returns the cleaned value. Raises HTTPException with a deterministic
    detail token on validation failure so the SPA can map the failure to
    inline form copy.
    """
    if raw is None:
        raise HTTPException(status_code=422, detail="organization_name_required")
    text = str(raw).strip()
    if len(text) < _ORG_NAME_MIN_LEN:
        raise HTTPException(status_code=422, detail="organization_name_required")
    if len(text) > _ORG_NAME_MAX_LEN:
        raise HTTPException(status_code=422, detail="organization_name_too_long")
    # Reject control characters — they break UI rendering and CSV export
    # and never represent legitimate company-name input.
    if any(ord(ch) < 0x20 for ch in text):
        raise HTTPException(status_code=422, detail="organization_name_invalid_characters")
    return text


def _normalise_org_domain(raw: Any) -> Optional[str]:
    """Trim a domain field; allow empty (returns None) to clear it."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > _ORG_DOMAIN_MAX_LEN:
        raise HTTPException(status_code=422, detail="organization_domain_too_long")
    if any(ord(ch) < 0x20 for ch in text):
        raise HTTPException(status_code=422, detail="organization_domain_invalid_characters")
    return text


@router.patch("/org/settings")
def patch_org_settings(
    request: OrgSettingsPatchRequest,
    user: TokenData = Depends(get_current_user),
):
    """Update top-level org fields (name / domain / integration_mode) plus
    arbitrary settings_json keys. Admin-only, tenant-scoped, validates
    inputs, and emits a per-field ``organization_*`` audit event for any
    top-level change so the rename trail is queryable from the timeline.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    org_before = db.ensure_organization(org_id, organization_name=org_id)
    settings = _load_org_settings(org_before)
    patch = request.patch or {}

    org_updates: Dict[str, Any] = {}
    # Track previous values so we can emit precise rename audits and
    # short-circuit no-op writes (saves a round-trip to the DB +
    # avoids an audit row that would just say "Acme → Acme").
    prev_name = str(org_before.get("name") or "")
    prev_domain = str(org_before.get("domain") or "")
    prev_integration_mode = str(org_before.get("integration_mode") or "shared")

    new_name: Optional[str] = None
    if "organization_name" in patch or "name" in patch:
        raw_name = patch.get("organization_name", patch.get("name"))
        new_name = _normalise_org_name(raw_name)
        if new_name != prev_name:
            org_updates["name"] = new_name

    new_domain: Optional[str] = None
    domain_changed = False
    if "domain" in patch:
        new_domain = _normalise_org_domain(patch.get("domain"))
        if (new_domain or "") != prev_domain:
            org_updates["domain"] = new_domain
            domain_changed = True

    new_mode: Optional[str] = None
    if "integration_mode" in patch:
        mode = str(patch.get("integration_mode") or "").strip().lower()
        if mode not in {"shared", "per_org"}:
            raise HTTPException(status_code=422, detail="invalid_integration_mode")
        new_mode = mode
        if new_mode != prev_integration_mode:
            org_updates["integration_mode"] = new_mode

    if org_updates:
        db.update_organization(org_id, **org_updates)

    # Apply the rest of the patch onto settings_json verbatim.
    for key, value in patch.items():
        if key in {"organization_name", "name", "domain", "integration_mode"}:
            continue
        settings[key] = value
    _save_org_settings(org_id, settings)

    # Per-field audit emission. ``audit_events`` is keyed on
    # ``(box_id, box_type)`` — using ``box_type='organization'`` extends
    # the timeline pattern from AP boxes to tenant-level mutations
    # without inventing a parallel audit table. Best-effort: failures
    # log + swallow so the user-facing PATCH never returns 500 on an
    # audit-side hiccup.
    actor_id = (
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or "system"
    )
    if "name" in org_updates and new_name is not None:
        try:
            db.append_audit_event({
                "box_id": org_id,
                "box_type": "organization",
                "event_type": "organization_renamed",
                "from_state": prev_name or None,
                "to_state": new_name,
                "actor_type": "user",
                "actor_id": actor_id,
                "organization_id": org_id,
                "source": "workspace_shell.patch_org_settings",
                "decision_reason": "manual_org_rename",
                "payload_json": {"prev_name": prev_name, "new_name": new_name},
                "idempotency_key": f"organization_renamed:{org_id}:{prev_name}->{new_name}",
            })
        except Exception as exc:
            logger.warning("[org_rename] audit emit failed for %s: %s", org_id, exc)

    if domain_changed:
        try:
            db.append_audit_event({
                "box_id": org_id,
                "box_type": "organization",
                "event_type": "organization_domain_changed",
                "from_state": prev_domain or None,
                "to_state": new_domain or None,
                "actor_type": "user",
                "actor_id": actor_id,
                "organization_id": org_id,
                "source": "workspace_shell.patch_org_settings",
                "payload_json": {"prev_domain": prev_domain, "new_domain": new_domain},
                "idempotency_key": f"organization_domain_changed:{org_id}:{prev_domain}->{new_domain}",
            })
        except Exception as exc:
            logger.warning("[org_domain_change] audit emit failed for %s: %s", org_id, exc)

    if "integration_mode" in org_updates and new_mode is not None:
        try:
            db.append_audit_event({
                "box_id": org_id,
                "box_type": "organization",
                "event_type": "organization_integration_mode_changed",
                "from_state": prev_integration_mode,
                "to_state": new_mode,
                "actor_type": "user",
                "actor_id": actor_id,
                "organization_id": org_id,
                "source": "workspace_shell.patch_org_settings",
                "payload_json": {"prev_mode": prev_integration_mode, "new_mode": new_mode},
                "idempotency_key": f"organization_integration_mode_changed:{org_id}:{prev_integration_mode}->{new_mode}",
            })
        except Exception as exc:
            logger.warning("[org_mode_change] audit emit failed for %s: %s", org_id, exc)

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


# /user/preferences moved to clearledgr/api/user_preferences.py
# (per-user data — doesn't belong under the /api/workspace/* ops surface)


@router.get("/rollback-controls")
def get_admin_rollback_controls(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    controls = _get_rollback_controls(org_id)
    return {"organization_id": org_id, "rollback_controls": controls}


@router.put("/rollback-controls")
def put_admin_rollback_controls(
    request: RollbackControlsRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    controls = _set_rollback_controls(
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
    evidence = _get_ga_readiness(org_id)
    rollback_controls = _get_rollback_controls(org_id)
    return {
        "organization_id": org_id,
        "ga_readiness": evidence,
        "rollback_controls": rollback_controls,
        "summary": _summarize_ga_readiness(evidence, rollback_controls=rollback_controls),
    }


@router.put("/ga-readiness")
def put_admin_ga_readiness(
    request: GAReadinessRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    evidence = _set_ga_readiness(
        org_id,
        request.evidence or {},
        updated_by=request.updated_by or user.user_id,
    )
    rollback_controls = _get_rollback_controls(org_id)
    return {
        "success": True,
        "organization_id": org_id,
        "ga_readiness": evidence,
        "rollback_controls": rollback_controls,
        "summary": _summarize_ga_readiness(evidence, rollback_controls=rollback_controls),
    }


@router.get("/ops/connector-readiness")
def get_ops_connector_readiness(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    _require_ops_access(user)
    org_id = _resolve_org_id(user, organization_id)
    report = _evaluate_erp_connector_readiness(org_id, db=get_db(), require_full_ga_scope=False)
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
    service = _get_learning_calibration_service(org_id, db=get_db())
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
    service = _get_learning_calibration_service(org_id, db=get_db())
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


# ------------------------------------------------------------------
# Chart of Accounts
# ------------------------------------------------------------------

@router.get("/chart-of-accounts")
async def get_chart_of_accounts_endpoint(
    organization_id: Optional[str] = Query(default=None),
    force_refresh: bool = Query(default=False),
    account_type: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
    user: TokenData = Depends(get_current_user),
):
    """Return chart of accounts from the connected ERP.

    Results are cached for 24h in org settings. Use ``force_refresh=true``
    to bypass cache and pull fresh data from the ERP.  Supports optional
    filters: ``account_type`` (expense, revenue, asset, liability, equity)
    and ``active_only`` (default true).
    """
    org_id = _resolve_org_id(user, organization_id)

    from clearledgr.integrations.erp_router import (
        get_chart_of_accounts as _get_coa,
        get_erp_connection as _get_erp_conn,
    )

    accounts = await _get_coa(
        organization_id=org_id,
        force_refresh=force_refresh,
    )

    # Apply filters
    if active_only:
        accounts = [a for a in accounts if a.get("active", True)]
    if account_type:
        normalized_type = account_type.strip().lower()
        accounts = [a for a in accounts if a.get("type") == normalized_type]

    erp_conn = _get_erp_conn(org_id)
    erp_type = erp_conn.type if erp_conn else None

    return {
        "organization_id": org_id,
        "erp_type": erp_type,
        "accounts": accounts,
        "account_count": len(accounts),
        "filtered": bool(account_type or active_only),
    }


@router.get("/reports/export")
async def export_report(
    report_type: str = Query(..., description="Report type: ap_aging, vendor_spend, posting_status"),
    format: str = Query(default="csv", description="Export format: csv or json"),
    organization_id: Optional[str] = Query(default=None),
    period_days: int = Query(default=30, ge=1, le=365),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    vendor: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Export a report as CSV or JSON.

    Supported report types:
    - ``ap_aging``: Open payables by aging bucket and vendor
    - ``vendor_spend``: Top vendors, GL categories, monthly trends
    - ``posting_status``: AP items with posting timing (filterable by date/vendor)

    For audit trail export, use ``GET /api/ap/items/audit/export`` instead.
    """
    from clearledgr.services.report_export import (
        REPORT_TYPES,
        generate_report,
        rows_to_csv,
    )

    org_id = _resolve_org_id(user, organization_id)

    if report_type not in REPORT_TYPES:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"error": f"Unknown report_type. Must be one of: {sorted(REPORT_TYPES)}"},
        )

    # §3 Multi-entity: parent accounts can pull consolidated reports
    # across all child entities via get_all_entity_org_ids
    report_org_ids = [org_id]
    try:
        db = get_db()
        if hasattr(db, "get_all_entity_org_ids"):
            all_ids = db.get_all_entity_org_ids(org_id)
            if len(all_ids) > 1:
                report_org_ids = all_ids
    except Exception:
        pass

    all_rows: list = []
    columns: list = []
    for rid in report_org_ids:
        r, c = generate_report(
            report_type=report_type,
            organization_id=rid,
            period_days=period_days,
            start_date=start_date,
            end_date=end_date,
            vendor=vendor,
        )
        if c and not columns:
            columns = c
        if r:
            for row in r:
                row["entity_org_id"] = rid
            all_rows.extend(r)
    rows = all_rows

    if format == "json":
        return {
            "report_type": report_type,
            "organization_id": org_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "row_count": len(rows),
            "columns": columns,
            "rows": rows,
        }

    # CSV download
    csv_content = rows_to_csv(rows, columns)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={report_type}_{org_id}.csv",
        },
    )


# ---------------------------------------------------------------------------
# Module 7 v1 — Audit Trail dashboard surface
#
# Admin-gated, org-scoped audit search + single-event detail. Covers the
# search/filter/pagination/detail half of Module 7; export (CSV/PDF) and
# SIEM webhook config land in Pass 2 + Pass 3.
#
# Backed by audit_events (already shipped, with governance_verdict +
# agent_confidence columns from migration v50). Append-only Postgres
# triggers reject UPDATE/DELETE on this table — see
# clearledgr/core/database.py:374. The dashboard is purely a read
# surface; nothing mutates the audit log here.
# ---------------------------------------------------------------------------


_AUDIT_SEARCH_MAX_LIMIT = 200


def _decode_audit_cursor(raw: Optional[str]) -> Optional[Tuple[str, str]]:
    """Decode the opaque base64 cursor sent by the SPA.

    Cursor wire format: base64(<ts>|<id>). Two pipe-separated fields
    keep parsing trivial — the audit_events.id is a UUID hex (no
    pipes) and ts is an ISO-8601 string (no pipes), so the delimiter
    is unambiguous. Failed decode falls back to no cursor (start
    fresh) rather than 400 — corrupt cursors come from copy-pasted
    URLs, not malice, and the next page request will succeed.
    """
    if not raw:
        return None
    try:
        import base64
        decoded = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
        ts, _, evt_id = decoded.partition("|")
        if ts and evt_id:
            return (ts, evt_id)
    except Exception:
        return None
    return None


def _encode_audit_cursor(pair: Optional[Tuple[str, str]]) -> Optional[str]:
    if not pair:
        return None
    import base64
    raw = f"{pair[0]}|{pair[1]}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


@router.get("/audit/search")
def search_audit(
    organization_id: Optional[str] = Query(default=None),
    from_ts: Optional[str] = Query(default=None, description="ISO 8601; inclusive lower bound."),
    to_ts: Optional[str] = Query(default=None, description="ISO 8601; inclusive upper bound."),
    event_type: Optional[str] = Query(default=None, description="Comma-separated event_type tokens."),
    actor_id: Optional[str] = Query(default=None, description="Exact-match actor (email or user_id)."),
    box_type: Optional[str] = Query(default=None, description="Narrow to one Box type (ap_item, organization, etc)."),
    box_id: Optional[str] = Query(default=None, description="Narrow to one Box's trail."),
    limit: int = Query(default=100, ge=1, le=_AUDIT_SEARCH_MAX_LIMIT),
    cursor: Optional[str] = Query(default=None, description="Opaque cursor from a prior response."),
    user: TokenData = Depends(get_current_user),
):
    """Org-scoped audit search with composite-cursor pagination.

    Returns ``{events: [...], next_cursor: <opaque>|None, count: N}``.
    Newest-first. Empty ``next_cursor`` means the page is the last one.
    Admin-only — the audit log is sensitive data and surfaces actor IDs
    + reasons that aren't in the operator's normal queue view.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()

    event_types: Optional[List[str]] = None
    if event_type:
        event_types = [t.strip() for t in event_type.split(",") if t.strip()] or None

    decoded_cursor = _decode_audit_cursor(cursor)
    result = db.search_audit_events(
        organization_id=org_id,
        from_ts=from_ts,
        to_ts=to_ts,
        event_types=event_types,
        actor_id=actor_id,
        box_type=box_type,
        box_id=box_id,
        limit=limit,
        cursor=decoded_cursor,
    )
    events = result.get("events") or []
    next_cursor = _encode_audit_cursor(result.get("next_cursor"))
    return {
        "organization_id": org_id,
        "events": events,
        "next_cursor": next_cursor,
        "count": len(events),
        "filters": {
            "from_ts": from_ts,
            "to_ts": to_ts,
            "event_type": event_type,
            "actor_id": actor_id,
            "box_type": box_type,
            "box_id": box_id,
        },
    }


# ---------------------------------------------------------------------------
# Module 7 v1 Pass 2 — async CSV export
#
# Mirrors the search/filter contract: the SPA collects the same filters
# the user already typed into the search bar, POSTs them here to start
# a job, then polls the GET endpoint until status='done' and downloads
# the CSV. Backed by:
#   * audit_exports table (migration v51)
#   * generate_audit_export Celery task (services/celery_tasks.py)
# ---------------------------------------------------------------------------


class AuditExportRequest(BaseModel):
    """Filters mirror GET /api/workspace/audit/search exactly so the
    SPA can submit the same query that produced the on-screen results."""
    organization_id: Optional[str] = None
    from_ts: Optional[str] = None
    to_ts: Optional[str] = None
    event_types: Optional[List[str]] = None
    actor_id: Optional[str] = None
    box_type: Optional[str] = None
    box_id: Optional[str] = None


@router.post("/audit/export")
def start_audit_export(
    request: AuditExportRequest,
    user: TokenData = Depends(get_current_user),
):
    """Kick off an async CSV export of the audit log with the given filters.

    Returns ``{job_id, status: 'queued'}``. The SPA polls
    ``GET /api/workspace/audit/exports/{job_id}`` for status, then
    downloads via ``GET /api/workspace/audit/exports/{job_id}?download=true``
    when status flips to ``done``. 24h retention; the row + content
    are reaped after that.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()

    actor = (
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or "system"
    )
    import json as _json
    filters_payload = {
        "from_ts": request.from_ts,
        "to_ts": request.to_ts,
        "event_types": request.event_types,
        "actor_id": request.actor_id,
        "box_type": request.box_type,
        "box_id": request.box_id,
    }
    export_row = db.create_audit_export(
        organization_id=org_id,
        requested_by=actor,
        filters_json=_json.dumps(filters_payload, separators=(",", ":")),
        export_format="csv",
        retention_hours=24,
    )

    # Dispatch the Celery task. If Celery is unreachable (broker
    # outage, dev env without worker), fail soft — flip the row to
    # 'failed' with a clear message rather than letting the SPA poll
    # forever.
    try:
        from clearledgr.services.celery_tasks import generate_audit_export
        generate_audit_export.delay(export_row["id"])
    except Exception as exc:
        logger.exception("[audit/export] dispatch failed: %s", exc)
        db.update_audit_export_status(
            export_row["id"],
            status="failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            error_message=f"dispatch_failed: {exc}",
        )
        export_row = db.get_audit_export(export_row["id"]) or export_row

    return {
        "job_id": export_row["id"],
        "status": export_row.get("status", "queued"),
        "created_at": export_row.get("created_at"),
        "expires_at": export_row.get("expires_at"),
    }


@router.get("/audit/exports/{job_id}")
def get_audit_export_status(
    job_id: str,
    organization_id: Optional[str] = Query(default=None),
    download: bool = Query(default=False),
    user: TokenData = Depends(get_current_user),
):
    """Poll status (default) or download the rendered CSV (download=true).

    The status payload omits the ``content`` BYTEA so the SPA's
    poll loop stays cheap regardless of CSV size. Cross-tenant
    requests 404 with the same token as truly-missing — never leak
    that another tenant's export exists.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    export = db.get_audit_export(job_id, include_content=download)
    if not export:
        raise HTTPException(status_code=404, detail="audit_export_not_found")
    if str(export.get("organization_id") or "") != org_id:
        raise HTTPException(status_code=404, detail="audit_export_not_found")

    if download:
        if export.get("status") != "done":
            raise HTTPException(
                status_code=409,
                detail={"reason": "export_not_ready", "status": export.get("status")},
            )
        content = export.get("content")
        if content is None:
            raise HTTPException(status_code=410, detail="audit_export_content_expired")
        filename = export.get("content_filename") or f"audit-{job_id}.csv"
        from fastapi.responses import Response
        # Convert memoryview / bytes from psycopg cleanly.
        if isinstance(content, memoryview):
            content = bytes(content)
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "private, no-store",
            },
        )

    # Status-only response (default poll path) — strip large columns
    # so the SPA's 2-second polling stays cheap.
    return {
        "job_id": export.get("id"),
        "status": export.get("status"),
        "format": export.get("format"),
        "total_rows": export.get("total_rows"),
        "content_size_bytes": export.get("content_size_bytes"),
        "content_filename": export.get("content_filename"),
        "error_message": export.get("error_message"),
        "created_at": export.get("created_at"),
        "started_at": export.get("started_at"),
        "completed_at": export.get("completed_at"),
        "expires_at": export.get("expires_at"),
    }


@router.get("/audit/event/{event_id}")
def get_audit_event_detail(
    event_id: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Single-event detail with full payload + tenant-scope check.

    Returns 404 if the event doesn't exist OR if it belongs to a
    different organization than the calling user. Same response shape
    in both cases — never leak the existence of a cross-tenant event.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    event = db.get_ap_audit_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="audit_event_not_found")
    # Tenant scope: org_id mismatch returns the same 404 token so we
    # never leak that the event exists in another tenant.
    event_org = str(event.get("organization_id") or "")
    if event_org and event_org != org_id:
        raise HTTPException(status_code=404, detail="audit_event_not_found")
    return {"event": event}


@router.get("/webhooks")
def list_webhooks(
    organization_id: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
    user: TokenData = Depends(get_current_user),
):
    """List webhook subscriptions for this organization."""
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    subs = db.list_webhook_subscriptions(org_id, active_only=active_only)
    # Redact secrets in response
    for s in subs:
        if s.get("secret"):
            s["secret"] = "***"
    return {"webhooks": subs, "count": len(subs)}


@router.post("/webhooks")
def create_webhook(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
    body: dict = {},
):
    """Register a new webhook subscription.

    Body:
        url: str (required)
        event_types: List[str] (required) — e.g. ["invoice.approved", "invoice.posted_to_erp"] or ["*"] for all
        secret: str (optional) — HMAC signing secret
        description: str (optional)
    """
    org_id = _resolve_org_id(user, organization_id)
    url = (body.get("url") or "").strip()
    event_types = body.get("event_types") or []
    secret = body.get("secret", "")
    description = body.get("description", "")

    if not url:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "url is required"})
    if not event_types:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "event_types is required"})

    db = get_db()
    sub = db.create_webhook_subscription(
        organization_id=org_id,
        url=url,
        event_types=event_types,
        secret=secret,
        description=description,
    )
    if sub.get("secret"):
        sub["secret"] = "***"
    return sub


@router.delete("/webhooks/{webhook_id}")
def delete_webhook(
    webhook_id: str,
    user: TokenData = Depends(get_current_user),
):
    """Delete a webhook subscription."""
    db = get_db()
    sub = db.get_webhook_subscription(webhook_id)
    if not sub:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Webhook not found"})

    # Verify org access
    org_id = sub.get("organization_id", "default")
    _resolve_org_id(user, org_id)

    db.delete_webhook_subscription(webhook_id)
    return {"status": "deleted", "id": webhook_id}


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(
    webhook_id: str,
    user: TokenData = Depends(get_current_user),
):
    """Send a test event to a webhook."""
    db = get_db()
    sub = db.get_webhook_subscription(webhook_id)
    if not sub:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Webhook not found"})

    from clearledgr.services.webhook_delivery import deliver_webhook

    ok = await deliver_webhook(
        url=sub["url"],
        event_type="test.ping",
        payload={"message": "Clearledgr webhook test", "webhook_id": webhook_id},
        secret=sub.get("secret", ""),
    )
    return {"delivered": ok, "url": sub["url"], "event": "test.ping"}


@router.post("/reports/export-to-sheets")
async def export_report_to_sheets(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
    body: dict = {},
):
    """Push a report to a Google Sheet.

    Body:
        spreadsheet_url: str (required) — full Google Sheets URL
        report_type: str (required) — ap_aging, vendor_spend, posting_status
        period_days: int (optional, default 30)
    """
    org_id = _resolve_org_id(user, organization_id)
    spreadsheet_url = (body.get("spreadsheet_url") or "").strip()
    report_type = (body.get("report_type") or "").strip()

    if not spreadsheet_url or not report_type:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "spreadsheet_url and report_type are required"})

    from clearledgr.services.sheets_api import SheetsAPIClient
    spreadsheet_id = SheetsAPIClient.extract_spreadsheet_id(spreadsheet_url)
    if not spreadsheet_id:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "Could not parse spreadsheet ID from URL"})

    from clearledgr.services.sheets_export import export_report_to_sheets as _export
    result = await _export(
        user_id=user.user_id,
        spreadsheet_id=spreadsheet_id,
        report_type=report_type,
        organization_id=org_id,
        period_days=body.get("period_days", 30),
    )
    return result


@router.get("/erp-vendors")
async def get_erp_vendor_list(
    organization_id: Optional[str] = Query(default=None),
    force_refresh: bool = Query(default=False),
    active_only: bool = Query(default=True),
    search: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Return full vendor directory from the connected ERP.

    Results are cached for 24h in org settings. Use ``force_refresh=true``
    to bypass cache and pull fresh data from the ERP.  Supports optional
    filters: ``active_only`` (default true) and ``search`` (case-insensitive
    name/email substring match).
    """
    org_id = _resolve_org_id(user, organization_id)

    from clearledgr.integrations.erp_router import (
        list_all_vendors as _list_vendors,
        get_erp_connection as _get_erp_conn,
    )

    vendors = await _list_vendors(
        organization_id=org_id,
        force_refresh=force_refresh,
    )

    # Apply filters
    if active_only:
        vendors = [v for v in vendors if v.get("active", True)]
    if search:
        needle = search.strip().lower()
        vendors = [
            v for v in vendors
            if needle in str(v.get("name") or "").lower()
            or needle in str(v.get("email") or "").lower()
        ]

    erp_conn = _get_erp_conn(org_id)
    erp_type = erp_conn.type if erp_conn else None

    return {
        "organization_id": org_id,
        "erp_type": erp_type,
        "vendors": vendors,
        "vendor_count": len(vendors),
        "filtered": bool(search or active_only),
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
    sql = (
        "SELECT * FROM vendor_profiles WHERE organization_id = %s ORDER BY invoice_count DESC LIMIT 200"
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


@router.get("/vendor-intelligence/duplicates")
def detect_vendor_duplicates(
    organization_id: Optional[str] = Query(default=None),
    threshold: float = Query(default=0.75, ge=0.5, le=1.0),
    user: TokenData = Depends(get_current_user),
):
    """Detect duplicate vendor profiles using fuzzy name matching."""
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.vendor_dedup import get_vendor_dedup_service
    service = get_vendor_dedup_service(org_id)
    clusters = service.detect_duplicates(threshold=threshold)
    return {
        "organization_id": org_id,
        "threshold": threshold,
        "clusters": clusters,
        "cluster_count": len(clusters),
    }


@router.post("/vendor-intelligence/merge")
def merge_vendors(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
    body: dict = {},
):
    """Merge duplicate vendors into a canonical profile.

    Body:
        canonical: str — the vendor name to keep
        duplicates: List[str] — vendor names to merge into canonical
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)

    canonical = (body.get("canonical") or "").strip()
    duplicates = body.get("duplicates") or []
    if not canonical or not duplicates:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"error": "canonical and duplicates are required"},
        )

    from clearledgr.services.vendor_dedup import get_vendor_dedup_service
    service = get_vendor_dedup_service(org_id)
    result = service.merge_vendors(canonical, duplicates)
    return result


@router.post("/vendor-intelligence/profiles/{vendor_name}/aliases")
def add_vendor_alias(
    vendor_name: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
    body: dict = {},
):
    """Add an alias to a vendor profile."""
    org_id = _resolve_org_id(user, organization_id)
    alias = (body.get("alias") or "").strip()
    if not alias:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "alias is required"})

    from clearledgr.services.vendor_dedup import get_vendor_dedup_service
    service = get_vendor_dedup_service(org_id)
    return service.add_alias(vendor_name, alias)


@router.delete("/vendor-intelligence/profiles/{vendor_name}/aliases/{alias}")
def remove_vendor_alias(
    vendor_name: str,
    alias: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Remove an alias from a vendor profile."""
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.vendor_dedup import get_vendor_dedup_service
    service = get_vendor_dedup_service(org_id)
    return service.remove_alias(vendor_name, alias)


@router.get("/disputes")
def list_disputes(
    organization_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    user: TokenData = Depends(get_current_user),
):
    """List disputes for this organization, optionally filtered by status."""
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    disputes = db.list_disputes(org_id, status=status, limit=limit)
    return {"disputes": disputes, "count": len(disputes)}


@router.get("/disputes/summary")
def get_dispute_summary(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Get dispute summary stats (counts by status and type)."""
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.dispute_service import get_dispute_service
    return get_dispute_service(org_id).get_dispute_summary()


@router.post("/disputes")
def create_dispute(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
    body: dict = {},
):
    """Open a new dispute for an AP item.

    Body:
        ap_item_id: str (required)
        dispute_type: str (required) — missing_po, wrong_amount, vendor_mismatch, missing_info, duplicate, bank_detail_change, other
        description: str (optional)
        vendor_name: str (optional, auto-filled from AP item)
        vendor_email: str (optional)
    """
    org_id = _resolve_org_id(user, organization_id)
    ap_item_id = (body.get("ap_item_id") or "").strip()
    dispute_type = (body.get("dispute_type") or "").strip()
    if not ap_item_id or not dispute_type:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "ap_item_id and dispute_type are required"})

    from clearledgr.services.dispute_service import get_dispute_service
    svc = get_dispute_service(org_id)
    return svc.open_dispute(
        ap_item_id=ap_item_id,
        dispute_type=dispute_type,
        description=body.get("description", ""),
        vendor_name=body.get("vendor_name", ""),
        vendor_email=body.get("vendor_email", ""),
    )


@router.post("/disputes/{dispute_id}/resolve")
def resolve_dispute(
    dispute_id: str,
    user: TokenData = Depends(get_current_user),
    body: dict = {},
):
    """Resolve a dispute with a resolution description."""
    resolution = (body.get("resolution") or "").strip()
    if not resolution:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "resolution is required"})

    db = get_db()
    dispute = db.get_dispute(dispute_id)
    if not dispute:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Dispute not found"})

    from clearledgr.services.dispute_service import get_dispute_service
    svc = get_dispute_service(dispute["organization_id"])
    svc.resolve_dispute(dispute_id, resolution)
    return {"status": "resolved", "id": dispute_id}


@router.post("/disputes/{dispute_id}/escalate")
def escalate_dispute(
    dispute_id: str,
    user: TokenData = Depends(get_current_user),
):
    """Escalate a dispute."""
    db = get_db()
    dispute = db.get_dispute(dispute_id)
    if not dispute:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Dispute not found"})

    from clearledgr.services.dispute_service import get_dispute_service
    svc = get_dispute_service(dispute["organization_id"])
    svc.escalate_dispute(dispute_id)
    return {"status": "escalated", "id": dispute_id}


@router.get("/delegation-rules")
def list_delegation_rules(
    organization_id: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
    user: TokenData = Depends(get_current_user),
):
    """List approval delegation rules."""
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.approval_delegation import get_delegation_service
    return {
        "rules": get_delegation_service(org_id).list_rules(active_only=active_only),
        "organization_id": org_id,
    }


@router.post("/delegation-rules")
def create_delegation_rule(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
    body: dict = {},
):
    """Create a delegation rule (approver A delegates to B).

    Body:
        delegator_email: str (required) — the approver going OOO
        delegate_email: str (required) — who takes over
        reason: str (optional) — e.g. "Annual leave 10-20 April"
        starts_at: str (optional) — ISO datetime, delegation starts
        ends_at: str (optional) — ISO datetime, delegation ends
    """
    org_id = _resolve_org_id(user, organization_id)
    delegator_email = (body.get("delegator_email") or "").strip()
    delegate_email = (body.get("delegate_email") or "").strip()
    if not delegator_email or not delegate_email:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "delegator_email and delegate_email are required"})

    from clearledgr.services.approval_delegation import get_delegation_service
    return get_delegation_service(org_id).create_rule(
        delegator_id=body.get("delegator_id", delegator_email),
        delegator_email=delegator_email,
        delegate_id=body.get("delegate_id", delegate_email),
        delegate_email=delegate_email,
        reason=body.get("reason", ""),
        starts_at=body.get("starts_at"),
        ends_at=body.get("ends_at"),
    )


@router.post("/delegation-rules/{rule_id}/deactivate")
def deactivate_delegation_rule(
    rule_id: str,
    user: TokenData = Depends(get_current_user),
):
    """Deactivate a delegation rule (approver returns from OOO)."""
    from clearledgr.services.approval_delegation import get_delegation_service
    org_id = _resolve_org_id(user, None)
    ok = get_delegation_service(org_id).deactivate_rule(rule_id)
    if not ok:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Rule not found"})
    return {"status": "deactivated", "id": rule_id}


@router.get("/period-close/current")
def get_current_period(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Get current accounting period and close status."""
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.period_close import get_period_close_service
    return get_period_close_service(org_id).get_current_period()


@router.get("/period-close/accruals/{period}")
def get_accrual_report(
    period: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Generate accrual report for a period (YYYY-MM).

    Returns uninvoiced liabilities: AP items that are approved/posted but not yet paid.
    """
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.period_close import get_period_close_service
    return get_period_close_service(org_id).generate_accrual_report(period)


@router.get("/period-close/backdated/{period}")
def get_backdated_invoices(
    period: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Find invoices received after cutoff that belong to a prior period."""
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.period_close import get_period_close_service
    items = get_period_close_service(org_id).detect_backdated_invoices(period)
    return {"period": period, "backdated_count": len(items), "items": items}


@router.post("/period-close/lock/{period}")
def lock_period(
    period: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Lock a period — prevents posting invoices dated in this month."""
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.period_close import get_period_close_service
    ok = get_period_close_service(org_id).lock_period(period)
    return {"status": "locked" if ok else "already_locked", "period": period}


@router.post("/period-close/unlock/{period}")
def unlock_period(
    period: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Unlock a period."""
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.period_close import get_period_close_service
    ok = get_period_close_service(org_id).unlock_period(period)
    return {"status": "unlocked" if ok else "not_locked", "period": period}


@router.post("/vendor-intelligence/reconcile-statement")
def reconcile_vendor_statement(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
    body: dict = {},
):
    """Reconcile a vendor statement against Clearledgr AP items.

    Body:
        vendor_name: str (required)
        statement_items: List[{date, reference, amount, description}] (required)
        period_days: int (optional, default 180)
    """
    org_id = _resolve_org_id(user, organization_id)
    vendor_name = (body.get("vendor_name") or "").strip()
    statement_items = body.get("statement_items") or []

    if not vendor_name or not statement_items:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"error": "vendor_name and statement_items are required"},
        )

    from clearledgr.services.vendor_statement_recon import get_vendor_statement_recon
    svc = get_vendor_statement_recon(org_id)
    return svc.reconcile(
        vendor_name=vendor_name,
        statement_items=statement_items,
        period_days=body.get("period_days", 180),
    )


@router.get("/tax-compliance/summary")
def get_tax_summary(
    organization_id: Optional[str] = Query(default=None),
    year: int = Query(default=0),
    buyer_country: str = Query(default=""),
    user: TokenData = Depends(get_current_user),
):
    """Tax compliance summary — vendor payment totals, VAT validation, reverse charge, WHT.

    Pass ``buyer_country`` (2-letter ISO code, e.g. "NG", "GB", "DE") to enable
    reverse charge and WHT detection.
    """
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.tax_compliance import get_tax_compliance_service
    return get_tax_compliance_service(org_id).generate_tax_summary(
        year=year, buyer_country=buyer_country,
    )


@router.post("/tax-compliance/validate-tax-id")
def validate_tax_id_endpoint(
    user: TokenData = Depends(get_current_user),
    body: dict = {},
):
    """Validate a tax ID / VAT number format by country."""
    tax_id = (body.get("tax_id") or "").strip()
    country_code = (body.get("country_code") or "").strip()
    if not tax_id:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "tax_id is required"})

    from clearledgr.services.tax_compliance import validate_tax_id
    return validate_tax_id(tax_id, country_code)


@router.get("/team/invites")
def list_team_invites(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    # Non-admins get an empty list (bootstrap calls this for all users).
    # Phase 2.3: "admin" here means Financial Controller or higher.
    from clearledgr.core.auth import has_admin_access
    if not has_admin_access(user.role):
        return {"organization_id": org_id, "invites": []}
    invites = get_db().list_team_invites(org_id)
    base = os.getenv("APP_BASE_URL", os.getenv("API_BASE_URL", "http://127.0.0.1:8010")).rstrip("/")
    for invite in invites:
        invite["invite_link"] = f"{base}/auth/google/start?invite_token={invite.get('token')}"
    return {"organization_id": org_id, "invites": invites}


@router.get("/team/approvers")
async def list_team_approvers(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    runtime = _resolve_slack_runtime(org_id)
    slack_connected = bool(runtime.get("connected"))
    slack_client = _get_slack_client(organization_id=org_id) if slack_connected else None

    approvers: List[Dict[str, Any]] = []
    for row in db.get_users(org_id):
        email = str(row.get("email") or "").strip().lower()
        if not email:
            continue

        name = str(row.get("name") or "").strip() or email
        slack_user_id = str(row.get("slack_user_id") or "").strip()
        slack_resolution = "resolved" if slack_user_id else ("not_connected" if not slack_connected else "not_found")

        if slack_connected and not slack_user_id and slack_client is not None:
            try:
                slack_user = await slack_client.lookup_user_by_email(email)
                resolved_id = str((slack_user or {}).get("id") or "").strip()
                if resolved_id:
                    slack_user_id = resolved_id
                    slack_resolution = "resolved"
                    try:
                        db.update_user(row["id"], slack_user_id=resolved_id)
                    except Exception:
                        pass
                else:
                    slack_resolution = "not_found"
            except Exception:
                slack_resolution = "lookup_failed"

        approvers.append(
            {
                "id": row.get("id"),
                "email": email,
                "name": name,
                "role": row.get("role") or "member",
                "slack_user_id": slack_user_id or None,
                "slack_resolution": slack_resolution,
                "approval_ready": bool(slack_user_id),
                "slack_mention": f"<@{slack_user_id}>" if slack_user_id else None,
            }
        )

    approvers.sort(key=lambda entry: ((entry.get("name") or entry.get("email") or "").lower(), entry.get("email") or ""))
    return {
        "organization_id": org_id,
        "slack_connected": slack_connected,
        "approvers": approvers,
    }


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
    return {"organization_id": org_id, "subscription": _get_subscription_service().get_subscription(org_id).to_dict()}


@router.get("/implementation/status")
async def get_implementation_status(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """§13 Implementation Service: get checklist status."""
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.implementation_service import get_implementation_status as _get_impl
    return await _get_impl(org_id)


class CompleteImplStepRequest(BaseModel):
    organization_id: Optional[str] = None
    step_id: str
    notes: str = ""


@router.post("/implementation/complete-step")
async def complete_impl_step(
    request: CompleteImplStepRequest,
    user: TokenData = Depends(get_current_user),
):
    """§13 Implementation Service: mark a step complete."""
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    actor = getattr(user, "email", None) or user.user_id
    from clearledgr.services.implementation_service import complete_implementation_step
    return await complete_implementation_step(org_id, request.step_id, actor, request.notes)


@router.get("/subscription/billing-summary")
def get_billing_summary(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """§13: Metered billing summary — seats, volume bands, credits, estimated total."""
    org_id = _resolve_org_id(user, organization_id)
    return _get_subscription_service().get_billing_summary(org_id)


@router.get("/llm-budget/status")
def get_llm_budget_status(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Read-only view of the LLM cost budget for the in-product banner.

    The banner uses this to tell an operator why the agent paused and
    whether they can lift it without leaving Gmail. Rendered at the
    top of ThreadSidebar + HomePage whenever ``paused`` is true.

    Returns:
        paused: whether calls are currently being refused
        paused_at: ISO timestamp of the pause event (null if not paused)
        cost_usd: month-to-date Claude spend for this workspace
        cap_usd: the effective monthly hard cap (tier default or org override)
        period_start / period_end: UTC month boundaries
        can_override: whether the calling user has rank ≥ CFO and can lift
    """
    from clearledgr.core.auth import has_cfo
    from datetime import datetime as _dt
    from calendar import monthrange

    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    org = db.get_organization(org_id) or {}

    paused_at = org.get("llm_cost_paused_at")
    paused = bool(paused_at)

    svc = _get_subscription_service()
    try:
        cap_usd = float(svc.get_effective_llm_cost_cap(org_id))
    except Exception:
        cap_usd = 0.0
    try:
        cost_row = svc._get_llm_cost_this_month(org_id) or {}
        cost_usd = float(cost_row.get("total_cost_usd") or 0.0)
    except Exception:
        cost_usd = 0.0

    now = _dt.now(timezone.utc)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_day = monthrange(now.year, now.month)[1]
    period_end = period_start.replace(day=last_day, hour=23, minute=59, second=59)

    return {
        "organization_id": org_id,
        "paused": paused,
        "paused_at": paused_at,
        "cost_usd": round(cost_usd, 4),
        "cap_usd": round(cap_usd, 4),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "can_override": has_cfo(user.role),
    }


class LLMBudgetOverrideRequest(BaseModel):
    """CFO-authorized override of the LLM runaway-spend guard.

    Clears ``organizations.llm_cost_paused_at`` so Claude calls resume.
    Requires CFO rank or higher (thesis §8: the CFO role is the
    financial authority — same role that can modify fraud controls).
    ``reason`` is required and written to the audit trail — this is
    an override of a cost ceiling, not a silent nudge.
    """
    organization_id: Optional[str] = None
    reason: str = Field(..., min_length=1, max_length=500)


@router.post("/llm-budget/override")
def override_llm_budget_pause(
    request: LLMBudgetOverrideRequest,
    user: TokenData = Depends(get_current_user),
):
    """Clear the LLM cost tombstone for this workspace.

    The runaway-spend guard in `llm_gateway.py` pauses Claude calls
    when a workspace crosses its monthly hard cap. This endpoint lets
    the customer's CFO (or higher) lift the pause without waiting for
    CS. Every override is audit-logged with the supplied reason —
    cost ceilings are load-bearing policy, not casual configuration.
    """
    from clearledgr.core.auth import has_cfo
    if not has_cfo(user.role):
        raise HTTPException(
            status_code=403,
            detail="cfo_role_required — llm budget override is CFO-authorized",
        )

    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()

    # Load + clear the tombstone. If it was never set, the endpoint
    # is still idempotent — clearing null is a no-op.
    try:
        db.update_organization(org_id, llm_cost_paused_at=None)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"failed_to_clear_pause: {exc}",
        )

    cleared_at = datetime.now(timezone.utc).isoformat()
    try:
        db.append_audit_event({
            "event_type": "llm_budget_override_applied",
            "box_id": org_id,
            "box_type": "organization",
            "actor_type": "user",
            "actor_id": user.email or user.user_id or "unknown",
            "organization_id": org_id,
            "decision_reason": request.reason,
            "payload_json": {
                "cleared_at": cleared_at,
                "actor_role": user.role,
                "source": "customer_cfo",
            },
        })
    except Exception:
        pass  # Audit failure does not block the override.

    return {
        "status": "cleared",
        "organization_id": org_id,
        "cleared_at": cleared_at,
    }


@router.patch("/subscription/plan")
def patch_subscription_plan(
    request: SubscriptionPlanPatchRequest,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    service = _get_subscription_service()
    PlanTier = _plan_tier()
    plan = request.plan.lower().strip()

    if plan == PlanTier.FREE.value:
        sub = service.downgrade_plan(org_id, PlanTier.FREE)
    elif plan == "trial":
        sub = service.start_trial(org_id)
    elif plan == PlanTier.STARTER.value:
        sub = service.upgrade_plan(org_id, PlanTier.STARTER)
    elif plan == PlanTier.PROFESSIONAL.value:
        sub = service.upgrade_plan(org_id, PlanTier.PROFESSIONAL)
    elif plan == PlanTier.ENTERPRISE.value:
        # §13: Enterprise contracts are annual only
        current_sub = service.get_subscription(org_id)
        if current_sub.billing_cycle != "yearly":
            raise HTTPException(
                status_code=400,
                detail="Enterprise plan requires annual billing. Switch to annual billing first.",
            )
        sub = service.upgrade_plan(org_id, PlanTier.ENTERPRISE)
    else:
        raise HTTPException(status_code=400, detail="invalid_plan")
    return {"success": True, "organization_id": org_id, "subscription": sub.to_dict()}


# ------------------------------------------------------------------
# Entity management (multi-entity support)
# ------------------------------------------------------------------

class EntityCreateRequest(BaseModel):
    organization_id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=200)
    code: Optional[str] = Field(default=None, max_length=50)
    erp_connection_id: Optional[str] = None
    gl_mapping: Optional[Dict[str, Any]] = None
    approval_rules: Optional[Dict[str, Any]] = None
    default_currency: str = Field(default="USD", max_length=10)


class EntityUpdateRequest(BaseModel):
    organization_id: Optional[str] = None
    name: Optional[str] = Field(default=None, max_length=200)
    code: Optional[str] = Field(default=None, max_length=50)
    erp_connection_id: Optional[str] = None
    gl_mapping: Optional[Dict[str, Any]] = None
    approval_rules: Optional[Dict[str, Any]] = None
    default_currency: Optional[str] = Field(default=None, max_length=10)


@router.get("/entities")
def list_entities(
    organization_id: Optional[str] = Query(default=None),
    include_inactive: bool = Query(default=False),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    entities = db.list_entities(org_id, include_inactive=include_inactive)
    return {"organization_id": org_id, "entities": entities}


@router.post("/entities")
def create_entity(
    request: EntityCreateRequest,
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    entity = db.create_entity(
        organization_id=org_id,
        name=request.name,
        code=request.code,
        erp_connection_id=request.erp_connection_id,
        gl_mapping=request.gl_mapping,
        approval_rules=request.approval_rules,
        currency=request.default_currency,
    )
    return {"success": True, "entity": entity}


@router.patch("/entities/{entity_id}")
def update_entity(
    entity_id: str,
    request: EntityUpdateRequest,
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    # Verify entity belongs to this org
    existing = db.get_entity(entity_id)
    if not existing or existing.get("organization_id") != org_id:
        raise HTTPException(status_code=404, detail="entity_not_found")
    updates: Dict[str, Any] = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.code is not None:
        updates["code"] = request.code
    if request.erp_connection_id is not None:
        updates["erp_connection_id"] = request.erp_connection_id
    if request.gl_mapping is not None:
        updates["gl_mapping"] = request.gl_mapping
    if request.approval_rules is not None:
        updates["approval_rules"] = request.approval_rules
    if request.default_currency is not None:
        updates["default_currency"] = request.default_currency
    if not updates:
        raise HTTPException(status_code=400, detail="no_fields_to_update")
    db.update_entity(entity_id, **updates)
    return {"success": True, "entity": db.get_entity(entity_id)}


@router.delete("/entities/{entity_id}")
def deactivate_entity(
    entity_id: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    existing = db.get_entity(entity_id)
    if not existing or existing.get("organization_id") != org_id:
        raise HTTPException(status_code=404, detail="entity_not_found")
    db.delete_entity(entity_id)
    return {"success": True, "entity_id": entity_id, "deactivated": True}


# ---------------------------------------------------------------------------
# Payment tracking (informational — agent never executes payments)
# ---------------------------------------------------------------------------

class PaymentStatusUpdate(BaseModel):
    status: Optional[str] = None
    payment_method: Optional[str] = None
    payment_reference: Optional[str] = None
    scheduled_date: Optional[str] = None
    completed_date: Optional[str] = None
    notes: Optional[str] = None


@router.get("/payments")
def list_payments(
    organization_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    vendor: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: TokenData = Depends(get_current_user),
):
    """List payment tracking records for an organization.

    Filter by status (ready_for_payment, scheduled, processing, completed,
    failed, cancelled) and/or vendor name.
    """
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    payments = db.list_payments_by_org(
        org_id, status=status, vendor=vendor, limit=limit, offset=offset,
    )
    return {"payments": payments, "count": len(payments)}


@router.patch("/payments/{payment_id}")
def update_payment_status(
    payment_id: str,
    body: PaymentStatusUpdate,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Update a payment record status.

    Humans use this to mark payments as scheduled, processing, completed,
    cancelled, or failed.  The agent never calls this endpoint.
    """
    from clearledgr.services.payment_models import PAYMENT_STATUSES

    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    existing = db.get_payment(payment_id)
    if not existing:
        raise HTTPException(status_code=404, detail="payment_not_found")
    if existing.get("organization_id") != org_id:
        raise HTTPException(status_code=403, detail="payment_org_mismatch")

    updates = {k: v for k, v in body.dict(exclude_unset=True).items() if v is not None}
    if "status" in updates and updates["status"] not in PAYMENT_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid_status: must be one of {sorted(PAYMENT_STATUSES)}",
        )

    if not updates:
        return existing

    updated = db.update_payment(payment_id, **updates)
    return updated or existing


@router.get("/payments/summary")
def get_payments_summary(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Return payment counts grouped by status."""
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    summary = db.get_payment_summary(org_id)
    return {"summary": summary, "total": sum(summary.values())}


# ------------------------------------------------------------------
# Spend analysis
# ------------------------------------------------------------------

@router.get("/spend-analysis")
def get_spend_analysis(
    period_days: int = Query(default=30, ge=1, le=365),
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Portfolio-level spend analysis: top vendors, GL breakdown, trends, anomalies."""
    org_id = _resolve_org_id(user, organization_id)
    from clearledgr.services.spend_analysis import get_spend_analysis_service
    service = get_spend_analysis_service(org_id)
    return service.analyze(period_days=period_days)


@router.get("/health")
def get_admin_health(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    health = _build_health(org_id, user)
    evidence = _get_ga_readiness(org_id)
    rollback_controls = _get_rollback_controls(org_id)
    health["launch_controls"] = {
        "rollback_controls": rollback_controls,
        "ga_readiness_summary": _summarize_ga_readiness(evidence, rollback_controls=rollback_controls),
    }
    return health
