"""
Clearledgr Auth API

Authentication endpoints for login, registration, and token management.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

import httpx
from fastapi import APIRouter, HTTPException, Depends, Query, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from clearledgr.core.auth import (
    TokenResponse, User,
    create_user,
    create_access_token,
    decode_token, get_current_user, get_optional_user, TokenData,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from clearledgr.core.database import get_db

router = APIRouter(prefix="/auth", tags=["Authentication"])

WORKSPACE_ACCESS_COOKIE_NAME = "clearledgr_workspace_access"
WORKSPACE_CSRF_COOKIE_NAME = "clearledgr_workspace_csrf"
SESSION_TOKEN_PLACEHOLDER = "__cookie_session__"


def _session_cookie_secure() -> bool:
    env_name = str(os.getenv("ENV", "dev")).strip().lower()
    return env_name in {"prod", "production", "staging", "stage"}


def _session_cookie_domain() -> Optional[str]:
    raw = str(os.getenv("WORKSPACE_SESSION_COOKIE_DOMAIN", "")).strip()
    return raw or None


def _set_workspace_session_cookies(response: Response, access_token: str) -> None:
    """Set the short-lived access cookie + CSRF cookie.

    Streak-aligned model: there is no Clearledgr-issued refresh token.
    When the access JWT expires, the Gmail extension silently re-runs
    Google's token flow (chrome.identity.getAuthToken) and re-exchanges
    via /auth/google/exchange to get a new access JWT. Google's grant
    is the source of truth for "is this user still allowed in" — we
    don't keep our own long-lived refresh credential around.
    """
    secure = _session_cookie_secure()
    domain = _session_cookie_domain()
    csrf_token = secrets.token_urlsafe(32)
    cookie_kwargs = {
        "path": "/",
        "secure": secure,
        "samesite": "lax",
        "domain": domain,
    }
    response.set_cookie(
        WORKSPACE_ACCESS_COOKIE_NAME,
        access_token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        **cookie_kwargs,
    )
    response.set_cookie(
        WORKSPACE_CSRF_COOKIE_NAME,
        csrf_token,
        httponly=False,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        **cookie_kwargs,
    )


def _clear_workspace_session_cookies(response: Response) -> None:
    domain = _session_cookie_domain()
    for name in (WORKSPACE_ACCESS_COOKIE_NAME, WORKSPACE_CSRF_COOKIE_NAME):
        response.delete_cookie(name, path="/", domain=domain)


class InviteAcceptRequest(BaseModel):
    token: str
    password: Optional[str] = None
    name: Optional[str] = None


class GoogleAuthCodeExchangeRequest(BaseModel):
    auth_code: str = Field(..., min_length=12, max_length=512)


def _oauth_secret() -> str:
    from clearledgr.core.secrets import require_secret
    return require_secret("CLEARLEDGR_SECRET_KEY")


def _google_oauth_redirect_uri() -> str:
    return os.getenv(
        "GOOGLE_CONSOLE_REDIRECT_URI",
        f"{os.getenv('API_BASE_URL', 'http://127.0.0.1:8010').rstrip('/')}/auth/google/callback",
    ).strip()


def _sign_google_state(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("utf-8")
    sig = hmac.new(_oauth_secret().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _unsign_google_state(state: str) -> dict:
    if "." not in state:
        raise HTTPException(status_code=400, detail="invalid_state")
    body, sig = state.split(".", 1)
    expected = hmac.new(_oauth_secret().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=400, detail="invalid_state_signature")
    decoded = json.loads(base64.urlsafe_b64decode(body.encode("utf-8")).decode("utf-8"))
    if int(decoded.get("iat") or 0) and datetime.now(timezone.utc).timestamp() - int(decoded["iat"]) > 900:
        raise HTTPException(status_code=400, detail="expired_state")
    return decoded


def _google_auth_code_ttl_seconds() -> int:
    raw = str(os.getenv("GOOGLE_AUTH_CODE_TTL_SECONDS", "180")).strip()
    try:
        value = int(raw)
    except Exception:
        value = 180
    return max(30, min(value, 600))


def _sanitize_redirect_path(redirect_path: Optional[str]) -> str:
    path = str(redirect_path or "/").strip()
    if (
        not path.startswith("/")
        or path.startswith("//")
        or path.startswith("/\\")
        or "\x00" in path
        or "://" in path
    ):
        raise HTTPException(status_code=400, detail="invalid_redirect_path")
    return path


def _append_query_params(url: str, params: Dict[str, Any]) -> str:
    split = urlsplit(url)
    existing = dict(parse_qsl(split.query, keep_blank_values=True))
    for key, value in params.items():
        if value is None:
            continue
        existing[str(key)] = str(value)
    query = urlencode(existing)
    return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))


def _issue_google_auth_code(*, access_token: str, refresh_token: str, organization_id: str) -> str:
    now = datetime.now(timezone.utc)
    db = get_db()
    try:
        db.purge_expired_google_auth_codes()
    except Exception:
        # Best-effort purge; issuance must continue even if cleanup fails.
        pass
    auth_code = secrets.token_urlsafe(32)
    expires_at = now + timedelta(seconds=_google_auth_code_ttl_seconds())
    if not organization_id:
        logger.warning("_issue_google_auth_code called without organization_id, falling back to 'default'")
    db.save_google_auth_code(
        auth_code=auth_code,
        access_token=access_token,
        refresh_token=refresh_token,
        organization_id=str(organization_id or "default"),
        expires_at=expires_at.isoformat(),
    )
    return auth_code


def _consume_google_auth_code(code: str) -> Dict[str, Any]:
    db = get_db()
    payload = db.consume_google_auth_code(str(code or "").strip())
    if not payload:
        raise HTTPException(status_code=400, detail="invalid_auth_code")
    expires_at_raw = str(payload.get("expires_at") or "").strip()
    expires_at: Optional[datetime] = None
    if expires_at_raw:
        try:
            expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            else:
                expires_at = expires_at.astimezone(timezone.utc)
        except Exception:
            expires_at = None
    if expires_at and datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="expired_auth_code")
    return payload


# NOTE: /auth/register, /auth/login, /auth/refresh have been deleted.
# Clearledgr is a Gmail-native product (Streak-aligned). The only auth
# path is "Continue with Google", which lands on /auth/google/callback
# and exchanges via /auth/google/exchange. There is no email/password
# login surface and no Clearledgr-issued refresh token — when the
# access JWT expires, the extension silently re-runs Google's token
# flow and re-exchanges. See _set_workspace_session_cookies for the
# cookie shape.


@router.get("/me", response_model=User)
async def get_me(current_user: TokenData = Depends(get_current_user)):
    """
    Get current authenticated user.
    """
    from clearledgr.core.auth import get_user_by_id
    user = get_user_by_id(current_user.user_id)
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return user


@router.post("/logout")
async def logout(
    response: Response,
    current_user: Optional[TokenData] = Depends(get_optional_user),
):
    """
    Logout current user.
    
    Note: In a stateless JWT system, logout is handled client-side
    by removing the token. This endpoint is for audit logging.
    """
    # In production, you might:
    # - Add token to blacklist
    # - Log the logout event
    # - Invalidate refresh tokens
    
    _clear_workspace_session_cookies(response)
    return {"message": "Logged out successfully", "user_id": getattr(current_user, "user_id", None)}


# ==================== GOOGLE IDENTITY (REMOVED) ====================
# The /google-identity endpoint was removed because it minted JWTs
# from self-reported email without validating a Google token.
# All auth now goes through Google OAuth token validation in
# core/auth.py:_validate_google_token() — the Streak pattern.


# ==================== USER MANAGEMENT ====================

class UserUpdateRequest(BaseModel):
    """Request to update user details."""
    name: str | None = None
    role: str | None = Field(None, pattern="^(admin|member|viewer)$")


class UserRoleRequest(BaseModel):
    """Request to update user role."""
    role: str = Field(..., pattern="^(admin|member|viewer)$")


class UserListResponse(BaseModel):
    """Response containing list of users."""
    users: list[User]
    total: int


@router.get("/users", response_model=UserListResponse)
async def list_users(
    current_user: TokenData = Depends(get_current_user),
    limit: int = 100,
    offset: int = 0
):
    """
    List all users in the current user's organization.
    
    Requires: admin role
    """
    from clearledgr.core.database import get_db
    from clearledgr.core.auth import get_user_by_id
    
    user = get_user_by_id(current_user.user_id)
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    db = get_db()
    users_data = db.get_users(user.organization_id)
    
    users = []
    for u in users_data[offset:offset + limit]:
        users.append(User(
            id=u["id"],
            email=u["email"],
            name=u.get("name", ""),
            organization_id=u["organization_id"],
            role=u.get("role", "member"),
            created_at=u.get("created_at", "")
        ))
    
    return UserListResponse(users=users, total=len(users_data))


@router.get("/users/{user_id}", response_model=User)
async def get_user(
    user_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """
    Get a specific user by ID.
    
    Users can view their own profile. Admins can view any user in their org.
    """
    from clearledgr.core.database import get_db
    from clearledgr.core.auth import get_user_by_id
    
    requesting_user = get_user_by_id(current_user.user_id)
    if not requesting_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check access
    if user_id != current_user.user_id and requesting_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
    
    db = get_db()
    user_data = db.get_user(user_id)
    
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user_data["organization_id"] != requesting_user.organization_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return User(
        id=user_data["id"],
        email=user_data["email"],
        name=user_data.get("name", ""),
        organization_id=user_data["organization_id"],
        role=user_data.get("role", "member"),
        created_at=user_data.get("created_at", "")
    )


@router.put("/users/{user_id}", response_model=User)
async def update_user(
    user_id: str,
    request: UserUpdateRequest,
    current_user: TokenData = Depends(get_current_user)
):
    """
    Update a user's details.
    
    Users can update their own name. Admins can update any user's name and role.
    """
    from clearledgr.core.database import get_db
    from clearledgr.core.auth import get_user_by_id
    
    requesting_user = get_user_by_id(current_user.user_id)
    if not requesting_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check access — admin access means Financial Controller or higher
    # under the Phase 2.3 thesis taxonomy (legacy "admin" → "financial_controller").
    from clearledgr.core.auth import has_admin_access
    is_self = user_id == current_user.user_id
    is_admin = has_admin_access(requesting_user.role)

    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    # Non-admins can't change roles
    if request.role and not is_admin:
        raise HTTPException(status_code=403, detail="Only admins can change roles")
    
    db = get_db()
    
    # Verify target user exists and is in same org
    user_data = db.get_user(user_id)
    if not user_data or user_data["organization_id"] != requesting_user.organization_id:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Update user
    updates = {}
    if request.name:
        updates["name"] = request.name
    if request.role and is_admin:
        updates["role"] = request.role
    
    if updates:
        db.update_user(user_id, **updates)
    
    # Return updated user
    user_data = db.get_user(user_id)
    return User(
        id=user_data["id"],
        email=user_data["email"],
        name=user_data.get("name", ""),
        organization_id=user_data["organization_id"],
        role=user_data.get("role", "member"),
        created_at=user_data.get("created_at", "")
    )


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    current_user: TokenData = Depends(get_current_user)
):
    """
    Delete (deactivate) a user.
    
    Requires: admin role. Cannot delete yourself.
    """
    from clearledgr.core.database import get_db
    from clearledgr.core.auth import get_user_by_id
    
    requesting_user = get_user_by_id(current_user.user_id)
    if not requesting_user or requesting_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    if user_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    
    db = get_db()
    
    # Verify target user exists and is in same org
    user_data = db.get_user(user_id)
    if not user_data or user_data["organization_id"] != requesting_user.organization_id:
        raise HTTPException(status_code=404, detail="User not found")
    
    # §5.4 Archived Users: soft delete with attribution
    actor_email = getattr(current_user, "email", None) or current_user.user_id
    db.delete_user(user_id, archived_by=actor_email)

    # Audit event
    try:
        db.append_ap_audit_event({
            "event_type": "user_archived",
            "actor_type": "user",
            "actor_id": actor_email,
            "organization_id": requesting_user.organization_id,
            "source": "auth_api",
            "payload_json": {
                "archived_user_id": user_id,
                "archived_user_email": user_data.get("email"),
                "archived_by": actor_email,
            },
        })
    except Exception:
        pass

    return {"message": "User archived", "user_id": user_id}


@router.post("/users/{user_id}/role", response_model=User)
async def set_user_role(
    user_id: str,
    request: UserRoleRequest,
    current_user: TokenData = Depends(get_current_user)
):
    """
    Set a user's role.
    
    Requires: admin role
    
    Available roles:
    - admin: Full access, can manage users and settings
    - member: Can process transactions and view data
    - viewer: Read-only access
    """
    from clearledgr.core.database import get_db
    from clearledgr.core.auth import get_user_by_id
    
    requesting_user = get_user_by_id(current_user.user_id)
    if not requesting_user or requesting_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    db = get_db()
    
    # Verify target user exists and is in same org
    user_data = db.get_user(user_id)
    if not user_data or user_data["organization_id"] != requesting_user.organization_id:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Update role
    db.update_user(user_id, role=request.role)
    
    # Return updated user
    user_data = db.get_user(user_id)
    return User(
        id=user_data["id"],
        email=user_data["email"],
        name=user_data.get("name", ""),
        organization_id=user_data["organization_id"],
        role=user_data.get("role", "member"),
        created_at=user_data.get("created_at", "")
    )


@router.post("/users/invite")
async def invite_user(
    email: EmailStr,
    role: str = "member",
    current_user: TokenData = Depends(get_current_user)
):
    """
    Invite a new user to the organization.
    
    Requires: admin role
    
    Creates a pending user account and sends an invitation email.
    """
    from clearledgr.core.database import get_db
    from clearledgr.core.auth import get_user_by_id, get_user_by_email
    import uuid
    
    from clearledgr.core.auth import has_admin_access, normalize_user_role, ROLE_RANK
    requesting_user = get_user_by_id(current_user.user_id)
    if not requesting_user or not has_admin_access(requesting_user.role):
        raise HTTPException(status_code=403, detail="Admin access required")

    # Phase 2.3: invite role must be a canonical thesis role. Legacy
    # values (admin/member/viewer) still work because normalize_user_role
    # upgrades them in place — but the canonical form is what gets
    # persisted on the new user record.
    normalized_role = normalize_user_role(role)
    if normalized_role not in ROLE_RANK or normalized_role == "owner":
        raise HTTPException(status_code=400, detail="Invalid role")
    role = normalized_role
    
    # Check if user already exists
    existing = get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")
    
    db = get_db()

    # Create invite token (reuses the existing team invite system)
    from datetime import datetime, timedelta, timezone
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    invite = db.create_team_invite(
        organization_id=requesting_user.organization_id,
        email=email,
        role=role,
        created_by=current_user.user_id,
        expires_at=expires_at,
    )
    base = os.getenv("APP_BASE_URL", os.getenv("API_BASE_URL", "http://127.0.0.1:8010")).rstrip("/")
    invite_link = f"{base}/auth/google/start?invite_token={invite.get('token')}"

    return {
        "message": "Invitation created",
        "email": email,
        "role": role,
        "invite_link": invite_link,
    }


@router.get("/google/start")
async def start_google_web_auth(
    organization_id: Optional[str] = Query(default=None),
    redirect_path: str = Query(default="/"),
    invite_token: Optional[str] = Query(default=None),
):
    """Start Google web OAuth flow for console sign-in."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        raise HTTPException(status_code=503, detail="GOOGLE_CLIENT_ID not configured")

    safe_redirect_path = _sanitize_redirect_path(redirect_path)
    if not organization_id:
        logger.warning("google_oauth_start called without organization_id, falling back to 'default'")
    state = _sign_google_state(
        {
            "organization_id": organization_id or "default",
            "redirect_path": safe_redirect_path,
            "invite_token": invite_token,
            "nonce": secrets.token_urlsafe(8),
            "iat": int(datetime.now(timezone.utc).timestamp()),
        }
    )
    params = {
        "client_id": client_id,
        "redirect_uri": _google_oauth_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "include_granted_scopes": "true",
        "prompt": "select_account",
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    # This endpoint is always hit as a top-level navigation: from the
    # OnboardingFlow popup, from invite-email links, from console sign-in.
    # 302 redirect to Google consent is what every caller expects.
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/google/callback")
async def google_web_auth_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
):
    """Handle Google OAuth callback for console sign-in."""
    if error:
        return RedirectResponse(url=f"/?auth_error={error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing_code_or_state")

    state_payload = _unsign_google_state(state)
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="google_oauth_not_configured")

    async with httpx.AsyncClient(timeout=30) as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": _google_oauth_redirect_uri(),
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    token_payload = token_resp.json() if token_resp.content else {}
    if token_resp.status_code >= 400 or "access_token" not in token_payload:
        raise HTTPException(status_code=400, detail={"message": "google_token_exchange_failed", "payload": token_payload})

    access_token = token_payload["access_token"]
    async with httpx.AsyncClient(timeout=30) as client:
        profile_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    profile = profile_resp.json() if profile_resp.content else {}
    if profile_resp.status_code >= 400:
        raise HTTPException(status_code=401, detail={"message": "google_profile_fetch_failed", "payload": profile})

    email = str(profile.get("email") or "").strip().lower()
    google_id = str(profile.get("id") or "")
    if not email or not google_id:
        raise HTTPException(status_code=400, detail="invalid_google_profile")

    from clearledgr.core.database import get_db
    from clearledgr.core.auth import create_user_from_google, get_user_by_email

    db = get_db()
    invite_token = state_payload.get("invite_token")
    invite = db.get_team_invite_by_token(str(invite_token)) if invite_token else None
    if invite and invite.get("status") != "pending":
        invite = None

    from clearledgr.core.auth import normalize_user_role, ROLE_AP_CLERK
    if invite:
        if str(invite.get("email")).lower().strip() != email:
            raise HTTPException(status_code=403, detail="invite_email_mismatch")
        org_id = str(invite.get("organization_id"))
        # Phase 2.3: normalize to canonical thesis role.
        role = normalize_user_role(invite.get("role")) or ROLE_AP_CLERK
    else:
        # Resolve org from email domain — never trust caller-supplied org_id
        email_domain = email.split("@")[1].lower() if "@" in email else ""
        from clearledgr.core.database import get_db as _get_db
        _db = _get_db()
        org = _db.get_organization_by_domain(email_domain) if email_domain else None
        if org:
            org_id = str(org.get("id") or org.get("organization_id"))
        else:
            org_id = "default"
            logger.warning("No org found for domain %s — new user will be placed in default org", email_domain)
        role = ROLE_AP_CLERK

    user = get_user_by_email(email)
    if user is None:
        user = create_user_from_google(email=email, google_id=google_id, organization_id=org_id)
    else:
        db.update_user(user.id, google_id=google_id, is_active=True)
        # Do not reassign existing users to a different org
        user = get_user_by_email(email)
    if user is None:
        raise HTTPException(status_code=500, detail="failed_to_create_user")

    if invite:
        db.update_user(user.id, role=role, organization_id=org_id)
        db.accept_team_invite(str(invite.get("id")), accepted_by=user.id)
        user = get_user_by_email(email) or user

    jwt_token = create_access_token(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        role=user.role,
    )
    auth_code = _issue_google_auth_code(
        access_token=jwt_token,
        refresh_token=None,
        organization_id=user.organization_id,
    )
    redirect_path = _sanitize_redirect_path(state_payload.get("redirect_path"))
    redirect_url = _append_query_params(
        redirect_path,
        {
            "auth_code": auth_code,
            "org": user.organization_id,
        },
    )
    return RedirectResponse(url=redirect_url)


@router.post("/google/exchange", response_model=TokenResponse)
async def exchange_google_auth_code(request: GoogleAuthCodeExchangeRequest, response: Response):
    payload = _consume_google_auth_code(request.auth_code)
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise HTTPException(status_code=400, detail="invalid_auth_code_payload")
    _set_workspace_session_cookies(response, access_token)
    return TokenResponse(
        access_token=SESSION_TOKEN_PLACEHOLDER,
        refresh_token=SESSION_TOKEN_PLACEHOLDER,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/popup-complete")
async def google_oauth_popup_complete():
    """HTML landing page for the Gmail-extension OAuth popup.

    The OnboardingFlow opens /auth/google/start in a popup window.
    Google redirects to /auth/google/callback, which redirects again
    to this page with ?auth_code=...&org=... in the URL.

    This page:
      1. Reads auth_code from window.location.search
      2. POSTs it to /auth/google/exchange (which sets HttpOnly
         session cookies on api.clearledgr.com)
      3. Notifies window.opener via postMessage so the parent (the
         Gmail tab running the extension) can re-bootstrap with the
         new cookies
      4. Auto-closes after a short delay

    The opener (oauthBridge in the extension) ALSO polls for popup
    close as a fallback in case postMessage is blocked by a strict
    referrer policy. Either path triggers the bootstrap refresh.
    """
    html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Clearledgr — signing you in</title>
    <style>
      body{font-family:-apple-system,'Segoe UI',sans-serif;background:#0A1628;color:#fff;
           display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
      .card{text-align:center;padding:32px;max-width:360px}
      h1{font-size:18px;font-weight:600;margin:0 0 8px}
      p{font-size:13px;opacity:0.7;margin:0}
      .ok{color:#00D67E}
      .err{color:#FCA5A5}
    </style>
  </head>
  <body>
    <div class="card">
      <h1 id="title">Signing you in…</h1>
      <p id="detail">This window will close automatically.</p>
    </div>
    <script>
      (async function () {
        const params = new URLSearchParams(window.location.search);
        const authCode = params.get('auth_code');
        const orgId = params.get('org') || 'default';
        const titleEl = document.getElementById('title');
        const detailEl = document.getElementById('detail');

        function notifyAndClose(success, detail) {
          try {
            if (window.opener && !window.opener.closed) {
              window.opener.postMessage({
                type: 'clearledgr_oauth_complete',
                success: !!success,
                organizationId: orgId,
                detail: detail || null
              }, '*');
            }
          } catch (_) { /* postMessage failures are fine — opener also polls */ }
          setTimeout(function () { try { window.close(); } catch (_) {} }, 800);
        }

        if (!authCode) {
          titleEl.textContent = 'Sign-in failed';
          titleEl.className = 'err';
          detailEl.textContent = 'No auth_code in the redirect URL.';
          notifyAndClose(false, 'missing_auth_code');
          return;
        }

        try {
          const res = await fetch('/auth/google/exchange', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ auth_code: authCode })
          });
          if (!res.ok) {
            const body = await res.text();
            titleEl.textContent = 'Sign-in failed';
            titleEl.className = 'err';
            detailEl.textContent = 'Server returned ' + res.status + '.';
            notifyAndClose(false, body.slice(0, 200));
            return;
          }
          titleEl.textContent = 'Signed in';
          titleEl.className = 'ok';
          detailEl.textContent = 'Returning to Gmail…';
          notifyAndClose(true);
        } catch (err) {
          titleEl.textContent = 'Sign-in failed';
          titleEl.className = 'err';
          detailEl.textContent = String(err && err.message || err);
          notifyAndClose(false, 'network_error');
        }
      })();
    </script>
  </body>
</html>"""
    return HTMLResponse(content=html)


@router.post("/invites/accept")
async def accept_invite(request: InviteAcceptRequest, response: Response):
    """Accept an invite-link and create/join user account."""
    from clearledgr.core.database import get_db
    from clearledgr.core.auth import get_user_by_email

    db = get_db()
    invite = db.get_team_invite_by_token(request.token)
    if not invite:
        raise HTTPException(status_code=404, detail="invite_not_found")
    if invite.get("status") != "pending":
        raise HTTPException(status_code=400, detail="invite_not_pending")

    expires_at = invite.get("expires_at")
    if expires_at:
        expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires:
            raise HTTPException(status_code=400, detail="invite_expired")

    email = str(invite.get("email")).lower().strip()
    # Phase 2.3: normalize legacy invite roles to canonical thesis values.
    from clearledgr.core.auth import normalize_user_role, ROLE_AP_CLERK
    role = normalize_user_role(invite.get("role")) or ROLE_AP_CLERK
    organization_id = str(invite.get("organization_id") or "default")
    user = get_user_by_email(email)
    if user is None:
        if not request.password:
            raise HTTPException(status_code=400, detail="password_required_for_new_user")
        created = create_user(
            email=email,
            password=request.password,
            name=(request.name or email.split("@")[0].replace(".", " ").title()),
            organization_id=organization_id,
            role=role,
        )
        user = created
    else:
        db.update_user(user.id, organization_id=organization_id, role=role, is_active=True)
        user = get_user_by_email(email)

    if user is None:
        raise HTTPException(status_code=500, detail="invite_accept_failed")

    db.accept_team_invite(str(invite.get("id")), accepted_by=user.id)
    access = create_access_token(user.id, user.email, user.organization_id, user.role)
    _set_workspace_session_cookies(response, access)
    return {
        "success": True,
        "user": user,
        "access_token": SESSION_TOKEN_PLACEHOLDER,
        "refresh_token": SESSION_TOKEN_PLACEHOLDER,
        "token_type": "bearer",
    }
