"""
Clearledgr Rate Limiting

Protect API from abuse with token bucket rate limiting.
"""

import time
from typing import Dict, Optional
from dataclasses import dataclass
from collections import defaultdict
from fastapi import HTTPException, Request, Depends
from functools import wraps


@dataclass
class RateLimitConfig:
    """Rate limit configuration."""
    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    burst_size: int = 10  # Allow short bursts


class TokenBucket:
    """Token bucket for rate limiting."""
    
    def __init__(self, rate: float, capacity: int):
        self.rate = rate  # tokens per second
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.time()
    
    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if allowed."""
        now = time.time()
        elapsed = now - self.last_update
        self.last_update = now
        
        # Add tokens based on elapsed time
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False
    
    @property
    def retry_after(self) -> float:
        """Seconds until a token is available."""
        if self.tokens >= 1:
            return 0
        return (1 - self.tokens) / self.rate


class RateLimiter:
    """
    Rate limiter with per-user and per-IP tracking.
    """
    
    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or RateLimitConfig()
        self._user_buckets: Dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(
                rate=self.config.requests_per_minute / 60,
                capacity=self.config.burst_size,
            )
        )
        self._ip_buckets: Dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(
                rate=self.config.requests_per_minute / 60 * 2,  # More lenient for IPs
                capacity=self.config.burst_size * 2,
            )
        )
    
    def check(self, user_id: Optional[str] = None, ip_address: Optional[str] = None) -> bool:
        """
        Check if request is allowed.
        
        Args:
            user_id: Authenticated user ID
            ip_address: Client IP address
        
        Returns:
            True if allowed, raises HTTPException if rate limited
        """
        # Check user bucket if authenticated
        if user_id:
            bucket = self._user_buckets[user_id]
            if not bucket.consume():
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded",
                    headers={"Retry-After": str(int(bucket.retry_after) + 1)},
                )
        
        # Check IP bucket
        if ip_address:
            bucket = self._ip_buckets[ip_address]
            if not bucket.consume():
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded",
                    headers={"Retry-After": str(int(bucket.retry_after) + 1)},
                )
        
        return True
    
    def get_remaining(self, user_id: Optional[str] = None, ip_address: Optional[str] = None) -> int:
        """Get remaining requests."""
        if user_id and user_id in self._user_buckets:
            return int(self._user_buckets[user_id].tokens)
        if ip_address and ip_address in self._ip_buckets:
            return int(self._ip_buckets[ip_address].tokens)
        return self.config.burst_size


# Global rate limiter
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    # Check for forwarded headers (behind proxy)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    return request.client.host if request.client else "unknown"


async def rate_limit_dependency(request: Request):
    """FastAPI dependency for rate limiting."""
    limiter = get_rate_limiter()
    ip = get_client_ip(request)
    
    # Get user ID if authenticated (from auth header)
    user_id = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            from clearledgr.core.auth import decode_token
            token = auth_header.split(" ")[1]
            payload = decode_token(token)
            user_id = payload.get("sub")
        except Exception:
            pass  # Not authenticated, use IP only
    
    limiter.check(user_id=user_id, ip_address=ip)
    
    return {"user_id": user_id, "ip": ip}


def rate_limit(requests_per_minute: int = 60):
    """
    Decorator for rate limiting specific endpoints.
    
    Usage:
        @router.get("/expensive")
        @rate_limit(requests_per_minute=10)
        async def expensive_operation():
            ...
    """
    def decorator(func):
        limiter = RateLimiter(RateLimitConfig(requests_per_minute=requests_per_minute))
        
        @wraps(func)
        async def wrapper(*args, request: Request = None, **kwargs):
            if request:
                ip = get_client_ip(request)
                limiter.check(ip_address=ip)
            return await func(*args, request=request, **kwargs)
        
        return wrapper
    return decorator
