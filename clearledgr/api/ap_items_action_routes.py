"""Mutating AP item routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import clearledgr.api.ap_items as shared
from clearledgr.api.ap_item_contracts import (
    BulkResolveFieldReviewRequest,
    LinkSourceRequest,
    MergeItemsRequest,
    ResolveEntityRouteRequest,
    ResolveFieldReviewRequest,
    ResolveNonInvoiceReviewRequest,
    ResubmitRejectedItemRequest,
    SplitItemRequest,
)
from clearledgr.core.ap_states import APState
from clearledgr.core.auth import require_ops_user
from clearledgr.core.errors import safe_error
from clearledgr.api.deps import verify_org_access
from clearledgr.core.ap_entity_routing import (
    match_entity_candidate,
    normalize_entity_candidate,
    resolve_entity_routing,
)


router = APIRouter()


@router.post("/{ap_item_id}/field-review/resolve")
async def resolve_ap_item_field_review(
    ap_item_id: str,
    request: ResolveFieldReviewRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    result = await shared._execute_field_review_resolution(
        db,
        ap_item_id=ap_item_id,
        request=request,
        organization_id=organization_id,
        user=user,
    )
    result["requires_field_review"] = bool((result.get("ap_item") or {}).get("requires_field_review"))
    return result


@router.post("/field-review/bulk-resolve")
async def bulk_resolve_ap_item_field_review(
    request: BulkResolveFieldReviewRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    ap_item_ids = [
        str(ap_item_id or "").strip()
        for ap_item_id in (request.ap_item_ids or [])
        if str(ap_item_id or "").strip()
    ]
    ap_item_ids = list(dict.fromkeys(ap_item_ids))[:50]
    if not ap_item_ids:
        raise HTTPException(status_code=400, detail="missing_ap_item_ids")

    single_request = ResolveFieldReviewRequest(
        field=request.field,
        source=request.source,
        manual_value=request.manual_value,
        note=request.note,
        auto_resume=request.auto_resume,
    )
    results: List[Dict[str, Any]] = []
    success_count = 0
    auto_resumed_count = 0

    for ap_item_id in ap_item_ids:
        try:
            result = await shared._execute_field_review_resolution(
                db,
                ap_item_id=ap_item_id,
                request=single_request,
                organization_id=organization_id,
                user=user,
            )
            result["requires_field_review"] = bool((result.get("ap_item") or {}).get("requires_field_review"))
            success_count += 1
            auto_resumed_count += int(bool(result.get("auto_resumed")))
            results.append(result)
        except HTTPException as exc:
            results.append(
                {
                    "status": "error",
                    "ap_item_id": ap_item_id,
                    "reason": str(exc.detail),
                    "http_status": exc.status_code,
                }
            )

    return {
        "status": "completed" if success_count == len(ap_item_ids) else ("partial" if success_count > 0 else "error"),
        "requested_count": len(ap_item_ids),
        "success_count": success_count,
        "failed_count": len(ap_item_ids) - success_count,
        "auto_resumed_count": auto_resumed_count,
        "results": results,
    }


@router.post("/{ap_item_id}/non-invoice/resolve")
async def resolve_non_invoice_review(
    ap_item_id: str,
    request: ResolveNonInvoiceReviewRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id)
    verify_org_access(item.get("organization_id") or organization_id or "default", user)

    metadata = shared._parse_json(item.get("metadata"))
    document_type = shared._normalize_document_type_token(
        item.get("document_type")
        or metadata.get("document_type")
        or metadata.get("email_type")
    )
    if document_type == "invoice":
        raise HTTPException(status_code=400, detail="invoice_document_not_supported")

    outcome = shared._normalize_non_invoice_outcome(request.outcome)
    allowed_outcomes = shared._NON_INVOICE_ALLOWED_OUTCOMES.get(document_type) or shared._NON_INVOICE_ALLOWED_OUTCOMES["other"]
    if outcome not in allowed_outcomes:
        raise HTTPException(status_code=400, detail="invalid_non_invoice_outcome")

    related_reference = str(request.related_reference or "").strip() or None
    related_ap_item_id = str(request.related_ap_item_id or "").strip() or None
    if outcome in {"apply_to_invoice", "link_to_payment"} and not (related_reference or related_ap_item_id):
        raise HTTPException(status_code=400, detail="related_reference_required")

    resolved_related_item, link_status = shared._resolve_related_ap_item_for_non_invoice(
        db,
        organization_id=str(item.get("organization_id") or organization_id or "default"),
        source_ap_item_id=ap_item_id,
        related_ap_item_id=related_ap_item_id,
        related_reference=related_reference,
    )
    if resolved_related_item and not related_ap_item_id:
        related_ap_item_id = str(resolved_related_item.get("id") or "").strip() or related_ap_item_id

    actor_id = shared._authenticated_actor(user)
    resolved_at = datetime.now(timezone.utc).isoformat()
    next_state = shared._non_invoice_resolution_state(
        current_state=str(item.get("state") or "").strip().lower() or APState.RECEIVED.value,
        outcome=outcome,
        close_record=bool(request.close_record),
    )

    resolution = {
        "document_type": document_type,
        "outcome": outcome,
        "related_reference": related_reference,
        "related_ap_item_id": related_ap_item_id,
        "note": str(request.note or "").strip() or None,
        "resolved_at": resolved_at,
        "resolved_by": actor_id,
        "closed_record": bool(request.close_record),
        "link_status": link_status,
    }
    resolution.update(
        shared._non_invoice_resolution_semantics(
            document_type=document_type,
            outcome=outcome,
            close_record=bool(request.close_record),
        )
    )
    if resolved_related_item:
        resolution["linked_record"] = shared._summarize_related_item(
            shared.build_worklist_item(db, resolved_related_item)
        )
    if document_type in {"statement", "bank_statement"} and outcome == "send_to_reconciliation":
        resolution.update(
            shared._create_statement_reconciliation_artifact(
                db,
                item=item,
                document_type=document_type,
                organization_id=str(item.get("organization_id") or organization_id or "default"),
                resolution=resolution,
                related_item=resolved_related_item,
            )
        )
    metadata["non_invoice_resolution"] = resolution
    metadata["non_invoice_review_required"] = False

    current_state = str(item.get("state") or "").strip().lower() or APState.RECEIVED.value
    update_payload: Dict[str, Any] = {
        "metadata": metadata,
    }
    if next_state != current_state:
        update_payload["state"] = next_state
    if outcome != "needs_followup":
        update_payload["exception_code"] = None
        update_payload["exception_severity"] = None

    db.update_ap_item(
        ap_item_id,
        **shared._filter_allowed_ap_item_updates(db, update_payload),
        _actor_type="user",
        _actor_id=actor_id,
        _source="non_invoice_review_resolution",
        _decision_reason=outcome,
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "non_invoice_review_resolved",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": str(item.get("organization_id") or organization_id or "default"),
            "source": "ap_item_non_invoice_review_resolution",
            "reason": outcome,
            "metadata": resolution,
        }
    )
    if resolved_related_item:
        linked_related = shared._link_related_item_for_non_invoice_resolution(
            db,
            source_item={**item, "document_type": document_type},
            source_document_type=document_type,
            resolution=resolution,
            related_item=resolved_related_item,
            actor_id=actor_id,
            organization_id=str(item.get("organization_id") or organization_id or "default"),
        )
        follow_on_result = await shared._execute_non_invoice_erp_follow_on(
            db,
            source_item={**item, "document_type": document_type},
            related_item=resolved_related_item,
            document_type=document_type,
            outcome=outcome,
            actor_id=actor_id,
            organization_id=str(item.get("organization_id") or organization_id or "default"),
        )
        if isinstance(follow_on_result, dict) and isinstance(follow_on_result.get("related_item"), dict):
            linked_related = follow_on_result.get("related_item")
        metadata = shared._parse_json((shared._require_item(db, ap_item_id)).get("metadata"))
        non_invoice_resolution = metadata.get("non_invoice_resolution")
        if isinstance(non_invoice_resolution, dict):
            non_invoice_resolution["linked_record"] = shared._summarize_related_item(linked_related)
            metadata["non_invoice_resolution"] = non_invoice_resolution
            db.update_ap_item(
                ap_item_id,
                **shared._filter_allowed_ap_item_updates(db, {"metadata": metadata}),
                _actor_type="user",
                _actor_id=actor_id,
                _source="non_invoice_link_refresh",
                _decision_reason=outcome,
            )

    refreshed = shared._require_item(db, ap_item_id)
    normalized_item = shared.build_worklist_item(db, refreshed)
    return {
        "status": "resolved",
        "ap_item_id": ap_item_id,
        "document_type": document_type,
        "outcome": outcome,
        "state": next_state,
        "ap_item": normalized_item,
    }


@router.post("/{ap_item_id}/entity-route/resolve")
async def resolve_ap_item_entity_route(
    ap_item_id: str,
    request: ResolveEntityRouteRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id)
    verify_org_access(item.get("organization_id") or organization_id or "default", user)

    metadata = shared._parse_json(item.get("metadata"))
    document_type = shared._normalize_document_type_token(
        item.get("document_type")
        or metadata.get("document_type")
        or metadata.get("email_type")
    )
    if document_type != "invoice":
        raise HTTPException(status_code=400, detail="entity_route_not_supported")

    organization_settings = shared._load_org_settings_for_item(
        db,
        item.get("organization_id") or organization_id or "default",
    )
    routing = resolve_entity_routing(metadata, item, organization_settings=organization_settings)
    candidates = routing.get("candidates") if isinstance(routing.get("candidates"), list) else []
    selected = match_entity_candidate(
        candidates,
        selection=request.selection,
        entity_id=request.entity_id,
        entity_code=request.entity_code,
        entity_name=request.entity_name,
    )
    if not selected and len(candidates) == 1:
        selected = dict(candidates[0])
    if not selected:
        selected = normalize_entity_candidate(
            {
                "entity_id": request.entity_id,
                "entity_code": request.entity_code or request.selection,
                "entity_name": request.entity_name or request.selection,
            }
        )
    if not selected:
        raise HTTPException(status_code=400, detail="entity_selection_required")

    actor_id = shared._authenticated_actor(user)
    resolved_at = datetime.now(timezone.utc).isoformat()
    note = str(request.note or "").strip() or None
    updated_routing = {
        **(metadata.get("entity_routing") if isinstance(metadata.get("entity_routing"), dict) else {}),
        "status": "resolved",
        "selected": selected,
        "candidates": candidates,
        "resolved_at": resolved_at,
        "resolved_by": actor_id,
        "reason": str(routing.get("reason") or note or "").strip() or None,
    }
    if note:
        updated_routing["note"] = note

    metadata.update(
        {
            "entity_routing": updated_routing,
            "entity_route_review_required": False,
            "entity_selection": selected,
            "entity_id": selected.get("entity_id") or metadata.get("entity_id"),
            "entity_code": selected.get("entity_code") or metadata.get("entity_code"),
            "entity_name": selected.get("entity_name") or metadata.get("entity_name"),
        }
    )
    db.update_ap_item(
        ap_item_id,
        metadata=metadata,
        _actor_type="user",
        _actor_id=actor_id,
        _source="ap_items_entity_route",
        _decision_reason="manual_entity_route_resolution",
    )

    refreshed = shared._require_item(db, ap_item_id)
    normalized_item = shared.build_worklist_item(
        db,
        refreshed,
        organization_settings=organization_settings,
    )
    response = {
        "status": "resolved",
        "ap_item_id": ap_item_id,
        "entity_selection": selected,
        "entity_routing_status": normalized_item.get("entity_routing_status"),
        "ap_item": normalized_item,
    }
    runtime = shared._finance_agent_runtime_cls()(
        organization_id=str(item.get("organization_id") or organization_id or "default"),
        actor_id=actor_id,
        actor_email=getattr(user, "email", None),
        db=db,
    )
    audit_row = runtime.append_runtime_audit(
        ap_item_id=ap_item_id,
        event_type="entity_route_resolved",
        reason="manual_entity_route_resolution",
        metadata={
            "entity_selection": selected,
            "response": response,
        },
        correlation_id=runtime.correlation_id_for_item(refreshed),
        skill_id="ap_v1",
    )
    response["audit_event_id"] = (audit_row or {}).get("id")
    return response


@router.post("/{ap_item_id}/sources/link")
def link_ap_item_source(
    ap_item_id: str,
    request: LinkSourceRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id)
    verify_org_access(item.get("organization_id") or "default", _user)
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


@router.post("/{ap_item_id}/resubmit")
def resubmit_rejected_item(
    ap_item_id: str,
    request: ResubmitRejectedItemRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    actor_id = shared._authenticated_actor(_user)
    source = shared._require_item(db, ap_item_id)
    verify_org_access(source.get("organization_id") or "default", _user)
    source_state = shared._normalized_state_value(source.get("state"))
    if source_state != APState.REJECTED.value:
        raise HTTPException(status_code=400, detail="resubmission_requires_rejected_state")

    existing_child_id = str(source.get("superseded_by_ap_item_id") or "").strip()
    if existing_child_id:
        existing_child = db.get_ap_item(existing_child_id)
        if existing_child:
            return {
                "status": "already_resubmitted",
                "source_ap_item_id": source["id"],
                "new_ap_item_id": existing_child_id,
                "ap_item": shared.build_worklist_item(db, existing_child),
                "linkage": {
                    "supersedes_ap_item_id": source["id"],
                    "supersedes_invoice_key": existing_child.get("supersedes_invoice_key")
                    or source.get("invoice_key"),
                    "superseded_by_ap_item_id": existing_child_id,
                },
            }

    initial_state = shared._normalized_state_value(request.initial_state)
    if initial_state not in {APState.RECEIVED.value, APState.VALIDATED.value}:
        raise HTTPException(status_code=400, detail="invalid_resubmission_initial_state")

    source_meta = shared._parse_json(source.get("metadata"))
    new_meta = dict(source_meta)
    for stale_key in (
        "merged_into",
        "merge_reason",
        "merge_status",
        "suppressed_from_worklist",
        "confidence_override",
    ):
        new_meta.pop(stale_key, None)
    new_meta["supersedes_ap_item_id"] = source["id"]
    new_meta["supersedes_invoice_key"] = shared._superseded_invoice_key(source, request)
    new_meta["resubmission_reason"] = request.reason
    new_meta["resubmission"] = {
        "source_ap_item_id": source["id"],
        "reason": request.reason,
        "actor_id": actor_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if request.actor_id and str(request.actor_id).strip() and str(request.actor_id).strip() != actor_id:
        new_meta["requested_actor_id"] = str(request.actor_id).strip()
    if request.metadata:
        new_meta.update(request.metadata)

    create_payload: Dict[str, Any] = {
        "invoice_key": shared._resubmission_invoice_key(source, request),
        "thread_id": request.thread_id or source.get("thread_id"),
        "message_id": request.message_id or source.get("message_id"),
        "subject": request.subject or source.get("subject"),
        "sender": request.sender or source.get("sender"),
        "vendor_name": request.vendor_name or source.get("vendor_name"),
        "amount": request.amount if request.amount is not None else source.get("amount"),
        "currency": request.currency or source.get("currency") or "USD",
        "invoice_number": request.invoice_number or source.get("invoice_number"),
        "invoice_date": request.invoice_date or source.get("invoice_date"),
        "due_date": request.due_date or source.get("due_date"),
        "state": initial_state,
        "confidence": source.get("confidence"),
        "approval_required": bool(source.get("approval_required", True)),
        "workflow_id": source.get("workflow_id"),
        "run_id": None,
        "approval_surface": source.get("approval_surface") or "hybrid",
        "approval_policy_version": source.get("approval_policy_version"),
        "post_attempted_at": None,
        "last_error": None,
        "organization_id": source.get("organization_id"),
        "user_id": source.get("user_id"),
        "po_number": source.get("po_number"),
        "attachment_url": source.get("attachment_url"),
        "supersedes_ap_item_id": source["id"],
        "supersedes_invoice_key": shared._superseded_invoice_key(source, request),
        "superseded_by_ap_item_id": None,
        "resubmission_reason": request.reason,
        "metadata": new_meta,
    }
    created = db.create_ap_item(create_payload)

    db.update_ap_item(
        source["id"],
        superseded_by_ap_item_id=created["id"],
        _actor_type="user",
        _actor_id=actor_id,
    )
    source_after = db.get_ap_item(source["id"]) or source
    source_after_meta = shared._parse_json(source_after.get("metadata"))
    source_after_meta["superseded_by_ap_item_id"] = created["id"]
    source_after_meta["resubmission_reason"] = request.reason
    db.update_ap_item(source["id"], metadata=source_after_meta, _actor_type="user", _actor_id=actor_id)

    copied_sources = 0
    if request.copy_sources:
        copied_sources = shared._copy_item_sources_for_resubmission(
            db,
            source_ap_item_id=source["id"],
            target_ap_item_id=created["id"],
            actor_id=actor_id,
        )

    audit_key = f"ap_item_resubmission:{source['id']}:{created['id']}"
    db.append_ap_audit_event(
        {
            "ap_item_id": source["id"],
            "event_type": "ap_item_resubmitted",
            "actor_type": "user",
            "actor_id": actor_id,
            "payload_json": {
                "source_ap_item_id": source["id"],
                "new_ap_item_id": created["id"],
                "reason": request.reason,
                "copied_sources": copied_sources,
            },
            "organization_id": source.get("organization_id") or "default",
            "source": "ap_items_api",
            "decision_reason": request.reason,
            "idempotency_key": audit_key,
        }
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": created["id"],
            "event_type": "ap_item_resubmission_created",
            "actor_type": "user",
            "actor_id": actor_id,
            "payload_json": {
                "source_ap_item_id": source["id"],
                "new_ap_item_id": created["id"],
                "reason": request.reason,
                "copied_sources": copied_sources,
            },
            "organization_id": created.get("organization_id") or "default",
            "source": "ap_items_api",
            "decision_reason": request.reason,
            "idempotency_key": f"{audit_key}:new",
        }
    )

    return {
        "status": "resubmitted",
        "source_ap_item_id": source["id"],
        "new_ap_item_id": created["id"],
        "copied_sources": copied_sources,
        "linkage": {
            "supersedes_ap_item_id": source["id"],
            "supersedes_invoice_key": created.get("supersedes_invoice_key")
            or shared._superseded_invoice_key(source, request),
            "superseded_by_ap_item_id": created["id"],
            "resubmission_reason": request.reason,
        },
        "ap_item": shared.build_worklist_item(db, created),
    }


@router.post("/{ap_item_id}/merge")
def merge_ap_items(ap_item_id: str, request: MergeItemsRequest, _user=Depends(require_ops_user)) -> Dict[str, Any]:
    db = shared.get_db()
    actor_id = shared._authenticated_actor(_user)
    target = shared._require_item(db, ap_item_id)
    verify_org_access(target.get("organization_id") or "default", _user)
    source = shared._require_item(db, request.source_ap_item_id)

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

    target_meta = shared._parse_json(target.get("metadata"))
    merge_history = target_meta.get("merge_history")
    if not isinstance(merge_history, list):
        merge_history = []
    merged_at = datetime.now(timezone.utc).isoformat()
    merge_history.append(
        {
            "source_ap_item_id": source["id"],
            "reason": request.reason,
            "actor_id": actor_id,
            "merged_at": merged_at,
        }
    )
    target_meta["merge_history"] = merge_history
    target_meta["merge_reason"] = request.reason
    target_meta["has_context_conflict"] = False
    target_meta["source_count"] = len(db.list_ap_item_sources(target["id"]))
    db.update_ap_item(target["id"], metadata=target_meta)

    source_meta = shared._parse_json(source.get("metadata"))
    source_meta["merged_into"] = target["id"]
    source_meta["merge_reason"] = request.reason
    source_meta["merged_at"] = merged_at
    source_meta["merged_by"] = actor_id
    source_meta["merge_status"] = "merged_source"
    source_meta["source_count"] = 0
    source_meta["suppressed_from_worklist"] = True
    if source.get("state"):
        source_meta["merge_source_state"] = source.get("state")
    db.update_ap_item(source["id"], metadata=source_meta)

    db.append_ap_audit_event(
        {
            "ap_item_id": target["id"],
            "event_type": "ap_item_merged",
            "actor_type": "user",
            "actor_id": actor_id,
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
    db.append_ap_audit_event(
        {
            "ap_item_id": source["id"],
            "event_type": "ap_item_merged_into",
            "actor_type": "user",
            "actor_id": actor_id,
            "payload_json": {
                "source_ap_item_id": source["id"],
                "target_ap_item_id": target["id"],
                "reason": request.reason,
            },
            "organization_id": source.get("organization_id") or "default",
            "source": "ap_items_api",
            "decision_reason": request.reason,
            "idempotency_key": f"merge-source:{source['id']}:{target['id']}",
        }
    )

    return {
        "status": "merged",
        "target_ap_item_id": target["id"],
        "source_ap_item_id": source["id"],
        "moved_sources": moved_count,
    }


@router.post("/{ap_item_id}/split")
def split_ap_item(ap_item_id: str, request: SplitItemRequest, _user=Depends(require_ops_user)) -> Dict[str, Any]:
    db = shared.get_db()
    actor_id = shared._authenticated_actor(_user)
    parent = shared._require_item(db, ap_item_id)
    verify_org_access(parent.get("organization_id") or "default", _user)
    if not request.sources:
        raise HTTPException(status_code=400, detail="sources_required")

    created_items: List[Dict[str, Any]] = []
    parent_meta = shared._parse_json(parent.get("metadata"))
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
                "split_actor_id": actor_id,
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
                "actor_id": actor_id,
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
        "created_items": [
            shared.build_worklist_item(
                db,
                item,
                approval_policy=shared._approval_followup_policy(
                    str(item.get("organization_id") or parent.get("organization_id") or "default")
                ),
            )
            for item in created_items
        ],
    }


@router.post("/{ap_item_id}/retry-post")
async def retry_erp_post(
    ap_item_id: str,
    organization_id: str = "default",
    _user=Depends(require_ops_user),
):
    verify_org_access(organization_id, _user)
    db = shared.get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="AP item not found")

    if item.get("organization_id") != organization_id:
        raise HTTPException(status_code=403, detail="Organization mismatch")

    current_state = item.get("state") or item.get("status")
    if current_state != APState.FAILED_POST:
        raise HTTPException(
            status_code=400,
            detail=f"Can only retry from failed_post state (current: {current_state})",
        )

    runtime = shared._finance_agent_runtime_cls()(
        organization_id=organization_id,
        actor_id=getattr(_user, "user_id", None) or getattr(_user, "email", None) or "ap_retry",
        actor_email=getattr(_user, "email", None) or getattr(_user, "user_id", None) or "ap_retry",
        db=db,
    )
    try:
        retry_result = await runtime.execute_intent(
            "retry_recoverable_failures",
            {
                "ap_item_id": ap_item_id,
                "email_id": str(item.get("thread_id") or ap_item_id),
                "reason": "retry_post_api",
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=safe_error(exc, "ERP posting")) from exc

    status = str((retry_result or {}).get("status") or "").strip().lower()
    if status == "posted":
        return {
            "status": "posted",
            "ap_item_id": ap_item_id,
            "erp_reference": (retry_result or {}).get("erp_reference"),
            "resume_result": retry_result.get("result") if isinstance(retry_result, dict) else None,
            "retry_result": retry_result,
        }
    if status == "blocked":
        reason = str((retry_result or {}).get("reason") or "retry_not_recoverable")
        raise HTTPException(status_code=400, detail=reason)
    if status == "ready_to_post":
        return {
            "status": "ready_to_post",
            "ap_item_id": ap_item_id,
            "erp_reference": (retry_result or {}).get("erp_reference"),
            "resume_result": retry_result.get("result") if isinstance(retry_result, dict) else None,
            "retry_result": retry_result,
        }
    if status == "error":
        reason = str((retry_result or {}).get("reason") or "erp_post_failed")
        raise HTTPException(
            status_code=502,
            detail=f"ERP posting failed: {reason}",
        )
    raise HTTPException(status_code=502, detail=f"ERP posting failed: {status or 'retry_failed'}")
