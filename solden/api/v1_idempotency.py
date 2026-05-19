"""Idempotency helpers for /v1/intents/execute.

Stripe pattern. Caller passes ``Idempotency-Key`` (header preferred,
body-field fallback). The server:

1. Computes a SHA-256 hash of the canonical-JSON request body.
2. Looks up ``(organization_id, idempotency_key)`` in
   ``intent_responses``.
3. If a row exists and the hash matches: replay the cached response.
4. If a row exists and the hash differs: return 409 conflict.
5. Otherwise: execute the intent, persist the response keyed by the
   idempotency key + payload hash, return.

24h TTL on cached responses. Cleanup happens lazily on read (a row
past its ``expires_at`` is treated as absent and the next write
overwrites it) and via a periodic cleanup task if one ever lands.

The hash binding is the safety net: same key + different payload is a
client bug (key reuse). Returning the cached response would be
*correct* idempotency for the original request but *wrong* for the
new one, so we 409 instead.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import Request

logger = logging.getLogger(__name__)

IDEMPOTENCY_TTL_HOURS = 24
IDEMPOTENCY_HEADER = "Idempotency-Key"


def extract_idempotency_key(
    request: Request, body_value: Optional[str]
) -> Optional[str]:
    """Read the key from the Idempotency-Key header first, then the
    body field as a fallback. Whitespace-only values become None so
    the cache layer can short-circuit."""
    header_value = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
    if header_value:
        return header_value
    if body_value:
        stripped = str(body_value).strip()
        if stripped:
            return stripped
    return None


def hash_payload(intent: str, payload: Dict[str, Any]) -> str:
    """SHA-256 over a canonical JSON serialisation of (intent, payload).

    canonical = sorted keys, no whitespace, ASCII. Same JSON in, same
    hash out, regardless of dict insertion order.
    """
    canonical = json.dumps(
        {"intent": intent, "input": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _get_db():
    """Indirection so tests can swap the db getter without importing
    ``solden.core.database`` (parallel pattern to
    ``solden.core.authorization._get_db``)."""
    from solden.core.database import get_db

    return get_db()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_iso() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=IDEMPOTENCY_TTL_HOURS)
    ).isoformat()


def lookup_cached_response(
    *,
    organization_id: str,
    idempotency_key: str,
    payload_hash: str,
) -> Dict[str, Any]:
    """Look up a cached response for ``(org, key)``.

    Returns a dict with one of three shapes:

    * ``{"status": "miss"}`` — no row, caller should execute.
    * ``{"status": "replay", "response": <dict>, "http_status": int}``
      — same hash, return the cached body verbatim.
    * ``{"status": "conflict"}`` — same key, different hash. Caller
      should return 409.
    """
    sql = (
        "SELECT payload_hash, response_json, http_status, expires_at "
        "FROM intent_responses "
        "WHERE organization_id = %s AND idempotency_key = %s"
    )
    db = _get_db()
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, idempotency_key))
            row = cur.fetchone()
    except Exception:
        # Cache lookup failure must never block the request. Log it
        # and fall through to execution — worst case the intent runs
        # again, which is what would have happened without the cache.
        logger.exception(
            "intent_responses lookup failed (org=%s key=%s)",
            organization_id,
            idempotency_key,
        )
        return {"status": "miss"}

    if row is None:
        return {"status": "miss"}

    row_dict = dict(row)
    # Expired rows are functionally absent. We don't delete them here
    # because that'd require a write on a read path; a periodic job
    # or the next write to the same key handles cleanup.
    expires_at = str(row_dict.get("expires_at") or "")
    if expires_at:
        try:
            if (
                datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                < datetime.now(timezone.utc)
            ):
                return {"status": "miss"}
        except (ValueError, TypeError):
            pass  # malformed timestamp → treat as not expired

    if str(row_dict.get("payload_hash") or "") != payload_hash:
        return {"status": "conflict"}

    try:
        response = json.loads(str(row_dict.get("response_json") or "{}"))
    except (ValueError, TypeError):
        return {"status": "miss"}

    return {
        "status": "replay",
        "response": response,
        "http_status": int(row_dict.get("http_status") or 200),
    }


def store_response(
    *,
    organization_id: str,
    idempotency_key: str,
    payload_hash: str,
    response: Dict[str, Any],
    http_status: int = 200,
) -> None:
    """Persist a response for future replay.

    Uses an UPSERT-style insert: a row for this ``(org, key)`` may
    already exist if a previous attempt's cleanup hasn't run yet. The
    payload_hash binding ensures we only ever upsert when the new
    payload matches; the caller (lookup → execute → store) only
    reaches here after a ``miss``, so the row is fresh.
    """
    sql = (
        "INSERT INTO intent_responses "
        "(organization_id, idempotency_key, payload_hash, response_json, "
        " http_status, ts, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (organization_id, idempotency_key) DO UPDATE SET "
        "  payload_hash = EXCLUDED.payload_hash, "
        "  response_json = EXCLUDED.response_json, "
        "  http_status = EXCLUDED.http_status, "
        "  ts = EXCLUDED.ts, "
        "  expires_at = EXCLUDED.expires_at"
    )
    db = _get_db()
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    organization_id,
                    idempotency_key,
                    payload_hash,
                    json.dumps(response),
                    http_status,
                    _now_iso(),
                    _expires_iso(),
                ),
            )
            conn.commit()
    except Exception:
        # Cache write failure is recoverable — the response went out,
        # the audit chain has the truth. Worst case, a retry runs the
        # intent again (which the audit_events.idempotency_key UNIQUE
        # constraint will still catch at the substrate layer).
        logger.exception(
            "intent_responses write failed (org=%s key=%s)",
            organization_id,
            idempotency_key,
        )
