"""AP audit feed APIs for admin and operator surfaces."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query

from clearledgr.api.deps import verify_org_access
from clearledgr.core.auth import get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.ap_operator_audit import normalize_operator_audit_events


router = APIRouter(prefix="/api/ap", tags=["ap-audit"])


@router.get("/audit/recent")
def get_recent_ap_audit(
    organization_id: str = Query(default="default"),
    limit: int = Query(default=30, ge=1, le=500),
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

    events = db.list_recent_ap_audit_events_with_retention(
        organization_id=organization_id,
        limit=limit,
        retention_days=retention_days,
    )
    return {
        "organization_id": organization_id,
        "events": normalize_operator_audit_events(events),
        "retention_days": retention_days,
    }

