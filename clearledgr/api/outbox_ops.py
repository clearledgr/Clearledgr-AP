"""Ops endpoints for the transactional outbox (Gap 4).

Surface for engineers + customer-success to inspect, retry, skip,
and replay outbox events.

Endpoints:

* ``GET /api/ops/outbox`` — list events, filter by status / event_type
* ``GET /api/ops/outbox/{event_id}`` — full row including error_log
* ``POST /api/ops/outbox/{event_id}/retry`` — force a dead/failed row
  back to pending
* ``POST /api/ops/outbox/{event_id}/skip`` — mark a stuck row as
  succeeded with metadata noting the manual skip
* ``POST /api/ops/outbox/replay`` — re-enqueue events matching a
  window. Useful for "we changed an observer; replay last 24h
  through the new logic."

Auth-gated through `get_current_user` + role check (ops / admin
only).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from clearledgr.api.deps import verify_org_access
from clearledgr.core.auth import get_current_user
from clearledgr.services.outbox import (
    OutboxEvent,
    list_events,
    replay_events,
    retry_event,
    skip_event,
)

router = APIRouter(prefix="/api/ops/outbox", tags=["ops-outbox"])


def _event_to_dict(e: OutboxEvent) -> Dict[str, Any]:
    return {
        "id": e.id,
        "organization_id": e.organization_id,
        "event_type": e.event_type,
        "target": e.target,
        "payload": e.payload,
        "dedupe_key": e.dedupe_key,
        "parent_event_id": e.parent_event_id,
        "status": e.status,
        "attempts": e.attempts,
        "max_attempts": e.max_attempts,
        "next_attempt_at": e.next_attempt_at,
        "last_attempted_at": e.last_attempted_at,
        "succeeded_at": e.succeeded_at,
        "error_log": e.error_log,
        "created_at": e.created_at,
        "updated_at": e.updated_at,
        "created_by": e.created_by,
    }


def _require_ops_access(user, organization_id: str) -> None:
    """Ops endpoints are restricted to admin/ops role within the org."""
    verify_org_access(organization_id, user)
    role = str(getattr(user, "role", "") or "").lower()
    if role not in {"admin", "ops", "owner"}:
        raise HTTPException(
            status_code=403,
            detail=f"role {role!r} cannot access outbox ops endpoints",
        )


# ─── Reads ──────────────────────────────────────────────────────────


@router.get("")
def list_outbox_events(
    organization_id: str = Query(default="default"),
    status: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    _require_ops_access(user, organization_id)
    try:
        events = list_events(
            organization_id=organization_id,
            status=status, event_type=event_type, limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    by_status: Dict[str, int] = {}
    for ev in events:
        by_status[ev.status] = by_status.get(ev.status, 0) + 1
    return {
        "organization_id": organization_id,
        "count": len(events),
        "by_status": by_status,
        "events": [_event_to_dict(e) for e in events],
    }


@router.get("/{event_id}")
def get_outbox_event(
    event_id: str,
    organization_id: str = Query(default="default"),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    _require_ops_access(user, organization_id)
    from clearledgr.services.outbox import _fetch_event_by_id
    event = _fetch_event_by_id(event_id)
    if event is None or event.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="event not found")
    return _event_to_dict(event)


# ─── Writes ─────────────────────────────────────────────────────────


@router.post("/{event_id}/retry")
def retry_outbox_event(
    event_id: str,
    organization_id: str = Query(default="default"),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    _require_ops_access(user, organization_id)
    actor = _actor_from_user(user)
    event = retry_event(event_id, actor=actor)
    if event is None:
        raise HTTPException(
            status_code=404,
            detail="event not found or not in failed/dead status",
        )
    return _event_to_dict(event)


class SkipRequest(BaseModel):
    reason: Optional[str] = ""


@router.post("/{event_id}/skip")
def skip_outbox_event(
    event_id: str,
    body: SkipRequest = Body(default_factory=SkipRequest),
    organization_id: str = Query(default="default"),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    _require_ops_access(user, organization_id)
    actor = _actor_from_user(user)
    event = skip_event(event_id, actor=actor, reason=body.reason or "")
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return _event_to_dict(event)


class ReplayRequest(BaseModel):
    organization_id: str
    event_type: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    limit: int = 200


@router.post("/replay")
def replay_outbox_events(
    body: ReplayRequest,
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    _require_ops_access(user, body.organization_id)
    actor = _actor_from_user(user)
    count = replay_events(
        organization_id=body.organization_id,
        event_type=body.event_type,
        since=body.since,
        until=body.until,
        limit=int(body.limit or 200),
        actor=actor,
    )
    return {
        "organization_id": body.organization_id,
        "events_replayed": count,
        "filter": {
            "event_type": body.event_type,
            "since": body.since,
            "until": body.until,
        },
    }


def _actor_from_user(user) -> str:
    if user is None:
        return "anonymous"
    email = str(getattr(user, "email", "") or "").strip()
    if email:
        return email
    user_id = str(getattr(user, "user_id", "") or getattr(user, "id", "") or "").strip()
    return user_id or "system"
