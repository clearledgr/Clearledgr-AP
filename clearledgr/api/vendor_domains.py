"""Vendor trusted-domains API — Phase 2.2.

DESIGN_THESIS.md §8: vendor domain lock. This router is the CFO's
surface for managing the sender-domain allowlist that the validation
gate consults on every invoice. Adding a domain is fraud-control-
admin-gated (CFO or owner) and audit-logged, matching the pattern in
``fraud_controls.py`` and ``iban_verification.py``.

Endpoints:
  GET    /api/vendors/{vendor_name}/trusted-domains
    — Returns the current allowlist. Any authenticated member of the
      organization can read.

  POST   /api/vendors/{vendor_name}/trusted-domains
    — Adds a domain to the allowlist. CFO or owner role required.

  DELETE /api/vendors/{vendor_name}/trusted-domains/{domain}
    — Removes a domain from the allowlist. CFO or owner role required.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

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
    tags=["vendor-trusted-domains"],
)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class AddTrustedDomainRequest(BaseModel):
    domain: str = Field(
        ...,
        min_length=3,
        max_length=253,
        pattern=r"^[a-zA-Z0-9.\-]+$",
        description=(
            "The domain to add to the allowlist. Matched case-insensitively "
            "against invoice sender domains using dot-boundary suffix "
            "matching — ``acme.com`` allows ``billing.acme.com``."
        ),
    )


# ---------------------------------------------------------------------------
# Shared guards
# ---------------------------------------------------------------------------


def _assert_same_org(user: TokenData, requested_org: str) -> None:
    """Cross-tenant access guard."""
    if str(user.organization_id or "").strip() != str(requested_org or "").strip():
        raise HTTPException(status_code=403, detail="cross_tenant_access_denied")


def _actor_label(user: TokenData) -> str:
    return (
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or "unknown_user"
    )


def _service(organization_id: str):
    from clearledgr.services.vendor_domain_lock import (
        get_vendor_domain_lock_service,
    )
    return get_vendor_domain_lock_service(organization_id, db=get_db())


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


@router.get("/{vendor_name}/trusted-domains")
def get_trusted_domains(
    vendor_name: str,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the current trusted-domain allowlist for a vendor."""
    _assert_same_org(user, organization_id)
    svc = _service(organization_id)
    domains: List[str] = svc.list_trusted_domains(vendor_name)
    return {
        "organization_id": organization_id,
        "vendor_name": vendor_name,
        "trusted_domains": domains,
    }


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------


@router.post("/{vendor_name}/trusted-domains")
def add_trusted_domain(
    vendor_name: str,
    body: AddTrustedDomainRequest,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(require_fraud_control_admin),
) -> Dict[str, Any]:
    """Add a domain to the vendor's trusted-domain allowlist.

    CFO or owner role required. The write is idempotent — adding a
    domain that is already present is a successful no-op. Every
    successful addition (not no-ops) emits a
    ``vendor_trusted_domain_added`` audit event.
    """
    _assert_same_org(user, organization_id)
    svc = _service(organization_id)
    ok = svc.add_trusted_domain(
        vendor_name=vendor_name,
        domain=body.domain,
        actor_id=_actor_label(user),
    )
    if not ok:
        raise HTTPException(
            status_code=500,
            detail={"error": "add_failed"},
        )
    return {
        "status": "added",
        "organization_id": organization_id,
        "vendor_name": vendor_name,
        "trusted_domains": svc.list_trusted_domains(vendor_name),
    }


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


@router.delete("/{vendor_name}/trusted-domains/{domain}")
def remove_trusted_domain(
    vendor_name: str,
    domain: str,
    organization_id: str = Query(..., description="Organization identifier"),
    user: TokenData = Depends(require_fraud_control_admin),
) -> Dict[str, Any]:
    """Remove a domain from the vendor's trusted-domain allowlist.

    CFO or owner role required. Returns 404 when the domain is not in
    the current allowlist — callers that wanted a no-op should check
    the GET endpoint first. Emits a ``vendor_trusted_domain_removed``
    audit event on success.
    """
    _assert_same_org(user, organization_id)
    svc = _service(organization_id)
    ok = svc.remove_trusted_domain(
        vendor_name=vendor_name,
        domain=domain,
        actor_id=_actor_label(user),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="domain_not_in_allowlist")
    return {
        "status": "removed",
        "organization_id": organization_id,
        "vendor_name": vendor_name,
        "trusted_domains": svc.list_trusted_domains(vendor_name),
    }
