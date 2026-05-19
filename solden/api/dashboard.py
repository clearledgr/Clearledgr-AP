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

from solden.core.auth import TokenData, get_current_user
from solden.core.database import get_db
from solden.services import approver_workload

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


@router.get("/recent-activity")
def get_recent_activity(
    user: TokenData = Depends(get_current_user),
    limit: int = 20,
) -> Dict[str, Any]:
    """Recent agent / operator actions for the workspace Home
    activity ribbon — the hero element of the coordination-layer
    control center.

    Pulls last N audit events for the org, filters to high-signal
    types (completed agent actions + operator actions + key state
    transitions; drops governance pre-checks and similar noise),
    normalises to a compact shape the SPA renders inline:

        { ts, action, subject, surface, tone, box_id, actor_type,
          actor_label }

    Newest first. Latency budget < 200ms — read-only, no joins
    beyond the audit table.
    """
    db = get_db()
    safe_limit = max(1, min(int(limit or 20), 100))
    rows = []
    if hasattr(db, "list_audit_events"):
        try:
            # Pull a wider slice than we'll return so filtering still
            # leaves enough high-signal rows.
            rows = db.list_audit_events(
                user.organization_id,
                limit=safe_limit * 6,
            ) or []
        except Exception as exc:
            logger.warning("[recent-activity] audit fetch failed: %s", exc)

    items = _shape_activity_events(rows, safe_limit)
    return {
        "organization_id": user.organization_id,
        "items": items,
        "count": len(items),
    }


# High-signal event-type prefixes — anything else gets dropped from
# the ribbon. Keeps the feed readable: governance pre-checks, lock
# attempts, debug pings don't belong on the leader's home view.
_RIBBON_KEEP_PREFIXES = (
    "agent_action:",       # agent action lifecycle (we filter to :completed below)
    "operator_action:",    # human action via UI
    "state_transition",    # AP state machine moves
    "ap_item:",            # AP-specific lifecycle events
    "vendor_inquiry:",     # vendor reply handling
    "label_changed",       # Gmail-label-driven intents
    "post_to_erp",         # explicit ERP post events
    "bank_match:",         # bank reconciliation closing leg
)

# Drop these explicitly even when the prefix matches.
_RIBBON_DROP_EXACT = frozenset({
    "agent_action:precheck",
    "governance:precheck:passed",
    "governance:precheck:failed",
})


def _shape_activity_events(
    rows: list, limit: int,
) -> list:
    """Filter + shape audit rows into ribbon items. Returns at most
    ``limit`` items, newest first."""
    out = []
    for row in rows:
        event_type = str(row.get("event_type") or "").strip()
        if not event_type or event_type in _RIBBON_DROP_EXACT:
            continue
        if not any(event_type.startswith(p) or event_type == p for p in _RIBBON_KEEP_PREFIXES):
            continue
        # Agent action lifecycle: only show :completed; skip :started
        # so each action is one ribbon row, not three.
        if event_type.startswith("agent_action:") and not event_type.endswith(":completed"):
            continue

        item = _format_ribbon_item(row)
        if item is None:
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _format_ribbon_item(row: Dict[str, Any]) -> Dict[str, Any]:
    """Compact ribbon shape derived from one audit row."""
    event_type = str(row.get("event_type") or "")
    payload = row.get("payload_json") or row.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = _json.loads(payload)
        except Exception:
            payload = {}

    actor_type = str(row.get("actor_type") or "agent").lower()
    actor_id = row.get("actor_id") or ""
    actor_label = {
        "agent":    "Agent",
        "operator": "Operator",
        "user":     "Operator",
        "system":   "System",
        "webhook":  "ERP",
        "auto":     "Agent",
    }.get(actor_type, actor_type.title() if actor_type else "Agent")

    # Verb / subject derivation. We deliberately keep this tight so
    # the ribbon scans like Vercel / Linear: short past-tense verb,
    # one-line subject, surface tag.
    new_state = str(row.get("new_state") or "").lower()
    prev_state = str(row.get("prev_state") or "").lower()
    box_id = row.get("box_id") or row.get("ap_item_id") or payload.get("ap_item_id") or ""

    vendor = (
        payload.get("vendor_name")
        or payload.get("vendor")
        or (row.get("box_summary") or {}).get("vendor_name")
        or ""
    )
    invoice_no = (
        payload.get("invoice_number")
        or payload.get("invoice_no")
        or (row.get("box_summary") or {}).get("invoice_number")
        or ""
    )

    subject_bits = []
    if invoice_no:
        subject_bits.append(f"#{invoice_no}")
    elif box_id:
        subject_bits.append(str(box_id))
    if vendor:
        subject_bits.append(f"from {vendor}")
    subject = " ".join(subject_bits).strip() or "AP item"

    action_label, tone = _action_label_and_tone(event_type, prev_state, new_state)

    surface = (
        payload.get("surface")
        or payload.get("source")
        or row.get("source")
        or _surface_from_event_type(event_type)
        or "agent"
    )

    return {
        "id": row.get("id") or row.get("event_id"),
        "ts": row.get("ts") or row.get("created_at") or row.get("recorded_at"),
        "action": action_label,
        "subject": subject,
        "surface": str(surface).lower(),
        "tone": tone,
        "box_id": box_id or None,
        "actor_type": actor_type,
        "actor_label": actor_label,
        "event_type": event_type,
    }


def _action_label_and_tone(
    event_type: str, prev_state: str, new_state: str,
) -> tuple[str, str]:
    """Map (event_type, transition) → (verb, tone) for the ribbon."""
    et = event_type.lower()
    # State-transition driven labels first — these are the most legible.
    transitions = {
        "approved":       ("Approved",        "success"),
        "ready_to_post":  ("Ready to post",   "info"),
        "posted_to_erp":  ("Posted to ERP",   "success"),
        "paid":           ("Paid",            "success"),
        "closed":         ("Closed",          "success"),
        "needs_info":     ("Asked for info",  "warning"),
        "needs_approval": ("Sent for approval", "info"),
        "needs_second_approval": ("Sent for second approval", "info"),
        "rejected":       ("Rejected",        "error"),
        "failed_post":    ("Post failed",     "error"),
        "snoozed":        ("Snoozed",         "info"),
        "reversed":       ("Reversed",        "warning"),
        "validated":      ("Validated",       "info"),
    }
    if new_state and new_state in transitions and new_state != prev_state:
        return transitions[new_state]

    # Event-type fallbacks for non-state events.
    if et.startswith("bank_match:matched") or et == "bank_match:reconciled":
        return ("Reconciled to bank", "success")
    if et.startswith("bank_match:ambiguous"):
        return ("Bank match needs review", "warning")
    if et.startswith("vendor_inquiry"):
        return ("Replied to vendor", "info")
    if et.startswith("label_changed"):
        return ("Gmail label changed", "info")
    if et.endswith(":completed"):
        return ("Action completed", "success")
    if et.endswith(":failed"):
        return ("Action failed", "error")
    if et.endswith(":paused"):
        return ("Action paused", "warning")

    # Fallback: humanise the event type itself.
    base = et.split(":")[-1] or "event"
    return (base.replace("_", " ").capitalize(), "info")


def _surface_from_event_type(event_type: str) -> str:
    """Best-effort surface inference when the payload doesn't carry one."""
    et = event_type.lower()
    if "slack" in et:    return "slack"
    if "teams" in et:    return "teams"
    if "gmail" in et:    return "gmail"
    if "netsuite" in et: return "netsuite"
    if "sap" in et:      return "sap"
    if "xero" in et:     return "xero"
    if "quickbooks" in et: return "quickbooks"
    return ""


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
      { "type": "activity", "data": {...recent_activity...} }
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
        from solden.api.workspace_shell import _safe_dashboard_stats

        last_stats_serialized = ""
        last_workload_serialized = ""
        last_activity_serialized = ""
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

            # 3. Recent agent activity (control-center hero ribbon).
            #    Same diff-on-emit pattern: only push on change so an
            #    idle workspace stays quiet.
            try:
                db = get_db()
                audit_rows = []
                if hasattr(db, "list_audit_events"):
                    audit_rows = db.list_audit_events(org_id, limit=120) or []
                activity_items = _shape_activity_events(audit_rows, 20)
                activity_payload = {
                    "organization_id": org_id,
                    "items": activity_items,
                    "count": len(activity_items),
                }
            except Exception as exc:
                logger.debug("[dashboard.stream] activity fetch failed: %s", exc)
                activity_payload = {"organization_id": org_id, "items": [], "count": 0}
            activity_serialized = _json.dumps(activity_payload, sort_keys=True, default=str)
            if activity_serialized != last_activity_serialized:
                yield _sse_message("activity", activity_payload)
                last_activity_serialized = activity_serialized

            # 4. Heartbeat every ~30s so reverse proxies don't reap
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
