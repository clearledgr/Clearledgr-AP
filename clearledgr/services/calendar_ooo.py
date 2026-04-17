"""Google Calendar out-of-office check — DESIGN_THESIS.md §6.8.

"If the assigned approver's Google Calendar shows OOO, the agent routes
to their backup. Backup is configured per role in Settings. The agent
never waits for an approver who is not available."

This module answers a single question: is the given approver out of
office right now (or inside the next approval-response window)? It
uses the Google Calendar freeBusy API with the user's own OAuth
token — not a service account — so the check only works for
approvers who are Clearledgr users with a Gmail token already on
file. External approvers (e.g. a fractional CFO who never installs
the extension) fall through to the delegation-rules path in
:func:`slack_notifications._check_ooo_and_get_backup`; the upstream
caller then picks that up.

Fail-open semantics: any error (missing token, expired refresh,
Calendar API outage, network blip) returns ``False`` — the approver
is treated as available. The cost of a false negative is a Slack DM
sitting unactioned for a few hours until a human notices. The cost
of a false positive (wrongly treating someone as OOO during a
Calendar outage) would be an unsolicited reroute to their backup,
which is the louder, more confusing failure. Availability wins ties.

In-memory TTL cache (5 min) keeps a burst of approvals from
hammering the Calendar API. The cache key includes the window hint
so different check windows don't collide.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_CALENDAR_FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

# 5-minute cache TTL. Short enough that an approver returning from
# OOO is routed to normally in minutes; long enough that a typical
# batch-approve run hits the cache 99% of the time.
_CACHE_TTL_SECONDS = 300

# 4-hour look-ahead window. Matches the §6.8 CFO response window;
# any busy time inside that range counts as "not available to
# respond within SLA", which is the practical semantic operators
# care about, not "OOO for the whole day".
_LOOKAHEAD_HOURS = 4

# Cache: {cache_key: (is_ooo, cached_at_unix_seconds)}
_cache: Dict[str, Tuple[bool, float]] = {}


def _cache_key(email: str, window_hours: int) -> str:
    return f"{email.lower().strip()}:{int(window_hours)}"


def _cache_get(key: str) -> Optional[bool]:
    entry = _cache.get(key)
    if entry is None:
        return None
    value, cached_at = entry
    if time.time() - cached_at > _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: bool) -> None:
    _cache[key] = (value, time.time())


def clear_cache() -> None:
    """Test hook — clears the TTL cache."""
    _cache.clear()


def _load_google_token(email: str, db: Any = None) -> Optional[Dict[str, Any]]:
    """Fetch the Google OAuth token row for this email.

    Returns the decrypted row (with an ``access_token`` key) or
    ``None`` if the user has no Google OAuth token on file. The
    workspace's Gmail OAuth is persisted under
    ``provider='google'`` by the extension-side
    ``register_gmail_token`` endpoint.
    """
    if db is None:
        from clearledgr.core.database import get_db
        db = get_db()
    try:
        if not hasattr(db, "get_oauth_token_by_email"):
            return None
        return db.get_oauth_token_by_email(email, "google")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[calendar_ooo] token lookup failed for %s: %s", email, exc)
        return None


async def _query_freebusy(
    access_token: str, email: str, window_hours: int,
) -> Optional[bool]:
    """Hit the Google Calendar freeBusy API.

    Returns ``True`` iff the user has any busy block inside ``[now,
    now + window_hours]`` with a transparency of ``opaque`` (the
    Calendar default for accepted events). Returns ``None`` on any
    network / auth error so the caller can decide the default.
    """
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(hours=window_hours)).isoformat()

    payload = {
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": email}],
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                _CALENDAR_FREEBUSY_URL,
                json=payload,
                headers=headers,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[calendar_ooo] freeBusy request failed for %s: %s", email, exc)
        return None

    if resp.status_code == 401:
        logger.info("[calendar_ooo] 401 for %s — token expired or scope not granted", email)
        return None
    if resp.status_code == 403:
        # Most commonly: calendar.readonly scope not granted. This is
        # an installation-level problem (user re-consent needed), not
        # a per-request one. Log once and fall through.
        logger.warning("[calendar_ooo] 403 for %s — calendar scope likely not granted", email)
        return None
    if resp.status_code != 200:
        logger.debug("[calendar_ooo] freeBusy returned HTTP %d for %s", resp.status_code, email)
        return None

    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError):
        return None

    calendars = body.get("calendars") or {}
    cal = calendars.get(email) or {}
    if cal.get("errors"):
        logger.debug("[calendar_ooo] freeBusy reported errors for %s: %s", email, cal["errors"])
        return None

    busy_blocks = cal.get("busy") or []
    return bool(busy_blocks)


async def is_approver_ooo(
    email: str,
    *,
    window_hours: int = _LOOKAHEAD_HOURS,
    db: Any = None,
) -> bool:
    """Return True iff ``email`` has a busy block inside the next
    ``window_hours`` according to their Google Calendar freeBusy feed.

    Fail-open: returns False on any error (missing token, API down,
    scope not granted). See module docstring for why availability
    wins ties.
    """
    email = str(email or "").strip().lower()
    if not email:
        return False

    cache_key = _cache_key(email, window_hours)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    token_row = _load_google_token(email, db=db)
    if not token_row or not token_row.get("access_token"):
        _cache_set(cache_key, False)
        return False

    busy = await _query_freebusy(
        access_token=str(token_row["access_token"]),
        email=email,
        window_hours=window_hours,
    )
    # Fail-open: freeBusy returned None (error) → treat as available.
    result = bool(busy) if busy is not None else False
    _cache_set(cache_key, result)
    return result


def is_approver_ooo_sync(
    email: str,
    *,
    window_hours: int = _LOOKAHEAD_HOURS,
    db: Any = None,
) -> bool:
    """Synchronous wrapper for callers outside an event loop — the
    routing helper in ``slack_notifications`` runs from sync code.
    Uses ``asyncio.run`` when no loop is active, schedules on the
    running loop otherwise.
    """
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Called from inside an async context — fire a task and block
        # briefly. This path is rare (sync routing called from async
        # code) but we guard against deadlock by capping the wait.
        import concurrent.futures

        future = asyncio.run_coroutine_threadsafe(
            is_approver_ooo(email, window_hours=window_hours, db=db),
            loop,
        )
        try:
            return future.result(timeout=6.0)
        except (concurrent.futures.TimeoutError, Exception) as exc:
            logger.debug("[calendar_ooo] sync wrap timeout/error: %s", exc)
            return False

    return asyncio.run(
        is_approver_ooo(email, window_hours=window_hours, db=db)
    )
