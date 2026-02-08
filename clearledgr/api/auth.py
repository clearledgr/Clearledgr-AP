"""
Clearledgr Auth API

Authentication endpoints for login, registration, and token management.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr, Field, validator
import re

from clearledgr.core.auth import (
    LoginRequest, TokenResponse, User,
    create_user, authenticate_user,
    create_access_token, create_refresh_token,
    decode_token, get_current_user, TokenData,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


class RegisterRequest(BaseModel):
    """Registration request with validation."""
    email: EmailStr
    password: str = Field(..., min_length=8)
    name: str = Field(..., min_length=2, max_length=100)
    organization_id: str = Field(..., min_length=1, max_length=50)
    
    @validator("password")
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        return v
    
    @validator("name")
    def sanitize_name(cls, v):
        # Remove any HTML/script tags
        v = re.sub(r"<[^>]*>", "", v)
        # Remove any SQL injection attempts
        v = re.sub(r"[;'\"\-\-]", "", v)
        return v.strip()
    
    @validator("organization_id")
    def sanitize_org_id(cls, v):
        # Only allow alphanumeric and hyphens
        if not re.match(r"^[a-zA-Z0-9\-_]+$", v):
            raise ValueError("Organization ID can only contain letters, numbers, hyphens, and underscores")
        return v.lower()


class RefreshRequest(BaseModel):
    """Token refresh request."""
    refresh_token: str


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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
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
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshRequest):
    """
    Get new access token using refresh token.
    """
    payload = decode_token(request.refresh_token)
    
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
async def logout(current_user: TokenData = Depends(get_current_user)):
    """
    Logout current user.
    
    Note: In a stateless JWT system, logout is handled client-side
    by removing the token. This endpoint is for audit logging.
    """
    # In production, you might:
    # - Add token to blacklist
    # - Log the logout event
    # - Invalidate refresh tokens
    
    return {"message": "Logged out successfully", "user_id": current_user.user_id}


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
