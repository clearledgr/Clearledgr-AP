"""Per-ERP Rate Limiter — Agent Design Specification §11.1.

Token bucket rate limiter per workspace per ERP type. Prevents
hitting ERP API rate limits under concurrent load.

Limits are conservative — set below the actual API limits to leave
headroom for burst absorption:
  Xero:       50 RPM (actual: 60 RPM)
  QuickBooks: 40 RPM (actual: ~60 RPM)
  NetSuite:   30 RPM (per-token, conservative)
  SAP:        20 RPM (per-session, conservative)

Uses Redis sliding window (same pattern as rate_limit.py).
Falls back to in-memory per-process if Redis unavailable.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# §11.1: Per-ERP rate limits (requests per window_seconds)
ERP_RATE_LIMITS: Dict[str, Dict[str, int]] = {
    "quickbooks": {"requests": 40, "window": 60},
    "xero":       {"requests": 50, "window": 60},
    "netsuite":   {"requests": 30, "window": 60},
    "sap":        {"requests": 20, "window": 60},
}

DEFAULT_LIMIT = {"requests": 30, "window": 60}


class ERPRateLimitError(Exception):
    """Raised when an ERP API call would exceed the rate limit.

    The execution engine treats this as a transient failure (§5.2)
    and retries with exponential backoff.
    """

    def __init__(self, erp_type: str, org_id: str, retry_after: int = 5):
        self.erp_type = erp_type
        self.org_id = org_id
        self.retry_after = retry_after
        super().__init__(
            f"ERP rate limit exceeded for {erp_type} (org={org_id}). "
            f"Retry after {retry_after}s."
        )


class ERPRateLimiter:
    """Per-workspace, per-ERP rate limiter using Redis sliding window."""

    def __init__(self, redis_client: Any = None):
        self._redis = redis_client
        self._memory_counters: Dict[str, list] = {}  # fallback

    def _get_redis(self) -> Any:
        if self._redis is not None:
            return self._redis
        try:
            import redis
            url = os.environ.get("REDIS_URL", "").strip()
            if not url:
                return None
            self._redis = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
            self._redis.ping()
            return self._redis
        except Exception:
            return None

    def check_and_consume(self, org_id: str, erp_type: str) -> bool:
        """Check if a request is allowed and consume a token.

        Returns True if allowed, raises ERPRateLimitError if limited.
        """
        limits = ERP_RATE_LIMITS.get(erp_type, DEFAULT_LIMIT)
        max_requests = limits["requests"]
        window = limits["window"]

        r = self._get_redis()
        if r is not None:
            return self._check_redis(r, org_id, erp_type, max_requests, window)
        return self._check_memory(org_id, erp_type, max_requests, window)

    def _check_redis(self, r: Any, org_id: str, erp_type: str, max_req: int, window: int) -> bool:
        key = f"clearledgr:erp_rate:{org_id}:{erp_type}"
        now = time.time()
        window_start = now - window

        try:
            pipe = r.pipeline(True)
            # Remove old entries outside the window
            pipe.zremrangebyscore(key, "-inf", window_start)
            # Count current entries
            pipe.zcard(key)
            # Add this request
            pipe.zadd(key, {f"{now}:{id(self)}": now})
            # Set TTL
            pipe.expire(key, window + 10)
            results = pipe.execute()

            current_count = results[1]
            if current_count >= max_req:
                # Over limit — remove the entry we just added
                r.zrem(key, f"{now}:{id(self)}")
                retry_after = max(1, int(window - (now - window_start)))
                raise ERPRateLimitError(erp_type, org_id, retry_after)

            return True
        except ERPRateLimitError:
            raise
        except Exception as exc:
            logger.debug("[ERPRateLimiter] Redis check failed, allowing through: %s", exc)
            return True

    def _check_memory(self, org_id: str, erp_type: str, max_req: int, window: int) -> bool:
        key = f"{org_id}:{erp_type}"
        now = time.time()
        window_start = now - window

        if key not in self._memory_counters:
            self._memory_counters[key] = []

        # Remove old entries
        self._memory_counters[key] = [
            t for t in self._memory_counters[key] if t > window_start
        ]

        if len(self._memory_counters[key]) >= max_req:
            raise ERPRateLimitError(erp_type, org_id, 5)

        self._memory_counters[key].append(now)
        return True

    def get_usage(self, org_id: str, erp_type: str) -> Dict[str, Any]:
        """Get current usage for monitoring."""
        limits = ERP_RATE_LIMITS.get(erp_type, DEFAULT_LIMIT)
        r = self._get_redis()
        if r:
            key = f"clearledgr:erp_rate:{org_id}:{erp_type}"
            try:
                now = time.time()
                r.zremrangebyscore(key, "-inf", now - limits["window"])
                count = r.zcard(key)
                return {
                    "current": count,
                    "limit": limits["requests"],
                    "window_seconds": limits["window"],
                    "utilization_pct": round(count / limits["requests"] * 100, 1),
                }
            except Exception:
                pass
        return {"current": 0, "limit": limits["requests"], "window_seconds": limits["window"]}


# Singleton
_limiter: Optional[ERPRateLimiter] = None


def get_erp_rate_limiter() -> ERPRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = ERPRateLimiter()
    return _limiter
