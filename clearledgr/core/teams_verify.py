"""Microsoft Teams Bot Framework JWT verification (PLAN.md Section 5.3).

Validates inbound HTTP requests from Microsoft Teams by checking the
JWT in the Authorization header against Microsoft's published JWKS.
Keys are cached for 24 hours to avoid repeated metadata fetches.
"""

import logging
import os
import time
from typing import Any, Dict, Optional

import httpx
import jwt
from jwt import PyJWKClient
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Microsoft Bot Framework OpenID metadata endpoint
_OPENID_METADATA_URL = (
    "https://login.botframework.com/v1/.well-known/openidconfiguration"
)

# Expected issuer for Bot Framework tokens
_EXPECTED_ISSUER = "https://api.botframework.com"

# Cache TTL for JWKS keys (24 hours)
_JWKS_CACHE_TTL_SECONDS = 86_400

# Maximum age of an inbound Teams request token (matches Slack's 5-minute window).
# The `iat` claim in the Bot Framework JWT must be within this window of the
# current server time to prevent replay attacks.
_MAX_TOKEN_AGE_SECONDS = 300

# Module-level JWKS cache: {"jwks_client": PyJWKClient, "fetched_at": float, "jwks_uri": str}
_jwks_cache: Dict[str, Any] = {}


def _get_jwks_client() -> PyJWKClient:
    """Return a cached PyJWKClient, refreshing if the cache has expired.

    The first call fetches the OpenID metadata synchronously (via httpx)
    to discover the ``jwks_uri``, then builds a ``PyJWKClient`` that
    PyJWT uses to look up signing keys by ``kid``.
    """
    now = time.time()
    cached_at = _jwks_cache.get("fetched_at", 0.0)

    if _jwks_cache.get("jwks_client") and (now - cached_at) < _JWKS_CACHE_TTL_SECONDS:
        return _jwks_cache["jwks_client"]

    # Synchronous fetch — acceptable at startup / once per 24h
    with httpx.Client() as client:
        resp = client.get(_OPENID_METADATA_URL, timeout=10)
        resp.raise_for_status()
        config = resp.json()

    jwks_uri = config.get("jwks_uri")
    if not jwks_uri:
        raise RuntimeError("Microsoft OpenID config missing 'jwks_uri'")

    jwks_client = PyJWKClient(jwks_uri, cache_keys=True)

    _jwks_cache["jwks_client"] = jwks_client
    _jwks_cache["jwks_uri"] = jwks_uri
    _jwks_cache["fetched_at"] = now

    logger.info("Refreshed Microsoft Bot Framework JWKS from %s", jwks_uri)
    return jwks_client


def verify_teams_token(auth_header: str) -> Dict[str, Any]:
    """Validate a Microsoft Teams Bot Framework JWT.

    Args:
        auth_header: The full ``Authorization`` header value
                     (e.g. ``"Bearer eyJ..."``).

    Returns:
        The decoded JWT claims dict on success.

    Raises:
        HTTPException: On any validation failure (401).
    """
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty bearer token")

    app_id = os.getenv("TEAMS_APP_ID", "").strip()
    if not app_id:
        logger.error("TEAMS_APP_ID not configured — cannot verify Teams token")
        raise HTTPException(status_code=503, detail="Teams integration not configured")

    try:
        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=_EXPECTED_ISSUER,
            audience=app_id,
            options={
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )

        # Replay-attack protection: reject tokens whose `iat` (issued-at) is
        # older than _MAX_TOKEN_AGE_SECONDS, matching Slack's 5-minute window.
        iat = claims.get("iat")
        if iat is not None:
            token_age = time.time() - float(iat)
            if token_age > _MAX_TOKEN_AGE_SECONDS:
                logger.warning(
                    "Teams token rejected: iat is %ds old (max %ds)",
                    int(token_age),
                    _MAX_TOKEN_AGE_SECONDS,
                )
                raise HTTPException(
                    status_code=401,
                    detail="Teams token too old — possible replay attack",
                )

        return claims

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Teams token has expired")
    except jwt.InvalidIssuerError:
        logger.warning("Teams token issuer mismatch")
        raise HTTPException(status_code=401, detail="Invalid token issuer")
    except jwt.InvalidAudienceError:
        logger.warning("Teams token audience mismatch")
        raise HTTPException(status_code=401, detail="Invalid token audience")
    except jwt.InvalidTokenError as exc:
        logger.warning("Teams JWT validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid Teams token")
    except httpx.HTTPError as exc:
        logger.error("Failed to fetch Microsoft JWKS: %s", exc)
        raise HTTPException(status_code=502, detail="Cannot reach Microsoft identity provider")
    except Exception as exc:
        from clearledgr.core.errors import safe_error

        raise HTTPException(
            status_code=500,
            detail=safe_error(exc, "Teams token verification"),
        )


async def require_teams_auth(request: Request) -> Dict[str, Any]:
    """FastAPI dependency that verifies Microsoft Teams Bot Framework JWTs.

    Usage::

        @router.post("/teams/webhook")
        async def teams_webhook(
            claims: dict = Depends(require_teams_auth),
        ):
            ...

    Returns:
        The decoded JWT claims dict.
    """
    auth_header: Optional[str] = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    return verify_teams_token(auth_header)
