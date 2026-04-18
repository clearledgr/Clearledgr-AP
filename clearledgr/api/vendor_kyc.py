"""Vendor KYC API — Phase 2.4.

DESIGN_THESIS.md §3 positions the Vendor as a first-class persistent
object with KYC fields (registration_number, vat_number, registered_
address, director_names, kyc_completion_date) plus computed signals
(iban_verified, iban_verified_at, ytd_spend, risk_score).

Endpoints:
  GET   /api/vendors/{vendor_name}/kyc
      — Any authenticated org member. Returns the KYC sub-dict +
        masked verified bank details + pending bank details (if frozen)
        + ytd_spend + risk_score with component breakdown.

  PUT   /api/vendors/{vendor_name}/kyc
      — Financial Controller or higher. Partial updates: only the
        fields present in the request body are modified. Every
        successful write emits a ``vendor_kyc_updated`` audit event
        with the list of changed field names (never values).

The GET response is the authoritative vendor-intelligence surface for
any client that wants a single call to render the Vendor object.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import (
    TokenData,
    get_current_user,
    require_financial_controller,
)
from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/vendors",
    tags=["vendor-kyc"],
)


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


class UpdateVendorKycRequest(BaseModel):
    """Partial KYC update payload.

    Every field is optional — clients send only what they want to
    change. Passing ``director_names=[]`` explicitly clears the list;
    passing ``director_names=None`` is equivalent (the store layer
    treats None as "clear").
    """

    registration_number: Optional[str] = Field(
        default=None, max_length=128,
        description="Company registration identifier.",
    )
    vat_number: Optional[str] = Field(
        default=None, max_length=64,
        description="Tax identification / VAT number.",
    )
    registered_address: Optional[str] = Field(
        default=None, max_length=512,
        description="Registered legal address of the vendor entity.",
    )
    director_names: Optional[List[str]] = Field(
        default=None,
        description=(
            "Names of the vendor's directors / beneficial owners. "
            "Pass an empty list or None to clear."
        ),
    )
    kyc_completion_date: Optional[str] = Field(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD) when KYC was completed. Use None "
            "to clear; clients should send a date string for the audit "
            "trail to reflect the completion."
        ),
    )

    def non_null_fields(self) -> Dict[str, Any]:
        """Return only the fields the client explicitly supplied.

        Pydantic v2: ``model_fields_set`` contains every field the
        client actually sent in the JSON body, so we can distinguish
        "client wants to clear this" from "client didn't mention this".
        """
        result: Dict[str, Any] = {}
        for field in self.model_fields_set:
            result[field] = getattr(self, field)
        return result


# ---------------------------------------------------------------------------
# Shared guards
# ---------------------------------------------------------------------------


def _assert_same_org(user: TokenData, requested_org: str) -> None:
    if str(user.organization_id or "").strip() != str(requested_org or "").strip():
        raise HTTPException(status_code=403, detail="cross_tenant_access_denied")


def _actor_label(user: TokenData) -> str:
    return (
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or "unknown_user"
    )


# ---------------------------------------------------------------------------
# Vendor intelligence read path
# ---------------------------------------------------------------------------


def _derive_iban_verification(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Derive iban_verified + iban_verified_at from existing state.

    Phase 2.4 explicitly does NOT store these as new columns — they're
    computed from Phase 2.1.a / 2.1.b state so there's no duplicate
    source of truth. Rules:

      - ``iban_verified`` is True iff ``bank_details_encrypted`` is
        set AND ``iban_change_pending`` is False. A vendor under an
        active freeze is NOT verified regardless of history.
      - ``iban_verified_at`` is the most recent
        ``bank_details_changed_at`` when iban_verified is True; None
        when unverified.
    """
    has_bank_details = bool(profile.get("bank_details_encrypted"))
    iban_freeze_active = bool(profile.get("iban_change_pending"))
    verified = has_bank_details and not iban_freeze_active
    verified_at: Optional[str] = None
    if verified:
        verified_at = profile.get("bank_details_changed_at")
    return {
        "iban_verified": verified,
        "iban_verified_at": verified_at,
    }


@router.get("/{vendor_name}/kyc")
def get_vendor_kyc(
    vendor_name: str,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the full vendor-intelligence view for a vendor.

    Shape (DESIGN_THESIS.md §3):
      {
        "organization_id": str,
        "vendor_name": str,
        "kyc": {
          registration_number, vat_number, registered_address,
          director_names, kyc_completion_date, vendor_kyc_updated_at
        },
        "iban_verified": bool,
        "iban_verified_at": str | None,
        "verified_bank_details_masked": dict | None,
        "iban_change_pending": bool,
        "ytd_spend": float,
        "ytd_spend_year": int,
        "risk_score": { score, components[], computed_at },
      }

    Returns 404 when the vendor doesn't exist in the profile table.
    """
    _assert_same_org(user, organization_id)
    db = get_db()

    profile = db.get_vendor_profile(organization_id, vendor_name)
    if not profile:
        raise HTTPException(status_code=404, detail="vendor_not_found")

    kyc = db.get_vendor_kyc(organization_id, vendor_name)
    iban_state = _derive_iban_verification(profile)
    ytd_year = datetime.now(timezone.utc).year
    ytd_spend = db.compute_vendor_ytd_spend(
        organization_id, vendor_name, year=ytd_year
    )
    verified_bank_masked = db.get_vendor_bank_details_masked(
        organization_id, vendor_name
    )

    from clearledgr.services.vendor_risk import (
        get_vendor_risk_score_service,
    )
    risk = get_vendor_risk_score_service(organization_id, db=db).compute(
        vendor_name
    )

    return {
        "organization_id": organization_id,
        "vendor_name": vendor_name,
        "kyc": kyc,
        "iban_verified": iban_state["iban_verified"],
        "iban_verified_at": iban_state["iban_verified_at"],
        "verified_bank_details_masked": verified_bank_masked,
        "iban_change_pending": bool(profile.get("iban_change_pending")),
        "ytd_spend": ytd_spend,
        "ytd_spend_year": ytd_year,
        "risk_score": risk.to_dict(),
    }


# ---------------------------------------------------------------------------
# KYC write path
# ---------------------------------------------------------------------------


@router.put("/{vendor_name}/kyc")
def update_vendor_kyc(
    vendor_name: str,
    body: UpdateVendorKycRequest,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(require_financial_controller),
) -> Dict[str, Any]:
    """Partially update a vendor's KYC fields.

    Financial Controller role or higher required. Every successful
    write emits a ``vendor_kyc_updated`` audit event with the list of
    changed field names (never values — per the §19 no-plaintext-in-
    logs discipline shared across Phase 2).
    """
    _assert_same_org(user, organization_id)

    patch = body.non_null_fields()
    if not patch:
        raise HTTPException(status_code=400, detail="empty_patch")

    db = get_db()
    profile = db.get_vendor_profile(organization_id, vendor_name)
    if not profile:
        raise HTTPException(status_code=404, detail="vendor_not_found")

    updated = db.update_vendor_kyc(
        organization_id,
        vendor_name,
        patch=patch,
        actor_id=_actor_label(user),
    )
    if updated is None:
        raise HTTPException(status_code=400, detail="kyc_update_failed")

    # Audit event — field names only, never values
    try:
        db.append_audit_event(
            {
                "ap_item_id": "",
                "event_type": "vendor_kyc_updated",
                "actor_type": "user",
                "actor_id": _actor_label(user),
                "reason": (
                    f"Vendor KYC updated for {vendor_name}: "
                    f"{sorted(patch.keys())}"
                ),
                "metadata": {
                    "vendor_name": vendor_name,
                    "changed_fields": sorted(patch.keys()),
                },
                "organization_id": organization_id,
                "source": "vendor_kyc_api",
            }
        )
    except Exception as audit_exc:
        logger.warning(
            "[vendor_kyc] audit event emission failed (non-fatal): %s",
            audit_exc,
        )

    return {
        "status": "updated",
        "organization_id": organization_id,
        "vendor_name": vendor_name,
        "kyc": updated,
        "changed_fields": sorted(patch.keys()),
    }
