"""
Solden Authentication

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
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Dict, Optional

from fastapi import Depends, Header, HTTPException, Cookie
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field

from solden.core.database import get_db as _canonical_get_db

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
    from solden.core.secrets import require_secret

    return require_secret("CLEARLEDGR_SECRET_KEY")


def _get_db():
    """Get database instance via canonical get_db()."""
    return _canonical_get_db()


class TokenData(BaseModel):
    """JWT token payload.

    Migration v89: ``workspace_role`` is the canonical org-governance
    axis. ``role`` survives as the legacy single-axis field so
    in-flight tokens / older callsites continue to deserialize.
    Helpers below (``_workspace_role_of``) prefer ``workspace_role``
    and fall back to ``role`` when only the legacy field is set.
    """

    user_id: str
    email: str
    organization_id: str
    role: str = "user"  # DEPRECATED post-v89; read via _workspace_role_of()
    workspace_role: str = ""
    exp: datetime


class User(BaseModel):
    """User model.

    Migration v89: ``workspace_role`` is the canonical org-governance
    axis. ``role`` is the legacy single-axis value, retained on the
    model for the sweep window so callers that still read ``user.role``
    don't 500. Both fields are written by ``_row_to_user`` from the DB
    columns ``users.role`` (legacy) and ``users.workspace_role`` (new).
    """

    id: str
    email: EmailStr
    name: str
    organization_id: str
    role: str = "user"  # DEPRECATED post-v89
    workspace_role: str = ""
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
    workspace_role: Optional[str] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a JWT access token.

    Migration v89: ``workspace_role`` is the canonical org-governance
    axis on the wire. ``role`` continues to be written for the sweep
    window so JWTs that hit older callers still decode usefully.
    If only ``role`` is supplied (legacy callers), workspace_role is
    derived via ``normalize_workspace_role``.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    expire = datetime.now(timezone.utc) + expires_delta
    resolved_workspace = (
        workspace_role
        or normalize_workspace_role(role)
        or WORKSPACE_ROLE_MEMBER
    )
    payload = {
        "sub": user_id,
        "email": email,
        "org": organization_id,
        "role": role,
        "workspace_role": resolved_workspace,
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
    """Decode JWT payload into TokenData.

    Migration v89: prefer the new ``workspace_role`` claim; fall back
    to deriving from the legacy ``role`` claim for tokens minted
    before the cutover. ``role`` is preserved on the model so legacy
    callers still see something useful.
    """
    raw_role = payload.get("role") or ""
    raw_workspace = payload.get("workspace_role") or ""
    workspace_role = (
        normalize_workspace_role(raw_workspace)
        or normalize_workspace_role(raw_role)
        or WORKSPACE_ROLE_MEMBER
    )
    return TokenData(
        user_id=payload["sub"],
        email=payload["email"],
        organization_id=payload["org"],
        role=raw_role or workspace_role,
        workspace_role=workspace_role,
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

    Module 6 offboarding: when the resolved user row has
    ``is_active = 0``, raise 403 ``user_deactivated``. Spec §228 calls
    for "removes access within 30 seconds across all surfaces" — since
    every authenticated request flows through this reconciliation,
    the next request after deactivation hits this gate and fails.
    Existing tokens cannot keep granting access. Lookup failure
    (DB hiccup) falls through to the original token data so a
    transient outage doesn't lock everyone out; auth still proves
    cryptographic possession of a valid token.
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

        # Module 6 §228: deactivated users cannot authenticate.
        # The auth path already filters api_keys on is_active; this
        # closes the same loop for JWT-bearer + cookie + Google-OAuth
        # auth surfaces.
        if row.get("is_active") in (0, False):
            raise HTTPException(
                status_code=403,
                detail="user_deactivated",
            )

        # Prefer the DB's new workspace_role column; fall back to
        # legacy role if workspace_role hasn't been backfilled yet
        # (v89 backfill runs as part of run_migrations on boot).
        raw_workspace = row.get("workspace_role") or ""
        raw_role = (
            row.get("role")
            or getattr(token_data, "role", None)
            or ""
        )
        workspace_role = (
            normalize_workspace_role(raw_workspace)
            or normalize_workspace_role(raw_role)
            or WORKSPACE_ROLE_MEMBER
        )
        return TokenData(
            user_id=str(row.get("id") or user_id or email or "unknown"),
            email=str(row.get("email") or email or getattr(token_data, "email", "") or ""),
            # M20 tenant-rename: ``_unprovisioned`` is the sentinel
            # for users without a bound org (post-OAuth, awaiting ops
            # provisioning). ``require_org`` rejects it with 403
            # ``organization_pending_provisioning``.
            organization_id=str(
                row.get("organization_id")
                or getattr(token_data, "organization_id", None)
                or "_unprovisioned"
            ),
            role=raw_role or workspace_role,
            workspace_role=workspace_role,
            exp=token_data.exp,
        )
    except HTTPException:
        # 403 user_deactivated must propagate; only DB-layer
        # exceptions fall back to the token data.
        raise
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
    1. Bearer token: Solden JWT OR Google OAuth access token (Streak-style)
    2. API key: X-API-Key header
    3. Session cookie
    """
    if credentials and credentials.credentials:
        token = credentials.credentials
        # Try Solden JWT first
        try:
            payload = decode_token(token)
            if payload.get("type") == "access":
                return _assert_org_not_deleted(
                    _reconcile_token_data(_token_data_from_payload(payload))
                )
        except HTTPException:
            pass  # Not a Solden JWT — try Google OAuth below

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
                role=WORKSPACE_ROLE_API,
                workspace_role=WORKSPACE_ROLE_API,
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


def _looks_like_google_access_token(token: str) -> bool:
    """Cheap shape check that avoids the network round-trip to Google's
    tokeninfo endpoint for tokens that obviously aren't Google access
    tokens — JWTs, API keys, garbage, expired bearers, etc.

    Google access tokens currently start with ``ya29.``; refresh tokens
    are ``1//``-prefixed (we never receive those here). JWTs always
    start with ``eyJ`` (the base64-encoded ``{"`` header opener).
    Anything else: not a Google token, don't burn 1.7s on a tokeninfo
    call we know will return 400.
    """
    if not token:
        return False
    # Reject anything that decodes as a JWT — those went through
    # ``decode_token`` already and were rejected.
    if token.startswith("eyJ"):
        return False
    # Conservative allowlist: today Google access tokens we accept all
    # start with ya29. If Google ever issues a different prefix the
    # behaviour will be a 401 (same as before), and we can extend this
    # check.
    return token.startswith("ya29.")


def _validate_google_token(token: str) -> Optional[TokenData]:
    """Validate a Google OAuth access token and resolve to a Solden user.

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

    # Bail out before the network round-trip if the token doesn't even
    # look like a Google access token. Saves ~1.7s per failed bearer
    # against ``tokeninfo`` (timeout was 10s with no body content type
    # check, so a misshapen token took the full TLS+roundtrip cost).
    if not _looks_like_google_access_token(token):
        return None

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
            # Auto-provision: resolve org from email domain (Streak pattern).
            # M20 tenant-rename: when no domain match, bind to the
            # ``_unprovisioned`` sentinel so the next ``require_org``
            # call returns 403 ``organization_pending_provisioning``.
            # Frontend routes the user to "your organization isn't
            # set up yet"; ops manually attaches them to a real org.
            email_domain = email.split("@")[1].lower() if "@" in email else ""
            domain_org = db.get_organization_by_domain(email_domain) if email_domain else None
            provision_org = str((domain_org or {}).get("id") or "_unprovisioned")
            if not domain_org:
                logger.warning(
                    "No org found for domain %s — user pending manual provisioning",
                    email_domain,
                )
            user = db.create_user(
                email=email,
                name=email.split("@")[0],
                organization_id=provision_org,
                role="operator",
            )
            logger.info("Auto-provisioned user from Google OAuth: %s org=%s", email, provision_org)

        user_id = user.get("id") or user.get("user_id") or email
        raw_workspace = user.get("workspace_role") or ""
        raw_role = user.get("role") or ""
        workspace_role = (
            normalize_workspace_role(raw_workspace)
            or normalize_workspace_role(raw_role)
            or WORKSPACE_ROLE_MEMBER
        )
        token_data = TokenData(
            user_id=user_id,
            email=email,
            # M20: same sentinel as the auto-provision path above.
            organization_id=user.get("organization_id", "_unprovisioned"),
            role=raw_role or workspace_role,
            workspace_role=workspace_role,
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
# Two-axis auth model (migration v89, 2026-05-19)
# ---------------------------------------------------------------------------
#
# The pre-v89 single-axis enum (ap_clerk → ap_manager → financial_controller
# → cfo → owner) conflated two distinct concerns in one column:
#
#   1. Org governance — who can manage users / connections / settings /
#      plan / API keys. Independent of which Box types are registered.
#   2. AP workflow rank — who can approve invoices / post to ERP /
#      override validation. Specific to the ap_item Box type.
#
# Migration v89 split that into:
#
#   * ``users.workspace_role`` — org-governance axis. Values:
#     {owner, admin, member, read_only, api}. Pinned by a DB CHECK
#     constraint (M21 doctrine — catch stale writes at the DB).
#
#   * ``user_box_roles`` — per-Box-type domain rank, looked up from DB
#     via ``get_user_box_role(user_id, org_id, box_type)``. For
#     box_type='ap_item' the values are {viewer, clerk, approver,
#     controller}. When a second Box type ships it declares its role
#     enum at the box-registry layer and adds rows here; no other
#     auth code needs to change.
#
# When the 2nd Box ships (procurement / audit / etc.), it just registers
# its role enum + permissions and the AP-side helpers below get a
# sibling module (e.g. ``procurement_roles.py``). The workspace_role
# axis is stable forever.

# ─── Workspace role enum (org governance axis) ────────────────────────

WORKSPACE_ROLE_READ_ONLY = "read_only"
WORKSPACE_ROLE_MEMBER = "member"
WORKSPACE_ROLE_ADMIN = "admin"
WORKSPACE_ROLE_OWNER = "owner"
WORKSPACE_ROLE_API = "api"

WORKSPACE_ROLES: frozenset = frozenset({
    WORKSPACE_ROLE_READ_ONLY,
    WORKSPACE_ROLE_MEMBER,
    WORKSPACE_ROLE_ADMIN,
    WORKSPACE_ROLE_OWNER,
    WORKSPACE_ROLE_API,
})

# Rank map for workspace roles. Higher = more org-governance authority.
# api shares the top rank with owner because service accounts route
# through the same permission gates as the org creator.
WORKSPACE_ROLE_RANK: Dict[str, int] = {
    WORKSPACE_ROLE_READ_ONLY: 10,
    WORKSPACE_ROLE_MEMBER:    30,
    WORKSPACE_ROLE_ADMIN:     70,
    WORKSPACE_ROLE_OWNER:     100,
    WORKSPACE_ROLE_API:       100,
}

# Map any legacy single-axis value (pre-v89) or stale JWT claim onto a
# canonical workspace_role. Applied at every read boundary by
# ``normalize_workspace_role`` so an in-flight JWT minted before the
# migration doesn't 500 the auth pipeline.
_LEGACY_TO_WORKSPACE: Dict[str, str] = {
    # Direct survivors
    "owner":                WORKSPACE_ROLE_OWNER,
    "api":                  WORKSPACE_ROLE_API,
    "admin":                WORKSPACE_ROLE_ADMIN,
    "member":               WORKSPACE_ROLE_MEMBER,
    "read_only":            WORKSPACE_ROLE_READ_ONLY,
    # Legacy AP-flavoured names — drop the AP-rank meaning; keep only
    # the workspace-governance signal.
    "cfo":                  WORKSPACE_ROLE_ADMIN,
    "financial_controller": WORKSPACE_ROLE_ADMIN,
    "ap_manager":           WORKSPACE_ROLE_MEMBER,
    "ap_clerk":             WORKSPACE_ROLE_MEMBER,
    "operator":             WORKSPACE_ROLE_MEMBER,
    "user":                 WORKSPACE_ROLE_MEMBER,
    "viewer":               WORKSPACE_ROLE_READ_ONLY,
}


def normalize_workspace_role(role: Optional[str]) -> str:
    """Return the canonical ``workspace_role`` for a potentially-legacy input.

    Empty, None, or fully-unknown inputs return an empty string — every
    ``has_workspace_*`` predicate rejects an empty role, so an unknown
    token fails closed rather than escalating to a default seat.
    """
    raw = str(role or "").strip().lower()
    if not raw:
        return ""
    if raw in WORKSPACE_ROLES:
        return raw
    return _LEGACY_TO_WORKSPACE.get(raw, "")


def has_workspace_role(role: Optional[str], minimum: str) -> bool:
    """Return True iff ``role`` has at least the rank of ``minimum`` on the
    workspace-governance axis.
    """
    rn = normalize_workspace_role(role)
    mn = normalize_workspace_role(minimum)
    return WORKSPACE_ROLE_RANK.get(rn, 0) >= WORKSPACE_ROLE_RANK.get(mn, 0)


def has_workspace_read_only(role: Optional[str]) -> bool:
    return has_workspace_role(role, WORKSPACE_ROLE_READ_ONLY)


def has_workspace_member(role: Optional[str]) -> bool:
    return has_workspace_role(role, WORKSPACE_ROLE_MEMBER)


def has_workspace_admin(role: Optional[str]) -> bool:
    """Manages users, connections, plan, API keys, settings."""
    return has_workspace_role(role, WORKSPACE_ROLE_ADMIN)


def has_workspace_owner(role: Optional[str]) -> bool:
    return has_workspace_role(role, WORKSPACE_ROLE_OWNER)


# ─── AP Box-type role enum (workflow domain axis) ────────────────────

AP_ROLE_VIEWER = "viewer"
AP_ROLE_CLERK = "clerk"
AP_ROLE_APPROVER = "approver"
AP_ROLE_CONTROLLER = "controller"

AP_ROLES: frozenset = frozenset({
    AP_ROLE_VIEWER, AP_ROLE_CLERK, AP_ROLE_APPROVER, AP_ROLE_CONTROLLER,
})

AP_ROLE_RANK: Dict[str, int] = {
    AP_ROLE_VIEWER:     10,
    AP_ROLE_CLERK:      30,
    AP_ROLE_APPROVER:   50,
    AP_ROLE_CONTROLLER: 90,
}

# Legacy single-axis AP names that pre-v89 code may still emit.
_LEGACY_TO_AP_ROLE: Dict[str, str] = {
    "ap_clerk":             AP_ROLE_CLERK,
    "ap_manager":           AP_ROLE_APPROVER,
    "financial_controller": AP_ROLE_CONTROLLER,
    "cfo":                  AP_ROLE_CONTROLLER,
    "owner":                AP_ROLE_CONTROLLER,
    "read_only":            AP_ROLE_VIEWER,
    "viewer":               AP_ROLE_VIEWER,
    "operator":             AP_ROLE_APPROVER,
    "user":                 AP_ROLE_CLERK,
    "member":               AP_ROLE_CLERK,
    "admin":                AP_ROLE_CONTROLLER,
}


def normalize_ap_role(role: Optional[str]) -> str:
    raw = str(role or "").strip().lower()
    if not raw:
        return ""
    if raw in AP_ROLES:
        return raw
    return _LEGACY_TO_AP_ROLE.get(raw, "")


def has_ap_role(role: Optional[str], minimum: str) -> bool:
    rn = normalize_ap_role(role)
    mn = normalize_ap_role(minimum)
    return AP_ROLE_RANK.get(rn, 0) >= AP_ROLE_RANK.get(mn, 0)


def has_ap_viewer(role: Optional[str]) -> bool:
    return has_ap_role(role, AP_ROLE_VIEWER)


def has_ap_clerk(role: Optional[str]) -> bool:
    """Clerk or above on the AP axis (clerk / approver / controller)."""
    return has_ap_role(role, AP_ROLE_CLERK)


def has_ap_approver(role: Optional[str]) -> bool:
    return has_ap_role(role, AP_ROLE_APPROVER)


def has_ap_controller(role: Optional[str]) -> bool:
    return has_ap_role(role, AP_ROLE_CONTROLLER)


# ─── DB-backed per-Box lookup ─────────────────────────────────────────

def get_user_ap_role(
    user_id: Optional[str],
    organization_id: Optional[str],
    db: Any = None,
) -> str:
    """Return the user's AP role from ``user_box_roles``, or '' if none.

    Helpers and FastAPI deps below use this to evaluate AP-specific
    permission gates. The lookup is read-light (one indexed point read
    on ``user_box_roles``); call sites that already hold a db handle
    can pass it through to avoid re-acquiring the singleton.
    """
    if not user_id or not organization_id:
        return ""
    if db is None:
        try:
            db = _get_db()
        except Exception:
            return ""
    try:
        row = db.get_user_box_role(user_id, organization_id, "ap_item")
    except Exception:
        return ""
    if not row:
        return ""
    return normalize_ap_role(row.get("role"))


def get_user_box_roles(
    user_id: Optional[str],
    organization_id: Optional[str],
    db: Any = None,
) -> Dict[str, str]:
    """Return the full ``{box_type: role}`` map for a user in an org."""
    if not user_id or not organization_id:
        return {}
    if db is None:
        try:
            db = _get_db()
        except Exception:
            return {}
    try:
        rows = db.list_user_box_roles(user_id, organization_id) or []
    except Exception:
        return {}
    out: Dict[str, str] = {}
    for row in rows:
        box_type = str(row.get("box_type") or "").strip()
        role = str(row.get("role") or "").strip().lower()
        if box_type and role:
            out[box_type] = role
    return out


# ─── FastAPI dependencies — workspace axis ───────────────────────────

def require_workspace_member(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Any authenticated org member with at least workspace_role=member."""
    if not has_workspace_member(_workspace_role_of(user)):
        raise HTTPException(
            status_code=403, detail="workspace_member_role_required",
        )
    return user


def require_workspace_admin(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Manages users / connections / plan / API keys / settings."""
    if not has_workspace_admin(_workspace_role_of(user)):
        raise HTTPException(
            status_code=403, detail="workspace_admin_role_required",
        )
    return user


def require_workspace_owner(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Org creator / billing controller only."""
    if not has_workspace_owner(_workspace_role_of(user)):
        raise HTTPException(
            status_code=403, detail="workspace_owner_role_required",
        )
    return user


# ─── FastAPI dependencies — AP box axis ──────────────────────────────

def _ap_role_for_user(user: Any) -> str:
    """Resolve the user's AP role from the DB (``user_box_roles``) with
    a derivation fallback for callers without a stored row.

    The DB is the source of truth — every user created post-v89 has a
    ``user_box_roles`` entry. The fallback handles three legitimate
    edge cases:

      1. Test fixtures that construct a TokenData directly without
         seeding ``user_box_roles``.
      2. In-flight requests during the v89 backfill window (every
         live user has been backfilled, but a brand-new user whose
         creation transaction hasn't committed yet may not yet).
      3. Service accounts (``workspace_role=api``) that intentionally
         carry no box-role row.

    The fallback derives the AP role from the user's stored
    single-axis ``role`` (legacy) or ``workspace_role`` (canonical)
    via the same ``_LEGACY_TO_AP_ROLE`` table that migration v89's
    backfill used. That keeps test fixtures + JWT-only auth paths
    working under the same contract as the DB-backed lookup.
    """
    ap_from_db = get_user_ap_role(
        getattr(user, "user_id", None),
        getattr(user, "organization_id", None),
    )
    if ap_from_db:
        return ap_from_db
    # Derive from the legacy single-axis role field on the TokenData /
    # User. ``role`` is preferred since it carries the pre-v89 AP
    # rank; fall back to ``workspace_role`` for callers that only
    # set the canonical axis.
    return (
        normalize_ap_role(getattr(user, "role", None))
        or normalize_ap_role(getattr(user, "workspace_role", None))
        or ""
    )


def require_ap_clerk(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """AP clerk or above (clerk / approver / controller)."""
    if not has_ap_clerk(_ap_role_for_user(user)):
        raise HTTPException(
            status_code=403, detail="ap_clerk_role_required",
        )
    return user


def require_ap_approver(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """AP approver or controller — can approve / reject / route."""
    if not has_ap_approver(_ap_role_for_user(user)):
        raise HTTPException(
            status_code=403, detail="ap_approver_role_required",
        )
    return user


def require_ap_controller(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """AP controller — override-post, reverse-post, mark-duplicate."""
    if not has_ap_controller(_ap_role_for_user(user)):
        raise HTTPException(
            status_code=403, detail="ap_controller_role_required",
        )
    return user


def _workspace_role_of(user: Any) -> Optional[str]:
    """Best-effort read of a user's workspace_role, with legacy fallback.

    Prefer the new ``workspace_role`` field; fall back to the legacy
    ``role`` field on stale TokenData objects so an in-flight JWT minted
    before the v89 cutover doesn't 500. The legacy value is normalized
    through ``normalize_workspace_role``.
    """
    explicit = getattr(user, "workspace_role", None)
    if explicit:
        return explicit
    legacy = getattr(user, "role", None)
    return normalize_workspace_role(legacy) if legacy else None


# ─── Compatibility shims (TEMPORARY — scheduled to remove with v90) ──
#
# A handful of older callers import ``normalize_user_role``,
# ``has_admin_access``, ``has_ops_access``, ``require_admin_user``,
# ``require_ops_user``, ``require_financial_controller``,
# ``require_cfo``, ``require_ap_manager``. They mean
# "workspace admin", "workspace member", and AP-axis approver/controller
# respectively under the new model. Aliases here keep the existing
# import paths working through the sweep; each alias gets removed when
# its last caller is migrated to the new helper name.


def normalize_user_role(role: Optional[str]) -> str:
    """DEPRECATED. Use ``normalize_workspace_role`` or
    ``normalize_ap_role`` depending on which axis you mean.
    Kept temporarily so legacy imports continue to resolve.
    """
    return normalize_workspace_role(role)


def has_at_least(role: Optional[str], minimum: str) -> bool:
    """DEPRECATED alias for ``has_workspace_role``. Pre-v89 callers
    treated rank as a single dimension; under the new model ``role``
    here refers to the workspace_role axis.
    """
    return has_workspace_role(role, minimum)


def has_admin_access(role: Optional[str]) -> bool:
    """DEPRECATED alias for ``has_workspace_admin``."""
    return has_workspace_admin(role)


def has_ops_access(role: Optional[str]) -> bool:
    """DEPRECATED. Historically gated AP Manager+ access. Use either
    ``has_workspace_member`` (workspace nav / view) or
    ``has_ap_approver`` (AP-specific write).
    """
    return has_workspace_member(role)


def has_financial_controller(role: Optional[str]) -> bool:
    """DEPRECATED alias for ``has_workspace_admin``."""
    return has_workspace_admin(role)


def has_cfo(role: Optional[str]) -> bool:
    """DEPRECATED. Gates fraud-control + autonomy parameters. Use
    ``has_workspace_admin`` (org governance) or
    ``has_ap_controller`` (AP-specific override authority).
    """
    return has_workspace_admin(role)


def has_owner(role: Optional[str]) -> bool:
    """DEPRECATED alias for ``has_workspace_owner``."""
    return has_workspace_owner(role)


def has_ap_manager(role: Optional[str]) -> bool:
    """DEPRECATED alias for ``has_ap_approver`` (rank renamed)."""
    return has_ap_approver(role)


def has_read_only(role: Optional[str]) -> bool:
    """DEPRECATED alias for ``has_workspace_read_only``."""
    return has_workspace_read_only(role)


def has_fraud_control_admin(role: Optional[str]) -> bool:
    """DEPRECATED. Same semantic as ``has_workspace_admin``."""
    return has_workspace_admin(role)


def require_ops_user(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """DEPRECATED. Pre-v89 callers gated AP-Manager-or-above access.
    Under the new model that's ``has_ap_approver`` on the AP-box axis.
    Error string preserved as ``ap_manager_role_required`` so existing
    integration tests + API consumers keep their detail key.
    """
    if not has_ap_approver(_ap_role_for_user(user)):
        raise HTTPException(
            status_code=403, detail="ap_manager_role_required",
        )
    return user


def require_admin_user(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """DEPRECATED. Workspace admin authority. Error string preserved
    as ``financial_controller_role_required`` for pre-v89 contract.
    """
    if not has_workspace_admin(_workspace_role_of(user)):
        raise HTTPException(
            status_code=403, detail="financial_controller_role_required",
        )
    return user


def require_cfo(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """DEPRECATED. Pre-v89 CFO-gated surfaces required CFO rank or
    above (CFO / owner / api). Under v89 that collapses to workspace
    admin — every CFO is an org admin. Error string preserved as
    ``cfo_role_required`` for fraud-control + IBAN-verify endpoint
    contracts.
    """
    if not has_workspace_admin(_workspace_role_of(user)):
        raise HTTPException(
            status_code=403, detail="cfo_role_required",
        )
    return user


def require_financial_controller(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """DEPRECATED. Workspace-admin gate with the legacy error string
    callers expect.
    """
    if not has_workspace_admin(_workspace_role_of(user)):
        raise HTTPException(
            status_code=403, detail="financial_controller_role_required",
        )
    return user


def require_ap_manager(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """DEPRECATED. AP-Manager-or-above semantic maps to AP approver
    rank under v89. Error string preserved.
    """
    if not has_ap_approver(_ap_role_for_user(user)):
        raise HTTPException(
            status_code=403, detail="ap_manager_role_required",
        )
    return user


# Legacy ROLE_* names — point at workspace-axis equivalents so the
# import sites resolve. Migration v89 has already rewritten the DB
# column values; these constants survive only so existing imports
# in tests + service modules still load. Every legacy import gets
# removed in the sweep below.
ROLE_READ_ONLY = WORKSPACE_ROLE_READ_ONLY
ROLE_AP_CLERK = WORKSPACE_ROLE_MEMBER  # was "ap_clerk"
ROLE_AP_MANAGER = WORKSPACE_ROLE_MEMBER  # was "ap_manager"
ROLE_FINANCIAL_CONTROLLER = WORKSPACE_ROLE_ADMIN  # was "financial_controller"
ROLE_CFO = WORKSPACE_ROLE_ADMIN  # was "cfo"
ROLE_OWNER = WORKSPACE_ROLE_OWNER
ROLE_API = WORKSPACE_ROLE_API

# Rank map — kept for the few stores that read it directly. New code
# should call has_workspace_role / has_ap_role instead.
ROLE_RANK: Dict[str, int] = WORKSPACE_ROLE_RANK


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

    raw_workspace = row.get("workspace_role") or ""
    raw_role = row.get("role") or ""
    workspace_role = (
        normalize_workspace_role(raw_workspace)
        or normalize_workspace_role(raw_role)
        or WORKSPACE_ROLE_MEMBER
    )
    return User(
        id=str(row.get("id")),
        email=str(row.get("email")),
        name=str(row.get("name") or ""),
        organization_id=str(row.get("organization_id")),
        role=str(raw_role or workspace_role),
        workspace_role=workspace_role,
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


def create_user_from_google(
    email: str,
    google_id: str,
    organization_id: str,
    *,
    name: Optional[str] = None,
) -> User:
    """
    Create or update a user from Google identity.

    First user in the org becomes the owner — they're the one claiming
    the organization through the onboarding flow and need to be able to
    configure the ERP, complete onboarding steps, invite teammates. Any
    subsequent Google sign-in uses the default ap_clerk seat; existing
    users keep their role on re-auth.

    The Microsoft entra-id callback also funnels through this helper
    (with a `ms:` google_id prefix) and supplies the Graph displayName
    via ``name``. Falls back to deriving from email when not provided.
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
        name=name or email.split("@")[0].replace(".", " ").title(),
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
