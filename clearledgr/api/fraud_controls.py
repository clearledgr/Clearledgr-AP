"""Fraud controls API — the only user-facing surface for modifying
architectural fraud-control parameters.

Per DESIGN_THESIS.md §8, fraud controls are architectural:

- The *check code paths* (payment ceiling, first payment hold, velocity,
  duplicate prevention, prompt injection) are unconditionally enforced
  at ``invoice_validation._evaluate_deterministic_validation``. They
  cannot be disabled from any API — only raised/lowered.
- The *numeric parameters* (ceiling amount, velocity max, dormancy days)
  can be modified only by the CFO role (or owner as the superset).
- Every modification is logged via ``db.append_ap_audit_event`` with
  ``event_type="fraud_control_modified"`` and a full before/after diff.

Endpoints:
  GET  /fraud-controls/{organization_id}
  PUT  /fraud-controls/{organization_id}
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import (
    TokenData,
    get_current_user,
    has_fraud_control_admin,
    require_cfo,
)
from clearledgr.core.database import get_db
from clearledgr.core.fraud_controls import (
    DEFAULT_FIRST_PAYMENT_DORMANCY_DAYS,
    DEFAULT_PAYMENT_CEILING,
    DEFAULT_VENDOR_VELOCITY_MAX_PER_WEEK,
    FraudControlConfig,
    load_fraud_controls,
    save_fraud_controls,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fraud-controls", tags=["fraud-controls"])


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class FraudControlsResponse(BaseModel):
    """Serialized fraud-control configuration for an organization."""

    organization_id: str
    payment_ceiling: float
    vendor_velocity_max_per_week: int
    first_payment_dormancy_days: int
    base_currency: str
    defaults: Dict[str, Any] = Field(
        default_factory=lambda: {
            "payment_ceiling": DEFAULT_PAYMENT_CEILING,
            "vendor_velocity_max_per_week": DEFAULT_VENDOR_VELOCITY_MAX_PER_WEEK,
            "first_payment_dormancy_days": DEFAULT_FIRST_PAYMENT_DORMANCY_DAYS,
        }
    )


class FraudControlsUpdateRequest(BaseModel):
    """Partial update to fraud-control parameters. Omitted fields keep their current value."""

    payment_ceiling: Optional[float] = Field(
        default=None,
        ge=0,
        description="Max invoice amount in base currency above which auto-approval is blocked.",
    )
    vendor_velocity_max_per_week: Optional[int] = Field(
        default=None,
        ge=1,
        description="Max invoices per vendor per 7 days before auto-approval is blocked.",
    )
    first_payment_dormancy_days: Optional[int] = Field(
        default=None,
        ge=0,
        description="Days of vendor silence that re-trigger first-payment hold.",
    )
    base_currency: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Za-z]{3}$",
        description="ISO 4217 currency code that payment_ceiling is denominated in.",
    )


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def _assert_same_org_or_admin(user: TokenData, organization_id: str) -> None:
    """Cross-tenant access guard.

    A user may only read or modify fraud controls for their own organization.
    CFO or owner from a different org is still denied — role elevation does
    not imply cross-tenant reach.
    """
    if str(user.organization_id or "").strip() != str(organization_id or "").strip():
        raise HTTPException(status_code=403, detail="cross_tenant_access_denied")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{organization_id}", response_model=FraudControlsResponse)
def get_fraud_controls(
    organization_id: str,
    user: TokenData = Depends(get_current_user),
) -> FraudControlsResponse:
    """Return the current fraud-control configuration for an organization.

    Readable by any authenticated member of the organization — the
    parameters are operationally relevant (AP Managers need to know
    the payment ceiling to understand why an invoice was held) but
    cannot be modified by non-CFO users.
    """
    _assert_same_org_or_admin(user, organization_id)
    db = get_db()
    config = load_fraud_controls(organization_id, db)
    return FraudControlsResponse(
        organization_id=organization_id,
        payment_ceiling=config.payment_ceiling,
        vendor_velocity_max_per_week=config.vendor_velocity_max_per_week,
        first_payment_dormancy_days=config.first_payment_dormancy_days,
        base_currency=config.base_currency,
    )


@router.put("/{organization_id}", response_model=FraudControlsResponse)
def update_fraud_controls(
    organization_id: str,
    request: FraudControlsUpdateRequest,
    user: TokenData = Depends(require_cfo),
) -> FraudControlsResponse:
    """Update fraud-control parameters. CFO or owner role required.

    Omitted fields keep their current value — this is a partial update,
    not a full replacement. Every call — whether it changes values or
    not — is logged to ``ap_audit_events`` with a full before/after
    diff. The audit entry includes the actor user_id so SOC reviews
    can trace each change.
    """
    _assert_same_org_or_admin(user, organization_id)

    # Defense-in-depth: even though require_cfo already
    # enforces the role, re-check here so any future refactor that
    # accidentally swaps the dependency cannot silently open the endpoint.
    if not has_fraud_control_admin(getattr(user, "role", None)):
        raise HTTPException(
            status_code=403,
            detail="cfo_role_required_for_fraud_control_modification",
        )

    db = get_db()
    current = load_fraud_controls(organization_id, db)

    updated_values: Dict[str, Any] = current.to_dict()
    if request.payment_ceiling is not None:
        updated_values["payment_ceiling"] = float(request.payment_ceiling)
    if request.vendor_velocity_max_per_week is not None:
        updated_values["vendor_velocity_max_per_week"] = int(
            request.vendor_velocity_max_per_week
        )
    if request.first_payment_dormancy_days is not None:
        updated_values["first_payment_dormancy_days"] = int(
            request.first_payment_dormancy_days
        )
    if request.base_currency is not None:
        updated_values["base_currency"] = request.base_currency.upper()

    new_config = FraudControlConfig.from_dict(
        updated_values, base_currency=updated_values.get("base_currency", current.base_currency)
    )

    try:
        saved = save_fraud_controls(
            organization_id,
            new_config,
            modified_by=user.user_id,
            db=db,
        )
    except Exception as exc:
        logger.error(
            "[FraudControlsAPI] Failed to save fraud controls for org %s: %s",
            organization_id, exc,
        )
        raise HTTPException(status_code=500, detail="fraud_control_save_failed")

    return FraudControlsResponse(
        organization_id=organization_id,
        payment_ceiling=saved.payment_ceiling,
        vendor_velocity_max_per_week=saved.vendor_velocity_max_per_week,
        first_payment_dormancy_days=saved.first_payment_dormancy_days,
        base_currency=saved.base_currency,
    )
