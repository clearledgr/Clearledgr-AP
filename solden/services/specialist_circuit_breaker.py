"""Per-specialist circuit breaker (Sprint 4 Phase 2).

Phase 1 shipped the failure-isolation wrapper — a skill exception
in vendor-compliance returns a structured error result instead of
crashing the runtime. Phase 2 adds the **automatic quarantine**:
when a specialist fails repeatedly within a window, the breaker
trips OPEN; subsequent dispatches short-circuit to a quarantined
result without invoking the skill, so a cascading failure mode
can't pile up retries against a downstream that's already broken.

States (classic three-state breaker):

* **CLOSED**     — normal. Every dispatch hits the skill.
* **OPEN**       — quarantined. Dispatches short-circuit; the breaker
                   stays open for ``cooldown_seconds`` after the trip.
* **HALF_OPEN**  — probing recovery. The first dispatch after the
                   cooldown is allowed through. If it succeeds, the
                   breaker resets to CLOSED. If it fails, the breaker
                   trips back to OPEN with the cooldown extended.

Thresholds are per-specialist and configurable. Defaults are
conservative: 5 errors within 60 seconds trips, 30-second cooldown,
half-open probing on the next dispatch after cooldown elapses.

Pure-functional state machine; the breaker holds counters in
process memory, no DB. That's the right scope for "this specialist
is sick right now in this process" — cluster-wide health is the ops
team's monitoring problem, not the breaker's.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from typing import Optional


logger = logging.getLogger(__name__)


# Three-state breaker.
BREAKER_STATE_CLOSED = "closed"
BREAKER_STATE_OPEN = "open"
BREAKER_STATE_HALF_OPEN = "half_open"


@dataclasses.dataclass
class BreakerConfig:
    """Per-specialist breaker tuning.

    Defaults trade off responsiveness vs noise:
    * 5 errors / 60s trips — survives a one-off blip without firing
    * 30s cooldown — long enough that a downstream blip can clear,
      short enough that a transient outage doesn't lock the
      specialist out for the whole shift
    """
    error_threshold: int = 5
    error_window_seconds: float = 60.0
    cooldown_seconds: float = 30.0
    # Specialists with this name pattern bypass the breaker entirely.
    # Useful for specialists handling system-critical paths where
    # quarantining is unsafe (e.g. compliance-required logging).
    # None = no bypass.
    bypass_pattern: Optional[str] = None


@dataclasses.dataclass
class _BreakerState:
    """Mutable per-specialist state. Guarded by a lock per breaker
    so concurrent dispatches don't corrupt the counters.
    """
    state: str = BREAKER_STATE_CLOSED
    error_timestamps: list = dataclasses.field(default_factory=list)
    opened_at: Optional[float] = None
    consecutive_open_trips: int = 0


class SpecialistCircuitBreaker:
    """Single-specialist breaker. The router holds one per
    registered specialist.

    Thread-safe (uses a per-breaker lock) so concurrent dispatches
    in an async runtime don't race the counters. The internal API
    is synchronous — the router calls ``allow()`` before dispatch
    and ``record_outcome(ok)`` after.
    """

    def __init__(self, name: str, config: Optional[BreakerConfig] = None) -> None:
        self.name = name
        self.config = config or BreakerConfig()
        self._state = _BreakerState()
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state.state

    @property
    def opened_at(self) -> Optional[float]:
        with self._lock:
            return self._state.opened_at

    def allow(self, *, now: Optional[float] = None) -> bool:
        """True if the next dispatch should hit the skill, False if
        the breaker is OPEN and still cooling down. HALF_OPEN
        transitions happen here (the cooldown elapses → next allow
        flips to HALF_OPEN and returns True for the probe).
        """
        ts = now if now is not None else time.monotonic()
        with self._lock:
            if self._state.state == BREAKER_STATE_CLOSED:
                return True
            if self._state.state == BREAKER_STATE_HALF_OPEN:
                # Already probing — exactly one probe in flight.
                # Subsequent callers are denied + the breaker
                # latches back to OPEN so the probe's outcome (when
                # it lands) drives recovery, not the second caller's.
                self._state.state = BREAKER_STATE_OPEN
                return False
            # state == OPEN
            opened_at = self._state.opened_at or ts
            if ts - opened_at >= self.config.cooldown_seconds:
                logger.info(
                    "[breaker:%s] cooldown elapsed → half_open probe", self.name,
                )
                self._state.state = BREAKER_STATE_HALF_OPEN
                return True
            return False

    def record_outcome(self, *, ok: bool, now: Optional[float] = None) -> None:
        """Record the dispatch result and update breaker state.

        ``ok=True``  → success. CLOSED stays CLOSED; HALF_OPEN
                        flips back to CLOSED and clears trip counters.
        ``ok=False`` → failure. Errors get appended to the rolling
                        window; if HALF_OPEN, immediately trips back
                        to OPEN (the probe failed); if CLOSED and
                        the window threshold is crossed, trips OPEN.
        """
        ts = now if now is not None else time.monotonic()
        with self._lock:
            if ok:
                if self._state.state == BREAKER_STATE_HALF_OPEN:
                    logger.info(
                        "[breaker:%s] probe succeeded → closed", self.name,
                    )
                    self._state.state = BREAKER_STATE_CLOSED
                    self._state.error_timestamps = []
                    self._state.opened_at = None
                    self._state.consecutive_open_trips = 0
                # CLOSED+ok: nothing to do. Don't clear the rolling
                # window on success because we want stale errors to
                # age out naturally; otherwise a flaky specialist
                # that toggles ok/error/ok/error could never trip.
                return

            # ok=False
            self._state.error_timestamps.append(ts)
            cutoff = ts - self.config.error_window_seconds
            self._state.error_timestamps = [
                e for e in self._state.error_timestamps if e >= cutoff
            ]

            if self._state.state == BREAKER_STATE_HALF_OPEN:
                logger.warning(
                    "[breaker:%s] probe failed → open (extended cooldown)",
                    self.name,
                )
                self._state.state = BREAKER_STATE_OPEN
                self._state.opened_at = ts
                self._state.consecutive_open_trips += 1
                return

            if self._state.state == BREAKER_STATE_CLOSED:
                if len(self._state.error_timestamps) >= self.config.error_threshold:
                    logger.warning(
                        "[breaker:%s] %d errors in %ss → open",
                        self.name,
                        len(self._state.error_timestamps),
                        self.config.error_window_seconds,
                    )
                    self._state.state = BREAKER_STATE_OPEN
                    self._state.opened_at = ts
                    self._state.consecutive_open_trips += 1
                return

            # state == OPEN: nothing to do. Failures while OPEN are
            # already short-circuited at the router; this branch is
            # defensive for direct callers.

    def reset(self) -> None:
        """Force-reset the breaker to CLOSED. Used by ops tooling
        (``solden specialist breaker reset <name>``) and by tests.
        """
        with self._lock:
            logger.info("[breaker:%s] manual reset", self.name)
            self._state = _BreakerState()
