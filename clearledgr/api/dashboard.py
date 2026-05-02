"""Workspace dashboard read endpoints — Module 1 (Live Operations).

  GET /api/workspace/dashboard/approver-workload
  GET /api/workspace/dashboard/stream      (Server-Sent Events)

The Live Operations page anchors on a few aggregations that don't
fit cleanly into either the AP-item routes (per-record) or the
reports surface (multi-day rollups). Per-approver pending counts
fall here.

Module 1 spec line 92: "Stat cards refresh in real time as agent
acts (websocket or SSE, max 30s lag)." We use SSE — single direction
(server → client), works through any HTTP proxy that supports
chunked encoding (Railway's edge does), no socket-upgrade headache.
The stream re-computes dashboard_stats every 15s and emits the
delta when it changes; the SPA's HomePage consumes via EventSource
and merges into its existing state. 15s lag is well within the 30s
spec bound.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import Any, AsyncGenerator, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services import approver_workload

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspace/dashboard", tags=["dashboard"])


@router.get("/approver-workload")
def get_approver_workload(
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Per-approver pending counts + oldest-stuck age for the
    Live Operations approver-workload strip."""
    db = get_db()
    rows = approver_workload.get_approver_workload(db, user.organization_id)
    return {
        "organization_id": user.organization_id,
        "approvers": rows,
        "count": len(rows),
    }


# Server-Sent Events stream. The SPA opens this with EventSource()
# and gets a JSON message per "tick" (every 15s) plus an immediate
# first message so the page paints fast. Heartbeats keep the
# connection alive across proxy idle-timeouts.
_TICK_SECONDS = 15
_HEARTBEAT_SECONDS = 30


@router.get("/stream")
async def stream_dashboard(
    request: Request,
    user: TokenData = Depends(get_current_user),
) -> StreamingResponse:
    """SSE stream of dashboard_stats + approver workload updates.

    Each event is a JSON payload of the form:
      { "type": "stats", "data": {...dashboard_stats...} }
      { "type": "workload", "data": {...approver_workload...} }
      { "type": "heartbeat" }    // keepalive only

    The stream emits an immediate "stats" + "workload" snapshot on
    connect so the SPA can render with real data on first tick.
    Subsequent ticks emit only when the payload changes (cheap diff
    via JSON-stringify equality) so the network stays quiet on idle
    workspaces.
    """
    org_id = user.organization_id

    async def event_generator() -> AsyncGenerator[bytes, None]:
        # Lazy imports — avoids a circular at module load.
        from clearledgr.api.workspace_shell import _safe_dashboard_stats

        last_stats_serialized = ""
        last_workload_serialized = ""
        ticks_since_heartbeat = 0

        while True:
            if await request.is_disconnected():
                logger.debug("[dashboard.stream] client disconnected; closing for org=%s", org_id)
                return

            # 1. Refresh dashboard stats. Only emit when it changes.
            try:
                stats = _safe_dashboard_stats(org_id)
            except Exception as exc:
                logger.debug("[dashboard.stream] stats fetch failed: %s", exc)
                stats = {}
            stats_serialized = _json.dumps(stats, sort_keys=True, default=str)
            if stats_serialized != last_stats_serialized:
                yield _sse_message("stats", stats)
                last_stats_serialized = stats_serialized

            # 2. Refresh approver workload. Same diff-on-emit pattern.
            try:
                db = get_db()
                rows = approver_workload.get_approver_workload(db, org_id)
                workload_payload = {"organization_id": org_id, "approvers": rows, "count": len(rows)}
            except Exception as exc:
                logger.debug("[dashboard.stream] workload fetch failed: %s", exc)
                workload_payload = {"organization_id": org_id, "approvers": [], "count": 0}
            workload_serialized = _json.dumps(workload_payload, sort_keys=True, default=str)
            if workload_serialized != last_workload_serialized:
                yield _sse_message("workload", workload_payload)
                last_workload_serialized = workload_serialized

            # 3. Heartbeat every ~30s so reverse proxies don't reap
            #    a quiet connection. SSE comments (lines starting
            #    with `:`) are ignored by EventSource clients but
            #    keep the TCP byte-stream warm.
            ticks_since_heartbeat += 1
            if ticks_since_heartbeat * _TICK_SECONDS >= _HEARTBEAT_SECONDS:
                yield b": heartbeat\n\n"
                ticks_since_heartbeat = 0

            # asyncio.sleep is cancellation-aware; if the connection
            # closes mid-tick we exit cleanly on the next loop check.
            try:
                await asyncio.sleep(_TICK_SECONDS)
            except asyncio.CancelledError:
                return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable proxy buffering (Nginx hint)
            "Connection": "keep-alive",
        },
    )


def _sse_message(event_type: str, data: Dict[str, Any]) -> bytes:
    """Serialise a Server-Sent Events frame.

    Format (per https://html.spec.whatwg.org/multipage/server-sent-events.html):
      data: <json>\n\n
    Multiple lines are concatenated with \n; we keep the JSON on
    one line so EventSource's default ``data`` accumulation works
    without splitting.
    """
    payload = _json.dumps({"type": event_type, "data": data}, default=str)
    return f"data: {payload}\n\n".encode("utf-8")
