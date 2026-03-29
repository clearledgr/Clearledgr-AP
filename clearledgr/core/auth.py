"""
Clearledgr Authentication

JWT-based authentication backed by persistent database records.
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

logger = logging.getLogger(__name__)

# Compatibility stub used by legacy tests that import _users_db directly.
# Auth is now DB-backed; this dict is not used for actual auth, but tests can
# call _users_db.clear() without breaking.
_users_db: dict = {}

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7

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
    from clearledgr.core.database import get_db

    return get_db()


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


class LoginRequest(BaseModel):
    """Login request."""

    email: EmailStr
    password: str


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


def create_refresh_token(user_id: str) -> str:
    """Create a JWT refresh token."""
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
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
    return TokenData(
        user_id=payload["sub"],
        email=payload["email"],
        organization_id=payload["org"],
        role=payload.get("role", "user"),
        exp=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
    )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    workspace_access_cookie: Optional[str] = Cookie(default=None, alias="clearledgr_workspace_access"),
) -> TokenData:
    """
    Get current authenticated user from JWT token or API key.

    Supports:
    - Bearer token: Authorization: Bearer <jwt>
    - API key: X-API-Key: <key>
    """
    if credentials and credentials.credentials:
        payload = decode_token(credentials.credentials)
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return _token_data_from_payload(payload)

    if x_api_key:
        db = _get_db()
        key_record = db.validate_api_key(x_api_key)
        if key_record:
            return TokenData(
                user_id=key_record.get("user_id", "api_user"),
                email="api@system",
                organization_id=key_record["organization_id"],
                role="api",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        raise HTTPException(status_code=401, detail="Invalid API key")

    if workspace_access_cookie:
        payload = decode_token(workspace_access_cookie)
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return _token_data_from_payload(payload)

    raise HTTPException(
        status_code=401,
        detail="Not authenticated. Provide Bearer token, X-API-Key header, or valid workspace session cookie.",
    )


def normalize_user_role(role: Optional[str]) -> str:
    return str(role or "").strip().lower()


def has_ops_access(role: Optional[str]) -> bool:
    return normalize_user_role(role) in {"owner", "admin", "operator", "api"}


def has_admin_access(role: Optional[str]) -> bool:
    return normalize_user_role(role) in {"owner", "admin", "api"}


def require_ops_user(user: TokenData = Depends(get_current_user)) -> TokenData:
    if not has_ops_access(getattr(user, "role", None)):
        raise HTTPException(status_code=403, detail="ops_role_required")
    return user


def require_admin_user(user: TokenData = Depends(get_current_user)) -> TokenData:
    if not has_admin_access(getattr(user, "role", None)):
        raise HTTPException(status_code=403, detail="admin_role_required")
    return user


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


def authenticate_user(email: str, password: str) -> Optional[User]:
    """Authenticate a user by email and password."""
    db = _get_db()
    row = db.get_user_by_email(email)
    if not row:
        return None
    if not verify_password(password, row.get("password_hash")):
        return None
    if not bool(row.get("is_active", True)):
        return None
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
    """
    db = _get_db()
    domain = email.split("@")[1] if "@" in email else None
    db.ensure_organization(
        organization_id=organization_id,
        organization_name=organization_id.replace("-", " ").replace("_", " ").title(),
        domain=domain,
    )
    row = db.upsert_google_user(
        email=email,
        google_id=google_id,
        organization_id=organization_id,
        name=email.split("@")[0].replace(".", " ").title(),
        role="user",
    )
    return _row_to_user(row)
