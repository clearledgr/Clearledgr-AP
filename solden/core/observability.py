"""Small observability helpers.

Background loops (agent_background, gmail_autopilot, outlook_autopilot,
finance runtime reapers) run outside any HTTP request context and
outside Celery task boundaries. Their broad ``except Exception`` blocks
normally call ``logger.warning("foo failed: %s", exc)`` and continue —
which is the right operational behaviour (loop survives, retries on
next tick) but means:

- The traceback is lost (``%s`` of exc prints one line, not the stack).
- Sentry's default LoggingIntegration only captures at ``ERROR`` and
  above, so warnings never become Sentry events.
- On Railway, those warnings land in stderr and scroll away without
  ever reaching the dashboards ops actually watches.

``capture_background_exception`` is the one function call that fixes
all three. It logs at the right level with ``exc_info`` attached (so
tracebacks land in structured logs) and explicitly pushes to Sentry
if the SDK is installed. Safe in every environment: if Sentry isn't
configured, the capture call is a no-op; if the SDK isn't installed,
the import fails silently and we still get the log.
"""
from __future__ import annotations

import logging
from typing import Optional


def capture_background_exception(
    logger: logging.Logger,
    context: str,
    exc: BaseException,
    *,
    extras: Optional[dict] = None,
) -> None:
    """Log + Sentry-capture an exception from a non-request background
    loop. Never raises — safe to call from inside an ``except`` block.

    ``context`` is a short human string like ``"override_window_reaper"``
    that identifies the loop; it lands in the log message and in the
    Sentry event's "tag" field so incidents can be grouped by loop.
    """
    try:
        logger.exception("[bg:%s] %s", context, exc)
    except Exception:  # noqa: BLE001
        # Logging itself should never take down a background loop.
        pass

    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            scope.set_tag("background_loop", context)
            if extras:
                for k, v in extras.items():
                    scope.set_extra(str(k), v)
            sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001
        # Sentry SDK missing / Sentry down / scope push raised — none
        # of those are the background loop's problem. Swallow and
        # rely on the structured log above.
        pass
