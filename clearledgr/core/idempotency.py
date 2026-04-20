"""HTTP-endpoint idempotency helper.

Stripe-style: the client sends an Idempotency-Key (header preferred,
body field fallback) on POST requests that mutate state or trigger
external side effects. The first call persists the response; retries
with the same key return the cached response without re-executing.

Storage: piggybacks on ``audit_events.idempotency_key`` (UNIQUE).
No new table required. Each replay row is itself an audit event with
``event_type='api_idempotent_response'`` and the original response
serialized into ``payload_json.response``.

The DB-layer ``append_audit_event`` already deduplicates concurrent
writers via the UNIQUE constraint, so this helper inherits crash/race
safety for free.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAY_EVENT_TYPE = "api_idempotent_response"


def resolve_idempotency_key(
    header_value: Optional[str],
    body_value: Optional[str] = None,
) -> Optional[str]:
    """Header wins over body. Empty/whitespace returns None."""
    h = (header_value or "").strip()
    if h:
        return h
    b = (body_value or "").strip()
    return b or None


def load_idempotent_response(
    db: Any,
    idempotency_key: Optional[str],
) -> Optional[dict]:
    """Return prior cached response for this key, else None."""
    if not idempotency_key:
        return None
    try:
        existing = db.get_ap_audit_event_by_key(idempotency_key)
    except Exception:
        logger.exception("idempotency lookup failed for key=%s", idempotency_key[:16])
        return None
    if not isinstance(existing, dict):
        return None
    payload = existing.get("payload_json")
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    replay = dict(response)
    replay.setdefault("audit_event_id", existing.get("id"))
    replay["idempotency_replayed"] = True
    return replay


def save_idempotent_response(
    db: Any,
    idempotency_key: Optional[str],
    response: Any,
    *,
    box_id: Optional[str] = None,
    box_type: Optional[str] = None,
    organization_id: Optional[str] = None,
    actor_id: str = "api",
) -> None:
    """Persist response under the key. No-op if key is missing.

    Falls back to a synthetic ``api_action`` box when no Box context
    is available (e.g., bulk endpoints that operate on N items). The
    synthetic box_id is derived from the key prefix so it stays
    stable across retries.
    """
    if not idempotency_key:
        return
    if not isinstance(response, dict):
        response = {"value": response}

    if box_id and box_type:
        resolved_box_id = box_id
        resolved_box_type = box_type
    else:
        resolved_box_id = f"api:{idempotency_key[:32]}"
        resolved_box_type = "api_action"

    payload = {
        "event_type": REPLAY_EVENT_TYPE,
        "actor_type": "api",
        "actor_id": actor_id,
        "box_id": resolved_box_id,
        "box_type": resolved_box_type,
        "idempotency_key": idempotency_key,
        "organization_id": organization_id,
        "payload_json": {"response": dict(response)},
    }
    try:
        db.append_audit_event(payload)
    except Exception:
        logger.exception(
            "idempotency persist failed for key=%s box=%s/%s",
            idempotency_key[:16], resolved_box_type, resolved_box_id[:16],
        )
