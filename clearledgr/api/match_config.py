"""AP matching configuration API.

Single combined surface for the two policy kinds that decide how an
incoming invoice is matched:

* ``match_mode`` — selects which match algorithm runs (3-way required,
  2-way fallback, or policy-only). Drives ``CoordinationEngine._handle_match``.
* ``match_tolerances`` — numeric thresholds the AP match path uses for
  price variance, quantity variance, and absolute amount fuzz. Read by
  ``PurchaseOrderService._get_tolerances``.

Both kinds are versioned via :class:`PolicyService`, so every PUT
creates a new version row and every match outcome can be audited
back to the policy version it ran under.

Endpoints:
  GET  /api/workspace/settings/match-config   — any authenticated org member
  PUT  /api/workspace/settings/match-config   — financial-controller role or higher
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clearledgr.core.auth import (
    TokenData,
    get_current_user,
    require_admin_user,
)
from clearledgr.services.policy_service import (
    VALID_MATCH_MODES,
    PolicyService,
)
from clearledgr.core.org_utils import require_org

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspace/settings", tags=["match-config"])


# ─── Request / response shapes ──────────────────────────────────────


class TolerancesPayload(BaseModel):
    """Configurable AP-match tolerances. Same dimensions
    ``PurchaseOrderService._get_tolerances`` reads."""

    price_tolerance_percent: float = Field(
        ge=0.0, le=100.0,
        description="Allowed price variance as a percentage of PO total.",
    )
    quantity_tolerance_percent: float = Field(
        ge=0.0, le=100.0,
        description="Allowed quantity variance as a percentage of PO line qty.",
    )
    amount_tolerance: float = Field(
        ge=0.0,
        description="Absolute amount tolerance floor (in PO currency).",
    )


class TolerancesUpdate(BaseModel):
    """Partial tolerance update. Omitted fields keep current value."""

    price_tolerance_percent: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    quantity_tolerance_percent: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    amount_tolerance: Optional[float] = Field(default=None, ge=0.0)


class MatchConfigResponse(BaseModel):
    organization_id: str
    mode: str
    tolerances: TolerancesPayload
    mode_version_id: str
    mode_version_number: int
    tolerances_version_id: str
    tolerances_version_number: int


class MatchConfigUpdateRequest(BaseModel):
    """Partial config update. Omit ``mode`` to leave it unchanged;
    omit ``tolerances`` (or any tolerance field) to leave that
    unchanged. Empty body is a no-op."""

    mode: Optional[str] = Field(
        default=None,
        description=(
            "One of: three_way_required, two_way_fallback, policy_only."
        ),
    )
    tolerances: Optional[TolerancesUpdate] = None


# ─── Helpers ────────────────────────────────────────────────────────


def _read_config(organization_id: str) -> MatchConfigResponse:
    svc = PolicyService(organization_id)
    mode_version = svc.get_active("match_mode")
    tol_version = svc.get_active("match_tolerances")

    mode = (mode_version.content or {}).get("mode") or "two_way_fallback"
    section = (tol_version.content or {}).get("ap_three_way") or {}

    return MatchConfigResponse(
        organization_id=organization_id,
        mode=mode,
        tolerances=TolerancesPayload(
            price_tolerance_percent=float(section.get("price_tolerance_percent", 2.0)),
            quantity_tolerance_percent=float(section.get("quantity_tolerance_percent", 5.0)),
            amount_tolerance=float(section.get("amount_tolerance", 10.0)),
        ),
        mode_version_id=mode_version.id,
        mode_version_number=mode_version.version_number,
        tolerances_version_id=tol_version.id,
        tolerances_version_number=tol_version.version_number,
    )


# ─── Endpoints ──────────────────────────────────────────────────────


@router.get("/match-config", response_model=MatchConfigResponse)
def get_match_config(
    user: TokenData = Depends(get_current_user),
) -> MatchConfigResponse:
    """Return the active match mode + tolerances for the caller's org.

    Readable by any authenticated org member — operational visibility
    matters (an AP clerk wondering why a match failed should be able
    to see the configured tolerance), but only admins can change it.
    """
    org_id = str(user.organization_id or "").strip() or "default"
    return _read_config(org_id)


@router.put("/match-config", response_model=MatchConfigResponse)
def update_match_config(
    request: MatchConfigUpdateRequest,
    user: TokenData = Depends(require_admin_user),
) -> MatchConfigResponse:
    """Update mode and/or tolerances. Each non-empty slice creates a
    new immutable policy version via :class:`PolicyService`.

    Partial: omit ``mode`` to leave it unchanged; omit ``tolerances``
    to leave them unchanged. Sending both updates them atomically
    from the caller's perspective (two version rows are written; the
    next match operation sees both).
    """
    org_id = str(user.organization_id or "").strip() or "default"
    actor = str(getattr(user, "user_id", "") or "system").strip() or "system"

    svc = PolicyService(org_id)

    # ── Mode update ─────────────────────────────────────────────
    if request.mode is not None:
        if request.mode not in VALID_MATCH_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"invalid_match_mode:{request.mode}",
            )
        svc.set_policy(
            "match_mode",
            content={"mode": request.mode},
            actor=actor,
            description=f"match_mode → {request.mode}",
        )

    # ── Tolerances update ───────────────────────────────────────
    if request.tolerances is not None:
        # Read current ap_three_way section so partial updates don't
        # wipe omitted fields.
        current_tol_version = svc.get_active("match_tolerances")
        current_content = dict(current_tol_version.content or {})
        section = dict(current_content.get("ap_three_way") or {})

        if request.tolerances.price_tolerance_percent is not None:
            section["price_tolerance_percent"] = float(
                request.tolerances.price_tolerance_percent
            )
        if request.tolerances.quantity_tolerance_percent is not None:
            section["quantity_tolerance_percent"] = float(
                request.tolerances.quantity_tolerance_percent
            )
        if request.tolerances.amount_tolerance is not None:
            section["amount_tolerance"] = float(request.tolerances.amount_tolerance)

        current_content["ap_three_way"] = section
        svc.set_policy(
            "match_tolerances",
            content=current_content,
            actor=actor,
            description="ap_three_way tolerances updated",
        )

    return _read_config(org_id)
