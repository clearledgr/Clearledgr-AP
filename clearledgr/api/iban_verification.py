"""IBAN change verification API — Phase 2.1.b.

DESIGN_THESIS.md §8: *"IBAN change freeze with three-factor verification
(vendor email domain + phone confirmation + AP Manager sign-off)."*

Endpoints:
  GET    /api/vendors/{vendor_name}/iban-verification
    — Returns the current freeze status for a vendor. Any authenticated
      member of the organization can read.

  POST   /api/vendors/{vendor_name}/iban-verification/factors/{factor}
    — Records a single verification factor. Valid factor names are
      ``email_domain_factor``, ``phone_factor``, ``sign_off_factor``.
      Requires CFO or owner role (``require_fraud_control_admin``).

  POST   /api/vendors/{vendor_name}/iban-verification/complete
    — Completes verification and lifts the freeze. All three factors
      must already be verified. Requires CFO or owner role.

  POST   /api/vendors/{vendor_name}/iban-verification/reject
    — Rejects the unverified change. Does NOT require factor progress.
      Requires CFO or owner role.

All write endpoints emit audit events through the
``IbanChangeFreezeService`` audit trail.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import (
    TokenData,
    get_current_user,
    require_fraud_control_admin,
)
from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/vendors",
    tags=["iban-verification"],
)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class RecordPhoneFactorRequest(BaseModel):
    """Payload for the phone_factor endpoint.

    The AP Manager records an out-of-band phone call confirming the
    IBAN change with the vendor. Fields are deliberately explicit so
    the audit trail can prove the call actually happened.
    """

    verified_phone_number: str = Field(
        ...,
        min_length=3,
        max_length=64,
        description=(
            "The phone number that was called to confirm the change. "
            "Must be the vendor's known phone number, not a number "
            "provided on the current invoice."
        ),
    )
    caller_name_at_vendor: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description=(
            "Name of the person at the vendor who confirmed the change "
            "during the call."
        ),
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="Free-form notes about the call.",
    )


class RecordSignOffFactorRequest(BaseModel):
    """Payload for the sign_off_factor endpoint.

    The sign-off is the final attestation that the CFO or AP Manager
    has reviewed every other factor and is authorizing the change.
    Minimal payload — the actor's identity (from the auth token) is
    the substantive record.
    """

    notes: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="Optional rationale for the sign-off.",
    )


class RecordEmailDomainFactorRequest(BaseModel):
    """Payload for manually overriding the auto-check on email_domain_factor.

    Used when the auto-check failed (sender domain isn't in the
    vendor's known sender_domains list) and the CFO decides to
    manually accept the domain anyway — for example, because the
    vendor just moved to a new subsidiary's email domain.
    """

    override_note: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description=(
            "Justification for manually verifying the email domain "
            "factor when the auto-check failed."
        ),
    )


class RejectFreezeRequest(BaseModel):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description=(
            "Mandatory reason for rejecting the change (e.g., "
            "'Suspicious domain, escalated to security')."
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_same_org(user: TokenData, requested_org: str) -> None:
    """Cross-tenant access guard — same pattern as fraud_controls.py."""
    if str(user.organization_id or "").strip() != str(requested_org or "").strip():
        raise HTTPException(status_code=403, detail="cross_tenant_access_denied")


def _actor_label(user: TokenData) -> str:
    return (
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or "unknown_user"
    )


def _service(organization_id: str):
    from clearledgr.services.iban_change_freeze import (
        get_iban_change_freeze_service,
    )
    return get_iban_change_freeze_service(organization_id, db=get_db())


# ---------------------------------------------------------------------------
# GET status
# ---------------------------------------------------------------------------


@router.get("/{vendor_name}/iban-verification")
def get_iban_verification_status(
    vendor_name: str,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the current freeze status for a vendor.

    Response:
        {
            "organization_id": ...,
            "vendor_name": ...,
            "frozen": bool,
            "detected_at": iso | null,
            "verified_bank_details_masked": dict | null,
            "pending_bank_details_masked": dict | null,
            "verification_state": dict | null,
            "missing_factors": [str]
        }
    """
    _assert_same_org(user, organization_id)
    svc = _service(organization_id)
    status = svc.get_freeze_status(vendor_name)
    return {
        "organization_id": organization_id,
        "vendor_name": vendor_name,
        **status,
    }


# ---------------------------------------------------------------------------
# POST record factor
# ---------------------------------------------------------------------------


@router.post("/{vendor_name}/iban-verification/factors/phone")
def record_phone_factor(
    vendor_name: str,
    body: RecordPhoneFactorRequest,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(require_fraud_control_admin),
) -> Dict[str, Any]:
    """Record the phone-confirmation factor.

    CFO or owner role required. Every call adds an
    ``iban_change_factor_recorded`` audit event.
    """
    _assert_same_org(user, organization_id)
    svc = _service(organization_id)
    result = svc.record_factor(
        vendor_name=vendor_name,
        factor="phone_factor",
        payload=body.model_dump(),
        actor_id=_actor_label(user),
    )
    return _factor_result_to_response(result, vendor_name, organization_id)


@router.post("/{vendor_name}/iban-verification/factors/sign-off")
def record_sign_off_factor(
    vendor_name: str,
    body: RecordSignOffFactorRequest,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(require_fraud_control_admin),
) -> Dict[str, Any]:
    """Record the CFO/AP Manager sign-off factor.

    CFO or owner role required (enforced via
    ``require_fraud_control_admin``). The actor's identity from the
    auth token is the substantive record.
    """
    _assert_same_org(user, organization_id)
    svc = _service(organization_id)
    result = svc.record_factor(
        vendor_name=vendor_name,
        factor="sign_off_factor",
        payload=body.model_dump(),
        actor_id=_actor_label(user),
    )
    return _factor_result_to_response(result, vendor_name, organization_id)


@router.post("/{vendor_name}/iban-verification/factors/email-domain")
def record_email_domain_factor(
    vendor_name: str,
    body: RecordEmailDomainFactorRequest,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(require_fraud_control_admin),
) -> Dict[str, Any]:
    """Manually override the auto-check on the email_domain_factor.

    Used when the auto-check failed (sender domain not in known
    ``sender_domains``) and the CFO still wants to accept. CFO or
    owner role required.
    """
    _assert_same_org(user, organization_id)
    svc = _service(organization_id)
    result = svc.record_factor(
        vendor_name=vendor_name,
        factor="email_domain_factor",
        payload={
            "manual_override": True,
            "override_note": body.override_note,
        },
        actor_id=_actor_label(user),
    )
    return _factor_result_to_response(result, vendor_name, organization_id)


# ---------------------------------------------------------------------------
# POST complete / reject
# ---------------------------------------------------------------------------


@router.post("/{vendor_name}/iban-verification/complete")
def complete_iban_verification(
    vendor_name: str,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(require_fraud_control_admin),
) -> Dict[str, Any]:
    """Complete three-factor verification and lift the freeze.

    All three factors must already be verified. CFO or owner role
    required. Returns the final verification state for the audit
    trail; the vendor's verified ``bank_details_encrypted`` column now
    holds the new details.
    """
    _assert_same_org(user, organization_id)
    svc = _service(organization_id)
    result = svc.complete_freeze(
        vendor_name=vendor_name,
        actor_id=_actor_label(user),
    )
    if result.status == "completed":
        return {
            "status": "completed",
            "organization_id": organization_id,
            "vendor_name": vendor_name,
            "verification_state": result.verification_state,
        }
    if result.status == "missing_factors":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "factors_incomplete",
                "missing_factors": result.missing_factors,
            },
        )
    if result.status == "not_frozen":
        raise HTTPException(status_code=404, detail="vendor_not_frozen")
    raise HTTPException(
        status_code=500,
        detail={"error": "completion_failed", "reason": result.reason},
    )


@router.post("/{vendor_name}/iban-verification/reject")
def reject_iban_verification(
    vendor_name: str,
    body: RejectFreezeRequest,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(require_fraud_control_admin),
) -> Dict[str, Any]:
    """Reject the unverified change and clear the freeze.

    Does NOT require factor progress — a rejection can happen
    immediately when the verifier spots something suspicious. The
    verified bank details column is untouched; future invoices with
    the OLD verified details will pass the gate normally.
    """
    _assert_same_org(user, organization_id)
    svc = _service(organization_id)
    result = svc.reject_freeze(
        vendor_name=vendor_name,
        actor_id=_actor_label(user),
        reason=body.reason,
    )
    if result.status == "rejected":
        return {
            "status": "rejected",
            "organization_id": organization_id,
            "vendor_name": vendor_name,
        }
    if result.status == "not_frozen":
        raise HTTPException(status_code=404, detail="vendor_not_frozen")
    raise HTTPException(
        status_code=500,
        detail={"error": "rejection_failed", "reason": result.reason},
    )


# ---------------------------------------------------------------------------
# Shared factor-result → response mapper
# ---------------------------------------------------------------------------


def _factor_result_to_response(
    result: Any, vendor_name: str, organization_id: str
) -> Dict[str, Any]:
    if result.status == "recorded":
        return {
            "status": "recorded",
            "organization_id": organization_id,
            "vendor_name": vendor_name,
            "verification_state": result.verification_state,
        }
    if result.status == "not_frozen":
        raise HTTPException(status_code=404, detail="vendor_not_frozen")
    if result.status == "unknown_factor":
        raise HTTPException(
            status_code=400,
            detail={"error": "unknown_factor", "reason": result.reason},
        )
    raise HTTPException(
        status_code=500,
        detail={"error": "factor_record_failed", "reason": result.reason},
    )
