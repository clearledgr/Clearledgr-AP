"""Audit events API contract for AP v1."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from clearledgr.core.database import get_db


router = APIRouter(tags=["audit"])


class AuditEventRequest(BaseModel):
    org_id: str
    ap_item_id: str
    actor_type: str
    actor_id: str
    event_type: str
    prev_state: Optional[str] = None
    new_state: Optional[str] = None
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    external_refs: Optional[Dict[str, Any]] = None
    payload_json: Dict[str, Any] = {}
    idempotency_key: Optional[str] = None


@router.post("/api/audit/events")
async def post_audit_event(request: AuditEventRequest):
    db = get_db()
    item = db.get_ap_item(request.ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")

    refs = dict(request.external_refs or {})
    if request.message_id and "gmail_message_id" not in refs:
        refs["gmail_message_id"] = request.message_id
    if request.thread_id and "gmail_thread_id" not in refs:
        refs["gmail_thread_id"] = request.thread_id

    event = db.append_ap_audit_event(
        {
            "ap_item_id": request.ap_item_id,
            "event_type": request.event_type,
            "from_state": request.prev_state,
            "to_state": request.new_state,
            "actor_type": request.actor_type,
            "actor_id": request.actor_id,
            "payload_json": request.payload_json,
            "external_refs": refs,
            "idempotency_key": request.idempotency_key,
            "organization_id": request.org_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    return {"event": event}
