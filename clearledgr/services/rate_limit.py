"""
Rate limiting for Clearledgr Reconciliation API.
"""
import time
from collections import defaultdict
from typing import Dict, Tuple
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
import os

# Rate limit configuration
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))  # requests per window
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds

# In-memory store for rate limiting (use Redis in production)
_rate_limit_store: Dict[str, Tuple[int, float]] = defaultdict(lambda: (0, time.time()))


def get_client_identifier(request: Request) -> str:
    """Get client identifier for rate limiting."""
    # Try to get API key first
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"api_key:{api_key}"
    
    # Fall back to IP address
    client_ip = request.client.host if request.client else "unknown"
    return f"ip:{client_ip}"


def check_rate_limit(client_id: str) -> Tuple[bool, int, int]:
    """
    Check if client has exceeded rate limit.
    
    Returns:
        Tuple of (allowed, remaining_requests, reset_after_seconds)
    """
    if not RATE_LIMIT_ENABLED:
        return True, RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW
    
    current_time = time.time()
    request_count, window_start = _rate_limit_store[client_id]
    
    # Reset window if it has expired
    if current_time - window_start >= RATE_LIMIT_WINDOW:
        _rate_limit_store[client_id] = (1, current_time)
        return True, RATE_LIMIT_REQUESTS - 1, RATE_LIMIT_WINDOW
    
    # Check if limit exceeded
    if request_count >= RATE_LIMIT_REQUESTS:
        reset_after = int(RATE_LIMIT_WINDOW - (current_time - window_start))
        return False, 0, reset_after
    
    # Increment counter
    _rate_limit_store[client_id] = (request_count + 1, window_start)
    remaining = RATE_LIMIT_REQUESTS - (request_count + 1)
    reset_after = int(RATE_LIMIT_WINDOW - (current_time - window_start))
    
    return True, remaining, reset_after


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce rate limiting."""
    
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health check
        if request.url.path == "/health" or request.url.path == "/docs" or request.url.path == "/openapi.json":
            return await call_next(request)
        
        client_id = get_client_identifier(request)
        allowed, remaining, reset_after = check_rate_limit(client_id)
        
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {reset_after} seconds.",
                headers={
                    "X-RateLimit-Limit": str(RATE_LIMIT_REQUESTS),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + reset_after),
                    "Retry-After": str(reset_after),
                },
            )
        
        response = await call_next(request)
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_REQUESTS)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(time.time()) + reset_after)
        
        return response

