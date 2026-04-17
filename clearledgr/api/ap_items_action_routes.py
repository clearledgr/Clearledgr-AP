"""Mutating AP item routes."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query

from clearledgr.api.ap_item_contracts import (
    AddApItemCommentRequest,
    AddApItemFileRequest,
    AddApItemNoteRequest,
    AddApItemTaskCommentRequest,
    AssignApItemTaskRequest,
    BulkApproveRequest,
    BulkRejectRequest,
    BulkResolveFieldReviewRequest,
    BulkRetryPostRequest,
    BulkSnoozeRequest,
    CreateComposeRecordRequest,
    CreateApItemTaskRequest,
    LinkSourceRequest,
    LinkComposeDraftRequest,
    LinkGmailThreadRequest,
    MergeItemsRequest,
    ResolveEntityRouteRequest,
    ResolveFieldReviewRequest,
    ResolveNonInvoiceReviewRequest,
    ResubmitRejectedItemRequest,
    SnoozeAPItemRequest,
    SplitItemRequest,
    UpdateApItemFieldsRequest,
    UpdateApItemTaskStatusRequest,
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


def _dispatch_mention_notifications(
    *, body: str, ap_item_id: str, item: Dict[str, Any], actor_id: str
) -> None:
    """§5.3 @Mentions — parse @email from note/comment body and bridge to
    the workspace's configured approval surface.

    "When a Box timeline @mention happens in Gmail, Clearledgr also sends a
    Slack or Teams DM to the mentioned person with the comment and a direct
    link to the thread."

    Slack is preferred when configured because it supports
    ``users.lookupByEmail`` + DM delivery with structured metadata that
    ``slack_invoices.py`` uses to sync replies back to the Box timeline
    (the bidirectional loop the thesis describes).

    Teams is the fallback when Slack isn't wired. Teams' incoming
    webhooks can only target the channel the webhook belongs to — they
    cannot DM an individual user without the full Bot Framework bot
    installation. We inline the mentioned email in the channel message
    so the intended recipient is visible; reply-sync from Teams back to
    the timeline is a separate bot integration that is scoped for a
    later pass (not shipped today).
    """
    import re

    mentions = re.findall(r"@([\w.+-]+@[\w.-]+\.\w+)", body)
    if not mentions:
        return

    vendor = item.get("vendor_name") or item.get("vendor") or "Unknown"
    org_id = item.get("organization_id") or "default"
    invoice_number = item.get("invoice_number", "N/A")

    # Preferred path: Slack DM (with reply-sync via message metadata).
    slack_handled = _dispatch_mention_slack_dm(
        mentions=mentions, vendor=vendor, invoice_number=invoice_number,
        body=body, ap_item_id=ap_item_id, org_id=org_id, actor_id=actor_id,
    )

    # Fallback: if Slack wasn't configured for any mention, try Teams.
    # Slack returning "user not found" still counts as Slack-handled —
    # the workspace has a Slack integration, the user just isn't a
    # member. We don't double-post to Teams in that case.
    if not slack_handled:
        _dispatch_mention_teams_channel(
            mentions=mentions, vendor=vendor, invoice_number=invoice_number,
            body=body, ap_item_id=ap_item_id, org_id=org_id, actor_id=actor_id,
        )


def _dispatch_mention_slack_dm(
    *, mentions: List[str], vendor: str, invoice_number: str, body: str,
    ap_item_id: str, org_id: str, actor_id: str,
) -> bool:
    """Send a Slack DM per mentioned email. Returns True iff Slack is
    configured for this workspace (regardless of per-user lookup
    success), so the caller knows not to fall through to Teams.
    """
    try:
        from clearledgr.services.slack_api import resolve_slack_runtime

        runtime = resolve_slack_runtime(org_id)
        if not runtime or not runtime.get("token"):
            return False
    except Exception as exc:
        logger.debug("[mentions] slack runtime lookup failed: %s", exc)
        return False

    import httpx

    headers = {"Authorization": f"Bearer {runtime['token']}", "Content-Type": "application/json"}

    for email in mentions:
        try:
            lookup_resp = httpx.post(
                "https://slack.com/api/users.lookupByEmail",
                json={"email": email},
                headers=headers,
                timeout=10,
            )
            lookup_data = lookup_resp.json()
            if not lookup_data.get("ok"):
                continue
            slack_user_id = lookup_data["user"]["id"]

            dm_text = (
                f"*{actor_id}* mentioned you on {vendor} (invoice {invoice_number}):\n"
                f">{body[:500]}\n"
                f"_Reply here — your response will be added to the invoice timeline._"
            )
            dm_payload = {
                "channel": slack_user_id,
                "text": dm_text,
                "metadata": {
                    "event_type": "clearledgr_mention",
                    "event_payload": {
                        "ap_item_id": ap_item_id,
                        "organization_id": org_id,
                    },
                },
            }
            httpx.post(
                "https://slack.com/api/chat.postMessage",
                json=dm_payload,
                headers=headers,
                timeout=10,
            )
        except Exception as exc:
            logger.warning("[mentions] slack notification to %s failed: %s", email, exc)

    return True  # Slack is wired; caller shouldn't fall through.


def _dispatch_mention_teams_channel(
    *, mentions: List[str], vendor: str, invoice_number: str, body: str,
    ap_item_id: str, org_id: str, actor_id: str,
) -> None:
    """Post a single Teams channel message for all @mentions on this
    comment. One message with all mentioned emails inline — not per-user
    DMs — because Teams incoming webhooks can't target individuals
    without a full bot installation.
    """
    try:
        from clearledgr.services.teams_api import TeamsAPIClient

        client = TeamsAPIClient.from_env(org_id)
        if not client.webhook_url:
            return
    except Exception as exc:
        logger.debug("[mentions] teams client resolve failed: %s", exc)
        return

    mention_list = ", ".join(f"**@{email}**" for email in mentions)
    snippet = body[:500].replace("\n", " ")
    text = (
        f"{mention_list} — *{actor_id}* mentioned you on {vendor} "
        f"(invoice {invoice_number}):\n"
        f"> {snippet}\n"
        f"_Open the thread in Gmail to respond — Teams reply-to-timeline "
        f"sync is not yet available._"
    )

    card = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "size": "Medium", "weight": "Bolder",
                         "text": "Clearledgr — you were mentioned"},
                        {"type": "TextBlock", "wrap": True, "text": text},
                        {"type": "TextBlock", "isSubtle": True, "spacing": "Small",
                         "text": f"AP item: {ap_item_id} · Org: {org_id}"},
                    ],
                },
            }
        ],
    }
    try:
        client._post_json(card)
    except Exception as exc:
        logger.warning("[mentions] teams channel post failed: %s", exc)


def _resolve_task_owner_item(db: Any, task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    related_id = str(task.get("related_entity_id") or "").strip()
    organization_id = str(task.get("organization_id") or "default").strip() or "default"
    if related_id:
        try:
            return shared._require_item(db, related_id, expected_organization_id=organization_id)
        except Exception:
            return None
    thread_id = str(task.get("source_thread_id") or "").strip()
    if thread_id and hasattr(db, "get_ap_item_by_thread"):
        try:
            return db.get_ap_item_by_thread(organization_id, thread_id)
        except Exception:
            return None
    return None


def _normalize_compose_recipients(values: List[str] | None) -> List[str]:
    recipients: List[str] = []
    for raw in values or []:
        normalized = str(raw or "").strip()
        if not normalized or normalized in recipients:
            continue
        recipients.append(normalized)
    return recipients[:12]


def _derive_vendor_name_from_recipients(recipients: List[str]) -> str:
    if not recipients:
        return "Draft finance record"
    first = recipients[0]
    local_part = first.split("@", 1)[0] if "@" in first else first
    normalized = " ".join(part for part in local_part.replace(".", " ").replace("_", " ").replace("-", " ").split() if part)
    if not normalized:
        return first
    return " ".join(token.capitalize() for token in normalized.split())


def _append_metadata_entry(
    metadata: Dict[str, Any],
    key: str,
    entry: Dict[str, Any],
    *,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    existing = metadata.get(key)
    rows = list(existing) if isinstance(existing, list) else []
    rows.insert(0, entry)
    metadata[key] = rows[:limit]
    return metadata[key]


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
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(user, "organization_id", None))
    verify_org_access(item.get("organization_id") or organization_id or "default", user)

    metadata = shared._parse_json(item.get("metadata"))
    document_type = shared._normalize_document_type_token(
        item.get("document_type") or "invoice"
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
        metadata = shared._parse_json((shared._require_item(db, ap_item_id, expected_organization_id=getattr(user, "organization_id", None))).get("metadata"))
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

    refreshed = shared._require_item(db, ap_item_id, expected_organization_id=getattr(user, "organization_id", None))
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
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(user, "organization_id", None))
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

    refreshed = shared._require_item(db, ap_item_id, expected_organization_id=getattr(user, "organization_id", None))
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
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
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


@router.post("/{ap_item_id}/gmail-link")
def link_ap_item_gmail_thread(
    ap_item_id: str,
    request: LinkGmailThreadRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    actor_id = shared._authenticated_actor(_user)
    organization_id = str(item.get("organization_id") or "default").strip() or "default"
    thread_id = str(request.thread_id or "").strip()
    message_id = str(request.message_id or "").strip() or None

    existing = db.get_ap_item_by_thread(organization_id, thread_id) if hasattr(db, "get_ap_item_by_thread") else None
    if existing and str(existing.get("id") or "").strip() != str(ap_item_id):
        raise HTTPException(status_code=409, detail="gmail_thread_already_linked")

    db.link_ap_item_source(
        {
            "ap_item_id": ap_item_id,
            "source_type": "gmail_thread",
            "source_ref": thread_id,
            "subject": request.subject or item.get("subject"),
            "sender": request.sender or item.get("sender"),
            "detected_at": request.detected_at,
            "metadata": {"link_origin": "gmail_sidebar"},
        }
    )
    if message_id:
        db.link_ap_item_source(
            {
                "ap_item_id": ap_item_id,
                "source_type": "gmail_message",
                "source_ref": message_id,
                "subject": request.subject or item.get("subject"),
                "sender": request.sender or item.get("sender"),
                "detected_at": request.detected_at,
                "metadata": {"link_origin": "gmail_sidebar"},
            }
        )

    db.update_ap_item(
        ap_item_id,
        thread_id=thread_id,
        message_id=message_id or item.get("message_id"),
        subject=request.subject or item.get("subject"),
        sender=request.sender or item.get("sender"),
        _actor_type="user",
        _actor_id=actor_id,
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "gmail_thread_linked",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": organization_id,
            "source": "ap_items_api",
            "payload_json": {
                "thread_id": thread_id,
                "message_id": message_id,
                "subject": request.subject,
                "sender": request.sender,
                "note": str(request.note or "").strip() or None,
            },
        }
    )
    updated = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    return {
        "status": "linked",
        "ap_item": shared.build_worklist_item(db, updated),
    }


@router.post("/{ap_item_id}/compose-link")
def link_ap_item_compose_draft(
    ap_item_id: str,
    request: LinkComposeDraftRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    actor_id = shared._authenticated_actor(_user)
    organization_id = str(item.get("organization_id") or "default").strip() or "default"
    draft_id = str(request.draft_id or "").strip() or None
    thread_id = str(request.thread_id or "").strip() or None
    subject = str(request.subject or "").strip() or None
    recipients = _normalize_compose_recipients(request.recipients)
    body_preview = str(request.body_preview or "").strip() or None

    if draft_id and hasattr(db, "list_ap_item_sources_by_ref"):
        for row in db.list_ap_item_sources_by_ref("compose_draft", draft_id):
            linked_ap_item_id = str(row.get("ap_item_id") or "").strip()
            if linked_ap_item_id and linked_ap_item_id != str(ap_item_id):
                raise HTTPException(status_code=409, detail="compose_draft_already_linked")

    if thread_id and hasattr(db, "get_ap_item_by_thread"):
        existing = db.get_ap_item_by_thread(organization_id, thread_id)
        if existing and str(existing.get("id") or "").strip() != str(ap_item_id):
            raise HTTPException(status_code=409, detail="gmail_thread_already_linked")

    if draft_id:
        db.link_ap_item_source(
            {
                "ap_item_id": ap_item_id,
                "source_type": "compose_draft",
                "source_ref": draft_id,
                "subject": subject or item.get("subject"),
                "sender": getattr(_user, "email", None) or item.get("sender"),
                "metadata": {
                    "link_origin": "gmail_compose",
                    "recipients": recipients,
                    "body_preview": body_preview,
                },
            }
        )
    if thread_id:
        db.link_ap_item_source(
            {
                "ap_item_id": ap_item_id,
                "source_type": "gmail_thread",
                "source_ref": thread_id,
                "subject": subject or item.get("subject"),
                "sender": item.get("sender"),
                "metadata": {"link_origin": "gmail_compose"},
            }
        )

    update_payload: Dict[str, Any] = {}
    if thread_id and str(item.get("thread_id") or "").strip() != thread_id:
        update_payload["thread_id"] = thread_id
    if subject and str(item.get("subject") or "").strip() != subject:
        update_payload["subject"] = subject
    if update_payload:
        db.update_ap_item(
            ap_item_id,
            **update_payload,
            _actor_type="user",
            _actor_id=actor_id,
            _source="ap_items_api",
            _decision_reason="compose_draft_linked",
        )

    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "compose_draft_linked",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": organization_id,
            "source": "ap_items_api",
            "payload_json": {
                "draft_id": draft_id,
                "thread_id": thread_id,
                "subject": subject,
                "recipients": recipients,
                "body_preview": body_preview,
                "note": str(request.note or "").strip() or None,
            },
        }
    )
    updated = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    return {
        "status": "linked",
        "ap_item": shared.build_worklist_item(db, updated),
    }


@router.post("/compose/create")
def create_compose_record(
    request: CreateComposeRecordRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    actor_id = shared._authenticated_actor(_user)
    organization_id = str(getattr(_user, "organization_id", None) or "default").strip() or "default"
    draft_id = str(request.draft_id or "").strip() or None
    thread_id = str(request.thread_id or "").strip() or None
    subject = str(request.subject or "").strip() or None
    recipients = _normalize_compose_recipients(request.recipients)
    body_preview = str(request.body_preview or "").strip() or None
    note = str(request.note or "").strip() or None

    if draft_id and hasattr(db, "list_ap_item_sources_by_ref"):
        for row in db.list_ap_item_sources_by_ref("compose_draft", draft_id):
            candidate_id = str(row.get("ap_item_id") or "").strip()
            if not candidate_id:
                continue
            existing = db.get_ap_item(candidate_id)
            if existing and str(existing.get("organization_id") or organization_id or "default").strip() == organization_id:
                return {
                    "status": "already_linked",
                    "ap_item": shared.build_worklist_item(db, existing),
                }

    if thread_id and hasattr(db, "get_ap_item_by_thread"):
        existing = db.get_ap_item_by_thread(organization_id, thread_id)
        if existing:
            return {
                "status": "already_linked",
                "ap_item": shared.build_worklist_item(db, existing),
            }

    compose_summary = {
        "draft_id": draft_id,
        "thread_id": thread_id,
        "recipients": recipients,
        "body_preview": body_preview,
        "created_from": "gmail_compose",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata: Dict[str, Any] = {
        "compose_origin": compose_summary,
    }
    if note:
        _append_metadata_entry(
            metadata,
            "record_comments",
            {
                "id": f"comment_{uuid.uuid4().hex}",
                "body": note,
                "author": actor_id,
                "created_at": compose_summary["created_at"],
                "origin": "compose_create",
            },
        )

    created = db.create_ap_item(
        {
            "thread_id": thread_id,
            "subject": subject or f"Draft with {(_derive_vendor_name_from_recipients(recipients) or 'finance contact')}",
            "sender": getattr(_user, "email", None),
            "vendor_name": _derive_vendor_name_from_recipients(recipients),
            "state": APState.NEEDS_INFO.value,
            "approval_required": False,
            "organization_id": organization_id,
            "user_id": getattr(_user, "user_id", None),
            "document_type": "other",
            "metadata": metadata,
        }
    )
    ap_item_id = str(created.get("id") or "").strip()

    if draft_id:
        db.link_ap_item_source(
            {
                "ap_item_id": ap_item_id,
                "source_type": "compose_draft",
                "source_ref": draft_id,
                "subject": subject or created.get("subject"),
                "sender": getattr(_user, "email", None),
                "metadata": {
                    "link_origin": "gmail_compose_create",
                    "recipients": recipients,
                    "body_preview": body_preview,
                },
            }
        )
    if thread_id:
        db.link_ap_item_source(
            {
                "ap_item_id": ap_item_id,
                "source_type": "gmail_thread",
                "source_ref": thread_id,
                "subject": subject or created.get("subject"),
                "sender": getattr(_user, "email", None),
                "metadata": {"link_origin": "gmail_compose_create"},
            }
        )

    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "compose_record_created",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": organization_id,
            "source": "ap_items_api",
            "payload_json": {
                "draft_id": draft_id,
                "thread_id": thread_id,
                "subject": subject or created.get("subject"),
                "recipients": recipients,
                "body_preview": body_preview,
                "note": note,
            },
        }
    )

    refreshed = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    return {
        "status": "created",
        "ap_item": shared.build_worklist_item(db, refreshed),
    }


@router.patch("/{ap_item_id}/fields")
def update_ap_item_fields(
    ap_item_id: str,
    request: UpdateApItemFieldsRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    actor_id = shared._authenticated_actor(_user)

    updates: Dict[str, Any] = {}
    changes: List[Dict[str, Any]] = []
    field_map = {
        "vendor_name": "vendor_name",
        "invoice_number": "invoice_number",
        "invoice_date": "invoice_date",
        "due_date": "due_date",
        "po_number": "po_number",
        "amount": "amount",
        "currency": "currency",
    }

    for request_field, column_name in field_map.items():
        value = getattr(request, request_field)
        if value is None:
            continue
        normalized = value
        if isinstance(value, str):
            normalized = value.strip() or None
        if request_field == "currency" and normalized:
            normalized = str(normalized).upper()
        current_value = item.get(column_name)
        if normalized == current_value:
            continue
        updates[column_name] = normalized
        changes.append(
            {
                "field": request_field,
                "previous_value": current_value,
                "new_value": normalized,
            }
        )

    if not updates:
        raise HTTPException(status_code=400, detail="no_field_changes")

    db.update_ap_item(
        ap_item_id,
        **updates,
        _actor_type="user",
        _actor_id=actor_id,
        _source="ap_items_api",
        _decision_reason="sidebar_record_edit",
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "record_fields_updated",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": item.get("organization_id") or "default",
            "source": "ap_items_api",
            "payload_json": {
                "changes": changes,
                "note": str(request.note or "").strip() or None,
            },
        }
    )
    updated = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    return {
        "status": "updated",
        "changes": changes,
        "ap_item": shared.build_worklist_item(db, updated),
    }


@router.post("/{ap_item_id}/tasks")
def create_ap_item_task(
    ap_item_id: str,
    request: CreateApItemTaskRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    from clearledgr.services.email_tasks import create_task_from_email

    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    actor_id = shared._authenticated_actor(_user)
    task = create_task_from_email(
        email_id=str(item.get("message_id") or item.get("thread_id") or ap_item_id),
        email_subject=str(item.get("subject") or request.title),
        email_sender=str(item.get("sender") or ""),
        thread_id=str(item.get("thread_id") or ""),
        created_by=actor_id,
        task_type=request.task_type,
        title=request.title,
        description=request.description,
        assignee_email=request.assignee_email,
        due_date=request.due_date,
        priority=request.priority,
        related_entity_type="ap_item",
        related_entity_id=ap_item_id,
        related_amount=item.get("amount"),
        related_vendor=item.get("vendor_name"),
        tags=["gmail_sidebar", "ap_record"],
        organization_id=item.get("organization_id") or "default",
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "task_created",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": item.get("organization_id") or "default",
            "source": "ap_items_api",
            "payload_json": {
                "task_id": task.get("task_id"),
                "title": task.get("title"),
                "task_type": task.get("task_type"),
                "assignee_email": task.get("assignee_email"),
                "due_date": task.get("due_date"),
                "note": str(request.note or "").strip() or None,
            },
        }
    )
    return {"status": "created", "task": task}


@router.post("/tasks/{task_id}/status")
def update_ap_item_task_status(
    task_id: str,
    request: UpdateApItemTaskStatusRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    from clearledgr.services.email_tasks import get_task, update_task_status

    db = shared.get_db()
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    item = _resolve_task_owner_item(db, task)
    if item:
        verify_org_access(item.get("organization_id") or "default", _user)
    updated = update_task_status(
        task_id,
        request.status,
        changed_by=shared._authenticated_actor(_user),
        notes=request.note,
    )
    return {"status": "updated", "task": updated}


@router.post("/tasks/{task_id}/assign")
def assign_ap_item_task(
    task_id: str,
    request: AssignApItemTaskRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    from clearledgr.services.email_tasks import assign_task, get_task

    db = shared.get_db()
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    item = _resolve_task_owner_item(db, task)
    if item:
        verify_org_access(item.get("organization_id") or "default", _user)
    updated = assign_task(task_id, request.assignee_email, shared._authenticated_actor(_user))
    return {"status": "updated", "task": updated}


@router.post("/tasks/{task_id}/comments")
def add_ap_item_task_comment(
    task_id: str,
    request: AddApItemTaskCommentRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    from clearledgr.services.email_tasks import add_comment, get_task

    db = shared.get_db()
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    item = _resolve_task_owner_item(db, task)
    if item:
        verify_org_access(item.get("organization_id") or "default", _user)
    comment = add_comment(task_id, shared._authenticated_actor(_user), request.comment)
    refreshed = get_task(task_id)
    return {"status": "created", "comment": comment, "task": refreshed}


@router.post("/{ap_item_id}/notes")
def add_ap_item_note(
    ap_item_id: str,
    request: AddApItemNoteRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    actor_id = shared._authenticated_actor(_user)
    metadata = shared._parse_json(item.get("metadata"))
    existing_notes = metadata.get("record_notes")
    notes = existing_notes if isinstance(existing_notes, list) else []
    note = {
        "id": f"note_{uuid.uuid4().hex}",
        "body": str(request.body or "").strip(),
        "author": actor_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    notes.insert(0, note)
    metadata["record_notes"] = notes[:100]
    db.update_ap_item(
        ap_item_id,
        metadata=metadata,
        _actor_type="user",
        _actor_id=actor_id,
        _source="ap_items_api",
        _decision_reason="record_note_added",
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "record_note_added",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": item.get("organization_id") or "default",
            "source": "ap_items_api",
            "payload_json": {
                "note_id": note["id"],
                "body": note["body"],
            },
        }
    )

    # §5.3 @Mentions — parse @email in note body, dispatch notifications
    _dispatch_mention_notifications(
        body=note["body"],
        ap_item_id=ap_item_id,
        item=item,
        actor_id=actor_id,
    )

    return {"status": "created", "note": note}


@router.post("/{ap_item_id}/comments")
def add_ap_item_comment(
    ap_item_id: str,
    request: AddApItemCommentRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    actor_id = shared._authenticated_actor(_user)
    metadata = shared._parse_json(item.get("metadata"))
    comment = {
        "id": f"comment_{uuid.uuid4().hex}",
        "body": str(request.body or "").strip(),
        "author": actor_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _append_metadata_entry(metadata, "record_comments", comment)
    db.update_ap_item(
        ap_item_id,
        metadata=metadata,
        _actor_type="user",
        _actor_id=actor_id,
        _source="ap_items_api",
        _decision_reason="record_comment_added",
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "record_comment_added",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": item.get("organization_id") or "default",
            "source": "ap_items_api",
            "payload_json": {
                "comment_id": comment["id"],
                "body": comment["body"],
            },
        }
    )

    # §5.3 @Mentions — parse @email in comment body, dispatch notifications
    _dispatch_mention_notifications(
        body=comment["body"],
        ap_item_id=ap_item_id,
        item=item,
        actor_id=actor_id,
    )

    return {"status": "created", "comment": comment}


@router.post("/{ap_item_id}/files")
def add_ap_item_file_link(
    ap_item_id: str,
    request: AddApItemFileRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)
    actor_id = shared._authenticated_actor(_user)
    metadata = shared._parse_json(item.get("metadata"))
    file_entry = {
        "id": f"file_{uuid.uuid4().hex}",
        "label": str(request.label or "").strip(),
        "url": str(request.url or "").strip() or None,
        "file_name": str(request.file_name or "").strip() or None,
        "file_type": str(request.file_type or "").strip() or None,
        "source": str(request.source or "").strip() or "manual_link",
        "note": str(request.note or "").strip() or None,
        "author": actor_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if not file_entry["url"] and not file_entry["file_name"]:
        file_entry["file_name"] = file_entry["label"]
    _append_metadata_entry(metadata, "record_file_links", file_entry, limit=50)
    db.update_ap_item(
        ap_item_id,
        metadata=metadata,
        _actor_type="user",
        _actor_id=actor_id,
        _source="ap_items_api",
        _decision_reason="record_file_linked",
    )
    db.append_ap_audit_event(
        {
            "ap_item_id": ap_item_id,
            "event_type": "record_file_linked",
            "actor_type": "user",
            "actor_id": actor_id,
            "organization_id": item.get("organization_id") or "default",
            "source": "ap_items_api",
            "payload_json": {
                "file_id": file_entry["id"],
                "label": file_entry["label"],
                "url": file_entry["url"],
                "file_name": file_entry["file_name"],
                "file_type": file_entry["file_type"],
                "source": file_entry["source"],
            },
        }
    )
    return {"status": "created", "file": file_entry}


@router.post("/{ap_item_id}/resubmit")
def resubmit_rejected_item(
    ap_item_id: str,
    request: ResubmitRejectedItemRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    db = shared.get_db()
    actor_id = shared._authenticated_actor(_user)
    source = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
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
    target = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(target.get("organization_id") or "default", _user)
    source = shared._require_item(db, request.source_ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))

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
    parent = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
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

    # D8: Track split items against subscription quota
    try:
        from clearledgr.services.subscription import get_subscription_service
        split_org_id = parent.get("organization_id") or "default"
        get_subscription_service().increment_usage(split_org_id, "invoices_this_month", amount=len(created_items))
    except Exception:
        pass

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


# ---------------------------------------------------------------------------
# Phase 1.4: Override-window reversal endpoint
# ---------------------------------------------------------------------------


from pydantic import BaseModel, Field as _PydField  # noqa: E402  (local import)


class ReverseAPItemRequest(BaseModel):
    """Request body for ``POST /api/ap/items/{ap_item_id}/reverse``."""

    reason: str = _PydField(
        ...,
        min_length=1,
        max_length=512,
        description=(
            "Mandatory human-supplied reason for the reversal. Recorded "
            "on the audit trail and forwarded to the ERP reverse_bill call."
        ),
    )


@router.post("/{ap_item_id}/reverse")
async def reverse_ap_item_post(
    ap_item_id: str,
    request: ReverseAPItemRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Reverse a posted bill via the override-window service.

    Phase 1.4 (DESIGN_THESIS.md §8): the API path for the override-window
    "Undo post" action. Mirrors the Slack button handler in
    ``api/slack_invoices.py`` but is callable from any non-Slack surface
    (Gmail sidebar, ops console, CLI). Requires the ops role and that
    the AP item belongs to the user's organization.

    The request fails with 404 if no override window exists for the
    given ap_item_id (the post happened before Phase 1.4 was enabled,
    or the window was already finalized). It fails with 410 Gone if
    the window has expired. It fails with 502 if the ERP rejects the
    reversal.
    """
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(user, "organization_id", None))
    verify_org_access(item.get("organization_id") or organization_id or "default", user)

    window = db.get_override_window_by_ap_item_id(ap_item_id)
    if not window:
        raise HTTPException(
            status_code=404,
            detail="no_override_window",
        )

    actor_label = (
        getattr(user, "email", None)
        or getattr(user, "user_id", None)
        or "ops_user"
    )

    from clearledgr.services import slack_cards
    from clearledgr.services.override_window import get_override_window_service

    org_id_for_service = (
        window.get("organization_id")
        or item.get("organization_id")
        or organization_id
        or "default"
    )

    service = get_override_window_service(org_id_for_service, db=db)
    outcome = await service.attempt_reversal(
        window_id=str(window.get("id")),
        actor_id=str(actor_label),
        reason=request.reason,
    )

    fresh_window = db.get_override_window(str(window.get("id"))) or window
    fresh_item = db.get_ap_item(ap_item_id) or item

    # Best-effort Slack card sync — same logic as the Slack handler.
    try:
        if outcome.status in {"reversed", "already_reversed"}:
            await slack_cards.update_card_to_reversed(
                organization_id=org_id_for_service,
                ap_item=fresh_item,
                window=fresh_window,
                actor_id=str(actor_label),
                reversal_ref=outcome.reversal_ref,
                reversal_method=outcome.reversal_method,
            )
        elif outcome.status == "expired":
            await slack_cards.update_card_to_finalized(
                organization_id=org_id_for_service,
                ap_item=fresh_item,
                window=fresh_window,
            )
        else:
            await slack_cards.update_card_to_reversal_failed(
                organization_id=org_id_for_service,
                ap_item=fresh_item,
                window=fresh_window,
                actor_id=str(actor_label),
                failure_reason=outcome.reason or outcome.status,
                failure_message=outcome.message,
            )
    except Exception:
        # Never let Slack failures break the API contract.
        pass

    if outcome.status == "reversed":
        return {
            "status": "reversed",
            "ap_item_id": ap_item_id,
            "window_id": outcome.window_id,
            "reversal_ref": outcome.reversal_ref,
            "reversal_method": outcome.reversal_method,
            "erp": outcome.erp,
        }
    if outcome.status == "already_reversed":
        return {
            "status": "already_reversed",
            "ap_item_id": ap_item_id,
            "window_id": outcome.window_id,
            "reversal_ref": outcome.reversal_ref,
            "erp": outcome.erp,
        }
    if outcome.status == "expired":
        raise HTTPException(
            status_code=410,
            detail="override_window_expired",
        )
    if outcome.status == "skipped":
        raise HTTPException(
            status_code=400,
            detail="no_erp_connected",
        )
    if outcome.status == "not_found":
        raise HTTPException(
            status_code=404,
            detail="no_override_window",
        )
    # status == "failed"
    raise HTTPException(
        status_code=502,
        detail={
            "error": "reversal_failed",
            "reason": outcome.reason,
            "message": outcome.message,
            "erp": outcome.erp,
        },
    )


# ==================== SNOOZE (DESIGN_THESIS.md §3 Gmail Power Features) ====================


@router.post("/{ap_item_id}/snooze")
async def snooze_ap_item(
    ap_item_id: str,
    request: SnoozeAPItemRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Snooze an AP item — archive it and return it to the queue after a set time.

    DESIGN_THESIS.md §3: "AP Managers can snooze a vendor email thread —
    archive on email and return it to the top of the queue after a set time.
    Snooze timings surface in the Box context."
    """
    from clearledgr.core.database import get_db
    from clearledgr.core.ap_states import transition_or_raise

    db = get_db()
    verify_org_access(organization_id, user)
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    current_state = str(item.get("state", "")).lower()
    transition_or_raise(current_state, "snoozed", ap_item_id)

    now = datetime.now(timezone.utc)
    from datetime import timedelta
    snoozed_until = now + timedelta(minutes=request.duration_minutes)

    # Store pre-snooze state so the reaper can restore it
    metadata = dict(item.get("metadata") or {})
    metadata["pre_snooze_state"] = current_state
    metadata["snoozed_until"] = snoozed_until.isoformat()
    if request.note:
        metadata["snooze_note"] = request.note

    db.update_ap_item(ap_item_id, state="snoozed", metadata=metadata)

    actor_id = getattr(user, "email", None) or getattr(user, "user_id", "system")
    db.append_ap_item_timeline_entry(ap_item_id, {
        "event_type": "snoozed",
        "summary": f"Snoozed for {request.duration_minutes} minutes.",
        "reason": request.note or "",
        "next_action": f"Returns to queue at {snoozed_until.strftime('%Y-%m-%d %H:%M UTC')}.",
        "actor": actor_id,
        "timestamp": now.isoformat(),
    })

    return {
        "status": "snoozed",
        "snoozed_until": snoozed_until.isoformat(),
        "pre_snooze_state": current_state,
    }


@router.post("/{ap_item_id}/unsnooze")
async def unsnooze_ap_item(
    ap_item_id: str,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Manually unsnooze an AP item before the timer expires."""
    from clearledgr.core.database import get_db

    db = get_db()
    verify_org_access(organization_id, user)
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    if str(item.get("state", "")).lower() != "snoozed":
        raise HTTPException(status_code=409, detail="not_snoozed")

    metadata = dict(item.get("metadata") or {})
    restore_state = metadata.pop("pre_snooze_state", "needs_approval")
    metadata.pop("snoozed_until", None)
    metadata.pop("snooze_note", None)

    db.update_ap_item(ap_item_id, state=restore_state, metadata=metadata)

    actor_id = getattr(user, "email", None) or getattr(user, "user_id", "system")
    db.append_ap_item_timeline_entry(ap_item_id, {
        "event_type": "unsnoozed",
        "summary": f"Unsnoozed manually. Restored to {restore_state.replace('_', ' ')}.",
        "actor": actor_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return {"status": "unsnoozed", "restored_state": restore_state}


# ---------------------------------------------------------------------------
# §2.2: Manual Classification
# ---------------------------------------------------------------------------


@router.post("/{ap_item_id}/classify")
async def classify_ap_item(
    ap_item_id: str,
    classification: str = Query(..., description="Classification: invoice, credit_note, payment_query, vendor_statement, irrelevant"),
    organization_id: Optional[str] = Query(default=None),
    user: Any = Depends(require_ops_user),
):
    """§2.2: AP Manager manually classifies a Review Required email.

    Enqueues a MANUAL_CLASSIFICATION event so the planning engine
    produces the appropriate plan for the new classification.
    """
    from clearledgr.api.deps import verify_org_access
    org_id = verify_org_access(user, organization_id)
    db = get_db()

    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    # Update the document_type on the item
    db.update_ap_item(
        ap_item_id,
        document_type=classification,
        _actor_type="user",
        _actor_id=getattr(user, "email", None) or getattr(user, "user_id", "system"),
    )

    # Enqueue MANUAL_CLASSIFICATION event
    try:
        from clearledgr.core.events import AgentEvent, AgentEventType
        from clearledgr.core.event_queue import get_event_queue
        get_event_queue().enqueue(AgentEvent(
            type=AgentEventType.MANUAL_CLASSIFICATION,
            source="ap_manager",
            payload={
                "message_id": item.get("message_id") or item.get("thread_id", ""),
                "classification": classification,
                "classified_by": getattr(user, "email", None) or getattr(user, "user_id", "system"),
                "ap_item_id": ap_item_id,
            },
            organization_id=org_id,
        ))
    except Exception as exc:
        logger.debug("[classify] Event enqueue failed (non-fatal): %s", exc)

    # Record in timeline
    if hasattr(db, "append_ap_item_timeline_entry"):
        db.append_ap_item_timeline_entry(ap_item_id, {
            "type": "human_action",
            "summary": f"Manually classified as {classification}",
            "actor": getattr(user, "email", "system"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    return {
        "status": "classified",
        "ap_item_id": ap_item_id,
        "classification": classification,
    }


# ---------------------------------------------------------------------------
# BatchOps — bulk endpoints (DESIGN_THESIS.md §6.7 power-user workflows)
#
# Every bulk endpoint:
#   - runs the action per item through the normal runtime / store path,
#     so every Rule 1 pre-write, audit event, and state transition still
#     fires. There is no bulk-specific short-circuit.
#   - captures a per-item result in the response, never aborts the
#     batch on a single failure.
#   - caps the batch at 100 items (pydantic max_length on the request).
# ---------------------------------------------------------------------------


def _bulk_resolve_item(db, ap_item_id: str, expected_org: str) -> Optional[Dict[str, Any]]:
    """Return the item dict if it exists and belongs to the org, else None."""
    item = db.get_ap_item(ap_item_id)
    if not item:
        return None
    if str(item.get("organization_id") or "") != str(expected_org or ""):
        return None
    return item


@router.post("/bulk-approve")
async def bulk_approve_ap_items(
    request: BulkApproveRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Approve N items in one call. Each runs through approve_invoice
    intent so the validation gate and ERP post still fire per item."""
    verify_org_access(organization_id, user)
    db = shared.get_db()

    runtime_cls = shared._finance_agent_runtime_cls()
    actor_id = getattr(user, "email", None) or getattr(user, "user_id", "bulk_approve")
    runtime = runtime_cls(
        organization_id=organization_id,
        actor_id=actor_id,
        actor_email=getattr(user, "email", None),
        db=db,
    )

    results: List[Dict[str, Any]] = []
    succeeded = 0
    for ap_item_id in request.ap_item_ids:
        item = _bulk_resolve_item(db, ap_item_id, organization_id)
        if not item:
            results.append({
                "ap_item_id": ap_item_id,
                "status": "error",
                "reason": "ap_item_not_found_or_wrong_org",
            })
            continue

        intent_payload = {
            "ap_item_id": ap_item_id,
            "email_id": str(item.get("thread_id") or ap_item_id),
            "source_channel": "gmail_extension_bulk",
            "source_channel_id": "gmail_extension_bulk",
            "actor_id": actor_id,
            "actor_display": actor_id,
        }
        if request.override:
            intent_payload["approve_override"] = True
            intent_payload["action_variant"] = "bulk_override"
            if request.override_justification:
                intent_payload["reason"] = request.override_justification
                intent_payload["override_justification"] = request.override_justification
        if request.note:
            intent_payload.setdefault("reason", request.note)

        try:
            result = await runtime.execute_intent("approve_invoice", intent_payload)
        except Exception as exc:
            logger.exception("[BatchOps] bulk-approve failure for %s", ap_item_id)
            results.append({
                "ap_item_id": ap_item_id,
                "status": "error",
                "reason": safe_error(exc, "bulk_approve"),
            })
            continue

        status = str((result or {}).get("status") or "").strip().lower()
        ok = status in {"approved", "posted", "posted_to_erp", "ready_to_post"}
        if ok:
            succeeded += 1
        results.append({
            "ap_item_id": ap_item_id,
            "status": status or "unknown",
            "ok": ok,
            "reason": (result or {}).get("reason"),
            "erp_reference": (result or {}).get("erp_reference"),
        })

    return {
        "total": len(request.ap_item_ids),
        "succeeded": succeeded,
        "failed": len(request.ap_item_ids) - succeeded,
        "results": results,
    }


@router.post("/bulk-reject")
async def bulk_reject_ap_items(
    request: BulkRejectRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Reject N items with a shared reason."""
    verify_org_access(organization_id, user)
    db = shared.get_db()

    runtime_cls = shared._finance_agent_runtime_cls()
    actor_id = getattr(user, "email", None) or getattr(user, "user_id", "bulk_reject")
    runtime = runtime_cls(
        organization_id=organization_id,
        actor_id=actor_id,
        actor_email=getattr(user, "email", None),
        db=db,
    )

    results: List[Dict[str, Any]] = []
    succeeded = 0
    for ap_item_id in request.ap_item_ids:
        item = _bulk_resolve_item(db, ap_item_id, organization_id)
        if not item:
            results.append({
                "ap_item_id": ap_item_id,
                "status": "error",
                "reason": "ap_item_not_found_or_wrong_org",
            })
            continue

        try:
            result = await runtime.execute_intent(
                "reject_invoice",
                {
                    "ap_item_id": ap_item_id,
                    "email_id": str(item.get("thread_id") or ap_item_id),
                    "reason": request.reason,
                    "source_channel": "gmail_extension_bulk",
                    "source_channel_id": "gmail_extension_bulk",
                    "source_message_ref": str(item.get("thread_id") or ap_item_id),
                    "actor_id": actor_id,
                    "actor_display": actor_id,
                },
            )
        except Exception as exc:
            logger.exception("[BatchOps] bulk-reject failure for %s", ap_item_id)
            results.append({
                "ap_item_id": ap_item_id,
                "status": "error",
                "reason": safe_error(exc, "bulk_reject"),
            })
            continue

        status = str((result or {}).get("status") or "").strip().lower()
        ok = status == "rejected"
        if ok:
            succeeded += 1
        results.append({
            "ap_item_id": ap_item_id,
            "status": status or "unknown",
            "ok": ok,
            "reason": (result or {}).get("reason"),
        })

    return {
        "total": len(request.ap_item_ids),
        "succeeded": succeeded,
        "failed": len(request.ap_item_ids) - succeeded,
        "results": results,
    }


@router.post("/bulk-snooze")
async def bulk_snooze_ap_items(
    request: BulkSnoozeRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Snooze N items for the same duration. Uses the same state-machine
    path as the single-item snooze endpoint, so the reaper restores them."""
    from datetime import timedelta
    from clearledgr.core.ap_states import transition_or_raise

    verify_org_access(organization_id, user)
    db = shared.get_db()

    results: List[Dict[str, Any]] = []
    succeeded = 0
    now = datetime.now(timezone.utc)
    snoozed_until = now + timedelta(minutes=request.duration_minutes)
    actor_id = getattr(user, "email", None) or getattr(user, "user_id", "bulk_snooze")

    for ap_item_id in request.ap_item_ids:
        item = _bulk_resolve_item(db, ap_item_id, organization_id)
        if not item:
            results.append({
                "ap_item_id": ap_item_id,
                "status": "error",
                "reason": "ap_item_not_found_or_wrong_org",
            })
            continue

        current_state = str(item.get("state", "")).lower()
        try:
            transition_or_raise(current_state, "snoozed", ap_item_id)
        except Exception as exc:
            results.append({
                "ap_item_id": ap_item_id,
                "status": "error",
                "reason": f"invalid_state_transition:{current_state}",
            })
            continue

        # metadata comes back as JSON text from SQLite, parsed dict from Postgres
        raw_meta = item.get("metadata")
        if isinstance(raw_meta, str) and raw_meta.strip():
            import json as _json
            try:
                metadata = dict(_json.loads(raw_meta) or {})
            except (ValueError, TypeError):
                metadata = {}
        elif isinstance(raw_meta, dict):
            metadata = dict(raw_meta)
        else:
            metadata = {}
        metadata["pre_snooze_state"] = current_state
        metadata["snoozed_until"] = snoozed_until.isoformat()
        if request.note:
            metadata["snooze_note"] = request.note

        try:
            db.update_ap_item(ap_item_id, state="snoozed", metadata=metadata)
            if hasattr(db, "append_ap_item_timeline_entry"):
                db.append_ap_item_timeline_entry(ap_item_id, {
                    "event_type": "snoozed",
                    "summary": f"Bulk snooze for {request.duration_minutes} minutes.",
                    "reason": request.note or "bulk_snooze",
                    "next_action": f"Returns to queue at {snoozed_until.strftime('%Y-%m-%d %H:%M UTC')}.",
                    "actor": actor_id,
                    "timestamp": now.isoformat(),
                })
            succeeded += 1
            results.append({
                "ap_item_id": ap_item_id,
                "status": "snoozed",
                "ok": True,
                "snoozed_until": snoozed_until.isoformat(),
            })
        except Exception as exc:
            logger.exception("[BatchOps] bulk-snooze failure for %s", ap_item_id)
            results.append({
                "ap_item_id": ap_item_id,
                "status": "error",
                "reason": safe_error(exc, "bulk_snooze"),
            })

    return {
        "total": len(request.ap_item_ids),
        "succeeded": succeeded,
        "failed": len(request.ap_item_ids) - succeeded,
        "snoozed_until": snoozed_until.isoformat(),
        "results": results,
    }


@router.post("/bulk-retry-post")
async def bulk_retry_post_ap_items(
    request: BulkRetryPostRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Retry ERP posting for N items stuck in failed_post."""
    verify_org_access(organization_id, user)
    db = shared.get_db()

    runtime_cls = shared._finance_agent_runtime_cls()
    actor_id = getattr(user, "email", None) or getattr(user, "user_id", "bulk_retry")
    runtime = runtime_cls(
        organization_id=organization_id,
        actor_id=actor_id,
        actor_email=getattr(user, "email", None),
        db=db,
    )

    results: List[Dict[str, Any]] = []
    succeeded = 0
    for ap_item_id in request.ap_item_ids:
        item = _bulk_resolve_item(db, ap_item_id, organization_id)
        if not item:
            results.append({
                "ap_item_id": ap_item_id,
                "status": "error",
                "reason": "ap_item_not_found_or_wrong_org",
            })
            continue

        current_state = str(item.get("state") or item.get("status") or "").lower()
        if current_state != APState.FAILED_POST:
            results.append({
                "ap_item_id": ap_item_id,
                "status": "error",
                "reason": f"invalid_state:{current_state}_expected_failed_post",
            })
            continue

        try:
            retry_result = await runtime.execute_intent(
                "retry_recoverable_failures",
                {
                    "ap_item_id": ap_item_id,
                    "email_id": str(item.get("thread_id") or ap_item_id),
                    "reason": "bulk_retry_post",
                },
            )
        except Exception as exc:
            logger.exception("[BatchOps] bulk-retry-post failure for %s", ap_item_id)
            results.append({
                "ap_item_id": ap_item_id,
                "status": "error",
                "reason": safe_error(exc, "bulk_retry_post"),
            })
            continue

        status = str((retry_result or {}).get("status") or "").strip().lower()
        ok = status in {"posted", "posted_to_erp", "ready_to_post"}
        if ok:
            succeeded += 1
        results.append({
            "ap_item_id": ap_item_id,
            "status": status or "unknown",
            "ok": ok,
            "reason": (retry_result or {}).get("reason"),
            "erp_reference": (retry_result or {}).get("erp_reference"),
        })

    return {
        "total": len(request.ap_item_ids),
        "succeeded": succeeded,
        "failed": len(request.ap_item_ids) - succeeded,
        "results": results,
    }
