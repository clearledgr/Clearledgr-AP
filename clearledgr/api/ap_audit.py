"""AP audit feed APIs for admin and operator surfaces."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from clearledgr.api.deps import verify_org_access
from clearledgr.core.auth import get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.ap_operator_audit import normalize_operator_audit_events


router = APIRouter(prefix="/api/ap", tags=["ap-audit"])


# Event types that indicate something went wrong. ``failures_only=true``
# uses this set to filter the feed to the rows CS actually needs when
# a customer calls in with "my invoice didn't work today."
_FAILURE_EVENT_TYPES = frozenset({
    "erp_post_failed",
    "state_transition_rejected",
    "approval_callback_rejected",
    "approval_nudge_failed",
    "approval_escalation_failed",
    "extraction_guardrail_failed",
    "validation_gate_failed",
    "webhook_delivery_failed",
    "agent_action:post_bill:failed",
    "agent_action:post_bill:aborted",
})


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


@router.get("/audit/recent")
def get_recent_ap_audit(
    organization_id: str = Query(default="default"),
    limit: int = Query(default=30, ge=1, le=500),
    since_ts: Optional[str] = Query(
        default=None,
        description="ISO 8601 timestamp. Only events at or after this time are returned.",
    ),
    event_type: Optional[str] = Query(
        default=None,
        description="Filter to a specific audit event_type. Multiple via comma-separated values.",
    ),
    failures_only: bool = Query(
        default=False,
        description="If true, return only events that indicate a failure.",
    ),
    _user: Any = Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, _user)
    db = get_db()

    # §13 Agent Activity feed retention — Starter 30 days, Pro/Enterprise
    # 7 years. audit_events is architecturally append-only (§7.6), so
    # tier retention is enforced here as a query-time filter rather
    # than a delete-reaper. Internal ops / audit export paths call
    # list_recent_ap_audit_events directly (no retention arg) to get
    # the full record when legally required.
    retention_days = None
    try:
        from clearledgr.services.subscription import get_subscription_service
        sub = get_subscription_service().get_subscription(organization_id)
        if sub.limits is not None:
            retention_days = int(
                getattr(sub.limits, "agent_activity_retention_days", 0) or 0
            )
    except Exception:
        pass  # Fail-open: customer sees the full feed if subscription lookup fails

    # Pull enough to satisfy the requested limit AFTER post-filters. The
    # tier retention filter runs in SQL; event_type / since_ts /
    # failures_only are Python-side so we over-fetch to compensate.
    filters_active = bool(since_ts or event_type or failures_only)
    fetch_limit = min(500, limit * 5) if filters_active else limit

    events = db.list_recent_ap_audit_events_with_retention(
        organization_id=organization_id,
        limit=fetch_limit,
        retention_days=retention_days,
    )

    # Post-filter: since_ts.
    since_dt = _parse_iso(since_ts) if since_ts else None
    if since_dt is not None:
        events = [
            e for e in events
            if (_parse_iso(e.get("ts")) or datetime.min.replace(tzinfo=timezone.utc)) >= since_dt
        ]

    # Post-filter: event_type (comma-separated allowed).
    if event_type:
        wanted = {t.strip() for t in event_type.split(",") if t.strip()}
        if wanted:
            events = [e for e in events if (e.get("event_type") or "") in wanted]

    # Post-filter: failures_only.
    if failures_only:
        events = [
            e for e in events
            if str(e.get("event_type") or "") in _FAILURE_EVENT_TYPES
            or str(e.get("event_type") or "").endswith((":failed", ":aborted"))
        ]

    events = events[:limit]

    return {
        "organization_id": organization_id,
        "events": normalize_operator_audit_events(events),
        "retention_days": retention_days,
        "filters": {
            "since_ts": since_ts,
            "event_type": event_type,
            "failures_only": failures_only,
        },
        "count": len(events),
    }

