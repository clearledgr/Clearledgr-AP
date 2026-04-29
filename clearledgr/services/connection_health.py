"""Per-integration connection-health derivation (Module 5 Pass B).

Connection health is *derived state* — there is no health table. The
view assembles three sources:

  1. ``organization_integrations`` — tenant's per-integration row
     (``last_sync_at``, ``status``, ``mode``).
  2. ``audit_events`` — the agent's append-only event stream.
     Counts + latest error are aggregated server-side over a
     configurable window.
  3. ``webhook_deliveries`` — outgoing webhook attempt status counts.

The derivation classifies each integration as ``healthy`` / ``degraded`` /
``down`` / ``not_configured`` using a deliberately small heuristic (see
``_classify_status``). A more elaborate per-integration scoring system
is post-GA scope; the dashboard goal is "leader sees the breakage
within 10 minutes" (per ``Clearledgr_Workspace_Scope_GA.md`` §Module 5
acceptance criteria), which a simple chip + last-error snippet
already satisfies.

This module is intentionally a thin orchestrator over store-level
queries. The SQL aggregate lives in
``clearledgr.core.stores.ap_store.get_connection_health_aggregates`` so
tests can hit it without going through HTTP.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Each integration the dashboard surfaces. ``integration_type`` matches
# the ``organization_integrations.integration_type`` column. ``kind``
# is the bucket the audit-event aggregator emits; ``label`` is the
# display name.
INTEGRATION_DESCRIPTORS = (
    {"integration_type": "gmail", "kind": "gmail", "label": "Gmail"},
    {"integration_type": "slack", "kind": "slack", "label": "Slack"},
    {"integration_type": "teams", "kind": "teams", "label": "Microsoft Teams"},
    {"integration_type": "erp", "kind": "erp", "label": "ERP"},
)


def _hours_since(iso_ts: Optional[str]) -> Optional[float]:
    """Return hours elapsed since ``iso_ts`` (UTC). None if unparseable."""
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        return max(0.0, delta.total_seconds() / 3600.0)
    except Exception:
        return None


def _classify_status(
    *,
    integration_row: Optional[Dict[str, Any]],
    aggregates: Dict[str, Any],
    window_hours: int,
) -> str:
    """Return ``healthy`` | ``degraded`` | ``down`` | ``not_configured``.

    Heuristic (deliberately simple — see module docstring for why):
      * No integration row at all → not_configured.
      * Integration row says ``status='disconnected'`` → not_configured.
      * Integration row says ``status='connected'`` but the last
        sync is older than the full window with zero events → down.
        (A connected integration with no traffic for 24h is broken.)
      * 5+ errors in window → down.
      * 1-4 errors in window → degraded.
      * Otherwise → healthy.
    """
    if not integration_row:
        return "not_configured"

    raw_status = str(integration_row.get("status") or "").strip().lower()
    if raw_status in {"disconnected", "not_connected", "uninstalled"}:
        return "not_configured"

    errors = int(aggregates.get("errors") or 0)
    events = int(aggregates.get("events") or 0)
    last_event_hours = _hours_since(aggregates.get("latest_event_at"))
    last_sync_hours = _hours_since(integration_row.get("last_sync_at"))

    if errors >= 5:
        return "down"

    # Stale-but-connected check: integration says it's connected, but
    # we've seen zero events in the window AND the last sync stamp is
    # older than the window.
    if (
        raw_status == "connected"
        and events == 0
        and (last_sync_hours is None or last_sync_hours >= window_hours)
        and (last_event_hours is None or last_event_hours >= window_hours)
    ):
        return "down"

    if errors > 0:
        return "degraded"

    return "healthy"


def build_connection_health(db, organization_id: str, *, window_hours: int = 24) -> Dict[str, Any]:
    """Assemble the full connection-health response for one tenant.

    Returns a dict ready to JSON-serialise; see
    ``clearledgr/api/workspace_shell.get_connection_health`` for the
    HTTP shape.
    """
    aggregates = db.get_connection_health_aggregates(
        organization_id=organization_id,
        window_hours=window_hours,
    )
    by_kind = aggregates.get("by_kind") or {}
    latest_error_by_kind = aggregates.get("latest_error_by_kind") or {}

    # Pre-fetch every integration row in one shot to avoid N round
    # trips. ``list_organization_integrations`` is keyed by org so it's
    # already index-served.
    integration_rows: List[Dict[str, Any]] = []
    if hasattr(db, "list_organization_integrations"):
        try:
            integration_rows = list(db.list_organization_integrations(organization_id) or [])
        except Exception as exc:
            logger.warning(
                "[connection_health] list_organization_integrations failed for org=%s: %s",
                organization_id, exc,
            )
            integration_rows = []
    by_type = {str(r.get("integration_type") or ""): r for r in integration_rows}

    integrations: List[Dict[str, Any]] = []
    for desc in INTEGRATION_DESCRIPTORS:
        kind = desc["kind"]
        kind_aggs = by_kind.get(kind) or {"events": 0, "errors": 0, "latest_event_at": None}
        row = by_type.get(desc["integration_type"])
        latest_error = latest_error_by_kind.get(kind)

        # Trim the latest-error payload to a UI-friendly summary —
        # we surface the timestamp + event_type + a short message
        # extracted from the payload, not the full audit row.
        error_summary = None
        if latest_error:
            payload = latest_error.get("payload_json") or {}
            message = None
            if isinstance(payload, dict):
                message = (
                    payload.get("error")
                    or payload.get("error_message")
                    or payload.get("reason")
                    or payload.get("detail")
                )
                if isinstance(message, dict):
                    message = (
                        message.get("message")
                        or message.get("reason")
                        or str(message)[:200]
                    )
            error_summary = {
                "ts": latest_error.get("ts"),
                "event_type": latest_error.get("event_type"),
                "message": str(message)[:300] if message else None,
            }

        integrations.append({
            "integration_type": desc["integration_type"],
            "label": desc["label"],
            "status": _classify_status(
                integration_row=row,
                aggregates=kind_aggs,
                window_hours=window_hours,
            ),
            "raw_status": (row or {}).get("status"),
            "last_sync_at": (row or {}).get("last_sync_at"),
            "events_24h": int(kind_aggs.get("events") or 0),
            "errors_24h": int(kind_aggs.get("errors") or 0),
            "latest_event_at": kind_aggs.get("latest_event_at"),
            "latest_error": error_summary,
        })

    return {
        "organization_id": organization_id,
        "window_hours": int(window_hours),
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "integrations": integrations,
        "webhooks": aggregates.get("webhooks") or {"delivered": 0, "failed": 0, "retrying": 0},
    }
