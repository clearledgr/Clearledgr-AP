"""GDPR retention + data subject request API (Wave 3 / E3).

Operator surface for the EU/UK launch:

  Retention:
    GET  /api/workspace/gdpr/retention/eligible
        Counts of vendors past the org's retention window.
    POST /api/workspace/gdpr/retention/purge
        Trigger an automated purge run.

  Data subject requests:
    POST /api/workspace/gdpr/data-subject-requests
        Open a new request (access / erasure / portability).
    GET  /api/workspace/gdpr/data-subject-requests
        Worklist; defaults to pending + in_progress; overdue
        highlighted by due_at.
    GET  /api/workspace/gdpr/data-subject-requests/{id}
    POST /api/workspace/gdpr/data-subject-requests/{id}/process
        Execute the request per its type.
    POST /api/workspace/gdpr/data-subject-requests/{id}/reject
        Refuse with a documented reason.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db
from clearledgr.services.data_subject_request import (
    create_request,
    get_request,
    list_requests,
    process_access_request,
    process_erasure_request,
    process_portability_request,
    reject_request,
)
from clearledgr.services.gdpr_retention import (
    get_retention_days,
    identify_expired_vendors,
    run_retention_purge,
    _retention_cutoff,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace/gdpr",
    tags=["gdpr"],
)


# ── Models ──────────────────────────────────────────────────────────


class RetentionEligibleResponse(BaseModel):
    retention_days: int
    cutoff_at: str
    expired_vendor_count: int
    expired_vendors_sample: List[str]


class RetentionPurgeResponse(BaseModel):
    id: str
    cutoff_at: str
    retention_days: int
    vendors_processed: int
    ap_items_anonymized: int
    vendor_profiles_anonymized: int
    errors_count: int


class CreateDSRBody(BaseModel):
    request_type: str = Field(..., description="access | erasure | portability")
    subject_kind: str = Field(..., description="vendor | user | external_contact")
    subject_identifier: str = Field(..., min_length=1, max_length=256)
    requestor_email: Optional[str] = Field(None, max_length=320)
    requestor_relationship: Optional[str] = Field(None, max_length=64)


class RejectBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


class DSRProcessBody(BaseModel):
    notes: Optional[str] = Field(None, max_length=2000)


class DSROut(BaseModel):
    id: str
    organization_id: str
    request_type: str
    subject_kind: str
    subject_identifier: str
    requestor_email: Optional[str] = None
    requestor_relationship: Optional[str] = None
    status: str
    received_at: str
    due_at: Optional[str] = None
    processed_at: Optional[str] = None
    processed_by: Optional[str] = None
    processing_notes: Optional[str] = None
    outcome_summary: Optional[Dict[str, Any]] = None
    export_payload: Optional[Dict[str, Any]] = None


def _serialize_dsr(row: Dict[str, Any]) -> DSROut:
    return DSROut(
        id=row["id"],
        organization_id=row["organization_id"],
        request_type=row["request_type"],
        subject_kind=row["subject_kind"],
        subject_identifier=row["subject_identifier"],
        requestor_email=row.get("requestor_email"),
        requestor_relationship=row.get("requestor_relationship"),
        status=row["status"],
        received_at=row["received_at"],
        due_at=row.get("due_at"),
        processed_at=row.get("processed_at"),
        processed_by=row.get("processed_by"),
        processing_notes=row.get("processing_notes"),
        outcome_summary=row.get("outcome_summary"),
        export_payload=row.get("export_payload"),
    )


# ── Retention endpoints ─────────────────────────────────────────────


@router.get("/retention/eligible", response_model=RetentionEligibleResponse)
def retention_eligible(
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    days = get_retention_days(db, user.organization_id)
    cutoff = _retention_cutoff(days)
    vendors = identify_expired_vendors(
        db, organization_id=user.organization_id,
        cutoff_iso=cutoff, limit=20,
    )
    return RetentionEligibleResponse(
        retention_days=days,
        cutoff_at=cutoff,
        expired_vendor_count=len(vendors),
        expired_vendors_sample=vendors,
    )


@router.post("/retention/purge", response_model=RetentionPurgeResponse)
def retention_purge(
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    result = run_retention_purge(
        db, organization_id=user.organization_id, actor=user.user_id,
    )
    return RetentionPurgeResponse(**{
        "id": result["id"],
        "cutoff_at": result["cutoff_at"],
        "retention_days": result["retention_days"],
        "vendors_processed": result["vendors_processed"],
        "ap_items_anonymized": result["ap_items_anonymized"],
        "vendor_profiles_anonymized": result["vendor_profiles_anonymized"],
        "errors_count": result["errors_count"],
    })


# ── Data subject request endpoints ──────────────────────────────────


@router.post("/data-subject-requests", response_model=DSROut)
def open_request(
    body: CreateDSRBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    try:
        row = create_request(
            db,
            organization_id=user.organization_id,
            request_type=body.request_type,
            subject_kind=body.subject_kind,
            subject_identifier=body.subject_identifier,
            requestor_email=body.requestor_email,
            requestor_relationship=body.requestor_relationship,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _serialize_dsr(row)


@router.get("/data-subject-requests", response_model=List[DSROut])
def list_dsr(
    status: Optional[str] = Query(default=None),
    request_type: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    try:
        rows = list_requests(
            db,
            organization_id=user.organization_id,
            status=status,
            request_type=request_type,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [_serialize_dsr(r) for r in rows]


@router.get("/data-subject-requests/{request_id}", response_model=DSROut)
def get_dsr(
    request_id: str,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    row = get_request(db, request_id)
    if row is None or row.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="request_not_found")
    return _serialize_dsr(row)


@router.post(
    "/data-subject-requests/{request_id}/process",
    response_model=DSROut,
)
def process_dsr(
    request_id: str,
    body: Optional[DSRProcessBody] = None,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    row = get_request(db, request_id)
    if row is None or row.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="request_not_found")
    if row.get("status") not in ("pending", "in_progress"):
        raise HTTPException(
            status_code=400,
            detail=f"already_processed:{row.get('status')}",
        )
    rt = row["request_type"]
    notes = body.notes if body else None
    try:
        if rt == "access":
            result = process_access_request(
                db, request_id, actor=user.user_id,
            )
        elif rt == "erasure":
            result = process_erasure_request(
                db, request_id, actor=user.user_id, notes=notes,
            )
        elif rt == "portability":
            result = process_portability_request(
                db, request_id, actor=user.user_id,
            )
        else:
            raise HTTPException(
                status_code=400, detail=f"unknown_request_type:{rt}",
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _serialize_dsr(result)


@router.post(
    "/data-subject-requests/{request_id}/reject",
    response_model=DSROut,
)
def reject_dsr(
    request_id: str,
    body: RejectBody,
    user: TokenData = Depends(get_current_user),
):
    db = get_db()
    row = get_request(db, request_id)
    if row is None or row.get("organization_id") != user.organization_id:
        raise HTTPException(status_code=404, detail="request_not_found")
    if row.get("status") not in ("pending", "in_progress"):
        raise HTTPException(
            status_code=400,
            detail=f"already_processed:{row.get('status')}",
        )
    fresh = reject_request(
        db, request_id, reason=body.reason, actor=user.user_id,
    )
    return _serialize_dsr(fresh)
