"""AP item APIs used by the Gmail extension focus-first sidebar."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.database import ClearledgrDB, get_db


router = APIRouter(prefix="/api/ap/items", tags=["ap-items"])


class LinkSourceRequest(BaseModel):
    source_type: str = Field(..., min_length=1)
    source_ref: str = Field(..., min_length=1)
    subject: Optional[str] = None
    sender: Optional[str] = None
    detected_at: Optional[str] = None
    metadata: Dict[str, Any] = {}


class MergeItemsRequest(BaseModel):
    source_ap_item_id: str = Field(..., min_length=1)
    actor_id: str = Field(default="system", min_length=1)
    reason: str = Field(default="manual_merge", min_length=1)


class SplitSourceRequest(BaseModel):
    source_type: str = Field(..., min_length=1)
    source_ref: str = Field(..., min_length=1)


class SplitItemRequest(BaseModel):
    actor_id: str = Field(default="system", min_length=1)
    reason: str = Field(default="manual_split", min_length=1)
    sources: List[SplitSourceRequest] = []


def _parse_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _build_primary_source(item: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    for source in sources:
        source_type = str(source.get("source_type") or "").strip().lower()
        source_ref = str(source.get("source_ref") or "").strip()
        if source_type == "gmail_thread" and source_ref:
            return {"thread_id": source_ref, "message_id": item.get("message_id")}
        if source_type == "gmail_message" and source_ref:
            return {"thread_id": item.get("thread_id"), "message_id": source_ref}
    return {"thread_id": item.get("thread_id"), "message_id": item.get("message_id")}


def build_worklist_item(db: ClearledgrDB, item: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(item or {})
    metadata = _parse_json(payload.get("metadata"))
    sources = db.list_ap_item_sources(payload.get("id"))

    # Preserve legacy behavior when source links do not exist yet.
    if not sources:
        if payload.get("thread_id"):
            sources.append(
                {
                    "source_type": "gmail_thread",
                    "source_ref": payload.get("thread_id"),
                    "subject": payload.get("subject"),
                    "sender": payload.get("sender"),
                    "detected_at": payload.get("created_at"),
                    "metadata": {},
                }
            )
        if payload.get("message_id"):
            sources.append(
                {
                    "source_type": "gmail_message",
                    "source_ref": payload.get("message_id"),
                    "subject": payload.get("subject"),
                    "sender": payload.get("sender"),
                    "detected_at": payload.get("created_at"),
                    "metadata": {},
                }
            )

    payload["source_count"] = int(metadata.get("source_count") or len(sources))
    payload["primary_source"] = _build_primary_source(payload, sources)
    payload["merge_reason"] = metadata.get("merge_reason")
    payload["has_context_conflict"] = bool(
        metadata.get("has_context_conflict") or metadata.get("context_conflict")
    )
    payload["exception_code"] = metadata.get("exception_code") or payload.get("exception_code")
    payload["exception_severity"] = metadata.get("exception_severity") or payload.get("exception_severity")
    payload["budget_status"] = metadata.get("budget_status") or payload.get("budget_status")
    payload["risk_signals"] = metadata.get("risk_signals") or {}
    payload["source_ranking"] = metadata.get("source_ranking") or {}
    payload["navigator"] = metadata.get("navigator") or {}
    payload["conflict_actions"] = metadata.get("conflict_actions") if isinstance(metadata.get("conflict_actions"), list) else []
    if metadata.get("priority_score") is not None:
        payload["priority_score"] = metadata.get("priority_score")
    return payload


def _require_item(db: ClearledgrDB, ap_item_id: str) -> Dict[str, Any]:
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    return item


def _build_context_payload(db: ClearledgrDB, item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _parse_json(item.get("metadata"))
    sources = db.list_ap_item_sources(item["id"])
    approvals = db.list_approvals_by_item(item["id"], limit=20)
    audit_events = db.list_ap_audit_events(item["id"])
    now = datetime.now(timezone.utc)

    source_types: Dict[str, int] = {}
    for source in sources:
        source_type = str(source.get("source_type") or "unknown")
        source_types[source_type] = source_types.get(source_type, 0) + 1
    distribution = ", ".join(f"{k}:{v}" for k, v in sorted(source_types.items()))

    browser_events = [
        event for event in audit_events if str(event.get("event_type") or "").startswith("browser_")
    ]
    recent_browser_events: List[Dict[str, Any]] = []
    for event in browser_events[-10:]:
        payload = event.get("payload_json") or {}
        request_payload = payload.get("request") if isinstance(payload, dict) else {}
        recent_browser_events.append(
            {
                "event_id": event.get("id"),
                "ts": event.get("ts"),
                "status": payload.get("status") if isinstance(payload, dict) else None,
                "tool_name": (request_payload or {}).get("tool_name"),
                "command_id": payload.get("command_id") if isinstance(payload, dict) else None,
                "result": payload.get("result") if isinstance(payload, dict) else None,
            }
        )

    payment_portals = [
        source for source in sources if str(source.get("source_type") or "").lower() == "portal"
    ]
    procurement = [
        source for source in sources if str(source.get("source_type") or "").lower() == "procurement"
    ]
    dms_documents = [
        source for source in sources if str(source.get("source_type") or "").lower() == "dms"
    ]

    latest_approval = approvals[0] if approvals else None
    latest_approval_payload = _parse_json(latest_approval.get("decision_payload")) if latest_approval else {}
    thread_preview = latest_approval_payload.get("thread_preview")
    if not isinstance(thread_preview, list):
        thread_preview = []

    erp_reference = item.get("erp_reference")
    connector_available = bool(erp_reference or metadata.get("erp_connector_available") or metadata.get("erp"))

    context = {
        "ap_item_id": item.get("id"),
        "generated_at": now.isoformat(),
        "freshness": {
            "age_seconds": 0,
            "is_stale": False,
        },
        "source_quality": {
            "distribution": distribution or "none",
            "total_sources": len(sources),
        },
        "email": {
            "source_count": len(sources),
            "sources": sources,
        },
        "web": {
            "browser_event_count": len(browser_events),
            "recent_browser_events": recent_browser_events[-5:],
            "related_portals": payment_portals,
            "payment_portals": payment_portals,
            "procurement": procurement,
            "dms_documents": dms_documents,
            "connector_coverage": {
                "payment_portal": bool(payment_portals),
                "procurement": bool(procurement),
                "dms": bool(dms_documents),
            },
        },
        "approvals": {
            "count": len(approvals),
            "latest": latest_approval,
            "slack": {
                "thread_preview": thread_preview[:5],
            },
            "budget": metadata.get("budget") or metadata.get("budget_check_result") or {},
        },
        "erp": {
            "state": item.get("state"),
            "erp_reference": erp_reference,
            "erp_posted_at": item.get("erp_posted_at"),
            "connector_available": connector_available,
        },
        "po_match": metadata.get("po_match") or metadata.get("po_match_result") or {},
        "budget": metadata.get("budget") or metadata.get("budget_check_result") or {},
        "risk_signals": metadata.get("risk_signals") or {},
    }
    return context


@router.get("/{ap_item_id}/audit")
def get_ap_item_audit(ap_item_id: str, browser_only: bool = Query(False)) -> Dict[str, Any]:
    db = get_db()
    _require_item(db, ap_item_id)
    events = db.list_ap_audit_events(ap_item_id)
    if browser_only:
        events = [event for event in events if str(event.get("event_type") or "").startswith("browser_")]
    return {"events": events}


@router.get("/{ap_item_id}/sources")
def get_ap_item_sources(ap_item_id: str) -> Dict[str, Any]:
    db = get_db()
    _require_item(db, ap_item_id)
    sources = db.list_ap_item_sources(ap_item_id)
    return {"sources": sources, "source_count": len(sources)}


@router.post("/{ap_item_id}/sources/link")
def link_ap_item_source(ap_item_id: str, request: LinkSourceRequest) -> Dict[str, Any]:
    db = get_db()
    _require_item(db, ap_item_id)
    source = db.link_ap_item_source(
        {
            "ap_item_id": ap_item_id,
            "source_type": request.source_type,
            "source_ref": request.source_ref,
            "subject": request.subject,
            "sender": request.sender,
            "detected_at": request.detected_at,
            "metadata": request.metadata or {},
        }
    )
    return {"source": source}


@router.get("/{ap_item_id}/context")
def get_ap_item_context(ap_item_id: str, refresh: bool = Query(False)) -> Dict[str, Any]:
    db = get_db()
    item = _require_item(db, ap_item_id)

    if not refresh:
        cached = db.get_ap_item_context_cache(ap_item_id)
        if cached and isinstance(cached.get("context_json"), dict):
            context = dict(cached.get("context_json") or {})
            updated_at = _parse_iso(cached.get("updated_at"))
            if updated_at:
                age_seconds = max(0, int((datetime.now(timezone.utc) - updated_at).total_seconds()))
                freshness = context.get("freshness") if isinstance(context.get("freshness"), dict) else {}
                freshness["age_seconds"] = age_seconds
                freshness["is_stale"] = age_seconds > 300
                context["freshness"] = freshness
            return context

    context = _build_context_payload(db, item)
    db.upsert_ap_item_context_cache(ap_item_id, context)
    return context


@router.post("/{ap_item_id}/merge")
def merge_ap_items(ap_item_id: str, request: MergeItemsRequest) -> Dict[str, Any]:
    db = get_db()
    target = _require_item(db, ap_item_id)
    source = _require_item(db, request.source_ap_item_id)

    if target.get("id") == source.get("id"):
        raise HTTPException(status_code=400, detail="cannot_merge_same_item")
    if str(target.get("organization_id") or "default") != str(source.get("organization_id") or "default"):
        raise HTTPException(status_code=400, detail="organization_mismatch")

    moved_count = 0
    for source_link in db.list_ap_item_sources(source["id"]):
        moved = db.move_ap_item_source(
            from_ap_item_id=source["id"],
            to_ap_item_id=target["id"],
            source_type=source_link.get("source_type"),
            source_ref=source_link.get("source_ref"),
        )
        if moved:
            moved_count += 1

    # Preserve legacy source pointers as explicit links when present.
    if source.get("thread_id"):
        db.link_ap_item_source(
            {
                "ap_item_id": target["id"],
                "source_type": "gmail_thread",
                "source_ref": source.get("thread_id"),
                "subject": source.get("subject"),
                "sender": source.get("sender"),
                "detected_at": source.get("created_at"),
                "metadata": {"merge_origin": source.get("id")},
            }
        )
    if source.get("message_id"):
        db.link_ap_item_source(
            {
                "ap_item_id": target["id"],
                "source_type": "gmail_message",
                "source_ref": source.get("message_id"),
                "subject": source.get("subject"),
                "sender": source.get("sender"),
                "detected_at": source.get("created_at"),
                "metadata": {"merge_origin": source.get("id")},
            }
        )

    target_meta = _parse_json(target.get("metadata"))
    merge_history = target_meta.get("merge_history")
    if not isinstance(merge_history, list):
        merge_history = []
    merge_history.append(
        {
            "source_ap_item_id": source["id"],
            "reason": request.reason,
            "actor_id": request.actor_id,
            "merged_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    target_meta["merge_history"] = merge_history
    target_meta["merge_reason"] = request.reason
    target_meta["has_context_conflict"] = False
    target_meta["source_count"] = len(db.list_ap_item_sources(target["id"]))
    db.update_ap_item(target["id"], metadata=target_meta)

    source_meta = _parse_json(source.get("metadata"))
    source_meta["merged_into"] = target["id"]
    source_meta["merge_reason"] = request.reason
    db.update_ap_item(source["id"], state="merged", metadata=source_meta)

    db.append_ap_audit_event(
        {
            "ap_item_id": target["id"],
            "event_type": "ap_item_merged",
            "actor_type": "user",
            "actor_id": request.actor_id,
            "payload_json": {
                "target_ap_item_id": target["id"],
                "source_ap_item_id": source["id"],
                "reason": request.reason,
                "moved_sources": moved_count,
            },
            "organization_id": target.get("organization_id") or "default",
            "source": "ap_items_api",
            "decision_reason": request.reason,
            "idempotency_key": f"merge:{target['id']}:{source['id']}",
        }
    )

    return {
        "status": "merged",
        "target_ap_item_id": target["id"],
        "source_ap_item_id": source["id"],
        "moved_sources": moved_count,
    }


@router.post("/{ap_item_id}/split")
def split_ap_item(ap_item_id: str, request: SplitItemRequest) -> Dict[str, Any]:
    db = get_db()
    parent = _require_item(db, ap_item_id)
    if not request.sources:
        raise HTTPException(status_code=400, detail="sources_required")

    created_items: List[Dict[str, Any]] = []
    parent_meta = _parse_json(parent.get("metadata"))
    now = datetime.now(timezone.utc).isoformat()

    for source in request.sources:
        current_sources = db.list_ap_item_sources(parent["id"], source_type=source.source_type)
        current = next((row for row in current_sources if row.get("source_ref") == source.source_ref), None)
        if not current:
            continue

        split_payload = {
            "invoice_key": f"{parent.get('invoice_key') or parent['id']}#split#{source.source_type}:{source.source_ref}",
            "thread_id": parent.get("thread_id"),
            "message_id": parent.get("message_id"),
            "subject": current.get("subject") or parent.get("subject"),
            "sender": current.get("sender") or parent.get("sender"),
            "vendor_name": parent.get("vendor_name"),
            "amount": parent.get("amount"),
            "currency": parent.get("currency") or "USD",
            "invoice_number": parent.get("invoice_number"),
            "invoice_date": parent.get("invoice_date"),
            "due_date": parent.get("due_date"),
            "state": "needs_info",
            "confidence": parent.get("confidence") or 0,
            "approval_required": bool(parent.get("approval_required", True)),
            "organization_id": parent.get("organization_id") or "default",
            "user_id": parent.get("user_id"),
            "metadata": {
                **parent_meta,
                "split_from_ap_item_id": parent["id"],
                "split_reason": request.reason,
                "split_actor_id": request.actor_id,
                "split_source": {"source_type": source.source_type, "source_ref": source.source_ref},
                "split_at": now,
            },
        }
        child = db.create_ap_item(split_payload)
        db.move_ap_item_source(
            from_ap_item_id=parent["id"],
            to_ap_item_id=child["id"],
            source_type=source.source_type,
            source_ref=source.source_ref,
        )

        if source.source_type == "gmail_thread":
            db.update_ap_item(child["id"], thread_id=source.source_ref)
        if source.source_type == "gmail_message":
            db.update_ap_item(child["id"], message_id=source.source_ref)

        db.append_ap_audit_event(
            {
                "ap_item_id": child["id"],
                "event_type": "ap_item_split_created",
                "actor_type": "user",
                "actor_id": request.actor_id,
                "payload_json": {
                    "parent_ap_item_id": parent["id"],
                    "source_type": source.source_type,
                    "source_ref": source.source_ref,
                    "reason": request.reason,
                },
                "organization_id": parent.get("organization_id") or "default",
                "source": "ap_items_api",
            }
        )
        created_items.append(child)

    if not created_items:
        raise HTTPException(status_code=400, detail="no_sources_split")

    parent_meta["source_count"] = len(db.list_ap_item_sources(parent["id"]))
    db.update_ap_item(parent["id"], metadata=parent_meta)

    return {
        "status": "split",
        "parent_ap_item_id": parent["id"],
        "created_items": [build_worklist_item(db, item) for item in created_items],
    }

