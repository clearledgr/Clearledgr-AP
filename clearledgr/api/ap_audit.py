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
    events = db.list_recent_ap_audit_events(organization_id=organization_id, limit=limit)
    return {
        "organization_id": organization_id,
        "events": normalize_operator_audit_events(events),
    }

