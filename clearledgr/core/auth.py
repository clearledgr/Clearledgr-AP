"""
Clearledgr Authentication

JWT-based authentication backed by persistent database records.

Naming conventions
~~~~~~~~~~~~~~~~~~
- **user_id**: The authenticated human user (from JWT ``sub`` claim).
  Always corresponds to a row in the ``users`` table.
- **actor_id**: Who performed an action.  May be a ``user_id``, or a
  synthetic value like ``"system"`` or ``"agent"`` for automated actions.
  Stored in audit events and AP item transition logs.
"""

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Dict, Optional

from fastapi import Depends, Header, HTTPException, Cookie
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field

from clearledgr.core.database import get_db as _canonical_get_db

logger = logging.getLogger(__name__)

# Compatibility stub used by legacy tests that import _users_db directly.
# Auth is now DB-backed; this dict is not used for actual auth, but tests can
# call _users_db.clear() without breaking.
_users_db: dict = {}

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Bearer token security
security = HTTPBearer(auto_error=False)
_JWT_MODULE = None
_PWD_CONTEXT = None
_FALLBACK_PWD_CONTEXT = None
_BCRYPT_LIB = None
_BCRYPT_CHECKED = False


def _jwt_module():
    global _JWT_MODULE
    if _JWT_MODULE is None:
        import jwt as module

        _JWT_MODULE = module
    return _JWT_MODULE


def _password_context():
    global _PWD_CONTEXT
    if _PWD_CONTEXT is None:
        from passlib.context import CryptContext

        _PWD_CONTEXT = CryptContext(
            schemes=["pbkdf2_sha256", "bcrypt"],
            deprecated="auto",
        )
    return _PWD_CONTEXT


def _fallback_password_context():
    global _FALLBACK_PWD_CONTEXT
    if _FALLBACK_PWD_CONTEXT is None:
        from passlib.context import CryptContext

        _FALLBACK_PWD_CONTEXT = CryptContext(
            schemes=["pbkdf2_sha256"],
            deprecated="auto",
        )
    return _FALLBACK_PWD_CONTEXT


def _bcrypt_lib():
    global _BCRYPT_LIB
    global _BCRYPT_CHECKED
    if not _BCRYPT_CHECKED:
        try:
            import bcrypt as module

            _BCRYPT_LIB = module
        except Exception as exc:  # pragma: no cover
            logger.info("bcrypt not available, using fallback: %s", exc)
            _BCRYPT_LIB = None
        _BCRYPT_CHECKED = True
    return _BCRYPT_LIB


def _secret_key() -> str:
    from clearledgr.core.secrets import require_secret

    return require_secret("CLEARLEDGR_SECRET_KEY")


def _get_db():
    """Get database instance via canonical get_db()."""
    return _canonical_get_db()


class TokenData(BaseModel):
    """JWT token payload."""

    user_id: str
    email: str
    organization_id: str
    role: str = "user"
    exp: datetime


class User(BaseModel):
    """User model."""

    id: str
    email: EmailStr
    name: str
    organization_id: str
    role: str = "user"
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TokenResponse(BaseModel):
    """Token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


def hash_password(password: str) -> str:
    """Hash a password."""
    try:
        return _password_context().hash(password)
    except Exception as e:
        logger.warning("Primary password hashing failed, using fallback: %s", e)
        return _fallback_password_context().hash(password)


def verify_password(plain_password: str, hashed_password: Optional[str]) -> bool:
    """Verify a password against its hash."""
    if not hashed_password:
        return False
    try:
        return _password_context().verify(plain_password, hashed_password)
    except Exception as e:
        logger.warning("Primary password verification failed, trying fallbacks: %s", e)
        bcrypt_lib = _bcrypt_lib()
        if hashed_password.startswith("$2") and bcrypt_lib is not None:
            try:
                return bool(
                    bcrypt_lib.checkpw(
                        plain_password.encode("utf-8"),
                        hashed_password.encode("utf-8"),
                    )
                )
            except Exception as e:
                logger.warning("bcrypt fallback verification failed: %s", e)
        # Fallback verification path for pbkdf2 hashes when bcrypt backend is unavailable.
        try:
            return _fallback_password_context().verify(plain_password, hashed_password)
        except Exception as e:
            logger.error("All password verification methods failed: %s", e)
            return False


def create_access_token(
    user_id: str,
    email: str,
    organization_id: str,
    role: str = "user",
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a JWT access token."""
    if expires_delta is None:
        expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    expire = datetime.now(timezone.utc) + expires_delta
    payload = {
        "sub": user_id,
        "email": email,
        "org": organization_id,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    return _jwt_module().encode(payload, _secret_key(), algorithm=ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT token."""
    jwt = _jwt_module()
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _token_data_from_payload(payload: Dict[str, Any]) -> TokenData:
    # Phase 2.3: normalize legacy role strings on every token decode
    # so predicates only ever see canonical thesis values.
    raw_role = payload.get("role") or ROLE_AP_CLERK
    return TokenData(
        user_id=payload["sub"],
        email=payload["email"],
        organization_id=payload["org"],
        role=normalize_user_role(raw_role) or ROLE_AP_CLERK,
        exp=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
    )


def _reconcile_token_data(token_data: TokenData) -> TokenData:
    """Prefer the canonical DB user record over stale token claims.

    Browser extension workspace tokens can outlive role or user-id migrations.
    Reconcile by user_id first, then email, so role changes land
    immediately without forcing the user through a full re-auth cycle.

    Phase 2.3: roles are always normalized to canonical thesis values
    on the way out so downstream predicates operate on ``cfo``,
    ``ap_manager``, etc., regardless of what the stale token claimed.
    """
    try:
        db = _get_db()
        row = None
        user_id = str(getattr(token_data, "user_id", "") or "").strip()
        email = str(getattr(token_data, "email", "") or "").strip().lower()
        if user_id:
            row = db.get_user(user_id)
        if not row and email:
            row = db.get_user_by_email(email)
        if not row:
            return token_data
        raw_role = (
            row.get("role")
            or getattr(token_data, "role", None)
            or ROLE_AP_CLERK
        )
        return TokenData(
            user_id=str(row.get("id") or user_id or email or "unknown"),
            email=str(row.get("email") or email or getattr(token_data, "email", "") or ""),
            organization_id=str(
                row.get("organization_id")
                or getattr(token_data, "organization_id", None)
                or "default"
            ),
            role=normalize_user_role(raw_role) or ROLE_AP_CLERK,
            exp=token_data.exp,
        )
    except Exception:
        return token_data


def _assert_org_not_deleted(token_data: TokenData) -> TokenData:
    """Reject requests scoped to a soft-deleted organization.

    The admin "DELETE /organizations/{id}" flow stamps
    organizations.deleted_at as a tombstone. A JWT minted before the
    deletion is still cryptographically valid, but we don't want it
    granting access to tenant data — the tenant is gone. Any valid
    token for a deleted org gets 403. Failure to read the org row
    (DB hiccup, unknown org) fails OPEN to preserve availability;
    we log and rely on tenant-isolation elsewhere. Defence in depth,
    not the only line.
    """
    try:
        org_id = str(getattr(token_data, "organization_id", "") or "").strip()
        if not org_id:
            return token_data
        db = _get_db()
        org = db.get_organization(org_id)
        if org and org.get("deleted_at"):
            raise HTTPException(
                status_code=403,
                detail="organization_deleted",
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("org deletion guard check failed: %s", exc)
    return token_data


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    workspace_access_cookie: Optional[str] = Cookie(default=None, alias="clearledgr_workspace_access"),
) -> TokenData:
    """
    Get current authenticated user.

    Supports (in order):
    1. Bearer token: Clearledgr JWT OR Google OAuth access token (Streak-style)
    2. API key: X-API-Key header
    3. Session cookie
    """
    if credentials and credentials.credentials:
        token = credentials.credentials
        # Try Clearledgr JWT first
        try:
            payload = decode_token(token)
            if payload.get("type") == "access":
                return _assert_org_not_deleted(
                    _reconcile_token_data(_token_data_from_payload(payload))
                )
        except HTTPException:
            pass  # Not a Clearledgr JWT — try Google OAuth below

        # Try Google OAuth token (Streak pattern: extension passes Google token directly)
        google_user = _validate_google_token(token)
        if google_user:
            return _assert_org_not_deleted(google_user)

        raise HTTPException(status_code=401, detail="Invalid token")

    if x_api_key:
        db = _get_db()
        key_record = db.validate_api_key(x_api_key)
        if key_record:
            return _assert_org_not_deleted(TokenData(
                user_id=key_record.get("user_id", "api_user"),
                email="api@system",
                organization_id=key_record["organization_id"],
                role="api",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
        raise HTTPException(status_code=401, detail="Invalid API key")

    if workspace_access_cookie:
        try:
            payload = decode_token(workspace_access_cookie)
            if payload.get("type") == "access":
                return _assert_org_not_deleted(
                    _reconcile_token_data(_token_data_from_payload(payload))
                )
        except HTTPException:
            pass
        # Cookie might also be a Google token
        google_user = _validate_google_token(workspace_access_cookie)
        if google_user:
            return _assert_org_not_deleted(google_user)

    raise HTTPException(
        status_code=401,
        detail="Not authenticated. Provide Bearer token, X-API-Key header, or valid workspace session cookie.",
    )


# Cache Google token validation results to avoid hitting tokeninfo on every request
_google_token_cache: Dict[str, tuple] = {}  # token -> (TokenData, expires_at)
_GOOGLE_TOKEN_CACHE_TTL = 300  # 5 minutes


def _validate_google_token(token: str) -> Optional[TokenData]:
    """Validate a Google OAuth access token and resolve to a Clearledgr user.

    Calls Google's tokeninfo endpoint to verify the token and get the email.
    Then looks up the user in the database by email.
    Results are cached for 5 minutes to avoid hammering Google on every request.
    """
    # Check cache first
    cached = _google_token_cache.get(token)
    if cached:
        token_data, expires_at = cached
        if datetime.now(timezone.utc) < expires_at:
            return token_data
        else:
            _google_token_cache.pop(token, None)

    try:
        import httpx
        response = httpx.get(
            "https://www.googleapis.com/oauth2/v3/tokeninfo",
            params={"access_token": token},
            timeout=10,
        )
        if response.status_code != 200:
            return None

        info = response.json()
        email = info.get("email")
        if not email:
            return None

        # Look up user by email
        db = _get_db()
        user = db.get_user_by_email(email)
        if not user:
            # Auto-provision: resolve org from email domain (Streak pattern)
            email_domain = email.split("@")[1].lower() if "@" in email else ""
            domain_org = db.get_organization_by_domain(email_domain) if email_domain else None
            provision_org = str((domain_org or {}).get("id") or "default")
            if not domain_org:
                logger.warning("No org found for domain %s — auto-provisioning into default org", email_domain)
            user = db.create_user(
                email=email,
                name=email.split("@")[0],
                organization_id=provision_org,
                role="operator",
            )
            logger.info("Auto-provisioned user from Google OAuth: %s org=%s", email, provision_org)

        user_id = user.get("id") or user.get("user_id") or email
        token_data = TokenData(
            user_id=user_id,
            email=email,
            organization_id=user.get("organization_id", "default"),
            role=user.get("role", "operator"),
            exp=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        # Record activity for approver health checks
        try:
            db.update_user(user_id, last_seen_at=datetime.now(timezone.utc).isoformat())
        except Exception:
            pass  # Non-critical — don't block auth on tracking failure

        # Cache the result
        _google_token_cache[token] = (
            token_data,
            datetime.now(timezone.utc) + timedelta(seconds=_GOOGLE_TOKEN_CACHE_TTL),
        )

        # Evict stale entries
        if len(_google_token_cache) > 100:
            now = datetime.now(timezone.utc)
            stale = [k for k, (_, exp) in _google_token_cache.items() if now >= exp]
            for k in stale:
                _google_token_cache.pop(k, None)

        return token_data

    except Exception as exc:
        logger.debug("Google token validation failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Phase 2.3 — Five-role thesis taxonomy (DESIGN_THESIS.md §17)
# ---------------------------------------------------------------------------
#
# The thesis defines five human roles plus an ``owner`` superuser and a
# service-account ``api`` role. Permissions are additive-upward: a CFO
# has every permission an AP Manager has, etc. Each role has an integer
# rank and a predicate ``has_<role>(role) → bool`` that returns True for
# the role itself AND any role with a higher rank.
#
# Role canonical values:
#     read_only             — view-only seat
#     ap_clerk              — default write seat; day-to-day invoice entry
#     ap_manager            — ops surface; approve within auto-approve limits
#     financial_controller  — configure org settings + high-value approvals
#     cfo                   — fraud control parameters + autonomy tier changes
#     owner                 — org creator; universal superuser
#     api                   — service account; equivalent to owner for automation
#
# Legacy role strings (``user``, ``admin``, ``operator``, ``viewer``,
# ``member``) are normalized to their thesis equivalent at every read
# boundary via ``normalize_user_role``. The same transformation is
# applied in place by migration v15 so database rows match the normalized
# shape. There is no backward-compat shim: after the migration runs,
# ``admin`` is not a canonical value anywhere in the system — it's
# silently upgraded to ``financial_controller`` at read time if it
# still appears on a stale JWT.

ROLE_READ_ONLY = "read_only"
ROLE_AP_CLERK = "ap_clerk"
ROLE_AP_MANAGER = "ap_manager"
ROLE_FINANCIAL_CONTROLLER = "financial_controller"
ROLE_CFO = "cfo"
ROLE_OWNER = "owner"
ROLE_API = "api"

# Canonical rank map. Higher = more powerful. Missing/unknown roles
# get rank 0, which fails every ``has_at_least`` check.
ROLE_RANK: Dict[str, int] = {
    ROLE_READ_ONLY: 10,
    ROLE_AP_CLERK: 20,
    ROLE_AP_MANAGER: 40,
    ROLE_FINANCIAL_CONTROLLER: 60,
    ROLE_CFO: 80,
    ROLE_OWNER: 100,
    # Service accounts equivalent to owner for automation paths.
    ROLE_API: 100,
}

# Mapping from legacy role strings → thesis canonical values. Applied
# by ``normalize_user_role`` at every read boundary. Migration v15
# rewrites the ``users.role`` column in place so stored values match
# the normalized shape going forward.
_LEGACY_ROLE_MAP: Dict[str, str] = {
    "user": ROLE_AP_CLERK,
    "member": ROLE_AP_CLERK,
    "operator": ROLE_AP_MANAGER,
    "admin": ROLE_FINANCIAL_CONTROLLER,
    "viewer": ROLE_READ_ONLY,
}


def normalize_user_role(role: Optional[str]) -> str:
    """Return the canonical thesis role for a potentially-legacy input.

    Empty, None, or unknown inputs return an empty string — callers
    that need a default seat should explicitly fall back to
    ``ROLE_AP_CLERK``. This function is intentionally conservative:
    it never promotes an unknown role to a default, because that
    would be a privilege-escalation vector on malformed tokens.
    """
    raw = str(role or "").strip().lower()
    if not raw:
        return ""
    if raw in ROLE_RANK:
        return raw
    if raw in _LEGACY_ROLE_MAP:
        return _LEGACY_ROLE_MAP[raw]
    return raw  # preserve unknown values so predicates reject them


def has_at_least(role: Optional[str], minimum: str) -> bool:
    """Return True iff ``role`` has at least the rank of ``minimum``."""
    normalized = normalize_user_role(role)
    min_normalized = normalize_user_role(minimum)
    return ROLE_RANK.get(normalized, 0) >= ROLE_RANK.get(min_normalized, 0)


def has_read_only(role: Optional[str]) -> bool:
    return has_at_least(role, ROLE_READ_ONLY)


def has_ap_clerk(role: Optional[str]) -> bool:
    return has_at_least(role, ROLE_AP_CLERK)


def has_ap_manager(role: Optional[str]) -> bool:
    return has_at_least(role, ROLE_AP_MANAGER)


def has_financial_controller(role: Optional[str]) -> bool:
    return has_at_least(role, ROLE_FINANCIAL_CONTROLLER)


def has_cfo(role: Optional[str]) -> bool:
    """Return True for CFO, owner, or api roles."""
    return has_at_least(role, ROLE_CFO)


def has_owner(role: Optional[str]) -> bool:
    return has_at_least(role, ROLE_OWNER)


# ---------------------------------------------------------------------------
# Legacy predicates — kept at the same names but delegate to the rank
# system. The semantic mapping is documented inline so future reviewers
# understand why ``has_ops_access`` means "AP Manager or higher".
# ---------------------------------------------------------------------------


def has_ops_access(role: Optional[str]) -> bool:
    """Ops surface access — AP Manager rank or higher.

    Historically ``has_ops_access`` gated on ``{owner, admin, operator, api}``.
    Under the thesis taxonomy these map to ``{owner, financial_controller,
    ap_manager, api}`` — all rank ≥ 40, which is ``ap_manager``.
    """
    return has_at_least(role, ROLE_AP_MANAGER)


def has_admin_access(role: Optional[str]) -> bool:
    """Admin surface access — Financial Controller rank or higher.

    Historically ``has_admin_access`` gated on ``{owner, admin, api}``.
    Under the thesis taxonomy these map to ``{owner, financial_controller, api}``
    — all rank ≥ 60, which is ``financial_controller``.
    """
    return has_at_least(role, ROLE_FINANCIAL_CONTROLLER)


def has_fraud_control_admin(role: Optional[str]) -> bool:
    """Alias for ``has_cfo``. Kept for readability at call sites that
    want to emphasize the fraud-control intent (e.g., Phase 1.2a's
    /fraud-controls API). Semantically identical.
    """
    return has_cfo(role)


def require_ops_user(user: TokenData = Depends(get_current_user)) -> TokenData:
    if not has_ops_access(getattr(user, "role", None)):
        raise HTTPException(status_code=403, detail="ap_manager_role_required")
    return user


def require_admin_user(user: TokenData = Depends(get_current_user)) -> TokenData:
    if not has_admin_access(getattr(user, "role", None)):
        raise HTTPException(
            status_code=403, detail="financial_controller_role_required"
        )
    return user


def require_cfo(user: TokenData = Depends(get_current_user)) -> TokenData:
    """FastAPI dependency: CFO or owner role required.

    Used for fraud-control parameter modification, IBAN change
    verification, vendor trusted-domain allowlist writes, and any
    other CFO-level write surface per DESIGN_THESIS.md §17.
    """
    if not has_cfo(getattr(user, "role", None)):
        raise HTTPException(
            status_code=403,
            detail="cfo_role_required",
        )
    return user


def require_financial_controller(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """FastAPI dependency: Financial Controller, CFO, or owner required."""
    if not has_financial_controller(getattr(user, "role", None)):
        raise HTTPException(
            status_code=403,
            detail="financial_controller_role_required",
        )
    return user


def require_ap_manager(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """FastAPI dependency: AP Manager, Controller, CFO, or owner required."""
    if not has_ap_manager(getattr(user, "role", None)):
        raise HTTPException(
            status_code=403,
            detail="ap_manager_role_required",
        )
    return user


def require_ap_clerk(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """FastAPI dependency: any write-capable human role (clerk or above)."""
    if not has_ap_clerk(getattr(user, "role", None)):
        raise HTTPException(
            status_code=403,
            detail="ap_clerk_role_required",
        )
    return user


# Phase 2.3 hard cutover: ``require_fraud_control_admin`` is removed.
# Every caller migrated to ``require_cfo`` in the same commit so there
# is no backcompat shim. If a future reviewer wants to track fraud-
# control surfaces specifically, grep for ``require_cfo`` (which is
# what every current CFO-gated endpoint uses).


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    workspace_access_cookie: Optional[str] = Cookie(default=None, alias="clearledgr_workspace_access"),
) -> Optional[TokenData]:
    """Get current user if authenticated, None otherwise."""
    try:
        return get_current_user(credentials, x_api_key, workspace_access_cookie)
    except HTTPException:
        return None


def require_role(allowed_roles: list[str]):
    """Decorator to require specific roles."""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, user: TokenData = Depends(get_current_user), **kwargs):
            if user.role not in allowed_roles:
                raise HTTPException(
                    status_code=403,
                    detail=f"Role '{user.role}' not authorized. Required: {allowed_roles}",
                )
            return await func(*args, user=user, **kwargs)

        return wrapper

    return decorator


def require_org(org_id_param: str = "organization_id"):
    """Decorator to verify user belongs to the organization."""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, user: TokenData = Depends(get_current_user), **kwargs):
            request_org = kwargs.get(org_id_param)
            if request_org and request_org != user.organization_id:
                raise HTTPException(
                    status_code=403,
                    detail="Not authorized to access this organization's data",
                )
            return await func(*args, user=user, **kwargs)

        return wrapper

    return decorator


def _row_to_user(row: Dict[str, Any]) -> User:
    created_raw = row.get("created_at")
    created_at = (
        datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
        if created_raw
        else datetime.now(timezone.utc)
    )
    # §13: Read Only seats expire after configurable period
    seat_type = str(row.get("seat_type") or "full").lower()
    seat_expires = row.get("seat_expires_at")
    if seat_type == "read_only" and seat_expires:
        try:
            expires_dt = datetime.fromisoformat(str(seat_expires).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires_dt:
                return None  # Expired Read Only seat — block access
        except (ValueError, TypeError):
            pass

    return User(
        id=str(row.get("id")),
        email=str(row.get("email")),
        name=str(row.get("name") or ""),
        organization_id=str(row.get("organization_id")),
        role=str(row.get("role") or "user"),
        is_active=bool(row.get("is_active", True)),
        created_at=created_at,
    )


def create_user(
    email: str,
    password: str,
    name: str,
    organization_id: str,
    role: str = "user",
) -> User:
    """Create a new user in persistent storage.  Idempotent: returns existing user if found."""
    db = _get_db()
    existing = db.get_user_by_email(email)
    if existing:
        return _row_to_user(existing)

    db.ensure_organization(
        organization_id=organization_id,
        organization_name=organization_id.replace("-", " ").replace("_", " ").title(),
        domain=(email.split("@")[1] if "@" in email else None),
    )
    row = db.create_user(
        email=email,
        name=name,
        organization_id=organization_id,
        role=role,
        password_hash=hash_password(password),
        is_active=True,
    )
    return _row_to_user(row)


def get_user_by_id(user_id: str) -> Optional[User]:
    """Get user by ID."""
    row = _get_db().get_user(user_id)
    return _row_to_user(row) if row else None


def get_user_by_email(email: str) -> Optional[User]:
    """Get user by email."""
    row = _get_db().get_user_by_email(email)
    return _row_to_user(row) if row else None


def create_user_from_google(email: str, google_id: str, organization_id: str) -> User:
    """
    Create or update a user from Google identity.

    First user in the org becomes the owner — they're the one claiming
    the organization through the onboarding flow and need to be able to
    configure the ERP, complete onboarding steps, invite teammates. Any
    subsequent Google sign-in uses the default ap_clerk seat; existing
    users keep their role on re-auth.
    """
    db = _get_db()
    domain = email.split("@")[1] if "@" in email else None
    db.ensure_organization(
        organization_id=organization_id,
        organization_name=organization_id.replace("-", " ").replace("_", " ").title(),
        domain=domain,
    )
    existing_users = db.get_users(organization_id, include_inactive=True) or []
    role = ROLE_OWNER if not existing_users else "user"
    row = db.upsert_google_user(
        email=email,
        google_id=google_id,
        organization_id=organization_id,
        name=email.split("@")[0].replace(".", " ").title(),
        role=role,
    )
    # Promote the sole user in an org to owner if they aren't already.
    # Covers two cases:
    #   1. Existing deploys where the first user signed in before this
    #      promotion logic landed and got stuck as ap_clerk.
    #   2. Re-auth on an empty org where upsert found their row but we
    #      want them to be the owner.
    # Does nothing once a second user joins — subsequent sign-ins keep
    # whatever role they were assigned.
    try:
        if len(existing_users) <= 1:
            row_id = str(row.get("id") or "")
            current_role = str(row.get("role") or "")
            if row_id and current_role != ROLE_OWNER:
                db.update_user(row_id, role=ROLE_OWNER)
                refreshed = db.get_user(row_id)
                if refreshed:
                    row = refreshed
    except Exception:  # noqa: BLE001 — promotion is best-effort
        pass
    return _row_to_user(row)
