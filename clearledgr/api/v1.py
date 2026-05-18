"""Clearledgr v1 core API routes — health, me, audit.

The shared baseline for the public ``/v1`` surface (the per-feature
routers ``v1_intents`` and ``v1_records`` register separately).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from clearledgr.api.v1_auth import AgentIdentity, require_agent_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["v1"])


@router.get("/health")
def health_check():
    """Unauthenticated liveness probe."""
    return {"status": "ok", "service": "clearledgr-core"}


@router.get("/me")
def whoami(agent: AgentIdentity = Depends(require_agent_key(None))):
    """Echo back the caller's resolved identity.

    Useful as a first call from any agent: it confirms the key
    authenticated, names which agent and organization it represents,
    and reports the scope set so the agent knows which subsequent
    calls will succeed.
    """
    return {
        "key_id": agent.key_id,
        "organization_id": agent.organization_id,
        "agent_id": agent.agent_id,
        "agent_version": agent.agent_version,
        "scopes": agent.scopes,  # None for legacy full-access keys
        "actor_label": agent.actor_label,
    }


@router.get("/audit")
def list_audit_events(
    request: Request,
    box_id: Optional[str] = Query(
        default=None,
        description="Filter to events about a specific Box id.",
    ),
    box_type: Optional[str] = Query(
        default=None,
        description="Filter to events about a specific Box type.",
    ),
    event_type: Optional[str] = Query(
        default=None,
        description="Filter to a specific event_type.",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    agent: AgentIdentity = Depends(require_agent_key("read:audit")),
):
    """Read the audit chain for the caller's organisation.

    Always pinned to the agent's org server-side; no parameter can
    widen the result beyond that boundary. Filters are AND-combined.
    """
    from clearledgr.core.database import get_db

    db = get_db()

    where = ["organization_id = %s"]
    params: List[Any] = [agent.organization_id]
    if box_id:
        where.append("box_id = %s")
        params.append(box_id)
    if box_type:
        where.append("box_type = %s")
        params.append(box_type)
    if event_type:
        where.append("event_type = %s")
        params.append(event_type)

    sql = (
        "SELECT id, box_id, box_type, event_type, prev_state, new_state, "
        "actor_type, actor_id, agent_version, source, organization_id, "
        "decision_reason, governance_verdict, agent_confidence, ts, "
        "policy_version, payload_json "
        "FROM audit_events "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY ts DESC, id DESC LIMIT %s"
    )
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params) + (limit,))
            rows = [dict(r) for r in (cur.fetchall() or [])]
    except Exception:
        logger.exception("/v1/audit query failure")
        rid = getattr(request.state, "correlation_id", None)
        body: Dict[str, Any] = {
            "error_code": "internal_error",
            "message": "internal_error",
        }
        if rid:
            body["request_id"] = rid
        return JSONResponse(status_code=500, content=body)

    return {"events": rows, "count": len(rows)}
