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


def verify_api_key(api_key: Optional[str] = Security(API_KEY_HEADER)) -> str:
    """
    Verify API key from request header.
    
    Args:
        api_key: API key from X-API-Key header
    
    Returns:
        API key if valid
    
    Raises:
        HTTPException: If API key is missing or invalid
    """
    # If no API key is configured, allow all requests (development mode)
    if API_KEY is None:
        return api_key or "dev-mode"
    
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
        return api_key or "dev-mode"
    
    if api_key and api_key == API_KEY:
        return api_key
    
    return None

