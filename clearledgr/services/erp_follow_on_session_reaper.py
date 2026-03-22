"""Timeout stale ERP follow-on browser fallback sessions."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from clearledgr.core.database import ClearledgrDB, get_db

logger = logging.getLogger(__name__)

_ACTIVE_SESSION_STATES = ("running", "blocked_for_approval")
_WORKFLOW_MACROS = {
    "erp_credit_application_fallback": "apply_credit_note_in_erp",
    "erp_settlement_application_fallback": "apply_settlement_in_erp",
}
_REAPER_ACTOR_ID = "erp_follow_on_timeout_reaper"


def _parse_json_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fallback_ttl_seconds() -> int:
    raw = os.getenv("ERP_FOLLOW_ON_BROWSER_FALLBACK_TTL_SECONDS", "14400")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 14400
    return max(300, min(value, 604800))


def reap_stale_erp_follow_on_sessions(
    db: Optional[ClearledgrDB] = None,
    *,
    organization_id: str = "default",
    now: Optional[datetime] = None,
    ttl_seconds: Optional[int] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    from clearledgr.services.erp_api_first import reconcile_browser_fallback_completion

    resolved_db = db or get_db()
    if now is None:
        now_utc = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now_utc = now.replace(tzinfo=timezone.utc)
    else:
        now_utc = now.astimezone(timezone.utc)
    ttl = max(300, int(ttl_seconds if ttl_seconds is not None else _fallback_ttl_seconds()))
    cutoff = now_utc - timedelta(seconds=ttl)

    summary = {
        "checked": 0,
        "stale": 0,
        "timed_out": 0,
        "errors": 0,
    }
    sessions = resolved_db.list_agent_sessions(
        organization_id=organization_id,
        states=list(_ACTIVE_SESSION_STATES),
        limit=limit,
    )

    for session in sessions:
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            continue
        metadata = _parse_json_dict(session.get("metadata"))
        workflow_id = str(metadata.get("workflow_id") or "").strip().lower()
        macro_name = _WORKFLOW_MACROS.get(workflow_id)
        if not macro_name:
            continue

        summary["checked"] += 1
        existing_completion = metadata.get("fallback_completion")
        if isinstance(existing_completion, dict) and existing_completion.get("completed_at"):
            continue

        dispatched_at = (
            _parse_iso(metadata.get("dispatched_at"))
            or _parse_iso(session.get("updated_at"))
            or _parse_iso(session.get("created_at"))
        )
        if dispatched_at is None or dispatched_at > cutoff:
            continue

        summary["stale"] += 1
        try:
            correlation_id = str(metadata.get("correlation_id") or "").strip() or None
            timed_out_at = now_utc.isoformat()
            completion = reconcile_browser_fallback_completion(
                session_id=session_id,
                macro_name=macro_name,
                status="failed",
                actor_id=_REAPER_ACTOR_ID,
                error_code="browser_fallback_timed_out",
                error_message_redacted="Browser fallback session timed out before completion",
                idempotency_key=f"browser_fallback_timeout_reaper:{session_id}",
                correlation_id=correlation_id,
                db=resolved_db,
            )

            refreshed_session = resolved_db.get_agent_session(session_id) or session
            refreshed_metadata = _parse_json_dict(refreshed_session.get("metadata"))
            refreshed_metadata["timeout_reaper"] = {
                "timed_out_at": timed_out_at,
                "timed_out_by": _REAPER_ACTOR_ID,
                "workflow_id": workflow_id,
                "macro_name": macro_name,
                "ttl_seconds": ttl,
                "dispatched_at": dispatched_at.isoformat(),
            }
            resolved_db.update_agent_session(
                session_id,
                state="timed_out",
                metadata=refreshed_metadata,
            )
            resolved_db.append_ap_audit_event(
                {
                    "ap_item_id": str(completion.get("ap_item_id") or session.get("ap_item_id") or "").strip(),
                    "event_type": "erp_follow_on_browser_fallback_timed_out",
                    "actor_type": "system",
                    "actor_id": _REAPER_ACTOR_ID,
                    "organization_id": organization_id,
                    "source": "erp_follow_on_timeout_reaper",
                    "reason": "browser_fallback_timed_out",
                    "metadata": {
                        "session_id": session_id,
                        "workflow_id": workflow_id,
                        "macro_name": macro_name,
                        "ttl_seconds": ttl,
                        "dispatched_at": dispatched_at.isoformat(),
                        "timed_out_at": timed_out_at,
                    },
                    "idempotency_key": f"erp_follow_on_browser_fallback_timed_out:{session_id}",
                    "correlation_id": correlation_id,
                }
            )
            summary["timed_out"] += 1
        except Exception:
            logger.exception("erp_follow_on_timeout_reaper: failed to timeout session=%s", session_id)
            summary["errors"] += 1

    if summary["timed_out"]:
        logger.warning(
            "erp_follow_on_timeout_reaper: timed out %d stale session(s) out of %d checked",
            summary["timed_out"],
            summary["checked"],
        )
    else:
        logger.debug(
            "erp_follow_on_timeout_reaper: checked %d session(s), no stale follow-on fallbacks",
            summary["checked"],
        )
    return summary


async def run_erp_follow_on_session_reaper(
    organization_id: str = "default",
) -> Dict[str, Any]:
    return reap_stale_erp_follow_on_sessions(organization_id=organization_id)
