"""
Authentication and Authorization for Clearledgr Reconciliation API.
"""
import os
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader
from typing import Optional

# API Key header
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# Get API key from environment
API_KEY = os.getenv("API_KEY", None)


def _is_dev_mode() -> bool:
    return os.getenv("ENV", "dev").lower() in ("dev", "development", "test")


def verify_api_key(api_key: Optional[str] = Security(API_KEY_HEADER)) -> str:
    """
    Verify API key from request header.

    In dev mode (ENV not set or set to dev/development/test), requests
    are allowed without an API key. In production, an API key is always required.
    """
    # Dev-mode bypass only when ENV is explicitly dev
    if API_KEY is None:
        if _is_dev_mode():
            return api_key or "dev-mode"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required. Set API_KEY env var or provide X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # If API key is configured, require it
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required. Provide X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return api_key


def get_api_key_optional(api_key: Optional[str] = Security(API_KEY_HEADER)) -> Optional[str]:
    """
    Get API key if provided, but don't require it.
    Useful for endpoints that work with or without authentication.
    """
    if API_KEY is None:
        if _is_dev_mode():
            return api_key or "dev-mode"
        return None

    if api_key and api_key == API_KEY:
        return api_key

    return None

