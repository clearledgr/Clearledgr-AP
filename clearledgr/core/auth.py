"""
Clearledgr Authentication

JWT-based authentication for all API endpoints.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from functools import wraps

from fastapi import HTTPException, Depends, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field

import jwt
from passlib.context import CryptContext

# Configuration
SECRET_KEY = os.getenv("CLEARLEDGR_SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Bearer token security
security = HTTPBearer(auto_error=False)


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
    role: str = "user"  # user, admin, owner
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
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


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
    
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """Create a JWT refresh token."""
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
    }
    
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> TokenData:
    """
    Get current authenticated user from JWT token or API key.
    
    Supports:
    - Bearer token: Authorization: Bearer <jwt>
    - API key: X-API-Key: <key>
    """
    # Try Bearer token first
    if credentials and credentials.credentials:
        payload = decode_token(credentials.credentials)
        
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        
        return TokenData(
            user_id=payload["sub"],
            email=payload["email"],
            organization_id=payload["org"],
            role=payload.get("role", "user"),
            exp=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        )
    
    # Try API key
    if x_api_key:
        # In production, look up API key in database
        # For now, accept format: org_<org_id>_<secret>
        if x_api_key.startswith("org_"):
            parts = x_api_key.split("_")
            if len(parts) >= 3:
                return TokenData(
                    user_id="api_user",
                    email="api@system",
                    organization_id=parts[1],
                    role="api",
                    exp=datetime.now(timezone.utc) + timedelta(hours=1),
                )
        
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    raise HTTPException(
        status_code=401,
        detail="Not authenticated. Provide Bearer token or X-API-Key header.",
    )


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Optional[TokenData]:
    """Get current user if authenticated, None otherwise."""
    try:
        return get_current_user(credentials, x_api_key)
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


# Simple in-memory user store for development
# In production, this would be a database table
_users_db: Dict[str, Dict[str, Any]] = {}


def create_user(email: str, password: str, name: str, organization_id: str, role: str = "user") -> User:
    """Create a new user."""
    import uuid
    
    if email in _users_db:
        raise HTTPException(status_code=400, detail="User already exists")
    
    user_id = str(uuid.uuid4())
    hashed = hash_password(password)
    
    user_data = {
        "id": user_id,
        "email": email,
        "name": name,
        "organization_id": organization_id,
        "role": role,
        "password_hash": hashed,
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    _users_db[email] = user_data
    
    return User(**{k: v for k, v in user_data.items() if k != "password_hash"})


def authenticate_user(email: str, password: str) -> Optional[User]:
    """Authenticate a user by email and password."""
    user_data = _users_db.get(email)
    
    if not user_data:
        return None
    
    if not verify_password(password, user_data["password_hash"]):
        return None
    
    if not user_data.get("is_active", True):
        return None
    
    return User(**{k: v for k, v in user_data.items() if k != "password_hash"})


def get_user_by_id(user_id: str) -> Optional[User]:
    """Get user by ID."""
    for user_data in _users_db.values():
        if user_data["id"] == user_id:
            return User(**{k: v for k, v in user_data.items() if k != "password_hash"})
    return None


def get_user_by_email(email: str) -> Optional[User]:
    """Get user by email."""
    user_data = _users_db.get(email)
    if user_data:
        return User(**{k: v for k, v in user_data.items() if k != "password_hash"})
    return None


def create_user_from_google(email: str, google_id: str, organization_id: str) -> User:
    """
    Create a user from Google Identity.
    
    This is used when someone uses the Gmail extension but isn't 
    registered yet. We auto-create their account.
    
    No password needed - they authenticate via Google.
    """
    import uuid
    
    if email in _users_db:
        # Return existing user
        user_data = _users_db[email]
        return User(**{k: v for k, v in user_data.items() if k != "password_hash"})
    
    # Extract name from email (best effort)
    name = email.split("@")[0].replace(".", " ").title()
    
    user_id = str(uuid.uuid4())
    
    user_data = {
        "id": user_id,
        "email": email,
        "name": name,
        "organization_id": organization_id,
        "role": "user",
        "google_id": google_id,
        "password_hash": None,  # No password - Google auth only
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    _users_db[email] = user_data
    
    # Also create default org config for the organization
    from clearledgr.core.org_config import get_or_create_config
    get_or_create_config(organization_id, organization_name=organization_id.title())
    
    return User(**{k: v for k, v in user_data.items() if k not in ["password_hash", "google_id"]})
