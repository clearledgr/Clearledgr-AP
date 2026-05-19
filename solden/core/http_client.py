"""Shared async HTTP client for outbound API calls.

Why this exists:

The pattern that used to be everywhere:

    async with httpx.AsyncClient(timeout=...) as client:
        resp = await client.post(...)

is correct (no socket leak — context manager closes cleanly) but it
spawns a fresh connection pool for every single call. Each spawn
pays:
  * one TCP handshake
  * one TLS handshake (TLS 1.3 is ~1 RTT, TLS 1.2 is 2 RTT)
  * pool setup/teardown overhead

Against Anthropic (LLM gateway, hit once per invoice + once per
sidebar question) that's 50-200ms of handshake latency *per call*.
At moderate load it's noticeable; at peak it adds up to a measurable
chunk of our user-facing response time budget.

A shared ``httpx.AsyncClient`` reuses TCP connections (keep-alive)
and the TLS session, so each call after the first is essentially a
free cold-handshake tax. This module owns that client.

Lifetime: the client is lazily created on first ``get_http_client()``
call. There's one per process — in FastAPI workers that's one per
uvicorn process; in Celery workers that's one per worker process.
httpx's AsyncClient is safe across coroutines on a single event loop,
which matches what we have (each process runs a single loop).

Shutdown: the FastAPI lifespan hook calls ``close_http_client()`` on
app shutdown so the pool is drained cleanly before the process
exits. Celery workers drop the client when they exit; Python's GC
handles the rest.

Migration policy: not every call needs this. Hot paths (LLM gateway,
Gmail fetch, ERP bill post) should use the shared client. One-off
calls (OAuth token exchanges that run once per org per day) can stay
as ``async with httpx.AsyncClient()`` — the overhead there is
negligible relative to call frequency.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


_shared_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    """Return the process-wide shared async client, creating on demand."""
    global _shared_client
    if _shared_client is None:
        _shared_client = httpx.AsyncClient(
            # Timeout defaults mirror the old per-call patterns.
            # Callers that need tighter deadlines pass ``timeout=`` on
            # the request itself — that overrides this default per
            # call without affecting other callers sharing the pool.
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(
                # Bounded pool prevents runaway fd usage under a load
                # spike while leaving plenty of headroom for normal
                # concurrency. The worker runs 4 greenlets (Celery
                # concurrency default) × a few concurrent calls each.
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30.0,
            ),
            # HTTP/1.1 keep-alive is the real win here — TLS-session
            # reuse + TCP connection reuse on subsequent calls to the
            # same host. HTTP/2 would be better for fanning out many
            # concurrent calls to Anthropic but requires the `h2`
            # extra which isn't in requirements.txt; skip until we
            # have a concrete reason to add the dep.
        )
        logger.info("[http_client] shared AsyncClient created (http1, pool=100)")
    return _shared_client


async def close_http_client() -> None:
    """Close the shared client if it was ever created. Safe to call
    multiple times or before the client is initialized."""
    global _shared_client
    if _shared_client is not None:
        client, _shared_client = _shared_client, None
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[http_client] aclose raised: %s", exc)


def _reset_for_testing() -> None:
    """Drop the cached shared client without awaiting aclose.

    Tests that patch ``httpx.AsyncClient`` to inject mocks need the
    next ``get_http_client()`` call to hit the patched constructor,
    which means the cached instance has to be cleared first. Using
    the async ``close_http_client`` would need an event loop; this
    synchronous variant is a no-op for test setup.
    """
    global _shared_client
    _shared_client = None
