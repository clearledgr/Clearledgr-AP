"""Rate limiting for the public ``/v1`` surface.

Two counters per request, both sliding 60-second windows:

* **per-key** — 100 req/min. Stops one runaway agent from monopolising
  a tenant's quota.
* **per-org** — 1000 req/min. Caps the blast radius when an org has
  many keys (CS bot + AP bot + finance-ops bot all on the same tenant).

A breach raises :class:`RateLimitExceeded`. The global handler in
``main.py`` converts that to a 429 with ``Retry-After`` and writes a
single ``rate_limit_exceeded`` row to ``audit_events`` so we can answer
"why did this agent stop working?" hours after the fact.

Backend: Redis when ``REDIS_URL`` is configured (shared across workers),
otherwise per-process in-memory (dev only). Same backend semantics as
:mod:`clearledgr.services.rate_limit`.
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Dict, Optional, Tuple

from fastapi import Request

from clearledgr.api.v1_auth import AgentIdentity

logger = logging.getLogger(__name__)


# ─── Config ────────────────────────────────────────────────────────


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Per-key sliding window — 100 req/min by default.
V1_KEY_LIMIT_PER_MIN = _int_env("V1_KEY_LIMIT_PER_MIN", 100)
# Per-org sliding window — 1000 req/min by default.
V1_ORG_LIMIT_PER_MIN = _int_env("V1_ORG_LIMIT_PER_MIN", 1000)
# Window length. Both counters share it.
V1_RATE_WINDOW_SECONDS = _int_env("V1_RATE_WINDOW_SECONDS", 60)
# Master kill-switch — leaves the deps in place, lets every request through.
V1_RATE_LIMIT_ENABLED = (
    os.getenv("V1_RATE_LIMIT_ENABLED", "true").strip().lower() == "true"
)


# ─── Exception ─────────────────────────────────────────────────────


class RateLimitExceeded(Exception):
    """Raised by :func:`enforce_v1_rate_limit` when a counter trips.

    ``main.py`` catches this and returns 429 with a typed error envelope
    plus a ``Retry-After`` header. The handler also writes one
    ``rate_limit_exceeded`` audit row.
    """

    def __init__(
        self,
        *,
        scope: str,  # "per_key" or "per_org"
        identifier: str,  # the key_id or organization_id that tripped
        organization_id: str,
        key_id: Optional[str],
        actor_id: Optional[str],
        limit: int,
        window_seconds: int,
        retry_after_seconds: int,
        request_path: Optional[str] = None,
        request_method: Optional[str] = None,
        tool_scope: Optional[list] = None,
    ) -> None:
        self.scope = scope
        self.identifier = identifier
        self.organization_id = organization_id
        self.key_id = key_id
        self.actor_id = actor_id
        self.limit = limit
        self.window_seconds = window_seconds
        self.retry_after_seconds = retry_after_seconds
        self.request_path = request_path
        self.request_method = request_method
        # Scope set the actor held at the moment of the breach.
        # Pinned on the audit row so post-hoc analysis can answer
        # "did this key have legitimate access to the endpoints it
        # was hammering?" without back-pressure on the api_keys row.
        self.tool_scope: Optional[list] = (
            list(tool_scope) if tool_scope is not None else None
        )
        super().__init__(
            f"v1_rate_limit_exceeded:{scope}:{identifier}"
        )


# ─── Backend (Redis preferred, memory fallback) ────────────────────


_redis_client = None
_backend = "memory"
_memory_store: Dict[str, Tuple[int, float]] = defaultdict(
    lambda: (0, time.time())
)


def _init_redis() -> None:
    """Best-effort Redis hookup. Mirrors services/rate_limit.py."""
    global _redis_client, _backend
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        return
    try:
        import redis

        _redis_client = redis.Redis.from_url(
            redis_url, decode_responses=True, socket_connect_timeout=2
        )
        _redis_client.ping()
        _backend = "redis"
    except Exception as exc:
        logger.warning(
            "v1 rate limit Redis unavailable (%s) — falling back to memory",
            exc,
        )
        _redis_client = None
        _backend = "memory"


_init_redis()


def get_backend() -> str:
    """Reports which backend is live. Used by /v1/health if we ever want it."""
    return _backend


def _reset_memory_for_tests() -> None:
    """Drop every per-process counter. Called from test fixtures."""
    _memory_store.clear()


def _check_redis(
    redis_key: str, limit: int, window_seconds: int
) -> Tuple[bool, int]:
    """Returns ``(allowed, retry_after_seconds)``."""
    try:
        pipe = _redis_client.pipeline()
        pipe.incr(redis_key)
        pipe.ttl(redis_key)
        count, ttl = pipe.execute()
        if ttl == -1:
            _redis_client.expire(redis_key, window_seconds)
            ttl = window_seconds
        retry_after = max(int(ttl), 1)
        if int(count) > limit:
            # Roll the failed call back so a denied request doesn't burn
            # a slot — best effort, matches services/rate_limit.py.
            try:
                _redis_client.decr(redis_key)
            except Exception:
                pass
            return False, retry_after
        return True, retry_after
    except Exception as exc:
        logger.error(
            "v1 rate limit Redis error (%s) — failing open for %s",
            exc,
            redis_key,
        )
        return True, window_seconds


def _check_memory(
    key: str, limit: int, window_seconds: int
) -> Tuple[bool, int]:
    """Returns ``(allowed, retry_after_seconds)``."""
    now = time.time()
    count, window_start = _memory_store[key]
    if now - window_start >= window_seconds:
        _memory_store[key] = (1, now)
        return True, window_seconds
    if count >= limit:
        retry_after = max(int(window_seconds - (now - window_start)), 1)
        return False, retry_after
    _memory_store[key] = (count + 1, window_start)
    retry_after = max(int(window_seconds - (now - window_start)), 1)
    return True, retry_after


def _check_counter(
    key: str, limit: int, window_seconds: int
) -> Tuple[bool, int]:
    if _redis_client is not None:
        return _check_redis(key, limit, window_seconds)
    return _check_memory(key, limit, window_seconds)


# ─── Enforcement ───────────────────────────────────────────────────


def enforce_v1_rate_limit(
    request: Optional[Request], agent: AgentIdentity
) -> None:
    """Check the per-key + per-org counters. Raise on breach.

    Per-key runs first because it's the narrower bound — a single
    misbehaving agent trips here before it can affect siblings. Per-org
    is the broader fence: caps blast radius even when an org has many
    keys distributed across teams.

    Note: every call increments both counters when both checks pass.
    That's intentional — one /v1 request = one tick of each window.
    """
    if not V1_RATE_LIMIT_ENABLED:
        return

    org_id = agent.organization_id
    key_id = agent.key_id
    actor_id = agent.actor_label

    # Per-key check
    per_key_key = f"v1rl:key:{key_id}"
    allowed, retry_after = _check_counter(
        per_key_key, V1_KEY_LIMIT_PER_MIN, V1_RATE_WINDOW_SECONDS
    )
    if not allowed:
        raise RateLimitExceeded(
            scope="per_key",
            identifier=key_id,
            organization_id=org_id,
            key_id=key_id,
            actor_id=actor_id,
            limit=V1_KEY_LIMIT_PER_MIN,
            window_seconds=V1_RATE_WINDOW_SECONDS,
            retry_after_seconds=retry_after,
            request_path=(
                request.url.path if request and request.url else None
            ),
            request_method=request.method if request else None,
            tool_scope=(
                list(agent.scopes) if agent.scopes is not None else None
            ),
        )

    # Per-org check (only reached when per-key passed)
    per_org_key = f"v1rl:org:{org_id}"
    allowed, retry_after = _check_counter(
        per_org_key, V1_ORG_LIMIT_PER_MIN, V1_RATE_WINDOW_SECONDS
    )
    if not allowed:
        raise RateLimitExceeded(
            scope="per_org",
            identifier=org_id,
            organization_id=org_id,
            key_id=key_id,
            actor_id=actor_id,
            limit=V1_ORG_LIMIT_PER_MIN,
            window_seconds=V1_RATE_WINDOW_SECONDS,
            retry_after_seconds=retry_after,
            request_path=(
                request.url.path if request and request.url else None
            ),
            request_method=request.method if request else None,
            tool_scope=(
                list(agent.scopes) if agent.scopes is not None else None
            ),
        )


# ─── Audit emission ────────────────────────────────────────────────


def emit_rate_limit_exceeded_audit(exc: RateLimitExceeded) -> None:
    """Write one ``rate_limit_exceeded`` row to ``audit_events``.

    Never raises. Mirrors :func:`emit_authorization_denied_audit`.
    The audit row makes "why did my agent stop working at 14:03 UTC?"
    answerable forever — limits / scope / window / path are all in
    ``payload_json``.
    """
    try:
        from clearledgr.core.authorization import _get_db

        db = _get_db()
        db.append_audit_event(
            {
                "event_type": "rate_limit_exceeded",
                "box_type": "organization",
                "box_id": exc.organization_id or "unknown",
                "actor_type": "agent",
                "actor_id": exc.actor_id or "unknown",
                "organization_id": exc.organization_id or "_unknown",
                "source": "v1_rate_limit",
                "tool_scope": exc.tool_scope,
                "payload_json": {
                    "scope": exc.scope,
                    "key_id": exc.key_id,
                    "limit": exc.limit,
                    "window_seconds": exc.window_seconds,
                    "retry_after_seconds": exc.retry_after_seconds,
                    "request_path": exc.request_path,
                    "request_method": exc.request_method,
                },
            }
        )
    except Exception:
        logger.exception("Failed to emit rate_limit_exceeded audit event")
