"""Idempotency cache for the single-pass invoice processor.

Gmail Pub/Sub is at-least-once delivery. When a webhook re-fires
for an email we've already processed, the canonical-shape inputs
(subject + sender + body + attachment manifest) are byte-identical
on the second hit. Without a cache, we re-run the single-pass
Claude call and pay the 1500-3000-token cost on every retry.

This module hashes the canonical inputs (SHA-256, normalised) and
caches the *parsed + validated* single-pass result for one hour
under that key. The next call with the same inputs returns the
cached dict directly — no Claude round-trip, no LLM gateway call,
no llm_call_log entry.

Design constraints honoured:

  - **No silent staleness**: 1-hour TTL caps how long a cached
    result can mask updated context. Vendor history / recent
    invoices change quickly; an hour is the rough freshness
    window.
  - **Failure isolation**: cache misses on the read path AND
    write-path errors are non-fatal. The single-pass processor
    keeps its existing fail-to-None contract — a Redis hiccup
    cannot break the AP intake flow.
  - **Deterministic hashing**: the content hash is sorted-keys
    JSON over a small canonical projection (subject, sender,
    body, attachment_manifest, has_visual_attachments). Vendor
    context / thread context / PO context are NOT in the hash —
    those change between calls within a Gmail-thread refresh
    window and would defeat the cache.
  - **Redis when available, in-memory dict fallback**: same
    pattern as ``clearledgr.services.rate_limit``. Tests + dev
    use the dict; prod uses Redis.

The cache is content-keyed, not user-keyed. Two orgs receiving
the same templated invoice email from the same sender would
*technically* hit the same cache key — but each call still
applies its own org-specific vendor / thread / PO context
*after* the cache hit (the cache only stores the LLM-extracted
JSON, not the formatted triage result), so org isolation is
preserved.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_CACHE_KEY_PREFIX = "solden:single_pass:v1:"

# Default TTL: vendor / org context can move within a day so an hour
# is a safe ceiling. Overridable via env for tests + cost-tuning.
_DEFAULT_TTL_SECONDS = int(os.getenv("CLEARLEDGR_SINGLE_PASS_CACHE_TTL", "3600"))

# In-memory fallback for dev / tests / no-REDIS_URL deployments.
# Maps cache_key → (expires_at_monotonic, value_dict).
_in_memory_store: Dict[str, tuple] = {}

_redis_client = None
_backend_resolved = False


def _resolve_backend():
    """Lazily resolve the Redis backend or fall back to the in-memory
    dict. Mirrors the rate_limit service pattern; failures fall
    through silently."""
    global _redis_client, _backend_resolved
    if _backend_resolved:
        return _redis_client
    _backend_resolved = True
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        return None
    try:
        import redis  # noqa: F401 — deferred import; redis is in requirements.txt
        from redis import Redis

        _redis_client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        # Cheap connectivity probe so a misconfigured URL fails over
        # to the dict immediately rather than on first cache call.
        _redis_client.ping()
        return _redis_client
    except Exception as exc:
        logger.warning(
            "[SinglePassCache] Redis unavailable (%s) — falling back to in-memory cache",
            exc,
        )
        _redis_client = None
        return None


def _reset_for_testing() -> None:
    """Drop the cached backend handle + the in-memory store. Tests
    that toggle REDIS_URL between cases need both reset."""
    global _redis_client, _backend_resolved
    _redis_client = None
    _backend_resolved = False
    _in_memory_store.clear()


def compute_content_hash(
    *,
    subject: str,
    sender: str,
    body: str,
    has_visual_attachments: bool,
    visual_attachments: Optional[List[Dict[str, Any]]] = None,
    attachment_text: str = "",
) -> str:
    """Compute the canonical SHA-256 of single-pass-relevant inputs.

    Excludes:
      - vendor/thread/PO/recent-invoices context (changes between
        retries even when the email is identical)
      - organization_id (cache is content-keyed; the LLM output is
        org-agnostic at the extraction level)
      - thread_id (the same email can have different thread_ids
        across restored Gmail history)

    Includes a digest of each visual attachment's bytes (or its
    pre-encoded data string) so a different attached PDF produces a
    different cache key even if the email body is identical.
    """
    att_digests: List[str] = []
    for att in visual_attachments or []:
        if not isinstance(att, dict):
            continue
        data = att.get("data", "")
        if isinstance(data, bytes):
            att_digests.append(hashlib.sha256(data).hexdigest())
        elif isinstance(data, str) and data:
            # Already-base64-encoded bytes; hash the encoded string.
            att_digests.append(hashlib.sha256(data.encode("utf-8")).hexdigest())
    canonical = {
        "subject": subject or "",
        "sender": sender or "",
        "body": body or "",
        "has_visual_attachments": bool(has_visual_attachments),
        "attachment_digests": att_digests,
        "attachment_text": attachment_text or "",
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_cached_result(content_hash: str) -> Optional[Dict[str, Any]]:
    """Return the cached parsed single-pass result for ``content_hash``,
    or None on miss / expiry / backend error."""
    key = _CACHE_KEY_PREFIX + content_hash
    backend = _resolve_backend()
    if backend is not None:
        try:
            raw = backend.get(key)
            if not raw:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.debug("[SinglePassCache] Redis get failed: %s", exc)
            return None
    # In-memory fallback
    entry = _in_memory_store.get(key)
    if not entry:
        return None
    expires_at, value = entry
    if time.monotonic() > expires_at:
        _in_memory_store.pop(key, None)
        return None
    return value


def set_cached_result(
    content_hash: str,
    result: Dict[str, Any],
    *,
    ttl_seconds: Optional[int] = None,
) -> None:
    """Cache the parsed single-pass result. Failure is non-fatal."""
    key = _CACHE_KEY_PREFIX + content_hash
    ttl = int(ttl_seconds if ttl_seconds is not None else _DEFAULT_TTL_SECONDS)
    if ttl <= 0:
        return
    backend = _resolve_backend()
    if backend is not None:
        try:
            backend.setex(key, ttl, json.dumps(result))
            return
        except Exception as exc:
            logger.debug("[SinglePassCache] Redis setex failed: %s", exc)
            # Fall through to in-memory so the same process still
            # benefits from the cache even when Redis hiccups.
    _in_memory_store[key] = (time.monotonic() + ttl, result)
