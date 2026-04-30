"""Ops + customer-facing endpoints for read-side projections (Gap 6).

* ``GET /api/vendors/{vendor_name}/summary`` — single-row vendor
  rollup from ``vendor_summary``. Customer-visible (vendor detail
  page, ledger Gmail sidebar).
* ``GET /api/vendors/summary`` — list of rollups for an org with
  light filtering. Customer-visible.
* ``POST /api/ops/projections/rebuild`` — recompute all projections
  for an org. Ops/admin only — used after schema migrations or
  projector logic changes.
* ``GET /api/ops/projections`` — list registered projectors + their
  declared box_types. Ops introspection.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from clearledgr.api.deps import verify_org_access
from clearledgr.core.auth import get_current_user
from clearledgr.services.box_projection import (
    get_vendor_summary_row,
    list_registered_projectors,
    list_vendor_summaries,
    rebuild_projections,
)


vendors_router = APIRouter(prefix="/api/vendors", tags=["vendor-summary"])
ops_router = APIRouter(prefix="/api/ops/projections", tags=["ops-projections"])


# ─── Customer-visible vendor summary ───────────────────────────────


@vendors_router.get("/summary")
def list_vendor_summary_rows(
    organization_id: str = Query(default="default"),
    order_by: str = Query(default="last_activity_at"),
    limit: int = Query(default=100, ge=1, le=500),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, user)
    rows = list_vendor_summaries(
        organization_id, order_by=order_by, limit=limit,
    )
    return {
        "organization_id": organization_id,
        "count": len(rows),
        "vendors": rows,
    }


@vendors_router.get("/{vendor_name}/summary")
def get_vendor_summary(
    vendor_name: str,
    organization_id: str = Query(default="default"),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, user)
    row = get_vendor_summary_row(organization_id, vendor_name)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"no vendor_summary row for vendor={vendor_name!r} in org={organization_id!r}",
        )
    return row


# ─── Ops endpoints ─────────────────────────────────────────────────


def _require_ops_access(user, organization_id: str) -> None:
    verify_org_access(organization_id, user)
    role = str(getattr(user, "role", "") or "").lower()
    if role not in {"admin", "ops", "owner"}:
        raise HTTPException(
            status_code=403,
            detail=f"role {role!r} cannot access projection ops endpoints",
        )


@ops_router.get("")
def list_projectors(user=Depends(get_current_user)) -> Dict[str, Any]:
    """Introspection: which projectors are registered + their kinds."""
    from clearledgr.services.box_projection import _PROJECTOR_REGISTRY  # noqa
    rows = []
    for name in list_registered_projectors():
        projector = _PROJECTOR_REGISTRY.get(name)
        if projector is None:
            continue
        rows.append({
            "projector_name": name,
            "box_types": list(getattr(projector, "box_types", ())),
            "class": type(projector).__name__,
        })
    return {"count": len(rows), "projectors": rows}


class RebuildRequest(BaseModel):
    organization_id: str
    box_type: str = "ap_item"
    limit: int = 5000


@ops_router.post("/rebuild")
def trigger_rebuild(
    body: RebuildRequest,
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Recompute every projection for the org from scratch.

    Walks ap_items and replays each through every registered
    projector. Use after schema migrations, projector logic changes,
    or when an outbox dead-letter accumulation has left rollups in
    drift. Synchronous — caller waits for completion.
    """
    _require_ops_access(user, body.organization_id)
    try:
        result = asyncio.run(rebuild_projections(
            body.organization_id,
            box_type=body.box_type,
            limit=int(body.limit or 5000),
        ))
    except RuntimeError:
        # Already inside an event loop (rare under FastAPI sync routes
        # but possible under TestClient async modes); use a new loop.
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(rebuild_projections(
                body.organization_id,
                box_type=body.box_type,
                limit=int(body.limit or 5000),
            ))
        finally:
            loop.close()
    return {"organization_id": body.organization_id, **result}
