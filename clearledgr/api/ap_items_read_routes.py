"""Read-focused AP item routes."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from clearledgr.core.auth import get_current_user
from clearledgr.core.money import money_sum, money_to_float
from clearledgr.api.deps import verify_org_access
from clearledgr.services.ap_operator_audit import normalize_operator_audit_events


router = APIRouter()


class _SharedProxy:
    def __init__(self) -> None:
        self._module = None

    def _resolve(self):
        if self._module is None:
            import clearledgr.services.ap_item_service as module

            self._module = module
        return self._module

    def __getattr__(self, name: str):
        return getattr(self._resolve(), name)


shared = _SharedProxy()


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


@router.get("/search")
def search_ap_items(
    organization_id: str = Query(default="default"),
    q: str = Query(default=""),
    limit: int = Query(default=12, ge=1, le=50),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, _user)
    db = shared.get_db()
    query = str(q or "").strip().lower()
    items = db.list_ap_items(organization_id, limit=1000)
    matches = []
    for item in items:
        if not query:
            matches.append(item)
            continue
        haystack = " ".join([
            str(item.get("vendor_name") or ""),
            str(item.get("invoice_number") or ""),
            str(item.get("subject") or ""),
            str(item.get("sender") or ""),
            str(item.get("thread_id") or ""),
            str(item.get("message_id") or ""),
        ]).lower()
        if query in haystack:
            matches.append(item)
    matches = sorted(
        matches,
        key=lambda row: shared._safe_sort_timestamp(row.get("updated_at") or row.get("created_at")),
        reverse=True,
    )[:limit]
    return {
        "organization_id": organization_id,
        "query": q,
        "items": [shared.build_worklist_item(db, item) for item in matches],
        "count": len(matches),
    }


@router.get("/compose/lookup")
def lookup_compose_record(
    organization_id: str = Query(default="default"),
    draft_id: str = Query(default=""),
    thread_id: str = Query(default=""),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    verify_org_access(organization_id, _user)
    db = shared.get_db()
    normalized_draft_id = str(draft_id or "").strip()
    normalized_thread_id = str(thread_id or "").strip()

    def _build_found(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "found",
            "ap_item": shared.build_worklist_item(db, item),
        }

    if normalized_draft_id and hasattr(db, "list_ap_item_sources_by_ref"):
        for row in db.list_ap_item_sources_by_ref("compose_draft", normalized_draft_id):
            candidate_id = str(row.get("ap_item_id") or "").strip()
            if not candidate_id:
                continue
            item = db.get_ap_item(candidate_id)
            if item and str(item.get("organization_id") or organization_id or "default").strip() == organization_id:
                return _build_found(item)

    if normalized_thread_id and hasattr(db, "get_ap_item_by_thread"):
        item = db.get_ap_item_by_thread(organization_id, normalized_thread_id)
        if item:
            return _build_found(item)

    return {"status": "missing", "ap_item": None}


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


@router.get("/aging")
def get_ap_aging_report(
    organization_id: str = Query(default="default"),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """AP aging report — open payables bucketed by days past due."""
    verify_org_access(organization_id, _user)
    from clearledgr.services.ap_aging_report import get_ap_aging_report as _get_report
    report = _get_report(organization_id)
    return report.generate()


@router.get("/audit/export")
def export_audit_trail(
    organization_id: str = Query(default="default"),
    format: str = Query(default="csv"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    vendor: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = Query(default=10000, ge=1, le=50000),
    _user=Depends(get_current_user),
) -> Any:
    """Export audit trail events as CSV or JSON.

    Filters by organization, date range, vendor, and AP item state.
    """
    verify_org_access(organization_id, _user)
    db = shared.get_db()

    # Fetch audit events joined with AP item data
    events = db.list_recent_ap_audit_events(organization_id, limit=limit)

    # Apply optional filters
    if start_date:
        events = [
            e for e in events
            if str(e.get("ts") or "") >= start_date
        ]
    if end_date:
        events = [
            e for e in events
            if str(e.get("ts") or "") <= end_date
        ]
    if vendor:
        vendor_lower = vendor.lower()
        events = [
            e for e in events
            if vendor_lower in str(e.get("vendor_name") or "").lower()
        ]
    if state:
        events = [
            e for e in events
            if str(e.get("new_state") or "") == state
            or str(e.get("prev_state") or "") == state
        ]

    export_fields = [
        # Box-keyed identifiers. For AP rows ``box_id`` equals the
        # original AP item id, so compliance consumers that previously
        # joined on ``ap_item_id`` can pivot by filtering
        # ``box_type='ap_item'``.
        "id", "box_id", "box_type",
        "event_type", "ts", "actor_type", "actor_id",
        "prev_state", "new_state", "decision_reason", "organization_id",
        "vendor_name", "amount", "currency", "invoice_number",
    ]

    if format == "json":
        rows = []
        for e in events:
            row = {field: e.get(field) for field in export_fields}
            row["details"] = e.get("payload_json") or {}
            rows.append(row)
        return {"events": rows, "count": len(rows), "organization_id": organization_id}

    # Default: CSV streaming response
    def _generate_csv():
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=export_fields + ["details"])
        writer.writeheader()
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for e in events:
            row = {field: e.get(field, "") for field in export_fields}
            details = e.get("payload_json")
            if isinstance(details, dict):
                import json
                row["details"] = json.dumps(details)
            else:
                row["details"] = str(details or "")
            writer.writerow(row)
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return StreamingResponse(
        _generate_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_trail.csv"},
    )


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
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    events = db.list_ap_audit_events(ap_item_id)
    if browser_only:
        events = [event for event in events if str(event.get("event_type") or "").startswith("browser_")]
    return {"events": normalize_operator_audit_events(events)}


@router.get("/{ap_item_id}/box")
def get_ap_item_box(
    ap_item_id: str,
    fresh: bool = Query(default=False),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the full §8 Box contract for this AP item.

    state + timeline + exceptions + outcome. This is the canonical
    read surfaces should consume — Gmail sidebar, Slack cards, admin
    console, Backoffice webhooks. Direct reads from ap_items.exception_code
    or ap_items.erp_reference miss the audit trail the deck promises
    customers.

    Gap 6: reads from the ``box_summary`` projection first; falls
    through to live composition (audit_events join) when the
    projection is missing or stale, or when ``?fresh=true`` is
    passed. The projection is updated by BoxSummaryProjector via the
    state-transition outbox so it's eventually consistent within
    seconds of every transition.
    """
    db = shared.get_db()
    item = shared._require_item(
        db, ap_item_id,
        expected_organization_id=getattr(_user, "organization_id", None),
    )
    verify_org_access(item.get("organization_id") or "default", _user)

    if not fresh:
        try:
            from clearledgr.services.box_projection import get_box_summary_row
            projection = get_box_summary_row("ap_item", ap_item_id, db=db)
        except Exception:
            projection = None
        if projection and not _is_projection_stale(db, ap_item_id, projection):
            return {
                "box_id": ap_item_id,
                "box_type": "ap_item",
                "state": projection.get("state") or item.get("state"),
                "timeline": projection.get("timeline_preview") or [],
                "exceptions": projection.get("exceptions") or [],
                "outcome": projection.get("outcome"),
                "summary": projection.get("summary") or {},
                "from_projection": True,
                "projection_updated_at": projection.get("updated_at"),
            }

    timeline = normalize_operator_audit_events(db.list_ap_audit_events(ap_item_id))

    exceptions: list = []
    if hasattr(db, "list_box_exceptions"):
        try:
            exceptions = db.list_box_exceptions(
                box_type="ap_item",
                box_id=ap_item_id,
            )
        except Exception:
            exceptions = []

    outcome = None
    if hasattr(db, "get_box_outcome"):
        try:
            outcome = db.get_box_outcome(
                box_type="ap_item",
                box_id=ap_item_id,
            )
        except Exception:
            outcome = None

    return {
        "box_id": ap_item_id,
        "box_type": "ap_item",
        "state": item.get("state"),
        "timeline": timeline,
        "exceptions": exceptions,
        "outcome": outcome,
        "from_projection": False,
    }


def _is_projection_stale(db: Any, ap_item_id: str, projection: Dict[str, Any]) -> bool:
    """A projection is stale when audit_events has rows newer than
    the projection's last_event_id. Falls open (treats as fresh) on
    any read error so the projection still serves under DB pressure."""
    last_event_id = projection.get("last_event_id")
    if not last_event_id:
        return True
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id FROM audit_events
                WHERE box_id = %s
                ORDER BY ts DESC LIMIT 1
                """,
                (ap_item_id,),
            )
            row = cur.fetchone()
        if not row:
            return False
        tip = str(row["id"]) if hasattr(row, "__getitem__") else str(row[0])
        return tip != str(last_event_id)
    except Exception:
        return False


@router.get("/{ap_item_id}/history")
def get_ap_item_history(
    ap_item_id: str,
    at: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Time-travel query against the ``box_summary_history`` table.

    With ``?at=<ISO ts>`` returns the latest snapshot at or before
    ``at`` (one row). Without ``at``, returns the most recent
    snapshots in descending order. Backed by Gap 6's append-only
    history projection.
    """
    db = shared.get_db()
    item = shared._require_item(
        db, ap_item_id,
        expected_organization_id=getattr(_user, "organization_id", None),
    )
    verify_org_access(item.get("organization_id") or "default", _user)

    from clearledgr.services.box_projection import get_box_history
    snapshots = get_box_history(
        "ap_item", ap_item_id,
        at=at, limit=limit, db=db,
    )
    return {
        "box_type": "ap_item",
        "box_id": ap_item_id,
        "at": at,
        "count": len(snapshots),
        "snapshots": snapshots,
    }


@router.get("/{ap_item_id}/sources")
def get_ap_item_sources(
    ap_item_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    sources = db.list_ap_item_sources(ap_item_id)
    return {"sources": sources, "source_count": len(sources)}


@router.get("/{ap_item_id}/tasks")
def get_ap_item_tasks(
    ap_item_id: str,
    include_completed: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    from clearledgr.services.email_tasks import get_tasks_for_ap_item

    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    tasks = get_tasks_for_ap_item(
        ap_item_id,
        thread_id=str(item.get("thread_id") or "").strip() or None,
        organization_id=item.get("organization_id") or "default",
        include_completed=include_completed,
        limit=limit,
    )
    return {"tasks": tasks, "count": len(tasks)}


@router.get("/{ap_item_id}/notes")
def get_ap_item_notes(
    ap_item_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    metadata = shared._parse_json(item.get("metadata"))
    notes = metadata.get("record_notes")
    if not isinstance(notes, list):
        notes = []
    return {"notes": notes, "count": len(notes)}


@router.get("/{ap_item_id}/comments")
def get_ap_item_comments(
    ap_item_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    metadata = shared._parse_json(item.get("metadata"))
    comments = metadata.get("record_comments")
    if not isinstance(comments, list):
        comments = []
    return {"comments": comments, "count": len(comments)}


@router.get("/{ap_item_id}/files")
def get_ap_item_files(
    ap_item_id: str,
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    metadata = shared._parse_json(item.get("metadata"))
    files = metadata.get("record_file_links")
    if not isinstance(files, list):
        files = []
    return {"files": files, "count": len(files)}


@router.get("/{ap_item_id}/context")
def get_ap_item_context(
    ap_item_id: str,
    refresh: bool = Query(False),
    _user=Depends(get_current_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
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


# ==================== §3 MULTI-ENTITY: CONSOLIDATED & DRILL-DOWN ====================


@router.get("/consolidated")
def get_consolidated_pipeline(
    parent_org_id: str = Query(...),
    limit: int = Query(default=500, ge=1, le=2000),
    _user=Depends(get_current_user),
):
    """§3 Multi-entity: consolidated pipeline across all child entities.

    Returns items grouped by entity with per-entity totals.
    Auth: requires Financial Controller or higher.
    """
    from clearledgr.core.auth import has_financial_controller

    if not has_financial_controller(_user):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="financial_controller_required")

    verify_org_access(parent_org_id, _user)
    db = shared.get_db()

    # Get all child org IDs
    child_orgs = db.get_child_organizations(parent_org_id) if hasattr(db, "get_child_organizations") else []
    all_org_ids = [parent_org_id] + [c["id"] for c in child_orgs]

    # Get all entities across the hierarchy
    all_entities = []
    for oid in all_org_ids:
        try:
            entities = db.list_entities(oid, include_inactive=False) if hasattr(db, "list_entities") else []
            all_entities.extend(entities)
        except Exception:
            pass

    # Gather AP items per entity
    by_entity = {}
    for entity in all_entities:
        eid = entity.get("id", "")
        oid = entity.get("organization_id", parent_org_id)
        items = db.list_ap_items(oid, entity_id=eid, limit=limit)
        by_entity[eid] = {
            "entity": {
                "id": eid,
                "name": entity.get("name", ""),
                "code": entity.get("code", ""),
                "organization_id": oid,
            },
            "items": items,
            "totals": {
                "count": len(items),
                "in_flight": sum(1 for i in items if i.get("state") not in ("closed", "rejected")),
                "exceptions": sum(1 for i in items if i.get("state") in ("needs_info", "failed_post")),
                "total_amount": money_to_float(money_sum(i.get("amount") for i in items)),
            },
        }

    # Also include items with no entity (org-level)
    unassigned = db.list_ap_items(parent_org_id, limit=limit)
    unassigned_items = [i for i in unassigned if not i.get("entity_id")]
    if unassigned_items:
        by_entity["_unassigned"] = {
            "entity": {"id": "_unassigned", "name": "Unassigned", "code": "", "organization_id": parent_org_id},
            "items": unassigned_items,
            "totals": {
                "count": len(unassigned_items),
                "in_flight": sum(1 for i in unassigned_items if i.get("state") not in ("closed", "rejected")),
                "exceptions": sum(1 for i in unassigned_items if i.get("state") in ("needs_info", "failed_post")),
                "total_amount": money_to_float(money_sum(i.get("amount") for i in unassigned_items)),
            },
        }

    grand_total = {
        "entities": len(by_entity),
        "total_items": sum(e["totals"]["count"] for e in by_entity.values()),
        "total_in_flight": sum(e["totals"]["in_flight"] for e in by_entity.values()),
        "total_exceptions": sum(e["totals"]["exceptions"] for e in by_entity.values()),
        "total_amount": money_to_float(money_sum(e["totals"]["total_amount"] for e in by_entity.values())),
    }

    return {
        "parent_org_id": parent_org_id,
        "by_entity": by_entity,
        "grand_total": grand_total,
    }
