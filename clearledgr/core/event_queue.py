"""Durable Event Queue — Agent Design Specification §2 + §11.2.

Redis Streams-backed durable event queue with consumer groups.
Two streams: high_priority (Enterprise) and standard (Professional, Starter).
Workers poll high_priority first, fall back to standard if empty.

If Redis is unavailable, falls back to a synchronous in-process queue
(for development and testing only — not durable).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from clearledgr.core.events import AgentEvent

logger = logging.getLogger(__name__)

STREAM_HIGH = "clearledgr:events:high_priority"
STREAM_STANDARD = "clearledgr:events:standard"
GROUP_NAME = "clearledgr-workers"
_VISIBILITY_TIMEOUT_MS = 60_000  # 60 seconds before reclaim


class RedisEventQueue:
    """Durable event queue backed by Redis Streams.

    §2: Events are not lost if the agent is unavailable — they wait.
    §11.2: Two priority streams with consumer groups.
    """

    def __init__(self, redis_url: Optional[str] = None):
        import redis as redis_lib

        url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._redis = redis_lib.Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=5,
        )
        self._ensure_streams()

    def _ensure_streams(self) -> None:
        """Create streams and consumer groups if they don't exist."""
        for stream in (STREAM_HIGH, STREAM_STANDARD):
            try:
                self._redis.xgroup_create(
                    stream, GROUP_NAME, id="0", mkstream=True,
                )
            except Exception as exc:
                # BUSYGROUP = group already exists (idempotent)
                if "BUSYGROUP" not in str(exc):
                    logger.warning("[EventQueue] Failed to create group for %s: %s", stream, exc)

    def enqueue(self, event: AgentEvent) -> str:
        """Enqueue an event into the appropriate priority stream.

        Returns the Redis stream entry ID.
        """
        # Deduplication by idempotency_key
        if event.idempotency_key:
            dedup_key = f"clearledgr:dedup:{event.idempotency_key}"
            if not self._redis.set(dedup_key, "1", nx=True, ex=86400):
                logger.debug(
                    "[EventQueue] Duplicate event dropped: %s", event.idempotency_key,
                )
                return "duplicate"

        stream = STREAM_HIGH if event.priority == "high_priority" else STREAM_STANDARD
        entry_id = self._redis.xadd(stream, event.to_dict())
        logger.info(
            "[EventQueue] Enqueued %s (%s) → %s [%s]",
            event.type.value, event.id, stream.split(":")[-1], entry_id,
        )
        return str(entry_id)

    def claim_next(
        self,
        consumer_name: str,
        block_ms: int = 5000,
    ) -> Optional[Tuple[str, str, AgentEvent]]:
        """Claim the next event from the queue.

        Checks high_priority first, then standard.
        Returns (stream_name, entry_id, event) or None if no events.
        """
        for stream in (STREAM_HIGH, STREAM_STANDARD):
            try:
                results = self._redis.xreadgroup(
                    GROUP_NAME, consumer_name, {stream: ">"},
                    count=1, block=block_ms if stream == STREAM_STANDARD else 0,
                )
                if results:
                    for _stream_name, entries in results:
                        for entry_id, data in entries:
                            event = AgentEvent.from_dict(data)
                            return (stream, str(entry_id), event)
            except Exception as exc:
                logger.warning("[EventQueue] Read from %s failed: %s", stream, exc)

        return None

    def ack(self, stream: str, entry_id: str) -> None:
        """Acknowledge successful processing of an event."""
        self._redis.xack(stream, GROUP_NAME, entry_id)

    def nack_and_requeue(self, stream: str, entry_id: str, delay_seconds: int = 5) -> None:
        """Return an event to the queue with a delay (workspace at concurrency limit)."""
        # Acknowledge the current claim, then re-add with delay marker
        self._redis.xack(stream, GROUP_NAME, entry_id)
        # The event stays in the stream's pending list for other consumers
        # For explicit requeue with delay, we read the event data and re-add
        try:
            entries = self._redis.xrange(stream, entry_id, entry_id)
            if entries:
                _, data = entries[0]
                data["_requeue_after"] = str(delay_seconds)
                self._redis.xadd(stream, data)
        except Exception as exc:
            logger.warning("[EventQueue] Requeue failed for %s: %s", entry_id, exc)

    def reclaim_stale(self, consumer_name: str) -> List[Tuple[str, str, AgentEvent]]:
        """Reclaim events from dead consumers (§12.1: crash recovery).

        Uses XAUTOCLAIM to take over events that have been pending
        longer than the visibility timeout.
        """
        reclaimed: List[Tuple[str, str, AgentEvent]] = []
        for stream in (STREAM_HIGH, STREAM_STANDARD):
            try:
                # XAUTOCLAIM: take over stale pending entries
                result = self._redis.xautoclaim(
                    stream, GROUP_NAME, consumer_name,
                    min_idle_time=_VISIBILITY_TIMEOUT_MS,
                    start_id="0-0", count=10,
                )
                if result and len(result) >= 2:
                    entries = result[1]  # list of (id, data) tuples
                    for entry_id, data in entries:
                        if data:
                            event = AgentEvent.from_dict(data)
                            reclaimed.append((stream, str(entry_id), event))
            except Exception as exc:
                logger.debug("[EventQueue] Reclaim from %s failed: %s", stream, exc)

        if reclaimed:
            logger.info("[EventQueue] Reclaimed %d stale events", len(reclaimed))
        return reclaimed

    def pending_count(self) -> Dict[str, int]:
        """Get pending message count per stream (for autoscaler metrics)."""
        counts = {}
        for stream in (STREAM_HIGH, STREAM_STANDARD):
            try:
                info = self._redis.xpending(stream, GROUP_NAME)
                counts[stream] = info.get("pending", 0) if isinstance(info, dict) else 0
            except Exception:
                counts[stream] = 0
        return counts

    def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return self._redis.ping()
        except Exception:
            return False


# ---------------------------------------------------------------------------
# In-memory fallback for development/testing
# ---------------------------------------------------------------------------

class InMemoryEventQueue:
    """Non-durable fallback queue for dev/test. Not for production."""

    def __init__(self) -> None:
        self._queues: Dict[str, List[Tuple[str, AgentEvent]]] = {
            STREAM_HIGH: [],
            STREAM_STANDARD: [],
        }
        self._counter = 0

    def enqueue(self, event: AgentEvent) -> str:
        self._counter += 1
        entry_id = f"{self._counter}-0"
        stream = STREAM_HIGH if event.priority == "high_priority" else STREAM_STANDARD
        self._queues[stream].append((entry_id, event))
        return entry_id

    def claim_next(self, consumer_name: str, block_ms: int = 0) -> Optional[Tuple[str, str, AgentEvent]]:
        for stream in (STREAM_HIGH, STREAM_STANDARD):
            if self._queues[stream]:
                entry_id, event = self._queues[stream].pop(0)
                return (stream, entry_id, event)
        return None

    def ack(self, stream: str, entry_id: str) -> None:
        pass

    def nack_and_requeue(self, stream: str, entry_id: str, delay_seconds: int = 5) -> None:
        pass

    def reclaim_stale(self, consumer_name: str) -> list:
        return []

    def pending_count(self) -> Dict[str, int]:
        return {s: len(q) for s, q in self._queues.items()}

    def ping(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_queue_instance: Optional[Any] = None


def get_event_queue() -> Any:
    """Get or create the event queue singleton.

    Returns RedisEventQueue if Redis is available, InMemoryEventQueue otherwise.
    """
    global _queue_instance
    if _queue_instance is not None:
        return _queue_instance

    redis_url = os.environ.get("REDIS_URL", "").strip()
    if redis_url:
        try:
            _queue_instance = RedisEventQueue(redis_url)
            if _queue_instance.ping():
                logger.info("[EventQueue] Using Redis Streams backend")
                return _queue_instance
        except Exception as exc:
            logger.warning("[EventQueue] Redis unavailable (%s), using in-memory fallback", exc)

    _queue_instance = InMemoryEventQueue()
    logger.info("[EventQueue] Using in-memory fallback (NOT durable)")
    return _queue_instance


def reset_event_queue() -> None:
    """Reset the singleton (for tests)."""
    global _queue_instance
    _queue_instance = None
