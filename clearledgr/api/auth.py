"""
Clearledgr Auth API

Authentication endpoints for login, registration, and token management.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Depends, Query, Response, Cookie
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
import re

from clearledgr.core.auth import (
    LoginRequest, TokenResponse, User,
    create_user, authenticate_user,
    create_access_token, create_refresh_token,
    decode_token, get_current_user, get_optional_user, TokenData,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

ADMIN_ACCESS_COOKIE_NAME = "clearledgr_admin_access"
ADMIN_REFRESH_COOKIE_NAME = "clearledgr_admin_refresh"
ADMIN_CSRF_COOKIE_NAME = "clearledgr_admin_csrf"


def _session_cookie_secure() -> bool:
    env_name = str(os.getenv("ENV", "dev")).strip().lower()
    return env_name in {"prod", "production", "staging", "stage"}


def _session_cookie_domain() -> Optional[str]:
    raw = str(os.getenv("ADMIN_SESSION_COOKIE_DOMAIN", "")).strip()
    return raw or None


def _set_admin_session_cookies(response: Response, access_token: str, refresh_token: str) -> None:
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
        ADMIN_ACCESS_COOKIE_NAME,
        access_token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        **cookie_kwargs,
    )
    response.set_cookie(
        ADMIN_REFRESH_COOKIE_NAME,
        refresh_token,
        httponly=True,
        max_age=7 * 24 * 60 * 60,
        **cookie_kwargs,
    )
    response.set_cookie(
        ADMIN_CSRF_COOKIE_NAME,
        csrf_token,
        httponly=False,
        max_age=7 * 24 * 60 * 60,
        **cookie_kwargs,
    )


def _clear_admin_session_cookies(response: Response) -> None:
    domain = _session_cookie_domain()
    for name in (ADMIN_ACCESS_COOKIE_NAME, ADMIN_REFRESH_COOKIE_NAME, ADMIN_CSRF_COOKIE_NAME):
        response.delete_cookie(name, path="/", domain=domain)


class RegisterRequest(BaseModel):
    """Registration request with validation."""
    email: EmailStr
    password: str = Field(..., min_length=8)
    name: str = Field(..., min_length=2, max_length=100)
    organization_id: str = Field(..., min_length=1, max_length=50)
    
    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        return v
    
    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        # Remove any HTML/script tags
        v = re.sub(r"<[^>]*>", "", v)
        # Remove any SQL injection attempts
        v = re.sub(r"[;'\"\-\-]", "", v)
        return v.strip()
    
    @field_validator("organization_id")
    @classmethod
    def sanitize_org_id(cls, v: str) -> str:
        # Only allow alphanumeric and hyphens
        if not re.match(r"^[a-zA-Z0-9\-_]+$", v):
            raise ValueError("Organization ID can only contain letters, numbers, hyphens, and underscores")
        return v.lower()


class RefreshRequest(BaseModel):
    """Token refresh request."""
    refresh_token: Optional[str] = None


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


_google_auth_code_store: Dict[str, Dict[str, Any]] = {}


def _google_auth_code_ttl_seconds() -> int:
    raw = str(os.getenv("GOOGLE_AUTH_CODE_TTL_SECONDS", "180")).strip()
    try:
        value = int(raw)
    except Exception:
        value = 180
    return max(30, min(value, 600))


def _sanitize_redirect_path(redirect_path: Optional[str]) -> str:
    path = str(redirect_path or "/").strip()
    if not path.startswith("/"):
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
    expired_codes = []
    for code, payload in _google_auth_code_store.items():
        expires_at = payload.get("expires_at")
        if isinstance(expires_at, datetime) and expires_at < now:
            expired_codes.append(code)
    for code in expired_codes:
        _google_auth_code_store.pop(code, None)

    auth_code = secrets.token_urlsafe(32)
    _google_auth_code_store[auth_code] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "organization_id": str(organization_id or "default"),
        "expires_at": now + timedelta(seconds=_google_auth_code_ttl_seconds()),
    }
    return auth_code


def _consume_google_auth_code(code: str) -> Dict[str, Any]:
    payload = _google_auth_code_store.pop(str(code or "").strip(), None)
    if not payload:
        raise HTTPException(status_code=400, detail="invalid_auth_code")
    expires_at = payload.get("expires_at")
    if isinstance(expires_at, datetime) and datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="expired_auth_code")
    return payload


@router.post("/register", response_model=User)
async def register(request: RegisterRequest):
    """
    Register a new user.
    
    Password requirements:
    - At least 8 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    """
    try:
        user = create_user(
            email=request.email,
            password=request.password,
            name=request.name,
            organization_id=request.organization_id,
        )
        return user
    except HTTPException:
        raise
    except Exception as e:
        from clearledgr.core.errors import safe_error
        raise HTTPException(status_code=500, detail=safe_error(e, "user registration"))


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, response: Response):
    """
    Login with email and password.
    
    Returns:
    - access_token: JWT for API access (expires in 60 min)
    - refresh_token: JWT for getting new access tokens (expires in 7 days)
    """
    user = authenticate_user(request.email, request.password)
    
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )
    
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        role=user.role,
    )
    
    refresh_token = create_refresh_token(user_id=user.id)
    _set_admin_session_cookies(response, access_token, refresh_token)
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: RefreshRequest,
    response: Response,
    refresh_cookie: Optional[str] = Cookie(default=None, alias=ADMIN_REFRESH_COOKIE_NAME),
):
    """
    Get new access token using refresh token.
    """
    refresh_token_value = str(request.refresh_token or refresh_cookie or "").strip()
    if not refresh_token_value:
        raise HTTPException(status_code=401, detail="Refresh token required")
    payload = decode_token(refresh_token_value)
    
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")
    
    user_id = payload["sub"]
    
    # In production, look up user in database
    from clearledgr.core.auth import get_user_by_id
    user = get_user_by_id(user_id)
    
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        role=user.role,
    )
    
    new_refresh_token = create_refresh_token(user_id=user.id)
    _set_admin_session_cookies(response, access_token, new_refresh_token)
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


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
    
    _clear_admin_session_cookies(response)
    return {"message": "Logged out successfully", "user_id": getattr(current_user, "user_id", None)}


# ==================== GOOGLE IDENTITY ====================

class GoogleIdentityRequest(BaseModel):
    """Request from Gmail extension with Google identity."""
    email: EmailStr
    google_id: str = Field(..., description="Google account ID")


class GoogleIdentityResponse(BaseModel):
    """Response with Clearledgr token."""
    access_token: str
    expires_in: int
    user_id: str
    organization_id: str
    is_new_user: bool = False


@router.post("/google-identity", response_model=GoogleIdentityResponse)
async def authenticate_with_google_identity(request: GoogleIdentityRequest):
    """
    Authenticate using Google Identity from Gmail.
    
    This is used by the Gmail extension. Since the user is already
    signed into Gmail, we trust their Google identity and:
    
    1. If they're a registered Clearledgr user, return a token
    2. If not, auto-create an account based on their email domain
    
    No password needed - they're already authenticated with Google.
    """
    from clearledgr.core.auth import get_user_by_email, create_user_from_google
    
    # Check if user exists
    user = get_user_by_email(request.email)
    is_new = False
    
    if not user:
        # Auto-create user based on email domain
        # e.g., user@company.com -> organization: company
        domain = request.email.split("@")[1].split(".")[0]
        
        user = create_user_from_google(
            email=request.email,
            google_id=request.google_id,
            organization_id=domain,
        )
        is_new = True
    
    # Generate Clearledgr token
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        role=user.role,
    )
    
    return GoogleIdentityResponse(
        access_token=access_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user_id=user.id,
        organization_id=user.organization_id,
        is_new_user=is_new,
    )


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
    
    # Check access
    is_self = user_id == current_user.user_id
    is_admin = requesting_user.role == "admin"
    
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
    
    # Soft delete
    db.delete_user(user_id)
    
    return {"message": "User deleted", "user_id": user_id}


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
    
    requesting_user = get_user_by_id(current_user.user_id)
    if not requesting_user or requesting_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    if role not in ("admin", "member", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role")
    
    # Check if user already exists
    existing = get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")
    
    db = get_db()
    
    # Create pending user
    user_id = db.save_user(
        email=email,
        role=role,
        organization_id=requesting_user.organization_id,
        user_id=str(uuid.uuid4())
    )
    
    # TODO: Send invitation email
    # In production, queue an email task
    
    return {
        "message": "User invited",
        "user_id": user_id,
        "email": email,
        "role": role
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
    return {"auth_url": auth_url}


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

    if invite:
        if str(invite.get("email")).lower().strip() != email:
            raise HTTPException(status_code=403, detail="invite_email_mismatch")
        org_id = str(invite.get("organization_id"))
        role = str(invite.get("role") or "member")
    else:
        org_id = str(state_payload.get("organization_id") or email.split("@")[1].split(".")[0] or "default")
        role = "user"

    user = get_user_by_email(email)
    if user is None:
        user = create_user_from_google(email=email, google_id=google_id, organization_id=org_id)
    else:
        db.update_user(user.id, google_id=google_id, organization_id=org_id, is_active=True)
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
    refresh = create_refresh_token(user.id)
    auth_code = _issue_google_auth_code(
        access_token=jwt_token,
        refresh_token=refresh,
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
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        raise HTTPException(status_code=400, detail="invalid_auth_code_payload")
    _set_admin_session_cookies(response, access_token, refresh_token)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


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
    role = str(invite.get("role") or "member")
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
    refresh = create_refresh_token(user.id)
    _set_admin_session_cookies(response, access, refresh)
    return {
        "success": True,
        "user": user,
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
    }
