"""Frontend performance telemetry — DESIGN_THESIS.md §4.07.

The thesis defines four pass/fail performance budgets for Clearledgr's
Gmail surfaces (sidebar, Kanban, Home, inbox labels). Before this
endpoint existed, the budgets were words in the spec — nothing
measured them once code reached the bundle. This endpoint accepts the
per-surface timing beacons the extension fires from
``ui/gmail-extension/src/utils/perf-budget.js`` and stores them in the
existing ``ap_sla_metrics`` table under the ``ui.*`` namespace so the
backend SLA tooling already in :mod:`clearledgr.core.sla_tracker`
becomes the single source of truth for both halves of the
performance picture.

The endpoint is intentionally unauthenticated and rate-limit-tolerant:

  - The beacon payload contains no sensitive data (surface name +
    latency + optional org id hint). Requiring auth would block
    ``navigator.sendBeacon`` on page unload, which is the canonical
    transport for "report this metric as the user navigates away"
    telemetry.
  - Rate-limiting is applied upstream at the reverse proxy layer;
    the extension itself only fires on first-commit of each surface
    per session, so steady-state volume is at most a few beacons per
    user per navigation. A runaway client cannot pollute the table
    faster than the reverse-proxy rate limit allows.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui", tags=["ui-perf"])


class PerfBeacon(BaseModel):
    surface: str = Field(..., min_length=1, max_length=64)
    latency_ms: int = Field(..., ge=0, le=600_000)
    budget_ms: int = Field(default=0, ge=0, le=600_000)
    breached: bool = Field(default=False)
    context: Optional[Dict[str, Any]] = None


# Thesis-named surfaces only. A freeform field would let a malicious
# client burn rows under garbage names; the fixed set means the table
# stays trivially queryable by surface.
_ALLOWED_SURFACES = frozenset({"sidebar", "kanban", "home", "inbox_labels"})


@router.post("/perf")
async def record_ui_perf(beacon: PerfBeacon, request: Request) -> Dict[str, Any]:
    """Record a frontend performance beacon.

    Always returns 200 — the extension fires as a fire-and-forget
    beacon and has no ability to react to 4xx/5xx responses from here.
    Invalid surface names are silently dropped with a debug log so the
    endpoint is resilient to bundle drift (an old extension version
    reporting a surface we renamed server-side does not crash the
    telemetry pipeline).
    """
    if beacon.surface not in _ALLOWED_SURFACES:
        logger.debug("[ui-perf] dropped unknown surface=%r", beacon.surface)
        return {"recorded": False, "reason": "unknown_surface"}

    step_name = f"ui.{beacon.surface}"
    context = beacon.context or {}
    org_id = str(context.get("org_id") or context.get("organization_id") or "default").strip() or "default"
    ap_item_id = str(context.get("ap_item_id") or "").strip() or None

    metric_id = f"UIP-{uuid.uuid4().hex[:12]}"
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        db = get_db()
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    "INSERT INTO ap_sla_metrics "
                    "(id, ap_item_id, organization_id, step_name, latency_ms, breached, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
                ),
                (
                    metric_id, ap_item_id, org_id, step_name,
                    int(beacon.latency_ms),
                    1 if beacon.breached else 0,
                    created_at,
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[ui-perf] insert failed (non-fatal): %s", exc)
        return {"recorded": False, "reason": "insert_failed"}

    if beacon.breached:
        logger.warning(
            "[ui-perf] %s BREACH %dms > budget %dms (org=%s)",
            step_name, beacon.latency_ms, beacon.budget_ms, org_id,
        )

    return {"recorded": True, "id": metric_id}
