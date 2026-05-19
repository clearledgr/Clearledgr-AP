"""Audit chain status endpoint.

Backs the marketing claim that the audit chain is "tamper-evident
at the schema layer" with a runtime check operators (and external
auditors) can run on a live tenant.

  GET /api/workspace/audit/chain-status

Returns a structured status block describing chain integrity, head
position, and last-verified timestamp. Any authenticated org
member can read — operational visibility matters even for
non-admin roles when an auditor is sitting next to them.

Heavy verification (full-chain replay) is NOT what this endpoint
does. Instead it samples the head N rows (default 100), which is
the most likely tampering surface: a malicious operator covering
their tracks would target recent rows, not the chain root buried
under months of history. Auditors who want a full replay can
re-run the verification helper offline against a database export
without holding a worker thread for minutes.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from solden.core.auth import TokenData, get_current_user
from solden.core.database import get_db
from solden.services.audit_chain_verify import (
    DEFAULT_SAMPLE_SIZE,
    verify_chain_head,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspace/audit", tags=["audit-chain"])


@router.get("/chain-status")
def get_chain_status(
    sample_size: int = Query(
        default=DEFAULT_SAMPLE_SIZE,
        ge=1,
        le=5000,
        description=(
            "Number of head rows to verify. Higher values give "
            "stronger assurance at the cost of longer response "
            "time. The head is the most likely tampering target."
        ),
    ),
    user: TokenData = Depends(get_current_user),
):
    """Return audit-chain integrity status for the caller's org."""
    org_id = str(user.organization_id or "").strip()
    if not org_id:
        # Mirror the per-tenant isolation invariant from group 5.
        # No silent fallback to "default".
        raise HTTPException(
            status_code=403,
            detail="missing_user_organization_id",
        )

    db = get_db()
    try:
        result = verify_chain_head(
            db, organization_id=org_id, sample_size=sample_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[audit_chain] verification failed for org=%s: %s",
            org_id, exc,
        )
        raise HTTPException(
            status_code=500,
            detail="chain_verification_failed",
        ) from exc

    return result
