"""Workspace Concurrency Semaphore — Agent Design Specification §11.2.2.

Per-workspace concurrency limits prevent a single high-volume workspace
from consuming the entire worker fleet. Limits are enforced at the
semaphore level — a workspace that hits its limit queues additional
events rather than blocking workers.

Tier limits:
  Starter:      5 concurrent boxes
  Professional: 15 concurrent boxes
  Enterprise:   50 concurrent boxes

Implemented as a Redis-based counting semaphore with TTL safety.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# §11.2.2: Per-tier concurrency limits
TIER_LIMITS = {
    "starter": 5,
    "professional": 15,
    "enterprise": 50,
}
DEFAULT_LIMIT = 5
SEMAPHORE_TTL_SECONDS = 900  # 15-min safety TTL


class WorkspaceSemaphore:
    """Redis-based counting semaphore per workspace.

    Before processing any event, a worker acquires a slot. If the
    workspace is at its concurrency limit, the worker should nack
    the event and requeue it with backoff.
    """

    def __init__(
        self,
        organization_id: str,
        redis_client: Any = None,
        tier: Optional[str] = None,
    ):
        self.organization_id = organization_id
        self.key = f"clearledgr:semaphore:{organization_id}"
        self._redis = redis_client
        self.limit = TIER_LIMITS.get(tier or "", DEFAULT_LIMIT)

    def _get_redis(self) -> Any:
        if self._redis is not None:
            return self._redis
        try:
            import redis
            url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            self._redis = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
            return self._redis
        except Exception:
            return None

    def acquire(self) -> bool:
        """Try to acquire a concurrency slot.

        Returns True if a slot was acquired, False if at limit.
        Uses INCR + GET atomically to prevent race conditions.
        """
        r = self._get_redis()
        if r is None:
            return True  # No Redis = no limiting (dev mode)

        try:
            pipe = r.pipeline(True)
            pipe.incr(self.key)
            pipe.expire(self.key, SEMAPHORE_TTL_SECONDS)
            results = pipe.execute()
            current = results[0]

            if current > self.limit:
                # Over limit — release the slot we just took
                r.decr(self.key)
                logger.debug(
                    "[Semaphore] %s at limit (%d/%d), rejecting",
                    self.organization_id, current - 1, self.limit,
                )
                return False

            logger.debug(
                "[Semaphore] %s acquired slot (%d/%d)",
                self.organization_id, current, self.limit,
            )
            return True

        except Exception as exc:
            logger.warning("[Semaphore] Redis error, allowing through: %s", exc)
            return True  # Fail open — don't block processing on Redis failure

    def release(self) -> None:
        """Release a concurrency slot."""
        r = self._get_redis()
        if r is None:
            return

        try:
            current = r.decr(self.key)
            # Guard against going negative (e.g., after Redis restart)
            if current < 0:
                r.set(self.key, 0, ex=SEMAPHORE_TTL_SECONDS)
        except Exception as exc:
            logger.debug("[Semaphore] Release failed: %s", exc)

    def current_count(self) -> int:
        """Get current active slot count (for monitoring)."""
        r = self._get_redis()
        if r is None:
            return 0
        try:
            return int(r.get(self.key) or 0)
        except Exception:
            return 0
