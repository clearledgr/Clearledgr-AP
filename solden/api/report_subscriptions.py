"""Report subscription API — Module 8 scheduled email delivery.

CRUD over ``report_subscriptions``. Every endpoint is org-scoped via
``get_current_user``; cross-tenant reads/writes are blocked at the
store layer (delete + update both check organization_id).

  POST   /api/workspace/reports/subscriptions
  GET    /api/workspace/reports/subscriptions
  GET    /api/workspace/reports/subscriptions/{id}
  PATCH  /api/workspace/reports/subscriptions/{id}
  DELETE /api/workspace/reports/subscriptions/{id}
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from solden.core.auth import TokenData, get_current_user
from solden.core.database import get_db
from solden.core.stores.report_subscription_store import VALID_CADENCES
from solden.services import workspace_reports

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace/reports/subscriptions",
    tags=["report-subscriptions"],
)


class SubscriptionCreateRequest(BaseModel):
    report_type: str = Field(..., min_length=1)
    cadence: str = Field("weekly")
    recipient_email: EmailStr
    params: Optional[Dict[str, Any]] = None


class SubscriptionPatchRequest(BaseModel):
    cadence: Optional[str] = None
    recipient_email: Optional[EmailStr] = None
    params: Optional[Dict[str, Any]] = None
    paused: Optional[bool] = None


@router.post("")
def create_subscription(
    body: SubscriptionCreateRequest,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    if body.report_type not in workspace_reports.VALID_REPORT_TYPES:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_report_type",
                "message": f"report_type must be one of {sorted(workspace_reports.VALID_REPORT_TYPES)}",
            },
        )
    if body.cadence not in VALID_CADENCES:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_cadence",
                "message": f"cadence must be one of {sorted(VALID_CADENCES)}",
            },
        )

    db = get_db()
    sub = db.create_report_subscription({
        "organization_id": user.organization_id,
        "user_id": getattr(user, "user_id", "") or getattr(user, "email", ""),
        "recipient_email": body.recipient_email,
        "report_type": body.report_type,
        "cadence": body.cadence,
        "params": body.params or {},
    })
    return sub


@router.get("")
def list_subscriptions(
    mine_only: bool = False,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    user_id = getattr(user, "user_id", "") or getattr(user, "email", "")
    rows: List[Dict[str, Any]] = db.list_report_subscriptions(
        user.organization_id,
        user_id=user_id if mine_only else None,
    )
    return {"subscriptions": rows}


@router.get("/{subscription_id}")
def get_subscription(
    subscription_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    sub = db.get_report_subscription(subscription_id)
    if not sub or sub.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="subscription_not_found")
    return sub


@router.patch("/{subscription_id}")
def patch_subscription(
    subscription_id: str,
    body: SubscriptionPatchRequest,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    existing = db.get_report_subscription(subscription_id)
    if not existing or existing.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="subscription_not_found")

    fields: Dict[str, Any] = {}
    if body.cadence is not None:
        if body.cadence not in VALID_CADENCES:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_cadence",
                        "message": f"cadence must be one of {sorted(VALID_CADENCES)}"},
            )
        fields["cadence"] = body.cadence
    if body.recipient_email is not None:
        fields["recipient_email"] = body.recipient_email
    if body.params is not None:
        fields["params"] = body.params
    if body.paused is not None:
        if body.paused:
            from datetime import datetime, timezone
            fields["paused_at"] = datetime.now(timezone.utc)
        else:
            fields["paused_at"] = None

    updated = db.update_report_subscription(
        subscription_id, user.organization_id, **fields,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="subscription_not_found")
    return updated


@router.delete("/{subscription_id}")
def delete_subscription(
    subscription_id: str,
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    db = get_db()
    deleted = db.delete_report_subscription(subscription_id, user.organization_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="subscription_not_found")
    return {"deleted": True, "subscription_id": subscription_id}
