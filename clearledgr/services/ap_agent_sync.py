"""Shared canonical sync helpers for AP execution surfaces outside the runtime."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from clearledgr.core.database import ClearledgrDB
from clearledgr.services.agent_memory import get_agent_memory_service
from clearledgr.services.finance_learning import get_finance_learning_service

logger = logging.getLogger(__name__)


def build_agent_memory_projection(
    *,
    db: Optional[ClearledgrDB],
    organization_id: str,
    ap_item_id: Optional[str],
    skill_id: str = "ap_v1",
) -> Dict[str, Any]:
    resolved_item_id = str(ap_item_id or "").strip()
    if not resolved_item_id or db is None:
        return {}
    try:
        return get_agent_memory_service(organization_id, db=db).build_surface(
            ap_item_id=resolved_item_id,
            skill_id=skill_id,
        )
    except Exception as exc:
        logger.warning("Could not build agent memory projection for %s: %s", resolved_item_id, exc)
        return {}


def sync_ap_execution_event(
    *,
    db: Optional[ClearledgrDB],
    organization_id: str,
    ap_item_id: Optional[str],
    event_type: str,
    reason: str,
    response: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    actor_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    skill_id: str = "ap_v1",
    source: str = "ap_agent_sync",
    update_belief: bool = True,
    record_learning: bool = True,
) -> Dict[str, Any]:
    resolved_item_id = str(ap_item_id or "").strip()
    if not resolved_item_id or db is None:
        return {}

    item = {}
    if hasattr(db, "get_ap_item"):
        try:
            item = db.get_ap_item(resolved_item_id) or {}
        except Exception:
            item = {}

    payload = dict(response or {})
    payload_status = str(payload.get("status") or "").strip().lower()
    if item:
        if not payload_status or payload_status in {"success", "completed", "ok", "error", "failed"}:
            payload["status"] = item.get("state")
        payload.setdefault("ap_item_state", item.get("state"))
        payload.setdefault("ap_item_id", resolved_item_id)
        payload.setdefault("email_id", item.get("thread_id"))
        payload.setdefault("currency", item.get("currency"))
    if correlation_id and "correlation_id" not in payload:
        payload["correlation_id"] = correlation_id

    results: Dict[str, Any] = {}
    try:
        memory = get_agent_memory_service(organization_id, db=db)
        if update_belief:
            results["memory"] = memory.record_outcome(
                skill_id=skill_id,
                ap_item=item,
                ap_item_id=resolved_item_id,
                event_type=event_type,
                reason=reason,
                response=payload,
                actor_id=actor_id,
                source=source,
                correlation_id=correlation_id,
            )
        else:
            results["memory"] = memory.observe_event(
                skill_id=skill_id,
                ap_item_id=resolved_item_id,
                thread_id=str((item or {}).get("thread_id") or payload.get("email_id") or "").strip() or None,
                event_type=event_type,
                payload=payload,
                actor_id=actor_id,
                correlation_id=correlation_id,
                source=source,
                summary=reason,
                channel=source,
            )
    except Exception as exc:
        logger.warning("Agent memory sync failed for %s/%s: %s", resolved_item_id, event_type, exc)

    if record_learning:
        try:
            results["learning"] = get_finance_learning_service(organization_id, db=db).record_action_outcome(
                event_type=event_type,
                ap_item=item,
                response=payload,
                actor_id=actor_id,
                metadata={
                    **dict(metadata or {}),
                    "reason": reason,
                    "correlation_id": correlation_id,
                    "skill_id": skill_id,
                    "ap_item_id": resolved_item_id,
                },
            )
        except Exception as exc:
            logger.warning("Finance learning sync failed for %s/%s: %s", resolved_item_id, event_type, exc)

    return results
