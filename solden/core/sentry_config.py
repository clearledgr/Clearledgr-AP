"""Shared Sentry init helpers.

The web (main.py) and the Celery worker (celery_app.py) both call
``sentry_sdk.init``. Both previously passed ``send_default_pii=False``
and relied on Sentry's stock scrubbing, but that only covers fields
Sentry *knows* are PII (``request.ip``, ``user.email``, etc.). It does
not touch the local-variable capture inside the exception frame,
which for us routinely includes:

- ``invoice`` / ``bill`` objects with vendor name + amount + IBAN
- ``token`` / ``access_token`` / ``refresh_token`` strings
- ``bank_details`` dicts post-decryption
- ``api_key`` parameters

Those values flow into ``event["exception"]["values"][*]["stacktrace"]
["frames"][*]["vars"]``, which Sentry happily stores server-side.
``build_sentry_before_send`` returns a callback that walks every
frame's var map and redacts entries whose name or value looks
sensitive. Belt + braces: we also scrub the top-level ``extra`` and
``contexts`` dicts that Celery/HTTPX integrations can populate.

Design choice: name-based redaction (substring match against
SENSITIVE_KEY_MARKERS) is coarse but fast and false-positives safe
— better to over-redact "receipt_id" than let "refresh_token" slip.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# Field-name substring markers. If a variable / key name (lowercased)
# contains any of these, its value is redacted before Sentry sees it.
SENSITIVE_KEY_MARKERS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "x-api-key",
    "cookie",
    "credit_card",
    "card_number",
    "iban",
    "account_number",
    "routing_number",
    "sort_code",
    "swift",
    "bic",
    "bank_details",
    "invoice",
    "vendor_name",
    "sender",
    "recipient",
    "email",
    "raw_body",
    "body_html",
    "body_text",
    "payload_json",
    "extraction",
    "ocr",
)

REDACTED = "[REDACTED]"


def _is_sensitive(name: str) -> bool:
    if not name:
        return False
    lowered = name.lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def _scrub_mapping(obj: Any, depth: int = 0) -> Any:
    """Recursively walk dict/list/tuple structures and redact sensitive
    keys. Depth-bounded so a circular or huge graph can't hang the
    hot path. Strings themselves are not pattern-matched — we rely on
    the field name, which is deterministic and doesn't false-positive
    on normal content."""
    if depth > 8:
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key_str = str(k)
            if _is_sensitive(key_str):
                out[k] = REDACTED
            else:
                out[k] = _scrub_mapping(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [_scrub_mapping(v, depth + 1) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_scrub_mapping(v, depth + 1) for v in obj)
    return obj


def build_sentry_before_send():
    """Return a before_send callback suitable for ``sentry_sdk.init``.

    Scrubs sensitive keys in:
      - event["exception"]["values"][*]["stacktrace"]["frames"][*]["vars"]
      - event["extra"]
      - event["contexts"]
      - event["request"]["data"] (form/JSON bodies)
      - event["request"]["headers"]
      - event["request"]["cookies"]

    Defensive: if the scrubbing itself raises, we return the event
    unmodified rather than drop the error report entirely — a
    partially-scrubbed event is still valuable for incident response,
    but losing the event means we never learn about the underlying
    bug.
    """

    def before_send(event: Dict[str, Any], hint: Optional[Dict[str, Any]] = None):
        try:
            # Frame-local variables
            for exc_value in (event.get("exception", {}) or {}).get("values", []) or []:
                stacktrace = exc_value.get("stacktrace") or {}
                for frame in stacktrace.get("frames", []) or []:
                    frame_vars = frame.get("vars")
                    if isinstance(frame_vars, dict):
                        frame["vars"] = _scrub_mapping(frame_vars)

            for key in ("extra", "contexts"):
                value = event.get(key)
                if isinstance(value, dict):
                    event[key] = _scrub_mapping(value)

            request = event.get("request")
            if isinstance(request, dict):
                for key in ("data", "headers", "cookies"):
                    sub = request.get(key)
                    if isinstance(sub, dict):
                        request[key] = _scrub_mapping(sub)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[sentry_config] before_send scrub raised: %s", exc)
        return event

    return before_send
