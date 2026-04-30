"""Mutating AP item routes."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

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
from clearledgr.api.deps import verify_org_access
from clearledgr.core.ap_states import APState
from clearledgr.core.auth import require_ops_user
from clearledgr.core.errors import safe_error
from clearledgr.core.idempotency import (
    IDEMPOTENCY_HEADER,
    load_idempotent_response,
    save_idempotent_response,
)

logger = logging.getLogger(__name__)


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
    """Resolve a non-invoice document via the ``resolve_non_invoice_review`` intent.

    Thin HTTP→intent wrapper. The handler validates outcome against
    document-type allow-list, resolves linked records, runs the
    statement-to-reconciliation artifact path when applicable, and
    audits the resolution with full agent context.
    """
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(user, "organization_id", None))
    verify_org_access(item.get("organization_id") or organization_id or "default", user)

    runtime = shared._finance_agent_runtime_cls()(
        organization_id=str(item.get("organization_id") or organization_id or "default"),
        actor_id=getattr(user, "user_id", None) or getattr(user, "email", None) or "system",
        actor_email=getattr(user, "email", None),
        db=db,
    )
    intent_payload = {
        "ap_item_id": ap_item_id,
        "outcome": request.outcome,
        "related_reference": request.related_reference,
        "related_ap_item_id": request.related_ap_item_id,
        "note": request.note,
        "close_record": bool(request.close_record),
        "actor_id": getattr(user, "user_id", None) or getattr(user, "email", None),
        "actor_email": getattr(user, "email", None),
        "source_channel": "workspace_spa",
    }
    result = await runtime.execute_intent(
        "resolve_non_invoice_review", intent_payload,
        idempotency_key=f"resolve_non_invoice_review:{ap_item_id}:{request.outcome}:{getattr(user, 'email', None) or 'system'}",
    )

    status = str((result or {}).get("status") or "").strip().lower()
    if status == "blocked":
        reason = str((result or {}).get("reason") or "")
        if reason in {"invoice_document_not_supported", "invalid_non_invoice_outcome", "related_reference_required"}:
            raise HTTPException(status_code=400, detail=reason)
        raise HTTPException(status_code=400, detail=reason or "non_invoice_resolve_blocked")
    if status == "error":
        raise HTTPException(status_code=500, detail=(result or {}).get("reason") or "non_invoice_resolve_failed")

    return {
        "status": "resolved",
        "ap_item_id": ap_item_id,
        "document_type": (result or {}).get("document_type"),
        "outcome": (result or {}).get("outcome"),
        "state": (result or {}).get("state"),
        "ap_item": (result or {}).get("ap_item"),
        "audit_event_id": (result or {}).get("audit_event_id"),
    }


@router.post("/{ap_item_id}/entity-route/resolve")
async def resolve_ap_item_entity_route(
    ap_item_id: str,
    request: ResolveEntityRouteRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Resolve an entity-routing selection via the ``resolve_entity_route`` intent.

    Thin HTTP→intent wrapper. The handler resolves the candidate set,
    matches the operator's selection against it, mutates entity_*
    metadata, and audits the decision with full agent context.
    """
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(user, "organization_id", None))
    verify_org_access(item.get("organization_id") or organization_id or "default", user)

    runtime = shared._finance_agent_runtime_cls()(
        organization_id=str(item.get("organization_id") or organization_id or "default"),
        actor_id=getattr(user, "user_id", None) or getattr(user, "email", None) or "system",
        actor_email=getattr(user, "email", None),
        db=db,
    )
    intent_payload = {
        "ap_item_id": ap_item_id,
        "selection": request.selection,
        "entity_id": request.entity_id,
        "entity_code": request.entity_code,
        "entity_name": request.entity_name,
        "note": request.note,
        "actor_id": getattr(user, "user_id", None) or getattr(user, "email", None),
        "actor_email": getattr(user, "email", None),
        "source_channel": "workspace_spa",
    }
    sel_signature = request.entity_id or request.entity_code or request.selection or "_"
    result = await runtime.execute_intent(
        "resolve_entity_route", intent_payload,
        idempotency_key=f"resolve_entity_route:{ap_item_id}:{sel_signature}",
    )

    status = str((result or {}).get("status") or "").strip().lower()
    if status == "blocked":
        reason = str((result or {}).get("reason") or "")
        if reason in {"entity_route_not_supported", "entity_selection_required"}:
            raise HTTPException(status_code=400, detail=reason)
        raise HTTPException(status_code=400, detail=reason or "entity_route_blocked")
    if status == "error":
        raise HTTPException(status_code=500, detail=(result or {}).get("reason") or "entity_route_failed")

    return {
        "status": "resolved",
        "ap_item_id": ap_item_id,
        "entity_selection": (result or {}).get("entity_selection"),
        "entity_routing_status": (result or {}).get("entity_routing_status"),
        "ap_item": (result or {}).get("ap_item"),
        "audit_event_id": (result or {}).get("audit_event_id"),
    }


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
    db.append_audit_event(
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

    db.append_audit_event(
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

    db.append_audit_event(
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
async def update_ap_item_fields(
    ap_item_id: str,
    request: UpdateApItemFieldsRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Update AP item header fields via the ``update_invoice_fields`` intent.

    Thin HTTP→intent wrapper. The handler diffs the requested fields
    against the current AP item, applies only the actual changes
    through the column-whitelisted ``update_ap_item`` path, and audits
    the diff with full agent context.
    """
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(item.get("organization_id") or "default", _user)

    runtime = shared._finance_agent_runtime_cls()(
        organization_id=str(item.get("organization_id") or "default"),
        actor_id=getattr(_user, "user_id", None) or getattr(_user, "email", None) or "system",
        actor_email=getattr(_user, "email", None),
        db=db,
    )

    # Carry every set field — including explicit-None — into the intent
    # payload so the handler's precheck can compute the real diff itself.
    field_payload: Dict[str, Any] = {
        "ap_item_id": ap_item_id,
        "actor_id": getattr(_user, "user_id", None) or getattr(_user, "email", None),
        "actor_email": getattr(_user, "email", None),
        "source_channel": "workspace_spa",
        "note": request.note,
    }
    for field_name in ("vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "amount", "currency"):
        value = getattr(request, field_name, None)
        if value is not None:
            field_payload[field_name] = value

    # Idempotency: a stable key keyed on the diff signature so repeated
    # edits with the same payload return the same audit row.
    diff_signature = ",".join(
        f"{k}={field_payload.get(k)}"
        for k in sorted(field_payload.keys())
        if k not in {"ap_item_id", "actor_id", "actor_email", "source_channel", "note"}
    )
    result = await runtime.execute_intent(
        "update_invoice_fields", field_payload,
        idempotency_key=f"update_invoice_fields:{ap_item_id}:{diff_signature}",
    )

    status = str((result or {}).get("status") or "").strip().lower()
    if status == "blocked":
        reason = str((result or {}).get("reason") or "")
        if reason == "no_field_changes":
            raise HTTPException(status_code=400, detail=reason)
        raise HTTPException(status_code=400, detail=reason or "update_blocked")
    if status == "error":
        raise HTTPException(status_code=500, detail=(result or {}).get("reason") or "update_failed")

    return {
        "status": "updated",
        "changes": (result or {}).get("changes") or [],
        "ap_item": (result or {}).get("ap_item"),
        "audit_event_id": (result or {}).get("audit_event_id"),
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
    db.append_audit_event(
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
    db.append_audit_event(
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
    db.append_audit_event(
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
    db.append_audit_event(
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
async def resubmit_rejected_item(
    ap_item_id: str,
    request: ResubmitRejectedItemRequest,
    _user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Resubmit a rejected AP item via the ``resubmit_invoice`` intent.

    Thin HTTP→intent wrapper. The handler creates the new linked
    item, updates supersession on the source, optionally copies
    source links, and emits a runtime audit event on each side of
    the chain.
    """
    db = shared.get_db()
    source = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(source.get("organization_id") or "default", _user)

    runtime = shared._finance_agent_runtime_cls()(
        organization_id=str(source.get("organization_id") or "default"),
        actor_id=getattr(_user, "user_id", None) or getattr(_user, "email", None) or "system",
        actor_email=getattr(_user, "email", None),
        db=db,
    )
    intent_payload = {
        "ap_item_id": ap_item_id,
        "actor_id": request.actor_id,
        "reason": request.reason,
        "initial_state": request.initial_state,
        "copy_sources": bool(request.copy_sources),
        "thread_id": request.thread_id,
        "message_id": request.message_id,
        "subject": request.subject,
        "sender": request.sender,
        "vendor_name": request.vendor_name,
        "amount": request.amount,
        "currency": request.currency,
        "invoice_number": request.invoice_number,
        "invoice_date": request.invoice_date,
        "due_date": request.due_date,
        "metadata": request.metadata or {},
        "actor_email": getattr(_user, "email", None),
        "source_channel": "workspace_spa",
    }
    result = await runtime.execute_intent(
        "resubmit_invoice", intent_payload,
        idempotency_key=f"resubmit_invoice:{ap_item_id}:{request.reason[:80]}",
    )

    status = str((result or {}).get("status") or "").strip().lower()
    if status == "blocked":
        reason = str((result or {}).get("reason") or "")
        if reason == "resubmission_requires_rejected_state":
            raise HTTPException(status_code=400, detail=reason)
        if reason == "invalid_resubmission_initial_state":
            raise HTTPException(status_code=400, detail=reason)
        if reason == "reason_required":
            raise HTTPException(status_code=400, detail=reason)
        raise HTTPException(status_code=400, detail=reason or "resubmit_blocked")
    if status == "error":
        raise HTTPException(status_code=500, detail=(result or {}).get("reason") or "resubmit_failed")

    return {
        "status": status,
        "source_ap_item_id": (result or {}).get("source_ap_item_id"),
        "new_ap_item_id": (result or {}).get("new_ap_item_id"),
        "copied_sources": (result or {}).get("copied_sources"),
        "linkage": (result or {}).get("linkage"),
        "ap_item": (result or {}).get("ap_item"),
        "audit_event_id": (result or {}).get("audit_event_id"),
    }


@router.post("/{ap_item_id}/merge")
async def merge_ap_items(ap_item_id: str, request: MergeItemsRequest, _user=Depends(require_ops_user)) -> Dict[str, Any]:
    """Merge two AP items via the ``merge_invoices`` intent.

    Thin HTTP→intent wrapper. The handler validates the source/target
    pair, moves source links onto the target, backfills gmail-origin
    links with merge_origin metadata, mutates both items' metadata
    (target → merge_history; source → suppressed_from_worklist), and
    audits both sides.
    """
    db = shared.get_db()
    target = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(target.get("organization_id") or "default", _user)

    runtime = shared._finance_agent_runtime_cls()(
        organization_id=str(target.get("organization_id") or "default"),
        actor_id=getattr(_user, "user_id", None) or getattr(_user, "email", None) or "system",
        actor_email=getattr(_user, "email", None),
        db=db,
    )
    intent_payload = {
        "ap_item_id": ap_item_id,
        "source_ap_item_id": request.source_ap_item_id,
        "actor_id": request.actor_id,
        "reason": request.reason,
        "actor_email": getattr(_user, "email", None),
        "source_channel": "workspace_spa",
    }
    result = await runtime.execute_intent(
        "merge_invoices", intent_payload,
        idempotency_key=f"merge_invoices:{ap_item_id}:{request.source_ap_item_id}",
    )

    status = str((result or {}).get("status") or "").strip().lower()
    if status == "blocked":
        reason = str((result or {}).get("reason") or "")
        if reason in {"cannot_merge_same_item", "organization_mismatch", "reason_required", "source_ap_item_id_required"}:
            raise HTTPException(status_code=400, detail=reason)
        if reason == "source_not_found":
            raise HTTPException(status_code=404, detail="source_ap_item_not_found")
        raise HTTPException(status_code=400, detail=reason or "merge_blocked")
    if status == "error":
        raise HTTPException(status_code=500, detail=(result or {}).get("reason") or "merge_failed")

    return {
        "status": "merged",
        "target_ap_item_id": (result or {}).get("target_ap_item_id") or ap_item_id,
        "source_ap_item_id": (result or {}).get("source_ap_item_id") or request.source_ap_item_id,
        "moved_sources": (result or {}).get("moved_sources"),
        "audit_event_id": (result or {}).get("audit_event_id"),
    }


@router.post("/{ap_item_id}/split")
async def split_ap_item(ap_item_id: str, request: SplitItemRequest, _user=Depends(require_ops_user)) -> Dict[str, Any]:
    """Split an AP item via the ``split_invoice`` intent.

    Thin HTTP→intent wrapper. The handler creates one new AP item per
    requested source-link, moves the source link onto the child,
    propagates thread/message IDs where applicable, audits each child
    creation, and tracks subscription quota usage.
    """
    db = shared.get_db()
    parent = shared._require_item(db, ap_item_id, expected_organization_id=getattr(_user, "organization_id", None))
    verify_org_access(parent.get("organization_id") or "default", _user)

    runtime = shared._finance_agent_runtime_cls()(
        organization_id=str(parent.get("organization_id") or "default"),
        actor_id=getattr(_user, "user_id", None) or getattr(_user, "email", None) or "system",
        actor_email=getattr(_user, "email", None),
        db=db,
    )
    intent_payload = {
        "ap_item_id": ap_item_id,
        "actor_id": request.actor_id,
        "reason": request.reason,
        "sources": [
            {"source_type": entry.source_type, "source_ref": entry.source_ref}
            for entry in (request.sources or [])
        ],
        "actor_email": getattr(_user, "email", None),
        "source_channel": "workspace_spa",
    }
    source_signature = ",".join(
        f"{entry.source_type}:{entry.source_ref}" for entry in (request.sources or [])
    )
    result = await runtime.execute_intent(
        "split_invoice", intent_payload,
        idempotency_key=f"split_invoice:{ap_item_id}:{source_signature}",
    )

    status = str((result or {}).get("status") or "").strip().lower()
    if status == "blocked":
        reason = str((result or {}).get("reason") or "")
        if reason == "no_sources_split":
            raise HTTPException(status_code=400, detail=reason)
        if reason in {"sources_required", "source_entry_invalid", "source_type_required", "source_ref_required"}:
            raise HTTPException(status_code=400, detail=reason)
        raise HTTPException(status_code=400, detail=reason or "split_blocked")
    if status == "error":
        raise HTTPException(status_code=500, detail=(result or {}).get("reason") or "split_failed")

    return {
        "status": "split",
        "parent_ap_item_id": (result or {}).get("parent_ap_item_id") or ap_item_id,
        "created_items": (result or {}).get("created_items") or [],
        "audit_event_id": (result or {}).get("audit_event_id"),
    }


@router.post("/{ap_item_id}/retry-post")
async def retry_erp_post(
    ap_item_id: str,
    organization_id: str = "default",
    idempotency_key: Optional[str] = Header(default=None, alias=IDEMPOTENCY_HEADER),
    _user=Depends(require_ops_user),
):
    verify_org_access(organization_id, _user)
    db = shared.get_db()

    if idempotency_key:
        replay = load_idempotent_response(db, idempotency_key)
        if replay:
            return replay

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
    actor_label = (
        getattr(_user, "email", None)
        or getattr(_user, "user_id", None)
        or "ap_retry"
    )
    if status == "posted":
        response = {
            "status": "posted",
            "ap_item_id": ap_item_id,
            "erp_reference": (retry_result or {}).get("erp_reference"),
            "resume_result": retry_result.get("result") if isinstance(retry_result, dict) else None,
            "retry_result": retry_result,
        }
        save_idempotent_response(
            db, idempotency_key, response,
            box_id=ap_item_id, box_type="ap_item",
            organization_id=organization_id, actor_id=actor_label,
        )
        return response
    if status == "blocked":
        reason = str((retry_result or {}).get("reason") or "retry_not_recoverable")
        raise HTTPException(status_code=400, detail=reason)
    if status == "ready_to_post":
        response = {
            "status": "ready_to_post",
            "ap_item_id": ap_item_id,
            "erp_reference": (retry_result or {}).get("erp_reference"),
            "resume_result": retry_result.get("result") if isinstance(retry_result, dict) else None,
            "retry_result": retry_result,
        }
        save_idempotent_response(
            db, idempotency_key, response,
            box_id=ap_item_id, box_type="ap_item",
            organization_id=organization_id, actor_id=actor_label,
        )
        return response
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
    """Reverse a posted bill via the ``reverse_invoice_post`` intent.

    Phase 1.4 (DESIGN_THESIS.md §8) override-window reversal. The
    handler runs the override-window service through the runtime so
    governance + agent memory + audit fire alongside the ERP-side
    reversal call. Slack-card sync is best-effort and runs in the
    route after the intent settles so a Slack outage never breaks
    the API contract.
    """
    db = shared.get_db()
    item = shared._require_item(db, ap_item_id, expected_organization_id=getattr(user, "organization_id", None))
    verify_org_access(item.get("organization_id") or organization_id or "default", user)

    org_id_for_service = (
        item.get("organization_id")
        or organization_id
        or "default"
    )
    runtime = shared._finance_agent_runtime_cls()(
        organization_id=str(org_id_for_service),
        actor_id=getattr(user, "user_id", None) or getattr(user, "email", None) or "ops_user",
        actor_email=getattr(user, "email", None),
        db=db,
    )
    intent_payload = {
        "ap_item_id": ap_item_id,
        "reason": request.reason,
        "actor_id": getattr(user, "user_id", None) or getattr(user, "email", None),
        "actor_email": getattr(user, "email", None),
        "source_channel": "workspace_spa",
    }
    result = await runtime.execute_intent(
        "reverse_invoice_post", intent_payload,
        idempotency_key=f"reverse_invoice_post:{ap_item_id}:{request.reason[:64]}",
    )

    status = str((result or {}).get("status") or "").strip().lower()
    if status == "blocked":
        reason = str((result or {}).get("reason") or "")
        if reason == "no_override_window":
            raise HTTPException(status_code=404, detail="no_override_window")
        if reason == "reason_required" or reason == "reason_too_long":
            raise HTTPException(status_code=400, detail=reason)
        if reason == "state_not_posted":
            raise HTTPException(status_code=409, detail="state_not_posted")
        raise HTTPException(status_code=400, detail=reason or "reverse_blocked")

    # Best-effort Slack card sync after the intent settles. Failures
    # here never break the API contract — Slack-side rendering is a
    # downstream concern and is reconciled by the override-window reaper.
    try:
        from clearledgr.services import slack_cards
        fresh_item = db.get_ap_item(ap_item_id) or item
        fresh_window = db.get_override_window((result or {}).get("window_id") or "") or {}
        actor_label = (
            getattr(user, "email", None)
            or getattr(user, "user_id", None)
            or "ops_user"
        )
        if status in {"reversed", "already_reversed"}:
            await slack_cards.update_card_to_reversed(
                organization_id=org_id_for_service,
                ap_item=fresh_item,
                window=fresh_window,
                actor_id=str(actor_label),
                reversal_ref=(result or {}).get("reversal_ref"),
                reversal_method=(result or {}).get("reversal_method"),
            )
        elif status == "expired":
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
                failure_reason=(result or {}).get("reason") or status,
                failure_message=(result or {}).get("message"),
            )
    except Exception:
        pass

    if status == "reversed":
        return {
            "status": "reversed",
            "ap_item_id": ap_item_id,
            "window_id": (result or {}).get("window_id"),
            "reversal_ref": (result or {}).get("reversal_ref"),
            "reversal_method": (result or {}).get("reversal_method"),
            "erp": (result or {}).get("erp"),
            "audit_event_id": (result or {}).get("audit_event_id"),
        }
    if status == "already_reversed":
        return {
            "status": "already_reversed",
            "ap_item_id": ap_item_id,
            "window_id": (result or {}).get("window_id"),
            "reversal_ref": (result or {}).get("reversal_ref"),
            "erp": (result or {}).get("erp"),
        }
    if status == "expired":
        raise HTTPException(status_code=410, detail="override_window_expired")
    if status == "skipped":
        raise HTTPException(status_code=400, detail="no_erp_connected")
    if status == "not_found":
        raise HTTPException(status_code=404, detail="no_override_window")
    # status == "failed" / "error"
    raise HTTPException(
        status_code=502,
        detail={
            "error": "reversal_failed",
            "reason": (result or {}).get("reason"),
            "message": (result or {}).get("message"),
            "erp": (result or {}).get("erp"),
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
    """Snooze an AP item via the ``snooze_invoice`` intent.

    DESIGN_THESIS.md §3: "AP Managers can snooze a vendor email thread —
    archive on email and return it to the top of the queue after a set time.
    Snooze timings surface in the Box context."

    The route is a thin HTTP→intent wrapper. All logic — state-machine
    validation, metadata mutation, timeline entry, agent runtime audit,
    governance deliberation — lives in :class:`SnoozeInvoiceHandler` so
    every surface (workspace, Slack, Gmail extension) shares the same
    contract and audit shape.
    """
    db = shared.get_db()
    verify_org_access(organization_id, user)
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    if request.idempotency_key:
        replay = load_idempotent_response(db, request.idempotency_key)
        if replay:
            return replay

    runtime = shared._finance_agent_runtime_cls()(
        organization_id=str(item.get("organization_id") or organization_id or "default"),
        actor_id=getattr(user, "user_id", None) or getattr(user, "email", None) or "system",
        actor_email=getattr(user, "email", None),
        db=db,
    )
    intent_payload = {
        "ap_item_id": ap_item_id,
        "duration_minutes": request.duration_minutes,
        "note": request.note,
        "actor_id": getattr(user, "user_id", None) or getattr(user, "email", None),
        "actor_email": getattr(user, "email", None),
        "source_channel": "workspace_spa",
    }
    intent_idempotency_key = (
        request.idempotency_key
        or f"snooze_invoice:{ap_item_id}:{request.duration_minutes}:{getattr(user, 'email', None) or 'system'}"
    )
    result = await runtime.execute_intent(
        "snooze_invoice", intent_payload, idempotency_key=intent_idempotency_key,
    )

    status = str((result or {}).get("status") or "").strip().lower()
    if status == "blocked":
        raise HTTPException(
            status_code=409 if (result or {}).get("reason") == "state_not_snoozeable" else 400,
            detail=(result or {}).get("reason") or "snooze_blocked",
        )
    if status == "error":
        raise HTTPException(status_code=500, detail=(result or {}).get("reason") or "snooze_failed")

    response = {
        "status": "snoozed",
        "snoozed_until": (result or {}).get("snoozed_until"),
        "pre_snooze_state": (result or {}).get("pre_snooze_state"),
        "audit_event_id": (result or {}).get("audit_event_id"),
    }
    actor_id = getattr(user, "email", None) or getattr(user, "user_id", "system")
    save_idempotent_response(
        db, request.idempotency_key, response,
        box_id=ap_item_id, box_type="ap_item",
        organization_id=organization_id, actor_id=actor_id,
    )
    return response


@router.post("/{ap_item_id}/unsnooze")
async def unsnooze_ap_item(
    ap_item_id: str,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Manually unsnooze an AP item via the ``unsnooze_invoice`` intent."""
    db = shared.get_db()
    verify_org_access(organization_id, user)
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    runtime = shared._finance_agent_runtime_cls()(
        organization_id=str(item.get("organization_id") or organization_id or "default"),
        actor_id=getattr(user, "user_id", None) or getattr(user, "email", None) or "system",
        actor_email=getattr(user, "email", None),
        db=db,
    )
    intent_payload = {
        "ap_item_id": ap_item_id,
        "actor_id": getattr(user, "user_id", None) or getattr(user, "email", None),
        "actor_email": getattr(user, "email", None),
        "source_channel": "workspace_spa",
    }
    result = await runtime.execute_intent(
        "unsnooze_invoice", intent_payload,
        idempotency_key=f"unsnooze_invoice:{ap_item_id}:{getattr(user, 'email', None) or 'system'}",
    )

    status = str((result or {}).get("status") or "").strip().lower()
    if status == "blocked":
        raise HTTPException(
            status_code=409,
            detail=(result or {}).get("reason") or "unsnooze_blocked",
        )
    if status == "error":
        raise HTTPException(status_code=500, detail=(result or {}).get("reason") or "unsnooze_failed")

    return {
        "status": "unsnoozed",
        "restored_state": (result or {}).get("restored_state"),
        "audit_event_id": (result or {}).get("audit_event_id"),
    }


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
    """§2.2: Manual classification via the ``manually_classify_invoice`` intent.

    Thin HTTP→intent wrapper. The handler enqueues a
    MANUAL_CLASSIFICATION event so the planning engine re-routes the
    item, writes the timeline entry, and emits the runtime audit event
    with full agent context.
    """
    from clearledgr.api.deps import verify_org_access as _verify
    org_id = _verify(user, organization_id)
    db = shared.get_db()
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")

    runtime = shared._finance_agent_runtime_cls()(
        organization_id=org_id,
        actor_id=getattr(user, "user_id", None) or getattr(user, "email", None) or "system",
        actor_email=getattr(user, "email", None),
        db=db,
    )
    intent_payload = {
        "ap_item_id": ap_item_id,
        "classification": classification,
        "actor_id": getattr(user, "user_id", None) or getattr(user, "email", None),
        "actor_email": getattr(user, "email", None),
        "source_channel": "workspace_spa",
    }
    result = await runtime.execute_intent(
        "manually_classify_invoice", intent_payload,
        idempotency_key=f"manually_classify:{ap_item_id}:{classification}:{getattr(user, 'email', None) or 'system'}",
    )

    status = str((result or {}).get("status") or "").strip().lower()
    if status == "blocked":
        raise HTTPException(status_code=400, detail=(result or {}).get("reason") or "classify_blocked")
    if status == "error":
        raise HTTPException(status_code=500, detail=(result or {}).get("reason") or "classify_failed")

    return {
        "status": "classified",
        "ap_item_id": ap_item_id,
        "classification": classification,
        "audit_event_id": (result or {}).get("audit_event_id"),
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

    if request.idempotency_key:
        replay = load_idempotent_response(db, request.idempotency_key)
        if replay:
            return replay

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

    response = {
        "total": len(request.ap_item_ids),
        "succeeded": succeeded,
        "failed": len(request.ap_item_ids) - succeeded,
        "results": results,
    }
    save_idempotent_response(
        db, request.idempotency_key, response,
        organization_id=organization_id, actor_id=actor_id,
    )
    return response


@router.post("/bulk-reject")
async def bulk_reject_ap_items(
    request: BulkRejectRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Reject N items with a shared reason."""
    verify_org_access(organization_id, user)
    db = shared.get_db()

    if request.idempotency_key:
        replay = load_idempotent_response(db, request.idempotency_key)
        if replay:
            return replay

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

    response = {
        "total": len(request.ap_item_ids),
        "succeeded": succeeded,
        "failed": len(request.ap_item_ids) - succeeded,
        "results": results,
    }
    save_idempotent_response(
        db, request.idempotency_key, response,
        organization_id=organization_id, actor_id=actor_id,
    )
    return response


@router.post("/bulk-snooze")
async def bulk_snooze_ap_items(
    request: BulkSnoozeRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Snooze N items by routing each through the ``snooze_invoice`` intent.

    Per-item routing keeps Rule 1 pre-write, audit emission, runtime
    governance + memory + learning consistent with the single-item
    endpoint. Bulk-specific handling: cap on items (pydantic), per-item
    idempotency derived from a shared ``snoozed_until`` so a re-run
    replays cleanly.
    """
    from datetime import timedelta

    verify_org_access(organization_id, user)
    db = shared.get_db()

    if request.idempotency_key:
        replay = load_idempotent_response(db, request.idempotency_key)
        if replay:
            return replay

    runtime_cls = shared._finance_agent_runtime_cls()
    actor_id = getattr(user, "email", None) or getattr(user, "user_id", "bulk_snooze")
    runtime = runtime_cls(
        organization_id=organization_id,
        actor_id=actor_id,
        actor_email=getattr(user, "email", None),
        db=db,
    )

    now = datetime.now(timezone.utc)
    snoozed_until = now + timedelta(minutes=request.duration_minutes)
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
            "duration_minutes": request.duration_minutes,
            "note": request.note,
            "actor_id": actor_id,
            "actor_email": getattr(user, "email", None),
            "source_channel": "workspace_spa_bulk",
        }
        try:
            result = await runtime.execute_intent(
                "snooze_invoice", intent_payload,
                idempotency_key=f"bulk_snooze:{ap_item_id}:{snoozed_until.isoformat()}",
            )
        except Exception as exc:
            logger.exception("[BatchOps] bulk-snooze failure for %s", ap_item_id)
            results.append({
                "ap_item_id": ap_item_id,
                "status": "error",
                "reason": safe_error(exc, "bulk_snooze"),
            })
            continue

        per_status = str((result or {}).get("status") or "").strip().lower()
        if per_status == "snoozed":
            succeeded += 1
            results.append({
                "ap_item_id": ap_item_id,
                "status": "snoozed",
                "ok": True,
                "snoozed_until": (result or {}).get("snoozed_until"),
                "audit_event_id": (result or {}).get("audit_event_id"),
            })
        else:
            # Per-item failures collapse to status="error" so the bulk
            # response stays consistent with bulk-approve / bulk-reject /
            # bulk-retry-post. The intent's blocked-reason becomes the
            # legacy ``invalid_state_transition:<current_state>`` token
            # when the block was a state-machine rejection so existing
            # consumers keep working.
            intent_reason = str((result or {}).get("reason") or per_status or "snooze_failed")
            if per_status == "blocked" and intent_reason in {"state_not_snoozeable", "duration_minutes_required"}:
                current_state = str(item.get("state") or "").strip().lower()
                reason_text = f"invalid_state_transition:{current_state}"
            else:
                reason_text = intent_reason
            results.append({
                "ap_item_id": ap_item_id,
                "status": "error",
                "ok": False,
                "reason": reason_text,
            })

    response = {
        "total": len(request.ap_item_ids),
        "succeeded": succeeded,
        "failed": len(request.ap_item_ids) - succeeded,
        "snoozed_until": snoozed_until.isoformat(),
        "results": results,
    }
    save_idempotent_response(
        db, request.idempotency_key, response,
        organization_id=organization_id, actor_id=actor_id,
    )
    return response


@router.post("/bulk-retry-post")
async def bulk_retry_post_ap_items(
    request: BulkRetryPostRequest,
    organization_id: str = Query(default="default"),
    user=Depends(require_ops_user),
) -> Dict[str, Any]:
    """Retry ERP posting for N items stuck in failed_post."""
    verify_org_access(organization_id, user)
    db = shared.get_db()

    if request.idempotency_key:
        replay = load_idempotent_response(db, request.idempotency_key)
        if replay:
            return replay

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

    response = {
        "total": len(request.ap_item_ids),
        "succeeded": succeeded,
        "failed": len(request.ap_item_ids) - succeeded,
        "results": results,
    }
    save_idempotent_response(
        db, request.idempotency_key, response,
        organization_id=organization_id, actor_id=actor_id,
    )
    return response
