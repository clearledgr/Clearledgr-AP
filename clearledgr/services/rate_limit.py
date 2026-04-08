"""
Rate limiting for Clearledgr Reconciliation API.

Uses Redis when REDIS_URL is configured (production), falls back to
in-memory storage for development. Logs a warning on startup when
running in-memory mode so operators know rate limits are not shared
across workers/processes.
"""
import logging
import time
from collections import defaultdict
from typing import Dict, Tuple
from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
import os

logger = logging.getLogger(__name__)
_PRODUCTION_ENVS = {"production", "prod", "staging", "stage"}

# Rate limit configuration
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "300"))  # requests per window
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds

# Redis-backed store (preferred) or in-memory fallback
_redis_client = None
_rate_limit_store: Dict[str, Tuple[int, float]] = defaultdict(lambda: (0, time.time()))
_backend = "memory"


def _is_production_like_env() -> bool:
    return str(os.getenv("ENV", "dev")).strip().lower() in _PRODUCTION_ENVS


def _allow_memory_backend_in_production() -> bool:
    return str(
        os.getenv("AP_V1_ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION", "false")
    ).strip().lower() in {"1", "true", "yes", "on"}


def _init_redis():
    """Try to connect to Redis for rate limiting. Returns True on success."""
    global _redis_client, _backend
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        return False
    try:
        import redis
        _redis_client = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
        _redis_client.ping()
        _backend = "redis"
        logger.info("Rate limiter using Redis backend")
        return True
    except Exception as exc:
        logger.warning("Rate limiter Redis unavailable (%s) — falling back to in-memory (not shared across workers)", exc)
        _redis_client = None
        _backend = "memory"
        return False


def enforce_production_backend_requirements() -> None:
    """Fail startup when production/staging runs without Redis rate limiting."""
    if not RATE_LIMIT_ENABLED:
        return
    if not _is_production_like_env():
        return
    if _backend == "redis" and _redis_client is not None:
        return
    if _allow_memory_backend_in_production():
        logger.warning(
            "Rate limiter running in-memory in production-like ENV due to AP_V1_ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION=true"
        )
        return
    raise RuntimeError("redis_rate_limit_backend_required_in_production")


def get_rate_limit_backend_status() -> Dict[str, str]:
    return {
        "backend": _backend,
        "env": str(os.getenv("ENV", "dev")).strip().lower(),
        "rate_limit_enabled": "true" if RATE_LIMIT_ENABLED else "false",
    }


# Attempt Redis on module load and validate startup contract.
_init_redis()
if _backend == "memory":
    logger.warning("Rate limiter running in-memory — limits are per-process and not shared across workers")
enforce_production_backend_requirements()


def get_client_identifier(request: Request) -> str:
    """Get client identifier for rate limiting."""
    # Try to get API key first
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"api_key:{api_key}"

    # Fall back to IP address
    client_ip = request.client.host if request.client else "unknown"
    return f"ip:{client_ip}"


def _check_rate_limit_redis(client_id: str) -> Tuple[bool, int, int]:
    """Redis-backed sliding window rate check."""
    key = f"rl:{client_id}"
    try:
        pipe = _redis_client.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()
        if ttl == -1:
            _redis_client.expire(key, RATE_LIMIT_WINDOW)
            ttl = RATE_LIMIT_WINDOW
        reset_after = max(ttl, 1)
        if count > RATE_LIMIT_REQUESTS:
            return False, 0, reset_after
        return True, RATE_LIMIT_REQUESTS - count, reset_after
    except Exception as exc:
        if _is_production_like_env() and not _allow_memory_backend_in_production():
            logger.error("Redis rate limit error in production-like env: %s — denying request", exc)
            return False, 0, RATE_LIMIT_WINDOW
        logger.error("Redis rate limit error: %s — allowing request", exc)
        return True, RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW


def _check_rate_limit_memory(client_id: str) -> Tuple[bool, int, int]:
    """In-memory rate check (per-process only)."""
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


def check_rate_limit(client_id: str) -> Tuple[bool, int, int]:
    """
    Check if client has exceeded rate limit.

    Returns:
        Tuple of (allowed, remaining_requests, reset_after_seconds)
    """
    if not RATE_LIMIT_ENABLED:
        return True, RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW

    if _redis_client is not None:
        return _check_rate_limit_redis(client_id)
    if _is_production_like_env() and not _allow_memory_backend_in_production():
        # Fail closed if distributed limiter is unavailable in production-like envs.
        return False, 0, RATE_LIMIT_WINDOW
    return _check_rate_limit_memory(client_id)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce rate limiting."""

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health check
        if request.url.path == "/health" or request.url.path == "/docs" or request.url.path == "/openapi.json":
            return await call_next(request)

        client_id = get_client_identifier(request)
        allowed, remaining, reset_after = check_rate_limit(client_id)

        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": f"Rate limit exceeded. Try again in {reset_after} seconds."},
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
