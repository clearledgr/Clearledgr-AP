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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field

from solden.core.auth import TokenData, get_current_user
from solden.core.database import get_db
from solden.core.http_client import get_http_client
from solden.core.org_utils import require_org, assert_org_id

logger = logging.getLogger(__name__)


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


# Hosts the same backend serves during the Clearledgr → Solden rename
# window. When a request lands on one of these, OAuth redirect URIs +
# Slack install URLs are generated against the request's own hostname
# (so a user who arrives at workspace.soldenai.com is redirected back
# through api.soldenai.com, not the env-configured clearledgr host).
_PUBLIC_BASE_URL_KNOWN_SUFFIXES = (".soldenai.com", ".clearledgr.com")


def _request_origin_base_url(request: Optional["Request"]) -> Optional[str]:
    if request is None:
        return None
    host = (getattr(request.url, "hostname", None) or "").lower()
    if not host:
        return None
    for suffix in _PUBLIC_BASE_URL_KNOWN_SUFFIXES:
        apex = suffix.lstrip(".")
        if host == apex or host.endswith(suffix):
            # Production hosts (*.soldenai.com / *.clearledgr.com) are
            # always behind Railway TLS termination, so the request seen
            # by the app is HTTP even when the customer's browser made an
            # HTTPS request. Honour ``X-Forwarded-Proto`` when present,
            # otherwise force ``https`` because we know these hostnames
            # are HTTPS-only in every environment that serves them.
            forwarded_proto = (
                request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
            )
            scheme = forwarded_proto or "https"
            return f"{scheme}://{host}".rstrip("/")
    return None


def _public_app_base_url(request: Optional["Request"] = None) -> str:
    derived = _request_origin_base_url(request)
    if derived:
        return derived
    base = str(
        os.getenv("APP_BASE_URL", os.getenv("API_BASE_URL", "http://127.0.0.1:8010")) or ""
    ).strip().rstrip("/")
    return base or "http://127.0.0.1:8010"


def _slack_redirect_uri(request: Optional["Request"] = None) -> str:
    # When the request matches a known Solden-family host, the redirect
    # URI is generated against THAT host so Slack returns the user to
    # the same brand they started on. Only fall back to the static env
    # override when the request host is unknown (local dev, custom
    # deployments) — otherwise a stale SLACK_REDIRECT_URI=api.clearledgr.com
    # would break installs initiated from workspace.soldenai.com.
    derived = _request_origin_base_url(request)
    if derived:
        return f"{derived}/api/workspace/integrations/slack/install/callback"
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
    from solden.core.secrets import require_secret
    return require_secret("SOLDEN_SECRET_KEY")


def _get_ga_readiness(*args, **kwargs):
    from solden.core.launch_controls import get_ga_readiness

    return get_ga_readiness(*args, **kwargs)


def _get_rollback_controls(*args, **kwargs):
    from solden.core.launch_controls import get_rollback_controls

    return get_rollback_controls(*args, **kwargs)


def _set_ga_readiness(*args, **kwargs):
    from solden.core.launch_controls import set_ga_readiness

    return set_ga_readiness(*args, **kwargs)


def _set_rollback_controls(*args, **kwargs):
    from solden.core.launch_controls import set_rollback_controls

    return set_rollback_controls(*args, **kwargs)


def _summarize_ga_readiness(*args, **kwargs):
    from solden.core.launch_controls import summarize_ga_readiness

    return summarize_ga_readiness(*args, **kwargs)


def _evaluate_erp_connector_readiness(*args, **kwargs):
    from solden.services.erp_readiness import evaluate_erp_connector_readiness

    return evaluate_erp_connector_readiness(*args, **kwargs)


def _get_learning_calibration_service(*args, **kwargs):
    from solden.services.learning_calibration import get_learning_calibration_service

    return get_learning_calibration_service(*args, **kwargs)


def _ap_policy_name() -> str:
    from solden.services.policy_compliance import AP_POLICY_NAME

    return AP_POLICY_NAME


def _get_approval_automation_policy(*args, **kwargs):
    from solden.services.policy_compliance import get_approval_automation_policy

    return get_approval_automation_policy(*args, **kwargs)


def _get_policy_compliance(*args, **kwargs):
    from solden.services.policy_compliance import get_policy_compliance

    return get_policy_compliance(*args, **kwargs)


def _generate_auth_url(*args, **kwargs):
    from solden.services.gmail_api import generate_auth_url

    return generate_auth_url(*args, **kwargs)


def _get_google_oauth_config() -> Dict[str, Any]:
    from solden.services.gmail_api import get_google_oauth_config

    return get_google_oauth_config()


def _slack_api_client_class():
    from solden.services.slack_api import SlackAPIClient

    return SlackAPIClient


def _slack_api_error_type():
    from solden.services.slack_api import SlackAPIError

    return SlackAPIError


def _resolve_slack_runtime(*args, **kwargs):
    from solden.services.slack_api import resolve_slack_runtime

    return resolve_slack_runtime(*args, **kwargs)


def _get_slack_client(*args, **kwargs):
    from solden.services.slack_api import get_slack_client

    return get_slack_client(*args, **kwargs)


def _teams_api_client_class():
    from solden.services.teams_api import TeamsAPIClient

    return TeamsAPIClient


def _get_subscription_service():
    from solden.services.subscription import get_subscription_service

    return get_subscription_service()


def _plan_tier():
    from solden.services.subscription import PlanTier

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
    from solden.core.auth import has_admin_access
    if not has_admin_access(user.role):
        raise HTTPException(status_code=403, detail="admin_role_required")


def _require_ops_access(user: TokenData) -> None:
    # Phase 2.3: ops access == AP Manager or higher.
    from solden.core.auth import has_ops_access
    if not has_ops_access(user.role):
        raise HTTPException(status_code=403, detail="ops_role_required")


def _workspace_capabilities(
    role: Optional[str],
    *,
    workspace_role: Optional[str] = None,
    ap_role: Optional[str] = None,
) -> Dict[str, bool]:
    """Flat (legacy) capability matrix.

    Preserves the pre-v89 dict shape the frontend already consumes —
    every key is preserved, the values are recomputed from the two
    axes. The new tree shape is produced by ``_workspace_capabilities_tree``;
    the two are kept in sync for the sweep window.

    ``workspace_role`` and ``ap_role`` are the canonical inputs.
    ``role`` is preserved as a legacy fallback so old callers that
    still pass a single positional value continue to resolve via
    ``normalize_workspace_role``.
    """
    from solden.core.auth import (
        has_ap_approver,
        has_ap_clerk,
        has_ap_controller,
        has_workspace_admin,
        has_workspace_member,
        normalize_ap_role,
        normalize_workspace_role,
    )

    resolved_workspace = (
        normalize_workspace_role(workspace_role)
        or normalize_workspace_role(role)
        or ""
    )
    resolved_ap = (
        normalize_ap_role(ap_role)
        or normalize_ap_role(role)
        or ""
    )

    has_any = bool(resolved_workspace)
    is_admin = has_workspace_admin(resolved_workspace)
    is_member = has_workspace_member(resolved_workspace)
    ap_clerk_plus = has_ap_clerk(resolved_ap)
    ap_approver_plus = has_ap_approver(resolved_ap)
    ap_controller = has_ap_controller(resolved_ap)

    return {
        # Workspace-axis views — everyone authenticated sees navigation.
        "view_home": True,
        "view_pipeline": True,
        "view_review": has_any,
        "view_upcoming": has_any,
        "view_activity": has_any,
        "view_vendors": has_any,
        "view_templates": has_any,
        "view_connections": has_any,
        "view_rules": has_any,
        "view_team": has_any,
        "view_company": has_any,
        "view_plan": has_any,
        "view_reconciliation": has_any,
        "view_system_status": has_any,
        "view_reports": has_any,
        # Workspace-axis ops + manage gates. ``view_ops_workspace`` /
        # ``operate_records`` historically meant "AP Manager or above"
        # (pre-v89) — under the two-axis model that's "workspace member
        # plus AP clerk-or-above on at least one Box". Membership-only
        # users still see workspace nav but can't act on AP records
        # without an AP role assignment.
        "view_ops_workspace": is_member and ap_clerk_plus,
        "operate_records": is_member and ap_clerk_plus,
        "manage_connections": is_admin,
        "manage_rules": is_admin,
        "manage_team": is_admin,
        "manage_company": is_admin,
        "manage_plan": is_admin,
        "manage_admin_pages": is_admin,
        # AP-axis box-specific actions surfaced here so the frontend
        # can check a single dict on each render. New code should
        # consume the tree shape (``capabilities.ap_item.*``) instead.
        "approve_invoice": ap_approver_plus,
        "post_to_erp": ap_approver_plus,
        "reverse_invoice_post": ap_controller,
        "override_post": ap_controller,
        "mark_duplicate": ap_approver_plus,
        "reclassify_invoice": ap_clerk_plus,
        "resubmit_invoice": ap_clerk_plus,
    }


def _workspace_capabilities_tree(
    workspace_role: Optional[str],
    ap_role: Optional[str],
) -> Dict[str, Dict[str, bool]]:
    """Tree-shaped capability matrix for the v89 bootstrap response.

    ``{ workspace: { manage_users: bool, ... }, ap_item: { approve: ... } }``.
    When a second Box type ships, it adds a sibling key (``procurement``,
    ``audit_engagement``, etc.) with its own capability set. Each Box
    declares its capability list at the registry layer.
    """
    from solden.core.auth import (
        has_ap_approver,
        has_ap_clerk,
        has_ap_controller,
        has_ap_viewer,
        has_workspace_admin,
        has_workspace_member,
        has_workspace_owner,
        has_workspace_read_only,
    )

    is_owner = has_workspace_owner(workspace_role)
    is_admin = has_workspace_admin(workspace_role)
    is_member = has_workspace_member(workspace_role)
    has_any = has_workspace_read_only(workspace_role)

    return {
        "workspace": {
            "view_workspace": has_any,
            "view_records": has_any,
            "view_vendors": has_any,
            "view_reports": has_any,
            "view_audit_log": has_any,
            "view_activity": has_any,
            "view_exceptions": has_any,
            "view_plan": has_any,
            "view_settings": is_member,
            "view_connections": is_member,
            "view_rules": is_member,
            "view_api_keys": is_admin,
            "manage_users": is_admin,
            "manage_connections": is_admin,
            "manage_rules": is_admin,
            "manage_settings": is_admin,
            "manage_plan": is_admin,
            "manage_api_keys": is_admin,
            "manage_workspace": is_owner,
        },
        "ap_item": {
            "view_ap_records": has_ap_viewer(ap_role) or is_member,
            "edit_ap_record": has_ap_clerk(ap_role),
            "manually_classify_invoice": has_ap_clerk(ap_role),
            "resubmit_invoice": has_ap_clerk(ap_role),
            "approve_invoice": has_ap_approver(ap_role),
            "reject_invoice": has_ap_approver(ap_role),
            "escalate_approval": has_ap_approver(ap_role),
            "reassign_approval": has_ap_approver(ap_role),
            "request_info": has_ap_approver(ap_role),
            "request_approval": has_ap_approver(ap_role),
            "post_to_erp": has_ap_approver(ap_role),
            "snooze_invoice": has_ap_approver(ap_role),
            "mark_duplicate": has_ap_approver(ap_role),
            "override_post": has_ap_controller(ap_role),
            "reverse_invoice_post": has_ap_controller(ap_role),
        },
    }


def _resolve_org_id(user: TokenData, organization_id: Optional[str]) -> str:
    """Resolve + enforce tenant scope on the workspace surface.

    Previously allowed owner role to access any org, cross-tenant
    vulnerability. Owner controls authority within the org, not across
    orgs. No platform-level super-admin concept exists on tenant APIs.

    M19 sweep: delegates to ``require_org`` for the canonical
    resolution + cross-check, but re-raises the existing
    ``"org_access_denied"`` detail string on mismatch so existing
    workspace test fixtures + frontend error handling that key on
    the legacy detail keep working. ``require_org``'s native
    ``"org_mismatch"`` / ``"user_missing_organization_id"`` strings
    are exposed on every other surface (ops, ap_items_action_routes,
    teams, slack), the workspace surface is the back-compat
    exception until the next breaking-change pass.
    """
    try:
        return require_org(user, requested=organization_id)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=403, detail="org_access_denied") from exc
        raise


def _default_org_name(user: TokenData, org_id: str) -> str:
    """Derive a friendlier display name for a brand-new organization.

    Used as the auto-provision default when the bootstrap call creates
    an organizations row that doesn't exist yet. Previously the name
    was set to the org_id itself ("default"), so every new tenant
    landed with "Workspace default" in the topbar and had to discover
    the rename UI.

    Order of preference (Linear / Vercel / Mercury all do similar):
      1. The user's email domain mapped to a title-cased label
         ("acme.com" → "Acme"). Skips public providers (gmail.com,
         outlook.com, etc.) where the domain isn't the org.
      2. The user's first name + "'s workspace" ("Mo's workspace").
      3. The literal org_id (legacy fallback — preserves old behaviour
         when no signal is available).

    Owners can rename it at any time via Settings → Workspace.
    """
    public_email_domains = {
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com",
        "yahoo.com", "icloud.com", "me.com", "live.com", "aol.com",
        "proton.me", "protonmail.com", "pm.me",
    }
    email = (getattr(user, "email", "") or "").strip().lower()
    if "@" in email:
        domain = email.rsplit("@", 1)[-1].strip()
        if domain and domain not in public_email_domains:
            label = domain.split(".")[0]
            if label:
                return label[:1].upper() + label[1:]

    name = (getattr(user, "name", "") or "").strip()
    if name:
        first = name.split()[0]
        if first:
            return f"{first}'s workspace"

    if "@" in email:
        local = email.split("@", 1)[0].strip()
        if local:
            return f"{local[:1].upper()}{local[1:]}'s workspace"

    return org_id or "Workspace"


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


def _outlook_status_for_org(organization_id: str, user: TokenData) -> Dict[str, Any]:
    """Outlook intake status for the workspace bootstrap response.

    Mirrors ``_gmail_status_for_org`` shape so the Connections page can
    treat Gmail + Outlook symmetrically — they're peer intake channels.

    When ``FEATURE_OUTLOOK_ENABLED`` is off, returns a terminal
    "disabled_in_v1" payload so the SPA can show a "post-launch" label
    instead of a Connect button that would 404 on click. Mirrors the
    teams helper below.
    """
    from solden.core.feature_flags import is_outlook_enabled
    if not is_outlook_enabled():
        return {
            "name": "outlook",
            "connected": False,
            "status": "disabled_in_v1",
            "mode": "oauth",
            "email": None,
            "durable": False,
            "has_refresh_token": False,
            "requires_reconnect": False,
            "last_sync_at": None,
            "watch_status": "disabled",
            "watch_expires_soon": False,
            "invoices_processed": 0,
            "reason": (
                "FEATURE_OUTLOOK_ENABLED is off. Flip the env var on "
                "api/worker/beat once MICROSOFT_CLIENT_ID + SECRET are "
                "configured to enable the Outlook intake channel."
            ),
        }

    db = get_db()
    # Resolve the Outlook token in the same order of specificity as
    # the Gmail helper: exact user_id → exact email → any token in
    # this org. Mirrors GmailTokenStore.get() lookup semantics.
    token_row = db.get_oauth_token(user.user_id, "outlook")
    if not token_row:
        user_email = str(getattr(user, "email", "") or "").strip().lower()
        if user_email:
            token_row = db.get_oauth_token_by_email(user_email, "outlook")
    if not token_row:
        user_ids = {str(item.get("id")) for item in db.get_users(organization_id, include_inactive=False)}
        for candidate in db.list_oauth_tokens(provider="outlook"):
            if str(candidate.get("user_id")) in user_ids:
                token_row = candidate
                break

    connected = bool(token_row)
    has_refresh_token = bool(token_row and str(token_row.get("refresh_token") or "").strip())
    durable = connected and has_refresh_token
    ap_state = db.get_outlook_autopilot_state(user.user_id) or {}

    # Microsoft Graph subscriptions expire after ~3 days unless renewed.
    # Surface the subscription state so the operator sees the same
    # "Watch active / polling / reconnect required / disconnected"
    # progression Gmail uses, just keyed on Graph subscription state.
    subscription_id = ap_state.get("subscription_id")
    subscription_exp = ap_state.get("subscription_expiration")
    watch_active = False
    watch_expires_soon = False
    if subscription_id and subscription_exp:
        try:
            exp_dt = datetime.fromisoformat(str(subscription_exp).replace("Z", "+00:00"))
            now = _utcnow()
            watch_active = exp_dt > now
            watch_expires_soon = watch_active and (exp_dt - now) < timedelta(hours=24)
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

    status = "connected" if durable else ("reconnect_required" if connected else "disconnected")
    return {
        "name": "outlook",
        "connected": connected,
        "status": status,
        "mode": "oauth",
        "email": token_row.get("email") if token_row else None,
        "durable": durable,
        "has_refresh_token": has_refresh_token,
        "requires_reconnect": connected and not durable,
        "last_sync_at": ap_state.get("last_scan_at"),
        "subscription_id": subscription_id,
        "subscription_expiration": subscription_exp,
        "watch_status": watch_status,
        "watch_expires_soon": watch_expires_soon,
        "last_error": ap_state.get("last_error"),
    }


def _teams_status_for_org(organization_id: str) -> Dict[str, Any]:
    # §12 / §6.8 — when Teams is disabled in V1, the bootstrap
    # response reports a terminal "disabled_in_v1" status so the
    # extension's integrations UI shows a clear "post-launch" label
    # instead of a "Connect Teams" CTA that would 404 when clicked.
    from solden.core.feature_flags import is_teams_enabled
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


def _build_health(
    organization_id: str,
    user: TokenData,
    http_request: Optional[Request] = None,
) -> Dict[str, Any]:
    db = get_db()
    org = db.ensure_organization(organization_id, organization_name=_default_org_name(user, organization_id))
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
    from solden.core.feature_flags import is_teams_enabled
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

    slack_redirect_uri = _slack_redirect_uri(http_request)

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
    message: str = "Solden admin test: Slack approval channel is connected."


class TeamsWebhookRequest(BaseModel):
    organization_id: Optional[str] = None
    webhook_url: str = Field(..., min_length=8, max_length=1024)


class TeamsTestRequest(BaseModel):
    organization_id: Optional[str] = None
    message: str = "Solden admin test: Teams approval channel is connected."


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
    # Legacy single-axis ``role`` (pre-v89). Normalised through the
    # workspace-role mapping at handler time. Kept on the wire for the
    # sweep window so older SPA builds still hit a valid request.
    role: str = Field(default="member")
    # v89 two-axis fields. ``workspace_role`` is the org-governance
    # axis ({owner, admin, member, read_only}); ``box_roles`` is a
    # ``{box_type: role}`` map for per-Box-type domain rank. When the
    # caller omits these, the handler derives them from ``role`` via
    # the legacy mapping.
    workspace_role: Optional[str] = None
    box_roles: Optional[Dict[str, str]] = None
    expires_in_days: int = Field(default=7, ge=1, le=30)
    # Module 6 Pass D — optional restriction to specific legal entities.
    # On invite accept the auth handler writes one user_entity_roles
    # row per entity_id, so the user lands scoped from day one. Empty
    # list / omitted = no restriction (org-wide access).
    entity_restrictions: Optional[List[str]] = Field(
        default=None, max_length=64,
    )


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


class OutlookConnectStartRequest(BaseModel):
    organization_id: Optional[str] = None
    redirect_path: str = Field(default="/connections", max_length=512)


class OutlookDisconnectRequest(BaseModel):
    organization_id: Optional[str] = None


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
        from solden.services.gmail_autopilot import ensure_gmail_autopilot_progress

        await ensure_gmail_autopilot_progress(request.app, user_id=str(getattr(user, "user_id", "") or "").strip())
    except Exception:
        pass
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    org = db.ensure_organization(org_id, organization_name=_default_org_name(user, org_id))
    org_settings = _load_org_settings(org)
    subscription = _get_subscription_service().get_subscription(org_id).to_dict()
    health = _build_health(org_id, user, request)

    current_user = db.get_user(user.user_id) or {}
    integrations = [
        _gmail_status_for_org(org_id, user),
        _outlook_status_for_org(org_id, user),
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

    # Intake channel: Gmail or Outlook satisfies the inbox-source side
    # of onboarding. Either is sufficient for the AP pipeline to flow.
    gmail_connected = _is_connected("gmail")
    outlook_connected = _is_connected("outlook")
    inbox_connected = gmail_connected or outlook_connected
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
    if derived_step >= 3 and inbox_connected:
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
                "name": "Connect intake channel",
                "description": "Connect Gmail or Outlook so invoices arriving by email flow into Solden. Install the matching browser sidebar (Gmail extension / Outlook add-in) for per-thread context. ERP-native customers (SAP S/4HANA, NetSuite-only intake) can skip.",
                "time_estimate": "2 minutes",
                "required": False,
            },
        ],
    }
    # v89 two-axis auth: emit workspace_role + box_roles on the user,
    # plus a tree-shaped capability matrix that splits workspace vs.
    # per-Box-type. The flat ``capabilities`` dict stays for the sweep
    # window so the frontend's pre-v89 ``hasCapability(bootstrap, 'X')``
    # calls continue to resolve.
    from solden.core.auth import (
        WORKSPACE_ROLE_MEMBER,
        get_user_box_roles,
        normalize_ap_role,
        normalize_workspace_role,
    )
    current_role = current_user.get("role") or getattr(user, "role", None)
    current_workspace_role = (
        normalize_workspace_role(current_user.get("workspace_role"))
        or normalize_workspace_role(getattr(user, "workspace_role", None))
        or normalize_workspace_role(current_role)
        or WORKSPACE_ROLE_MEMBER
    )
    box_roles = get_user_box_roles(
        current_user.get("id") or user.user_id,
        org_id,
    )
    current_ap_role = normalize_ap_role(box_roles.get("ap_item")) or normalize_ap_role(current_role) or ""
    capabilities = _workspace_capabilities(
        current_role,
        workspace_role=current_workspace_role,
        ap_role=current_ap_role,
    )
    capabilities_tree = _workspace_capabilities_tree(
        current_workspace_role, current_ap_role,
    )

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
            "role": current_role,  # DEPRECATED post-v89
            "workspace_role": current_workspace_role,
            "box_roles": box_roles,
            "organization_id": org_id,
            "preferences": _load_user_preferences(current_user),
            "capabilities": capabilities,
            "capabilities_tree": capabilities_tree,
        },
        "capabilities": capabilities,
        "capabilities_tree": capabilities_tree,
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
        from solden.services.trust_arc import get_trust_arc_status
        return get_trust_arc_status(db, org_id)
    except Exception:
        return {"phase": "not_started"}


def _safe_dashboard_stats(org_id: str) -> Dict[str, Any]:
    """Load dashboard stats for bootstrap. Never fails — returns empty on error."""
    try:
        db = get_db()
        pipeline = db.get_invoice_pipeline(org_id) if hasattr(db, "get_invoice_pipeline") else {}
        from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz
        today = _date.today().isoformat()
        seven_days_ago_iso = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
        # Module 1 spec stat cards (Live Operations, line 76):
        #   in flight | awaiting approval | processed this week | agent exceptions
        # "in flight" = anything not in a terminal state. Terminals are
        # closed / reversed / rejected per ap_states.py.
        open_states = (
            "received", "validated", "needs_info", "needs_approval",
            "pending_approval", "approved", "ready_to_post",
        )
        in_flight = sum(len(pipeline.get(s, [])) for s in open_states) if pipeline else 0
        pending = len(pipeline.get("needs_approval", []) + pipeline.get("pending_approval", []))  if pipeline else 0
        posted = sum(1 for inv in pipeline.get("posted_to_erp", []) + pipeline.get("closed", []) if isinstance(inv, dict) and str(inv.get("created_at", "")).startswith(today)) if pipeline else 0
        rejected = sum(1 for inv in pipeline.get("rejected", []) if isinstance(inv, dict) and str(inv.get("created_at", "")).startswith(today)) if pipeline else 0
        # Last 7 calendar days, posted-or-closed terminals.
        processed_week = sum(
            1
            for state in ("posted_to_erp", "closed")
            for inv in pipeline.get(state, [])
            if isinstance(inv, dict) and str(inv.get("updated_at") or inv.get("created_at") or "") >= seven_days_ago_iso
        ) if pipeline else 0
        total = sum(len(v) for v in pipeline.values()) if pipeline else 0
        approval_sla_minutes = _approval_sla_minutes_for_org(org_id)
        kpis = db.get_ap_kpis(org_id, approval_sla_minutes=approval_sla_minutes) if hasattr(db, "get_ap_kpis") else {}
        agentic_snapshot = _build_agentic_snapshot(kpis)
        pilot_snapshot = _build_pilot_snapshot(kpis)
        proof_snapshot = _build_proof_snapshot(kpis)
        return {
            "total_invoices": total,
            "in_flight": in_flight,
            "pending_approval": pending,
            "processed_this_week": processed_week,
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


@router.post("/integrations/outlook/connect/start")
def start_outlook_connect(
    request: OutlookConnectStartRequest,
    user: TokenData = Depends(get_current_user),
):
    """Workspace-shell wrapper for the Outlook OAuth start flow.

    The canonical Outlook routes live under ``/outlook/*``; this
    endpoint mirrors the workspace-shell shape the SPA's Connections
    page already uses for Gmail / Slack so the frontend can talk to
    one ``/api/workspace/integrations/<provider>/connect/start`` API
    surface across every intake channel.

    Behind ``FEATURE_OUTLOOK_ENABLED``; returns 404 when off so the
    UI's Connect CTA can fall back to the "post-launch" copy that
    ``_outlook_status_for_org`` already emits.
    """
    from solden.core.feature_flags import is_outlook_enabled, outlook_disabled_payload
    if not is_outlook_enabled():
        raise HTTPException(status_code=404, detail=outlook_disabled_payload())

    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    redirect_path = str(request.redirect_path or "/connections").strip()
    if not redirect_path.startswith("/"):
        raise HTTPException(status_code=400, detail="invalid_redirect_path")

    from solden.services.outlook_api import (
        is_outlook_configured,
        generate_auth_url,
    )
    if not is_outlook_configured():
        raise HTTPException(
            status_code=503,
            detail="outlook_oauth_not_configured",
        )

    # State carries the user_id + org_id back through the Microsoft
    # OAuth callback so we can bind the resulting token correctly.
    # The Outlook callback already parses ``state.split(":", 1)`` —
    # match that shape exactly so the existing handler keeps working.
    state = f"{user.user_id}:{org_id}"
    auth_url = generate_auth_url(state=state)
    return {
        "auth_url": auth_url,
        "state": state,
        "organization_id": org_id,
        "redirect_path": redirect_path,
    }


@router.post("/integrations/outlook/disconnect")
def disconnect_outlook(
    request: OutlookDisconnectRequest,
    user: TokenData = Depends(get_current_user),
):
    """Workspace-shell wrapper for the Outlook disconnect flow."""
    from solden.core.feature_flags import is_outlook_enabled, outlook_disabled_payload
    if not is_outlook_enabled():
        raise HTTPException(status_code=404, detail=outlook_disabled_payload())

    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)

    from solden.services.outlook_api import outlook_token_store
    outlook_token_store.delete(user.user_id)
    return {"success": True, "organization_id": org_id}


@router.post("/integrations/slack/install/start")
def start_slack_install(
    request: SlackInstallStartRequest,
    http_request: Request,
    user: TokenData = Depends(get_current_user),
):
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    client_id = os.getenv("SLACK_CLIENT_ID", "").strip()
    client_secret = os.getenv("SLACK_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="slack_oauth_not_configured")

    redirect_uri = _slack_redirect_uri(http_request)
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
    http_request: Request,
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
    # M19+: pre-fix coerced missing org to "default"; M19b switched to
    # ``assert_org_id`` which raises ValueError -> 500 stack trace
    # surfaced to the user during OAuth. State-payload corruption is
    # a 400, not an internal error, re-cast.
    raw_org = str(state_payload.get("organization_id") or "").strip()
    if not raw_org:
        raise HTTPException(status_code=400, detail="invalid_state_payload")
    org_id = raw_org
    mode = str(state_payload.get("mode") or "per_org")

    client_id = os.getenv("SLACK_CLIENT_ID", "").strip()
    client_secret = os.getenv("SLACK_CLIENT_SECRET", "").strip()
    redirect_uri = _slack_redirect_uri(http_request)
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
    db.ensure_organization(org_id, organization_name=_default_org_name(user, org_id))
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
    org = db.ensure_organization(org_id, organization_name=_default_org_name(user, org_id))
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


@router.get("/integrations/teams/manifest")
def download_teams_manifest_package(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Return the Teams app .zip ready for sideload into a customer tenant.

    Mirrors ``ui/teams/manifest/build_package.sh`` but runs in-process
    so an admin can download the package straight from the workspace
    Connections page once ``MICROSOFT_APP_ID`` is configured.

    The package contains the rendered ``manifest.json`` + the two icons,
    flat-pathed (Teams rejects nested directories). The bot ID is read
    from the ``MICROSOFT_APP_ID`` env var; the helper 503s when it's
    missing so the admin sees a useful error instead of an opaque zip
    with placeholder text.

    Gated behind ``FEATURE_TEAMS_ENABLED`` for the same reason every
    Teams route is — until Microsoft-side registrations land, the
    download would produce a manifest the customer can't use.
    """
    from solden.core.feature_flags import is_teams_enabled, teams_disabled_payload
    if not is_teams_enabled():
        raise HTTPException(status_code=404, detail=teams_disabled_payload())

    _require_admin(user)
    _resolve_org_id(user, organization_id)

    import io
    import json as _json
    import re
    import zipfile
    from pathlib import Path
    from fastapi.responses import Response

    app_id = os.getenv("MICROSOFT_APP_ID", "").strip()
    if not app_id:
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "microsoft_app_id_not_configured",
                "message": (
                    "Set MICROSOFT_APP_ID on api/worker/beat with the Bot "
                    "Framework registration ID. See ui/teams/INSTALL.md "
                    "Part A.2 / A.3 for the Microsoft-side steps."
                ),
            },
        )
    if not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        app_id,
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "microsoft_app_id_invalid",
                "message": "MICROSOFT_APP_ID must be a hyphenated UUID.",
            },
        )

    manifest_dir = (
        Path(__file__).resolve().parent.parent.parent
        / "ui" / "teams" / "manifest"
    )
    manifest_path = manifest_dir / "manifest.json"
    color_path = manifest_dir / "color.png"
    outline_path = manifest_dir / "outline.png"

    for required in (manifest_path, color_path, outline_path):
        if not required.is_file():
            raise HTTPException(
                status_code=500,
                detail={
                    "reason": "teams_manifest_assets_missing",
                    "missing": str(required),
                },
            )

    app_version = os.getenv("TEAMS_APP_VERSION", "1.0.0").strip() or "1.0.0"
    raw_manifest = manifest_path.read_text(encoding="utf-8")
    rendered_manifest = (
        raw_manifest
        .replace("${MICROSOFT_APP_ID}", app_id)
        .replace('"version": "1.0.0"', f'"version": "{app_version}"', 1)
    )

    try:
        _json.loads(rendered_manifest)
    except _json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "reason": "teams_manifest_invalid_after_render",
                "error": str(exc),
            },
        ) from exc

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", rendered_manifest)
        zf.write(color_path, arcname="color.png")
        zf.write(outline_path, arcname="outline.png")
    buf.seek(0)

    filename = f"solden-teams-{app_version}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/integrations/teams/webhook")
def set_teams_webhook(
    request: TeamsWebhookRequest,
    user: TokenData = Depends(get_current_user),
):
    # §12 / §6.8 — Teams is scoped post-V1. Admins cannot wire a Teams
    # webhook in a V1 deployment regardless of role, so the admin UI
    # can't accidentally connect a surface we don't ship yet.
    from solden.core.feature_flags import is_teams_enabled, teams_disabled_payload
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


@router.post("/integrations/erp/test")
def test_erp_connection(
    organization_id: Optional[str] = Query(default=None),
    erp_type: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Module 5 spec line 183 — test transaction probe for ERP.

    Pings the configured ERP's read endpoint and times the wall-clock.
    Returns ``{status, erp_type, latency_ms, http_status, error,
    probed_at}`` so the connection-health panel can light up green
    on success or surface a useful error on failure.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    from solden.services.erp_test_probe import probe_erp_connection
    return probe_erp_connection(db, org_id, erp_type=erp_type)


@router.post("/integrations/teams/test")
def test_teams_webhook(
    request: TeamsTestRequest,
    user: TokenData = Depends(get_current_user),
):
    from solden.core.feature_flags import is_teams_enabled, teams_disabled_payload
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
                        {"type": "TextBlock", "weight": "Bolder", "text": "Solden Teams connectivity test"},
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
    http_request: Request,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    redirect_uri = _slack_redirect_uri(http_request)
    app_base = _public_app_base_url(http_request)
    bot_scopes = [scope for scope in _configured_slack_oauth_scopes().split(",") if scope]
    user_scopes = [scope for scope in _configured_slack_user_oauth_scopes().split(",") if scope]
    return {
        "organization_id": org_id,
        "manifest": {
            "display_information": {"name": "Solden AP"},
            "features": {"bot_user": {"display_name": "Solden AP"}},
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
                    {"command": "/clearledgr", "url": f"{app_base}/slack/commands", "description": "Solden AP"}
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
    from solden.api.erp_connections import (
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
            from solden.api.erp_connections import _classify_erp_connect_error
            classified = _classify_erp_connect_error(
                "sap", Exception(f"HTTP {response.status_code}: {response.text[:200]}"),
            )
            raise HTTPException(status_code=400, detail=classified)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # §15 structured ERP error — name the permission + link to
        # remediation instead of leaking the raw exception.
        from solden.api.erp_connections import _classify_erp_connect_error
        classified = _classify_erp_connect_error("sap", exc)
        raise HTTPException(status_code=400, detail=classified) from exc

    from solden.integrations.erp_router import ERPConnection, set_erp_connection

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

    from solden.integrations.erp_router import (
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


@router.get("/fraud-thresholds")
def get_fraud_thresholds(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Return the org's customer-configurable fraud rule thresholds.

    Module 4 spec line 158: "Basic fraud signals (rule-based, not ML):
    new IBAN doesn't match prior payments; unusually large invoice
    from low-frequency vendor; vendor created within last 30 days
    with first invoice over $X. Configurable per customer."
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    org = get_db().get_organization(org_id) or {}
    settings = _load_org_settings(org)
    cfg = settings.get("fraud_thresholds") or {}
    defaults = {
        "bank_change_alert_days": 14,
        "bank_change_warn_days": 30,
        "low_frequency_invoice_count_threshold": 3,
        "low_frequency_invoice_multiplier": 3.0,
        "new_vendor_days": 30,
        "new_vendor_first_invoice_max": 10000.0,
    }
    return {
        "organization_id": org_id,
        "configured": {k: cfg.get(k) for k in defaults},
        "defaults": defaults,
    }


class FraudThresholdsRequest(BaseModel):
    organization_id: Optional[str] = None
    bank_change_alert_days: Optional[int] = Field(default=None, ge=0, le=365)
    bank_change_warn_days: Optional[int] = Field(default=None, ge=0, le=365)
    low_frequency_invoice_count_threshold: Optional[int] = Field(default=None, ge=0, le=100)
    low_frequency_invoice_multiplier: Optional[float] = Field(default=None, ge=1.0, le=100.0)
    new_vendor_days: Optional[int] = Field(default=None, ge=0, le=365)
    new_vendor_first_invoice_max: Optional[float] = Field(default=None, ge=0, le=10_000_000)


@router.patch("/fraud-thresholds")
def patch_fraud_thresholds(
    request: FraudThresholdsRequest,
    user: TokenData = Depends(get_current_user),
):
    """Save customer-configurable fraud thresholds. None values keep
    the org's existing setting (or fall through to the default at
    risk-score time). Validation in the request model enforces
    sensible bounds — e.g. you can't set new_vendor_first_invoice_max
    above $10M; thresholds outside the cap need an engineering
    decision."""
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    org = db.get_organization(org_id) or {}
    settings = _load_org_settings(org)
    current = dict(settings.get("fraud_thresholds") or {})
    payload = request.dict(exclude_none=True)
    payload.pop("organization_id", None)
    current.update(payload)
    settings["fraud_thresholds"] = current
    db.update_organization(org_id, settings_json=settings)
    return {"organization_id": org_id, "configured": current}


@router.post("/onboarding/integration-health-gate")
def run_integration_health_gate(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Module 10 spec line 321: "Integration health checks: confirms
    each integration is correctly configured before allowing go-live."

    Runs the test-transaction probe against the org's primary ERP
    plus inspects Gmail / Slack / Teams connection state from the
    bootstrap-side helpers. Returns a per-integration result so the
    onboarding wizard can light up green per row and gate the
    "complete onboarding" CTA.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()

    results: Dict[str, Any] = {"organization_id": org_id, "checks": []}

    # 1. ERP — actually probe the connection, not just check that
    #    a row exists. This catches expired tokens.
    from solden.services.erp_test_probe import probe_erp_connection
    erp_probe = probe_erp_connection(db, org_id)
    results["checks"].append({
        "name": "erp",
        "label": "ERP connection",
        "status": "ok" if erp_probe.get("status") == "ok" else (
            "skip" if erp_probe.get("status") == "no_connection" else "fail"
        ),
        "detail": erp_probe.get("error") or (
            f"{(erp_probe.get('erp_type') or '').title()} responded in {erp_probe.get('latency_ms')} ms"
            if erp_probe.get("status") == "ok" else None
        ),
        "raw": erp_probe,
    })

    # 2. Gmail — bootstrap status helper already classifies durable
    #    vs reconnect-required. Reuse it.
    gmail_status = _gmail_status_for_org(org_id, user)
    results["checks"].append({
        "name": "gmail",
        "label": "Gmail integration",
        "status": "ok" if gmail_status.get("connected") and not gmail_status.get("requires_reconnect") else (
            "fail" if gmail_status.get("connected") else "skip"
        ),
        "detail": (
            "Reconnect required" if gmail_status.get("requires_reconnect")
            else f"Connected as {gmail_status.get('email')}" if gmail_status.get("connected")
            else "Not connected"
        ),
    })

    # 3. Approval surface — Slack OR Teams must be live (the spec
    #    onboarding wizard treats these as a single "approvals" step).
    slack = _slack_status_for_org(org_id)
    teams = _teams_status_for_org(org_id)
    approvals_ok = bool(
        (slack.get("connected") and not slack.get("requires_reauthorization"))
        or teams.get("connected"),
    )
    results["checks"].append({
        "name": "approvals",
        "label": "Approval surface (Slack or Teams)",
        "status": "ok" if approvals_ok else "fail",
        "detail": (
            "Slack ready" if slack.get("connected") and not slack.get("requires_reauthorization")
            else "Teams ready" if teams.get("connected")
            else "Connect Slack or Teams"
        ),
    })

    # Aggregate verdict — fail if any required check failed.
    required_failed = any(
        c["status"] == "fail" and c["name"] in ("erp", "gmail", "approvals")
        for c in results["checks"]
    )
    results["status"] = "fail" if required_failed else "ok"
    results["ready_for_go_live"] = not required_failed
    return results


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
    org = db.ensure_organization(org_id, organization_name=_default_org_name(user, org_id))
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
    org_before = db.ensure_organization(org_id, organization_name=_default_org_name(user, org_id))
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

    from solden.integrations.erp_router import (
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
    from solden.services.report_export import (
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

    # Module 9 §300: per-entity audit log scoping. The resolver returns
    # None for org-wide admins (owner / cfo / financial_controller
    # without an entity restriction); search_audit_events skips the
    # entity filter in that case. Restricted users (admin role +
    # entity_restrictions, or per-(user, entity) role overrides) see
    # only their entities' events plus org-level rows (entity_id IS NULL).
    from solden.services.audit_entity_scope import (
        resolve_audit_entity_scope,
    )
    entity_scope = resolve_audit_entity_scope(db, user)

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
        entity_scope=entity_scope,
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
# Module 7 v1 Pass 3 — webhook delivery log endpoint
#
# Per-webhook delivery history; each row is one attempt. Backed by
# webhook_deliveries (migration v52). Used by the SIEM config panel
# to show "did Splunk receive last Tuesday's events?" + the failure-
# triage queue for any subscriber having delivery issues.
# ---------------------------------------------------------------------------


@router.get("/webhooks/{webhook_id}/deliveries")
def list_webhook_deliveries(
    webhook_id: str,
    organization_id: Optional[str] = Query(default=None),
    audit_event_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, description="success | failed | retrying"),
    from_ts: Optional[str] = Query(default=None),
    to_ts: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    user: TokenData = Depends(get_current_user),
):
    """List webhook delivery attempts for a given subscription.

    Tenant-scoped + admin-gated. The webhook_id must belong to the
    caller's org or 404 (same token as missing — no existence leak).
    Newest-first.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    sub = db.get_webhook_subscription(webhook_id, org_id) if hasattr(db, "get_webhook_subscription") else None
    if not sub:
        raise HTTPException(status_code=404, detail="webhook_not_found")

    rows = db.list_webhook_deliveries(
        organization_id=org_id,
        webhook_subscription_id=webhook_id,
        audit_event_id=audit_event_id,
        status=status,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
    )
    return {
        "webhook_id": webhook_id,
        "organization_id": org_id,
        "deliveries": rows,
        "count": len(rows),
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
    # Module 7 spec line 244: "Export: CSV and PDF". Default keeps the
    # existing CSV behaviour for callers that don't pass it.
    format: str = Field(default="csv", pattern="^(csv|pdf)$")


@router.get("/audit/retention")
def get_audit_retention(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Return the current audit-log retention policy + tier ceiling.

    Module 7 spec line 246: "Retention: indefinite by default.
    Customer can configure longer retention for compliance reasons."
    Tier ceiling is the plan's `agent_activity_retention_days` from
    PlanLimits (Starter 1y, Growth 3y, Enterprise 7y/indefinite).
    Customers configure within that ceiling; default = ceiling
    (i.e. they keep what their plan affords until they shorten it).
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    org = db.get_organization(org_id) or {}
    settings = _load_org_settings(org)
    sub = _get_subscription_service().get_subscription(org_id)
    from solden.services.subscription import PlanLimits, PlanTier
    try:
        plan_tier = PlanTier(sub.plan)
    except Exception:
        plan_tier = PlanTier.FREE
    limits = PlanLimits.for_tier(plan_tier)
    tier_ceiling = int(limits.agent_activity_retention_days or 365)
    configured = settings.get("audit_retention_days")
    effective = int(configured) if configured else tier_ceiling
    return {
        "organization_id": org_id,
        "configured_days": int(configured) if configured else None,
        "tier_ceiling_days": tier_ceiling,
        "effective_days": effective,
        "plan": sub.plan,
    }


class AuditRetentionRequest(BaseModel):
    organization_id: Optional[str] = None
    days: int = Field(..., ge=30, le=3650)


@router.patch("/audit/retention")
def set_audit_retention(
    request: AuditRetentionRequest,
    user: TokenData = Depends(get_current_user),
):
    """Update the org's audit retention configuration.

    Validates the requested value against the plan tier ceiling so
    a Starter-tier customer can't claim 7 years. Lower values are
    fine — a customer might WANT to retain less to limit blast
    radius (compliance posture varies by jurisdiction).
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, request.organization_id)
    db = get_db()
    sub = _get_subscription_service().get_subscription(org_id)
    from solden.services.subscription import PlanLimits, PlanTier
    try:
        plan_tier = PlanTier(sub.plan)
    except Exception:
        plan_tier = PlanTier.FREE
    limits = PlanLimits.for_tier(plan_tier)
    tier_ceiling = int(limits.agent_activity_retention_days or 365)
    if request.days > tier_ceiling:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "exceeds_tier_ceiling",
                "tier_ceiling_days": tier_ceiling,
                "plan": sub.plan,
                "message": f"{sub.plan.title()} retains for at most {tier_ceiling} days. Upgrade to extend.",
            },
        )
    org = db.get_organization(org_id) or {}
    settings = _load_org_settings(org)
    settings["audit_retention_days"] = int(request.days)
    db.update_organization(org_id, settings_json=settings)
    return {
        "organization_id": org_id,
        "configured_days": int(request.days),
        "tier_ceiling_days": tier_ceiling,
        "effective_days": int(request.days),
        "plan": sub.plan,
    }


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
    # Module 9 §300: bake the requesting user's entity scope into the
    # export filter payload so the worker (which doesn't carry the
    # original auth context) applies the same scope. ``None`` = no
    # restriction; non-None list narrows the export to the user's
    # entities + org-level (entity_id IS NULL) rows.
    from solden.services.audit_entity_scope import (
        resolve_audit_entity_scope,
    )
    entity_scope = resolve_audit_entity_scope(db, user)

    import json as _json
    filters_payload = {
        "from_ts": request.from_ts,
        "to_ts": request.to_ts,
        "event_types": request.event_types,
        "actor_id": request.actor_id,
        "box_type": request.box_type,
        "box_id": request.box_id,
        "entity_scope": entity_scope,
    }
    export_row = db.create_audit_export(
        organization_id=org_id,
        requested_by=actor,
        filters_json=_json.dumps(filters_payload, separators=(",", ":")),
        export_format=request.format,
        retention_hours=24,
    )

    # Dispatch the Celery task. If Celery is unreachable (broker
    # outage, dev env without worker), fail soft — flip the row to
    # 'failed' with a clear message rather than letting the SPA poll
    # forever.
    try:
        from solden.services.celery_tasks import generate_audit_export
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
        export_format = str(export.get("export_format") or "csv").lower()
        filename = export.get("content_filename") or f"audit-{job_id}.{export_format}"
        media_type = (
            "application/pdf"
            if export_format == "pdf"
            else "text/csv; charset=utf-8"
        )
        from fastapi.responses import Response
        # Convert memoryview / bytes from psycopg cleanly.
        if isinstance(content, memoryview):
            content = bytes(content)
        return Response(
            content=content,
            media_type=media_type,
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
        "format": export.get("format") or export.get("export_format"),
        "export_format": export.get("export_format"),
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

    # Module 9 §300: per-entity scope. Same 404 token for events
    # outside the user's entity scope so we don't leak which
    # entities exist in the org.
    from solden.services.audit_entity_scope import (
        resolve_audit_entity_scope,
    )
    entity_scope = resolve_audit_entity_scope(db, user)
    if entity_scope is not None:
        event_entity = event.get("entity_id")
        # Org-level events (entity_id IS NULL) stay visible to
        # entity-scoped auditors so the trail isn't artificially
        # incomplete; non-null events must be in the user's scope.
        if event_entity and str(event_entity) not in entity_scope:
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
    org_id = _resolve_org_id(user, None)
    sub = db.get_webhook_subscription(webhook_id, org_id)
    if not sub:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Webhook not found"})

    db.delete_webhook_subscription(webhook_id, org_id)
    return {"status": "deleted", "id": webhook_id}


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(
    webhook_id: str,
    user: TokenData = Depends(get_current_user),
):
    """Send a test event to a webhook."""
    db = get_db()
    org_id = _resolve_org_id(user, None)
    sub = db.get_webhook_subscription(webhook_id, org_id)
    if not sub:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Webhook not found"})

    from solden.services.webhook_delivery import deliver_webhook

    ok = await deliver_webhook(
        url=sub["url"],
        event_type="test.ping",
        payload={"message": "Solden webhook test", "webhook_id": webhook_id},
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

    from solden.services.sheets_api import SheetsAPIClient
    spreadsheet_id = SheetsAPIClient.extract_spreadsheet_id(spreadsheet_url)
    if not spreadsheet_id:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "Could not parse spreadsheet ID from URL"})

    from solden.services.sheets_export import export_report_to_sheets as _export
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

    from solden.integrations.erp_router import (
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
    from solden.services.vendor_bootstrap import bootstrap_vendor_intelligence as _bootstrap
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
    except Exception:
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
    from solden.services.vendor_dedup import get_vendor_dedup_service
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

    from solden.services.vendor_dedup import get_vendor_dedup_service
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

    from solden.services.vendor_dedup import get_vendor_dedup_service
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
    from solden.services.vendor_dedup import get_vendor_dedup_service
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
    from solden.services.dispute_service import get_dispute_service
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

    from solden.services.dispute_service import get_dispute_service
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
    org_id = _resolve_org_id(user, None)
    dispute = db.get_dispute(dispute_id, org_id)
    if not dispute:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Dispute not found"})

    from solden.services.dispute_service import get_dispute_service
    svc = get_dispute_service(org_id)
    svc.resolve_dispute(dispute_id, resolution)
    return {"status": "resolved", "id": dispute_id}


@router.post("/disputes/{dispute_id}/escalate")
def escalate_dispute(
    dispute_id: str,
    user: TokenData = Depends(get_current_user),
):
    """Escalate a dispute."""
    db = get_db()
    org_id = _resolve_org_id(user, None)
    dispute = db.get_dispute(dispute_id, org_id)
    if not dispute:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Dispute not found"})

    from solden.services.dispute_service import get_dispute_service
    svc = get_dispute_service(org_id)
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
    from solden.services.approval_delegation import get_delegation_service
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

    from solden.services.approval_delegation import get_delegation_service
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
    from solden.services.approval_delegation import get_delegation_service
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
    from solden.services.period_close import get_period_close_service
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
    from solden.services.period_close import get_period_close_service
    return get_period_close_service(org_id).generate_accrual_report(period)


@router.get("/period-close/backdated/{period}")
def get_backdated_invoices(
    period: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Find invoices received after cutoff that belong to a prior period."""
    org_id = _resolve_org_id(user, organization_id)
    from solden.services.period_close import get_period_close_service
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
    from solden.services.period_close import get_period_close_service
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
    from solden.services.period_close import get_period_close_service
    ok = get_period_close_service(org_id).unlock_period(period)
    return {"status": "unlocked" if ok else "not_locked", "period": period}


@router.post("/vendor-intelligence/reconcile-statement")
def reconcile_vendor_statement(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
    body: dict = {},
):
    """Reconcile a vendor statement against Solden AP items.

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

    from solden.services.vendor_statement_recon import get_vendor_statement_recon
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
    from solden.services.tax_compliance import get_tax_compliance_service
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

    from solden.services.tax_compliance import validate_tax_id
    return validate_tax_id(tax_id, country_code)


@router.get("/team/invites")
def list_team_invites(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    # Non-admins get an empty list (bootstrap calls this for all users).
    # Phase 2.3: "admin" here means Financial Controller or higher.
    from solden.core.auth import has_admin_access
    if not has_admin_access(user.role):
        return {"organization_id": org_id, "invites": []}
    invites = get_db().list_team_invites(org_id)
    base = os.getenv("APP_BASE_URL", os.getenv("API_BASE_URL", "http://127.0.0.1:8010")).rstrip("/")
    for invite in invites:
        invite["invite_link"] = f"{base}/signup/accept?token={invite.get('token')}"
    return {"organization_id": org_id, "invites": invites}


@router.get("/team/users")
def list_team_users(
    organization_id: Optional[str] = Query(default=None),
    include_inactive: bool = Query(default=False),
    user: TokenData = Depends(get_current_user),
):
    """Lightweight user list for admin surfaces.

    Used by the Roles & permissions UI (Pass B) to show per-user
    entity-role overrides without paying the cost of /team/approvers'
    Slack-resolution probe. Returns {id, email, name, role,
    is_active} only — sensitive fields (password_hash, slack tokens)
    never appear in the response.

    Tenant-scoped via _resolve_org_id. Read-gated to admin since the
    user list itself is privileged information.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    rows = db.get_users(org_id, include_inactive=include_inactive)
    users = [
        {
            "id": row.get("id"),
            "email": (row.get("email") or "").strip().lower(),
            "name": (row.get("name") or "").strip() or (row.get("email") or ""),
            "role": (row.get("role") or "ap_clerk").strip().lower(),
            "is_active": bool(row.get("is_active")),
            # Module 6 spec line 214 — "last active". `last_seen_at`
            # is stamped on each authenticated request via the auth
            # middleware (migration v18). Falls back to updated_at
            # which is set at user-row creation/edit time.
            "last_active_at": row.get("last_seen_at") or row.get("updated_at"),
        }
        for row in rows
    ]
    users.sort(key=lambda u: (u["name"] or u["email"] or "").lower())
    return {
        "organization_id": org_id,
        "users": users,
        "count": len(users),
    }


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

    # v89 two-axis role resolution. Prefer the new workspace_role +
    # box_roles fields; fall back to legacy ``role`` via the workspace
    # mapping. ``owner`` is reserved for the org creator and cannot
    # be granted via invite.
    from solden.core.auth import (
        AP_ROLE_CLERK,
        AP_ROLES,
        WORKSPACE_ROLE_MEMBER,
        WORKSPACE_ROLE_OWNER,
        WORKSPACE_ROLES,
        normalize_ap_role,
        normalize_workspace_role,
    )

    normalized_workspace = (
        normalize_workspace_role(request.workspace_role)
        or normalize_workspace_role(request.role)
        or WORKSPACE_ROLE_MEMBER
    )
    if normalized_workspace not in WORKSPACE_ROLES or normalized_workspace == WORKSPACE_ROLE_OWNER:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "invalid_workspace_role",
                "workspace_role": request.workspace_role or request.role,
            },
        )

    # Per-Box role assignments. Today only ``ap_item`` is registered;
    # when more Box types ship they get keys in this dict and the
    # validation loop below picks them up automatically.
    requested_box_roles = dict(request.box_roles or {})
    if "ap_item" not in requested_box_roles:
        # Derive AP-side role from the legacy single-axis ``role``
        # value so older SPA builds get the right rank assignment.
        derived_ap = normalize_ap_role(request.role) or AP_ROLE_CLERK
        requested_box_roles["ap_item"] = derived_ap

    normalized_box_roles: Dict[str, str] = {}
    for box_type, raw_role in requested_box_roles.items():
        if box_type == "ap_item":
            normalized = normalize_ap_role(raw_role)
            if not normalized or normalized not in AP_ROLES:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "reason": "invalid_box_role",
                        "box_type": box_type,
                        "role": raw_role,
                    },
                )
            normalized_box_roles[box_type] = normalized
        # Other Box types: pass through; validation happens when they
        # register their role enum at the registry layer.

    # Encode the (workspace_role, ap_role) pair as a single legacy
    # role string for the team_invites.role column. The accept handler
    # passes this through to ``create_user``, which uses the legacy
    # mapping to derive both axes. v90 will swap this for a structured
    # column on the invite row.
    _AP_ROLE_BY_BOX = normalized_box_roles.get("ap_item", AP_ROLE_CLERK)
    _PAIR_TO_LEGACY = {
        (WORKSPACE_ROLE_OWNER, "controller"): "owner",
        ("admin", "controller"): "cfo",
        ("admin", "approver"): "financial_controller",
        ("member", "approver"): "ap_manager",
        ("member", "clerk"): "ap_clerk",
        ("read_only", "viewer"): "read_only",
    }
    normalized_role = _PAIR_TO_LEGACY.get(
        (normalized_workspace, _AP_ROLE_BY_BOX),
        # Fall back to the workspace value when the combo doesn't
        # have a single legacy alias. create_user's legacy mapping
        # will derive the AP role from there.
        normalized_workspace,
    )

    expires_at = (_utcnow() + timedelta(days=request.expires_in_days)).isoformat()
    db = get_db()

    # Idempotency: a previous "Send invite" click on the same email
    # used to create a fresh row each time. Two pending invites for
    # the same address is confusing (the admin doesn't know which
    # to share) and inflates the pending-invites stat. Reject the
    # duplicate at the route boundary with the existing invite id
    # so the SPA can offer to revoke+resend.
    normalized_email = str(request.email or "").lower().strip()
    if normalized_email:
        try:
            existing_invites = db.list_team_invites(org_id) or []
        except Exception:
            existing_invites = []
        for prior in existing_invites:
            if (
                str(prior.get("status") or "").lower() == "pending"
                and str(prior.get("email") or "").lower().strip() == normalized_email
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "reason": "invite_already_pending",
                        "email": normalized_email,
                        "invite_id": prior.get("id"),
                        "message": (
                            f"A pending invite for {normalized_email} already "
                            "exists. Revoke it first if you want to resend."
                        ),
                    },
                )

    invite = db.create_team_invite(
        organization_id=org_id,
        email=request.email,
        role=normalized_role,
        created_by=user.user_id,
        expires_at=expires_at,
        entity_restrictions=request.entity_restrictions,
    )
    base = os.getenv("APP_BASE_URL", os.getenv("API_BASE_URL", "http://127.0.0.1:8010")).rstrip("/")
    # Invite link lands the teammate on /signup/accept (the SPA route
    # backed by InviteAcceptPage). From there they pick their auth
    # provider — Google, Microsoft, or set-a-password. The previous
    # shape (/auth/google/start?invite_token=...) was Google-only and
    # locked out anyone without a Gmail account.
    invite_link = f"{base}/signup/accept?token={invite.get('token')}"

    # Send the invite email via the SMTP relay. The transactional
    # service short-circuits to skipped=True when SMTP isn't
    # configured (dev / staging without secrets), in which case we
    # still return the invite_link so the admin can copy-share it
    # manually. We never fail the whole request on email delivery
    # failure — the invite row is the source of truth; email is the
    # convenience layer.
    org_name = ""
    try:
        org_row = db.get_organization(org_id) or {}
        org_name = str(org_row.get("name") or "").strip()
    except Exception:
        pass
    # Email composition + send lives in solden.services.team_invite_email
    # so the body + delivery-state shape stay testable without the
    # workspace_shell import chain. send_team_invite_email never
    # raises; it returns {"delivered": bool, "skipped": bool, "error": str|None}.
    from solden.services.team_invite_email import send_team_invite_email

    email_status = send_team_invite_email(
        recipient=request.email,
        invite_link=invite_link,
        inviter_email=getattr(user, "email", None) or user.user_id,
        org_name=org_name,
        role=normalized_role,
    )

    return {
        "success": True,
        "organization_id": org_id,
        "invite": invite,
        "invite_link": invite_link,
        "email_delivered": email_status["delivered"],
        "email_skipped": email_status["skipped"],
        "email_error": email_status["error"],
    }


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
    from solden.services.implementation_service import get_implementation_status as _get_impl
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
    from solden.services.implementation_service import complete_implementation_step
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
    from solden.core.auth import has_cfo
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
    from solden.core.auth import has_cfo
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
    # Module 9 spec line 296: "Entity hierarchy: parent and subsidiary
    # structure mirrored from ERP." Optional parent — top-level
    # entities have parent_entity_id=null.
    parent_entity_id: Optional[str] = Field(default=None, max_length=64)


class EntityUpdateRequest(BaseModel):
    organization_id: Optional[str] = None
    name: Optional[str] = Field(default=None, max_length=200)
    code: Optional[str] = Field(default=None, max_length=50)
    erp_connection_id: Optional[str] = None
    gl_mapping: Optional[Dict[str, Any]] = None
    approval_rules: Optional[Dict[str, Any]] = None
    default_currency: Optional[str] = Field(default=None, max_length=10)
    parent_entity_id: Optional[str] = Field(default=None, max_length=64)


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
    # Validate parent if provided — must belong to the same org and
    # not create a cycle. Cycle prevention is shallow (we don't walk
    # the chain on every create) because the depth cap of "small
    # number of legal entities per org" makes cycles operationally
    # implausible; the UI prevents the obvious self-parent case.
    parent_id = (request.parent_entity_id or "").strip() or None
    if parent_id:
        parent = db.get_entity(parent_id)
        if not parent or parent.get("organization_id") != org_id:
            raise HTTPException(status_code=400, detail="parent_entity_not_found")
    entity = db.create_entity(
        organization_id=org_id,
        name=request.name,
        code=request.code,
        erp_connection_id=request.erp_connection_id,
        gl_mapping=request.gl_mapping,
        approval_rules=request.approval_rules,
        currency=request.default_currency,
        parent_entity_id=parent_id,
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
    if request.parent_entity_id is not None:
        parent_id = (request.parent_entity_id or "").strip() or None
        if parent_id == entity_id:
            raise HTTPException(status_code=400, detail="parent_cannot_be_self")
        if parent_id:
            parent = db.get_entity(parent_id)
            if not parent or parent.get("organization_id") != org_id:
                raise HTTPException(status_code=400, detail="parent_entity_not_found")
        updates["parent_entity_id"] = parent_id
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
    from solden.services.payment_models import PAYMENT_STATUSES

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
    from solden.services.spend_analysis import get_spend_analysis_service
    service = get_spend_analysis_service(org_id)
    return service.analyze(period_days=period_days)


@router.get("/health")
def get_admin_health(
    http_request: Request,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    org_id = _resolve_org_id(user, organization_id)
    health = _build_health(org_id, user, http_request)
    evidence = _get_ga_readiness(org_id)
    rollback_controls = _get_rollback_controls(org_id)
    health["launch_controls"] = {
        "rollback_controls": rollback_controls,
        "ga_readiness_summary": _summarize_ga_readiness(evidence, rollback_controls=rollback_controls),
    }
    return health


# ---------------------------------------------------------------------------
# Module 5 Pass A — Custom ERP field mappings.
#
# The leader maps non-default ERP field IDs (NetSuite custom-bodies, SAP
# Z-fields, QB classes, Xero tracking categories) to the bounded set of
# Solden fields the agent runtime understands. Backed by:
#   * clearledgr/integrations/field_mapping_catalog.py — the catalog
#     defines what's mappable and the per-ERP regex each value must match
#   * settings_json["erp_field_mappings"][erp_type] — persistence
#   * audit_events row (event_type=erp_admin_action:field_mapping_updated)
#     emitted on every diff so compliance can reconstruct who-changed-what
#
# The catalog is bounded by design (per scope §Module 5): the surface
# is "structured UI, not infinitely flexible". Adding a new mappable
# field is a code change that ships with the corresponding poster
# wired to consume it.
# ---------------------------------------------------------------------------

from solden.integrations.field_mapping_catalog import (  # noqa: E402
    diff_mappings as _diff_field_mappings,
    list_supported_erps as _list_supported_erps,
    serialize_catalog as _serialize_field_catalog,
    validate_mapping as _validate_field_mapping,
)


class _FieldMappingRequest(BaseModel):
    erp_type: str
    mappings: Dict[str, str]


def _read_field_mappings(settings_json: Any, erp_type: str) -> Dict[str, str]:
    """Pull the persisted mapping for an ERP out of org settings.

    Tolerant of legacy tenants where ``settings_json`` is a stringified
    JSON blob (older onboarding paths persisted strings). Returns ``{}``
    on any parse failure rather than raising — the dashboard should
    never 500 on a malformed legacy row.
    """
    settings = settings_json or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            return {}
    if not isinstance(settings, dict):
        return {}
    all_mappings = settings.get("erp_field_mappings") or {}
    if not isinstance(all_mappings, dict):
        return {}
    erp_key = str(erp_type or "").strip().lower()
    erp_mapping = all_mappings.get(erp_key) or {}
    return dict(erp_mapping) if isinstance(erp_mapping, dict) else {}


def _emit_field_mapping_audit(
    *,
    user: TokenData,
    org_id: str,
    erp_type: str,
    diff: Dict[str, Any],
    mapping_count: int,
) -> None:
    """Append a single audit row recording a field-mapping change.

    Best-effort: if the audit write fails we still return success to
    the operator (we'd rather complete the user's intent than block on
    telemetry), but we log so the gap is visible. Mirrors the pattern
    in clearledgr/api/erp_connections._audit_erp_admin_action.
    """
    try:
        db = get_db()
        actor_id = str(getattr(user, "user_id", "") or "").strip() or "unknown"
        actor_email = str(getattr(user, "email", "") or "").strip() or None
        db.append_audit_event({
            "event_type": "erp_admin_action:field_mapping_updated",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": org_id,
            # ERP-config changes are org-scoped, not Box-scoped — key the
            # event on the organization itself so it groups with other
            # org-level admin actions (rename, integration mode, etc).
            "box_id": org_id,
            "box_type": "organization",
            "source": "workspace_shell",
            "payload_json": {
                "erp": erp_type,
                "actor_email": actor_email,
                "success": True,
                "diff": diff,
                "mapping_count": mapping_count,
            },
        })
    except Exception as exc:
        logger.warning(
            "[field_mapping_audit] failed to write audit event for org=%s erp=%s: %s",
            org_id, erp_type, exc,
        )


@router.get("/erp/field-mappings")
async def get_erp_field_mappings(
    erp_type: str = Query(..., description="netsuite | sap | quickbooks | xero"),
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Return the catalog + persisted custom field mappings for an ERP.

    Response shape::

        {
          "organization_id": "org_xyz",
          "erp_type": "netsuite",
          "supported_erps": ["netsuite", "sap", "quickbooks", "xero"],
          "catalog": [
            {"key": "state_field", "label": "...", "description": "...",
             "default": "custbody_clearledgr_state",
             "pattern": "^[a-z]...", "category": "workflow"},
            ...
          ],
          "mappings": {"state_field": "custbody_acme_state", ...}
        }

    The ``mappings`` dict only contains keys the operator has
    explicitly customised; unset entries fall back to ``default`` at
    posting time.
    """
    org_id = _resolve_org_id(user, organization_id)
    erp_key = str(erp_type or "").strip().lower()
    if erp_key not in _list_supported_erps():
        raise HTTPException(status_code=400, detail=f"unsupported_erp_type:{erp_key}")

    db = get_db()
    org = db.get_organization(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="organization_not_found")
    mappings = _read_field_mappings(
        org.get("settings_json") or org.get("settings") or {},
        erp_key,
    )
    return {
        "organization_id": org_id,
        "erp_type": erp_key,
        "supported_erps": _list_supported_erps(),
        "catalog": _serialize_field_catalog(erp_key),
        "mappings": mappings,
    }


@router.put("/erp/field-mappings")
async def update_erp_field_mappings(
    body: _FieldMappingRequest,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Persist a custom field mapping for an ERP.

    Validates each key against the bounded catalog and each value
    against the per-ERP regex. Empty values are dropped — they
    represent "revert to the default field id". Emits an audit
    event with a per-key before/after diff on any change.

    Admin-gated because field mappings can change where bills post.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    erp_key = str(body.erp_type or "").strip().lower()
    if erp_key not in _list_supported_erps():
        raise HTTPException(status_code=400, detail=f"unsupported_erp_type:{erp_key}")

    cleaned, errors = _validate_field_mapping(erp_key, body.mappings or {})
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"reason": "validation_failed", "errors": errors},
        )

    db = get_db()
    org = db.get_organization(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="organization_not_found")

    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            settings = {}
    if not isinstance(settings, dict):
        settings = {}

    all_mappings = settings.get("erp_field_mappings") or {}
    if not isinstance(all_mappings, dict):
        all_mappings = {}
    before = dict(all_mappings.get(erp_key) or {})

    all_mappings[erp_key] = cleaned
    settings["erp_field_mappings"] = all_mappings
    db.update_organization(org_id, settings_json=settings)

    diff = _diff_field_mappings(before, cleaned)
    if diff:
        # Audit only when something actually changed — re-saving the
        # same form should not flood the audit log with no-op rows.
        _emit_field_mapping_audit(
            user=user,
            org_id=org_id,
            erp_type=erp_key,
            diff=diff,
            mapping_count=len(cleaned),
        )

    return {
        "organization_id": org_id,
        "erp_type": erp_key,
        "mappings": cleaned,
    }


# ---------------------------------------------------------------------------
# Module 5 Pass B — Connection health.
#
# Per scope §Module 5 acceptance: "Connection errors surface to leader
# within 10 minutes of detection." This endpoint is the dashboard's
# read surface for that — derived state from the existing audit-event
# stream + organization_integrations + webhook_deliveries. No new
# persistence layer.
# ---------------------------------------------------------------------------

from solden.services.connection_health import (  # noqa: E402
    build_connection_health as _build_connection_health,
)


@router.get("/connections/health")
def get_connection_health(
    organization_id: Optional[str] = Query(default=None),
    window_hours: int = Query(default=24, ge=1, le=168),
    user: TokenData = Depends(get_current_user),
):
    """Return per-integration health summary for the dashboard.

    Response shape::

        {
          "organization_id": "org_xyz",
          "window_hours": 24,
          "computed_at": "2026-04-29T...",
          "integrations": [
            {
              "integration_type": "gmail",
              "label": "Gmail",
              "status": "healthy" | "degraded" | "down" | "not_configured",
              "raw_status": "connected",
              "last_sync_at": "2026-04-29T...",
              "events_24h": 142,
              "errors_24h": 0,
              "latest_event_at": "...",
              "latest_error": null | {"ts", "event_type", "message"}
            },
            ...
          ],
          "webhooks": {"delivered": 4, "failed": 0, "retrying": 0}
        }

    Window is configurable (1-168 hours). Default 24h matches the
    "errors surface in 10 minutes" target — narrower windows would
    miss intermittent failures that cleared up; wider windows would
    let stale events linger past their relevance.
    """
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    return _build_connection_health(db, org_id, window_hours=int(window_hours))


# ---------------------------------------------------------------------------
# Module 6 Pass A — Permission catalog + custom roles
#
# Per scope §Module 6: standard roles cover 80% of cases; the
# remaining 20% need leader-composed custom roles. The 8 canonical
# permissions and the standard role taxonomy live in
# clearledgr/core/permissions.py. Custom roles persist in the
# ``custom_roles`` table (migration v53), bounded to 10 per org.
# ---------------------------------------------------------------------------

from solden.core import permissions as _permissions  # noqa: E402
from solden.core.stores.custom_roles_store import (  # noqa: E402
    CustomRoleLimitExceeded,
    CustomRoleNameTaken,
)


class CustomRoleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    permissions: List[str] = Field(..., min_length=1)
    description: Optional[str] = Field(default=None, max_length=300)


class CustomRoleUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    permissions: Optional[List[str]] = None
    description: Optional[str] = Field(default=None, max_length=300)


def _audit_custom_role(
    *,
    user: TokenData,
    org_id: str,
    action: str,
    role_id: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Audit emit for custom-role mutations (create/update/delete)."""
    try:
        db = get_db()
        payload: Dict[str, Any] = {
            "actor_email": getattr(user, "email", None),
            "role_id": role_id,
        }
        if extra:
            payload.update(extra)
        db.append_audit_event({
            "event_type": f"custom_role_{action}",
            "actor_type": "user",
            "actor_id": str(getattr(user, "user_id", "") or "unknown"),
            "organization_id": org_id,
            "box_id": role_id,
            "box_type": "custom_role",
            "source": "workspace_admin",
            "payload_json": payload,
        })
    except Exception as exc:
        logger.warning(
            "[custom_role_audit] failed for org=%s role_id=%s action=%s: %s",
            org_id, role_id, action, exc,
        )


@router.get("/permissions/catalog")
def get_permissions_catalog(
    user: TokenData = Depends(get_current_user),
):
    """Return the canonical permission catalog + standard role mapping.

    Public to any authenticated workspace user — the catalog is the
    contract the UI renders the "Custom roles" composer against, and
    every user benefits from seeing their own role's permission set
    inline. Mutations (create/update/delete) are admin-gated below.
    """
    return {
        "permissions": _permissions.serialize_catalog(),
        "standard_roles": _permissions.serialize_role_permissions(),
        "custom_role_limit": _permissions.CUSTOM_ROLES_PER_ORG_LIMIT,
    }


@router.get("/roles/custom")
def list_custom_roles(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """List custom roles for the org (any authenticated user).

    Read-only: rendering the role chip on a user's profile shouldn't
    require admin. Mutations are admin-gated below.
    """
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    rows = db.list_custom_roles(org_id)
    return {
        "organization_id": org_id,
        "custom_roles": rows,
        "count": len(rows),
        "limit": _permissions.CUSTOM_ROLES_PER_ORG_LIMIT,
    }


@router.post("/roles/custom")
def create_custom_role(
    body: CustomRoleCreateRequest,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Create a custom role.

    Admin-gated. Enforces the 10-per-org limit + (org, lower(name))
    uniqueness invariant. Validates each permission against the
    bounded catalog — unknown strings are silently dropped, but at
    least one valid permission must remain or the request 422s.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    try:
        row = db.create_custom_role(
            organization_id=org_id,
            name=body.name,
            permissions=body.permissions,
            description=body.description,
            created_by=str(getattr(user, "user_id", "") or "unknown"),
        )
    except CustomRoleLimitExceeded as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "custom_role_limit",
                "limit": _permissions.CUSTOM_ROLES_PER_ORG_LIMIT,
                "message": str(exc),
            },
        )
    except CustomRoleNameTaken as exc:
        raise HTTPException(
            status_code=409,
            detail={"reason": "name_taken", "message": str(exc)},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"reason": "validation_failed", "message": str(exc)},
        )

    _audit_custom_role(
        user=user, org_id=org_id, action="created", role_id=row["id"],
        extra={
            "name": row["name"],
            "permissions": list(row["permissions"]),
        },
    )
    return row


@router.put("/roles/custom/{role_id}")
def update_custom_role(
    role_id: str,
    body: CustomRoleUpdateRequest,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Update a custom role's name / description / permissions.

    Tenant-scoped: a 403 if the role belongs to a different org. The
    audit payload includes a before/after diff of permissions so
    compliance can reconstruct who changed which permission, when.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()

    existing = db.get_custom_role(role_id, org_id)
    if not existing:
        # Same status code as missing so we don't leak the existence
        # of roles in other tenants.
        raise HTTPException(status_code=404, detail="custom_role_not_found")

    before_perms = sorted(existing.get("permissions") or [])
    try:
        updated = db.update_custom_role(
            role_id,
            org_id,
            name=body.name,
            permissions=body.permissions,
            description=body.description,
        )
    except CustomRoleNameTaken as exc:
        raise HTTPException(
            status_code=409,
            detail={"reason": "name_taken", "message": str(exc)},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"reason": "validation_failed", "message": str(exc)},
        )
    if not updated:
        raise HTTPException(status_code=404, detail="custom_role_not_found")

    after_perms = sorted(updated.get("permissions") or [])
    diff_added = sorted(set(after_perms) - set(before_perms))
    diff_removed = sorted(set(before_perms) - set(after_perms))
    _audit_custom_role(
        user=user, org_id=org_id, action="updated", role_id=role_id,
        extra={
            "name": updated.get("name"),
            "permissions_added": diff_added,
            "permissions_removed": diff_removed,
        },
    )
    return updated


@router.delete("/roles/custom/{role_id}")
def delete_custom_role(
    role_id: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Delete a custom role.

    Tenant-scoped. Audit-emitted. Users currently assigned to the
    role keep working in the meantime — Pass B's user_entity_roles
    resolver collapses unknown role ids to the standard role
    ladder rather than 500ing.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()

    existing = db.get_custom_role(role_id, org_id)
    if not existing:
        raise HTTPException(status_code=404, detail="custom_role_not_found")

    deleted = db.delete_custom_role(role_id, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="custom_role_not_found")

    _audit_custom_role(
        user=user, org_id=org_id, action="deleted", role_id=role_id,
        extra={"name": existing.get("name")},
    )
    return {"id": role_id, "status": "deleted"}


# ---------------------------------------------------------------------------
# Module 6 Pass B — Per-entity role + approval ceiling
#
# A user's effective role can vary per legal entity (Sara is AP
# Manager in EU, Read-only in US). Per-amount approval ceilings
# compose with that. Backed by the user_entity_roles table
# (migration v54) and resolved by clearledgr/services/role_resolver.py.
# ---------------------------------------------------------------------------

from solden.core.permissions import ROLE_PERMISSIONS as _ROLE_PERMISSIONS  # noqa: E402
from solden.services import role_resolver as _role_resolver  # noqa: E402


class EntityRoleAssignment(BaseModel):
    entity_id: str = Field(..., min_length=1, max_length=128)
    role: str = Field(..., min_length=1, max_length=64)
    approval_ceiling: Optional[float] = Field(default=None, ge=0)


class EntityRolesPutRequest(BaseModel):
    assignments: List[EntityRoleAssignment]


def _resolve_role_token(role_token: str, db, organization_id: str) -> bool:
    """Validate a role token: standard taxonomy OR existing custom role
    id within the supplied organization.

    Returns ``True`` if the token is acceptable. Used at the API
    boundary so a malformed admin request 422s before persistence.
    Pre-fix this looked up ``cr_*`` tokens unscoped — an admin from
    tenant A could assign a custom role belonging to tenant B simply
    by knowing its id. Now the lookup is org-scoped at the SQL level.
    """
    token = (role_token or "").strip().lower()
    if not token:
        return False
    if token in _ROLE_PERMISSIONS:
        return True
    if token.startswith("cr_"):
        try:
            row = db.get_custom_role(token, organization_id)
        except Exception:
            return False
        return bool(row)
    return False


def _serialize_entity_role(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a DB row to a JSON-friendly dict (Decimal → str)."""
    out = {
        "user_id": row.get("user_id"),
        "entity_id": row.get("entity_id"),
        "organization_id": row.get("organization_id"),
        "role": row.get("role"),
        "approval_ceiling": (
            str(row["approval_ceiling"])
            if row.get("approval_ceiling") is not None else None
        ),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    return out


def _audit_entity_role(
    *,
    user: TokenData,
    org_id: str,
    target_user_id: str,
    action: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Audit emit for per-entity role mutations."""
    try:
        db = get_db()
        payload: Dict[str, Any] = {
            "actor_email": getattr(user, "email", None),
            "target_user_id": target_user_id,
        }
        if extra:
            payload.update(extra)
        db.append_audit_event({
            "event_type": f"user_entity_role_{action}",
            "actor_type": "user",
            "actor_id": str(getattr(user, "user_id", "") or "unknown"),
            "organization_id": org_id,
            "box_id": target_user_id,
            "box_type": "user",
            "source": "workspace_admin",
            "payload_json": payload,
        })
    except Exception as exc:
        logger.warning(
            "[entity_role_audit] failed for org=%s user=%s action=%s: %s",
            org_id, target_user_id, action, exc,
        )


@router.get("/users/{user_id}/entity-roles")
def list_entity_roles_for_user(
    user_id: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """List per-entity role assignments for one user.

    Tenant-scoped. Returns the resolver's view: which entities have
    overrides + their ceilings. Includes the user's org-level role
    on the response so the UI can render the fallback label.
    """
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    rows = db.list_user_entity_roles(user_id)
    # Filter out any rows that belong to a different tenant — there
    # shouldn't be any (the writer scopes by org_id), but a stale
    # row would otherwise leak across tenants.
    rows = [r for r in rows if str(r.get("organization_id") or "") == org_id]
    return {
        "user_id": user_id,
        "organization_id": org_id,
        "assignments": [_serialize_entity_role(r) for r in rows],
        "count": len(rows),
    }


@router.put("/users/{user_id}/entity-roles")
def replace_entity_roles_for_user(
    user_id: str,
    body: EntityRolesPutRequest,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Idempotently replace ALL per-entity assignments for one user.

    The PUT body is the full desired state — any entity_id in the
    request is upserted, any prior assignment whose entity is missing
    from the request is deleted. The replace happens in one
    transaction so the user is never observed in a half-applied
    state.

    Validation:
      * each ``role`` must be a known standard role token OR an
        existing custom role id in this tenant;
      * ``approval_ceiling`` must be non-negative if provided;
      * an empty assignments[] is valid — it clears every per-entity
        assignment for the user.

    Audit-emitted with a per-entity diff so compliance can
    reconstruct what changed.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()

    # Validate every role token before any DB write.
    for a in body.assignments:
        if not _resolve_role_token(a.role, db, org_id):
            raise HTTPException(
                status_code=422,
                detail={
                    "reason": "invalid_role",
                    "entity_id": a.entity_id,
                    "role": a.role,
                },
            )

    # Capture before-state for audit diff.
    before_rows = db.list_user_entity_roles(user_id)
    before_map = {r["entity_id"]: r for r in before_rows}

    incoming_payload = [
        {
            "entity_id": a.entity_id,
            "role": a.role,
            "approval_ceiling": a.approval_ceiling,
        }
        for a in body.assignments
    ]

    try:
        new_rows = db.replace_user_entity_roles(
            user_id=user_id,
            organization_id=org_id,
            assignments=incoming_payload,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"reason": "validation_failed", "message": str(exc)},
        )

    after_map = {r["entity_id"]: r for r in new_rows}
    added = sorted(set(after_map) - set(before_map))
    removed = sorted(set(before_map) - set(after_map))
    changed = sorted([
        e for e in (set(before_map) & set(after_map))
        if (
            before_map[e].get("role") != after_map[e].get("role")
            or before_map[e].get("approval_ceiling") != after_map[e].get("approval_ceiling")
        )
    ])

    if added or removed or changed:
        _audit_entity_role(
            user=user, org_id=org_id, target_user_id=user_id,
            action="replaced",
            extra={
                "added": added, "removed": removed, "changed": changed,
                "count_after": len(new_rows),
            },
        )

    return {
        "user_id": user_id,
        "organization_id": org_id,
        "assignments": [_serialize_entity_role(r) for r in new_rows],
        "count": len(new_rows),
    }


@router.get("/users/{user_id}/effective-permissions")
def get_effective_permissions_for_user(
    user_id: str,
    entity_id: Optional[str] = Query(default=None),
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Return the resolver's view of one user's effective permissions
    in one entity (or org-level if entity_id is omitted).

    The resolver decides which scope wins (entity > org), so the
    frontend can probe this endpoint to know exactly what to render
    or gate without re-implementing the resolver client-side.
    """
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()

    # Look up the target user's org-level role. Tolerant of users that
    # don't exist in this tenant — return an empty resolver result
    # rather than 404 so the UI can render "user not found" cleanly.
    target_role = ""
    if hasattr(db, "get_user"):
        try:
            target = db.get_user(user_id)
            if target and str(target.get("organization_id") or "") == org_id:
                target_role = str(target.get("role") or "").strip().lower()
        except Exception:
            target_role = ""

    resolved = _role_resolver.resolve_role(
        db,
        user_id=user_id,
        org_role=target_role,
        organization_id=org_id,
        entity_id=entity_id,
    )
    return {
        "user_id": user_id,
        "organization_id": org_id,
        "entity_id": entity_id,
        "role": resolved.role,
        "scope": resolved.scope,
        "permissions": sorted(resolved.permissions),
        "approval_ceiling": (
            str(resolved.approval_ceiling) if resolved.approval_ceiling is not None else None
        ),
    }


# ---------------------------------------------------------------------------
# Wave 1 / A1 — SOX immutable original-PDF storage download surface
#
# The intake path archives every attachment to ``invoice_originals`` with
# trigger-enforced append-only semantics (see migration v57 +
# database._install_audit_append_only_guards). Auditors and operators
# need a tenant-scoped read path; this is it.
#
# Two endpoints:
#   * GET /api/ap/items/{ap_item_id}/originals
#       Lists every archived original linked to the AP item — used by
#       the detail page to render a "View original" affordance.
#   * GET /api/ap/items/originals/{content_hash}
#       Streams the bytes back. Tenant-scoped (a hash from another
#       org returns 404 — no existence leak).
#
# Audit emit on every download so an audit-trail-of-the-audit-trail
# exists; SOC 2 controls expect this kind of access logging.
# ---------------------------------------------------------------------------

from solden.services.invoice_archive import (  # noqa: E402
    fetch_pdf as _fetch_archived_pdf,
    list_originals_for_ap_item as _list_originals_for_ap_item,
)


@router.get("/ap/items/{ap_item_id}/originals")
def list_ap_item_originals(
    ap_item_id: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Return the archived originals linked to one AP item.

    Tenant-scoped via ``_resolve_org_id``. The list excludes the
    bytes themselves — clients fetch each via the
    ``/originals/{content_hash}`` endpoint.
    """
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    rows = _list_originals_for_ap_item(
        db, organization_id=org_id, ap_item_id=ap_item_id,
    )
    return {
        "organization_id": org_id,
        "ap_item_id": ap_item_id,
        "originals": rows,
        "count": len(rows),
    }


@router.get("/ap/items/originals/{content_hash}")
def download_ap_item_original(
    content_hash: str,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Stream the archived bytes back to an authorised client.

    Tenant-scoped: a hash that exists in another tenant returns 404,
    not 403, to avoid existence leaks across tenants.

    Audit-emit ``invoice_original_downloaded`` so SOC 2 access
    auditing has a trail. The audit row carries the requesting
    user's email + the content hash + the AP item id (when the
    archive is linked).
    """
    from fastapi.responses import Response

    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    row = _fetch_archived_pdf(db, organization_id=org_id, content_hash=content_hash)
    if not row:
        raise HTTPException(status_code=404, detail="archive_not_found")

    # Best-effort audit emit; the download itself succeeds even if
    # the audit write hiccups.
    try:
        db.append_audit_event({
            "event_type": "invoice_original_downloaded",
            "actor_type": "user",
            "actor_id": str(getattr(user, "user_id", "") or "unknown"),
            "organization_id": org_id,
            "box_id": row.get("ap_item_id") or content_hash,
            "box_type": "ap_item" if row.get("ap_item_id") else "invoice_original",
            "source": "workspace",
            "payload_json": {
                "actor_email": getattr(user, "email", None),
                "content_hash": content_hash,
                "filename": row.get("filename"),
                "size_bytes": row.get("size_bytes"),
            },
        })
    except Exception as exc:
        logger.warning(
            "[invoice_archive] download audit failed for org=%s hash=%s: %s",
            org_id, content_hash[:16], exc,
        )

    filename = row.get("filename") or f"invoice-{content_hash[:12]}.pdf"
    return Response(
        content=row.get("content") or b"",
        media_type=row.get("content_type") or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Hash": content_hash,
        },
    )


# ─── Module 11 — full-account data export ─────────────────────────
#
# Spec line 348 + 352: "Data export: full account data export
# (CSV/JSON) for portability. Data export of full account completes
# for accounts up to 1M invoices."
#
# Implementation: synchronous JSON dump streamed to the client. Caps
# at MAX_EXPORT_ROWS_AP per section to keep the worker memory bounded
# and prevent a misconfigured export from OOMing the api. Customers
# beyond the cap receive a partial dump with a warning header in the
# JSON envelope; deeper pagination is the next iteration.

@router.get("/account/export")
def export_full_account(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Stream a JSON dump of the org's portable data.

    Sections (in order): organization metadata + settings, AP items,
    vendors, approval rules, custom roles, team members, integrations
    summary (no secrets), API key metadata (no raw keys, no hashes).

    Capped at 50K rows per AP/vendor section. Audit + logs are
    excluded — they live behind /audit/export with their own retention
    + format options.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()

    AP_CAP = 50_000
    VENDOR_CAP = 50_000

    org_row = db.get_organization(org_id) or {}
    settings = _load_org_settings(org_row)

    ap_items = []
    if hasattr(db, "list_ap_items"):
        try:
            ap_items = db.list_ap_items(org_id, limit=AP_CAP) or []
        except Exception as exc:
            logger.warning("[account/export] ap_items read failed for %s: %s", org_id, exc)
            ap_items = []

    vendors = []
    if hasattr(db, "list_vendor_summary_rows"):
        try:
            vendors = db.list_vendor_summary_rows(org_id, limit=VENDOR_CAP) or []
        except Exception:
            vendors = []
    elif hasattr(db, "get_vendor_summary"):
        try:
            vendors = db.get_vendor_summary(org_id) or []
        except Exception:
            vendors = []

    rules = []
    if hasattr(db, "list_workspace_rules"):
        try:
            rules = db.list_workspace_rules(org_id, include_inactive=True) or []
        except Exception:
            rules = []

    custom_roles = []
    if hasattr(db, "list_custom_roles"):
        try:
            custom_roles = db.list_custom_roles(org_id) or []
        except Exception:
            custom_roles = []

    users_dump = []
    if hasattr(db, "get_users"):
        try:
            for row in (db.get_users(org_id, include_inactive=True) or []):
                users_dump.append({
                    "id": row.get("id"),
                    "email": row.get("email"),
                    "name": row.get("name"),
                    "role": row.get("role"),
                    "is_active": bool(row.get("is_active")),
                    "created_at": row.get("created_at"),
                    "last_seen_at": row.get("last_seen_at"),
                })
        except Exception:
            pass

    api_keys_dump = []
    if hasattr(db, "list_api_keys"):
        try:
            for row in (db.list_api_keys(org_id, include_revoked=True) or []):
                # NEVER include key_hash or raw key. Prefix + scopes
                # is enough for the operator to reconstruct what
                # existed without leaking auth material.
                api_keys_dump.append({
                    "id": row.get("id"),
                    "label": row.get("label"),
                    "key_prefix": row.get("key_prefix"),
                    "scopes": row.get("scopes"),
                    "is_active": bool(row.get("is_active")),
                    "created_at": row.get("created_at"),
                    "last_used_at": row.get("last_used_at"),
                })
        except Exception:
            pass

    integrations_summary = {
        "gmail": _gmail_status_for_org(org_id, user),
        "slack": _slack_status_for_org(org_id),
        "teams": _teams_status_for_org(org_id),
        "erp": _erp_status_for_org(org_id),
    }

    payload = {
        "organization": {
            "id": org_id,
            "name": org_row.get("organization_name") or org_row.get("name"),
            "settings": settings,
            "created_at": org_row.get("created_at"),
        },
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_by": getattr(user, "email", None) or getattr(user, "user_id", None),
        "caps": {"ap_items": AP_CAP, "vendors": VENDOR_CAP},
        "ap_items": ap_items,
        "ap_items_truncated": len(ap_items) >= AP_CAP,
        "vendors": vendors,
        "vendors_truncated": len(vendors) >= VENDOR_CAP,
        "approval_rules": rules,
        "custom_roles": custom_roles,
        "users": users_dump,
        "api_keys": api_keys_dump,
        "integrations": integrations_summary,
    }

    import json as _json
    body = _json.dumps(payload, default=str, separators=(",", ":"))
    filename = f"clearledgr-account-{org_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )
