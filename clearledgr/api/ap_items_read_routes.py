"""Read-focused AP item routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query

import clearledgr.api.ap_items as shared
from clearledgr.core.auth import get_current_user
from clearledgr.api.deps import verify_org_access
from clearledgr.services.ap_operator_audit import normalize_operator_audit_events


router = APIRouter()


@router.get("/upcoming")
def get_upcoming_ap_tasks(
    organization_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=200),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, _user)
    db = shared.get_db()
    return shared._build_upcoming_tasks_payload(db, organization_id, limit=limit)


@router.get("/vendors")
def get_vendor_directory(
    organization_id: str = Query(default="default"),
    search: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, _user)
    db = shared.get_db()
    rows = shared._build_vendor_summary_rows(db, organization_id, search=search, limit=limit)
    return {
        "organization_id": organization_id,
        "vendors": rows,
        "count": len(rows),
    }


@router.get("/vendors/{vendor_name}")
def get_vendor_record(
    vendor_name: str,
    organization_id: str = Query(default="default"),
    days: int = Query(default=180, ge=30, le=365),
    invoice_limit: int = Query(default=20, ge=6, le=30),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, _user)
    db = shared.get_db()
    return shared._build_vendor_detail_payload(
        db,
        organization_id,
        vendor_name,
        days=days,
        invoice_limit=invoice_limit,
    )


@router.get("/metrics/aggregation")
def get_ap_aggregation_metrics(
    organization_id: str = Query(default="default"),
    limit: int = Query(default=10000, ge=100, le=50000),
    vendor_limit: int = Query(default=10, ge=1, le=50),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, _user)
    db = shared.get_db()
    metrics = db.get_ap_aggregation_metrics(
        organization_id=organization_id,
        limit=limit,
        vendor_limit=vendor_limit,
    )
    return {"metrics": metrics}


@router.get("/{ap_item_id}")
def get_ap_item_detail(
    ap_item_id: str,
    organization_id: str = Query(default="default"),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, _user)
    db = shared.get_db()
    item = shared._resolve_item_for_detail(
        db,
        organization_id=organization_id,
        ap_item_ref=ap_item_id,
    )
    return shared.build_worklist_item(db, item)


@router.get("/{ap_item_id}/audit")
def get_ap_item_audit(
    ap_item_id: str,
    browser_only: bool = Query(False),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id)
    verify_org_access(item.get("organization_id") or "default", _user)
    events = db.list_ap_audit_events(ap_item_id)
    if browser_only:
        events = [event for event in events if str(event.get("event_type") or "").startswith("browser_")]
    return {"events": normalize_operator_audit_events(events)}


@router.get("/{ap_item_id}/sources")
def get_ap_item_sources(
    ap_item_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id)
    verify_org_access(item.get("organization_id") or "default", _user)
    sources = db.list_ap_item_sources(ap_item_id)
    return {"sources": sources, "source_count": len(sources)}


@router.get("/{ap_item_id}/context")
def get_ap_item_context(
    ap_item_id: str,
    refresh: bool = Query(False),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id)
    verify_org_access(item.get("organization_id") or "default", _user)

    if not refresh:
        cached = db.get_ap_item_context_cache(ap_item_id)
        if cached and isinstance(cached.get("context_json"), dict):
            context = dict(cached.get("context_json") or {})
            schema_version = str(context.get("schema_version") or "")
            if not schema_version.startswith("2."):
                context = {}
            if context:
                updated_at = shared._parse_iso(cached.get("updated_at"))
                if updated_at:
                    age_seconds = max(0, int((datetime.now(timezone.utc) - updated_at).total_seconds()))
                    freshness = context.get("freshness") if isinstance(context.get("freshness"), dict) else {}
                    freshness["age_seconds"] = age_seconds
                    freshness["is_stale"] = age_seconds > 300
                    context["freshness"] = freshness
                return context

    context = shared._build_context_payload(db, item)
    db.upsert_ap_item_context_cache(ap_item_id, context)
    return context
